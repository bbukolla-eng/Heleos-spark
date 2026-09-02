"""One-item P1A runner using private, finite trusted-local subprocess attempts."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..errors import ValidationError
from .artifacts import ArtifactRecord, ArtifactStore
from .contracts import WorkerConfig, WorkersConfig, validate_baseline_audit_payload, validate_worker_result
from .manifest_worker import WORKER_INPUT_PROTOCOL
from .processes import run_bounded_process
from .service import AgentExecutionService


class RunnerInfrastructureError(OSError):
    """Raised before claim when the configured attempt root is unusable."""


class FatalArtifactPersistenceError(RuntimeError):
    """Raised when required failure artifacts cannot be durably persisted."""


class AgentRunner:
    """Claim and finish no more than one eligible immutable work item."""

    def __init__(
        self,
        service: AgentExecutionService,
        *,
        artifact_store: ArtifactStore,
        attempt_root: Path,
        timeout_seconds: int = 60,
        workers_config: WorkersConfig | None = None,
    ) -> None:
        if not isinstance(service, AgentExecutionService):
            raise ValidationError("service must be an AgentExecutionService")
        if not isinstance(artifact_store, ArtifactStore):
            raise ValidationError("artifact_store must be an ArtifactStore")
        if not isinstance(attempt_root, Path):
            raise ValidationError("attempt_root must be a Path")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds < 1:
            raise ValidationError("timeout_seconds must be a positive integer")
        if workers_config is not None and not isinstance(workers_config, WorkersConfig):
            raise ValidationError("workers_config must be a WorkersConfig")
        self.service = service
        self.artifact_store = artifact_store
        self.attempt_root = attempt_root
        self.timeout_seconds = timeout_seconds
        self.workers_config = workers_config

    def run_once(self) -> dict[str, Any] | None:
        self._preflight_attempt_root()
        claim = self.service.claim_next_work_item()
        if claim is None:
            return None
        if claim.get("state") in {"BLOCKED", "ESCALATED"}:
            return {
                "job_id": claim["job_id"],
                "work_item_id": claim["work_item_id"],
                "attempt_id": claim["attempt_id"],
                "attempt_number": claim["attempt_number"],
                "state": claim["state"],
                "reason_code": claim["reason_code"],
                "returncode": None,
            }

        attempt_directory: Path | None = None
        stdout = b""
        stderr = b""
        stage = "ATTEMPT_DIRECTORY"
        try:
            attempt_directory = Path(
                tempfile.mkdtemp(prefix=f"{claim['attempt_id']}-", dir=self.attempt_root)
            )
            os.chmod(attempt_directory, 0o700)
            try:
                stage = "INPUT_MATERIALIZATION"
                manifest_bytes = self.artifact_store.read_bytes(self._input_record(claim))
                manifest_name = "input-manifest.json"
                self._write_private_file(attempt_directory / manifest_name, manifest_bytes)
            except (OSError, ValidationError) as error:
                return self._persist_failure(
                    claim,
                    stdout=stdout,
                    stderr=stderr,
                    reason_code="ARTIFACT_VALIDATION_ERROR",
                    message=str(error),
                    transient=False,
                )

            command = self._command_for(claim)
            if command is None:
                reason_code = (
                    "WORKER_CONFIG_MISMATCH"
                    if claim["adapter_kind"] == "SUBPROCESS" and self.workers_config is not None
                    else "WORKER_COMMAND_UNCONFIGURED"
                )
                return self._persist_blocked(
                    claim,
                    stdout=stdout,
                    stderr=stderr,
                    reason_code=reason_code,
                    message="no execution command matches the claimed adapter configuration",
                )

            envelope = {
                "protocol": WORKER_INPUT_PROTOCOL,
                "job_id": claim["job_id"],
                "work_item_id": claim["work_item_id"],
                "attempt_id": claim["attempt_id"],
                "input": {
                    "path": manifest_name,
                    "sha256": claim["input_sha256"],
                    "media_type": claim["input_media_type"],
                    "schema_version": claim["input_schema_version"],
                },
            }
            envelope_bytes = json.dumps(
                envelope,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            try:
                stage = "PROCESS_EXECUTION"
                completed = run_bounded_process(
                    command,
                    input_bytes=envelope_bytes,
                    cwd=attempt_directory,
                    env=self._worker_environment(self._configured_worker(claim)),
                    timeout_seconds=self._configured_timeout(claim),
                )
                stdout = completed.stdout
                stderr = completed.stderr
            except OSError as error:
                return self._persist_blocked(
                    claim,
                    stdout=stdout,
                    stderr=stderr,
                    reason_code="WORKER_COMMAND_UNAVAILABLE",
                    message=str(error),
                )
            if completed.stdout_truncated or completed.stderr_truncated:
                return self._persist_failure(
                    claim,
                    stdout=stdout,
                    stderr=stderr,
                    reason_code="OUTPUT_TRUNCATED",
                    message="worker output exceeded the bounded capture limit",
                    transient=False,
                    returncode=completed.returncode,
                    stdout_truncated=completed.stdout_truncated,
                    stderr_truncated=completed.stderr_truncated,
                )
            if completed.timed_out:
                return self._persist_failure(
                    claim,
                    stdout=stdout,
                    stderr=stderr,
                    reason_code="WORKER_TIMEOUT",
                    message=f"worker exceeded {self._configured_timeout(claim)} second timeout",
                    transient=True,
                )
            if completed.returncode != 0:
                return self._persist_failure(
                    claim,
                    stdout=stdout,
                    stderr=stderr,
                    reason_code="WORKER_EXIT_NONZERO",
                    message="worker exited with a nonzero status",
                    transient=False,
                    returncode=completed.returncode,
                )
            try:
                result = json.loads(stdout.decode("utf-8"))
                validated = validate_worker_result(result, expected_input_sha256=claim["input_sha256"])
                validate_baseline_audit_payload(
                    validated["report"]["payload"],
                    expected_project_id=claim["project_id"],
                    expected_revision_set_id=claim["revision_set_id"],
                    expected_input_sha256=claim["input_sha256"],
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
                return self._persist_failure(
                    claim,
                    stdout=stdout,
                    stderr=stderr,
                    reason_code="WORKER_RESULT_INVALID",
                    message=str(error),
                    transient=False,
                    returncode=completed.returncode,
                )

            stage = "SUCCESS_OUTPUT_PERSISTENCE"
            stdout_record = self.artifact_store.put_bytes(
                stdout,
                media_type="application/vnd.helios.worker-stdout",
                schema_version="helios.p1a.worker-stdout/v1",
            )
            stderr_record = self.artifact_store.put_bytes(
                stderr,
                media_type="application/vnd.helios.worker-stderr",
                schema_version="helios.p1a.worker-stderr/v1",
            )
            report_record = self.artifact_store.put_json(
                validated["report"],
                media_type="application/vnd.helios.baseline-audit-report+json",
                schema_version=validated["report"]["schema_version"],
            )
            if not self.artifact_store.verify(report_record):
                return self._persist_failure(
                    claim,
                    stdout=stdout,
                    stderr=stderr,
                    reason_code="REPORT_ARTIFACT_INVALID",
                    message="persisted report did not verify",
                    transient=False,
                    returncode=completed.returncode,
                )
            stage = "SUCCESS_RECORDING"
            state = self.service.record_succeeded(
                work_item_id=claim["work_item_id"],
                attempt_id=claim["attempt_id"],
                stdout=stdout_record,
                stderr=stderr_record,
                report=report_record,
                artifact_store=self.artifact_store,
            )
            return self._outcome(claim, state=state, reason_code="WORKER_COMPLETED", returncode=0)
        except FatalArtifactPersistenceError:
            raise
        except Exception as error:
            reason_code = (
                "OUTPUT_ARTIFACT_PERSISTENCE_FAILED"
                if stage == "SUCCESS_OUTPUT_PERSISTENCE"
                else "RUNNER_INFRASTRUCTURE_FAILURE"
            )
            return self._persist_failure(
                claim,
                stdout=stdout,
                stderr=stderr,
                reason_code=reason_code,
                message=str(error),
                transient=True,
            )
        finally:
            if attempt_directory is not None:
                shutil.rmtree(attempt_directory, ignore_errors=True)

    def _preflight_attempt_root(self) -> None:
        probe_directory: Path | None = None
        try:
            self.attempt_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.attempt_root.is_symlink() or not self.attempt_root.is_dir():
                raise OSError("attempt root must be a real directory")
            probe_directory = Path(tempfile.mkdtemp(prefix=".preflight-", dir=self.attempt_root))
            os.chmod(probe_directory, 0o700)
        except OSError as error:
            raise RunnerInfrastructureError("attempt root is not usable") from error
        finally:
            if probe_directory is not None:
                shutil.rmtree(probe_directory, ignore_errors=True)

    def _command_for(self, claim: dict[str, Any]) -> list[str] | None:
        if claim["worker_code"] == "helios-baseline-auditor":
            return [sys.executable, "-m", "helios_takeoff_core.agentic.manifest_worker"]
        configured = self._configured_worker(claim)
        if configured is not None:
            return list(configured.argv)
        return None

    @staticmethod
    def _worker_environment(configured: WorkerConfig | None = None) -> dict[str, str]:
        import_paths = []
        current_directory = Path.cwd()
        for entry in sys.path:
            if not entry:
                entry = str(current_directory)
            path = str(Path(entry).resolve())
            if path not in import_paths:
                import_paths.append(path)
        environment = {
            "PATH": os.defpath,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": os.pathsep.join(import_paths),
        }
        if configured is not None:
            for name in configured.environment_variables:
                if name in os.environ:
                    environment[name] = os.environ[name]
        return environment

    def _configured_worker(self, claim: dict[str, Any]) -> WorkerConfig | None:
        if self.workers_config is None:
            return None
        configured = self.workers_config.find_worker(claim["worker_code"])
        if configured is None:
            return None
        if (
            configured.adapter_name != claim["adapter_name"]
            or configured.adapter_kind != claim["adapter_kind"]
            or configured.adapter_revision != claim["adapter_revision"]
            or configured.configuration_sha256 != claim["configuration_sha256"]
        ):
            return None
        return configured

    def _configured_timeout(self, claim: dict[str, Any]) -> int:
        configured = self._configured_worker(claim)
        return configured.timeout_seconds if configured is not None else self.timeout_seconds

    @staticmethod
    def _write_private_file(path: Path, content: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _process_bytes(value: bytes | str | None) -> bytes:
        if value is None:
            return b""
        if isinstance(value, bytes):
            return value
        return value.encode("utf-8", errors="replace")

    @staticmethod
    def _input_record(claim: dict[str, Any]) -> ArtifactRecord:
        return ArtifactRecord(
            sha256=claim["input_sha256"],
            byte_size=claim["input_byte_size"],
            media_type=claim["input_media_type"],
            schema_version=claim["input_schema_version"],
            store_key=claim["input_store_key"],
        )

    def _persist_failure(
        self,
        claim: dict[str, Any],
        *,
        stdout: bytes,
        stderr: bytes,
        reason_code: str,
        message: str,
        transient: bool,
        returncode: int | None = None,
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
    ) -> dict[str, Any]:
        stdout_record, stderr_record, error_record = self._failure_artifacts(
            claim,
            stdout=stdout,
            stderr=stderr,
            reason_code=reason_code,
            message=message,
            returncode=returncode,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        signature = json.dumps(
            {
                "reason_code": reason_code,
                "returncode": returncode,
                "stdout_sha256": stdout_record.sha256,
                "stderr_sha256": stderr_record.sha256,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        state = self.service.record_failure_or_retry(
            work_item_id=claim["work_item_id"],
            attempt_id=claim["attempt_id"],
            failure_signature=signature,
            transient=transient,
            stdout=stdout_record,
            stderr=stderr_record,
            error=error_record,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        return self._outcome(claim, state=state, reason_code=reason_code, returncode=returncode)

    def _persist_blocked(
        self,
        claim: dict[str, Any],
        *,
        stdout: bytes,
        stderr: bytes,
        reason_code: str,
        message: str,
    ) -> dict[str, Any]:
        stdout_record, stderr_record, error_record = self._failure_artifacts(
            claim,
            stdout=stdout,
            stderr=stderr,
            reason_code=reason_code,
            message=message,
            returncode=None,
        )
        self.service.record_blocked(
            work_item_id=claim["work_item_id"],
            reason_code=reason_code,
            stdout=stdout_record,
            stderr=stderr_record,
            error=error_record,
        )
        return self._outcome(claim, state="BLOCKED", reason_code=reason_code, returncode=None)

    def _failure_artifacts(
        self,
        claim: dict[str, Any],
        *,
        stdout: bytes,
        stderr: bytes,
        reason_code: str,
        message: str,
        returncode: int | None,
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
    ) -> tuple[ArtifactRecord, ArtifactRecord, ArtifactRecord]:
        try:
            stdout_record = self.artifact_store.put_bytes(
                stdout,
                media_type="application/vnd.helios.worker-stdout",
                schema_version="helios.p1a.worker-stdout/v1",
            )
            stderr_record = self.artifact_store.put_bytes(
                stderr,
                media_type="application/vnd.helios.worker-stderr",
                schema_version="helios.p1a.worker-stderr/v1",
            )
            error_record = self.artifact_store.put_json(
                {
                    "protocol": "helios.p1a.execution-error/v1",
                    "attempt_id": claim["attempt_id"],
                    "reason_code": reason_code,
                    "message": message,
                    "returncode": returncode,
                    "stdout_sha256": stdout_record.sha256,
                    "stderr_sha256": stderr_record.sha256,
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                },
                media_type="application/vnd.helios.execution-error+json",
                schema_version="helios.p1a.execution-error/v1",
            )
        except Exception as error:
            raise FatalArtifactPersistenceError(
                "required failure artifacts could not be persisted; attempt remains STARTED"
            ) from error
        return stdout_record, stderr_record, error_record

    @staticmethod
    def _outcome(
        claim: dict[str, Any],
        *,
        state: str,
        reason_code: str,
        returncode: int | None,
    ) -> dict[str, Any]:
        return {
            "job_id": claim["job_id"],
            "work_item_id": claim["work_item_id"],
            "attempt_id": claim["attempt_id"],
            "attempt_number": claim["attempt_number"],
            "state": state,
            "reason_code": reason_code,
            "returncode": returncode,
        }
