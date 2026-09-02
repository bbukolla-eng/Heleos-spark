"""Acceptance test for the entire P0 evidence-to-as-bid workflow."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.demo import build_demo_release


class P0EndToEndTests(unittest.TestCase):
    """A shipped demo must exercise the governed workflow, not a mock path."""

    def test_demo_creates_a_released_bid_with_traceable_takeoff_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "helios-demo.sqlite3"

            result = build_demo_release(database_path)

            self.assertEqual(result["bid_release"]["status"], "RELEASED")
            self.assertEqual(result["takeoff_version"]["status"], "APPROVED")
            self.assertEqual(len(result["takeoff_version"]["lines"]), 2)
            self.assertEqual(len(result["bid_release"]["estimate_lines"]), 2)
            self.assertEqual(result["bid_release"]["estimate_lines"][0]["extended_price"], "3696.00")
            self.assertTrue(database_path.is_file())

            with self.assertRaises(FileExistsError):
                build_demo_release(database_path)


if __name__ == "__main__":
    unittest.main()
