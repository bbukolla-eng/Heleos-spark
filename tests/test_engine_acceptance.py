"""Blank-database acceptance contracts for the HELIOS P1B engine foundation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.agentic.acceptance import P0_BID_IMPACTING_TABLES
from helios_takeoff_core.db import Database


P1A_EXECUTION_TABLES = (
    "agent_adapter_revisions",
    "agent_artifacts",
    "agent_execution_attempt_artifacts",
    "agent_execution_attempts",
    "agent_jobs",
    "agent_work_item_dependencies",
    "agent_work_item_events",
    "agent_work_items",
    "agent_worker_adapter_assignments",
    "agent_worker_availability_events",
    "agent_worker_capabilities",
    "agent_workers",
    "agent_workflow_runs",
)


class EngineAcceptanceTests(unittest.TestCase):
    """Catch fake engine success, authority writes, and hidden execution."""

    def test_acceptance_runs_real_engine_pipeline_without_p0_or_p1a_mutation(self) -> None:
        """A synthetic proof or authority-table write breaks the acceptance contract."""
        from helios_takeoff_core.engine.acceptance import build_engine_foundation_acceptance

        with tempfile.TemporaryDirectory() as temporary_directory:
            work_root = Path(temporary_directory) / "acceptance"

            result = build_engine_foundation_acceptance(work_root)

            self.assertEqual(result["acceptance"], "P1B_ENGINE_FOUNDATION_SUCCEEDED")
            self.assertEqual(result["fixture"]["authority"], "NON_AUTHORITATIVE_DEMO")
            self.assertTrue(result["compile"]["deterministic"])
            self.assertTrue(result["import"]["idempotent_replay"])
            self.assertTrue(result["import"]["same_version_conflict_rejected"])
            self.assertEqual(result["import"]["pack_id"], result["import"]["replay_pack_id"])
            self.assertEqual(
                result["query"]["item"],
                {
                    "code": "CMP-SHARED",
                    "type": "COMPONENT",
                    "title": "Demo component",
                    "uom": "EA",
                },
            )
            self.assertEqual(
                result["query"]["relations"],
                [
                    {"from_code": "ASM-NESTED", "relation": "REQUIRES", "to_code": "CMP-SHARED"},
                    {"from_code": "CMP-SHARED", "relation": "PART_OF", "to_code": "SYS-AIR"},
                    {"from_code": "CMP-SHARED", "relation": "USES_MATERIAL", "to_code": "MAT-SHEET"},
                ],
            )
            evaluation = result["evaluation"]
            self.assertEqual(evaluation["pack_code"], result["fixture"]["pack_code"])
            self.assertEqual(evaluation["version"], result["fixture"]["version"])
            self.assertEqual(evaluation["sha256"], result["compile"]["sha256"])
            self.assertEqual(evaluation["subject_code"], "CMP-SHARED")
            self.assertEqual(evaluation["candidate_claims"], [
                {
                    "rule_code": "A-READY-RULE",
                    "rule_type": "MEASURE",
                    "subject_code": "CMP-SHARED",
                    "output_claim_type": "EQUIPMENT_COUNT",
                    "output_uom": "EA",
                    "observations": {"COUNT": 2},
                    "evidence_kinds": ["MECHANICAL_PLAN"],
                }
            ])
            self.assertEqual(evaluation["blocked_rules"], [
                {
                    "rule_code": "Z-BLOCKED-RULE",
                    "rule_type": "CLASSIFY",
                    "subject_code": "CMP-SHARED",
                    "output_claim_type": "EQUIPMENT_CLASS",
                    "missing_observations": ["MODEL"],
                    "missing_evidence_kinds": ["SCHEDULE"],
                }
            ])
            assembly = result["assembly"]
            self.assertEqual(assembly["pack_code"], result["fixture"]["pack_code"])
            self.assertEqual(assembly["version"], result["fixture"]["version"])
            self.assertEqual(assembly["sha256"], result["compile"]["sha256"])
            self.assertEqual(assembly["item_code"], "ASM-ROOT")
            self.assertEqual(assembly["items"], [
                {"code": "ASM-NESTED", "type": "ASSEMBLY", "title": "Demo nested assembly", "uom": "EA"},
                {"code": "ASM-ROOT", "type": "ASSEMBLY", "title": "Demo root assembly", "uom": "EA"},
                {"code": "CMP-SHARED", "type": "COMPONENT", "title": "Demo component", "uom": "EA"},
                {"code": "MAT-SHEET", "type": "MATERIAL", "title": "Demo sheet metal", "uom": "SF"},
            ])
            self.assertEqual(assembly["relations"], [
                {"from_code": "ASM-NESTED", "relation": "REQUIRES", "to_code": "CMP-SHARED"},
                {"from_code": "ASM-ROOT", "relation": "COMPOSED_OF", "to_code": "ASM-NESTED"},
                {"from_code": "CMP-SHARED", "relation": "USES_MATERIAL", "to_code": "MAT-SHEET"},
            ])
            for key, expected_tables in (
                ("p0_bid_impacting", P0_BID_IMPACTING_TABLES),
                ("p1a_execution", P1A_EXECUTION_TABLES),
            ):
                snapshot = result["authority_tables"][key]
                self.assertTrue(snapshot["unchanged"])
                self.assertEqual(snapshot["before"], {table: 0 for table in expected_tables})
                self.assertEqual(snapshot["after"], snapshot["before"])
            self.assertNotIn("execution_counters", result)
            self.assertNotIn("execution_evidence", result)
            self.assertEqual(result["execution"], {
                "worker_attempts": {"before": 0, "after": 0, "delta": 0},
            })

            database = Database(work_root / "helios-p1b-engine.sqlite3")
            with database.connection() as connection:
                engine_count = connection.execute(
                    "SELECT COUNT(*) FROM engine_domain_packs"
                ).fetchone()[0]
                p1a_tables = tuple(
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'agent_%' ORDER BY name"
                    )
                )
            self.assertEqual(engine_count, 1)
            self.assertEqual(p1a_tables, P1A_EXECUTION_TABLES)

            with self.assertRaises(FileExistsError):
                build_engine_foundation_acceptance(work_root)

    def test_cli_acceptance_uses_the_shipped_acceptance_builder(self) -> None:
        """A separate CLI demo could drift from the installed acceptance proof."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_root = Path(temporary_directory) / "acceptance"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "helios_takeoff_core.engine_cli",
                    "acceptance",
                    "--work-root",
                    str(work_root),
                ],
                capture_output=True,
                text=True,
                env=environment,
                timeout=20,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            result = json.loads(completed.stdout)
            self.assertEqual(result["acceptance"], "P1B_ENGINE_FOUNDATION_SUCCEEDED")
            self.assertEqual(result["execution"]["worker_attempts"]["delta"], 0)


if __name__ == "__main__":
    unittest.main()
