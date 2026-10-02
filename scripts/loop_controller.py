#!/usr/bin/env python3
"""Bounded foreground driver for already admitted takeoff build tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from loop_runner_handoff import HandoffError, import_candidate, native_workflow_evidence

P1_SELECTED_SOURCE_PATHS = {
    ".agents/skills/claude-code-headless/SKILL.md", "AGENTS.md", "CLAUDE.md",
    "docs/operations/claude-code-headless.md",
    "docs/operations/claude-opus-5-5-ultracode.md",
    "docs/operations/claude-opus-5-5-ultracode.json",
    "scripts/experiments/grounding_dino_cpu_probe.py",
    "scripts/experiments/grounding_dino_public_cases.py",
    "tests/experiments/test_grounding_dino_cpu_probe.py",
}
P1_REPAIRED_SEED_SHA256 = {
    "docs/operations/loop-controller-mvp-2026-10-01/p1-trial-v3-offline-repair/candidate/scripts/experiments/grounding_dino_p1_harness.py":
        "0649b2807fe0cfbcc3f06058a3ae42e8e09c9a0f32c8acf923e56d2802d03a63",
    "docs/operations/loop-controller-mvp-2026-10-01/p1-trial-v3-offline-repair/candidate/tests/experiments/test_grounding_dino_p1_harness.py":
        "b40e259d5bd124d9a880a94f4ae3f9013c50e39ceaa7cf466c14ecf8777b93fa",
}
P1_AUDIT_WRAPPER = "scripts/loop_p1_seed_audit.py"
P1_FINAL_REPAIRED_SEED_SHA256 = {
    "docs/operations/loop-controller-mvp-2026-10-01/p1-trial-v4-offline-repair/candidate/scripts/experiments/grounding_dino_p1_harness.py":
        "ab916bed50dff9027052c4b57d0f92064fb3506465cfb03190b0b08fec87492c",
    "docs/operations/loop-controller-mvp-2026-10-01/p1-trial-v4-offline-repair/candidate/tests/experiments/test_grounding_dino_p1_harness.py":
        "b727c1d13ae518e5e312ede43c0b680cc4f24fc04ab31bbc4fa378920fcfc303",
}
P1_FINAL_AUDIT_WRAPPER = "scripts/loop_p1_seed_audit_v5.py"
P1_V6_REPAIRED_SEED_SHA256 = {
    "docs/operations/loop-controller-mvp-2026-10-01/p1-trial-v5-offline-repair/candidate/scripts/experiments/grounding_dino_p1_harness.py":
        "2ec579038d44d3276fecbdfded1735bad57545c09cce0aa0b845d91867268d2d",
    "docs/operations/loop-controller-mvp-2026-10-01/p1-trial-v5-offline-repair/candidate/tests/experiments/test_grounding_dino_p1_harness.py":
        "1c2618b9c126658d58892bd356d4faa23082282638652c46d1835a6ea6739dd5",
}
P1_V6_AUDIT_WRAPPER = "scripts/loop_p1_seed_audit_v6.py"
P1_CHECK_PYTHON = os.environ.get("HELEOS_P1_CHECK_PYTHON", "")


class LoopError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def portable_provider_argv():
    settings = {
        "model": "claude-opus-5-5", "effortLevel": "xhigh", "ultracode": True,
        "workflowSizeGuideline": "small",
        "env": {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0",
                "CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-5-5",
                "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1",
                "CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS": "15",
                "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "15",
                "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1"},
        "permissions": {"deny": ["Agent"]},
        "claudeMdExcludes": [str(Path.home() / ".claude" / "CLAUDE.md")],
    }
    return ["-p", "--output-format", "stream-json", "--verbose",
            "--forward-subagent-text", "--no-session-persistence",
            "--model", "claude-opus-5-5", "--effort", "ultracode", "--restricted",
            "--max-budget-usd", "5.00", "--strict-mcp-config", "--no-chrome",
            "--setting-sources", "", "--permission-mode", "acceptEdits",
            "--permission-prompts", "none", "--tools", "Read,Glob,Grep,Edit,Write,Workflow",
            "--allowedTools", "Workflow", "--settings",
            json.dumps(settings, indent=2, sort_keys=True, ensure_ascii=False) + "\n"]


def relpath(value):
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise LoopError("invalid relative path")
    if value.startswith("/") or any(part in ("", ".", "..") for part in value.split("/")):
        raise LoopError("unsafe relative path: " + value)
    return value


def within(path, prefixes):
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def file_at(root, path, required=True):
    path = relpath(path)
    target = root
    for part in path.split("/"):
        target = target / part
        if target.is_symlink():
            raise LoopError("symlink task path: " + path)
    if required and not target.is_file():
        raise LoopError("missing artifact: " + path)
    return target


def git_head(root):
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True)
    if result.returncode:
        raise LoopError("worktree is not a Git checkout")
    return result.stdout.strip()


def spec_at(root, ref, base, allowed):
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise LoopError("invalid pinned spec reference")
    raw = file_at(root, ref["path"]).read_bytes()
    if digest(raw) != ref["sha256"]:
        raise LoopError("pinned spec changed: " + ref["path"])
    try:
        spec = json.loads(raw)
    except ValueError as exc:
        raise LoopError("invalid guard spec JSON") from exc
    if not isinstance(spec, dict) or spec.get("expected_head") != base or not spec.get("task_id"):
        raise LoopError("guard spec has wrong base or task ID")
    paths = spec.get("allowed_paths")
    if not isinstance(paths, list) or not paths or any(
            not isinstance(p, str) or not within(relpath(p), allowed) for p in paths):
        raise LoopError("guard spec exceeds result allowlist")
    return spec


def input_drift(root, spec):
    inputs = spec.get("inputs")
    if not isinstance(inputs, dict):
        return "missing pinned input map"
    for path, expected in sorted(inputs.items()):
        try:
            actual = digest(file_at(root, path).read_bytes())
        except LoopError:
            return "input missing: " + str(path)
        if actual != expected:
            return "input changed: " + path
    return None


def load_manifest(path):
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file():
        raise LoopError("manifest must be a regular file")
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise LoopError("invalid manifest JSON") from exc
    if not isinstance(data, dict) or set(data) != {
            "schema", "base_head", "worktree", "allowed_result_paths", "tasks"
    } or data["schema"] not in ("heleos.loop-controller/v1", "heleos.loop-controller/v2",
                                "heleos.loop-controller/v3", "heleos.loop-controller/v4",
                                "heleos.loop-controller/v5", "heleos.loop-controller/v6",
                                "heleos.loop-controller/v7"):
        raise LoopError("invalid manifest schema")
    versioned = data["schema"] != "heleos.loop-controller/v1"
    receipt_version = data["schema"] in ("heleos.loop-controller/v3", "heleos.loop-controller/v4",
                                          "heleos.loop-controller/v5", "heleos.loop-controller/v6",
                                          "heleos.loop-controller/v7")
    if not isinstance(data["worktree"], str) or not isinstance(data["base_head"], str):
        raise LoopError("manifest worktree or base HEAD is malformed")
    root = Path(data["worktree"])
    if not root.is_absolute() or not root.is_dir() or root.is_symlink() or root.resolve() != root:
        raise LoopError("worktree must be canonical")
    if not path.is_relative_to(root) or git_head(root) != data["base_head"]:
        raise LoopError("manifest outside worktree or base HEAD changed")
    allowed = data["allowed_result_paths"]
    if (not isinstance(allowed, list) or not allowed
            or any(not isinstance(path, str) for path in allowed)
            or len(set(allowed)) != len(allowed)):
        raise LoopError("invalid result allowlist")
    for p in allowed:
        relpath(p)
        file_at(root, p, required=False)
        if p == ".loop-trial" or p.startswith(".loop-trial/"):
            raise LoopError("ledger path in result allowlist")
    tasks = data["tasks"]
    if not isinstance(tasks, list) or not tasks or any(not isinstance(t, dict) for t in tasks):
        raise LoopError("invalid task queue")
    ids = [t.get("id") for t in tasks]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise LoopError("duplicate or invalid task IDs")
    seen_specs = set()
    result_artifacts = []
    for task in tasks:
        required = {"id", "depends_on", "worker", "tests", "review", "candidate_paths",
                    "review_path", "worker_identity", "reviewer_identity"}
        if versioned:
            required |= {"native_workflow", "review_source_path"}
        if not required <= set(task) or set(task) - (required | {"repair"}):
            raise LoopError("invalid task fields")
        deps = task["depends_on"]
        if not isinstance(deps, list) or any(not isinstance(dep, str) for dep in deps) \
                or len(set(deps)) != len(deps) or task["id"] in deps or any(
                d not in ids for d in deps):
            raise LoopError("invalid dependency")
        if not isinstance(task["tests"], list) or not task["tests"]:
            raise LoopError("at least one check required")
        paths = task["candidate_paths"]
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths) \
                or len(set(paths)) != len(paths):
            raise LoopError("invalid candidate paths")
        workflow = task.get("native_workflow") if versioned else None
        if versioned and (not isinstance(workflow, dict) or set(workflow) !=
                          ({"child_label", "child_report_path", "model", "effort", "script_sha256"}
                           | ({"report_binding"} if receipt_version else set()))
                          or not isinstance(workflow["child_label"], str) or not workflow["child_label"]
                          or workflow["child_report_path"] not in paths
                          or workflow["model"] != "claude-opus-5-5" or workflow["effort"] != "xhigh"
                          or (receipt_version and workflow["report_binding"] != "controller_receipt")
                          or not isinstance(workflow["script_sha256"], str)
                          or len(workflow["script_sha256"]) != 64
                          or any(c not in "0123456789abcdef" for c in workflow["script_sha256"])):
            raise LoopError("versioned task requires one named native Workflow child")
        for p in [*paths, task["review_path"],
                  *([task["review_source_path"]] if versioned else [])]:
            if not within(relpath(p), allowed):
                raise LoopError("artifact exceeds result allowlist")
            file_at(root, p, required=False)
            if any(p == prior or p.startswith(prior + "/") or prior.startswith(p + "/")
                   for prior in result_artifacts):
                raise LoopError("shared result artifact across tasks")
            result_artifacts.append(p)
        if task["review_path"] in paths or not isinstance(task["worker_identity"], str) or not task["worker_identity"] \
                or not isinstance(task["reviewer_identity"], str) or not task["reviewer_identity"] \
                or task["reviewer_identity"] == task["worker_identity"]:
            raise LoopError("invalid review separation")
        task_specs = set()
        for role, ref in [("worker", task["worker"]),
                          *(("test", x) for x in task["tests"]),
                          ("review", task["review"])]:
            spec = spec_at(root, ref, data["base_head"], allowed)
            if role == "worker" and (spec.get("kind") != "worker" or set(spec["allowed_paths"]) != set(paths)):
                raise LoopError("worker write scope must equal candidate paths")
            if role == "review" and (spec.get("kind") != "test" or spec["allowed_paths"] != [task["review_path"]]):
                raise LoopError("review write scope must equal review artifact")
            if role == "test" and (spec.get("kind") != "test" or any(
                    p in paths or p == task["review_path"] or
                    (versioned and p == task["review_source_path"]) for p in spec["allowed_paths"])):
                raise LoopError("check may not write candidate or review artifact")
            sid = spec["task_id"]
            if sid in seen_specs:
                raise LoopError("duplicate guard ID")
            seen_specs.add(sid)
            task_specs.add(sid)
        if "repair" in task:
            repair = task["repair"]
            if not isinstance(repair, dict) or set(repair) != {"on_guard_id", "spec", "tests", "review"}:
                raise LoopError("invalid repair")
            spec = spec_at(root, repair["spec"], data["base_head"], allowed)
            if spec.get("kind") != "worker" or set(spec["allowed_paths"]) != set(paths):
                raise LoopError("repair write scope must equal candidate paths")
            if repair["on_guard_id"] not in task_specs or spec.get("predecessor") != repair["on_guard_id"] or not spec.get("changed_condition") or spec["task_id"] in seen_specs:
                raise LoopError("repair must have a unique ID and a linked changed condition")
            seen_specs.add(spec["task_id"])
            if not isinstance(repair["tests"], list) or not repair["tests"]:
                raise LoopError("repair requires fresh checks")
            for role, ref in [*(("test", x) for x in repair["tests"]),
                              ("review", repair["review"])]:
                later = spec_at(root, ref, data["base_head"], allowed)
                if role == "review" and (later.get("kind") != "test" or later["allowed_paths"] != [task["review_path"]]):
                    raise LoopError("repair review write scope mismatch")
                if role == "test" and (later.get("kind") != "test" or any(
                        p in paths or p == task["review_path"] or
                        (versioned and p == task["review_source_path"]) for p in later["allowed_paths"])):
                    raise LoopError("repair check write scope mismatch")
                if later["task_id"] in seen_specs:
                    raise LoopError("repair checks and review need fresh guard IDs")
                seen_specs.add(later["task_id"])
    deps = {t["id"]: t["depends_on"] for t in tasks}
    visited, visiting = set(), set()
    def visit(i):
        if i in visiting:
            raise LoopError("dependency cycle")
        if i not in visited:
            visiting.add(i)
            for d in deps[i]:
                visit(d)
            visiting.remove(i)
            visited.add(i)
    for i in ids:
        visit(i)
    return data, digest(raw)


class GuardClient:
    def __init__(self, root):
        self.root = root
        self.script = Path(__file__).with_name("build_run_guard.py")
        self.children = []

    def status(self, task_id):
        self.children = [proc for proc in self.children if proc.poll() is None]
        proc = subprocess.run([sys.executable, "-B", str(self.script), "--repo", str(self.root),
                               "status", "--task-id", task_id], capture_output=True, text=True)
        if proc.returncode not in (0, 3, 5):
            raise LoopError("guard status refused")
        try:
            return json.loads(proc.stdout)
        except ValueError as exc:
            raise LoopError("guard status JSON unavailable") from exc

    def launch(self, spec_path, spec_sha256):
        logs = self.root / ".loop-trial" / "guard-logs"
        logs.mkdir(parents=True, exist_ok=True)
        with (logs / (spec_sha256 + ".log")).open("ab") as output:
            proc = subprocess.Popen([sys.executable, "-B", str(self.script), "--repo", str(self.root),
                                     "run", "--spec", str(spec_path), "--spec-sha256", spec_sha256],
                                    stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.STDOUT, close_fds=True, start_new_session=True)
        self.children.append(proc)

    def reap_terminal(self):
        for proc in self.children:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass  # An active guard remains the sole owner; never signal it here.
        self.children = [proc for proc in self.children if proc.poll() is None]


class LoopController:
    def __init__(self, manifest, guard=None, admitted_digest=None, fixture_mode=False,
                 admission_path=None, admission_sha256=None):
        self.manifest_path = Path(manifest).absolute()
        self.manifest, self.manifest_digest = load_manifest(self.manifest_path)
        self.root = Path(self.manifest["worktree"])
        ledger_name = ("ledger.sqlite3" if self.manifest["schema"] == "heleos.loop-controller/v1"
                       else "ledger-" + self.manifest["schema"].rsplit("/", 1)[1] + "-" +
                       self.manifest_digest + ".sqlite3")
        self.db = self.root / ".loop-trial" / ledger_name
        self.guard = guard if guard is not None else GuardClient(self.root)
        self.tasks = {t["id"]: t for t in self.manifest["tasks"]}
        self.admitted_digest = admitted_digest
        self.fixture_mode = fixture_mode
        self.admission_path = admission_path
        self.admission_sha256 = admission_sha256
        self.real_admission = None

    def _require_admission(self):
        if self.fixture_mode:
            if not self.root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
                raise LoopError("synthetic fixture mode requires a temporary checkout")
            return
        if self.manifest["schema"] == "heleos.loop-controller/v2":
            raise LoopError("versioned real queue requires separately reviewed admission and supported independent Codex review delivery")
        if self.manifest["schema"] in ("heleos.loop-controller/v3", "heleos.loop-controller/v4",
                                       "heleos.loop-controller/v5", "heleos.loop-controller/v6"):
            self._require_versioned_admission()
            return
        if self.manifest["schema"] == "heleos.loop-controller/v7":
            self._require_portable_admission()
            return
        if self.admitted_digest != self.manifest_digest:
            raise LoopError("exact manifest admission digest required before dispatch")
        if not self.admission_path or not self.admission_sha256:
            raise LoopError("exact P1 admission packet required before real dispatch")
        admission_file = Path(self.admission_path)
        if (not admission_file.is_absolute() or not admission_file.is_relative_to(self.root)
                or admission_file.is_symlink() or admission_file.resolve() != admission_file):
            raise LoopError("P1 admission packet must be a regular local file")
        raw = admission_file.read_bytes()
        if digest(raw) != self.admission_sha256:
            raise LoopError("P1 admission packet changed")
        try:
            admission = json.loads(raw)
        except ValueError as exc:
            raise LoopError("invalid P1 admission packet") from exc
        required = {"schema", "manifest_sha256", "task_id", "worker_task",
                    "runner_workspace_root", "runner_sha256", "claude_sha256",
                    "worker_argv", "check_argv", "review_argv", "scope",
                    "preparation", "egress_record", "read_boundary",
                    "max_list_price_usd"}
        if (not isinstance(admission, dict) or set(admission) != required
                or admission["schema"] != "heleos.loop-p1-admission/v2"
                or admission["manifest_sha256"] != self.manifest_digest
                or admission["scope"] != "synthetic-only P1 measurement-harness preparation"
                or len(self.manifest["tasks"]) != 1):
            raise LoopError("P1 admission identity or scope differs")
        task = self.manifest["tasks"][0]
        if (task["id"] != admission["task_id"] or task.get("repair")
                or task["candidate_paths"] != ["scripts/experiments/grounding_dino_p1_harness.py",
                                                 "tests/experiments/test_grounding_dino_p1_harness.py"]):
            raise LoopError("P1 queue task or writer scope differs")
        worker = spec_at(self.root, task["worker"], self.manifest["base_head"],
                         self.manifest["allowed_result_paths"])
        check = spec_at(self.root, task["tests"][0], self.manifest["base_head"],
                        self.manifest["allowed_result_paths"])
        review = spec_at(self.root, task["review"], self.manifest["base_head"],
                         self.manifest["allowed_result_paths"])
        if (len(task["tests"]) != 1 or worker["argv"] != admission["worker_argv"]
                or check["argv"] != admission["check_argv"]
                or review["argv"] != admission["review_argv"]
                or worker.get("timeout_seconds", 1) is not None
                or worker.get("cwd") != "."):
            raise LoopError("P1 commands differ from admitted packet")
        prep_ref = admission["preparation"]
        if not isinstance(prep_ref, dict) or set(prep_ref) != {"path", "sha256"}:
            raise LoopError("invalid P1 preparation binding")
        prep_raw = file_at(self.root, prep_ref["path"]).read_bytes()
        if digest(prep_raw) != prep_ref["sha256"]:
            raise LoopError("P1 preparation changed")
        prep = json.loads(prep_raw)
        if (prep.get("base_commit") != self.manifest["base_head"]
                or prep.get("source_proposal", {}).get("status") != "prepared_proposal_not_admitted"):
            raise LoopError("P1 preparation base or proposal differs")
        for item in [prep["source_proposal"], *prep["verified_inputs"]]:
            if (digest(file_at(self.root, item["path"]).read_bytes()) != item["sha256"]
                    or worker.get("inputs", {}).get(item["path"]) != item["sha256"]):
                raise LoopError("P1 frozen public input differs")
        egress_ref = admission["egress_record"]
        if not isinstance(egress_ref, dict) or set(egress_ref) != {"path", "sha256"}:
            raise LoopError("invalid P1 egress record binding")
        egress_raw = file_at(self.root, egress_ref["path"]).read_bytes()
        if digest(egress_raw) != egress_ref["sha256"]:
            raise LoopError("P1 egress decision changed")
        egress = json.loads(egress_raw)
        expected_local = {item["path"]: item["sha256"] for item in
                          [prep["source_proposal"], *prep["verified_inputs"]]}
        if (egress.get("schema") != "heleos.p1-egress-record/v2"
                or egress.get("result") != "preapproved_not_submitted"
                or egress.get("local_pinned_inputs") != expected_local
                or egress.get("startup_context") != {
                    "home_inherited_for_subscription_auth": True,
                    "auto_memory": "disabled_by_provider_environment",
                    "user_claude_md_excluded": str(Path.home() / ".claude" / "CLAUDE.md"),
                    "managed_policy": "No applicable source observed on stated personal Max account and checked local policy paths",
                }):
            raise LoopError("P1 egress record scope differs")
        boundary = admission["read_boundary"]
        if (not isinstance(boundary, dict)
                or set(boundary) != {"native_file_tools", "os_reads_restricted", "evidence"}
                or boundary["native_file_tools"] != "selected-view"
                or boundary["os_reads_restricted"] is not False
                or not isinstance(boundary["evidence"], list)
                or [ref.get("path") if isinstance(ref, dict) else None
                    for ref in boundary["evidence"]] != [
                        "docs/operations/loop-controller-mvp-2026-10-01/native-read-probe-2026-10-01/result.md",
                        "docs/operations/loop-controller-mvp-2026-10-01/native-read-probe-2026-10-01/observations.json",
                        "docs/operations/loop-controller-mvp-2026-10-01/native-read-probe-2026-10-01/native-tool-events.jsonl",
                    ]):
            raise LoopError("P1 native read boundary evidence differs")
        for ref in boundary["evidence"]:
            if (not isinstance(ref, dict) or set(ref) != {"path", "sha256"}
                    or digest(file_at(self.root, ref["path"]).read_bytes()) != ref["sha256"]):
                raise LoopError("P1 native read boundary evidence changed")
        observations = json.loads(file_at(self.root, boundary["evidence"][1]["path"]).read_bytes())
        if (observations.get("runner", {}).get("containment", {}).get("reads_restricted") is not False
                or observations.get("cli", {}).get("mcp_servers") != []
                or observations.get("cli", {}).get("tools") !=
                ["Edit", "Glob", "Grep", "Read", "Workflow", "Write"]
                or observations.get("workflow", {}).get("terminal_status") != "completed"
                or len(observations.get("child_permission_denials", [])) < 3):
            raise LoopError("P1 native read evidence does not support selected tools")
        if admission["max_list_price_usd"] != "5.00":
            raise LoopError("P1 list-price budget differs")
        argv = worker["argv"]
        if not isinstance(argv, list) or len(argv) < 10 or not isinstance(argv[0], str):
            raise LoopError("invalid P1 runner command")
        if argv[:3] != ["/usr/bin/env", "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0",
                        "CLAUDE_CODE_DISABLE_AUTO_MEMORY=1"]:
            raise LoopError("P1 launch-only environment differs")
        argv = argv[3:]
        runner = Path(argv[0])
        if not runner.is_absolute() or digest(runner.read_bytes()) != admission["runner_sha256"]:
            raise LoopError("P1 runner binary changed")
        if runner.name != "heleos-worker-runner":
            raise LoopError("P1 command is not the repository worker runner")
        def option(name):
            if argv.count(name) != 1:
                raise LoopError("missing or duplicate runner option: " + name)
            return argv[argv.index(name) + 1]
        task_ref = admission["worker_task"]
        if (not isinstance(task_ref, dict) or set(task_ref) != {"path", "sha256"}
                or option("--task") != str(self.root / relpath(task_ref["path"]))
                or option("--source") != str(self.root)
                or option("--workspace-root") != admission["runner_workspace_root"]
                or option("--provider") != "claude_code"
                or option("--containment") != "macos_seatbelt"
                or option("--approved-internal-task-sha256") != task_ref["sha256"]
                or argv.count("--inherit-env") != 6
                or [argv[i + 1] for i, value in enumerate(argv[:-1])
                    if value == "--inherit-env"] !=
                    ["HOME", "USER", "LOGNAME", "SHELL", "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS",
                     "CLAUDE_CODE_DISABLE_AUTO_MEMORY"]
                or "--cleanup-on-failure" in argv):
            raise LoopError("P1 runner profile or task binding differs")
        worker_task_raw = file_at(self.root, task_ref["path"]).read_bytes()
        worker_task = json.loads(worker_task_raw)
        if (digest(worker_task_raw) != task_ref["sha256"]
                or worker.get("inputs", {}).get(task_ref["path"]) != task_ref["sha256"]
                or worker_task.get("schema") != "heleos.worker-task/v1"
                or worker_task.get("provider") != "claude_code"
                or worker_task.get("mode") != "implementation"
                or worker_task.get("base_commit") != self.manifest["base_head"]
                or worker_task.get("allowed_paths") != task["candidate_paths"]
                or worker_task.get("input_data_class") != "INTERNAL"
                or worker_task.get("egress_policy") != "approved_external"
                or worker_task.get("limits", {}).get("max_actions") != 100
                or worker_task.get("limits", {}).get("max_duration_seconds", 1) is not None):
            raise LoopError("P1 worker task or egress differs")
        selected = worker_task.get("instruction_sha256")
        if not isinstance(selected, dict) or set(selected) != P1_SELECTED_SOURCE_PATHS:
            raise LoopError("P1 selected source set differs")
        for path, expected in selected.items():
            if (worker.get("inputs", {}).get(path) != expected
                    or digest(file_at(self.root, path).read_bytes()) != expected):
                raise LoopError("P1 instruction input differs")
        if (egress.get("source_sha256") != worker_task["instruction_sha256"]
                or egress.get("task_sha256") != task_ref["sha256"]):
            raise LoopError("P1 provider egress identities differ")
        claude = Path(option("--command"))
        if not claude.is_absolute() or digest(claude.read_bytes()) != admission["claude_sha256"]:
            raise LoopError("Claude executable changed")
        if option("--git") != "/usr/bin/git":
            raise LoopError("P1 Git executable differs")
        if "--" not in argv:
            raise LoopError("P1 provider arguments missing")
        settings = json.loads(file_at(
            self.root, "docs/operations/claude-opus-5-5-ultracode.json").read_bytes())
        settings["claudeMdExcludes"] = [str(Path.home() / ".claude" / "CLAUDE.md")]
        expected_settings = json.dumps(settings, indent=2, sort_keys=True,
                                       ensure_ascii=False) + "\n"
        provider_args = argv[argv.index("--") + 1:]
        expected_provider_args = [
            "-p", "--output-format", "json", "--no-session-persistence",
            "--model", "claude-opus-5-5", "--effort", "ultracode", "--restricted",
            "--max-budget-usd", "5.00", "--strict-mcp-config", "--no-chrome",
            "--setting-sources", "", "--permission-mode", "acceptEdits",
            "--permission-prompts", "none", "--tools", "Read,Glob,Grep,Edit,Write,Workflow",
            "--allowedTools", "Workflow", "--settings", expected_settings,
        ]
        if provider_args != expected_provider_args:
            raise LoopError("P1 Claude profile or startup exclusions differ")
        expected_runner_argv = [
            str(runner), "--task", str(self.root / relpath(task_ref["path"])),
            "--source", str(self.root), "--workspace-root", admission["runner_workspace_root"],
            "--provider", "claude_code", "--command", str(claude), "--git", "/usr/bin/git",
            "--containment", "macos_seatbelt", "--max-prompt-bytes", "262144",
            "--max-output-bytes", "1048576",
            *[value for path, file_hash in sorted(selected.items())
              for value in ("--selected-source-file", path + "=" + file_hash)],
            "--approved-internal-task-sha256", task_ref["sha256"],
            *[value for name in ("HOME", "USER", "LOGNAME", "SHELL",
                                "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS",
                                "CLAUDE_CODE_DISABLE_AUTO_MEMORY")
              for value in ("--inherit-env", name)],
        ]
        if argv[:argv.index("--")] != expected_runner_argv:
            raise LoopError("P1 selected-source runner command differs")
        if not isinstance(admission["runner_workspace_root"], str):
            raise LoopError("portable runner workspace is malformed")
        workspace = Path(admission["runner_workspace_root"])
        if not workspace.is_absolute() or not workspace.is_dir() or workspace.resolve() != workspace \
                or workspace == self.root or workspace.is_relative_to(self.root):
            raise LoopError("invalid P1 runner workspace")
        # Seatbelt still allows reads. The narrower admission is for the pinned
        # selected view and Claude's restricted native file tools only.
        self.real_admission = admission

    def _require_versioned_admission(self):
        """Admit only exact synthetic v3-v6 profiles; never infer commands from the queue."""
        version = self.manifest["schema"].rsplit("/", 1)[1]
        audit = version in ("v4", "v5", "v6")
        seed_hashes = {"v4": P1_REPAIRED_SEED_SHA256,
                       "v5": P1_FINAL_REPAIRED_SEED_SHA256,
                       "v6": P1_V6_REPAIRED_SEED_SHA256}.get(version, {})
        audit_wrapper = {"v4": P1_AUDIT_WRAPPER,
                         "v5": P1_FINAL_AUDIT_WRAPPER,
                         "v6": P1_V6_AUDIT_WRAPPER}.get(version)
        audit_id = {"v3": "003", "v4": "004", "v5": "005", "v6": "006"}[version]
        if self.admitted_digest != self.manifest_digest or not self.admission_path or not self.admission_sha256:
            raise LoopError("exact versioned admission digest required before dispatch")
        admission_path = Path(self.admission_path)
        if (not admission_path.is_absolute() or not admission_path.is_relative_to(self.root)
                or admission_path.is_symlink() or admission_path.resolve() != admission_path):
            raise LoopError("versioned admission path is unsafe")
        raw = admission_path.read_bytes()
        if digest(raw) != self.admission_sha256:
            raise LoopError("versioned admission changed")
        try:
            admission = json.loads(raw)
        except ValueError as exc:
            raise LoopError("invalid versioned admission") from exc
        required = {"schema", "manifest_sha256", "task_id", "worker_task", "workflow_script",
                    "preparation", "egress_record", "read_boundary", "operational_limits",
                    "review_delivery", "runner_workspace_root", "runner_sha256", "claude_sha256",
                    "worker_argv", "check_argv", "review_argv", "scope"}
        if audit:
            required |= {"seed_wrapper", "seeded_candidate_sha256", "check_python_sha256"}
        if (not isinstance(admission, dict) or set(admission) != required
                or admission["schema"] != "heleos.loop-p1-admission/" + version
                or admission["manifest_sha256"] != self.manifest_digest
                or admission["scope"] != ("synthetic-only P1 repaired-harness native audit" if audit
                                          else "synthetic-only P1 measurement-harness preparation")
                or admission["review_delivery"] != "parent_supervised_independent_codex_source"
                or len(self.manifest["tasks"]) != 1):
            raise LoopError("versioned admission identity or scope differs")
        task = self.manifest["tasks"][0]
        trial = "docs/operations/loop-controller-mvp-2026-10-01/p1-trial-" + version
        candidate_root = ("docs/operations/loop-controller-mvp-2026-10-01/"
                          + ("p1-trial-v5-offline-repair/candidate" if version == "v6" else
                             "p1-trial-v4-offline-repair/candidate" if version == "v5" else
                             "p1-trial-v3-offline-repair/candidate") if audit else trial + "/candidate")
        code = [candidate_root + "/scripts/experiments/grounding_dino_p1_harness.py",
                candidate_root + "/tests/experiments/test_grounding_dino_p1_harness.py"]
        child = candidate_root + "/child-review.json"
        candidates = sorted([*code, child])
        check_path, source_path, review_path = (trial + "/check-placeholder.txt",
                                                trial + "/independent-review.json",
                                                trial + "/review.json")
        workflow = task["native_workflow"]
        if (task["id"] != ("p1-synthetic-harness-native-audit-" + audit_id if audit
                           else "p1-synthetic-harness-preparation-native")
                or admission["task_id"] != task["id"] or task.get("repair")
                or task["candidate_paths"] != candidates
                or task["review_source_path"] != source_path or task["review_path"] != review_path
                or task["reviewer_identity"] != "codex-independent-p1-review"
                or self.manifest["allowed_result_paths"] !=
                [*candidates, check_path, source_path, review_path]
                or workflow["child_label"] != "p1-harness-audit"
                or workflow["child_report_path"] != child):
            raise LoopError("versioned task or candidate scope differs")
        script_ref = admission["workflow_script"]
        script_path = ("docs/operations/loop-controller-mvp-2026-10-01/"
                       "p1-native-workflow-" + version + ".js")
        if (not isinstance(script_ref, dict) or set(script_ref) !=
                {"path", "sha256", "called_script_sha256"} or script_ref["path"] != script_path):
            raise LoopError("versioned Workflow script reference differs")
        script_bytes = file_at(self.root, script_path).read_bytes()
        script = script_bytes.decode().rstrip("\n")
        if (digest(script_bytes) != script_ref["sha256"]
                or digest(script.encode()) != script_ref["called_script_sha256"]
                or workflow["script_sha256"] != script_ref["called_script_sha256"]):
            raise LoopError("versioned Workflow script changed")
        limits = {"worker_task_max_actions_declarative": 100,
                  "worker_max_duration_seconds": None, "guard_worker_timeout_seconds": None,
                  "prompt_bytes": 262144, "output_bytes": 1048576,
                  "check_timeout_seconds": 300, "review_timeout_seconds": 300,
                  "controller_max_actions": 10000, "controller_max_seconds": 86400,
                  "controller_poll_seconds": 10}
        if audit:
            limits.update({"typed_trace": "incremental_jsonl_no_aggregate_cap",
                           "per_record_bytes": 1048576, "post_exit_drain_seconds": 30})
        if admission["operational_limits"] != limits:
            raise LoopError("versioned operational limits differ")
        task_ref = admission["worker_task"]
        if (not isinstance(task_ref, dict) or set(task_ref) != {"path", "sha256"}
                or task_ref["path"] !=
                "docs/operations/loop-controller-mvp-2026-10-01/p1-worker-task-" + version + ".json"):
            raise LoopError("versioned worker task reference differs")
        task_bytes = file_at(self.root, task_ref["path"]).read_bytes()
        if digest(task_bytes) != task_ref["sha256"]:
            raise LoopError("versioned worker task changed")
        worker_task = json.loads(task_bytes)
        if (worker_task.get("schema") != "heleos.worker-task/v1"
                or worker_task.get("task_id") != ("loop-p1-harness-native-audit-" + audit_id if audit
                                                    else "loop-p1-harness-prep-native-003")
                or worker_task.get("provider") != "claude_code"
                or worker_task.get("mode") != "implementation"
                or worker_task.get("base_commit") != self.manifest["base_head"]
                or worker_task.get("allowed_paths") != candidates
                or worker_task.get("input_data_class") != "INTERNAL"
                or worker_task.get("egress_policy") != "approved_external"
                or worker_task.get("limits") != {"max_actions": 100, "max_duration_seconds": None}
                or worker_task.get("objective", "").count(script) != 1):
            raise LoopError("versioned worker task or Workflow objective differs")
        selected = worker_task.get("instruction_sha256")
        if not isinstance(selected, dict) or set(selected) != P1_SELECTED_SOURCE_PATHS:
            raise LoopError("versioned selected source set differs")
        for path, expected in selected.items():
            committed = subprocess.run(["git", "-C", str(self.root), "show",
                                        "HEAD:" + path], capture_output=True)
            if (digest(file_at(self.root, path).read_bytes()) != expected
                    or committed.returncode or digest(committed.stdout) != expected):
                raise LoopError("versioned selected source changed: " + path)
        prep_ref = admission["preparation"]
        if (not isinstance(prep_ref, dict) or set(prep_ref) != {"path", "sha256"}
                or prep_ref["path"] !=
                "docs/operations/loop-controller-mvp-2026-10-01/p1-pilot-preparation.json"):
            raise LoopError("versioned preparation reference differs")
        prep_raw = file_at(self.root, prep_ref["path"]).read_bytes()
        if digest(prep_raw) != prep_ref["sha256"]:
            raise LoopError("versioned preparation changed")
        prep = json.loads(prep_raw)
        if (prep.get("base_commit") != self.manifest["base_head"]
                or prep.get("source_proposal", {}).get("status") != "prepared_proposal_not_admitted"):
            raise LoopError("versioned preparation scope differs")
        local_inputs = {item["path"]: item["sha256"] for item in
                        [prep["source_proposal"], *prep["verified_inputs"]]}
        for path, expected in local_inputs.items():
            if digest(file_at(self.root, path).read_bytes()) != expected:
                raise LoopError("versioned frozen public input changed: " + path)
        common_inputs = {**local_inputs, prep_ref["path"]: prep_ref["sha256"], **selected}
        seed_inputs = {}
        if audit:
            wrapper_ref = admission["seed_wrapper"]
            if (not isinstance(wrapper_ref, dict) or set(wrapper_ref) != {"path", "sha256"}
                    or wrapper_ref["path"] != audit_wrapper
                    or digest(file_at(self.root, audit_wrapper).read_bytes()) != wrapper_ref["sha256"]
                    or admission["seeded_candidate_sha256"] != seed_hashes
                    or set(seed_hashes) != set(code)):
                raise LoopError("exact repaired seed or wrapper differs")
            for path, expected in seed_hashes.items():
                if digest(file_at(self.root, path).read_bytes()) != expected:
                    raise LoopError("repaired seed bytes changed: " + path)
            if digest(Path(P1_CHECK_PYTHON).read_bytes()) != admission["check_python_sha256"]:
                raise LoopError("pinned synthetic check interpreter differs")
            seed_inputs = {**seed_hashes, audit_wrapper: wrapper_ref["sha256"]}
        egress_ref = admission["egress_record"]
        if (not isinstance(egress_ref, dict) or set(egress_ref) != {"path", "sha256"}
                or egress_ref["path"] !=
                "docs/operations/loop-controller-mvp-2026-10-01/p1-egress-record-" + version + ".json"):
            raise LoopError("versioned egress reference differs")
        egress_raw = file_at(self.root, egress_ref["path"]).read_bytes()
        if digest(egress_raw) != egress_ref["sha256"]:
            raise LoopError("versioned egress record changed")
        egress = json.loads(egress_raw)
        if (egress.get("schema") != "heleos.p1-egress-record/" + version
                or egress.get("result") != "preapproved_not_submitted"
                or egress.get("source_sha256") != selected
                or egress.get("local_pinned_inputs") != local_inputs
                or egress.get("inline_workflow_script_sha256") != workflow["script_sha256"]
                or egress.get("task_sha256") != task_ref["sha256"]
                or egress.get("excluded") != ["private drawings", "bid packages", "credentials",
                                               "unrelated plans", "hidden assistant instructions"]):
            raise LoopError("versioned egress scope differs")
        if audit and (egress.get("seed_source_sha256") != seed_hashes
                      or egress.get("launch_wrapper_sha256") != seed_inputs[audit_wrapper]):
            raise LoopError("audit source egress scope differs")
        boundary = admission["read_boundary"]
        expected_boundary_paths = [
            "docs/operations/loop-controller-mvp-2026-10-01/native-read-probe-2026-10-01/result.md",
            "docs/operations/loop-controller-mvp-2026-10-01/native-read-probe-2026-10-01/observations.json",
            "docs/operations/loop-controller-mvp-2026-10-01/native-read-probe-2026-10-01/native-tool-events.jsonl"]
        if (not isinstance(boundary, dict) or set(boundary) !=
                {"native_file_tools", "os_reads_restricted", "evidence"}
                or boundary["native_file_tools"] != "selected-view"
                or boundary["os_reads_restricted"] is not False
                or [item.get("path") for item in boundary["evidence"]] != expected_boundary_paths):
            raise LoopError("versioned native read boundary differs")
        for item in boundary["evidence"]:
            if digest(file_at(self.root, item["path"]).read_bytes()) != item["sha256"]:
                raise LoopError("versioned native read evidence changed")
        worker = spec_at(self.root, task["worker"], self.manifest["base_head"],
                         self.manifest["allowed_result_paths"])
        check = spec_at(self.root, task["tests"][0], self.manifest["base_head"],
                        self.manifest["allowed_result_paths"])
        review = spec_at(self.root, task["review"], self.manifest["base_head"],
                         self.manifest["allowed_result_paths"])
        if (len(task["tests"]) != 1 or worker["task_id"] != "p1-controller-worker-native-" + audit_id
                or check["task_id"] != "p1-controller-check-native-" + audit_id
                or review["task_id"] != "p1-controller-review-native-" + audit_id
                or worker["kind"] != "worker" or check["kind"] != "test"
                or review["kind"] != "test" or worker["allowed_paths"] != candidates
                or check["allowed_paths"] != [check_path]
                or review["allowed_paths"] != [review_path]
                or worker["timeout_seconds"] is not None
                or check["timeout_seconds"] != 300 or review["timeout_seconds"] != 300
                or any(x["cwd"] != "." or x["max_output_bytes"] != 1048576
                       for x in (worker, check, review))
                or worker["inputs"] != {**common_inputs, **seed_inputs,
                                        task_ref["path"]: task_ref["sha256"]}
                or check["inputs"] != {**common_inputs,
                                       **seed_hashes}
                or review["inputs"] != {**common_inputs,
                    **seed_hashes,
                    "scripts/loop_p1_review_gate.py": digest(file_at(
                        self.root, "scripts/loop_p1_review_gate.py").read_bytes())}):
            raise LoopError("versioned guard scope or inputs differ")
        runner = self.root / "target/debug/heleos-worker-runner"
        claude = Path.home() / ".local" / "node" / "bin" / "claude"
        workspace = Path(admission["runner_workspace_root"])
        if (not workspace.is_absolute() or not workspace.is_dir()
                or workspace.resolve() != workspace or workspace.is_relative_to(self.root)
                or digest(runner.read_bytes()) != admission["runner_sha256"]
                or digest(claude.read_bytes()) != admission["claude_sha256"]):
            raise LoopError("versioned runner, Claude or workspace changed")
        settings = json.loads(file_at(
            self.root, "docs/operations/claude-opus-5-5-ultracode.json").read_bytes())
        settings["claudeMdExcludes"] = [str(Path.home() / ".claude" / "CLAUDE.md")]
        expected_settings = json.dumps(settings, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        provider_args = ["-p", "--output-format", "stream-json", "--verbose",
                         "--forward-subagent-text", "--no-session-persistence",
                         "--model", "claude-opus-5-5", "--effort", "ultracode", "--restricted",
                         "--strict-mcp-config", "--no-chrome", "--setting-sources", "",
                         "--permission-mode", "acceptEdits", "--permission-prompts", "none",
                         "--tools", ("Read,Glob,Grep,Write,Workflow" if audit else
                                     "Read,Glob,Grep,Edit,Write,Workflow"), "--allowedTools", "Workflow",
                         "--settings", expected_settings]
        runner_args = [str(runner), "--task", str(self.root / task_ref["path"]),
                       "--source", str(self.root), "--workspace-root", str(workspace),
                       "--provider", "claude_code", "--command",
                       ("/usr/bin/python3" if audit else str(claude)),
                       "--git", "/usr/bin/git", "--containment", "macos_seatbelt",
                       *(["--capture-claude-stream-trace"] if audit else []),
                       "--max-prompt-bytes", "262144", "--max-output-bytes", "1048576",
                       *[value for path, file_hash in sorted(selected.items())
                         for value in ("--selected-source-file", path + "=" + file_hash)],
                       "--approved-internal-task-sha256", task_ref["sha256"],
                       *[value for name in ("HOME", "USER", "LOGNAME", "SHELL",
                                             "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS",
                                             "CLAUDE_CODE_DISABLE_AUTO_MEMORY")
                         for value in ("--inherit-env", name)], "--",
                       *([str(self.root / audit_wrapper)] if audit else []), *provider_args]
        expected_worker_argv = ["/usr/bin/env", "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0",
                                "CLAUDE_CODE_DISABLE_AUTO_MEMORY=1", *runner_args]
        expected_check_argv = [(P1_CHECK_PYTHON if audit else sys.executable), "-B", "-m",
                               "unittest", "discover", "-s",
                               candidate_root + "/tests/experiments", "-p",
                               "test_grounding_dino_p1_harness.py", "-v"]
        expected_review_argv = [sys.executable, "-B", "scripts/loop_p1_review_gate.py",
                                "--source", str(self.root / source_path),
                                "--output", str(self.root / review_path)]
        if (worker["argv"] != expected_worker_argv or admission["worker_argv"] != expected_worker_argv
                or check["argv"] != expected_check_argv or admission["check_argv"] != expected_check_argv
                or review["argv"] != expected_review_argv
                or admission["review_argv"] != expected_review_argv):
            raise LoopError("versioned commands differ from reviewed profile")
        self.real_admission = admission

    def _require_portable_admission(self):
        """Bind a reviewed task packet and contained runner for every v7 task."""
        if self.admitted_digest != self.manifest_digest or not self.admission_path or not self.admission_sha256:
            raise LoopError("exact portable admission digest required before dispatch")
        admission_path = Path(self.admission_path)
        if (not admission_path.is_absolute() or not admission_path.is_relative_to(self.root)
                or admission_path.is_symlink() or admission_path.resolve() != admission_path):
            raise LoopError("portable admission path is unsafe")
        raw = admission_path.read_bytes()
        if digest(raw) != self.admission_sha256:
            raise LoopError("portable admission changed")
        try:
            admission = json.loads(raw)
        except ValueError as exc:
            raise LoopError("invalid portable admission JSON") from exc
        keys = {"schema", "manifest_sha256", "scope", "runner_workspace_root", "runner_sha256",
                "provider_sha256", "worker_tasks", "worker_argv", "egress_record"}
        tasks = self.manifest["tasks"]
        ids = {task["id"] for task in tasks}
        if (not isinstance(admission, dict) or set(admission) != keys
                or admission["schema"] != "heleos.loop-admission/v1"
                or admission["manifest_sha256"] != self.manifest_digest
                or not isinstance(admission["scope"], str) or not admission["scope"].strip()
                or not isinstance(admission["worker_tasks"], dict)
                or set(admission["worker_tasks"]) != ids
                or not isinstance(admission["worker_argv"], dict)
                or set(admission["worker_argv"]) != ids):
            raise LoopError("portable admission identity or task set differs")
        if not isinstance(admission["runner_workspace_root"], str):
            raise LoopError("portable runner workspace is malformed")
        workspace = Path(admission["runner_workspace_root"])
        if (not workspace.is_absolute() or not workspace.is_dir() or workspace.is_symlink()
                or workspace.resolve() != workspace or workspace == self.root
                or workspace.is_relative_to(self.root)):
            raise LoopError("portable runner workspace is unsafe")
        egress_ref = admission["egress_record"]
        if not isinstance(egress_ref, dict) or set(egress_ref) != {"path", "sha256"}:
            raise LoopError("portable egress proof is malformed")
        egress_raw = file_at(self.root, egress_ref["path"]).read_bytes()
        if digest(egress_raw) != egress_ref["sha256"]:
            raise LoopError("portable egress proof changed")
        try:
            egress = json.loads(egress_raw)
        except ValueError as exc:
            raise LoopError("portable egress proof is invalid JSON") from exc
        if (not isinstance(egress, dict) or egress.get("schema") != "heleos.loop-egress/v1"
                or egress.get("decision") != "approved"
                or egress.get("provider") != "claude_code"
                or not isinstance(egress.get("task_ids"), list)
                or any(not isinstance(item, str) for item in egress["task_ids"])
                or set(egress["task_ids"]) != ids
                or not isinstance(egress.get("source_sha256"), dict)
                or set(egress["source_sha256"]) != ids):
            raise LoopError("portable provider egress is not approved for this queue")
        for task in tasks:
            task_id = task["id"]
            ref = admission["worker_tasks"][task_id]
            if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
                raise LoopError("portable worker task proof is malformed")
            task_raw = file_at(self.root, ref["path"]).read_bytes()
            if digest(task_raw) != ref["sha256"]:
                raise LoopError("portable worker task changed")
            try:
                packet = json.loads(task_raw)
            except ValueError as exc:
                raise LoopError("portable worker task is invalid JSON") from exc
            if not isinstance(packet, dict):
                raise LoopError("portable worker task is not an object")
            selected = packet.get("instruction_sha256")
            if (packet.get("schema") != "heleos.worker-task/v1" or packet.get("provider") != "claude_code"
                    or packet.get("mode") != "implementation"
                    or packet.get("task_id") != task_id
                    or packet.get("base_commit") != self.manifest["base_head"]
                    or packet.get("allowed_paths") != task["candidate_paths"]
                    or packet.get("egress_policy") != "approved_external"
                    or packet.get("input_data_class") not in ("PUBLIC", "INTERNAL")
                    or not isinstance(selected, dict) or not selected
                    or any(not isinstance(path, str) for path in selected)
                    or not isinstance(packet.get("limits"), dict)
                    or type(packet["limits"].get("max_actions")) is not int
                    or not 1 <= packet["limits"]["max_actions"] <= 100
                    or packet["limits"].get("max_duration_seconds", 1) is not None):
                raise LoopError("portable worker task or limits differ")
            worker = spec_at(self.root, task["worker"], self.manifest["base_head"],
                             self.manifest["allowed_result_paths"])
            if not isinstance(worker.get("inputs"), dict):
                raise LoopError("portable worker inputs are malformed")
            if worker.get("timeout_seconds", 1) is not None or worker.get("cwd") != ".":
                raise LoopError("portable worker guard has a deadline or different cwd")
            if (worker.get("inputs", {}).get(ref["path"]) != ref["sha256"]
                    or egress["source_sha256"][task_id] != selected):
                raise LoopError("portable worker task or egress source differs")
            for path, expected in selected.items():
                if (not isinstance(expected, str) or len(expected) != 64
                        or worker.get("inputs", {}).get(path) != expected
                        or digest(file_at(self.root, path).read_bytes()) != expected):
                    raise LoopError("portable selected source differs")
            argv = admission["worker_argv"][task_id]
            if (not isinstance(argv, list) or not argv
                    or any(not isinstance(arg, str) for arg in argv)
                    or argv != worker.get("argv")):
                raise LoopError("portable worker command differs")
            env_wrapper = ["/usr/bin/env", "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0",
                           "CLAUDE_CODE_DISABLE_AUTO_MEMORY=1"]
            if argv[:3] != env_wrapper:
                raise LoopError("portable provider environment wrapper differs")
            runner_argv = argv[3:]
            if not runner_argv or not isinstance(runner_argv[0], str):
                raise LoopError("portable runner command missing")
            runner = Path(runner_argv[0])
            if (not runner.is_absolute() or runner.name != "heleos-worker-runner"
                    or not runner.is_file() or runner.is_symlink() or not os.access(runner, os.X_OK)
                    or digest(runner.read_bytes()) != admission["runner_sha256"]):
                raise LoopError("portable runner binary differs")
            if runner_argv.count("--") != 1:
                raise LoopError("portable runner provider separator differs")
            before_provider, provider_argv = runner_argv[:runner_argv.index("--")], \
                runner_argv[runner_argv.index("--") + 1:]
            if before_provider.count("--command") != 1:
                raise LoopError("portable provider executable is missing")
            command_index = before_provider.index("--command") + 1
            if command_index >= len(before_provider):
                raise LoopError("portable provider executable is missing")
            provider = Path(before_provider[command_index])
            if (not provider.is_absolute() or not provider.is_file() or provider.is_symlink()
                    or not os.access(provider, os.X_OK)
                    or digest(provider.read_bytes()) != admission["provider_sha256"]):
                raise LoopError("portable provider executable differs")
            inherit = ["HOME", "USER", "LOGNAME", "SHELL",
                       "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS", "CLAUDE_CODE_DISABLE_AUTO_MEMORY"]
            expected_runner_argv = [
                str(runner), "--task", str(self.root / relpath(ref["path"])),
                "--source", str(self.root), "--workspace-root", str(workspace),
                "--provider", "claude_code", "--command", str(provider),
                "--git", "/usr/bin/git", "--containment", "macos_seatbelt",
                "--capture-claude-stream-trace", "--max-prompt-bytes", "262144",
                "--max-output-bytes", "1048576",
                *[value for path, file_hash in sorted(selected.items())
                  for value in ("--selected-source-file", path + "=" + file_hash)],
                *(["--approved-internal-task-sha256", ref["sha256"]]
                  if packet["input_data_class"] == "INTERNAL" else []),
                *[value for name in inherit for value in ("--inherit-env", name)],
            ]
            if before_provider != expected_runner_argv or provider_argv != portable_provider_argv():
                raise LoopError("portable runner or provider command differs from reviewed profile")
        self.real_admission = admission

    def _connect(self, create=True):
        if self.db.parent.is_symlink() or self.db.is_symlink():
            raise LoopError("symlink trial ledger path")
        if not create and not self.db.exists():
            return None
        if create:
            self.db.parent.mkdir(exist_ok=True)
            if self.db.parent.resolve() != self.root / ".loop-trial":
                raise LoopError("trial ledger escaped pilot worktree")
        con = (sqlite3.connect(self.db, isolation_level=None, timeout=10) if create else
               sqlite3.connect("file:" + str(self.db) + "?mode=ro", uri=True,
                               isolation_level=None, timeout=10))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        if not create:
            try:
                prior = con.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()
            except sqlite3.Error as exc:
                con.close()
                raise LoopError("trial ledger has no manifest identity") from exc
            if not prior or prior[0] != self.manifest_digest:
                con.close()
                raise LoopError("admitted manifest changed; preserve ledger")
        if create:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(
                  id TEXT PRIMARY KEY, state TEXT NOT NULL, role TEXT, step_index INTEGER NOT NULL DEFAULT 0,
                  guard_id TEXT, repair_used INTEGER NOT NULL DEFAULT 0, candidates TEXT,
                  checks TEXT NOT NULL DEFAULT '[]', reason TEXT, launch_at REAL,
                  review_sha256 TEXT, runner_evidence TEXT);
                CREATE TABLE IF NOT EXISTS events(
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, task_id TEXT NOT NULL,
                  from_state TEXT, to_state TEXT NOT NULL, guard_id TEXT, reason TEXT NOT NULL,
                  manifest_digest TEXT NOT NULL, evidence_json TEXT NOT NULL);
            """)
            con.execute("BEGIN IMMEDIATE")
            prior = con.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()
            if prior and prior[0] != self.manifest_digest:
                con.rollback()
                con.close()
                raise LoopError("admitted manifest changed; preserve ledger")
            if not prior:
                con.execute("INSERT INTO meta VALUES ('manifest',?)", (self.manifest_digest,))
                for task in self.manifest["tasks"]:
                    con.execute("INSERT INTO tasks(id,state) VALUES (?, 'queued')", (task["id"],))
            con.commit()
        return con

    def _move(self, con, row, state, why, **updates):
        fields = {"state": state, **updates}
        con.execute("UPDATE tasks SET " + ", ".join(k + "=?" for k in fields) + " WHERE id=?",
                    (*fields.values(), row["id"]))
        con.execute("INSERT INTO events(at,task_id,from_state,to_state,guard_id,reason,manifest_digest,evidence_json) "
                    "VALUES (datetime('now'),?,?,?,?,?,?,?)",
                    (row["id"], row["state"], state, updates.get("guard_id", row["guard_id"]),
                     why, self.manifest_digest, json.dumps(updates, sort_keys=True)))

    @staticmethod
    def _next_action(state, reason):
        if state == "uncertain":
            return "Inspect the recorded guard process and obtain independent incident reconciliation; do not dispatch another ID."
        if state == "review_pending":
            return "Await the named independent review source; rerun the bounded foreground controller to receive it."
        if state == "decision_wait":
            if reason and "native Workflow" in reason:
                return "Retain this incomplete candidate; admit a new native Workflow child assignment with exact source, evidence and guard ID."
            if reason and "review" in reason:
                return "Ask the named independent reviewer to resolve the evidence, or obtain an owner decision."
            if reason and "input" in reason:
                return "Restore the pinned input or admit a new exact manifest and task identity."
            if reason and "artifact" in reason:
                return "Inspect the worker result and supply the missing required artifact under a new admitted task."
            return "Inspect the guard failure and admit a changed-condition repair or obtain an owner decision."
        return None

    def status(self):
        con = self._connect(create=False)
        if con is None:
            return {"tasks": {t["id"]: "queued" for t in self.manifest["tasks"]},
                    "details": {}, "reason": "not_started"}
        try:
            rows = con.execute("SELECT * FROM tasks").fetchall()
            return {"tasks": {r["id"]: r["state"] for r in rows},
                    "details": {r["id"]: {"guard_id": r["guard_id"], "reason": r["reason"],
                                           "repair_used": bool(r["repair_used"]),
                                           "review_sha256": r["review_sha256"],
                                           "runner_evidence": json.loads(r["runner_evidence"] or "null"),
                                           "next_action": self._next_action(r["state"], r["reason"])}
                                for r in rows}}
        finally:
            con.close()

    def _row(self, con):
        rows = {r["id"]: r for r in con.execute("SELECT * FROM tasks")}
        for t in self.manifest["tasks"]:
            r = rows[t["id"]]
            if r["state"] in ("decision_wait", "failed", "uncertain"):
                return r
            if r["state"] not in ("accepted", "queued"):
                return r
            if r["state"] == "queued" and all(rows[d]["state"] == "accepted" for d in t["depends_on"]):
                return r
        return None

    def _intent(self, con, row, role, ref):
        spec = spec_at(self.root, ref, self.manifest["base_head"], self.manifest["allowed_result_paths"])
        self._move(con, row, "dispatch_intent", "pinned step ready", role=role, guard_id=spec["task_id"])

    def _candidate_hashes(self, task):
        return {p: digest(file_at(self.root, p).read_bytes()) for p in task["candidate_paths"]}

    def _review_absent(self, task):
        return not file_at(self.root, task["review_path"], required=False).exists()

    def _review_source(self, task, row):
        path = file_at(self.root, task["review_source_path"], required=False)
        if not path.exists():
            return None
        try:
            report = json.loads(path.read_bytes())
            expected = {"decision": "accepted", "reviewer": task["reviewer_identity"],
                        "candidate_sha256": json.loads(row["candidates"]),
                        "checks": json.loads(row["checks"]),
                        "runner_evidence": json.loads(row["runner_evidence"])}
            if report != expected:
                raise ValueError("independent review source does not bind candidate, checks and runner")
        except (ValueError, TypeError, OSError) as exc:
            raise LoopError("independent review source rejected or mismatched") from exc
        return path

    def _candidate_unchanged(self, task, row):
        try:
            return self._candidate_hashes(task) == json.loads(row["candidates"])
        except (LoopError, ValueError, TypeError):
            return False

    def _checks(self, task, row):
        return task["repair"]["tests"] if row["repair_used"] else task["tests"]

    def _review(self, task, row):
        return task["repair"]["review"] if row["repair_used"] else task["review"]

    def _failure(self, con, row, task, why):
        repair = task.get("repair")
        if repair and not row["repair_used"] and repair["on_guard_id"] == row["guard_id"]:
            sid = spec_at(self.root, repair["spec"], self.manifest["base_head"],
                          self.manifest["allowed_result_paths"])["task_id"]
            self._move(con, row, "dispatch_intent", "linked repair: " + why,
                       role="repair", guard_id=sid, repair_used=1, reason=why)
        else:
            self._move(con, row, "decision_wait", why, reason=why)

    def _success(self, con, row, task, record):
        if row["role"] in ("worker", "repair"):
            if not self._review_absent(task):
                self._move(con, row, "decision_wait", "review report existed before review run",
                           reason="review report has no independent run provenance; inspect retained artifact")
                return
            try:
                evidence = None
                if not self.fixture_mode:
                    task_ref = (self.real_admission["worker_tasks"][task["id"]]
                                if self.manifest["schema"] == "heleos.loop-controller/v7"
                                else self.real_admission["worker_task"])
                    worker_task_bytes = file_at(self.root, task_ref["path"]).read_bytes()
                    evidence = import_candidate(
                        {**record, **({"required_workflow": task["native_workflow"]}
                                    if "native_workflow" in task else {})}, task_bytes=worker_task_bytes,
                        workspace_root=self.real_admission["runner_workspace_root"],
                        destination=self.root, expected_paths=task["candidate_paths"],
                        expected_selected_source_sha256=json.loads(worker_task_bytes)[
                            "instruction_sha256"])
                elif "native_workflow" in task:
                    candidate = {p: file_at(self.root, p).read_bytes() for p in task["candidate_paths"]}
                    child = task["native_workflow"]["child_report_path"]
                    workflow = native_workflow_evidence(record.get("workflow_stream", b""),
                                                        candidate[child], candidate,
                                                        task["native_workflow"])
                    evidence = {"candidate_sha256": {p: digest(raw) for p, raw in candidate.items()},
                                "native_workflow": workflow}
                candidates = self._candidate_hashes(task)
                if evidence is not None and evidence.get("candidate_sha256") != candidates:
                    raise HandoffError("candidate changed after runner handoff")
                if (self.real_admission is not None
                        and self.real_admission.get("schema") in
                        ("heleos.loop-p1-admission/v4", "heleos.loop-p1-admission/v5",
                         "heleos.loop-p1-admission/v6")
                        and {path: candidates.get(path) for path in
                             self.real_admission["seeded_candidate_sha256"]}
                        != self.real_admission["seeded_candidate_sha256"]):
                    raise HandoffError("audited code differs from exact repaired seed")
            except (LoopError, HandoffError, OSError, ValueError, KeyError, TypeError) as exc:
                self._move(con, row, "decision_wait", str(exc), reason=str(exc))
                return
            self._move(con, row, "checks_pending", "candidate artifacts recorded",
                       candidates=json.dumps(candidates, sort_keys=True), checks="[]",
                       step_index=0, role=None, guard_id=None,
                       runner_evidence=json.dumps(evidence, sort_keys=True) if evidence else None)
        elif row["role"] == "test":
            if not self._candidate_unchanged(task, row):
                self._move(con, row, "decision_wait", "candidate changed during check",
                           reason="candidate changed during check; inspect frozen worker evidence")
                return
            checks = json.loads(row["checks"])
            checks.append({"guard_id": row["guard_id"],
                           "record_sha256": digest(json.dumps(record, sort_keys=True,
                                                                separators=(",", ":")).encode())})
            self._move(con, row, "checks_pending", "check succeeded", checks=json.dumps(checks),
                       step_index=row["step_index"] + 1, role=None, guard_id=None)
        elif row["role"] == "review":
            try:
                report_bytes = file_at(self.root, task["review_path"]).read_bytes()
                report = json.loads(report_bytes)
                hashes = self._candidate_hashes(task)
                good = (isinstance(report, dict) and report.get("decision") == "accepted"
                        and report.get("reviewer") == task["reviewer_identity"]
                        and hashes == json.loads(row["candidates"])
                        and report.get("candidate_sha256") == hashes
                        and report.get("checks") == json.loads(row["checks"])
                        and (self.fixture_mode and self.manifest["schema"] ==
                             "heleos.loop-controller/v1" or report.get("runner_evidence") ==
                             json.loads(row["runner_evidence"] or "null")))
            except (LoopError, ValueError, TypeError):
                good = False
            if good:
                self._move(con, row, "accepted", "candidate, checks and independent review matched",
                           role=None, guard_id=None, reason=None,
                           review_sha256=digest(report_bytes))
            else:
                self._move(con, row, "decision_wait", "review rejected, missing or mismatched",
                           reason="review rejected, missing or mismatched; independent reviewer or owner decision required")
        else:
            self._move(con, row, "uncertain", "unknown successful step", reason="inspect ledger")

    def tick(self):
        fresh, fingerprint = load_manifest(self.manifest_path)
        if fresh != self.manifest or fingerprint != self.manifest_digest:
            raise LoopError("manifest changed during run")
        self._require_admission()
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = self._row(con)
            if row is None:
                con.commit()
                return {"reason": "queue_exhausted", **self.status()}
            task = self.tasks[row["id"]]
            state = row["state"]
            if state in ("decision_wait", "failed", "uncertain"):
                con.commit()
                return {"reason": state, "task": row["id"], **self.status()}
            if state == "queued":
                if not self._review_absent(task) or ("review_source_path" in task and
                                                    file_at(self.root, task["review_source_path"],
                                                            required=False).exists()):
                    self._move(con, row, "decision_wait", "preexisting review report",
                               reason="remove or separately reconcile stale review artifact before new admission")
                else:
                    self._intent(con, row, "worker", task["worker"])
            elif state == "checks_pending":
                checks = self._checks(task, row)
                if not self._candidate_unchanged(task, row):
                    self._move(con, row, "decision_wait", "candidate changed before check",
                               reason="candidate changed before check; inspect frozen worker evidence")
                elif row["step_index"] < len(checks):
                    self._intent(con, row, "test", checks[row["step_index"]])
                elif not self._review_absent(task):
                    self._move(con, row, "decision_wait", "review report existed before review run",
                               reason="review report has no independent run provenance; inspect retained artifact")
                else:
                    self._move(con, row, "review_pending", "all checks passed")
            elif state == "review_pending":
                if not self._review_absent(task):
                    self._move(con, row, "decision_wait", "review report existed before review run",
                               reason="review report has no independent run provenance; inspect retained artifact")
                else:
                    try:
                        source = self._review_source(task, row) if "review_source_path" in task else True
                    except LoopError as exc:
                        self._move(con, row, "decision_wait", str(exc), reason=str(exc))
                    else:
                        if source is None:
                            con.commit()
                            return {"reason": "awaiting_independent_review", "task": row["id"],
                                    "review_source_path": task["review_source_path"], **self.status()}
                        self._intent(con, row, "review", self._review(task, row))
            elif state in ("dispatch_intent", "running"):
                ref = (task["worker"] if row["role"] == "worker" else
                       task["repair"]["spec"] if row["role"] == "repair" else
                       self._checks(task, row)[row["step_index"]] if row["role"] == "test" else
                       self._review(task, row))
                spec = spec_at(self.root, ref, self.manifest["base_head"],
                               self.manifest["allowed_result_paths"])
                if spec["task_id"] != row["guard_id"]:
                    raise LoopError("durable guard identity changed")
                observed = self.guard.status(row["guard_id"])
                record, active = observed.get("task"), observed.get("active")
                if record is None:
                    if active:
                        self._move(con, row, "uncertain", "guard record absent or different owner active",
                                   reason="inspect guard registry and reconcile ownership")
                    elif state == "running" and row["launch_at"] is not None and time.time() - row["launch_at"] < 2:
                        pass  # The guard may still be reserving its durable slot.
                    elif state != "dispatch_intent":
                        self._move(con, row, "uncertain", "launched guard has no durable record",
                                   reason="inspect launch outcome and guard registry before retry")
                    else:
                        drift = ("review report existed before review run" if row["role"] == "review" and
                                 not self._review_absent(task) else
                                 "candidate changed before check" if row["role"] == "test" and
                                 not self._candidate_unchanged(task, row) else input_drift(self.root, spec))
                        if drift:
                            self._move(con, row, "decision_wait", drift, reason=drift)
                        else:
                            self.guard.launch(file_at(self.root, ref["path"]), ref["sha256"])
                            self._move(con, row, "running", "same pinned guard ID dispatched",
                                       launch_at=time.time())
                elif record.get("task_id") != row["guard_id"] or record.get("spec") != spec:
                    self._move(con, row, "uncertain", "guard record does not match pinned spec",
                               reason="inspect guard task ID, recorded spec and immutable manifest; do not consume result")
                elif record.get("uncertain") or record.get("state") == "needs_reconciliation":
                    self._move(con, row, "uncertain", "guard ownership uncertain",
                               reason="inspect processes; independent incident reconciliation required")
                elif record["state"] in ("reserved", "running", "integrating"):
                    if active and active.get("task_id") != row["guard_id"]:
                        self._move(con, row, "uncertain", "different guard owner active",
                                   reason="inspect active guard owner")
                    elif state != "running":
                        self._move(con, row, "running", "existing guard ID observed")
                elif record["state"] == "succeeded":
                    drift = input_drift(self.root, spec)
                    if drift:
                        self._move(con, row, "decision_wait", drift, reason=drift)
                    else:
                        self._success(con, row, task, record)
                elif record["state"] in ("failed", "timed_out"):
                    self._failure(con, row, task, "guard " + record["state"] + ": " + row["guard_id"])
                elif record["state"] in ("cancelled", "reconciled"):
                    self._move(con, row, "decision_wait", "guard " + record["state"] + ": " + row["guard_id"],
                               reason="guard " + record["state"] + "; inspect retained evidence before new admission")
                else:
                    self._move(con, row, "uncertain", "unknown guard outcome",
                               reason="inspect guard ID " + row["guard_id"])
            else:
                raise LoopError("invalid durable state: " + state)
            con.commit()
            return {"task": row["id"], **self.status()}
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def run(self, max_actions=100, max_seconds=60, poll_seconds=0.25):
        if not isinstance(max_actions, int) or not 1 <= max_actions <= 10000 or not 0 < max_seconds <= 86400 or not 0 <= poll_seconds <= 60:
            raise LoopError("invalid run budget")
        deadline = time.monotonic() + max_seconds
        last = None
        try:
            for _ in range(max_actions):
                if time.monotonic() >= deadline:
                    break
                last = self.tick()
                if last.get("reason") in ("queue_exhausted", "decision_wait", "failed", "uncertain"):
                    if hasattr(self.guard, "reap_terminal"):
                        self.guard.reap_terminal()
                    return last
                if poll_seconds:
                    time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
        except KeyboardInterrupt:
            return {"reason": "controller_cancelled", **self.status()}
        status = self.status()
        waiting_for_review = last and last.get("reason") == "awaiting_independent_review"
        if not waiting_for_review:
            for task in self.manifest["tasks"]:
                if (status["tasks"].get(task["id"]) == "review_pending"
                        and "review_source_path" in task):
                    try:
                        waiting_for_review = not file_at(
                            self.root, task["review_source_path"], required=False).exists()
                    except LoopError:
                        pass  # A bad source needs a later tick to enter decision_wait.
                    if waiting_for_review:
                        break
        if waiting_for_review:
            return {"reason": "awaiting_independent_review", "last": last, **status}
        return {"reason": "controller_budget", "last": last, **status}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--admitted-manifest-sha256",
                        help="exact owner-admitted manifest digest; required for dispatch")
    parser.add_argument("--admission", help="exact reviewed P1 admission packet")
    parser.add_argument("--admission-sha256", help="SHA-256 of the reviewed admission packet")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    sub.add_parser("status")
    sub.add_parser("tick")
    run = sub.add_parser("run")
    run.add_argument("--max-actions", type=int, required=True)
    run.add_argument("--max-seconds", type=float, required=True)
    run.add_argument("--poll-seconds", type=float, default=0.25)
    args = parser.parse_args(argv)
    try:
        controller = LoopController(args.manifest, admitted_digest=args.admitted_manifest_sha256,
                                    admission_path=args.admission,
                                    admission_sha256=args.admission_sha256)
        if args.command == "plan":
            result = {"manifest_sha256": controller.manifest_digest,
                      "tasks": [t["id"] for t in controller.manifest["tasks"]],
                      "base_head": controller.manifest["base_head"]}
        elif args.command == "status":
            result = controller.status()
        elif args.command == "tick":
            result = controller.tick()
        else:
            result = controller.run(args.max_actions, args.max_seconds, args.poll_seconds)
        print(json.dumps({"ok": True, **result}, sort_keys=True))
        return 0
    except (LoopError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
