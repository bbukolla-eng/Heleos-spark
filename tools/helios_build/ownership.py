"""Conservative ownership claims for concurrent Build Fabric tasks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import Iterable, Mapping

from tools.helios_build.errors import CollisionError, ContractError


_OWNERSHIP_FIELDS = (
    "files",
    "path_globs",
    "modules",
    "migrations",
    "schemas",
    "public_interfaces",
    "forbidden_paths",
)


@dataclass(frozen=True, slots=True)
class OwnershipClaim:
    """The immutable resources a task may write or must not touch."""

    task_id: str
    files: tuple[str, ...]
    path_globs: tuple[str, ...]
    modules: tuple[str, ...]
    migrations: tuple[str, ...]
    schemas: tuple[str, ...]
    public_interfaces: tuple[str, ...]
    forbidden_paths: tuple[str, ...]

    @classmethod
    def from_manifest(cls, payload: Mapping[str, object]) -> "OwnershipClaim":
        """Build a claim from a complete task manifest or ownership payload."""
        ownership_value = payload.get("ownership", payload)
        if not isinstance(ownership_value, Mapping):
            raise ContractError("task ownership must be an object")
        missing = [field for field in _OWNERSHIP_FIELDS if field not in ownership_value]
        if missing:
            raise ContractError(f"task ownership is missing fields: {', '.join(missing)}")
        task_id = payload.get("task_id", "anonymous-task")
        if not isinstance(task_id, str) or not task_id:
            raise ContractError("task ownership requires a non-empty task_id")
        values: dict[str, tuple[str, ...]] = {}
        for field in _OWNERSHIP_FIELDS:
            raw = ownership_value[field]
            if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
                raise ContractError(f"task ownership {field} must be an array of non-empty strings")
            if len(set(raw)) != len(raw):
                raise ContractError(f"task ownership {field} must not contain duplicates")
            values[field] = tuple(raw)
        return cls(task_id=task_id, **values)


@dataclass(frozen=True, slots=True)
class Collision:
    """One overlapping claimed resource between a candidate and active writer."""

    kind: str
    candidate_task_id: str
    active_task_id: str
    candidate_resource: str
    active_resource: str


def find_collisions(candidate: OwnershipClaim, active: Iterable[OwnershipClaim]) -> tuple[Collision, ...]:
    """Return every conservative collision between a candidate and active writers."""
    collisions: list[Collision] = []
    for existing in active:
        collisions.extend(_path_collisions(candidate, existing))
        collisions.extend(
            _exact_collisions(candidate, existing, "MODULE", candidate.modules, existing.modules, _modules_overlap)
        )
        collisions.extend(
            _exact_collisions(candidate, existing, "MIGRATION", candidate.migrations, existing.migrations)
        )
        collisions.extend(_exact_collisions(candidate, existing, "SCHEMA", candidate.schemas, existing.schemas))
        collisions.extend(
            _exact_collisions(
                candidate,
                existing,
                "PUBLIC_INTERFACE",
                candidate.public_interfaces,
                existing.public_interfaces,
            )
        )
    return tuple(collisions)


def assert_owned_changes(task: OwnershipClaim | Mapping[str, object], changed_paths: Iterable[str]) -> None:
    """Reject forbidden or undeclared Git-derived paths for a task claim."""
    claim = task if isinstance(task, OwnershipClaim) else OwnershipClaim.from_manifest(task)
    for path in changed_paths:
        if not isinstance(path, str) or not path:
            raise CollisionError("changed path must be a non-empty repository-relative string")
        if _matches_any(path, claim.forbidden_paths):
            raise CollisionError(f"task {claim.task_id} changed forbidden path: {path}")
        if not (
            path in claim.files
            or path in claim.migrations
            or path in claim.schemas
            or _matches_any(path, claim.path_globs)
        ):
            raise CollisionError(f"task {claim.task_id} changed unowned path: {path}")


def _path_collisions(candidate: OwnershipClaim, existing: OwnershipClaim) -> list[Collision]:
    collisions: list[Collision] = []
    for candidate_file in candidate.files:
        for active_file in existing.files:
            if candidate_file == active_file:
                collisions.append(_collision("FILE", candidate, existing, candidate_file, active_file))
        for active_glob in existing.path_globs:
            if _matches(active_glob, candidate_file):
                collisions.append(_collision("PATH_GLOB", candidate, existing, candidate_file, active_glob))
    for candidate_glob in candidate.path_globs:
        for active_file in existing.files:
            if _matches(candidate_glob, active_file):
                collisions.append(_collision("PATH_GLOB", candidate, existing, candidate_glob, active_file))
        for active_glob in existing.path_globs:
            if _globs_overlap(candidate_glob, active_glob):
                collisions.append(_collision("PATH_GLOB", candidate, existing, candidate_glob, active_glob))
    return collisions


def _exact_collisions(
    candidate: OwnershipClaim,
    existing: OwnershipClaim,
    kind: str,
    candidate_items: tuple[str, ...],
    active_items: tuple[str, ...],
    overlaps: object | None = None,
) -> list[Collision]:
    predicate = overlaps if callable(overlaps) else lambda left, right: left == right
    return [
        _collision(kind, candidate, existing, candidate_item, active_item)
        for candidate_item in candidate_items
        for active_item in active_items
        if predicate(candidate_item, active_item)
    ]


def _collision(
    kind: str,
    candidate: OwnershipClaim,
    existing: OwnershipClaim,
    candidate_resource: str,
    active_resource: str,
) -> Collision:
    return Collision(kind, candidate.task_id, existing.task_id, candidate_resource, active_resource)


def _matches(pattern: str, path: str) -> bool:
    return PurePath(path).match(pattern)


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(path == pattern or _matches(pattern, path) for pattern in patterns)


def _globs_overlap(left: str, right: str) -> bool:
    left_prefix = _literal_directory_prefix(left)
    right_prefix = _literal_directory_prefix(right)
    if not left_prefix or not right_prefix:
        return True
    return _directory_prefix(left_prefix, right_prefix) or _directory_prefix(right_prefix, left_prefix)


def _literal_directory_prefix(pattern: str) -> tuple[str, ...]:
    parts: list[str] = []
    for part in PurePath(pattern).parts:
        if any(character in part for character in "*?["):
            break
        parts.append(part)
    return tuple(parts)


def _directory_prefix(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return len(left) <= len(right) and left == right[: len(left)]


def _modules_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(f"{right}.") or right.startswith(f"{left}.")
