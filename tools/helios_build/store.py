"""Immutable, content-addressed files for repository build-control state."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.helios_build.canonical import canonical_json_bytes, load_strict_json, sha256_hex
from tools.helios_build.errors import ContractError
from tools.helios_build.types import JsonValue


@dataclass(frozen=True, slots=True)
class StoredObject:
    """One immutable object published into a content-addressed collection."""

    sha256: str
    path: Path
    replayed: bool


class ContentAddressedStore:
    """Publish canonical JSON and opaque bytes without overwriting an object."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def put_json(self, collection: str, payload: JsonValue) -> StoredObject:
        """Canonicalize and immutably publish one JSON object."""
        return self.put_bytes(collection, canonical_json_bytes(payload), ".json")

    def put_bytes(self, collection: str, content: bytes, suffix: str) -> StoredObject:
        """Publish bytes using an exclusive hard-link operation."""
        self._validate_collection(collection)
        self._validate_suffix(suffix)
        digest = sha256_hex(content)
        destination = self.root / collection / "sha256" / digest[:2] / f"{digest}{suffix}"
        destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{digest}.", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                existing = self._read_verified_object(destination)
                if existing != content:
                    raise ContractError(
                        f"content-address collision at {destination} for digest {digest}"
                    )
                return StoredObject(sha256=digest, path=destination, replayed=True)
            os.chmod(destination, 0o644)
            self._fsync_published_object(destination)
            self._fsync_directory(destination.parent)
            return StoredObject(sha256=digest, path=destination, replayed=False)
        except OSError as error:
            raise ContractError(f"cannot publish content-addressed object {destination}: {error}") from error
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError as error:
                raise ContractError(f"cannot remove temporary CAS object {temporary}: {error}") from error

    def get_json(self, digest: str) -> dict[str, Any]:
        """Load a canonical JSON object by digest from any local collection."""
        self._validate_digest(digest)
        candidates = sorted(
            self.root.glob(f"*/sha256/{digest[:2]}/{digest}.json"), key=lambda item: str(item)
        )
        if not candidates:
            raise ContractError(f"content-addressed JSON object not found: {digest}")
        for path in candidates:
            try:
                content = path.read_bytes()
            except OSError as error:
                raise ContractError(f"cannot read content-addressed JSON object {path}: {error}") from error
            if sha256_hex(content) != digest:
                raise ContractError(f"content-addressed JSON digest mismatch at {path}")
            return load_strict_json(path)
        raise AssertionError("unreachable")

    @staticmethod
    def _validate_collection(collection: str) -> None:
        if not collection or collection in {".", ".."} or any(
            character in collection for character in "/\\\x00"
        ):
            raise ContractError("CAS collection must be a single non-empty directory name")

    @staticmethod
    def _validate_suffix(suffix: str) -> None:
        if not suffix.startswith(".") or suffix == "." or any(
            character in suffix for character in "/\\\x00"
        ):
            raise ContractError("CAS suffix must be a safe filename suffix")

    @staticmethod
    def _validate_digest(digest: str) -> None:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ContractError("CAS digest must be lowercase SHA-256 hex")

    @staticmethod
    def _read_verified_object(path: Path) -> bytes:
        descriptor = ContentAddressedStore._open_verified_object(path)
        try:
            with os.fdopen(descriptor, "rb") as handle:
                return handle.read()
        except OSError as error:
            raise ContractError(f"cannot read content-addressed object {path}: {error}") from error

    @staticmethod
    def _fsync_published_object(path: Path) -> None:
        descriptor = ContentAddressedStore._open_verified_object(path)
        try:
            os.fsync(descriptor)
        except OSError as error:
            raise ContractError(f"cannot fsync content-addressed object {path}: {error}") from error
        finally:
            os.close(descriptor)

    @staticmethod
    def _open_verified_object(path: Path) -> int:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise ContractError("cannot safely open CAS object without O_NOFOLLOW support")
        try:
            descriptor = os.open(path, os.O_RDONLY | no_follow)
        except OSError as error:
            raise ContractError(f"cannot safely open content-addressed object {path}: {error}") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ContractError(f"content-addressed object is not a regular file: {path}")
            if stat.S_IMODE(metadata.st_mode) != 0o644:
                raise ContractError(f"content-addressed object has unexpected mode: {path}")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        try:
            descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError as error:
            raise ContractError(f"cannot open CAS directory for fsync {directory}: {error}") from error
        try:
            os.fsync(descriptor)
        except OSError as error:
            raise ContractError(f"cannot fsync CAS directory {directory}: {error}") from error
        finally:
            os.close(descriptor)
