#!/usr/bin/env python3
"""Print read-only local Git continuity evidence as one deterministic JSON object.

Usage: python3 scripts/verify-repo-state.py --repo /exact/checkout \
    --expect-branch build/example --expect-head FULL_COMMIT_ID --require-clean

Exit 0 means inspection and requested checks passed; exit 1 means failure. Local
refs/heads/main is the comparison baseline, never a fetched or inferred remote.
Ignored files are excluded from dirtiness. Detached HEAD is reported as null.
This observes live Git state; it does not lock the checkout against other writers.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


class StateError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise StateError("invalid_arguments", message)


def git(repo, *args, allowed=(0,)):
    # Disallow optional index refresh writes and fsmonitor hook execution. All
    # commands below inspect local state; no shell or network operation is used.
    result = subprocess.run(
        [
            "git", "--no-optional-locks", "-c", "core.fsmonitor=false",
            "-c", "core.untrackedCache=false", "-C", str(repo), *args,
        ],
        stdin=subprocess.DEVNULL, capture_output=True,
        env=dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0"),
    )
    if result.returncode not in allowed:
        raise StateError("git_failed", f"Local Git {args[0]} failed (exit {result.returncode}).")
    return result.returncode, result.stdout


def git_text(repo, *args):
    return os.fsdecode(git(repo, *args)[1].removesuffix(b"\n"))


def branch_name(repo):
    code, output = git(repo, "symbolic-ref", "--quiet", "HEAD", allowed=(0, 1))
    if code == 1:
        return None
    ref = os.fsdecode(output.removesuffix(b"\n"))
    if not ref.startswith("refs/heads/"):
        raise StateError("invalid_branch_ref", "HEAD does not reference a local branch.")
    return ref.removeprefix("refs/heads/")


def dirtiness(repo):
    raw = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none")[1]
    entries = iter(raw.split(b"\0"))
    tracked = []
    untracked = []
    for entry in entries:
        if not entry:
            continue
        state = entry[:2].decode("ascii")
        path = os.fsdecode(entry[3:])
        if state == "??":
            untracked.append(path)
        else:
            change = {"status": state, "path": path}
            if "R" in state or "C" in state:
                change["original_path"] = os.fsdecode(next(entries))
            tracked.append(change)
    return tracked, untracked


def inspect(repo, report):
    root = Path(git_text(repo, "rev-parse", "--show-toplevel")).resolve()
    report["checkout_root"] = str(root)
    common_dir = Path(git_text(root, "rev-parse", "--git-common-dir"))
    report["git_common_dir"] = str((root / common_dir).resolve())
    report["branch"] = branch_name(root)
    report["head"] = git_text(root, "rev-parse", "--verify", "HEAD^{commit}")
    main_code, main_bytes = git(root, "rev-parse", "--verify", "--quiet", "refs/heads/main^{commit}", allowed=(0, 1))
    if main_code == 0:
        report["main_head"] = os.fsdecode(main_bytes.removesuffix(b"\n"))
        # Freeze both IDs for graph counting even if a ref moves during the call.
        counts = git_text(root, "rev-list", "--left-right", "--count", report["head"] + "..." + report["main_head"]).split()
        report["divergence"] = {"ahead_of_main": int(counts[0]), "behind_main": int(counts[1])}
    tracked, untracked = dirtiness(root)
    report.update({
        "tracked_changes": tracked, "untracked_paths": untracked,
        "tracked_dirty": bool(tracked), "untracked_dirty": bool(untracked),
        "clean": not tracked and not untracked,
    })
    worktrees = git(root, "worktree", "list", "--porcelain", "-z")[1]
    report["worktree_paths"] = sorted(
        os.fsdecode(entry.removeprefix(b"worktree "))
        for entry in worktrees.split(b"\0") if entry.startswith(b"worktree ")
    )
    if main_code != 0:
        raise StateError("main_unavailable", "Local refs/heads/main is unavailable; main identity and divergence cannot be verified.")
    # Detect common concurrent ref/status changes, without acquiring write locks.
    if (
        git_text(root, "rev-parse", "--verify", "HEAD^{commit}") != report["head"]
        or branch_name(root) != report["branch"]
        or git_text(root, "rev-parse", "--verify", "refs/heads/main^{commit}") != report["main_head"]
        or dirtiness(root) != (tracked, untracked)
    ):
        raise StateError("state_changed", "Repository refs or dirtiness changed during inspection; take a fresh snapshot.")


def main(argv=None):
    report = {
        "schema_version": 1, "status": "FAIL", "checkout_root": None,
        "git_common_dir": None, "branch": None, "head": None, "main_head": None,
        "divergence": None, "tracked_dirty": None, "untracked_dirty": None,
        "clean": None, "tracked_changes": [], "untracked_paths": [],
        "worktree_paths": [], "errors": [],
    }
    try:
        parser = JsonArgumentParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--repo", default=".", help="checkout or nested directory (default: current directory)")
        parser.add_argument("--expect-branch", help="exact local branch name; detached HEAD cannot match")
        parser.add_argument("--expect-head", help="complete 40- or 64-digit hexadecimal commit ID")
        parser.add_argument("--require-clean", action="store_true", help="reject tracked and nonignored untracked changes")
        args = parser.parse_args(argv)
        if args.expect_head is not None and re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", args.expect_head) is None:
            raise StateError("invalid_expected_head", "--expect-head requires a full hexadecimal commit ID, not a prefix or revision expression.")
        for name in (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
            "GIT_NAMESPACE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
        ):
            if name in os.environ:
                raise StateError("git_environment_override", f"Unset {name}; repository routing and injected Git configuration are not accepted.")
        inspect(Path(args.repo).resolve(), report)
        if args.expect_branch is not None and report["branch"] != args.expect_branch:
            report["errors"].append({"code": "branch_mismatch", "message": "Actual branch does not match --expect-branch."})
        if args.expect_head is not None and report["head"] != args.expect_head.lower():
            report["errors"].append({"code": "head_mismatch", "message": "Actual HEAD does not match --expect-head."})
        if args.require_clean and not report["clean"]:
            report["errors"].append({"code": "dirty_checkout", "message": "Tracked or nonignored untracked changes are present."})
    except StateError as error:
        report["errors"].append({"code": error.code, "message": str(error)})
    except (OSError, ValueError, StopIteration) as error:
        report["errors"].append({"code": "inspection_failed", "message": f"Repository inspection failed ({type(error).__name__})."})
    report["status"] = "FAIL" if report["errors"] else "PASS"
    print(json.dumps(report, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
