"""Owner-private content-addressed custody for non-Git Build Fabric bytes."""

from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass

from tools.helios_build.canonical import sha256_hex
from tools.helios_build.doctor import _open_private_child_directory, _open_secure_state_root
from tools.helios_build.errors import ContractError
from tools.helios_build.paths import BuildPaths


_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


@dataclass(frozen=True, slots=True)
class PrivateStoredObject:
    sha256: str
    byte_size: int
    store_key: str
    replayed: bool


def put_private_bytes(
    paths: BuildPaths, namespace: str, content: bytes
) -> PrivateStoredObject:
    """Exclusively publish bytes beneath an owner-only descriptor walk."""
    _validate_namespace(namespace)
    if not isinstance(content, bytes):
        raise ContractError("private CAS content must be bytes")
    digest = sha256_hex(content)
    key = _store_key(namespace, digest)
    state, leaf = _open_leaf(paths, namespace, digest)
    temporary = f".tmp-{uuid.uuid4().hex}"
    descriptor: int | None = None
    replayed = False
    try:
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=leaf,
            )
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("private CAS write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            try:
                os.link(
                    temporary,
                    f"{digest}.bin",
                    src_dir_fd=leaf,
                    dst_dir_fd=leaf,
                    follow_symlinks=False,
                )
                os.fsync(leaf)
            except FileExistsError:
                replayed = True
        except OSError as error:
            raise ContractError(f"cannot publish private CAS object: {error}") from error
        existing = _read_object_at(leaf, digest, len(content))
        if existing != content:
            raise ContractError("private CAS replay bytes do not match their digest")
        return PrivateStoredObject(digest, len(content), key, replayed)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=leaf)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ContractError(f"cannot remove private CAS temporary file: {error}") from error
        os.close(leaf)
        os.close(state)


def read_private_bytes(
    paths: BuildPaths,
    store_key: str,
    expected_sha256: str,
    expected_size: int,
    *,
    max_bytes: int | None = None,
) -> bytes:
    """Read and reverify one exact private object without following links."""
    namespace = _validate_store_key(store_key, expected_sha256)
    if not isinstance(expected_size, int) or expected_size < 0:
        raise ContractError("private CAS expected size is invalid")
    if max_bytes is not None and (
        not isinstance(max_bytes, int) or max_bytes < 0 or expected_size > max_bytes
    ):
        raise ContractError("private CAS object exceeds its finite size bound")
    state, leaf = _open_leaf(paths, namespace, expected_sha256)
    try:
        content = _read_object_at(leaf, expected_sha256, expected_size)
    finally:
        os.close(leaf)
        os.close(state)
    if sha256_hex(content) != expected_sha256:
        raise ContractError("private CAS object digest mismatch")
    return content


def _open_leaf(paths: BuildPaths, namespace: str, digest: str) -> tuple[int, int]:
    _validate_digest(digest)
    state = _open_secure_state_root(paths.state_root)
    descriptors: list[int] = []
    try:
        current = _open_private_child_directory(state, "private-cas")
        descriptors.append(current)
        for component in (namespace, "sha256", digest[:2]):
            current = _open_private_child_directory(current, component)
            descriptors.append(current)
        leaf = descriptors.pop()
        return state, leaf
    except BaseException:
        os.close(state)
        raise
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_object_at(parent: int, digest: str, expected_size: int) -> bytes:
    try:
        descriptor = os.open(f"{digest}.bin", _FILE_FLAGS, dir_fd=parent)
    except OSError as error:
        raise ContractError(f"cannot open private CAS object: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        current_uid = os.getuid() if hasattr(os, "getuid") else None
        if (
            current_uid is None
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != current_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size != expected_size
        ):
            raise ContractError("private CAS object lacks exact size/mode/owner custody")
        remaining = expected_size + 1
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) != expected_size:
            raise ContractError("private CAS object changed while being read")
        return content
    finally:
        os.close(descriptor)


def _validate_namespace(namespace: str) -> None:
    if (
        not isinstance(namespace, str)
        or not namespace
        or namespace in {".", ".."}
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in namespace)
    ):
        raise ContractError("private CAS namespace is unsafe")


def _validate_store_key(store_key: str, digest: str) -> str:
    _validate_digest(digest)
    if not isinstance(store_key, str):
        raise ContractError("private CAS store key is invalid")
    parts = store_key.split("/")
    if len(parts) != 4:
        raise ContractError("private CAS store key is not canonical")
    namespace, marker, prefix, filename = parts
    _validate_namespace(namespace)
    if marker != "sha256" or prefix != digest[:2] or filename != f"{digest}.bin":
        raise ContractError("private CAS store key does not match its digest")
    return namespace


def _store_key(namespace: str, digest: str) -> str:
    return f"{namespace}/sha256/{digest[:2]}/{digest}.bin"


def _validate_digest(value: object) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ContractError("private CAS digest must be lowercase SHA-256 hex")
