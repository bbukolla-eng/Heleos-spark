"""Tests that catch a CLI bootstrap that leaves a local database unusable."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.cli import main
from helios_takeoff_core.db import Database


class CliTests(unittest.TestCase):
    """The production change caught is a CLI init command that does not apply the canonical migrations."""

    def test_init_creates_a_ready_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p0.sqlite3"

            exit_code = main(["init", "--database", str(path)])

            self.assertEqual(exit_code, 0)
            with Database(path).connection() as connection:
                self.assertEqual(connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 17)


if __name__ == "__main__":
    unittest.main()
