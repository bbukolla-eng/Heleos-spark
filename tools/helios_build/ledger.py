"""Append-only, hash-chained JSONL ledgers guarded by an external lock."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tools.helios_build.canonical import canonical_json_bytes, reject_floats_and_invalid_values, sha256_hex
from tools.helios_build.errors import ContractError


def append_event(path: Path, event: dict[str, Any], lock_root: Path) -> str:
    """Append one derived chain record while holding an exclusive controller lock."""
    if not isinstance(event, dict):
        raise ContractError("event ledger event must be an object")
    lock_directory = lock_root / "locks"
    lock_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = lock_directory / "build-control-ledger.lock"
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ContractError(f"event ledger lock already exists: {lock_path}") from error
    except OSError as error:
        raise ContractError(f"cannot acquire event ledger lock {lock_path}: {error}") from error

    succeeded = False
    try:
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        prior_events = replay_events(path)
        prior_digest = prior_events[-1]["event_sha256"] if prior_events else None
        record = dict(event)
        record.pop("sequence", None)
        record.pop("previous_event_sha256", None)
        record.pop("event_sha256", None)
        record["sequence"] = len(prior_events) + 1
        record["previous_event_sha256"] = prior_digest
        record["event_sha256"] = sha256_hex(canonical_json_bytes(record))
        line = canonical_json_bytes(record) + b"\n"
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        try:
            with path.open("ab") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as error:
            raise ContractError(f"cannot append event ledger {path}: {error}") from error
        succeeded = True
        return record["event_sha256"]
    finally:
        if succeeded:
            try:
                lock_path.unlink()
                _fsync_directory(lock_directory)
            except OSError as error:
                raise ContractError(f"cannot release event ledger lock {lock_path}: {error}") from error


def replay_events(path: Path) -> tuple[dict[str, Any], ...]:
    """Read and validate every record in an append-only event ledger."""
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise ContractError(f"cannot read event ledger {path}: {error}") from error
    if not content:
        return ()
    if not content.endswith(b"\n"):
        raise ContractError("event ledger is missing its final newline")

    records: list[dict[str, Any]] = []
    previous_digest: str | None = None
    for sequence, line in enumerate(content[:-1].split(b"\n"), start=1):
        if not line:
            raise ContractError("event ledger contains a blank interior record")
        record = _parse_record(line, path)
        if record.get("sequence") != sequence or isinstance(record.get("sequence"), bool):
            raise ContractError("event ledger has a sequence gap")
        if record.get("previous_event_sha256") != previous_digest:
            raise ContractError("event ledger has a prior-hash mismatch")
        supplied_digest = record.get("event_sha256")
        if not _is_digest(supplied_digest):
            raise ContractError("event ledger has an invalid event hash")
        body = dict(record)
        del body["event_sha256"]
        expected_digest = sha256_hex(canonical_json_bytes(body))
        if supplied_digest != expected_digest:
            raise ContractError("event ledger has a body-hash mismatch")
        records.append(record)
        previous_digest = supplied_digest
    return tuple(records)


def _parse_record(line: bytes, path: Path) -> dict[str, Any]:
    try:
        source = line.decode("utf-8", errors="strict")
        value = json.loads(
            source,
            object_pairs_hook=_unique_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ContractError) as error:
        raise ContractError(f"event ledger contains malformed JSON in {path}") from error
    if not isinstance(value, dict):
        raise ContractError("event ledger record must be an object")
    try:
        reject_floats_and_invalid_values(value)
    except ContractError as error:
        raise ContractError("event ledger contains invalid JSON values") from error
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContractError("duplicate object key")
        value[key] = item
    return value


def _reject_float(_: str) -> None:
    raise ContractError("floating-point values are forbidden")


def _reject_constant(_: str) -> None:
    raise ContractError("invalid JSON constant")


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
