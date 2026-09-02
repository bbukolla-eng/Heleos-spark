"""Operator-boundary tests for the finite HELIOS P1B engine CLI."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database


def cli_pack() -> dict[str, object]:
    return {
        "protocol": "helios.p1b.domain-pack/v1",
        "pack_code": "DIV23-CLI",
        "version": "1.0.0",
        "title": "Non-authoritative CLI fixture",
        "jurisdiction": "US",
        "provenance": [
            {"kind": "PUBLIC_URL", "reference": "https://example.test/div23-cli"}
        ],
        "catalog": [
            {"code": "SYS-AIR", "type": "SYSTEM", "title": "Air system", "uom": "EA"},
            {"code": "ASM-AHU", "type": "ASSEMBLY", "title": "AHU assembly", "uom": "EA"},
            {"code": "CMP-AHU", "type": "COMPONENT", "title": "Air handler", "uom": "EA"},
            {"code": "MAT-SHEET", "type": "MATERIAL", "title": "Sheet metal", "uom": "SF"},
        ],
        "relations": [
            {"from_code": "CMP-AHU", "relation": "PART_OF", "to_code": "SYS-AIR"},
            {"from_code": "ASM-AHU", "relation": "COMPOSED_OF", "to_code": "CMP-AHU"},
            {"from_code": "CMP-AHU", "relation": "USES_MATERIAL", "to_code": "MAT-SHEET"},
        ],
        "rules": [
            {
                "code": "A-READY",
                "type": "MEASURE",
                "subject_code": "CMP-AHU",
                "required_observations": ["COUNT"],
                "required_evidence": ["MECHANICAL_PLAN"],
                "output_claim_type": "EQUIPMENT_COUNT",
                "output_uom": "EA",
            },
            {
                "code": "Z-BLOCKED",
                "type": "CLASSIFY",
                "subject_code": "CMP-AHU",
                "required_observations": ["MODEL"],
                "required_evidence": ["SCHEDULE"],
                "output_claim_type": "EQUIPMENT_CLASS",
            },
        ],
    }


class EngineCliTests(unittest.TestCase):
    """Catch non-canonical output, query bypasses, and non-JSON failures."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.pack_path = self.root / "pack.json"
        self.database_path = self.root / "helios.sqlite3"
        self.pack_path.write_text(json.dumps(cli_pack()), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _run_cli(
        self, *arguments: str, expected_exit: int = 0
    ) -> tuple[dict[str, object], subprocess.CompletedProcess[str]]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        completed = subprocess.run(
            [sys.executable, "-m", "helios_takeoff_core.engine_cli", *arguments],
            capture_output=True,
            text=True,
            cwd=self.root,
            env=environment,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, expected_exit, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(completed.stdout.count("\n"), 1)
        return json.loads(completed.stdout), completed

    def test_compile_emits_compact_sorted_json_and_writes_only_canonical_bytes(self) -> None:
        """Pretty output or metadata in the compiled file breaks content identity."""
        output_path = self.root / "compiled.json"
        canonical_document = cli_pack()
        canonical_document["catalog"] = sorted(
            canonical_document["catalog"], key=lambda item: item["code"]
        )
        canonical_document["relations"] = sorted(
            canonical_document["relations"],
            key=lambda relation: (
                relation["from_code"], relation["relation"], relation["to_code"]
            ),
        )
        canonical_document["rules"] = sorted(
            canonical_document["rules"], key=lambda rule: rule["code"]
        )
        canonical_bytes = json.dumps(
            canonical_document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        expected = {
            "document": canonical_document,
            "sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        }

        result, completed = self._run_cli(
            "compile", "--input", str(self.pack_path), "--output", str(output_path)
        )

        self.assertEqual(result, expected)
        self.assertEqual(
            completed.stdout,
            json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n",
        )
        self.assertEqual(output_path.read_bytes(), canonical_bytes)

    def test_database_commands_import_query_evaluate_and_resolve_production_pack(self) -> None:
        """Skipping repository reload or the production kernel changes observable results."""
        imported, _ = self._run_cli(
            "import", "--database", str(self.database_path), "--input", str(self.pack_path)
        )
        shown, _ = self._run_cli(
            "pack", "show", "--database", str(self.database_path),
            "--pack-code", "DIV23-CLI", "--version", "1.0.0",
        )
        item, _ = self._run_cli(
            "item", "show", "--database", str(self.database_path),
            "--pack-code", "DIV23-CLI", "--version", "1.0.0", "--item-code", "CMP-AHU",
        )
        context_path = self.root / "context.json"
        context_path.write_text(
            json.dumps(
                {
                    "subject_code": "CMP-AHU",
                    "observations": {"COUNT": 2},
                    "evidence_kinds": ["MECHANICAL_PLAN"],
                }
            ),
            encoding="utf-8",
        )
        evaluated, _ = self._run_cli(
            "evaluate", "--database", str(self.database_path),
            "--pack-code", "DIV23-CLI", "--version", "1.0.0", "--input", str(context_path),
        )
        assembly, _ = self._run_cli(
            "assembly", "resolve", "--database", str(self.database_path),
            "--pack-code", "DIV23-CLI", "--version", "1.0.0", "--item-code", "ASM-AHU",
        )

        self.assertEqual(imported["status"], "IMPORTED")
        self.assertEqual(imported["sha256"], evaluated["sha256"])
        self.assertEqual(shown["pack_code"], "DIV23-CLI")
        self.assertEqual(item["item"]["code"], "CMP-AHU")
        self.assertEqual(
            {(edge["relation"], edge["to_code"]) for edge in item["relations"]},
            {("PART_OF", "SYS-AIR"), ("USES_MATERIAL", "MAT-SHEET"), ("COMPOSED_OF", "CMP-AHU")},
        )
        self.assertEqual([claim["rule_code"] for claim in evaluated["candidate_claims"]], ["A-READY"])
        self.assertEqual([rule["rule_code"] for rule in evaluated["blocked_rules"]], ["Z-BLOCKED"])
        self.assertEqual(
            [entry["code"] for entry in assembly["items"]], ["ASM-AHU", "CMP-AHU", "MAT-SHEET"]
        )
        with Database(self.database_path).connection() as connection:
            version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        self.assertEqual(version, 17)

    def test_parser_and_domain_errors_emit_one_json_error_and_exit_one(self) -> None:
        """Argparse text or a traceback would violate the machine-readable boundary."""
        error, _ = self._run_cli(
            "pack", "show", "--database", str(self.database_path), expected_exit=1
        )

        self.assertEqual(set(error), {"error"})
        self.assertEqual(set(error["error"]), {"code", "message"})
        self.assertTrue(error["error"]["message"])


if __name__ == "__main__":
    unittest.main()
