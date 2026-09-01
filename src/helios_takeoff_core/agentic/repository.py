"""Transactional persistence for the immutable P1A execution ledger."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from ..contracts import require_text
from ..db import Database
from ..errors import ConflictError, NotFoundError, PreconditionError, ValidationError
from .artifacts import ArtifactRecord


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WORKER_CODE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TOKEN_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _sha256(value: str, field_name: str) -> str:
    value = require_text(value, field_name)
    if SHA256_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must be a 64-character lowercase hexadecimal SHA-256 digest")
    return value


def _token(value: str, field_name: str) -> str:
    value = require_text(value, field_name).upper()
    if TOKEN_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must be an uppercase token")
    return value


class AgentRepository:
    """Write and read append-only P1A execution facts."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def register_worker(self, *, worker_code: str, name: str, capabilities: list[str]) -> str:
        worker_code = require_text(worker_code, "worker_code")
        if WORKER_CODE_PATTERN.fullmatch(worker_code) is None:
            raise ValidationError("worker_code must be lowercase hyphenated text")
        name = require_text(name, "name")
        if not isinstance(capabilities, list) or not capabilities:
            raise ValidationError("capabilities must contain at least one capability")
        normalized_capabilities = sorted({_token(capability, "capability") for capability in capabilities})
        if len(normalized_capabilities) != len(capabilities):
            raise ValidationError("capabilities must not contain duplicates")
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT id, name FROM agent_workers WHERE worker_code = ?", (worker_code,)
            ).fetchone()
            if existing is not None:
                current = [
                    row[0]
                    for row in connection.execute(
                        "SELECT capability FROM agent_worker_capabilities WHERE worker_id = ? ORDER BY capability",
                        (existing["id"],),
                    )
                ]
                if existing["name"] != name or current != normalized_capabilities:
                    raise ConflictError("worker code is already registered with different immutable metadata")
                return existing["id"]
            worker_id = _new_id()
            created_at = _now()
            connection.execute(
                "INSERT INTO agent_workers(id, name, created_at, worker_code) VALUES (?, ?, ?, ?)",
                (worker_id, name, created_at, worker_code),
            )
            for capability in normalized_capabilities:
                connection.execute(
                    "INSERT INTO agent_worker_capabilities(worker_id, capability, created_at) VALUES (?, ?, ?)",
                    (worker_id, capability, created_at),
                )
        return worker_id

    def register_adapter_revision(
        self,
        *,
        adapter_name: str,
        adapter_kind: str,
        revision: str,
        configuration_sha256: str,
    ) -> str:
        adapter_name = require_text(adapter_name, "adapter_name")
        adapter_kind = _token(adapter_kind, "adapter_kind")
        if adapter_kind not in {"BUILTIN", "SUBPROCESS"}:
            raise ValidationError("adapter_kind must be BUILTIN or SUBPROCESS")
        revision = require_text(revision, "revision")
        configuration_sha256 = _sha256(configuration_sha256, "configuration_sha256")
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT id, adapter_kind, configuration_sha256
                FROM agent_adapter_revisions
                WHERE adapter_name = ? AND revision = ?
                """,
                (adapter_name, revision),
            ).fetchone()
            if existing is not None:
                if (
                    existing["adapter_kind"] != adapter_kind
                    or existing["configuration_sha256"] != configuration_sha256
                ):
                    raise ConflictError("adapter revision is already registered with different immutable metadata")
                return existing["id"]
            adapter_revision_id = _new_id()
            connection.execute(
                """
                INSERT INTO agent_adapter_revisions(
                    id, adapter_name, adapter_kind, revision, configuration_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (adapter_revision_id, adapter_name, adapter_kind, revision, configuration_sha256, _now()),
            )
        return adapter_revision_id

    def assign_worker_adapter(self, *, worker_id: str, adapter_revision_id: str) -> None:
        worker_id = require_text(worker_id, "worker_id")
        adapter_revision_id = require_text(adapter_revision_id, "adapter_revision_id")
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM agent_workers WHERE id = ?", (worker_id,)).fetchone() is None:
                raise NotFoundError("worker was not found")
            if connection.execute(
                "SELECT 1 FROM agent_adapter_revisions WHERE id = ?", (adapter_revision_id,)
            ).fetchone() is None:
                raise NotFoundError("adapter revision was not found")
            connection.execute(
                """
                INSERT OR IGNORE INTO agent_worker_adapter_assignments(
                    worker_id, adapter_revision_id, created_at
                ) VALUES (?, ?, ?)
                """,
                (worker_id, adapter_revision_id, _now()),
            )

    def record_worker_availability(
        self,
        *,
        worker_id: str,
        adapter_revision_id: str,
        availability: str,
        reason_code: str,
    ) -> str:
        return self._record_worker_availability(
            worker_id=worker_id,
            adapter_revision_id=adapter_revision_id,
            availability=availability,
            reason_code=reason_code,
            seed_only=False,
        )

    def seed_worker_availability(
        self,
        *,
        worker_id: str,
        adapter_revision_id: str,
        availability: str,
        reason_code: str,
    ) -> str:
        """Record static roster availability only when this exact adapter has no fact."""
        return self._record_worker_availability(
            worker_id=worker_id,
            adapter_revision_id=adapter_revision_id,
            availability=availability,
            reason_code=reason_code,
            seed_only=True,
        )

    def _record_worker_availability(
        self,
        *,
        worker_id: str,
        adapter_revision_id: str,
        availability: str,
        reason_code: str,
        seed_only: bool,
    ) -> str:
        worker_id = require_text(worker_id, "worker_id")
        adapter_revision_id = require_text(adapter_revision_id, "adapter_revision_id")
        availability = _token(availability, "availability")
        if availability not in {"AVAILABLE", "UNAVAILABLE"}:
            raise ValidationError("availability must be AVAILABLE or UNAVAILABLE")
        reason_code = _token(reason_code, "reason_code")
        with self.database.transaction() as connection:
            assignment = connection.execute(
                """
                SELECT 1 FROM agent_worker_adapter_assignments
                WHERE worker_id = ? AND adapter_revision_id = ?
                """,
                (worker_id, adapter_revision_id),
            ).fetchone()
            if assignment is None:
                raise PreconditionError("worker availability requires a registered adapter assignment")
            latest = connection.execute(
                """
                SELECT id, adapter_revision_id, availability, reason_code
                FROM agent_worker_availability_events
                WHERE worker_id = ? AND adapter_revision_id = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (worker_id, adapter_revision_id),
            ).fetchone()
            if latest is not None:
                if seed_only or (latest["availability"], latest["reason_code"]) == (availability, reason_code):
                    return latest["id"]
            event_id = _new_id()
            connection.execute(
                """
                INSERT INTO agent_worker_availability_events(
                    id, worker_id, availability, created_at, adapter_revision_id, reason_code
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, worker_id, availability, _now(), adapter_revision_id, reason_code),
            )
        return event_id

    def record_artifact(self, *, project_id: str, revision_set_id: str, record: ArtifactRecord) -> str:
        project_id = require_text(project_id, "project_id")
        revision_set_id = require_text(revision_set_id, "revision_set_id")
        if not isinstance(record, ArtifactRecord):
            raise ValidationError("record must be an ArtifactRecord")
        digest = _sha256(record.sha256, "record.sha256")
        if not isinstance(record.byte_size, int) or isinstance(record.byte_size, bool) or record.byte_size < 0:
            raise ValidationError("record.byte_size must be a non-negative integer")
        media_type = require_text(record.media_type, "record.media_type")
        schema_version = require_text(record.schema_version, "record.schema_version")
        store_key = require_text(record.store_key, "record.store_key")
        expected_store_key = f"sha256/{digest[:2]}/{digest}"
        if store_key != expected_store_key:
            raise ValidationError("record.store_key must be the relative content-addressed artifact key")
        with self.database.transaction() as connection:
            baseline = connection.execute(
                "SELECT project_id, status FROM revision_sets WHERE id = ?", (revision_set_id,)
            ).fetchone()
            if baseline is None:
                raise NotFoundError("revision set was not found")
            if baseline["project_id"] != project_id:
                raise ValidationError("revision set does not belong to project")
            if baseline["status"] != "FROZEN":
                raise PreconditionError("agent artifacts require a frozen revision set")
            existing = connection.execute(
                """
                SELECT id, project_id, revision_set_id, byte_size
                FROM agent_artifacts
                WHERE project_id = ? AND revision_set_id = ?
                  AND sha256 = ? AND media_type = ? AND schema_version = ? AND store_key = ?
                """,
                (project_id, revision_set_id, digest, media_type, schema_version, store_key),
            ).fetchone()
            if existing is not None:
                if existing["byte_size"] != record.byte_size:
                    raise ConflictError("artifact content identity has inconsistent byte size")
                return existing["id"]
            artifact_id = _new_id()
            connection.execute(
                """
                INSERT INTO agent_artifacts(
                    id, project_id, revision_set_id, sha256, byte_size, media_type,
                    schema_version, store_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    project_id,
                    revision_set_id,
                    digest,
                    record.byte_size,
                    media_type,
                    schema_version,
                    store_key,
                    _now(),
                ),
            )
        return artifact_id

    def create_baseline_audit_job(
        self,
        *,
        project_id: str,
        revision_set_id: str,
        requested_by_actor_id: str,
        worker_id: str,
        adapter_revision_id: str,
        input_artifact_id: str,
        policy_sha256: str,
        workflow_definition_sha256: str,
        max_attempts: int = 2,
    ) -> dict[str, str]:
        project_id = require_text(project_id, "project_id")
        revision_set_id = require_text(revision_set_id, "revision_set_id")
        requested_by_actor_id = require_text(requested_by_actor_id, "requested_by_actor_id")
        worker_id = require_text(worker_id, "worker_id")
        adapter_revision_id = require_text(adapter_revision_id, "adapter_revision_id")
        input_artifact_id = require_text(input_artifact_id, "input_artifact_id")
        policy_sha256 = _sha256(policy_sha256, "policy_sha256")
        workflow_definition_sha256 = _sha256(workflow_definition_sha256, "workflow_definition_sha256")
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValidationError("max_attempts must be a positive integer")
        identifiers = {
            "job_id": _new_id(),
            "workflow_run_id": _new_id(),
            "work_item_id": _new_id(),
            "queued_event_id": _new_id(),
            "input_artifact_id": input_artifact_id,
        }
        created_at = _now()
        with self.database.transaction() as connection:
            baseline = connection.execute(
                "SELECT project_id, status FROM revision_sets WHERE id = ?", (revision_set_id,)
            ).fetchone()
            if baseline is None:
                raise NotFoundError("revision set was not found")
            if baseline["project_id"] != project_id:
                raise ValidationError("revision set does not belong to project")
            if baseline["status"] != "FROZEN":
                raise PreconditionError("agent jobs require a frozen revision set")
            if connection.execute("SELECT 1 FROM actors WHERE id = ?", (requested_by_actor_id,)).fetchone() is None:
                raise NotFoundError("requested actor was not found")
            artifact = connection.execute(
                "SELECT project_id, revision_set_id FROM agent_artifacts WHERE id = ?", (input_artifact_id,)
            ).fetchone()
            if artifact is None:
                raise NotFoundError("input artifact was not found")
            if (artifact["project_id"], artifact["revision_set_id"]) != (project_id, revision_set_id):
                raise ValidationError("input artifact does not match the job baseline")
            assignment = connection.execute(
                """
                SELECT 1
                FROM agent_worker_adapter_assignments AS assignment
                JOIN agent_worker_capabilities AS capability ON capability.worker_id = assignment.worker_id
                WHERE assignment.worker_id = ?
                  AND assignment.adapter_revision_id = ?
                  AND capability.capability = 'BASELINE_AUDIT'
                """,
                (worker_id, adapter_revision_id),
            ).fetchone()
            if assignment is None:
                raise PreconditionError("worker lacks the assigned baseline-audit adapter")
            connection.execute(
                """
                INSERT INTO agent_jobs(
                    id, project_id, revision_set_id, job_type, input_artifact_id,
                    created_at, requested_by_actor_id, policy_sha256
                ) VALUES (?, ?, ?, 'BASELINE_AUDIT', ?, ?, ?, ?)
                """,
                (
                    identifiers["job_id"],
                    project_id,
                    revision_set_id,
                    input_artifact_id,
                    created_at,
                    requested_by_actor_id,
                    policy_sha256,
                ),
            )
            connection.execute(
                """
                INSERT INTO agent_workflow_runs(
                    id, job_id, project_id, revision_set_id, workflow_name, created_at,
                    workflow_definition_sha256
                ) VALUES (?, ?, ?, ?, 'baseline-audit', ?, ?)
                """,
                (
                    identifiers["workflow_run_id"],
                    identifiers["job_id"],
                    project_id,
                    revision_set_id,
                    created_at,
                    workflow_definition_sha256,
                ),
            )
            connection.execute(
                """
                INSERT INTO agent_work_items(
                    id, job_id, workflow_run_id, project_id, revision_set_id, capability,
                    worker_id, adapter_revision_id, input_artifact_id, max_attempts, created_at
                ) VALUES (?, ?, ?, ?, ?, 'BASELINE_AUDIT', ?, ?, ?, ?, ?)
                """,
                (
                    identifiers["work_item_id"],
                    identifiers["job_id"],
                    identifiers["workflow_run_id"],
                    project_id,
                    revision_set_id,
                    worker_id,
                    adapter_revision_id,
                    input_artifact_id,
                    max_attempts,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO agent_work_item_events(
                    id, work_item_id, execution_attempt_id, event_type, created_at,
                    reason_code, detail_json
                ) VALUES (?, ?, NULL, 'QUEUED', ?, 'SUBMITTED', NULL)
                """,
                (identifiers["queued_event_id"], identifiers["work_item_id"], created_at),
            )
        return identifiers

    def get_job(self, job_id: str) -> dict[str, Any]:
        job_id = require_text(job_id, "job_id")
        with self.database.connection() as connection:
            job = connection.execute(
                """
                SELECT job.*, run.id AS workflow_run_id, run.workflow_definition_sha256,
                       item.id AS work_item_id, item.worker_id, item.adapter_revision_id,
                       item.max_attempts, worker.worker_code, worker.name AS worker_name,
                       adapter.adapter_name, adapter.adapter_kind,
                       adapter.revision AS adapter_revision,
                       adapter.configuration_sha256
                FROM agent_jobs AS job
                JOIN agent_workflow_runs AS run ON run.job_id = job.id
                JOIN agent_work_items AS item ON item.workflow_run_id = run.id
                JOIN agent_workers AS worker ON worker.id = item.worker_id
                JOIN agent_adapter_revisions AS adapter ON adapter.id = item.adapter_revision_id
                WHERE job.id = ?
                ORDER BY run.created_at, run.rowid, item.created_at, item.rowid
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if job is None:
                raise NotFoundError("agent job was not found")
            events = connection.execute(
                """
                SELECT id, execution_attempt_id, event_type, created_at,
                       reason_code, detail_json
                FROM agent_work_item_events
                WHERE work_item_id = ?
                ORDER BY rowid
                """,
                (job["work_item_id"],),
            ).fetchall()
            attempt_rows = connection.execute(
                """
                SELECT id, attempt_number, worker_id, adapter_revision_id, created_at
                FROM agent_execution_attempts
                WHERE work_item_id = ?
                ORDER BY attempt_number
                """,
                (job["work_item_id"],),
            ).fetchall()
            artifact_rows = connection.execute(
                """
                SELECT link.execution_attempt_id, link.purpose,
                       artifact.id, artifact.sha256, artifact.byte_size,
                       artifact.media_type, artifact.schema_version,
                       artifact.store_key, artifact.created_at
                FROM agent_execution_attempt_artifacts AS link
                JOIN agent_artifacts AS artifact ON artifact.id = link.artifact_id
                JOIN agent_execution_attempts AS attempt
                  ON attempt.id = link.execution_attempt_id
                WHERE attempt.work_item_id = ?
                ORDER BY attempt.attempt_number, link.purpose
                """,
                (job["work_item_id"],),
            ).fetchall()
        result = dict(job)
        result["event_types"] = [event["event_type"] for event in events]
        result["state"] = events[-1]["event_type"] if events else None
        result["latest_reason_code"] = events[-1]["reason_code"] if events else None
        result["latest_detail"] = json.loads(events[-1]["detail_json"]) if events and events[-1]["detail_json"] else None
        result["worker"] = {
            "id": job["worker_id"],
            "code": job["worker_code"],
            "name": job["worker_name"],
        }
        result["adapter"] = {
            "id": job["adapter_revision_id"],
            "name": job["adapter_name"],
            "kind": job["adapter_kind"],
            "revision": job["adapter_revision"],
            "configuration_sha256": job["configuration_sha256"],
        }
        result["events"] = [
            {
                "id": event["id"],
                "attempt_id": event["execution_attempt_id"],
                "type": event["event_type"],
                "created_at": event["created_at"],
                "reason_code": event["reason_code"],
                "detail": json.loads(event["detail_json"]) if event["detail_json"] else None,
            }
            for event in events
        ]
        artifacts_by_attempt: dict[str, dict[str, Any]] = {}
        for artifact in artifact_rows:
            artifacts_by_attempt.setdefault(artifact["execution_attempt_id"], {})[artifact["purpose"]] = {
                "id": artifact["id"],
                "sha256": artifact["sha256"],
                "byte_size": artifact["byte_size"],
                "media_type": artifact["media_type"],
                "schema_version": artifact["schema_version"],
                "store_key": artifact["store_key"],
                "created_at": artifact["created_at"],
            }
        result["attempts"] = [
            {
                "id": attempt["id"],
                "attempt_number": attempt["attempt_number"],
                "worker_id": attempt["worker_id"],
                "adapter_revision_id": attempt["adapter_revision_id"],
                "created_at": attempt["created_at"],
                "artifacts": artifacts_by_attempt.get(attempt["id"], {}),
            }
            for attempt in attempt_rows
        ]
        result["artifacts"] = result["attempts"][-1]["artifacts"] if result["attempts"] else {}
        report = result["artifacts"].get("REPORT")
        result["report_artifact_id"] = report["id"] if report is not None else None
        return result

    def worker_assignment(self, worker_code: str) -> dict[str, Any]:
        worker_code = require_text(worker_code, "worker_code")
        if WORKER_CODE_PATTERN.fullmatch(worker_code) is None:
            raise ValidationError("worker_code must be lowercase hyphenated text")
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT worker.id AS worker_id, worker.worker_code, assignment.adapter_revision_id,
                       adapter.adapter_kind, adapter.adapter_name, adapter.revision,
                       adapter.configuration_sha256
                FROM agent_workers AS worker
                JOIN agent_worker_adapter_assignments AS assignment ON assignment.worker_id = worker.id
                JOIN agent_adapter_revisions AS adapter ON adapter.id = assignment.adapter_revision_id
                WHERE worker.worker_code = ?
                ORDER BY assignment.created_at DESC, assignment.rowid DESC LIMIT 1
                """,
                (worker_code,),
            ).fetchone()
        if row is None:
            raise NotFoundError("worker code was not found")
        return dict(row)

    @staticmethod
    def encode_detail(detail: dict[str, Any]) -> str:
        return json.dumps(detail, sort_keys=True, separators=(",", ":"), allow_nan=False)
