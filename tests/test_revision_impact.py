"""Tests that catch addenda or RFIs mutating an approved baseline in place."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database
from helios_takeoff_core.errors import PreconditionError
from helios_takeoff_core.repository import TakeoffRepository
from helios_takeoff_core.services import TakeoffService


class RevisionImpactTests(unittest.TestCase):
    """The production changes caught are silently clearing a conflict or altering an approved takeoff after an addendum."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "p0.sqlite3")
        self.database.initialize()
        self.repository = TakeoffRepository(self.database)
        self.service = TakeoffService(self.repository)
        self.project_id = self.repository.create_project(code="P-500", name="School Modernization")
        self.actor_id = self.repository.create_actor(display_name="Estimator", actor_type="USER")
        self.repository.grant_role(
            actor_id=self.actor_id,
            project_id=self.project_id,
            role="ESTIMATOR_REVIEWER",
        )
        self.drawing_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-501",
            title="Mechanical Plan",
            sha256="a" * 64,
            issue_date="2026-08-27",
        )
        self.revision_set_id = self.repository.create_revision_set(project_id=self.project_id, name="BID-01")
        self.repository.include_document_revision(self.revision_set_id, self.drawing_id)
        self.repository.freeze_revision_set(self.revision_set_id)
        self.evidence_id = self.repository.record_evidence_item(
            document_revision_id=self.drawing_id,
            sheet_id="M-501",
            page_number=1,
            geometry={"bbox": [1.0, 2.0, 3.0, 4.0]},
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _approved_takeoff(self) -> str:
        assertion_id = self.service.create_quantity_assertion(
            revision_set_id=self.revision_set_id,
            subject_kind="EQUIPMENT",
            subject_key="M-501:AHU-1",
            uom="EA",
            quantity="1",
            scope_state="NEW",
            evidence_allocations=[{"evidence_item_id": self.evidence_id, "allocation_quantity": "1"}],
        )
        self.service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=self.actor_id,
            outcome="VERIFIED",
            rationale="Tag visible.",
        )
        self.service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=self.actor_id,
            outcome="APPROVED",
            rationale="Schedule reconciled.",
        )
        takeoff_id = self.service.create_takeoff_version(revision_set_id=self.revision_set_id)
        self.service.approve_takeoff_version(
            takeoff_version_id=takeoff_id,
            actor_id=self.actor_id,
            rationale="Takeoff approved.",
        )
        return takeoff_id

    def test_addendum_creates_successor_baseline_without_altering_approved_takeoff(self) -> None:
        takeoff_id = self._approved_takeoff()
        addendum_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="ADDENDUM",
            document_number="ADD-01",
            title="Addendum 01",
            sha256="b" * 64,
            issue_date="2026-08-28",
        )

        successor_id = self.service.create_successor_revision_set(
            predecessor_revision_set_id=self.revision_set_id,
            triggering_document_revision_id=addendum_id,
        )

        original = self.repository.get_takeoff_version(takeoff_id)
        successor = self.repository.get_revision_set(successor_id)
        self.assertEqual(original["status"], "APPROVED")
        self.assertEqual(original["lines"][0]["quantity"], "1")
        self.assertEqual(successor["status"], "OPEN")
        self.assertEqual(self.repository.get_revision_set_impact(successor_id)["triggering_document_revision_id"], addendum_id)

    def test_open_conflict_blocks_takeoff_until_a_formal_rfi_response_is_dispositioned(self) -> None:
        assertion_id = self.service.create_quantity_assertion(
            revision_set_id=self.revision_set_id,
            subject_kind="EQUIPMENT",
            subject_key="M-501:AHU-2",
            uom="EA",
            quantity="1",
            scope_state="NEW",
            evidence_allocations=[{"evidence_item_id": self.evidence_id, "allocation_quantity": "1"}],
        )
        self.service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=self.actor_id,
            outcome="VERIFIED",
            rationale="Tag visible.",
        )
        self.service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=self.actor_id,
            outcome="APPROVED",
            rationale="Quantity approved.",
        )
        conflict_id = self.service.create_conflict(
            revision_set_id=self.revision_set_id,
            conflict_type="PLAN_SCHEDULE_MISMATCH",
            severity="HIGH",
            description="Plan shows AHU-2 but schedule has conflicting capacity.",
        )
        with self.assertRaises(PreconditionError):
            self.service.create_takeoff_version(revision_set_id=self.revision_set_id)

        rfi_id = self.service.create_rfi(
            revision_set_id=self.revision_set_id,
            question="Confirm AHU-2 scheduled capacity.",
        )
        response_document_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="RFI_RESPONSE",
            document_number="RFI-01-RESP",
            title="RFI 01 Response",
            sha256="c" * 64,
            issue_date="2026-08-29",
        )
        response_id = self.service.record_rfi_response(
            rfi_id=rfi_id,
            response_document_revision_id=response_document_id,
            response_text="Use 20-ton capacity.",
        )
        self.service.disposition_conflict(
            conflict_id=conflict_id,
            actor_id=self.actor_id,
            rfi_response_id=response_id,
            rationale="Formal response selected; estimate will use 20 tons.",
        )

        takeoff_id = self.service.create_takeoff_version(revision_set_id=self.revision_set_id)
        self.assertEqual(self.repository.get_takeoff_version(takeoff_id)["lines"][0]["subject_key"], "M-501:AHU-2")


if __name__ == "__main__":
    unittest.main()
