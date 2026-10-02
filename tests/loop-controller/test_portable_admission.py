"""Portable real-mode admission checks use only synthetic local executables."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from loop_controller import LoopController, LoopError, portable_provider_argv  # noqa: E402


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    raw = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


class Guard:
    def __init__(self):
        self.launched = []

    def status(self, _task_id):
        return {"task": None, "active": None}

    def launch(self, path, expected):
        assert sha(Path(path).read_bytes()) == expected
        self.launched.append(path)


class PortableAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="loop-portable-")
        self.addCleanup(self.temp.cleanup)
        parent = Path(self.temp.name).resolve()
        self.repo, self.workspace = parent / "repo", parent / "runner-workspace"
        self.repo.mkdir()
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        (self.repo / "source.txt").write_text("synthetic selected source\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "source.txt"], check=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "fixture"], check=True, env=env)
        self.head = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        self.runner = parent / "heleos-worker-runner"
        self.provider = parent / "fake-claude"
        for path in (self.runner, self.provider):
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
        self.task_id = "synthetic-admitted-task"
        self.candidates = ["candidate.py", "child-report.json"]
        self.selected = {"source.txt": sha((self.repo / "source.txt").read_bytes())}
        packet = {"schema": "heleos.worker-task/v1", "task_id": self.task_id,
                  "provider": "claude_code", "mode": "implementation", "base_commit": self.head,
                  "objective": "Write the two named synthetic candidate files.",
                  "allowed_paths": self.candidates, "forbidden_paths": [".git"],
                  "input_data_class": "PUBLIC", "egress_policy": "approved_external",
                  "instruction_sha256": self.selected, "acceptance_commands": ["synthetic checks"],
                  "limits": {"max_actions": 1, "max_duration_seconds": None}}
        self.packet_path = self.repo / "worker-task.json"
        packet_sha = sha(write_json(self.packet_path, packet))
        self.argv = ["/usr/bin/env", "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0",
                     "CLAUDE_CODE_DISABLE_AUTO_MEMORY=1",
                     str(self.runner), "--task", str(self.packet_path), "--source", str(self.repo),
                     "--workspace-root", str(self.workspace), "--provider", "claude_code",
                     "--command", str(self.provider), "--git", "/usr/bin/git",
                     "--containment", "macos_seatbelt", "--capture-claude-stream-trace",
                     "--max-prompt-bytes", "262144", "--max-output-bytes", "1048576",
                     "--selected-source-file", "source.txt=" + self.selected["source.txt"],
                     *[value for name in ("HOME", "USER", "LOGNAME", "SHELL",
                                          "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS",
                                          "CLAUDE_CODE_DISABLE_AUTO_MEMORY")
                       for value in ("--inherit-env", name)],
                     "--", *portable_provider_argv()]
        def spec(name, kind, paths, argv, inputs):
            return {"task_id": name, "kind": kind, "expected_head": self.head,
                    "argv": argv, "cwd": ".", "inputs": inputs, "allowed_paths": paths,
                    "timeout_seconds": None if kind == "worker" else 30,
                    "max_output_bytes": 65536}
        self.worker = spec("guard-worker", "worker", self.candidates, self.argv,
                           {"worker-task.json": packet_sha, **self.selected})
        refs = {}
        for role, value in {
            "worker": self.worker,
            "test": spec("guard-test", "test", ["check.json"], [sys.executable, "-c", "pass"], {}),
            "review": spec("guard-review", "test", ["review.json"], [sys.executable, "-c", "pass"], {}),
        }.items():
            path = self.repo / (role + "-spec.json")
            refs[role] = {"path": path.name, "sha256": sha(write_json(path, value))}
        task = {"id": self.task_id, "depends_on": [], "worker": refs["worker"],
                "tests": [refs["test"]], "review": refs["review"],
                "candidate_paths": self.candidates, "review_source_path": "review-source.json",
                "review_path": "review.json", "worker_identity": "claude-opus-5-5",
                "reviewer_identity": "independent-codex",
                "native_workflow": {"child_label": "child-a", "child_report_path": "child-report.json",
                                    "model": "claude-opus-5-5", "effort": "xhigh",
                                    "script_sha256": "a" * 64, "report_binding": "controller_receipt"}}
        manifest = {"schema": "heleos.loop-controller/v7", "base_head": self.head,
                    "worktree": str(self.repo), "allowed_result_paths": [
                        *self.candidates, "check.json", "review-source.json", "review.json"],
                    "tasks": [task]}
        self.manifest_path = self.repo / "queue.json"
        self.manifest_sha = sha(write_json(self.manifest_path, manifest))
        egress = {"schema": "heleos.loop-egress/v1", "decision": "approved",
                  "provider": "claude_code", "task_ids": [self.task_id],
                  "source_sha256": {self.task_id: self.selected}}
        self.egress_path = self.repo / "egress.json"
        egress_sha = sha(write_json(self.egress_path, egress))
        self.admission = {"schema": "heleos.loop-admission/v1", "manifest_sha256": self.manifest_sha,
                          "scope": "synthetic selected-source task", "runner_workspace_root": str(self.workspace),
                          "runner_sha256": sha(self.runner.read_bytes()),
                          "provider_sha256": sha(self.provider.read_bytes()),
                          "worker_tasks": {self.task_id: {"path": self.packet_path.name, "sha256": packet_sha}},
                          "worker_argv": {self.task_id: self.argv},
                          "egress_record": {"path": self.egress_path.name, "sha256": egress_sha}}
        self.admission_path = self.repo / "admission.json"
        self.admission_sha = sha(write_json(self.admission_path, self.admission))

    def controller(self, guard=None):
        return LoopController(self.manifest_path, guard=guard or Guard(),
                              admitted_digest=self.manifest_sha, admission_path=self.admission_path,
                              admission_sha256=self.admission_sha)

    def test_pinned_public_admission_dispatches_one_guard_identity(self):
        guard = Guard()
        controller = self.controller(guard)
        self.assertEqual(controller.tick()["tasks"][self.task_id], "dispatch_intent")
        self.assertEqual(controller.tick()["tasks"][self.task_id], "running")
        self.assertEqual(len(guard.launched), 1)

    def test_changed_selected_source_and_worker_packet_refuse_dispatch(self):
        controller = self.controller()
        (self.repo / "source.txt").write_text("changed\n")
        with self.assertRaisesRegex(LoopError, "selected source"):
            controller.tick()
        (self.repo / "source.txt").write_text("synthetic selected source\n")
        self.packet_path.write_text("{}\n")
        with self.assertRaisesRegex(LoopError, "worker task changed"):
            controller.tick()

    def test_changed_runner_and_provider_refuse_dispatch(self):
        controller = self.controller()
        self.runner.write_text("#!/bin/sh\nexit 1\n")
        with self.assertRaisesRegex(LoopError, "runner binary differs"):
            controller.tick()
        self.runner.write_text("#!/bin/sh\nexit 0\n")
        self.provider.write_text("#!/bin/sh\nexit 1\n")
        with self.assertRaisesRegex(LoopError, "provider executable differs"):
            controller.tick()

    def test_missing_or_unapproved_egress_refuses_dispatch(self):
        controller = self.controller()
        self.egress_path.write_text('{}\n')
        with self.assertRaisesRegex(LoopError, "egress proof changed"):
            controller.tick()

    def test_malformed_egress_task_ids_refuse_dispatch(self):
        egress = json.loads(self.egress_path.read_bytes())
        egress["task_ids"] = [{"unhashable": "item"}]
        self.admission["egress_record"]["sha256"] = sha(write_json(self.egress_path, egress))
        self.admission_sha = sha(write_json(self.admission_path, self.admission))
        with self.assertRaisesRegex(LoopError, "provider egress"):
            self.controller().tick()

    def _repin_worker_command(self):
        write_json(self.repo / "worker-spec.json", self.worker)
        manifest = json.loads(self.manifest_path.read_bytes())
        manifest["tasks"][0]["worker"]["sha256"] = sha((self.repo / "worker-spec.json").read_bytes())
        self.manifest_sha = sha(write_json(self.manifest_path, manifest))
        self.admission["manifest_sha256"] = self.manifest_sha
        self.admission_sha = sha(write_json(self.admission_path, self.admission))

    def test_missing_provider_model_refuses_dispatch(self):
        self.argv.remove("--model")
        self.argv.remove("claude-opus-5-5")
        self._repin_worker_command()
        with self.assertRaisesRegex(LoopError, "reviewed profile"):
            self.controller().tick()

    def test_extra_environment_inheritance_refuses_dispatch(self):
        self.argv[self.argv.index("--"):self.argv.index("--")] = [
            "--inherit-env", "CLAUDE_CODE_OAUTH_TOKEN"]
        self._repin_worker_command()
        with self.assertRaisesRegex(LoopError, "reviewed profile"):
            self.controller().tick()

    def test_missing_environment_wrapper_refuses_dispatch(self):
        del self.argv[:3]
        self._repin_worker_command()
        with self.assertRaisesRegex(LoopError, "environment wrapper"):
            self.controller().tick()

    def test_extra_provider_read_path_refuses_dispatch(self):
        self.argv += ["--add-dir", "/private"]
        self._repin_worker_command()
        with self.assertRaisesRegex(LoopError, "reviewed profile"):
            self.controller().tick()

    @unittest.skipUnless(sys.platform == "darwin", "macOS Seatbelt integration")
    def test_real_runner_guard_check_and_independent_review_resume(self):
        built = Path(__file__).resolve().parents[2] / "target/debug/heleos-worker-runner"
        if not built.is_file():
            self.skipTest("offline worker runner has not been built")
        shutil.copy2(built, self.runner)
        workflow_script = "await agent(prompt, { label: 'child-a', effort: 'xhigh' })"
        provider_source = '''#!/usr/bin/env python3
import hashlib, json
from pathlib import Path
script = "await agent(prompt, { label: 'child-a', effort: 'xhigh' })"
candidate = Path("candidate.py")
candidate.write_text("# synthetic candidate\\n")
report = {"child_label": "child-a", "decision": "reviewed", "reviewed_paths": ["candidate.py"],
          "findings": [], "configured_model": "claude-opus-5-5", "configured_effort": "xhigh"}
Path("child-report.json").write_text(json.dumps(report))
tool = "toolu-workflow-a"
events = [
 {"type":"assistant","message":{"content":[{"type":"tool_use","name":"Workflow","id":tool,"input":{"script":script}}]}},
 {"type":"system","subtype":"task_started","task_id":"wg-a","tool_use_id":tool},
 {"type":"system","subtype":"task_progress","task_id":"wg-a","tool_use_id":tool,
  "workflow_progress":[{"type":"workflow_agent","label":"child-a","model":"claude-opus-5-5",
                        "agentId":"agent-a","state":"start"}]},
 {"type":"system","subtype":"task_progress","task_id":"wg-a","tool_use_id":tool,
  "workflow_progress":[{"type":"workflow_agent","label":"child-a","model":"claude-opus-5-5",
                        "agentId":"agent-a","state":"done","lastToolName":"Write"}]},
 {"type":"system","subtype":"task_notification","task_id":"wg-a","tool_use_id":tool,
  "status":"completed","output_file":"/tmp/synthetic-child"},
 {"type":"result","subtype":"success","is_error":False,
  "modelUsage":{"claude-opus-5-5":{"canonicalModel":"claude-opus-5-5"}}},
]
for event in events:
 print(json.dumps(event), flush=True)
'''
        self.provider.write_text(provider_source)
        self.provider.chmod(0o755)
        self.admission["runner_sha256"] = sha(self.runner.read_bytes())
        self.admission["provider_sha256"] = sha(self.provider.read_bytes())
        manifest = json.loads(self.manifest_path.read_bytes())
        manifest["tasks"][0]["native_workflow"]["script_sha256"] = sha(workflow_script.encode())
        check = self.repo / "test-spec.json"
        check_spec = json.loads(check.read_bytes())
        check_spec["argv"] = [sys.executable, "-B", "-c",
                              "from pathlib import Path; "
                              "assert Path('candidate.py').read_text() == '# synthetic candidate\\n'; "
                              "Path('check.json').write_text('{\\\"passed\\\":true}\\n')"]
        manifest["tasks"][0]["tests"][0]["sha256"] = sha(write_json(check, check_spec))
        gate = Path(__file__).resolve().parents[2] / "scripts/loop_review_gate.py"
        local_gate = self.repo / "loop_review_gate.py"
        local_gate.write_bytes(gate.read_bytes())
        review = self.repo / "review-spec.json"
        review_spec = json.loads(review.read_bytes())
        review_spec["argv"] = [sys.executable, "-B", str(local_gate), "--source",
                                str(self.repo / "review-source.json"), "--output",
                                str(self.repo / "review.json"), "--reviewer", "independent-codex"]
        review_spec["inputs"] = {"loop_review_gate.py": sha(local_gate.read_bytes())}
        manifest["tasks"][0]["review"]["sha256"] = sha(write_json(review, review_spec))
        self.manifest_sha = sha(write_json(self.manifest_path, manifest))
        self.admission["manifest_sha256"] = self.manifest_sha
        self.admission_sha = sha(write_json(self.admission_path, self.admission))
        controller = LoopController(self.manifest_path, admitted_digest=self.manifest_sha,
                                    admission_path=self.admission_path,
                                    admission_sha256=self.admission_sha)
        for _ in range(15):
            first = controller.run(max_actions=1000, max_seconds=1, poll_seconds=0.05)
            if first["reason"] == "awaiting_independent_review":
                break
            if first["reason"] != "controller_budget":
                break
        logs = [p.read_text(errors="replace") for p in
                (self.repo / ".loop-trial/guard-logs").glob("*.log")]
        outputs = []
        for log in logs:
            for line in log.splitlines():
                try:
                    record = json.loads(line).get("record", {})
                    path = Path(record.get("stdout_path", ""))
                    if path.is_file():
                        outputs.append(path.read_text(errors="replace"))
                except (ValueError, OSError):
                    pass
        self.assertEqual(first["reason"], "awaiting_independent_review", (first, outputs))
        with sqlite3.connect(controller.db) as connection:
            row = connection.execute("SELECT candidates,checks,runner_evidence FROM tasks WHERE id=?",
                                     (self.task_id,)).fetchone()
        report = {"decision": "accepted", "reviewer": "independent-codex",
                  "candidate_sha256": json.loads(row[0]), "checks": json.loads(row[1]),
                  "runner_evidence": json.loads(row[2])}
        write_json(self.repo / "review-source.json", report)
        second = controller.run(max_actions=1000, max_seconds=10, poll_seconds=0.05)
        self.assertEqual(second["reason"], "queue_exhausted", second)
        self.assertEqual(second["tasks"][self.task_id], "accepted")
        self.assertEqual(controller.run(max_actions=1, max_seconds=1)["reason"], "queue_exhausted")


if __name__ == "__main__":
    unittest.main()
