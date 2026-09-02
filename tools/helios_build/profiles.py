"""Command-free worker policy and strict host-local adapter configuration."""

from __future__ import annotations

import errno
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from tools.helios_build.canonical import canonical_json_bytes, load_strict_json, sha256_hex
from tools.helios_build.errors import ContractError
from tools.helios_build.schemas import SchemaRegistry


_TOKEN_PATTERN = re.compile(r"\{[^{}]*\}")
_DISPATCH_TOKENS = frozenset(
    {
        "{task_manifest}",
        "{handoff_path}",
        "{checkpoint_dir}",
        "{worktree}",
        "{attempt_manifest_sha256}",
        "{task_manifest_sha256}",
    }
)


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """One explicit, revision-bound skill or plugin policy grant."""

    name: str
    revision: str
    allowed_tools: tuple[str, ...]
    purpose: str


@dataclass(frozen=True, slots=True)
class WorkerProfile:
    """A repository policy profile with no provider command or host path."""

    profile_id: str
    worker_id: str
    display_name: str
    roles: tuple[str, ...]
    transports: tuple[str, ...]
    adapter_protocol: str | None
    skills: tuple[CapabilityGrant, ...]
    plugins: tuple[CapabilityGrant, ...]
    allowed_tools: tuple[str, ...]
    allowed_data_classes: tuple[str, ...]
    allowed_task_purposes: tuple[str, ...]
    authority_limits: tuple[str, ...]
    max_concurrency: int


@dataclass(frozen=True, slots=True)
class AdapterConfiguration:
    """One normalized, non-secret adapter entry owned by the current host."""

    host_id: str
    worker_id: str
    adapter_name: str
    adapter_revision: str
    executable: Path
    executable_sha256: str
    preflight_argv: tuple[str, ...]
    dispatch_argv: tuple[str, ...]
    environment_variable_names: tuple[str, ...]
    preflight_timeout_seconds: int
    attempt_timeout_seconds: int
    max_capture_bytes: int
    result_protocol: str


def load_worker_profile(path: Path, schemas: SchemaRegistry) -> WorkerProfile:
    """Load and normalize one committed command-free worker policy profile."""
    if not isinstance(path, Path) or not isinstance(schemas, SchemaRegistry):
        raise ContractError("worker profile loading requires a Path and SchemaRegistry")
    payload = load_strict_json(path)
    return _worker_profile_from_payload(payload, schemas)


def load_worker_profile_bytes(content: bytes, schemas: SchemaRegistry) -> WorkerProfile:
    """Parse a worker profile from already-captured immutable bytes."""
    if not isinstance(content, bytes) or not isinstance(schemas, SchemaRegistry):
        raise ContractError("worker profile byte loading requires bytes and SchemaRegistry")
    try:
        source = content.decode("utf-8", errors="strict")
        payload = json.loads(
            source,
            object_pairs_hook=_unique_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("captured worker profile is not strict UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ContractError("captured worker profile root must be an object")
    return _worker_profile_from_payload(payload, schemas)


def _worker_profile_from_payload(
    payload: dict[str, Any], schemas: SchemaRegistry
) -> WorkerProfile:
    schemas.validate(payload, "worker-profile-v1.schema.json")
    adapter_protocol = payload["adapter_protocol"]
    transports = tuple(payload["transports"])
    if "LOCAL_ADAPTER" in transports and adapter_protocol is None:
        raise ContractError("LOCAL_ADAPTER profiles must declare an adapter_protocol")
    if "LOCAL_ADAPTER" not in transports and adapter_protocol is not None:
        raise ContractError("profiles without LOCAL_ADAPTER must not declare an adapter_protocol")
    return WorkerProfile(
        profile_id=payload["profile_id"],
        worker_id=payload["worker_id"],
        display_name=payload["display_name"],
        roles=tuple(payload["roles"]),
        transports=transports,
        adapter_protocol=adapter_protocol,
        skills=_grants(payload["skills"]),
        plugins=_grants(payload["plugins"]),
        allowed_tools=tuple(payload["allowed_tools"]),
        allowed_data_classes=tuple(payload["allowed_data_classes"]),
        allowed_task_purposes=tuple(payload["allowed_task_purposes"]),
        authority_limits=tuple(payload["authority_limits"]),
        max_concurrency=payload["max_concurrency"],
    )


def load_adapter_configuration(
    path: Path,
    schemas: SchemaRegistry,
    *,
    repo_root: Path | None = None,
) -> tuple[AdapterConfiguration, ...]:
    """Load strict external adapter state without reading credential values."""
    if not isinstance(path, Path) or not isinstance(schemas, SchemaRegistry):
        raise ContractError("adapter configuration loading requires a Path and SchemaRegistry")
    repository = Path(os.path.abspath(repo_root or schemas.repo_root))
    absolute_path = Path(os.path.abspath(path))
    try:
        absolute_path.relative_to(repository)
    except ValueError:
        pass
    else:
        raise ContractError("adapter configuration must be outside the repository")
    payload = _load_strict_json_no_follow(absolute_path)
    schemas.validate(payload, "adapter-config-v1.schema.json")
    host_id = payload["host_id"]
    adapters: list[AdapterConfiguration] = []
    worker_ids: set[str] = set()
    for entry in payload["adapters"]:
        worker_id = entry["worker_id"]
        if worker_id in worker_ids:
            raise ContractError(f"adapter configuration repeats worker_id: {worker_id}")
        worker_ids.add(worker_id)
        preflight_argv = tuple(entry["preflight_argv"])
        dispatch_argv = tuple(entry["dispatch_argv"])
        _validate_preflight_tokens(preflight_argv)
        _validate_dispatch_tokens(dispatch_argv)
        adapters.append(
            AdapterConfiguration(
                host_id=host_id,
                worker_id=worker_id,
                adapter_name=entry["adapter_name"],
                adapter_revision=entry["adapter_revision"],
                executable=Path(entry["executable"]),
                executable_sha256=entry["executable_sha256"],
                preflight_argv=preflight_argv,
                dispatch_argv=dispatch_argv,
                environment_variable_names=tuple(sorted(entry["environment_variable_names"])),
                preflight_timeout_seconds=entry["preflight_timeout_seconds"],
                attempt_timeout_seconds=entry["attempt_timeout_seconds"],
                max_capture_bytes=entry["max_capture_bytes"],
                result_protocol=entry["result_protocol"],
            )
        )
    return tuple(sorted(adapters, key=lambda adapter: adapter.worker_id))


def adapter_configuration_sha256(adapter: AdapterConfiguration) -> str:
    """Hash exact normalized non-secret adapter fields and environment names."""
    if not isinstance(adapter, AdapterConfiguration):
        raise ContractError("adapter_configuration_sha256 requires AdapterConfiguration")
    normalized: dict[str, Any] = {
        "protocol": "helios.build.adapter-configuration-hash/v1",
        "host_id": adapter.host_id,
        "worker_id": adapter.worker_id,
        "adapter_name": adapter.adapter_name,
        "adapter_revision": adapter.adapter_revision,
        "executable": str(adapter.executable),
        "executable_sha256": adapter.executable_sha256,
        "preflight_argv": list(adapter.preflight_argv),
        "dispatch_argv": list(adapter.dispatch_argv),
        "environment_variable_names": sorted(adapter.environment_variable_names),
        "preflight_timeout_seconds": adapter.preflight_timeout_seconds,
        "attempt_timeout_seconds": adapter.attempt_timeout_seconds,
        "max_capture_bytes": adapter.max_capture_bytes,
        "result_protocol": adapter.result_protocol,
    }
    return sha256_hex(canonical_json_bytes(normalized))


def worker_profile_sha256(profile: WorkerProfile) -> str:
    """Hash the exact normalized command-free policy profile."""
    normalized: dict[str, Any] = {
        "protocol": "helios.build.worker-profile/v1",
        "profile_id": profile.profile_id,
        "worker_id": profile.worker_id,
        "display_name": profile.display_name,
        "roles": list(profile.roles),
        "transports": list(profile.transports),
        "adapter_protocol": profile.adapter_protocol,
        "skills": [_grant_payload(grant) for grant in profile.skills],
        "plugins": [_grant_payload(grant) for grant in profile.plugins],
        "allowed_tools": list(profile.allowed_tools),
        "allowed_data_classes": list(profile.allowed_data_classes),
        "allowed_task_purposes": list(profile.allowed_task_purposes),
        "authority_limits": list(profile.authority_limits),
        "max_concurrency": profile.max_concurrency,
    }
    return sha256_hex(canonical_json_bytes(normalized))


def _grants(entries: list[dict[str, Any]]) -> tuple[CapabilityGrant, ...]:
    return tuple(
        CapabilityGrant(
            name=entry["name"],
            revision=entry["revision"],
            allowed_tools=tuple(entry["allowed_tools"]),
            purpose=entry["purpose"],
        )
        for entry in entries
    )


def _grant_payload(grant: CapabilityGrant) -> dict[str, Any]:
    return {
        "name": grant.name,
        "revision": grant.revision,
        "allowed_tools": list(grant.allowed_tools),
        "purpose": grant.purpose,
    }


def _validate_preflight_tokens(argv: tuple[str, ...]) -> None:
    if any("{" in argument or "}" in argument or _TOKEN_PATTERN.search(argument) for argument in argv):
        raise ContractError("preflight_argv must not contain tokens")


def _validate_dispatch_tokens(argv: tuple[str, ...]) -> None:
    for argument in argv:
        if "{" not in argument and "}" not in argument:
            continue
        if argument not in _DISPATCH_TOKENS:
            raise ContractError(f"dispatch_argv token is not permitted: {argument}")


def _load_strict_json_no_follow(path: Path) -> dict[str, Any]:
    if not path.is_absolute():
        raise ContractError("adapter configuration path must be absolute")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if no_follow is None or directory_flag is None or nonblocking is None:
        raise ContractError(
            "adapter configuration custody requires no-follow nonblocking directory opens"
        )
    components = path.parts[1:]
    if not components or any(component in {"", ".", ".."} for component in components):
        raise ContractError("adapter configuration path contains an unsafe component")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | directory_flag
        | no_follow
        | nonblocking
    )
    try:
        parent_descriptor = os.open(Path(path.anchor), directory_flags)
    except OSError as error:
        raise ContractError(f"cannot open adapter configuration filesystem root: {error}") from error
    try:
        for component in components[:-1]:
            try:
                next_descriptor = os.open(
                    component,
                    directory_flags,
                    dir_fd=parent_descriptor,
                )
            except OSError as error:
                raise ContractError(
                    "adapter configuration path contains a symlink or non-directory "
                    f"component: {component}"
                ) from error
            os.close(parent_descriptor)
            parent_descriptor = next_descriptor
        try:
            descriptor = os.open(
                components[-1],
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | no_follow
                | nonblocking,
                dir_fd=parent_descriptor,
            )
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise ContractError("adapter configuration must not be a symlink") from error
            raise ContractError(f"cannot open adapter configuration {path}: {error}") from error
        try:
            metadata = os.fstat(descriptor)
            current_uid = os.getuid() if hasattr(os, "getuid") else None
            if current_uid is None:
                raise ContractError("adapter configuration custody requires POSIX ownership")
            if not stat.S_ISREG(metadata.st_mode):
                raise ContractError("adapter configuration must be a regular file")
            if metadata.st_uid != current_uid:
                raise ContractError("adapter configuration must be owned by the current user")
            source_bytes = _read_config_bytes(descriptor, path)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent_descriptor)
    try:
        source = source_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ContractError(f"cannot read UTF-8 JSON from {path}: {error}") from error
    try:
        value = json.loads(
            source,
            object_pairs_hook=_unique_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except ContractError:
        raise
    except json.JSONDecodeError as error:
        raise ContractError(f"invalid JSON in {path}: {error.msg}") from error
    if not isinstance(value, dict):
        raise ContractError(f"JSON root in {path} must be an object")
    return value


def _read_config_bytes(descriptor: int, path: Path) -> bytes:
    chunks: list[bytes] = []
    try:
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
    except OSError as error:
        raise ContractError(f"cannot read adapter configuration {path}: {error}") from error
    return b"".join(chunks)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContractError(f"duplicate object key: {key}")
        value[key] = item
    return value


def _reject_float(_: str) -> NoReturn:
    raise ContractError("floating-point values are forbidden in Build Fabric JSON")


def _reject_constant(value: str) -> NoReturn:
    raise ContractError(f"invalid JSON constant: {value}")
