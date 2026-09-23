#!/usr/bin/env python3
"""Install this checkout's completion guard without replacing existing hooks."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def install(repo):
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)

    located = git("rev-parse", "--show-toplevel")
    if located.returncode:
        raise ValueError("Choose a non-bare Git checkout.")
    root = Path(located.stdout.strip())
    hook = root / ".githooks/pre-commit"
    checker = root / "scripts/verify-build-checkpoint.py"
    if hook.is_symlink() or not hook.is_file() or not os.access(hook, os.X_OK):
        raise ValueError("The repository .githooks/pre-commit must be a regular executable file.")
    if checker.is_symlink() or not checker.is_file():
        raise ValueError("The repository checkpoint validator is missing or is not a regular file.")
    configured = git("config", "--get", "core.hooksPath")
    if configured.returncode not in (0, 1):
        raise ValueError("Could not read the existing hook configuration.")
    if configured.returncode == 0 and configured.stdout.strip() != ".githooks":
        raise ValueError("Existing core.hooksPath is preserved. Integrate its hooks explicitly before installation.")
    if configured.returncode == 1:
        found = git("rev-parse", "--git-path", "hooks")
        if found.returncode:
            raise ValueError("Could not locate the existing Git hooks.")
        directory = Path(found.stdout.strip())
        if not directory.is_absolute():
            directory = Path(repo).resolve() / directory
        active = [p.name for p in directory.iterdir()
                  if not p.name.endswith(".sample") and p.is_file() and os.access(p, os.X_OK)] if directory.exists() else []
        if active:
            raise ValueError("Existing executable Git hooks are preserved: " + ", ".join(sorted(active)))
    result = git("config", "--local", "core.hooksPath", ".githooks")
    if result.returncode:
        raise ValueError("Could not set the repository-local hook path.")
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        root = install(args.repo)
    except (OSError, ValueError) as error:
        print(f"Hook installation stopped: {error}", file=sys.stderr)
        return 1
    print(f"Completion guard installed locally in {root}; core.hooksPath=.githooks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
