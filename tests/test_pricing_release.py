"""Tests that catch pricing without a valid quote trail or unsafe bid release."""

from __future__ import annotations

import sys
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database
from helios_takeoff_core.errors import PreconditionError
from helios_takeoff_core.repository import TakeoffRepository
from helios_takeoff_core.services import TakeoffService


class PricingAndReleaseTests(unittest.TestCase):
    """The production changes caught are release of an unapproved estimate or use of expired quote input."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "p0.sqlite3")
        self.database.initialize()
        self.repository = TakeoffRepository(self.database)
        self.service = TakeoffService(self.repository)
        self.project_id = self.repository.create_project(code="P-600", name="Medical Office")
        self.estimator_id = self.repository.create_actor(display_name="Estimator", actor_type="USER")
        self.authorized_bidder_id = self.repository.create_actor(display_name="Bidder", actor_type="USER")
        self.submitter_id = self.repository.create_actor(display_name="Submitter", actor_type="USER")
        for actor_id, role in (
            (self.estimator_id, "ESTIMATOR_REVIEWER"),
            (self.authorized_bidder_id, "AUTHORIZED_BIDDER"),
            (self.submitter_id, "SUBMITTER"),
        ):
            self.repository.grant_role(actor_id=actor_id, project_id=self.project_id, role=role)
        self.drawing_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-601",
            title="Equipment Plan",
            sha256="a" * 64,
            issue_date="2026-08-27",
        )
        self.revision_set_id = self.repository.create_revision_set(project_id=self.project_id, name="BID-01")
        self.repository.include_document_revision(self.revision_set_id, self.drawing_id)
        self.repository.freeze_revision_set(self.revision_set_id)
        evidence_id = self.repository.record_evidence_item(
            document_revision_id=self.drawing_id,
            sheet_id="M-601",
            page_number=1,
            geometry={"bbox": [1.0, 2.0, 3.0, 4.0]},
        )
        assertion_id = self.service.create_quantity_assertion(
            revision_set_id=self.revision_set_id,
            subject_kind="EQUIPMENT",
            subject_key="M-601:EF-1",
            uom="EA",
            quantity="1",
            scope_state="NEW",
            evidence_allocations=[{"evidence_item_id": evidence_id, "allocation_quantity": "1"}],
        )
        self.service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=self.estimator_id,
            outcome="VERIFIED",
            rationale="Fan tag visible.",
        )
        self.service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=self.estimator_id,
            outcome="APPROVED",
            rationale="Quantity approved.",
        )
        self.takeoff_id = self.service.create_takeoff_version(revision_set_id=self.revision_set_id)
        self.service.approve_takeoff_version(
            takeoff_version_id=self.takeoff_id,
            actor_id=self.estimator_id,
            rationale="Takeoff baseline approved.",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _approved_estimate(self, valid_through: str) -> str:
        quote_document_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="OTHER",
            document_number=f"QUOTE-{valid_through}",
            title="Supplier Quote",
            sha256=("b" if valid_through > "2026-08-27" else "c") * 64,
            issue_date="2026-08-27",
        )
        supplier_id = self.service.create_supplier(project_id=self.project_id, name="Verified Supply")
        quote_id = self.service.create_quote_revision(
            project_id=self.project_id,
            supplier_id=supplier_id,
            source_document_revision_id=quote_document_id,
            quote_number="QS-100",
            issued_at="2026-08-27" if valid_through > "2026-08-27" else "1999-12-01",
            valid_through=valid_through,
            currency="USD",
            terms="Delivered to jobsite; freight included.",
        )
        quote_line_id = self.service.add_quote_line(
            quote_revision_id=quote_id,
            supplier_part_number="EF-1",
            description="Exhaust fan",
            uom="EA",
            unit_price="1500.00",
        )
        takeoff_line_id = self.repository.get_takeoff_version(self.takeoff_id)["lines"][0]["id"]
        estimate_id = self.service.create_estimate_version(
            takeoff_version_id=self.takeoff_id,
            line_inputs=[{"takeoff_line_id": takeoff_line_id, "quote_line_id": quote_line_id}],
        )
        self.service.approve_estimate_version(
            estimate_version_id=estimate_id,
            actor_id=self.estimator_id,
            rationale="Quote and quantity reconciliation complete.",
        )
        return estimate_id

    def test_released_bid_has_approved_takeoff_estimate_and_dual_role_decisions(self) -> None:
        estimate_id = self._approved_estimate(valid_through="2099-01-01")

        release_id = self.service.release_bid(
            takeoff_version_id=self.takeoff_id,
            estimate_version_id=estimate_id,
            authorized_bidder_actor_id=self.authorized_bidder_id,
            submitter_actor_id=self.submitter_id,
            rationale="Bid package is ready for submission.",
        )

        release = self.repository.get_bid_release(release_id)
        self.assertEqual(release["status"], "RELEASED")
        self.assertEqual(release["estimate_lines"][0]["extended_price"], "1500.00")
        self.assertEqual({event["role"] for event in release["approval_events"]}, {"AUTHORIZED_BIDDER", "SUBMITTER"})

    def test_expired_quote_blocks_as_bid_release(self) -> None:
        estimate_id = self._approved_estimate(valid_through="2000-01-01")

        with self.assertRaises(PreconditionError):
            self.service.release_bid(
                takeoff_version_id=self.takeoff_id,
                estimate_version_id=estimate_id,
                authorized_bidder_actor_id=self.authorized_bidder_id,
                submitter_actor_id=self.submitter_id,
                rationale="Attempt to release with expired material price.",
            )

    def test_direct_insert_cannot_change_an_approved_or_released_estimate(self) -> None:
        estimate_id = self._approved_estimate(valid_through="2099-01-01")
        self.service.release_bid(
            takeoff_version_id=self.takeoff_id,
            estimate_version_id=estimate_id,
            authorized_bidder_actor_id=self.authorized_bidder_id,
            submitter_actor_id=self.submitter_id,
            rationale="Release before direct estimate mutation attempt.",
        )
        later_evidence_id = self.repository.record_evidence_item(
            document_revision_id=self.drawing_id,
            sheet_id="M-601",
            page_number=1,
            geometry={"bbox": [11.0, 12.0, 13.0, 14.0]},
        )
        later_assertion_id = self.service.create_quantity_assertion(
            revision_set_id=self.revision_set_id,
            subject_kind="EQUIPMENT",
            subject_key="M-601:EF-2",
            uom="EA",
            quantity="1",
            scope_state="NEW",
            evidence_allocations=[{"evidence_item_id": later_evidence_id, "allocation_quantity": "1"}],
        )
        for outcome in ("VERIFIED", "APPROVED"):
            self.service.record_quantity_review(
                assertion_id=later_assertion_id,
                actor_id=self.estimator_id,
                outcome=outcome,
                rationale=f"EF-2 {outcome.lower()} for a later takeoff.",
            )
        later_takeoff_id = self.service.create_takeoff_version(revision_set_id=self.revision_set_id)
        later_line_id = next(
            line["id"]
            for line in self.repository.get_takeoff_version(later_takeoff_id)["lines"]
            if line["subject_key"] == "M-601:EF-2"
        )
        with self.database.connection() as connection:
            quote_line_id = connection.execute(
                "SELECT quote_line_id FROM estimate_lines WHERE estimate_version_id = ?",
                (estimate_id,),
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO estimate_lines(
                        id, estimate_version_id, takeoff_line_id, quote_line_id,
                        uom, quantity, unit_price, extended_price, created_at
                    ) VALUES (?, ?, ?, ?, 'EA', '1', '1500.00', '1500.00', '2026-08-27T00:00:00+00:00')
                    """,
                    (str(uuid.uuid4()), estimate_id, later_line_id, quote_line_id),
                )

    def test_direct_estimate_approval_requires_all_takeoff_lines_and_release_rechecks_quote_expiry(self) -> None:
        incomplete_estimate_id = str(uuid.uuid4())
        expired_estimate_id = self._approved_estimate(valid_through="2000-01-01")
        with self.database.connection() as connection:
            reviewer_grant_id = connection.execute(
                "SELECT id FROM role_grants WHERE actor_id = ? AND role = 'ESTIMATOR_REVIEWER'",
                (self.estimator_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO estimate_versions(id, project_id, takeoff_version_id, created_at)
                VALUES (?, ?, ?, '2099-01-01T00:00:00+00:00')
                """,
                (incomplete_estimate_id, self.project_id, self.takeoff_id),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO estimate_approval_events(
                        id, estimate_version_id, actor_id, role_grant_id, rationale, created_at
                    ) VALUES (?, ?, ?, ?, 'Forged incomplete estimate approval', '2099-01-01T00:00:00+00:00')
                    """,
                    (str(uuid.uuid4()), incomplete_estimate_id, self.estimator_id, reviewer_grant_id),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO bid_releases(
                        id, project_id, takeoff_version_id, estimate_version_id, created_at
                    ) VALUES (?, ?, ?, ?, '2026-08-27T00:00:00+00:00')
                    """,
                    (str(uuid.uuid4()), self.project_id, self.takeoff_id, expired_estimate_id),
                )


if __name__ == "__main__":
    unittest.main()
