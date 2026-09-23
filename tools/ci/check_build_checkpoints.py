#!/usr/bin/env python3
"""Validate every new build checkpoint in a GitHub PR or push commit range.

The adoption baseline is deliberately pinned: absence of the guard from a later
tree never exempts that commit. This is a read-only graph dispatcher, not a
scheduler, receipt writer or remote fetcher.
"""

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ADOPTION_BASELINE = "db695750d095c6845f0689e3cc4da79d09333935"
ROOT = Path(__file__).resolve().parents[2]
COMMIT_ID = re.compile(r"[0-9a-f]{40}\Z")
ZERO_ID = "0" * 40


class CheckpointError(Exception):
    """A missing prerequisite or invalid checkpoint prevents CI acceptance."""


@dataclass
class RangeResult:
    checked: list[str] = field(default_factory=list)
    reused_merges: list[str] = field(default_factory=list)
    historical: list[str] = field(default_factory=list)


def git(repo: Path, *args: str) -> str:
    """Use argument arrays and real object identities; never interpret a shell."""
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repo), *args],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise CheckpointError(f"Git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def resolve_commit(repo: Path, value: str, label: str) -> str:
    if not isinstance(value, str) or not COMMIT_ID.fullmatch(value) or value == ZERO_ID:
        raise CheckpointError(f"{label} must name a nonzero full 40-character commit SHA")
    try:
        actual = git(repo, "rev-parse", "--verify", value + "^{commit}").strip()
    except CheckpointError as error:
        raise CheckpointError(
            f"Missing {label} commit {value}; provide complete local Git history. {error}"
        ) from error
    if actual != value:
        raise CheckpointError(f"{label} {value} is not an exact commit object")
    return actual


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(repo),
         "merge-base", "--is-ancestor", older, newer],
        capture_output=True, text=True,
    )
    if result.returncode not in (0, 1):
        raise CheckpointError(f"Cannot resolve Git ancestry: {result.stderr.strip()}")
    return result.returncode == 0


def event_range(event_name: str, event_path: Path) -> tuple[str, str]:
    """Read actual source commits, never a PR's synthetic GITHUB_SHA merge."""
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        if not isinstance(event, dict):
            raise ValueError("event must be a JSON object")
        if event_name == "pull_request":
            return event["pull_request"]["base"]["sha"], event["pull_request"]["head"]["sha"]
        if event_name == "push":
            return event["before"], event["after"]
        raise ValueError(f"unsupported event {event_name!r}; use pull_request or push")
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise CheckpointError(f"Cannot select checkpoint range from event: {error}") from error


def validate_range(repo: Path, base: str, head: str, *,
                   baseline: str = ADOPTION_BASELINE,
                   validator_path: Path | None = None) -> RangeResult:
    """Validate all newly reachable post-adoption commits in parent-first order.

    ``baseline`` and ``validator_path`` are injectable only at the Python API
    boundary for temporary-repository tests. The CLI always uses the pinned
    project baseline and the installed validator beside this CI adapter.
    """
    repo = Path(repo).resolve()
    baseline = resolve_commit(repo, baseline, "adoption baseline")
    head = resolve_commit(repo, head, "head")
    if base == ZERO_ID:
        # A branch creation has no old tip. The baseline bounds the first push,
        # while unrelated histories need a separately defined import policy.
        base = baseline
    else:
        base = resolve_commit(repo, base, "base")
    if is_ancestor(repo, head, baseline):
        return RangeResult(historical=[head])
    if not is_ancestor(repo, baseline, head):
        raise CheckpointError(
            "Head does not descend from the adoption baseline. Reconcile this "
            "branch with the adopted checkpoint policy before CI acceptance; "
            "unrelated-root imports are not supported."
        )
    commits = git(repo, "rev-list", "--reverse", "--topo-order", head, "^" + base).splitlines()
    validator_path = Path(validator_path) if validator_path else ROOT / "scripts/verify-build-checkpoint.py"
    if not validator_path.is_file():
        raise CheckpointError(f"Checkpoint validator is unavailable: {validator_path}")
    result = RangeResult()
    for commit in commits:
        if is_ancestor(repo, commit, baseline):
            result.historical.append(commit)
            continue
        parents = git(repo, "rev-list", "--parents", "-n", "1", commit).split()[1:]
        if len(parents) > 2:
            raise CheckpointError(
                f"Octopus merge {commit} is unsupported: remerge verification requires two parents"
            )
        if len(parents) == 2:
            delta = git(repo, "show", "--remerge-diff", "--format=", "--binary",
                        "--no-ext-diff", "--no-textconv", commit, "--")
            if not delta:
                result.reused_merges.append(commit)
                continue
        checked = subprocess.run(
            [sys.executable, str(validator_path), "--repo", str(repo), "--commit", commit],
            capture_output=True, text=True,
        )
        if checked.returncode:
            detail = "\n".join(part.strip() for part in (checked.stdout, checked.stderr) if part.strip())
            raise CheckpointError(
                f"Checkpoint rejected for {commit} (exit {checked.returncode}):\n{detail}"
            )
        result.checked.append(commit)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--base", help="full old/base commit SHA")
    parser.add_argument("--head", help="full new/head commit SHA")
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME"))
    parser.add_argument("--event-path", type=Path, default=os.environ.get("GITHUB_EVENT_PATH"))
    args = parser.parse_args(argv)
    try:
        if args.base is not None or args.head is not None:
            if args.base is None or args.head is None:
                raise CheckpointError("Supply both --base and --head")
            base, head = args.base, args.head
        else:
            if not args.event_name or not args.event_path:
                raise CheckpointError("Supply a GitHub event name/path or explicit --base and --head")
            base, head = event_range(args.event_name, args.event_path)
        result = validate_range(args.repo, base, head)
    except (CheckpointError, OSError) as error:
        print(f"Build checkpoint CI failed: {error}", file=sys.stderr)
        return 1
    print(f"Build checkpoints passed: {len(result.checked)} validated commits, "
          f"{len(result.reused_merges)} automatic merges, "
          f"{len(result.historical)} historical commits excluded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
