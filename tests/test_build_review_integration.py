"""Git-derived collection, bounded gates, and independent review contracts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_build_external_sessions import BuildFabricRepo
from tools.helios_build._external_cas import put_private_bytes
from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build.collect import collect_task
from tools.helios_build.errors import CollisionError, ContractError, TransitionError
from tools.helios_build.external_sessions import begin_external_session
from tools.helios_build.graph import ControllerLock, transition_node_locked
from tools.helios_build.integrate import record_integration
from tools.helios_build.ledger import replay_events
from tools.helios_build.profiles import load_worker_profile, worker_profile_sha256
from tools.helios_build.review import (
    create_correction_successor,
    import_external_review,
    review_task,
)
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore
from tools.helios_build.status import (
    _is_build_fabric_core_task,
    _substantive_paths,
    build_report,
    graph_status,
)
from tools.helios_build.verification import run_verification_gate
from tools.helios_build.worktrees import create_detached_worktree


class ReviewIntegrationTests(BuildFabricRepo, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._local_ordinal = 0

    def _add_source_fixture(self, relative: str) -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("base\n", encoding="utf-8")
        self._git("add", relative)
        self._git("commit", "-m", f"add source fixture {relative}")
        self.base_sha = self._git("rev-parse", "HEAD").stdout.strip()

    def _retag_task_capability(
        self,
        task_sha: str,
        task: dict[str, object],
        capability_id: str,
    ) -> tuple[str, dict[str, object]]:
        updated = dict(task)
        updated["capability_id"] = capability_id
        updated_sha = sha256_hex(canonical_json_bytes(updated))
        old_path = self.repo / "build_control" / "tasks" / f"{task_sha}.json"
        old_path.unlink()
        new_path = self.repo / "build_control" / "tasks" / f"{updated_sha}.json"
        new_path.write_bytes(canonical_json_bytes(updated))
        graph_path = (
            self.repo / "build_control" / "graph" / f"{task['task_id']}.json"
        )
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        graph["nodes"][0]["manifest_sha256"] = updated_sha
        graph_path.write_bytes(canonical_json_bytes(graph))
        self._git(
            "add",
            "--all",
            "--",
            "build_control/tasks",
            str(graph_path.relative_to(self.repo)),
        )
        self._git("commit", "-m", f"set capability {capability_id}")
        return updated_sha, updated

    def _integrate_external_task(
        self,
        task_id: str,
        owned_file: str,
        *,
        capability_id: str | None = None,
    ) -> tuple[str, dict[str, object], object, object, object]:
        task_sha, task, _ = self.add_task(task_id, owned_file=owned_file)
        if capability_id is not None:
            task_sha, task = self._retag_task_capability(
                task_sha, task, capability_id
            )
        _, handoff, patch_bytes, _ = self.import_builder(
            task_sha, task, owned_file=owned_file
        )
        reviewer = begin_external_session(
            self.paths,
            task_sha,
            "codex-reviewer-v1",
            "CODEX",
            f"{task_id}-reviewer",
            "REVIEWER",
            "EXTERNAL_SESSION_SELECTED",
        )
        review_path, raw_path = self.write_review(
            reviewer, task, handoff.sha256
        )
        review = import_external_review(
            self.paths, task_sha, reviewer.sha256, review_path, raw_path
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "apply", "--index", "--binary", "-"],
            input=patch_bytes,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._git("commit", "-m", f"integrate {task_id}")
        integration = record_integration(
            self.paths,
            task_sha,
            self._git("rev-parse", "HEAD").stdout.strip(),
        )
        return task_sha, task, handoff, review, integration

    def test_integration_requires_an_accepted_independent_review(self) -> None:
        task_sha, task, node_id = self.add_task(
            "integration-before-review", owned_file="owned-11.txt"
        )
        self.import_builder(task_sha, task, owned_file="owned-11.txt")

        with self.assertRaisesRegex(ContractError, "accepted independent review"):
            record_integration(self.paths, task_sha)

        self.assertEqual(self.event_state(node_id), "RETURNED")

    def test_integration_verifies_the_canonical_commit_and_reports_truthfully(self) -> None:
        task_sha, task, node_id = self.add_task(
            "integration-success", owned_file="owned-11.txt"
        )
        _, handoff, patch_bytes, _ = self.import_builder(
            task_sha, task, owned_file="owned-11.txt"
        )
        reviewer = begin_external_session(
            self.paths,
            task_sha,
            "codex-reviewer-v1",
            "CODEX",
            "integration-reviewer",
            "REVIEWER",
            "EXTERNAL_SESSION_SELECTED",
        )
        review_path, raw_path = self.write_review(
            reviewer, task, handoff.sha256
        )
        review = import_external_review(
            self.paths, task_sha, reviewer.sha256, review_path, raw_path
        )
        self.assertEqual(self.event_state(node_id), "ACCEPTED")

        canonical_parent = self._git("rev-parse", "HEAD").stdout.strip()
        subprocess.run(
            ["git", "-C", str(self.repo), "apply", "--index", "--binary", "-"],
            input=patch_bytes,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._git("commit", "-m", "integrate accepted task")
        integrated_commit = self._git("rev-parse", "HEAD").stdout.strip()

        receipt = record_integration(self.paths, task_sha, integrated_commit)

        self.assertEqual(receipt.integrator_profile_id, "codex-control-v1")
        self.assertEqual(receipt.canonical_parent_sha, canonical_parent)
        self.assertEqual(receipt.integrated_commit_sha, integrated_commit)
        self.assertEqual(receipt.changed_paths, ("owned-11.txt",))
        self.assertEqual(receipt.handoff_sha256, handoff.sha256)
        self.assertEqual(receipt.review_receipt_sha256, review.sha256)
        self.assertEqual(self.event_state(node_id), "INTEGRATED")
        stored_handoff = ContentAddressedStore(
            self.repo / "build_control" / "graph"
        ).get_json(handoff.sha256)
        self.assertEqual(stored_handoff["transport"], "EXTERNAL_SESSION")

        status = graph_status(self.paths)
        self.assertEqual(status["node_counts"]["INTEGRATED"], 1)
        self.assertEqual(status["ready_nodes"], [])
        self.assertEqual(status["lane_count"], 0)
        self.assertEqual(status["integrity_errors"], [])
        report = build_report(self.paths)
        self.assertFalse(report["BUILD_FABRIC_CORE_CODE_COMPLETE"])
        self.assertFalse(report["TARGET_HOST_ACCEPTED"])
        self.assertEqual(
            report["PRODUCTION_SELF_USE"],
            {
                "DELIVERABLE_B_DIV23_V2": False,
                "DELIVERABLE_C_MODEL_REGISTRY": False,
                "DELIVERABLE_D_DATASET_INTAKE": False,
                "DELIVERABLE_E_LOCAL_SERVICE_SDK": False,
                "DELIVERABLE_F_CLI_ADAPTER": False,
            },
        )
        self.assertFalse(report["CORE_CODE_COMPLETE"])
        self.assertFalse(report["RESEARCH_ROUNDTRIP_ACCEPTED"])
        self.assertFalse(report["CYCLE_COMPLETE"])

    def test_core_completion_requires_exact_substantive_capability(self) -> None:
        source = "src/helios_takeoff_core/build_fabric/core_feature.py"
        self._add_source_fixture(source)
        _, _, _, _, integration = self._integrate_external_task(
            "core-status",
            source,
            capability_id="BUILD_FABRIC_CORE_CODE",
        )

        report = build_report(self.paths)

        self.assertTrue(report["BUILD_FABRIC_CORE_CODE_COMPLETE"])
        self.assertEqual(
            report["SUPPORTING_RECEIPT_SHA256S"][
                "BUILD_FABRIC_CORE_CODE_COMPLETE"
            ],
            [integration.sha256],
        )

    def test_documentation_only_core_task_does_not_count_as_substantive(self) -> None:
        documentation = "src/helios_takeoff_core/documentation/README.md"
        self._add_source_fixture(documentation)
        self._integrate_external_task(
            "documentation-only-core",
            documentation,
            capability_id="BUILD_FABRIC_CORE_CODE",
        )

        self.assertFalse(
            build_report(self.paths)["BUILD_FABRIC_CORE_CODE_COMPLETE"]
        )

    def test_substantive_paths_separate_build_fabric_from_production_code(self) -> None:
        self.assertFalse(_substantive_paths(["tools/helios_build/status.py"]))
        self.assertFalse(
            _substantive_paths(["build_control/schemas/task-manifest-v1.schema.json"])
        )
        self.assertTrue(
            _substantive_paths(
                ["tools/helios_build/status.py"], allow_build_fabric=True
            )
        )
        self.assertTrue(
            _substantive_paths(["src/helios_takeoff_core/engine/service.py"])
        )
        for documentation in (
            "src/helios_takeoff_core/CHANGELOG",
            "src/helios_takeoff_core/manual.adoc",
            "src/helios_takeoff_core/notes.md",
        ):
            with self.subTest(documentation=documentation):
                self.assertFalse(_substantive_paths([documentation]))

        task = {
            "task_id": "build-fabric-cli",
            "capability_id": "BUILD_FABRIC_CORE_CODE",
            "capability": "Build Fabric operator CLI",
            "ownership": {
                "files": ["tools/helios_build/cli.py"],
                "path_globs": [],
            },
        }
        self.assertTrue(_is_build_fabric_core_task(task))

    def test_external_evidence_validators_are_explicit_and_integrity_gated(self) -> None:
        research_sha = "1" * 64
        target_sha = "2" * 64

        def research_validator(_: object) -> dict[str, object]:
            return {
                "accepted": True,
                "supporting_receipt_sha256s": [research_sha],
            }

        def target_validator(_: object) -> dict[str, object]:
            return {
                "accepted": True,
                "supporting_receipt_sha256s": [target_sha],
            }

        default = build_report(self.paths)
        self.assertFalse(default["RESEARCH_ROUNDTRIP_ACCEPTED"])
        self.assertFalse(default["TARGET_HOST_ACCEPTED"])
        accepted = build_report(
            self.paths,
            research_evidence_validator=research_validator,
            target_host_evidence_validator=target_validator,
        )
        self.assertTrue(accepted["RESEARCH_ROUNDTRIP_ACCEPTED"])
        self.assertTrue(accepted["TARGET_HOST_ACCEPTED"])
        self.assertEqual(
            accepted["SUPPORTING_RECEIPT_SHA256S"][
                "RESEARCH_ROUNDTRIP_ACCEPTED"
            ],
            [research_sha],
        )
        self.assertEqual(
            accepted["SUPPORTING_RECEIPT_SHA256S"]["TARGET_HOST_ACCEPTED"],
            [target_sha],
        )

        malformed = [
            {"accepted": True, "supporting_receipt_sha256s": []},
            {
                "accepted": True,
                "supporting_receipt_sha256s": [research_sha, research_sha],
            },
            {"accepted": True, "supporting_receipt_sha256s": ["not-a-digest"]},
            {"accepted": "yes", "supporting_receipt_sha256s": [research_sha]},
        ]
        for evidence in malformed:
            with self.subTest(evidence=evidence):
                with self.assertRaises(ContractError):
                    build_report(
                        self.paths,
                        research_evidence_validator=lambda _paths, value=evidence: value,
                    )

        (self.repo / "build_control" / "graph" / "routing.jsonl").write_bytes(
            b"not-json\n"
        )
        suppressed = build_report(
            self.paths,
            research_evidence_validator=research_validator,
            target_host_evidence_validator=target_validator,
        )
        self.assertFalse(suppressed["RESEARCH_ROUNDTRIP_ACCEPTED"])
        self.assertFalse(suppressed["TARGET_HOST_ACCEPTED"])
        self.assertEqual(
            suppressed["SUPPORTING_RECEIPT_SHA256S"][
                "RESEARCH_ROUNDTRIP_ACCEPTED"
            ],
            [],
        )
        self.assertTrue(suppressed["INTEGRITY_ERRORS"])

    def test_status_revalidates_every_affected_review_command_receipt(self) -> None:
        source = "src/helios_takeoff_core/build_fabric/review_gate.py"
        self._add_source_fixture(source)
        task_sha, _, handoff, review, integration = self._integrate_external_task(
            "review-gate-status",
            source,
            capability_id="BUILD_FABRIC_CORE_CODE",
        )
        self.assertTrue(
            build_report(self.paths)["BUILD_FABRIC_CORE_CODE_COMPLETE"]
        )
        store = ContentAddressedStore(
            self.repo / "build_control" / "graph"
        )
        review_payload = store.get_json(review.sha256)
        integration_payload = store.get_json(integration.sha256)
        command_sha = review_payload["command_receipt_sha256s"][0]
        command = store.get_json(command_sha)
        clone = dict(command)
        clone["started_at"] = "2026-09-01T00:00:05Z"
        duplicate_sha = store.put_json("receipts", clone).sha256
        wrong_patch = dict(command)
        wrong_patch["patch_sha256"] = "3" * 64
        wrong_patch_sha = store.put_json("receipts", wrong_patch).sha256
        forged = dict(command)
        forged["argv_sha256"] = "4" * 64
        forged_sha = store.put_json("receipts", forged).sha256
        variants = {
            "missing": [],
            "duplicate": [command_sha, duplicate_sha],
            "wrong-patch": [wrong_patch_sha],
            "forged-argv": [forged_sha],
        }
        integration.path.unlink()
        for name, references in variants.items():
            with self.subTest(case=name):
                changed_review = dict(review_payload)
                changed_review["command_receipt_sha256s"] = references
                changed_review_object = store.put_json("receipts", changed_review)
                changed_integration = dict(integration_payload)
                changed_integration["review_receipt_sha256"] = (
                    changed_review_object.sha256
                )
                changed_integration["command_receipt_sha256s"] = references
                changed_integration_object = store.put_json(
                    "receipts", changed_integration
                )
                try:
                    report = build_report(self.paths)
                    self.assertFalse(report["BUILD_FABRIC_CORE_CODE_COMPLETE"])
                finally:
                    changed_integration_object.path.unlink()
        store.put_json("receipts", integration_payload)
        self.assertEqual(handoff.task_manifest_sha256, task_sha)

    def test_collection_rejects_reported_scope_source_schema_and_interface_mismatches(self) -> None:
        cases = [
            {
                "name": "underreported",
                "owned": "owned-0.txt",
                "changes": {"owned-0.txt": "changed\n"},
                "reported": [],
                "error": "files_changed",
            },
            {
                "name": "forbidden",
                "owned": "forbidden.py",
                "changes": {"forbidden.py": "FORBIDDEN = True\n"},
                "reported": ["forbidden.py"],
                "ownership": {
                    "files": ["forbidden.py"],
                    "forbidden_paths": ["forbidden.py", "build_control/**"],
                },
                "error": "forbidden.py",
                "exception": CollisionError,
            },
            {
                "name": "source",
                "owned": "owned-1.txt",
                "changes": {"owned-1.txt": "changed\n"},
                "reported": ["owned-1.txt"],
                "records": ["required-record"],
                "reported_records": [],
                "error": "source_record_ids",
            },
            {
                "name": "schema",
                "owned": "owned-2.txt",
                "changes": {"schema.json": '{"changed":true}\n'},
                "reported": ["schema.json"],
                "ownership": {"files": [], "schemas": ["schema.json"]},
                "error": "schema impact",
            },
            {
                "name": "interface",
                "owned": "owned-3.txt",
                "changes": {"owned-3.txt": "changed\n"},
                "reported": ["owned-3.txt"],
                "ownership": {"public_interfaces": ["api-v1"]},
                "frozen": ["api-v1"],
                "effects": ["PUBLIC_INTERFACE:api-v1"],
                "error": "frozen interface",
            },
        ]
        for case in cases:
            with self.subTest(case=case["name"]):
                task_sha, task, node_id = self.add_task(
                    f"collect-{case['name']}",
                    owned_file=str(case["owned"]),
                    builder="claude-builder-v1",
                    reviewer="kimi-builder-v1",
                    required_records=case.get("records"),
                    ownership_updates=case.get("ownership"),
                    frozen_interfaces=case.get("frozen"),
                )
                self._seed_local_handoff(
                    task_sha,
                    task,
                    node_id,
                    changes=case["changes"],
                    reported_files=case["reported"],
                    source_record_ids=case.get("reported_records"),
                    dependency_effects=case.get("effects"),
                )
                expected = case.get("exception", ContractError)
                with self.assertRaisesRegex(expected, str(case["error"])):
                    collect_task(self.paths, task_sha)
                self.assertEqual(self.event_state(node_id), "DISPATCHED")
                self._terminalize_local_fixture(node_id)

    def test_collection_derives_exact_commit_patch_and_rejects_unrelated_commit(self) -> None:
        task_sha, task, node_id = self.add_task(
            "commit-success",
            owned_file="owned-4.txt",
            builder="claude-builder-v1",
            reviewer="kimi-builder-v1",
        )
        _, commit_sha, canonical_patch = self._seed_local_handoff(
            task_sha,
            task,
            node_id,
            changes={"owned-4.txt": "committed result\n"},
            reported_files=["owned-4.txt"],
            output_kind="COMMIT",
        )
        receipt = collect_task(self.paths, task_sha)
        self.assertEqual(receipt.output_kind, "COMMIT")
        self.assertEqual(receipt.commit_sha, commit_sha)
        self.assertEqual(receipt.files_changed, ("owned-4.txt",))
        self.assertEqual(receipt.canonical_patch_sha256, sha256_hex(canonical_patch))
        stored_patch = next(
            (self.repo / "build_control" / "graph" / "patches").rglob(
                f"{receipt.canonical_patch_sha256}.patch"
            )
        )
        self.assertEqual(stored_patch.read_bytes(), canonical_patch)
        self.assertEqual(self.event_state(node_id), "RETURNED")

        bad_sha, bad_task, bad_node = self.add_task(
            "commit-unrelated",
            owned_file="owned-5.txt",
            builder="claude-builder-v1",
            reviewer="kimi-builder-v1",
        )
        unrelated = self._git(
            "commit-tree", self._git("rev-parse", f"{self.base_sha}^{{tree}}").stdout.strip(),
            "-m", "unrelated root",
        ).stdout.strip()
        self._seed_local_handoff(
            bad_sha,
            bad_task,
            bad_node,
            changes={"owned-5.txt": "descendant\n"},
            reported_files=["owned-5.txt"],
            output_kind="COMMIT",
            reported_commit=unrelated,
        )
        with self.assertRaisesRegex(ContractError, "descend.*exact base"):
            collect_task(self.paths, bad_sha)

    def test_local_review_rejects_builder_adapter_before_launch(self) -> None:
        task_sha, task, node_id = self.add_task(
            "self-review",
            owned_file="owned-6.txt",
            builder="claude-builder-v1",
            reviewer="kimi-builder-v1",
        )
        self._seed_local_handoff(
            task_sha,
            task,
            node_id,
            changes={"owned-6.txt": "needs review\n"},
            reported_files=["owned-6.txt"],
        )
        collect_task(self.paths, task_sha)
        adapter = self._adapter_config("claude")
        with self.assertRaisesRegex(ContractError, "reviewer must differ"):
            review_task(self.paths, task_sha, adapter)

    def test_local_review_uses_the_review_receipt_preflight_protocol(self) -> None:
        task_sha, task, node_id = self.add_task(
            "local-review-success",
            owned_file="owned-6.txt",
            builder="claude-builder-v1",
            reviewer="kimi-builder-v1",
        )
        self._seed_local_handoff(
            task_sha,
            task,
            node_id,
            changes={"owned-6.txt": "locally reviewed\n"},
            reported_files=["owned-6.txt"],
        )
        handoff = collect_task(self.paths, task_sha)
        reviewer_profile = load_worker_profile(
            self.repo / "build_control" / "worker_profiles" / "kimi-builder-v1.json",
            SchemaRegistry(self.repo),
        )
        provider_receipt = {
            "protocol": "helios.build.review-receipt/v1",
            "transport": "LOCAL_ADAPTER",
            "task_manifest_sha256": task_sha,
            "handoff_sha256": handoff.sha256,
            "reviewer_profile_sha256": worker_profile_sha256(reviewer_profile),
            "external_session_assignment_sha256": None,
            "raw_evidence_sha256": None,
            "verdict": "ACCEPTED",
            "findings": [],
            "command_receipt_sha256s": [],
            "source_verification": {
                "source_record_ids": [],
                "source_packet_ids": [],
            },
            "started_at": "2026-09-01T00:00:03Z",
            "duration_ms": 1,
        }
        adapter = self._adapter_config("kimi", review_payload=provider_receipt)
        receipt = review_task(self.paths, task_sha, adapter)
        self.assertEqual(receipt.transport, "LOCAL_ADAPTER")
        self.assertIsNotNone(receipt.preflight_receipt_sha256)
        self.assertTrue(receipt.command_receipt_sha256s)
        self.assertEqual(self.event_state(node_id), "ACCEPTED")

    def test_failed_affected_gate_is_persisted_and_unchanged_rerun_refused(self) -> None:
        task_sha, task, _ = self.add_task(
            "failed-gate", owned_file="owned-7.txt", affected_exit=3
        )
        worktree = create_detached_worktree(
            self.paths, sha256_hex(b"failed-gate-worktree"), self.base_sha
        )
        (worktree / "owned-7.txt").write_text("gate patch\n", encoding="utf-8")
        self._git_at(worktree, "add", "-N", "--all")
        first = run_verification_gate(
            self.paths, task, worktree, "AFFECTED_INTEGRATION"
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].outcome, "FAIL")
        self.assertIsNotNone(first[0].patch_sha256)
        before = tuple(
            (self.repo / "build_control" / "graph" / "receipts").rglob("*.json")
        )
        with self.assertRaisesRegex(ContractError, "unchanged failing command"):
            run_verification_gate(
                self.paths, task, worktree, "AFFECTED_INTEGRATION"
            )
        after = tuple(
            (self.repo / "build_control" / "graph" / "receipts").rglob("*.json")
        )
        self.assertEqual(before, after)
        payload = ContentAddressedStore(
            self.repo / "build_control" / "graph"
        ).get_json(first[0].sha256)
        self.assertEqual(payload["task_manifest_sha256"], task_sha)
        self.assertEqual(payload["patch_sha256"], first[0].patch_sha256)

    def test_critical_finding_prevents_acceptance_despite_passing_gate(self) -> None:
        task_sha, task, node_id = self.add_task(
            "critical-review", owned_file="owned-8.txt"
        )
        _, handoff, _, _ = self.import_builder(
            task_sha, task, owned_file="owned-8.txt"
        )
        reviewer = begin_external_session(
            self.paths, task_sha, "codex-reviewer-v1", "CODEX", "critical-reviewer",
            "REVIEWER", "EXTERNAL_SESSION_SELECTED",
        )
        review_path, raw_path = self.write_review(
            reviewer,
            task,
            handoff.sha256,
            verdict="ACCEPTED",
            findings=[{"severity": "CRITICAL", "code": "scope-breach"}],
        )
        receipt = import_external_review(
            self.paths, task_sha, reviewer.sha256, review_path, raw_path
        )
        self.assertEqual(receipt.verdict, "ACCEPTED")
        self.assertTrue(receipt.command_receipt_sha256s)
        self.assertEqual(self.event_state(node_id), "REVIEWED")

    def test_changes_required_creates_one_exact_successor_and_route(self) -> None:
        task_sha, task, node_id = self.add_task(
            "correction", owned_file="owned-9.txt"
        )
        _, handoff, _, _ = self.import_builder(
            task_sha, task, owned_file="owned-9.txt"
        )
        reviewer = begin_external_session(
            self.paths, task_sha, "codex-reviewer-v1", "CODEX", "correction-reviewer",
            "REVIEWER", "EXTERNAL_SESSION_SELECTED",
        )
        review_path, raw_path = self.write_review(
            reviewer,
            task,
            handoff.sha256,
            verdict="CHANGES_REQUIRED",
            findings=[{"severity": "MAJOR", "code": "needs-fix"}],
        )
        receipt = import_external_review(
            self.paths, task_sha, reviewer.sha256, review_path, raw_path
        )
        successor_sha = receipt.successor_task_manifest_sha256
        self.assertIsNotNone(successor_sha)
        successor = ContentAddressedStore(self.repo / "build_control").get_json(successor_sha)
        self.assertEqual(successor["supersedes_task_manifest_sha256"], task_sha)
        self.assertEqual(successor["correction_round"], 1)
        routes = replay_events(
            self.repo / "build_control" / "graph" / "routing.jsonl"
        )
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["action"], "CORRECTION")
        self.assertEqual(routes[0]["reason_code"], "REVIEW_CORRECTION")
        self.assertEqual(routes[0]["successor_task_manifest_sha256"], successor_sha)
        self.assertEqual(self.event_state(node_id), "SUPERSEDED")

    def test_concurrent_correction_callers_publish_one_successor(self) -> None:
        task_sha, task, node_id = self.add_task(
            "correction-race", owned_file="owned-10.txt"
        )
        _, handoff, _, _ = self.import_builder(
            task_sha, task, owned_file="owned-10.txt"
        )
        review_payload = {
            "protocol": "helios.build.review-receipt/v1",
            "transport": "LOCAL_ADAPTER",
            "task_manifest_sha256": task_sha,
            "handoff_sha256": handoff.sha256,
            "reviewer_profile_sha256": "c" * 64,
            "external_session_assignment_sha256": None,
            "raw_evidence_sha256": None,
            "verdict": "CHANGES_REQUIRED",
            "findings": [{"severity": "MAJOR", "code": "race-fix"}],
            "command_receipt_sha256s": [],
            "source_verification": {
                "source_record_ids": [],
                "source_packet_ids": [],
            },
            "started_at": "2026-09-01T00:00:04Z",
            "duration_ms": 1,
        }
        review = ContentAddressedStore(
            self.repo / "build_control" / "graph"
        ).put_json("receipts", review_payload)
        with ControllerLock(self.paths) as lock:
            transition_node_locked(
                lock,
                node_id,
                "RETURNED",
                "REVIEWED",
                "TEST_REVIEW_READY",
                "codex-control-v1",
            )

        original_put = ContentAddressedStore.put_json
        publisher_guard = threading.Lock()
        both_publishers = threading.Event()
        publisher_count = 0

        def delayed_task_publish(
            store: ContentAddressedStore,
            collection: str,
            payload: dict[str, object],
        ) -> object:
            nonlocal publisher_count
            if collection == "tasks":
                with publisher_guard:
                    publisher_count += 1
                    if publisher_count == 2:
                        both_publishers.set()
                both_publishers.wait(timeout=0.5)
            return original_put(store, collection, payload)

        start = threading.Barrier(3)
        results: list[object] = []
        failures: list[BaseException] = []

        def invoke() -> None:
            start.wait()
            try:
                results.append(
                    create_correction_successor(self.paths, task_sha, review.sha256)
                )
            except BaseException as error:
                failures.append(error)

        threads = [threading.Thread(target=invoke) for _ in range(2)]
        with patch.object(ContentAddressedStore, "put_json", new=delayed_task_publish):
            for thread in threads:
                thread.start()
            start.wait()
            for thread in threads:
                thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertTrue(results)
        self.assertTrue(
            all(isinstance(error, (ContractError, TransitionError)) for error in failures)
        )
        successors = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (self.repo / "build_control" / "tasks").rglob("*.json")
            if json.loads(path.read_text(encoding="utf-8")).get(
                "supersedes_task_manifest_sha256"
            ) == task_sha
        ]
        self.assertEqual(len(successors), 1)
        self.assertEqual(publisher_count, 1)
        self.assertEqual(self.event_state(node_id), "SUPERSEDED")

    def _seed_local_handoff(
        self,
        task_sha: str,
        task: dict[str, object],
        node_id: str,
        *,
        changes: dict[str, str],
        reported_files: list[str],
        output_kind: str = "PATCH",
        source_record_ids: list[str] | None = None,
        dependency_effects: list[str] | None = None,
        reported_commit: str | None = None,
    ) -> tuple[Path, str | None, bytes]:
        self._local_ordinal += 1
        run_sha = sha256_hex(f"local-{self._local_ordinal}-{task_sha}".encode("utf-8"))
        worktree = create_detached_worktree(self.paths, run_sha, self.base_sha)
        for relative, content in changes.items():
            target = worktree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        commit_sha: str | None = None
        if output_kind == "COMMIT":
            self._git_at(worktree, "add", "--all")
            self._git_at(worktree, "commit", "-m", "worker result")
            commit_sha = self._git_at(worktree, "rev-parse", "HEAD").stdout.strip()
        else:
            self._git_at(worktree, "add", "-N", "--all")
        canonical_patch = subprocess.run(
            [
                "git", "-C", str(worktree), "diff", "--binary", "--full-index",
                self.base_sha, "--",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        profile = load_worker_profile(
            self.repo / "build_control" / "worker_profiles"
            / f"{task['roles']['builder_profile_id']}.json",
            SchemaRegistry(self.repo),
        )
        attempt = {
            "protocol": "helios.build.attempt-manifest/v1",
            "attempt_id": f"attempt-{run_sha}",
            "task_manifest_sha256": task_sha,
            "base_commit_sha": self.base_sha,
            "adapter_configuration_sha256": "a" * 64,
            "run_identity_sha256": run_sha,
            "worker_profile_sha256": worker_profile_sha256(profile),
            "attempt_ordinal": 1,
            "preflight_receipt_sha256": "b" * 64,
            "worktree_key": run_sha,
            "resume_from_checkpoint_sha256": None,
            "created_at": "2026-09-01T00:00:01Z",
        }
        store = ContentAddressedStore(self.repo / "build_control" / "graph")
        attempt_object = store.put_json("attempts", attempt)
        with ControllerLock(self.paths) as lock:
            transition_node_locked(
                lock,
                node_id,
                "READY",
                "DISPATCHED",
                "LOCAL_ADAPTER_DISPATCHED",
                "codex-control-v1",
                attempt_manifest_sha256=attempt_object.sha256,
            )
        handoff = {
            "protocol": "helios.build.worker-handoff/v1",
            "transport": "LOCAL_ADAPTER",
            "attempt_manifest_sha256": attempt_object.sha256,
            "external_session_assignment_sha256": None,
            "task_manifest_sha256": task_sha,
            "output_kind": output_kind,
            "patch_sha256": sha256_hex(canonical_patch) if output_kind == "PATCH" else None,
            "commit_sha": (
                reported_commit if reported_commit is not None else commit_sha
            ) if output_kind == "COMMIT" else None,
            "files_changed": reported_files,
            "commands_executed": [],
            "focused_test_results": [],
            "assumptions": [],
            "unresolved_issues": [],
            "dependency_effects": dependency_effects or [],
            "security_effects": [],
            "source_record_ids": (
                task["required_source_record_ids"]
                if source_record_ids is None else source_record_ids
            ),
            "source_packet_ids": task["required_source_packet_ids"],
            "raw_evidence_sha256": None,
            "duration_ms": 1,
            "cost_microusd": 0,
        }
        raw = canonical_json_bytes(handoff)
        private = put_private_bytes(self.paths, "handoffs", raw)
        artifact = {
            "protocol": "helios.build.artifact-manifest/v1",
            "artifact_id": f"local-handoff-{attempt_object.sha256}",
            "attempt_manifest_sha256": attempt_object.sha256,
            "kind": "LOCAL_WORKER_HANDOFF_RAW",
            "sha256": private.sha256,
            "byte_size": private.byte_size,
            "media_type": "application/json",
            "store_key": private.store_key,
            "created_at": attempt["created_at"],
        }
        store.put_json("artifacts", artifact)
        return worktree, commit_sha, canonical_patch

    def _adapter_config(
        self,
        worker_id: str,
        *,
        review_payload: dict[str, object] | None = None,
    ) -> Path:
        executable = Path(sys.executable).resolve()
        dispatch_argv = ["-c", "raise SystemExit(99)"]
        if review_payload is not None:
            dispatch_argv = [
                "-c",
                "import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(bytes.fromhex(sys.argv[2]))",
                "{handoff_path}",
                canonical_json_bytes(review_payload).hex(),
            ]
        payload = {
            "protocol": "helios.build.adapter-config/v1",
            "host_id": "review-fixture",
            "adapters": [{
                "worker_id": worker_id,
                "adapter_name": "review-fixture",
                "adapter_revision": "v1",
                "executable": str(executable),
                "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "preflight_argv": ["-c", "raise SystemExit(0)"],
                "dispatch_argv": dispatch_argv,
                "environment_variable_names": [],
                "preflight_timeout_seconds": 2,
                "attempt_timeout_seconds": 2,
                "max_capture_bytes": 4096,
                "result_protocol": "helios.build.review-receipt/v1",
            }],
        }
        path = self.root / f"{worker_id}-review-adapter.json"
        path.write_bytes(canonical_json_bytes(payload))
        path.chmod(0o600)
        return path

    def _terminalize_local_fixture(self, node_id: str) -> None:
        dispatch = next(
            event for event in replay_events(
                self.repo / "build_control" / "graph" / "events.jsonl"
            )
            if event["node_id"] == node_id and event["new_state"] == "DISPATCHED"
        )
        with ControllerLock(self.paths) as lock:
            transition_node_locked(
                lock,
                node_id,
                "DISPATCHED",
                "FAILED",
                "TEST_FIXTURE_TERMINATED",
                "codex-control-v1",
                attempt_manifest_sha256=dispatch["attempt_manifest_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
