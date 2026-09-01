"""Exact host preflight and sanitized Build Fabric doctor reporting."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.helios_build.canonical import sha256_hex
from tools.helios_build.errors import ContractError
from tools.helios_build.paths import BuildPaths
from tools.helios_build.process import (
    ProcessLaunchError,
    ProcessResult,
    run_bounded_process,
)
from tools.helios_build.profiles import (
    AdapterConfiguration,
    WorkerProfile,
    adapter_configuration_sha256,
    load_adapter_configuration,
    load_worker_profile,
    worker_profile_sha256,
)
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_FILE_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_CURRENT_UID = os.getuid() if hasattr(os, "getuid") else None
_LAUNCH_UNAVAILABLE_ERRNOS = {
    errno.EACCES,
    errno.ENOENT,
    errno.ENOEXEC,
    errno.ETXTBSY,
}


class _ExecutableUnavailable(OSError):
    """The configured path cannot name one exact executable inode."""


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """One exact availability decision and its optional sanitized receipt."""

    availability: str
    reason_code: str
    worker_profile_sha256: str
    adapter_configuration_sha256: str | None
    receipt_sha256: str | None
    receipt_path: Path | None
    returncode: int | None
    timed_out: bool


@dataclass(slots=True)
class _VerifiedExecutable:
    descriptor: int
    sha256: str

    def close(self) -> None:
        if self.descriptor < 0:
            return
        descriptor = self.descriptor
        self.descriptor = -1
        os.close(descriptor)


@dataclass(slots=True)
class _RunDirectory:
    state_descriptor: int
    preflight_descriptor: int
    run_descriptor: int
    run_name: str
    path: Path

    def close_and_remove(self) -> None:
        error: OSError | None = None
        try:
            _remove_tree_contents(self.run_descriptor)
        except OSError as caught:
            error = caught
        finally:
            os.close(self.run_descriptor)
        try:
            os.rmdir(self.run_name, dir_fd=self.preflight_descriptor)
            os.fsync(self.preflight_descriptor)
        except OSError as caught:
            error = error or caught
        finally:
            os.close(self.preflight_descriptor)
            os.close(self.state_descriptor)
        if error is not None:
            raise ContractError(f"cannot securely remove preflight run directory: {error}") from error


def preflight_worker(
    paths: BuildPaths,
    profile: WorkerProfile,
    adapter: AdapterConfiguration | None,
    *,
    expected_result_protocol: str | None = None,
) -> PreflightResult:
    """Evaluate one configured local adapter exactly once without retry or discovery."""
    if not isinstance(paths, BuildPaths) or not isinstance(profile, WorkerProfile):
        raise ContractError("preflight requires BuildPaths and WorkerProfile")
    profile_sha = worker_profile_sha256(profile)
    if adapter is None:
        return PreflightResult(
            availability="UNAVAILABLE",
            reason_code="ADAPTER_NOT_CONFIGURED",
            worker_profile_sha256=profile_sha,
            adapter_configuration_sha256=None,
            receipt_sha256=None,
            receipt_path=None,
            returncode=None,
            timed_out=False,
        )
    if "LOCAL_ADAPTER" not in profile.transports:
        raise ContractError(f"profile {profile.profile_id} does not permit LOCAL_ADAPTER")
    if adapter.worker_id != profile.worker_id:
        raise ContractError("adapter worker_id does not match worker profile")
    expected_protocol = (
        profile.adapter_protocol
        if expected_result_protocol is None
        else expected_result_protocol
    )
    if not isinstance(expected_protocol, str) or not expected_protocol:
        raise ContractError("preflight expected result protocol must be non-empty")
    if adapter.result_protocol != expected_protocol:
        raise ContractError("adapter result_protocol does not match the expected result protocol")

    started_at = _utc_now()
    started_ns = time.monotonic_ns()
    adapter_sha = adapter_configuration_sha256(adapter)
    try:
        executable = _open_verified_executable(adapter.executable)
    except _ExecutableUnavailable:
        return _publish_preflight(
            paths,
            profile_sha=profile_sha,
            adapter=adapter,
            adapter_sha=adapter_sha,
            availability="UNAVAILABLE",
            reason_code="EXECUTABLE_MISSING",
            result=None,
            started_at=started_at,
            started_ns=started_ns,
        )
    try:
        if executable.sha256 != adapter.executable_sha256:
            return _publish_preflight(
                paths,
                profile_sha=profile_sha,
                adapter=adapter,
                adapter_sha=adapter_sha,
                availability="UNAVAILABLE",
                reason_code="EXECUTABLE_HASH_MISMATCH",
                result=None,
                started_at=started_at,
                started_ns=started_ns,
            )
        if any(name not in os.environ for name in adapter.environment_variable_names):
            return _publish_preflight(
                paths,
                profile_sha=profile_sha,
                adapter=adapter,
                adapter_sha=adapter_sha,
                availability="UNAVAILABLE",
                reason_code="ENVIRONMENT_MISSING",
                result=None,
                started_at=started_at,
                started_ns=started_ns,
            )
        descriptor_path = _descriptor_execution_path(executable.descriptor)
        run_directory = _create_run_directory(paths.state_root)
        try:
            cwd_descriptor_path = _descriptor_directory_path(run_directory.run_descriptor)
            result = run_bounded_process(
                [descriptor_path, *adapter.preflight_argv],
                b"",
                Path(cwd_descriptor_path),
                {name: os.environ[name] for name in adapter.environment_variable_names},
                adapter.preflight_timeout_seconds,
                adapter.max_capture_bytes,
                pass_fds=(executable.descriptor, run_directory.run_descriptor),
            )
        except ProcessLaunchError as error:
            if error.stage != "executable" or error.errno not in _LAUNCH_UNAVAILABLE_ERRNOS:
                raise ContractError(
                    f"cannot launch preflight during {error.stage} setup: {error}"
                ) from error
            result = None
        except OSError as error:
            raise ContractError(f"cannot set up bounded preflight process: {error}") from error
        finally:
            run_directory.close_and_remove()
        if result is None:
            return _publish_preflight(
                paths,
                profile_sha=profile_sha,
                adapter=adapter,
                adapter_sha=adapter_sha,
                availability="UNAVAILABLE",
                reason_code="EXECUTABLE_MISSING",
                result=None,
                started_at=started_at,
                started_ns=started_ns,
            )
    finally:
        executable.close()

    _persist_raw_stream(paths.state_root, "stdout", result.stdout)
    _persist_raw_stream(paths.state_root, "stderr", result.stderr)
    if (
        result.stdout_truncated
        or result.stderr_truncated
        or result.capture_incomplete
    ):
        availability, reason_code = "UNAVAILABLE", "PREFLIGHT_OUTPUT_TRUNCATED"
    elif result.timed_out:
        availability, reason_code = "UNAVAILABLE", "PREFLIGHT_TIMEOUT"
    elif result.returncode != 0:
        availability, reason_code = "UNAVAILABLE", "PREFLIGHT_EXIT_NONZERO"
    else:
        availability, reason_code = "AVAILABLE", "PREFLIGHT_READY"
    return _publish_preflight(
        paths,
        profile_sha=profile_sha,
        adapter=adapter,
        adapter_sha=adapter_sha,
        availability=availability,
        reason_code=reason_code,
        result=result,
        started_at=started_at,
        started_ns=started_ns,
    )


def build_doctor_report(
    paths: BuildPaths, adapter_config_path: Path | None
) -> dict[str, Any]:
    """Preflight committed profiles and return only sanitized availability metadata."""
    schemas = SchemaRegistry(paths.repo_root)
    adapters = (
        load_adapter_configuration(
            adapter_config_path,
            schemas,
            repo_root=paths.repo_root,
        )
        if adapter_config_path is not None
        else ()
    )
    adapters_by_worker = {adapter.worker_id: adapter for adapter in adapters}
    workers: list[dict[str, Any]] = []
    profiles_root = paths.repo_root / "build_control" / "worker_profiles"
    for profile_path in sorted(profiles_root.glob("*.json")):
        profile = load_worker_profile(profile_path, schemas)
        adapter = (
            adapters_by_worker.get(profile.worker_id)
            if "LOCAL_ADAPTER" in profile.transports
            else None
        )
        result = preflight_worker(paths, profile, adapter)
        workers.append(
            {
                "profile_id": profile.profile_id,
                "worker_id": profile.worker_id,
                "roles": list(profile.roles),
                "transports": list(profile.transports),
                "adapter_configured": adapter is not None,
                "availability": result.availability,
                "reason_code": result.reason_code,
                "worker_profile_sha256": result.worker_profile_sha256,
                "adapter_configuration_sha256": result.adapter_configuration_sha256,
                "preflight_receipt_sha256": result.receipt_sha256,
            }
        )
    return {
        "protocol": "helios.build.doctor-report/v1",
        "workers": workers,
    }


def _publish_preflight(
    paths: BuildPaths,
    *,
    profile_sha: str,
    adapter: AdapterConfiguration,
    adapter_sha: str,
    availability: str,
    reason_code: str,
    result: ProcessResult | None,
    started_at: str,
    started_ns: int,
) -> PreflightResult:
    stdout = result.stdout if result is not None else b""
    stderr = result.stderr if result is not None else b""
    returncode = result.returncode if result is not None else None
    timed_out = result.timed_out if result is not None else False
    receipt: dict[str, Any] = {
        "protocol": "helios.build.preflight-receipt/v1",
        "worker_profile_sha256": profile_sha,
        "adapter_configuration_sha256": adapter_sha,
        "executable_sha256": adapter.executable_sha256,
        "host_id_sha256": sha256_hex(adapter.host_id.encode("utf-8")),
        "availability": availability,
        "reason_code": reason_code,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout_sha256": sha256_hex(stdout),
        "stderr_sha256": sha256_hex(stderr),
        "started_at": started_at,
        "duration_ms": max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
    }
    SchemaRegistry(paths.repo_root).validate(receipt, "preflight-receipt-v1.schema.json")
    stored = ContentAddressedStore(paths.repo_root / "build_control" / "graph").put_json(
        "receipts", receipt
    )
    return PreflightResult(
        availability=availability,
        reason_code=reason_code,
        worker_profile_sha256=profile_sha,
        adapter_configuration_sha256=adapter_sha,
        receipt_sha256=stored.sha256,
        receipt_path=stored.path,
        returncode=returncode,
        timed_out=timed_out,
    )


def _open_verified_executable(path: Path) -> _VerifiedExecutable:
    if not path.is_absolute():
        raise _ExecutableUnavailable("configured executable path must be absolute")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if no_follow is None or nonblocking is None:
        raise ContractError(
            "exact executable verification requires no-follow nonblocking opens"
        )
    try:
        metadata = path.lstat()
    except OSError as error:
        if error.errno in {errno.EACCES, errno.ENOENT, errno.ENOTDIR}:
            raise _ExecutableUnavailable(str(error)) from error
        raise ContractError(f"cannot inspect configured executable: {error}") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise _ExecutableUnavailable("configured executable must not be a symlink")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | no_follow
            | nonblocking,
        )
    except OSError as error:
        if error.errno in {errno.EACCES, errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
            raise _ExecutableUnavailable(str(error)) from error
        raise ContractError(f"cannot open configured executable: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not (
            metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        ):
            raise _ExecutableUnavailable("configured executable is not a regular executable")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return _VerifiedExecutable(descriptor=descriptor, sha256=digest.hexdigest())
    except BaseException:
        os.close(descriptor)
        raise


def _descriptor_execution_path(descriptor: int) -> str:
    descriptor_root = Path("/dev/fd")
    if not descriptor_root.is_dir():
        raise ContractError("exact descriptor execution is unsupported: /dev/fd is unavailable")
    descriptor_path = descriptor_root / str(descriptor)
    try:
        metadata = descriptor_path.stat()
    except OSError as error:
        raise ContractError("exact descriptor execution is unsupported") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ContractError("exact descriptor execution did not resolve to a regular file")
    return str(descriptor_path)


def _descriptor_directory_path(descriptor: int) -> str:
    descriptor_root = Path("/dev/fd")
    if not descriptor_root.is_dir():
        raise ContractError("exact descriptor working-directory custody is unsupported")
    descriptor_path = descriptor_root / str(descriptor)
    try:
        descriptor_metadata = os.fstat(descriptor)
        path_metadata = descriptor_path.stat()
    except OSError as error:
        raise ContractError("cannot pin the preflight working directory") from error
    if (
        not stat.S_ISDIR(descriptor_metadata.st_mode)
        or not stat.S_ISDIR(path_metadata.st_mode)
        or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
        != (path_metadata.st_dev, path_metadata.st_ino)
    ):
        raise ContractError("preflight working-directory descriptor is not exact")
    return str(descriptor_path)


def _create_run_directory(state_root: Path) -> _RunDirectory:
    state_descriptor = _open_secure_state_root(state_root)
    try:
        preflight_descriptor = _open_private_child_directory(state_descriptor, "preflight")
    except BaseException:
        os.close(state_descriptor)
        raise
    run_name = f"run-{uuid.uuid4().hex}"
    run_descriptor: int | None = None
    try:
        os.mkdir(run_name, mode=0o700, dir_fd=preflight_descriptor)
        os.fsync(preflight_descriptor)
        run_descriptor = os.open(run_name, _DIRECTORY_FLAGS, dir_fd=preflight_descriptor)
        _verify_private_directory(run_descriptor, "preflight run directory")
    except OSError as error:
        if run_descriptor is not None:
            os.close(run_descriptor)
        os.close(preflight_descriptor)
        os.close(state_descriptor)
        raise ContractError(f"cannot create secure preflight run directory: {error}") from error
    except BaseException:
        if run_descriptor is not None:
            os.close(run_descriptor)
        os.close(preflight_descriptor)
        os.close(state_descriptor)
        raise
    assert run_descriptor is not None
    return _RunDirectory(
        state_descriptor=state_descriptor,
        preflight_descriptor=preflight_descriptor,
        run_descriptor=run_descriptor,
        run_name=run_name,
        path=state_root / "preflight" / run_name,
    )


def _open_secure_state_root(state_root: Path) -> int:
    if _CURRENT_UID is None:
        raise ContractError("secure external state custody requires POSIX ownership")
    if (
        not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NONBLOCK")
    ):
        raise ContractError(
            "secure external state custody requires no-follow nonblocking directory opens"
        )
    if not state_root.is_absolute():
        raise ContractError("external state root must be absolute")
    components = state_root.parts[1:]
    try:
        current = os.open(Path(state_root.anchor), _DIRECTORY_FLAGS)
    except OSError as error:
        raise ContractError(f"cannot open external state filesystem root: {error}") from error
    try:
        for index, component in enumerate(components):
            if component in {"", ".", ".."}:
                raise ContractError("external state root contains an unsafe component")
            created = False
            try:
                next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current)
                    os.fsync(current)
                    created = True
                    next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
                except OSError as error:
                    raise ContractError(
                        f"cannot create secure external state directory: {error}"
                    ) from error
            except OSError as error:
                raise ContractError(
                    f"external state root contains a symlink or non-directory: {component}"
                ) from error
            os.close(current)
            current = next_descriptor
            if created or index == len(components) - 1:
                _verify_private_directory(current, "external state directory")
        return current
    except BaseException:
        os.close(current)
        raise


def _open_private_child_directory(parent_descriptor: int, name: str) -> int:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ContractError("private directory name is unsafe")
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
    except FileNotFoundError:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
        except OSError as error:
            raise ContractError(f"cannot create private external state directory: {error}") from error
    except OSError as error:
        raise ContractError(
            f"external state child is a symlink or non-directory: {name}"
        ) from error
    try:
        _verify_private_directory(descriptor, f"external state directory {name}")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _verify_private_directory(descriptor: int, label: str) -> None:
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise ContractError(f"cannot inspect {label}: {error}") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != _CURRENT_UID
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ContractError(f"{label} must be a current-owner directory with mode 0700")


def _persist_raw_stream(state_root: Path, stream_name: str, content: bytes) -> Path:
    if stream_name not in {"stdout", "stderr"}:
        raise ContractError("raw preflight stream name is invalid")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
        raise ContractError(
            "secure raw artifact custody requires no-follow nonblocking opens"
        )
    digest = sha256_hex(content)
    state_descriptor = _open_secure_state_root(state_root)
    descriptors = [state_descriptor]
    try:
        parent = state_descriptor
        for component in ("preflight", "raw", "sha256", digest[:2]):
            parent = _open_private_child_directory(parent, component)
            descriptors.append(parent)
        filename = f"{digest}.{stream_name}"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(filename, flags, 0o600, dir_fd=parent)
        except FileExistsError:
            _verify_existing_raw_object(parent, filename, digest, len(content))
            return state_root / "preflight" / "raw" / "sha256" / digest[:2] / filename
        except OSError as error:
            raise ContractError(f"cannot create raw preflight object: {error}") from error
        try:
            _verify_private_raw_file(descriptor, "new raw preflight object")
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("raw preflight write made no progress")
                view = view[written:]
            os.fsync(descriptor)
        except OSError as error:
            os.close(descriptor)
            try:
                os.unlink(filename, dir_fd=parent)
            except OSError:
                pass
            raise ContractError(f"cannot persist raw preflight object: {error}") from error
        except BaseException:
            os.close(descriptor)
            try:
                os.unlink(filename, dir_fd=parent)
            except OSError:
                pass
            raise
        else:
            os.close(descriptor)
        try:
            os.fsync(parent)
        except OSError as error:
            raise ContractError(
                f"cannot fsync raw preflight publication directory: {error}"
            ) from error
        return state_root / "preflight" / "raw" / "sha256" / digest[:2] / filename
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _verify_existing_raw_object(
    parent_descriptor: int,
    filename: str,
    expected_digest: str,
    expected_size: int,
) -> None:
    try:
        descriptor = os.open(filename, _FILE_READ_FLAGS, dir_fd=parent_descriptor)
    except OSError as error:
        raise ContractError(
            f"existing raw preflight object is unsafe or unreadable: {error}"
        ) from error
    try:
        metadata = _verify_private_raw_file(descriptor, "existing raw preflight object")
        if metadata.st_size != expected_size:
            raise ContractError("existing raw preflight object size does not match its digest")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        if digest.hexdigest() != expected_digest:
            raise ContractError("existing raw preflight object digest mismatch")
    finally:
        os.close(descriptor)


def _verify_private_raw_file(descriptor: int, label: str) -> os.stat_result:
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise ContractError(f"cannot inspect {label}: {error}") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != _CURRENT_UID
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ContractError(f"{label} must be a current-owner regular file with mode 0600")
    return metadata


def _remove_tree_contents(directory_descriptor: int) -> None:
    for name in os.listdir(directory_descriptor):
        metadata = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISDIR(metadata.st_mode):
            child = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_descriptor)
            try:
                _remove_tree_contents(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=directory_descriptor)
        else:
            os.unlink(name, dir_fd=directory_descriptor)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
