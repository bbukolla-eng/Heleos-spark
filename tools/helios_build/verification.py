"""Finite verification gates with immutable patch-bound command receipts."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.helios_build._external_cas import put_private_bytes
from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build.collect import (
    _derive_worktree_truth,
    _intent_to_add_untracked,
    _is_digest,
    _load_committed_task,
    _strict_json_bytes,
)
from tools.helios_build.errors import ContractError
from tools.helios_build.paths import BuildPaths
from tools.helios_build.process import ProcessLaunchError, run_bounded_process
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore, StoredObject
from tools.helios_build.worktrees import verify_detached_worktree


_GATES = frozenset({"FOCUSED", "AFFECTED_INTEGRATION", "MILESTONE"})
_BUDGET_FIELDS = {
    "FOCUSED": "focused_test_seconds",
    "AFFECTED_INTEGRATION": "affected_integration_seconds",
    "MILESTONE": "milestone_seconds",
}
_MAX_CAPTURE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    sha256: str
    path: Path
    replayed: bool
    task_manifest_sha256: str
    gate: str
    command_id: str
    correction_round: int
    argv_sha256: str
    cwd_key: str
    patch_sha256: str | None
    started_at: str
    duration_ms: int
    exit_code: int | None
    outcome: str
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool


def run_verification_gate(
    paths: BuildPaths,
    task: Mapping[str, Any] | str | Path,
    worktree: Path,
    gate: str,
) -> tuple[CommandReceipt, ...]:
    """Run each command declared for one gate at most once for one immutable key."""
    if not isinstance(paths, BuildPaths) or not isinstance(worktree, Path):
        raise ContractError("verification gate requires BuildPaths and a worktree Path")
    if gate not in _GATES:
        raise ContractError(f"verification gate is invalid: {gate!r}")
    task_sha, payload = _task_payload(paths, task)
    base = payload["base_commit_sha"]
    assert isinstance(base, str)
    verify_detached_worktree(
        paths,
        worktree,
        base,
        require_clean=False,
        require_exact_head=False,
    )
    patch_sha: str | None = None
    if gate != "FOCUSED":
        _intent_to_add_untracked(worktree)
        patch, _ = _derive_worktree_truth(worktree, base)
        patch_sha = sha256_hex(patch)
    commands = [
        command for command in payload["acceptance"]["commands"]
        if command["gate"] == gate
    ]
    if not commands:
        return ()
    all_receipts = _command_receipts(paths)
    results: list[CommandReceipt] = []
    for command in commands:
        exact = [
            item for item in all_receipts
            if item[1]["task_manifest_sha256"] == task_sha
            and item[1]["gate"] == gate
            and item[1]["command_id"] == command["command_id"]
            and item[1]["correction_round"] == payload["correction_round"]
        ]
        if len(exact) > 1:
            raise ContractError("verification command key has multiple immutable receipts")
        argv_sha = sha256_hex(canonical_json_bytes(command["argv"]))
        cwd_key = sha256_hex(canonical_json_bytes({"cwd": command["cwd"]}))
        if exact:
            stored, existing = exact[0]
            expected = {
                "argv_sha256": argv_sha,
                "cwd_key": cwd_key,
                "patch_sha256": patch_sha,
            }
            if any(existing.get(key) != value for key, value in expected.items()):
                raise ContractError("verification command replay does not match its immutable key")
            if existing["outcome"] != "PASS":
                raise ContractError("unchanged failing command may not be rerun")
            results.append(_receipt(stored, existing, replayed=True))
            continue
        _validate_correction_progression(
            paths,
            payload,
            gate,
            command["command_id"],
            patch_sha,
            all_receipts,
        )
        cwd = _resolve_cwd(worktree, command["cwd"])
        timeout = min(
            command["timeout_seconds"],
            payload["budgets"][_BUDGET_FIELDS[gate]],
        )
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ContractError("verification command has no positive finite timeout")
        started_at = _utc_now()
        started = time.monotonic()
        try:
            process = run_bounded_process(
                command["argv"],
                b"",
                cwd,
                dict(os.environ),
                timeout,
                _MAX_CAPTURE_BYTES,
            )
            duration_ms = max(0, int((time.monotonic() - started) * 1000))
            if process.timed_out or process.capture_incomplete:
                outcome = "OUTCOME_UNKNOWN"
            elif process.returncode == command["expected_exit_code"]:
                outcome = "PASS"
            else:
                outcome = "FAIL"
            returncode = process.returncode
            stdout = process.stdout
            stderr = process.stderr
            stdout_truncated = process.stdout_truncated
            stderr_truncated = process.stderr_truncated
        except ProcessLaunchError as error:
            duration_ms = max(0, int((time.monotonic() - started) * 1000))
            outcome = "BLOCKED"
            returncode = None
            stdout = b""
            stderr = str(error).encode("utf-8", errors="replace")
            stdout_truncated = False
            stderr_truncated = False
        stdout_object = put_private_bytes(paths, "command-stdout", stdout)
        stderr_object = put_private_bytes(paths, "command-stderr", stderr)
        command_receipt: dict[str, Any] = {
            "protocol": "helios.build.command-receipt/v1",
            "task_manifest_sha256": task_sha,
            "gate": gate,
            "command_id": command["command_id"],
            "correction_round": payload["correction_round"],
            "argv_sha256": argv_sha,
            "cwd_key": cwd_key,
            "patch_sha256": patch_sha,
            "started_at": started_at,
            "duration_ms": duration_ms,
            "exit_code": returncode,
            "outcome": outcome,
            "stdout_sha256": stdout_object.sha256,
            "stderr_sha256": stderr_object.sha256,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
        SchemaRegistry(paths.repo_root).validate(
            command_receipt, "command-receipt-v1.schema.json"
        )
        stored = ContentAddressedStore(
            paths.repo_root / "build_control" / "graph"
        ).put_json("receipts", command_receipt)
        all_receipts = (*all_receipts, (stored, command_receipt))
        results.append(_receipt(stored, command_receipt))
    return tuple(results)


def _task_payload(
    paths: BuildPaths, task: Mapping[str, Any] | str | Path
) -> tuple[str, dict[str, Any]]:
    if isinstance(task, (str, Path)):
        digest, payload, _ = _load_committed_task(paths, task)
        return digest, payload
    if not isinstance(task, Mapping):
        raise ContractError("verification task must be a manifest mapping or reference")
    payload = dict(task)
    SchemaRegistry(paths.repo_root).validate(payload, "task-manifest-v1.schema.json")
    digest = sha256_hex(canonical_json_bytes(payload))
    committed_digest, committed, _ = _load_committed_task(paths, digest)
    if canonical_json_bytes(committed) != canonical_json_bytes(payload):
        raise ContractError("verification task differs from its committed manifest")
    return committed_digest, committed


def _validate_correction_progression(
    paths: BuildPaths,
    task: Mapping[str, Any],
    gate: str,
    command_id: str,
    patch_sha: str | None,
    receipts: tuple[tuple[StoredObject, dict[str, Any]], ...],
) -> None:
    if gate == "FOCUSED":
        return
    ancestors = _ancestor_task_shas(paths, task)
    prior = [
        payload for _, payload in receipts
        if payload["task_manifest_sha256"] in ancestors
        and payload["gate"] == gate
        and payload["command_id"] == command_id
    ]
    if not prior:
        return
    latest = max(prior, key=lambda payload: payload["correction_round"])
    if task["correction_round"] <= latest["correction_round"]:
        raise ContractError("later verification requires a higher correction round")
    if patch_sha == latest["patch_sha256"]:
        raise ContractError("later verification requires a changed patch digest")


def _ancestor_task_shas(paths: BuildPaths, task: Mapping[str, Any]) -> frozenset[str]:
    found: set[str] = set()
    current = task.get("supersedes_task_manifest_sha256")
    while current is not None:
        if not _is_digest(current) or current in found:
            raise ContractError("task correction lineage is invalid or cyclic")
        found.add(current)
        _, predecessor, _ = _load_committed_task(paths, current)
        current = predecessor.get("supersedes_task_manifest_sha256")
    return frozenset(found)


def _command_receipts(
    paths: BuildPaths,
) -> tuple[tuple[StoredObject, dict[str, Any]], ...]:
    root = paths.repo_root / "build_control" / "graph" / "receipts" / "sha256"
    if not root.is_dir():
        return ()
    registry = SchemaRegistry(paths.repo_root)
    found: list[tuple[StoredObject, dict[str, Any]]] = []
    for path in sorted(root.glob("*/*.json")):
        content = path.read_bytes()
        payload = _strict_json_bytes(content, "command receipt")
        if payload.get("protocol") != "helios.build.command-receipt/v1":
            continue
        digest = sha256_hex(content)
        if path.name != f"{digest}.json" or canonical_json_bytes(payload) != content:
            raise ContractError("command receipt CAS filename/content mismatch")
        registry.validate(payload, "command-receipt-v1.schema.json")
        found.append((StoredObject(digest, path, True), payload))
    return tuple(found)


def _resolve_cwd(worktree: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ContractError("verification cwd must be a non-empty repository path")
    target = worktree
    for component in Path(relative).parts:
        if component in {"", ".", ".."}:
            raise ContractError("verification cwd contains an unsafe component")
        target = target / component
        try:
            metadata = target.lstat()
        except OSError as error:
            raise ContractError(f"verification cwd does not resolve: {relative}") from error
        if target.is_symlink() or not target.is_dir():
            raise ContractError("verification cwd must be a real directory inside the worktree")
    try:
        target.resolve().relative_to(worktree.resolve())
    except ValueError as error:
        raise ContractError("verification cwd escapes the worktree") from error
    return target


def _receipt(
    stored: StoredObject,
    payload: Mapping[str, Any],
    *,
    replayed: bool | None = None,
) -> CommandReceipt:
    return CommandReceipt(
        sha256=stored.sha256,
        path=stored.path,
        replayed=stored.replayed if replayed is None else replayed,
        task_manifest_sha256=payload["task_manifest_sha256"],
        gate=payload["gate"],
        command_id=payload["command_id"],
        correction_round=payload["correction_round"],
        argv_sha256=payload["argv_sha256"],
        cwd_key=payload["cwd_key"],
        patch_sha256=payload.get("patch_sha256"),
        started_at=payload["started_at"],
        duration_ms=payload["duration_ms"],
        exit_code=payload["exit_code"],
        outcome=payload["outcome"],
        stdout_sha256=payload["stdout_sha256"],
        stderr_sha256=payload["stderr_sha256"],
        stdout_truncated=payload["stdout_truncated"],
        stderr_truncated=payload["stderr_truncated"],
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
