"""Tests that catch evidence without a revision-bound locator or reproducible claim lineage."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database
from helios_takeoff_core.errors import ValidationError
from helios_takeoff_core.repository import TakeoffRepository


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


class EvidenceAndClaimTests(unittest.TestCase):
    """The production changes caught are unlocatable evidence and cross-project claim lineage."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "p0.sqlite3")
        self.database.initialize()
        self.repository = TakeoffRepository(self.database)
        self.project_id = self.repository.create_project(code="P-200", name="School Addition")
        self.document_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-201",
            title="Ductwork Plan",
            sha256=HASH_A,
            issue_date="2026-08-27",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_evidence_and_claim_keep_document_sheet_geometry_and_run_provenance(self) -> None:
        evidence_id = self.repository.record_evidence_item(
            document_revision_id=self.document_id,
            sheet_id="M-201",
            page_number=2,
            geometry={"bbox": [100.0, 200.0, 320.0, 240.0]},
        )
        run_id = self.repository.start_extraction_run(
            project_id=self.project_id,
            plugin_id="duct.extractor",
            plugin_version="1.0.0",
            configuration_sha256=HASH_B,
            input_manifest_sha256=HASH_C,
        )

        claim_id = self.repository.record_extraction_claim(
            extraction_run_id=run_id,
            evidence_item_ids=[evidence_id],
            subject_kind="DUCT_SEGMENT",
            subject_key="M-201:duct:001",
            claim_type="DETECTED",
            payload={"size": "24x12", "material": "G90"},
            confidence="0.94",
        )

        claim = self.repository.get_extraction_claim(claim_id)
        self.assertEqual(claim["plugin_id"], "duct.extractor")
        self.assertEqual(claim["sheet_id"], "M-201")
        self.assertEqual(claim["geometry"]["bbox"], [100.0, 200.0, 320.0, 240.0])
        self.assertEqual(claim["confidence"], "0.94")

    def test_evidence_requires_a_real_locator(self) -> None:
        with self.assertRaises(ValidationError):
            self.repository.record_evidence_item(
                document_revision_id=self.document_id,
                sheet_id="M-201",
                page_number=2,
            )


if __name__ == "__main__":
    unittest.main()
