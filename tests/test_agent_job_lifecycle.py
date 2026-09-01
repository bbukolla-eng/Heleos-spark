"""Behavioral tests for durable P1A baseline-audit job lifecycle state."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.agentic.artifacts import ArtifactStore
from helios_takeoff_core.db import Database
from helios_takeoff_core.errors import ImmutableStateError, NotFoundError, PreconditionError, ValidationError
from helios_takeoff_core.repository import TakeoffRepository


DOCUMENT_SHA256 = "a" * 64
SIDE_EFFECT_TABLES = (
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


class AgentJobLifecycleTests(unittest.TestCase):
    """Catch dispatch or retry state that escapes its immutable frozen baseline."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database = Database(root / "helios.sqlite3")
        self.database.initialize()
        self.takeoff_repository = TakeoffRepository(self.database)
        self.artifact_store = ArtifactStore(root / "artifacts")
        self.project_id = self.takeoff_repository.create_project(code="P-100", name="Clinic Renovation")
        self.actor_id = self.takeoff_repository.create_actor(display_name="Estimator", actor_type="USER")
        self.document_id = self.takeoff_repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-101",
            title="Mechanical Plan",
            sha256=DOCUMENT_SHA256,
            issue_date="2026-08-27",
        )
        self.revision_set_id = self._revision_set("BID-01", frozen=True)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _revision_set(self, name: str, *, frozen: bool) -> str:
        revision_set_id = self.takeoff_repository.create_revision_set(project_id=self.project_id, name=name)
        self.takeoff_repository.include_document_revision(revision_set_id, self.document_id)
        if frozen:
            self.takeoff_repository.freeze_revision_set(revision_set_id)
        return revision_set_id

    def _service(self):
        try:
            from helios_takeoff_core.agentic.service import AgentExecutionService
        except ModuleNotFoundError:
            self.fail("Task 3 durable AgentExecutionService is missing")
        return AgentExecutionService(self.takeoff_repository)

    def _submit(self, service, *, worker_code: str = "helios-baseline-auditor") -> dict[str, object]:
        return service.submit_baseline_audit(
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

    def _failure_artifacts(self) -> dict[str, object]:
        return {
            "stdout": self.artifact_store.put_bytes(
                b"",
                media_type="application/vnd.helios.worker-stdout",
                schema_version="helios.p1a.worker-stdout/v1",
            ),
            "stderr": self.artifact_store.put_bytes(
                b"",
                media_type="application/vnd.helios.worker-stderr",
                schema_version="helios.p1a.worker-stderr/v1",
            ),
            "error": self.artifact_store.put_json(
                {"protocol": "helios.p1a.test-execution-error/v1"},
                media_type="application/vnd.helios.execution-error+json",
                schema_version="helios.p1a.execution-error/v1",
            ),
        }

    @staticmethod
    def _manual_work_item(
        connection: sqlite3.Connection,
        source_work_item_id: str,
        *,
        max_attempts: object | None = None,
    ) -> str:
        work_item_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO agent_work_items(
                id, job_id, workflow_run_id, project_id, revision_set_id, capability,
                worker_id, adapter_revision_id, input_artifact_id, max_attempts, created_at
            )
            SELECT ?, job_id, workflow_run_id, project_id, revision_set_id, capability,
                   worker_id, adapter_revision_id, input_artifact_id,
                   COALESCE(?, max_attempts),
                   '2026-08-27T12:00:00.000000+00:00'
            FROM agent_work_items WHERE id = ?
            """,
            (work_item_id, max_attempts, source_work_item_id),
        )
        return work_item_id

    @staticmethod
    def _manual_attempt(connection: sqlite3.Connection, work_item_id: str) -> str:
        attempt_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO agent_execution_attempts(
                id, work_item_id, attempt_number, worker_id, adapter_revision_id, created_at
            )
            SELECT ?, id, 1, worker_id, adapter_revision_id, '2026-08-27T12:00:01.000000+00:00'
            FROM agent_work_items WHERE id = ?
            """,
            (attempt_id, work_item_id),
        )
        return attempt_id

    @staticmethod
    def _direct_event(
        connection: sqlite3.Connection,
        *,
        work_item_id: str,
        event_type: str,
        attempt_id: str | None,
        reason_code: str,
        created_at: str = "2026-08-27T12:00:02.000000+00:00",
    ) -> None:
        connection.execute(
            """
            INSERT INTO agent_work_item_events(
                id, work_item_id, execution_attempt_id, event_type, created_at, reason_code
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), work_item_id, attempt_id, event_type, created_at, reason_code),
        )

    def test_frozen_baseline_submission_persists_manifest_and_queued_graph_only(self) -> None:
        service = self._service()
        service.bootstrap_roster()
        before = self._side_effect_counts()

        submitted = self._submit(service)

        job = service.get_job(submitted["job_id"])
        self.assertEqual(job["state"], "QUEUED")
        self.assertEqual(job["project_id"], self.project_id)
        self.assertEqual(job["revision_set_id"], self.revision_set_id)
        self.assertEqual(job["requested_by_actor_id"], self.actor_id)
        self.assertEqual(job["max_attempts"], 2)
        self.assertEqual(job["event_types"], ["QUEUED"])
        self.assertEqual(before, self._side_effect_counts())
        with self.database.connection() as connection:
            artifact = connection.execute(
                "SELECT * FROM agent_artifacts WHERE id = ?", (submitted["input_artifact_id"],)
            ).fetchone()
            run = connection.execute(
                "SELECT * FROM agent_workflow_runs WHERE id = ?", (submitted["workflow_run_id"],)
            ).fetchone()
            item = connection.execute(
                "SELECT * FROM agent_work_items WHERE id = ?", (submitted["work_item_id"],)
            ).fetchone()
        self.assertEqual((artifact["project_id"], artifact["revision_set_id"]), (self.project_id, self.revision_set_id))
        self.assertEqual((run["project_id"], run["revision_set_id"]), (self.project_id, self.revision_set_id))
        self.assertEqual((item["project_id"], item["revision_set_id"]), (self.project_id, self.revision_set_id))
        stored_manifest = json.loads((Path(self.temporary_directory.name) / "artifacts" / artifact["store_key"]).read_text())
        self.assertEqual(stored_manifest["project_id"], self.project_id)
        self.assertEqual(stored_manifest["revision_set_id"], self.revision_set_id)
        self.assertEqual(stored_manifest["documents"][0]["sha256"], DOCUMENT_SHA256)

    def test_roster_bootstrap_is_idempotent_and_only_builtin_worker_is_available(self) -> None:
        service = self._service()

        first = service.bootstrap_roster()
        second = service.bootstrap_roster()

        expected_codes = {
            "codex",
            "claude",
            "kimi",
            "grok",
            "cursor",
            "grokbot",
            "helios-baseline-auditor",
        }
        self.assertEqual(set(first), expected_codes)
        self.assertEqual(second, first)
        with self.database.connection() as connection:
            workers = connection.execute("SELECT worker_code, id FROM agent_workers ORDER BY worker_code").fetchall()
            assignments = connection.execute("SELECT COUNT(*) FROM agent_worker_adapter_assignments").fetchone()[0]
            availability = connection.execute(
                """
                SELECT worker.worker_code, event.availability, event.reason_code
                FROM agent_workers AS worker
                JOIN agent_worker_availability_events AS event ON event.worker_id = worker.id
                WHERE event.rowid = (
                    SELECT latest.rowid FROM agent_worker_availability_events AS latest
                    WHERE latest.worker_id = worker.id
                    ORDER BY latest.created_at DESC, latest.rowid DESC LIMIT 1
                )
                ORDER BY worker.worker_code
                """
            ).fetchall()
        self.assertEqual({row["worker_code"]: row["id"] for row in workers}, first)
        self.assertEqual(assignments, 7)
        self.assertEqual(
            {row["worker_code"] for row in availability if row["availability"] == "AVAILABLE"},
            {"helios-baseline-auditor"},
        )
        self.assertEqual(
            {row["reason_code"] for row in availability if row["availability"] == "UNAVAILABLE"},
            {"UNCONFIGURED"},
        )

    def test_submission_rejects_invalid_baselines_actor_and_worker_without_fake_success(self) -> None:
        service = self._service()
        service.bootstrap_roster()
        open_revision_set_id = self._revision_set("BID-OPEN", frozen=False)
        other_project_id = self.takeoff_repository.create_project(code="P-200", name="Other Project")

        invalid_requests = (
            ({"revision_set_id": open_revision_set_id}, PreconditionError),
            ({"project_id": other_project_id}, ValidationError),
            ({"requested_by_actor_id": "unknown-actor"}, NotFoundError),
            ({"worker_code": "unknown-worker"}, NotFoundError),
        )
        for changes, error_type in invalid_requests:
            with self.subTest(changes=changes):
                arguments = {
                    "project_id": self.project_id,
                    "revision_set_id": self.revision_set_id,
                    "requested_by_actor_id": self.actor_id,
                    "worker_code": "helios-baseline-auditor",
                    "artifact_store": self.artifact_store,
                } | changes
                with self.assertRaises(error_type):
                    service.submit_baseline_audit(**arguments)

        external = self._submit(service, worker_code="codex")
        blocked_outcome = service.claim_next_work_item()
        self.assertEqual(blocked_outcome["state"], "BLOCKED")
        self.assertEqual(blocked_outcome["job_id"], external["job_id"])
        blocked = service.get_job(external["job_id"])
        self.assertEqual(blocked["state"], "BLOCKED")
        self.assertEqual(blocked["latest_reason_code"], "UNCONFIGURED")

    def test_claim_creates_bounded_attempt_input_link_and_adapter_lineage(self) -> None:
        service = self._service()
        service.bootstrap_roster()
        submitted = self._submit(service)

        claimed = service.claim_next_work_item()

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["work_item_id"], submitted["work_item_id"])
        self.assertEqual(claimed["attempt_number"], 1)
        self.assertEqual(claimed["input_artifact_id"], submitted["input_artifact_id"])
        job = service.get_job(submitted["job_id"])
        self.assertEqual(job["state"], "STARTED")
        self.assertEqual(job["event_types"], ["QUEUED", "LEASED", "STARTED"])
        with self.database.connection() as connection:
            attempt = connection.execute(
                "SELECT * FROM agent_execution_attempts WHERE id = ?", (claimed["attempt_id"],)
            ).fetchone()
            link = connection.execute(
                "SELECT * FROM agent_execution_attempt_artifacts WHERE execution_attempt_id = ?",
                (claimed["attempt_id"],),
            ).fetchone()
            assignment = connection.execute(
                """
                SELECT 1 FROM agent_worker_adapter_assignments
                WHERE worker_id = ? AND adapter_revision_id = ?
                """,
                (attempt["worker_id"], attempt["adapter_revision_id"]),
            ).fetchone()
        self.assertEqual(link["purpose"], "INPUT")
        self.assertEqual(link["artifact_id"], submitted["input_artifact_id"])
        self.assertIsNotNone(assignment)

    def test_retry_policy_escalates_non_progress_and_non_transient_failure(self) -> None:
        service = self._service()
        service.bootstrap_roster()
        repeated_job = self._submit(service)
        first = service.claim_next_work_item()

        first_state = service.record_failure_or_retry(
            work_item_id=first["work_item_id"],
            attempt_id=first["attempt_id"],
            failure_signature="timeout-reading-manifest",
            transient=True,
            **self._failure_artifacts(),
        )
        second = service.claim_next_work_item()
        second_state = service.record_failure_or_retry(
            work_item_id=second["work_item_id"],
            attempt_id=second["attempt_id"],
            failure_signature="timeout-reading-manifest",
            transient=True,
            **self._failure_artifacts(),
        )

        self.assertEqual(first_state, "RETRY_SCHEDULED")
        self.assertEqual(second_state, "ESCALATED")
        repeated = service.get_job(repeated_job["job_id"])
        self.assertEqual(repeated["latest_reason_code"], "NON_PROGRESS")
        with self.database.connection() as connection:
            attempt_count = connection.execute(
                "SELECT COUNT(*) FROM agent_execution_attempts WHERE work_item_id = ?",
                (first["work_item_id"],),
            ).fetchone()[0]
        self.assertEqual(attempt_count, 2)
        with self.assertRaises(ImmutableStateError):
            service.record_failure_or_retry(
                work_item_id=first["work_item_id"],
                attempt_id=second["attempt_id"],
                failure_signature="different",
                transient=True,
                **self._failure_artifacts(),
            )

        immediate_job = self._submit(service)
        immediate = service.claim_next_work_item()
        state = service.record_failure_or_retry(
            work_item_id=immediate["work_item_id"],
            attempt_id=immediate["attempt_id"],
            failure_signature="invalid-result-contract",
            transient=False,
            **self._failure_artifacts(),
        )
        self.assertEqual(state, "ESCALATED")
        self.assertEqual(service.get_job(immediate_job["job_id"])["latest_reason_code"], "NON_TRANSIENT_FAILURE")

    def test_database_rejects_illegal_direct_lifecycle_histories(self) -> None:
        service = self._service()
        service.bootstrap_roster()
        submitted = self._submit(service)

        with self.database.connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                self._direct_event(
                    connection,
                    work_item_id=submitted["work_item_id"],
                    event_type="RETRY_SCHEDULED",
                    attempt_id=None,
                    reason_code="FORGED_RETRY",
                )

            no_history = self._manual_work_item(connection, submitted["work_item_id"])
            with self.assertRaises(sqlite3.IntegrityError):
                self._direct_event(
                    connection,
                    work_item_id=no_history,
                    event_type="STARTED",
                    attempt_id=None,
                    reason_code="FORGED_START",
                )

            skipped_lease = self._manual_work_item(connection, submitted["work_item_id"])
            self._direct_event(
                connection,
                work_item_id=skipped_lease,
                event_type="QUEUED",
                attempt_id=None,
                reason_code="SUBMITTED",
            )
            skipped_lease_attempt = self._manual_attempt(connection, skipped_lease)
            with self.assertRaises(sqlite3.IntegrityError):
                self._direct_event(
                    connection,
                    work_item_id=skipped_lease,
                    event_type="STARTED",
                    attempt_id=skipped_lease_attempt,
                    reason_code="FORGED_START",
                )

            no_input = self._manual_work_item(connection, submitted["work_item_id"])
            self._direct_event(
                connection,
                work_item_id=no_input,
                event_type="QUEUED",
                attempt_id=None,
                reason_code="SUBMITTED",
            )
            no_input_attempt = self._manual_attempt(connection, no_input)
            self._direct_event(
                connection,
                work_item_id=no_input,
                event_type="LEASED",
                attempt_id=no_input_attempt,
                reason_code="CLAIMED",
            )
            self._direct_event(
                connection,
                work_item_id=no_input,
                event_type="STARTED",
                attempt_id=no_input_attempt,
                reason_code="ATTEMPT_STARTED",
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self._direct_event(
                    connection,
                    work_item_id=no_input,
                    event_type="SUCCEEDED",
                    attempt_id=no_input_attempt,
                    reason_code="FORGED_SUCCESS",
                )

    def test_database_rejects_control_whitespace_and_malformed_reason_codes(self) -> None:
        service = self._service()
        roster = service.bootstrap_roster()
        assignment = service.repository.worker_assignment("helios-baseline-auditor")
        submitted = self._submit(service)

        with self.database.connection() as connection:
            for reason_code in ("\t\n", "lowercase", "BAD-REASON", "VALID\x00HIDDEN", "VALID\x1fHIDDEN"):
                with self.subTest(table="work_item", reason_code=reason_code):
                    work_item_id = self._manual_work_item(connection, submitted["work_item_id"])
                    with self.assertRaises(sqlite3.IntegrityError):
                        self._direct_event(
                            connection,
                            work_item_id=work_item_id,
                            event_type="QUEUED",
                            attempt_id=None,
                            reason_code=reason_code,
                        )
                with self.subTest(table="availability", reason_code=reason_code):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            """
                            INSERT INTO agent_worker_availability_events(
                                id, worker_id, availability, created_at,
                                adapter_revision_id, reason_code
                            ) VALUES (?, ?, 'AVAILABLE', ?, ?, ?)
                            """,
                            (
                                str(uuid.uuid4()),
                                roster["helios-baseline-auditor"],
                                "2026-08-27T12:10:00+00:00",
                                assignment["adapter_revision_id"],
                                reason_code,
                            ),
                        )

    def test_failure_signature_detail_contains_only_canonical_sha256(self) -> None:
        service = self._service()
        service.bootstrap_roster()
        submitted = self._submit(service)
        claimed = service.claim_next_work_item()

        service.record_failure_or_retry(
            work_item_id=claimed["work_item_id"],
            attempt_id=claimed["attempt_id"],
            failure_signature="leak me\nsecret",
            transient=True,
            **self._failure_artifacts(),
        )

        job = service.get_job(submitted["job_id"])
        encoded_detail = json.dumps(job["latest_detail"], sort_keys=True)
        self.assertNotIn("leak me", encoded_detail)
        self.assertNotIn("secret", encoded_detail)
        self.assertNotIn("failure_signature", job["latest_detail"])
        self.assertEqual(
            job["latest_detail"]["failure_signature_sha256"],
            "043d50507ac347b99b758a7da50371e03a50e451a6f11801129b4774b33533f9",
        )

    def test_bootstrap_does_not_supersede_later_exact_adapter_availability(self) -> None:
        service = self._service()
        service.bootstrap_roster()
        assignment = service.repository.worker_assignment("helios-baseline-auditor")
        service.repository.record_worker_availability(
            worker_id=assignment["worker_id"],
            adapter_revision_id=assignment["adapter_revision_id"],
            availability="UNAVAILABLE",
            reason_code="MAINTENANCE",
        )
        with self.database.connection() as connection:
            before = connection.execute(
                """
                SELECT COUNT(*) FROM agent_worker_availability_events
                WHERE worker_id = ? AND adapter_revision_id = ?
                """,
                (assignment["worker_id"], assignment["adapter_revision_id"]),
            ).fetchone()[0]

        service.bootstrap_roster()

        with self.database.connection() as connection:
            events = connection.execute(
                """
                SELECT availability, reason_code
                FROM agent_worker_availability_events
                WHERE worker_id = ? AND adapter_revision_id = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (assignment["worker_id"], assignment["adapter_revision_id"]),
            ).fetchall()
        self.assertEqual(len(events), before)
        self.assertEqual((events[0]["availability"], events[0]["reason_code"]), ("UNAVAILABLE", "MAINTENANCE"))
        submitted = self._submit(service)
        blocked_outcome = service.claim_next_work_item()
        self.assertEqual(blocked_outcome["state"], "BLOCKED")
        self.assertEqual(service.get_job(submitted["job_id"])["latest_reason_code"], "MAINTENANCE")

    def test_claim_uses_availability_for_work_item_exact_adapter(self) -> None:
        service = self._service()
        service.bootstrap_roster()
        submitted = self._submit(service)
        static_assignment = service.repository.worker_assignment("helios-baseline-auditor")
        alternate_adapter_id = service.repository.register_adapter_revision(
            adapter_name="alternate-auditor-adapter",
            adapter_kind="BUILTIN",
            revision="v1",
            configuration_sha256="b" * 64,
        )
        service.repository.assign_worker_adapter(
            worker_id=static_assignment["worker_id"],
            adapter_revision_id=alternate_adapter_id,
        )
        service.repository.record_worker_availability(
            worker_id=static_assignment["worker_id"],
            adapter_revision_id=alternate_adapter_id,
            availability="UNAVAILABLE",
            reason_code="ALTERNATE_OFFLINE",
        )

        claimed = service.claim_next_work_item()

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["work_item_id"], submitted["work_item_id"])
        self.assertEqual(claimed["adapter_revision_id"], static_assignment["adapter_revision_id"])

    def test_migration_013_preserves_legacy_009_history_as_readable(self) -> None:
        migration_directory = Path(__file__).resolve().parents[1] / "src" / "helios_takeoff_core" / "migrations"
        legacy_path = Path(self.temporary_directory.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            for migration in sorted(migration_directory.glob("00[1-9]_*.sql")):
                connection.executescript(migration.read_text(encoding="utf-8"))
            connection.executescript(
                """
                INSERT INTO projects(id, code, name, created_at)
                VALUES ('project', 'LEGACY', 'Legacy', '2026-01-01T00:00:00+00:00');
                INSERT INTO revision_sets(id, project_id, name, status, created_at, frozen_at)
                VALUES ('baseline', 'project', 'Legacy', 'FROZEN',
                        '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
                INSERT INTO agent_workers(id, name, created_at)
                VALUES ('worker', 'Legacy', '2026-01-01T00:00:00+00:00');
                INSERT INTO agent_worker_capabilities(worker_id, capability, created_at)
                VALUES ('worker', 'BASELINE_AUDIT', '2026-01-01T00:00:00+00:00');
                INSERT INTO agent_adapter_revisions(
                    id, adapter_name, adapter_kind, revision, configuration_sha256, created_at
                ) VALUES ('adapter', 'legacy', 'BUILTIN', 'v1',
                          'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                          '2026-01-01T00:00:00+00:00');
                INSERT INTO agent_jobs(id, project_id, revision_set_id, job_type, created_at)
                VALUES ('job', 'project', 'baseline', 'BASELINE_AUDIT', '2026-01-01T00:00:00+00:00');
                INSERT INTO agent_workflow_runs(
                    id, job_id, project_id, revision_set_id, workflow_name, created_at
                ) VALUES ('run', 'job', 'project', 'baseline', 'legacy', '2026-01-01T00:00:00+00:00');
                INSERT INTO agent_work_items(
                    id, job_id, workflow_run_id, project_id, revision_set_id, capability,
                    worker_id, adapter_revision_id, max_attempts, created_at
                ) VALUES ('item', 'job', 'run', 'project', 'baseline', 'BASELINE_AUDIT',
                          'worker', 'adapter', 1, '2026-01-01T00:00:00+00:00');
                INSERT INTO agent_work_item_events(
                    id, work_item_id, execution_attempt_id, event_type, created_at
                ) VALUES ('legacy-start', 'item', NULL, 'STARTED', '2026-01-01T00:00:00+00:00');
                """
            )
            connection.execute("PRAGMA foreign_keys = OFF")
            for migration in sorted(migration_directory.glob("01[0-3]_*.sql")):
                connection.executescript(migration.read_text(encoding="utf-8"))
            connection.execute("PRAGMA foreign_keys = ON")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

            legacy_event = connection.execute(
                "SELECT event_type, reason_code FROM agent_work_item_events WHERE id = 'legacy-start'"
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(legacy_event, ("STARTED", None))

    def test_database_rejects_skipped_attempt_ordinals_and_terminal_retry(self) -> None:
        service = self._service()
        service.bootstrap_roster()
        submitted = self._submit(service)

        with self.database.connection() as connection:
            skipped = self._manual_work_item(connection, submitted["work_item_id"], max_attempts=3)
            self._direct_event(
                connection,
                work_item_id=skipped,
                event_type="QUEUED",
                attempt_id=None,
                reason_code="SUBMITTED",
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO agent_execution_attempts(
                        id, work_item_id, attempt_number, worker_id,
                        adapter_revision_id, created_at
                    )
                    SELECT ?, id, 2, worker_id, adapter_revision_id,
                           '2026-08-27T12:01:00+00:00'
                    FROM agent_work_items WHERE id = ?
                    """,
                    (str(uuid.uuid4()), skipped),
                )

            precreated = self._manual_work_item(connection, submitted["work_item_id"], max_attempts=3)
            self._direct_event(
                connection,
                work_item_id=precreated,
                event_type="QUEUED",
                attempt_id=None,
                reason_code="SUBMITTED",
            )
            self._manual_attempt(connection, precreated)
            second_precreated_attempt = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO agent_execution_attempts(
                    id, work_item_id, attempt_number, worker_id,
                    adapter_revision_id, created_at
                )
                SELECT ?, id, 2, worker_id, adapter_revision_id,
                       '2026-08-27T12:01:30+00:00'
                FROM agent_work_items WHERE id = ?
                """,
                (second_precreated_attempt, precreated),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self._direct_event(
                    connection,
                    work_item_id=precreated,
                    event_type="LEASED",
                    attempt_id=second_precreated_attempt,
                    reason_code="FORGED_LEASE",
                )

            exhausted = self._manual_work_item(connection, submitted["work_item_id"], max_attempts=1)
            self._direct_event(
                connection,
                work_item_id=exhausted,
                event_type="QUEUED",
                attempt_id=None,
                reason_code="SUBMITTED",
            )
            exhausted_attempt = self._manual_attempt(connection, exhausted)
            self._direct_event(
                connection,
                work_item_id=exhausted,
                event_type="LEASED",
                attempt_id=exhausted_attempt,
                reason_code="CLAIMED",
            )
            self._direct_event(
                connection,
                work_item_id=exhausted,
                event_type="STARTED",
                attempt_id=exhausted_attempt,
                reason_code="ATTEMPT_STARTED",
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self._direct_event(
                    connection,
                    work_item_id=exhausted,
                    event_type="RETRY_SCHEDULED",
                    attempt_id=exhausted_attempt,
                    reason_code="FORGED_RETRY",
                )

    def test_database_rejects_noncanonical_storage_and_backdated_facts(self) -> None:
        service = self._service()
        roster = service.bootstrap_roster()
        assignment = service.repository.worker_assignment("helios-baseline-auditor")
        submitted = self._submit(service)

        with self.database.connection() as connection:
            for invalid_budget in ("UNBOUNDED", "2", sqlite3.Binary(b"2")):
                with self.subTest(field="max_attempts", value=invalid_budget):
                    with self.assertRaises(sqlite3.IntegrityError):
                        self._manual_work_item(
                            connection,
                            submitted["work_item_id"],
                            max_attempts=invalid_budget,
                        )

            for invalid_ordinal in ("1", sqlite3.Binary(b"1")):
                with self.subTest(field="attempt_number", value=invalid_ordinal):
                    work_item_id = self._manual_work_item(
                        connection,
                        submitted["work_item_id"],
                    )
                    self._direct_event(
                        connection,
                        work_item_id=work_item_id,
                        event_type="QUEUED",
                        attempt_id=None,
                        reason_code="SUBMITTED",
                    )
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            """
                            INSERT INTO agent_execution_attempts(
                                id, work_item_id, attempt_number, worker_id,
                                adapter_revision_id, created_at
                            )
                            SELECT ?, id, ?, worker_id, adapter_revision_id,
                                   '2026-08-27T12:00:03.000000+00:00'
                            FROM agent_work_items WHERE id = ?
                            """,
                            (str(uuid.uuid4()), invalid_ordinal, work_item_id),
                        )

            blob_reason_item_id = self._manual_work_item(
                connection,
                submitted["work_item_id"],
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self._direct_event(
                    connection,
                    work_item_id=blob_reason_item_id,
                    event_type="QUEUED",
                    attempt_id=None,
                    reason_code=sqlite3.Binary(b"SUBMITTED"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO agent_worker_availability_events(
                        id, worker_id, availability, created_at,
                        adapter_revision_id, reason_code
                    ) VALUES (?, ?, 'AVAILABLE', ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        roster["helios-baseline-auditor"],
                        "2026-08-27T12:10:00.000000+00:00",
                        assignment["adapter_revision_id"],
                        sqlite3.Binary(b"BUILTIN_READY"),
                    ),
                )

            invalid_time_item_id = self._manual_work_item(
                connection,
                submitted["work_item_id"],
            )
            with self.assertRaises(sqlite3.IntegrityError):
                self._direct_event(
                    connection,
                    work_item_id=invalid_time_item_id,
                    event_type="QUEUED",
                    attempt_id=None,
                    reason_code="SUBMITTED",
                    created_at="2026-02-31T12:00:02.000000+00:00",
                )

            ordered_item_id = self._manual_work_item(
                connection,
                submitted["work_item_id"],
            )
            self._direct_event(
                connection,
                work_item_id=ordered_item_id,
                event_type="QUEUED",
                attempt_id=None,
                reason_code="SUBMITTED",
                created_at="2026-08-27T12:00:02.000000+00:00",
            )
            ordered_attempt_id = self._manual_attempt(connection, ordered_item_id)
            with self.assertRaises(sqlite3.IntegrityError):
                self._direct_event(
                    connection,
                    work_item_id=ordered_item_id,
                    event_type="LEASED",
                    attempt_id=ordered_attempt_id,
                    reason_code="CLAIMED",
                    created_at="2026-08-27T12:00:01.000000+00:00",
                )

            latest_availability = connection.execute(
                """
                SELECT created_at FROM agent_worker_availability_events
                WHERE worker_id = ? AND adapter_revision_id = ?
                ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (roster["helios-baseline-auditor"], assignment["adapter_revision_id"]),
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO agent_worker_availability_events(
                        id, worker_id, availability, created_at,
                        adapter_revision_id, reason_code
                    ) VALUES (?, ?, 'UNAVAILABLE', ?, ?, 'MAINTENANCE')
                    """,
                    (
                        str(uuid.uuid4()),
                        roster["helios-baseline-auditor"],
                        "2026-01-01T00:00:00.000000+00:00",
                        assignment["adapter_revision_id"],
                    ),
                )
            self.assertRegex(latest_availability, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$")

    def test_database_rejects_lifecycle_event_below_persisted_append_position(self) -> None:
        service = self._service()
        service.bootstrap_roster()
        submitted = self._submit(service)

        with self.database.connection() as connection:
            queued_at = connection.execute(
                "SELECT created_at FROM agent_work_item_events WHERE work_item_id = ?",
                (submitted["work_item_id"],),
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO agent_work_item_events(
                        rowid, id, work_item_id, execution_attempt_id, event_type,
                        created_at, reason_code, detail_json
                    ) VALUES (0, ?, ?, NULL, 'BLOCKED', ?, 'FORGED_BLOCK', NULL)
                    """,
                    (str(uuid.uuid4()), submitted["work_item_id"], queued_at),
                )

    def test_database_rejects_availability_fact_below_exact_adapter_append_position(self) -> None:
        service = self._service()
        roster = service.bootstrap_roster()
        assignment = service.repository.worker_assignment("helios-baseline-auditor")

        with self.database.connection() as connection:
            latest_at = connection.execute(
                """
                SELECT created_at FROM agent_worker_availability_events
                WHERE worker_id = ? AND adapter_revision_id = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (roster["helios-baseline-auditor"], assignment["adapter_revision_id"]),
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO agent_worker_availability_events(
                        rowid, id, worker_id, availability, created_at,
                        adapter_revision_id, reason_code
                    ) VALUES (0, ?, ?, 'UNAVAILABLE', ?, ?, 'FORGED_OFFLINE')
                    """,
                    (
                        str(uuid.uuid4()),
                        roster["helios-baseline-auditor"],
                        latest_at,
                        assignment["adapter_revision_id"],
                    ),
                )

    def test_post_claim_blocked_records_the_owned_started_attempt(self) -> None:
        service = self._service()
        service.bootstrap_roster()
        submitted = self._submit(service)
        claimed = service.claim_next_work_item()

        service.record_blocked(
            work_item_id=claimed["work_item_id"],
            reason_code="command_missing",
            **self._failure_artifacts(),
        )

        job = service.get_job(submitted["job_id"])
        self.assertEqual(job["state"], "BLOCKED")
        self.assertEqual(job["latest_reason_code"], "COMMAND_MISSING")
        with self.database.connection() as connection:
            blocked = connection.execute(
                """
                SELECT execution_attempt_id FROM agent_work_item_events
                WHERE work_item_id = ? AND event_type = 'BLOCKED'
                """,
                (claimed["work_item_id"],),
            ).fetchone()
        self.assertEqual(blocked["execution_attempt_id"], claimed["attempt_id"])


if __name__ == "__main__":
    unittest.main()
