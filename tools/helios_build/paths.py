"""Repository discovery and strict separation of external mutable state."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from tools.helios_build.errors import StateRootError


@dataclass(frozen=True, slots=True)
class BuildPaths:
    """Resolved repository and externally located Build Fabric state roots."""

    repo_root: Path
    state_root: Path

    @classmethod
    def discover(cls, start: Path, state_root: Path | None = None) -> "BuildPaths":
        candidate = start.resolve()
        if candidate.is_file():
            candidate = candidate.parent
        repo_root = cls._find_repo_root(candidate)

        configured_root = state_root
        if configured_root is None:
            configured = os.environ.get("HELIOS_BUILD_STATE_ROOT")
            configured_root = Path(configured) if configured else repo_root.parent / ".helios-build-state"
        resolved_state_root = configured_root.expanduser().resolve()
        if resolved_state_root == Path(resolved_state_root.anchor):
            raise StateRootError("Build Fabric state root must not be the filesystem root")
        if resolved_state_root == Path.home().resolve():
            raise StateRootError("Build Fabric state root must not be the current user's home directory")
        try:
            resolved_state_root.relative_to(repo_root)
        except ValueError:
            pass
        else:
            raise StateRootError("Build Fabric state root must be outside the repository")
        return cls(repo_root=repo_root, state_root=resolved_state_root)

    @staticmethod
    def _find_repo_root(start: Path) -> Path:
        for candidate in (start, *start.parents):
            pyproject = candidate / "pyproject.toml"
            if not pyproject.is_file():
                continue
            try:
                document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
                raise StateRootError(f"cannot read repository metadata at {pyproject}: {error}") from error
            if document.get("project", {}).get("name") == "helios-takeoff-core":
                return candidate.resolve()
        raise StateRootError("could not locate the helios-takeoff-core repository root")
