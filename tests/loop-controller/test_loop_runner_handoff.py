"""Synthetic runner receipts; no provider, model, network, or checkpoint."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from loop_runner_handoff import HandoffError, NativeChildDefects, import_candidate  # noqa: E402


def sha(data):
    return hashlib.sha256(data).hexdigest()


WORKFLOW_SCRIPT = "await agent(prompt, { label: 'review-a', effort: 'xhigh' })"


class RunnerHandoffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="loop-handoff-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.workspace = root / "runs"
        self.run = self.workspace / "run-001"
        self.checkout = self.run / "checkout"
        self.checkout.mkdir(parents=True)
        self.dest = root / "destination"
        self.dest.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.checkout)], check=True)
        (self.checkout / "base.txt").write_text("base")
        subprocess.run(["git", "-C", str(self.checkout), "add", "base.txt"], check=True)
        env = {"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
               "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        import os
        subprocess.run(["git", "-C", str(self.checkout), "commit", "-qm", "base"],
                       check=True, env={**os.environ, **env})
        head = subprocess.check_output(["git", "-C", str(self.checkout), "rev-parse", "HEAD"],
                                       text=True).strip()
        self.path = "scripts/experiments/grounding_dino_p1_harness.py"
        target = self.checkout / self.path
        target.parent.mkdir(parents=True)
        target.write_bytes(b"# synthetic preparation\n")
        self.task = json.dumps({"base_commit": head, "provider": "claude_code",
                                "input_data_class": "INTERNAL"},
                               sort_keys=True, separators=(",", ":")).encode()
        self.digest = sha(self.task)
        (self.run / "task.original.json").write_bytes(self.task)
        (self.run / "task.sha256").write_text(self.digest)
        self.handoff = {"task_digest": self.digest, "provider": "claude_code",
                        "terminal_state": "blocked", "candidate_commit": None,
                        "changed_paths": [self.path]}
        self.summary = {"schema": "heleos.worker-run/v1", "run_directory": str(self.run),
                        "checkout_path": str(self.checkout), "handoff": self.handoff,
                        "changed_files": [{"path": self.path, "sha256": sha(target.read_bytes()),
                                           "executable": False}],
                        "containment": {"mode": "macos_seatbelt",
                                        "host_path_writes_restricted": True},
                        "provider_exit_code": 0, "provider_invocations": 1,
                        "approved_internal_task_sha256": self.digest,
                        "stdout": {"truncated": False}, "stderr": {"truncated": False}}
        self.output = root / "guard-stdout.json"
        self.record = {"state": "succeeded", "outcome": "succeeded",
                       "stdout_path": str(self.output)}
        self.persist()

    def persist(self):
        (self.run / "handoff.json").write_text(json.dumps(self.handoff))
        (self.run / "run.json").write_text(json.dumps(self.summary))
        self.output.write_text(json.dumps(self.summary))

    def receive(self):
        return import_candidate(self.record, task_bytes=self.task,
                                workspace_root=self.workspace, destination=self.dest,
                                expected_paths=[self.path])

    def require_workflow(self):
        child = "docs/native-child-review.json"
        candidate_hash = sha((self.checkout / self.path).read_bytes())
        report = {"child_label": "review-a", "decision": "accepted",
                  "candidate_sha256": {self.path: candidate_hash},
                  "configured_model": "claude-opus-5-5", "configured_effort": "xhigh"}
        target = self.checkout / child
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report))
        self.handoff["changed_paths"].append(child)
        self.summary["changed_files"].append({"path": child, "sha256": sha(target.read_bytes()),
                                              "executable": False})
        self.record["required_workflow"] = {"child_label": "review-a",
                                            "child_report_path": child,
                                            "model": "claude-opus-5-5", "effort": "xhigh",
                                            "script_sha256": sha(WORKFLOW_SCRIPT.encode())}
        self.persist()
        return child

    def receive_workflow(self):
        return import_candidate(self.record, task_bytes=self.task,
                                workspace_root=self.workspace, destination=self.dest,
                                expected_paths=[self.path, "docs/native-child-review.json"])

    def typed_trace(self, raw):
        events = [json.loads(line) for line in raw.splitlines()]
        (self.run / "event-trace.jsonl").write_bytes(raw)
        self.summary["stdout"] = {"retained_bytes": 0, "truncated": True}
        self.summary["raw_stdout_omitted"] = True
        self.summary["stream_trace"] = {
            "schema": "heleos.claude-stream-trace/v1", "path": "event-trace.jsonl",
            "sha256": sha(raw), "retained_bytes": len(raw), "raw_sha256": sha(raw),
            "raw_bytes": len(raw), "events_seen": len(events), "events_retained": len(events),
            "thinking_events_omitted": 0, "malformed": False, "truncated": False,
            "complete": True,
        }
        (self.run / "trace-progress.json").write_text(json.dumps({
            "complete": True, "raw_bytes": len(raw), "events_seen": len(events),
            "events_retained": len(events), "thinking_events_omitted": 0,
            "malformed": False, "truncated": False}))
        (self.run / "stdout.bin").write_bytes(b"")
        self.persist()

    def require_workflow_receipt(self):
        child = self.require_workflow()
        self.record["required_workflow"]["report_binding"] = "controller_receipt"
        target = self.checkout / child
        target.write_text(json.dumps({"child_label": "review-a", "decision": "reviewed",
                                      "reviewed_paths": [self.path], "findings": [],
                                      "configured_model": "claude-opus-5-5",
                                      "configured_effort": "xhigh"}))
        self.summary["changed_files"][-1]["sha256"] = sha(target.read_bytes())
        self.persist()
        return target

    def workflow_trace(self, *, model="claude-opus-5-5", effort="xhigh", script=None,
                       child_done=True, terminal="completed", early_result=False,
                       final_result=True):
        tool_id, task_id, agent_id = "toolu-workflow-a", "wg-a", "agent-a"
        if script is None:
            script = WORKFLOW_SCRIPT.replace("'xhigh'", "'" + effort + "'")
        events = [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Workflow",
                "id": tool_id, "input": {"script": script}}]}},
            {"type": "system", "subtype": "task_started", "task_id": task_id,
             "tool_use_id": tool_id},
            {"type": "system", "subtype": "task_progress", "task_id": task_id,
             "tool_use_id": tool_id, "workflow_progress": [{"type": "workflow_agent",
                "label": "review-a", "model": model, "agentId": agent_id, "state": "start"}]},
        ]
        if child_done:
            events.append({"type": "system", "subtype": "task_progress", "task_id": task_id,
                           "tool_use_id": tool_id, "workflow_progress": [{"type": "workflow_agent",
                "label": "review-a", "model": model, "agentId": agent_id,
                "state": "done", "lastToolName": "Write"}]})
        if early_result:
            events.append({"type": "result", "subtype": "success", "is_error": False,
                           "result": "Child still running",
                           "modelUsage": {"claude-opus-5-5": {"canonicalModel": "claude-opus-5-5"}}})
        events.extend([
            {"type": "system", "subtype": "task_notification", "task_id": task_id,
             "tool_use_id": tool_id, "status": terminal, "output_file": "/tmp/workflow-child-output"},
        ])
        if final_result:
            events.append({"type": "result", "subtype": "success", "is_error": False,
                           "modelUsage": {"claude-opus-5-5": {"canonicalModel": "claude-opus-5-5"}},
                           "total_cost_usd": 1.25})
        raw = "".join(json.dumps(event) + "\n" for event in events).encode()
        (self.run / "stdout.bin").write_bytes(raw)
        return raw

    def test_completed_native_child_and_candidate_bound_report_import(self):
        self.require_workflow()
        raw = self.workflow_trace()
        evidence = self.receive_workflow()
        self.assertEqual(evidence["native_workflow"]["trace_sha256"], sha(raw))
        self.assertEqual(evidence["native_workflow"]["child_label"], "review-a")
        self.assertEqual(evidence["native_workflow"]["model"], "claude-opus-5-5")
        self.assertEqual(evidence["native_workflow"]["effort_configured"], "xhigh")
        self.assertEqual(evidence["native_workflow"]["candidate_sha256"],
                         {self.path: sha((self.checkout / self.path).read_bytes())})
        self.assertEqual(self.receive_workflow(), evidence)

    def test_early_parent_result_requires_later_terminal_result(self):
        self.require_workflow()
        self.workflow_trace(early_result=True)
        self.receive_workflow()
        self.workflow_trace(early_result=True, final_result=False)
        with self.assertRaisesRegex(HandoffError, "Workflow terminal result"):
            self.receive_workflow()

    def test_controller_receipt_binds_child_report_and_dynamic_candidate(self):
        target = self.require_workflow_receipt()
        self.workflow_trace()
        evidence = self.receive_workflow()["native_workflow"]
        self.assertEqual(evidence["candidate_sha256"],
                         {self.path: sha((self.checkout / self.path).read_bytes())})
        self.assertEqual(evidence["child_report_sha256"], sha(target.read_bytes()))
        report = json.loads(target.read_text())
        report["reviewed_paths"] = ["unrelated.txt"]
        target.write_text(json.dumps(report))
        self.summary["changed_files"][-1]["sha256"] = sha(target.read_bytes())
        self.persist()
        with self.assertRaisesRegex(HandoffError, "child report"):
            self.receive_workflow()

    def test_controller_receipt_distinguishes_bound_defects_without_import(self):
        target = self.require_workflow_receipt()
        raw = self.workflow_trace()
        report = json.loads(target.read_text())
        report.update(decision="defects_found", findings=[{"id": "F1", "summary": "false departure"}],
                      checked_without_defect={"input_gates": "reviewed"})
        target.write_text(json.dumps(report))
        self.summary["changed_files"][-1]["sha256"] = sha(target.read_bytes())
        self.persist()
        with self.assertRaisesRegex(NativeChildDefects, "native_child_defects_found: child report records 1 finding"):
            self.receive_workflow()
        self.assertFalse((self.dest / self.path).exists())

        (self.run / "stdout.bin").write_bytes(b"")
        with self.assertRaisesRegex(HandoffError, "exactly one Workflow call required"):
            self.receive_workflow()
        (self.run / "stdout.bin").write_bytes(raw)

        report["reviewed_paths"] = ["unrelated.txt"]
        target.write_text(json.dumps(report))
        self.summary["changed_files"][-1]["sha256"] = sha(target.read_bytes())
        self.persist()
        with self.assertRaisesRegex(HandoffError, "child report does not bind candidate"):
            self.receive_workflow()

    def test_complete_typed_trace_imports_and_mismatch_or_incomplete_stops(self):
        self.require_workflow_receipt()
        raw = self.workflow_trace()
        self.typed_trace(raw)
        evidence = self.receive_workflow()
        self.assertEqual(evidence["native_workflow"]["trace_sha256"], sha(raw))
        (self.run / "event-trace.jsonl").write_bytes(raw + b"x")
        with self.assertRaisesRegex(HandoffError, "typed stream trace bytes"):
            self.receive_workflow()
        (self.run / "event-trace.jsonl").write_bytes(raw)
        self.summary["stream_trace"]["complete"] = False
        self.persist()
        with self.assertRaisesRegex(HandoffError, "typed stream trace is incomplete"):
            self.receive_workflow()

    def test_typed_trace_rejects_retained_raw_stdout(self):
        self.require_workflow_receipt()
        self.typed_trace(self.workflow_trace())
        (self.run / "stdout.bin").write_bytes(b"private output")
        with self.assertRaisesRegex(HandoffError, "typed stream trace is incomplete"):
            self.receive_workflow()

    def test_typed_trace_over_one_megabyte_keeps_native_terminal(self):
        self.require_workflow_receipt()
        terminal = self.workflow_trace()
        tool = (json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5",
                            "content": [{"type": "tool_use", "name": "Read",
                                         "id": "x" * 300}]}}) + "\n").encode()
        raw = tool * 4000 + terminal
        self.assertGreater(len(raw), 1_048_576)
        self.typed_trace(raw)
        evidence = self.receive_workflow()
        self.assertEqual(evidence["native_workflow"]["trace_sha256"], sha(raw))

    def test_typed_trace_rejects_workflow_input_extra_fields(self):
        self.require_workflow_receipt()
        events = [json.loads(line) for line in self.workflow_trace().splitlines()]
        events[0]["message"]["content"][0]["input"]["private_text"] = "secret marker"
        self.typed_trace(b"".join((json.dumps(event) + "\n").encode() for event in events))
        with self.assertRaisesRegex(HandoffError, "projection differs"):
            self.receive_workflow()

    def test_typed_trace_rejects_child_event_reordering_and_failure(self):
        self.require_workflow_receipt()
        original = [json.loads(line) for line in self.workflow_trace().splitlines()]
        cases = []
        done_before_start = [json.loads(json.dumps(event)) for event in original]
        done_before_start[2], done_before_start[3] = done_before_start[3], done_before_start[2]
        cases.append(done_before_start)
        notification_before_done = [json.loads(json.dumps(event)) for event in original]
        notification_before_done[3], notification_before_done[4] = (
            notification_before_done[4], notification_before_done[3])
        cases.append(notification_before_done)
        failed_then_completed = [json.loads(json.dumps(event)) for event in original]
        failed_then_completed.insert(4, {**failed_then_completed[4], "status": "failed"})
        cases.append(failed_then_completed)
        child_failed_then_done = [json.loads(json.dumps(event)) for event in original]
        child_failed_then_done.insert(3, json.loads(json.dumps(child_failed_then_done[3])))
        child_failed_then_done[3]["workflow_progress"][0]["state"] = "failed"
        cases.append(child_failed_then_done)
        start_before_call = [json.loads(json.dumps(event)) for event in original]
        start_before_call[0], start_before_call[1] = start_before_call[1], start_before_call[0]
        cases.append(start_before_call)
        unknown_child_state = [json.loads(json.dumps(event)) for event in original]
        unknown_child_state.insert(3, json.loads(json.dumps(unknown_child_state[3])))
        unknown_child_state[3]["workflow_progress"][0]["state"] = "unexpected"
        cases.append(unknown_child_state)
        for events in cases:
            with self.subTest(events=events):
                self.typed_trace(b"".join((json.dumps(event) + "\n").encode()
                                          for event in events))
                with self.assertRaisesRegex(HandoffError, "required native Workflow"):
                    self.receive_workflow()

    def test_typed_trace_accepts_child_progress_only_between_start_and_done(self):
        self.require_workflow_receipt()
        original = [json.loads(line) for line in self.workflow_trace().splitlines()]
        progress = json.loads(json.dumps(original[2]))
        progress["workflow_progress"][0].update(state="progress", lastToolName="Read")
        valid = [*original[:3], progress, *original[3:]]
        self.typed_trace(b"".join((json.dumps(event) + "\n").encode() for event in valid))
        self.receive_workflow()

        after_done = [*original[:4], progress, *original[4:]]
        self.typed_trace(b"".join((json.dumps(event) + "\n").encode()
                                  for event in after_done))
        with self.assertRaisesRegex(HandoffError, "required native Workflow"):
            self.receive_workflow()

    def test_native_child_wrong_model_effort_or_missing_completion_is_incomplete(self):
        self.require_workflow()
        for change in ({"model": "claude-sonnet-4"}, {"effort": "medium"},
                       {"child_done": False}, {"terminal": "running"}):
            with self.subTest(change=change):
                self.workflow_trace(**change)
                with self.assertRaisesRegex(HandoffError, "required native Workflow"):
                    self.receive_workflow()
                self.assertFalse((self.dest / self.path).exists())

    def test_workflow_decoy_effort_text_cannot_replace_pinned_script(self):
        self.require_workflow()
        decoy = "// label: 'review-a', effort: 'xhigh'\n" + WORKFLOW_SCRIPT.replace(
            "'xhigh'", "'medium'")
        self.workflow_trace(script=decoy)
        with self.assertRaisesRegex(HandoffError, "Workflow script differs"):
            self.receive_workflow()
        self.assertFalse((self.dest / self.path).exists())

    def test_native_child_report_must_bind_exact_candidate_bytes(self):
        child = self.require_workflow()
        self.workflow_trace()
        target = self.checkout / child
        report = json.loads(target.read_text())
        report["candidate_sha256"] = {self.path: "0" * 64}
        target.write_text(json.dumps(report))
        self.summary["changed_files"][-1]["sha256"] = sha(target.read_bytes())
        self.persist()
        with self.assertRaisesRegex(HandoffError, "required native Workflow"):
            self.receive_workflow()
        self.assertFalse((self.dest / self.path).exists())

    def test_required_native_workflow_missing_trace_is_incomplete_before_copy(self):
        self.require_workflow()
        (self.run / "stdout.bin").write_text(json.dumps({"type": "result", "subtype": "success",
                                                         "total_cost_usd": 4.8271354}) + "\n")
        with self.assertRaisesRegex(HandoffError, "required native Workflow"):
            self.receive_workflow()
        self.assertFalse((self.dest / self.path).exists())

    def test_budget_exhaustion_cannot_substitute_for_required_child(self):
        self.require_workflow()
        (self.run / "stdout.bin").write_text(json.dumps({"type": "result", "subtype": "success",
                                                         "total_cost_usd": 4.99,
                                                         "result": "Skipped child because budget remained low"}) + "\n")
        with self.assertRaisesRegex(HandoffError, "required native Workflow"):
            self.receive_workflow()
        self.assertFalse((self.dest / self.path).exists())

    def test_exact_contained_candidate_import_and_restart(self):
        first = self.receive()
        self.assertEqual(first["candidate_sha256"][self.path],
                         sha((self.dest / self.path).read_bytes()))
        self.assertEqual(self.receive(), first)

    def test_changed_candidate_or_destination_stops(self):
        (self.checkout / self.path).write_text("changed")
        with self.assertRaisesRegex(HandoffError, "bytes"):
            self.receive()
        (self.checkout / self.path).write_text("# synthetic preparation\n")
        (self.dest / self.path).parent.mkdir(parents=True)
        (self.dest / self.path).write_text("foreign")
        with self.assertRaisesRegex(HandoffError, "destination candidate"):
            self.receive()

    def test_scope_and_containment_mismatch_stop_before_copy(self):
        self.summary["changed_files"].append({"path": "secrets.txt", "sha256": sha(b"x"),
                                              "executable": False})
        self.persist()
        with self.assertRaisesRegex(HandoffError, "inventory"):
            self.receive()
        self.summary["changed_files"].pop()
        self.summary["containment"]["mode"] = "none"
        self.persist()
        with self.assertRaisesRegex(HandoffError, "containment"):
            self.receive()
        self.assertFalse((self.dest / self.path).exists())

    def test_wrong_task_and_symlinked_candidate_stop(self):
        self.handoff["task_digest"] = "0" * 64
        self.persist()
        with self.assertRaisesRegex(HandoffError, "identity"):
            self.receive()
        self.handoff["task_digest"] = self.digest
        target = self.checkout / self.path
        target.unlink()
        target.symlink_to(self.checkout / "base.txt")
        self.persist()
        with self.assertRaisesRegex(HandoffError, "symlink"):
            self.receive()


if __name__ == "__main__":
    unittest.main()
