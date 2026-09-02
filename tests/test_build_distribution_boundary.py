"""Prove research helpers remain repository-only and provider-free."""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ResearchDistributionBoundaryTests(unittest.TestCase):
    def test_clean_runtime_wheel_excludes_research_build_control_and_provider_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
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
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=300,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            wheels = list(Path(temporary).glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(wheels[0]) as archive:
                names = archive.namelist()
                forbidden_prefixes = (
                    "build_control/",
                    "tools/helios_build/",
                    "helios_takeoff_core/research/",
                    "helios_takeoff_core/notebooklm/",
                    "helios_takeoff_core/browser/",
                    "helios_takeoff_core/grokbot/",
                )
                self.assertFalse(any(name.startswith(forbidden_prefixes) for name in names))
                entry_name = next(name for name in names if name.endswith("entry_points.txt"))
                entries = archive.read(entry_name).decode("utf-8")
                self.assertNotIn("helios-build", entries)
                self.assertNotIn("notebooklm", entries.lower())
                self.assertNotIn("grokbot", entries.lower())

    def test_research_modules_use_only_local_file_contracts(self) -> None:
        forbidden_imports = {
            "aiohttp",
            "browser_use",
            "httpx",
            "notebooklm",
            "openai",
            "playwright",
            "puppeteer",
            "requests",
            "selenium",
            "xai_sdk",
        }
        violations: dict[str, list[str]] = {}
        for source in (
            ROOT / "tools/helios_build/research.py",
            ROOT / "tools/helios_build/source_registry.py",
        ):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".", 1)[0])
            found = sorted(imported & forbidden_imports)
            if found:
                violations[source.name] = found
        self.assertEqual(violations, {})

        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertNotIn("helios-build", pyproject["project"]["scripts"])
        serialized = (ROOT / "tools/helios_build/research.py").read_text(encoding="utf-8").lower()
        for forbidden in ("cookie", "password", "browser profile", "notebooklm api", "grok bot cli"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
