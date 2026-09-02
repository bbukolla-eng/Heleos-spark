"""Honest identified external-session assignments and evidence imports."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from tools.helios_build._external_cas import put_private_bytes, read_private_bytes
from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build.collect import (
    HandoffReceipt,
    _apply_patch_in_worktree,
    _handoff_receipt,
    _handoffs_for_proof,
    _head_sha,
    _is_digest,
    _iter_cas_json,
    _load_cas_json,
    _load_committed_profile,
    _load_committed_task,
    _node_state,
    _strict_json_bytes,
    _task_node_id,
    _validate_scope_and_impacts,
    _validate_source_lineage,
    _git_show,
    _git_tree,
    _git,
)
from tools.helios_build.errors import CollisionError, ContractError, TransitionError
from tools.helios_build.graph import (
    BuildGraph,
    ControllerLock,
    transition_node_locked,
    validate_acyclic,
)
from tools.helios_build.ledger import replay_events
from tools.helios_build.ownership import OwnershipClaim, find_collisions
from tools.helios_build.paths import BuildPaths
from tools.helios_build.profiles import worker_profile_sha256
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore, StoredObject
from tools.helios_build.types import ExternalProvider, HandoffTransport, NodeState
from tools.helios_build.worktrees import create_detached_worktree


_ASSIGNMENTS = "external-session-assignments"
_EVENTS = Path("build_control/graph/events.jsonl")
_ROUTING = Path("build_control/graph/routing.jsonl")
_ACTIVE_WRITERS = frozenset({
    NodeState.DISPATCHED,
    NodeState.RETURNED,
    NodeState.REVIEWED,
    NodeState.ACCEPTED,
})
_MAX_UNTRUSTED_JSON = 1024 * 1024
_MAX_PATCH_BYTES = 64 * 1024 * 1024
_MAX_RAW_EVIDENCE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ExternalSessionAssignment:
    sha256: str
    path: Path
    replayed: bool
    assignment_id: str
    task_manifest_sha256: str
    base_commit_sha: str
    role: str
    worker_profile_id: str
    worker_profile_sha256: str
    provider: ExternalProvider
    session_id: str
    transport: HandoffTransport
    routing_action: str
    routing_reason: str
    created_at: str
    created_by_profile_id: str
    target_host_eligible: bool

    @property
    def attempt_manifest_sha256(self) -> None:
        return None

    @property
    def preflight_receipt_sha256(self) -> None:
        return None

    @property
    def adapter_configuration_sha256(self) -> None:
        return None


def begin_external_session(
    paths: BuildPaths,
    task_ref: str | Path,
    worker_profile_id: str,
    provider: str | ExternalProvider,
    session_id: str,
    role: str,
    routing_reason: str,
) -> ExternalSessionAssignment:
    """Freeze one external identity before builder or reviewer work starts."""
    if not isinstance(paths, BuildPaths):
        raise ContractError("external session begin requires BuildPaths")
    try:
        provider_value = ExternalProvider(provider)
    except ValueError as error:
        raise ContractError(f"external-session provider is not allowed: {provider!r}") from error
    if role not in {"BUILDER", "REVIEWER"}:
        raise ContractError("external-session role must be BUILDER or REVIEWER")
    task_sha, task, _ = _load_committed_task(paths, task_ref)
    _validate_task_base(paths, task["base_commit_sha"])
    node_id = _task_node_id(paths, task_sha)
    profile, _ = _load_committed_profile(paths, worker_profile_id)
    designated = task["roles"][
        "builder_profile_id" if role == "BUILDER" else "reviewer_profile_id"
    ]
    if worker_profile_id != designated:
        label = "builder" if role == "BUILDER" else "designated reviewer"
        raise ContractError(f"external session does not use the task {label} profile")
    if role not in profile.roles:
        raise ContractError(f"worker profile {worker_profile_id} does not permit role {role}")
    if "EXTERNAL_SESSION" not in profile.transports:
        raise ContractError(f"worker profile {worker_profile_id} does not permit EXTERNAL_SESSION")
    if profile.worker_id.upper() != provider_value.value:
        raise ContractError("external-session provider does not match the worker profile identity")
    action = _routing_action(role, routing_reason)
    profile_sha = worker_profile_sha256(profile)
    with ControllerLock(paths) as lock:
        existing = _assignments_for(paths, task_sha, role)
        if existing:
            if len(existing) != 1:
                raise ContractError("task role has multiple immutable external assignments")
            stored, payload = existing[0]
            expected = {
                "worker_profile_id": worker_profile_id,
                "worker_profile_sha256": profile_sha,
                "provider": provider_value.value,
                "session_id": session_id,
                "routing_action": action,
                "routing_reason": routing_reason,
            }
            if any(payload.get(key) != value for key, value in expected.items()):
                raise ContractError("task role already has a differing external assignment")
            if role == "BUILDER":
                events = replay_events(paths.repo_root / _EVENTS)
                current = _node_state(events, node_id)
                if current is NodeState.READY:
                    _validate_builder_route(paths, task_sha, task, routing_reason)
                    _validate_ready_dispatch(paths, task_sha, task, node_id, events)
                    transition_node_locked(
                        lock,
                        node_id,
                        NodeState.READY,
                        NodeState.DISPATCHED,
                        "EXTERNAL_SESSION_DISPATCHED",
                        "codex-control-v1",
                        external_session_assignment_sha256=stored.sha256,
                    )
                elif current not in {
                    NodeState.DISPATCHED, NodeState.RETURNED, NodeState.REVIEWED,
                    NodeState.ACCEPTED, NodeState.INTEGRATED, NodeState.SUPERSEDED,
                }:
                    raise TransitionError(
                        f"external builder assignment replay found {current.value}"
                    )
            return _assignment(stored, payload, replayed=True)
        events = replay_events(paths.repo_root / _EVENTS)
        current = _node_state(events, node_id)
        if role == "BUILDER":
            if current is not NodeState.READY:
                raise TransitionError(
                    f"external builder requires READY, found {current.value}"
                )
            _validate_builder_route(paths, task_sha, task, routing_reason)
            _validate_ready_dispatch(paths, task_sha, task, node_id, events)
        else:
            if current is not NodeState.RETURNED:
                raise TransitionError(
                    f"external reviewer requires RETURNED, found {current.value}"
                )
            if routing_reason != "EXTERNAL_SESSION_SELECTED":
                raise ContractError("external reviewer permits only EXTERNAL_SESSION_SELECTED")
            _validate_reviewer_independence(
                paths, task, worker_profile_id, profile_sha, session_id
            )
        created_at = _utc_now()
        payload: dict[str, Any] = {
            "protocol": "helios.build.external-session-assignment/v1",
            "assignment_id": f"external-{task_sha}-{role.lower()}",
            "task_manifest_sha256": task_sha,
            "base_commit_sha": task["base_commit_sha"],
            "role": role,
            "worker_profile_id": worker_profile_id,
            "worker_profile_sha256": profile_sha,
            "provider": provider_value.value,
            "session_id": session_id,
            "transport": "EXTERNAL_SESSION",
            "routing_action": action,
            "routing_reason": routing_reason,
            "created_at": created_at,
            "created_by_profile_id": "codex-control-v1",
            "target_host_eligible": False,
        }
        SchemaRegistry(paths.repo_root).validate(
            payload, "external-session-assignment-v1.schema.json"
        )
        stored = ContentAddressedStore(
            paths.repo_root / "build_control" / "graph"
        ).put_json(_ASSIGNMENTS, payload)
        if role == "BUILDER":
            transition_node_locked(
                lock,
                node_id,
                NodeState.READY,
                NodeState.DISPATCHED,
                "EXTERNAL_SESSION_DISPATCHED",
                "codex-control-v1",
                external_session_assignment_sha256=stored.sha256,
            )
        return _assignment(stored, payload)


def import_external_handoff(
    paths: BuildPaths,
    task_ref: str | Path,
    assignment_ref: str | Path,
    handoff_path: Path,
    patch_path: Path,
    raw_evidence_path: Path,
) -> HandoffReceipt:
    """Verify untrusted provider bytes, derive Git truth, and return one handoff."""
    if not all(isinstance(value, Path) for value in (handoff_path, patch_path, raw_evidence_path)):
        raise ContractError("external handoff import requires Path inputs")
    task_sha, task, _ = _load_committed_task(paths, task_ref)
    node_id = _task_node_id(paths, task_sha)
    assignment_sha, assignment, _ = _load_assignment(paths, assignment_ref)
    if assignment["role"] != "BUILDER":
        raise ContractError("external handoff requires a builder assignment")
    if assignment["task_manifest_sha256"] != task_sha:
        raise ContractError("external handoff assignment belongs to a different task")
    _require_exact_builder_dispatch(paths, node_id, assignment_sha)
    payload = _strict_json_bytes(
        _read_untrusted_file(handoff_path, "external handoff", _MAX_UNTRUSTED_JSON),
        "external handoff",
    )
    registry = SchemaRegistry(paths.repo_root)
    registry.validate(payload, "external-session-handoff-v1.schema.json")
    expected = {
        "assignment_sha256": assignment_sha,
        "task_manifest_sha256": task_sha,
        "base_commit_sha": task["base_commit_sha"],
        "worker_profile_sha256": assignment["worker_profile_sha256"],
        "provider": assignment["provider"],
        "session_id": assignment["session_id"],
        "target_host_eligible": False,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ContractError("external handoff assignment/task/profile/session lineage mismatch")
    _require_not_before(payload["returned_at"], assignment["created_at"], "handoff")
    patch = _read_untrusted_file(patch_path, "external patch", _MAX_PATCH_BYTES)
    if sha256_hex(patch) != payload["patch_sha256"]:
        raise ContractError("external patch digest does not match submitted bytes")
    raw = _read_raw_evidence(paths, raw_evidence_path)
    if sha256_hex(raw) != payload["raw_evidence_sha256"]:
        raise ContractError("external raw evidence digest does not match submitted bytes")
    _reject_raw_leak(payload, raw, raw_evidence_path, patch)
    _validate_source_lineage(
        task, payload["source_record_ids"], payload["source_packet_ids"]
    )
    existing = _handoffs_for_proof(
        paths, "external_session_assignment_sha256", assignment_sha
    )
    if existing:
        if len(existing) != 1:
            raise ContractError("external assignment has multiple immutable handoffs")
        stored, receipt_payload = existing[0]
        _bind_validated_external_input(
            paths,
            "external-handoff-input",
            assignment_sha,
            payload,
            "external assignment has a differing handoff replay",
        )
        expected_receipt = _external_handoff_receipt_payload(
            task_sha,
            assignment_sha,
            payload,
            tuple(receipt_payload["files_changed"]),
        )
        if canonical_json_bytes(expected_receipt) != canonical_json_bytes(receipt_payload):
            raise ContractError("external assignment has a differing handoff replay")
        stored_patch = _read_canonical_patch(paths, receipt_payload["patch_sha256"])
        if stored_patch != patch:
            raise ContractError("external handoff replay differs from the canonical patch")
        put_private_bytes(paths, "external-evidence", raw)
        state = _node_state(replay_events(paths.repo_root / _EVENTS), node_id)
        if state is NodeState.DISPATCHED:
            _transition_external_returned(paths, node_id)
        elif state not in {
            NodeState.RETURNED, NodeState.REVIEWED, NodeState.ACCEPTED,
            NodeState.INTEGRATED, NodeState.SUPERSEDED,
        }:
            raise TransitionError(f"external handoff replay found state {state.value}")
        return _handoff_receipt(
            stored,
            receipt_payload,
            receipt_payload["patch_sha256"],
            (),
            replayed=True,
        )
    state = _node_state(replay_events(paths.repo_root / _EVENTS), node_id)
    if state is not NodeState.DISPATCHED:
        raise TransitionError(
            f"external handoff requires DISPATCHED, found {state.value}"
        )
    worktree_key = sha256_hex(canonical_json_bytes({
        "kind": "external-handoff-import",
        "assignment_sha256": assignment_sha,
        "patch_sha256": payload["patch_sha256"],
        "provider_handoff_sha256": sha256_hex(canonical_json_bytes(payload)),
    }))
    worktree = create_detached_worktree(
        paths, worktree_key, assignment["base_commit_sha"]
    )
    canonical_patch, changed = _apply_patch_in_worktree(worktree, patch)
    if canonical_patch != patch or sha256_hex(canonical_patch) != payload["patch_sha256"]:
        raise ContractError("submitted patch does not equal the Git-derived canonical patch")
    _validate_scope_and_impacts(task, changed, payload["dependency_effects"])
    _bind_validated_external_input(
        paths,
        "external-handoff-input",
        assignment_sha,
        payload,
        "external assignment has a differing handoff replay",
    )
    private = put_private_bytes(paths, "external-evidence", raw)
    if private.sha256 != payload["raw_evidence_sha256"]:
        raise ContractError("private custody changed the raw evidence digest")
    patch_object = ContentAddressedStore(
        paths.repo_root / "build_control" / "graph"
    ).put_bytes("patches", canonical_patch, ".patch")
    receipt_payload = _external_handoff_receipt_payload(
        task_sha, assignment_sha, payload, changed
    )
    registry.validate(receipt_payload, "worker-handoff-v1.schema.json")
    stored = ContentAddressedStore(
        paths.repo_root / "build_control" / "graph"
    ).put_json("receipts", receipt_payload)
    _transition_external_returned(paths, node_id)
    return _handoff_receipt(
        stored, receipt_payload, patch_object.sha256, (), replayed=False
    )


def _validate_ready_dispatch(
    paths: BuildPaths,
    task_sha: str,
    task: Mapping[str, Any],
    node_id: str,
    events: tuple[dict[str, Any], ...],
) -> None:
    manifests = _committed_graph_manifests(paths)
    matches = [manifest for manifest in manifests if any(
        node["node_id"] == node_id for node in manifest["nodes"]
    )]
    if len(matches) != 1:
        raise ContractError("task node does not resolve to one committed graph")
    manifest = matches[0]
    validate_acyclic(BuildGraph.load(manifest, ()))
    dependencies = {
        edge["from_node_id"] for edge in manifest["edges"]
        if edge["edge_type"] == "DEPENDS_ON" and edge["to_node_id"] == node_id
    }
    if set(task["dependency_node_ids"]) != dependencies:
        raise TransitionError("task dependency declarations are not ready")
    states = _event_states(events)
    if any(states.get(parent, NodeState.DRAFT) is not NodeState.INTEGRATED for parent in dependencies):
        raise TransitionError("task dependencies are not integrated")
    for opaque in [*task["required_source_record_ids"], *task["required_source_packet_ids"]]:
        if opaque not in dependencies or states.get(opaque) is not NodeState.INTEGRATED:
            raise TransitionError("task required source lineage is not integrated")
    resume = task["resume_from_checkpoint_sha256"]
    if resume is not None and not any(
        node["node_type"] == "Checkpoint"
        and node["manifest_sha256"] == resume
        and node["node_id"] in dependencies
        and states.get(node["node_id"]) is NodeState.INTEGRATED
        for node in manifest["nodes"]
    ):
        raise TransitionError("task checkpoint lineage is not ready")
    active_claims: list[OwnershipClaim] = []
    lanes = 0
    for graph_manifest in manifests:
        for node in graph_manifest["nodes"]:
            if node["node_type"] != "BuildTask" or node["node_id"] == node_id:
                continue
            state = states.get(node["node_id"], NodeState.DRAFT)
            if state is NodeState.DISPATCHED:
                lanes += 1
            if state in _ACTIVE_WRITERS:
                _, active_task, _ = _load_committed_task(paths, node["manifest_sha256"])
                active_claims.append(OwnershipClaim.from_manifest(active_task))
    collisions = find_collisions(OwnershipClaim.from_manifest(task), active_claims)
    if collisions:
        kinds = ", ".join(sorted({collision.kind for collision in collisions}))
        raise CollisionError(f"BuildTask ownership collision for {node_id}: {kinds}")
    if lanes >= 3:
        raise TransitionError("at most three implementation BuildTasks may be DISPATCHED")
    if task_sha != next(
        node["manifest_sha256"] for node in manifest["nodes"] if node["node_id"] == node_id
    ):
        raise ContractError("graph task manifest does not match the committed task")


def _validate_task_base(paths: BuildPaths, base_commit: object) -> None:
    if (
        not isinstance(base_commit, str)
        or len(base_commit) != 40
        or any(character not in "0123456789abcdef" for character in base_commit)
    ):
        raise ContractError("task base commit is invalid")
    head = _head_sha(paths.repo_root)
    if (
        _git(paths.repo_root, "cat-file", "-e", f"{base_commit}^{{commit}}", check=False).returncode
        != 0
        or _git(
            paths.repo_root,
            "merge-base",
            "--is-ancestor",
            base_commit,
            head,
            check=False,
        ).returncode
        != 0
    ):
        raise ContractError("task base commit is not an exact committed ancestor of HEAD")


def _validate_builder_route(
    paths: BuildPaths,
    task_sha: str,
    task: Mapping[str, Any],
    reason: str,
) -> None:
    if reason in {"EXTERNAL_SESSION_SELECTED", "LOCAL_ADAPTER_UNAVAILABLE"}:
        return
    if reason != "LOCAL_ADAPTER_BLOCKED_SUCCESSOR":
        raise ContractError("external builder route reason is not allowed")
    predecessor_sha = task.get("supersedes_task_manifest_sha256")
    if not _is_digest(predecessor_sha):
        raise ContractError("blocked local successor lacks an exact predecessor")
    _, predecessor, _ = _load_committed_task(paths, predecessor_sha)
    predecessor_node = _task_node_id(paths, predecessor_sha)
    lifecycle = [
        event for event in replay_events(paths.repo_root / _EVENTS)
        if event.get("node_id") == predecessor_node
    ]
    if (
        not lifecycle
        or lifecycle[-1].get("new_state") != "SUPERSEDED"
        or lifecycle[-1].get("prior_state") != "BLOCKED"
        or lifecycle[-1].get("external_session_assignment_sha256") is not None
    ):
        raise ContractError("blocked local successor lacks terminal local-block evidence")
    dispatches = [
        event for event in lifecycle
        if event.get("prior_state") == "READY"
        and event.get("new_state") == "DISPATCHED"
    ]
    if (
        len(dispatches) != 1
        or not _is_digest(dispatches[0].get("attempt_manifest_sha256"))
        or dispatches[0].get("external_session_assignment_sha256") is not None
    ):
        raise ContractError(
            "blocked local successor predecessor lacks its original local dispatch proof"
        )
    attempt = _load_cas_json(
        paths,
        "attempts",
        dispatches[0]["attempt_manifest_sha256"],
        "attempt-manifest-v1.schema.json",
    )
    if (
        attempt["task_manifest_sha256"] != predecessor_sha
        or attempt["base_commit_sha"] != predecessor["base_commit_sha"]
    ):
        raise ContractError(
            "blocked local successor predecessor has a mismatched original local attempt"
        )
    if predecessor["base_commit_sha"] != task["base_commit_sha"]:
        raise ContractError("blocked local successor changed the exact base")
    routes = replay_events(paths.repo_root / _ROUTING)
    matches = [
        route for route in routes
        if route.get("task_manifest_sha256") == predecessor_sha
        and route.get("successor_task_manifest_sha256") == task_sha
    ]
    if len(matches) != 1 or (
        matches[0].get("action") != "REROUTE"
        or matches[0].get("reason_code") != "WORKER_REROUTE"
    ):
        raise ContractError("blocked local successor lacks its exact immutable route")


def _validate_reviewer_independence(
    paths: BuildPaths,
    task: Mapping[str, Any],
    reviewer_profile_id: str,
    reviewer_profile_sha: str,
    reviewer_session_id: str,
) -> None:
    task_sha = sha256_hex(canonical_json_bytes(task))
    handoffs = _task_handoffs(paths, task_sha)
    if len(handoffs) != 1:
        raise ContractError("external reviewer requires one exact collected handoff")
    _, handoff = handoffs[0]
    if reviewer_profile_id == task["roles"]["builder_profile_id"]:
        raise ContractError("reviewer profile must differ from the builder profile")
    if handoff["transport"] == "EXTERNAL_SESSION":
        builder_sha = handoff["external_session_assignment_sha256"]
        _, builder, _ = _load_assignment(paths, builder_sha)
        if builder["worker_profile_sha256"] == reviewer_profile_sha:
            raise ContractError("reviewer profile identity must differ from the builder")
        if builder["session_id"] == reviewer_session_id:
            raise ContractError("reviewer session must differ from the builder session")
    else:
        attempt = _load_cas_json(
            paths,
            "attempts",
            handoff["attempt_manifest_sha256"],
            "attempt-manifest-v1.schema.json",
        )
        if attempt["worker_profile_sha256"] == reviewer_profile_sha:
            raise ContractError("reviewer profile identity must differ from the builder")


def _task_handoffs(
    paths: BuildPaths, task_sha: str
) -> tuple[tuple[StoredObject, dict[str, Any]], ...]:
    return tuple(
        (stored, payload)
        for stored, payload in _iter_cas_json(
            paths, "receipts", "worker-handoff-v1.schema.json"
        )
        if payload["task_manifest_sha256"] == task_sha
    )


def _routing_action(role: str, reason: str) -> str:
    if role == "REVIEWER":
        if reason != "EXTERNAL_SESSION_SELECTED":
            raise ContractError("external reviewer permits only EXTERNAL_SESSION_SELECTED")
        return "EXTERNAL_SESSION_ASSIGN"
    mapping = {
        "EXTERNAL_SESSION_SELECTED": "EXTERNAL_SESSION_ASSIGN",
        "LOCAL_ADAPTER_UNAVAILABLE": "REROUTE_TO_EXTERNAL_SESSION",
        "LOCAL_ADAPTER_BLOCKED_SUCCESSOR": "REROUTE_TO_EXTERNAL_SESSION",
    }
    try:
        return mapping[reason]
    except KeyError as error:
        raise ContractError("external builder route reason is not allowed") from error


def _external_handoff_receipt_payload(
    task_sha: str,
    assignment_sha: str,
    provider_payload: Mapping[str, Any],
    changed: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "protocol": "helios.build.worker-handoff/v1",
        "transport": "EXTERNAL_SESSION",
        "attempt_manifest_sha256": None,
        "external_session_assignment_sha256": assignment_sha,
        "task_manifest_sha256": task_sha,
        "output_kind": "PATCH",
        "patch_sha256": provider_payload["patch_sha256"],
        "commit_sha": None,
        "files_changed": list(changed),
        "commands_executed": provider_payload["commands_executed"],
        "focused_test_results": provider_payload["focused_test_results"],
        "assumptions": provider_payload["assumptions"],
        "unresolved_issues": provider_payload["unresolved_issues"],
        "dependency_effects": provider_payload["dependency_effects"],
        "security_effects": provider_payload["security_effects"],
        "source_record_ids": provider_payload["source_record_ids"],
        "source_packet_ids": provider_payload["source_packet_ids"],
        "raw_evidence_sha256": provider_payload["raw_evidence_sha256"],
        "duration_ms": provider_payload["duration_ms"],
        "cost_microusd": provider_payload["cost_microusd"],
    }


def _require_exact_builder_dispatch(
    paths: BuildPaths,
    node_id: str,
    assignment_sha: str,
) -> None:
    dispatches = [
        event for event in replay_events(paths.repo_root / _EVENTS)
        if event.get("node_id") == node_id
        and event.get("prior_state") == "READY"
        and event.get("new_state") == "DISPATCHED"
    ]
    if (
        len(dispatches) != 1
        or dispatches[0].get("attempt_manifest_sha256") is not None
        or dispatches[0].get("external_session_assignment_sha256") != assignment_sha
    ):
        raise ContractError(
            "external handoff dispatch does not bind the exact assignment"
        )


def _bind_validated_external_input(
    paths: BuildPaths,
    namespace_prefix: str,
    assignment_sha: str,
    payload: Mapping[str, Any],
    differing_message: str,
) -> None:
    """Privately bind one full validated provider object to one assignment."""
    if not _is_digest(assignment_sha):
        raise ContractError("external input binding assignment is invalid")
    content = canonical_json_bytes(payload)
    digest = sha256_hex(content)
    namespace = f"{namespace_prefix}-{assignment_sha}"
    with ControllerLock(paths):
        existing = _private_binding(paths, namespace)
        if existing is not None:
            existing_digest, existing_size = existing
            existing_content = read_private_bytes(
                paths,
                f"{namespace}/sha256/{existing_digest[:2]}/{existing_digest}.bin",
                existing_digest,
                existing_size,
                max_bytes=_MAX_UNTRUSTED_JSON,
            )
            if existing_digest != digest or existing_content != content:
                raise ContractError(differing_message)
            return
        stored = put_private_bytes(paths, namespace, content)
        if stored.sha256 != digest:
            raise ContractError("private external input binding changed its digest")


def _private_binding(
    paths: BuildPaths, namespace: str
) -> tuple[str, int] | None:
    root = paths.state_root / "private-cas" / namespace / "sha256"
    if not root.exists():
        return None
    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise ContractError("cannot inspect private external input binding") from error
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ContractError("private external input binding root is not a directory")
    found: list[tuple[str, int]] = []
    try:
        prefixes = tuple(root.iterdir())
    except OSError as error:
        raise ContractError("cannot enumerate private external input bindings") from error
    for prefix in prefixes:
        prefix_metadata = prefix.lstat()
        if not stat.S_ISDIR(prefix_metadata.st_mode):
            raise ContractError("private external input binding prefix is unsafe")
        for candidate in prefix.iterdir():
            metadata = candidate.lstat()
            digest = candidate.stem
            if (
                candidate.suffix != ".bin"
                or not _is_digest(digest)
                or prefix.name != digest[:2]
                or not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ContractError("private external input binding object is unsafe")
            found.append((digest, metadata.st_size))
    if len(found) > 1:
        raise ContractError("external assignment has multiple private input bindings")
    return found[0] if found else None


def _load_assignment(
    paths: BuildPaths, assignment_ref: str | Path
) -> tuple[str, dict[str, Any], Path]:
    if isinstance(assignment_ref, Path):
        try:
            content = _read_untrusted_file(
                assignment_ref, "external assignment", _MAX_UNTRUSTED_JSON
            )
        except ContractError:
            raise
        payload = _strict_json_bytes(content, "external assignment")
        digest = sha256_hex(canonical_json_bytes(payload))
    else:
        digest = assignment_ref
        if not _is_digest(digest):
            raise ContractError("external assignment reference must be a SHA-256 digest")
        payload = _load_cas_json(
            paths, _ASSIGNMENTS, digest, "external-session-assignment-v1.schema.json"
        )
    path = (
        paths.repo_root / "build_control" / "graph" / _ASSIGNMENTS / "sha256"
        / digest[:2] / f"{digest}.json"
    )
    if not path.is_file() or path.read_bytes() != canonical_json_bytes(payload):
        raise ContractError("external assignment does not resolve in its dedicated CAS")
    SchemaRegistry(paths.repo_root).validate(
        payload, "external-session-assignment-v1.schema.json"
    )
    return digest, payload, path


def _assignments_for(
    paths: BuildPaths, task_sha: str, role: str
) -> tuple[tuple[StoredObject, dict[str, Any]], ...]:
    return tuple(
        (stored, payload)
        for stored, payload in _iter_cas_json(
            paths, _ASSIGNMENTS, "external-session-assignment-v1.schema.json"
        )
        if payload["task_manifest_sha256"] == task_sha and payload["role"] == role
    )


def _assignment(
    stored: StoredObject,
    payload: Mapping[str, Any],
    *,
    replayed: bool | None = None,
) -> ExternalSessionAssignment:
    return ExternalSessionAssignment(
        sha256=stored.sha256,
        path=stored.path,
        replayed=stored.replayed if replayed is None else replayed,
        assignment_id=payload["assignment_id"],
        task_manifest_sha256=payload["task_manifest_sha256"],
        base_commit_sha=payload["base_commit_sha"],
        role=payload["role"],
        worker_profile_id=payload["worker_profile_id"],
        worker_profile_sha256=payload["worker_profile_sha256"],
        provider=ExternalProvider(payload["provider"]),
        session_id=payload["session_id"],
        transport=HandoffTransport(payload["transport"]),
        routing_action=payload["routing_action"],
        routing_reason=payload["routing_reason"],
        created_at=payload["created_at"],
        created_by_profile_id=payload["created_by_profile_id"],
        target_host_eligible=payload["target_host_eligible"],
    )


def _committed_graph_manifests(paths: BuildPaths) -> tuple[dict[str, Any], ...]:
    head = _head_sha(paths.repo_root)
    registry = SchemaRegistry(paths.repo_root)
    found: list[dict[str, Any]] = []
    for relative in _git_tree(paths.repo_root, head, "build_control/graph"):
        if Path(relative).parent.as_posix() != "build_control/graph" or not relative.endswith(".json"):
            continue
        content = _git_show(paths.repo_root, head, relative)
        payload = _strict_json_bytes(content, "committed graph manifest")
        if payload.get("protocol") != "helios.build.graph-manifest/v1":
            continue
        registry.validate(payload, "graph-manifest-v1.schema.json")
        if (paths.repo_root / relative).read_bytes() != content:
            raise ContractError("working graph manifest differs from captured HEAD")
        found.append(payload)
    return tuple(found)


def _event_states(events: tuple[dict[str, Any], ...]) -> dict[str, NodeState]:
    result: dict[str, NodeState] = {}
    for event in events:
        node_id = event.get("node_id")
        if isinstance(node_id, str):
            try:
                result[node_id] = NodeState(event["new_state"])
            except ValueError as error:
                raise ContractError("lifecycle event has an invalid state") from error
    return result


def _transition_external_returned(paths: BuildPaths, node_id: str) -> None:
    from tools.helios_build.graph import transition_node

    transition_node(
        paths,
        node_id,
        NodeState.DISPATCHED,
        NodeState.RETURNED,
        "EXTERNAL_SESSION_HANDOFF_IMPORTED",
        "codex-control-v1",
    )


def _read_canonical_patch(paths: BuildPaths, digest: str) -> bytes:
    if not _is_digest(digest):
        raise ContractError("canonical patch reference is invalid")
    path = (
        paths.repo_root / "build_control" / "graph" / "patches" / "sha256"
        / digest[:2] / f"{digest}.patch"
    )
    try:
        content = path.read_bytes()
    except OSError as error:
        raise ContractError("canonical patch does not resolve exactly") from error
    if sha256_hex(content) != digest:
        raise ContractError("canonical patch digest mismatch")
    return content


def _read_untrusted_file(path: Path, label: str, max_bytes: int) -> bytes:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory_flag is None:
        raise ContractError(f"cannot safely read {label} without no-follow directory custody")
    absolute = Path(os.path.abspath(path))
    components = absolute.parts[1:]
    if not components or any(component in {"", ".", ".."} for component in components):
        raise ContractError(f"{label} path contains an unsafe component")
    directory_flags = (
        os.O_RDONLY
        | directory_flag
        | no_follow
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        parent = os.open(Path(absolute.anchor), directory_flags)
    except OSError as error:
        raise ContractError(f"cannot safely open {label} filesystem root: {error}") from error
    try:
        for component in components[:-1]:
            try:
                child = os.open(component, directory_flags, dir_fd=parent)
            except OSError as error:
                raise ContractError(
                    f"{label} path contains a symlink or non-directory component"
                ) from error
            os.close(parent)
            parent = child
        try:
            descriptor = os.open(
                components[-1],
                os.O_RDONLY | no_follow | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent,
            )
        except OSError as error:
            raise ContractError(f"cannot safely open {label}: {error}") from error
        try:
            metadata = os.fstat(descriptor)
            current_uid = os.getuid() if hasattr(os, "getuid") else None
            if (
                current_uid is None
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != current_uid
                or metadata.st_size > max_bytes
            ):
                raise ContractError(f"{label} is not a bounded current-owner regular file")
            chunks: list[bytes] = []
            remaining = metadata.st_size + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) != metadata.st_size:
                raise ContractError(f"{label} changed while being read")
            return content
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def _read_raw_evidence(paths: BuildPaths, path: Path) -> bytes:
    absolute = Path(os.path.abspath(path))
    state = Path(os.path.abspath(paths.state_root))
    try:
        relative = absolute.relative_to(state)
    except ValueError as error:
        raise ContractError("raw evidence must already be under the external state root") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ContractError("raw evidence path contains an unsafe component")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        parent = os.open(state, directory_flags)
    except OSError as error:
        raise ContractError(f"cannot open external state root for raw evidence: {error}") from error
    try:
        for component in relative.parts[:-1]:
            try:
                child = os.open(component, directory_flags, dir_fd=parent)
            except OSError as error:
                raise ContractError("raw evidence path contains a symlink or non-directory") from error
            os.close(parent)
            parent = child
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(relative.parts[-1], flags, dir_fd=parent)
        except OSError as error:
            raise ContractError(f"cannot safely open raw evidence: {error}") from error
        try:
            metadata = os.fstat(descriptor)
            current_uid = os.getuid() if hasattr(os, "getuid") else None
            if (
                current_uid is None
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != current_uid
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > _MAX_RAW_EVIDENCE_BYTES
            ):
                raise ContractError(
                    "raw evidence lacks exact owner/mode/size external custody"
                )
            chunks: list[bytes] = []
            remaining = metadata.st_size + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            if len(content) != metadata.st_size:
                raise ContractError("raw evidence changed while being read")
            return content
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def _require_not_before(value: str, lower: str, label: str) -> None:
    if _parse_utc(value) < _parse_utc(lower):
        raise ContractError(f"external {label} timestamp is before assignment")


def _reject_raw_leak(
    payload: Mapping[str, Any],
    raw: bytes,
    raw_path: Path,
    other_git_bytes: bytes = b"",
) -> None:
    sanitized = canonical_json_bytes(dict(payload))
    raw_path_bytes = str(raw_path).encode("utf-8", errors="strict")
    if raw_path_bytes and raw_path_bytes in sanitized:
        raise ContractError("sanitized receipt metadata contains the raw evidence path")
    if len(raw) >= 16 and (raw in sanitized or raw in other_git_bytes):
        raise ContractError("raw evidence bytes may not enter sanitized Git objects")


def _parse_utc(value: str) -> datetime:
    try:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise ContractError("external timestamp is not RFC3339 UTC") from error
    return parsed.astimezone(UTC)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
