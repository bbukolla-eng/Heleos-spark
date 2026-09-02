"""Collect worker output from Git truth into sanitized immutable receipts."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

from tools.helios_build._external_cas import read_private_bytes
from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build.errors import ContractError, TransitionError
from tools.helios_build.ledger import replay_events
from tools.helios_build.ownership import OwnershipClaim, assert_owned_changes
from tools.helios_build.paths import BuildPaths
from tools.helios_build.profiles import (
    WorkerProfile,
    load_worker_profile_bytes,
    worker_profile_sha256,
)
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore, StoredObject
from tools.helios_build.types import HandoffTransport, NodeState
from tools.helios_build.worktrees import verify_detached_worktree


_EVENTS = Path("build_control/graph/events.jsonl")
_GRAPH_ROOT = Path("build_control/graph")
_LATER_HANDOFF_STATES = frozenset({
    NodeState.RETURNED,
    NodeState.REVIEWED,
    NodeState.ACCEPTED,
    NodeState.INTEGRATED,
    NodeState.SUPERSEDED,
})


@dataclass(frozen=True, slots=True)
class HandoffReceipt:
    sha256: str
    path: Path
    replayed: bool
    transport: HandoffTransport
    attempt_manifest_sha256: str | None
    external_session_assignment_sha256: str | None
    task_manifest_sha256: str
    output_kind: str
    patch_sha256: str | None
    canonical_patch_sha256: str
    commit_sha: str | None
    files_changed: tuple[str, ...]
    commands_executed: tuple[str, ...]
    focused_test_results: tuple[str, ...]
    assumptions: tuple[str, ...]
    unresolved_issues: tuple[str, ...]
    dependency_effects: tuple[str, ...]
    security_effects: tuple[str, ...]
    source_record_ids: tuple[str, ...]
    source_packet_ids: tuple[str, ...]
    raw_evidence_sha256: str | None
    duration_ms: int
    cost_microusd: int
    command_receipt_sha256s: tuple[str, ...] = ()
    target_host_eligible: bool = False


def collect_task(paths: BuildPaths, task_ref: str | Path) -> HandoffReceipt:
    """Validate one successful local-adapter handoff against the attempt worktree."""
    _require_paths(paths)
    task_sha, task, _ = _load_committed_task(paths, task_ref)
    node_id = _task_node_id(paths, task_sha)
    events = replay_events(paths.repo_root / _EVENTS)
    state = _node_state(events, node_id)
    dispatches = [
        event for event in events
        if event.get("node_id") == node_id and event.get("new_state") == "DISPATCHED"
    ]
    if len(dispatches) != 1:
        raise ContractError("local collection requires one exact dispatch event")
    dispatch = dispatches[0]
    attempt_sha = dispatch.get("attempt_manifest_sha256")
    if not _is_digest(attempt_sha) or dispatch.get("external_session_assignment_sha256") is not None:
        raise ContractError("local collection requires exactly one local attempt dispatch proof")
    attempt = _load_cas_json(paths, "attempts", attempt_sha, "attempt-manifest-v1.schema.json")
    if (
        attempt["task_manifest_sha256"] != task_sha
        or attempt["base_commit_sha"] != task["base_commit_sha"]
    ):
        raise ContractError("local attempt task/base lineage mismatch")
    profile_id = task["roles"]["builder_profile_id"]
    assert isinstance(profile_id, str)
    profile, _ = _load_committed_profile(paths, profile_id)
    if attempt["worker_profile_sha256"] != worker_profile_sha256(profile):
        raise ContractError("local attempt worker profile lineage mismatch")

    raw_payload = _local_raw_handoff(paths, attempt_sha, task, attempt)
    existing = _handoffs_for_proof(paths, "attempt_manifest_sha256", attempt_sha)
    if existing:
        if len(existing) != 1:
            raise ContractError("local attempt has multiple immutable handoff receipts")
        stored, payload = existing[0]
        patch, changed = _derive_local_truth(paths, task, attempt, payload)
        _validate_handoff_truth(task, payload, patch, changed)
        normalized_raw = dict(raw_payload)
        normalized_raw["files_changed"] = list(changed)
        if canonical_json_bytes(payload) != canonical_json_bytes(normalized_raw):
            raise ContractError("local attempt has a differing handoff replay")
        if state is NodeState.DISPATCHED:
            _transition_returned(paths, node_id, "LOCAL_ADAPTER_HANDOFF_COLLECTED")
        elif state not in _LATER_HANDOFF_STATES:
            raise TransitionError(f"node {node_id} is {state.value}, not collectable")
        return _handoff_receipt(
            stored,
            payload,
            sha256_hex(patch),
            (),
            replayed=True,
        )
    if state is not NodeState.DISPATCHED:
        raise TransitionError(f"node {node_id} is {state.value}, not expected DISPATCHED")

    patch, changed = _derive_local_truth(paths, task, attempt, raw_payload)
    _validate_handoff_truth(task, raw_payload, patch, changed)
    canonical_patch = ContentAddressedStore(paths.repo_root / _GRAPH_ROOT).put_bytes(
        "patches", patch, ".patch"
    )
    if canonical_patch.sha256 != sha256_hex(patch):
        raise ContractError("canonical patch publication changed its digest")
    from tools.helios_build.verification import run_verification_gate

    commands = run_verification_gate(paths, task, _attempt_worktree(paths, attempt), "FOCUSED")
    sanitized = dict(raw_payload)
    sanitized["files_changed"] = list(changed)
    SchemaRegistry(paths.repo_root).validate(sanitized, "worker-handoff-v1.schema.json")
    stored = ContentAddressedStore(paths.repo_root / _GRAPH_ROOT).put_json(
        "receipts", sanitized
    )
    _transition_returned(paths, node_id, "LOCAL_ADAPTER_HANDOFF_COLLECTED")
    return _handoff_receipt(
        stored,
        sanitized,
        canonical_patch.sha256,
        tuple(receipt.sha256 for receipt in commands),
    )


def _local_raw_handoff(
    paths: BuildPaths,
    attempt_sha: str,
    task: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> dict[str, Any]:
    artifacts = []
    for _, payload in _iter_cas_json(paths, "artifacts", "artifact-manifest-v1.schema.json"):
        if (
            payload.get("attempt_manifest_sha256") == attempt_sha
            and payload.get("kind") == "LOCAL_WORKER_HANDOFF_RAW"
        ):
            artifacts.append(payload)
    if len(artifacts) != 1:
        raise ContractError("local attempt does not resolve to one raw handoff artifact")
    artifact = artifacts[0]
    raw = read_private_bytes(
        paths,
        artifact["store_key"],
        artifact["sha256"],
        artifact["byte_size"],
    )
    payload = _strict_json_bytes(raw, "local worker handoff")
    SchemaRegistry(paths.repo_root).validate(payload, "worker-handoff-v1.schema.json")
    expected = {
        "transport": "LOCAL_ADAPTER",
        "attempt_manifest_sha256": attempt_sha,
        "external_session_assignment_sha256": None,
        "task_manifest_sha256": attempt["task_manifest_sha256"],
        "raw_evidence_sha256": None,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ContractError("local worker handoff protocol or lineage mismatch")
    _validate_source_lineage(task, payload["source_record_ids"], payload["source_packet_ids"])
    return payload


def _derive_local_truth(
    paths: BuildPaths,
    task: Mapping[str, Any],
    attempt: Mapping[str, Any],
    handoff: Mapping[str, Any],
) -> tuple[bytes, tuple[str, ...]]:
    worktree = _attempt_worktree(paths, attempt)
    base = task["base_commit_sha"]
    assert isinstance(base, str)
    kind = handoff["output_kind"]
    if kind == "PATCH":
        verify_detached_worktree(
            paths, worktree, base, require_clean=False, require_exact_head=True
        )
        _intent_to_add_untracked(worktree)
        return _derive_worktree_truth(worktree, base)
    if kind != "COMMIT":
        raise ContractError("worker handoff output kind is unsupported")
    commit = handoff["commit_sha"]
    if not isinstance(commit, str):
        raise ContractError("commit handoff lacks its commit")
    truth = _derive_commit_truth(paths.repo_root, base, commit)
    verify_detached_worktree(
        paths, worktree, base, require_clean=True, require_exact_head=False
    )
    head = _git_text(worktree, "rev-parse", "HEAD").strip()
    if head != commit:
        raise ContractError("reported commit is not the exact attempt worktree HEAD")
    return truth


def _validate_handoff_truth(
    task: Mapping[str, Any],
    handoff: Mapping[str, Any],
    patch: bytes,
    changed: tuple[str, ...],
) -> None:
    if not patch or not changed:
        raise ContractError("worker handoff has no Git-derived change")
    if set(handoff["files_changed"]) != set(changed) or len(handoff["files_changed"]) != len(changed):
        raise ContractError("worker handoff files_changed does not match Git-derived paths")
    if handoff["output_kind"] == "PATCH" and handoff["patch_sha256"] != sha256_hex(patch):
        raise ContractError("worker handoff patch digest does not match Git-derived patch")
    _validate_scope_and_impacts(task, changed, handoff["dependency_effects"])


def _validate_scope_and_impacts(
    task: Mapping[str, Any],
    changed_paths: tuple[str, ...],
    dependency_effects: object,
) -> None:
    assert_owned_changes(OwnershipClaim.from_manifest(task), changed_paths)
    ownership = task["ownership"]
    assert isinstance(ownership, Mapping)
    schema_impact = set(task["schema_impact"])
    for path in changed_paths:
        if path in ownership["schemas"] and not _declared_impact(schema_impact, "SCHEMA", path):
            raise ContractError(f"changed schema lacks declared schema impact: {path}")
        if path in ownership["migrations"] and not _declared_impact(schema_impact, "MIGRATION", path):
            raise ContractError(f"changed migration lacks declared schema impact: {path}")
    if not isinstance(dependency_effects, list):
        raise ContractError("handoff dependency_effects must be an array")
    public = set(ownership["public_interfaces"])
    frozen = set(task["frozen_interfaces"])
    for effect in dependency_effects:
        if not isinstance(effect, str):
            raise ContractError("handoff dependency effect must be a string")
        if effect.startswith("PUBLIC_INTERFACE:"):
            interface = effect.removeprefix("PUBLIC_INTERFACE:")
            if interface not in public:
                raise ContractError(f"undeclared public interface impact: {interface}")
            if interface in frozen:
                raise ContractError(f"handoff changes frozen interface: {interface}")
        elif effect.startswith("SCHEMA:"):
            path = effect.removeprefix("SCHEMA:")
            if path not in ownership["schemas"] or not _declared_impact(schema_impact, "SCHEMA", path):
                raise ContractError(f"undeclared schema impact: {path}")
        elif effect.startswith("MIGRATION:"):
            path = effect.removeprefix("MIGRATION:")
            if path not in ownership["migrations"] or not _declared_impact(schema_impact, "MIGRATION", path):
                raise ContractError(f"undeclared migration impact: {path}")


def _declared_impact(declared: set[object], kind: str, path: str) -> bool:
    return path in declared or f"{kind}:{path}" in declared


def _validate_source_lineage(
    task: Mapping[str, Any], record_ids: object, packet_ids: object
) -> None:
    if record_ids != task["required_source_record_ids"]:
        raise ContractError("handoff source_record_ids do not match the task")
    if packet_ids != task["required_source_packet_ids"]:
        raise ContractError("handoff source_packet_ids do not match the task")
    total = len(task["required_source_record_ids"]) + len(task["required_source_packet_ids"])
    if total < task["evidence_threshold"]:
        raise ContractError("task evidence threshold exceeds its required source lineage")


def _derive_worktree_truth(worktree: Path, base_commit: str) -> tuple[bytes, tuple[str, ...]]:
    _require_commit(base_commit, "task base commit")
    _require_commit_exists(worktree, base_commit)
    patch = _git_bytes(worktree, "diff", "--binary", "--full-index", base_commit, "--")
    names = _git_bytes(
        worktree, "diff", "--name-only", "-z", "--no-renames", base_commit, "--"
    )
    return patch, _decode_git_paths(names)


def _derive_commit_truth(
    repo: Path, base_commit: str, commit: str
) -> tuple[bytes, tuple[str, ...]]:
    _require_commit(base_commit, "task base commit")
    _require_commit(commit, "worker commit")
    _require_commit_exists(repo, base_commit)
    _require_commit_exists(repo, commit)
    ancestor = _git(repo, "merge-base", "--is-ancestor", base_commit, commit, check=False)
    if ancestor.returncode != 0:
        raise ContractError("worker commit does not descend from the exact base")
    patch = _git_bytes(
        repo, "diff", "--binary", "--full-index", base_commit, commit, "--"
    )
    names = _git_bytes(
        repo, "diff", "--name-only", "-z", "--no-renames", base_commit, commit, "--"
    )
    return patch, _decode_git_paths(names)


def _apply_patch_in_worktree(worktree: Path, patch: bytes) -> tuple[bytes, tuple[str, ...]]:
    checked = _git(
        worktree,
        "apply",
        "--check",
        "--index",
        "--binary",
        "--whitespace=nowarn",
        "-",
        input_bytes=patch,
        check=False,
    )
    if checked.returncode != 0:
        raise ContractError("submitted patch does not apply cleanly to the exact base")
    applied = _git(
        worktree,
        "apply",
        "--index",
        "--binary",
        "--whitespace=nowarn",
        "-",
        input_bytes=patch,
        check=False,
    )
    if applied.returncode != 0:
        raise ContractError("submitted patch failed after a successful apply check")
    head = _git_text(worktree, "rev-parse", "HEAD").strip()
    return _derive_worktree_truth(worktree, head)


def _intent_to_add_untracked(worktree: Path) -> None:
    raw = _git_bytes(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    paths = _decode_git_paths(raw)
    if paths:
        _git(worktree, "add", "-N", "--", *paths)


def _decode_git_paths(content: bytes) -> tuple[str, ...]:
    if not content:
        return ()
    if not content.endswith(b"\x00"):
        raise ContractError("Git path output is not NUL terminated")
    decoded: list[str] = []
    for raw in content[:-1].split(b"\x00"):
        try:
            value = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ContractError("Git-derived path is not UTF-8") from error
        path = PurePosixPath(value)
        if (
            not value
            or value.startswith("/")
            or "\\" in value
            or any(part in {"", ".", "..", ".git"} for part in path.parts)
            or any(ord(character) < 32 for character in value)
        ):
            raise ContractError(f"Git-derived path is unsafe: {value!r}")
        decoded.append(value)
    if len(set(decoded)) != len(decoded):
        raise ContractError("Git-derived paths contain duplicates")
    return tuple(sorted(decoded))


def _load_committed_task(
    paths: BuildPaths, task_ref: str | Path
) -> tuple[str, dict[str, Any], str]:
    _require_paths(paths)
    head = _head_sha(paths.repo_root)
    tree = _git_tree(paths.repo_root, head, "build_control/tasks")
    candidates: list[tuple[str, bytes, dict[str, Any], str]] = []
    for relative in tree:
        if not relative.endswith(".json"):
            continue
        content = _git_show(paths.repo_root, head, relative)
        payload = _strict_json_bytes(content, "committed task manifest")
        if payload.get("protocol") != "helios.build.task-manifest/v1":
            continue
        SchemaRegistry(paths.repo_root).validate(payload, "task-manifest-v1.schema.json")
        digest = sha256_hex(canonical_json_bytes(payload))
        if Path(relative).name != f"{digest}.json":
            raise ContractError("committed task filename/content digest mismatch")
        candidates.append((relative, content, payload, digest))
    if isinstance(task_ref, Path):
        candidate_path = task_ref if task_ref.is_absolute() else paths.repo_root / task_ref
        try:
            requested = candidate_path.resolve().relative_to(paths.repo_root).as_posix()
        except ValueError as error:
            raise ContractError("task path is outside the repository") from error
        matches = [candidate for candidate in candidates if candidate[0] == requested]
    else:
        if not _is_digest(task_ref):
            raise ContractError("task manifest reference must be a SHA-256 digest")
        matches = [candidate for candidate in candidates if candidate[3] == task_ref]
    if len(matches) != 1:
        raise ContractError("task manifest reference does not resolve exactly once in HEAD")
    relative, content, payload, digest = matches[0]
    _assert_working_file(paths.repo_root / relative, content, "task manifest")
    return digest, payload, relative


def _load_committed_profile(
    paths: BuildPaths, profile_id: str
) -> tuple[WorkerProfile, bytes]:
    if not isinstance(profile_id, str) or not profile_id:
        raise ContractError("worker profile ID must be non-empty")
    relative = f"build_control/worker_profiles/{profile_id}.json"
    head = _head_sha(paths.repo_root)
    if relative not in _git_tree(paths.repo_root, head, "build_control/worker_profiles"):
        raise ContractError("worker profile does not resolve exactly in HEAD")
    content = _git_show(paths.repo_root, head, relative)
    _assert_working_file(paths.repo_root / relative, content, "worker profile")
    profile = load_worker_profile_bytes(content, SchemaRegistry(paths.repo_root))
    if profile.profile_id != profile_id:
        raise ContractError("worker profile filename/content identity mismatch")
    return profile, content


def _task_node_id(paths: BuildPaths, task_sha: str) -> str:
    head = _head_sha(paths.repo_root)
    matches: list[str] = []
    for relative in _git_tree(paths.repo_root, head, "build_control/graph"):
        if Path(relative).parent.as_posix() != "build_control/graph" or not relative.endswith(".json"):
            continue
        content = _git_show(paths.repo_root, head, relative)
        payload = _strict_json_bytes(content, "committed graph manifest")
        if payload.get("protocol") != "helios.build.graph-manifest/v1":
            continue
        SchemaRegistry(paths.repo_root).validate(payload, "graph-manifest-v1.schema.json")
        _assert_working_file(paths.repo_root / relative, content, "graph manifest")
        for node in payload["nodes"]:
            if node["node_type"] == "BuildTask" and node["manifest_sha256"] == task_sha:
                matches.append(node["node_id"])
    if len(matches) != 1:
        raise ContractError("task must resolve to exactly one committed BuildTask graph node")
    return matches[0]


def _load_cas_json(
    paths: BuildPaths, collection: str, digest: str, schema: str
) -> dict[str, Any]:
    if not _is_digest(digest):
        raise ContractError(f"{collection} reference is not a SHA-256 digest")
    path = (
        paths.repo_root / _GRAPH_ROOT / collection / "sha256" / digest[:2]
        / f"{digest}.json"
    )
    if not path.is_file():
        raise ContractError(f"{collection} object does not resolve exactly: {digest}")
    content = path.read_bytes()
    payload = _strict_json_bytes(content, f"{collection} object")
    if sha256_hex(content) != digest or canonical_json_bytes(payload) != content:
        raise ContractError(f"{collection} CAS filename/content mismatch")
    SchemaRegistry(paths.repo_root).validate(payload, schema)
    return payload


def _iter_cas_json(
    paths: BuildPaths, collection: str, schema: str
) -> tuple[tuple[StoredObject, dict[str, Any]], ...]:
    root = paths.repo_root / _GRAPH_ROOT / collection / "sha256"
    if not root.is_dir():
        return ()
    found: list[tuple[StoredObject, dict[str, Any]]] = []
    protocols = {
        "artifact-manifest-v1.schema.json": "helios.build.artifact-manifest/v1",
        "worker-handoff-v1.schema.json": "helios.build.worker-handoff/v1",
        "review-receipt-v1.schema.json": "helios.build.review-receipt/v1",
        "external-session-assignment-v1.schema.json":
            "helios.build.external-session-assignment/v1",
    }
    expected_protocol = protocols.get(schema)
    for path in sorted(root.glob("*/*.json")):
        content = path.read_bytes()
        payload = _strict_json_bytes(content, f"{collection} object")
        if expected_protocol is not None and payload.get("protocol") != expected_protocol:
            continue
        digest = sha256_hex(content)
        if path.name != f"{digest}.json" or canonical_json_bytes(payload) != content:
            raise ContractError(f"{collection} CAS filename/content mismatch")
        SchemaRegistry(paths.repo_root).validate(payload, schema)
        found.append((StoredObject(digest, path, True), payload))
    return tuple(found)


def _handoffs_for_proof(
    paths: BuildPaths, key: str, digest: str
) -> tuple[tuple[StoredObject, dict[str, Any]], ...]:
    return tuple(
        (stored, payload)
        for stored, payload in _iter_cas_json(
            paths, "receipts", "worker-handoff-v1.schema.json"
        )
        if payload.get("protocol") == "helios.build.worker-handoff/v1"
        and payload.get(key) == digest
    )


def _handoff_receipt(
    stored: StoredObject,
    payload: Mapping[str, Any],
    canonical_patch_sha: str,
    command_receipts: tuple[str, ...],
    *,
    replayed: bool | None = None,
) -> HandoffReceipt:
    return HandoffReceipt(
        sha256=stored.sha256,
        path=stored.path,
        replayed=stored.replayed if replayed is None else replayed,
        transport=HandoffTransport(payload["transport"]),
        attempt_manifest_sha256=payload["attempt_manifest_sha256"],
        external_session_assignment_sha256=payload["external_session_assignment_sha256"],
        task_manifest_sha256=payload["task_manifest_sha256"],
        output_kind=payload["output_kind"],
        patch_sha256=payload["patch_sha256"],
        canonical_patch_sha256=canonical_patch_sha,
        commit_sha=payload["commit_sha"],
        files_changed=tuple(payload["files_changed"]),
        commands_executed=tuple(payload["commands_executed"]),
        focused_test_results=tuple(payload["focused_test_results"]),
        assumptions=tuple(payload["assumptions"]),
        unresolved_issues=tuple(payload["unresolved_issues"]),
        dependency_effects=tuple(payload["dependency_effects"]),
        security_effects=tuple(payload["security_effects"]),
        source_record_ids=tuple(payload["source_record_ids"]),
        source_packet_ids=tuple(payload["source_packet_ids"]),
        raw_evidence_sha256=payload["raw_evidence_sha256"],
        duration_ms=payload["duration_ms"],
        cost_microusd=payload["cost_microusd"],
        command_receipt_sha256s=command_receipts,
        target_host_eligible=payload["transport"] == "LOCAL_ADAPTER",
    )


def _attempt_worktree(paths: BuildPaths, attempt: Mapping[str, Any]) -> Path:
    key = attempt["worktree_key"]
    if not _is_digest(key):
        raise ContractError("attempt worktree key is invalid")
    return paths.state_root / "worktrees" / key


def _transition_returned(paths: BuildPaths, node_id: str, reason: str) -> None:
    from tools.helios_build.graph import transition_node

    transition_node(
        paths,
        node_id,
        NodeState.DISPATCHED,
        NodeState.RETURNED,
        reason,
        "codex-control-v1",
    )


def _node_state(events: tuple[dict[str, Any], ...], node_id: str) -> NodeState:
    matches = [event for event in events if event.get("node_id") == node_id]
    if not matches:
        return NodeState.DRAFT
    try:
        return NodeState(matches[-1]["new_state"])
    except ValueError as error:
        raise ContractError("lifecycle event has an invalid state") from error


def _head_sha(repo: Path) -> str:
    value = _git_text(repo, "rev-parse", "HEAD").strip()
    _require_commit(value, "repository HEAD")
    return value


def _git_tree(repo: Path, head: str, prefix: str) -> tuple[str, ...]:
    content = _git_bytes(repo, "ls-tree", "-r", "-z", "--name-only", head, "--", prefix)
    if not content:
        return ()
    if not content.endswith(b"\x00"):
        raise ContractError("captured Git tree is not NUL terminated")
    try:
        return tuple(item.decode("utf-8", errors="strict") for item in content[:-1].split(b"\x00"))
    except UnicodeDecodeError as error:
        raise ContractError("captured Git tree path is not UTF-8") from error


def _git_show(repo: Path, head: str, relative: str) -> bytes:
    result = _git(repo, "show", f"{head}:{relative}", check=False)
    if result.returncode != 0:
        raise ContractError(f"cannot resolve committed object: {relative}")
    return result.stdout


def _assert_working_file(path: Path, expected: bytes, label: str) -> None:
    try:
        current = path.read_bytes()
    except OSError as error:
        raise ContractError(f"cannot read working {label}: {error}") from error
    if current != expected:
        raise ContractError(f"working {label} differs from captured HEAD")


def _strict_json_bytes(content: bytes, label: str) -> dict[str, Any]:
    try:
        source = content.decode("utf-8", errors="strict")
        payload = json.loads(
            source,
            object_pairs_hook=_unique_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ContractError(f"{label} root must be an object")
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate object key: {key}")
        result[key] = value
    return result


def _reject_float(_: str) -> NoReturn:
    raise ContractError("floating-point values are forbidden in Build Fabric JSON")


def _reject_constant(value: str) -> NoReturn:
    raise ContractError(f"invalid JSON constant: {value}")


def _git_bytes(repo: Path, *argv: str) -> bytes:
    return _git(repo, *argv).stdout


def _git_text(repo: Path, *argv: str) -> str:
    try:
        return _git_bytes(repo, *argv).decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ContractError("Git output is not UTF-8") from error


def _git(
    repo: Path,
    *argv: str,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(repo), *argv],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise ContractError(f"Git command failed: {' '.join(argv)}")
    return result


def _require_commit_exists(repo: Path, commit: str) -> None:
    result = _git(repo, "cat-file", "-e", f"{commit}^{{commit}}", check=False)
    if result.returncode != 0:
        raise ContractError("Git commit does not resolve exactly")


def _require_commit(value: object, label: str) -> None:
    if not isinstance(value, str) or len(value) != 40 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ContractError(f"{label} must be lowercase 40-character Git hex")


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _require_paths(paths: object) -> None:
    if not isinstance(paths, BuildPaths):
        raise ContractError("Build Fabric operation requires BuildPaths")
