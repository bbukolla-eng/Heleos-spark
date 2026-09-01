"""Typed graph validation and fail-closed lifecycle transitions."""

from __future__ import annotations

import os
import stat
import subprocess
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools.helios_build.canonical import canonical_json_bytes, load_strict_json, sha256_hex
from tools.helios_build.errors import CollisionError, ContractError, TransitionError
from tools.helios_build.ledger import append_event, replay_events
from tools.helios_build.ownership import OwnershipClaim, find_collisions
from tools.helios_build.paths import BuildPaths
from tools.helios_build.profiles import (
    WorkerProfile,
    load_worker_profile_bytes,
    worker_profile_sha256,
)
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.types import EdgeType, ExternalProvider, NodeState, NodeType


_ACTIVE_STATES = frozenset({NodeState.DRAFT, NodeState.READY, NodeState.DISPATCHED,
                            NodeState.RETURNED, NodeState.REVIEWED, NodeState.ACCEPTED})
_ACTIVE_WRITER_STATES = frozenset({NodeState.DISPATCHED, NodeState.RETURNED,
                                   NodeState.REVIEWED, NodeState.ACCEPTED})
_NORMAL_TRANSITIONS = {
    NodeState.DRAFT: NodeState.READY,
    NodeState.READY: NodeState.DISPATCHED,
    NodeState.DISPATCHED: NodeState.RETURNED,
    NodeState.RETURNED: NodeState.REVIEWED,
    NodeState.REVIEWED: NodeState.ACCEPTED,
    NodeState.ACCEPTED: NodeState.INTEGRATED,
}
SuccessorResolver = Callable[["GraphNode"], bool]
ReadinessResolver = Callable[["BuildGraph", "GraphNode"], bool]


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: str
    node_type: NodeType
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class GraphEdge:
    edge_type: EdgeType
    from_node_id: str
    to_node_id: str


@dataclass(frozen=True, slots=True)
class BuildGraph:
    """A validated graph and replayed state; readiness is closed by default."""

    nodes: dict[str, GraphNode]
    edges: tuple[GraphEdge, ...]
    event_states: dict[str, NodeState]
    readiness_resolver: ReadinessResolver | None = None

    @classmethod
    def load(
        cls,
        manifest: Mapping[str, object],
        events: Iterable[Mapping[str, object]],
        *,
        successor_resolver: SuccessorResolver | None = None,
        readiness_resolver: ReadinessResolver | None = None,
    ) -> "BuildGraph":
        """Load a graph without granting implicit artifact/successor authority."""
        if not isinstance(manifest, Mapping):
            raise ContractError("graph manifest must be an object")
        if manifest.get("protocol") != "helios.build.graph-manifest/v1":
            raise ContractError("graph manifest has an invalid protocol")
        raw_nodes, raw_edges = manifest.get("nodes"), manifest.get("edges")
        if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
            raise ContractError("graph manifest nodes and edges must be arrays")
        nodes: dict[str, GraphNode] = {}
        for raw_node in raw_nodes:
            if not isinstance(raw_node, Mapping):
                raise ContractError("graph node must be an object")
            node_id, digest = raw_node.get("node_id"), raw_node.get("manifest_sha256")
            try:
                node_type = NodeType(raw_node.get("node_type"))
            except ValueError as error:
                raise ContractError(f"graph node {node_id!r} has an invalid node type") from error
            if not isinstance(node_id, str) or not node_id:
                raise ContractError("graph node requires a non-empty node_id")
            if node_id in nodes:
                raise ContractError(f"graph contains duplicate node_id: {node_id}")
            if not _is_digest(digest):
                raise ContractError(f"graph node {node_id} has an invalid manifest hash")
            nodes[node_id] = GraphNode(node_id, node_type, digest)
        edges: list[GraphEdge] = []
        for raw_edge in raw_edges:
            if not isinstance(raw_edge, Mapping):
                raise ContractError("graph edge must be an object")
            from_id, to_id = raw_edge.get("from_node_id"), raw_edge.get("to_node_id")
            try:
                edge_type = EdgeType(raw_edge.get("edge_type"))
            except ValueError as error:
                raise ContractError("graph edge has an invalid edge type") from error
            if not isinstance(from_id, str) or not isinstance(to_id, str):
                raise ContractError("graph edge endpoints must be node identifiers")
            if from_id not in nodes or to_id not in nodes:
                raise ContractError(f"graph edge endpoint does not exist: {from_id} -> {to_id}")
            edge = GraphEdge(edge_type, from_id, to_id)
            if edge in edges:
                raise ContractError("graph contains a duplicate edge")
            edges.append(edge)
        base = cls(nodes, tuple(edges), {}, readiness_resolver)
        return cls(nodes, tuple(edges), _event_states(base, events, successor_resolver), readiness_resolver)

    def state(self, node_id: str) -> NodeState:
        """Return explicit state or a proven DRAFT-derived state."""
        if node_id not in self.nodes:
            raise ContractError(f"unknown graph node: {node_id}")
        explicit = self.event_states.get(node_id, NodeState.DRAFT)
        if explicit is not NodeState.DRAFT:
            return explicit
        dependencies = self.dependencies(node_id)
        if any(self.state(parent) is NodeState.BLOCKED for parent in dependencies):
            return NodeState.BLOCKED
        if not all(self.state(parent) is NodeState.INTEGRATED for parent in dependencies):
            return NodeState.DRAFT
        if self.readiness_resolver is None:
            return NodeState.DRAFT
        return NodeState.READY if self.readiness_resolver(self, self.nodes[node_id]) else NodeState.DRAFT

    def dependencies(self, node_id: str) -> tuple[str, ...]:
        if node_id not in self.nodes:
            raise ContractError(f"unknown graph node: {node_id}")
        return tuple(edge.from_node_id for edge in self.edges
                     if edge.edge_type is EdgeType.DEPENDS_ON and edge.to_node_id == node_id)


@dataclass(frozen=True, slots=True)
class _TaskRecord:
    digest: str
    payload: dict[str, object]
    path: Path


@dataclass(frozen=True, slots=True)
class _PolicySnapshot:
    """Immutable task/graph policy captured from one exact repository HEAD."""

    task_records: tuple[_TaskRecord, ...]
    graph_manifests: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class _GraphContext:
    paths: BuildPaths
    graph: BuildGraph
    node: GraphNode
    task_records: tuple[_TaskRecord, ...]
    policy_snapshot: _PolicySnapshot

    def task_for_node(self, node: GraphNode | None = None) -> dict[str, object]:
        selected = self.node if node is None else node
        if selected.node_type is not NodeType.BUILD_TASK:
            raise TransitionError(f"node {selected.node_id} is not a BuildTask")
        matches = [record for record in self.task_records if record.digest == selected.manifest_sha256]
        if len(matches) != 1:
            raise TransitionError(
                f"BuildTask {selected.node_id} does not resolve to exactly one committed task manifest"
            )
        return matches[0].payload


def validate_acyclic(graph: BuildGraph) -> None:
    """Reject a DEPENDS_ON cycle and name its concrete node path."""
    colors = {node_id: 0 for node_id in graph.nodes}
    stack: list[str] = []
    adjacent: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if edge.edge_type is EdgeType.DEPENDS_ON:
            adjacent[edge.from_node_id].append(edge.to_node_id)

    def visit(node_id: str) -> None:
        colors[node_id] = 1
        stack.append(node_id)
        for next_id in adjacent[node_id]:
            if colors[next_id] == 0:
                visit(next_id)
            elif colors[next_id] == 1:
                raise ContractError(f"dependency cycle: {' -> '.join([*stack[stack.index(next_id):], next_id])}")
        stack.pop()
        colors[node_id] = 2

    for node_id in graph.nodes:
        if colors[node_id] == 0:
            visit(node_id)


def ready_nodes(graph: BuildGraph) -> tuple[str, ...]:
    return tuple(node_id for node_id in graph.nodes if graph.state(node_id) is NodeState.READY)


def descendants(graph: BuildGraph, node_id: str) -> frozenset[str]:
    if node_id not in graph.nodes:
        raise ContractError(f"unknown graph node: {node_id}")
    adjacent: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if edge.edge_type is EdgeType.DEPENDS_ON:
            adjacent[edge.from_node_id].append(edge.to_node_id)
    found: set[str] = set()
    pending = list(adjacent[node_id])
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending.extend(adjacent[current])
    return frozenset(found)


def transition_node(
    paths: BuildPaths,
    node_id: str,
    expected_state: NodeState | str,
    new_state: NodeState | str,
    reason_code: str,
    actor_profile_id: str,
) -> str:
    """Append one repository-proven transition under the outer transition lock."""
    expected = _transition_state(expected_state, "expected state")
    target = _transition_state(new_state, "new state")
    if not isinstance(node_id, str) or not node_id:
        raise TransitionError("node_id must be a non-empty identifier")
    if not isinstance(reason_code, str) or not reason_code:
        raise TransitionError("reason_code must be a non-empty identifier")
    if not isinstance(actor_profile_id, str) or not actor_profile_id:
        raise TransitionError("actor_profile_id must be a non-empty identifier")
    with ControllerLock(paths) as lock:
        return transition_node_locked(
            lock,
            node_id,
            expected,
            target,
            reason_code,
            actor_profile_id,
        )


def transition_node_locked(
    lock: "ControllerLock",
    node_id: str,
    expected_state: NodeState | str,
    new_state: NodeState | str,
    reason_code: str,
    actor_profile_id: str,
    *,
    attempt_manifest_sha256: str | None = None,
    external_session_assignment_sha256: str | None = None,
    routing_event_sha256: str | None = None,
    policy_snapshot: _PolicySnapshot | None = None,
) -> str:
    """Validate and append one fresh lifecycle transition under the shared lock."""
    if not isinstance(lock, ControllerLock) or not lock.acquired:
        raise TransitionError("lifecycle transition requires the acquired controller lock")
    expected = _transition_state(expected_state, "expected state")
    target = _transition_state(new_state, "new state")
    if not isinstance(node_id, str) or not node_id:
        raise TransitionError("node_id must be a non-empty identifier")
    if not isinstance(reason_code, str) or not reason_code:
        raise TransitionError("reason_code must be a non-empty identifier")
    if not isinstance(actor_profile_id, str) or not actor_profile_id:
        raise TransitionError("actor_profile_id must be a non-empty identifier")
    events_path = lock.paths.repo_root / "build_control" / "graph" / "events.jsonl"
    events = replay_events(events_path)
    context = _resolve_graph_context(
        lock.paths, node_id, events, policy_snapshot=policy_snapshot
    )
    current = context.graph.event_states.get(node_id, NodeState.DRAFT)
    if current is not expected:
        raise TransitionError(f"node {node_id} is {current.value}, not expected {expected.value}")
    _validate_transition(context, current, target, actor_profile_id, events)
    _validate_event_bindings(
        lock.paths,
        context,
        current,
        target,
        attempt_manifest_sha256,
        external_session_assignment_sha256,
        routing_event_sha256,
    )
    event = {
        "protocol": "helios.build.lifecycle-event/v1",
        "node_id": node_id,
        "prior_state": current.value,
        "new_state": target.value,
        "reason_code": reason_code,
        "actor_profile_id": actor_profile_id,
        "attempt_manifest_sha256": attempt_manifest_sha256,
        "external_session_assignment_sha256": external_session_assignment_sha256,
        "routing_event_sha256": routing_event_sha256,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    }
    _validate_next_lifecycle_event(lock.paths, events, event)
    # Fixed order: controller lock first, then append_event's ledger lock.
    return append_event(events_path, event, lock.paths.state_root)


def _validate_event_bindings(
    paths: BuildPaths,
    context: _GraphContext,
    current: NodeState,
    target: NodeState,
    attempt_sha: str | None,
    external_assignment_sha: str | None,
    routing_sha: str | None,
) -> None:
    if attempt_sha is not None:
        if not _is_digest(attempt_sha):
            raise TransitionError("lifecycle attempt binding is not a digest")
        attempt_path = paths.repo_root / "build_control" / "graph" / "attempts" / "sha256" / attempt_sha[:2] / f"{attempt_sha}.json"
        try:
            attempt = load_strict_json(attempt_path)
            SchemaRegistry(paths.repo_root).validate(attempt, "attempt-manifest-v1.schema.json")
        except ContractError as error:
            raise TransitionError("lifecycle attempt binding does not resolve exactly") from error
        if sha256_hex(canonical_json_bytes(attempt)) != attempt_sha:
            raise TransitionError("lifecycle attempt binding has a digest mismatch")
        if attempt["task_manifest_sha256"] != context.node.manifest_sha256:
            raise TransitionError("lifecycle attempt belongs to a different task")
    if external_assignment_sha is not None:
        if not _is_digest(external_assignment_sha):
            raise TransitionError("lifecycle external-session assignment binding is not a digest")
        assignment_path = (
            paths.repo_root / "build_control" / "graph"
            / "external-session-assignments" / "sha256"
            / external_assignment_sha[:2] / f"{external_assignment_sha}.json"
        )
        try:
            assignment = load_strict_json(assignment_path)
            SchemaRegistry(paths.repo_root).validate(
                assignment, "external-session-assignment-v1.schema.json"
            )
        except ContractError as error:
            raise TransitionError(
                "lifecycle external-session assignment binding does not resolve exactly"
            ) from error
        if sha256_hex(canonical_json_bytes(assignment)) != external_assignment_sha:
            raise TransitionError(
                "lifecycle external-session assignment binding has a digest mismatch"
            )
        if assignment["task_manifest_sha256"] != context.node.manifest_sha256:
            raise TransitionError(
                "lifecycle external-session assignment belongs to a different task"
            )
        task = context.task_for_node()
        if assignment["base_commit_sha"] != task["base_commit_sha"]:
            raise TransitionError(
                "lifecycle external-session assignment has a different task base"
            )
        if assignment["role"] != "BUILDER":
            raise TransitionError(
                "lifecycle dispatch requires a builder external-session assignment"
            )
        designated_profile_id = task["roles"]["builder_profile_id"]
        if assignment["worker_profile_id"] != designated_profile_id:
            raise TransitionError(
                "lifecycle external-session assignment does not use the task-designated builder profile"
            )
        profile = _committed_worker_profile(paths, designated_profile_id)
        if assignment["worker_profile_sha256"] != worker_profile_sha256(profile):
            raise TransitionError(
                "lifecycle external-session assignment has a different committed builder profile digest"
            )
        if "BUILDER" not in profile.roles:
            raise TransitionError(
                "task-designated external-session profile does not permit BUILDER"
            )
        if "EXTERNAL_SESSION" not in profile.transports:
            raise TransitionError(
                "task-designated builder profile does not permit EXTERNAL_SESSION"
            )
        try:
            provider = ExternalProvider(assignment["provider"])
        except ValueError as error:
            raise TransitionError(
                "lifecycle external-session assignment provider is not allowed"
            ) from error
        if profile.worker_id.upper() != provider.value:
            raise TransitionError(
                "lifecycle external-session assignment provider does not match the committed builder profile"
            )
        if assignment["transport"] != "EXTERNAL_SESSION":
            raise TransitionError(
                "lifecycle external-session assignment transport is not EXTERNAL_SESSION"
            )
        if assignment["target_host_eligible"] is not False:
            raise TransitionError(
                "lifecycle external-session assignment cannot be target-host eligible"
            )
    if (
        context.node.node_type is NodeType.BUILD_TASK
        and current is NodeState.READY
        and target is NodeState.DISPATCHED
        and ((attempt_sha is None) == (external_assignment_sha is None))
    ):
        raise TransitionError(
            "BuildTask dispatch requires exactly one local attempt or external-session assignment binding"
        )
    if (
        context.node.node_type is NodeType.BUILD_TASK
        and target is NodeState.FAILED
        and attempt_sha is None
    ):
        raise TransitionError(
            "BuildTask terminal transition requires an exact attempt binding"
        )
    if not (
        context.node.node_type is NodeType.BUILD_TASK
        and current is NodeState.READY
        and target is NodeState.DISPATCHED
    ) and external_assignment_sha is not None:
        raise TransitionError(
            "external-session assignment bindings are valid only for BuildTask dispatch"
        )
    if routing_sha is not None:
        routes = replay_events(paths.repo_root / "build_control" / "graph" / "routing.jsonl")
        matches = [route for route in routes if route.get("event_sha256") == routing_sha]
        if len(matches) != 1:
            raise TransitionError("lifecycle routing binding does not resolve exactly")
        SchemaRegistry(paths.repo_root).validate(matches[0], "routing-event-v1.schema.json")
        if matches[0]["task_manifest_sha256"] != context.node.manifest_sha256:
            raise TransitionError("lifecycle routing binding belongs to a different task")
    if target is NodeState.SUPERSEDED and routing_sha is None:
        raise TransitionError("supersession requires an exact routing binding")
    if target is not NodeState.SUPERSEDED and routing_sha is not None:
        raise TransitionError("routing bindings are valid only for supersession")


def _validate_next_lifecycle_event(
    paths: BuildPaths,
    events: tuple[dict[str, object], ...],
    event: dict[str, object],
) -> None:
    prospective = dict(event)
    prospective["sequence"] = len(events) + 1
    prospective["previous_event_sha256"] = events[-1]["event_sha256"] if events else None
    prospective["event_sha256"] = sha256_hex(canonical_json_bytes(prospective))
    SchemaRegistry(paths.repo_root).validate(prospective, "lifecycle-event-v1.schema.json")


def _event_states(
    graph: BuildGraph,
    events: Iterable[Mapping[str, object]],
    successor_resolver: SuccessorResolver | None,
) -> dict[str, NodeState]:
    states: dict[str, NodeState] = {}
    for event in events:
        if not isinstance(event, Mapping):
            raise ContractError("lifecycle event must be an object")
        node_id = event.get("node_id")
        if not isinstance(node_id, str) or node_id not in graph.nodes:
            raise ContractError(f"lifecycle event references unknown graph node: {node_id}")
        prior = _optional_node_state(event.get("prior_state"), "lifecycle prior state")
        target = _node_state(event.get("new_state"), "lifecycle new state")
        current = states.get(node_id, NodeState.DRAFT)
        if prior is not None and prior is not current:
            raise ContractError(f"lifecycle event for {node_id} has prior state {prior.value}, expected {current.value}")
        has_successor = target is NodeState.SUPERSEDED and successor_resolver is not None and successor_resolver(graph.nodes[node_id])
        _validate_state_pair(current, target, has_successor=has_successor, error_type=ContractError)
        states[node_id] = target
    return states


def _validate_transition(
    context: _GraphContext,
    current: NodeState,
    target: NodeState,
    actor_profile_id: str,
    events: tuple[dict[str, object], ...],
) -> None:
    node = context.node
    _validate_state_pair(current, target, has_successor=_has_valid_successor(node, context.task_records), error_type=TransitionError)
    if target is NodeState.INTEGRATED and actor_profile_id != "codex-control-v1":
        raise TransitionError("only codex-control-v1 may integrate a node")
    if node.node_type is NodeType.BUILD_TASK and target in {
        NodeState.READY,
        NodeState.DISPATCHED,
        NodeState.INTEGRATED,
    }:
        context.task_for_node()  # Schema validation occurred while records were loaded.
    if current is NodeState.DRAFT and target is NodeState.READY and not _node_is_ready(context.graph, node, context):
        raise TransitionError(f"node {node.node_id} is not ready from committed prerequisites")
    if current is NodeState.READY and target is NodeState.DISPATCHED and node.node_type is NodeType.BUILD_TASK:
        candidate = OwnershipClaim.from_manifest(context.task_for_node())
        collisions = find_collisions(candidate, _active_build_task_claims(context, events))
        if collisions:
            kinds = ", ".join(sorted({collision.kind for collision in collisions}))
            raise CollisionError(f"BuildTask ownership collision for {node.node_id}: {kinds}")
        if _active_dispatched_build_tasks(context, events) >= 3:
            raise TransitionError("at most three implementation BuildTasks may be DISPATCHED")


def _validate_state_pair(
    current: NodeState,
    target: NodeState,
    *,
    has_successor: bool,
    error_type: type[ContractError] | type[TransitionError],
) -> None:
    if current in {NodeState.INTEGRATED, NodeState.SUPERSEDED}:
        raise error_type(f"{current.value} is a terminal lifecycle state")
    if target is NodeState.SUPERSEDED:
        if current not in _ACTIVE_STATES | {NodeState.BLOCKED, NodeState.FAILED}:
            raise error_type(f"cannot supersede node from {current.value}")
        if not has_successor:
            raise error_type("cannot supersede a node without a valid successor")
        return
    if target in {NodeState.BLOCKED, NodeState.FAILED} and current in _ACTIVE_STATES:
        return
    if _NORMAL_TRANSITIONS.get(current) is target:
        return
    raise error_type(f"illegal lifecycle transition: {current.value} -> {target.value}")


def _resolve_graph_context(
    paths: BuildPaths,
    node_id: str,
    events: tuple[dict[str, object], ...],
    *,
    policy_snapshot: _PolicySnapshot | None = None,
) -> _GraphContext:
    if policy_snapshot is None:
        registry = SchemaRegistry(paths.repo_root)
        policy_snapshot = _PolicySnapshot(
            _task_records(paths, registry), _graph_manifests(paths, registry)
        )
    tasks = policy_snapshot.task_records
    matching: list[dict[str, object]] = []
    for graph_manifest in policy_snapshot.graph_manifests:
        nodes = graph_manifest["nodes"]
        assert isinstance(nodes, list)
        if any(isinstance(raw_node, dict) and raw_node.get("node_id") == node_id for raw_node in nodes):
            matching.append(graph_manifest)
    if not matching:
        raise TransitionError(f"unknown committed graph node: {node_id}")
    if len(matching) != 1:
        raise TransitionError(f"ambiguous committed graph node: {node_id}")
    manifest = matching[0]
    raw_nodes = manifest["nodes"]
    assert isinstance(raw_nodes, list)
    node_ids = {raw_node["node_id"] for raw_node in raw_nodes if isinstance(raw_node, dict)}
    graph = BuildGraph.load(
        manifest,
        tuple(event for event in events if event.get("node_id") in node_ids),
        successor_resolver=lambda graph_node: _has_valid_successor(graph_node, tasks),
    )
    validate_acyclic(graph)
    node = graph.nodes[node_id]
    provisional = _GraphContext(paths, graph, node, tasks, policy_snapshot)
    graph = BuildGraph(graph.nodes, graph.edges, graph.event_states,
                       lambda loaded, graph_node: _node_is_ready(loaded, graph_node, provisional))
    return _GraphContext(paths, graph, node, tasks, policy_snapshot)


def _graph_manifests(paths: BuildPaths, registry: SchemaRegistry) -> tuple[dict[str, object], ...]:
    root = paths.repo_root / "build_control" / "graph"
    if not root.is_dir():
        raise TransitionError("committed graph directory does not exist")
    manifests: list[dict[str, object]] = []
    for graph_path in sorted(root.rglob("*.json")):
        try:
            payload = load_strict_json(graph_path)
        except ContractError:
            continue
        if payload.get("protocol") != "helios.build.graph-manifest/v1":
            continue
        try:
            registry.validate(payload, "graph-manifest-v1.schema.json")
        except ContractError as error:
            raise TransitionError(f"invalid committed graph manifest {graph_path}: {error}") from error
        manifests.append(payload)
    return tuple(manifests)


def _task_records(paths: BuildPaths, registry: SchemaRegistry) -> tuple[_TaskRecord, ...]:
    root = paths.repo_root / "build_control" / "tasks"
    if not root.is_dir():
        raise TransitionError("committed task directory does not exist")
    records: list[_TaskRecord] = []
    for task_path in sorted(root.rglob("*.json")):
        try:
            payload = load_strict_json(task_path)
        except ContractError:
            continue
        if payload.get("protocol") != "helios.build.task-manifest/v1":
            continue
        try:
            registry.validate(payload, "task-manifest-v1.schema.json")
        except ContractError as error:
            raise TransitionError(f"invalid committed task manifest {task_path}: {error}") from error
        records.append(_TaskRecord(sha256_hex(canonical_json_bytes(payload)), payload, task_path))
    return tuple(records)


def _node_is_ready(graph: BuildGraph, node: GraphNode, context: _GraphContext | None) -> bool:
    dependencies = graph.dependencies(node.node_id)
    if not all(graph.state(parent) is NodeState.INTEGRATED for parent in dependencies):
        return False
    if node.node_type is not NodeType.BUILD_TASK:
        return True
    if context is None:
        return False
    task = context.task_for_node(node)
    declared = task["dependency_node_ids"]
    records = task["required_source_record_ids"]
    packets = task["required_source_packet_ids"]
    resume = task["resume_from_checkpoint_sha256"]
    assert isinstance(declared, list) and isinstance(records, list) and isinstance(packets, list)
    if set(declared) != set(dependencies):
        return False
    for opaque_id in [*records, *packets]:
        if opaque_id not in dependencies or graph.state(opaque_id) is not NodeState.INTEGRATED:
            return False
    if resume is not None and not any(
        graph.nodes[parent].node_type is NodeType.CHECKPOINT
        and graph.nodes[parent].manifest_sha256 == resume
        and graph.state(parent) is NodeState.INTEGRATED
        for parent in dependencies
    ):
        return False
    return True


def _has_valid_successor(node: GraphNode, records: tuple[_TaskRecord, ...]) -> bool:
    return any(record.payload["supersedes_task_manifest_sha256"] == node.manifest_sha256 for record in records)


def _active_build_task_claims(context: _GraphContext, events: tuple[dict[str, object], ...]) -> tuple[OwnershipClaim, ...]:
    states = _event_state_map(events)
    claims: list[OwnershipClaim] = []
    for graph_manifest in context.policy_snapshot.graph_manifests:
        graph = BuildGraph.load(graph_manifest, ())
        for node in graph.nodes.values():
            if node.node_type is NodeType.BUILD_TASK and node.node_id != context.node.node_id and states.get(node.node_id) in _ACTIVE_WRITER_STATES:
                matches = [record for record in context.task_records if record.digest == node.manifest_sha256]
                if len(matches) != 1:
                    raise TransitionError(f"active BuildTask {node.node_id} lacks an exact committed task manifest")
                claims.append(OwnershipClaim.from_manifest(matches[0].payload))
    return tuple(claims)


def _active_dispatched_build_tasks(context: _GraphContext, events: tuple[dict[str, object], ...]) -> int:
    states = _event_state_map(events)
    return sum(
        node.node_type is NodeType.BUILD_TASK
        and node.node_id != context.node.node_id
        and states.get(node.node_id) is NodeState.DISPATCHED
        for manifest in context.policy_snapshot.graph_manifests
        for node in BuildGraph.load(manifest, ()).nodes.values()
    )


def _event_state_map(events: tuple[dict[str, object], ...]) -> dict[str, NodeState]:
    return {event["node_id"]: _node_state(event.get("new_state"), "lifecycle new state")
            for event in events if isinstance(event.get("node_id"), str)}


def _committed_worker_profile(
    paths: BuildPaths, profile_id: object
) -> WorkerProfile:
    if not isinstance(profile_id, str) or not profile_id:
        raise TransitionError("task-designated builder profile ID is invalid")
    relative = f"build_control/worker_profiles/{profile_id}.json"
    result = subprocess.run(
        ["git", "-C", str(paths.repo_root), "show", f"HEAD:{relative}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise TransitionError(
            "task-designated builder profile does not resolve exactly in HEAD"
        )
    try:
        profile = load_worker_profile_bytes(
            result.stdout, SchemaRegistry(paths.repo_root)
        )
    except ContractError as error:
        raise TransitionError(
            "task-designated committed builder profile is invalid"
        ) from error
    if profile.profile_id != profile_id:
        raise TransitionError(
            "task-designated builder profile filename/content identity mismatch"
        )
    try:
        working = (paths.repo_root / relative).read_bytes()
    except OSError as error:
        raise TransitionError(
            "task-designated builder profile working file is unavailable"
        ) from error
    if working != result.stdout:
        raise TransitionError(
            "task-designated builder profile differs from captured HEAD"
        )
    return profile


class ControllerLock:
    """Outer transition lock; never removes a lock it did not acquire."""

    def __init__(self, paths: BuildPaths) -> None:
        self.paths = paths
        self.root = paths.state_root / "locks"
        self.path = self.root / "build-control-transition.lock"
        self.acquired = False
        self.state_descriptor: int | None = None
        self.lock_descriptor: int | None = None

    def __enter__(self) -> "ControllerLock":
        from tools.helios_build.doctor import _open_private_child_directory, _open_secure_state_root

        if self.paths.state_root.exists():
            metadata = self.paths.state_root.lstat()
            current_uid = os.getuid() if hasattr(os, "getuid") else None
            if current_uid is None or stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != current_uid:
                raise TransitionError("transition state root lacks current-owner directory custody")
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                raise TransitionError("transition state root must already have mode 0700")
        self.state_descriptor = _open_secure_state_root(self.paths.state_root)
        self.lock_descriptor = _open_private_child_directory(self.state_descriptor, "locks")
        try:
            descriptor = os.open("build-control-transition.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=self.lock_descriptor)
        except FileExistsError as error:
            self._close_descriptors()
            raise TransitionError(f"transition lock already exists: {self.path}") from error
        except OSError as error:
            self._close_descriptors()
            raise TransitionError(f"cannot acquire transition lock {self.path}: {error}") from error
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        self.acquired = True
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        error: OSError | None = None
        if self.acquired and self.lock_descriptor is not None:
            try:
                os.unlink("build-control-transition.lock", dir_fd=self.lock_descriptor)
                os.fsync(self.lock_descriptor)
            except OSError as caught:
                error = caught
        self._close_descriptors()
        if error is not None:
            raise TransitionError(f"cannot release transition lock {self.path}: {error}") from error

    def _close_descriptors(self) -> None:
        for name in ("lock_descriptor", "state_descriptor"):
            descriptor = getattr(self, name)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)


# Compatibility for callers that relied on the Task 3 private name.
_TransitionLock = ControllerLock


def _transition_state(value: NodeState | str, label: str) -> NodeState:
    try:
        return NodeState(value)
    except ValueError as error:
        raise TransitionError(f"{label} is invalid: {value!r}") from error


def _node_state(value: object, label: str) -> NodeState:
    try:
        return NodeState(value)
    except ValueError as error:
        raise ContractError(f"{label} is invalid: {value!r}") from error


def _optional_node_state(value: object, label: str) -> NodeState | None:
    return None if value is None else _node_state(value, label)


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)
