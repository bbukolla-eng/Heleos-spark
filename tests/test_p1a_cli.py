"""Operator-boundary tests for the real one-item P1A CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database
from helios_takeoff_core.repository import TakeoffRepository
from helios_takeoff_core.agentic.service import AgentExecutionService


EXPECTED_WORKERS = {
    "codex",
    "claude",
    "kimi",
    "grok",
    "cursor",
    "grokbot",
    "helios-baseline-auditor",
}


class P1aCliTests(unittest.TestCase):
    """Catch fake CLI success, hidden loops, and metadata-free verification."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "helios.sqlite3"
        self.artifact_root = self.root / "artifacts"
        self.attempt_root = self.root / "attempts"
        self.workers_config = self.root / "workers.json"
        self.workers_config.write_text(
            json.dumps({"protocol": "helios.p1a.workers-config/v1", "workers": []}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _run_cli(self, *arguments: str, expected_exit: int = 0) -> tuple[dict | None, subprocess.CompletedProcess[str]]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        completed = subprocess.run(
            [sys.executable, "-m", "helios_takeoff_core.p1a_cli", *arguments],
            capture_output=True,
            text=True,
            cwd=self.root,
            env=environment,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, expected_exit, completed.stderr)
        return json.loads(completed.stdout), completed

    def _create_frozen_baseline(self) -> tuple[str, str, str]:
        repository = TakeoffRepository(Database(self.database_path))
        project_id = repository.create_project(code="P-500", name="School Renovation")
        actor_id = repository.create_actor(display_name="Estimator", actor_type="USER")
        document_id = repository.register_document_revision(
            project_id=project_id,
            document_type="DRAWING",
            document_number="M-501",
            title="Mechanical Plan",
            sha256="5" * 64,
            issue_date="2026-08-27",
        )
        revision_set_id = repository.create_revision_set(project_id=project_id, name="BID-01")
        repository.include_document_revision(revision_set_id, document_id)
        repository.freeze_revision_set(revision_set_id)
        return project_id, revision_set_id, actor_id

    def test_blank_database_initializes_and_bootstraps_exact_roster(self) -> None:
        initialized, _ = self._run_cli("init", "--database", str(self.database_path))
        bootstrapped, _ = self._run_cli("bootstrap-roster", "--database", str(self.database_path))

        self.assertEqual(initialized["status"], "INITIALIZED")
        self.assertEqual(initialized["schema_version"], 17)
        self.assertEqual(bootstrapped["status"], "BOOTSTRAPPED")
        self.assertEqual(set(bootstrapped["workers"]), EXPECTED_WORKERS)
        with Database(self.database_path).connection() as connection:
            roster = connection.execute(
                "SELECT worker_code FROM agent_workers ORDER BY worker_code"
            ).fetchall()
        self.assertEqual({row["worker_code"] for row in roster}, EXPECTED_WORKERS)

    def test_submit_run_once_and_job_show_use_real_subprocess_then_report_no_work(self) -> None:
        self._run_cli("init", "--database", str(self.database_path))
        self._run_cli("bootstrap-roster", "--database", str(self.database_path))
        project_id, revision_set_id, actor_id = self._create_frozen_baseline()

        submitted, _ = self._run_cli(
            "submit-baseline-audit",
            "--database", str(self.database_path),
            "--artifact-root", str(self.artifact_root),
            "--project-id", project_id,
            "--revision-set-id", revision_set_id,
            "--requested-by-actor-id", actor_id,
            "--worker-code", "helios-baseline-auditor",
        )
        first, _ = self._run_cli(
            "run-once",
            "--database", str(self.database_path),
            "--artifact-root", str(self.artifact_root),
            "--attempt-root", str(self.attempt_root),
            "--workers-config", str(self.workers_config),
        )
        second, _ = self._run_cli(
            "run-once",
            "--database", str(self.database_path),
            "--artifact-root", str(self.artifact_root),
            "--attempt-root", str(self.attempt_root),
            "--workers-config", str(self.workers_config),
        )
        shown, _ = self._run_cli(
            "job", "show", "--database", str(self.database_path), "--job-id", submitted["job_id"]
        )

        self.assertEqual(submitted["state"], "QUEUED")
        self.assertEqual(first["state"], "SUCCEEDED")
        self.assertEqual(second, None)
        self.assertEqual(shown["state"], "SUCCEEDED")
        self.assertEqual(shown["event_types"], ["QUEUED", "LEASED", "STARTED", "SUCCEEDED"])
        with Database(self.database_path).connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM agent_execution_attempts").fetchone()[0], 1)

    def test_preflight_command_registers_exact_configured_adapter_receipt(self) -> None:
        self._run_cli("init", "--database", str(self.database_path))
        self._run_cli("bootstrap-roster", "--database", str(self.database_path))
        payload = {
            "protocol": "helios.p1a.workers-config/v1",
            "workers": [
                {
                    "worker_code": "codex",
                    "adapter_name": "codex-local",
                    "adapter_kind": "SUBPROCESS",
                    "adapter_revision": "cli-v1",
                    "capability": "BASELINE_AUDIT",
                    "result_protocol": "helios.p1a.worker-result/v1",
                    "argv": [sys.executable, "-m", "helios_takeoff_core.agentic.manifest_worker"],
                    "preflight_argv": [sys.executable, "-c", "raise SystemExit(0)"],
                    "timeout_seconds": 5,
                    "environment_variables": [],
                }
            ],
        }
        self.workers_config.write_text(json.dumps(payload), encoding="utf-8")

        preflighted, _ = self._run_cli(
            "preflight",
            "--database", str(self.database_path),
            "--workers-config", str(self.workers_config),
        )

        self.assertEqual(preflighted["status"], "PREFLIGHTED")
        self.assertEqual(preflighted["receipts"][0]["availability"], "AVAILABLE")
        self.assertEqual(preflighted["receipts"][0]["reason_code"], "PREFLIGHT_READY")
        with Database(self.database_path).connection() as connection:
            adapter = connection.execute(
                """
                SELECT adapter_name, revision, configuration_sha256
                FROM agent_adapter_revisions
                WHERE adapter_name = 'codex-local'
                """
            ).fetchone()
        self.assertEqual(adapter["revision"], "cli-v1")
        self.assertEqual(adapter["configuration_sha256"], preflighted["receipts"][0]["configuration_sha256"])

    def test_artifact_verify_reconstructs_database_metadata_and_detects_tampering(self) -> None:
        self._run_cli("init", "--database", str(self.database_path))
        self._run_cli("bootstrap-roster", "--database", str(self.database_path))
        project_id, revision_set_id, actor_id = self._create_frozen_baseline()
        submitted, _ = self._run_cli(
            "submit-baseline-audit",
            "--database", str(self.database_path),
            "--artifact-root", str(self.artifact_root),
            "--project-id", project_id,
            "--revision-set-id", revision_set_id,
            "--requested-by-actor-id", actor_id,
            "--worker-code", "helios-baseline-auditor",
        )
        artifact_id = submitted["input_artifact_id"]

        valid, _ = self._run_cli(
            "artifact", "verify",
            "--database", str(self.database_path),
            "--artifact-root", str(self.artifact_root),
            "--artifact-id", artifact_id,
        )
        artifact_path = self.artifact_root / valid["artifact"]["store_key"]
        artifact_path.write_bytes(b"tampered")
        tampered, _ = self._run_cli(
            "artifact", "verify",
            "--database", str(self.database_path),
            "--artifact-root", str(self.artifact_root),
            "--artifact-id", artifact_id,
            expected_exit=1,
        )

        self.assertTrue(valid["valid"])
        self.assertEqual(valid["integrity_status"], "VERIFIED")
        self.assertEqual(valid["artifact"]["id"], artifact_id)
        self.assertFalse(tampered["valid"])
        self.assertEqual(tampered["integrity_status"], "TAMPERED")

    def test_reconcile_attempt_requires_explicit_dead_process_assertion_and_audits_operator(self) -> None:
        self._run_cli("init", "--database", str(self.database_path))
        self._run_cli("bootstrap-roster", "--database", str(self.database_path))
        project_id, revision_set_id, actor_id = self._create_frozen_baseline()
        submitted, _ = self._run_cli(
            "submit-baseline-audit",
            "--database", str(self.database_path),
            "--artifact-root", str(self.artifact_root),
            "--project-id", project_id,
            "--revision-set-id", revision_set_id,
            "--requested-by-actor-id", actor_id,
            "--worker-code", "helios-baseline-auditor",
        )
        service = AgentExecutionService(TakeoffRepository(Database(self.database_path)))
        claimed = service.claim_next_work_item()

        rejected, _ = self._run_cli(
            "reconcile-attempt",
            "--database", str(self.database_path),
            "--artifact-root", str(self.artifact_root),
            "--attempt-id", claimed["attempt_id"],
            "--operator-actor-id", actor_id,
            "--reason", "Confirmed process death from the host process table.",
            expected_exit=1,
        )
        self.assertIn("process is dead", rejected["error"]["message"])

        reconciled, _ = self._run_cli(
            "reconcile-attempt",
            "--database", str(self.database_path),
            "--artifact-root", str(self.artifact_root),
            "--attempt-id", claimed["attempt_id"],
            "--operator-actor-id", actor_id,
            "--reason", "Confirmed process death from the host process table.",
            "--assert-process-dead",
        )
        shown, _ = self._run_cli(
            "job", "show", "--database", str(self.database_path), "--job-id", submitted["job_id"]
        )

        self.assertEqual(reconciled["state"], "ESCALATED")
        self.assertEqual(reconciled["reason_code"], "OUTCOME_UNKNOWN")
        self.assertEqual(shown["report_artifact_id"], None)
        self.assertEqual(set(shown["artifacts"]), {"INPUT", "STDOUT", "STDERR", "ERROR"})
        self.assertEqual(shown["latest_detail"]["operator_actor_id"], actor_id)


if __name__ == "__main__":
    unittest.main()
