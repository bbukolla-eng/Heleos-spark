#!/usr/bin/env python3
"""Cooperative lifecycle guard for one build worker, test or integration at a time.

BUILD-WORKER-LIFECYCLE-GUARD-1 (LG01-LG07). Standard library only.

The guard keeps a durable SQLite registry under the canonical Git common
directory (``<git-common-dir>/heleos-run-guard/registry.sqlite3``), so every
worktree of one repository shares a single active slot. A run is reserved in a
``BEGIN IMMEDIATE`` transaction, with a durable ``active_slot`` row, *before*
any command is spawned. The slot is released only by a certain terminal outcome
or by explicit, evidence-backed reconciliation.

Commands (all print one JSON object on stdout)::

    build_run_guard.py [--repo PATH] status [--task-id ID]
    build_run_guard.py [--repo PATH] run --spec SPEC.json
    build_run_guard.py [--repo PATH] request-cancel --task-id ID --operator NAME [--wait SECONDS]
    build_run_guard.py [--repo PATH] reconcile --task-id ID --operator NAME \\
        --evidence INCIDENT.json --evidence-sha256 HEX
    build_run_guard.py [--repo PATH] integrate --manifest MANIFEST.json [--manifest-sha256 HEX]

Run spec (JSON object, no other keys)::

    {"task_id": "...", "kind": "worker" | "test", "expected_head": "<40 hex>",
     "argv": ["program", "arg", ...], "cwd": "relative/dir or .",
     "inputs": {"relative/path": "<sha256>", ...},
     "allowed_paths": ["relative/path", ...],
     "timeout_seconds": <positive number, or null for worker>, "max_output_bytes": <positive int>,
     "predecessor": "<failed task id>" (optional),
     "changed_condition": "<what materially changed>" (required with predecessor),
     "description": "..." (optional)}

The HEAD, cwd and every input hash are checked against current bytes inside
the reservation transaction. ``argv`` is executed without a shell. A task ID is
bound to the fingerprint of its spec: the same ID with a changed spec is
refused, the same active ID returns the existing record, and the same terminal
ID never executes again. A new ID repeating the command of an earlier run must
name a failed predecessor and a non-empty changed condition; the predecessor
record is kept.

Integration manifest (JSON object)::

    {"task_id": "...", "expected_head": "<40 hex>",
     "candidate_root": "/absolute/candidate/dir", "candidate_author": "...",
     "allowed_paths": ["relative/path", ...],
     "files": [{"path": "rel", "before_sha256": "<hex>" | null,
                "after_sha256": "<hex>"}, ...],
     "review": {"path": "/absolute/review.json", "sha256": "<hex>",
                "reviewer": "..."}}

The review file must be outside the candidate root, must be written by a
reviewer other than ``candidate_author`` and must contain
``{"task_id", "decision": "accepted", "reviewer", "expected_head",
"before_files": {path: before_sha256}, "files": {path: after_sha256}}``
covering exactly the manifest files. Every path, hash, HEAD and review is
validated before the slot is claimed and again inside the claim transaction,
before any destination write. Files are replaced atomically one at a time with
a durable per-file journal; an interrupted integration stays uncertain and its
ID can never be replayed.

Incident evidence for ``reconcile`` is a JSON file outside the registry
directory, pinned by SHA-256::

    {"schema": "heleos.run-guard-incident/v1", "task_id": "...",
     "attested_by": "<not the operator>", "no_unrecorded_survivors": true,
     "investigation": "<what was checked>"}

Reconciliation is refused while the recorded supervisor, child, child process
group or any recorded descendant is still live.

Scope and limits (read before relying on this guard):

* Cooperative only. The guard serializes callers that use it. It does not stop
  an editor, shell or agent that ignores it, and it does not replace the
  contained worker runner's sandbox or egress controls.
* The command runs in a new session/process group owned by the live
  supervisor. Descendants are discovered by periodic ``ps`` scans (child tree
  and same-group members) and recorded with a start-time marker. On timeout,
  cancellation or a catchable signal (SIGTERM, SIGINT, SIGHUP) the live
  supervisor signals its own group and the recorded descendants whose start
  marker still matches, including nested groups (for example an outer runner
  that starts an inner Claude process in another group). Signalling the outer
  group alone is not assumed to clean a nested group.
* A descendant that leaves the tree and its group before a scan observes it is
  not known to the guard. If such a process keeps the output pipe open, the run
  is left ``needs_reconciliation`` rather than reported terminal. Arbitrary
  session-escaping descendants are not claimed to be contained.
* SIGKILL of the supervisor cannot run cleanup. The record then stays
  ``reserved``/``running``/``integrating`` with an active slot; ``status``
  reports it as uncertain. The guard never signals a persisted PID or group
  after a restart, and a missing PID never frees the slot. Only ``reconcile``
  with pinned independent incident evidence clears it.
* Process identity uses PID plus the ``ps`` ``lstart`` start marker (one
  second resolution). PID reuse within the same second is not distinguished.
* The worker scope check compares path-level ``git status`` before and after
  the command; it is an observation of the invoking checkout, not a sandbox.

Exit codes: 0 success/idle, 1 internal error, 2 invalid input, 3 slot busy or
same task already active, 4 refused (replay, changed spec, stale or changed
bytes, live processes), 5 uncertain state requiring reconciliation, 6 command
finished failed, timed out or cancelled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INVALID = 2
EXIT_BUSY = 3
EXIT_REFUSED = 4
EXIT_UNCERTAIN = 5
EXIT_COMMAND_FAILED = 6

RUNTIME_DIR_NAME = "heleos-run-guard"
REGISTRY_NAME = "registry.sqlite3"
INCIDENT_SCHEMA = "heleos.run-guard-incident/v1"

ACTIVE_STATES = ("reserved", "running", "integrating", "needs_reconciliation")
TERMINAL_STATES = ("succeeded", "failed", "timed_out", "cancelled", "integrated", "reconciled")
RETRYABLE_STATES = ("failed", "timed_out", "cancelled", "reconciled")
RUN_KINDS = ("worker", "test")

MAX_TIMEOUT_SECONDS = 7 * 24 * 3600
MAX_OUTPUT_BYTES = 1 << 30
MAX_WAIT_SECONDS = 3600
POLL_SECONDS = 0.1
SCAN_SECONDS = 0.25
TERM_GRACE_SECONDS = 3.0
KILL_GRACE_SECONDS = 2.0
PIPE_DRAIN_SECONDS = 2.0

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
PROTECTED_COMPONENTS = frozenset({".git", ".heleos"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    task_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    command_fingerprint TEXT,
    spec_json TEXT NOT NULL,
    state TEXT NOT NULL,
    outcome TEXT,
    predecessor TEXT,
    changed_condition TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    supervisor_pid INTEGER,
    supervisor_start TEXT,
    child_pid INTEGER,
    child_pgid INTEGER,
    child_start TEXT,
    exit_code INTEGER,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    cancel_requested_by TEXT,
    stdout_path TEXT,
    stderr_path TEXT,
    stdout_bytes INTEGER,
    stderr_bytes INTEGER,
    stdout_truncated INTEGER,
    stderr_truncated INTEGER,
    result_json TEXT
);
CREATE TABLE IF NOT EXISTS active_slot (
    slot INTEGER PRIMARY KEY CHECK (slot = 1),
    task_id TEXT NOT NULL UNIQUE REFERENCES runs(task_id),
    claimed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processes (
    task_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    ppid INTEGER,
    pgid INTEGER,
    start_marker TEXT,
    role TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (task_id, pid, start_marker)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    at TEXT NOT NULL,
    event TEXT NOT NULL,
    detail_json TEXT
);
CREATE TABLE IF NOT EXISTS journal (
    task_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    path TEXT NOT NULL,
    before_sha256 TEXT,
    after_sha256 TEXT NOT NULL,
    step TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (task_id, seq, step)
);
CREATE TRIGGER IF NOT EXISTS runs_identity_immutable
BEFORE UPDATE OF task_id, kind, fingerprint, command_fingerprint, spec_json ON runs
BEGIN SELECT RAISE(ABORT, 'run identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS runs_terminal_immutable
BEFORE UPDATE ON runs
WHEN OLD.state IN ('succeeded', 'failed', 'timed_out', 'cancelled', 'integrated', 'reconciled')
BEGIN SELECT RAISE(ABORT, 'terminal run records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_delete
BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'run records are never deleted'); END;
"""


class GuardError(Exception):
    """Structured refusal carried to the CLI as JSON and an exit code."""

    def __init__(self, code: int, reason: str, **detail):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.detail = detail


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------
# Repository context


class Context:
    def __init__(self, top: Path, common: Path):
        self.top = top
        self.common = common
        self.runtime = common / RUNTIME_DIR_NAME


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise GuardError(EXIT_INVALID, "git_failed", args=list(args), stderr=result.stderr.strip())
    return result.stdout.strip()


def resolve_context(repo: str) -> Context:
    base = Path(repo).resolve()
    top = Path(git(base, "rev-parse", "--show-toplevel")).resolve()
    common = Path(git(base, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = base / common
    return Context(top, common.resolve())


def current_head(ctx: Context) -> str:
    return git(ctx.top, "rev-parse", "--verify", "HEAD")


def git_dirty_paths(top: Path) -> set:
    raw = subprocess.run(
        ["git", "-C", str(top), "status", "--porcelain=v1", "-z", "-uall"],
        capture_output=True,
    )
    if raw.returncode != 0:
        raise GuardError(EXIT_ERROR, "git_status_failed", stderr=raw.stderr.decode(errors="replace"))
    tokens = raw.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    paths = set()
    skip = False
    for token in tokens:
        if skip:
            skip = False
            paths.add(token)
            continue
        if len(token) < 4:
            continue
        xy, path = token[:2], token[3:]
        paths.add(path)
        if "R" in xy or "C" in xy:
            skip = True
    return paths


# --------------------------------------------------------------------------
# Path and value validation


def safe_relpath(value, what: str, allow_dot: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise GuardError(EXIT_INVALID, "invalid_path", field=what, value=value)
    if allow_dot and value == ".":
        return value
    if "\0" in value or "\\" in value or value.startswith("/"):
        raise GuardError(EXIT_INVALID, "unsafe_path", field=what, value=value)
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise GuardError(EXIT_INVALID, "unsafe_path", field=what, value=value)
    if any(part in PROTECTED_COMPONENTS for part in parts):
        raise GuardError(EXIT_INVALID, "protected_path", field=what, value=value)
    return value


def within(path: str, prefixes) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def walk_no_symlink(root: Path, rel: str, must_exist: bool):
    """lstat each component of root/rel; refuse symlinks and non-directory parents.

    Returns the final lstat result, or None when a component is absent and
    must_exist is false.
    """
    current = root
    parts = rel.split("/")
    result = None
    for index, part in enumerate(parts):
        current = current / part
        try:
            result = os.lstat(current)
        except FileNotFoundError:
            if must_exist:
                raise GuardError(EXIT_REFUSED, "path_absent", root=str(root), path=rel)
            return None
        if stat.S_ISLNK(result.st_mode):
            raise GuardError(EXIT_INVALID, "symlink_in_path", root=str(root), path=rel)
        if index < len(parts) - 1 and not stat.S_ISDIR(result.st_mode):
            raise GuardError(EXIT_INVALID, "non_directory_parent", root=str(root), path=rel)
    return result


def read_regular(root: Path, rel: str) -> tuple:
    st = walk_no_symlink(root, rel, must_exist=True)
    if not stat.S_ISREG(st.st_mode):
        raise GuardError(EXIT_INVALID, "not_regular_file", root=str(root), path=rel)
    with open(root / rel, "rb") as handle:
        return handle.read(), st


def positive_number(value, what: str, limit, integer: bool):
    if isinstance(value, bool) or not isinstance(value, (int,) if integer else (int, float)):
        raise GuardError(EXIT_INVALID, "invalid_number", field=what, value=value)
    if not (value > 0) or value > limit:
        raise GuardError(EXIT_INVALID, "number_out_of_range", field=what, value=value, limit=limit)
    return value


def require_task_id(value, what: str = "task_id") -> str:
    if not isinstance(value, str) or not TASK_ID_RE.match(value):
        raise GuardError(EXIT_INVALID, "invalid_task_id", field=what, value=value)
    return value


def require_sha(value, what: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.match(value):
        raise GuardError(EXIT_INVALID, "invalid_sha256", field=what, value=value)
    return value


def require_head(value) -> str:
    if not isinstance(value, str) or not HEAD_RE.match(value):
        raise GuardError(EXIT_INVALID, "invalid_expected_head", value=value)
    return value


def require_text(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise GuardError(EXIT_INVALID, "invalid_text", field=what)
    return value


def exact_keys(raw, required: set, optional: set, what: str):
    if not isinstance(raw, dict):
        raise GuardError(EXIT_INVALID, "not_an_object", field=what)
    missing = sorted(required - raw.keys())
    unknown = sorted(raw.keys() - required - optional)
    if missing or unknown:
        raise GuardError(EXIT_INVALID, "invalid_keys", field=what, missing=missing, unknown=unknown)


def load_json_file(path: str, what: str, expected_sha: str | None = None):
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise GuardError(EXIT_INVALID, "unreadable_file", field=what, error=str(exc))
    if expected_sha is not None and sha256_bytes(data) != expected_sha:
        raise GuardError(EXIT_INVALID, "file_hash_mismatch", field=what, actual=sha256_bytes(data))
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardError(EXIT_INVALID, "invalid_json", field=what, error=str(exc))


def validate_spec(raw) -> dict:
    required = {"task_id", "kind", "expected_head", "argv", "cwd", "inputs",
                "allowed_paths", "timeout_seconds", "max_output_bytes"}
    exact_keys(raw, required, {"predecessor", "changed_condition", "description"}, "spec")
    require_task_id(raw["task_id"])
    if raw["kind"] not in RUN_KINDS:
        raise GuardError(EXIT_INVALID, "invalid_kind", value=raw["kind"])
    require_head(raw["expected_head"])
    argv = raw["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and "\0" not in a for a in argv) \
            or not argv[0]:
        raise GuardError(EXIT_INVALID, "invalid_argv")
    safe_relpath(raw["cwd"], "cwd", allow_dot=True)
    inputs = raw["inputs"]
    if not isinstance(inputs, dict):
        raise GuardError(EXIT_INVALID, "invalid_inputs")
    for path, digest in inputs.items():
        safe_relpath(path, "inputs")
        require_sha(digest, "inputs[%s]" % path)
    allowed = raw["allowed_paths"]
    if not isinstance(allowed, list) or not allowed or len(set(allowed)) != len(allowed):
        raise GuardError(EXIT_INVALID, "invalid_allowed_paths")
    for path in allowed:
        safe_relpath(path, "allowed_paths")
    if raw["timeout_seconds"] is not None or raw["kind"] != "worker":
        positive_number(raw["timeout_seconds"], "timeout_seconds", MAX_TIMEOUT_SECONDS, integer=False)
    positive_number(raw["max_output_bytes"], "max_output_bytes", MAX_OUTPUT_BYTES, integer=True)
    predecessor = raw.get("predecessor")
    condition = raw.get("changed_condition")
    if predecessor is not None:
        require_task_id(predecessor, "predecessor")
        if predecessor == raw["task_id"]:
            raise GuardError(EXIT_INVALID, "self_predecessor")
        require_text(condition, "changed_condition")
    elif condition is not None:
        raise GuardError(EXIT_INVALID, "changed_condition_without_predecessor")
    if "description" in raw and not isinstance(raw["description"], str):
        raise GuardError(EXIT_INVALID, "invalid_description")
    return raw


def command_part(spec: dict) -> dict:
    return {key: value for key, value in spec.items()
            if key not in ("task_id", "predecessor", "changed_condition", "description")}


def check_run_inputs(spec: dict, ctx: Context) -> None:
    head = current_head(ctx)
    if head != spec["expected_head"]:
        raise GuardError(EXIT_REFUSED, "stale_head", expected=spec["expected_head"], actual=head)
    if spec["cwd"] != ".":
        st = walk_no_symlink(ctx.top, spec["cwd"], must_exist=True)
        if not stat.S_ISDIR(st.st_mode):
            raise GuardError(EXIT_INVALID, "cwd_not_directory", cwd=spec["cwd"])
    mismatched = []
    for path, digest in sorted(spec["inputs"].items()):
        try:
            data, _ = read_regular(ctx.top, path)
        except GuardError as exc:
            mismatched.append({"path": path, "reason": exc.reason})
            continue
        actual = sha256_bytes(data)
        if actual != digest:
            mismatched.append({"path": path, "expected": digest, "actual": actual})
    if mismatched:
        raise GuardError(EXIT_REFUSED, "input_mismatch", inputs=mismatched)


# --------------------------------------------------------------------------
# Process observation (read-only)


def _ps(args) -> str:
    result = subprocess.run(["ps", *args], capture_output=True, text=True,
                            env={**os.environ, "LC_ALL": "C", "LANG": "C"})
    if result.returncode != 0 and not ("-p" in args and result.returncode == 1 and not result.stderr.strip()):
        raise GuardError(EXIT_UNCERTAIN, "process_observation_failed", exit_code=result.returncode)
    return result.stdout


def start_marker(pid: int):
    out = _ps(["-o", "stat=,lstart=", "-p", str(pid)]).strip()
    if not out:
        return None
    parts = out.split(None, 1)
    return parts[1].strip() if len(parts) == 2 else None


def identity_state(pid, marker) -> str:
    """Observe a recorded identity without signalling it (signal 0 only)."""
    if pid is None:
        return "not_recorded"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "absent"
    except PermissionError:
        pass
    out = _ps(["-o", "stat=,lstart=", "-p", str(pid)]).strip()
    if not out:
        return "unverifiable_live"
    parts = out.split(None, 1)
    if parts[0].startswith("Z"):
        return "zombie"
    if marker is None:
        return "unverifiable_live"
    current = parts[1].strip() if len(parts) == 2 else None
    return "live" if current == marker else "absent_reused"


def group_state(pgid) -> str:
    if not pgid:
        return "not_recorded"
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return "absent"
    except PermissionError:
        return "present"
    return "present"


def process_table() -> dict:
    table = {}
    for line in _ps(["-A", "-o", "pid=,ppid=,pgid=,stat=,lstart="]).splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid, ppid, pgid = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        table[pid] = (ppid, pgid, parts[3], parts[4].strip())
    return table


LIVE_STATES = ("live", "unverifiable_live")


# --------------------------------------------------------------------------
# Registry


def open_db(runtime: Path, readonly: bool = False):
    db = runtime / REGISTRY_NAME
    if readonly:
        if not db.exists():
            return None
        conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=30, isolation_level=None)
    else:
        runtime.mkdir(mode=0o700, exist_ok=True)
        conn = sqlite3.connect(str(db), timeout=30, isolation_level=None)
        conn.execute("PRAGMA synchronous=FULL")
        conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


class Tx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("COMMIT" if exc_type is None else "ROLLBACK")
        return False


def add_event(conn, task_id: str, name: str, **detail) -> None:
    conn.execute("INSERT INTO events (task_id, at, event, detail_json) VALUES (?, ?, ?, ?)",
                 (task_id, now(), name, canonical_json(detail)))


def get_run(conn, task_id: str):
    return conn.execute("SELECT * FROM runs WHERE task_id = ?", (task_id,)).fetchone()


def active_run(conn):
    return conn.execute(
        "SELECT r.* FROM active_slot a JOIN runs r ON r.task_id = a.task_id").fetchone()


def describe(conn, row, observe: bool = True) -> dict:
    record = dict(row)
    record["spec"] = json.loads(record.pop("spec_json"))
    record["result"] = json.loads(record.pop("result_json") or "null")
    task_id = record["task_id"]
    record["processes"] = [dict(r) for r in conn.execute(
        "SELECT pid, ppid, pgid, start_marker, role, first_seen FROM processes "
        "WHERE task_id = ? ORDER BY first_seen, pid", (task_id,))]
    if record["kind"] == "integration":
        record["journal"] = [dict(r) for r in conn.execute(
            "SELECT seq, path, before_sha256, after_sha256, step, at FROM journal "
            "WHERE task_id = ? ORDER BY seq, at", (task_id,))]
    record["events"] = [dict(r) for r in conn.execute(
        "SELECT at, event, detail_json FROM events WHERE task_id = ? ORDER BY id", (task_id,))]
    record["active"] = record["state"] in ACTIVE_STATES
    if observe and record["active"]:
        observed = {
            "supervisor": identity_state(record["supervisor_pid"], record["supervisor_start"]),
            "child": identity_state(record["child_pid"], record["child_start"]),
            "child_group": group_state(record["child_pgid"]),
            "known": {str(p["pid"]): identity_state(p["pid"], p["start_marker"])
                      for p in record["processes"]},
        }
        record["observed"] = observed
        record["owner_live"] = observed["supervisor"] == "live"
        record["uncertain"] = record["state"] == "needs_reconciliation" or not record["owner_live"]
    return record


def refuse_existing(conn, task_id, fp) -> None:
    """A recorded task ID is never executed again: attach, refuse or report."""
    existing = get_run(conn, task_id)
    if existing is None:
        return
    if existing["fingerprint"] != fp:
        raise GuardError(EXIT_REFUSED, "spec_changed_for_task_id",
                         task_id=task_id, recorded_fingerprint=existing["fingerprint"],
                         new_fingerprint=fp)
    if existing["state"] in ACTIVE_STATES:
        record = describe(conn, existing)
        if record.get("uncertain"):
            raise GuardError(EXIT_UNCERTAIN, "active_uncertain", record=record)
        raise GuardError(EXIT_BUSY, "already_active", record=record)
    raise GuardError(EXIT_REFUSED, "terminal_replay", record=describe(conn, existing))


def claim(conn, task_id, kind, fp, command_fp, spec, precheck, sup_marker,
          predecessor=None, changed_condition=None) -> None:
    """Reserve the single active slot before anything is spawned or written."""
    with Tx(conn):
        refuse_existing(conn, task_id, fp)
        if predecessor is not None:
            prior = get_run(conn, predecessor)
            if prior is None:
                raise GuardError(EXIT_REFUSED, "predecessor_unknown", predecessor=predecessor)
            if prior["state"] not in RETRYABLE_STATES:
                raise GuardError(EXIT_REFUSED, "predecessor_not_failed", predecessor=predecessor,
                                 state=prior["state"])
        elif command_fp is not None:
            prior = conn.execute(
                "SELECT task_id, state FROM runs WHERE command_fingerprint = ? ORDER BY created_at",
                (command_fp,)).fetchall()
            if prior:
                raise GuardError(EXIT_REFUSED, "unlinked_retry",
                                 prior=[dict(r) for r in prior],
                                 required="predecessor and changed_condition")
        busy = active_run(conn)
        if busy is not None:
            raise GuardError(EXIT_BUSY, "slot_busy", active=describe(conn, busy))
        precheck()
        stamp = now()
        conn.execute(
            "INSERT INTO runs (task_id, kind, fingerprint, command_fingerprint, spec_json, state, "
            "predecessor, changed_condition, created_at, updated_at, supervisor_pid, supervisor_start) "
            "VALUES (?, ?, ?, ?, ?, 'reserved', ?, ?, ?, ?, ?, ?)",
            (task_id, kind, fp, command_fp, canonical_json(spec), predecessor, changed_condition,
             stamp, stamp, os.getpid(), sup_marker))
        conn.execute("INSERT INTO active_slot (slot, task_id, claimed_at) VALUES (1, ?, ?)",
                     (task_id, stamp))
        add_event(conn, task_id, "reserved", supervisor_pid=os.getpid(), fingerprint=fp)


# --------------------------------------------------------------------------
# Signals


class StopFlag:
    def __init__(self):
        self.reason = None

    def install(self):
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(signum, self._handle)
        return self

    def _handle(self, signum, _frame):
        if self.reason is None:
            self.reason = "signal:%s" % signal.Signals(signum).name


# --------------------------------------------------------------------------
# Supervised run


class Pump(threading.Thread):
    """Drain one pipe, keeping at most `limit` bytes on disk."""

    def __init__(self, pipe, path: Path, limit: int):
        super().__init__(daemon=True)
        self.pipe = pipe
        self.limit = limit
        self.handle = open(path, "wb")
        self.written = 0
        self.total = 0
        self.truncated = False

    def run(self):
        fd = self.pipe.fileno()
        try:
            while True:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                self.total += len(chunk)
                room = self.limit - self.written
                if room > 0:
                    part = chunk[:room]
                    self.handle.write(part)
                    self.handle.flush()
                    self.written += len(part)
                if len(chunk) > max(room, 0):
                    self.truncated = True
        finally:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()


class Supervisor:
    def __init__(self, conn, ctx: Context, spec: dict, stop: StopFlag):
        self.conn = conn
        self.ctx = ctx
        self.spec = spec
        self.task_id = spec["task_id"]
        self.stop = stop
        self.child = None
        self.pgid = None
        self.known = {}

    # -- bookkeeping

    def _record_process(self, pid, ppid, pgid, marker, role):
        with Tx(self.conn):
            self.conn.execute(
                "INSERT OR IGNORE INTO processes (task_id, pid, ppid, pgid, start_marker, role, first_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self.task_id, pid, ppid, pgid, marker, role, now()))
        self.known[pid] = (pgid, marker)

    def scan(self):
        """Record descendants of the child and members of its process group."""
        if self.child is None:
            return
        table = process_table()
        me = os.getpid()
        children = {}
        for pid, (ppid, _pgid, _st, _ls) in table.items():
            children.setdefault(ppid, []).append(pid)
        found = set()
        for pid, (_ppid, pgid, _st, _ls) in table.items():
            if pgid == self.pgid and pid != me:
                found.add(pid)
        stack = [pid for pid, (_pg, marker) in self.known.items()
                 if pid in table and table[pid][3] == marker]
        seen = set(stack)
        while stack:
            for kid in children.get(stack.pop(), ()):
                if kid not in seen and kid != me:
                    seen.add(kid)
                    stack.append(kid)
                    found.add(kid)
        for pid in sorted(found):
            ppid, pgid, _st, marker = table[pid]
            if pid in self.known and self.known[pid][1] == marker:
                continue
            self._record_process(pid, ppid, pgid, marker, "descendant")

    def cancel_requested(self) -> bool:
        row = self.conn.execute("SELECT cancel_requested FROM runs WHERE task_id = ?",
                                (self.task_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    # -- cleanup of live owned processes only

    def _signal_owned(self, signum):
        if group_state(self.pgid) == "present":
            try:
                os.killpg(self.pgid, signum)
            except (ProcessLookupError, PermissionError):
                pass
        for pid, (pgid, marker) in list(self.known.items()):
            if identity_state(pid, marker) != "live":
                continue
            try:
                os.kill(pid, signum)
            except (ProcessLookupError, PermissionError):
                pass
            if pgid == pid and pgid != self.pgid:
                try:
                    os.killpg(pgid, signum)
                except (ProcessLookupError, PermissionError):
                    pass

    def _remaining(self) -> dict:
        self.child.poll()
        remaining = {}
        if self.child.returncode is None:
            remaining["child"] = self.child.pid
        if group_state(self.pgid) == "present":
            remaining["child_group"] = self.pgid
        for pid, (_pgid, marker) in self.known.items():
            if pid == self.child.pid:
                continue
            state = identity_state(pid, marker)
            if state in LIVE_STATES:
                remaining[str(pid)] = state
        return remaining

    def terminate_all(self) -> dict:
        for signum, grace in ((signal.SIGTERM, TERM_GRACE_SECONDS), (signal.SIGKILL, KILL_GRACE_SECONDS)):
            self.scan()
            self._signal_owned(signum)
            deadline = time.monotonic() + grace
            next_scan = time.monotonic() + SCAN_SECONDS
            while time.monotonic() < deadline:
                if not self._remaining():
                    return {}
                if time.monotonic() >= next_scan:
                    self.scan()
                    self._signal_owned(signum)
                    next_scan = time.monotonic() + SCAN_SECONDS
                time.sleep(0.05)
        try:
            self.child.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return self._remaining()

    # -- finalization

    def _finish(self, state, outcome, result, exit_code=None, pumps=None):
        release = state in TERMINAL_STATES
        with Tx(self.conn):
            values = {
                "state": state, "outcome": outcome, "exit_code": exit_code,
                "result_json": canonical_json(result), "updated_at": now(),
            }
            if pumps:
                values.update({
                    "stdout_bytes": pumps[0].written, "stderr_bytes": pumps[1].written,
                    "stdout_truncated": int(pumps[0].truncated), "stderr_truncated": int(pumps[1].truncated),
                })
            assignments = ", ".join("%s = ?" % key for key in values)
            self.conn.execute("UPDATE runs SET %s WHERE task_id = ?" % assignments,
                              (*values.values(), self.task_id))
            if release:
                self.conn.execute("DELETE FROM active_slot WHERE task_id = ?", (self.task_id,))
            add_event(self.conn, self.task_id, state, outcome=outcome, released=release)
        record = describe(self.conn, get_run(self.conn, self.task_id))
        if state == "succeeded":
            code = EXIT_OK
        elif state == "needs_reconciliation":
            code = EXIT_UNCERTAIN
        else:
            code = EXIT_COMMAND_FAILED
        return code, {"ok": code == EXIT_OK, "record": record}

    def execute(self):
        out_dir = self.ctx.runtime / "runs" / self.task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        stdout_path, stderr_path = out_dir / "stdout.log", out_dir / "stderr.log"
        with Tx(self.conn):
            self.conn.execute("UPDATE runs SET stdout_path = ?, stderr_path = ?, updated_at = ? "
                              "WHERE task_id = ?", (str(stdout_path), str(stderr_path), now(), self.task_id))
        if self.stop.reason:
            return self._finish("cancelled", "cancelled", {"launched": False, "detail": self.stop.reason})
        dirty_before = git_dirty_paths(self.ctx.top) if self.spec["kind"] == "worker" else None
        cwd = self.ctx.top if self.spec["cwd"] == "." else self.ctx.top / self.spec["cwd"]
        try:
            self.child = subprocess.Popen(
                self.spec["argv"], cwd=str(cwd), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, close_fds=True)
        except OSError as exc:
            return self._finish("failed", "spawn_failed", {"launched": False, "error": str(exc)})
        try:
            return self._supervise(stdout_path, stderr_path, dirty_before)
        except BaseException as exc:  # never leave a live child with a false record
            remaining = self.terminate_all()
            return self._finish("needs_reconciliation", "supervisor_error",
                                {"error": repr(exc), "remaining": remaining},
                                exit_code=self.child.returncode)

    def _supervise(self, stdout_path, stderr_path, dirty_before):
        child = self.child
        self.pgid = child.pid
        marker = start_marker(child.pid)
        with Tx(self.conn):
            self.conn.execute(
                "UPDATE runs SET state = 'running', child_pid = ?, child_pgid = ?, child_start = ?, "
                "updated_at = ? WHERE task_id = ?", (child.pid, self.pgid, marker, now(), self.task_id))
            self.conn.execute(
                "INSERT OR IGNORE INTO processes (task_id, pid, ppid, pgid, start_marker, role, first_seen) "
                "VALUES (?, ?, ?, ?, ?, 'child', ?)",
                (self.task_id, child.pid, os.getpid(), self.pgid, marker, now()))
            add_event(self.conn, self.task_id, "launched", child_pid=child.pid, pgid=self.pgid)
        self.known[child.pid] = (self.pgid, marker)
        limit = self.spec["max_output_bytes"]
        pumps = [Pump(child.stdout, stdout_path, limit), Pump(child.stderr, stderr_path, limit)]
        for pump in pumps:
            pump.start()
        timeout = self.spec["timeout_seconds"]
        deadline = None if timeout is None else time.monotonic() + timeout
        next_scan = 0.0
        reason = None
        detail = None
        while True:
            if child.poll() is not None:
                break
            if self.stop.reason:
                reason, detail = "cancelled", self.stop.reason
                break
            if deadline is not None and time.monotonic() >= deadline:
                reason, detail = "timed_out", "timeout_seconds=%s" % self.spec["timeout_seconds"]
                break
            if time.monotonic() >= next_scan:
                self.scan()
                if self.cancel_requested():
                    reason, detail = "cancelled", "request-cancel"
                    break
                next_scan = time.monotonic() + SCAN_SECONDS
            time.sleep(POLL_SECONDS)
        result = {"launched": True, "detail": detail}
        if reason is None:
            self.scan()
            leftover = self._remaining()
            if leftover:
                result["survivors_after_exit"] = leftover
                remaining = self.terminate_all()
            else:
                remaining = {}
        else:
            remaining = self.terminate_all()
        exit_code = child.returncode
        result["remaining_after_cleanup"] = remaining
        for pump in pumps:
            pump.join(PIPE_DRAIN_SECONDS)
        undrained = [name for name, pump in zip(("stdout", "stderr"), pumps) if pump.is_alive()]
        if undrained:
            result["unrecorded_output_holder"] = undrained
        if reason is None:
            outcome = "succeeded" if exit_code == 0 else "failed"
        else:
            outcome = reason
        state = "failed" if outcome == "failed" else outcome
        if dirty_before is not None and outcome == "succeeded":
            new_paths = sorted(git_dirty_paths(self.ctx.top) - dirty_before)
            outside = [p for p in new_paths if not within(p.rstrip("/"), self.spec["allowed_paths"])]
            result["new_dirty_paths"] = new_paths
            if outside:
                result["outside_allowed_paths"] = outside
                outcome, state = "scope_violation", "failed"
        if remaining or undrained:
            state = "needs_reconciliation"
        return self._finish(state, outcome, result, exit_code=exit_code, pumps=pumps)


# --------------------------------------------------------------------------
# Integration


def validate_manifest(raw) -> dict:
    required = {"task_id", "expected_head", "candidate_root", "candidate_author",
                "allowed_paths", "files", "review"}
    exact_keys(raw, required, set(), "manifest")
    require_task_id(raw["task_id"])
    require_head(raw["expected_head"])
    root = raw["candidate_root"]
    if not isinstance(root, str) or not os.path.isabs(root) or "\0" in root:
        raise GuardError(EXIT_INVALID, "invalid_candidate_root", value=root)
    require_text(raw["candidate_author"], "candidate_author")
    allowed = raw["allowed_paths"]
    if not isinstance(allowed, list) or not allowed:
        raise GuardError(EXIT_INVALID, "invalid_allowed_paths")
    for path in allowed:
        safe_relpath(path, "allowed_paths")
    files = raw["files"]
    if not isinstance(files, list) or not files:
        raise GuardError(EXIT_INVALID, "invalid_files")
    seen = set()
    for entry in files:
        exact_keys(entry, {"path", "before_sha256", "after_sha256"}, set(), "files[]")
        path = safe_relpath(entry["path"], "files.path")
        if path in seen:
            raise GuardError(EXIT_INVALID, "duplicate_path", path=path)
        seen.add(path)
        if entry["before_sha256"] is not None:
            require_sha(entry["before_sha256"], "before_sha256")
        require_sha(entry["after_sha256"], "after_sha256")
        if not within(path, allowed):
            raise GuardError(EXIT_INVALID, "path_not_allowed", path=path)
    for a in seen:
        for b in seen:
            if a != b and b.startswith(a + "/"):
                raise GuardError(EXIT_INVALID, "file_directory_conflict", paths=[a, b])
    review = raw["review"]
    exact_keys(review, {"path", "sha256", "reviewer"}, set(), "review")
    if not isinstance(review["path"], str) or not os.path.isabs(review["path"]):
        raise GuardError(EXIT_INVALID, "invalid_review_path")
    require_sha(review["sha256"], "review.sha256")
    require_text(review["reviewer"], "review.reviewer")
    if review["reviewer"].strip() == raw["candidate_author"].strip():
        raise GuardError(EXIT_INVALID, "self_review", reviewer=review["reviewer"])
    return raw


def prevalidate_integration(manifest: dict, ctx: Context) -> list:
    """Check HEAD, review, candidate and destination bytes; no writes."""
    head = current_head(ctx)
    if head != manifest["expected_head"]:
        raise GuardError(EXIT_REFUSED, "stale_head", expected=manifest["expected_head"], actual=head)
    cand_root = Path(manifest["candidate_root"])
    try:
        cand_real = cand_root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise GuardError(EXIT_INVALID, "candidate_root_missing", root=str(cand_root))
    if not cand_real.is_dir() or cand_real == ctx.top or is_within(cand_real, ctx.common):
        raise GuardError(EXIT_INVALID, "invalid_candidate_root", root=str(cand_real))
    review_path = Path(manifest["review"]["path"])
    try:
        review_st = os.lstat(review_path)
    except OSError:
        raise GuardError(EXIT_REFUSED, "review_absent", path=str(review_path))
    if stat.S_ISLNK(review_st.st_mode) or not stat.S_ISREG(review_st.st_mode):
        raise GuardError(EXIT_INVALID, "review_not_regular_file", path=str(review_path))
    review_real = review_path.resolve()
    if is_within(review_real, cand_real) or is_within(review_real, cand_root):
        raise GuardError(EXIT_INVALID, "review_inside_candidate", path=str(review_path))
    review_bytes = review_path.read_bytes()
    if sha256_bytes(review_bytes) != manifest["review"]["sha256"]:
        raise GuardError(EXIT_REFUSED, "review_changed", actual=sha256_bytes(review_bytes))
    try:
        review = json.loads(review_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GuardError(EXIT_INVALID, "review_not_json")
    if not isinstance(review, dict) or review.get("decision") != "accepted" \
            or review.get("task_id") != manifest["task_id"] \
            or review.get("reviewer") != manifest["review"]["reviewer"]:
        raise GuardError(EXIT_REFUSED, "review_not_accepting")
    if review.get("expected_head") != manifest["expected_head"]:
        raise GuardError(EXIT_REFUSED, "review_base_mismatch")
    before_files = {e["path"]: e["before_sha256"] for e in manifest["files"]}
    if review.get("before_files") != before_files:
        raise GuardError(EXIT_REFUSED, "review_destination_mismatch")
    expected_files = {e["path"]: e["after_sha256"] for e in manifest["files"]}
    if review.get("files") != expected_files:
        reviewed = review.get("files") if isinstance(review.get("files"), dict) else {}
        raise GuardError(EXIT_REFUSED, "unreviewed_or_unlisted_path",
                         unreviewed=sorted(set(expected_files) - set(reviewed)),
                         unlisted=sorted(set(reviewed) - set(expected_files)),
                         changed=sorted(p for p in expected_files
                                        if p in reviewed and reviewed[p] != expected_files[p]))
    plan = []
    for entry in manifest["files"]:
        rel = entry["path"]
        data, cand_st = read_regular(cand_real, rel)
        if sha256_bytes(data) != entry["after_sha256"]:
            raise GuardError(EXIT_REFUSED, "candidate_changed", path=rel, actual=sha256_bytes(data))
        dest_st = walk_no_symlink(ctx.top, rel, must_exist=False)
        if dest_st is None:
            if entry["before_sha256"] is not None:
                raise GuardError(EXIT_REFUSED, "destination_absent", path=rel)
        else:
            if not stat.S_ISREG(dest_st.st_mode):
                raise GuardError(EXIT_INVALID, "destination_not_regular_file", path=rel)
            actual = sha256_bytes((ctx.top / rel).read_bytes())
            if entry["before_sha256"] is None:
                raise GuardError(EXIT_REFUSED, "destination_exists", path=rel, actual=actual)
            if actual != entry["before_sha256"]:
                raise GuardError(EXIT_REFUSED, "destination_changed", path=rel, actual=actual)
        plan.append({"path": rel, "before": entry["before_sha256"], "after": entry["after_sha256"],
                     "mode": stat.S_IMODE(dest_st.st_mode) if dest_st else stat.S_IMODE(cand_st.st_mode)})
    return plan


def journal_step(conn, task_id, seq, item, step):
    with Tx(conn):
        conn.execute(
            "INSERT INTO journal (task_id, seq, path, before_sha256, after_sha256, step, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, seq, item["path"], item["before"], item["after"], step, now()))


def apply_file(ctx: Context, cand_root: Path, item: dict) -> None:
    rel = item["path"]
    data, _ = read_regular(cand_root, rel)
    if sha256_bytes(data) != item["after"]:
        raise GuardError(EXIT_REFUSED, "candidate_changed", path=rel)
    current = ctx.top
    for part in rel.split("/")[:-1]:
        current = current / part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            os.mkdir(current, 0o755)
            st = os.lstat(current)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise GuardError(EXIT_INVALID, "unsafe_destination_parent", path=rel)
    dest_st = walk_no_symlink(ctx.top, rel, must_exist=False)
    actual = sha256_bytes((ctx.top / rel).read_bytes()) if dest_st is not None else None
    if actual != item["before"]:
        raise GuardError(EXIT_REFUSED, "destination_changed", path=rel, actual=actual)
    parent = (ctx.top / rel).parent
    fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".run-guard-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), item["mode"])
            os.fsync(handle.fileno())
        os.replace(tmp, ctx.top / rel)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dir_fd = os.open(str(parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def finish_integration(conn, task_id, state, outcome, result):
    release = state in TERMINAL_STATES
    with Tx(conn):
        conn.execute("UPDATE runs SET state = ?, outcome = ?, result_json = ?, updated_at = ? "
                     "WHERE task_id = ?", (state, outcome, canonical_json(result), now(), task_id))
        if release:
            conn.execute("DELETE FROM active_slot WHERE task_id = ?", (task_id,))
        add_event(conn, task_id, state, outcome=outcome, released=release)
    return describe(conn, get_run(conn, task_id))


def cmd_integrate(ctx: Context, args):
    expected = require_sha(args.manifest_sha256, "manifest_sha256") if args.manifest_sha256 else None
    manifest = validate_manifest(load_json_file(args.manifest, "manifest", expected))
    stop = StopFlag().install()
    task_id = manifest["task_id"]
    conn = open_db(ctx.runtime)
    refuse_existing(conn, task_id, fingerprint(manifest))  # replay is refused before byte checks
    prevalidate_integration(manifest, ctx)
    holder = {}

    def precheck():
        holder["plan"] = prevalidate_integration(manifest, ctx)

    claim(conn, task_id, "integration", fingerprint(manifest), None, manifest, precheck,
          start_marker(os.getpid()))
    plan = holder["plan"]
    with Tx(conn):
        conn.execute("UPDATE runs SET state = 'integrating', updated_at = ? WHERE task_id = ?",
                     (now(), task_id))
        for seq, item in enumerate(plan):
            conn.execute(
                "INSERT INTO journal (task_id, seq, path, before_sha256, after_sha256, step, at) "
                "VALUES (?, ?, ?, ?, ?, 'planned', ?)",
                (task_id, seq, item["path"], item["before"], item["after"], now()))
    cand_root = Path(manifest["candidate_root"]).resolve()
    touched = []
    try:
        for seq, item in enumerate(plan):
            if stop.reason:
                raise GuardError(EXIT_UNCERTAIN, "interrupted", signal=stop.reason)
            if current_head(ctx) != manifest["expected_head"]:
                raise GuardError(EXIT_UNCERTAIN, "head_changed_during_integration")
            journal_step(conn, task_id, seq, item, "writing")
            touched.append(item["path"])
            apply_file(ctx, cand_root, item)
            journal_step(conn, task_id, seq, item, "written")
        if current_head(ctx) != manifest["expected_head"]:
            raise GuardError(EXIT_UNCERTAIN, "head_changed_during_integration")
        mismatched = [item["path"] for item in plan
                      if sha256_bytes((ctx.top / item["path"]).read_bytes()) != item["after"]]
        if mismatched:
            raise GuardError(EXIT_UNCERTAIN, "post_write_mismatch", paths=mismatched)
    except BaseException as exc:
        reason = exc.reason if isinstance(exc, GuardError) else repr(exc)
        if not touched:
            record = finish_integration(conn, task_id, "cancelled", reason,
                                        {"written": [], "error": reason})
            return EXIT_COMMAND_FAILED, {"ok": False, "reason": reason, "record": record}
        record = finish_integration(conn, task_id, "needs_reconciliation", reason,
                                    {"touched": touched, "error": reason})
        return EXIT_UNCERTAIN, {"ok": False, "reason": reason, "record": record}
    record = finish_integration(conn, task_id, "integrated", "integrated",
                                {"written": [item["path"] for item in plan]})
    return EXIT_OK, {"ok": True, "record": record}


# --------------------------------------------------------------------------
# Commands


def cmd_status(ctx: Context, args):
    conn = open_db(ctx.runtime, readonly=True)
    payload = {"ok": True, "registry": str(ctx.runtime / REGISTRY_NAME), "active": None}
    if conn is None:
        payload["registry_present"] = False
        if args.task_id:
            payload["task"] = None
        return EXIT_OK, payload
    payload["registry_present"] = True
    row = active_run(conn)
    code = EXIT_OK
    if row is not None:
        payload["active"] = describe(conn, row)
        code = EXIT_UNCERTAIN if payload["active"].get("uncertain") else EXIT_BUSY
    if args.task_id:
        task = get_run(conn, require_task_id(args.task_id))
        payload["task"] = describe(conn, task) if task is not None else None
    payload["recent"] = [dict(r) for r in conn.execute(
        "SELECT task_id, kind, state, outcome, updated_at FROM runs ORDER BY updated_at DESC LIMIT 20")]
    return code, payload


def cmd_run(ctx: Context, args):
    expected = require_sha(args.spec_sha256, "spec_sha256") if args.spec_sha256 else None
    spec = validate_spec(load_json_file(args.spec, "spec", expected))
    stop = StopFlag().install()
    conn = open_db(ctx.runtime)
    claim(conn, spec["task_id"], spec["kind"], fingerprint(spec), fingerprint(command_part(spec)), spec,
          lambda: check_run_inputs(spec, ctx), start_marker(os.getpid()),
          predecessor=spec.get("predecessor"), changed_condition=spec.get("changed_condition"))
    return Supervisor(conn, ctx, spec, stop).execute()


def cmd_request_cancel(ctx: Context, args):
    task_id = require_task_id(args.task_id)
    operator = require_text(args.operator, "operator")
    conn = open_db(ctx.runtime)
    with Tx(conn):
        row = get_run(conn, task_id)
        if row is None:
            raise GuardError(EXIT_INVALID, "unknown_task", task_id=task_id)
        if row["state"] not in ("reserved", "running"):
            raise GuardError(EXIT_REFUSED, "not_cancellable", state=row["state"],
                             hint="needs_reconciliation uses reconcile" if row["state"] in ACTIVE_STATES else None)
        conn.execute("UPDATE runs SET cancel_requested = 1, cancel_requested_by = ?, updated_at = ? "
                     "WHERE task_id = ?", (operator, now(), task_id))
        add_event(conn, task_id, "cancel_requested", operator=operator)
    owner = identity_state(row["supervisor_pid"], row["supervisor_start"])
    if args.wait:
        positive_number(args.wait, "wait", MAX_WAIT_SECONDS, integer=False)
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline:
            state = get_run(conn, task_id)["state"]
            if state not in ("reserved", "running"):
                break
            time.sleep(POLL_SECONDS)
    record = describe(conn, get_run(conn, task_id))
    payload = {"ok": owner == "live", "owner_state": owner, "record": record,
               "note": "cooperative request; the guard never signals a persisted PID"}
    return (EXIT_OK if owner == "live" else EXIT_UNCERTAIN), payload


def cmd_reconcile(ctx: Context, args):
    task_id = require_task_id(args.task_id)
    operator = require_text(args.operator, "operator")
    digest = require_sha(args.evidence_sha256, "evidence_sha256")
    evidence_path = Path(args.evidence)
    try:
        ev_st = os.lstat(evidence_path)
    except OSError:
        raise GuardError(EXIT_INVALID, "evidence_absent")
    if stat.S_ISLNK(ev_st.st_mode) or not stat.S_ISREG(ev_st.st_mode):
        raise GuardError(EXIT_INVALID, "evidence_not_regular_file")
    if is_within(evidence_path.resolve(), ctx.runtime.resolve()):
        raise GuardError(EXIT_INVALID, "evidence_inside_registry")
    evidence = load_json_file(str(evidence_path), "evidence", digest)
    exact_keys(evidence, {"schema", "task_id", "attested_by", "no_unrecorded_survivors", "investigation"},
               {"integration_files"}, "evidence")
    if evidence["schema"] != INCIDENT_SCHEMA or evidence["task_id"] != task_id:
        raise GuardError(EXIT_INVALID, "evidence_not_for_task")
    if evidence["no_unrecorded_survivors"] is not True:
        raise GuardError(EXIT_REFUSED, "survivors_not_excluded")
    require_text(evidence["investigation"], "investigation")
    attester = require_text(evidence["attested_by"], "attested_by")
    if attester.strip() == operator.strip():
        raise GuardError(EXIT_INVALID, "attestation_not_independent")
    conn = open_db(ctx.runtime)
    row = get_run(conn, task_id)
    if row is None:
        raise GuardError(EXIT_INVALID, "unknown_task", task_id=task_id)
    if row["state"] not in ACTIVE_STATES:
        raise GuardError(EXIT_REFUSED, "not_active", state=row["state"])
    record = describe(conn, row)
    observed = record["observed"]
    live = {}
    if observed["supervisor"] in LIVE_STATES:
        live["supervisor"] = observed["supervisor"]
    if observed["child"] in LIVE_STATES:
        live["child"] = observed["child"]
    if observed["child_group"] == "present":
        live["child_group"] = row["child_pgid"]
    for pid, state in observed["known"].items():
        if state in LIVE_STATES:
            live[pid] = state
    if live:
        reason = "owner_live" if "supervisor" in live else "known_process_live"
        raise GuardError(EXIT_REFUSED, reason, live=live)
    if row["kind"] == "integration":
        manifest = json.loads(row["spec_json"])
        inspected = {}
        for item in manifest["files"]:
            rel = item["path"]
            st = walk_no_symlink(ctx.top, rel, must_exist=False)
            actual = None if st is None else sha256_bytes(read_regular(ctx.top, rel)[0])
            if actual not in (item["before_sha256"], item["after_sha256"]):
                raise GuardError(EXIT_REFUSED, "integration_target_unresolved", path=rel)
            inspected[rel] = actual
        if evidence.get("integration_files") != inspected:
            raise GuardError(EXIT_REFUSED, "integration_journal_not_reconciled")
    with Tx(conn):
        current = get_run(conn, task_id)
        if current["state"] != row["state"] or current["updated_at"] != row["updated_at"]:
            raise GuardError(EXIT_REFUSED, "record_changed_during_reconcile")
        result = json.loads(row["result_json"] or "null")
        result = {"prior_state": row["state"], "prior_outcome": row["outcome"], "prior_result": result,
                  "reconciliation": {"operator": operator, "attested_by": attester,
                                     "evidence_path": str(evidence_path.resolve()),
                                     "evidence_sha256": digest, "observed": observed, "at": now()}}
        conn.execute("UPDATE runs SET state = 'reconciled', result_json = ?, updated_at = ? "
                     "WHERE task_id = ?", (canonical_json(result), now(), task_id))
        conn.execute("DELETE FROM active_slot WHERE task_id = ?", (task_id,))
        add_event(conn, task_id, "reconciled", operator=operator, evidence_sha256=digest)
    return EXIT_OK, {"ok": True, "record": describe(conn, get_run(conn, task_id))}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_run_guard.py",
        description=__doc__.split("\n\n", 1)[0],
        epilog=("Cooperative guard: serializes callers that use it; it is not a sandbox and does not "
                "stop tools that ignore it. SIGKILL of the supervisor leaves an uncertain active record "
                "that only evidence-backed reconcile clears; persisted PIDs are never signalled. "
                "See the module docstring for spec, manifest and incident evidence formats. "
                "Exit codes: 0 ok/idle, 1 error, 2 invalid, 3 busy/active, 4 refused, "
                "5 uncertain, 6 command failed/timed out/cancelled."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", default=".", help="any path inside the Git checkout or worktree")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="read-only view of the active slot and records")
    status.add_argument("--task-id")
    run = sub.add_parser("run", help="reserve the slot, then supervise one worker/test command")
    run.add_argument("--spec", required=True)
    run.add_argument("--spec-sha256")
    cancel = sub.add_parser("request-cancel", help="ask the live owner to cancel (never signals)")
    cancel.add_argument("--task-id", required=True)
    cancel.add_argument("--operator", required=True)
    cancel.add_argument("--wait", type=float, default=0.0, help="bounded seconds to wait for the owner")
    reconcile = sub.add_parser("reconcile", help="clear an uncertain record with pinned incident evidence")
    reconcile.add_argument("--task-id", required=True)
    reconcile.add_argument("--operator", required=True)
    reconcile.add_argument("--evidence", required=True)
    reconcile.add_argument("--evidence-sha256", required=True)
    integrate = sub.add_parser("integrate", help="apply a reviewed candidate manifest once")
    integrate.add_argument("--manifest", required=True)
    integrate.add_argument("--manifest-sha256")
    return parser


HANDLERS = {
    "status": cmd_status,
    "run": cmd_run,
    "request-cancel": cmd_request_cancel,
    "reconcile": cmd_reconcile,
    "integrate": cmd_integrate,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = resolve_context(args.repo)
        code, payload = HANDLERS[args.command](ctx, args)
    except GuardError as exc:
        code, payload = exc.code, {"ok": False, "reason": exc.reason, **exc.detail}
    except sqlite3.Error as exc:
        code, payload = EXIT_ERROR, {"ok": False, "reason": "registry_error", "error": str(exc)}
    payload["exit_code"] = code
    sys.stdout.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
