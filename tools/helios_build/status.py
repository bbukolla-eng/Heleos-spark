"""Reconstruct conservative Build Fabric status from immutable repository truth."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from tools.helios_build.canonical import (
    canonical_json_bytes,
    load_strict_json,
    sha256_hex,
)
from tools.helios_build.errors import ContractError
from tools.helios_build.graph import BuildGraph, descendants, validate_acyclic
from tools.helios_build.ledger import replay_events
from tools.helios_build.ownership import OwnershipClaim, find_collisions
from tools.helios_build.paths import BuildPaths
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.types import NodeState, NodeType


_EVENTS = Path("build_control/graph/events.jsonl")
_ROUTING = Path("build_control/graph/routing.jsonl")
_SELF_USE_KEYS = (
    "DELIVERABLE_B_DIV23_V2",
    "DELIVERABLE_C_MODEL_REGISTRY",
    "DELIVERABLE_D_DATASET_INTAKE",
    "DELIVERABLE_E_LOCAL_SERVICE_SDK",
    "DELIVERABLE_F_CLI_ADAPTER",
)
_ACTIVE_WRITER_STATES = frozenset({
    NodeState.DISPATCHED,
    NodeState.RETURNED,
    NodeState.REVIEWED,
    NodeState.ACCEPTED,
})
_RECEIPT_SCHEMAS = {
    "helios.build.worker-handoff/v1": "worker-handoff-v1.schema.json",
    "helios.build.review-receipt/v1": "review-receipt-v1.schema.json",
    "helios.build.integration-receipt/v1": "integration-receipt-v1.schema.json",
    "helios.build.command-receipt/v1": "command-receipt-v1.schema.json",
    "helios.build.preflight-receipt/v1": "preflight-receipt-v1.schema.json",
    "helios.build.attempt-manifest/v1": "attempt-manifest-v1.schema.json",
    "helios.build.external-session-assignment/v1":
        "external-session-assignment-v1.schema.json",
}
EvidenceValidator = Callable[[BuildPaths], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class _Snapshot:
    tasks: dict[str, dict[str, Any]]
    graphs: tuple[dict[str, Any], ...]
    loaded_graphs: tuple[BuildGraph, ...]
    node_states: dict[str, NodeState]
    node_manifest_shas: dict[str, str]
    node_types: dict[str, NodeType]
    integrity_errors: tuple[str, ...]


def graph_status(paths: BuildPaths) -> dict[str, Any]:
    """Replay graph/task manifests and hash-chained ledgers into one status view."""
    snapshot = _snapshot(paths)
    counts = {state.value: 0 for state in NodeState}
    for state in snapshot.node_states.values():
        counts[state.value] += 1
    ready = sorted(
        node_id
        for node_id, state in snapshot.node_states.items()
        if state is NodeState.READY
    )
    blocked = sorted(
        node_id
        for node_id, state in snapshot.node_states.items()
        if state is NodeState.BLOCKED
    )
    blocked_descendants: dict[str, list[str]] = {}
    for node_id in blocked:
        containing = [graph for graph in snapshot.loaded_graphs if node_id in graph.nodes]
        blocked_descendants[node_id] = (
            sorted(descendants(containing[0], node_id)) if len(containing) == 1 else []
        )
    lane_count = sum(
        state is NodeState.DISPATCHED
        and snapshot.node_types.get(node_id) is NodeType.BUILD_TASK
        for node_id, state in snapshot.node_states.items()
    )
    collisions = _active_collisions(snapshot)
    return {
        "node_counts": counts,
        "node_states": {
            node_id: state.value
            for node_id, state in sorted(snapshot.node_states.items())
        },
        "ready_nodes": ready,
        "lane_count": lane_count,
        "blocked_nodes": blocked,
        "blocked_descendants": blocked_descendants,
        "collisions": collisions,
        "integrity_errors": list(snapshot.integrity_errors),
    }


def build_report(
    paths: BuildPaths,
    *,
    research_evidence_validator: EvidenceValidator | None = None,
    target_host_evidence_validator: EvidenceValidator | None = None,
) -> dict[str, Any]:
    """Return independent completion claims with the hashes that support each one."""
    snapshot = _snapshot(paths)
    receipts, receipt_errors = _receipts(paths)
    errors = [*snapshot.integrity_errors, *receipt_errors]
    integrations = _by_protocol(receipts, "helios.build.integration-receipt/v1")

    core_task_shas = {
        digest
        for digest, task in snapshot.tasks.items()
        if _is_build_fabric_core_task(task)
    }
    core_nodes = {
        node_id
        for node_id, digest in snapshot.node_manifest_shas.items()
        if digest in core_task_shas
        and snapshot.node_types.get(node_id) is NodeType.BUILD_TASK
    }
    core_integration_hashes = sorted(
        digest
        for digest, receipt in integrations.items()
        if receipt["task_manifest_sha256"] in core_task_shas
        and _valid_integration_chain(paths, receipt, receipts, snapshot.tasks)
    )
    build_fabric_complete = bool(core_nodes) and not errors and all(
        snapshot.node_states.get(node_id) is NodeState.INTEGRATED
        for node_id in core_nodes
    ) and {
        receipt["task_manifest_sha256"]
        for receipt in integrations.values()
        if _valid_integration_chain(paths, receipt, receipts, snapshot.tasks)
    }.issuperset(core_task_shas)

    production: dict[str, bool] = {}
    production_hashes: dict[str, list[str]] = {}
    for capability in _SELF_USE_KEYS:
        proof = _production_self_use_proof(
            paths, capability, snapshot, receipts, integrations
        )
        production[capability] = proof is not None and not errors
        production_hashes[capability] = [] if proof is None else sorted(proof)
    core_code_complete = all(production.values())
    supporting_core_hashes = tuple(sorted({
        item for values in production_hashes.values() for item in values
    })) if core_code_complete else ()

    research_claim, research_hashes = _external_evidence(
        paths, research_evidence_validator, "research"
    )
    target_claim, target_hashes = _external_evidence(
        paths, target_host_evidence_validator, "target-host"
    )
    research_accepted = research_claim and not errors
    target_host_accepted = target_claim and not errors
    if not research_accepted:
        research_hashes = ()
    if not target_host_accepted:
        target_hashes = ()
    cycle_complete = (
        core_code_complete and research_accepted and target_host_accepted
    )
    support: dict[str, Any] = {
        "BUILD_FABRIC_CORE_CODE_COMPLETE": (
            core_integration_hashes if build_fabric_complete else []
        ),
        "PRODUCTION_SELF_USE": production_hashes,
        "CORE_CODE_COMPLETE": list(supporting_core_hashes),
        "RESEARCH_ROUNDTRIP_ACCEPTED": list(research_hashes),
        "TARGET_HOST_ACCEPTED": list(target_hashes),
        "CYCLE_COMPLETE": (
            sorted({
                *supporting_core_hashes,
                *research_hashes,
                *target_hashes,
            })
            if cycle_complete else []
        ),
    }
    return {
        "BUILD_FABRIC_CORE_CODE_COMPLETE": build_fabric_complete,
        "PRODUCTION_SELF_USE": production,
        "CORE_CODE_COMPLETE": core_code_complete,
        "RESEARCH_ROUNDTRIP_ACCEPTED": research_accepted,
        "TARGET_HOST_ACCEPTED": target_host_accepted,
        "CYCLE_COMPLETE": cycle_complete,
        "SUPPORTING_RECEIPT_SHA256S": support,
        "INTEGRITY_ERRORS": errors,
    }


def _external_evidence(
    paths: BuildPaths,
    validator: EvidenceValidator | None,
    label: str,
) -> tuple[bool, tuple[str, ...]]:
    if validator is None:
        return False, ()
    if not callable(validator):
        raise ContractError(f"{label} evidence validator must be callable")
    try:
        evidence = validator(paths)
    except ContractError:
        raise
    except Exception as error:
        raise ContractError(f"{label} evidence validator failed: {error}") from error
    if not isinstance(evidence, Mapping) or set(evidence) != {
        "accepted", "supporting_receipt_sha256s"
    }:
        raise ContractError(
            f"{label} evidence must contain only accepted and supporting receipt hashes"
        )
    accepted = evidence["accepted"]
    digests = evidence["supporting_receipt_sha256s"]
    if not isinstance(accepted, bool):
        raise ContractError(f"{label} evidence accepted value must be boolean")
    if not isinstance(digests, (list, tuple)) or not all(
        isinstance(digest, str)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
        for digest in digests
    ):
        raise ContractError(f"{label} evidence supporting hashes are malformed")
    if len(set(digests)) != len(digests):
        raise ContractError(f"{label} evidence supporting hashes contain duplicates")
    if accepted != bool(digests):
        raise ContractError(
            f"{label} evidence acceptance must match non-empty supporting hashes"
        )
    return accepted, tuple(digests)


def _snapshot(paths: BuildPaths) -> _Snapshot:
    if not isinstance(paths, BuildPaths):
        raise ContractError("status requires BuildPaths")
    registry = SchemaRegistry(paths.repo_root)
    errors: list[str] = []
    tasks = _task_manifests(paths, registry, errors)
    graphs = _graph_manifests(paths, registry, errors)
    try:
        events = replay_events(paths.repo_root / _EVENTS)
    except ContractError as error:
        errors.append(f"lifecycle ledger: {error}")
        events = ()
    for event in events:
        try:
            registry.validate(event, "lifecycle-event-v1.schema.json")
        except ContractError as error:
            errors.append(f"lifecycle event {event.get('sequence')}: {error}")
    try:
        routes = replay_events(paths.repo_root / _ROUTING)
    except ContractError as error:
        errors.append(f"routing ledger: {error}")
        routes = ()
    for route in routes:
        try:
            registry.validate(route, "routing-event-v1.schema.json")
        except ContractError as error:
            errors.append(f"routing event {route.get('sequence')}: {error}")

    all_node_ids = [
        node["node_id"] for graph in graphs for node in graph["nodes"]
    ]
    duplicates = sorted({item for item in all_node_ids if all_node_ids.count(item) > 1})
    if duplicates:
        errors.append(f"duplicate graph node IDs: {', '.join(duplicates)}")
    known = set(all_node_ids)
    unknown_events = sorted({
        str(event.get("node_id"))
        for event in events
        if event.get("node_id") not in known
    })
    if unknown_events:
        errors.append(f"lifecycle events reference unknown nodes: {', '.join(unknown_events)}")

    loaded: list[BuildGraph] = []
    states: dict[str, NodeState] = {}
    manifest_shas: dict[str, str] = {}
    node_types: dict[str, NodeType] = {}
    successor_shas = {
        task["supersedes_task_manifest_sha256"]
        for task in tasks.values()
        if task["supersedes_task_manifest_sha256"] is not None
    }
    for raw in graphs:
        node_ids = {node["node_id"] for node in raw["nodes"]}
        subset = tuple(event for event in events if event.get("node_id") in node_ids)
        try:
            base = BuildGraph.load(
                raw,
                subset,
                successor_resolver=lambda node: node.manifest_sha256 in successor_shas,
            )
            validate_acyclic(base)
            graph = BuildGraph(
                base.nodes,
                base.edges,
                base.event_states,
                lambda current, node: _node_ready(current, node, tasks),
            )
            for node_id, node in graph.nodes.items():
                if node_id in states:
                    continue
                states[node_id] = graph.state(node_id)
                manifest_shas[node_id] = node.manifest_sha256
                node_types[node_id] = node.node_type
            loaded.append(graph)
        except ContractError as error:
            errors.append(f"graph {raw.get('graph_id')}: {error}")
    return _Snapshot(
        tasks,
        graphs,
        tuple(loaded),
        states,
        manifest_shas,
        node_types,
        tuple(dict.fromkeys(errors)),
    )


def _task_manifests(
    paths: BuildPaths, registry: SchemaRegistry, errors: list[str]
) -> dict[str, dict[str, Any]]:
    root = paths.repo_root / "build_control" / "tasks"
    if not root.is_dir():
        errors.append("task manifest directory is missing")
        return {}
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*.json")):
        try:
            payload = load_strict_json(path)
            if payload.get("protocol") != "helios.build.task-manifest/v1":
                continue
            registry.validate(payload, "task-manifest-v1.schema.json")
            content = canonical_json_bytes(payload)
            digest = sha256_hex(content)
            if path.read_bytes() != content or path.name != f"{digest}.json":
                raise ContractError("task manifest is not canonical and content-addressed")
            if digest in found:
                raise ContractError("duplicate task manifest digest")
            found[digest] = payload
        except ContractError as error:
            errors.append(f"task manifest {path}: {error}")
    return found


def _graph_manifests(
    paths: BuildPaths, registry: SchemaRegistry, errors: list[str]
) -> tuple[dict[str, Any], ...]:
    root = paths.repo_root / "build_control" / "graph"
    if not root.is_dir():
        errors.append("graph manifest directory is missing")
        return ()
    found: list[dict[str, Any]] = []
    graph_ids: set[str] = set()
    for path in sorted(root.glob("*.json")):
        try:
            payload = load_strict_json(path)
            if payload.get("protocol") != "helios.build.graph-manifest/v1":
                continue
            registry.validate(payload, "graph-manifest-v1.schema.json")
            if canonical_json_bytes(payload) != path.read_bytes():
                raise ContractError("graph manifest is not canonical")
            graph_id = payload["graph_id"]
            if graph_id in graph_ids:
                raise ContractError("duplicate graph manifest ID")
            graph_ids.add(graph_id)
            found.append(payload)
        except ContractError as error:
            errors.append(f"graph manifest {path}: {error}")
    return tuple(found)


def _node_ready(
    graph: BuildGraph,
    node: Any,
    tasks: Mapping[str, Mapping[str, Any]],
) -> bool:
    if node.node_type is not NodeType.BUILD_TASK:
        return True
    task = tasks.get(node.manifest_sha256)
    if task is None:
        return False
    dependencies = graph.dependencies(node.node_id)
    if set(task["dependency_node_ids"]) != set(dependencies):
        return False
    opaque = [*task["required_source_record_ids"], *task["required_source_packet_ids"]]
    if any(
        item not in dependencies or graph.state(item) is not NodeState.INTEGRATED
        for item in opaque
    ):
        return False
    resume = task["resume_from_checkpoint_sha256"]
    return resume is None or any(
        graph.nodes[parent].node_type is NodeType.CHECKPOINT
        and graph.nodes[parent].manifest_sha256 == resume
        and graph.state(parent) is NodeState.INTEGRATED
        for parent in dependencies
    )


def _active_collisions(snapshot: _Snapshot) -> list[dict[str, Any]]:
    active: list[OwnershipClaim] = []
    for node_id, state in sorted(snapshot.node_states.items()):
        if (
            state not in _ACTIVE_WRITER_STATES
            or snapshot.node_types.get(node_id) is not NodeType.BUILD_TASK
        ):
            continue
        task = snapshot.tasks.get(snapshot.node_manifest_shas[node_id])
        if task is not None:
            active.append(OwnershipClaim.from_manifest(task))
    collisions: list[dict[str, Any]] = []
    for index, claim in enumerate(active):
        collisions.extend(
            asdict(item) for item in find_collisions(claim, active[index + 1 :])
        )
    return sorted(
        collisions,
        key=lambda item: (
            item["candidate_task_id"], item["active_task_id"], item["kind"],
            item["candidate_resource"], item["active_resource"],
        ),
    )


def _receipts(
    paths: BuildPaths,
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    registry = SchemaRegistry(paths.repo_root)
    found: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    graph_root = paths.repo_root / "build_control" / "graph"
    paths_to_read = sorted(
        path
        for collection in ("receipts", "attempts", "external-session-assignments")
        for path in (graph_root / collection / "sha256").glob("*/*.json")
    )
    for path in paths_to_read:
        try:
            payload = load_strict_json(path)
            schema = _RECEIPT_SCHEMAS.get(payload.get("protocol"))
            if schema is None:
                continue
            content = canonical_json_bytes(payload)
            digest = sha256_hex(content)
            if path.name != f"{digest}.json" or path.read_bytes() != content:
                raise ContractError("receipt is not canonical and content-addressed")
            registry.validate(payload, schema)
            if digest in found:
                raise ContractError("duplicate receipt digest")
            found[digest] = payload
        except ContractError as error:
            errors.append(f"receipt {path}: {error}")
    return found, tuple(errors)


def _by_protocol(
    receipts: Mapping[str, Mapping[str, Any]], protocol: str
) -> dict[str, Mapping[str, Any]]:
    return {
        digest: payload
        for digest, payload in receipts.items()
        if payload.get("protocol") == protocol
    }


def _is_build_fabric_core_task(task: Mapping[str, Any]) -> bool:
    return (
        task.get("capability_id") == "BUILD_FABRIC_CORE_CODE"
        and _substantive_task(task, allow_build_fabric=True)
    )


def _valid_integration_chain(
    paths: BuildPaths,
    integration: Mapping[str, Any],
    receipts: Mapping[str, Mapping[str, Any]],
    tasks: Mapping[str, Mapping[str, Any]],
) -> bool:
    task = tasks.get(integration.get("task_manifest_sha256"))
    handoff = receipts.get(integration.get("handoff_sha256"))
    review = receipts.get(integration.get("review_receipt_sha256"))
    if task is None or handoff is None or review is None:
        return False
    if integration.get("integrator_profile_id") != "codex-control-v1":
        return False
    if (
        handoff.get("protocol") != "helios.build.worker-handoff/v1"
        or handoff.get("task_manifest_sha256") != integration["task_manifest_sha256"]
        or review.get("protocol") != "helios.build.review-receipt/v1"
        or review.get("task_manifest_sha256") != integration["task_manifest_sha256"]
        or review.get("handoff_sha256") != integration["handoff_sha256"]
        or review.get("verdict") != "ACCEPTED"
        or any(item["severity"] == "CRITICAL" for item in review.get("findings", []))
        or sorted(handoff.get("files_changed", []))
        != sorted(integration.get("changed_paths", []))
    ):
        return False
    builder_sha = _builder_profile_digest(handoff, receipts)
    if builder_sha is None or builder_sha == review.get("reviewer_profile_sha256"):
        return False
    commit = integration.get("integrated_commit_sha")
    parent = integration.get("canonical_parent_sha")
    if not _is_git_sha(commit) or not _is_git_sha(parent):
        return False
    lineage = _git(paths.repo_root, "rev-list", "--parents", "-n", "1", commit)
    if lineage.returncode != 0:
        return False
    try:
        parts = lineage.stdout.decode("ascii", errors="strict").strip().split()
    except UnicodeDecodeError:
        return False
    if parts != [commit, parent]:
        return False
    canonical_patch = _handoff_patch(paths, task, handoff)
    if canonical_patch is None:
        return False
    if not _valid_affected_review_commands(
        integration["task_manifest_sha256"],
        task,
        review,
        integration,
        receipts,
        sha256_hex(canonical_patch),
    ):
        return False
    commit_patch = _git(
        paths.repo_root, "diff", "--binary", "--full-index", parent, commit, "--"
    )
    names = _git(
        paths.repo_root,
        "diff", "--name-only", "-z", "--no-renames", parent, commit, "--",
    )
    if commit_patch.returncode != 0 or names.returncode != 0:
        return False
    try:
        changed = tuple(sorted(
            item.decode("utf-8", errors="strict")
            for item in names.stdout.rstrip(b"\x00").split(b"\x00")
            if item
        ))
    except UnicodeDecodeError:
        return False
    return (
        commit_patch.stdout == canonical_patch
        and changed == tuple(sorted(integration.get("changed_paths", [])))
    )


def _valid_affected_review_commands(
    task_sha: str,
    task: Mapping[str, Any],
    review: Mapping[str, Any],
    integration: Mapping[str, Any],
    receipts: Mapping[str, Mapping[str, Any]],
    canonical_patch_sha: str,
) -> bool:
    declared = [
        command for command in task["acceptance"]["commands"]
        if command["gate"] == "AFFECTED_INTEGRATION"
    ]
    references = review.get("command_receipt_sha256s")
    integration_references = integration.get("command_receipt_sha256s")
    if (
        not declared
        or not isinstance(references, list)
        or not isinstance(integration_references, list)
        or len(references) != len(declared)
        or len(set(references)) != len(references)
        or not set(references).issubset(integration_references)
    ):
        return False
    expected = {command["command_id"]: command for command in declared}
    if len(expected) != len(declared):
        return False
    seen: set[str] = set()
    for digest in references:
        receipt = receipts.get(digest)
        if receipt is None or receipt.get("protocol") != "helios.build.command-receipt/v1":
            return False
        command_id = receipt.get("command_id")
        command = expected.get(command_id)
        if command is None or command_id in seen:
            return False
        fields = {
            "task_manifest_sha256": task_sha,
            "correction_round": task["correction_round"],
            "patch_sha256": canonical_patch_sha,
            "command_id": command_id,
            "argv_sha256": sha256_hex(canonical_json_bytes(command["argv"])),
            "cwd_key": sha256_hex(canonical_json_bytes({"cwd": command["cwd"]})),
            "gate": "AFFECTED_INTEGRATION",
            "outcome": "PASS",
        }
        if any(receipt.get(key) != value for key, value in fields.items()):
            return False
        seen.add(command_id)
    return seen == set(expected)


def _builder_profile_digest(
    handoff: Mapping[str, Any], receipts: Mapping[str, Mapping[str, Any]]
) -> str | None:
    if handoff.get("transport") == "EXTERNAL_SESSION":
        assignment = receipts.get(handoff.get("external_session_assignment_sha256"))
        return None if assignment is None else assignment.get("worker_profile_sha256")
    attempt = receipts.get(handoff.get("attempt_manifest_sha256"))
    return None if attempt is None else attempt.get("worker_profile_sha256")


def _production_self_use_proof(
    paths: BuildPaths,
    capability: str,
    snapshot: _Snapshot,
    receipts: Mapping[str, Mapping[str, Any]],
    integrations: Mapping[str, Mapping[str, Any]],
) -> set[str] | None:
    tasks = [
        (digest, task)
        for digest, task in snapshot.tasks.items()
        if task.get("capability_id") == capability and _substantive_task(task)
    ]
    for task_sha, task in tasks:
        nodes = [
            node_id
            for node_id, digest in snapshot.node_manifest_shas.items()
            if digest == task_sha
            and snapshot.node_types.get(node_id) is NodeType.BUILD_TASK
            and snapshot.node_states.get(node_id) is NodeState.INTEGRATED
        ]
        if len(nodes) != 1 or not _has_review_integration_edges(
            snapshot.graphs, nodes[0]
        ):
            continue
        for integration_sha, integration in integrations.items():
            if integration.get("task_manifest_sha256") != task_sha:
                continue
            chain = _production_chain(
                paths,
                capability,
                task_sha,
                task,
                integration_sha,
                integration,
                receipts,
            )
            if chain is not None:
                return chain
    return None


def _production_chain(
    paths: BuildPaths,
    capability: str,
    task_sha: str,
    task: Mapping[str, Any],
    integration_sha: str,
    integration: Mapping[str, Any],
    receipts: Mapping[str, Mapping[str, Any]],
) -> set[str] | None:
    handoff_sha = integration.get("handoff_sha256")
    review_sha = integration.get("review_receipt_sha256")
    handoff = receipts.get(handoff_sha)
    review = receipts.get(review_sha)
    if handoff is None or review is None:
        return None
    if not _valid_integration_chain(paths, integration, receipts, {task_sha: task}):
        return None
    if (
        handoff.get("task_manifest_sha256") != task_sha
        or handoff.get("transport") not in {"LOCAL_ADAPTER", "EXTERNAL_SESSION"}
        or not _substantive_paths(handoff.get("files_changed", []))
        or review.get("task_manifest_sha256") != task_sha
        or review.get("handoff_sha256") != handoff_sha
        or review.get("verdict") != "ACCEPTED"
        or any(item["severity"] == "CRITICAL" for item in review.get("findings", []))
        or integration.get("integrator_profile_id") != "codex-control-v1"
        or sorted(integration.get("changed_paths", []))
        != sorted(handoff.get("files_changed", []))
    ):
        return None
    builder_sha = _resolved_builder_profile_digest(handoff, receipts)
    if builder_sha is None or builder_sha == review.get("reviewer_profile_sha256"):
        return None
    command_id = f"{capability}_INSTALLED_CODE_ACCEPTED"
    installed = [
        digest
        for digest in integration.get("command_receipt_sha256s", [])
        if (receipt := receipts.get(digest)) is not None
        and receipt.get("protocol") == "helios.build.command-receipt/v1"
        and receipt.get("task_manifest_sha256") == task_sha
        and receipt.get("gate") == "MILESTONE"
        and receipt.get("command_id") == command_id
        and receipt.get("outcome") == "PASS"
    ]
    if len(installed) != 1:
        return None
    return {handoff_sha, review_sha, integration_sha, installed[0]}


def _resolved_builder_profile_digest(
    handoff: Mapping[str, Any], receipts: Mapping[str, Mapping[str, Any]]
) -> str | None:
    reference = (
        handoff.get("external_session_assignment_sha256")
        if handoff.get("transport") == "EXTERNAL_SESSION"
        else handoff.get("attempt_manifest_sha256")
    )
    manifest = receipts.get(reference) if isinstance(reference, str) else None
    return None if manifest is None else manifest.get("worker_profile_sha256")


def _substantive_task(
    task: Mapping[str, Any], *, allow_build_fabric: bool = False
) -> bool:
    text = " ".join(
        str(task.get(field, "")).lower()
        for field in ("task_id", "capability_id", "capability")
    )
    return not any(marker in text for marker in ("fixture", "demo", "canned")) and (
        _substantive_paths(
            task.get("ownership", {}).get("files", []),
            allow_build_fabric=allow_build_fabric,
        )
        or _substantive_paths(
            task.get("ownership", {}).get("path_globs", []),
            allow_build_fabric=allow_build_fabric,
        )
    )


def _substantive_paths(paths: Any, *, allow_build_fabric: bool = False) -> bool:
    if not isinstance(paths, list):
        return False
    documentation_directories = frozenset({"doc", "docs", "documentation"})
    excluded_directories = frozenset({
        "test", "tests", "demo", "demos", "fixture", "fixtures", "canned"
    })
    substantive_source_suffixes = frozenset({
        ".graphql", ".json", ".proto", ".py", ".pyi", ".sql", ".toml",
        ".yaml", ".yml",
    })
    build_fabric_roots = {
        "tools/helios_build/": frozenset({".py"}),
        "build_control/schemas/": frozenset({".json"}),
    }
    for value in paths:
        if not isinstance(value, str):
            continue
        path = PurePosixPath(value)
        lowered_parts = tuple(part.lower() for part in path.parts)
        name = path.name.lower()
        if (
            any(part in documentation_directories for part in lowered_parts[:-1])
            or any(part in excluded_directories for part in lowered_parts[:-1])
            or name.startswith("readme")
            or name.startswith("test_")
        ):
            continue
        suffix = path.suffix.lower()
        if (
            value.startswith("src/helios_takeoff_core/")
            and suffix in substantive_source_suffixes
        ):
            return True
        if allow_build_fabric and any(
            value.startswith(root) and suffix in suffixes
            for root, suffixes in build_fabric_roots.items()
        ):
            return True
    return False


def _has_review_integration_edges(
    graphs: tuple[dict[str, Any], ...], build_node_id: str
) -> bool:
    for graph in graphs:
        types = {node["node_id"]: node["node_type"] for node in graph["nodes"]}
        if types.get(build_node_id) != "BuildTask":
            continue
        review_nodes = {
            edge["to_node_id"] for edge in graph["edges"]
            if edge["edge_type"] == "DEPENDS_ON"
            and edge["from_node_id"] == build_node_id
            and types.get(edge["to_node_id"]) == "Review"
        }
        if any(
            edge["edge_type"] == "DEPENDS_ON"
            and edge["from_node_id"] in review_nodes
            and types.get(edge["to_node_id"]) == "Integration"
            for edge in graph["edges"]
        ):
            return True
    return False


def _handoff_patch(
    paths: BuildPaths,
    task: Mapping[str, Any],
    handoff: Mapping[str, Any],
) -> bytes | None:
    if handoff.get("output_kind") == "PATCH":
        digest = handoff.get("patch_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            return None
        path = (
            paths.repo_root / "build_control" / "graph" / "patches" / "sha256"
            / digest[:2] / f"{digest}.patch"
        )
        try:
            content = path.read_bytes()
        except OSError:
            return None
        return content if sha256_hex(content) == digest else None
    commit = handoff.get("commit_sha")
    base = task.get("base_commit_sha")
    if not _is_git_sha(commit) or not _is_git_sha(base):
        return None
    result = _git(
        paths.repo_root, "diff", "--binary", "--full-index", base, commit, "--"
    )
    return result.stdout if result.returncode == 0 else None


def _git(repo: Path, *argv: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *argv],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _is_git_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(
        character in "0123456789abcdef" for character in value
    )
