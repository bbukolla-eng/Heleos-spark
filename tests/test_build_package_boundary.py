"""The repository Build Fabric must never ship inside the runtime wheel."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path


class BuildPackageBoundaryTests(unittest.TestCase):
    def test_clean_wheel_excludes_build_fabric_and_preserves_runtime_entries(self) -> None:
        """Including repository tools/state or changing runtime scripts fails the gate."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    ".",
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    temporary,
                ],
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=300,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            wheels = list(Path(temporary).glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(wheels[0]) as archive:
                names = tuple(archive.namelist())
                self.assertFalse(any(name.startswith("tools/") for name in names))
                self.assertFalse(any(name.startswith("build_control/") for name in names))
                self.assertFalse(any("browser" in name.lower() for name in names))
                self.assertFalse(any("provider" in name.lower() for name in names))
                entry_points = [
                    name for name in names if name.endswith(".dist-info/entry_points.txt")
                ]
                self.assertEqual(len(entry_points), 1)
                entries = archive.read(entry_points[0]).decode("utf-8")

        project = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            project["project"]["scripts"],
            {
                "helios-p0": "helios_takeoff_core.cli:main",
                "helios-p1a": "helios_takeoff_core.p1a_cli:main",
                "helios-engine": "helios_takeoff_core.engine_cli:main",
            },
        )
        self.assertNotIn("helios-build", entries)
        self.assertIn("helios-p0 = helios_takeoff_core.cli:main", entries)
        self.assertIn("helios-p1a = helios_takeoff_core.p1a_cli:main", entries)
        self.assertIn("helios-engine = helios_takeoff_core.engine_cli:main", entries)


if __name__ == "__main__":
    unittest.main()
