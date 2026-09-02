"""Real-process tests for the bounded P1A baseline-audit runner."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.agentic.artifacts import ArtifactRecord, ArtifactStore
from helios_takeoff_core.agentic.service import AgentExecutionService
from helios_takeoff_core.db import Database
from helios_takeoff_core.errors import ValidationError
from helios_takeoff_core.repository import TakeoffRepository


SIDE_EFFECT_TABLES = (
    "evidence_items",
    "extraction_claims",
    "quantity_assertions",
    "quantity_review_events",
    "takeoff_versions",
    "takeoff_approval_events",
    "quote_revisions",
    "estimate_versions",
    "estimate_approval_events",
    "bid_releases",
)


class AgentRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = Database(self.root / "helios.sqlite3")
        self.database.initialize()
        self.takeoff_repository = TakeoffRepository(self.database)
        self.service = AgentExecutionService(self.takeoff_repository)
        self.service.bootstrap_roster()
        self.artifact_store = ArtifactStore(self.root / "artifacts")
        self.project_id = self.takeoff_repository.create_project(code="P-400", name="Hospital Addition")
        self.actor_id = self.takeoff_repository.create_actor(display_name="Estimator", actor_type="USER")
        prior_id = self.takeoff_repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-100",
            title="Prior Mechanical Plan",
            sha256="a" * 64,
            issue_date="2026-08-01",
        )
        drawing_id = self.takeoff_repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-101",
            title="Mechanical Plan",
            sha256="b" * 64,
            issue_date="2026-08-27",
            supersedes_document_revision_id=prior_id,
        )
        other_id = str(uuid.uuid4())
        self.other_id = other_id
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO document_revisions(
                    id, project_id, document_type, document_number, title, sha256,
                    issue_date, received_at, supersedes_document_revision_id, created_at
                ) VALUES (?, ?, 'OTHER', 'G-001', 'Cover Sheet', ?, '',
                          '2026-08-26T12:00:00.000000+00:00', NULL,
                          '2026-08-26T12:00:00.000000+00:00')
                """,
                (other_id, self.project_id, "c" * 64),
            )
        self.revision_set_id = self.takeoff_repository.create_revision_set(
            project_id=self.project_id,
            name="BID-01",
        )
        self.takeoff_repository.include_document_revision(self.revision_set_id, drawing_id)
        self.takeoff_repository.include_document_revision(self.revision_set_id, other_id)
        self.takeoff_repository.freeze_revision_set(self.revision_set_id)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _runner_class(self):
        try:
            from helios_takeoff_core.agentic.runner import AgentRunner
        except ModuleNotFoundError:
            self.fail("Task 4 AgentRunner is missing")
        return AgentRunner

    def _submit(self, worker_code: str = "helios-baseline-auditor") -> dict[str, object]:
        return self.service.submit_baseline_audit(
            project_id=self.project_id,
            revision_set_id=self.revision_set_id,
            requested_by_actor_id=self.actor_id,
            worker_code=worker_code,
            artifact_store=self.artifact_store,
        )

    def _side_effect_counts(self) -> dict[str, int]:
        with self.database.connection() as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in SIDE_EFFECT_TABLES
            }

    def _attempt_artifacts(self, attempt_id: str) -> dict[str, tuple[ArtifactRecord, bytes]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT link.purpose, artifact.*
                FROM agent_execution_attempt_artifacts AS link
                JOIN agent_artifacts AS artifact ON artifact.id = link.artifact_id
                WHERE link.execution_attempt_id = ?
                ORDER BY link.purpose
                """,
                (attempt_id,),
            ).fetchall()
        result = {}
        for row in rows:
            record = ArtifactRecord(
                sha256=row["sha256"],
                byte_size=row["byte_size"],
                media_type=row["media_type"],
                schema_version=row["schema_version"],
                store_key=row["store_key"],
            )
            result[row["purpose"]] = (record, self.artifact_store.read_bytes(record))
        return result

    def _make_script_runner(self, body: str, *, timeout_seconds: int = 60):
        script = self.root / f"worker-{uuid.uuid4()}.py"
        script.write_text(textwrap.dedent(body), encoding="utf-8")
        AgentRunner = self._runner_class()

        class ScriptRunner(AgentRunner):
            def _command_for(self, claim):
                return [sys.executable, str(script)]

        return ScriptRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
            timeout_seconds=timeout_seconds,
        )

    def _make_external_worker_available(self, worker_code: str = "codex") -> None:
        assignment = self.service.repository.worker_assignment(worker_code)
        self.service.repository.record_worker_availability(
            worker_id=assignment["worker_id"],
            adapter_revision_id=assignment["adapter_revision_id"],
            availability="AVAILABLE",
            reason_code="TEST_PROCESS_READY",
        )

    def test_builtin_runner_executes_real_manifest_and_persists_verified_report(self) -> None:
        submitted = self._submit()
        before = self._side_effect_counts()
        AgentRunner = self._runner_class()
        runner = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
        )

        outcome = runner.run_once()

        self.assertEqual(outcome["state"], "SUCCEEDED")
        self.assertEqual(self.service.get_job(submitted["job_id"])["state"], "SUCCEEDED")
        artifacts = self._attempt_artifacts(outcome["attempt_id"])
        self.assertEqual(set(artifacts), {"INPUT", "STDOUT", "STDERR", "REPORT"})
        self.assertTrue(all(self.artifact_store.verify(record) for record, _ in artifacts.values()))
        self.assertEqual(artifacts["STDERR"][1], b"")
        report = json.loads(artifacts["REPORT"][1])
        self.assertEqual(report["kind"], "BASELINE_AUDIT_REPORT")
        self.assertEqual(report["payload"]["project_id"], self.project_id)
        self.assertEqual(report["payload"]["revision_set_id"], self.revision_set_id)
        self.assertEqual(report["payload"]["document_count"], 2)
        self.assertEqual(report["payload"]["document_counts_by_type"], {"DRAWING": 1, "OTHER": 1})
        self.assertEqual(report["payload"]["required_document_types_absent"], ["SCHEDULE", "SPECIFICATION"])
        self.assertEqual(len(report["payload"]["superseded_document_references_not_included"]), 1)
        self.assertEqual(report["payload"]["missing_issue_date_document_ids"], [self.other_id])
        self.assertIn("not drawing/spec contents or quantities", report["payload"]["limitation"])
        self.assertEqual(before, self._side_effect_counts())

    def test_run_once_returns_none_after_processing_one_item(self) -> None:
        self._submit()
        AgentRunner = self._runner_class()
        runner = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
        )

        first = runner.run_once()
        second = runner.run_once()

        self.assertEqual(first["state"], "SUCCEEDED")
        self.assertIsNone(second)
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM agent_execution_attempts").fetchone()[0], 1)

    def test_run_once_blocks_only_first_ordered_candidate_then_runs_next(self) -> None:
        blocked_job = self._submit("codex")
        runnable_job = self._submit()
        AgentRunner = self._runner_class()
        runner = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
        )

        first = runner.run_once()

        self.assertEqual(first["job_id"], blocked_job["job_id"])
        self.assertEqual(first["state"], "BLOCKED")
        self.assertEqual(first["reason_code"], "UNCONFIGURED")
        self.assertIsNone(first["attempt_id"])
        self.assertEqual(self.service.get_job(runnable_job["job_id"])["state"], "QUEUED")

        second = runner.run_once()

        self.assertEqual(second["job_id"], runnable_job["job_id"])
        self.assertEqual(second["state"], "SUCCEEDED")

    def test_identical_cas_bytes_can_be_recorded_for_two_baselines(self) -> None:
        first_job = self._submit()
        AgentRunner = self._runner_class()
        runner = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
        )
        first = runner.run_once()
        self.assertEqual(first["state"], "SUCCEEDED")

        other_project_id = self.takeoff_repository.create_project(code="P-401", name="Second Hospital")
        other_actor_id = self.takeoff_repository.create_actor(display_name="Second Estimator", actor_type="USER")
        other_document_id = self.takeoff_repository.register_document_revision(
            project_id=other_project_id,
            document_type="DRAWING",
            document_number="M-201",
            title="Second Mechanical Plan",
            sha256="d" * 64,
            issue_date="2026-08-27",
        )
        other_revision_set_id = self.takeoff_repository.create_revision_set(
            project_id=other_project_id,
            name="BID-02",
        )
        self.takeoff_repository.include_document_revision(other_revision_set_id, other_document_id)
        self.takeoff_repository.freeze_revision_set(other_revision_set_id)
        second_job = self.service.submit_baseline_audit(
            project_id=other_project_id,
            revision_set_id=other_revision_set_id,
            requested_by_actor_id=other_actor_id,
            worker_code="helios-baseline-auditor",
            artifact_store=self.artifact_store,
        )

        second = runner.run_once()

        self.assertEqual(second["state"], "SUCCEEDED")
        self.assertEqual(self.service.get_job(first_job["job_id"])["state"], "SUCCEEDED")
        self.assertEqual(self.service.get_job(second_job["job_id"])["state"], "SUCCEEDED")
        with self.database.connection() as connection:
            empty_stderr_rows = connection.execute(
                """
                SELECT project_id, revision_set_id
                FROM agent_artifacts
                WHERE sha256 = ?
                  AND media_type = 'application/vnd.helios.worker-stderr'
                  AND schema_version = 'helios.p1a.worker-stderr/v1'
                ORDER BY rowid
                """,
                ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",),
            ).fetchall()
            started = connection.execute(
                """
                SELECT COUNT(*)
                FROM agent_work_item_states
                WHERE state = 'STARTED'
                """
            ).fetchone()[0]
        self.assertEqual(
            {(row["project_id"], row["revision_set_id"]) for row in empty_stderr_rows},
            {
                (self.project_id, self.revision_set_id),
                (other_project_id, other_revision_set_id),
            },
        )
        self.assertEqual(started, 0)

    def test_manifest_worker_rejects_malformed_input_without_success_json(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "helios_takeoff_core.agentic.manifest_worker"],
            input=b'{"protocol":"wrong"}',
            capture_output=True,
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            cwd=self.root,
            timeout=5,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"manifest worker error", result.stderr)

    def test_supersession_audit_flags_only_predecessor_references_absent_from_baseline(self) -> None:
        with self.database.connection() as connection:
            successor = connection.execute(
                """
                SELECT id, supersedes_document_revision_id
                FROM document_revisions
                WHERE project_id = ? AND supersedes_document_revision_id IS NOT NULL
                """,
                (self.project_id,),
            ).fetchone()
        complete_revision_set_id = self.takeoff_repository.create_revision_set(
            project_id=self.project_id,
            name="BID-WITH-PREDECESSOR",
        )
        self.takeoff_repository.include_document_revision(
            complete_revision_set_id,
            successor["supersedes_document_revision_id"],
        )
        self.takeoff_repository.include_document_revision(complete_revision_set_id, successor["id"])
        self.takeoff_repository.freeze_revision_set(complete_revision_set_id)
        self.service.submit_baseline_audit(
            project_id=self.project_id,
            revision_set_id=complete_revision_set_id,
            requested_by_actor_id=self.actor_id,
            worker_code="helios-baseline-auditor",
            artifact_store=self.artifact_store,
        )
        AgentRunner = self._runner_class()
        outcome = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
        ).run_once()
        report = json.loads(self._attempt_artifacts(outcome["attempt_id"])["REPORT"][1])

        self.assertEqual(report["payload"]["superseded_document_references_not_included"], [])

    def test_malformed_stdout_escalates_and_persists_complete_failure_artifacts(self) -> None:
        self._make_external_worker_available()
        submitted = self._submit("codex")
        before = self._side_effect_counts()
        runner = self._make_script_runner(
            """
            import sys
            sys.stdin.buffer.read()
            sys.stderr.write("test worker diagnostic\\n")
            sys.stdout.write("not-json\\n")
            """
        )

        outcome = runner.run_once()

        self.assertEqual(outcome["state"], "ESCALATED")
        self.assertEqual(self.service.get_job(submitted["job_id"])["state"], "ESCALATED")
        artifacts = self._attempt_artifacts(outcome["attempt_id"])
        self.assertEqual(set(artifacts), {"INPUT", "STDOUT", "STDERR", "ERROR"})
        self.assertEqual(artifacts["STDOUT"][1], b"not-json\n")
        self.assertEqual(artifacts["STDERR"][1], b"test worker diagnostic\n")
        error = json.loads(artifacts["ERROR"][1])
        self.assertEqual(error["reason_code"], "WORKER_RESULT_INVALID")
        self.assertEqual(before, self._side_effect_counts())

    def test_timeout_schedules_finite_retry_and_preserves_p0_rows(self) -> None:
        self._make_external_worker_available()
        self._submit("codex")
        before = self._side_effect_counts()
        runner = self._make_script_runner(
            """
            import sys
            import time
            sys.stdin.buffer.read()
            sys.stderr.write("starting slow worker\\n")
            sys.stderr.flush()
            time.sleep(5)
            """,
            timeout_seconds=1,
        )

        outcome = runner.run_once()

        self.assertEqual(outcome["state"], "RETRY_SCHEDULED")
        artifacts = self._attempt_artifacts(outcome["attempt_id"])
        self.assertEqual(set(artifacts), {"INPUT", "STDOUT", "STDERR", "ERROR"})
        error = json.loads(artifacts["ERROR"][1])
        self.assertEqual(error["reason_code"], "WORKER_TIMEOUT")
        self.assertEqual(before, self._side_effect_counts())

    def test_unconfigured_worker_command_blocks_with_attempt_artifacts(self) -> None:
        self._make_external_worker_available()
        self._submit("codex")
        AgentRunner = self._runner_class()
        runner = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
        )

        outcome = runner.run_once()

        self.assertEqual(outcome["state"], "BLOCKED")
        self.assertEqual(outcome["reason_code"], "WORKER_COMMAND_UNCONFIGURED")
        artifacts = self._attempt_artifacts(outcome["attempt_id"])
        self.assertEqual(set(artifacts), {"INPUT", "STDOUT", "STDERR", "ERROR"})

    def test_database_rejects_success_without_complete_result_artifacts(self) -> None:
        submitted = self._submit()
        claim = self.service.claim_next_work_item()

        with self.database.connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO agent_work_item_events(
                        id, work_item_id, execution_attempt_id, event_type,
                        created_at, reason_code, detail_json
                    ) VALUES (?, ?, ?, 'SUCCEEDED',
                              '2026-08-27T23:59:59.000000+00:00', 'WORKER_COMPLETED', NULL)
                    """,
                    (str(uuid.uuid4()), submitted["work_item_id"], claim["attempt_id"]),
                )

    def test_database_rejects_reusing_input_as_success_output_artifacts(self) -> None:
        submitted = self._submit()
        claim = self.service.claim_next_work_item()

        with self.database.connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                for purpose in ("STDOUT", "STDERR", "REPORT"):
                    connection.execute(
                        """
                        INSERT INTO agent_execution_attempt_artifacts(
                            execution_attempt_id, artifact_id, purpose, created_at
                        ) VALUES (?, ?, ?, '2026-08-27T23:59:58.000000+00:00')
                        """,
                        (claim["attempt_id"], submitted["input_artifact_id"], purpose),
                    )
                connection.execute(
                    """
                    INSERT INTO agent_work_item_events(
                        id, work_item_id, execution_attempt_id, event_type,
                        created_at, reason_code, detail_json
                    ) VALUES (?, ?, ?, 'SUCCEEDED',
                              '2026-08-27T23:59:59.000000+00:00',
                              'WORKER_COMPLETED', ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        submitted["work_item_id"],
                        claim["attempt_id"],
                        json.dumps({"report_sha256": submitted["artifact"].sha256}),
                    ),
                )

    def test_service_rejects_success_without_verified_artifact_store(self) -> None:
        submitted = self._submit()
        claim = self.service.claim_next_work_item()

        with self.assertRaisesRegex(Exception, "artifact store"):
            self.service.record_succeeded(
                work_item_id=claim["work_item_id"],
                attempt_id=claim["attempt_id"],
                stdout=submitted["artifact"],
                stderr=submitted["artifact"],
                report=submitted["artifact"],
            )

    def test_service_rejects_stdout_whose_result_does_not_match_report_artifact(self) -> None:
        self._submit()
        claim = self.service.claim_next_work_item()
        report = {
            "kind": "BASELINE_AUDIT_REPORT",
            "schema_version": "helios.p1a.baseline-audit-report/v1",
            "payload": {
                "project_id": self.project_id,
                "revision_set_id": self.revision_set_id,
                "input_sha256": claim["input_sha256"],
                "document_count": 2,
                "document_counts_by_type": {"DRAWING": 1, "OTHER": 1},
                "required_document_types_absent": ["SCHEDULE", "SPECIFICATION"],
                "superseded_document_references_not_included": [],
                "missing_issue_date_document_ids": [self.other_id],
                "limitation": "Audited manifest metadata only, not drawing/spec contents or quantities.",
            },
        }
        result = {
            "protocol": "helios.p1a.worker-result/v1",
            "status": "SUCCEEDED",
            "input_sha256": claim["input_sha256"],
            "report": report,
        }
        stdout = self.artifact_store.put_bytes(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            media_type="application/vnd.helios.worker-stdout",
            schema_version="helios.p1a.worker-stdout/v1",
        )
        stderr = self.artifact_store.put_bytes(
            b"",
            media_type="application/vnd.helios.worker-stderr",
            schema_version="helios.p1a.worker-stderr/v1",
        )
        mismatched_report = self.artifact_store.put_json(
            report | {"payload": report["payload"] | {"limitation": "Different report bytes."}},
            media_type="application/vnd.helios.baseline-audit-report+json",
            schema_version="helios.p1a.baseline-audit-report/v1",
        )

        with self.assertRaisesRegex(Exception, "does not match"):
            self.service.record_succeeded(
                work_item_id=claim["work_item_id"],
                attempt_id=claim["attempt_id"],
                stdout=stdout,
                stderr=stderr,
                report=mismatched_report,
                artifact_store=self.artifact_store,
            )

    def test_invalid_attempt_root_is_rejected_before_claim(self) -> None:
        submitted = self._submit()
        invalid_root = self.root / "attempt-root-is-a-file"
        invalid_root.write_text("not a directory", encoding="utf-8")
        AgentRunner = self._runner_class()
        runner = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=invalid_root,
        )

        with self.assertRaises(OSError):
            runner.run_once()

        self.assertEqual(self.service.get_job(submitted["job_id"])["state"], "QUEUED")
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM agent_execution_attempts").fetchone()[0], 0)

    def test_one_shot_success_output_write_failure_records_non_success(self) -> None:
        class OneShotFailureStore(ArtifactStore):
            fail_next_output = False

            def put_bytes(self, content, *, media_type, schema_version):
                if self.fail_next_output:
                    self.fail_next_output = False
                    raise ValidationError("injected one-shot output persistence failure")
                return super().put_bytes(content, media_type=media_type, schema_version=schema_version)

        self.artifact_store = OneShotFailureStore(self.root / "artifacts")
        submitted = self._submit()
        self.artifact_store.fail_next_output = True
        AgentRunner = self._runner_class()
        runner = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
        )

        try:
            outcome = runner.run_once()
        except ValidationError:
            outcome = None

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome["state"], "RETRY_SCHEDULED")
        self.assertEqual(outcome["reason_code"], "OUTPUT_ARTIFACT_PERSISTENCE_FAILED")
        self.assertEqual(self.service.get_job(submitted["job_id"])["state"], "RETRY_SCHEDULED")
        self.assertEqual(set(self._attempt_artifacts(outcome["attempt_id"])), {"INPUT", "STDOUT", "STDERR", "ERROR"})

    def test_permanent_artifact_write_failure_raises_distinct_fatal_error(self) -> None:
        from helios_takeoff_core.agentic.runner import FatalArtifactPersistenceError

        class PermanentlyFailingStore(ArtifactStore):
            fail_outputs = False

            def put_bytes(self, content, *, media_type, schema_version):
                if self.fail_outputs:
                    raise OSError("injected permanent artifact failure")
                return super().put_bytes(content, media_type=media_type, schema_version=schema_version)

        self.artifact_store = PermanentlyFailingStore(self.root / "artifacts")
        submitted = self._submit()
        self.artifact_store.fail_outputs = True
        AgentRunner = self._runner_class()
        runner = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
        )

        with self.assertRaisesRegex(FatalArtifactPersistenceError, "attempt remains STARTED"):
            runner.run_once()

        self.assertEqual(self.service.get_job(submitted["job_id"])["state"], "STARTED")
        with self.database.connection() as connection:
            purposes = connection.execute(
                "SELECT purpose FROM agent_execution_attempt_artifacts ORDER BY purpose"
            ).fetchall()
        self.assertEqual([row["purpose"] for row in purposes], ["INPUT"])

    def test_restored_store_reconciles_orphaned_started_attempt_as_outcome_unknown(self) -> None:
        submitted = self._submit()
        claimed = self.service.claim_next_work_item()
        restored_database = Database(Path(self.database.path))
        restored_database.initialize()
        restored_service = AgentExecutionService(TakeoffRepository(restored_database))
        restored_store = ArtifactStore(self.root / "artifacts")

        with self.assertRaisesRegex(ValidationError, "process is dead"):
            restored_service.reconcile_attempt(
                attempt_id=claimed["attempt_id"],
                operator_actor_id=self.actor_id,
                operator_reason="Verified the orphaned worker PID is no longer present.",
                process_dead_asserted=False,
                artifact_store=restored_store,
            )

        reconciled = restored_service.reconcile_attempt(
            attempt_id=claimed["attempt_id"],
            operator_actor_id=self.actor_id,
            operator_reason="Verified the orphaned worker PID is no longer present.",
            process_dead_asserted=True,
            artifact_store=restored_store,
        )

        self.assertEqual(reconciled["state"], "ESCALATED")
        self.assertEqual(reconciled["reason_code"], "OUTCOME_UNKNOWN")
        job = restored_service.get_job(submitted["job_id"])
        self.assertEqual(job["state"], "ESCALATED")
        self.assertEqual(job["latest_detail"]["operator_actor_id"], self.actor_id)
        self.assertEqual(
            job["latest_detail"]["operator_reason"],
            "Verified the orphaned worker PID is no longer present.",
        )
        self.assertTrue(job["latest_detail"]["process_dead_asserted"])
        artifacts = self._attempt_artifacts(claimed["attempt_id"])
        self.assertEqual(set(artifacts), {"INPUT", "STDOUT", "STDERR", "ERROR"})
        error = json.loads(artifacts["ERROR"][1])
        self.assertTrue(error["outcome_unknown"])
        self.assertEqual(error["operator_actor_id"], self.actor_id)

    def test_completed_job_projection_exposes_worker_adapter_events_attempts_and_report_id(self) -> None:
        submitted = self._submit()
        AgentRunner = self._runner_class()
        outcome = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
        ).run_once()

        job = self.service.get_job(submitted["job_id"])

        self.assertEqual(job["worker"]["code"], "helios-baseline-auditor")
        self.assertEqual(job["worker"]["name"], "HELIOS Baseline Auditor")
        self.assertEqual(job["adapter"]["kind"], "BUILTIN")
        self.assertEqual(job["adapter"]["name"], "helios-baseline-auditor-adapter")
        self.assertEqual(job["adapter"]["revision"], "static-v1")
        self.assertRegex(job["adapter"]["configuration_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual([event["type"] for event in job["events"]], job["event_types"])
        self.assertTrue(all(event["created_at"].endswith("+00:00") for event in job["events"]))
        self.assertEqual(len(job["attempts"]), 1)
        attempt = job["attempts"][0]
        self.assertEqual(attempt["id"], outcome["attempt_id"])
        self.assertEqual(attempt["attempt_number"], 1)
        self.assertEqual(set(attempt["artifacts"]), {"INPUT", "STDOUT", "STDERR", "REPORT"})
        report = attempt["artifacts"]["REPORT"]
        self.assertEqual(job["report_artifact_id"], report["id"])
        self.assertRegex(report["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(report["media_type"], "application/vnd.helios.baseline-audit-report+json")
        self.assertEqual(report["schema_version"], "helios.p1a.baseline-audit-report/v1")


if __name__ == "__main__":
    unittest.main()
