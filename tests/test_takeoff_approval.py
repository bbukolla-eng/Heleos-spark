"""Tests that catch mutable takeoff snapshots or unauthorized approvals."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database
from helios_takeoff_core.errors import PreconditionError
from helios_takeoff_core.repository import TakeoffRepository
from helios_takeoff_core.services import TakeoffService


class TakeoffApprovalTests(unittest.TestCase):
    """The production changes caught are making a takeoff from unapproved facts or editing an approved snapshot."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "p0.sqlite3")
        self.database.initialize()
        self.repository = TakeoffRepository(self.database)
        self.service = TakeoffService(self.repository)
        self.project_id = self.repository.create_project(code="P-400", name="Data Center")
        self.actor_id = self.repository.create_actor(display_name="Estimator", actor_type="USER")
        self.repository.grant_role(
            actor_id=self.actor_id,
            project_id=self.project_id,
            role="ESTIMATOR_REVIEWER",
        )
        self.document_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-401",
            title="Supply Air Plan",
            sha256="a" * 64,
            issue_date="2026-08-27",
        )
        self.revision_set_id = self.repository.create_revision_set(project_id=self.project_id, name="BID-01")
        self.repository.include_document_revision(self.revision_set_id, self.document_id)
        self.repository.freeze_revision_set(self.revision_set_id)
        self.evidence_id = self.repository.record_evidence_item(
            document_revision_id=self.document_id,
            sheet_id="M-401",
            page_number=1,
            geometry={"bbox": [1.0, 2.0, 3.0, 4.0]},
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _approved_assertion(self) -> str:
        assertion_id = self.service.create_quantity_assertion(
            revision_set_id=self.revision_set_id,
            subject_kind="DUCT_SEGMENT",
            subject_key="M-401:SA-01",
            uom="LF",
            quantity="42.50",
            scope_state="NEW",
            evidence_allocations=[{"evidence_item_id": self.evidence_id, "allocation_quantity": "42.50"}],
        )
        self.service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=self.actor_id,
            outcome="VERIFIED",
            rationale="Evidence reviewed.",
        )
        self.service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=self.actor_id,
            outcome="APPROVED",
            rationale="Quantity approved.",
        )
        return assertion_id

    def test_takeoff_snapshots_approved_quantities_and_rejects_sql_mutation(self) -> None:
        self._approved_assertion()

        takeoff_id = self.service.create_takeoff_version(revision_set_id=self.revision_set_id)
        self.service.approve_takeoff_version(
            takeoff_version_id=takeoff_id,
            actor_id=self.actor_id,
            rationale="Bid baseline reviewed.",
        )

        takeoff = self.repository.get_takeoff_version(takeoff_id)
        self.assertEqual(takeoff["status"], "APPROVED")
        self.assertEqual(takeoff["lines"][0]["quantity"], "42.50")
        with self.database.connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE takeoff_lines SET quantity = '0' WHERE takeoff_version_id = ?",
                    (takeoff_id,),
                )

    def test_takeoff_rejects_candidate_assertions(self) -> None:
        self.service.create_quantity_assertion(
            revision_set_id=self.revision_set_id,
            subject_kind="DUCT_SEGMENT",
            subject_key="M-401:SA-02",
            uom="LF",
            quantity="10",
            scope_state="NEW",
            evidence_allocations=[{"evidence_item_id": self.evidence_id, "allocation_quantity": "10"}],
        )

        with self.assertRaises(PreconditionError):
            self.service.create_takeoff_version(revision_set_id=self.revision_set_id)

    def test_direct_inserts_cannot_change_reviewed_quantity_or_approved_takeoff(self) -> None:
        assertion_id = self._approved_assertion()
        takeoff_id = self.service.create_takeoff_version(revision_set_id=self.revision_set_id)
        self.service.approve_takeoff_version(
            takeoff_version_id=takeoff_id,
            actor_id=self.actor_id,
            rationale="Approved before direct insert attempts.",
        )
        later_evidence_id = self.repository.record_evidence_item(
            document_revision_id=self.document_id,
            sheet_id="M-401",
            page_number=1,
            geometry={"bbox": [10.0, 20.0, 30.0, 40.0]},
        )
        later_assertion_id = self.service.create_quantity_assertion(
            revision_set_id=self.revision_set_id,
            subject_kind="DUCT_SEGMENT",
            subject_key="M-401:SA-03",
            uom="LF",
            quantity="11",
            scope_state="NEW",
            evidence_allocations=[{"evidence_item_id": later_evidence_id, "allocation_quantity": "11"}],
        )
        self.service.record_quantity_review(
            assertion_id=later_assertion_id,
            actor_id=self.actor_id,
            outcome="VERIFIED",
            rationale="Later candidate verified.",
        )
        self.service.record_quantity_review(
            assertion_id=later_assertion_id,
            actor_id=self.actor_id,
            outcome="APPROVED",
            rationale="Later candidate approved for a later snapshot.",
        )

        with self.database.connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO quantity_evidence_allocations(
                        quantity_assertion_id, evidence_item_id, extraction_claim_id, allocation_quantity
                    ) VALUES (?, ?, NULL, '42.50')
                    """,
                    (assertion_id, later_evidence_id),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO takeoff_lines(
                        id, takeoff_version_id, source_quantity_assertion_id, subject_kind,
                        subject_key, uom, quantity, scope_state, created_at
                    ) VALUES (?, ?, ?, 'DUCT_SEGMENT', 'M-401:SA-03', 'LF', '11', 'NEW', '2026-08-27T00:00:00+00:00')
                    """,
                    (str(uuid.uuid4()), takeoff_id, later_assertion_id),
                )

    def test_direct_takeoff_approval_requires_matching_active_reviewer_grant(self) -> None:
        self._approved_assertion()
        takeoff_id = self.service.create_takeoff_version(revision_set_id=self.revision_set_id)
        submitter_id = self.repository.create_actor(display_name="Submitter", actor_type="USER")
        submitter_grant_id = self.repository.grant_role(
            actor_id=submitter_id,
            project_id=self.project_id,
            role="SUBMITTER",
        )

        with self.database.connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO takeoff_approval_events(
                        id, takeoff_version_id, actor_id, role_grant_id, rationale, created_at
                    ) VALUES (?, ?, ?, ?, 'Forged role link', '2026-08-27T00:00:00+00:00')
                    """,
                    (str(uuid.uuid4()), takeoff_id, submitter_id, submitter_grant_id),
                )

    def test_direct_snapshot_cannot_use_an_approved_assertion_without_evidence(self) -> None:
        assertion_id = str(uuid.uuid4())
        takeoff_id = str(uuid.uuid4())
        with self.database.connection() as connection:
            reviewer_grant_id = connection.execute(
                "SELECT id FROM role_grants WHERE actor_id = ? AND role = 'ESTIMATOR_REVIEWER'",
                (self.actor_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO quantity_assertions(
                    id, revision_set_id, subject_kind, subject_key, uom, quantity, scope_state, created_at
                ) VALUES (?, ?, 'DUCT_SEGMENT', 'M-401:UNSUPPORTED', 'LF', '9', 'NEW', '2099-01-01T00:00:00+00:00')
                """,
                (assertion_id, self.revision_set_id),
            )
            for outcome in ("VERIFIED", "APPROVED"):
                connection.execute(
                    """
                    INSERT INTO quantity_review_events(
                        id, quantity_assertion_id, actor_id, role_grant_id, outcome, rationale, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'Forged direct review', '2099-01-01T00:00:00+00:00')
                    """,
                    (str(uuid.uuid4()), assertion_id, self.actor_id, reviewer_grant_id, outcome),
                )
            connection.execute(
                """
                INSERT INTO takeoff_versions(id, project_id, revision_set_id, created_at)
                VALUES (?, ?, ?, '2099-01-01T00:00:00+00:00')
                """,
                (takeoff_id, self.project_id, self.revision_set_id),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO takeoff_lines(
                        id, takeoff_version_id, source_quantity_assertion_id, subject_kind,
                        subject_key, uom, quantity, scope_state, created_at
                    ) VALUES (?, ?, ?, 'DUCT_SEGMENT', 'M-401:UNSUPPORTED', 'LF', '9', 'NEW', '2099-01-01T00:00:00+00:00')
                    """,
                    (str(uuid.uuid4()), takeoff_id, assertion_id),
                )

    def test_direct_takeoff_version_requires_its_revision_set_project(self) -> None:
        unrelated_project_id = self.repository.create_project(code="P-401", name="Unrelated Project")
        with self.database.connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO takeoff_versions(id, project_id, revision_set_id, created_at)
                    VALUES (?, ?, ?, '2026-08-27T00:00:00+00:00')
                    """,
                    (str(uuid.uuid4()), unrelated_project_id, self.revision_set_id),
                )


if __name__ == "__main__":
    unittest.main()
