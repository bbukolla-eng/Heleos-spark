"""Tests that catch mutation of issued bid documents or frozen baselines."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database
from helios_takeoff_core.errors import ImmutableStateError, ValidationError
from helios_takeoff_core.repository import TakeoffRepository


DOCUMENT_HASH = "a" * 64


class DocumentRevisionTests(unittest.TestCase):
    """The production changes caught are allowing mutable issued source data or a frozen-set write."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "p0.sqlite3")
        self.database.initialize()
        self.repository = TakeoffRepository(self.database)
        self.project_id = self.repository.create_project(code="P-100", name="Clinic Renovation")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_frozen_revision_set_rejects_new_document_membership(self) -> None:
        drawing_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-101",
            title="Mechanical Plan",
            sha256=DOCUMENT_HASH,
            issue_date="2026-08-27",
        )
        revision_set_id = self.repository.create_revision_set(
            project_id=self.project_id,
            name="BID-01",
        )
        self.repository.include_document_revision(revision_set_id, drawing_id)
        self.repository.freeze_revision_set(revision_set_id)

        with self.assertRaises(ImmutableStateError):
            self.repository.include_document_revision(revision_set_id, drawing_id)

    def test_document_revision_rejects_invalid_hash(self) -> None:
        with self.assertRaises(ValidationError):
            self.repository.register_document_revision(
                project_id=self.project_id,
                document_type="DRAWING",
                document_number="M-101",
                title="Mechanical Plan",
                sha256="not-a-sha256",
                issue_date="2026-08-27",
            )

    def test_sql_update_cannot_mutate_an_issued_document_revision(self) -> None:
        drawing_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-101",
            title="Mechanical Plan",
            sha256=DOCUMENT_HASH,
            issue_date="2026-08-27",
        )

        with self.database.connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE document_revisions SET title = ? WHERE id = ?",
                    ("Changed in place", drawing_id),
                )


if __name__ == "__main__":
    unittest.main()
