"""Honest identified external-session assignment and import contracts."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build.errors import ContractError, TransitionError
from tools.helios_build.external_sessions import (
    begin_external_session,
    import_external_handoff,
)
from tools.helios_build.graph import ControllerLock, transition_node_locked
from tools.helios_build.ledger import append_event, replay_events
from tools.helios_build.paths import BuildPaths
from tools.helios_build.profiles import load_worker_profile, worker_profile_sha256
from tools.helios_build.review import import_external_review
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore


class BuildFabricRepo:
    """Small real Git repository used by both focused Task 6 modules."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.repo = self.root / "repo"
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(
            source / "build_control" / "schemas",
            self.repo / "build_control" / "schemas",
        )
        shutil.copytree(
            source / "build_control" / "worker_profiles",
            self.repo / "build_control" / "worker_profiles",
        )
        (self.repo / "build_control" / "tasks").mkdir(parents=True)
        (self.repo / "build_control" / "graph" / "receipts").mkdir(parents=True)
        (self.repo / "build_control" / "graph" / "events.jsonl").write_bytes(b"")
        (self.repo / "build_control" / "graph" / "routing.jsonl").write_bytes(b"")
        (self.repo / "checks").mkdir()
        (self.repo / "checks" / ".keep").write_text("", encoding="utf-8")
        for index in range(12):
            (self.repo / f"owned-{index}.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "forbidden.py").write_text("BASE = True\n", encoding="utf-8")
        (self.repo / "schema.json").write_text("{}\n", encoding="utf-8")
        self._git("init")
        self._git("config", "user.email", "fixture@example.invalid")
        self._git("config", "user.name", "Build Fixture")
        self._git("add", ".")
        self._git("commit", "-m", "fixture base")
        self.base_sha = self._git("rev-parse", "HEAD").stdout.strip()
        self.paths = BuildPaths(self.repo.resolve(), (self.root / "state").resolve())
        self._patch_ordinal = 0

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def add_task(
        self,
        task_id: str,
        *,
        owned_file: str = "owned-0.txt",
        builder: str = "codex-builder-v1",
        reviewer: str = "codex-reviewer-v1",
        affected_exit: int = 0,
        required_records: list[str] | None = None,
        required_packets: list[str] | None = None,
        ownership_updates: dict[str, object] | None = None,
        frozen_interfaces: list[str] | None = None,
        schema_impact: list[str] | None = None,
        correction_round: int = 0,
        supersedes: str | None = None,
    ) -> tuple[str, dict[str, object], str]:
        ownership: dict[str, object] = {
            "files": [owned_file],
            "path_globs": [],
            "modules": [],
            "migrations": [],
            "schemas": [],
            "public_interfaces": [],
            "forbidden_paths": ["forbidden.py", "build_control/**"],
        }
        if ownership_updates:
            ownership.update(ownership_updates)
        command = {
            "command_id": "affected-check",
            "gate": "AFFECTED_INTEGRATION",
            "argv": [sys.executable, "-c", f"raise SystemExit({affected_exit})"],
            "cwd": "checks",
            "expected_exit_code": 0,
            "timeout_seconds": 5,
        }
        task: dict[str, object] = {
            "protocol": "helios.build.task-manifest/v1",
            "task_id": task_id,
            "capability_id": "task-6-fixture",
            "capability": "Exercise verified Build Fabric handoffs.",
            "node_type": "BuildTask",
            "base_commit_sha": self.base_sha,
            "dependency_node_ids": [],
            "roles": {
                "builder_profile_id": builder,
                "reviewer_profile_id": reviewer,
                "integrator_profile_id": "codex-control-v1",
            },
            "ownership": ownership,
            "frozen_interfaces": frozen_interfaces or [],
            "schema_impact": schema_impact or [],
            "required_source_record_ids": required_records or [],
            "required_source_packet_ids": required_packets or [],
            "evidence_threshold": len(required_records or []) + len(required_packets or []),
            "deliverables": [owned_file],
            "acceptance": {
                "behaviors": ["verified handoff"],
                "commands": [command],
            },
            "budgets": {
                "implementation_seconds": 5,
                "cost_microusd": 0,
                "focused_test_seconds": 5,
                "affected_integration_seconds": 5,
                "milestone_seconds": 5,
                "maximum_correction_rounds": 2,
            },
            "checkpoint_boundaries": [],
            "conditions": {"stop": [], "block": [], "reroute": [], "escalation": []},
            "correction_round": correction_round,
            "supersedes_task_manifest_sha256": supersedes,
            "resume_from_checkpoint_sha256": None,
            "created_at": "2026-09-01T00:00:00Z",
            "created_by_profile_id": "codex-control-v1",
        }
        task_sha = sha256_hex(canonical_json_bytes(task))
        task_path = self.repo / "build_control" / "tasks" / f"{task_sha}.json"
        task_path.write_bytes(canonical_json_bytes(task))
        node_id = f"node-{task_id}"
        graph = {
            "protocol": "helios.build.graph-manifest/v1",
            "graph_id": f"graph-{task_id}",
            "nodes": [{
                "node_id": node_id,
                "node_type": "BuildTask",
                "manifest_sha256": task_sha,
            }],
            "edges": [],
            "created_at": "2026-09-01T00:00:00Z",
            "created_by_profile_id": "codex-control-v1",
        }
        graph_path = self.repo / "build_control" / "graph" / f"{task_id}.json"
        graph_path.write_bytes(canonical_json_bytes(graph))
        self._git("add", str(task_path.relative_to(self.repo)), str(graph_path.relative_to(self.repo)))
        self._git("commit", "-m", f"add {task_id}")
        append_event(
            self.repo / "build_control" / "graph" / "events.jsonl",
            {
                "protocol": "helios.build.lifecycle-event/v1",
                "node_id": node_id,
                "prior_state": "DRAFT",
                "new_state": "READY",
                "reason_code": "TASK_CONTRACT_READY",
                "actor_profile_id": "codex-control-v1",
                "attempt_manifest_sha256": None,
                "external_session_assignment_sha256": None,
                "routing_event_sha256": None,
                "recorded_at": "2026-09-01T00:00:00Z",
            },
            self.paths.state_root,
        )
        self.paths.state_root.chmod(0o700)
        return task_sha, task, node_id

    def make_patch(self, changes: dict[str, str]) -> bytes:
        self._patch_ordinal += 1
        worktree = self.root / f"patch-source-{self._patch_ordinal}"
        self._git("worktree", "add", "--detach", str(worktree), self.base_sha)
        for relative, content in changes.items():
            target = worktree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        self._git_at(worktree, "add", "-N", "--all")
        return subprocess.run(
            [
                "git", "-C", str(worktree), "diff", "--binary", "--full-index",
                self.base_sha, "--",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout

    def write_external_handoff(
        self,
        assignment: object,
        task: dict[str, object],
        patch_bytes: bytes,
        *,
        raw_bytes: bytes = b"RAW-EXTERNAL-EVIDENCE-SENTINEL",
        returned_at: str | None = None,
        patch_sha: str | None = None,
        raw_sha: str | None = None,
        assumptions: list[str] | None = None,
        extra: dict[str, object] | None = None,
    ) -> tuple[Path, Path, Path, dict[str, object]]:
        incoming = self.paths.state_root / "incoming"
        incoming.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw_path = incoming / f"raw-{assignment.sha256}.bin"
        raw_path.write_bytes(raw_bytes)
        raw_path.chmod(0o600)
        patch_path = self.root / f"patch-{assignment.sha256}.diff"
        patch_path.write_bytes(patch_bytes)
        payload: dict[str, object] = {
            "protocol": "helios.build.external-session-handoff/v1",
            "assignment_sha256": assignment.sha256,
            "task_manifest_sha256": assignment.task_manifest_sha256,
            "base_commit_sha": assignment.base_commit_sha,
            "worker_profile_sha256": assignment.worker_profile_sha256,
            "provider": str(assignment.provider),
            "session_id": assignment.session_id,
            "patch_sha256": patch_sha or sha256_hex(patch_bytes),
            "raw_evidence_sha256": raw_sha or sha256_hex(raw_bytes),
            "commands_executed": ["focused-check"],
            "focused_test_results": ["PASS"],
            "assumptions": assumptions or [],
            "unresolved_issues": [],
            "dependency_effects": [],
            "security_effects": [],
            "source_record_ids": task["required_source_record_ids"],
            "source_packet_ids": task["required_source_packet_ids"],
            "duration_ms": 1,
            "cost_microusd": 0,
            "returned_at": returned_at or assignment.created_at,
            "target_host_eligible": False,
        }
        if extra:
            payload.update(extra)
        handoff_path = self.root / f"handoff-{assignment.sha256}.json"
        handoff_path.write_bytes(canonical_json_bytes(payload))
        return handoff_path, patch_path, raw_path, payload

    def import_builder(
        self,
        task_sha: str,
        task: dict[str, object],
        *,
        session_id: str = "builder-session",
        owned_file: str = "owned-0.txt",
    ) -> tuple[object, object, bytes, Path]:
        assignment = begin_external_session(
            self.paths,
            task_sha,
            str(task["roles"]["builder_profile_id"]),
            "CODEX",
            session_id,
            "BUILDER",
            "EXTERNAL_SESSION_SELECTED",
        )
        patch_bytes = self.make_patch({owned_file: "external change\n"})
        handoff, patch_path, raw_path, _ = self.write_external_handoff(
            assignment, task, patch_bytes
        )
        imported = import_external_handoff(
            self.paths, task_sha, assignment.sha256, handoff, patch_path, raw_path
        )
        return assignment, imported, patch_bytes, raw_path

    def write_review(
        self,
        assignment: object,
        task: dict[str, object],
        handoff_sha: str,
        *,
        verdict: str = "ACCEPTED",
        findings: list[dict[str, str]] | None = None,
        raw_bytes: bytes = b"RAW-REVIEW-EVIDENCE-SENTINEL",
        extra: dict[str, object] | None = None,
    ) -> tuple[Path, Path]:
        incoming = self.paths.state_root / "incoming"
        incoming.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw_path = incoming / f"review-{assignment.sha256}.bin"
        raw_path.write_bytes(raw_bytes)
        raw_path.chmod(0o600)
        payload: dict[str, object] = {
            "protocol": "helios.build.external-session-review/v1",
            "assignment_sha256": assignment.sha256,
            "task_manifest_sha256": assignment.task_manifest_sha256,
            "handoff_sha256": handoff_sha,
            "reviewer_profile_sha256": assignment.worker_profile_sha256,
            "provider": str(assignment.provider),
            "session_id": assignment.session_id,
            "raw_evidence_sha256": sha256_hex(raw_bytes),
            "verdict": verdict,
            "findings": findings or [],
            "command_receipt_sha256s": [],
            "source_verification": {
                "source_record_ids": task["required_source_record_ids"],
                "source_packet_ids": task["required_source_packet_ids"],
            },
            "reviewed_at": assignment.created_at,
            "duration_ms": 1,
            "target_host_eligible": False,
        }
        if extra:
            payload.update(extra)
        review_path = self.root / f"review-{assignment.sha256}.json"
        review_path.write_bytes(canonical_json_bytes(payload))
        return review_path, raw_path

    def event_state(self, node_id: str) -> str:
        records = [
            event for event in replay_events(
                self.repo / "build_control" / "graph" / "events.jsonl"
            ) if event["node_id"] == node_id
        ]
        return str(records[-1]["new_state"])

    def git_control_contains(self, value: bytes) -> bool:
        for path in self.repo.rglob("*"):
            if ".git" in path.parts or not path.is_file():
                continue
            if value in path.read_bytes():
                return True
        return False

    def assignment_payload(self, digest: str) -> dict[str, object]:
        return ContentAddressedStore(
            self.repo / "build_control" / "graph"
        ).get_json(digest)

    def _git(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repo), *argv],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @staticmethod
    def _git_at(path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(path), *argv],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


class ExternalSessionTests(BuildFabricRepo, unittest.TestCase):
    def test_dispatch_and_import_require_the_exact_designated_assignment(self) -> None:
        task_sha, task, node_id = self.add_task(
            "assignment-lineage", owned_file="owned-10.txt"
        )
        reviewer_profile = load_worker_profile(
            self.repo / "build_control" / "worker_profiles" / "codex-reviewer-v1.json",
            SchemaRegistry(self.repo),
        )
        wrong_payload = {
            "protocol": "helios.build.external-session-assignment/v1",
            "assignment_id": "wrong-designated-builder",
            "task_manifest_sha256": task_sha,
            "base_commit_sha": task["base_commit_sha"],
            "role": "BUILDER",
            "worker_profile_id": reviewer_profile.profile_id,
            "worker_profile_sha256": worker_profile_sha256(reviewer_profile),
            "provider": "CODEX",
            "session_id": "wrong-designated-session",
            "transport": "EXTERNAL_SESSION",
            "routing_action": "EXTERNAL_SESSION_ASSIGN",
            "routing_reason": "EXTERNAL_SESSION_SELECTED",
            "created_at": "2026-09-01T00:00:01Z",
            "created_by_profile_id": "codex-control-v1",
            "target_host_eligible": False,
        }
        wrong = ContentAddressedStore(
            self.repo / "build_control" / "graph"
        ).put_json("external-session-assignments", wrong_payload)
        with self.assertRaisesRegex(TransitionError, "designated builder profile"):
            with ControllerLock(self.paths) as lock:
                transition_node_locked(
                    lock,
                    node_id,
                    "READY",
                    "DISPATCHED",
                    "EXTERNAL_SESSION_DISPATCHED",
                    "codex-control-v1",
                    external_session_assignment_sha256=wrong.sha256,
                )

        import_task_sha, import_task, import_node_id = self.add_task(
            "import-lineage", owned_file="owned-9.txt"
        )
        assignment = begin_external_session(
            self.paths, import_task_sha, "codex-builder-v1", "CODEX", "bound-session",
            "BUILDER", "EXTERNAL_SESSION_SELECTED",
        )
        orphan_payload = dict(self.assignment_payload(assignment.sha256))
        orphan_payload["assignment_id"] = "orphan-builder-assignment"
        orphan_payload["session_id"] = "orphan-session"
        orphan = ContentAddressedStore(
            self.repo / "build_control" / "graph"
        ).put_json("external-session-assignments", orphan_payload)
        orphan_assignment = replace(
            assignment,
            sha256=orphan.sha256,
            path=orphan.path,
            session_id="orphan-session",
        )
        patch_bytes = self.make_patch({"owned-9.txt": "orphan result\n"})
        handoff, patch_path, raw_path, _ = self.write_external_handoff(
            orphan_assignment, import_task, patch_bytes
        )
        with self.assertRaisesRegex(ContractError, "dispatch.*exact assignment"):
            import_external_handoff(
                self.paths,
                import_task_sha,
                orphan.sha256,
                handoff,
                patch_path,
                raw_path,
            )
        self.assertEqual(self.event_state(import_node_id), "DISPATCHED")

    def test_blocked_successor_route_requires_original_local_dispatch(self) -> None:
        predecessor_sha, predecessor, predecessor_node = self.add_task(
            "external-predecessor", owned_file="owned-11.txt"
        )
        begin_external_session(
            self.paths, predecessor_sha, "codex-builder-v1", "CODEX",
            "external-predecessor-session", "BUILDER", "EXTERNAL_SESSION_SELECTED",
        )
        successor_sha, successor, successor_node = self.add_task(
            "blocked-successor",
            owned_file="owned-11.txt",
            supersedes=predecessor_sha,
        )
        with ControllerLock(self.paths) as lock:
            transition_node_locked(
                lock,
                predecessor_node,
                "DISPATCHED",
                "BLOCKED",
                "LOCAL_ADAPTER_BLOCKED",
                "codex-control-v1",
            )
        route_sha = append_event(
            self.repo / "build_control" / "graph" / "routing.jsonl",
            {
                "protocol": "helios.build.routing-event/v1",
                "task_manifest_sha256": predecessor_sha,
                "action": "REROUTE",
                "from_profile_id": predecessor["roles"]["builder_profile_id"],
                "to_profile_id": successor["roles"]["builder_profile_id"],
                "reason_code": "WORKER_REROUTE",
                "successor_task_manifest_sha256": successor_sha,
                "checkpoint_sha256": None,
                "recorded_at": "2026-09-01T00:00:02Z",
                "actor_profile_id": "codex-control-v1",
            },
            self.paths.state_root,
        )
        with ControllerLock(self.paths) as lock:
            transition_node_locked(
                lock,
                predecessor_node,
                "BLOCKED",
                "SUPERSEDED",
                "WORKER_REROUTE",
                "codex-control-v1",
                routing_event_sha256=route_sha,
            )
        with self.assertRaisesRegex(ContractError, "original local dispatch"):
            begin_external_session(
                self.paths, successor_sha, "codex-builder-v1", "CODEX",
                "blocked-successor-session", "BUILDER",
                "LOCAL_ADAPTER_BLOCKED_SUCCESSOR",
            )
        self.assertEqual(self.event_state(successor_node), "READY")

    def test_assignment_is_dedicated_dispatch_proof_and_counts_lane(self) -> None:
        routing_before = (self.repo / "build_control" / "graph" / "routing.jsonl").read_bytes()
        assignments = []
        for index in range(3):
            task_sha, _, _ = self.add_task(
                f"lane-{index}", owned_file=f"owned-{index}.txt"
            )
            assignments.append(begin_external_session(
                self.paths, task_sha, "codex-builder-v1", "CODEX",
                f"session-{index}", "BUILDER", "EXTERNAL_SESSION_SELECTED",
            ))
        fourth_sha, _, fourth_node = self.add_task("lane-3", owned_file="owned-3.txt")
        with self.assertRaisesRegex(TransitionError, "three implementation"):
            begin_external_session(
                self.paths, fourth_sha, "codex-builder-v1", "CODEX",
                "session-3", "BUILDER", "EXTERNAL_SESSION_SELECTED",
            )

        assignment = assignments[0]
        payload = self.assignment_payload(assignment.sha256)
        self.assertEqual(payload["transport"], "EXTERNAL_SESSION")
        self.assertFalse(payload["target_host_eligible"])
        self.assertFalse(
            {"attempt_manifest_sha256", "preflight_receipt_sha256",
             "adapter_configuration_sha256", "availability"} & payload.keys()
        )
        events = replay_events(self.repo / "build_control" / "graph" / "events.jsonl")
        dispatch = next(
            event for event in events
            if event["node_id"] == "node-lane-0" and event["new_state"] == "DISPATCHED"
        )
        self.assertIsNone(dispatch["attempt_manifest_sha256"])
        self.assertEqual(dispatch["external_session_assignment_sha256"], assignment.sha256)
        self.assertEqual(
            (self.repo / "build_control" / "graph" / "routing.jsonl").read_bytes(),
            routing_before,
        )
        self.assertEqual(self.event_state(fourth_node), "READY")
        self.assertFalse((self.paths.state_root / "preflight").exists())

    def test_invalid_route_and_state_pairs_fail_without_mutation(self) -> None:
        task_sha, _, node_id = self.add_task("bad-route", owned_file="owned-4.txt")
        events_before = (self.repo / "build_control" / "graph" / "events.jsonl").read_bytes()
        with self.assertRaisesRegex(ContractError, "blocked local successor"):
            begin_external_session(
                self.paths, task_sha, "codex-builder-v1", "CODEX", "bad-route",
                "BUILDER", "LOCAL_ADAPTER_BLOCKED_SUCCESSOR",
            )
        with self.assertRaisesRegex(ContractError, "provider"):
            begin_external_session(
                self.paths, task_sha, "codex-builder-v1", "IMAGINARY", "bad-provider",
                "BUILDER", "EXTERNAL_SESSION_SELECTED",
            )
        with self.assertRaisesRegex(TransitionError, "RETURNED"):
            begin_external_session(
                self.paths, task_sha, "codex-reviewer-v1", "CODEX", "early-review",
                "REVIEWER", "EXTERNAL_SESSION_SELECTED",
            )
        self.assertEqual(
            (self.repo / "build_control" / "graph" / "events.jsonl").read_bytes(),
            events_before,
        )
        self.assertEqual(self.event_state(node_id), "READY")
        assignment_root = (
            self.repo / "build_control" / "graph" / "external-session-assignments"
        )
        self.assertFalse(assignment_root.exists())

    def test_handoff_import_verifies_hash_time_scope_and_replay_without_raw_git(self) -> None:
        task_sha, task, node_id = self.add_task("handoff", owned_file="owned-5.txt")
        assignment = begin_external_session(
            self.paths, task_sha, "codex-builder-v1", "CODEX", "handoff-builder",
            "BUILDER", "EXTERNAL_SESSION_SELECTED",
        )
        patch_bytes = self.make_patch({"owned-5.txt": "verified external patch\n"})
        handoff, patch_path, raw_path, payload = self.write_external_handoff(
            assignment, task, patch_bytes,
            raw_bytes=b"PRIVATE-RAW-CUSTODY-SENTINEL",
            returned_at="2000-01-01T00:00:00Z",
        )
        with self.assertRaisesRegex(ContractError, "before assignment"):
            import_external_handoff(
                self.paths, task_sha, assignment.sha256, handoff, patch_path, raw_path
            )
        payload["returned_at"] = assignment.created_at
        payload["patch_sha256"] = "f" * 64
        handoff.write_bytes(canonical_json_bytes(payload))
        with self.assertRaisesRegex(ContractError, "patch.*digest"):
            import_external_handoff(
                self.paths, task_sha, assignment.sha256, handoff, patch_path, raw_path
            )
        payload["patch_sha256"] = sha256_hex(patch_bytes)
        payload["raw_evidence_sha256"] = "e" * 64
        handoff.write_bytes(canonical_json_bytes(payload))
        with self.assertRaisesRegex(ContractError, "raw evidence.*digest"):
            import_external_handoff(
                self.paths, task_sha, assignment.sha256, handoff, patch_path, raw_path
            )
        payload["raw_evidence_sha256"] = sha256_hex(raw_path.read_bytes())
        handoff.write_bytes(canonical_json_bytes(payload))

        first = import_external_handoff(
            self.paths, task_sha, assignment.sha256, handoff, patch_path, raw_path
        )
        replay = import_external_handoff(
            self.paths, task_sha, assignment.sha256, handoff, patch_path, raw_path
        )
        self.assertEqual(first.sha256, replay.sha256)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.files_changed, ("owned-5.txt",))
        self.assertEqual(first.patch_sha256, sha256_hex(patch_bytes))
        self.assertIsNone(first.attempt_manifest_sha256)
        self.assertFalse(first.target_host_eligible)
        self.assertEqual(self.event_state(node_id), "RETURNED")
        payload["returned_at"] = "2999-01-01T00:00:00Z"
        handoff.write_bytes(canonical_json_bytes(payload))
        with self.assertRaisesRegex(ContractError, "differing handoff"):
            import_external_handoff(
                self.paths, task_sha, assignment.sha256, handoff, patch_path, raw_path
            )
        self.assertFalse(self.git_control_contains(b"PRIVATE-RAW-CUSTODY-SENTINEL"))
        self.assertFalse(self.git_control_contains(str(raw_path).encode("utf-8")))

    def test_unowned_scope_and_local_relabel_are_rejected(self) -> None:
        task_sha, task, node_id = self.add_task("scope", owned_file="owned-6.txt")
        assignment = begin_external_session(
            self.paths, task_sha, "codex-builder-v1", "CODEX", "scope-builder",
            "BUILDER", "EXTERNAL_SESSION_SELECTED",
        )
        patch_bytes = self.make_patch({"owned-7.txt": "unowned\n"})
        handoff, patch_path, raw_path, payload = self.write_external_handoff(
            assignment, task, patch_bytes
        )
        with self.assertRaisesRegex(ContractError, "[Aa]dditional properties"):
            payload["transport"] = "LOCAL_ADAPTER"
            handoff.write_bytes(canonical_json_bytes(payload))
            import_external_handoff(
                self.paths, task_sha, assignment.sha256, handoff, patch_path, raw_path
            )
        del payload["transport"]
        handoff.write_bytes(canonical_json_bytes(payload))
        with self.assertRaisesRegex(Exception, "unowned.*owned-7.txt"):
            import_external_handoff(
                self.paths, task_sha, assignment.sha256, handoff, patch_path, raw_path
            )
        self.assertEqual(self.event_state(node_id), "DISPATCHED")

    def test_review_requires_designated_fresh_profile_and_session(self) -> None:
        task_sha, task, node_id = self.add_task("independent", owned_file="owned-8.txt")
        builder, handoff, _, _ = self.import_builder(
            task_sha, task, session_id="shared-session", owned_file="owned-8.txt"
        )
        with self.assertRaisesRegex(ContractError, "designated reviewer"):
            begin_external_session(
                self.paths, task_sha, "codex-builder-v1", "CODEX", "other-session",
                "REVIEWER", "EXTERNAL_SESSION_SELECTED",
            )
        with self.assertRaisesRegex(ContractError, "session.*differ"):
            begin_external_session(
                self.paths, task_sha, "codex-reviewer-v1", "CODEX", builder.session_id,
                "REVIEWER", "EXTERNAL_SESSION_SELECTED",
            )
        reviewer = begin_external_session(
            self.paths, task_sha, "codex-reviewer-v1", "CODEX", "fresh-review-session",
            "REVIEWER", "EXTERNAL_SESSION_SELECTED",
        )
        review_path, raw_path = self.write_review(reviewer, task, handoff.sha256)
        receipt = import_external_review(
            self.paths, task_sha, reviewer.sha256, review_path, raw_path
        )
        self.assertEqual(receipt.transport, "EXTERNAL_SESSION")
        self.assertFalse(receipt.target_host_eligible)
        self.assertEqual(self.event_state(node_id), "ACCEPTED")
        provider_review = json.loads(review_path.read_text(encoding="utf-8"))
        provider_review["command_receipt_sha256s"] = list(
            receipt.command_receipt_sha256s
        )
        review_path.write_bytes(canonical_json_bytes(provider_review))
        with self.assertRaisesRegex(ContractError, "differing review"):
            import_external_review(
                self.paths, task_sha, reviewer.sha256, review_path, raw_path
            )


if __name__ == "__main__":
    unittest.main()
