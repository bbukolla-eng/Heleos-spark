"""Finite local dispatch, replay, timeout, and checkpoint contract tests."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build.errors import CollisionError, ContractError, TransitionError
from tools.helios_build.ledger import append_event
from tools.helios_build.paths import BuildPaths
from tools.helios_build.profiles import load_worker_profile
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore
from tools.helios_build.types import AttemptOutcome


class BuildDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        prior_umask = os.umask(0o022)
        self.addCleanup(os.umask, prior_umask)
        self.source_root = Path(__file__).resolve().parents[1]
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.repo = self.root / "repo"
        shutil.copytree(
            self.source_root / "build_control" / "schemas",
            self.repo / "build_control" / "schemas",
        )
        shutil.copytree(
            self.source_root / "build_control" / "worker_profiles",
            self.repo / "build_control" / "worker_profiles",
        )
        (self.repo / "build_control" / "tasks").mkdir(parents=True)
        (self.repo / "build_control" / "graph" / "receipts").mkdir(parents=True)
        (self.repo / "build_control" / "graph" / "events.jsonl").write_bytes(b"")
        (self.repo / "build_control" / "graph" / "routing.jsonl").write_bytes(b"")
        for directory in (
            self.repo,
            self.repo / "build_control",
            self.repo / "build_control" / "graph",
            self.repo / "build_control" / "graph" / "receipts",
        ):
            directory.chmod(0o755)
        (self.repo / "owned.txt").write_text("base\n", encoding="utf-8")
        self._git("init")
        self._git("config", "user.email", "fixture@example.invalid")
        self._git("config", "user.name", "Build Fixture")
        self._git("config", "maintenance.auto", "false")
        self._git("config", "gc.autoDetach", "false")
        self._git("add", ".")
        self._git("commit", "-m", "fixture base")
        self.base_sha = self._git("rev-parse", "HEAD").stdout.strip()
        self.paths = BuildPaths(self.repo.resolve(), (self.root / "state").resolve())
        self.counter = self.root / "launch-count"
        self.adapter_path = self.root / "adapters.json"
        self._write_adapter()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_identity_is_canonical_and_order_independent(self) -> None:
        dispatch = self._dispatch_module()
        identity = dispatch.compute_run_identity("a" * 64, "b" * 40, "c" * 64)
        expected = sha256_hex(
            canonical_json_bytes(
                {
                    "task_manifest_sha256": "a" * 64,
                    "base_commit_sha": "b" * 40,
                    "adapter_configuration_sha256": "c" * 64,
                }
            )
        )
        self.assertEqual(identity.sha256, expected)

    def test_dispatch_replay_launches_only_one_process_and_preserves_stdin(self) -> None:
        dispatch = self._dispatch_module()
        task_sha, task = self._commit_task("task-one", "task-node-one")
        with patch.dict(os.environ, {"HELIOS_TEST_COUNTER": str(self.counter)}, clear=False):
            first = dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)
            second = dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)

        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(first.run_identity_sha256, second.run_identity_sha256)
        self.assertEqual(self.counter.read_text(encoding="utf-8"), "1")
        self.assertEqual(first.outcome, AttemptOutcome.SUCCEEDED)
        self.assertEqual(first.state, "DISPATCHED")
        self.assertEqual(first.attempt.task_manifest_sha256, task_sha)
        self.assertEqual(first.attempt.worktree.resolve(), second.attempt.worktree.resolve())
        self.assertEqual(first.attempt.task_bytes, canonical_json_bytes(task))
        self.assertTrue(first.attempt.handoff_path.is_file())

    def test_timeout_is_unknown_and_undeclared_checkpoint_is_rejected(self) -> None:
        dispatch = self._dispatch_module()
        checkpoints = self._checkpoints_module()
        task_sha, task = self._commit_task("timeout-task", "timeout-task-node")
        with patch.dict(os.environ, {"HELIOS_TEST_COUNTER": str(self.counter)}, clear=False):
            result = dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)

        self.assertEqual(result.outcome, AttemptOutcome.OUTCOME_UNKNOWN)
        bad_checkpoint = {
            "protocol": "helios.build.checkpoint/v1",
            "checkpoint_id": "checkpoint-invalid",
            "attempt_manifest_sha256": result.attempt.sha256,
            "boundary_id": "not-declared",
            "base_commit_sha": self.base_sha,
            "commit_sha": None,
            "patch_sha256": None,
            "artifact_sha256s": [],
            "command_receipt_sha256s": [],
            "published_at": "2026-09-01T00:00:00Z",
        }
        result.attempt.checkpoint_dir.joinpath("bad.json").write_bytes(
            canonical_json_bytes(bad_checkpoint)
        )
        result.attempt.checkpoint_dir.joinpath("bad.json").chmod(0o600)
        with self.assertRaisesRegex(ContractError, "undeclared checkpoint boundary"):
            checkpoints.collect_checkpoints(self.paths, result.attempt, task)

    def test_missing_adapter_blocks_without_creating_a_worktree_or_attempt(self) -> None:
        dispatch = self._dispatch_module()
        task_sha, _ = self._commit_task("missing-adapter", "missing-adapter-node")
        payload = json.loads(self.adapter_path.read_text(encoding="utf-8"))
        payload["adapters"] = []
        missing = self.root / "missing-adapter.json"
        missing.write_bytes(canonical_json_bytes(payload))

        result = dispatch.dispatch_task(self.paths, task_sha, missing)

        self.assertEqual(result.state, "BLOCKED")
        self.assertIsNone(result.attempt)
        self.assertFalse((self.paths.state_root / "worktrees").exists())
        self.assertFalse(self.counter.exists())

    def test_partial_staged_replay_is_reconciled_unknown_without_relaunch(self) -> None:
        dispatch = self._dispatch_module()
        task_sha, _ = self._commit_task("partial-task", "partial-node")
        with patch.dict(os.environ, {"HELIOS_TEST_COUNTER": str(self.counter)}, clear=False):
            first = dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)
            terminal = (self.paths.state_root / "dispatch" / "runs"
                        / first.run_identity_sha256 / "03-terminal.json")
            terminal.unlink()
            replay = dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.outcome, AttemptOutcome.OUTCOME_UNKNOWN)
        self.assertEqual(replay.state, "FAILED")
        self.assertEqual(self.counter.read_text(encoding="utf-8"), "1")

    def test_attempt_cas_before_first_stage_recovers_real_inode_without_launch(self) -> None:
        dispatch = self._dispatch_module()
        task_sha, _ = self._commit_task("cas-before-stage", "cas-before-stage-node")
        ready_events = (self.repo / "build_control" / "graph" / "events.jsonl").read_bytes()
        with patch.dict(os.environ, {"HELIOS_TEST_COUNTER": str(self.counter)}, clear=False):
            first = dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)
            stages = self.paths.state_root / "dispatch" / "runs" / first.run_identity_sha256
            for stage in stages.glob("*.json"):
                stage.unlink()
            (self.repo / "build_control" / "graph" / "events.jsonl").write_bytes(ready_events)
            self.counter.write_text("0", encoding="utf-8")
            replay = dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)
        reserved = json.loads(stages.joinpath("01-reserved.json").read_text(encoding="utf-8"))
        self.assertGreater(reserved["worktree_dev"], 0)
        self.assertGreater(reserved["worktree_ino"], 0)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.reason_code, "ATTEMPT_RECOVERY_NEVER_DISPATCHED")
        self.assertEqual(self.counter.read_text(encoding="utf-8"), "0")

    def test_captured_profile_bytes_are_immune_to_working_path_substitution(self) -> None:
        from tools.helios_build.profiles import load_worker_profile_bytes
        task_sha, _ = self._commit_task("profile-task", "profile-node")
        profile_path = self.repo / "build_control" / "worker_profiles" / "claude-builder-v1.json"
        captured = subprocess.run(
            ["git", "-C", str(self.repo), "show", f"HEAD:{profile_path.relative_to(self.repo)}"],
            check=True, stdout=subprocess.PIPE,
        ).stdout
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["profile_id"] = "substituted-builder-v1"
        profile_path.write_bytes(canonical_json_bytes(profile))
        loaded = load_worker_profile_bytes(captured, SchemaRegistry(self.repo))
        self.assertEqual(loaded.profile_id, "claude-builder-v1")
        self.assertNotEqual(loaded.profile_id, profile["profile_id"])

    def test_deleted_or_substituted_policy_cannot_affect_captured_dispatch(self) -> None:
        dispatch = self._dispatch_module()
        for kind in ("task-deleted", "graph-erased"):
            with self.subTest(kind=kind):
                self.tearDown()
                self.setUp()
                task_sha, _ = self._commit_task(kind, f"{kind}-node")
                original_preflight = dispatch.preflight_worker

                def tampering_preflight(*args, **kwargs):
                    if kind == "task-deleted":
                        (self.repo / "build_control" / "tasks" / f"{task_sha}.json").unlink()
                    else:
                        graph_path = self.repo / "build_control" / "graph" / f"{kind}.json"
                        graph_path.write_bytes(canonical_json_bytes({"protocol": "erased"}))
                    return original_preflight(*args, **kwargs)

                with patch.object(dispatch, "preflight_worker", side_effect=tampering_preflight):
                    with self.assertRaisesRegex(ContractError, "captured HEAD"):
                        with patch.dict(
                            os.environ,
                            {"HELIOS_TEST_COUNTER": str(self.counter)},
                            clear=False,
                        ):
                            dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)
                self.assertFalse(self.counter.exists())

    def test_stale_transition_and_invalid_successor_do_not_mutate_predecessor(self) -> None:
        from tools.helios_build.graph import ControllerLock, transition_node_locked

        dispatch = self._dispatch_module()
        predecessor = self._task("predecessor")
        predecessor_sha, _ = self._commit_manifest(predecessor, "predecessor-node")
        self._append_state("predecessor-node", "READY", "FAILED", "DETERMINISTIC_FAILURE")
        successor = self._task("invalid-successor")
        successor["supersedes_task_manifest_sha256"] = predecessor_sha
        successor["correction_round"] = 3
        successor_sha, _ = self._commit_manifest(successor, "invalid-successor-node")

        with self.assertRaisesRegex(ContractError, "maximum correction rounds"):
            with patch.dict(os.environ, {"HELIOS_TEST_COUNTER": str(self.counter)}, clear=False):
                dispatch.dispatch_task(self.paths, successor_sha, self.adapter_path)
        events = self._event_records()
        predecessor_events = [event for event in events if event["node_id"] == "predecessor-node"]
        self.assertEqual(predecessor_events[-1]["new_state"], "FAILED")
        self.assertEqual((self.repo / "build_control" / "graph" / "routing.jsonl").read_bytes(), b"")
        with ControllerLock(self.paths) as lock:
            with self.assertRaisesRegex(TransitionError, "not expected READY"):
                transition_node_locked(
                    lock, "predecessor-node", "READY", "DISPATCHED",
                    "STALE", "codex-control-v1",
                )

    def test_route_is_unique_on_both_sides_and_exact_replay_completes(self) -> None:
        from tools.helios_build.graph import ControllerLock

        dispatch = self._dispatch_module()
        predecessor_sha, _ = self._commit_task("route-predecessor", "route-predecessor-node")
        empty_adapter = json.loads(self.adapter_path.read_text(encoding="utf-8"))
        empty_adapter["adapters"] = []
        empty_path = self.root / "empty-adapter.json"
        empty_path.write_bytes(canonical_json_bytes(empty_adapter))
        dispatch.dispatch_task(self.paths, predecessor_sha, empty_path)

        adapter = json.loads(self.adapter_path.read_text(encoding="utf-8"))
        adapter["adapters"][0]["worker_id"] = "kimi"
        self.adapter_path.write_bytes(canonical_json_bytes(adapter))
        successor_a = self._task("route-successor-a")
        successor_a["roles"]["builder_profile_id"] = "kimi-builder-v1"
        successor_a["roles"]["reviewer_profile_id"] = "codex-reviewer-v1"
        successor_a["supersedes_task_manifest_sha256"] = predecessor_sha
        successor_a_sha, _ = self._commit_manifest(successor_a, "route-successor-a-node")
        schemas = SchemaRegistry(self.repo)
        head = dispatch._head_sha(self.repo)
        policy = dispatch._capture_policy_snapshot(self.paths, head, schemas)
        context = dispatch._task_context(self.paths, successor_a_sha, policy)
        with ControllerLock(self.paths) as lock:
            with patch.object(
                dispatch, "transition_node_locked", side_effect=RuntimeError("crash-after-route")
            ):
                with self.assertRaisesRegex(RuntimeError, "crash-after-route"):
                    dispatch._ensure_successor_routing(
                        lock,
                        self.paths,
                        context,
                        successor_a_sha,
                        successor_a,
                        schemas,
                        policy,
                    )
        successor_b = self._task("route-successor-b")
        successor_b["roles"]["builder_profile_id"] = "kimi-builder-v1"
        successor_b["roles"]["reviewer_profile_id"] = "codex-reviewer-v1"
        successor_b["supersedes_task_manifest_sha256"] = predecessor_sha
        successor_b_sha, _ = self._commit_manifest(successor_b, "route-successor-b-node")
        with patch.dict(os.environ, {"HELIOS_TEST_COUNTER": str(self.counter)}, clear=False):
            with self.assertRaisesRegex(ContractError, "different successor"):
                dispatch.dispatch_task(self.paths, successor_b_sha, self.adapter_path)
            first = dispatch.dispatch_task(self.paths, successor_a_sha, self.adapter_path)
            replay = dispatch.dispatch_task(self.paths, successor_a_sha, self.adapter_path)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.handoff_sha256, first.handoff_sha256)
        self.assertEqual(self.counter.read_text(encoding="utf-8"), "1")

    def test_positive_checkpoint_is_immutable_and_bound_to_attempt_worktree(self) -> None:
        checkpoints = self._checkpoints_module()
        task, result = self._dispatch_checkpoint_task("checkpoint-positive")
        artifact_sha, command_sha = self._checkpoint_evidence(task, result)
        commit_sha = self._commit_attempt_change(result)
        proposal = self._checkpoint_payload(result, commit_sha, artifact_sha, command_sha)
        proposal_path = result.attempt.checkpoint_dir / "checkpoint.json"
        proposal_path.write_bytes(canonical_json_bytes(proposal))
        proposal_path.chmod(0o600)

        records = checkpoints.collect_checkpoints(self.paths, result.attempt, task)

        self.assertEqual(len(records), 1)
        self.assertNotEqual(records[0].path, proposal_path)
        immutable = records[0].path.read_bytes()
        proposal_path.write_text("{}", encoding="utf-8")
        self.assertEqual(records[0].path.read_bytes(), immutable)

    def test_resume_uses_canonical_artifact_and_rejects_deep_tampering(self) -> None:
        checkpoints = self._checkpoints_module()
        task, result = self._dispatch_checkpoint_task("checkpoint-resume-durable")
        artifact_sha, command_sha = self._checkpoint_evidence(task, result)
        commit_sha = self._commit_attempt_change(result)
        proposal = self._checkpoint_payload(result, commit_sha, artifact_sha, command_sha)
        proposal_path = result.attempt.checkpoint_dir / "resume.json"
        proposal_path.write_bytes(canonical_json_bytes(proposal)); proposal_path.chmod(0o600)
        record = checkpoints.collect_checkpoints(self.paths, result.attempt, task)[0]

        staging = self.paths.state_root / "artifacts" / "objects" / "evidence.bin"
        staging.write_bytes(b"mutated after collection")
        checkpoints._validate_durable_checkpoint_for_resume(
            self.paths, record.sha256, result.attempt.task_manifest_sha256, task
        )

        store = ContentAddressedStore(self.repo / "build_control" / "graph")
        canonical_artifact = store.get_json(record.payload["artifact_sha256s"][0])
        canonical_path = self.paths.state_root / "private-cas" / canonical_artifact["store_key"]
        canonical_path.chmod(0o644)
        with self.assertRaisesRegex(ContractError, "mode|custody"):
            checkpoints._validate_durable_checkpoint_for_resume(
                self.paths, record.sha256, result.attempt.task_manifest_sha256, task
            )
        canonical_path.chmod(0o600)

        command_path = (
            self.repo / "build_control" / "graph" / "receipts" / "sha256"
            / command_sha[:2] / f"{command_sha}.json"
        )
        command_path.chmod(0o600)
        with self.assertRaisesRegex(ContractError, "mode"):
            checkpoints._validate_durable_checkpoint_for_resume(
                self.paths, record.sha256, result.attempt.task_manifest_sha256, task
            )
        command_path.chmod(0o644)

        bad = dict(record.payload)
        bad["commit_sha"] = self._git("rev-parse", "HEAD").stdout.strip()
        bad_sha = store.put_json("checkpoints", bad).sha256
        with self.assertRaisesRegex(CollisionError, "unowned|forbidden"):
            checkpoints._validate_durable_checkpoint_for_resume(
                self.paths, bad_sha, result.attempt.task_manifest_sha256, task
            )

    def test_checkpoint_rejects_arbitrary_commit_artifact_and_command_evidence(self) -> None:
        checkpoints = self._checkpoints_module()
        cases = ("commit", "artifact", "command")
        for case in cases:
            with self.subTest(case=case):
                self.tearDown()
                self.setUp()
                task, result = self._dispatch_checkpoint_task(f"checkpoint-{case}")
                artifact_sha, command_sha = self._checkpoint_evidence(task, result)
                commit_sha = self._commit_attempt_change(result)
                if case == "commit":
                    commit_sha = self._git("rev-parse", "HEAD").stdout.strip()
                elif case == "artifact":
                    artifact_path = self.paths.state_root / "artifacts" / "objects" / "evidence.bin"
                    artifact_path.write_bytes(b"tampered")
                else:
                    command_sha = self._command_receipt(task, exit_code=7)
                proposal = self._checkpoint_payload(result, commit_sha, artifact_sha, command_sha)
                result.attempt.checkpoint_dir.joinpath("bad.json").write_bytes(canonical_json_bytes(proposal))
                result.attempt.checkpoint_dir.joinpath("bad.json").chmod(0o600)
                with self.assertRaisesRegex(ContractError, "commit|artifact|command"):
                    checkpoints.collect_checkpoints(self.paths, result.attempt, task)

    def test_checkpoint_directory_symlink_is_rejected(self) -> None:
        checkpoints = self._checkpoints_module()
        task, result = self._dispatch_checkpoint_task("checkpoint-symlink")
        result.attempt.checkpoint_dir.rmdir()
        target = self.root / "attacker-checkpoints"
        target.mkdir()
        result.attempt.checkpoint_dir.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ContractError, "safely enumerate|external"):
            checkpoints.collect_checkpoints(self.paths, result.attempt, task)

    def test_unsafe_existing_state_root_is_rejected_without_chmod(self) -> None:
        dispatch = self._dispatch_module()
        task_sha, _ = self._commit_task("unsafe-root", "unsafe-root-node")
        self.paths.state_root.mkdir(mode=0o755, exist_ok=True)
        self.paths.state_root.chmod(0o755)
        with self.assertRaisesRegex(TransitionError, "mode 0700"):
            dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)
        self.assertEqual(self.paths.state_root.stat().st_mode & 0o777, 0o755)

    def test_exact_orphan_worktree_is_recovered_without_duplicate_creation(self) -> None:
        from tools.helios_build.worktrees import recover_or_create_detached_worktree
        self.paths.state_root.mkdir(mode=0o700, exist_ok=True); self.paths.state_root.chmod(0o700)
        first, inode, created = recover_or_create_detached_worktree(
            self.paths, "a" * 64, self.base_sha)
        first.chmod(0o755)
        second, recovered_inode, recreated = recover_or_create_detached_worktree(
            self.paths, "a" * 64, self.base_sha)
        self.assertTrue(created); self.assertFalse(recreated)
        self.assertEqual((first, inode), (second, recovered_inode))
        self.assertEqual(second.stat().st_mode & 0o777, 0o700)

        wrong, _, _ = recover_or_create_detached_worktree(
            self.paths, "b" * 64, self.base_sha)
        wrong.joinpath("unproved.txt").write_text("dirty\n", encoding="utf-8")
        wrong.chmod(0o755)
        with self.assertRaisesRegex(ContractError, "clean"):
            recover_or_create_detached_worktree(self.paths, "b" * 64, self.base_sha)
        self.assertEqual(wrong.stat().st_mode & 0o777, 0o755)

    def test_terminal_success_replay_does_not_require_worktree_path(self) -> None:
        dispatch = self._dispatch_module()
        task_sha, _ = self._commit_task("terminal-replay", "terminal-replay-node")
        with patch.dict(os.environ, {"HELIOS_TEST_COUNTER": str(self.counter)}, clear=False):
            first = dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)
            shutil.rmtree(first.attempt.worktree)
            replay = dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.handoff_sha256, first.handoff_sha256)
        manifest = ContentAddressedStore(
            self.repo / "build_control" / "graph"
        ).get_json(first.handoff_sha256)
        self.assertEqual(manifest["kind"], "LOCAL_WORKER_HANDOFF_RAW")
        self.assertNotIn(b"PRIVATE-HANDOFF-SENTINEL", canonical_json_bytes(manifest))
        raw_path = self.paths.state_root / "private-cas" / manifest["store_key"]
        self.assertIn(b"PRIVATE-HANDOFF-SENTINEL", raw_path.read_bytes())

    def test_invalid_or_oversized_handoff_fails_without_raw_git_object(self) -> None:
        dispatch = self._dispatch_module()
        for case in ("malformed", "wrong-lineage", "oversized"):
            with self.subTest(case=case):
                self.tearDown()
                self.setUp()
                adapter = json.loads(self.adapter_path.read_text(encoding="utf-8"))
                script = adapter["adapters"][0]["dispatch_argv"][1]
                if case == "malformed":
                    script = script.rsplit("pathlib.Path", 1)[0] + "pathlib.Path(sys.argv[1]).write_text('not-json')"
                elif case == "wrong-lineage":
                    script = script.replace(
                        "task_manifest_sha256=sys.argv[5]",
                        "task_manifest_sha256='0'*64",
                    )
                else:
                    script = script.replace(
                        "'PRIVATE-HANDOFF-SENTINEL'", "'X'*5000"
                    )
                adapter["adapters"][0]["dispatch_argv"][1] = script
                self.adapter_path.write_bytes(canonical_json_bytes(adapter))
                task_sha, _ = self._commit_task(f"handoff-{case}", f"handoff-{case}-node")
                with patch.dict(os.environ, {"HELIOS_TEST_COUNTER": str(self.counter)}, clear=False):
                    result = dispatch.dispatch_task(self.paths, task_sha, self.adapter_path)
                self.assertEqual(result.outcome, AttemptOutcome.FAILED)
                self.assertIn(result.reason_code, {"HANDOFF_INVALID", "HANDOFF_OVERSIZED"})
                artifacts = self.repo / "build_control" / "graph" / "artifacts"
                if artifacts.exists():
                    self.assertFalse(any(
                        b"LOCAL_WORKER_HANDOFF_RAW" in path.read_bytes()
                        for path in artifacts.rglob("*.json")
                    ))

    def test_checkpoint_rejects_empty_evidence_and_unchanged_commit(self) -> None:
        checkpoints = self._checkpoints_module()
        task, result = self._dispatch_checkpoint_task("checkpoint-empty")
        proposal = self._checkpoint_payload(result, self.base_sha, "0" * 64, "1" * 64)
        proposal["artifact_sha256s"] = []
        proposal["command_receipt_sha256s"] = []
        path = result.attempt.checkpoint_dir / "empty.json"
        path.write_bytes(canonical_json_bytes(proposal)); path.chmod(0o600)
        with self.assertRaisesRegex(ContractError, "evidence cannot be empty"):
            checkpoints.collect_checkpoints(self.paths, result.attempt, task)

    def _dispatch_module(self):
        try:
            return importlib.import_module("tools.helios_build.dispatch")
        except ModuleNotFoundError:
            self.fail("Task 5 dispatch module is not implemented")

    def _checkpoints_module(self):
        try:
            return importlib.import_module("tools.helios_build.checkpoints")
        except ModuleNotFoundError:
            self.fail("Task 5 checkpoints module is not implemented")

    def _commit_task(self, task_id: str, node_id: str) -> tuple[str, dict[str, object]]:
        return self._commit_manifest(self._task(task_id), node_id)

    def _commit_manifest(self, task: dict[str, object], node_id: str) -> tuple[str, dict[str, object]]:
        task_id = str(task["task_id"])
        task_sha = sha256_hex(canonical_json_bytes(task))
        task_path = self.repo / "build_control" / "tasks" / f"{task_sha}.json"
        task_path.write_bytes(canonical_json_bytes(task))
        graph = {
            "protocol": "helios.build.graph-manifest/v1",
            "graph_id": f"graph-{task_id}",
            "nodes": [
                {
                    "node_id": node_id,
                    "node_type": "BuildTask",
                    "manifest_sha256": task_sha,
                }
            ],
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
        return task_sha, task

    def _append_state(self, node_id: str, prior: str, target: str, reason: str) -> None:
        append_event(
            self.repo / "build_control" / "graph" / "events.jsonl",
            {
                "protocol": "helios.build.lifecycle-event/v1",
                "node_id": node_id,
                "prior_state": prior,
                "new_state": target,
                "reason_code": reason,
                "actor_profile_id": "codex-control-v1",
                "attempt_manifest_sha256": None,
                "external_session_assignment_sha256": None,
                "routing_event_sha256": None,
                "recorded_at": "2026-09-01T00:00:01Z",
            },
            self.paths.state_root,
        )

    def _event_records(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in (self.repo / "build_control" / "graph" / "events.jsonl").read_text().splitlines()]

    def _task(self, task_id: str) -> dict[str, object]:
        return {
            "protocol": "helios.build.task-manifest/v1",
            "task_id": task_id,
            "capability_id": "dispatch-fixture",
            "capability": "Exercise one finite local adapter attempt.",
            "node_type": "BuildTask",
            "base_commit_sha": self.base_sha,
            "dependency_node_ids": [],
            "roles": {
                "builder_profile_id": "claude-builder-v1",
                "reviewer_profile_id": "kimi-builder-v1",
                "integrator_profile_id": "codex-control-v1",
            },
            "ownership": {
                "files": ["owned.txt"],
                "path_globs": [],
                "modules": [],
                "migrations": [],
                "schemas": [],
                "public_interfaces": [],
                "forbidden_paths": ["build_control/**"],
            },
            "frozen_interfaces": [],
            "schema_impact": [],
            "required_source_record_ids": [],
            "required_source_packet_ids": [],
            "evidence_threshold": 0,
            "deliverables": ["owned.txt"],
            "acceptance": {"behaviors": ["finite"], "commands": []},
            "budgets": {
                "implementation_seconds": 5,
                "cost_microusd": 0,
                "focused_test_seconds": 5,
                "affected_integration_seconds": 5,
                "milestone_seconds": 5,
                "maximum_correction_rounds": 2,
            },
            "checkpoint_boundaries": ["tests-green"],
            "conditions": {"stop": [], "block": [], "reroute": [], "escalation": []},
            "correction_round": 0,
            "supersedes_task_manifest_sha256": None,
            "resume_from_checkpoint_sha256": None,
            "created_at": "2026-09-01T00:00:00Z",
            "created_by_profile_id": "codex-control-v1",
        }

    def _write_adapter(self) -> None:
        executable = Path(sys.executable).resolve()
        script = (
            "import json,os,pathlib,sys,time;"
            "task=json.load(sys.stdin);"
            "counter=pathlib.Path(os.environ['HELIOS_TEST_COUNTER']);"
            "counter.write_text(str((int(counter.read_text()) if counter.exists() else 0)+1));"
            "time.sleep(3) if task['task_id']=='timeout-task' else None;"
            "handoff=dict(protocol='helios.build.worker-handoff/v1',transport='LOCAL_ADAPTER',"
            "attempt_manifest_sha256=sys.argv[4],external_session_assignment_sha256=None,"
            "task_manifest_sha256=sys.argv[5],output_kind='COMMIT',patch_sha256=None,"
            "commit_sha=task['base_commit_sha'],files_changed=list(),commands_executed=list(),"
            "focused_test_results=list(),assumptions=list(('PRIVATE-HANDOFF-SENTINEL',)),"
            "unresolved_issues=list(),dependency_effects=list(),security_effects=list(),"
            "source_record_ids=task['required_source_record_ids'],"
            "source_packet_ids=task['required_source_packet_ids'],raw_evidence_sha256=None,"
            "duration_ms=1,cost_microusd=0);"
            "pathlib.Path(sys.argv[1]).write_text(json.dumps(handoff,sort_keys=True,separators=(',',':')))"
        )
        payload = {
            "protocol": "helios.build.adapter-config/v1",
            "host_id": "dispatch-test-host",
            "adapters": [
                {
                    "worker_id": "claude",
                    "adapter_name": "dispatch-fixture",
                    "adapter_revision": "v1",
                    "executable": str(executable),
                    "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                    "preflight_argv": ["-c", "raise SystemExit(0)"],
                    "dispatch_argv": [
                        "-c", script, "{handoff_path}", "{checkpoint_dir}", "{worktree}",
                        "{attempt_manifest_sha256}", "{task_manifest_sha256}",
                    ],
                    "environment_variable_names": ["HELIOS_TEST_COUNTER"],
                    "preflight_timeout_seconds": 2,
                    "attempt_timeout_seconds": 1,
                    "max_capture_bytes": 4096,
                    "result_protocol": "helios.build.worker-handoff/v1",
                }
            ],
        }
        self.adapter_path.write_bytes(canonical_json_bytes(payload))

    def _dispatch_checkpoint_task(self, task_id: str):
        task = self._task(task_id)
        task["acceptance"] = {
            "behaviors": ["finite"],
            "commands": [{
                "command_id": "focused-check",
                "gate": "FOCUSED",
                "argv": ["python3", "-m", "unittest"],
                "cwd": "src",
                "expected_exit_code": 0,
                "timeout_seconds": 2,
            }],
        }
        task_sha, task = self._commit_manifest(task, f"{task_id}-node")
        with patch.dict(os.environ, {"HELIOS_TEST_COUNTER": str(self.counter)}, clear=False):
            result = self._dispatch_module().dispatch_task(self.paths, task_sha, self.adapter_path)
        self.assertIsNotNone(result.attempt)
        return task, result

    def _checkpoint_evidence(self, task, result) -> tuple[str, str]:
        artifact_path = self.paths.state_root / "artifacts" / "objects" / "evidence.bin"
        (self.paths.state_root / "artifacts").mkdir(mode=0o700, exist_ok=True)
        artifact_path.parent.mkdir(mode=0o700, exist_ok=True)
        artifact_path.write_bytes(b"verified evidence")
        artifact_path.chmod(0o600)
        artifact = {
            "protocol": "helios.build.artifact-manifest/v1",
            "artifact_id": "checkpoint-evidence",
            "attempt_manifest_sha256": result.attempt.sha256,
            "kind": "EVIDENCE",
            "sha256": sha256_hex(b"verified evidence"),
            "byte_size": len(b"verified evidence"),
            "media_type": "application/octet-stream",
            "store_key": "objects/evidence.bin",
            "created_at": "2026-09-01T00:00:02Z",
        }
        store = ContentAddressedStore(self.repo / "build_control" / "graph")
        artifact_sha = store.put_json("artifacts", artifact).sha256
        return artifact_sha, self._command_receipt(task)

    def _command_receipt(self, task, *, exit_code: int = 0) -> str:
        task_sha = sha256_hex(canonical_json_bytes(task))
        command = task["acceptance"]["commands"][0]
        receipt = {
            "protocol": "helios.build.command-receipt/v1",
            "task_manifest_sha256": task_sha,
            "gate": command["gate"],
            "command_id": command["command_id"],
            "correction_round": task["correction_round"],
            "argv_sha256": sha256_hex(canonical_json_bytes(command["argv"])),
            "cwd_key": sha256_hex(canonical_json_bytes({"cwd": command["cwd"]})),
            "started_at": "2026-09-01T00:00:03Z",
            "duration_ms": 1,
            "exit_code": exit_code,
            "outcome": "PASS",
            "stdout_sha256": sha256_hex(b""),
            "stderr_sha256": sha256_hex(b""),
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
        from tools.helios_build.doctor import _persist_raw_stream
        _persist_raw_stream(self.paths.state_root, "stdout", b"")
        _persist_raw_stream(self.paths.state_root, "stderr", b"")
        return ContentAddressedStore(self.repo / "build_control" / "graph").put_json("receipts", receipt).sha256

    def _commit_attempt_change(self, result) -> str:
        owned = result.attempt.worktree / "owned.txt"
        owned.write_text("checkpoint\n", encoding="utf-8")
        self._git_at(result.attempt.worktree, "add", "owned.txt")
        self._git_at(result.attempt.worktree, "commit", "-m", "checkpoint")
        return self._git_at(result.attempt.worktree, "rev-parse", "HEAD").stdout.strip()

    def _checkpoint_payload(self, result, commit_sha: str, artifact_sha: str, command_sha: str):
        return {
            "protocol": "helios.build.checkpoint/v1",
            "checkpoint_id": "tests-green-checkpoint",
            "attempt_manifest_sha256": result.attempt.sha256,
            "boundary_id": "tests-green",
            "base_commit_sha": self.base_sha,
            "commit_sha": commit_sha,
            "patch_sha256": None,
            "artifact_sha256s": [artifact_sha],
            "command_receipt_sha256s": [command_sha],
            "published_at": "2026-09-01T00:00:04Z",
        }

    def _git_at(self, path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(path), *argv], check=True, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _git(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repo), *argv],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


if __name__ == "__main__":
    unittest.main()
