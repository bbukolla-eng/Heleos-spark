"""Finite repository-local operator CLI for the HELIOS Build Fabric."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TypeAlias

from tools.helios_build.canonical import load_strict_json, sha256_hex
from tools.helios_build.collect import collect_task
from tools.helios_build.dispatch import dispatch_task
from tools.helios_build.doctor import build_doctor_report
from tools.helios_build.errors import (
    BuildFabricError,
    CollisionError,
    ContractError,
    StateRootError,
    TransitionError,
)
from tools.helios_build.external_sessions import (
    begin_external_session,
    import_external_handoff,
)
from tools.helios_build.integrate import record_integration
from tools.helios_build.paths import BuildPaths
from tools.helios_build.review import import_external_review, review_task
from tools.helios_build.research import (
    export_research_request,
    import_source_packets,
    prepare_task_source_bundle,
)
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.status import build_report, graph_status
from tools.helios_build.store import ContentAddressedStore
from tools.helios_build.types import AttemptOutcome, ExternalProvider


CommandHandler: TypeAlias = Callable[[BuildPaths, argparse.Namespace], dict[str, Any]]
CommandConfigurer: TypeAlias = Callable[[argparse.ArgumentParser], None]

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ROUTING_REASONS = (
    "EXTERNAL_SESSION_SELECTED",
    "LOCAL_ADAPTER_UNAVAILABLE",
    "LOCAL_ADAPTER_BLOCKED_SUCCESSOR",
)


class _UsageError(Exception):
    """An argparse failure that can be rendered as structured JSON."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


def register_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    handlers: MutableMapping[str, CommandHandler],
    name: str,
    configure: CommandConfigurer,
    handler: CommandHandler,
) -> None:
    """Register one command without permitting silent handler replacement."""
    if not isinstance(name, str) or not name:
        raise ContractError("command name must be non-empty")
    if name in handlers:
        raise ContractError(f"duplicate command registration: {name}")
    if not callable(configure) or not callable(handler):
        raise ContractError("command configure and handler values must be callable")
    command = subparsers.add_parser(name)
    configure(command)
    handlers[name] = handler


def main(argv: Sequence[str] | None = None) -> int:
    """Parse one command, invoke one handler, emit one JSON result, and stop."""
    try:
        parser, handlers = _build_parser()
        arguments = parser.parse_args(list(argv) if argv is not None else None)
        paths = BuildPaths.discover(
            Path(arguments.repo_root) if arguments.repo_root else Path.cwd(),
            Path(arguments.state_root) if arguments.state_root else None,
        )
        result = handlers[arguments.command](paths, arguments)
        _write_json(sys.stdout, _json_ready(result))
        return _result_exit_code(result)
    except (_UsageError, ContractError, StateRootError) as error:
        _write_error("CONTRACT_ERROR", str(error))
        return 2
    except (CollisionError, TransitionError) as error:
        _write_error("BLOCKED", str(error))
        return 3
    except KeyboardInterrupt:
        _write_error("OUTCOME_UNKNOWN", "operator interrupted the finite command")
        return 5
    except BuildFabricError as error:
        _write_error("FAILED", str(error))
        return 4
    except Exception as error:  # the CLI boundary never emits a traceback
        _write_error("FAILED", f"{type(error).__name__}: {error}")
        return 4


def _build_parser() -> tuple[_ArgumentParser, dict[str, CommandHandler]]:
    parser = _ArgumentParser(prog="python -m tools.helios_build")
    parser.add_argument("--repo-root")
    parser.add_argument("--state-root")
    subparsers = parser.add_subparsers(dest="command", required=True)
    handlers: dict[str, CommandHandler] = {}
    register_command(subparsers, handlers, "doctor", _configure_doctor, _handle_doctor)
    register_command(subparsers, handlers, "graph", _configure_graph, _handle_graph)
    register_command(subparsers, handlers, "task", _configure_task, _handle_task)
    register_command(subparsers, handlers, "dispatch", _configure_dispatch, _handle_dispatch)
    register_command(subparsers, handlers, "collect", _configure_task_ref, _handle_collect)
    register_command(subparsers, handlers, "review", _configure_review, _handle_review)
    register_command(subparsers, handlers, "integrate", _configure_integrate, _handle_integrate)
    register_command(
        subparsers,
        handlers,
        "external-session",
        _configure_external_session,
        _handle_external_session,
    )
    register_command(subparsers, handlers, "research", _configure_research, _handle_research)
    register_command(subparsers, handlers, "report", _configure_empty, _handle_report)
    return parser, handlers


def _configure_empty(_: argparse.ArgumentParser) -> None:
    return None


def _configure_doctor(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--adapter-config", type=Path)


def _configure_graph(parser: argparse.ArgumentParser) -> None:
    children = parser.add_subparsers(dest="graph_command", required=True)
    children.add_parser("status")


def _configure_task(parser: argparse.ArgumentParser) -> None:
    children = parser.add_subparsers(dest="task_command", required=True)
    validate = children.add_parser("validate")
    validate.add_argument("task")


def _configure_task_ref(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("task")


def _configure_dispatch(parser: argparse.ArgumentParser) -> None:
    _configure_task_ref(parser)
    parser.add_argument("--adapter-config", type=Path, required=True)


def _configure_review(parser: argparse.ArgumentParser) -> None:
    _configure_dispatch(parser)


def _configure_integrate(parser: argparse.ArgumentParser) -> None:
    _configure_task_ref(parser)
    parser.add_argument("--commit-sha")


def _configure_external_session(parser: argparse.ArgumentParser) -> None:
    children = parser.add_subparsers(dest="external_command", required=True)
    begin = children.add_parser("begin")
    begin.add_argument("task")
    begin.add_argument("--worker-profile", required=True)
    begin.add_argument(
        "--provider", choices=tuple(provider.value for provider in ExternalProvider), required=True
    )
    begin.add_argument("--session-id", required=True)
    begin.add_argument("--role", choices=("BUILDER", "REVIEWER"), required=True)
    begin.add_argument("--routing-reason", choices=_ROUTING_REASONS, required=True)

    handoff = children.add_parser("import-handoff")
    handoff.add_argument("task")
    handoff.add_argument("--assignment", required=True)
    handoff.add_argument("--handoff", type=Path, required=True)
    handoff.add_argument("--patch", type=Path, required=True)
    handoff.add_argument("--raw-evidence", type=Path, required=True)

    review = children.add_parser("import-review")
    review.add_argument("task")
    review.add_argument("--assignment", required=True)
    review.add_argument("--review", type=Path, required=True)
    review.add_argument("--raw-evidence", type=Path, required=True)


def _configure_research(parser: argparse.ArgumentParser) -> None:
    children = parser.add_subparsers(dest="research_command", required=True)

    request = children.add_parser("request")
    request_children = request.add_subparsers(dest="research_request_command", required=True)
    request_export = request_children.add_parser("export")
    request_export.add_argument("request", type=Path)

    packet = children.add_parser("packet")
    packet_children = packet.add_subparsers(dest="research_packet_command", required=True)
    packet_import = packet_children.add_parser("import")
    packet_import.add_argument("packets", type=Path, nargs="+")

    prepare = children.add_parser("prepare-task")
    prepare.add_argument("task")


def _handle_doctor(paths: BuildPaths, arguments: argparse.Namespace) -> dict[str, Any]:
    return build_doctor_report(paths, arguments.adapter_config)


def _handle_graph(paths: BuildPaths, arguments: argparse.Namespace) -> dict[str, Any]:
    if arguments.graph_command != "status":
        raise ContractError("unknown graph command")
    return graph_status(paths)


def _handle_task(paths: BuildPaths, arguments: argparse.Namespace) -> dict[str, Any]:
    if arguments.task_command != "validate":
        raise ContractError("unknown task command")
    return _validate_task_reference(paths, arguments.task)


def _handle_dispatch(paths: BuildPaths, arguments: argparse.Namespace) -> dict[str, Any]:
    return _as_result(dispatch_task(paths, _task_ref(arguments.task), arguments.adapter_config))


def _handle_collect(paths: BuildPaths, arguments: argparse.Namespace) -> dict[str, Any]:
    return _as_result(collect_task(paths, _task_ref(arguments.task)))


def _handle_review(paths: BuildPaths, arguments: argparse.Namespace) -> dict[str, Any]:
    return _as_result(review_task(paths, _task_ref(arguments.task), arguments.adapter_config))


def _handle_integrate(paths: BuildPaths, arguments: argparse.Namespace) -> dict[str, Any]:
    return _as_result(
        record_integration(paths, _task_ref(arguments.task), arguments.commit_sha)
    )


def _handle_external_session(
    paths: BuildPaths, arguments: argparse.Namespace
) -> dict[str, Any]:
    task = _task_ref(arguments.task)
    if arguments.external_command == "begin":
        return _as_result(
            begin_external_session(
                paths,
                task,
                arguments.worker_profile,
                arguments.provider,
                arguments.session_id,
                arguments.role,
                arguments.routing_reason,
            )
        )
    if arguments.external_command == "import-handoff":
        return _as_result(
            import_external_handoff(
                paths,
                task,
                arguments.assignment,
                arguments.handoff,
                arguments.patch,
                arguments.raw_evidence,
            )
        )
    if arguments.external_command == "import-review":
        return _as_result(
            import_external_review(
                paths,
                task,
                arguments.assignment,
                arguments.review,
                arguments.raw_evidence,
            )
        )
    raise ContractError("unknown external-session command")


def _handle_research(paths: BuildPaths, arguments: argparse.Namespace) -> dict[str, Any]:
    if arguments.research_command == "request":
        if arguments.research_request_command != "export":
            raise ContractError("unknown research request command")
        stored = export_research_request(paths, arguments.request)
        return {
            "kind": "tasks/research_requests",
            "path": stored.path.relative_to(paths.repo_root).as_posix(),
            "replayed": stored.replayed,
            "sha256": stored.sha256,
            "status": "EXPORTED",
        }
    if arguments.research_command == "packet":
        if arguments.research_packet_command != "import":
            raise ContractError("unknown research packet command")
        imported = import_source_packets(paths, arguments.packets)
        return {
            "import_event_sha256": imported.import_event_sha256,
            "notebook_memberships": imported.notebook_memberships,
            "packet_ids": imported.packet_ids,
            "packet_sha256s": imported.packet_sha256s,
            "replayed": imported.replayed,
            "source_ids": imported.source_ids,
            "source_sha256s": imported.source_sha256s,
            "status": "IMPORTED",
        }
    if arguments.research_command == "prepare-task":
        stored = prepare_task_source_bundle(paths, arguments.task)
        return {
            "kind": "builder_source_bundles",
            "path": stored.path.relative_to(paths.repo_root).as_posix(),
            "replayed": stored.replayed,
            "sha256": stored.sha256,
            "status": "PREPARED",
            "task_manifest_sha256": arguments.task,
        }
    raise ContractError("unknown research command")


def _handle_report(paths: BuildPaths, _: argparse.Namespace) -> dict[str, Any]:
    return build_report(paths)


def _validate_task_reference(paths: BuildPaths, task_ref: str) -> dict[str, Any]:
    tasks_root = paths.repo_root / "build_control" / "tasks"
    if _DIGEST.fullmatch(task_ref):
        source = tasks_root / "sha256" / task_ref[:2] / f"{task_ref}.json"
    else:
        source = Path(task_ref)
        if not source.is_absolute():
            source = paths.repo_root / source
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(tasks_root.resolve())
    except (OSError, ValueError) as error:
        raise ContractError("task validation path must be under build_control/tasks") from error
    if not resolved.is_file() or resolved.suffix != ".json":
        raise ContractError("task validation requires a JSON file")
    payload = load_strict_json(resolved)
    SchemaRegistry(paths.repo_root).validate(payload, "task-manifest-v1.schema.json")
    stored = ContentAddressedStore(paths.repo_root / "build_control").put_json(
        "tasks", payload
    )
    expected = sha256_hex(stored.path.read_bytes())
    if expected != stored.sha256:
        raise ContractError("published task manifest failed digest verification")
    if _DIGEST.fullmatch(task_ref) and stored.sha256 != task_ref:
        raise ContractError("task manifest content does not match its digest reference")
    return {
        "path": stored.path.relative_to(paths.repo_root).as_posix(),
        "protocol": "helios.build.task-validation/v1",
        "replayed": stored.replayed,
        "task_manifest_sha256": stored.sha256,
    }


def _task_ref(value: str) -> str | Path:
    return value if _DIGEST.fullmatch(value) else Path(value)


def _as_result(value: Any) -> dict[str, Any]:
    normalized = _json_ready(value)
    if not isinstance(normalized, dict):
        raise ContractError("command handler result must be a JSON object")
    return normalized


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    elif hasattr(value, "__dict__") and not isinstance(value, type):
        value = vars(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    raise ContractError(f"command result contains unsupported type: {type(value).__name__}")


def _result_exit_code(result: dict[str, Any]) -> int:
    state = result.get("state")
    outcome = result.get("outcome")
    verdict = result.get("verdict")
    if outcome == AttemptOutcome.OUTCOME_UNKNOWN.value:
        return 5
    if state == "BLOCKED" or result.get("availability") == "UNAVAILABLE":
        return 3
    if state == "FAILED" or outcome == AttemptOutcome.FAILED.value:
        return 4
    if verdict == "CHANGES_REQUIRED":
        return 4
    return 0


def _write_json(stream: Any, payload: Any) -> None:
    stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def _write_error(code: str, message: str) -> None:
    _write_json(sys.stderr, {"error": {"code": code, "message": message}})
