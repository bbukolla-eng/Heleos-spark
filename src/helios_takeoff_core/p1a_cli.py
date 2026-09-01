"""JSON operator CLI for bounded P1A baseline-audit execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .agentic.artifacts import ArtifactRecord, ArtifactStore
from .agentic.contracts import load_workers_config
from .agentic.runner import AgentRunner
from .agentic.service import AgentExecutionService
from .db import Database
from .errors import HeliosTakeoffError, NotFoundError
from .repository import TakeoffRepository


def _database_service(database_path: Path) -> tuple[Database, AgentExecutionService]:
    database = Database(database_path)
    database.initialize()
    return database, AgentExecutionService(TakeoffRepository(database))


def _artifact(database: Database, artifact_id: str) -> tuple[dict[str, Any], ArtifactRecord]:
    with database.connection() as connection:
        row = connection.execute(
            "SELECT * FROM agent_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
    if row is None:
        raise NotFoundError("agent artifact was not found")
    metadata = dict(row)
    record = ArtifactRecord(
        sha256=row["sha256"],
        byte_size=row["byte_size"],
        media_type=row["media_type"],
        schema_version=row["schema_version"],
        store_key=row["store_key"],
    )
    return metadata, record


def _emit(payload: Any) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="helios-p1a")
    commands = parser.add_subparsers(dest="command", required=True)

    acceptance = commands.add_parser("acceptance", help="run installed blank-database P1A acceptance")
    acceptance.add_argument("--work-root", required=True, type=Path)

    initialize = commands.add_parser("init", help="create or upgrade the local HELIOS database")
    initialize.add_argument("--database", required=True, type=Path)

    bootstrap = commands.add_parser("bootstrap-roster", help="register the fixed P1A worker roster")
    bootstrap.add_argument("--database", required=True, type=Path)

    preflight = commands.add_parser("preflight", help="probe exact host-configured worker adapters")
    preflight.add_argument("--database", required=True, type=Path)
    preflight.add_argument("--workers-config", required=True, type=Path)

    submit = commands.add_parser("submit-baseline-audit", help="queue one frozen-baseline metadata audit")
    submit.add_argument("--database", required=True, type=Path)
    submit.add_argument("--artifact-root", required=True, type=Path)
    submit.add_argument("--project-id", required=True)
    submit.add_argument("--revision-set-id", required=True)
    submit.add_argument("--requested-by-actor-id", required=True)
    submit.add_argument("--worker-code", required=True)

    run_once = commands.add_parser("run-once", help="process at most one eligible work item")
    run_once.add_argument("--database", required=True, type=Path)
    run_once.add_argument("--artifact-root", required=True, type=Path)
    run_once.add_argument("--attempt-root", required=True, type=Path)
    run_once.add_argument("--workers-config", required=True, type=Path)

    reconcile = commands.add_parser(
        "reconcile-attempt",
        help="escalate one orphaned STARTED attempt after verified process death",
    )
    reconcile.add_argument("--database", required=True, type=Path)
    reconcile.add_argument("--artifact-root", required=True, type=Path)
    reconcile.add_argument("--attempt-id", required=True)
    reconcile.add_argument("--operator-actor-id", required=True)
    reconcile.add_argument("--reason", required=True)
    reconcile.add_argument("--assert-process-dead", action="store_true")

    job = commands.add_parser("job", help="inspect a P1A job")
    job_commands = job.add_subparsers(dest="job_command", required=True)
    job_show = job_commands.add_parser("show", help="show job state, events, and lineage")
    job_show.add_argument("--database", required=True, type=Path)
    job_show.add_argument("--job-id", required=True)

    artifact = commands.add_parser("artifact", help="inspect a P1A artifact")
    artifact_commands = artifact.add_subparsers(dest="artifact_command", required=True)
    artifact_verify = artifact_commands.add_parser("verify", help="verify stored bytes against immutable metadata")
    artifact_verify.add_argument("--database", required=True, type=Path)
    artifact_verify.add_argument("--artifact-root", required=True, type=Path)
    artifact_verify.add_argument("--artifact-id", required=True)
    return parser


def _run(arguments: argparse.Namespace) -> int:
    if arguments.command == "acceptance":
        from .agentic.acceptance import DEFAULT_METADATA, build_baseline_audit

        _emit(build_baseline_audit(arguments.work_root, DEFAULT_METADATA))
        return 0
    database, service = _database_service(arguments.database)
    if arguments.command == "init":
        with database.connection() as connection:
            schema_version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        _emit({"database": str(arguments.database), "schema_version": schema_version, "status": "INITIALIZED"})
        return 0
    if arguments.command == "bootstrap-roster":
        _emit({"status": "BOOTSTRAPPED", "workers": service.bootstrap_roster()})
        return 0
    if arguments.command == "preflight":
        receipts = service.preflight_workers(load_workers_config(arguments.workers_config))
        _emit({"status": "PREFLIGHTED", "receipts": receipts})
        return 0
    if arguments.command == "submit-baseline-audit":
        submitted = service.submit_baseline_audit(
            project_id=arguments.project_id,
            revision_set_id=arguments.revision_set_id,
            requested_by_actor_id=arguments.requested_by_actor_id,
            worker_code=arguments.worker_code,
            artifact_store=ArtifactStore(arguments.artifact_root),
        )
        response = {key: value for key, value in submitted.items() if key != "artifact"}
        response["input_artifact"] = {
            "sha256": submitted["artifact"].sha256,
            "byte_size": submitted["artifact"].byte_size,
            "media_type": submitted["artifact"].media_type,
            "schema_version": submitted["artifact"].schema_version,
            "store_key": submitted["artifact"].store_key,
        }
        _emit(response)
        return 0
    if arguments.command == "run-once":
        outcome = AgentRunner(
            service,
            artifact_store=ArtifactStore(arguments.artifact_root),
            attempt_root=arguments.attempt_root,
            workers_config=load_workers_config(arguments.workers_config),
        ).run_once()
        _emit(outcome)
        return 0
    if arguments.command == "reconcile-attempt":
        reconciled = service.reconcile_attempt(
            attempt_id=arguments.attempt_id,
            operator_actor_id=arguments.operator_actor_id,
            operator_reason=arguments.reason,
            process_dead_asserted=arguments.assert_process_dead,
            artifact_store=ArtifactStore(arguments.artifact_root),
        )
        _emit(reconciled)
        return 0
    if arguments.command == "job":
        _emit(service.get_job(arguments.job_id))
        return 0
    metadata, record = _artifact(database, arguments.artifact_id)
    valid = ArtifactStore(arguments.artifact_root).verify(record)
    _emit(
        {
            "artifact": metadata,
            "integrity_status": "VERIFIED" if valid else "TAMPERED",
            "valid": valid,
        }
    )
    return 0 if valid else 1


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return _run(arguments)
    except (HeliosTakeoffError, OSError, RuntimeError) as error:
        _emit({"error": {"code": type(error).__name__, "message": str(error)}})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
