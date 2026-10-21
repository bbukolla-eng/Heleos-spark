"""Static proof that Build Fabric cannot acquire runtime database authority."""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path


def _schema_property_names(repo: Path) -> set[str]:
    """Collect property names declared by committed Build Fabric JSON schemas."""
    names: set[str] = set()

    def _collect(node: object) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                names.update(key.lower() for key in properties)
            for value in node.values():
                _collect(value)
        elif isinstance(node, list):
            for item in node:
                _collect(item)

    for schema_path in (repo / "build_control" / "schemas").glob("*.json"):
        _collect(json.loads(schema_path.read_text(encoding="utf-8")))
    return names


_Span = tuple[int, int, int, int]


def _contract_field_literal_spans(
    text: str, filename: str, contract_fields: set[str]
) -> dict[str, list[_Span]]:
    """Map each contract field to its dict-key and subscript literal spans."""
    tree = ast.parse(text, filename=filename)
    spans: dict[str, list[_Span]] = {}

    def _consider(node: ast.AST | None) -> None:
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            return
        field = node.value.lower()
        if (
            field not in contract_fields
            or node.end_lineno is None
            or node.end_col_offset is None
        ):
            return
        spans.setdefault(field, []).append(
            (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                _consider(key)
        elif isinstance(node, ast.DictComp):
            _consider(node.key)
        elif isinstance(node, ast.Subscript):
            _consider(node.slice)
    return spans


def _position(text: str, offset: int) -> tuple[int, int]:
    """Return the 1-based line and 0-based column of a source offset."""
    line = text.count("\n", 0, offset) + 1
    previous_newline = text.rfind("\n", 0, offset)
    column = offset if previous_newline < 0 else offset - previous_newline - 1
    return line, column


def _span_contains(span: _Span, start: tuple[int, int], end: tuple[int, int]) -> bool:
    start_line, start_column, end_line, end_column = span
    return (start_line, start_column) <= start and end <= (end_line, end_column)


def _quoted_table_outside_contract_fields(
    text: str, table: str, spans: dict[str, list[_Span]]
) -> bool:
    """True when a quoted table name is outside a contract-field key or subscript."""
    pattern = re.compile(rf"[\"']{re.escape(table)}[\"']", re.IGNORECASE)
    allowed = spans.get(table, [])
    for match in pattern.finditer(text):
        start = _position(text, match.start())
        end = _position(text, match.end())
        if not any(_span_contains(span, start, end) for span in allowed):
            return True
    return False


def _runtime_table_violations(
    text: str,
    filename: str,
    table_names: set[str],
    contract_fields: set[str],
) -> list[str]:
    """Return runtime tables quoted outside contract fields or named in SQL."""
    spans = _contract_field_literal_spans(text, filename, contract_fields)
    violations: list[str] = []
    for table in sorted(table_names):
        quoted = _quoted_table_outside_contract_fields(text, table, spans)
        sql = re.search(
            rf"\b(?:FROM|INTO|JOIN|UPDATE|TABLE)\s+[\"`\[]?{re.escape(table)}\b",
            text,
            re.IGNORECASE,
        )
        if quoted or sql:
            violations.append(table)
    return violations


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
        contract_fields = _schema_property_names(repo)
        for source in sorted((repo / "tools" / "helios_build").glob("*.py")):
            relative = source.relative_to(repo).as_posix()
            text = source.read_text(encoding="utf-8")
            violations.extend(
                f"{relative}:{table}"
                for table in _runtime_table_violations(
                    text, relative, table_names, contract_fields
                )
            )
        self.assertEqual(violations, [])

    def test_contract_field_keys_and_subscripts_are_not_runtime_table_access(self) -> None:
        """A schema property used as a mapping key or subscript is not SQL."""
        source = (
            "def collect(packet):\n"
            "    rows = packet['conflicts']\n"
            "    return {'conflicts': rows}\n"
        )
        self.assertEqual(
            _runtime_table_violations(source, "snippet.py", {"conflicts"}, {"conflicts"}),
            [],
        )

    def test_composed_sql_still_counts_as_runtime_table_access(self) -> None:
        """Joining a runtime table name into SQL stays a boundary violation."""
        source = (
            "def read(connection):\n"
            '    connection.execute("SELECT * FROM " + "conflicts")\n'
        )
        self.assertEqual(
            _runtime_table_violations(source, "snippet.py", {"conflicts"}, {"conflicts"}),
            ["conflicts"],
        )

    def test_non_contract_subscript_is_still_runtime_table_access(self) -> None:
        """Subscripting a runtime table absent from the contract still fails."""
        source = "def read(packet):\n    return packet['projects']\n"
        self.assertEqual(
            _runtime_table_violations(source, "snippet.py", {"projects"}, {"conflicts"}),
            ["projects"],
        )

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
