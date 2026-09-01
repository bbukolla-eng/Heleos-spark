"""Static proof that Build Fabric cannot acquire runtime database authority."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


class BuildAuthorityBoundaryTests(unittest.TestCase):
    def test_build_tools_never_import_runtime_or_database_authority(self) -> None:
        """Adding a runtime/DB import to a Build Fabric module fails immediately."""
        repo = Path(__file__).resolve().parents[1]
        forbidden = {"helios_takeoff_core", "sqlite3", "sqlalchemy"}
        violations: dict[str, list[str]] = {}
        for source in sorted((repo / "tools" / "helios_build").glob("*.py")):
            found = sorted(_import_roots(source) & forbidden)
            if found:
                violations[source.relative_to(repo).as_posix()] = found
        self.assertEqual(violations, {})

    def test_build_tools_contain_no_p0_or_p1a_table_access(self) -> None:
        """Quoting or issuing SQL against a runtime table fails this boundary."""
        repo = Path(__file__).resolve().parents[1]
        table_names: set[str] = set()
        for migration in (repo / "src" / "helios_takeoff_core" / "migrations").glob("*.sql"):
            table_names.update(
                name.lower()
                for name in re.findall(
                    r"\bCREATE\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)",
                    migration.read_text(encoding="utf-8"),
                    flags=re.IGNORECASE,
                )
            )
        violations: list[str] = []
        for source in sorted((repo / "tools" / "helios_build").glob("*.py")):
            text = source.read_text(encoding="utf-8")
            for table in sorted(table_names):
                quoted = re.search(rf"[\"']{re.escape(table)}[\"']", text, re.IGNORECASE)
                sql = re.search(
                    rf"\b(?:FROM|INTO|JOIN|UPDATE|TABLE)\s+[\"`\[]?{re.escape(table)}\b",
                    text,
                    re.IGNORECASE,
                )
                if quoted or sql:
                    violations.append(f"{source.relative_to(repo).as_posix()}:{table}")
        self.assertEqual(violations, [])

    def test_runtime_never_imports_repository_build_tools(self) -> None:
        """Making runtime code depend on tools.helios_build fails package separation."""
        repo = Path(__file__).resolve().parents[1]
        violations: list[str] = []
        for source in sorted((repo / "src" / "helios_takeoff_core").rglob("*.py")):
            if "tools" in _import_roots(source):
                violations.append(source.relative_to(repo).as_posix())
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
