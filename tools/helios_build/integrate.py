"""Verify an already-created canonical commit and record controlled integration."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from tools.helios_build.canonical import canonical_json_bytes, load_strict_json, sha256_hex
from tools.helios_build.collect import (
    _derive_commit_truth,
    _is_digest,
    _iter_cas_json,
    _load_committed_task,
    _node_state,
    _task_node_id,
)
from tools.helios_build.errors import ContractError
from tools.helios_build.external_sessions import _task_handoffs
from tools.helios_build.graph import ControllerLock, transition_node_locked
from tools.helios_build.ledger import replay_events
from tools.helios_build.paths import BuildPaths
from tools.helios_build.review import _builder_profile_sha, _exact_handoff, _may_accept
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore, StoredObject
from tools.helios_build.types import NodeState
from tools.helios_build.verification import CommandReceipt, run_verification_gate
from tools.helios_build.worktrees import create_detached_worktree


_EVENTS = Path("build_control/graph/events.jsonl")
_INTEGRATOR = "codex-control-v1"


@dataclass(frozen=True, slots=True)
class IntegrationReceipt:
    """Immutable proof that Codex verified, but did not create, a canonical commit."""

    sha256: str
    path: Path
    replayed: bool
    task_manifest_sha256: str
    handoff_sha256: str
    review_receipt_sha256: str
    integrator_profile_id: str
    canonical_parent_sha: str
    integrated_commit_sha: str
    changed_paths: tuple[str, ...]
    command_receipt_sha256s: tuple[str, ...]
    recorded_at: str


def record_integration(
    paths: BuildPaths,
    task_ref: str | Path,
    commit_sha: str | None = None,
) -> IntegrationReceipt:
    """Verify one accepted handoff against canonical Git truth and record it.

    The canonical commit must already be the repository ``HEAD``.  This function
    creates no canonical Git state: it never stages, commits, pushes, opens a pull
    request, or merges.
    """
    if not isinstance(paths, BuildPaths):
        raise ContractError("integration requires BuildPaths")
    if commit_sha is not None and not _is_commit_sha(commit_sha):
        raise ContractError("integrated commit SHA must be lowercase 40-hex")

    task_sha, task, task_relative = _load_committed_task(paths, task_ref)
    node_id = _task_node_id(paths, task_sha)
    existing = _integration_receipts(paths, task_sha)
    state = _node_state(replay_events(paths.repo_root / _EVENTS), node_id)
    if state is NodeState.INTEGRATED:
        return _replayed_integration(existing, commit_sha)
    if state is not NodeState.ACCEPTED:
        raise ContractError("integration requires an accepted independent review")
    if task["roles"]["integrator_profile_id"] != _INTEGRATOR:
        raise ContractError("integration requires the codex-control-v1 integrator")
    if existing:
        raise ContractError("accepted task already has an integration receipt")

    handoffs = _task_handoffs(paths, task_sha)
    if len(handoffs) != 1:
        raise ContractError("integration requires one exact verified handoff")
    handoff_stored, handoff = handoffs[0]
    reviews = _task_reviews(paths, task_sha, handoff_stored.sha256)
    if len(reviews) != 1:
        raise ContractError("integration requires one accepted independent review")
    review_stored, review = reviews[0]
    if review["verdict"] != "ACCEPTED" or any(
        finding["severity"] == "CRITICAL" for finding in review["findings"]
    ):
        raise ContractError("integration requires one accepted independent review")
    handoff_stored, handoff, canonical_patch = _exact_handoff(
        paths, task, handoff_stored.sha256
    )
    patch_sha = sha256_hex(canonical_patch)
    if not _may_accept(paths, task, review, patch_sha):
        raise ContractError("integration requires an accepted independent review")
    if review["reviewer_profile_sha256"] == _builder_profile_sha(paths, handoff):
        raise ContractError("integration requires an independent reviewer")

    integrated, parent, changed_paths = _verify_canonical_commit(
        paths,
        task,
        task_relative,
        canonical_patch,
        tuple(sorted(handoff["files_changed"])),
        commit_sha,
    )
    commands = _record_remaining_gates(paths, task, integrated)
    command_shas = tuple(
        dict.fromkeys(
            [*review["command_receipt_sha256s"], *(item.sha256 for item in commands)]
        )
    )
    recorded_at = _utc_now()
    payload: dict[str, Any] = {
        "protocol": "helios.build.integration-receipt/v1",
        "task_manifest_sha256": task_sha,
        "handoff_sha256": handoff_stored.sha256,
        "review_receipt_sha256": review_stored.sha256,
        "integrator_profile_id": _INTEGRATOR,
        "canonical_parent_sha": parent,
        "integrated_commit_sha": integrated,
        "changed_paths": list(changed_paths),
        "command_receipt_sha256s": list(command_shas),
        "recorded_at": recorded_at,
    }
    SchemaRegistry(paths.repo_root).validate(
        payload, "integration-receipt-v1.schema.json"
    )

    with ControllerLock(paths) as lock:
        locked_state = _node_state(
            replay_events(paths.repo_root / _EVENTS), node_id
        )
        concurrent = _integration_receipts(paths, task_sha)
        if locked_state is NodeState.INTEGRATED:
            return _replayed_integration(concurrent, integrated)
        if locked_state is not NodeState.ACCEPTED:
            raise ContractError("integration requires an accepted independent review")
        if concurrent:
            raise ContractError("accepted task already has an integration receipt")
        stored = ContentAddressedStore(
            paths.repo_root / "build_control" / "graph"
        ).put_json("receipts", payload)
        transition_node_locked(
            lock,
            node_id,
            NodeState.ACCEPTED,
            NodeState.INTEGRATED,
            "CANONICAL_COMMIT_VERIFIED",
            _INTEGRATOR,
        )
    return _integration_receipt(stored, payload)


def _task_reviews(
    paths: BuildPaths, task_sha: str, handoff_sha: str
) -> tuple[tuple[StoredObject, dict[str, Any]], ...]:
    return tuple(
        (stored, payload)
        for stored, payload in _iter_cas_json(
            paths, "receipts", "review-receipt-v1.schema.json"
        )
        if payload["task_manifest_sha256"] == task_sha
        and payload["handoff_sha256"] == handoff_sha
    )


def _integration_receipts(
    paths: BuildPaths, task_sha: str
) -> tuple[tuple[StoredObject, dict[str, Any]], ...]:
    root = (
        paths.repo_root / "build_control" / "graph" / "receipts" / "sha256"
    )
    if not root.is_dir():
        return ()
    registry = SchemaRegistry(paths.repo_root)
    found: list[tuple[StoredObject, dict[str, Any]]] = []
    for path in sorted(root.glob("*/*.json")):
        payload = load_strict_json(path)
        if payload.get("protocol") != "helios.build.integration-receipt/v1":
            continue
        content = canonical_json_bytes(payload)
        digest = sha256_hex(content)
        if path.name != f"{digest}.json" or path.read_bytes() != content:
            raise ContractError("integration receipt CAS filename/content mismatch")
        registry.validate(payload, "integration-receipt-v1.schema.json")
        if payload["task_manifest_sha256"] == task_sha:
            found.append((StoredObject(digest, path, True), payload))
    return tuple(found)


def _replayed_integration(
    existing: tuple[tuple[StoredObject, dict[str, Any]], ...],
    requested_commit: str | None,
) -> IntegrationReceipt:
    if len(existing) != 1:
        raise ContractError("integrated task does not have one exact integration receipt")
    stored, payload = existing[0]
    if requested_commit is not None and payload["integrated_commit_sha"] != requested_commit:
        raise ContractError("integration replay names a different canonical commit")
    return _integration_receipt(stored, payload, replayed=True)


def _verify_canonical_commit(
    paths: BuildPaths,
    task: Mapping[str, Any],
    task_relative: str,
    handoff_patch: bytes,
    handoff_paths: tuple[str, ...],
    requested_commit: str | None,
) -> tuple[str, str, tuple[str, ...]]:
    head = _git_text(paths.repo_root, "rev-parse", "HEAD").strip()
    if not _is_commit_sha(head):
        raise ContractError("repository HEAD is not a canonical commit")
    integrated = head if requested_commit is None else requested_commit
    if integrated != head:
        raise ContractError("integrated commit must be the exact canonical HEAD")
    if _git(paths.repo_root, "cat-file", "-e", f"{integrated}^{{commit}}").returncode != 0:
        raise ContractError("integrated commit does not exist as a Git commit")
    lineage = _git_text(
        paths.repo_root, "rev-list", "--parents", "-n", "1", integrated
    ).strip().split()
    if len(lineage) != 2 or lineage[0] != integrated:
        raise ContractError("integrated commit must have one exact canonical parent")
    parent = lineage[1]
    base = task["base_commit_sha"]
    if not isinstance(base, str) or _git(
        paths.repo_root, "merge-base", "--is-ancestor", base, parent
    ).returncode != 0:
        raise ContractError("canonical parent does not descend from the exact task base")
    committed_task = _git_bytes(paths.repo_root, "show", f"{parent}:{task_relative}")
    if committed_task != canonical_json_bytes(dict(task)):
        raise ContractError("canonical parent does not contain the exact task manifest")
    commit_patch, commit_paths = _derive_commit_truth(paths.repo_root, parent, integrated)
    if commit_patch != handoff_patch:
        raise ContractError("canonical commit patch does not equal the accepted handoff")
    if commit_paths != handoff_paths:
        raise ContractError("canonical commit paths do not equal the accepted handoff")
    return integrated, parent, commit_paths


def _record_remaining_gates(
    paths: BuildPaths,
    task: Mapping[str, Any],
    integrated_commit: str,
) -> tuple[CommandReceipt, ...]:
    remaining = {
        command["gate"]
        for command in task["acceptance"]["commands"]
        if command["gate"] in {"FOCUSED", "MILESTONE"}
    }
    if not remaining:
        return ()
    worktree_key = sha256_hex(canonical_json_bytes({
        "kind": "integration-verification",
        "task_manifest_sha256": sha256_hex(canonical_json_bytes(dict(task))),
        "integrated_commit_sha": integrated_commit,
        "created_at": _utc_now(),
    }))
    worktree = create_detached_worktree(paths, worktree_key, integrated_commit)
    recorded: list[CommandReceipt] = []
    for gate in ("FOCUSED", "MILESTONE"):
        if gate not in remaining:
            continue
        receipts = run_verification_gate(paths, task, worktree, gate)
        recorded.extend(receipts)
        failed = [receipt for receipt in receipts if receipt.outcome != "PASS"]
        if failed:
            raise ContractError(
                f"integration {gate} gate did not pass: {failed[0].command_id}"
            )
    return tuple(recorded)


def _integration_receipt(
    stored: StoredObject,
    payload: Mapping[str, Any],
    *,
    replayed: bool | None = None,
) -> IntegrationReceipt:
    return IntegrationReceipt(
        sha256=stored.sha256,
        path=stored.path,
        replayed=stored.replayed if replayed is None else replayed,
        task_manifest_sha256=payload["task_manifest_sha256"],
        handoff_sha256=payload["handoff_sha256"],
        review_receipt_sha256=payload["review_receipt_sha256"],
        integrator_profile_id=payload["integrator_profile_id"],
        canonical_parent_sha=payload["canonical_parent_sha"],
        integrated_commit_sha=payload["integrated_commit_sha"],
        changed_paths=tuple(payload["changed_paths"]),
        command_receipt_sha256s=tuple(payload["command_receipt_sha256s"]),
        recorded_at=payload["recorded_at"],
    )


def _git(repo: Path, *argv: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *argv],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git_bytes(repo: Path, *argv: str) -> bytes:
    result = _git(repo, *argv)
    if result.returncode != 0:
        raise ContractError(f"Git command failed while verifying integration: {' '.join(argv)}")
    return result.stdout


def _git_text(repo: Path, *argv: str) -> str:
    try:
        return _git_bytes(repo, *argv).decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise ContractError("Git returned non-ASCII integration metadata") from error


def _is_commit_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(
        character in "0123456789abcdef" for character in value
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
