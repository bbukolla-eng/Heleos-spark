"""Offline behavior tests for the admitted-task loop controller."""

import hashlib
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "loop_controller.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("loop_controller", SCRIPT)
loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loop)


def digest(data):
    return hashlib.sha256(data).hexdigest()


WORKFLOW_SCRIPT = "await agent(prompt, { label: 'review-a', effort: 'xhigh' })"


class GuardFixture:
    def __init__(self, repo):
        self.repo = repo
        self.records = {}
        self.launched = []
        self.effects = {}

    def status(self, task_id):
        task = self.records.get(task_id)
        active = next((v for v in self.records.values() if v["state"] == "running"), None)
        return {"task": task, "active": active}

    def launch(self, spec_path, spec_sha256):
        raw = Path(spec_path).read_bytes()
        assert digest(raw) == spec_sha256
        spec = json.loads(raw)
        task_id = spec["task_id"]
        self.launched.append(task_id)
        effect = self.effects.get(task_id)
        if effect:
            effect()
        if task_id not in self.records:
            self.records[task_id] = {"task_id": task_id, "state": "succeeded", "outcome": "succeeded"}
        self.records[task_id].setdefault("spec", spec)


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="loop-controller-test-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve() / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        (self.repo / "input.txt").write_text("fixed input\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "input.txt"], check=True)
        env = {"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        import os
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "fixture"],
                       check=True, env={**os.environ, **env})
        self.head = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        self.guard = GuardFixture(self.repo)
        (self.repo / "specs").mkdir()
        (self.repo / "out").mkdir()
        self.specs = {}

    def spec(self, task_id, kind="test", predecessor=None, changed_condition=None,
             allowed_paths=None):
        body = {"task_id": task_id, "kind": kind, "expected_head": self.head,
                "argv": [sys.executable, "-c", "pass"], "cwd": ".",
                "inputs": {"input.txt": digest(b"fixed input\n")},
                "allowed_paths": allowed_paths or ["out/" + task_id + ".evidence"],
                "timeout_seconds": None if kind == "worker" else 30,
                "max_output_bytes": 65536}
        if predecessor:
            body.update(predecessor=predecessor, changed_condition=changed_condition)
        path = self.repo / "specs" / (task_id + ".json")
        raw = json.dumps(body, sort_keys=True).encode()
        path.write_bytes(raw)
        self.specs[task_id] = {"path": "specs/" + path.name, "sha256": digest(raw)}
        return self.specs[task_id]

    def task(self, name, depends_on=(), repair=None):
        worker = self.spec(name + "-worker", "worker",
                           allowed_paths=["out/" + name + ".txt"])
        check = self.spec(name + "-check")
        review = self.spec(name + "-review",
                           allowed_paths=["out/" + name + "-review.json"])
        task = {"id": name, "depends_on": list(depends_on), "worker": worker,
                "tests": [check], "review": review,
                "candidate_paths": ["out/" + name + ".txt"],
                "review_path": "out/" + name + "-review.json",
                "worker_identity": "worker-" + name,
                "reviewer_identity": "reviewer-" + name}
        if repair:
            task["repair"] = repair
        self.guard.effects[name + "-worker"] = lambda n=name: (self.repo / "out" / (n + ".txt")).write_text(n)
        self.guard.effects[name + "-review"] = lambda n=name: self._write_review(n)
        return task

    def _write_review(self, name, decision="accepted", include_hashes=True):
        candidate = "out/" + name + ".txt"
        con = sqlite3.connect(self.repo / ".loop-trial/ledger.sqlite3")
        checks = json.loads(con.execute("SELECT checks FROM tasks WHERE id=?", (name,)).fetchone()[0])
        con.close()
        report = {"decision": decision, "reviewer": "reviewer-" + name,
                  "candidate_sha256": {candidate: digest((self.repo / candidate).read_bytes())},
                  "checks": checks if include_hashes else [name + "-check"]}
        (self.repo / "out" / (name + "-review.json")).write_text(json.dumps(report))

    def manifest(self, tasks, schema="heleos.loop-controller/v1"):
        body = {"schema": schema, "base_head": self.head,
                "worktree": str(self.repo), "allowed_result_paths": ["out"],
                "tasks": tasks}
        path = self.repo / "queue.json"
        path.write_text(json.dumps(body, sort_keys=True))
        return path

    def controller(self, manifest):
        controller = loop.LoopController(manifest, guard=self.guard, fixture_mode=True)
        self.ledger_path = controller.db
        return controller

    def native_task(self, receipt=False):
        task = self.task("a")
        child = "out/a-child.json"
        task["candidate_paths"].append(child)
        task["native_workflow"] = {"child_label": "review-a", "child_report_path": child,
                                   "model": "claude-opus-5-5", "effort": "xhigh",
                                   "script_sha256": digest(WORKFLOW_SCRIPT.encode())}
        if receipt:
            task["native_workflow"]["report_binding"] = "controller_receipt"
        task["review_source_path"] = "out/a-independent.json"
        spec_path = self.repo / task["worker"]["path"]
        spec = json.loads(spec_path.read_text())
        spec["allowed_paths"] = task["candidate_paths"]
        raw = json.dumps(spec, sort_keys=True).encode()
        spec_path.write_bytes(raw)
        task["worker"]["sha256"] = digest(raw)
        self.guard.effects["a-worker"] = self._native_worker
        self.guard.effects["a-review"] = lambda: (self.repo / task["review_path"]).write_bytes(
            (self.repo / task["review_source_path"]).read_bytes())
        return task

    def _native_stream(self, *, child=True, cost=1.25):
        events = []
        if child:
            events.extend([
                {"type": "assistant", "message": {"content": [{"type": "tool_use",
                    "name": "Workflow", "id": "toolu-a", "input": {"script": WORKFLOW_SCRIPT}}]}},
                {"type": "system", "subtype": "task_started", "task_id": "wg-a",
                 "tool_use_id": "toolu-a"},
                *[{"type": "system", "subtype": "task_progress", "task_id": "wg-a",
                   "tool_use_id": "toolu-a", "workflow_progress": [{"type": "workflow_agent",
                    "label": "review-a", "model": "claude-opus-5-5", "agentId": "agent-a",
                    "state": state, **({"lastToolName": "Write"} if state == "done" else {})}]}
                  for state in ("start", "done")],
                {"type": "system", "subtype": "task_notification", "task_id": "wg-a",
                 "tool_use_id": "toolu-a", "status": "completed", "output_file": "/tmp/child"},
            ])
        events.append({"type": "result", "subtype": "success", "is_error": False,
                       "modelUsage": {"claude-opus-5-5": {"canonicalModel": "claude-opus-5-5"}},
                       "total_cost_usd": cost})
        return "".join(json.dumps(event) + "\n" for event in events).encode()

    def _native_worker(self, *, child=True, cost=1.25):
        candidate = self.repo / "out/a.txt"
        candidate.write_text("a")
        report = {"child_label": "review-a", "decision": "accepted",
                  "candidate_sha256": {"out/a.txt": digest(candidate.read_bytes())},
                  "configured_model": "claude-opus-5-5", "configured_effort": "xhigh"}
        (self.repo / "out/a-child.json").write_text(json.dumps(report))
        self.guard.records["a-worker"] = {"task_id": "a-worker", "state": "succeeded",
                                           "outcome": "succeeded",
                                           "workflow_stream": self._native_stream(child=child, cost=cost)}

    def _write_native_review_source(self):
        con = sqlite3.connect(self.ledger_path)
        row = con.execute("SELECT candidates,checks,runner_evidence FROM tasks WHERE id='a'").fetchone()
        con.close()
        report = {"decision": "accepted", "reviewer": "reviewer-a",
                  "candidate_sha256": json.loads(row[0]), "checks": json.loads(row[1]),
                  "runner_evidence": json.loads(row[2])}
        (self.repo / "out/a-independent.json").write_text(json.dumps(report))

    def test_foreground_run_advances_admitted_dependency_graph(self):
        path = self.manifest([self.task("a"), self.task("b", depends_on=["a"])])
        result = self.controller(path).run(max_actions=30, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "queue_exhausted")
        self.assertEqual(result["tasks"], {"a": "accepted", "b": "accepted"})
        self.assertEqual(self.guard.launched, ["a-worker", "a-check", "a-review",
                                                "b-worker", "b-check", "b-review"])

    def test_restart_after_intent_and_after_worker_result_reuses_guard_id(self):
        path = self.manifest([self.task("a")])
        self.controller(path).tick()
        self.assertEqual(self.guard.launched, [])
        self.controller(path).tick()
        self.assertEqual(self.guard.launched, ["a-worker"])
        result = self.controller(path).run(max_actions=20, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "queue_exhausted")
        self.assertEqual(self.guard.launched.count("a-worker"), 1)

    def test_hung_guard_stops_at_controller_budget_without_duplicate_or_cancel(self):
        task = self.task("a")
        self.guard.effects["a-worker"] = lambda: self.guard.records.update(
            {"a-worker": {"task_id": "a-worker", "state": "running"}})
        path = self.manifest([task])
        first = self.controller(path).run(max_actions=8, max_seconds=3, poll_seconds=0)
        self.assertEqual(first["reason"], "controller_budget")
        self.assertEqual(first["tasks"]["a"], "running")
        again = self.controller(path).run(max_actions=2, max_seconds=3, poll_seconds=0)
        self.assertEqual(again["tasks"]["a"], "running")
        self.assertEqual(self.guard.launched, ["a-worker"])

    def test_uncertain_guard_ownership_blocks_and_preserves_actionable_reason(self):
        task = self.task("a")
        self.guard.effects["a-worker"] = lambda: self.guard.records.update(
            {"a-worker": {"task_id": "a-worker", "state": "running", "uncertain": True}})
        result = self.controller(self.manifest([task])).run(
            max_actions=10, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "uncertain")
        self.assertIn("reconciliation", result["details"]["a"]["reason"])
        self.assertEqual(self.guard.launched, ["a-worker"])

    def test_review_rejection_waits_with_candidate_and_check_evidence(self):
        task = self.task("a")
        self.guard.effects["a-review"] = lambda: self._write_review("a", "rejected")
        path = self.manifest([task])
        result = self.controller(path).run(max_actions=20, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")
        self.assertIn("review", result["details"]["a"]["reason"])
        self.assertEqual(self.guard.launched, ["a-worker", "a-check", "a-review"])

    def test_input_drift_stops_before_any_launch(self):
        path = self.manifest([self.task("a")])
        (self.repo / "input.txt").write_text("changed input\n")
        result = self.controller(path).run(max_actions=10, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")
        self.assertIn("input", result["details"]["a"]["reason"])
        self.assertEqual(self.guard.launched, [])

    def test_real_guard_runs_synthetic_worker_check_and_review(self):
        task = self.task("a")
        commands = {
            "a-worker": "from pathlib import Path; Path('out/a.txt').write_text('a')",
            "a-check": "from pathlib import Path; assert Path('out/a.txt').read_text() == 'a'",
            "a-review": (
                "import hashlib,json,sqlite3; from pathlib import Path; "
                "p=Path('out/a.txt'); h=hashlib.sha256(p.read_bytes()).hexdigest(); "
                "db=sqlite3.connect('.loop-trial/ledger.sqlite3'); "
                "checks=json.loads(db.execute(\"SELECT checks FROM tasks WHERE id='a'\").fetchone()[0]); "
                "db.close(); "
                "Path('out/a-review.json').write_text(json.dumps({"
                "'decision':'accepted','reviewer':'reviewer-a',"
                "'candidate_sha256':{'out/a.txt':h},'checks':checks}))"
            ),
        }
        for task_id, script in commands.items():
            ref = self.specs[task_id]
            spec_path = self.repo / ref["path"]
            body = json.loads(spec_path.read_text())
            body["argv"] = [sys.executable, "-c", script]
            raw = json.dumps(body, sort_keys=True).encode()
            spec_path.write_bytes(raw)
            ref["sha256"] = digest(raw)
        path = self.manifest([task])
        result = loop.LoopController(path, fixture_mode=True).run(
            max_actions=120, max_seconds=20, poll_seconds=0.05)
        logs = [p.read_text() for p in (self.repo / ".loop-trial" / "guard-logs").glob("*.log")]
        self.assertEqual(result["reason"], "queue_exhausted", {"result": result, "logs": logs})
        self.assertEqual(result["tasks"]["a"], "accepted")

    def test_failed_check_uses_one_linked_repair_then_fresh_checks_and_review(self):
        task = self.task("a")
        repair = self.spec("a-repair", "worker", predecessor="a-check",
                           changed_condition="replace failed candidate from check evidence",
                           allowed_paths=["out/a.txt"])
        fresh_check = self.spec("a-check-repaired", predecessor="a-check",
                                changed_condition="check repaired candidate")
        fresh_review = self.spec("a-review-repaired",
                                 allowed_paths=["out/a-review.json"])
        task["repair"] = {"on_guard_id": "a-check", "spec": repair,
                          "tests": [fresh_check], "review": fresh_review}
        self.guard.effects["a-check"] = lambda: self.guard.records.update(
            {"a-check": {"task_id": "a-check", "state": "failed"}})
        self.guard.effects["a-repair"] = lambda: (self.repo / "out/a.txt").write_text("repaired")
        def review_repair():
            self._write_review("a")
        self.guard.effects["a-review-repaired"] = review_repair
        result = self.controller(self.manifest([task])).run(
            max_actions=30, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "queue_exhausted", result)
        self.assertEqual(result["details"]["a"]["repair_used"], True)
        self.assertEqual(self.guard.launched, ["a-worker", "a-check", "a-repair",
                                                "a-check-repaired", "a-review-repaired"])

    def test_concurrent_ticks_request_only_one_guard_launch(self):
        path = self.manifest([self.task("a")])
        self.controller(path).tick()  # Intent is durable before either dispatcher starts.
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.controller(path).tick(), range(2)))
        self.assertEqual(len(results), 2)
        self.assertEqual(self.guard.launched.count("a-worker"), 1)

    def test_review_cannot_accept_candidate_changed_after_checks(self):
        task = self.task("a")
        def change_then_review():
            (self.repo / "out/a.txt").write_text("changed after checks")
            self._write_review("a")
        self.guard.effects["a-review"] = change_then_review
        result = self.controller(self.manifest([task])).run(
            max_actions=20, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")
        self.assertEqual(result["tasks"]["a"], "decision_wait")

    def test_cycle_and_changed_spec_are_rejected_before_dispatch(self):
        a, b = self.task("a", ["b"]), self.task("b", ["a"])
        with self.assertRaisesRegex(loop.LoopError, "cycle"):
            self.controller(self.manifest([a, b]))
        path = self.manifest([self.task("c")])
        (self.repo / "specs/c-worker.json").write_text("{}")
        with self.assertRaisesRegex(loop.LoopError, "pinned spec changed"):
            self.controller(path)

    def test_self_review_identity_and_review_write_scope_are_rejected(self):
        task = self.task("a")
        task["reviewer_identity"] = task["worker_identity"]
        with self.assertRaisesRegex(loop.LoopError, "review"):
            self.controller(self.manifest([task]))

    def test_event_ledger_binds_manifest_and_candidate_evidence(self):
        path = self.manifest([self.task("a")])
        self.controller(path).run(max_actions=20, max_seconds=3, poll_seconds=0)
        con = sqlite3.connect(self.repo / ".loop-trial/ledger.sqlite3")
        events = con.execute("SELECT manifest_digest,evidence_json FROM events ORDER BY seq").fetchall()
        con.close()
        self.assertTrue(events)
        self.assertTrue(all(item[0] == digest(path.read_bytes()) for item in events))
        self.assertTrue(any("out/a.txt" in item[1] for item in events))
        stored = sqlite3.connect(self.repo / ".loop-trial/ledger.sqlite3")
        review_digest = stored.execute("SELECT review_sha256 FROM tasks WHERE id='a'").fetchone()[0]
        stored.close()
        self.assertEqual(review_digest, digest((self.repo / "out/a-review.json").read_bytes()))

    def test_decision_wait_names_smallest_next_action(self):
        task = self.task("a")
        self.guard.effects["a-review"] = lambda: self._write_review("a", "rejected")
        result = self.controller(self.manifest([task])).run(
            max_actions=20, max_seconds=3, poll_seconds=0)
        self.assertIn("reviewer", result["details"]["a"]["next_action"])

    def test_failed_repair_exhausts_budget_and_does_not_retry(self):
        task = self.task("a")
        task["repair"] = {
            "on_guard_id": "a-worker",
            "spec": self.spec("a-repair", "worker", predecessor="a-worker",
                              changed_condition="new candidate approach after worker failure",
                              allowed_paths=["out/a.txt"]),
            "tests": [self.spec("a-check-repaired")],
            "review": self.spec("a-review-repaired", allowed_paths=["out/a-review.json"]),
        }
        self.guard.effects["a-worker"] = lambda: self.guard.records.update(
            {"a-worker": {"task_id": "a-worker", "state": "failed"}})
        self.guard.effects["a-repair"] = lambda: self.guard.records.update(
            {"a-repair": {"task_id": "a-repair", "state": "failed"}})
        path = self.manifest([task])
        result = self.controller(path).run(max_actions=20, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")
        self.assertTrue(result["details"]["a"]["repair_used"])
        self.assertEqual(self.guard.launched, ["a-worker", "a-repair"])
        self.controller(path).run(max_actions=3, max_seconds=3, poll_seconds=0)
        self.assertEqual(self.guard.launched, ["a-worker", "a-repair"])

    def test_launched_guard_without_registry_record_becomes_uncertain_once(self):
        task = self.task("a")
        self.guard.effects["a-worker"] = lambda: None
        path = self.manifest([task])
        self.controller(path).tick()  # Persist intent.
        self.controller(path).tick()  # Request launch.
        self.guard.records.clear()  # Simulate guard failing before reservation.
        con = sqlite3.connect(self.repo / ".loop-trial/ledger.sqlite3")
        con.execute("UPDATE tasks SET launch_at=0 WHERE id='a'")
        con.commit()
        con.close()
        result = self.controller(path).tick()
        self.assertEqual(result["tasks"]["a"], "uncertain")
        self.assertEqual(self.guard.launched, ["a-worker"])

    def test_read_only_status_does_not_create_trial_ledger(self):
        path = self.manifest([self.task("a")])
        status = self.controller(path).status()
        self.assertEqual(status["reason"], "not_started")
        self.assertFalse((self.repo / ".loop-trial").exists())

    def test_review_must_bind_check_result_hashes(self):
        task = self.task("a")
        self.guard.effects["a-review"] = lambda: self._write_review("a", include_hashes=False)
        result = self.controller(self.manifest([task])).run(
            max_actions=20, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")

    def test_existing_guard_id_with_different_spec_is_never_consumed(self):
        task = self.task("a")
        path = self.manifest([task])
        self.controller(path).tick()
        self.guard.records["a-worker"] = {
            "task_id": "a-worker", "state": "succeeded",
            "spec": {"task_id": "a-worker", "expected_head": self.head, "argv": ["wrong"]},
        }
        result = self.controller(path).tick()
        self.assertEqual(result["tasks"]["a"], "uncertain")
        self.assertEqual(self.guard.launched, [])

    def test_cancelled_worker_never_auto_repairs(self):
        task = self.task("a")
        task["repair"] = {
            "on_guard_id": "a-worker",
            "spec": self.spec("a-repair", "worker", predecessor="a-worker",
                              changed_condition="new approach after failure",
                              allowed_paths=["out/a.txt"]),
            "tests": [self.spec("a-check-repaired")],
            "review": self.spec("a-review-repaired", allowed_paths=["out/a-review.json"]),
        }
        self.guard.effects["a-worker"] = lambda: self.guard.records.update(
            {"a-worker": {"task_id": "a-worker", "state": "cancelled"}})
        result = self.controller(self.manifest([task])).run(
            max_actions=20, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")
        self.assertEqual(self.guard.launched, ["a-worker"])
        self.assertFalse(result["details"]["a"]["repair_used"])

    def test_real_dispatch_requires_exact_admission_and_containment(self):
        path = self.manifest([self.task("a")])
        with self.assertRaisesRegex(loop.LoopError, "admission"):
            loop.LoopController(path, guard=self.guard).tick()
        self.assertEqual(self.guard.launched, [])
        with self.assertRaisesRegex(loop.LoopError, "admission packet"):
            loop.LoopController(path, guard=self.guard,
                                admitted_digest=digest(path.read_bytes())).tick()
        self.assertEqual(self.guard.launched, [])

    def test_shared_output_paths_across_tasks_are_rejected(self):
        a, b = self.task("a"), self.task("b")
        b["candidate_paths"] = list(a["candidate_paths"])
        spec_path = self.repo / b["worker"]["path"]
        spec = json.loads(spec_path.read_text())
        spec["allowed_paths"] = list(a["candidate_paths"])
        raw = json.dumps(spec, sort_keys=True).encode()
        spec_path.write_bytes(raw)
        b["worker"]["sha256"] = digest(raw)
        with self.assertRaisesRegex(loop.LoopError, "shared"):
            self.controller(self.manifest([a, b]))

    def test_trial_ledger_symlink_is_rejected_without_external_write(self):
        path = self.manifest([self.task("a")])
        external = Path(self.temp.name).resolve() / "outside"
        external.mkdir()
        (self.repo / ".loop-trial").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(loop.LoopError, "symlink"):
            self.controller(path).tick()
        self.assertEqual(list(external.iterdir()), [])

    def test_candidate_drift_before_check_blocks_check_launch(self):
        path = self.manifest([self.task("a")])
        controller = self.controller(path)
        controller.tick()  # worker intent
        controller.tick()  # worker result produced
        controller.tick()  # worker result consumed and candidate hash frozen
        (self.repo / "out/a.txt").write_text("changed before check")
        result = controller.run(max_actions=10, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")
        self.assertEqual(self.guard.launched, ["a-worker"])

    def test_candidate_drift_during_check_blocks_review(self):
        task = self.task("a")
        self.guard.effects["a-check"] = lambda: (self.repo / "out/a.txt").write_text(
            "changed during check")
        result = self.controller(self.manifest([task])).run(
            max_actions=20, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")
        self.assertEqual(self.guard.launched, ["a-worker", "a-check"])

    def test_review_report_prewritten_by_worker_is_not_accepted(self):
        task = self.task("a")
        def worker_with_report():
            (self.repo / "out/a.txt").write_text("a")
            (self.repo / "out/a-review.json").write_text("{}")
        self.guard.effects["a-worker"] = worker_with_report
        result = self.controller(self.manifest([task])).run(
            max_actions=20, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")
        self.assertEqual(self.guard.launched, ["a-worker"])

    def test_preexisting_review_report_blocks_worker(self):
        path = self.manifest([self.task("a")])
        (self.repo / "out/a-review.json").write_text("{}")
        result = self.controller(path).run(max_actions=10, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")
        self.assertEqual(self.guard.launched, [])

    def test_foreground_interrupt_stops_without_cancelling_guard(self):
        path = self.manifest([self.task("a")])
        self.controller(path).tick()
        def interrupted(_):
            raise KeyboardInterrupt
        self.guard.status = interrupted
        result = self.controller(path).run(max_actions=10, max_seconds=3, poll_seconds=0)
        self.assertEqual(result["reason"], "controller_cancelled")
        self.assertEqual(result["tasks"]["a"], "dispatch_intent")
        self.assertEqual(self.guard.launched, [])

    def test_versioned_queue_cannot_accept_worker_without_required_native_child(self):
        task = self.native_task()
        self.guard.effects["a-worker"] = lambda: self._native_worker(child=False, cost=4.99)
        path = self.manifest([task], schema="heleos.loop-controller/v2")
        result = self.controller(path).run(max_actions=20, max_seconds=2, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")
        self.assertIn("required native Workflow", result["details"]["a"]["reason"])
        self.assertIn("native Workflow", result["details"]["a"]["next_action"])
        self.assertEqual(self.guard.launched, ["a-worker"])

    def test_bound_child_defects_have_distinct_wait_reason_and_no_checks(self):
        task = self.native_task(receipt=True)

        def defective_worker():
            self._native_worker()
            (self.repo / "out/a-child.json").write_text(json.dumps({
                "child_label": "review-a", "decision": "defects_found",
                "reviewed_paths": ["out/a.txt"],
                "findings": [{"id": "F1", "summary": "false departure"}],
                "configured_model": "claude-opus-5-5", "configured_effort": "xhigh"}))

        self.guard.effects["a-worker"] = defective_worker
        path = self.manifest([task], schema="heleos.loop-controller/v3")
        result = self.controller(path).run(max_actions=20, max_seconds=2, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait")
        self.assertIn("native_child_defects_found", result["details"]["a"]["reason"])
        self.assertEqual(self.guard.launched, ["a-worker"])

    def test_versioned_import_hash_drift_stops_before_checks(self):
        path = self.manifest([self.native_task()], schema="heleos.loop-controller/v2")
        controller = self.controller(path)
        controller.tick()  # Durable intent.
        controller.tick()  # Worker result and candidate files.
        wrong = {"out/a.txt": "0" * 64, "out/a-child.json": "0" * 64}
        with mock.patch.object(controller, "_candidate_hashes", return_value=wrong):
            result = controller.tick()
        self.assertEqual(result["tasks"]["a"], "decision_wait", result)
        self.assertIn("candidate", result["details"]["a"]["reason"])
        self.assertEqual(self.guard.launched, ["a-worker"])

    def test_versioned_foreground_waits_for_independent_review_arrival(self):
        path = self.manifest([self.native_task()], schema="heleos.loop-controller/v2")
        controller = self.controller(path)
        failures = []
        def arrive():
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    ledger = self.ledger_path
                    if ledger.exists():
                        con = sqlite3.connect(ledger)
                        state = con.execute("SELECT state FROM tasks WHERE id='a'").fetchone()[0]
                        con.close()
                        if state == "review_pending":
                            self._write_native_review_source()
                            return
                    time.sleep(0.01)
                failures.append("review_pending never reached")
            except Exception as exc:
                failures.append(repr(exc))
        thread = threading.Thread(target=arrive)
        thread.start()
        result = controller.run(max_actions=300, max_seconds=3, poll_seconds=0.01)
        thread.join(timeout=4)
        self.assertFalse(failures, failures)
        self.assertEqual(result["reason"], "queue_exhausted", result)
        self.assertEqual(result["tasks"]["a"], "accepted")
        self.assertEqual(self.guard.launched, ["a-worker", "a-check", "a-review"])

    def test_v3_receipt_waits_for_independent_review_and_accepts_exact_bytes(self):
        task = self.native_task(receipt=True)
        def worker():
            self._native_worker()
            (self.repo / "out/a-child.json").write_text(json.dumps({
                "child_label": "review-a", "decision": "reviewed",
                "reviewed_paths": ["out/a.txt"], "findings": [],
                "configured_model": "claude-opus-5-5", "configured_effort": "xhigh"}))
        self.guard.effects["a-worker"] = worker
        path = self.manifest([task], schema="heleos.loop-controller/v3")
        waiting = self.controller(path).run(max_actions=60, max_seconds=0.2, poll_seconds=0.01)
        self.assertEqual(waiting["reason"], "awaiting_independent_review", waiting)
        self.assertEqual(self.guard.launched, ["a-worker", "a-check"])
        self._write_native_review_source()
        done = self.controller(path).run(max_actions=30, max_seconds=2, poll_seconds=0)
        self.assertEqual(done["reason"], "queue_exhausted", done)
        self.assertEqual(done["tasks"]["a"], "accepted")

    def test_versioned_review_wait_restart_and_cancel_do_not_duplicate_dispatch(self):
        path = self.manifest([self.native_task()], schema="heleos.loop-controller/v2")
        first = self.controller(path).run(max_actions=60, max_seconds=0.2, poll_seconds=0.01)
        self.assertEqual(first["reason"], "awaiting_independent_review", first)
        self.assertEqual(first["tasks"]["a"], "review_pending")
        self.assertIn("independent review", first["details"]["a"]["next_action"])
        self.assertEqual(self.guard.launched, ["a-worker", "a-check"])
        with mock.patch.object(loop.time, "sleep", side_effect=KeyboardInterrupt):
            interrupted = self.controller(path).run(max_actions=4, max_seconds=1, poll_seconds=0.01)
        self.assertEqual(interrupted["reason"], "controller_cancelled")
        self.assertEqual(self.guard.launched, ["a-worker", "a-check"])
        self._write_native_review_source()
        final = self.controller(path).run(max_actions=30, max_seconds=2, poll_seconds=0)
        self.assertEqual(final["reason"], "queue_exhausted", final)
        self.assertEqual(self.guard.launched, ["a-worker", "a-check", "a-review"])

    def test_budget_at_review_pending_reports_missing_independent_source(self):
        path = self.manifest([self.native_task()], schema="heleos.loop-controller/v2")
        result = self.controller(path).run(max_actions=7, max_seconds=2, poll_seconds=0)
        self.assertEqual(result["tasks"]["a"], "review_pending", result)
        self.assertEqual(result["reason"], "awaiting_independent_review", result)
        self.assertEqual(self.guard.launched, ["a-worker", "a-check"])

    def test_versioned_stale_or_mismatched_review_source_never_launches_review(self):
        task = self.native_task()
        path = self.manifest([task], schema="heleos.loop-controller/v2")
        (self.repo / task["review_source_path"]).write_text("{}")
        stale = self.controller(path).run(max_actions=10, max_seconds=1, poll_seconds=0)
        self.assertEqual(stale["reason"], "decision_wait")
        self.assertEqual(self.guard.launched, [])

    def test_versioned_review_source_mismatch_after_checks_is_actionable(self):
        task = self.native_task()
        path = self.manifest([task], schema="heleos.loop-controller/v2")
        waiting = self.controller(path).run(max_actions=60, max_seconds=0.2, poll_seconds=0.01)
        self.assertEqual(waiting["reason"], "awaiting_independent_review")
        (self.repo / task["review_source_path"]).write_text(json.dumps({"decision": "accepted"}))
        result = self.controller(path).run(max_actions=5, max_seconds=1, poll_seconds=0)
        self.assertEqual(result["reason"], "decision_wait", result)
        self.assertIn("review source", result["details"]["a"]["reason"])
        self.assertEqual(self.guard.launched, ["a-worker", "a-check"])

    def test_versioned_queue_uses_new_ledger_and_preserves_historical_acceptance(self):
        historical = self.controller(self.manifest([self.task("a")]))
        old = historical.run(max_actions=20, max_seconds=2, poll_seconds=0)
        self.assertEqual(old["tasks"]["a"], "accepted")
        old_ledger = historical.db
        old_bytes = old_ledger.read_bytes()
        versioned = self.controller(self.manifest([self.native_task()],
                                                  schema="heleos.loop-controller/v2"))
        self.assertNotEqual(versioned.db, old_ledger)
        self.assertEqual(versioned.status()["reason"], "not_started")
        self.assertEqual(old_ledger.read_bytes(), old_bytes)

    def test_read_only_status_refuses_other_manifest_on_existing_ledger(self):
        first = self.controller(self.manifest([self.task("a")]))
        self.assertEqual(first.run(max_actions=20, max_seconds=2, poll_seconds=0)["tasks"]["a"],
                         "accepted")
        changed = self.controller(self.manifest([self.task("b")]))
        with self.assertRaisesRegex(loop.LoopError, "manifest changed"):
            changed.status()

    def test_versioned_real_dispatch_stops_at_unadmitted_review_boundary(self):
        path = self.manifest([self.native_task()], schema="heleos.loop-controller/v2")
        old_packet = self.repo / "old-admission.json"
        old_packet.write_text(json.dumps({"schema": "heleos.loop-p1-admission/v2"}))
        controller = loop.LoopController(path, guard=self.guard,
                                         admitted_digest=digest(path.read_bytes()),
                                         admission_path=old_packet,
                                         admission_sha256=digest(old_packet.read_bytes()))
        with self.assertRaisesRegex(loop.LoopError, "versioned real queue requires"):
            controller.tick()
        self.assertEqual(self.guard.launched, [])
        self.assertFalse(controller.db.exists())


if __name__ == "__main__":
    unittest.main()
