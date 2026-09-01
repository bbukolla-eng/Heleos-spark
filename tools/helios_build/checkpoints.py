"""Immutable checkpoint collection from exact attempt and Git truth."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, NoReturn

from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build._external_cas import put_private_bytes, read_private_bytes
from tools.helios_build.dispatch import (
    AttemptRecord,
    _head_sha,
    _load_committed_task,
    _read_regular_no_follow,
)
from tools.helios_build.doctor import _open_private_child_directory, _open_secure_state_root
from tools.helios_build.errors import ContractError
from tools.helios_build.ownership import assert_owned_changes
from tools.helios_build.paths import BuildPaths
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore
from tools.helios_build.worktrees import open_worktree_descriptor, verify_detached_worktree


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    sha256: str
    path: Path
    payload: dict[str, Any]

    @property
    def boundary_id(self) -> str:
        return str(self.payload["boundary_id"])


def collect_checkpoints(paths: BuildPaths, attempt: object,
                        task: Mapping[str, object]) -> tuple[CheckpointRecord, ...]:
    """Validate external checkpoint proposals and publish canonical immutable records."""
    if not isinstance(paths, BuildPaths) or type(attempt) is not AttemptRecord:
        raise ContractError("checkpoint collection requires an exact durable AttemptRecord")
    assert isinstance(attempt, AttemptRecord)
    schemas = SchemaRegistry(paths.repo_root)
    persisted = _resolve_attempt(paths, schemas, attempt)
    task_sha = persisted["task_manifest_sha256"]
    persisted_task_sha, persisted_task, _ = _load_committed_task(
        paths, task_sha, schemas, head_sha=_head_sha(paths.repo_root)
    )
    if persisted_task_sha != task_sha or canonical_json_bytes(dict(task)) != canonical_json_bytes(persisted_task):
        raise ContractError("checkpoint caller task does not match the exact persisted task")
    if persisted["base_commit_sha"] != persisted_task["base_commit_sha"]:
        raise ContractError("checkpoint attempt base does not match its task")
    expected_run = persisted["run_identity_sha256"]
    expected_dir = paths.state_root / "attempts" / expected_run / "checkpoints"
    if attempt.checkpoint_dir != expected_dir:
        raise ContractError("checkpoint directory is not derived from the persisted attempt")
    verify_detached_worktree(
        paths, attempt.worktree, persisted["base_commit_sha"],
        expected_inode=attempt.worktree_inode, require_clean=False,
        require_exact_head=False,
    )
    proposals = _checkpoint_proposals(paths, expected_run)
    boundaries = persisted_task["checkpoint_boundaries"]
    store = ContentAddressedStore(paths.repo_root / "build_control" / "graph")
    seen: set[str] = set()
    collected: list[CheckpointRecord] = []
    for _, content in proposals:
        payload = _strict_json(content, "checkpoint")
        schemas.validate(payload, "checkpoint-v1.schema.json")
        if payload["attempt_manifest_sha256"] != attempt.sha256:
            raise ContractError("checkpoint attempt_manifest_sha256 mismatch")
        if payload["base_commit_sha"] != persisted["base_commit_sha"]:
            raise ContractError("checkpoint base_commit_sha mismatch")
        boundary = payload["boundary_id"]
        if boundary not in boundaries:
            raise ContractError(f"undeclared checkpoint boundary: {boundary}")
        if boundary in seen:
            raise ContractError(f"duplicate checkpoint boundary: {boundary}")
        seen.add(boundary)
        payload = dict(payload)
        payload["artifact_sha256s"] = list(
            _canonicalize_artifacts(
                paths,
                store,
                schemas,
                payload["artifact_sha256s"],
                attempt.sha256,
            )
        )
        _validate_durable_checkpoint_payload(
            paths,
            payload,
            persisted,
            persisted_task,
            task_sha,
            schemas,
            store,
            live_attempt=attempt,
        )
        stored = store.put_json("checkpoints", payload)
        collected.append(CheckpointRecord(stored.sha256, stored.path, payload))
    return tuple(collected)


def _resolve_attempt(paths: BuildPaths, schemas: SchemaRegistry,
                     attempt: AttemptRecord) -> dict[str, Any]:
    if not _is_digest(attempt.sha256):
        raise ContractError("attempt digest is invalid")
    expected = (paths.repo_root / "build_control" / "graph" / "attempts" / "sha256"
                / attempt.sha256[:2] / f"{attempt.sha256}.json")
    if attempt.path != expected:
        raise ContractError("attempt path is not the exact CAS path")
    content = _read_regular_no_follow(expected, "attempt manifest")
    if sha256_hex(content) != attempt.sha256:
        raise ContractError("attempt manifest digest mismatch")
    payload = _strict_json(content, "attempt manifest")
    schemas.validate(payload, "attempt-manifest-v1.schema.json")
    if canonical_json_bytes(attempt.manifest) != content:
        raise ContractError("AttemptRecord manifest differs from persisted attempt")
    run = payload["run_identity_sha256"]
    if (
        attempt.worktree != paths.state_root / "worktrees" / run
        or attempt.task_manifest_path != paths.state_root / "attempts" / run / "task-manifest.json"
        or attempt.handoff_path != paths.state_root / "attempts" / run / "handoff.json"
    ):
        raise ContractError("AttemptRecord external paths are not derived from its identity")
    return payload


def _validate_durable_checkpoint_for_resume(
    paths: BuildPaths,
    checkpoint_sha: str,
    predecessor_sha: str,
    predecessor: Mapping[str, object],
) -> dict[str, Any]:
    """Revalidate a checkpoint exclusively from immutable Git/private-CAS truth."""
    if not _is_digest(checkpoint_sha):
        raise ContractError("resume checkpoint digest is invalid")
    schemas = SchemaRegistry(paths.repo_root)
    store = ContentAddressedStore(paths.repo_root / "build_control" / "graph")
    checkpoint = _load_cas_json(
        paths, "checkpoints", checkpoint_sha, schemas, "checkpoint-v1.schema.json"
    )
    attempt_sha = checkpoint["attempt_manifest_sha256"]
    attempt = _load_cas_json(
        paths, "attempts", attempt_sha, schemas, "attempt-manifest-v1.schema.json"
    )
    if sha256_hex(canonical_json_bytes(dict(predecessor))) != predecessor_sha:
        raise ContractError("resume predecessor task digest mismatch")
    _validate_durable_checkpoint_payload(
        paths,
        checkpoint,
        attempt,
        predecessor,
        predecessor_sha,
        schemas,
        store,
        live_attempt=None,
    )
    return checkpoint


def _validate_durable_checkpoint_payload(
    paths: BuildPaths,
    checkpoint: dict[str, Any],
    attempt: Mapping[str, object],
    task: Mapping[str, object],
    task_sha: str,
    schemas: SchemaRegistry,
    store: ContentAddressedStore,
    *,
    live_attempt: AttemptRecord | None,
) -> None:
    schemas.validate(checkpoint, "checkpoint-v1.schema.json")
    attempt_sha = sha256_hex(canonical_json_bytes(dict(attempt)))
    if (
        checkpoint["attempt_manifest_sha256"] != attempt_sha
        or attempt["task_manifest_sha256"] != task_sha
        or attempt["base_commit_sha"] != task["base_commit_sha"]
        or checkpoint["base_commit_sha"] != task["base_commit_sha"]
    ):
        raise ContractError("checkpoint attempt/task/base lineage mismatch")
    artifacts = checkpoint["artifact_sha256s"]
    commands = checkpoint["command_receipt_sha256s"]
    if not artifacts:
        raise ContractError("checkpoint artifact evidence cannot be empty")
    if len(artifacts) < task["evidence_threshold"]:
        raise ContractError("checkpoint artifact evidence is below the task threshold")
    if len(set(artifacts)) != len(artifacts):
        raise ContractError("checkpoint contains duplicate artifact evidence")
    _validate_canonical_artifacts(paths, store, schemas, artifacts, attempt_sha)
    _validate_commands(paths, store, schemas, commands, task, task_sha)
    if (checkpoint["commit_sha"] is None) == (checkpoint["patch_sha256"] is None):
        raise ContractError("checkpoint must declare exactly one commit or patch")
    if live_attempt is not None:
        if live_attempt.sha256 != attempt_sha:
            raise ContractError("live checkpoint attempt differs from durable attempt")
        changed = _changed_paths_from_attempt(paths, live_attempt, checkpoint)
    else:
        changed = _changed_paths_from_immutable(paths, attempt, checkpoint)
    if not changed:
        raise ContractError("checkpoint contains no changed paths")
    assert_owned_changes(task, changed)


def _checkpoint_proposals(paths: BuildPaths, run_sha: str) -> tuple[tuple[str, bytes], ...]:
    state = _open_secure_state_root(paths.state_root)
    descriptors = [state]
    try:
        attempts = _open_existing_directory(state, "attempts"); descriptors.append(attempts)
        run = _open_existing_directory(attempts, run_sha); descriptors.append(run)
        checkpoints = _open_existing_directory(run, "checkpoints"); descriptors.append(checkpoints)
        names = sorted(os.listdir(checkpoints))
        proposals: list[tuple[str, bytes]] = []
        for name in names:
            if not name.endswith(".json") or "/" in name or "\\" in name:
                raise ContractError("checkpoint directory contains an undeclared file type")
            descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                                 | getattr(os, "O_NOFOLLOW", 0), dir_fd=checkpoints)
            try:
                metadata = os.fstat(descriptor)
                current_uid = os.getuid() if hasattr(os, "getuid") else None
                if (current_uid is None or not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_uid != current_uid or stat.S_IMODE(metadata.st_mode) != 0o600):
                    raise ContractError("checkpoint proposal lacks exact private custody")
                proposals.append((name, _read_fd(descriptor)))
            finally:
                os.close(descriptor)
        return tuple(proposals)
    except OSError as error:
        raise ContractError(f"cannot safely enumerate checkpoint proposals: {error}") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _canonicalize_artifacts(
    paths: BuildPaths,
    store: ContentAddressedStore,
    schemas: SchemaRegistry,
    digests: list[str],
    attempt_sha: str,
) -> tuple[str, ...]:
    canonical: list[str] = []
    for manifest_sha in digests:
        artifact = _load_cas_json(
            paths, "artifacts", manifest_sha, schemas, "artifact-manifest-v1.schema.json"
        )
        if artifact["attempt_manifest_sha256"] != attempt_sha:
            raise ContractError("checkpoint artifact belongs to a different attempt")
        if artifact["store_key"] == (
            f"artifacts/sha256/{artifact['sha256'][:2]}/{artifact['sha256']}.bin"
        ):
            content = read_private_bytes(
                paths,
                artifact["store_key"],
                artifact["sha256"],
                artifact["byte_size"],
            )
        else:
            content = _read_artifact_bytes(paths, artifact["store_key"])
        if len(content) != artifact["byte_size"] or sha256_hex(content) != artifact["sha256"]:
            raise ContractError("checkpoint artifact byte hash/size mismatch")
        private = put_private_bytes(paths, "artifacts", content)
        canonical_manifest = dict(artifact)
        canonical_manifest["store_key"] = private.store_key
        schemas.validate(canonical_manifest, "artifact-manifest-v1.schema.json")
        canonical.append(store.put_json("artifacts", canonical_manifest).sha256)
    return tuple(canonical)


def _validate_canonical_artifacts(
    paths: BuildPaths,
    store: ContentAddressedStore,
    schemas: SchemaRegistry,
    digests: list[str],
    attempt_sha: str,
) -> None:
    for manifest_sha in digests:
        artifact = _load_cas_json(
            paths, "artifacts", manifest_sha, schemas, "artifact-manifest-v1.schema.json"
        )
        if artifact["attempt_manifest_sha256"] != attempt_sha:
            raise ContractError("canonical checkpoint artifact belongs to a different attempt")
        expected_key = f"artifacts/sha256/{artifact['sha256'][:2]}/{artifact['sha256']}.bin"
        if artifact["store_key"] != expected_key:
            raise ContractError("checkpoint artifact is not in canonical private custody")
        read_private_bytes(
            paths,
            artifact["store_key"],
            artifact["sha256"],
            artifact["byte_size"],
        )


def _read_artifact_bytes(paths: BuildPaths, store_key: str) -> bytes:
    if not isinstance(store_key, str):
        raise ContractError("artifact store key is invalid")
    parts = Path(store_key).parts
    if not parts or any(part in {"", ".", ".."} for part in parts) or Path(store_key).is_absolute():
        raise ContractError("artifact store key is unsafe")
    state = _open_secure_state_root(paths.state_root)
    descriptors = [state]
    try:
        artifacts = _open_existing_directory(state, "artifacts"); descriptors.append(artifacts)
        parent = artifacts
        for component in parts[:-1]:
            parent = _open_existing_directory(parent, component); descriptors.append(parent)
        descriptor = os.open(parts[-1], os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                             | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        try:
            metadata = os.fstat(descriptor)
            current_uid = os.getuid() if hasattr(os, "getuid") else None
            if (current_uid is None or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != current_uid or stat.S_IMODE(metadata.st_mode) != 0o600):
                raise ContractError("artifact bytes lack exact current-owner custody")
            return _read_fd(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ContractError(f"cannot resolve artifact store key: {error}") from error
    finally:
        for descriptor in reversed(descriptors): os.close(descriptor)


def _validate_commands(paths: BuildPaths, store: ContentAddressedStore, schemas: SchemaRegistry,
                       digests: list[str], task: Mapping[str, object],
                       task_sha: str) -> None:
    acceptance = task["acceptance"]
    commands = acceptance["commands"]
    declared = {command["command_id"]: command for command in commands}
    if len(digests) != len(set(digests)):
        raise ContractError("checkpoint contains duplicate command receipts")
    seen_ids: set[str] = set()
    for digest in digests:
        receipt = _load_cas_json(
            paths, "receipts", digest, schemas, "command-receipt-v1.schema.json"
        )
        command = declared.get(receipt["command_id"])
        if command is None or receipt["task_manifest_sha256"] != task_sha:
            raise ContractError("checkpoint command receipt is not declared by this task")
        if receipt["command_id"] in seen_ids:
            raise ContractError("checkpoint repeats a declared command")
        seen_ids.add(receipt["command_id"])
        expected = {
            "gate": command["gate"],
            "argv_sha256": sha256_hex(canonical_json_bytes(command["argv"])),
            "cwd_key": sha256_hex(canonical_json_bytes({"cwd": command["cwd"]})),
            "correction_round": task["correction_round"],
            "exit_code": command["expected_exit_code"],
            "outcome": "PASS",
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ContractError("checkpoint command receipt semantic mismatch")
        if receipt["duration_ms"] > command["timeout_seconds"] * 1000:
            raise ContractError("checkpoint command receipt exceeds declared timeout")
        _validate_raw_object(paths, "stdout", receipt["stdout_sha256"])
        _validate_raw_object(paths, "stderr", receipt["stderr_sha256"])
    if seen_ids != set(declared):
        raise ContractError("checkpoint does not contain the complete declared command set")


def _validate_raw_object(paths: BuildPaths, stream: str, digest: str) -> None:
    if stream not in {"stdout", "stderr"}:
        raise ContractError("checkpoint raw stream name is invalid")
    _is_digest_value = isinstance(digest, str) and len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest)
    if not _is_digest_value:
        raise ContractError("checkpoint raw stream digest is invalid")
    state = _open_secure_state_root(paths.state_root)
    descriptors = [state]
    try:
        parent = state
        for component in ("preflight", "raw", "sha256", digest[:2]):
            parent = _open_existing_directory(parent, component)
            descriptors.append(parent)
        try:
            descriptor = os.open(
                f"{digest}.{stream}",
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent,
            )
        except OSError as error:
            raise ContractError(
                f"cannot open checkpoint {stream} raw object: {error}"
            ) from error
        try:
            metadata = os.fstat(descriptor)
            current_uid = os.getuid() if hasattr(os, "getuid") else None
            if (
                current_uid is None
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != current_uid
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ContractError(f"checkpoint {stream} raw object lacks exact custody")
            content = _read_fd(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ContractError(
            f"cannot descriptor-walk checkpoint {stream} raw custody: {error}"
        ) from error
    finally:
        for directory in reversed(descriptors):
            os.close(directory)
    if sha256_hex(content) != digest:
        raise ContractError(f"checkpoint {stream} raw object digest mismatch")


def _changed_paths_from_attempt(paths: BuildPaths, attempt: AttemptRecord,
                                checkpoint: dict[str, Any]) -> tuple[str, ...]:
    commit_sha, patch_sha = checkpoint["commit_sha"], checkpoint["patch_sha256"]
    if (commit_sha is None) == (patch_sha is None):
        raise ContractError("checkpoint must declare exactly one commit or patch")
    descriptor = open_worktree_descriptor(paths, attempt.worktree)
    try:
        cwd = Path("/dev/fd") / str(descriptor)
        head = _git_at(cwd, "rev-parse", "HEAD", pass_fd=descriptor)
        if head.returncode != 0:
            raise ContractError("cannot resolve attempt worktree HEAD")
        exact_head = head.stdout.decode("ascii", errors="strict").strip()
        if commit_sha is not None:
            if exact_head != commit_sha:
                raise ContractError("checkpoint commit is not the exact attempt worktree HEAD")
            if _git_at(cwd, "merge-base", "--is-ancestor", attempt.base_commit_sha, commit_sha, pass_fd=descriptor).returncode != 0:
                raise ContractError("checkpoint commit does not descend from the attempt base")
            status = _git_at(cwd, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                             pass_fd=descriptor)
            if status.returncode != 0 or status.stdout:
                raise ContractError("checkpoint commit worktree is not exactly clean")
        else:
            patch = _git_at(cwd, "diff", "--binary", attempt.base_commit_sha, "--", pass_fd=descriptor)
            if patch.returncode != 0 or sha256_hex(patch.stdout) != patch_sha:
                raise ContractError("checkpoint patch is not the exact attempt worktree patch")
            persisted_patch = _find_patch(paths, patch_sha)
            if persisted_patch != patch.stdout:
                raise ContractError("checkpoint patch bytes differ from immutable patch CAS")
            status = _git_at(cwd, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                             pass_fd=descriptor)
            if status.returncode != 0 or any(entry.startswith(b"?? ") for entry in status.stdout.split(b"\0") if entry):
                raise ContractError("checkpoint patch has untracked or unreadable status entries")
        changed = _git_at(cwd, "diff", "--name-only", "-z", attempt.base_commit_sha,
                          commit_sha or exact_head, "--", pass_fd=descriptor) if commit_sha is not None else _git_at(
                              cwd, "diff", "--name-only", "-z", attempt.base_commit_sha, "--", pass_fd=descriptor)
        if changed.returncode != 0:
            raise ContractError("cannot derive checkpoint changes from attempt worktree")
        paths_changed = _decode_nul_paths(changed.stdout)
        if not paths_changed:
            raise ContractError("checkpoint contains no changed paths")
        return paths_changed
    finally:
        os.close(descriptor)


def _changed_paths_from_immutable(
    paths: BuildPaths,
    attempt: Mapping[str, object],
    checkpoint: Mapping[str, object],
) -> tuple[str, ...]:
    base = attempt["base_commit_sha"]
    commit_sha, patch_sha = checkpoint["commit_sha"], checkpoint["patch_sha256"]
    if not isinstance(base, str):
        raise ContractError("resume attempt base commit is invalid")
    if commit_sha is not None:
        if not isinstance(commit_sha, str):
            raise ContractError("resume checkpoint commit is invalid")
        if (
            _git_at(paths.repo_root, "cat-file", "-e", f"{commit_sha}^{{commit}}").returncode
            != 0
            or _git_at(
                paths.repo_root, "merge-base", "--is-ancestor", base, commit_sha
            ).returncode
            != 0
        ):
            raise ContractError("resume checkpoint commit does not descend from its base")
        changed = _git_at(
            paths.repo_root, "diff", "--name-only", "-z", base, commit_sha, "--"
        )
        if changed.returncode != 0:
            raise ContractError("cannot derive resume changes from immutable commit")
        return _decode_nul_paths(changed.stdout)
    if not isinstance(patch_sha, str):
        raise ContractError("resume checkpoint patch is invalid")
    patch = _find_patch(paths, patch_sha)
    return _changed_paths_from_patch(paths, base, patch)


def _changed_paths_from_patch(
    paths: BuildPaths, base_commit_sha: str, patch: bytes
) -> tuple[str, ...]:
    try:
        with tempfile.TemporaryDirectory(prefix="checkpoint-index-", dir=paths.state_root) as root:
            index = Path(root) / "index"
            environment = {
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "Never",
                "GIT_INDEX_FILE": str(index),
            }
            read_tree = subprocess.run(
                ["git", "-C", str(paths.repo_root), "read-tree", base_commit_sha],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                shell=False,
                check=False,
                timeout=30,
            )
            if read_tree.returncode != 0:
                raise ContractError("cannot materialize immutable checkpoint patch base")
            check = subprocess.run(
                ["git", "-C", str(paths.repo_root), "apply", "--cached", "--check", "--binary", "-"],
                input=patch,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                shell=False,
                check=False,
                timeout=30,
            )
            if check.returncode != 0:
                raise ContractError("checkpoint patch does not apply to its immutable base")
        numstat = subprocess.run(
            ["git", "-C", str(paths.repo_root), "apply", "--numstat", "-z", "--binary", "-"],
            input=patch,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
            shell=False,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ContractError(f"cannot inspect immutable checkpoint patch: {error}") from error
    if numstat.returncode != 0 or not numstat.stdout.endswith(b"\0"):
        raise ContractError("cannot derive changed paths from immutable checkpoint patch")
    items = numstat.stdout[:-1].split(b"\0")
    raw_paths: list[bytes] = []
    index = 0
    while index < len(items):
        fields = items[index].split(b"\t", 2)
        if len(fields) != 3:
            raise ContractError("immutable checkpoint patch numstat is malformed")
        if fields[2]:
            raw_paths.append(fields[2])
            index += 1
        else:
            if index + 2 >= len(items):
                raise ContractError("immutable checkpoint rename numstat is malformed")
            raw_paths.extend((items[index + 1], items[index + 2]))
            index += 3
    try:
        changed = tuple(dict.fromkeys(path.decode("utf-8", errors="strict") for path in raw_paths))
    except UnicodeDecodeError as error:
        raise ContractError("immutable checkpoint patch contains a non-UTF-8 path") from error
    if any(not path for path in changed):
        raise ContractError("immutable checkpoint patch returned an empty path")
    return changed


def _find_patch(paths: BuildPaths, digest: str) -> bytes:
    return _read_git_cas_bytes(
        paths, "patches", digest, ".patch", "checkpoint patch"
    )


def _open_existing_directory(parent: int, name: str) -> int:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ContractError("external directory component is unsafe")
    descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                         | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
    metadata = os.fstat(descriptor)
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    if (current_uid is None or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != current_uid
            or stat.S_IMODE(metadata.st_mode) != 0o700):
        os.close(descriptor)
        raise ContractError("external directory lacks current-owner custody")
    return descriptor


def _git_at(cwd: Path, *argv: str, pass_fd: int | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["git", "-C", str(cwd), *argv], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env={"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
                              shell=False, check=False, timeout=30,
                              pass_fds=() if pass_fd is None else (pass_fd,))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ContractError(f"cannot inspect exact attempt worktree: {error}") from error


def _decode_nul_paths(content: bytes) -> tuple[str, ...]:
    if not content: return ()
    if not content.endswith(b"\0"): raise ContractError("Git returned an unterminated path list")
    try: paths = tuple(item.decode("utf-8", errors="strict") for item in content[:-1].split(b"\0"))
    except UnicodeDecodeError as error: raise ContractError("Git returned a non-UTF-8 path") from error
    if any(not path for path in paths): raise ContractError("Git returned an empty path")
    return paths


def _read_git_cas_bytes(
    paths: BuildPaths,
    collection: str,
    digest: str,
    suffix: str,
    label: str,
) -> bytes:
    if collection not in {"artifacts", "attempts", "checkpoints", "patches", "receipts"}:
        raise ContractError("Git CAS collection is not permitted")
    if not _is_digest(digest) or suffix not in {".json", ".patch"}:
        raise ContractError("Git CAS digest or suffix is invalid")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise ContractError("Git CAS custody requires no-follow nonblocking opens")
    directory_flags = (
        os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        root = os.open(paths.repo_root, directory_flags)
    except OSError as error:
        raise ContractError(f"cannot pin repository root for {label}: {error}") from error
    descriptors = [root]
    try:
        _verify_git_cas_directory(root, "repository root")
        parent = root
        for component in (
            "build_control", "graph", collection, "sha256", digest[:2]
        ):
            parent = _open_git_cas_directory(parent, component)
            descriptors.append(parent)
        try:
            descriptor = os.open(
                f"{digest}{suffix}", file_flags, dir_fd=parent
            )
        except OSError as error:
            raise ContractError(f"cannot open {label}: {error}") from error
        try:
            metadata = os.fstat(descriptor)
            current_uid = os.getuid() if hasattr(os, "getuid") else None
            if (
                current_uid is None
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != current_uid
                or stat.S_IMODE(metadata.st_mode) != 0o644
            ):
                raise ContractError(
                    f"{label} lacks exact current-owner regular-file mode-0644 Git CAS custody"
                )
            content = _read_fd(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ContractError(f"cannot descriptor-walk {label}: {error}") from error
    finally:
        for directory in reversed(descriptors):
            os.close(directory)
    if sha256_hex(content) != digest:
        raise ContractError(f"{label} digest mismatch")
    return content


def _open_git_cas_directory(parent: int, name: str) -> int:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ContractError("Git CAS directory component is unsafe")
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=parent,
    )
    try:
        _verify_git_cas_directory(descriptor, f"Git CAS directory {name}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _verify_git_cas_directory(descriptor: int, label: str) -> None:
    metadata = os.fstat(descriptor)
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    if (
        current_uid is None
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != current_uid
        or stat.S_IMODE(metadata.st_mode) != 0o755
    ):
        raise ContractError(f"{label} lacks exact current-owner mode-0755 custody")


def _load_cas_json(
    paths: BuildPaths,
    collection: str,
    digest: str,
    schemas: SchemaRegistry,
    schema_name: str,
) -> dict[str, Any]:
    content = _read_git_cas_bytes(
        paths, collection, digest, ".json", f"canonical {collection} evidence"
    )
    payload = _strict_json(content, f"canonical {collection} evidence")
    schemas.validate(payload, schema_name)
    return payload


def _strict_json(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8", errors="strict"), object_pairs_hook=_unique_object,
                           parse_float=_reject_float, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != content:
        raise ContractError(f"{label} must be a canonical JSON object")
    return value


def _read_fd(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 1024 * 1024): chunks.append(chunk)
    return b"".join(chunks)


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
