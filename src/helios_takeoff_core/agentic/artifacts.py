"""Local content-addressed storage for immutable P1A artifact bytes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ValidationError


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SECURE_STORAGE_UNAVAILABLE = "secure P1A artifact storage is unavailable on this platform"
_REQUIRED_FLAG_NAMES = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
_REQUIRED_DIR_FD_OPERATIONS = (os.open, os.mkdir, os.link, os.unlink)
_secure_flag_values = tuple(getattr(os, name, None) for name in _REQUIRED_FLAG_NAMES)
_SECURE_STORAGE_AVAILABLE = all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in _secure_flag_values) and all(
    operation in getattr(os, "supports_dir_fd", frozenset()) for operation in _REQUIRED_DIR_FD_OPERATIONS
)
_READ_ONLY_FLAG = getattr(os, "O_RDONLY", 0)
_DIRECTORY_FLAGS = _READ_ONLY_FLAG | sum(value for value in _secure_flag_values if isinstance(value, int))
_FILE_FLAGS = _READ_ONLY_FLAG | sum(
    value for value in (getattr(os, "O_CLOEXEC", None), getattr(os, "O_NOFOLLOW", None)) if isinstance(value, int)
)


@dataclass(frozen=True)
class ArtifactRecord:
    """JSON-compatible metadata required to retrieve and verify one artifact."""

    sha256: str
    byte_size: int
    media_type: str
    schema_version: str
    store_key: str


class ArtifactStore:
    """Store bytes beneath one caller-owned root by their SHA-256 digest."""

    def __init__(self, root: Path) -> None:
        if not _SECURE_STORAGE_AVAILABLE:
            raise ValidationError(SECURE_STORAGE_UNAVAILABLE)
        root.mkdir(parents=True, exist_ok=True)
        try:
            self._root_fd = os.open(root, _DIRECTORY_FLAGS)
        except OSError as error:
            raise ValidationError("artifact store root must be a real directory") from error

    def __del__(self) -> None:
        root_fd = getattr(self, "_root_fd", None)
        if root_fd is not None:
            try:
                os.close(root_fd)
            except OSError:
                pass

    def put_json(self, payload: dict[str, Any], *, media_type: str, schema_version: str) -> ArtifactRecord:
        """Serialize an object canonically and store its UTF-8 bytes."""
        if not isinstance(payload, dict):
            raise ValidationError("artifact JSON payload must be an object")
        try:
            content = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValidationError("artifact JSON payload must be JSON-serializable") from error
        return self._put_bytes(content, media_type=media_type, schema_version=schema_version)

    def put_file(self, source: Path, *, media_type: str, schema_version: str) -> ArtifactRecord:
        """Copy source bytes into the store without changing the source file."""
        try:
            content = source.read_bytes()
        except OSError as error:
            raise ValidationError("artifact source file cannot be read") from error
        return self._put_bytes(content, media_type=media_type, schema_version=schema_version)

    def put_bytes(self, content: bytes, *, media_type: str, schema_version: str) -> ArtifactRecord:
        """Store exact process bytes without text decoding or normalization."""
        if not isinstance(content, bytes):
            raise ValidationError("artifact content must be bytes")
        return self._put_bytes(content, media_type=media_type, schema_version=schema_version)

    def read_bytes(self, record: ArtifactRecord) -> bytes:
        """Return stored bytes only when their complete content identity verifies."""
        if not self._record_is_valid(record):
            raise ValidationError("artifact record is invalid")
        try:
            directory_fd = self._artifact_directory(record.sha256, create=False)
            try:
                artifact_fd = os.open(record.sha256, _FILE_FLAGS, dir_fd=directory_fd)
                try:
                    if not stat.S_ISREG(os.fstat(artifact_fd).st_mode):
                        raise ValidationError("artifact path is not a regular file")
                    chunks: list[bytes] = []
                    digest = hashlib.sha256()
                    byte_size = 0
                    while chunk := os.read(artifact_fd, 1024 * 1024):
                        chunks.append(chunk)
                        digest.update(chunk)
                        byte_size += len(chunk)
                finally:
                    os.close(artifact_fd)
            finally:
                os.close(directory_fd)
        except ValidationError:
            raise
        except OSError as error:
            raise ValidationError("artifact bytes cannot be read securely") from error
        if byte_size != record.byte_size or digest.hexdigest() != record.sha256:
            raise ValidationError("artifact content identity verification failed")
        return b"".join(chunks)

    def verify(self, record: ArtifactRecord) -> bool:
        """Return whether record still names unmodified bytes within this store."""
        if not self._record_is_valid(record):
            return False
        try:
            directory_fd = self._artifact_directory(record.sha256, create=False)
            try:
                matches = self._existing_matches(directory_fd, record)
            finally:
                os.close(directory_fd)
        except (OSError, ValueError):
            return False
        return matches is True

    def _put_bytes(self, content: bytes, *, media_type: str, schema_version: str) -> ArtifactRecord:
        self._validate_metadata(media_type=media_type, schema_version=schema_version)
        digest = hashlib.sha256(content).hexdigest()
        record = ArtifactRecord(
            sha256=digest,
            byte_size=len(content),
            media_type=media_type,
            schema_version=schema_version,
            store_key=self._store_key(digest),
        )
        try:
            directory_fd = self._artifact_directory(digest, create=True)
            try:
                existing_matches = self._existing_matches(directory_fd, record)
                if existing_matches is True:
                    return record
                if existing_matches is False:
                    raise ValidationError("content-addressed artifact path contains unexpected bytes")
                temporary_name = self._write_temporary(directory_fd, content)
                try:
                    published = False
                    try:
                        os.link(
                            temporary_name,
                            digest,
                            src_dir_fd=directory_fd,
                            dst_dir_fd=directory_fd,
                            follow_symlinks=False,
                        )
                        published = True
                    except FileExistsError:
                        if self._existing_matches(directory_fd, record) is not True:
                            raise ValidationError("content-addressed artifact path contains unexpected bytes") from None
                    if published:
                        os.fsync(directory_fd)
                    return record
                finally:
                    try:
                        os.unlink(temporary_name, dir_fd=directory_fd)
                    except FileNotFoundError:
                        pass
            finally:
                os.close(directory_fd)
        except ValidationError:
            raise
        except OSError as error:
            raise ValidationError("artifact store rejected an unsafe filesystem path") from error

    def _artifact_directory(self, digest: str, *, create: bool) -> int:
        sha256_fd = self._open_directory(self._root_fd, "sha256", create=create)
        try:
            return self._open_directory(sha256_fd, digest[:2], create=create)
        finally:
            os.close(sha256_fd)

    @staticmethod
    def _open_directory(parent_fd: int, name: str, *, create: bool) -> int:
        try:
            return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileExistsError:
                pass
            return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)

    @staticmethod
    def _existing_matches(directory_fd: int, record: ArtifactRecord) -> bool | None:
        try:
            artifact_fd = os.open(record.sha256, _FILE_FLAGS, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(artifact_fd).st_mode):
                raise ValueError("artifact path is not a regular file")
            digest = hashlib.sha256()
            byte_size = 0
            while chunk := os.read(artifact_fd, 1024 * 1024):
                digest.update(chunk)
                byte_size += len(chunk)
            return byte_size == record.byte_size and digest.hexdigest() == record.sha256
        finally:
            os.close(artifact_fd)

    @staticmethod
    def _write_temporary(directory_fd: int, content: bytes) -> str:
        for _ in range(100):
            temporary_name = f".tmp-{secrets.token_hex(16)}"
            try:
                temporary_fd = os.open(
                    temporary_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                continue
            try:
                view = memoryview(content)
                while view:
                    written = os.write(temporary_fd, view)
                    view = view[written:]
                os.fsync(temporary_fd)
            except BaseException:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
                raise
            finally:
                os.close(temporary_fd)
            return temporary_name
        raise OSError("could not allocate an artifact temporary file")

    @staticmethod
    def _record_is_valid(record: ArtifactRecord) -> bool:
        return (
            isinstance(record, ArtifactRecord)
            and isinstance(record.sha256, str)
            and SHA256_PATTERN.fullmatch(record.sha256) is not None
            and isinstance(record.byte_size, int)
            and not isinstance(record.byte_size, bool)
            and record.byte_size >= 0
            and record.store_key == ArtifactStore._store_key(record.sha256)
        )

    @staticmethod
    def _store_key(digest: str) -> str:
        return f"sha256/{digest[:2]}/{digest}"

    @staticmethod
    def _validate_metadata(*, media_type: str, schema_version: str) -> None:
        if not isinstance(media_type, str) or not media_type.strip():
            raise ValidationError("artifact media_type is required")
        if not isinstance(schema_version, str) or not schema_version.strip():
            raise ValidationError("artifact schema_version is required")
