#!/usr/bin/env python3
"""Validate a human-authored completion checkpoint against exact Git bytes.

This is a read-only consistency check, not an acceptance authority or scheduler.
It never executes commands declared in receipts or reads evidence from the worktree.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile


STATUS = "CURRENT_STATUS.md"
RECEIPT = "docs/operations/build-checkpoint.json"
REGULAR = {"100644", "100755"}
PROTECTED_PREFIXES = ("apps/", "crates/", "src/", "scripts/", "tests/", "tools/",
                      ".github/", ".githooks/", ".agents/", ".claude/", ".codex/",
                      "governance/", "docs/plans/", "docs/policies/",
                      "docs/superpowers/", "docs/operations/")
PROTECTED_NAMES = {STATUS, RECEIPT, "AGENTS.md", "CLAUDE.md", "KIMI.md", "GROK.md",
                   "GROKBOTS.md", "CURSOR.md", "SKILLS.md", "GOAL.md", "Cargo.toml",
                   "Cargo.lock", "rust-toolchain.toml", "package.json", "package-lock.json",
                   "deny.toml", "rustfmt.toml", ".gitignore", ".gitattributes",
                   "ROADMAP.md", "SECURITY.md", "docs/roadmap.md"}
CSI_NAMES = {"docs/plans/division-23-section-register.json",
             "docs/plans/division-23-task-contracts.json",
             "docs/plans/division-23-section-completion-matrix.md",
             "docs/plans/division-23-section-work-breakdown.md"}
CSI_CARDS = "docs/superpowers/plans/division23-sections/"
CSI_CHECKER = "scripts/verify-csi-division23.py"
HEX256 = re.compile(r"[0-9a-f]{64}\Z")
BLOCK_START = "<!-- build-checkpoint:v1 -->"
BLOCK_END = "<!-- /build-checkpoint -->"


class CheckpointError(Exception):
    pass


def git(repo, *args, input=None):
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), *args], input=input,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise CheckpointError("Git operation failed: " + result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def text(value):
    return isinstance(value, str) and bool(value.strip())


def normalized(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def safe_path(value):
    if not text(value) or "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        return False
    path = PurePosixPath(value)
    return (not path.is_absolute() and str(path) == value and
            not any(part in (".", "..", ".git") for part in path.parts))


def protected(path):
    archival_document = (path.startswith("docs/operations/status-archive/") and
                         PurePosixPath(path).suffix.lower() in {".md", ".txt", ".rst"})
    return not archival_document and (path in PROTECTED_NAMES or path.startswith(PROTECTED_PREFIXES))


def csi_changed(path):
    # The two display filenames have historical variants; all are CSI scope.
    return (path in CSI_NAMES or path.startswith(CSI_CARDS) or
            (path.startswith("docs/plans/division-23-") and
             ("completion-matrix" in path or "work-breakdown" in path)))


class Snapshot:
    def __init__(self, repo, target):
        self.repo = repo
        self.entries = {}
        self.cache = {}
        if target is None:
            raw = git(repo, "ls-files", "--stage", "-z")
            for record in raw.split(b"\0"):
                if record:
                    metadata, path = record.split(b"\t", 1)
                    mode, oid, stage = metadata.decode("ascii").split()
                    if stage != "0":
                        raise CheckpointError("unmerged index: resolve staged conflicts before checkpoint validation")
                    self.entries[path.decode("utf-8")] = (mode, oid)
        else:
            raw = git(repo, "ls-tree", "-r", "-z", target)
            for record in raw.split(b"\0"):
                if record:
                    metadata, path = record.split(b"\t", 1)
                    mode, kind, oid = metadata.decode("ascii").split()
                    self.entries[path.decode("utf-8")] = (mode, oid)

    def read(self, path, regular=True):
        if not safe_path(path):
            raise CheckpointError(f"unsafe repository path: {path!r}")
        entry = self.entries.get(path)
        if entry is None:
            raise CheckpointError(f"{path}: missing from selected Git index/tree")
        mode, oid = entry
        if regular and mode not in REGULAR:
            raise CheckpointError(f"{path}: must be a regular file in selected Git index/tree")
        if path not in self.cache:
            self.cache[path] = git(self.repo, "cat-file", "blob", oid)
        return self.cache[path]


def json_object(data, label):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise CheckpointError(f"{label}: malformed JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CheckpointError(f"{label} must be an object")
    return value


def check_proof(snapshot, proof, label, errors, type_changes):
    if not isinstance(proof, dict):
        errors.append(f"{label}: evidence must be a path/SHA-256 object")
        return
    path, claimed = proof.get("path"), proof.get("sha256")
    if not safe_path(path):
        errors.append(f"{label}: unsafe evidence path")
        return
    if path in (STATUS, RECEIPT):
        errors.append(f"{label}: status/receipt cannot serve as self-proof")
        return
    if path in type_changes:
        errors.append(f"{label}: evidence type changes are not permitted: {path}")
    try:
        data = snapshot.read(path)
    except CheckpointError as exc:
        errors.append(str(exc))
        return
    if not isinstance(claimed, str) or not HEX256.fullmatch(claimed) or hashlib.sha256(data).hexdigest() != claimed:
        errors.append(f"{label}: evidence SHA-256 does not match selected Git bytes: {path}")


def check_status(snapshot, receipt, errors):
    try:
        status = snapshot.read(STATUS).decode("utf-8")
        if status.count(BLOCK_START) != 1 or status.count(BLOCK_END) != 1:
            raise CheckpointError("CURRENT_STATUS.md requires exactly one build-checkpoint:v1 block")
        expression = re.escape(BLOCK_START) + r"\s*```json\s*\n(.*?)\n```\s*" + re.escape(BLOCK_END)
        match = re.search(expression, status, re.DOTALL)
        if not match:
            raise CheckpointError("CURRENT_STATUS.md checkpoint block must contain fenced JSON")
        marker = json_object(match[1], "status checkpoint")
        if type(marker.get("schema_version")) is not int or marker["schema_version"] != 1:
            errors.append("status checkpoint schema_version must be 1")
        if marker.get("receipt") != RECEIPT:
            errors.append("status checkpoint receipt must use canonical path")
        for field in ("task_id", "outcome", "next_task_id", "next_action"):
            if marker.get(field) != receipt.get(field):
                errors.append(f"status checkpoint {field} disagrees with receipt")
        primaries = re.findall(r"\*\*Primary product task:\s*([^\n]+?)\.\*\*", status)
        if len(primaries) != 1 or normalized(primaries[0]) != normalized(receipt.get("next_task_id")):
            errors.append("visible Primary product task must match next_task_id")
        actions = re.findall(r"\*\*Next executable action:\*\*\s*([^\n]*(?:\n(?!\s*\n)[^\n]+)*)", status)
        if len(actions) != 1 or normalized(actions[0]) != normalized(receipt.get("next_action")):
            errors.append("visible Next executable action must match next_action")
    except (CheckpointError, UnicodeError) as exc:
        errors.append(str(exc))


def check_csi(snapshot, errors):
    # Only the CSI checker and its declared input/card closure is materialized.
    # There are no symlinks and no fallback to the working tree.
    try:
        paths = {CSI_CHECKER, "docs/plans/division-23-section-register.json",
                 "docs/plans/division-23-task-contracts.json"}
        register = snapshot.read("docs/plans/division-23-section-register.json")
        try:
            value = json.loads(register)
            if isinstance(value, dict) and isinstance(value.get("sections"), list):
                paths.update(section["delivery_plan"] for section in value["sections"]
                             if isinstance(section, dict) and safe_path(section.get("delivery_plan")))
        except (ValueError, UnicodeError):
            pass  # The selected CSI checker produces the structural diagnostic.
        paths.update(path for path in snapshot.entries if path.startswith(CSI_CARDS))
        with tempfile.TemporaryDirectory(prefix="heleos-checkpoint-csi-") as scratch:
            root = Path(scratch)
            for path in sorted(paths):
                destination = root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(snapshot.read(path))
            result = subprocess.run([sys.executable, str(root / CSI_CHECKER), "--root", str(root)],
                                    cwd=root, capture_output=True, text=True, timeout=60)
            if result.returncode:
                errors.append(f"CSI selected-tree check failed (exit {result.returncode}): " +
                              (result.stdout + result.stderr).strip())
    except (CheckpointError, OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"CSI selected-tree check failed: {exc}")


def validate(repo, base, target=None):
    """Return errors for an exact base-to-index/tree checkpoint; perform no writes."""
    errors = []
    try:
        snapshot = Snapshot(repo, target)
        args = ["diff", "--no-ext-diff", "--no-renames", "--name-status", "-z", base]
        if target is None:
            args.insert(1, "--cached")
        else:
            args.append(target)
        records = git(repo, *args).split(b"\0")
        changes, type_changes = {}, set()
        for index in range(0, len(records) - 1, 2):
            kind, path = records[index].decode("ascii"), records[index + 1].decode("utf-8")
            changes[path] = kind
            if kind == "T":
                type_changes.add(path)
        if not any(protected(path) for path in changes):
            return []
        for path in (STATUS, RECEIPT):
            if path not in changes or changes[path] == "D":
                errors.append(f"{path} must change with protected work")
            if path in type_changes:
                errors.append(f"{path}: type changes are not permitted")
        if errors:
            return errors
        receipt = json_object(snapshot.read(RECEIPT), "receipt")
        if type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1:
            errors.append("receipt schema_version must be 1")
        for field in ("task_id", "summary", "next_task_id", "next_action"):
            if not text(receipt.get(field)):
                errors.append(f"receipt {field} must be a nonempty string")
        if receipt.get("base_commit") != base:
            errors.append(f"receipt base_commit must exactly match {base}")
        outcome = receipt.get("outcome")
        if outcome not in ("complete", "in_progress", "blocked"):
            errors.append("receipt outcome must be complete, in_progress, or blocked")
        limits = receipt.get("remaining_limits")
        if not isinstance(limits, list) or not limits or not all(text(limit) for limit in limits):
            errors.append("remaining_limits must be a nonempty array of explicit strings")
        for field in ("missing_prerequisite", "independent_action"):
            if field not in receipt or (receipt[field] is not None and not text(receipt[field])):
                errors.append(f"receipt {field} must be a nonempty string or null")
            if outcome == "blocked" and not text(receipt.get(field)):
                errors.append(f"blocked checkpoint requires {field}")

        manifest = receipt.get("changed_files")
        seen = set()
        if not isinstance(manifest, list):
            errors.append("changed_files must be an array")
        else:
            for item in manifest:
                if not isinstance(item, dict) or not safe_path(item.get("path")):
                    errors.append("changed_files contains an unsafe/malformed path entry")
                    continue
                path = item["path"]
                if path in seen:
                    errors.append(f"duplicate changed_files path: {path}")
                seen.add(path)
                if path not in changes or path == RECEIPT:
                    continue
                if changes[path] == "D":
                    if "sha256" not in item or item["sha256"] is not None:
                        errors.append(f"deleted path requires null sha256: {path}")
                    continue
                try:
                    actual = hashlib.sha256(snapshot.read(path, regular=False)).hexdigest()
                    if item.get("sha256") != actual:
                        errors.append(f"changed_files SHA-256 mismatch: {path}")
                except CheckpointError as exc:
                    errors.append(str(exc))
            if seen != set(changes) - {RECEIPT}:
                errors.append("changed_files must exactly cover all changed paths except the receipt; "
                              f"missing={sorted(set(changes) - {RECEIPT} - seen)!r}, "
                              f"extra={sorted(seen - (set(changes) - {RECEIPT}))!r}")

        checks = receipt.get("checks")
        if not isinstance(checks, list):
            errors.append("checks must be an array")
            checks = []
        for index, check in enumerate(checks):
            label = f"checks[{index}]"
            if not isinstance(check, dict):
                errors.append(f"{label} must be an object")
                continue
            if not text(check.get("command")) or type(check.get("exit_code")) is not int:
                errors.append(f"{label} requires command and integer exit_code")
            check_proof(snapshot, check.get("evidence"), label, errors, type_changes)
        review = receipt.get("review")
        if "review" not in receipt:
            errors.append("review must be an accepted review object or null")
        if review is not None:
            if not isinstance(review, dict) or not text(review.get("reviewer")) or review.get("outcome") != "accepted":
                errors.append("review must name a reviewer with outcome accepted")
            if isinstance(review, dict):
                check_proof(snapshot, review.get("evidence"), "review", errors, type_changes)
        if outcome == "complete":
            if normalized(receipt.get("next_task_id")) == normalized(receipt.get("task_id")):
                errors.append("complete must advance next_task_id beyond the closed task")
            if not checks or any(not isinstance(check, dict) or type(check.get("exit_code")) is not int or check["exit_code"] != 0 for check in checks):
                errors.append("complete requires successful checks with exit_code 0")
            if not isinstance(review, dict) or review.get("outcome") != "accepted":
                errors.append("complete requires an accepted review with pinned evidence")
        check_status(snapshot, receipt, errors)
        if not errors and any(csi_changed(path) for path in changes):
            check_csi(snapshot, errors)
    except (CheckpointError, UnicodeError, ValueError, OSError) as exc:
        errors.append(str(exc))
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--commit", metavar="SHA")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        repo = Path(git(args.repo, "rev-parse", "--show-toplevel").decode().strip())
        if args.staged:
            head = subprocess.run(["git", "--no-replace-objects", "-C", str(repo), "rev-parse", "--verify", "HEAD"], capture_output=True)
            base = head.stdout.decode().strip() if head.returncode == 0 else git(repo, "hash-object", "-t", "tree", "--stdin", input=b"").decode().strip()
            target = None
        else:
            if not re.fullmatch(r"[0-9a-fA-F]{40}", args.commit):
                raise CheckpointError("--commit requires a full 40-character commit SHA")
            target = git(repo, "rev-parse", "--verify", "--end-of-options", args.commit + "^{commit}").decode().strip()
            lineage = git(repo, "rev-list", "--parents", "-n", "1", target).decode().split()
            base = lineage[1] if len(lineage) > 1 else git(repo, "hash-object", "-t", "tree", "--stdin", input=b"").decode().strip()
        errors = validate(repo, base, target)
    except (CheckpointError, OSError, UnicodeError) as exc:
        errors = [str(exc)]
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Build checkpoint: valid (or no protected changes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
