"""Tests for the immutable P1A execution ledger and its P0 manifest projection."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database
from helios_takeoff_core.errors import NotFoundError, PreconditionError
from helios_takeoff_core.repository import TakeoffRepository


DOCUMENT_HASH = "a" * 64
PRIOR_DOCUMENT_HASH = "b" * 64
ADAPTER_HASH = "c" * 64
ARTIFACT_HASH = "d" * 64
CREATED_AT = "2026-08-27T12:00:00.000000+00:00"


class AgentExecutionSchemaTests(unittest.TestCase):
    """Catch mutable execution facts and execution lineage detached from frozen P0 input."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "p0.sqlite3")
        self.database.initialize()
        self.repository = TakeoffRepository(self.database)
        self.project_id = self.repository.create_project(code="P-100", name="Clinic Renovation")
        self.actor_id = self.repository.create_actor(display_name="P1A requester", actor_type="USER")
        self.prior_document_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-100",
            title="Mechanical Plan - Prior",
            sha256=PRIOR_DOCUMENT_HASH,
            issue_date="2026-08-01",
        )
        self.document_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-101",
            title="Mechanical Plan",
            sha256=DOCUMENT_HASH,
            issue_date="2026-08-27",
            supersedes_document_revision_id=self.prior_document_id,
        )
        self.frozen_revision_set_id = self._create_revision_set(name="BID-01", frozen=True)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _create_revision_set(self, *, name: str, frozen: bool) -> str:
        revision_set_id = self.repository.create_revision_set(project_id=self.project_id, name=name)
        self.repository.include_document_revision(revision_set_id, self.document_id)
        if frozen:
            self.repository.freeze_revision_set(revision_set_id)
        return revision_set_id

    def _insert_execution_graph(self, connection: sqlite3.Connection, *, max_attempts: int = 1) -> None:
        connection.execute(
            """
            INSERT INTO agent_workers(id, name, created_at, worker_code)
            VALUES ('worker-1', 'Audit worker', ?, 'audit-worker')
            """,
            (CREATED_AT,),
        )
        connection.execute(
            """
            INSERT INTO agent_worker_capabilities(worker_id, capability, created_at)
            VALUES ('worker-1', 'BASELINE_AUDIT', ?)
            """,
            (CREATED_AT,),
        )
        connection.execute(
            """
            INSERT INTO agent_adapter_revisions(
                id, adapter_name, adapter_kind, revision, configuration_sha256, created_at
            ) VALUES ('adapter-1', 'local-audit', 'SUBPROCESS', 'v1', ?, ?)
            """,
            (ADAPTER_HASH, CREATED_AT),
        )
        connection.execute(
            """
            INSERT INTO agent_worker_adapter_assignments(worker_id, adapter_revision_id, created_at)
            VALUES ('worker-1', 'adapter-1', ?)
            """,
            (CREATED_AT,),
        )
        connection.execute(
            """
            INSERT INTO agent_jobs(
                id, project_id, revision_set_id, job_type, created_at,
                requested_by_actor_id, policy_sha256
            ) VALUES ('job-1', ?, ?, 'BASELINE_AUDIT', ?, ?, ?)
            """,
            (self.project_id, self.frozen_revision_set_id, CREATED_AT, self.actor_id, "f" * 64),
        )
        connection.execute(
            """
            INSERT INTO agent_workflow_runs(
                id, job_id, project_id, revision_set_id, workflow_name, created_at,
                workflow_definition_sha256
            ) VALUES ('run-1', 'job-1', ?, ?, 'baseline-audit', ?, ?)
            """,
            (self.project_id, self.frozen_revision_set_id, CREATED_AT, "e" * 64),
        )
        connection.execute(
            """
            INSERT INTO agent_work_items(
                id, job_id, workflow_run_id, project_id, revision_set_id, capability,
                worker_id, adapter_revision_id, max_attempts, created_at
            ) VALUES ('item-1', 'job-1', 'run-1', ?, ?, 'BASELINE_AUDIT',
                      'worker-1', 'adapter-1', ?, ?)
            """,
            (self.project_id, self.frozen_revision_set_id, max_attempts, CREATED_AT),
        )

    def test_migration_and_frozen_manifest_export_canonical_metadata(self) -> None:
        self.repository.record_evidence_item(
            document_revision_id=self.document_id,
            sheet_id="M-101",
            page_number=1,
            text_span="AHU-1",
        )
        self.repository.record_evidence_item(
            document_revision_id=self.document_id,
            sheet_id="M-101",
            page_number=2,
            geometry={"bbox": [1, 2, 3, 4]},
        )

        manifest = self.repository.export_revision_set_manifest(self.frozen_revision_set_id)

        with self.database.connection() as connection:
            migration = connection.execute(
                "SELECT name FROM schema_migrations WHERE version = 9"
            ).fetchone()
        self.assertEqual(migration["name"], "009_agent_execution_spine.sql")
        self.assertEqual(
            manifest,
            {
                "revision_set_id": self.frozen_revision_set_id,
                "project_id": self.project_id,
                "documents": [
                    {
                        "document_revision_id": self.document_id,
                        "document_type": "DRAWING",
                        "document_number": "M-101",
                        "title": "Mechanical Plan",
                        "sha256": DOCUMENT_HASH,
                        "issue_date": "2026-08-27",
                        "supersedes_document_revision_id": self.prior_document_id,
                        "evidence_count": 2,
                    }
                ],
            },
        )

    def test_manifest_rejects_open_or_unknown_revision_set(self) -> None:
        open_revision_set_id = self._create_revision_set(name="BID-OPEN", frozen=False)

        with self.assertRaises(PreconditionError):
            self.repository.export_revision_set_manifest(open_revision_set_id)
        with self.assertRaises(NotFoundError):
            self.repository.export_revision_set_manifest("missing-revision-set")

    def test_open_baseline_cannot_become_agent_job(self) -> None:
        open_revision_set_id = self._create_revision_set(name="BID-OPEN", frozen=False)

        with self.database.connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO agent_jobs(
                        id, project_id, revision_set_id, job_type, created_at,
                        requested_by_actor_id, policy_sha256
                    ) VALUES ('job-open', ?, ?, 'BASELINE_AUDIT', ?, ?, ?)
                    """,
                    (self.project_id, open_revision_set_id, CREATED_AT, self.actor_id, "f" * 64),
                )

    def test_p1a_identity_rejects_direct_update_and_delete(self) -> None:
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO agent_workers(id, name, created_at, worker_code)
                VALUES ('worker-1', 'Audit worker', ?, 'audit-worker')
                """,
                (CREATED_AT,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE agent_workers SET name = 'Changed' WHERE id = 'worker-1'")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM agent_workers WHERE id = 'worker-1'")

    def test_work_item_lineage_must_match_job_and_run(self) -> None:
        other_revision_set_id = self._create_revision_set(name="BID-02", frozen=True)
        with self.database.connection() as connection:
            self._insert_execution_graph(connection)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO agent_work_items(
                        id, job_id, workflow_run_id, project_id, revision_set_id, capability,
                        worker_id, adapter_revision_id, max_attempts, created_at
                    ) VALUES ('item-wrong', 'job-1', 'run-1', ?, ?, 'BASELINE_AUDIT',
                              'worker-1', 'adapter-1', 1, ?)
                    """,
                    (self.project_id, other_revision_set_id, CREATED_AT),
                )

    def test_attempt_must_match_assignment_and_not_exceed_max_attempts(self) -> None:
        with self.database.connection() as connection:
            self._insert_execution_graph(connection, max_attempts=1)
            connection.execute(
                """
                INSERT INTO agent_adapter_revisions(
                    id, adapter_name, adapter_kind, revision, configuration_sha256, created_at
                ) VALUES ('adapter-2', 'builtin-audit', 'BUILTIN', 'v1', ?, ?)
                """,
                ("e" * 64, CREATED_AT),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO agent_execution_attempts(
                        id, work_item_id, attempt_number, worker_id, adapter_revision_id, created_at
                    ) VALUES ('attempt-wrong', 'item-1', 1, 'worker-1', 'adapter-2', ?)
                    """,
                    (CREATED_AT,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO agent_execution_attempts(
                        id, work_item_id, attempt_number, worker_id, adapter_revision_id, created_at
                    ) VALUES ('attempt-2', 'item-1', 2, 'worker-1', 'adapter-1', ?)
                    """,
                    (CREATED_AT,),
                )

    def test_terminal_work_item_rejects_a_later_event(self) -> None:
        with self.database.connection() as connection:
            self._insert_execution_graph(connection)
            connection.execute(
                """
                INSERT INTO agent_work_item_events(
                    id, work_item_id, execution_attempt_id, event_type, created_at, reason_code
                ) VALUES ('event-queued', 'item-1', NULL, 'QUEUED', ?, 'TEST_QUEUED')
                """,
                (CREATED_AT,),
            )
            connection.execute(
                """
                INSERT INTO agent_work_item_events(
                    id, work_item_id, execution_attempt_id, event_type, created_at, reason_code
                ) VALUES ('event-1', 'item-1', NULL, 'BLOCKED', ?, 'TEST_BLOCKED')
                """,
                ("2026-08-27T12:00:30.000000+00:00",),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO agent_work_item_events(
                        id, work_item_id, execution_attempt_id, event_type, created_at, reason_code
                    ) VALUES ('event-2', 'item-1', NULL, 'QUEUED', ?, 'TEST_QUEUED')
                    """,
                    ("2026-08-27T12:01:00.000000+00:00",),
                )


if __name__ == "__main__":
    unittest.main()
