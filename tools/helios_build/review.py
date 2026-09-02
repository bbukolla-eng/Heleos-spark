"""Independent local or identified external review and bounded correction routing."""

from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.helios_build._external_cas import put_private_bytes
from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build.collect import (
    _apply_patch_in_worktree,
    _derive_commit_truth,
    _handoffs_for_proof,
    _is_digest,
    _iter_cas_json,
    _load_cas_json,
    _load_committed_profile,
    _load_committed_task,
    _node_state,
    _strict_json_bytes,
    _task_node_id,
    _validate_source_lineage,
)
from tools.helios_build.doctor import preflight_worker
from tools.helios_build.errors import ContractError, TransitionError
from tools.helios_build.external_sessions import (
    _bind_validated_external_input,
    _load_assignment,
    _read_canonical_patch,
    _read_raw_evidence,
    _read_untrusted_file,
    _reject_raw_leak,
    _require_not_before,
    _task_handoffs,
    _validate_reviewer_independence,
)
from tools.helios_build.graph import ControllerLock, transition_node, transition_node_locked
from tools.helios_build.ledger import append_event, replay_events
from tools.helios_build.paths import BuildPaths
from tools.helios_build.process import ProcessLaunchError, run_bounded_process
from tools.helios_build.profiles import (
    load_adapter_configuration,
    worker_profile_sha256,
)
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore, StoredObject
from tools.helios_build.types import HandoffTransport, NodeState
from tools.helios_build.verification import CommandReceipt, run_verification_gate
from tools.helios_build.worktrees import create_detached_worktree


_EVENTS = Path("build_control/graph/events.jsonl")
_ROUTING = Path("build_control/graph/routing.jsonl")
_MAX_REVIEW_JSON = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ReviewReceipt:
    sha256: str
    path: Path
    replayed: bool
    transport: HandoffTransport
    task_manifest_sha256: str
    handoff_sha256: str
    reviewer_profile_sha256: str
    external_session_assignment_sha256: str | None
    raw_evidence_sha256: str | None
    verdict: str
    findings: tuple[dict[str, str], ...]
    command_receipt_sha256s: tuple[str, ...]
    source_record_ids: tuple[str, ...]
    source_packet_ids: tuple[str, ...]
    started_at: str
    duration_ms: int
    target_host_eligible: bool
    successor_task_manifest_sha256: str | None = None
    preflight_receipt_sha256: str | None = None


def import_external_review(
    paths: BuildPaths,
    task_ref: str | Path,
    assignment_ref: str | Path,
    review_path: Path,
    raw_evidence_path: Path,
) -> ReviewReceipt:
    """Validate a fresh external reviewer against the exact collected patch."""
    if not isinstance(review_path, Path) or not isinstance(raw_evidence_path, Path):
        raise ContractError("external review import requires Path inputs")
    task_sha, task, _ = _load_committed_task(paths, task_ref)
    node_id = _task_node_id(paths, task_sha)
    assignment_sha, assignment, _ = _load_assignment(paths, assignment_ref)
    if assignment["role"] != "REVIEWER":
        raise ContractError("external review requires a reviewer assignment")
    if assignment["task_manifest_sha256"] != task_sha:
        raise ContractError("external review assignment belongs to a different task")
    _validate_reviewer_independence(
        paths,
        task,
        assignment["worker_profile_id"],
        assignment["worker_profile_sha256"],
        assignment["session_id"],
    )
    state = _node_state(replay_events(paths.repo_root / _EVENTS), node_id)
    if state not in {
        NodeState.RETURNED, NodeState.REVIEWED, NodeState.ACCEPTED, NodeState.SUPERSEDED,
    }:
        raise TransitionError(f"external review requires RETURNED, found {state.value}")
    provider_review = _strict_json_bytes(
        _read_untrusted_file(review_path, "external review", _MAX_REVIEW_JSON),
        "external review",
    )
    registry = SchemaRegistry(paths.repo_root)
    registry.validate(provider_review, "external-session-review-v1.schema.json")
    expected = {
        "assignment_sha256": assignment_sha,
        "task_manifest_sha256": task_sha,
        "reviewer_profile_sha256": assignment["worker_profile_sha256"],
        "provider": assignment["provider"],
        "session_id": assignment["session_id"],
        "target_host_eligible": False,
    }
    if any(provider_review.get(key) != value for key, value in expected.items()):
        raise ContractError("external review assignment/task/profile/session lineage mismatch")
    _require_not_before(provider_review["reviewed_at"], assignment["created_at"], "review")
    raw = _read_raw_evidence(paths, raw_evidence_path)
    if sha256_hex(raw) != provider_review["raw_evidence_sha256"]:
        raise ContractError("external review raw evidence digest does not match submitted bytes")
    source = provider_review["source_verification"]
    _validate_source_lineage(
        task, source["source_record_ids"], source["source_packet_ids"]
    )
    handoff_stored, handoff, patch = _exact_handoff(
        paths, task, provider_review["handoff_sha256"]
    )
    _reject_raw_leak(provider_review, raw, raw_evidence_path, patch)
    del handoff_stored
    patch_sha = sha256_hex(patch)
    supplied_commands = _validate_command_references(
        paths,
        provider_review["command_receipt_sha256s"],
        task_sha,
        patch_sha,
    )
    existing = _reviews_for_assignment(paths, assignment_sha)
    if existing:
        if len(existing) != 1:
            raise ContractError("external reviewer assignment has multiple review receipts")
        stored, receipt_payload = existing[0]
        _bind_validated_external_input(
            paths,
            "external-review-input",
            assignment_sha,
            provider_review,
            "external reviewer assignment has a differing review replay",
        )
        expected_receipt = _external_review_receipt_payload(
            assignment_sha,
            provider_review,
            tuple(receipt_payload["command_receipt_sha256s"]),
        )
        if canonical_json_bytes(expected_receipt) != canonical_json_bytes(receipt_payload):
            raise ContractError("external reviewer assignment has a differing review replay")
        put_private_bytes(paths, "external-review-evidence", raw)
        successor = _reconcile_review_state(
            paths, task_sha, task, node_id, stored.sha256, receipt_payload, patch_sha
        )
        return _review_receipt(
            stored, receipt_payload, successor=successor, replayed=True
        )
    if state is not NodeState.RETURNED:
        raise TransitionError(f"new external review requires RETURNED, found {state.value}")
    worktree_key = sha256_hex(canonical_json_bytes({
        "kind": "external-review",
        "assignment_sha256": assignment_sha,
        "handoff_sha256": provider_review["handoff_sha256"],
        "provider_review_sha256": sha256_hex(canonical_json_bytes(provider_review)),
    }))
    worktree = create_detached_worktree(paths, worktree_key, task["base_commit_sha"])
    derived_patch, _ = _apply_patch_in_worktree(worktree, patch)
    if derived_patch != patch:
        raise ContractError("review worktree patch differs from the canonical handoff")
    gate_receipts = run_verification_gate(
        paths, task, worktree, "AFFECTED_INTEGRATION"
    )
    command_shas = _merge_command_receipts(supplied_commands, gate_receipts)
    private = put_private_bytes(paths, "external-review-evidence", raw)
    if private.sha256 != provider_review["raw_evidence_sha256"]:
        raise ContractError("private custody changed the external review evidence digest")
    _bind_validated_external_input(
        paths,
        "external-review-input",
        assignment_sha,
        provider_review,
        "external reviewer assignment has a differing review replay",
    )
    receipt_payload = _external_review_receipt_payload(
        assignment_sha, provider_review, command_shas
    )
    registry.validate(receipt_payload, "review-receipt-v1.schema.json")
    stored = ContentAddressedStore(
        paths.repo_root / "build_control" / "graph"
    ).put_json("receipts", receipt_payload)
    transition_node(
        paths,
        node_id,
        NodeState.RETURNED,
        NodeState.REVIEWED,
        "EXTERNAL_SESSION_REVIEW_IMPORTED",
        "codex-control-v1",
    )
    successor = _finalize_review(
        paths, task_sha, task, node_id, stored.sha256, receipt_payload, patch_sha
    )
    return _review_receipt(stored, receipt_payload, successor=successor)


def review_task(
    paths: BuildPaths,
    task_ref: str | Path,
    adapter_config_path: Path,
) -> ReviewReceipt:
    """Run one finite exact-preflight local reviewer in a fresh patch worktree."""
    if not isinstance(adapter_config_path, Path):
        raise ContractError("local review requires an adapter configuration Path")
    task_sha, task, _ = _load_committed_task(paths, task_ref)
    node_id = _task_node_id(paths, task_sha)
    state = _node_state(replay_events(paths.repo_root / _EVENTS), node_id)
    if state is not NodeState.RETURNED:
        raise TransitionError(f"local review requires RETURNED, found {state.value}")
    handoffs = _task_handoffs(paths, task_sha)
    if len(handoffs) != 1:
        raise ContractError("local review requires one exact collected handoff")
    handoff_stored, handoff = handoffs[0]
    reviewer_id = task["roles"]["reviewer_profile_id"]
    builder_id = task["roles"]["builder_profile_id"]
    if reviewer_id == builder_id:
        raise ContractError("reviewer must differ from the builder")
    profile, _ = _load_committed_profile(paths, reviewer_id)
    if "REVIEWER" not in profile.roles or "LOCAL_ADAPTER" not in profile.transports:
        raise ContractError("designated reviewer profile does not permit local review")
    adapters = load_adapter_configuration(
        adapter_config_path, SchemaRegistry(paths.repo_root), repo_root=paths.repo_root
    )
    matches = [adapter for adapter in adapters if adapter.worker_id == profile.worker_id]
    if not matches:
        builder_profile, _ = _load_committed_profile(paths, builder_id)
        if any(adapter.worker_id == builder_profile.worker_id for adapter in adapters):
            raise ContractError("reviewer must differ from the builder adapter identity")
        raise ContractError("designated reviewer adapter is not configured")
    if len(matches) != 1:
        raise ContractError("designated reviewer resolves to multiple adapters")
    adapter = matches[0]
    builder_profile_sha = _builder_profile_sha(paths, handoff)
    reviewer_profile_sha = worker_profile_sha256(profile)
    if builder_profile_sha == reviewer_profile_sha:
        raise ContractError("reviewer must differ from the builder profile")
    preflight = preflight_worker(
        paths,
        profile,
        adapter,
        expected_result_protocol="helios.build.review-receipt/v1",
    )
    if preflight.availability != "AVAILABLE" or preflight.receipt_sha256 is None:
        raise ContractError("designated reviewer did not pass exact local preflight")
    _, _, patch = _exact_handoff(paths, task, handoff_stored.sha256)
    patch_sha = sha256_hex(patch)
    worktree_key = sha256_hex(canonical_json_bytes({
        "kind": "local-review",
        "task_manifest_sha256": task_sha,
        "handoff_sha256": handoff_stored.sha256,
        "reviewer_profile_sha256": reviewer_profile_sha,
    }))
    worktree = create_detached_worktree(paths, worktree_key, task["base_commit_sha"])
    derived, _ = _apply_patch_in_worktree(worktree, patch)
    if derived != patch:
        raise ContractError("local review worktree differs from the canonical handoff")
    raw_review = _run_local_reviewer(
        paths,
        task_sha,
        task,
        handoff_stored.sha256,
        reviewer_profile_sha,
        adapter,
        worktree,
    )
    SchemaRegistry(paths.repo_root).validate(raw_review, "review-receipt-v1.schema.json")
    expected = {
        "transport": "LOCAL_ADAPTER",
        "task_manifest_sha256": task_sha,
        "handoff_sha256": handoff_stored.sha256,
        "reviewer_profile_sha256": reviewer_profile_sha,
        "external_session_assignment_sha256": None,
        "raw_evidence_sha256": None,
    }
    if any(raw_review.get(key) != value for key, value in expected.items()):
        raise ContractError("local reviewer receipt has mismatched task/profile lineage")
    source = raw_review["source_verification"]
    _validate_source_lineage(
        task, source["source_record_ids"], source["source_packet_ids"]
    )
    supplied = _validate_command_references(
        paths, raw_review["command_receipt_sha256s"], task_sha, patch_sha
    )
    gate_receipts = run_verification_gate(
        paths, task, worktree, "AFFECTED_INTEGRATION"
    )
    raw_review["command_receipt_sha256s"] = list(
        _merge_command_receipts(supplied, gate_receipts)
    )
    stored = ContentAddressedStore(
        paths.repo_root / "build_control" / "graph"
    ).put_json("receipts", raw_review)
    transition_node(
        paths,
        node_id,
        NodeState.RETURNED,
        NodeState.REVIEWED,
        "LOCAL_ADAPTER_REVIEW_COLLECTED",
        "codex-control-v1",
    )
    successor = _finalize_review(
        paths, task_sha, task, node_id, stored.sha256, raw_review, patch_sha
    )
    return _review_receipt(
        stored,
        raw_review,
        successor=successor,
        preflight_sha=preflight.receipt_sha256,
    )


def create_correction_successor(
    paths: BuildPaths,
    task_ref: str | Path,
    review_receipt_sha256: str,
) -> StoredObject:
    """Create and route one immutable higher-round successor, or escalate."""
    task_sha, task, _ = _load_committed_task(paths, task_ref)
    review = _load_cas_json(
        paths, "receipts", review_receipt_sha256, "review-receipt-v1.schema.json"
    )
    if review["task_manifest_sha256"] != task_sha or review["verdict"] != "CHANGES_REQUIRED":
        raise ContractError("correction successor requires the task's CHANGES_REQUIRED review")
    node_id = _task_node_id(paths, task_sha)
    with ControllerLock(paths) as lock:
        state = _node_state(replay_events(paths.repo_root / _EVENTS), node_id)
        if state is NodeState.SUPERSEDED:
            existing = _successors_for(paths, task_sha)
            if len(existing) != 1:
                raise ContractError(
                    "superseded correction task lacks one immutable successor"
                )
            _validate_existing_correction_route(
                paths, task_sha, node_id, existing[0].sha256
            )
            return StoredObject(existing[0].sha256, existing[0].path, True)
        if state is not NodeState.REVIEWED:
            raise TransitionError(
                f"correction successor requires REVIEWED, found {state.value}"
            )
        next_round = task["correction_round"] + 1
        maximum = task["budgets"]["maximum_correction_rounds"]
        if next_round > maximum:
            raise ContractError(
                "correction rounds exhausted; escalate instead of a third correction"
            )
        existing = _successors_for(paths, task_sha)
        if existing:
            if len(existing) != 1:
                raise ContractError("task has multiple immutable correction successors")
            stored = existing[0]
            successor = ContentAddressedStore(
                paths.repo_root / "build_control"
            ).get_json(stored.sha256)
        else:
            successor = copy.deepcopy(task)
            successor["task_id"] = f"{task['task_id']}-correction-{next_round}"
            successor["correction_round"] = next_round
            successor["supersedes_task_manifest_sha256"] = task_sha
            successor["resume_from_checkpoint_sha256"] = None
            successor["created_at"] = _utc_now()
            SchemaRegistry(paths.repo_root).validate(
                successor, "task-manifest-v1.schema.json"
            )
            stored = ContentAddressedStore(
                paths.repo_root / "build_control"
            ).put_json("tasks", successor)
        routes = replay_events(paths.repo_root / _ROUTING)
        route_matches = [
            route for route in routes
            if route.get("task_manifest_sha256") == task_sha
            or route.get("successor_task_manifest_sha256") == stored.sha256
        ]
        if route_matches:
            if len(route_matches) != 1:
                raise ContractError("correction route is not unique")
            route = route_matches[0]
            expected = {
                "task_manifest_sha256": task_sha,
                "action": "CORRECTION",
                "from_profile_id": task["roles"]["builder_profile_id"],
                "to_profile_id": successor["roles"]["builder_profile_id"],
                "reason_code": "REVIEW_CORRECTION",
                "successor_task_manifest_sha256": stored.sha256,
                "checkpoint_sha256": None,
                "actor_profile_id": "codex-control-v1",
            }
            if any(route.get(key) != value for key, value in expected.items()):
                raise ContractError("existing correction route has differing lineage")
            routing_sha = route["event_sha256"]
        else:
            body = {
                "protocol": "helios.build.routing-event/v1",
                "task_manifest_sha256": task_sha,
                "action": "CORRECTION",
                "from_profile_id": task["roles"]["builder_profile_id"],
                "to_profile_id": successor["roles"]["builder_profile_id"],
                "reason_code": "REVIEW_CORRECTION",
                "successor_task_manifest_sha256": stored.sha256,
                "checkpoint_sha256": None,
                "recorded_at": _utc_now(),
                "actor_profile_id": "codex-control-v1",
            }
            prospective = dict(body)
            prospective["sequence"] = len(routes) + 1
            prospective["previous_event_sha256"] = routes[-1]["event_sha256"] if routes else None
            prospective["event_sha256"] = sha256_hex(canonical_json_bytes(prospective))
            SchemaRegistry(paths.repo_root).validate(
                prospective, "routing-event-v1.schema.json"
            )
            routing_sha = append_event(
                paths.repo_root / _ROUTING, body, paths.state_root
            )
        transition_node_locked(
            lock,
            node_id,
            NodeState.REVIEWED,
            NodeState.SUPERSEDED,
            "REVIEW_CORRECTION",
            "codex-control-v1",
            routing_event_sha256=routing_sha,
        )
    return stored


def _exact_handoff(
    paths: BuildPaths,
    task: Mapping[str, Any],
    handoff_sha: str,
) -> tuple[StoredObject, dict[str, Any], bytes]:
    if not _is_digest(handoff_sha):
        raise ContractError("review handoff reference is invalid")
    matches = [
        (stored, payload)
        for stored, payload in _task_handoffs(
            paths, sha256_hex(canonical_json_bytes(task))
        )
        if stored.sha256 == handoff_sha
    ]
    if len(matches) != 1:
        raise ContractError("review handoff does not resolve exactly for the task")
    stored, handoff = matches[0]
    if handoff["output_kind"] == "PATCH":
        patch = _read_canonical_patch(paths, handoff["patch_sha256"])
    else:
        patch, changed = _derive_commit_truth(
            paths.repo_root, task["base_commit_sha"], handoff["commit_sha"]
        )
        if tuple(sorted(handoff["files_changed"])) != changed:
            raise ContractError("commit handoff paths no longer match Git truth")
        patch_object = ContentAddressedStore(
            paths.repo_root / "build_control" / "graph"
        ).put_bytes("patches", patch, ".patch")
        if patch_object.sha256 != sha256_hex(patch):
            raise ContractError("commit patch publication changed its digest")
    return stored, handoff, patch


def _external_review_receipt_payload(
    assignment_sha: str,
    provider: Mapping[str, Any],
    command_shas: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "protocol": "helios.build.review-receipt/v1",
        "transport": "EXTERNAL_SESSION",
        "task_manifest_sha256": provider["task_manifest_sha256"],
        "handoff_sha256": provider["handoff_sha256"],
        "reviewer_profile_sha256": provider["reviewer_profile_sha256"],
        "external_session_assignment_sha256": assignment_sha,
        "raw_evidence_sha256": provider["raw_evidence_sha256"],
        "verdict": provider["verdict"],
        "findings": provider["findings"],
        "command_receipt_sha256s": list(command_shas),
        "source_verification": provider["source_verification"],
        "started_at": provider["reviewed_at"],
        "duration_ms": provider["duration_ms"],
    }


def _validate_command_references(
    paths: BuildPaths,
    references: object,
    task_sha: str,
    patch_sha: str,
) -> tuple[tuple[StoredObject, dict[str, Any]], ...]:
    if not isinstance(references, list):
        raise ContractError("review command receipt references must be an array")
    found: list[tuple[StoredObject, dict[str, Any]]] = []
    for digest in references:
        payload = _load_cas_json(
            paths, "receipts", digest, "command-receipt-v1.schema.json"
        )
        if (
            payload["task_manifest_sha256"] != task_sha
            or payload["gate"] != "AFFECTED_INTEGRATION"
            or payload["patch_sha256"] != patch_sha
        ):
            raise ContractError("review command receipt does not bind the exact affected patch")
        path = (
            paths.repo_root / "build_control" / "graph" / "receipts" / "sha256"
            / digest[:2] / f"{digest}.json"
        )
        found.append((StoredObject(digest, path, True), payload))
    return tuple(found)


def _merge_command_receipts(
    supplied: tuple[tuple[StoredObject, dict[str, Any]], ...],
    executed: tuple[CommandReceipt, ...],
) -> tuple[str, ...]:
    ordered = [stored.sha256 for stored, _ in supplied]
    ordered.extend(receipt.sha256 for receipt in executed)
    return tuple(dict.fromkeys(ordered))


def _finalize_review(
    paths: BuildPaths,
    task_sha: str,
    task: Mapping[str, Any],
    node_id: str,
    review_sha: str,
    review: Mapping[str, Any],
    patch_sha: str,
) -> str | None:
    if _may_accept(paths, task, review, patch_sha):
        transition_node(
            paths,
            node_id,
            NodeState.REVIEWED,
            NodeState.ACCEPTED,
            "INDEPENDENT_REVIEW_ACCEPTED",
            "codex-control-v1",
        )
        return None
    if review["verdict"] == "CHANGES_REQUIRED":
        successor = create_correction_successor(paths, task_sha, review_sha)
        return successor.sha256
    return None


def _reconcile_review_state(
    paths: BuildPaths,
    task_sha: str,
    task: Mapping[str, Any],
    node_id: str,
    review_sha: str,
    review: Mapping[str, Any],
    patch_sha: str,
) -> str | None:
    state = _node_state(replay_events(paths.repo_root / _EVENTS), node_id)
    if state is NodeState.RETURNED:
        transition_node(
            paths,
            node_id,
            NodeState.RETURNED,
            NodeState.REVIEWED,
            "EXTERNAL_SESSION_REVIEW_IMPORTED",
            "codex-control-v1",
        )
        state = NodeState.REVIEWED
    if state is NodeState.REVIEWED:
        return _finalize_review(
            paths, task_sha, task, node_id, review_sha, review, patch_sha
        )
    if state is NodeState.SUPERSEDED:
        successors = _successors_for(paths, task_sha)
        return successors[0].sha256 if len(successors) == 1 else None
    if state in {NodeState.ACCEPTED, NodeState.INTEGRATED}:
        return None
    raise TransitionError(f"review replay found inconsistent state {state.value}")


def _may_accept(
    paths: BuildPaths,
    task: Mapping[str, Any],
    review: Mapping[str, Any],
    patch_sha: str,
) -> bool:
    if review["verdict"] != "ACCEPTED" or any(
        finding["severity"] == "CRITICAL" for finding in review["findings"]
    ):
        return False
    command_ids = {
        command["command_id"] for command in task["acceptance"]["commands"]
        if command["gate"] == "AFFECTED_INTEGRATION"
    }
    if not command_ids:
        return False
    receipts = [
        _load_cas_json(
            paths, "receipts", digest, "command-receipt-v1.schema.json"
        )
        for digest in review["command_receipt_sha256s"]
    ]
    matching = {
        receipt["command_id"] for receipt in receipts
        if receipt["task_manifest_sha256"] == sha256_hex(canonical_json_bytes(task))
        and receipt["gate"] == "AFFECTED_INTEGRATION"
        and receipt["patch_sha256"] == patch_sha
        and receipt["correction_round"] == task["correction_round"]
        and receipt["outcome"] == "PASS"
    }
    return matching == command_ids


def _reviews_for_assignment(
    paths: BuildPaths, assignment_sha: str
) -> tuple[tuple[StoredObject, dict[str, Any]], ...]:
    return tuple(
        (stored, payload)
        for stored, payload in _iter_cas_json(
            paths, "receipts", "review-receipt-v1.schema.json"
        )
        if payload["external_session_assignment_sha256"] == assignment_sha
    )


def _review_receipt(
    stored: StoredObject,
    payload: Mapping[str, Any],
    *,
    successor: str | None,
    replayed: bool | None = None,
    preflight_sha: str | None = None,
) -> ReviewReceipt:
    source = payload["source_verification"]
    return ReviewReceipt(
        sha256=stored.sha256,
        path=stored.path,
        replayed=stored.replayed if replayed is None else replayed,
        transport=HandoffTransport(payload["transport"]),
        task_manifest_sha256=payload["task_manifest_sha256"],
        handoff_sha256=payload["handoff_sha256"],
        reviewer_profile_sha256=payload["reviewer_profile_sha256"],
        external_session_assignment_sha256=payload["external_session_assignment_sha256"],
        raw_evidence_sha256=payload["raw_evidence_sha256"],
        verdict=payload["verdict"],
        findings=tuple(dict(finding) for finding in payload["findings"]),
        command_receipt_sha256s=tuple(payload["command_receipt_sha256s"]),
        source_record_ids=tuple(source["source_record_ids"]),
        source_packet_ids=tuple(source["source_packet_ids"]),
        started_at=payload["started_at"],
        duration_ms=payload["duration_ms"],
        target_host_eligible=payload["transport"] == "LOCAL_ADAPTER",
        successor_task_manifest_sha256=successor,
        preflight_receipt_sha256=preflight_sha,
    )


def _builder_profile_sha(paths: BuildPaths, handoff: Mapping[str, Any]) -> str:
    if handoff["transport"] == "EXTERNAL_SESSION":
        _, assignment, _ = _load_assignment(
            paths, handoff["external_session_assignment_sha256"]
        )
        return assignment["worker_profile_sha256"]
    attempt = _load_cas_json(
        paths,
        "attempts",
        handoff["attempt_manifest_sha256"],
        "attempt-manifest-v1.schema.json",
    )
    return attempt["worker_profile_sha256"]


def _run_local_reviewer(
    paths: BuildPaths,
    task_sha: str,
    task: Mapping[str, Any],
    handoff_sha: str,
    reviewer_profile_sha: str,
    adapter: Any,
    worktree: Path,
) -> dict[str, Any]:
    try:
        executable_sha = hashlib.sha256(adapter.executable.read_bytes()).hexdigest()
    except OSError as error:
        raise ContractError("local reviewer executable became unavailable") from error
    if executable_sha != adapter.executable_sha256:
        raise ContractError("local reviewer executable hash changed after preflight")
    missing = [name for name in adapter.environment_variable_names if name not in os.environ]
    if missing:
        raise ContractError("local reviewer environment became unavailable after preflight")
    root = paths.state_root / "reviews"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="local-review-", dir=root))
    run.chmod(0o700)
    task_path = run / "task.json"
    output_path = run / "review.json"
    task_path.write_bytes(canonical_json_bytes(task))
    task_path.chmod(0o600)
    values = {
        "{task_manifest}": str(task_path),
        "{handoff_path}": str(output_path),
        "{checkpoint_dir}": str(run),
        "{worktree}": str(worktree),
        "{attempt_manifest_sha256}": handoff_sha,
        "{task_manifest_sha256}": task_sha,
    }
    argv = [str(adapter.executable)]
    for argument in adapter.dispatch_argv:
        argv.append(values.get(argument, argument))
    timeout = min(adapter.attempt_timeout_seconds, task["budgets"]["implementation_seconds"])
    started = time.monotonic()
    try:
        result = run_bounded_process(
            argv,
            canonical_json_bytes(task),
            worktree,
            {name: os.environ[name] for name in adapter.environment_variable_names},
            timeout,
            adapter.max_capture_bytes,
        )
    except ProcessLaunchError as error:
        raise ContractError("local reviewer launch failed") from error
    put_private_bytes(paths, "local-review-stdout", result.stdout)
    put_private_bytes(paths, "local-review-stderr", result.stderr)
    if (
        result.returncode != 0
        or result.timed_out
        or result.capture_incomplete
        or result.stdout_truncated
        or result.stderr_truncated
    ):
        raise ContractError("local reviewer did not return one finite successful result")
    raw = _read_untrusted_file(output_path, "local review", adapter.max_capture_bytes)
    put_private_bytes(paths, "local-review-raw", raw)
    payload = _strict_json_bytes(raw, "local review")
    if payload.get("protocol") != adapter.result_protocol:
        raise ContractError("local reviewer result protocol mismatch")
    del started, reviewer_profile_sha
    return payload


def _successors_for(paths: BuildPaths, predecessor_sha: str) -> tuple[StoredObject, ...]:
    root = paths.repo_root / "build_control" / "tasks"
    found: list[StoredObject] = []
    registry = SchemaRegistry(paths.repo_root)
    for path in sorted(root.rglob("*.json")):
        content = path.read_bytes()
        payload = _strict_json_bytes(content, "task successor")
        if payload.get("protocol") != "helios.build.task-manifest/v1":
            continue
        registry.validate(payload, "task-manifest-v1.schema.json")
        digest = sha256_hex(canonical_json_bytes(payload))
        if payload.get("supersedes_task_manifest_sha256") == predecessor_sha:
            if path.name != f"{digest}.json":
                raise ContractError("task successor filename/content digest mismatch")
            found.append(StoredObject(digest, path, True))
    return tuple(found)


def _validate_existing_correction_route(
    paths: BuildPaths, predecessor_sha: str, node_id: str, successor_sha: str
) -> None:
    routes = [
        route for route in replay_events(paths.repo_root / _ROUTING)
        if route.get("task_manifest_sha256") == predecessor_sha
        or route.get("successor_task_manifest_sha256") == successor_sha
    ]
    if (
        len(routes) != 1
        or routes[0].get("task_manifest_sha256") != predecessor_sha
        or routes[0].get("successor_task_manifest_sha256") != successor_sha
        or routes[0].get("action") != "CORRECTION"
        or routes[0].get("reason_code") != "REVIEW_CORRECTION"
    ):
        raise ContractError("superseded correction task lacks its exact immutable route")
    lifecycle = [
        event for event in replay_events(paths.repo_root / _EVENTS)
        if event.get("node_id") == node_id
    ]
    if (
        not lifecycle
        or lifecycle[-1].get("new_state") != "SUPERSEDED"
        or lifecycle[-1].get("routing_event_sha256") != routes[0]["event_sha256"]
    ):
        raise ContractError("superseded correction task lacks route-bound lifecycle evidence")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
