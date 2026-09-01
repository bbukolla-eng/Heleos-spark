"""Strict external-worker configuration, preflight, and process-bound tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.agentic.artifacts import ArtifactRecord, ArtifactStore
from helios_takeoff_core.db import Database
from helios_takeoff_core.errors import ValidationError
from helios_takeoff_core.repository import TakeoffRepository


class WorkerConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = Database(self.root / "helios.sqlite3")
        self.database.initialize()
        self.repository = TakeoffRepository(self.database)
        from helios_takeoff_core.agentic.service import AgentExecutionService

        self.service = AgentExecutionService(self.repository)
        self.service.bootstrap_roster()
        self.artifact_store = ArtifactStore(self.root / "artifacts")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _configuration_payload(
        self,
        *,
        revision: str = "configured-v1",
        argv: list[str] | None = None,
        preflight_argv: list[str] | None = None,
        environment_variables: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "protocol": "helios.p1a.workers-config/v1",
            "workers": [
                {
                    "worker_code": "codex",
                    "adapter_name": "local-codex-test-adapter",
                    "adapter_kind": "SUBPROCESS",
                    "adapter_revision": revision,
                    "capability": "BASELINE_AUDIT",
                    "result_protocol": "helios.p1a.worker-result/v1",
                    "argv": argv
                    or [sys.executable, "-m", "helios_takeoff_core.agentic.manifest_worker"],
                    "preflight_argv": preflight_argv
                    or [sys.executable, "-c", "import sys; sys.exit(0)"],
                    "timeout_seconds": 5,
                    "environment_variables": environment_variables or [],
                }
            ],
        }

    def _load(self, payload: dict[str, object]):
        from helios_takeoff_core.agentic.contracts import load_workers_config

        path = self.root / f"workers-{time.time_ns()}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_workers_config(path)

    def _baseline(self) -> tuple[str, str, str]:
        project_id = self.repository.create_project(code="P-CONFIG", name="Configured Worker")
        actor_id = self.repository.create_actor(display_name="Operator", actor_type="USER")
        document_id = self.repository.register_document_revision(
            project_id=project_id,
            document_type="DRAWING",
            document_number="M-100",
            title="Mechanical",
            sha256="7" * 64,
            issue_date="2026-08-27",
        )
        revision_set_id = self.repository.create_revision_set(project_id=project_id, name="BID")
        self.repository.include_document_revision(revision_set_id, document_id)
        self.repository.freeze_revision_set(revision_set_id)
        return project_id, revision_set_id, actor_id

    def _run_configured_worker(self, command: str) -> tuple[dict[str, object], dict[str, object]]:
        from helios_takeoff_core.agentic.runner import AgentRunner

        configuration = self._load(
            self._configuration_payload(argv=[sys.executable, "-c", command])
        )
        self.assertEqual(self.service.preflight_workers(configuration)[0]["availability"], "AVAILABLE")
        project_id, revision_set_id, actor_id = self._baseline()
        submitted = self.service.submit_baseline_audit(
            project_id=project_id,
            revision_set_id=revision_set_id,
            requested_by_actor_id=actor_id,
            worker_code="codex",
            artifact_store=self.artifact_store,
        )
        outcome = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
            workers_config=configuration,
        ).run_once()
        job = self.service.get_job(submitted["job_id"])
        return outcome, job

    def _read_error_artifact(self, job: dict[str, object]) -> dict[str, object]:
        error_metadata = job["artifacts"]["ERROR"]
        error_record = ArtifactRecord(
            sha256=error_metadata["sha256"],
            byte_size=error_metadata["byte_size"],
            media_type=error_metadata["media_type"],
            schema_version=error_metadata["schema_version"],
            store_key=error_metadata["store_key"],
        )
        return json.loads(self.artifact_store.read_bytes(error_record))

    def test_config_parser_is_strict_and_hashes_normalized_nonsecret_fields(self) -> None:
        first_payload = self._configuration_payload(environment_variables=["Z_TOKEN", "A_TOKEN"])
        second_payload = self._configuration_payload(environment_variables=["A_TOKEN", "Z_TOKEN"])

        first = self._load(first_payload)
        second = self._load(second_payload)

        self.assertEqual(first.worker("codex").configuration_sha256, second.worker("codex").configuration_sha256)
        invalid = first_payload | {"unexpected": True}
        with self.assertRaisesRegex(ValidationError, "fields"):
            self._load(invalid)
        duplicate_path = self.root / "duplicate.json"
        duplicate_path.write_text(
            '{"protocol":"helios.p1a.workers-config/v1","protocol":"duplicate","workers":[]}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValidationError, "duplicate"):
            from helios_takeoff_core.agentic.contracts import load_workers_config

            load_workers_config(duplicate_path)

    def test_real_preflight_registers_exact_adapter_without_persisting_command_or_secret(self) -> None:
        secret = "DO-NOT-PERSIST-THIS-CREDENTIAL"
        variable_name = "HELIOS_P1A_TEST_SECRET"
        os.environ[variable_name] = secret
        try:
            configuration = self._load(
                self._configuration_payload(
                    environment_variables=[variable_name],
                    preflight_argv=[
                        sys.executable,
                        "-c",
                        f"import os,sys;sys.exit(0 if os.environ.get('{variable_name}') else 3)",
                    ],
                )
            )

            receipts = self.service.preflight_workers(configuration)
        finally:
            os.environ.pop(variable_name, None)

        configured = configuration.worker("codex")
        self.assertEqual(receipts[0]["availability"], "AVAILABLE")
        self.assertEqual(receipts[0]["reason_code"], "PREFLIGHT_READY")
        assignment = self.service.repository.worker_assignment("codex")
        self.assertEqual(assignment["adapter_name"], configured.adapter_name)
        self.assertEqual(assignment["revision"], configured.adapter_revision)
        self.assertEqual(assignment["configuration_sha256"], configured.configuration_sha256)
        database_bytes = Path(self.database.path).read_bytes()
        self.assertNotIn(secret.encode(), database_bytes)
        self.assertNotIn("sys.exit(0 if".encode(), database_bytes)

    def test_external_execution_requires_the_exact_preflighted_configuration_hash(self) -> None:
        from helios_takeoff_core.agentic.runner import AgentRunner

        ready_configuration = self._load(self._configuration_payload())
        self.assertEqual(self.service.preflight_workers(ready_configuration)[0]["availability"], "AVAILABLE")
        project_id, revision_set_id, actor_id = self._baseline()
        first_job = self.service.submit_baseline_audit(
            project_id=project_id,
            revision_set_id=revision_set_id,
            requested_by_actor_id=actor_id,
            worker_code="codex",
            artifact_store=self.artifact_store,
        )
        mismatched_configuration = self._load(
            self._configuration_payload(
                argv=[sys.executable, "-c", "raise SystemExit('must not execute')"],
            )
        )

        blocked = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
            workers_config=mismatched_configuration,
        ).run_once()

        self.assertEqual(blocked["state"], "BLOCKED")
        self.assertEqual(blocked["reason_code"], "WORKER_CONFIG_MISMATCH")
        self.assertEqual(self.service.get_job(first_job["job_id"])["state"], "BLOCKED")

        second_job = self.service.submit_baseline_audit(
            project_id=project_id,
            revision_set_id=revision_set_id,
            requested_by_actor_id=actor_id,
            worker_code="codex",
            artifact_store=self.artifact_store,
        )
        succeeded = AgentRunner(
            self.service,
            artifact_store=self.artifact_store,
            attempt_root=self.root / "attempts",
            workers_config=ready_configuration,
        ).run_once()
        self.assertEqual(succeeded["state"], "SUCCEEDED")
        self.assertEqual(self.service.get_job(second_job["job_id"])["state"], "SUCCEEDED")

    def test_bounded_process_caps_streams_and_terminates_descendants_on_timeout(self) -> None:
        from helios_takeoff_core.agentic.processes import MAX_CAPTURE_BYTES, run_bounded_process

        output = run_bounded_process(
            [sys.executable, "-c", f"import sys;sys.stdout.buffer.write(b'x'*{MAX_CAPTURE_BYTES + 4096})"],
            input_bytes=b"",
            cwd=self.root,
            env={"PATH": os.defpath},
            timeout_seconds=5,
        )
        self.assertEqual(len(output.stdout), MAX_CAPTURE_BYTES)
        self.assertTrue(output.stdout_truncated)

        marker = self.root / "descendant-survived"
        descendant_code = f"import time,pathlib;time.sleep(1.5);pathlib.Path({str(marker)!r}).write_text('alive')"
        parent_code = (
            "import subprocess,sys,time;"
            f"subprocess.Popen([sys.executable,'-c',{descendant_code!r}]);"
            "time.sleep(20)"
        )
        timed_out = run_bounded_process(
            [sys.executable, "-c", parent_code],
            input_bytes=b"",
            cwd=self.root,
            env={"PATH": os.defpath},
            timeout_seconds=1,
        )
        self.assertTrue(timed_out.timed_out)
        time.sleep(1.0)
        self.assertFalse(marker.exists())

    def test_valid_result_prefix_with_truncated_stdout_cannot_succeed(self) -> None:
        command = (
            "import sys;from helios_takeoff_core.agentic.manifest_worker import main;"
            "status=main();sys.stdout.flush();"
            "sys.stdout.buffer.write(b' '*(1024*1024)+b'INVALID');"
            "sys.stdout.buffer.flush();raise SystemExit(status)"
        )

        outcome, job = self._run_configured_worker(command)

        self.assertEqual(outcome["state"], "ESCALATED")
        self.assertEqual(outcome["reason_code"], "OUTPUT_TRUNCATED")
        self.assertEqual(set(job["artifacts"]), {"INPUT", "STDOUT", "STDERR", "ERROR"})
        self.assertEqual(job["artifacts"]["STDOUT"]["byte_size"], 1024 * 1024)
        error = self._read_error_artifact(job)
        self.assertTrue(error["stdout_truncated"])
        self.assertFalse(error["stderr_truncated"])
        self.assertTrue(job["latest_detail"]["stdout_truncated"])
        self.assertFalse(job["latest_detail"]["stderr_truncated"])

    def test_truncated_stderr_cannot_succeed(self) -> None:
        command = (
            "import sys;from helios_takeoff_core.agentic.manifest_worker import main;"
            "status=main();sys.stdout.flush();"
            "sys.stderr.buffer.write(b'e'*(1024*1024+1));"
            "sys.stderr.buffer.flush();raise SystemExit(status)"
        )

        outcome, job = self._run_configured_worker(command)

        self.assertEqual(outcome["state"], "ESCALATED")
        self.assertEqual(outcome["reason_code"], "OUTPUT_TRUNCATED")
        self.assertEqual(set(job["artifacts"]), {"INPUT", "STDOUT", "STDERR", "ERROR"})
        self.assertEqual(job["artifacts"]["STDERR"]["byte_size"], 1024 * 1024)
        error = self._read_error_artifact(job)
        self.assertFalse(error["stdout_truncated"])
        self.assertTrue(error["stderr_truncated"])
        self.assertFalse(job["latest_detail"]["stdout_truncated"])
        self.assertTrue(job["latest_detail"]["stderr_truncated"])


if __name__ == "__main__":
    unittest.main()
