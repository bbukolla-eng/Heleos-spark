"""Strict JSON parsing, canonical serialization, and hashing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, NoReturn

from tools.helios_build.errors import ContractError
from tools.helios_build.types import JsonValue


def _reject_float(_: str) -> NoReturn:
    raise ContractError("floating-point values are forbidden in Build Fabric JSON")


def _reject_constant(value: str) -> NoReturn:
    raise ContractError(f"invalid JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContractError(f"duplicate object key: {key}")
        value[key] = item
    return value


def load_strict_json(path: Path) -> dict[str, Any]:
    """Load one UTF-8 JSON object while rejecting ambiguous JSON constructs."""
    try:
        source = path.read_bytes().decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as error:
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


def reject_floats_and_invalid_values(value: object) -> None:
    """Reject values that cannot be represented by the frozen JSON subset."""
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        raise ContractError("floating-point values are forbidden in Build Fabric JSON")
    if isinstance(value, list):
        for item in value:
            reject_floats_and_invalid_values(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError("JSON object keys must be strings")
            reject_floats_and_invalid_values(item)
        return
    raise ContractError(f"invalid JSON value type: {type(value).__name__}")


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Serialize a frozen JSON value deterministically as UTF-8 bytes."""
    reject_floats_and_invalid_values(value)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ContractError(f"cannot canonicalize JSON value: {error}") from error


def sha256_hex(data: bytes) -> str:
    """Return the lowercase SHA-256 digest for immutable bytes."""
    return hashlib.sha256(data).hexdigest()
