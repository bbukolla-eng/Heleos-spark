#!/usr/bin/env python3
"""Discover explicitly active builds from the checked-out local main branch.

Run: python3 /path/to/scripts/active-build-status.py [--repo CHECKOUT] [--human]
Default output is deterministic JSON; exit 1 rejects incomplete/stale authority.
No Git mutation, network call, hook, or optional index refresh is requested.

The committed main-checkout CURRENT_STATUS.md must contain exactly one block:
<!-- active-build-authority:v1 -->
```json
{"schema_version":1,"active_builds":[{"path":".worktrees/example",
 "branch":"build/example","checkpoint":"FULL_COMMIT_ID"}]}
```
<!-- /active-build-authority -->

Paths are relative to the main checkout; each must name a registered checkout.
A checkpoint is a minimum ancestor, not a claim that live HEAD is unchanged.
An empty active_builds list explicitly declares no active builds. Dirty active
checkouts are reported; only the authority file itself must be committed and
unchanged. Ignored files are excluded from cleanliness, as in verify-repo-state.
Results observe live state and do not lock out concurrent writers.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys


# Reuse the repository's read-only Git parser without writing a bytecode cache.
sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "repo_state", Path(__file__).with_name("verify-repo-state.py"))
state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state)
StateError = state.StateError
OPEN = "<!-- active-build-authority:v1 -->"
CLOSE = "<!-- /active-build-authority -->"


def snapshot(repo):
    report = {"main_head": None, "divergence": None}
    state.inspect(repo, report)
    return report


def registrations(repo):
    raw = state.git(repo, "worktree", "list", "--porcelain", "-z")[1]
    records = []
    for block in raw.split(b"\0\0"):
        record = {}
        for field in block.split(b"\0"):
            key, _, value = field.partition(b" ")
            if key:
                record[os.fsdecode(key)] = os.fsdecode(value)
        if record:
            records.append(record)
    return records


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StateError("ambiguous_authority", "Duplicate JSON key in active-build authority.")
        result[key] = value
    return result


def parse_authority(raw):
    text = raw.decode("utf-8")
    if OPEN not in text and CLOSE not in text:
        raise StateError("missing_authority", "CURRENT_STATUS.md has no active-build authority block.")
    if text.count(OPEN) != 1 or text.count(CLOSE) != 1:
        raise StateError("ambiguous_authority", "Expected exactly one active-build authority block.")
    match = re.search(re.escape(OPEN) + r"\s*```json\s*\n(.*?)\n```\s*" + re.escape(CLOSE), text, re.DOTALL)
    if match is None:
        raise StateError("invalid_authority", "Active-build authority must be a fenced JSON block.")
    try:
        value = json.loads(match.group(1), object_pairs_hook=unique_keys)
    except ValueError:
        raise StateError("invalid_authority", "Active-build authority is not valid JSON.")
    if (not isinstance(value, dict) or set(value) != {"schema_version", "active_builds"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["active_builds"], list)):
        raise StateError("invalid_authority", "Unsupported active-build authority schema.")
    paths, branches = set(), set()
    for build in value["active_builds"]:
        if (not isinstance(build, dict) or set(build) != {"path", "branch", "checkpoint"}
                or not all(isinstance(item, str) and item for item in build.values())):
            raise StateError("invalid_authority", "Each build requires path, branch, and checkpoint strings.")
        path = PurePosixPath(build["path"])
        if (path.is_absolute() or ".." in path.parts or "\\" in build["path"]
                or ":" in build["path"] or str(path) != build["path"]
                or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", build["checkpoint"]) is None):
            raise StateError("invalid_authority", "Build path must be canonical and relative; checkpoint must be a full lowercase commit ID.")
        if build["path"] in paths or build["branch"] in branches:
            raise StateError("ambiguous_authority", "Active build paths and branches must be unique.")
        paths.add(build["path"])
        branches.add(build["branch"])
    return value["active_builds"]


def authority_bytes(root, main):
    path = root / "CURRENT_STATUS.md"
    if path.is_symlink() or not path.is_file():
        raise StateError("missing_authority", "Main CURRENT_STATUS.md must be a regular tracked file.")
    raw = path.read_bytes()
    code, committed = state.git(root, "show", main["head"] + ":CURRENT_STATUS.md", allowed=(0, 128))
    changed = any(item["path"] == "CURRENT_STATUS.md" for item in main["tracked_changes"])
    if code or changed or raw != committed:
        raise StateError("uncommitted_authority", "Main CURRENT_STATUS.md must match its committed bytes and index.")
    return raw


def inspect(repo, report):
    initial = snapshot(repo)
    records = registrations(repo)
    candidates = [r for r in records if r.get("branch") == "refs/heads/main"]
    if len(candidates) != 1 or "prunable" in candidates[0]:
        raise StateError("main_checkout_unavailable", "Exactly one existing checkout of local main is required.")
    root = Path(candidates[0]["worktree"]).resolve()
    main = snapshot(root)
    if main["branch"] != "main" or main["git_common_dir"] != initial["git_common_dir"]:
        raise StateError("stale_authority", "Main checkout identity does not match its Git registration.")
    report["main"] = main
    report["authority"] = {"path": str(root / "CURRENT_STATUS.md"), "sha256": None}
    raw = authority_bytes(root, main)
    report["authority"]["sha256"] = hashlib.sha256(raw).hexdigest()
    entries = parse_authority(raw)
    registered = {Path(r["worktree"]).resolve(): r for r in records}
    observed = []
    for entry in sorted(entries, key=lambda item: item["path"]):
        path = root / entry["path"]
        canonical = path.resolve()
        if (canonical != path or canonical not in registered
                or "prunable" in registered[canonical] or not canonical.is_dir()):
            raise StateError("stale_authority", "Active path is missing, aliased, or no longer a registered checkout: " + str(path))
        actual = snapshot(canonical)
        observed.append((canonical, actual))
        build = dict(actual, checkpoint=entry["checkpoint"])
        report["active_builds"].append(build)
        code, commit = state.git(root, "rev-parse", "--verify", "--quiet",
                                 entry["checkpoint"] + "^{commit}", allowed=(0, 1))
        valid_checkpoint = not code and os.fsdecode(commit).strip() == entry["checkpoint"]
        ancestor = valid_checkpoint and state.git(root, "merge-base", "--is-ancestor",
                                                  entry["checkpoint"], actual["head"], allowed=(0, 1))[0] == 0
        if (actual["git_common_dir"] != main["git_common_dir"] or actual["checkout_root"] != str(canonical)
                or actual["branch"] != entry["branch"] or not ancestor):
            report["errors"].append({"code": "stale_authority", "message":
                "Active checkout branch, repository, or checkpoint ancestry no longer matches authority: " + str(path)})
    if (registrations(repo) != records or snapshot(root) != main
            or authority_bytes(root, main) != raw
            or any(snapshot(path) != actual for path, actual in observed)):
        raise StateError("state_changed", "Repository or authority changed during inspection; take a fresh snapshot.")


def main(argv=None):
    report = {"schema_version": 1, "status": "FAIL", "main": None,
              "authority": None, "active_builds": [], "errors": []}
    human = False
    try:
        parser = state.JsonArgumentParser(description=__doc__, allow_abbrev=False,
                                         formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("--repo", default=".", help="any checkout or nested directory")
        parser.add_argument("--human", action="store_true", help="concise human output instead of JSON")
        args = parser.parse_args(argv)
        human = args.human
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_NAMESPACE",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
            if name in os.environ:
                raise StateError("git_environment_override", "Unset " + name + "; Git routing overrides are not accepted.")
        inspect(Path(args.repo).resolve(), report)
    except StateError as error:
        report["errors"].append({"code": error.code, "message": str(error)})
    except (OSError, ValueError, StopIteration) as error:
        report["errors"].append({"code": "inspection_failed", "message": "Inspection failed (" + type(error).__name__ + ")."})
    report["status"] = "FAIL" if report["errors"] else "PASS"
    if human:
        print(report["status"])
        if report["authority"]:
            print("Authority: " + report["authority"]["path"])
        for build in report["active_builds"]:
            divergence = build["divergence"]
            print("Active: " + build["checkout_root"])
            print("  " + str(build["branch"]) + " " + build["head"])
            print("  {} ahead={} behind={}".format("clean" if build["clean"] else "dirty",
                  divergence["ahead_of_main"], divergence["behind_main"]))
        if not report["active_builds"] and not report["errors"]:
            print("No active builds declared.")
        for error in report["errors"]:
            print(error["code"] + ": " + error["message"])
    else:
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
