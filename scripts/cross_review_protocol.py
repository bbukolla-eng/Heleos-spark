#!/usr/bin/env python3
"""Local exact-revision gate for Codex/Claude author and cross-review worktrees.

This module reads Git and files and, for pinned controller checkpoints, runs the
repository's read-only checkpoint validator on exact commits. It does not run
author task checks, invoke providers, commit, publish branches, create PRs, post
reviews, or grant merge authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any


class ProtocolError(ValueError):
    """An admitted task, candidate, or review no longer meets its exact contract."""


_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TEAMS = frozenset(("codex", "claude"))
_GIT_OUTPUT_LIMIT = 16 * 1024 * 1024
_OBJECT_OUTPUT_LIMITS = {"commit": 4 * 1024 * 1024, "tree": 16 * 1024 * 1024, "blob": 128 * 1024 * 1024}
_VERIFIED_OBJECT_BUDGET = 1024 * 1024 * 1024
_GIT_TIMEOUT_SECONDS = 120
_PROTECTED = (
    ".git/", ".github/", ".githooks/", ".agents/", ".claude/", ".loop-trial/",
    "AGENTS.md", "CLAUDE.md", "GROK.md", "CURSOR.md", "GROKBOTS.md",
    "SKILLS.md", "CURRENT_STATUS.md", ".gitignore", ".gitattributes", ".gitmodules",
    "docs/decisions/", "docs/policies/", "docs/operations/owner-standing-approval-2026-09-27/",
    "docs/operations/build-checkpoint.json",
    "docs/operations/checkpoint-evidence-policy.json",
    "docs/operations/build-checkpoints.md",
    "scripts/verify-build-checkpoint.py", "scripts/verify-csi-division23.py",
    "scripts/active-build-status.py",
    "scripts/cross_review_protocol.py", "scripts/build_run_guard.py", "tests/cross-review/test_cross_review_protocol.py",
    "tools/ci/check_build_checkpoints.py",
    "docs/architecture/decisions/", "docs/operations/acceptance.md",
    "docs/operations/build-run-guard.md", "docs/operations/guarded-worker-runner.md",
    "docs/operations/claude-code-headless.md", "docs/operations/claude-opus-5-5-ultracode.json",
    "docs/operations/claude-opus-5-5-ultracode.md",
    "docs/plans/division-23-section-register.json",
    "docs/plans/division-23-task-contracts.json", ".cursor/", ".codex/",
    ".worktrees/", "crates/heleos-worker-runner/", "docs/runs/",
    "docs/research/notebooklm/", "docs/operations/loop-controller-mvp-2026-10-01/",
    "docs/operations/agent-coordination.md", "docs/operations/notebooklm-research.md",
    "scripts/run-windows-native-candidate.ps1", "scripts/import-windows-native-candidate.ps1",
    "scripts/verify-windows-worker-containment.ps1",
    "scripts/cross_review_protocol/", "scripts/build_run_guard/", "scripts/__pycache__/",
    "tests/cross-review/__pycache__/", "tests/cross-review/test_cross_review_protocol/",
)
_CONTROLLER_CHECKPOINT_PATHS = ("CURRENT_STATUS.md", "docs/operations/build-checkpoint.json")
_CONTROLLER_EVIDENCE_ROOT = "docs/operations/loop-controller-mvp-2026-10-01/cross-review-run-001/checkpoint-evidence/"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _git(root: Path, *args: str, output_limit: int | None = None) -> bytes:
    """Read bounded Git stdout, killing the whole subprocess group on overrun."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")
    # An older Git that lacks --no-lazy-fetch fails closed before reading objects.
    command = ["git", "--no-lazy-fetch", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", "core.fileMode=true", "-c", "core.commitGraph=false", "-C", str(root), *args]
    limit = _GIT_OUTPUT_LIMIT if output_limit is None else output_limit
    if limit < 1:
        raise ProtocolError("Git output budget exhausted")
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, start_new_session=True)
    except OSError as exc:
        raise ProtocolError("Git executable unavailable") from exc
    completed = False
    try:
        assert process.stdout is not None
        deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
        chunks: list[bytes] = []
        size = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0 or not selector.select(remaining_time):
                    raise ProtocolError("Git command timed out")
                chunk = os.read(process.stdout.fileno(), min(64 * 1024, limit - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ProtocolError("Git output limit exceeded")
                chunks.append(chunk)
        try:
            status = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise ProtocolError("Git command timed out") from exc
        if status:
            raise ProtocolError(f"Git state unavailable for {root}: {' '.join(args)}")
        completed = True
        stdout = b"".join(chunks)
        return stdout if "-z" in args or args[0] in ("show", "cat-file") else stdout.strip()
    finally:
        if not completed:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                if process.poll() is None:
                    process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if process.stdout is not None:
            process.stdout.close()


def _path_key(value: str) -> tuple[str, ...]:
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in value.rstrip("/").split("/"))


def _safe_path(value: Any, *, directory: bool = False) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ProtocolError("unsafe path")
    if directory:
        if not value.endswith("/"):
            raise ProtocolError("unsafe directory path")
        value = value[:-1]
    path = PurePosixPath(value)
    if value in (".", "..") or value.startswith("/") or value.startswith("./") or "//" in value or path.as_posix() != value:
        raise ProtocolError("unsafe path")
    if any(part in (".", "..") or part.casefold() == ".git" for part in path.parts):
        raise ProtocolError("unsafe path")
    if any(ord(c) < 32 or ord(c) == 127 or unicodedata.category(c) == "Cf" for c in value):
        raise ProtocolError("unsafe path")
    try:
        value.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise ProtocolError("unsafe path") from exc
    for part in path.parts:
        normalized = unicodedata.normalize("NFC", part).casefold()
        if part.endswith((".", " ")) or ":" in part or "~" in part or normalized.split(".")[0] in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
            raise ProtocolError("unsafe Windows/HFS path alias")
    return value + ("/" if directory else "")


def _allowed(spec: str, path: str) -> bool:
    return path.startswith(spec) and len(path) > len(spec) if spec.endswith("/") else path == spec


def _controller_checkpoint_pins(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pin controller-owned checkpoint pairs and exact evidence per commit."""
    raw = entry.get("controller_checkpoint_commits", [])
    if not isinstance(raw, list):
        raise ProtocolError("controller checkpoint commits must be a list")
    pins: dict[str, dict[str, Any]] = {}
    seen_evidence: set[str] = set()
    required = {"commit", "status_sha256", "receipt_sha256", "evidence_files"}
    for item in raw:
        if (not isinstance(item, dict) or set(item) != required or
                not isinstance(item.get("commit"), str) or not re.fullmatch(r"[0-9a-f]{40}", item["commit"]) or
                any(not isinstance(item.get(key), str) or not _SHA256.fullmatch(item[key])
                    for key in ("status_sha256", "receipt_sha256")) or
                not isinstance(item.get("evidence_files"), list)):
            raise ProtocolError("invalid controller checkpoint pin")
        if item["commit"] in pins:
            raise ProtocolError("duplicate controller checkpoint commit")
        for evidence in item["evidence_files"]:
            if (not isinstance(evidence, dict) or set(evidence) != {"path", "sha256"} or
                    not isinstance(evidence.get("path"), str) or
                    not evidence["path"].startswith(_CONTROLLER_EVIDENCE_ROOT) or
                    evidence["path"] == _CONTROLLER_EVIDENCE_ROOT or
                    _safe_path(evidence["path"]) != evidence["path"] or
                    not isinstance(evidence.get("sha256"), str) or
                    not _SHA256.fullmatch(evidence["sha256"])):
                raise ProtocolError("invalid controller checkpoint evidence pin")
            if evidence["path"] in seen_evidence:
                raise ProtocolError("duplicate controller checkpoint evidence path")
            seen_evidence.add(evidence["path"])
        pins[item["commit"]] = item
    return pins


def _overlap(a: str, b: str) -> bool:
    left, right = _path_key(a), _path_key(b)
    return left[:len(right)] == right or right[:len(left)] == left


def _path_overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _lexical_path(value: str) -> Path:
    """Collapse dot components without following any symlink."""
    return Path(os.path.abspath(value))


def _full_sha(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ProtocolError(f"invalid {field}")
    return value


def _verify_registered_gitfile(root: Path, common: Path) -> None:
    gitfile = root / ".git"
    try:
        details = gitfile.lstat()
        if not stat.S_ISREG(details.st_mode):
            raise ProtocolError("author gitfile must be a regular file")
        content = gitfile.read_text(encoding="utf-8").strip()
        if not content.startswith("gitdir: "):
            raise ProtocolError("invalid author gitfile")
        named = Path(content[8:])
        linked = (root / named).resolve(strict=True) if not named.is_absolute() else named.resolve(strict=True)
        if linked.parent != common / "worktrees":
            raise ProtocolError("author gitfile points outside pinned common repository")
        backlink = Path((linked / "gitdir").read_text(encoding="utf-8").strip()).resolve(strict=True)
        if backlink != gitfile:
            raise ProtocolError("author gitfile backlink mismatch")
    except OSError as exc:
        raise ProtocolError("author gitfile identity unavailable") from exc


def validate_manifest(manifest: Any, subject_team: str | None = None) -> dict[str, Any]:
    """Validate exact base, two real distinct Git worktrees, branches and path grants."""
    if subject_team is not None and subject_team not in _TEAMS:
        raise ProtocolError("unknown author team")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("task_id"), str) or not manifest["task_id"].strip():
        raise ProtocolError("invalid task ID")
    base = _full_sha(manifest.get("base_commit"), field="base commit")
    raw_parent = manifest.get("worktree_parent")
    if not isinstance(raw_parent, str) or not Path(raw_parent).is_absolute():
        raise ProtocolError("approved worktree parent must be an absolute path")
    try:
        approved_parent = Path(raw_parent).resolve(strict=True)
    except OSError as exc:
        raise ProtocolError("approved worktree parent is unavailable") from exc
    if not approved_parent.is_dir():
        raise ProtocolError("approved worktree parent is not a directory")
    raw_common = manifest.get("repository_common_dir")
    if not isinstance(raw_common, str) or not Path(raw_common).is_absolute():
        raise ProtocolError("pinned repository common dir must be absolute")
    pinned_common = Path(raw_common).resolve(strict=True)
    if not pinned_common.is_dir() or pinned_common == approved_parent or approved_parent in pinned_common.parents:
        raise ProtocolError("pinned repository common dir must be outside author worktrees")
    raw_evidence = manifest.get("review_evidence_root")
    if not isinstance(raw_evidence, str) or not Path(raw_evidence).is_absolute():
        raise ProtocolError("controller review evidence root must be absolute")
    evidence_root = Path(raw_evidence).resolve(strict=True)
    raw_evidence_path = _lexical_path(raw_evidence)
    raw_parent_path = _lexical_path(raw_parent)
    raw_common_path = _lexical_path(raw_common)
    if (not evidence_root.is_dir() or _path_overlap(evidence_root, approved_parent) or
            _path_overlap(evidence_root, pinned_common) or
            _path_overlap(raw_evidence_path, raw_parent_path) or
            _path_overlap(raw_evidence_path, raw_common_path)):
        raise ProtocolError("controller review evidence root overlaps author worktrees or common Git metadata")
    checks = manifest.get("required_checks")
    if not isinstance(checks, list) or not checks or any(not isinstance(c, str) or not c.strip() for c in checks) or len(set(checks)) != len(checks):
        raise ProtocolError("invalid required checks")
    authors = manifest.get("authors")
    if not isinstance(authors, dict) or set(authors) != _TEAMS:
        raise ProtocolError("exactly Codex and Claude authors are required")
    roots: dict[str, Path] = {}
    common_dirs: set[Path] = set()
    grants: dict[str, list[str]] = {}
    for team in ("codex", "claude"):
        entry = authors[team]
        if not isinstance(entry, dict):
            raise ProtocolError("invalid author entry")
        raw_root = entry.get("worktree")
        if not isinstance(raw_root, str) or not Path(raw_root).is_absolute():
            raise ProtocolError("author worktree must be absolute")
        live = subject_team is None or subject_team == team
        root = Path(raw_root).resolve(strict=live)
        if root == approved_parent or approved_parent not in root.parents:
            raise ProtocolError(f"{team} worktree is outside the approved worktree parent")
        roots[team] = root
        branch = entry.get("branch")
        if not isinstance(branch, str) or not branch:
            raise ProtocolError(f"{team} branch missing")
        if entry.get("reviewer") != ("claude" if team == "codex" else "codex"):
            raise ProtocolError(f"{team} reviewer must be opposite author")
        expected_head = _full_sha(entry.get("head_commit", base), field="head commit")
        if live:
            if not root.is_dir() or Path(_git(root, "rev-parse", "--show-toplevel").decode()).resolve() != root:
                raise ProtocolError("author worktree is not a Git root")
            registered = {Path(record[9:].decode()).resolve() for record in _git(root, "worktree", "list", "--porcelain", "-z").split(b"\x00") if record.startswith(b"worktree ")}
            if root not in registered:
                raise ProtocolError("author worktree is not registered")
            common_raw = Path(_git(root, "rev-parse", "--git-common-dir").decode())
            actual_common = (root / common_raw).resolve() if not common_raw.is_absolute() else common_raw.resolve()
            common_dirs.add(actual_common)
            if actual_common != pinned_common:
                raise ProtocolError(f"{team} common repository differs from pin")
            _verify_registered_gitfile(root, pinned_common)
            if _git(root, "branch", "--show-current").decode() != branch:
                raise ProtocolError(f"{team} branch drift")
            try:
                resolved_base = _git(root, "rev-parse", "--verify", f"{base}^{{commit}}").decode()
            except ProtocolError as exc:
                raise ProtocolError(f"{team} base commit drift") from exc
            if resolved_base != base:
                raise ProtocolError(f"{team} base commit drift")
            actual_head = _git(root, "rev-parse", "HEAD").decode()
            if actual_head != expected_head:
                raise ProtocolError(f"{team} head drift: expected {expected_head}, found {actual_head}")
            if _git(root, "rev-parse", "--verify", f"refs/heads/{branch}").decode() != expected_head:
                raise ProtocolError(f"{team} branch ref differs from pinned head")
            _raw_chain(root, base, expected_head)
        paths = entry.get("allowed_paths")
        if not isinstance(paths, list) or not paths:
            raise ProtocolError(f"{team} allowed paths missing")
        grants[team] = [_safe_path(p, directory=isinstance(p, str) and p.endswith("/")) for p in paths]
        if len(set(grants[team])) != len(grants[team]):
            raise ProtocolError(f"{team} duplicate allowed paths")
        if any(_overlap(grant, protected) for grant in grants[team] for protected in _PROTECTED):
            raise ProtocolError(f"{team} grant overlaps protected authority path")
        _controller_checkpoint_pins(entry)
    if any(_controller_checkpoint_pins(authors[team]) for team in _TEAMS):
        expected_validator = manifest.get("checkpoint_validator_sha256")
        validator = Path(__file__).resolve().with_name("verify-build-checkpoint.py")
        if (not isinstance(expected_validator, str) or not _SHA256.fullmatch(expected_validator)
                or not validator.is_file() or hashlib.sha256(validator.read_bytes()).hexdigest() != expected_validator):
            raise ProtocolError("trusted checkpoint validator identity differs")
    if roots["codex"] == roots["claude"] or (subject_team is None and len(common_dirs) != 1):
        raise ProtocolError("authors require distinct worktrees in the same repository")
    if roots["codex"] in roots["claude"].parents or roots["claude"] in roots["codex"].parents:
        raise ProtocolError("author worktrees cannot be nested")
    if authors["codex"]["branch"] == authors["claude"]["branch"]:
        raise ProtocolError("authors require distinct branches")
    if any(_overlap(a, b) for a in grants["codex"] for b in grants["claude"]):
        raise ProtocolError("author allowed paths overlap")
    for team in _TEAMS:
        for pin in _controller_checkpoint_pins(authors[team]).values():
            for evidence in pin["evidence_files"]:
                if any(_overlap(evidence["path"], grant) for grant in grants["codex"] + grants["claude"]):
                    raise ProtocolError("controller checkpoint evidence overlaps author grant")
    return manifest


def _subject_manifest_digest(manifest: dict[str, Any], team: str) -> str:
    opposite = "claude" if team == "codex" else "codex"
    projected = {key: manifest[key] for key in ("task_id", "base_commit", "worktree_parent", "repository_common_dir", "review_evidence_root", "required_checks")}
    projected["author"] = manifest["authors"][team]
    projected["opposite_allowed_paths"] = manifest["authors"][opposite]["allowed_paths"]
    if "checkpoint_validator_sha256" in manifest:
        projected["checkpoint_validator_sha256"] = manifest["checkpoint_validator_sha256"]
    return _digest(projected)


class _ObjectBudget:
    def __init__(self) -> None:
        self.remaining = _VERIFIED_OBJECT_BUDGET


def _verified_object(root: Path, kind: str, oid: str, algorithm: str, budget: _ObjectBudget | None = None) -> bytes:
    expected_length = 40 if algorithm == "sha1" else 64
    if not _SHA.fullmatch(oid) or len(oid) != expected_length:
        raise ProtocolError("invalid Git object identity")
    limit = _OBJECT_OUTPUT_LIMITS[kind]
    if budget is not None:
        limit = min(limit, budget.remaining)
    raw = _git(root, "cat-file", kind, oid, output_limit=limit)
    if budget is not None:
        budget.remaining -= len(raw)
    if hashlib.new(algorithm, f"{kind} {len(raw)}\0".encode() + raw).hexdigest() != oid:
        raise ProtocolError(f"{kind} object hash mismatch")
    return raw


def _tree(root: Path, revision: str, algorithm: str, budget: _ObjectBudget | None = None) -> dict[str, tuple[str, str]]:
    """Parse and rehash every raw tree and blob, including nested base subtrees."""
    result: dict[str, tuple[str, str]] = {}
    oid_size = 20 if algorithm == "sha1" else 32
    count = 0

    def walk(tree_oid: str, prefix: str, depth: int) -> None:
        nonlocal count
        if depth > 128:
            raise ProtocolError("Git tree nesting limit exceeded")
        raw = _verified_object(root, "tree", tree_oid, algorithm, budget)
        offset = 0
        previous_key: bytes | None = None
        names: set[bytes] = set()
        while offset < len(raw):
            space = raw.find(b" ", offset)
            nul = raw.find(b"\0", space + 1) if space != -1 else -1
            if space == -1 or nul == -1 or nul + 1 + oid_size > len(raw):
                raise ProtocolError("malformed raw Git tree")
            mode = raw[offset:space]
            name = raw[space + 1:nul]
            object_oid = raw[nul + 1:nul + 1 + oid_size].hex()
            offset = nul + 1 + oid_size
            if not name or b"/" in name or name in (b".", b"..") or name in names:
                raise ProtocolError("duplicate or malformed raw Git tree entry")
            names.add(name)
            if mode not in (b"40000", b"100644", b"100755", b"120000"):
                raise ProtocolError("unsupported raw Git tree mode")
            sort_key = name + (b"/" if mode == b"40000" else b"")
            if previous_key is not None and sort_key <= previous_key:
                raise ProtocolError("misordered raw Git tree")
            previous_key = sort_key
            path = prefix + name.decode("utf-8", "strict")
            _safe_path(path)
            count += 1
            if count > 100000:
                raise ProtocolError("Git tree entry limit exceeded")
            if mode == b"40000":
                walk(object_oid, path + "/", depth + 1)
            else:
                _verified_object(root, "blob", object_oid, algorithm, budget)
                result[path] = (mode.decode("ascii"), object_oid)

    walk(revision, "", 0)
    return result


def _raw_commit(root: Path, oid: str, algorithm: str, budget: _ObjectBudget | None = None) -> dict[str, str | None]:
    """Read and rehash the commit object; Git's graft and commit graph are not ancestry."""
    if not _SHA.fullmatch(oid) or len(oid) != (40 if algorithm == "sha1" else 64):
        raise ProtocolError("invalid raw commit identity")
    raw = _verified_object(root, "commit", oid, algorithm, budget)
    header, separator, _message = raw.partition(b"\n\n")
    if not separator:
        raise ProtocolError("malformed raw commit object")
    fields: dict[str, list[bytes]] = {}
    header_names: list[str] = []
    previous = ""
    for line in header.split(b"\n"):
        if line.startswith(b" "):
            if previous not in ("gpgsig", "gpgsig-sha256", "mergetag"):
                raise ProtocolError("malformed raw commit header")
            continue
        name, space, value = line.partition(b" ")
        if not space or not value:
            raise ProtocolError("malformed raw commit header")
        try:
            previous = name.decode("ascii")
        except UnicodeError as exc:
            raise ProtocolError("malformed raw commit header") from exc
        if previous not in ("tree", "parent", "author", "committer", "encoding", "gpgsig", "gpgsig-sha256", "mergetag"):
            raise ProtocolError("unexpected raw commit header")
        header_names.append(previous)
        fields.setdefault(previous, []).append(value)
    if any(len(fields.get(key, [])) != 1 for key in ("tree", "author", "committer")) or len(fields.get("parent", [])) > 1:
        raise ProtocolError("nonlinear or malformed raw commit")
    expected_prefix = ["tree"] + (["parent"] if fields.get("parent") else []) + ["author", "committer"]
    if header_names[:len(expected_prefix)] != expected_prefix or any(name in expected_prefix for name in header_names[len(expected_prefix):]):
        raise ProtocolError("misordered raw commit headers")
    try:
        tree = fields["tree"][0].decode("ascii")
        parent = fields.get("parent", [None])[0]
        parent = parent.decode("ascii") if parent is not None else None
    except UnicodeError as exc:
        raise ProtocolError("malformed raw commit identity") from exc
    expected_length = 40 if algorithm == "sha1" else 64
    if not _SHA.fullmatch(tree) or len(tree) != expected_length or (parent is not None and (not _SHA.fullmatch(parent) or len(parent) != expected_length)):
        raise ProtocolError("malformed raw commit identity")
    return {"commit": oid, "tree": tree, "parent": parent, "object_sha256": hashlib.sha256(raw).hexdigest()}


def _raw_chain(root: Path, base: str, head: str, budget: _ObjectBudget | None = None) -> tuple[str, list[dict[str, str | None]]]:
    """Walk actual parent bytes backward to base, independent of Git graph metadata."""
    algorithm = _git(root, "rev-parse", "--show-object-format").decode()
    if algorithm not in ("sha1", "sha256"):
        raise ProtocolError("unsupported Git object format")
    if budget is None:
        budget = _ObjectBudget()
    base_object = _raw_commit(root, base, algorithm, budget)
    chain: list[dict[str, str | None]] = []
    seen: set[str] = set()
    current = head
    while current != base:
        if current in seen or len(chain) >= 1000:
            raise ProtocolError("cyclic or oversized raw commit history")
        seen.add(current)
        item = _raw_commit(root, current, algorithm, budget)
        if item["parent"] is None:
            raise ProtocolError("raw commit history does not reach pinned base")
        chain.append(item)
        current = item["parent"]
    chain.reverse()
    return str(base_object["tree"]), chain


def _index(root: Path) -> dict[str, tuple[str, str]]:
    for record in _git(root, "ls-files", "-v", "-z").split(b"\x00"):
        if record and record[:2] != b"H ":
            raise ProtocolError("unsafe index flag (assume-unchanged or skip-worktree)")
    result: dict[str, tuple[str, str]] = {}
    for record in _git(root, "ls-files", "--stage", "-z").split(b"\x00"):
        if not record:
            continue
        info, raw_path = record.split(b"\t", 1)
        mode, oid, stage = info.decode("ascii").split(" ")
        if stage != "0" or mode not in ("100644", "100755", "120000"):
            raise ProtocolError("unmerged or unsupported Git index entry")
        result[raw_path.decode("utf-8", "strict")] = (mode, oid)
    return result


def _physical_file(root: Path, path: str, algorithm: str) -> tuple[str, str, str]:
    """Read one file by directory descriptors, never following a symlink."""
    parts = PurePosixPath(path).parts
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
            os.close(directory)
            directory = child
        leaf = parts[-1]
        before = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            content = os.fsencode(os.readlink(leaf, dir_fd=directory))
            mode = "120000"
        elif stat.S_ISREG(before.st_mode):
            descriptor = os.open(leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
            try:
                observed = os.fstat(descriptor)
                if not stat.S_ISREG(observed.st_mode) or observed.st_size > 128 * 1024 * 1024:
                    raise ProtocolError(f"nonregular or oversized candidate: {path}")
                chunks = []
                while True:
                    part = os.read(descriptor, 1024 * 1024)
                    if not part:
                        break
                    chunks.append(part)
                content = b"".join(chunks)
                after = os.fstat(descriptor)
                if len(content) != observed.st_size or (observed.st_size, observed.st_mtime_ns, observed.st_mode) != (after.st_size, after.st_mtime_ns, after.st_mode):
                    raise ProtocolError(f"candidate changed during read: {path}")
                mode = "100755" if observed.st_mode & stat.S_IXUSR else "100644"
            finally:
                os.close(descriptor)
        else:
            raise ProtocolError(f"nonregular candidate: {path}")
        blob = f"blob {len(content)}\0".encode() + content
        oid = hashlib.new(algorithm, blob).hexdigest()
        return mode, oid, hashlib.sha256(content).hexdigest()
    finally:
        os.close(directory)


def _physical(root: Path, algorithm: str) -> dict[str, tuple[str, str, str]]:
    result: dict[str, tuple[str, str, str]] = {}
    def inventory_error(error: OSError) -> None:
        raise ProtocolError(f"physical inventory directory unreadable: {error.filename}") from error

    for current, dirs, files in os.walk(root, followlinks=False, onerror=inventory_error):
        relative = Path(current).relative_to(root)
        if relative == Path("."):
            dirs[:] = [d for d in dirs if d != ".git"]
            files = [f for f in files if f != ".git"]
        for name in list(dirs):
            if (Path(current) / name).is_symlink():
                dirs.remove(name)
                files.append(name)
        for name in files:
            path = (relative / name).as_posix()
            _safe_path(path)
            try:
                result[path] = _physical_file(root, path, algorithm)
            except OSError as exc:
                raise ProtocolError(f"physical inventory file unreadable: {path}") from exc
    return result


def _commit_history(root: Path, base: str, head: str, grants: list[str], algorithm: str, budget: _ObjectBudget, checkpoint_pins: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Inventory every linear commit, including paths later removed from HEAD."""
    history: list[dict[str, Any]] = []
    base_tree_oid, chain = _raw_chain(root, base, head, budget)
    previous_commit = base
    previous_tree = _tree(root, base_tree_oid, algorithm, budget)
    all_evidence = {e["path"] for pin in checkpoint_pins.values() for e in pin["evidence_files"]}
    for commit_object in chain:
        if commit_object["parent"] != previous_commit:
            raise ProtocolError("nonlinear or unexpected committed history")
        commit = str(commit_object["commit"])
        current_tree = _tree(root, str(commit_object["tree"]), algorithm, budget)
        changed = sorted(path for path in set(previous_tree) | set(current_tree) if previous_tree.get(path) != current_tree.get(path))
        if checkpoint_pins and (set(changed) & set(_CONTROLLER_CHECKPOINT_PATHS)) != set(_CONTROLLER_CHECKPOINT_PATHS):
            raise ProtocolError("every candidate commit requires both controller checkpoint paths")
        pin = checkpoint_pins.get(commit)
        if checkpoint_pins and pin is None:
            raise ProtocolError("candidate commit has no controller checkpoint pin")
        evidence_pins = {e["path"]: e["sha256"] for e in pin["evidence_files"]} if pin else {}
        if set(changed) & all_evidence != set(evidence_pins):
            raise ProtocolError("controller checkpoint evidence differs from committed history")
        entries: list[dict[str, str]] = []
        for path in changed:
            _safe_path(path)
            if path not in _CONTROLLER_CHECKPOINT_PATHS and path not in evidence_pins and not any(_allowed(grant, path) for grant in grants):
                raise ProtocolError(f"forbidden committed history path: {path}")
            if path in _CONTROLLER_CHECKPOINT_PATHS and pin is None:
                raise ProtocolError(f"unadmitted controller checkpoint path: {path}")
            item: dict[str, str] = {"path": path, "status": "deleted" if path not in current_tree else "added" if path not in previous_tree else "modified"}
            if path in current_tree:
                mode, blob = current_tree[path]
                if mode not in ("100644", "100755"):
                    raise ProtocolError(f"symlink or nonregular committed history path: {path}")
                digest = hashlib.sha256(_verified_object(root, "blob", blob, algorithm, budget)).hexdigest()
                if path in _CONTROLLER_CHECKPOINT_PATHS and digest != pin["status_sha256" if path == _CONTROLLER_CHECKPOINT_PATHS[0] else "receipt_sha256"]:
                    raise ProtocolError(f"controller checkpoint pin mismatch: {path}")
                if path in evidence_pins and (path in previous_tree or digest != evidence_pins[path]):
                    raise ProtocolError(f"controller checkpoint evidence pin mismatch: {path}")
                item.update(mode=mode, sha256=digest)
            elif path in _CONTROLLER_CHECKPOINT_PATHS or path in evidence_pins:
                raise ProtocolError(f"controller checkpoint path deleted: {path}")
            entries.append(item)
        history.append({"commit": commit, "parent": previous_commit, "commit_object_sha256": commit_object["object_sha256"], "files": entries})
        previous_commit, previous_tree = commit, current_tree
    if previous_commit != head:
        raise ProtocolError("committed history does not reach pinned head")
    if checkpoint_pins and set(checkpoint_pins) != {item["commit"] for item in history}:
        raise ProtocolError("controller checkpoint pins differ from committed history")
    return history


def _inventory(root: Path, base: str, head: str, grants: list[str], checkpoint_pins: dict[str, dict[str, Any]]) -> tuple[list[dict[str, str]], bool, list[dict[str, Any]]]:
    algorithm = _git(root, "rev-parse", "--show-object-format").decode()
    if algorithm not in ("sha1", "sha256"):
        raise ProtocolError("unsupported Git object format")
    budget = _ObjectBudget()
    base_tree_oid, chain = _raw_chain(root, base, head, budget)
    base_tree, head_tree, index = _tree(root, base_tree_oid, algorithm, budget), _tree(root, str(chain[-1]["tree"]) if chain else base_tree_oid, algorithm, budget), _index(root)
    physical = _physical(root, algorithm)
    disk_objects = {path: value[:2] for path, value in physical.items()}
    paths = set(base_tree) | set(head_tree) | set(index) | set(physical)
    if len({_path_key(path) for path in paths}) != len(paths):
        raise ProtocolError("case or Unicode path collision")
    changed = sorted(path for path in paths if any(tree.get(path) != base_tree.get(path) for tree in (head_tree, index, disk_objects)))
    if not changed:
        raise ProtocolError("empty candidate")
    evidence_pins = {e["path"]: e["sha256"] for pin in checkpoint_pins.values() for e in pin["evidence_files"]}
    files: list[dict[str, str]] = []
    for path in changed:
        _safe_path(path)
        if path not in _CONTROLLER_CHECKPOINT_PATHS and path not in evidence_pins and not any(_allowed(grant, path) for grant in grants):
            raise ProtocolError(f"forbidden candidate path: {path}")
        if path in _CONTROLLER_CHECKPOINT_PATHS and not checkpoint_pins:
            raise ProtocolError(f"unadmitted controller checkpoint path: {path}")
        if path in physical and path not in index and (path in base_tree or path in head_tree):
            raise ProtocolError(f"index/worktree divergence at {path}")
        if path in index and path not in physical and index[path] != base_tree.get(path):
            raise ProtocolError(f"index/worktree divergence at {path}")
        if path in physical and physical[path][0] == "120000":
            raise ProtocolError(f"symlink candidate: {path}")
        current = physical.get(path)
        old = base_tree.get(path)
        if path in evidence_pins and (current is None or current[2] != evidence_pins[path]):
            raise ProtocolError(f"controller checkpoint evidence final pin mismatch: {path}")
        if path in _CONTROLLER_CHECKPOINT_PATHS:
            last_pin = checkpoint_pins.get(head)
            key = "status_sha256" if path == _CONTROLLER_CHECKPOINT_PATHS[0] else "receipt_sha256"
            if last_pin is None or current is None or current[2] != last_pin[key]:
                raise ProtocolError(f"controller checkpoint final pin mismatch: {path}")
        if (path in evidence_pins or path in _CONTROLLER_CHECKPOINT_PATHS) and index.get(path) != head_tree.get(path):
            raise ProtocolError(f"controller checkpoint staged pin mismatch: {path}")
        item = {"path": path, "status": "added" if old is None else "deleted" if current is None else "modified"}
        if current:
            item.update(sha256=current[2], mode=current[0])
        elif old:
            item["sha256"] = hashlib.sha256(_verified_object(root, "blob", old[1], algorithm, budget)).hexdigest()
        if path in head_tree:
            item["head_sha256"] = hashlib.sha256(_verified_object(root, "blob", head_tree[path][1], algorithm, budget)).hexdigest()
            item["head_mode"] = head_tree[path][0]
        if path in index:
            item["index_sha256"] = hashlib.sha256(_verified_object(root, "blob", index[path][1], algorithm, budget)).hexdigest()
            item["index_mode"] = index[path][0]
        files.append(item)
    return files, head_tree == index == disk_objects, _commit_history(root, base, head, grants, algorithm, budget, checkpoint_pins)


def _verify_controller_checkpoints(
    root: Path, history: list[dict[str, Any]], expected_validator_sha256: str,
    worktree_parent: Path,
) -> None:
    """Run exact trusted validator bytes with hardened Git ancestry on each commit."""
    validator = Path(__file__).resolve().with_name("verify-build-checkpoint.py")
    if _path_overlap(validator.resolve(strict=True), worktree_parent):
        raise ProtocolError("trusted checkpoint validator overlaps author worktrees")
    validator_bytes = validator.read_bytes()
    if hashlib.sha256(validator_bytes).hexdigest() != expected_validator_sha256:
        raise ProtocolError("trusted checkpoint validator identity differs")
    common_raw = Path(_git(root, "rev-parse", "--git-common-dir").decode())
    common = (root / common_raw).resolve() if not common_raw.is_absolute() else common_raw.resolve()
    def check_git_metadata() -> None:
        if os.path.lexists(common / "info" / "grafts") or os.path.lexists(common / "shallow"):
            raise ProtocolError("controller checkpoint Git graft or shallow state present")
    check_git_metadata()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_") and key not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
               GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0",
               PYTHONDONTWRITEBYTECODE="1", PYTHONSAFEPATH="1",
               GIT_CONFIG_COUNT="4",
               GIT_CONFIG_KEY_0="core.commitGraph", GIT_CONFIG_VALUE_0="false",
               GIT_CONFIG_KEY_1="core.fsmonitor", GIT_CONFIG_VALUE_1="false",
               GIT_CONFIG_KEY_2="log.showSignature", GIT_CONFIG_VALUE_2="false",
               GIT_CONFIG_KEY_3="core.untrackedCache", GIT_CONFIG_VALUE_3="false")
    for item in history:
        commit = item["commit"]
        expected_parents = [commit, item["parent"]]
        try:
            parents = subprocess.run(
                ["git", "--no-lazy-fetch", "--no-replace-objects", "-C", str(root),
                 "rev-list", "--parents", "-n", "1", commit],
                cwd=root, env=env, capture_output=True, timeout=_GIT_TIMEOUT_SECONDS,
                check=False,
            )
            if parents.returncode or parents.stdout.decode("ascii", "strict").split() != expected_parents:
                raise ProtocolError(f"controller checkpoint Git parent differs from verified history: {commit}")
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-", "--repo", str(root), "--commit", commit],
                input=validator_bytes, cwd=root, env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_GIT_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            raise ProtocolError(f"controller checkpoint validation unavailable for {commit}") from exc
        check_git_metadata()
        if result.returncode:
            raise ProtocolError(f"controller checkpoint validator rejected {commit}")


def capture_revision(manifest: dict[str, Any], author_team: str) -> dict[str, Any]:
    """Hash the live complete changed-file inventory, including untracked files."""
    validate_manifest(manifest, subject_team=author_team)
    if author_team not in _TEAMS:
        raise ProtocolError("unknown author team")
    entry = manifest["authors"][author_team]
    root = Path(entry["worktree"]).resolve(strict=True)
    base = manifest["base_commit"]
    head = entry.get("head_commit", base)
    checkpoint_pins = _controller_checkpoint_pins(entry)
    files, clean, history = _inventory(root, base, head, entry["allowed_paths"], checkpoint_pins)
    if (Path(entry["worktree"]).resolve(strict=True) != root or
            _git(root, "rev-parse", "HEAD").decode() != head or
            _git(root, "branch", "--show-current").decode() != entry["branch"] or
            (files, clean, history) != _inventory(root, base, head, entry["allowed_paths"], checkpoint_pins)):
        raise ProtocolError("candidate changed during revision capture")
    if checkpoint_pins:
        _verify_controller_checkpoints(
            root, history, manifest["checkpoint_validator_sha256"],
            Path(manifest["worktree_parent"]).resolve(strict=True),
        )
        if (files, clean, history) != _inventory(root, base, head, entry["allowed_paths"], checkpoint_pins):
            raise ProtocolError("candidate changed during checkpoint validation")
    validate_manifest(manifest, subject_team=author_team)
    revision = {"task_id": manifest["task_id"], "author_team": author_team, "branch": entry["branch"], "base_commit": base, "head_commit": head, "manifest_digest": _subject_manifest_digest(manifest, author_team), "files": files, "history": history, "committed_clean": clean and head != base}
    return {**revision, "revision_digest": _digest(revision)}


def _evidence_bytes(manifest: dict[str, Any], reference: Any, label: str) -> bytes:
    if not isinstance(reference, dict) or not isinstance(reference.get("path"), str) or not isinstance(reference.get("sha256"), str) or not _SHA256.fullmatch(reference["sha256"]):
        raise ProtocolError(f"invalid {label} evidence reference")
    raw = Path(reference["path"])
    if not raw.is_absolute():
        raise ProtocolError(f"{label} evidence path must be absolute")
    raw_root = Path(manifest["review_evidence_root"])
    try:
        relative = _lexical_path(str(raw)).relative_to(_lexical_path(str(raw_root)))
    except ValueError as exc:
        raise ProtocolError(f"{label} is outside the controller evidence root") from exc
    if not relative.parts or any(part in (".", "..") for part in relative.parts):
        raise ProtocolError(f"{label} evidence path is unsafe")
    path = raw.resolve(strict=True)
    evidence_root = Path(manifest["review_evidence_root"]).resolve(strict=True)
    if evidence_root not in path.parents:
        raise ProtocolError(f"{label} is outside the controller evidence root")
    approved_parent = Path(manifest["worktree_parent"]).resolve(strict=True)
    pinned_common = Path(manifest["repository_common_dir"]).resolve(strict=True)
    if (_path_overlap(approved_parent, path) or _path_overlap(approved_parent, _lexical_path(str(raw))) or
            _path_overlap(pinned_common, path) or _path_overlap(pinned_common, _lexical_path(str(raw)))):
        raise ProtocolError(f"{label} cannot be under the author worktree parent")
    for entry in manifest["authors"].values():
        author_root = Path(entry["worktree"]).resolve(strict=False)
        if author_root == path or author_root in path.parents or author_root == raw or author_root in raw.parents:
            raise ProtocolError(f"{label} evidence cannot be inside an author worktree")
    try:
        directory = os.open(evidence_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ProtocolError(f"invalid {label} evidence root") from exc
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(relative.parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > 16 * 1024 * 1024:
                raise ProtocolError(f"invalid {label} evidence file")
            content = os.read(descriptor, before.st_size + 1)
            after = os.fstat(descriptor)
            if len(content) != before.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ProtocolError(f"{label} evidence changed during read")
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ProtocolError(f"invalid {label} evidence path") from exc
    finally:
        os.close(directory)
    if hashlib.sha256(content).hexdigest() != reference["sha256"]:
        raise ProtocolError(f"{label} evidence SHA-256 mismatch")
    return content


def validate_receipt(manifest: dict[str, Any], author_team: str, receipt: Any) -> dict[str, Any]:
    """Require an opposite-team review and checks bound to the current bytes."""
    candidate = capture_revision(manifest, author_team)
    if not isinstance(receipt, dict):
        raise ProtocolError("invalid review receipt")
    for key in ("task_id", "author_team", "base_commit", "head_commit", "manifest_digest", "revision_digest"):
        if receipt.get(key) != candidate[key]:
            raise ProtocolError(f"review {key} or revision drift")
    opposite = "claude" if author_team == "codex" else "codex"
    if receipt.get("reviewer_team") != opposite:
        raise ProtocolError("wrong reviewer team")
    if not isinstance(receipt.get("reviewer_run_id"), str) or not receipt["reviewer_run_id"].strip():
        raise ProtocolError("missing reviewer run ID")
    if receipt.get("decision") not in ("accepted", "rejected") or not isinstance(receipt.get("findings"), list):
        raise ProtocolError("invalid review decision or findings")
    if receipt["decision"] == "rejected" and not receipt["findings"]:
        raise ProtocolError("rejected review requires findings")
    if any(not isinstance(finding, dict) or not isinstance(finding.get("reason"), str) or not finding["reason"].strip() for finding in receipt["findings"]):
        raise ProtocolError("invalid review finding")
    if not isinstance(receipt.get("reviewed_at"), str) or not receipt["reviewed_at"].strip():
        raise ProtocolError("missing review time")
    checks = receipt.get("checks")
    required = manifest["required_checks"]
    if not isinstance(checks, list) or len(checks) != len(required):
        raise ProtocolError("missing or extra review checks")
    by_command: dict[str, dict[str, Any]] = {}
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("command"), str) or check["command"] in by_command:
            raise ProtocolError("duplicate or invalid review check")
        if type(check.get("exit_code")) is not int:
            raise ProtocolError("invalid review check exit")
        if not isinstance(check.get("evidence_sha256"), str) or not _SHA256.fullmatch(check["evidence_sha256"]):
            raise ProtocolError("invalid review check evidence hash")
        record_bytes = _evidence_bytes(manifest, {"path": check.get("evidence_path"), "sha256": check["evidence_sha256"]}, "check")
        try:
            record = json.loads(record_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProtocolError("invalid check record JSON") from exc
        expected_record = {key: candidate[key] for key in ("task_id", "author_team", "base_commit", "head_commit", "revision_digest")}
        expected_record.update(command=check["command"], exit_code=check["exit_code"])
        if not isinstance(record, dict) or any(_canonical(record.get(key)) != _canonical(value) for key, value in expected_record.items()):
            raise ProtocolError("check record does not bind this command, revision and exit")
        output_ref = {"path": record.get("output_path"), "sha256": record.get("output_sha256")}
        _evidence_bytes(manifest, output_ref, "check output")
        by_command[check["command"]] = check
    if set(by_command) != set(required):
        raise ProtocolError("review checks differ from admitted commands")
    if receipt["decision"] == "accepted" and (receipt["findings"] or any(c["exit_code"] != 0 for c in checks)):
        raise ProtocolError("accepted review contains findings or failed check")
    report_bytes = _evidence_bytes(manifest, receipt.get("reviewer_report"), "reviewer report")
    try:
        report = json.loads(report_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid reviewer report JSON") from exc
    required_report = {key: receipt[key] for key in ("task_id", "author_team", "reviewer_team", "reviewer_run_id", "base_commit", "head_commit", "revision_digest", "decision", "findings")}
    required_report["checks"] = [{"command": c["command"], "exit_code": c["exit_code"], "evidence_sha256": c["evidence_sha256"]} for c in checks]
    if not isinstance(report, dict) or any(_canonical(report.get(key)) != _canonical(value) for key, value in required_report.items()):
        raise ProtocolError("reviewer report does not bind this revision, reviewer, checks and decision")
    run_bytes = _evidence_bytes(manifest, receipt.get("reviewer_run_record"), "reviewer run")
    try:
        run_record = json.loads(run_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid reviewer run record JSON") from exc
    expected_run = {key: receipt[key] for key in ("task_id", "author_team", "reviewer_team", "reviewer_run_id", "base_commit", "head_commit", "revision_digest")}
    expected_run.update(provider="anthropic" if opposite == "claude" else "openai", reviewer_report_sha256=receipt["reviewer_report"]["sha256"], check_record_sha256s=[c["evidence_sha256"] for c in checks], run_status="terminal_success")
    if not isinstance(run_record, dict) or any(_canonical(run_record.get(key)) != _canonical(value) for key, value in expected_run.items()):
        raise ProtocolError("reviewer run record does not bind provider, report, checks and revision")
    return candidate


def prepare_pr_packet(manifest: dict[str, Any], author_team: str, receipt: dict[str, Any]) -> dict[str, Any]:
    """Build a local exact-byte packet; publication remains a separate decision."""
    candidate = validate_receipt(manifest, author_team, receipt)
    if receipt["decision"] != "accepted":
        raise ProtocolError("rejected review cannot prepare PR packet")
    if not candidate["committed_clean"]:
        raise ProtocolError("PR packet requires committed clean HEAD with reviewed bytes")
    if capture_revision(manifest, author_team)["revision_digest"] != candidate["revision_digest"]:
        raise ProtocolError("candidate revision changed before PR packet")
    packet = {
        "schema_version": 1,
        **candidate,
        "reviewer_team": receipt["reviewer_team"],
        "review_receipt_sha256": _digest(receipt),
        "checks": [{"command": c["command"], "exit_code": c["exit_code"], "evidence_sha256": c["evidence_sha256"]} for c in receipt["checks"]],
        "publication_authorized": False,
        "merge_authorized": False,
    }
    return {**packet, "packet_sha256": _digest(packet)}


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate-manifest", "capture", "validate-review", "prepare-pr"))
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--author", choices=sorted(_TEAMS))
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    try:
        exit_status = 0
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        if args.action == "validate-manifest":
            result = validate_manifest(manifest)
        else:
            if not args.author:
                raise ProtocolError("--author is required")
            if args.action == "capture":
                result = capture_revision(manifest, args.author)
            else:
                if not args.receipt:
                    raise ProtocolError("--receipt is required")
                receipt = json.loads(args.receipt.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
                if not isinstance(receipt, dict):
                    raise ProtocolError("review receipt must be a JSON object")
                if args.action == "validate-review":
                    result = {"decision": receipt.get("decision"), "candidate": validate_receipt(manifest, args.author, receipt)}
                    exit_status = 3 if receipt["decision"] == "rejected" else 0
                else:
                    result = prepare_pr_packet(manifest, args.author, receipt)
        print(json.dumps(result, sort_keys=True, indent=2))
        return exit_status
    except (ValueError, OSError, UnicodeError, RuntimeError, AttributeError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"cross-review protocol rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
