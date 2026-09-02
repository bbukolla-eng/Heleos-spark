"""Tests that catch a missing or unsafe database bootstrap."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database
from helios_takeoff_core.agentic.artifacts import ArtifactRecord
from helios_takeoff_core.agentic.repository import AgentRepository
from helios_takeoff_core.repository import TakeoffRepository


class DatabaseBootstrapTests(unittest.TestCase):
    """The production change that should fail these tests is a missing migration or FK pragma."""

    def test_initialize_applies_versioned_schema_and_enables_foreign_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "p0.sqlite3"
            database = Database(database_path)

            database.initialize()

            with database.connection() as connection:
                version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
                foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
                projects_table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'projects'"
                ).fetchone()

            self.assertEqual(version, 17)
            self.assertEqual(foreign_keys, 1)
            self.assertEqual(projects_table[0], "projects")

    def test_migration_runner_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "p0.sqlite3")

            database.initialize()
            database.initialize()

            with database.connection() as connection:
                applied_migrations = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]

            self.assertEqual(applied_migrations, 17)

    def test_failed_migration_rolls_back_schema_and_ledger_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            migration_directory = root / "migrations"
            migration_directory.mkdir()
            (migration_directory / "001_broken.sql").write_text(
                "CREATE TABLE partially_applied(id TEXT PRIMARY KEY);\n"
                "INSERT INTO missing_table(id) VALUES ('failure');\n",
                encoding="utf-8",
            )
            database_path = root / "p0.sqlite3"
            database = Database(database_path)
            database._migration_directory = lambda: migration_directory  # type: ignore[method-assign]

            with self.assertRaises(sqlite3.OperationalError):
                database.initialize()

            with sqlite3.connect(database_path) as connection:
                leaked_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'partially_applied'"
                ).fetchone()
                applied_version = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 1"
                ).fetchone()

            self.assertIsNone(leaked_table)
            self.assertIsNone(applied_version)

    def test_project_and_actor_identity_records_reject_direct_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "p0.sqlite3")
            database.initialize()
            repository = TakeoffRepository(database)
            project_id = repository.create_project(code="P-IMMUTABLE", name="Immutable Project")
            actor_id = repository.create_actor(display_name="Immutable Actor", actor_type="USER")

            with database.connection() as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE projects SET name = 'Changed' WHERE id = ?", (project_id,))
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE actors SET display_name = 'Changed' WHERE id = ?", (actor_id,))

    def test_migration_016_preserves_prior_artifacts_and_scopes_dedup_to_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "p1a-upgrade.sqlite3")
            connection = database._new_connection()
            try:
                connection.execute(
                    """
                    CREATE TABLE schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                for migration in sorted(database._migration_directory().glob("[0-9][0-9][0-9]_*.sql")):
                    version = int(migration.name.split("_", 1)[0])
                    if version <= 15:
                        database._apply_migration(connection, migration, version)
            finally:
                connection.close()
            repository = TakeoffRepository(database)
            agent_repository = AgentRepository(database)

            def frozen_baseline(code: str) -> tuple[str, str]:
                project_id = repository.create_project(code=code, name=code)
                document_id = repository.register_document_revision(
                    project_id=project_id,
                    document_type="DRAWING",
                    document_number="M-1",
                    title="Mechanical",
                    sha256="a" * 64,
                    issue_date="2026-08-27",
                )
                revision_set_id = repository.create_revision_set(project_id=project_id, name="BID")
                repository.include_document_revision(revision_set_id, document_id)
                repository.freeze_revision_set(revision_set_id)
                return project_id, revision_set_id

            first_project_id, first_revision_set_id = frozen_baseline("FIRST")
            second_project_id, second_revision_set_id = frozen_baseline("SECOND")
            record = ArtifactRecord(
                sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                byte_size=0,
                media_type="application/vnd.helios.worker-stderr",
                schema_version="helios.p1a.worker-stderr/v1",
                store_key="sha256/e3/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            )
            first_artifact_id = agent_repository.record_artifact(
                project_id=first_project_id,
                revision_set_id=first_revision_set_id,
                record=record,
            )
            database.initialize()
            second_artifact_id = agent_repository.record_artifact(
                project_id=second_project_id,
                revision_set_id=second_revision_set_id,
                record=record,
            )

            self.assertNotEqual(first_artifact_id, second_artifact_id)
            with database.connection() as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                rows = connection.execute(
                    "SELECT project_id, revision_set_id FROM agent_artifacts ORDER BY rowid"
                ).fetchall()
            self.assertEqual(
                [(row["project_id"], row["revision_set_id"]) for row in rows],
                [
                    (first_project_id, first_revision_set_id),
                    (second_project_id, second_revision_set_id),
                ],
            )


if __name__ == "__main__":
    unittest.main()
