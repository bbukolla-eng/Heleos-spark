"""Durable baseline-audit submission, claim, and bounded retry policy."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..contracts import require_text
from ..errors import ImmutableStateError, NotFoundError, PreconditionError, ValidationError
from ..repository import TakeoffRepository
from .artifacts import ArtifactRecord, ArtifactStore
from .contracts import WorkersConfig, validate_baseline_audit_payload, validate_worker_result
from .processes import run_bounded_process
from .repository import AgentRepository


TOKEN_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "BLOCKED", "ESCALATED", "CANCELLED"})
ROSTER = (
    ("codex", "Codex", "SUBPROCESS", "UNAVAILABLE"),
    ("claude", "Claude", "SUBPROCESS", "UNAVAILABLE"),
    ("kimi", "Kimi", "SUBPROCESS", "UNAVAILABLE"),
    ("grok", "Grok", "SUBPROCESS", "UNAVAILABLE"),
    ("cursor", "Cursor", "SUBPROCESS", "UNAVAILABLE"),
    ("grokbot", "Grokbot", "SUBPROCESS", "UNAVAILABLE"),
    ("helios-baseline-auditor", "HELIOS Baseline Auditor", "BUILTIN", "AVAILABLE"),
)
POLICY_DESCRIPTOR = {
    "max_attempts": 2,
    "non_progress": "same-failure-signature-input-adapter",
    "policy": "helios.p1a.baseline-audit-execution-policy/v1",
}
WORKFLOW_DESCRIPTOR = {
    "capability": "BASELINE_AUDIT",
    "input": "helios.p0.revision-set-manifest/v1",
    "workflow": "helios.p1a.baseline-audit/v1",
}
SUCCESS_ARTIFACT_CONTRACTS = {
    "INPUT": (
        "application/vnd.helios.revision-set-manifest+json",
        "helios.p0.revision-set-manifest/v1",
    ),
    "STDOUT": ("application/vnd.helios.worker-stdout", "helios.p1a.worker-stdout/v1"),
    "STDERR": ("application/vnd.helios.worker-stderr", "helios.p1a.worker-stderr/v1"),
    "REPORT": (
        "application/vnd.helios.baseline-audit-report+json",
        "helios.p1a.baseline-audit-report/v1",
    ),
}


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reason(value: str) -> str:
    value = require_text(value, "reason_code").upper()
    if TOKEN_PATTERN.fullmatch(value) is None:
        raise ValidationError("reason_code must be an uppercase token")
    return value


class AgentExecutionService:
    """Coordinate P1A job facts without writing P0 bid-impacting records."""

    def __init__(
        self,
        repository: TakeoffRepository,
        agent_repository: AgentRepository | None = None,
    ) -> None:
        if not isinstance(repository, TakeoffRepository):
            raise ValidationError("repository must be a TakeoffRepository")
        self.takeoff_repository = repository
        self.database = repository.database
        self.repository = agent_repository or AgentRepository(self.database)
        if self.repository.database is not self.database:
            raise ValidationError("agent repository must use the takeoff repository database")

    def bootstrap_roster(self) -> dict[str, str]:
        worker_ids: dict[str, str] = {}
        for worker_code, name, adapter_kind, availability in ROSTER:
            worker_id = self.repository.register_worker(
                worker_code=worker_code,
                name=name,
                capabilities=["BASELINE_AUDIT"],
            )
            descriptor = {
                "adapter_kind": adapter_kind,
                "configured": adapter_kind == "BUILTIN",
                "protocol": "helios.p1a.static-adapter/v1",
                "worker_code": worker_code,
            }
            adapter_revision_id = self.repository.register_adapter_revision(
                adapter_name=f"{worker_code}-adapter",
                adapter_kind=adapter_kind,
                revision="static-v1",
                configuration_sha256=_canonical_sha256(descriptor),
            )
            self.repository.assign_worker_adapter(
                worker_id=worker_id,
                adapter_revision_id=adapter_revision_id,
            )
            self.repository.seed_worker_availability(
                worker_id=worker_id,
                adapter_revision_id=adapter_revision_id,
                availability=availability,
                reason_code="BUILTIN_READY" if availability == "AVAILABLE" else "UNCONFIGURED",
            )
            worker_ids[worker_code] = worker_id
        return worker_ids

    def preflight_workers(self, workers_config: WorkersConfig) -> list[dict[str, Any]]:
        """Run each configured probe and append availability for its exact adapter revision."""
        if not isinstance(workers_config, WorkersConfig):
            raise ValidationError("workers_config must be a WorkersConfig")
        receipts: list[dict[str, Any]] = []
        for configured in workers_config.workers:
            assignment = self.repository.worker_assignment(configured.worker_code)
            if assignment["worker_code"] == "helios-baseline-auditor":
                raise ValidationError("the built-in worker cannot be replaced by workers configuration")
            adapter_revision_id = self.repository.register_adapter_revision(
                adapter_name=configured.adapter_name,
                adapter_kind=configured.adapter_kind,
                revision=configured.adapter_revision,
                configuration_sha256=configured.configuration_sha256,
            )
            self.repository.assign_worker_adapter(
                worker_id=assignment["worker_id"],
                adapter_revision_id=adapter_revision_id,
            )
            environment = {"PATH": os.defpath, "PYTHONIOENCODING": "utf-8"}
            for name in configured.environment_variables:
                if name in os.environ:
                    environment[name] = os.environ[name]
            try:
                with tempfile.TemporaryDirectory(prefix="helios-p1a-preflight-") as temporary_directory:
                    result = run_bounded_process(
                        configured.preflight_argv,
                        input_bytes=b"",
                        cwd=Path(temporary_directory),
                        env=environment,
                        timeout_seconds=configured.timeout_seconds,
                    )
            except OSError:
                availability, reason_code, returncode = "UNAVAILABLE", "PREFLIGHT_COMMAND_UNAVAILABLE", None
                stdout, stderr, stdout_truncated, stderr_truncated = b"", b"", False, False
            else:
                returncode = result.returncode
                stdout, stderr = result.stdout, result.stderr
                stdout_truncated, stderr_truncated = result.stdout_truncated, result.stderr_truncated
                if result.timed_out:
                    availability, reason_code = "UNAVAILABLE", "PREFLIGHT_TIMEOUT"
                elif result.returncode != 0:
                    availability, reason_code = "UNAVAILABLE", "PREFLIGHT_EXIT_NONZERO"
                else:
                    availability, reason_code = "AVAILABLE", "PREFLIGHT_READY"
            availability_event_id = self.repository.record_worker_availability(
                worker_id=assignment["worker_id"],
                adapter_revision_id=adapter_revision_id,
                availability=availability,
                reason_code=reason_code,
            )
            receipts.append(
                {
                    "worker_code": configured.worker_code,
                    "worker_id": assignment["worker_id"],
                    "adapter_revision_id": adapter_revision_id,
                    "adapter_name": configured.adapter_name,
                    "adapter_revision": configured.adapter_revision,
                    "configuration_sha256": configured.configuration_sha256,
                    "availability_event_id": availability_event_id,
                    "availability": availability,
                    "reason_code": reason_code,
                    "returncode": returncode,
                    "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
                    "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
                    "stdout_byte_size": len(stdout),
                    "stderr_byte_size": len(stderr),
                    "stdout_truncated": stdout_truncated,
                    "stderr_truncated": stderr_truncated,
                }
            )
        return receipts

    def submit_baseline_audit(
        self,
        *,
        project_id: str,
        revision_set_id: str,
        requested_by_actor_id: str,
        worker_code: str,
        artifact_store: ArtifactStore,
    ) -> dict[str, Any]:
        project_id = require_text(project_id, "project_id")
        revision_set_id = require_text(revision_set_id, "revision_set_id")
        requested_by_actor_id = require_text(requested_by_actor_id, "requested_by_actor_id")
        worker_code = require_text(worker_code, "worker_code")
        if not isinstance(artifact_store, ArtifactStore):
            raise ValidationError("artifact_store must be an ArtifactStore")
        manifest = self.takeoff_repository.export_revision_set_manifest(revision_set_id)
        if manifest["project_id"] != project_id:
            raise ValidationError("revision set does not belong to project")
        with self.database.connection() as connection:
            if connection.execute("SELECT 1 FROM actors WHERE id = ?", (requested_by_actor_id,)).fetchone() is None:
                raise NotFoundError("requested actor was not found")
        assignment = self.repository.worker_assignment(worker_code)
        record = artifact_store.put_json(
            manifest,
            media_type="application/vnd.helios.revision-set-manifest+json",
            schema_version="helios.p0.revision-set-manifest/v1",
        )
        input_artifact_id = self.repository.record_artifact(
            project_id=project_id,
            revision_set_id=revision_set_id,
            record=record,
        )
        created = self.repository.create_baseline_audit_job(
            project_id=project_id,
            revision_set_id=revision_set_id,
            requested_by_actor_id=requested_by_actor_id,
            worker_id=assignment["worker_id"],
            adapter_revision_id=assignment["adapter_revision_id"],
            input_artifact_id=input_artifact_id,
            policy_sha256=_canonical_sha256(POLICY_DESCRIPTOR),
            workflow_definition_sha256=_canonical_sha256(WORKFLOW_DESCRIPTOR),
            max_attempts=2,
        )
        return created | {
            "artifact": record,
            "worker_code": worker_code,
            "worker_id": assignment["worker_id"],
            "adapter_revision_id": assignment["adapter_revision_id"],
            "state": "QUEUED",
        }

    def claim_next_work_item(self) -> dict[str, Any] | None:
        with self.database.transaction() as connection:
            candidates = connection.execute(
                """
                SELECT item.*, job.requested_by_actor_id, job.policy_sha256,
                       run.workflow_definition_sha256, worker.worker_code,
                       adapter.adapter_kind, adapter.adapter_name, adapter.revision AS adapter_revision,
                       adapter.configuration_sha256,
                       artifact.sha256 AS input_sha256, artifact.byte_size AS input_byte_size,
                       artifact.media_type AS input_media_type,
                       artifact.schema_version AS input_schema_version,
                       artifact.store_key AS input_store_key,
                       (
                           SELECT event.event_type FROM agent_work_item_events AS event
                           WHERE event.work_item_id = item.id
                           ORDER BY event.rowid DESC LIMIT 1
                       ) AS state
                FROM agent_work_items AS item
                JOIN agent_jobs AS job ON job.id = item.job_id
                JOIN agent_workflow_runs AS run ON run.id = item.workflow_run_id
                JOIN agent_workers AS worker ON worker.id = item.worker_id
                JOIN agent_adapter_revisions AS adapter ON adapter.id = item.adapter_revision_id
                JOIN agent_artifacts AS artifact ON artifact.id = item.input_artifact_id
                JOIN agent_worker_adapter_assignments AS assignment
                  ON assignment.worker_id = item.worker_id
                 AND assignment.adapter_revision_id = item.adapter_revision_id
                WHERE job.requested_by_actor_id IS NOT NULL
                  AND job.policy_sha256 IS NOT NULL
                  AND run.workflow_definition_sha256 IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM agent_work_item_dependencies AS dependency
                      WHERE dependency.work_item_id = item.id
                        AND COALESCE((
                            SELECT predecessor.event_type
                            FROM agent_work_item_events AS predecessor
                            WHERE predecessor.work_item_id = dependency.depends_on_work_item_id
                            ORDER BY predecessor.rowid DESC LIMIT 1
                        ), '') <> 'SUCCEEDED'
                  )
                ORDER BY item.created_at, item.rowid
                """
            ).fetchall()
            for item in candidates:
                if item["state"] not in {"QUEUED", "RETRY_SCHEDULED"}:
                    continue
                availability = connection.execute(
                    """
                    SELECT adapter_revision_id, availability, reason_code
                    FROM agent_worker_availability_events
                    WHERE worker_id = ? AND adapter_revision_id = ?
                    ORDER BY rowid DESC LIMIT 1
                    """,
                    (item["worker_id"], item["adapter_revision_id"]),
                ).fetchone()
                if (
                    availability is None
                    or availability["adapter_revision_id"] != item["adapter_revision_id"]
                    or availability["availability"] != "AVAILABLE"
                ):
                    reason_code = (
                        availability["reason_code"]
                        if availability is not None and availability["availability"] == "UNAVAILABLE"
                        else "WORKER_UNAVAILABLE"
                    )
                    self._insert_event(
                        connection,
                        work_item_id=item["id"],
                        attempt_id=None,
                        event_type="BLOCKED",
                        reason_code=reason_code,
                        detail={"adapter_revision_id": item["adapter_revision_id"]},
                    )
                    return {
                        "attempt_id": None,
                        "attempt_number": None,
                        "work_item_id": item["id"],
                        "job_id": item["job_id"],
                        "state": "BLOCKED",
                        "reason_code": reason_code,
                    }
                attempt_number = connection.execute(
                    "SELECT COUNT(*) + 1 FROM agent_execution_attempts WHERE work_item_id = ?",
                    (item["id"],),
                ).fetchone()[0]
                if attempt_number > item["max_attempts"]:
                    self._insert_event(
                        connection,
                        work_item_id=item["id"],
                        attempt_id=None,
                        event_type="ESCALATED",
                        reason_code="ATTEMPT_LIMIT",
                        detail={"max_attempts": item["max_attempts"]},
                    )
                    return {
                        "attempt_id": None,
                        "attempt_number": attempt_number,
                        "work_item_id": item["id"],
                        "job_id": item["job_id"],
                        "state": "ESCALATED",
                        "reason_code": "ATTEMPT_LIMIT",
                    }
                attempt_id = _new_id()
                created_at = _now()
                connection.execute(
                    """
                    INSERT INTO agent_execution_attempts(
                        id, work_item_id, attempt_number, worker_id, adapter_revision_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        item["id"],
                        attempt_number,
                        item["worker_id"],
                        item["adapter_revision_id"],
                        created_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO agent_execution_attempt_artifacts(
                        execution_attempt_id, artifact_id, purpose, created_at
                    ) VALUES (?, ?, 'INPUT', ?)
                    """,
                    (attempt_id, item["input_artifact_id"], created_at),
                )
                self._insert_event(
                    connection,
                    work_item_id=item["id"],
                    attempt_id=attempt_id,
                    event_type="LEASED",
                    reason_code="CLAIMED",
                    detail={"attempt_number": attempt_number},
                )
                self._insert_event(
                    connection,
                    work_item_id=item["id"],
                    attempt_id=attempt_id,
                    event_type="STARTED",
                    reason_code="ATTEMPT_STARTED",
                    detail={"attempt_number": attempt_number},
                )
                return {
                    "attempt_id": attempt_id,
                    "attempt_number": attempt_number,
                    "work_item_id": item["id"],
                    "job_id": item["job_id"],
                    "workflow_run_id": item["workflow_run_id"],
                    "project_id": item["project_id"],
                    "revision_set_id": item["revision_set_id"],
                    "worker_id": item["worker_id"],
                    "worker_code": item["worker_code"],
                    "adapter_revision_id": item["adapter_revision_id"],
                    "adapter_kind": item["adapter_kind"],
                    "adapter_name": item["adapter_name"],
                    "adapter_revision": item["adapter_revision"],
                    "configuration_sha256": item["configuration_sha256"],
                    "input_artifact_id": item["input_artifact_id"],
                    "input_sha256": item["input_sha256"],
                    "input_byte_size": item["input_byte_size"],
                    "input_media_type": item["input_media_type"],
                    "input_schema_version": item["input_schema_version"],
                    "input_store_key": item["input_store_key"],
                    "max_attempts": item["max_attempts"],
                }
        return None

    def record_succeeded(
        self,
        *,
        work_item_id: str,
        attempt_id: str,
        stdout: ArtifactRecord,
        stderr: ArtifactRecord,
        report: ArtifactRecord,
        artifact_store: ArtifactStore | None = None,
    ) -> str:
        """Link a verified process result and append its successful terminal fact."""
        work_item_id = require_text(work_item_id, "work_item_id")
        attempt_id = require_text(attempt_id, "attempt_id")
        if not isinstance(artifact_store, ArtifactStore):
            raise ValidationError("successful result requires its artifact store for byte verification")
        with self.database.transaction() as connection:
            self._require_started_attempt(connection, work_item_id=work_item_id, attempt_id=attempt_id)
            item = connection.execute(
                """
                SELECT item.project_id, item.revision_set_id, item.input_artifact_id,
                       artifact.sha256 AS input_sha256,
                       artifact.byte_size AS input_byte_size,
                       artifact.media_type AS input_media_type,
                       artifact.schema_version AS input_schema_version,
                       artifact.store_key AS input_store_key
                FROM agent_work_items AS item
                JOIN agent_artifacts AS artifact ON artifact.id = item.input_artifact_id
                WHERE item.id = ?
                """,
                (work_item_id,),
            ).fetchone()
            if item is None:
                raise NotFoundError("work item input artifact was not found")
            input_record = ArtifactRecord(
                sha256=item["input_sha256"],
                byte_size=item["input_byte_size"],
                media_type=item["input_media_type"],
                schema_version=item["input_schema_version"],
                store_key=item["input_store_key"],
            )
            self._validate_success_artifacts(
                artifact_store=artifact_store,
                input_record=input_record,
                stdout=stdout,
                stderr=stderr,
                report=report,
                project_id=item["project_id"],
                revision_set_id=item["revision_set_id"],
            )
            self._record_attempt_artifacts(
                connection,
                work_item_id=work_item_id,
                attempt_id=attempt_id,
                records={"STDOUT": stdout, "STDERR": stderr, "REPORT": report},
            )
            self._insert_event(
                connection,
                work_item_id=work_item_id,
                attempt_id=attempt_id,
                event_type="SUCCEEDED",
                reason_code="WORKER_COMPLETED",
                detail={"report_sha256": report.sha256},
            )
        return "SUCCEEDED"

    @staticmethod
    def _validate_success_artifacts(
        *,
        artifact_store: ArtifactStore,
        input_record: ArtifactRecord,
        stdout: ArtifactRecord,
        stderr: ArtifactRecord,
        report: ArtifactRecord,
        project_id: str,
        revision_set_id: str,
    ) -> None:
        records = {"INPUT": input_record, "STDOUT": stdout, "STDERR": stderr, "REPORT": report}
        stored: dict[str, bytes] = {}
        for purpose, record in records.items():
            expected_media_type, expected_schema = SUCCESS_ARTIFACT_CONTRACTS[purpose]
            if record.media_type != expected_media_type or record.schema_version != expected_schema:
                raise ValidationError(f"{purpose} artifact does not match its media and schema contract")
            stored[purpose] = artifact_store.read_bytes(record)
        try:
            result = json.loads(stored["STDOUT"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError("stored stdout does not contain one valid worker result") from error
        validated = validate_worker_result(result, expected_input_sha256=input_record.sha256)
        validate_baseline_audit_payload(
            validated["report"]["payload"],
            expected_project_id=project_id,
            expected_revision_set_id=revision_set_id,
            expected_input_sha256=input_record.sha256,
        )
        canonical_report = json.dumps(
            validated["report"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if stored["REPORT"] != canonical_report:
            raise ValidationError("stored report artifact does not match the validated stdout result")

    def record_blocked(
        self,
        *,
        work_item_id: str,
        reason_code: str,
        stdout: ArtifactRecord | None = None,
        stderr: ArtifactRecord | None = None,
        error: ArtifactRecord | None = None,
    ) -> None:
        work_item_id = require_text(work_item_id, "work_item_id")
        reason_code = _reason(reason_code)
        with self.database.transaction() as connection:
            state = self._work_item_state(connection, work_item_id)
            if state in TERMINAL_STATES:
                raise ImmutableStateError("terminal work item cannot be blocked")
            if state in {"QUEUED", "RETRY_SCHEDULED"}:
                attempt_id = None
            elif state == "STARTED":
                started = connection.execute(
                    """
                    SELECT execution_attempt_id FROM agent_work_item_events
                    WHERE work_item_id = ? AND event_type = 'STARTED'
                    ORDER BY rowid DESC LIMIT 1
                    """,
                    (work_item_id,),
                ).fetchone()
                if started is None or started["execution_attempt_id"] is None:
                    raise PreconditionError("started work item lacks an owned execution attempt")
                attempt_id = started["execution_attempt_id"]
                if stdout is None or stderr is None or error is None:
                    raise PreconditionError("post-claim blocked outcome requires process output and error artifacts")
                self._record_attempt_artifacts(
                    connection,
                    work_item_id=work_item_id,
                    attempt_id=attempt_id,
                    records={"STDOUT": stdout, "STDERR": stderr, "ERROR": error},
                )
            else:
                raise PreconditionError("work item can only be blocked during preflight or after start")
            self._insert_event(
                connection,
                work_item_id=work_item_id,
                attempt_id=attempt_id,
                event_type="BLOCKED",
                reason_code=reason_code,
                detail=None,
            )

    def record_failure_or_retry(
        self,
        *,
        work_item_id: str,
        attempt_id: str,
        failure_signature: str,
        transient: bool,
        stdout: ArtifactRecord,
        stderr: ArtifactRecord,
        error: ArtifactRecord,
        stdout_truncated: bool = False,
        stderr_truncated: bool = False,
    ) -> str:
        work_item_id = require_text(work_item_id, "work_item_id")
        attempt_id = require_text(attempt_id, "attempt_id")
        failure_signature = require_text(failure_signature, "failure_signature")
        failure_signature_sha256 = hashlib.sha256(failure_signature.encode("utf-8")).hexdigest()
        if not isinstance(transient, bool):
            raise ValidationError("transient must be a boolean")
        if not isinstance(stdout_truncated, bool) or not isinstance(stderr_truncated, bool):
            raise ValidationError("output truncation flags must be booleans")
        with self.database.transaction() as connection:
            state = self._work_item_state(connection, work_item_id)
            if state in TERMINAL_STATES:
                raise ImmutableStateError("terminal work item cannot receive a failure decision")
            if state != "STARTED":
                raise PreconditionError("failure decisions require a started work item")
            attempt = connection.execute(
                """
                SELECT attempt.attempt_number, attempt.adapter_revision_id,
                       item.input_artifact_id, item.max_attempts
                FROM agent_execution_attempts AS attempt
                JOIN agent_work_items AS item ON item.id = attempt.work_item_id
                WHERE attempt.id = ? AND attempt.work_item_id = ?
                """,
                (attempt_id, work_item_id),
            ).fetchone()
            if attempt is None:
                raise NotFoundError("execution attempt was not found for work item")
            latest_attempt = connection.execute(
                """
                SELECT id FROM agent_execution_attempts
                WHERE work_item_id = ? ORDER BY attempt_number DESC LIMIT 1
                """,
                (work_item_id,),
            ).fetchone()
            if latest_attempt["id"] != attempt_id:
                raise PreconditionError("failure decision must name the current attempt")
            self._record_attempt_artifacts(
                connection,
                work_item_id=work_item_id,
                attempt_id=attempt_id,
                records={"STDOUT": stdout, "STDERR": stderr, "ERROR": error},
            )
            detail = {
                "adapter_revision_id": attempt["adapter_revision_id"],
                "failure_signature_sha256": failure_signature_sha256,
                "input_artifact_id": attempt["input_artifact_id"],
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }
            if not transient:
                event_type = "ESCALATED"
                reason_code = "NON_TRANSIENT_FAILURE"
            elif self._same_prior_failure(connection, work_item_id=work_item_id, attempt_id=attempt_id, detail=detail):
                event_type = "ESCALATED"
                reason_code = "NON_PROGRESS"
            elif attempt["attempt_number"] < attempt["max_attempts"]:
                event_type = "RETRY_SCHEDULED"
                reason_code = "TRANSIENT_FAILURE"
            else:
                event_type = "ESCALATED"
                reason_code = "ATTEMPT_LIMIT"
            self._insert_event(
                connection,
                work_item_id=work_item_id,
                attempt_id=attempt_id,
                event_type=event_type,
                reason_code=reason_code,
                detail=detail,
            )
        return event_type

    def reconcile_attempt(
        self,
        *,
        attempt_id: str,
        operator_actor_id: str,
        operator_reason: str,
        process_dead_asserted: bool,
        artifact_store: ArtifactStore,
    ) -> dict[str, Any]:
        """Escalate one orphaned STARTED attempt after a positive operator death assertion."""
        attempt_id = require_text(attempt_id, "attempt_id")
        operator_actor_id = require_text(operator_actor_id, "operator_actor_id")
        operator_reason = require_text(operator_reason, "operator_reason")
        if len(operator_reason) > 2000 or any(ord(character) < 32 and character not in "\t\n" for character in operator_reason):
            raise ValidationError("operator_reason is too long or contains invalid controls")
        if process_dead_asserted is not True:
            raise ValidationError("reconciliation requires a positive assertion that the worker process is dead")
        if not isinstance(artifact_store, ArtifactStore):
            raise ValidationError("artifact_store must be an ArtifactStore")
        with self.database.transaction() as connection:
            attempt = connection.execute(
                """
                SELECT attempt.work_item_id, item.job_id, item.project_id,
                       item.revision_set_id
                FROM agent_execution_attempts AS attempt
                JOIN agent_work_items AS item ON item.id = attempt.work_item_id
                WHERE attempt.id = ?
                """,
                (attempt_id,),
            ).fetchone()
            if attempt is None:
                raise NotFoundError("execution attempt was not found")
            actor = connection.execute(
                "SELECT actor_type FROM actors WHERE id = ?",
                (operator_actor_id,),
            ).fetchone()
            if actor is None:
                raise NotFoundError("operator actor was not found")
            if actor["actor_type"] != "USER":
                raise PreconditionError("reconciliation requires a human USER actor")
            self._require_started_attempt(
                connection,
                work_item_id=attempt["work_item_id"],
                attempt_id=attempt_id,
            )
            stdout = artifact_store.put_bytes(
                b"",
                media_type="application/vnd.helios.worker-stdout",
                schema_version="helios.p1a.worker-stdout/v1",
            )
            stderr = artifact_store.put_bytes(
                b"",
                media_type="application/vnd.helios.worker-stderr",
                schema_version="helios.p1a.worker-stderr/v1",
            )
            error = artifact_store.put_json(
                {
                    "protocol": "helios.p1a.execution-error/v1",
                    "attempt_id": attempt_id,
                    "reason_code": "OUTCOME_UNKNOWN",
                    "message": "Operator reconciled an orphaned attempt after asserting its process is dead.",
                    "returncode": None,
                    "stdout_sha256": stdout.sha256,
                    "stderr_sha256": stderr.sha256,
                    "outcome_unknown": True,
                    "operator_actor_id": operator_actor_id,
                    "operator_reason": operator_reason,
                    "process_dead_asserted": True,
                },
                media_type="application/vnd.helios.execution-error+json",
                schema_version="helios.p1a.execution-error/v1",
            )
            self._record_attempt_artifacts(
                connection,
                work_item_id=attempt["work_item_id"],
                attempt_id=attempt_id,
                records={"STDOUT": stdout, "STDERR": stderr, "ERROR": error},
            )
            detail = {
                "operator_actor_id": operator_actor_id,
                "operator_reason": operator_reason,
                "outcome_unknown": True,
                "process_dead_asserted": True,
            }
            self._insert_event(
                connection,
                work_item_id=attempt["work_item_id"],
                attempt_id=attempt_id,
                event_type="ESCALATED",
                reason_code="OUTCOME_UNKNOWN",
                detail=detail,
            )
        return {
            "job_id": attempt["job_id"],
            "work_item_id": attempt["work_item_id"],
            "attempt_id": attempt_id,
            "state": "ESCALATED",
            "reason_code": "OUTCOME_UNKNOWN",
            "operator_actor_id": operator_actor_id,
        }

    def _record_attempt_artifacts(
        self,
        connection: Any,
        *,
        work_item_id: str,
        attempt_id: str,
        records: dict[str, ArtifactRecord],
    ) -> None:
        item = connection.execute(
            "SELECT project_id, revision_set_id FROM agent_work_items WHERE id = ?",
            (work_item_id,),
        ).fetchone()
        if item is None:
            raise NotFoundError("work item was not found")
        created_at = _now()
        for purpose, record in records.items():
            artifact_id = self.repository.record_artifact(
                project_id=item["project_id"],
                revision_set_id=item["revision_set_id"],
                record=record,
            )
            connection.execute(
                """
                INSERT INTO agent_execution_attempt_artifacts(
                    execution_attempt_id, artifact_id, purpose, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (attempt_id, artifact_id, purpose, created_at),
            )

    def _require_started_attempt(self, connection: Any, *, work_item_id: str, attempt_id: str) -> None:
        if self._work_item_state(connection, work_item_id) != "STARTED":
            raise PreconditionError("successful result requires a started work item")
        started = connection.execute(
            """
            SELECT execution_attempt_id
            FROM agent_work_item_events
            WHERE work_item_id = ? AND event_type = 'STARTED'
            ORDER BY rowid DESC LIMIT 1
            """,
            (work_item_id,),
        ).fetchone()
        if started is None or started["execution_attempt_id"] != attempt_id:
            raise PreconditionError("successful result must name the current started attempt")

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self.repository.get_job(job_id)

    @staticmethod
    def _same_prior_failure(
        connection: Any,
        *,
        work_item_id: str,
        attempt_id: str,
        detail: dict[str, Any],
    ) -> bool:
        prior_events = connection.execute(
            """
            SELECT execution_attempt_id, detail_json
            FROM agent_work_item_events
            WHERE work_item_id = ?
              AND event_type = 'RETRY_SCHEDULED'
              AND execution_attempt_id <> ?
            ORDER BY rowid
            """,
            (work_item_id, attempt_id),
        ).fetchall()
        for event in prior_events:
            if event["detail_json"] is not None and json.loads(event["detail_json"]) == detail:
                return True
        return False

    @staticmethod
    def _insert_event(
        connection: Any,
        *,
        work_item_id: str,
        attempt_id: str | None,
        event_type: str,
        reason_code: str,
        detail: dict[str, Any] | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO agent_work_item_events(
                id, work_item_id, execution_attempt_id, event_type, created_at,
                reason_code, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id(),
                work_item_id,
                attempt_id,
                event_type,
                _now(),
                _reason(reason_code),
                AgentRepository.encode_detail(detail) if detail is not None else None,
            ),
        )

    @staticmethod
    def _work_item_state(connection: Any, work_item_id: str) -> str:
        item = connection.execute("SELECT 1 FROM agent_work_items WHERE id = ?", (work_item_id,)).fetchone()
        if item is None:
            raise NotFoundError("work item was not found")
        event = connection.execute(
            """
            SELECT event_type FROM agent_work_item_events
            WHERE work_item_id = ? ORDER BY rowid DESC LIMIT 1
            """,
            (work_item_id,),
        ).fetchone()
        if event is None:
            raise PreconditionError("work item has no lifecycle event")
        return event["event_type"]
