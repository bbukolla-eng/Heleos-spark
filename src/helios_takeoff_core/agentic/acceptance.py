"""Installed blank-database acceptance for the real P1A baseline auditor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..db import Database
from ..repository import TakeoffRepository
from .artifacts import ArtifactRecord, ArtifactStore
from .runner import AgentRunner
from .service import AgentExecutionService


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

DEFAULT_METADATA = {
    "project": {"code": "P1A-BASELINE", "name": "P1A Baseline Audit Metadata"},
    "actor": {"display_name": "P1A Acceptance Estimator", "actor_type": "USER"},
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


def _table_counts(database: Database) -> dict[str, int]:
    with database.connection() as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in P0_BID_IMPACTING_TABLES
        }


def _roster(database: Database) -> dict[str, str]:
    with database.connection() as connection:
        rows = connection.execute(
            """
            SELECT worker.worker_code, availability.availability
            FROM agent_workers AS worker
            JOIN agent_worker_adapter_assignments AS assignment
              ON assignment.worker_id = worker.id
            JOIN agent_worker_availability_events AS availability
              ON availability.worker_id = worker.id
             AND availability.adapter_revision_id = assignment.adapter_revision_id
            WHERE assignment.rowid = (
                SELECT MAX(current_assignment.rowid)
                FROM agent_worker_adapter_assignments AS current_assignment
                WHERE current_assignment.worker_id = worker.id
            )
              AND availability.rowid = (
                SELECT MAX(current_availability.rowid)
                FROM agent_worker_availability_events AS current_availability
                WHERE current_availability.worker_id = worker.id
                  AND current_availability.adapter_revision_id = assignment.adapter_revision_id
            )
            ORDER BY worker.worker_code
            """
        ).fetchall()
    return {row["worker_code"]: row["availability"] for row in rows}


def build_baseline_audit(work_root: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    """Create supplied baseline metadata and execute exactly one installed worker process."""
    work_root = Path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    database_path = work_root / "helios-p1a.sqlite3"
    if database_path.exists():
        raise FileExistsError(f"refusing to overwrite existing database: {database_path}")
    database = Database(database_path)
    database.initialize()
    repository = TakeoffRepository(database)
    service = AgentExecutionService(repository)
    artifact_store = ArtifactStore(work_root / "artifacts")

    project_id = repository.create_project(**metadata["project"])
    actor_id = repository.create_actor(**metadata["actor"])
    document_ids = [
        repository.register_document_revision(project_id=project_id, **document)
        for document in metadata["documents"]
    ]
    revision_set_id = repository.create_revision_set(
        project_id=project_id,
        name=metadata["revision_set_name"],
    )
    for document_id in document_ids:
        repository.include_document_revision(revision_set_id, document_id)
    repository.freeze_revision_set(revision_set_id)

    p0_before = _table_counts(database)
    service.bootstrap_roster()
    roster = _roster(database)
    submitted = service.submit_baseline_audit(
        project_id=project_id,
        revision_set_id=revision_set_id,
        requested_by_actor_id=actor_id,
        worker_code="helios-baseline-auditor",
        artifact_store=artifact_store,
    )
    runner = AgentRunner(
        service,
        artifact_store=artifact_store,
        attempt_root=work_root / "attempts",
    )
    first_run = runner.run_once()
    second_run = runner.run_once()
    if first_run is None:
        raise RuntimeError("the submitted baseline audit was not claimed")
    job = service.get_job(submitted["job_id"])
    artifacts = job["artifacts"]
    verified_artifacts: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] | None = None
    for purpose, metadata_record in artifacts.items():
        record = ArtifactRecord(
            sha256=metadata_record["sha256"],
            byte_size=metadata_record["byte_size"],
            media_type=metadata_record["media_type"],
            schema_version=metadata_record["schema_version"],
            store_key=metadata_record["store_key"],
        )
        verified = artifact_store.verify(record)
        verified_artifacts[purpose] = metadata_record | {"artifact_id": metadata_record["id"], "verified": verified}
        if purpose == "REPORT" and verified:
            report = json.loads(artifact_store.read_bytes(record))
    p0_after = _table_counts(database)

    expected_roster = {
        "codex": "UNAVAILABLE",
        "claude": "UNAVAILABLE",
        "kimi": "UNAVAILABLE",
        "grok": "UNAVAILABLE",
        "cursor": "UNAVAILABLE",
        "grokbot": "UNAVAILABLE",
        "helios-baseline-auditor": "AVAILABLE",
    }
    if first_run["state"] != "SUCCEEDED" or job["state"] != "SUCCEEDED":
        raise RuntimeError("the real baseline-audit subprocess did not succeed")
    if job["event_types"] != ["QUEUED", "LEASED", "STARTED", "SUCCEEDED"]:
        raise RuntimeError("the baseline-audit event progression is not exact")
    if second_run is not None or len(job["attempts"]) != 1:
        raise RuntimeError("run-once processed hidden or repeated work")
    if roster != expected_roster:
        raise RuntimeError("the fixed worker roster availability is not exact")
    if set(verified_artifacts) != {"INPUT", "STDOUT", "STDERR", "REPORT"}:
        raise RuntimeError("the successful attempt artifact set is incomplete")
    if not all(artifact["verified"] for artifact in verified_artifacts.values()) or report is None:
        raise RuntimeError("a successful attempt artifact failed hash verification")
    if p0_before != p0_after:
        raise RuntimeError("P1A changed a P0 bid-impacting table")

    return {
        "acceptance": "P1A_BASELINE_AUDIT_SUCCEEDED",
        "database": str(database_path),
        "artifact_root": str(work_root / "artifacts"),
        "project_id": project_id,
        "actor_id": actor_id,
        "document_revision_ids": document_ids,
        "revision_set_id": revision_set_id,
        "roster": roster,
        "execution": {
            "job_id": submitted["job_id"],
            "work_item_id": submitted["work_item_id"],
            "first_run": first_run,
            "second_run": second_run,
            "event_types": job["event_types"],
            "attempt_count": len(job["attempts"]),
            "report_artifact_id": job["report_artifact_id"],
        },
        "artifacts": verified_artifacts,
        "report": report,
        "p0_bid_impacting_tables": {
            "before": p0_before,
            "after": p0_after,
            "unchanged": p0_before == p0_after,
        },
        "scope": {
            "audited": "frozen revision-set manifest metadata only",
            "not_accepted": [
                "PDF ingestion",
                "drawing interpretation",
                "takeoff",
                "pricing",
                "external provider execution",
            ],
        },
    }
