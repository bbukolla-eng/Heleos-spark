"""Idempotent finite dispatch with append-only crash reconciliation."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, NoReturn

from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build._external_cas import put_private_bytes, read_private_bytes
from tools.helios_build.doctor import (
    _ExecutableUnavailable,
    _descriptor_directory_path,
    _descriptor_execution_path,
    _open_private_child_directory,
    _open_secure_state_root,
    _open_verified_executable,
    _persist_raw_stream,
    preflight_worker,
)
from tools.helios_build.errors import ContractError, TransitionError
from tools.helios_build.graph import (
    ControllerLock,
    _PolicySnapshot,
    _TaskRecord,
    _node_is_ready,
    _resolve_graph_context,
    _validate_transition,
    transition_node_locked,
)
from tools.helios_build.ledger import append_event, replay_events
from tools.helios_build.paths import BuildPaths
from tools.helios_build.process import ProcessLaunchError, run_bounded_process
from tools.helios_build.profiles import (
    AdapterConfiguration,
    adapter_configuration_sha256,
    load_adapter_configuration,
    load_worker_profile_bytes,
    worker_profile_sha256,
)
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore
from tools.helios_build.types import AttemptOutcome, NodeState, NodeType
from tools.helios_build.worktrees import (
    open_worktree_descriptor,
    open_verified_worktree_descriptor,
    recover_or_create_detached_worktree,
    verify_detached_worktree,
)


_EVENTS = Path("build_control/graph/events.jsonl")
_RUN_PROTOCOL = "helios.build.dispatch-run-stage/v1"
_RUN_STAGES = frozenset({"RESERVED", "DISPATCHED", "RECOVERED", "TERMINAL"})
_FAILED_REASONS = frozenset({
    "EXECUTABLE_BECAME_UNAVAILABLE", "EXECUTABLE_HASH_CHANGED",
    "ENVIRONMENT_BECAME_UNAVAILABLE", "ADAPTER_LAUNCH_FAILED",
    "ADAPTER_EXIT_NONZERO", "ADAPTER_OUTPUT_TRUNCATED", "HANDOFF_MISSING",
    "HANDOFF_INVALID", "HANDOFF_OVERSIZED",
})
_UNKNOWN_REASONS = frozenset({
    "ATTEMPT_OUTCOME_UNKNOWN", "ATTEMPT_RECOVERY_OUTCOME_UNKNOWN",
    "ATTEMPT_RECOVERY_NEVER_DISPATCHED",
})


@dataclass(frozen=True, slots=True)
class RunIdentity:
    task_manifest_sha256: str
    base_commit_sha: str
    adapter_configuration_sha256: str
    sha256: str


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    sha256: str
    path: Path
    manifest: dict[str, Any]
    worktree: Path
    task_manifest_path: Path
    handoff_path: Path
    checkpoint_dir: Path
    worktree_inode: tuple[int, int]
    timeout_seconds: int

    @property
    def task_manifest_sha256(self) -> str:
        return str(self.manifest["task_manifest_sha256"])

    @property
    def base_commit_sha(self) -> str:
        return str(self.manifest["base_commit_sha"])

    @property
    def run_identity_sha256(self) -> str:
        return str(self.manifest["run_identity_sha256"])

    @property
    def task_bytes(self) -> bytes:
        return _read_regular_no_follow(self.task_manifest_path, "durable task snapshot")


@dataclass(frozen=True, slots=True)
class DispatchResult:
    run_identity_sha256: str | None
    replayed: bool
    outcome: AttemptOutcome
    state: str
    reason_code: str
    attempt: AttemptRecord | None
    handoff_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class _CapturedPolicy:
    head_sha: str
    graph_policy: _PolicySnapshot
    task_objects: tuple[tuple[str, bytes], ...]
    graph_objects: tuple[tuple[str, bytes], ...]


def compute_run_identity(task_manifest_sha256: str, base_commit_sha: str,
                         adapter_configuration_sha256: str) -> RunIdentity:
    _require_digest(task_manifest_sha256, "task manifest")
    _require_commit(base_commit_sha)
    _require_digest(adapter_configuration_sha256, "adapter configuration")
    body = {
        "task_manifest_sha256": task_manifest_sha256,
        "base_commit_sha": base_commit_sha,
        "adapter_configuration_sha256": adapter_configuration_sha256,
    }
    return RunIdentity(task_manifest_sha256, base_commit_sha,
                       adapter_configuration_sha256,
                       sha256_hex(canonical_json_bytes(body)))


def dispatch_task(paths: BuildPaths, task_ref: str | Path,
                  adapter_config_path: Path) -> DispatchResult:
    """Dispatch once under the shared graph controller lock, or reconcile replay."""
    if not isinstance(paths, BuildPaths) or not isinstance(adapter_config_path, Path):
        raise ContractError("dispatch requires BuildPaths and an adapter configuration Path")
    with ControllerLock(paths) as lock:
        schemas = SchemaRegistry(paths.repo_root)
        head_sha = _head_sha(paths.repo_root)
        policy = _capture_policy_snapshot(paths, head_sha, schemas)
        _assert_control_snapshot(paths, policy)
        task_sha, task, _ = _load_committed_task(
            paths, task_ref, schemas, policy_snapshot=policy
        )
        base_sha = task["base_commit_sha"]
        assert isinstance(base_sha, str)
        _validate_base_commit(paths.repo_root, head_sha, base_sha)
        context = _task_context(paths, task_sha, policy)
        profile_id = task["roles"]["builder_profile_id"]
        assert isinstance(profile_id, str)
        profile_path = paths.repo_root / "build_control" / "worker_profiles" / f"{profile_id}.json"
        profile_bytes = _git_show(paths.repo_root, head_sha, profile_path.relative_to(paths.repo_root).as_posix())
        profile = _load_profile_snapshot(paths, schemas, profile_path, profile_bytes)
        if profile.profile_id != profile_id:
            raise ContractError("builder profile filename/content identity mismatch")
        if "BUILDER" not in profile.roles:
            raise ContractError(f"worker profile {profile_id} is not a builder")
        if "LOCAL_ADAPTER" not in profile.transports:
            _validate_fresh_dispatch(paths, context, task)
            return _block_locked(
                lock, context.node.node_id, "LOCAL_ADAPTER_NOT_PERMITTED", policy
            )
        adapters = load_adapter_configuration(adapter_config_path, schemas, repo_root=paths.repo_root)
        matches = [adapter for adapter in adapters if adapter.worker_id == profile.worker_id]
        if len(matches) > 1:
            raise ContractError("adapter configuration resolved more than one worker entry")
        if not matches:
            _validate_fresh_dispatch(paths, context, task)
            return _block_locked(
                lock, context.node.node_id, "ADAPTER_NOT_CONFIGURED", policy
            )
        adapter = matches[0]
        adapter_sha = adapter_configuration_sha256(adapter)
        identity = compute_run_identity(task_sha, base_sha, adapter_sha)
        timeout_seconds = min(adapter.attempt_timeout_seconds, task["budgets"]["implementation_seconds"])
        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise ContractError("task and adapter must permit a positive finite attempt timeout")

        replay = _reconcile_existing(
            lock, paths, schemas, context.node.node_id, identity, task, profile,
            adapter, timeout_seconds, policy,
        )
        if replay is not None:
            _assert_control_snapshot(paths, policy)
            context = _task_context(paths, task_sha, policy)
            _ensure_successor_routing(
                lock, paths, context, task_sha, task, schemas, policy
            )
            return replay

        _validate_fresh_dispatch(paths, context, task)
        preflight = preflight_worker(paths, profile, adapter)
        if preflight.availability != "AVAILABLE" or preflight.receipt_sha256 is None:
            return _block_locked(lock, context.node.node_id, preflight.reason_code, policy)
        # Availability is proven before routing can mutate a predecessor.
        _assert_control_snapshot(paths, policy)
        context = _task_context(paths, task_sha, policy)
        _validate_fresh_dispatch(paths, context, task)
        _ensure_successor_routing(lock, paths, context, task_sha, task, schemas, policy)
        context = _task_context(paths, task_sha, policy)
        _validate_fresh_dispatch(paths, context, task)

        reservation = _reserve_run_identity(
            paths, identity, profile, preflight.receipt_sha256, timeout_seconds
        )
        try:
            worktree, inode, created = recover_or_create_detached_worktree(
                paths, identity.sha256, base_sha
            )
        except ContractError:
            _record_incomplete_worktree_creation(paths, identity.sha256)
            raise
        _record_worktree_creation(paths, identity.sha256, inode, created)
        run_directory, checkpoint_dir = _create_attempt_directories(paths, identity.sha256)
        task_path = run_directory / "task-manifest.json"
        handoff_path = run_directory / "handoff.json"
        _publish_or_verify_external(task_path, canonical_json_bytes(task))
        attempt_payload: dict[str, Any] = {
            "protocol": "helios.build.attempt-manifest/v1",
            "attempt_id": f"attempt-{identity.sha256}",
            "task_manifest_sha256": task_sha,
            "base_commit_sha": base_sha,
            "adapter_configuration_sha256": adapter_sha,
            "run_identity_sha256": identity.sha256,
            "worker_profile_sha256": worker_profile_sha256(profile),
            "attempt_ordinal": 1,
            "preflight_receipt_sha256": preflight.receipt_sha256,
            "worktree_key": identity.sha256,
            "resume_from_checkpoint_sha256": task["resume_from_checkpoint_sha256"],
            "created_at": reservation["attempt_created_at"],
        }
        schemas.validate(attempt_payload, "attempt-manifest-v1.schema.json")
        stored = ContentAddressedStore(paths.repo_root / "build_control" / "graph").put_json(
            "attempts", attempt_payload
        )
        attempt = AttemptRecord(stored.sha256, stored.path, attempt_payload, worktree,
                                task_path, handoff_path, checkpoint_dir, inode, timeout_seconds)
        _append_run_stage(paths, identity, attempt, profile, preflight.receipt_sha256,
                          "RESERVED", AttemptOutcome.OUTCOME_UNKNOWN,
                          NodeState.READY, "ATTEMPT_RESERVED")
        transition_node_locked(
            lock, context.node.node_id, NodeState.READY, NodeState.DISPATCHED,
            "LOCAL_ADAPTER_DISPATCHED", "codex-control-v1",
            attempt_manifest_sha256=attempt.sha256,
            policy_snapshot=policy.graph_policy,
        )
        _append_run_stage(paths, identity, attempt, profile, preflight.receipt_sha256,
                          "DISPATCHED", AttemptOutcome.OUTCOME_UNKNOWN,
                          NodeState.DISPATCHED, "ATTEMPT_DISPATCHED")
        if _head_sha(paths.repo_root) != head_sha:
            raise ContractError("repository HEAD changed during dispatch reservation")
        verify_detached_worktree(paths, worktree, base_sha, expected_inode=inode, require_clean=True)
        result = _execute_attempt(paths, adapter, task, attempt)
        if result.outcome is not AttemptOutcome.SUCCEEDED:
            transition_node_locked(
                lock, context.node.node_id, NodeState.DISPATCHED, NodeState.FAILED,
                result.reason_code, "codex-control-v1",
                attempt_manifest_sha256=attempt.sha256,
                policy_snapshot=policy.graph_policy,
            )
        _append_run_stage(paths, identity, attempt, profile, preflight.receipt_sha256,
                          "TERMINAL", result.outcome, NodeState(result.state), result.reason_code,
                          handoff_sha256=result.handoff_sha256)
        return result


def _execute_attempt(paths: BuildPaths, adapter: AdapterConfiguration,
                     task: dict[str, Any], attempt: AttemptRecord) -> DispatchResult:
    try:
        executable = _open_verified_executable(adapter.executable)
    except _ExecutableUnavailable:
        return _terminal(attempt, AttemptOutcome.FAILED, "EXECUTABLE_BECAME_UNAVAILABLE")
    worktree_descriptor: int | None = None
    run_descriptor: int | None = None
    checkpoint_descriptor: int | None = None
    handoff_bytes: bytes | None = None
    try:
        if executable.sha256 != adapter.executable_sha256:
            return _terminal(attempt, AttemptOutcome.FAILED, "EXECUTABLE_HASH_CHANGED")
        if any(name not in os.environ for name in adapter.environment_variable_names):
            return _terminal(attempt, AttemptOutcome.FAILED, "ENVIRONMENT_BECAME_UNAVAILABLE")
        worktree_descriptor = open_verified_worktree_descriptor(
            paths, attempt.worktree, attempt.base_commit_sha,
            expected_inode=attempt.worktree_inode, require_clean=True,
        )
        run_descriptor, checkpoint_descriptor = _open_attempt_launch_descriptors(
            paths, attempt.run_identity_sha256
        )
        values = {
            "{task_manifest}": f"/dev/fd/{run_descriptor}/task-manifest.json",
            "{handoff_path}": f"/dev/fd/{run_descriptor}/handoff.json",
            "{checkpoint_dir}": f"/dev/fd/{checkpoint_descriptor}",
            "{worktree}": f"/dev/fd/{worktree_descriptor}",
            "{attempt_manifest_sha256}": attempt.sha256,
            "{task_manifest_sha256}": attempt.task_manifest_sha256,
        }
        argv = [_descriptor_execution_path(executable.descriptor)]
        for argument in adapter.dispatch_argv:
            if "{" in argument or "}" in argument:
                if argument not in values:
                    raise ContractError(f"dispatch argument has an undeclared token: {argument}")
                argv.append(values[argument])
            else:
                argv.append(argument)
        environment = {name: os.environ[name] for name in adapter.environment_variable_names}
        environment.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"})
        try:
            process = run_bounded_process(
                argv, canonical_json_bytes(task),
                Path(_descriptor_directory_path(worktree_descriptor)), environment,
                attempt.timeout_seconds, adapter.max_capture_bytes,
                pass_fds=(executable.descriptor, worktree_descriptor,
                          run_descriptor, checkpoint_descriptor),
            )
            if process.returncode == 0 and not process.timed_out:
                handoff_bytes = _read_child_file(
                    run_descriptor, "handoff.json", adapter.max_capture_bytes
                )
        except ProcessLaunchError:
            return _terminal(attempt, AttemptOutcome.FAILED, "ADAPTER_LAUNCH_FAILED")
        except ContractError as error:
            reason = "HANDOFF_OVERSIZED" if "finite size bound" in str(error) else "HANDOFF_INVALID"
            return _terminal(attempt, AttemptOutcome.FAILED, reason)
    finally:
        if worktree_descriptor is not None:
            os.close(worktree_descriptor)
        if checkpoint_descriptor is not None:
            os.close(checkpoint_descriptor)
        if run_descriptor is not None:
            os.close(run_descriptor)
        executable.close()
    _persist_raw_stream(paths.state_root, "stdout", process.stdout)
    _persist_raw_stream(paths.state_root, "stderr", process.stderr)
    if process.timed_out or process.capture_incomplete or process.returncode is None:
        return _terminal(attempt, AttemptOutcome.OUTCOME_UNKNOWN, "ATTEMPT_OUTCOME_UNKNOWN")
    if process.returncode != 0:
        return _terminal(attempt, AttemptOutcome.FAILED, "ADAPTER_EXIT_NONZERO")
    if process.stdout_truncated or process.stderr_truncated:
        return _terminal(attempt, AttemptOutcome.FAILED, "ADAPTER_OUTPUT_TRUNCATED")
    if handoff_bytes is None:
        return _terminal(attempt, AttemptOutcome.FAILED, "HANDOFF_MISSING")
    try:
        _validate_local_handoff_bytes(
            paths, handoff_bytes, attempt, task, adapter.result_protocol
        )
        private = put_private_bytes(paths, "handoffs", handoff_bytes)
        safe_manifest: dict[str, Any] = {
            "protocol": "helios.build.artifact-manifest/v1",
            "artifact_id": f"local-handoff-{attempt.sha256}",
            "attempt_manifest_sha256": attempt.sha256,
            "kind": "LOCAL_WORKER_HANDOFF_RAW",
            "sha256": private.sha256,
            "byte_size": private.byte_size,
            "media_type": "application/json",
            "store_key": private.store_key,
            "created_at": attempt.manifest["created_at"],
        }
        registry = SchemaRegistry(paths.repo_root)
        registry.validate(safe_manifest, "artifact-manifest-v1.schema.json")
        stored_handoff = ContentAddressedStore(
            paths.repo_root / "build_control" / "graph"
        ).put_json("artifacts", safe_manifest)
    except ContractError as error:
        reason = "HANDOFF_OVERSIZED" if "finite size bound" in str(error) else "HANDOFF_INVALID"
        return _terminal(attempt, AttemptOutcome.FAILED, reason)
    return DispatchResult(attempt.run_identity_sha256, False, AttemptOutcome.SUCCEEDED,
                          NodeState.DISPATCHED.value, "HANDOFF_AWAITING_COLLECTION", attempt,
                          stored_handoff.sha256)


def _terminal(attempt: AttemptRecord, outcome: AttemptOutcome, reason: str) -> DispatchResult:
    return DispatchResult(attempt.run_identity_sha256, False, outcome,
                          NodeState.FAILED.value, reason, attempt)


def _reconcile_existing(
    lock: ControllerLock, paths: BuildPaths, schemas: SchemaRegistry, node_id: str,
    identity: RunIdentity, task: dict[str, Any], profile: Any,
    adapter: AdapterConfiguration, timeout_seconds: int,
    policy: _CapturedPolicy,
) -> DispatchResult | None:
    attempts = _attempts_for_identity(paths, schemas, identity.sha256)
    records = _load_run_stages(paths, identity.sha256)
    reservation = _load_run_reservation(paths, identity.sha256)
    if not attempts and not records and reservation is None:
        return None
    if reservation is None:
        raise ContractError("durable attempt/run exists without prior identity reservation")
    _validate_reservation(reservation, identity, profile, timeout_seconds)
    recovered_inode: tuple[int, int] | None = None
    if not attempts:
        attempt_payload, attempt_path, attempt_sha, recovered_inode = _recover_reserved_attempt(
            paths, schemas, identity, task, profile, reservation
        )
        attempts = ((attempt_payload, attempt_path, attempt_sha),)
        records = _load_run_stages(paths, identity.sha256)
    if len(attempts) != 1:
        raise ContractError("run identity does not resolve to exactly one durable attempt")
    attempt_payload, attempt_path, attempt_sha = attempts[0]
    _validate_attempt_relationships(paths, schemas, identity, task, profile, adapter,
                                    timeout_seconds, attempt_payload, attempt_sha)
    run_directory = paths.state_root / "attempts" / identity.sha256
    worktree = paths.state_root / "worktrees" / identity.sha256
    if records:
        inode = (records[0]["worktree_dev"], records[0]["worktree_ino"])
        if inode[0] <= 0 or inode[1] <= 0:
            raise ContractError("dispatch run stage contains an invalid worktree inode")
    elif recovered_inode is not None:
        inode = recovered_inode
    else:
        inode = _load_verified_worktree_creation(paths, identity)
    attempt = AttemptRecord(
        attempt_sha, attempt_path, attempt_payload, worktree,
        run_directory / "task-manifest.json", run_directory / "handoff.json",
        run_directory / "checkpoints", inode, timeout_seconds,
    )
    if not records:
        _append_run_stage(paths, identity, attempt, profile,
                          attempt_payload["preflight_receipt_sha256"], "RESERVED",
                          AttemptOutcome.OUTCOME_UNKNOWN, NodeState.READY, "ATTEMPT_RESERVED")
        records = _load_run_stages(paths, identity.sha256)
    _validate_run_stages(records, identity, attempt, profile, timeout_seconds)
    terminal = records[-1] if records[-1]["stage"] == "TERMINAL" else None
    events = replay_events(paths.repo_root / _EVENTS)
    node_events = [event for event in events if event.get("node_id") == node_id]
    current = NodeState(node_events[-1]["new_state"]) if node_events else NodeState.DRAFT
    dispatch_events = [event for event in node_events if event.get("new_state") == NodeState.DISPATCHED.value]
    if terminal is not None:
        _validate_terminal_ledger(terminal, current, node_events, dispatch_events, attempt_sha)
        _validate_terminal_handoff(paths, terminal, attempt, task, adapter)
        return _result_from_stage(terminal, attempt)
    try:
        worktree.lstat()
    except FileNotFoundError:
        if inode != (0, 0):
            raise ContractError("reserved worktree disappeared before dispatch")
    else:
        verify_detached_worktree(paths, worktree, identity.base_commit_sha,
                                 expected_inode=inode, require_clean=False)
    if attempt.task_bytes != canonical_json_bytes(task):
        raise ContractError("durable task snapshot does not match the committed task")
    if not dispatch_events:
        if current is not NodeState.READY:
            raise ContractError("reserved attempt conflicts with pre-dispatch lifecycle state")
        reason = "ATTEMPT_RECOVERY_NEVER_DISPATCHED"
        transition_node_locked(
            lock, node_id, NodeState.READY, NodeState.FAILED,
            reason, "codex-control-v1", attempt_manifest_sha256=attempt_sha,
            policy_snapshot=policy.graph_policy,
        )
        if records[-1]["stage"] == "RESERVED":
            _append_run_stage(paths, identity, attempt, profile,
                              attempt_payload["preflight_receipt_sha256"], "RECOVERED",
                              AttemptOutcome.OUTCOME_UNKNOWN, NodeState.FAILED, reason)
        _append_run_stage(paths, identity, attempt, profile,
                          attempt_payload["preflight_receipt_sha256"], "TERMINAL",
                          AttemptOutcome.OUTCOME_UNKNOWN, NodeState.FAILED, reason)
        return DispatchResult(identity.sha256, True, AttemptOutcome.OUTCOME_UNKNOWN,
                              NodeState.FAILED.value, reason, attempt)
    elif len(dispatch_events) != 1 or dispatch_events[0]["attempt_manifest_sha256"] != attempt_sha:
        raise ContractError("lifecycle dispatch binding conflicts with the durable attempt")
    if records[-1]["stage"] == "RESERVED":
        _append_run_stage(paths, identity, attempt, profile,
                          attempt_payload["preflight_receipt_sha256"], "DISPATCHED",
                          AttemptOutcome.OUTCOME_UNKNOWN, NodeState.DISPATCHED, "ATTEMPT_DISPATCHED")
    if current is NodeState.DISPATCHED:
        transition_node_locked(
            lock, node_id, NodeState.DISPATCHED, NodeState.FAILED,
            "ATTEMPT_RECOVERY_OUTCOME_UNKNOWN", "codex-control-v1",
            attempt_manifest_sha256=attempt_sha,
            policy_snapshot=policy.graph_policy,
        )
        outcome, reason = AttemptOutcome.OUTCOME_UNKNOWN, "ATTEMPT_RECOVERY_OUTCOME_UNKNOWN"
    elif current is NodeState.FAILED:
        failed_reason = node_events[-1]["reason_code"]
        outcome = AttemptOutcome.OUTCOME_UNKNOWN if "UNKNOWN" in failed_reason else AttemptOutcome.FAILED
        reason = str(failed_reason)
    else:
        raise ContractError("partial dispatch record conflicts with lifecycle state")
    _append_run_stage(paths, identity, attempt, profile,
                      attempt_payload["preflight_receipt_sha256"], "TERMINAL",
                      outcome, NodeState.FAILED, reason)
    return DispatchResult(identity.sha256, True, outcome, NodeState.FAILED.value, reason, attempt)


def _validate_attempt_relationships(
    paths: BuildPaths, schemas: SchemaRegistry, identity: RunIdentity,
    task: dict[str, Any], profile: Any, adapter: AdapterConfiguration,
    timeout_seconds: int, attempt: dict[str, Any], attempt_sha: str,
) -> None:
    expected = {
        "task_manifest_sha256": identity.task_manifest_sha256,
        "base_commit_sha": identity.base_commit_sha,
        "adapter_configuration_sha256": identity.adapter_configuration_sha256,
        "run_identity_sha256": identity.sha256,
        "worker_profile_sha256": worker_profile_sha256(profile),
        "attempt_ordinal": 1,
        "worktree_key": identity.sha256,
        "resume_from_checkpoint_sha256": task["resume_from_checkpoint_sha256"],
    }
    if any(attempt.get(key) != value for key, value in expected.items()):
        raise ContractError("durable attempt relationship mismatch")
    receipt_sha = attempt["preflight_receipt_sha256"]
    receipt = ContentAddressedStore(paths.repo_root / "build_control" / "graph").get_json(receipt_sha)
    schemas.validate(receipt, "preflight-receipt-v1.schema.json")
    if (
        receipt["availability"] != "AVAILABLE"
        or receipt["adapter_configuration_sha256"] != adapter_configuration_sha256(adapter)
        or receipt["worker_profile_sha256"] != worker_profile_sha256(profile)
        or receipt["executable_sha256"] != adapter.executable_sha256
    ):
        raise ContractError("durable attempt preflight relationship mismatch")
    if timeout_seconds <= 0 or sha256_hex(canonical_json_bytes(attempt)) != attempt_sha:
        raise ContractError("durable attempt is not exact")


def _validate_run_stages(records: tuple[dict[str, Any], ...], identity: RunIdentity,
                         attempt: AttemptRecord, profile: Any, timeout: int) -> None:
    if not records or records[0]["stage"] != "RESERVED":
        raise ContractError("dispatch run stages do not begin with reservation")
    stages = tuple(record["stage"] for record in records)
    if stages not in {("RESERVED",), ("RESERVED", "DISPATCHED"),
                      ("RESERVED", "DISPATCHED", "TERMINAL"),
                      ("RESERVED", "RECOVERED"),
                      ("RESERVED", "RECOVERED", "TERMINAL")}:
        raise ContractError("dispatch run stage ordering is invalid")
    for record in records:
        if (
            record["run_identity_sha256"] != identity.sha256
            or record["task_manifest_sha256"] != identity.task_manifest_sha256
            or record["base_commit_sha"] != identity.base_commit_sha
            or record["adapter_configuration_sha256"] != identity.adapter_configuration_sha256
            or record["worker_profile_sha256"] != worker_profile_sha256(profile)
            or record["attempt_manifest_sha256"] != attempt.sha256
            or record["preflight_receipt_sha256"] != attempt.manifest["preflight_receipt_sha256"]
            or record["timeout_seconds"] != timeout
            or (record["worktree_dev"], record["worktree_ino"]) != attempt.worktree_inode
        ):
            raise ContractError("dispatch run stage relationship mismatch")
    legal = {
        "RESERVED": (AttemptOutcome.OUTCOME_UNKNOWN.value, NodeState.READY.value, "ATTEMPT_RESERVED"),
        "DISPATCHED": (AttemptOutcome.OUTCOME_UNKNOWN.value, NodeState.DISPATCHED.value, "ATTEMPT_DISPATCHED"),
        "RECOVERED": (AttemptOutcome.OUTCOME_UNKNOWN.value, NodeState.FAILED.value,
                      "ATTEMPT_RECOVERY_NEVER_DISPATCHED"),
    }
    for record in records[:-1] if records[-1]["stage"] == "TERMINAL" else records:
        if (record["outcome"], record["state"], record["reason_code"]) != legal[record["stage"]]:
            raise ContractError("dispatch run stage has an illegal outcome/state/reason")
    if records[-1]["stage"] == "TERMINAL":
        terminal = records[-1]
        outcome, state, reason = terminal["outcome"], terminal["state"], terminal["reason_code"]
        if outcome == AttemptOutcome.SUCCEEDED.value:
            if state != NodeState.DISPATCHED.value or reason != "HANDOFF_AWAITING_COLLECTION":
                raise ContractError("successful terminal dispatch stage is inconsistent")
            if not _is_digest(terminal.get("handoff_sha256")):
                raise ContractError("successful terminal dispatch lacks durable handoff evidence")
        elif state != NodeState.FAILED.value or outcome not in {
            AttemptOutcome.FAILED.value, AttemptOutcome.OUTCOME_UNKNOWN.value
        } or not isinstance(reason, str) or not reason:
            raise ContractError("failed terminal dispatch stage is inconsistent")
        elif outcome == AttemptOutcome.FAILED.value and reason not in _FAILED_REASONS:
            raise ContractError("failed terminal dispatch reason is not legal")
        elif outcome == AttemptOutcome.OUTCOME_UNKNOWN.value and reason not in _UNKNOWN_REASONS:
            raise ContractError("unknown terminal dispatch reason is not legal")
        elif terminal.get("handoff_sha256") is not None:
            raise ContractError("failed terminal dispatch cannot claim a handoff")


def _validate_terminal_ledger(terminal: dict[str, Any], current: NodeState,
                              node_events: list[dict[str, Any]],
                              dispatch_events: list[dict[str, Any]], attempt_sha: str) -> None:
    never_dispatched = terminal["reason_code"] == "ATTEMPT_RECOVERY_NEVER_DISPATCHED"
    if never_dispatched:
        if dispatch_events:
            raise ContractError("never-dispatched recovery conflicts with lifecycle dispatch")
    elif len(dispatch_events) != 1 or dispatch_events[0]["attempt_manifest_sha256"] != attempt_sha:
        raise ContractError("terminal run lacks its exact lifecycle dispatch binding")
    expected = NodeState.DISPATCHED if terminal["outcome"] == AttemptOutcome.SUCCEEDED.value else NodeState.FAILED
    if current is not expected:
        raise ContractError("terminal run state conflicts with the lifecycle ledger")
    if expected is NodeState.FAILED:
        terminal_event = node_events[-1]
        if (terminal_event.get("attempt_manifest_sha256") != attempt_sha
                or terminal_event.get("reason_code") != terminal["reason_code"]):
            raise ContractError("terminal lifecycle event binding/reason conflicts with run stage")


def _result_from_stage(stage: dict[str, Any], attempt: AttemptRecord) -> DispatchResult:
    return DispatchResult(stage["run_identity_sha256"], True,
                          AttemptOutcome(stage["outcome"]), stage["state"],
                          stage["reason_code"], attempt, stage.get("handoff_sha256"))


def _append_run_stage(paths: BuildPaths, identity: RunIdentity, attempt: AttemptRecord,
                      profile: Any, preflight_sha: str, stage: str,
                      outcome: AttemptOutcome, state: NodeState, reason: str,
                      *, handoff_sha256: str | None = None) -> None:
    if stage not in _RUN_STAGES:
        raise ContractError("invalid dispatch run stage")
    records = _load_run_stages(paths, identity.sha256)
    expected_index = len(records)
    allowed = {(): {"RESERVED"}, ("RESERVED",): {"DISPATCHED", "RECOVERED"},
               ("RESERVED", "DISPATCHED"): {"TERMINAL"},
               ("RESERVED", "RECOVERED"): {"TERMINAL"}}
    prior = tuple(record["stage"] for record in records)
    if stage not in allowed.get(prior, set()):
        raise ContractError("dispatch run stage append is stale or duplicated")
    record: dict[str, Any] = {
        "protocol": _RUN_PROTOCOL,
        "sequence": expected_index + 1,
        "previous_record_sha256": records[-1]["record_sha256"] if records else None,
        "run_identity_sha256": identity.sha256,
        "task_manifest_sha256": identity.task_manifest_sha256,
        "base_commit_sha": identity.base_commit_sha,
        "adapter_configuration_sha256": identity.adapter_configuration_sha256,
        "worker_profile_sha256": worker_profile_sha256(profile),
        "preflight_receipt_sha256": preflight_sha,
        "attempt_manifest_sha256": attempt.sha256,
        "timeout_seconds": attempt.timeout_seconds,
        "worktree_dev": attempt.worktree_inode[0],
        "worktree_ino": attempt.worktree_inode[1],
        "stage": stage,
        "outcome": outcome.value,
        "state": state.value,
        "reason_code": reason,
        "handoff_sha256": handoff_sha256,
        "recorded_at": _utc_now(),
    }
    record["record_sha256"] = sha256_hex(canonical_json_bytes(record))
    state_descriptor, run_directory = _run_stage_directory(paths, identity.sha256)
    try:
        _atomic_publish_at(
            run_directory,
            f"{expected_index + 1:02d}-{stage.lower()}.json",
            canonical_json_bytes(record),
        )
    finally:
        os.close(run_directory)
        os.close(state_descriptor)


def _load_run_stages(paths: BuildPaths, run_sha: str) -> tuple[dict[str, Any], ...]:
    state_descriptor, run_directory = _run_stage_directory(paths, run_sha)
    try:
        names = sorted(name for name in os.listdir(run_directory) if not name.startswith(".tmp-"))
        if len(names) > 3 or any(not name.endswith(".json") for name in names):
            raise ContractError("dispatch run stage files are missing, conflicting, or out of order")
        contents = []
        for name in names:
            descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                                 | getattr(os, "O_NOFOLLOW", 0), dir_fd=run_directory)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
                    raise ContractError("dispatch run stage file has unsafe custody")
                contents.append(_read_fd(descriptor))
            finally:
                os.close(descriptor)
    finally:
        os.close(run_directory)
        os.close(state_descriptor)
    records: list[dict[str, Any]] = []
    previous: str | None = None
    for sequence, content in enumerate(contents, 1):
        record = _strict_json_bytes(content, "dispatch run stage")
        supplied = record.pop("record_sha256", None)
        if record.get("protocol") != _RUN_PROTOCOL or record.get("sequence") != sequence:
            raise ContractError("dispatch run stage sequence/protocol mismatch")
        if record.get("previous_record_sha256") != previous or not _is_digest(supplied):
            raise ContractError("dispatch run stage chain mismatch")
        if sha256_hex(canonical_json_bytes(record)) != supplied:
            raise ContractError("dispatch run stage digest mismatch")
        record["record_sha256"] = supplied
        records.append(record)
        previous = supplied
    sequence = tuple(record["stage"] for record in records)
    if sequence not in {(), ("RESERVED",), ("RESERVED", "DISPATCHED"),
                        ("RESERVED", "RECOVERED"),
                        ("RESERVED", "DISPATCHED", "TERMINAL"),
                        ("RESERVED", "RECOVERED", "TERMINAL")}:
        raise ContractError("dispatch run stage files are missing, conflicting, or out of order")
    return tuple(records)


def _run_stage_directory(paths: BuildPaths, run_sha: str) -> tuple[int, int]:
    state = _open_secure_state_root(paths.state_root)
    try:
        dispatch = _open_private_child_directory(state, "dispatch")
        try:
            runs = _open_private_child_directory(dispatch, "runs")
            try:
                run = _open_private_child_directory(runs, run_sha)
            finally:
                os.close(runs)
        finally:
            os.close(dispatch)
    except BaseException:
        os.close(state)
        raise
    return state, run


def _atomic_publish_at(parent: int, filename: str, content: bytes) -> None:
    temporary = f".tmp-{uuid.uuid4().hex}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("atomic publication made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.link(temporary, filename, src_dir_fd=parent, dst_dir_fd=parent,
                follow_symlinks=False)
        os.fsync(parent)
    except FileExistsError as error:
        raise ContractError(f"immutable external record already exists: {filename}") from error
    except OSError as error:
        raise ContractError(f"cannot atomically publish external record: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ContractError(f"cannot remove unpublished external temp: {error}") from error


def _dispatch_record_directory(paths: BuildPaths, collection: str) -> tuple[int, int]:
    state = _open_secure_state_root(paths.state_root)
    try:
        dispatch = _open_private_child_directory(state, "dispatch")
        try:
            result = _open_private_child_directory(dispatch, collection)
        finally:
            os.close(dispatch)
    except BaseException:
        os.close(state)
        raise
    return state, result


def _load_dispatch_record(paths: BuildPaths, collection: str, run_sha: str,
                          label: str) -> dict[str, Any] | None:
    state, directory = _dispatch_record_directory(paths, collection)
    try:
        try:
            descriptor = os.open(f"{run_sha}.json", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                                 | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
        except FileNotFoundError:
            return None
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
                raise ContractError(f"{label} has unsafe custody")
            return _strict_json_bytes(_read_fd(descriptor), label)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)
        os.close(state)


def _reserve_run_identity(paths: BuildPaths, identity: RunIdentity, profile: Any,
                          preflight_sha: str, timeout_seconds: int) -> dict[str, Any]:
    existing = _load_run_reservation(paths, identity.sha256)
    if existing is not None:
        _validate_reservation(existing, identity, profile, timeout_seconds)
        if existing["preflight_receipt_sha256"] != preflight_sha:
            raise ContractError("run reservation preflight conflicts with exact replay")
        return existing
    record = {
        "protocol": "helios.build.dispatch-reservation/v1",
        "run_identity_sha256": identity.sha256,
        "task_manifest_sha256": identity.task_manifest_sha256,
        "base_commit_sha": identity.base_commit_sha,
        "adapter_configuration_sha256": identity.adapter_configuration_sha256,
        "worker_profile_sha256": worker_profile_sha256(profile),
        "preflight_receipt_sha256": preflight_sha,
        "timeout_seconds": timeout_seconds,
        "attempt_created_at": _utc_now(),
    }
    record["reservation_sha256"] = sha256_hex(canonical_json_bytes(record))
    state, directory = _dispatch_record_directory(paths, "reservations")
    try:
        _atomic_publish_at(directory, f"{identity.sha256}.json", canonical_json_bytes(record))
    finally:
        os.close(directory); os.close(state)
    return record


def _load_run_reservation(paths: BuildPaths, run_sha: str) -> dict[str, Any] | None:
    return _load_dispatch_record(paths, "reservations", run_sha, "dispatch reservation")


def _validate_reservation(record: dict[str, Any], identity: RunIdentity,
                          profile: Any, timeout_seconds: int) -> None:
    supplied = record.get("reservation_sha256")
    body = dict(record); body.pop("reservation_sha256", None)
    expected = {
        "protocol": "helios.build.dispatch-reservation/v1",
        "run_identity_sha256": identity.sha256,
        "task_manifest_sha256": identity.task_manifest_sha256,
        "base_commit_sha": identity.base_commit_sha,
        "adapter_configuration_sha256": identity.adapter_configuration_sha256,
        "worker_profile_sha256": worker_profile_sha256(profile),
        "timeout_seconds": timeout_seconds,
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise ContractError("dispatch reservation relationship mismatch")
    if not _is_digest(record.get("preflight_receipt_sha256")) or not isinstance(record.get("attempt_created_at"), str):
        raise ContractError("dispatch reservation is incomplete")
    if supplied != sha256_hex(canonical_json_bytes(body)):
        raise ContractError("dispatch reservation digest mismatch")


def _record_worktree_creation(paths: BuildPaths, run_sha: str,
                              inode: tuple[int, int], created: bool) -> None:
    if (
        not isinstance(inode, tuple)
        or len(inode) != 2
        or any(not isinstance(value, int) or value <= 0 for value in inode)
    ):
        raise ContractError("worktree creation evidence has an invalid inode")
    record = {"protocol": "helios.build.worktree-creation/v1", "run_identity_sha256": run_sha,
              "worktree_dev": inode[0], "worktree_ino": inode[1],
              "created_by_this_call": created}
    existing = _load_dispatch_record(paths, "creations", run_sha, "worktree creation record")
    if existing is not None:
        body = dict(existing); supplied = body.pop("record_sha256", None)
        if supplied != sha256_hex(canonical_json_bytes(body)):
            raise ContractError("worktree creation record digest mismatch")
        if any(existing.get(key) != value for key, value in record.items() if key != "created_by_this_call"):
            raise ContractError("worktree creation record conflicts with exact orphan")
        return
    record["record_sha256"] = sha256_hex(canonical_json_bytes(record))
    state, directory = _dispatch_record_directory(paths, "creations")
    try:
        _atomic_publish_at(directory, f"{run_sha}.json", canonical_json_bytes(record))
    finally:
        os.close(directory); os.close(state)


def _load_verified_worktree_creation(
    paths: BuildPaths, identity: RunIdentity
) -> tuple[int, int]:
    record = _load_dispatch_record(
        paths, "creations", identity.sha256, "worktree creation record"
    )
    if record is None:
        raise ContractError("durable attempt lacks immutable worktree creation evidence")
    body = dict(record)
    supplied = body.pop("record_sha256", None)
    inode = (record.get("worktree_dev"), record.get("worktree_ino"))
    if (
        record.get("protocol") != "helios.build.worktree-creation/v1"
        or record.get("run_identity_sha256") != identity.sha256
        or not isinstance(record.get("created_by_this_call"), bool)
        or any(not isinstance(value, int) or value <= 0 for value in inode)
        or supplied != sha256_hex(canonical_json_bytes(body))
    ):
        raise ContractError("worktree creation evidence is malformed or mismatched")
    exact_inode = (int(inode[0]), int(inode[1]))
    verify_detached_worktree(
        paths,
        paths.state_root / "worktrees" / identity.sha256,
        identity.base_commit_sha,
        expected_inode=exact_inode,
        require_clean=True,
    )
    return exact_inode


def _record_incomplete_worktree_creation(paths: BuildPaths, run_sha: str) -> None:
    record = {"protocol": "helios.build.worktree-creation-incomplete/v1",
              "run_identity_sha256": run_sha, "status": "PIN_UNPROVEN"}
    record["record_sha256"] = sha256_hex(canonical_json_bytes(record))
    existing = _load_dispatch_record(paths, "creation-incomplete", run_sha,
                                     "incomplete worktree creation record")
    if existing is not None:
        if existing != record:
            raise ContractError("incomplete worktree creation record conflicts")
        return
    state, directory = _dispatch_record_directory(paths, "creation-incomplete")
    try:
        _atomic_publish_at(directory, f"{run_sha}.json", canonical_json_bytes(record))
    finally:
        os.close(directory); os.close(state)


def _recover_reserved_attempt(paths: BuildPaths, schemas: SchemaRegistry,
                              identity: RunIdentity, task: dict[str, Any], profile: Any,
                              reservation: dict[str, Any]) -> tuple[dict[str, Any], Path, str, tuple[int, int]]:
    try:
        worktree, inode, created = recover_or_create_detached_worktree(
            paths, identity.sha256, identity.base_commit_sha)
    except ContractError:
        _record_incomplete_worktree_creation(paths, identity.sha256)
        raise
    _record_worktree_creation(paths, identity.sha256, inode, created)
    run_directory, _ = _create_attempt_directories(paths, identity.sha256)
    _publish_or_verify_external(run_directory / "task-manifest.json", canonical_json_bytes(task))
    payload = {
        "protocol": "helios.build.attempt-manifest/v1",
        "attempt_id": f"attempt-{identity.sha256}",
        "task_manifest_sha256": identity.task_manifest_sha256,
        "base_commit_sha": identity.base_commit_sha,
        "adapter_configuration_sha256": identity.adapter_configuration_sha256,
        "run_identity_sha256": identity.sha256,
        "worker_profile_sha256": worker_profile_sha256(profile), "attempt_ordinal": 1,
        "preflight_receipt_sha256": reservation["preflight_receipt_sha256"],
        "worktree_key": identity.sha256,
        "resume_from_checkpoint_sha256": task["resume_from_checkpoint_sha256"],
        "created_at": reservation["attempt_created_at"],
    }
    schemas.validate(payload, "attempt-manifest-v1.schema.json")
    stored = ContentAddressedStore(paths.repo_root / "build_control" / "graph").put_json("attempts", payload)
    attempt = AttemptRecord(stored.sha256, stored.path, payload, worktree,
                            run_directory / "task-manifest.json", run_directory / "handoff.json",
                            run_directory / "checkpoints", inode, reservation["timeout_seconds"])
    if not _load_run_stages(paths, identity.sha256):
        _append_run_stage(paths, identity, attempt, profile, reservation["preflight_receipt_sha256"],
                          "RESERVED", AttemptOutcome.OUTCOME_UNKNOWN, NodeState.READY, "ATTEMPT_RESERVED")
    return payload, stored.path, stored.sha256, inode


def _validate_terminal_handoff(
    paths: BuildPaths,
    terminal: dict[str, Any],
    attempt: AttemptRecord,
    task: dict[str, Any],
    adapter: AdapterConfiguration,
) -> None:
    digest = terminal.get("handoff_sha256")
    if terminal["outcome"] != AttemptOutcome.SUCCEEDED.value:
        if digest is not None:
            raise ContractError("failed terminal run has handoff evidence")
        return
    _require_digest(digest, "terminal handoff")
    path = (paths.repo_root / "build_control" / "graph" / "artifacts" / "sha256"
            / digest[:2] / f"{digest}.json")
    content = ContentAddressedStore._read_verified_object(path)
    if sha256_hex(content) != digest:
        raise ContractError("safe terminal handoff manifest digest mismatch")
    manifest = _strict_json_bytes(content, "safe terminal handoff manifest")
    SchemaRegistry(paths.repo_root).validate(
        manifest, "artifact-manifest-v1.schema.json"
    )
    expected = {
        "artifact_id": f"local-handoff-{attempt.sha256}",
        "attempt_manifest_sha256": attempt.sha256,
        "kind": "LOCAL_WORKER_HANDOFF_RAW",
        "media_type": "application/json",
        "created_at": attempt.manifest["created_at"],
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ContractError("safe terminal handoff manifest lineage mismatch")
    raw = read_private_bytes(
        paths,
        manifest["store_key"],
        manifest["sha256"],
        manifest["byte_size"],
        max_bytes=adapter.max_capture_bytes,
    )
    _validate_local_handoff_bytes(paths, raw, attempt, task, adapter.result_protocol)


def _validate_local_handoff_bytes(
    paths: BuildPaths,
    content: bytes,
    attempt: AttemptRecord,
    task: Mapping[str, Any],
    result_protocol: str,
) -> dict[str, Any]:
    payload = _strict_json_bytes(content, "local worker handoff")
    SchemaRegistry(paths.repo_root).validate(payload, "worker-handoff-v1.schema.json")
    expected = {
        "protocol": result_protocol,
        "transport": "LOCAL_ADAPTER",
        "attempt_manifest_sha256": attempt.sha256,
        "external_session_assignment_sha256": None,
        "task_manifest_sha256": attempt.task_manifest_sha256,
        "raw_evidence_sha256": None,
        "source_record_ids": task["required_source_record_ids"],
        "source_packet_ids": task["required_source_packet_ids"],
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ContractError("local worker handoff protocol or lineage mismatch")
    return payload


def _attempts_for_identity(paths: BuildPaths, schemas: SchemaRegistry, run_sha: str
                           ) -> tuple[tuple[dict[str, Any], Path, str], ...]:
    root = paths.repo_root / "build_control" / "graph" / "attempts" / "sha256"
    found: list[tuple[dict[str, Any], Path, str]] = []
    if not root.is_dir():
        return ()
    for path in sorted(root.glob("*/*.json")):
        content = _read_regular_no_follow(path, "attempt manifest")
        payload = _strict_json_bytes(content, "attempt manifest")
        schemas.validate(payload, "attempt-manifest-v1.schema.json")
        digest = sha256_hex(content)
        if path.name != f"{digest}.json":
            raise ContractError("attempt CAS filename/content mismatch")
        if payload["run_identity_sha256"] == run_sha:
            found.append((payload, path, digest))
    return tuple(found)


def _ensure_successor_routing(lock: ControllerLock, paths: BuildPaths, context: Any,
                              successor_sha: str, successor: dict[str, Any],
                              schemas: SchemaRegistry, policy: _CapturedPolicy) -> None:
    predecessor_sha = successor.get("supersedes_task_manifest_sha256")
    if predecessor_sha is None:
        return
    _require_digest(predecessor_sha, "superseded task manifest")
    _, predecessor, _ = _load_committed_task(
        paths, predecessor_sha, schemas, policy_snapshot=policy
    )
    predecessor_context = _task_context(paths, predecessor_sha, policy)
    successor_current = context.graph.event_states.get(context.node.node_id, NodeState.DRAFT)
    predecessor_state = predecessor_context.graph.event_states.get(
        predecessor_context.node.node_id, NodeState.DRAFT
    )
    lifecycle = [event for event in replay_events(paths.repo_root / _EVENTS)
                 if event.get("node_id") == predecessor_context.node.node_id]
    pre_supersession_state = predecessor_state
    if predecessor_state is NodeState.SUPERSEDED:
        if not lifecycle or lifecycle[-1].get("new_state") != NodeState.SUPERSEDED.value:
            raise ContractError("superseded predecessor lacks terminal lifecycle evidence")
        pre_supersession_state = NodeState(lifecycle[-1]["prior_state"])
    if pre_supersession_state not in {NodeState.BLOCKED, NodeState.FAILED, NodeState.REVIEWED}:
        raise ContractError("successor predecessor has no terminal retry/reroute evidence")
    if successor["base_commit_sha"] != predecessor["base_commit_sha"]:
        raise ContractError("successor must preserve the predecessor base commit")
    predecessor_round, successor_round = predecessor["correction_round"], successor["correction_round"]
    maximum = predecessor["budgets"]["maximum_correction_rounds"]
    if successor["budgets"]["maximum_correction_rounds"] != maximum:
        raise ContractError("successor must preserve maximum correction rounds")
    if successor_round > maximum:
        raise ContractError("successor exceeds maximum correction rounds")
    checkpoint_sha = successor.get("resume_from_checkpoint_sha256")
    from_profile = predecessor["roles"]["builder_profile_id"]
    to_profile = successor["roles"]["builder_profile_id"]
    routes = replay_events(paths.repo_root / "build_control" / "graph" / "routing.jsonl")
    for route in routes:
        schemas.validate(route, "routing-event-v1.schema.json")
    predecessor_matches = [
        route for route in routes if route["task_manifest_sha256"] == predecessor_sha
    ]
    successor_matches = [
        route for route in routes
        if route["successor_task_manifest_sha256"] == successor_sha
    ]
    if len(predecessor_matches) > 1 or len(successor_matches) > 1:
        raise ContractError("predecessor or successor has multiple immutable routing events")
    if predecessor_matches and (
        predecessor_matches[0]["successor_task_manifest_sha256"] != successor_sha
    ):
        raise ContractError("predecessor already routes to a different successor")
    if successor_matches and successor_matches[0]["task_manifest_sha256"] != predecessor_sha:
        raise ContractError("successor already routes from a different predecessor")
    if (
        predecessor_matches
        and successor_matches
        and predecessor_matches[0]["event_sha256"] != successor_matches[0]["event_sha256"]
    ):
        raise ContractError("predecessor and successor route indices disagree")
    matches = predecessor_matches or successor_matches
    if matches:
        if successor_current not in {
            NodeState.READY,
            NodeState.DISPATCHED,
            NodeState.FAILED,
            NodeState.BLOCKED,
        }:
            raise ContractError("routed successor lifecycle state is inconsistent")
    elif (
        successor_current is not NodeState.READY
        or not _node_is_ready(context.graph, context.node, context)
    ):
        raise ContractError("successor task is not fully ready")
    if matches:
        route = matches[0]
        if route["task_manifest_sha256"] != predecessor_sha:
            raise ContractError("existing successor route names a different predecessor")
        action, reason = route["action"], route["reason_code"]
        checkpoint_sha = route["checkpoint_sha256"]
        if reason != {"RESUME": "CHECKPOINT_RESUME", "CORRECTION": "REVIEW_CORRECTION",
                       "REROUTE": "WORKER_REROUTE", "RETRY": "EXPLICIT_RETRY"}.get(action):
            raise ContractError("existing successor route action/reason contract mismatch")
        if action == "RESUME":
            if checkpoint_sha is None or successor_round != predecessor_round:
                raise ContractError("existing resume route violates exact round/checkpoint lineage")
            _validate_resume_lineage(paths, predecessor_sha, predecessor, checkpoint_sha)
        elif action == "CORRECTION":
            if (pre_supersession_state is not NodeState.REVIEWED
                    or successor_round != predecessor_round + 1 or successor_round > maximum):
                raise ContractError("existing correction route lacks review-terminal increment evidence")
        elif action == "REROUTE":
            if from_profile == to_profile or successor_round != predecessor_round:
                raise ContractError("existing reroute violates exact profile/round evidence")
        elif action == "RETRY":
            if (pre_supersession_state is not NodeState.FAILED or from_profile != to_profile
                    or successor_round != predecessor_round):
                raise ContractError("existing retry violates exact failed/profile/round evidence")
        else:
            raise ContractError("existing successor route has an unsupported action")
    elif checkpoint_sha is not None:
        action, reason = "RESUME", "CHECKPOINT_RESUME"
        _validate_resume_lineage(paths, predecessor_sha, predecessor, checkpoint_sha)
        if successor_round != predecessor_round:
            raise ContractError("checkpoint resume must preserve correction round")
    elif successor_round == predecessor_round + 1 and pre_supersession_state is NodeState.REVIEWED:
        if successor_round > maximum:
            raise ContractError("successor exceeds maximum correction rounds")
        action, reason = "CORRECTION", "REVIEW_CORRECTION"
    elif from_profile != to_profile and successor_round == predecessor_round:
        action, reason = "REROUTE", "WORKER_REROUTE"
    elif from_profile == to_profile and successor_round == predecessor_round and pre_supersession_state is NodeState.FAILED:
        action, reason = "RETRY", "EXPLICIT_RETRY"
    else:
        raise ContractError("successor action lacks exact retry/reroute/correction evidence")
    if pre_supersession_state is NodeState.FAILED:
        _validate_predecessor_terminal(paths, predecessor_context.node.node_id, predecessor_sha)
    expected_fields = {
        "task_manifest_sha256": predecessor_sha, "action": action,
        "from_profile_id": from_profile, "to_profile_id": to_profile,
        "reason_code": reason, "successor_task_manifest_sha256": successor_sha,
        "checkpoint_sha256": checkpoint_sha, "actor_profile_id": "codex-control-v1",
    }
    if matches:
        route = matches[0]
        if any(route.get(key) != value for key, value in expected_fields.items()):
            raise ContractError("existing successor route does not match proven action")
        routing_sha = route["event_sha256"]
    else:
        if predecessor_state is NodeState.SUPERSEDED:
            raise ContractError("superseded predecessor lacks its immutable routing event")
        body = {"protocol": "helios.build.routing-event/v1", **expected_fields,
                "recorded_at": _utc_now()}
        prospective = dict(body)
        prospective["sequence"] = len(routes) + 1
        prospective["previous_event_sha256"] = routes[-1]["event_sha256"] if routes else None
        prospective["event_sha256"] = sha256_hex(canonical_json_bytes(prospective))
        schemas.validate(prospective, "routing-event-v1.schema.json")
        routing_sha = append_event(paths.repo_root / "build_control" / "graph" / "routing.jsonl",
                                   body, paths.state_root)
    if predecessor_state is not NodeState.SUPERSEDED:
        transition_node_locked(
            lock, predecessor_context.node.node_id, predecessor_state, NodeState.SUPERSEDED,
            reason, "codex-control-v1", routing_event_sha256=routing_sha,
            policy_snapshot=policy.graph_policy,
        )
    else:
        terminal = lifecycle[-1]
        if (terminal.get("routing_event_sha256") != routing_sha
                or terminal.get("reason_code") != reason):
            raise ContractError("existing route lacks its exact lifecycle supersession binding")


def _validate_predecessor_terminal(paths: BuildPaths, node_id: str,
                                   predecessor_sha: str) -> None:
    events = replay_events(paths.repo_root / _EVENTS)
    dispatches = [event for event in events if event.get("node_id") == node_id
                  and event.get("new_state") == NodeState.DISPATCHED.value]
    if len(dispatches) != 1 or not _is_digest(dispatches[0].get("attempt_manifest_sha256")):
        raise ContractError("failed predecessor lacks one exact dispatched attempt")
    attempt_sha = dispatches[0]["attempt_manifest_sha256"]
    attempt = ContentAddressedStore(paths.repo_root / "build_control" / "graph").get_json(attempt_sha)
    if attempt["task_manifest_sha256"] != predecessor_sha:
        raise ContractError("failed predecessor attempt belongs to another task")
    records = _load_run_stages(paths, attempt["run_identity_sha256"])
    if not records or records[-1]["stage"] != "TERMINAL" or records[-1]["attempt_manifest_sha256"] != attempt_sha:
        raise ContractError("failed predecessor lacks an exact terminal run outcome")
    if records[-1]["outcome"] not in {AttemptOutcome.FAILED.value, AttemptOutcome.OUTCOME_UNKNOWN.value}:
        raise ContractError("failed predecessor terminal outcome is inconsistent")


def _validate_resume_lineage(paths: BuildPaths, predecessor_sha: str,
                             predecessor: dict[str, Any], checkpoint_sha: str) -> None:
    _require_digest(checkpoint_sha, "resume checkpoint")
    from tools.helios_build.checkpoints import _validate_durable_checkpoint_for_resume

    _validate_durable_checkpoint_for_resume(
        paths, checkpoint_sha, predecessor_sha, predecessor
    )


def _validate_fresh_dispatch(paths: BuildPaths, context: Any, task: dict[str, Any]) -> None:
    events = replay_events(paths.repo_root / _EVENTS)
    current = context.graph.event_states.get(context.node.node_id, NodeState.DRAFT)
    if current is not NodeState.READY:
        raise TransitionError(f"node {context.node.node_id} is {current.value}, not expected READY")
    if not _node_is_ready(context.graph, context.node, context):
        raise TransitionError("task dependencies, sources, or checkpoint requirements are not ready")
    _validate_transition(context, current, NodeState.DISPATCHED, "codex-control-v1", events)
    if canonical_json_bytes(context.task_for_node()) != canonical_json_bytes(task):
        raise ContractError("graph task manifest does not match the committed task")


def _block_locked(
    lock: ControllerLock, node_id: str, reason: str, policy: _CapturedPolicy
) -> DispatchResult:
    transition_node_locked(lock, node_id, NodeState.READY, NodeState.BLOCKED,
                           reason, "codex-control-v1",
                           policy_snapshot=policy.graph_policy)
    return DispatchResult(None, False, AttemptOutcome.FAILED,
                          NodeState.BLOCKED.value, reason, None)


def _task_context(paths: BuildPaths, task_sha: str, policy: _CapturedPolicy) -> Any:
    matches: list[str] = []
    for payload in policy.graph_policy.graph_manifests:
        matches.extend(node["node_id"] for node in payload["nodes"]
                       if node["node_type"] == NodeType.BUILD_TASK.value
                       and node["manifest_sha256"] == task_sha)
    if len(matches) != 1:
        raise ContractError("task must resolve to exactly one committed BuildTask graph node")
    return _resolve_graph_context(
        paths,
        matches[0],
        replay_events(paths.repo_root / _EVENTS),
        policy_snapshot=policy.graph_policy,
    )


def _load_committed_task(paths: BuildPaths, task_ref: str | Path, schemas: SchemaRegistry,
                         *, head_sha: str | None = None,
                         policy_snapshot: _CapturedPolicy | None = None,
                         ) -> tuple[str, dict[str, Any], str]:
    policy = policy_snapshot or _capture_policy_snapshot(
        paths, head_sha or _head_sha(paths.repo_root), schemas
    )
    tree = dict(policy.task_objects)
    if isinstance(task_ref, Path):
        try:
            relative = (task_ref if task_ref.is_absolute() else paths.repo_root / task_ref).relative_to(paths.repo_root).as_posix()
        except ValueError as error:
            raise ContractError("task path is outside the repository") from error
        candidates = [relative] if relative in tree else []
    else:
        _require_digest(task_ref, "task manifest")
        candidates = [path for path in tree if Path(path).name == f"{task_ref}.json"]
    if len(candidates) != 1:
        raise ContractError("task manifest reference does not resolve exactly once in HEAD")
    content = tree[candidates[0]]
    payload = _strict_json_bytes(content, "committed task manifest")
    schemas.validate(payload, "task-manifest-v1.schema.json")
    digest = sha256_hex(canonical_json_bytes(payload))
    if not isinstance(task_ref, Path) and digest != task_ref:
        raise ContractError("task manifest content does not match its reference")
    if Path(candidates[0]).name != f"{digest}.json":
        raise ContractError("task manifest filename/content digest mismatch")
    return digest, payload, candidates[0]


def _load_profile_snapshot(paths: BuildPaths, schemas: SchemaRegistry, path: Path,
                           content: bytes) -> Any:
    del paths, path
    return load_worker_profile_bytes(content, schemas)


def _capture_policy_snapshot(
    paths: BuildPaths, head: str, schemas: SchemaRegistry
) -> _CapturedPolicy:
    task_paths = tuple(
        path for path in _git_tree_paths(paths.repo_root, head, "build_control/tasks")
        if path.endswith(".json")
    )
    task_objects: list[tuple[str, bytes]] = []
    task_records: list[_TaskRecord] = []
    for relative in task_paths:
        content = _git_show(paths.repo_root, head, relative)
        payload = _strict_json_bytes(content, "captured task manifest")
        schemas.validate(payload, "task-manifest-v1.schema.json")
        digest = sha256_hex(canonical_json_bytes(payload))
        if Path(relative).name != f"{digest}.json":
            raise ContractError("captured task filename/content digest mismatch")
        task_objects.append((relative, content))
        task_records.append(_TaskRecord(digest, payload, paths.repo_root / relative))
    graph_paths = tuple(
        path for path in _git_tree_paths(paths.repo_root, head, "build_control/graph")
        if Path(path).parent.as_posix() == "build_control/graph" and path.endswith(".json")
    )
    graph_objects: list[tuple[str, bytes]] = []
    graph_manifests: list[dict[str, object]] = []
    for relative in graph_paths:
        content = _git_show(paths.repo_root, head, relative)
        payload = _strict_json_bytes(content, "captured graph control object")
        graph_objects.append((relative, content))
        if payload.get("protocol") != "helios.build.graph-manifest/v1":
            raise ContractError("captured top-level graph object erased its manifest protocol")
        schemas.validate(payload, "graph-manifest-v1.schema.json")
        graph_manifests.append(payload)
    return _CapturedPolicy(
        head,
        _PolicySnapshot(tuple(task_records), tuple(graph_manifests)),
        tuple(task_objects),
        tuple(graph_objects),
    )


def _assert_control_snapshot(paths: BuildPaths, policy: _CapturedPolicy) -> None:
    expected_tasks = dict(policy.task_objects)
    working_task_paths = {
        path.relative_to(paths.repo_root).as_posix()
        for path in (paths.repo_root / "build_control" / "tasks").rglob("*.json")
    }
    if working_task_paths != set(expected_tasks):
        raise ContractError("working task set differs from captured HEAD")
    for relative, expected in expected_tasks.items():
        if _read_regular_no_follow(paths.repo_root / relative, "task manifest") != expected:
            raise ContractError("working task control object differs from captured HEAD")
    expected_graphs = dict(policy.graph_objects)
    working_graph_paths = {
        path.relative_to(paths.repo_root).as_posix()
        for path in (paths.repo_root / "build_control" / "graph").glob("*.json")
    }
    if working_graph_paths != set(expected_graphs):
        raise ContractError("working graph manifest set differs from captured HEAD")
    for relative, expected in expected_graphs.items():
        if _read_regular_no_follow(paths.repo_root / relative, "graph manifest") != expected:
            raise ContractError("working graph control object differs from captured HEAD")


def _validate_base_commit(repo: Path, head: str, base: str) -> None:
    _require_commit(base)
    if (_git(repo, "cat-file", "-e", f"{base}^{{commit}}").returncode != 0
            or _git(repo, "merge-base", "--is-ancestor", base, head).returncode != 0):
        raise ContractError("task base_commit_sha is not a committed ancestor of captured HEAD")


def _create_attempt_directories(paths: BuildPaths, run_sha: str) -> tuple[Path, Path]:
    state = _open_secure_state_root(paths.state_root)
    descriptors = [state]
    try:
        attempts = _open_private_child_directory(state, "attempts"); descriptors.append(attempts)
        run = _open_private_child_directory(attempts, run_sha); descriptors.append(run)
        checkpoints = _open_private_child_directory(run, "checkpoints"); descriptors.append(checkpoints)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    run_path = paths.state_root / "attempts" / run_sha
    return run_path, run_path / "checkpoints"


def _open_attempt_launch_descriptors(paths: BuildPaths, run_sha: str) -> tuple[int, int]:
    state = _open_secure_state_root(paths.state_root)
    attempts: int | None = None
    run: int | None = None
    try:
        attempts = _open_private_child_directory(state, "attempts")
        run = _open_private_child_directory(attempts, run_sha)
        checkpoints = _open_private_child_directory(run, "checkpoints")
        return run, checkpoints
    except BaseException:
        if run is not None:
            os.close(run)
        raise
    finally:
        if attempts is not None:
            os.close(attempts)
        os.close(state)


def _read_child_file(parent_descriptor: int, name: str, max_bytes: int) -> bytes | None:
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ContractError("adapter handoff finite size bound is invalid")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ContractError(f"cannot safely open adapter handoff: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ContractError("adapter handoff is not a regular file")
        if metadata.st_size > max_bytes:
            raise ContractError("adapter handoff exceeds its finite size bound")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > max_bytes:
            raise ContractError("adapter handoff exceeds its finite size bound")
        return content
    finally:
        os.close(descriptor)


def _publish_or_verify_external(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        if _read_regular_no_follow(path, "external attempt file") != content:
            raise ContractError("existing external attempt file differs from exact content")
        return
    except OSError as error:
        raise ContractError(f"cannot publish external attempt file: {error}") from error
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0: raise OSError("external attempt write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _head_sha(repo: Path) -> str:
    result = _git(repo, "rev-parse", "HEAD")
    if result.returncode != 0:
        raise ContractError("cannot capture repository HEAD")
    value = result.stdout.decode("ascii", errors="strict").strip()
    _require_commit(value)
    return value


def _git_tree_paths(repo: Path, head: str, prefix: str) -> tuple[str, ...]:
    result = _git(repo, "ls-tree", "-r", "--name-only", "-z", head, "--", prefix)
    if result.returncode != 0:
        raise ContractError("cannot read captured HEAD tree")
    if not result.stdout: return ()
    if not result.stdout.endswith(b"\0"): raise ContractError("Git tree path list is malformed")
    return tuple(item.decode("utf-8", errors="strict") for item in result.stdout[:-1].split(b"\0"))


def _git_show(repo: Path, head: str, relative: str) -> bytes:
    result = _git(repo, "show", f"{head}:{relative}")
    if result.returncode != 0:
        raise ContractError(f"captured HEAD object is unavailable: {relative}")
    return result.stdout


def _git(repo: Path, *argv: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["git", "-C", str(repo), *argv], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env={"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
                              shell=False, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ContractError(f"cannot inspect committed Git state: {error}") from error


def _regular_file_no_follow(path: Path) -> bool:
    try: metadata = path.lstat()
    except FileNotFoundError: return False
    except OSError as error: raise ContractError(f"cannot inspect adapter handoff: {error}") from error
    return stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)


def _read_regular_no_follow(path: Path, label: str) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise ContractError(f"cannot safely open {label}: {error}") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode): raise ContractError(f"{label} is not a regular file")
        return _read_fd(descriptor)
    finally: os.close(descriptor)


def _read_fd(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 1024 * 1024): chunks.append(chunk)
    return b"".join(chunks)


def _strict_json_bytes(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8", errors="strict"), object_pairs_hook=_unique_object,
                           parse_float=_reject_float, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        raise ContractError(f"{label} must be a canonical JSON object")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value: raise ContractError(f"duplicate object key: {key}")
        value[key] = item
    return value


def _reject_float(_: str) -> NoReturn: raise ContractError("floating-point values are forbidden")
def _reject_constant(value: str) -> NoReturn: raise ContractError(f"invalid JSON constant: {value}")
def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
def _require_digest(value: object, label: str) -> None:
    if not _is_digest(value): raise ContractError(f"{label} SHA-256 must be lowercase hexadecimal")
def _require_commit(value: object) -> None:
    if not isinstance(value, str) or len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
        raise ContractError("base commit SHA must be lowercase hexadecimal")
def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
