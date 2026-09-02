"""Blank-database acceptance for the real P1A baseline-audit subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helios_takeoff_core.agentic.artifacts import ArtifactRecord, ArtifactStore
from helios_takeoff_core.db import Database


EXPECTED_ROSTER = {
    "codex": "UNAVAILABLE",
    "claude": "UNAVAILABLE",
    "kimi": "UNAVAILABLE",
    "grok": "UNAVAILABLE",
    "cursor": "UNAVAILABLE",
    "grokbot": "UNAVAILABLE",
    "helios-baseline-auditor": "AVAILABLE",
}

P0_BID_IMPACTING_TABLES = (
    "evidence_items",
    "extraction_runs",
    "extraction_claims",
    "claim_evidence",
    "quantity_assertions",
    "quantity_evidence_allocations",
    "quantity_review_events",
    "revision_set_impacts",
    "conflicts",
    "rfis",
    "rfi_responses",
    "conflict_dispositions",
    "takeoff_versions",
    "takeoff_lines",
    "takeoff_approval_events",
    "suppliers",
    "quote_revisions",
    "quote_lines",
    "estimate_versions",
    "estimate_lines",
    "estimate_approval_events",
    "bid_releases",
    "bid_release_approval_events",
    "bid_release_void_events",
)


def _acceptance_module():
    from helios_takeoff_core.agentic import acceptance

    return acceptance


class P1aEndToEndTests(unittest.TestCase):
    """Catch synthetic success, incomplete lineage, hidden loops, and P0 writes."""

    def test_blank_database_runs_real_baseline_audit_without_p0_bid_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_root = Path(temporary_directory)
            metadata = {
                "project": {"code": "P1A-ACCEPT", "name": "Acceptance Metadata Project"},
                "actor": {"display_name": "Acceptance Estimator", "actor_type": "USER"},
                "revision_set_name": "BID-BASELINE",
                "documents": [
                    {
                        "document_type": "DRAWING",
                        "document_number": "M-101",
                        "title": "Mechanical Floor Plan Metadata",
                        "sha256": "1" * 64,
                        "issue_date": "2026-08-27",
                    }
                ],
            }

            result = _acceptance_module().build_baseline_audit(work_root, metadata)

            self.assertEqual(result["acceptance"], "P1A_BASELINE_AUDIT_SUCCEEDED")
            self.assertEqual(result["execution"]["first_run"]["state"], "SUCCEEDED")
            self.assertIsNone(result["execution"]["second_run"])
            self.assertEqual(result["execution"]["event_types"], ["QUEUED", "LEASED", "STARTED", "SUCCEEDED"])
            self.assertEqual(result["execution"]["attempt_count"], 1)
            self.assertEqual(result["roster"], EXPECTED_ROSTER)
            self.assertEqual(set(result["artifacts"]), {"INPUT", "STDOUT", "STDERR", "REPORT"})
            self.assertTrue(all(artifact["verified"] for artifact in result["artifacts"].values()))
            self.assertEqual(result["p0_bid_impacting_tables"]["before"], result["p0_bid_impacting_tables"]["after"])
            self.assertEqual(result["p0_bid_impacting_tables"]["after"], {table: 0 for table in P0_BID_IMPACTING_TABLES})

            report = result["report"]
            self.assertEqual(report["kind"], "BASELINE_AUDIT_REPORT")
            self.assertEqual(report["payload"]["project_id"], result["project_id"])
            self.assertEqual(report["payload"]["revision_set_id"], result["revision_set_id"])
            self.assertEqual(report["payload"]["document_count"], 1)
            self.assertEqual(report["payload"]["document_counts_by_type"], {"DRAWING": 1})
            limitation = report["payload"]["limitation"].lower()
            self.assertIn("frozen manifest metadata only", limitation)
            self.assertIn("not drawing/spec contents or quantities", limitation)
            for excluded_capability in (
                "pdf ingestion",
                "drawing interpretation",
                "takeoff",
                "pricing",
                "external provider acceptance",
            ):
                self.assertIn(excluded_capability, limitation)
            self.assertEqual(
                result["scope"],
                {
                    "audited": "frozen revision-set manifest metadata only",
                    "not_accepted": [
                        "PDF ingestion",
                        "drawing interpretation",
                        "takeoff",
                        "pricing",
                        "external provider execution",
                    ],
                },
            )

            database = Database(work_root / "helios-p1a.sqlite3")
            artifact_store = ArtifactStore(work_root / "artifacts")
            with database.connection() as connection:
                events = connection.execute(
                    "SELECT event_type FROM agent_work_item_events ORDER BY rowid"
                ).fetchall()
                attempts = connection.execute(
                    "SELECT id, attempt_number FROM agent_execution_attempts"
                ).fetchall()
                artifact_rows = connection.execute(
                    """
                    SELECT link.purpose, artifact.*
                    FROM agent_execution_attempt_artifacts AS link
                    JOIN agent_artifacts AS artifact ON artifact.id = link.artifact_id
                    WHERE link.execution_attempt_id = ?
                    ORDER BY link.purpose
                    """,
                    (attempts[0]["id"],),
                ).fetchall()
                after = {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in P0_BID_IMPACTING_TABLES
                }
            self.assertEqual([event["event_type"] for event in events], ["QUEUED", "LEASED", "STARTED", "SUCCEEDED"])
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["attempt_number"], 1)
            self.assertEqual(after, {table: 0 for table in P0_BID_IMPACTING_TABLES})

            verified_bytes = {}
            for row in artifact_rows:
                record = ArtifactRecord(
                    sha256=row["sha256"],
                    byte_size=row["byte_size"],
                    media_type=row["media_type"],
                    schema_version=row["schema_version"],
                    store_key=row["store_key"],
                )
                self.assertTrue(artifact_store.verify(record))
                verified_bytes[row["purpose"]] = artifact_store.read_bytes(record)
            self.assertEqual(set(verified_bytes), {"INPUT", "STDOUT", "STDERR", "REPORT"})
            self.assertEqual(verified_bytes["STDERR"], b"")
            self.assertEqual(json.loads(verified_bytes["REPORT"]), report)

    def test_cli_acceptance_runs_from_the_shipped_package_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            work_root = Path(temporary_directory) / "acceptance"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "helios_takeoff_core.p1a_cli",
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
            result = json.loads(completed.stdout)
            self.assertEqual(result["acceptance"], "P1A_BASELINE_AUDIT_SUCCEEDED")
            self.assertEqual(result["execution"]["first_run"]["state"], "SUCCEEDED")
            self.assertIsNotNone(result["execution"]["report_artifact_id"])


if __name__ == "__main__":
    unittest.main()
