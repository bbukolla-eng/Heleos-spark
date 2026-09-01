"""Tests that the local P0 package carries its migrations when installed."""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path


class PackageMetadataTests(unittest.TestCase):
    """A plug-and-play install must not depend on an editable source checkout."""

    def test_pyproject_declares_package_discovery_migration_data_and_cli(self) -> None:
        project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertIn("setuptools", " ".join(project["build-system"]["requires"]))
        self.assertEqual(project["build-system"]["build-backend"], "setuptools.build_meta")
        self.assertEqual(project["tool"]["setuptools"]["package-dir"][""], "src")
        self.assertIn("migrations/*.sql", project["tool"]["setuptools"]["package-data"]["helios_takeoff_core"])
        self.assertIn(
            "engine/v2/migrations/*.sql",
            project["tool"]["setuptools"]["package-data"]["helios_takeoff_core"],
        )
        self.assertIn(
            "engine/v2/schemas/*.json",
            project["tool"]["setuptools"]["package-data"]["helios_takeoff_core"],
        )
        observation_schema = (
            Path(__file__).resolve().parents[1]
            / "src" / "helios_takeoff_core" / "engine" / "v2" / "schemas"
            / "observation-bundle-v1.schema.json"
        )
        self.assertTrue(observation_schema.is_file())
        self.assertEqual(project["project"]["scripts"]["helios-p0"], "helios_takeoff_core.cli:main")
        self.assertEqual(project["project"]["scripts"]["helios-p1a"], "helios_takeoff_core.p1a_cli:main")
        self.assertEqual(project["project"]["scripts"]["helios-engine"], "helios_takeoff_core.engine_cli:main")
        self.assertEqual(project["tool"]["helios"]["database_schema_version"], "17")


if __name__ == "__main__":
    unittest.main()
