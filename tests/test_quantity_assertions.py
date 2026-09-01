"""Tests that catch unverifiable, mutable, or unauthorized quantity changes."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database
from helios_takeoff_core.errors import AuthorizationError, ImmutableStateError, PreconditionError, ValidationError
from helios_takeoff_core.repository import TakeoffRepository
from helios_takeoff_core.services import TakeoffService


HASH_A = "a" * 64


class QuantityAssertionTests(unittest.TestCase):
    """The production changes caught are unproven quantities, invalid decimal inputs, and invalid state transitions."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "p0.sqlite3")
        self.database.initialize()
        self.repository = TakeoffRepository(self.database)
        self.service = TakeoffService(self.repository)
        self.project_id = self.repository.create_project(code="P-300", name="Hospital Fitout")
        self.actor_id = self.repository.create_actor(display_name="Estimator", actor_type="USER")
        self.repository.grant_role(
            actor_id=self.actor_id,
            project_id=self.project_id,
            role="ESTIMATOR_REVIEWER",
        )
        self.document_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-301",
            title="Supply Air Plan",
            sha256=HASH_A,
            issue_date="2026-08-27",
        )
        self.revision_set_id = self.repository.create_revision_set(project_id=self.project_id, name="BID-01")
        self.repository.include_document_revision(self.revision_set_id, self.document_id)
        self.repository.freeze_revision_set(self.revision_set_id)
        self.evidence_id = self.repository.record_evidence_item(
            document_revision_id=self.document_id,
            sheet_id="M-301",
            page_number=1,
            geometry={"bbox": [1.0, 2.0, 3.0, 4.0]},
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_quantity_assertion_progresses_without_mutating_candidate(self) -> None:
        assertion_id = self.service.create_quantity_assertion(
            revision_set_id=self.revision_set_id,
            subject_kind="DUCT_SEGMENT",
            subject_key="M-301:SA-01",
            uom="LF",
            quantity="42.50",
            scope_state="NEW",
            evidence_allocations=[{"evidence_item_id": self.evidence_id, "allocation_quantity": "42.50"}],
        )

        self.service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=self.actor_id,
            outcome="VERIFIED",
            rationale="Measured centerline from evidence geometry.",
        )
        self.service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=self.actor_id,
            outcome="APPROVED",
            rationale="Schedule and plan reconciliation complete.",
        )

        assertion = self.repository.get_quantity_assertion(assertion_id)
        self.assertEqual(assertion["quantity"], "42.50")
        self.assertEqual(assertion["state"], "APPROVED")
        with self.assertRaises(ImmutableStateError):
            self.service.record_quantity_review(
                assertion_id=assertion_id,
                actor_id=self.actor_id,
                outcome="VERIFIED",
                rationale="Attempt to reopen a terminal assertion.",
            )

    def test_quantity_rejects_float_and_evidence_outside_frozen_baseline(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.create_quantity_assertion(
                revision_set_id=self.revision_set_id,
                subject_kind="DUCT_SEGMENT",
                subject_key="M-301:SA-02",
                uom="LF",
                quantity=42.5,
                scope_state="NEW",
                evidence_allocations=[{"evidence_item_id": self.evidence_id, "allocation_quantity": "42.50"}],
            )

        unlisted_document_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-302",
            title="Unlisted Detail",
            sha256="b" * 64,
            issue_date="2026-08-27",
        )
        unlisted_evidence_id = self.repository.record_evidence_item(
            document_revision_id=unlisted_document_id,
            sheet_id="M-302",
            page_number=1,
            geometry={"bbox": [1.0, 2.0, 3.0, 4.0]},
        )
        with self.assertRaises(PreconditionError):
            self.service.create_quantity_assertion(
                revision_set_id=self.revision_set_id,
                subject_kind="DUCT_SEGMENT",
                subject_key="M-302:SA-01",
                uom="LF",
                quantity="10",
                scope_state="NEW",
                evidence_allocations=[{"evidence_item_id": unlisted_evidence_id, "allocation_quantity": "10"}],
            )

    def test_only_a_recorded_role_grant_can_review_quantity(self) -> None:
        assertion_id = self.service.create_quantity_assertion(
            revision_set_id=self.revision_set_id,
            subject_kind="DUCT_SEGMENT",
            subject_key="M-301:SA-03",
            uom="LF",
            quantity="10",
            scope_state="NEW",
            evidence_allocations=[{"evidence_item_id": self.evidence_id, "allocation_quantity": "10"}],
        )
        ungranted_actor_id = self.repository.create_actor(display_name="Viewer", actor_type="USER")

        with self.assertRaises(AuthorizationError):
            self.service.record_quantity_review(
                assertion_id=assertion_id,
                actor_id=ungranted_actor_id,
                outcome="VERIFIED",
                rationale="No role grant exists.",
            )


if __name__ == "__main__":
    unittest.main()
