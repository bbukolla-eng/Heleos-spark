"""Tests for scripts/build_run_guard.py (BUILD-WORKER-LIFECYCLE-GUARD-1 LG01-LG07).

Every test uses a disposable Git repository and fabricated Python child
processes in a temporary directory. Waits are bounded (at most 20 seconds each).
Tests marked FIXTURE write registry rows directly to simulate a narrowly frozen
crash window (supervisor SIGKILL between reservation and child launch, or
mid-integration); they do not exercise production fault-injection flags,
because the guard has none.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_run_guard.py"
WAIT_LIMIT = 20.0

_spec = importlib.util.spec_from_file_location("build_run_guard", SCRIPT)
guard_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard_module)

LONG = ("import os, sys, time\n"
        "open(sys.argv[1], 'a').write('%d\\n' % os.getpid())\n"
        "time.sleep(float(sys.argv[2]))\n")

NESTED = r'''
import os, subprocess, sys, time
pidfile = sys.argv[1]
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
nested = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
with open(pidfile + ".tmp", "w") as handle:
    handle.write("%d %d %d\n" % (os.getpid(), grandchild.pid, nested.pid))
os.replace(pidfile + ".tmp", pidfile)
time.sleep(60)
'''

DAEMON = r'''
import os, sys, time
pidfile = sys.argv[1]
middle = os.fork()
if middle == 0:
    os.setsid()
    if os.fork() == 0:
        with open(pidfile + ".tmp", "w") as handle:
            handle.write(str(os.getpid()))
        os.replace(pidfile + ".tmp", pidfile)
        time.sleep(30)
        os._exit(0)
    os._exit(0)
os.waitpid(middle, 0)
for _ in range(500):
    if os.path.exists(pidfile):
        break
    time.sleep(0.01)
sys.exit(0)
'''


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


def wait_for(predicate, limit=WAIT_LIMIT, interval=0.05):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met within %.0fs" % limit)


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class GuardTestCase(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="run-guard-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.env = dict(os.environ)
        self.env.update({
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        })
        self.repo = self.scratch / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.repo / "a.txt").write_bytes(b"old\n")
        (self.repo / "input.txt").write_bytes(b"input\n")
        (self.repo / "keep.txt").write_bytes(b"keep\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixture")
        self.head = self.git("rev-parse", "HEAD")
        self.pids_to_kill = []
        self.addCleanup(self._kill_leftovers)
        self.counter = 0

    def _kill_leftovers(self):
        for pid in self.pids_to_kill:
            for target in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
                try:
                    target()
                except (ProcessLookupError, PermissionError):
                    pass

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args], env=self.env,
                              capture_output=True, text=True, check=True).stdout.strip()

    # -- guard invocation

    def guard(self, *args, repo=None, timeout=60):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--repo", str(repo or self.repo), *args],
                                env=self.env, capture_output=True, text=True, timeout=timeout)
        return result.returncode, json.loads(result.stdout)

    def spawn(self, *args, repo=None):
        proc = subprocess.Popen([sys.executable, "-B", str(SCRIPT), "--repo", str(repo or self.repo), *args],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.pids_to_kill.append(proc.pid)
        self.addCleanup(self._reap, proc)
        return proc

    def _reap(self, proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=WAIT_LIMIT)

    def finish(self, proc):
        out, _ = proc.communicate(timeout=WAIT_LIMIT + 10)
        return proc.returncode, json.loads(out)

    def spec(self, task_id, command_argv, **overrides):
        spec = {
            "task_id": task_id, "kind": "test", "expected_head": self.head, "argv": command_argv, "cwd": ".",
            "inputs": {"input.txt": sha(b"input\n")}, "allowed_paths": ["a.txt"],
            "timeout_seconds": 30, "max_output_bytes": 65536,
        }
        spec.update(overrides)
        self.counter += 1
        path = self.scratch / ("spec-%s-%d.json" % (task_id, self.counter))
        path.write_text(json.dumps(spec))
        return str(path)

    def long_argv(self, name, seconds):
        return [sys.executable, "-c", LONG, str(self.scratch / name), str(seconds)]

    def marker_lines(self, name):
        path = self.scratch / name
        return path.read_text().splitlines() if path.exists() else []

    def status(self, task_id=None, repo=None):
        args = ["status"] + (["--task-id", task_id] if task_id else [])
        return self.guard(*args, repo=repo)

    def wait_running(self, task_id):
        def check():
            _, payload = self.status(task_id)
            task = payload.get("task")
            return task if task and task["state"] == "running" and task["child_pid"] else None
        return wait_for(check)

    def evidence(self, task_id, attested_by="independent-reviewer", **overrides):
        body = {"schema": "heleos.run-guard-incident/v1", "task_id": task_id, "attested_by": attested_by,
                "no_unrecorded_survivors": True,
                "investigation": "Fixture: process table inspected; no unrecorded survivors."}
        body.update(overrides)
        self.counter += 1
        path = self.scratch / ("incident-%s-%d.json" % (task_id, self.counter))
        path.write_text(json.dumps(body))
        return str(path), sha(path.read_bytes())

    def reconcile(self, task_id, evidence, digest, operator="codex"):
        return self.guard("reconcile", "--task-id", task_id, "--operator", operator,
                          "--evidence", evidence, "--evidence-sha256", digest)

    def cancel(self, task_id, wait=15):
        return self.guard("request-cancel", "--task-id", task_id, "--operator", "codex", "--wait", str(wait))

    def fixture_db(self):
        common = Path(self.git("rev-parse", "--git-common-dir"))
        if not common.is_absolute():
            common = self.repo / common
        return guard_module.open_db(common.resolve() / guard_module.RUNTIME_DIR_NAME)


class LG01ConcurrentClaim(GuardTestCase):
    def test_simultaneous_contenders_launch_exactly_one(self):
        first = self.spawn("run", "--spec", self.spec("claim-a", self.long_argv("a.marker", 4)))
        second = self.spawn("run", "--spec", self.spec("claim-b", self.long_argv("b.marker", 4)))
        results = {"claim-a": self.finish(first), "claim-b": self.finish(second)}
        codes = sorted(code for code, _ in results.values())
        self.assertEqual(codes, [0, 3], results)
        winner = next(t for t, (code, _) in results.items() if code == 0)
        loser_payload = next(p for code, p in results.values() if code == 3)
        self.assertEqual(loser_payload["reason"], "slot_busy")
        self.assertEqual(loser_payload["active"]["task_id"], winner)
        launched = self.marker_lines("a.marker") + self.marker_lines("b.marker")
        self.assertEqual(len(launched), 1, "duplicate command starts")

    def test_worktree_shares_the_single_slot(self):
        worktree = self.scratch / "wt"
        self.git("worktree", "add", "-q", "-b", "wt-branch", str(worktree))
        bg = self.spawn("run", "--spec", self.spec("main-run", self.long_argv("main.marker", 30)))
        self.wait_running("main-run")
        code, payload = self.guard("run", "--spec", self.spec("wt-run", self.long_argv("wt.marker", 1)),
                                   repo=worktree)
        self.assertEqual(code, 3, payload)
        self.assertEqual(payload["active"]["task_id"], "main-run")
        self.assertEqual(self.marker_lines("wt.marker"), [])
        code, payload = self.status(repo=worktree)
        self.assertEqual(code, 3)
        self.assertEqual(payload["active"]["task_id"], "main-run")
        self.assertTrue(payload["active"]["owner_live"])
        self.assertEqual(self.cancel("main-run")[0], 0)
        code, payload = self.finish(bg)
        self.assertEqual((code, payload["record"]["state"]), (6, "cancelled"))


class LG02RestartReplayRetry(GuardTestCase):
    def test_restart_attaches_and_terminal_replay_refused(self):
        spec = self.spec("attach-1", self.long_argv("attach.marker", 3))
        bg = self.spawn("run", "--spec", spec)
        running = self.wait_running("attach-1")
        # A fresh controller process observes the live record without launching.
        code, payload = self.status()
        self.assertEqual(code, 3)
        self.assertEqual(payload["active"]["task_id"], "attach-1")
        code, payload = self.guard("run", "--spec", spec)
        self.assertEqual((code, payload["reason"]), (3, "already_active"))
        self.assertEqual(payload["record"]["child_pid"], running["child_pid"])
        code, payload = self.finish(bg)
        self.assertEqual((code, payload["record"]["state"]), (0, "succeeded"))
        code, payload = self.guard("run", "--spec", spec)
        self.assertEqual((code, payload["reason"]), (4, "terminal_replay"))
        self.assertEqual(len(self.marker_lines("attach.marker")), 1)
        changed = self.spec("attach-1", self.long_argv("attach.marker", 1))
        code, payload = self.guard("run", "--spec", changed)
        self.assertEqual((code, payload["reason"]), (4, "spec_changed_for_task_id"))
        self.assertEqual(len(self.marker_lines("attach.marker")), 1)

    def test_failed_retry_requires_predecessor_and_changed_condition(self):
        argv = [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]
        code, payload = self.guard("run", "--spec", self.spec("fail-1", argv))
        self.assertEqual(code, 6)
        self.assertEqual((payload["record"]["state"], payload["record"]["exit_code"]), ("failed", 3))
        code, payload = self.guard("run", "--spec", self.spec("fail-2", argv))
        self.assertEqual((code, payload["reason"]), (4, "unlinked_retry"))
        code, payload = self.guard("run", "--spec", self.spec("fail-2", argv, predecessor="fail-1"))
        self.assertEqual(code, 2, payload)
        code, payload = self.guard("run", "--spec", self.spec("fail-2", argv, predecessor="fail-1",
                                                              changed_condition="   "))
        self.assertEqual(code, 2, payload)
        code, payload = self.guard("run", "--spec", self.spec(
            "fail-2", argv, predecessor="fail-1", changed_condition="fixture dependency repaired"))
        self.assertEqual(code, 6, payload)
        self.assertEqual(payload["record"]["predecessor"], "fail-1")
        code, payload = self.status("fail-1")
        self.assertEqual(payload["task"]["state"], "failed")
        self.assertEqual(payload["task"]["exit_code"], 3)


class LG03UncertainStates(GuardTestCase):
    def test_reserved_without_pid_blocks_until_evidence_reconcile(self):
        # FIXTURE: simulates supervisor SIGKILL after reservation, before child PID recording.
        conn = self.fixture_db()
        stamp = guard_module.now()
        spec = {"task_id": "ghost-1", "fixture": True}
        conn.execute("INSERT INTO runs (task_id, kind, fingerprint, spec_json, state, created_at, updated_at, "
                     "supervisor_pid, supervisor_start) VALUES (?, 'worker', ?, ?, 'reserved', ?, ?, ?, ?)",
                     ("ghost-1", guard_module.fingerprint(spec), json.dumps(spec), stamp, stamp,
                      dead_pid(), "fixture-start"))
        conn.execute("INSERT INTO active_slot (slot, task_id, claimed_at) VALUES (1, 'ghost-1', ?)", (stamp,))
        conn.close()
        code, payload = self.status()
        self.assertEqual(code, 5)
        self.assertTrue(payload["active"]["uncertain"])
        self.assertEqual(payload["active"]["observed"]["child"], "not_recorded")
        code, payload = self.guard("run", "--spec", self.spec("next-1", self.long_argv("next.marker", 0)))
        self.assertEqual((code, payload["reason"]), (3, "slot_busy"))
        self.assertEqual(self.marker_lines("next.marker"), [])
        path, digest = self.evidence("ghost-1", attested_by="codex")
        self.assertEqual(self.reconcile("ghost-1", path, digest)[1]["reason"], "attestation_not_independent")
        path, digest = self.evidence("ghost-1")
        self.assertEqual(self.reconcile("ghost-1", path, "0" * 64)[1]["reason"], "file_hash_mismatch")
        bad_path, bad_digest = self.evidence("ghost-1", no_unrecorded_survivors=False)
        self.assertEqual(self.reconcile("ghost-1", bad_path, bad_digest)[0], 4)
        code, payload = self.reconcile("ghost-1", path, digest)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["record"]["state"], "reconciled")
        self.assertEqual(payload["record"]["result"]["prior_state"], "reserved")
        code, payload = self.guard("run", "--spec", self.spec("next-1", self.long_argv("next.marker", 0)))
        self.assertEqual(code, 0, payload)

    def test_killed_supervisor_with_surviving_child_fails_closed(self):
        bg = self.spawn("run", "--spec", self.spec("orphan-1", self.long_argv("orphan.marker", 60)))
        running = self.wait_running("orphan-1")
        child = running["child_pid"]
        self.pids_to_kill.append(child)
        bg.send_signal(signal.SIGKILL)
        bg.communicate(timeout=WAIT_LIMIT)
        code, payload = self.status("orphan-1")
        self.assertEqual(code, 5)
        self.assertEqual(payload["active"]["observed"]["supervisor"], "absent")
        self.assertEqual(payload["active"]["observed"]["child"], "live")
        self.assertEqual(payload["active"]["state"], "running")
        code, payload = self.guard("run", "--spec", self.spec("orphan-2", self.long_argv("o2.marker", 0)))
        self.assertEqual(code, 3)
        path, digest = self.evidence("orphan-1")
        code, payload = self.reconcile("orphan-1", path, digest)
        self.assertEqual((code, payload["reason"]), (4, "known_process_live"))
        # Restart, status and refused commands never signalled the persisted child.
        self.assertTrue(alive(child))
        os.killpg(child, signal.SIGKILL)  # test-owned incident cleanup
        wait_for(lambda: not alive(child))
        code, payload = self.reconcile("orphan-1", path, digest)
        self.assertEqual(code, 0, payload)

    def test_live_owner_cannot_be_reconciled(self):
        bg = self.spawn("run", "--spec", self.spec("live-1", self.long_argv("live.marker", 30)))
        self.wait_running("live-1")
        path, digest = self.evidence("live-1")
        code, payload = self.reconcile("live-1", path, digest, operator="other-operator")
        self.assertEqual((code, payload["reason"]), (4, "owner_live"))
        code, payload = self.cancel("live-1")
        self.assertEqual(code, 0, payload)
        code, payload = self.finish(bg)
        self.assertEqual((code, payload["record"]["state"]), (6, "cancelled"))
        self.assertEqual(payload["record"]["result"]["detail"], "request-cancel")


class LG04Outcomes(GuardTestCase):
    def read_pids(self, name):
        path = self.scratch / name
        wait_for(path.exists)
        return [int(x) for x in path.read_text().split()]

    def test_success_output_preserved(self):
        argv = [sys.executable, "-c", "import sys; print('hello'); sys.stderr.write('warn')"]
        code, payload = self.guard("run", "--spec", self.spec("ok-1", argv))
        self.assertEqual(code, 0, payload)
        record = payload["record"]
        self.assertEqual(Path(record["stdout_path"]).read_bytes(), b"hello\n")
        self.assertEqual(Path(record["stderr_path"]).read_bytes(), b"warn")
        self.assertEqual(self.status()[1]["active"], None)

    def test_timeout_cleans_group_and_known_nested_group(self):
        argv = [sys.executable, "-c", NESTED, str(self.scratch / "nested.pids")]
        bg = self.spawn("run", "--spec", self.spec("timeout-1", argv, timeout_seconds=4))
        pids = self.read_pids("nested.pids")
        self.pids_to_kill.extend(pids)
        code, payload = self.finish(bg)
        self.assertEqual(code, 6, payload)
        self.assertEqual(payload["record"]["state"], "timed_out")
        recorded = {p["pid"] for p in payload["record"]["processes"]}
        self.assertTrue(set(pids) <= recorded, (pids, recorded))
        wait_for(lambda: not any(alive(p) for p in pids))

    def test_supervisor_sigterm_cancels_descendants(self):
        argv = [sys.executable, "-c", NESTED, str(self.scratch / "term.pids")]
        bg = self.spawn("run", "--spec", self.spec("term-1", argv))
        pids = self.read_pids("term.pids")
        self.pids_to_kill.extend(pids)
        wait_for(lambda: set(pids) <= {p["pid"] for p in self.status("term-1")[1]["task"]["processes"]})
        bg.send_signal(signal.SIGTERM)
        code, payload = self.finish(bg)
        self.assertEqual(code, 6, payload)
        self.assertEqual(payload["record"]["state"], "cancelled")
        self.assertEqual(payload["record"]["result"]["detail"], "signal:SIGTERM")
        wait_for(lambda: not any(alive(p) for p in pids))

    def test_unrecorded_survivor_holding_output_is_not_terminal(self):
        argv = [sys.executable, "-c", DAEMON, str(self.scratch / "daemon.pid")]
        code, payload = self.guard("run", "--spec", self.spec("daemon-1", argv))
        daemon = int((self.scratch / "daemon.pid").read_text())
        self.pids_to_kill.append(daemon)
        record = payload["record"]
        if daemon in {p["pid"] for p in record["processes"]}:
            # The scan happened to observe the daemon before it left the tree: it was cleaned.
            self.assertEqual(code, 0, payload)
            wait_for(lambda: not alive(daemon))
            return
        self.assertEqual(code, 5, payload)
        self.assertEqual(record["state"], "needs_reconciliation")
        self.assertIn("unrecorded_output_holder", record["result"])
        self.assertTrue(alive(daemon))
        code, payload = self.guard("run", "--spec", self.spec("after-daemon", self.long_argv("ad.marker", 0)))
        self.assertEqual(code, 3)


class LG05TestsAndBounds(GuardTestCase):
    def test_long_test_blocks_worker_and_integration(self):
        bg = self.spawn("run", "--spec", self.spec("long-test", self.long_argv("long.marker", 30)))
        self.wait_running("long-test")
        code, payload = self.guard("run", "--spec", self.spec(
            "worker-1", self.long_argv("w.marker", 0), kind="worker"))
        self.assertEqual((code, payload["reason"]), (3, "slot_busy"))
        manifest = IntegrationFixture(self).manifest_path()
        code, payload = self.guard("integrate", "--manifest", manifest)
        self.assertEqual((code, payload["reason"]), (3, "slot_busy"))
        self.assertEqual((self.repo / "a.txt").read_bytes(), b"old\n")
        self.assertEqual(self.cancel("long-test")[0], 0)
        self.assertEqual(self.finish(bg)[0], 6)

    def test_output_is_bounded(self):
        argv = [sys.executable, "-c", "import sys; sys.stdout.write('x' * 200000)"]
        code, payload = self.guard("run", "--spec", self.spec("bound-1", argv, max_output_bytes=1000))
        self.assertEqual(code, 0, payload)
        record = payload["record"]
        self.assertEqual(Path(record["stdout_path"]).stat().st_size, 1000)
        self.assertEqual((record["stdout_bytes"], record["stdout_truncated"]), (1000, 1))

    def test_invalid_or_changed_inputs_refused_before_launch(self):
        argv = self.long_argv("invalid.marker", 0)
        cases = [
            ({"expected_head": "abc"}, 2),
            ({"expected_head": "0" * 40}, 4),
            ({"timeout_seconds": -1}, 2),
            ({"timeout_seconds": True}, 2),
            ({"max_output_bytes": 0}, 2),
            ({"argv": "python3 -c pass"}, 2),
            ({"argv": []}, 2),
            ({"kind": "deploy"}, 2),
            ({"cwd": "../outside"}, 2),
            ({"allowed_paths": [".git/config"]}, 2),
            ({"inputs": {"input.txt": "0" * 64}}, 4),
            ({"inputs": {"missing.txt": "0" * 64}}, 4),
            ({"unexpected": 1}, 2),
            ({"changed_condition": "no predecessor"}, 2),
        ]
        for index, (override, expected) in enumerate(cases):
            with self.subTest(override=override):
                code, payload = self.guard("run", "--spec", self.spec("bad-%d" % index, argv, **override))
                self.assertEqual(code, expected, payload)
                self.assertIsNone(self.status("bad-%d" % index)[1].get("task"))
        self.assertEqual(self.marker_lines("invalid.marker"), [])


class ND04UnlimitedWorker(GuardTestCase):
    """The explicit absent worker deadline preserves lifecycle protections."""

    def test_absent_deadline_requires_explicit_null_and_is_worker_only(self):
        path = self.spec("duration-schema", [sys.executable, "-c", "pass"], kind="worker",
                         timeout_seconds=None)
        spec = json.loads(Path(path).read_text())
        self.assertIsNone(guard_module.validate_spec(spec)["timeout_seconds"])
        for kind in ("worker", "test"):
            finite = dict(spec, kind=kind, timeout_seconds=0.25)
            self.assertEqual(guard_module.validate_spec(finite)["timeout_seconds"], 0.25)
            for value in (0, -1, True, False, "unlimited", "30", [], {}, float("nan"),
                          float("inf"), -float("inf"), guard_module.MAX_TIMEOUT_SECONDS + 1):
                with self.subTest(kind=kind, value=value):
                    with self.assertRaises(guard_module.GuardError):
                        guard_module.validate_spec(dict(spec, kind=kind, timeout_seconds=value))
            missing = dict(spec, kind=kind)
            del missing["timeout_seconds"]
            with self.subTest(kind=kind, value="missing"):
                with self.assertRaises(guard_module.GuardError):
                    guard_module.validate_spec(missing)
        with self.assertRaises(guard_module.GuardError):
            guard_module.validate_spec(dict(spec, kind="test"))

    def test_unlimited_worker_finishes_after_clock_advance_beyond_former_maximum(self):
        # Test-only clock injection, isolated in a subprocess. Each observed tick
        # crosses the old maximum; no production hook or week-long wait is used.
        driver = r'''
import importlib.util, json, sys, time, types
spec = importlib.util.spec_from_file_location("guard", sys.argv[1])
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
calls = 0
step = g.MAX_TIMEOUT_SECONDS + 1
def advanced_clock():
    global calls
    calls += 1
    return calls * step
g.time = types.SimpleNamespace(monotonic=advanced_clock, sleep=time.sleep)
code = g.main(["--repo", sys.argv[2], "run", "--spec", sys.argv[3]])
sys.stderr.write(json.dumps({"calls": calls, "elapsed": max(0, calls - 1) * step}))
sys.exit(code)
'''
        argv = [sys.executable, "-c", "import time; time.sleep(0.5); print('natural completion')"]
        path = self.spec("no-deadline-clock", argv, kind="worker", timeout_seconds=None)
        result = subprocess.run([sys.executable, "-B", "-c", driver, str(SCRIPT), str(self.repo), path],
                                env=self.env, capture_output=True, text=True, timeout=WAIT_LIMIT)
        payload = json.loads(result.stdout)
        clock = json.loads(result.stderr)
        self.assertEqual((result.returncode, payload["record"]["state"]), (0, "succeeded"), payload)
        self.assertGreater(clock["elapsed"], guard_module.MAX_TIMEOUT_SECONDS)
        record = payload["record"]
        self.assertIsNone(record["spec"]["timeout_seconds"])
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual(Path(record["stdout_path"]).read_bytes(), b"natural completion\n")
        self.assertEqual(record["result"]["remaining_after_cleanup"], {})
        self.assertIsNone(self.status()[1]["active"])

    def test_unlimited_worker_blocks_duplicates_and_integration_then_cancels_descendants(self):
        worktree = self.scratch / "unlimited-wt"
        self.git("worktree", "add", "-q", "-b", "unlimited-wt", str(worktree))
        manifest = IntegrationFixture(self, "no-deadline-integration").manifest_path()
        pidfile = self.scratch / "unlimited.pids"
        argv = [sys.executable, "-c", NESTED, str(pidfile)]
        path = self.spec("no-deadline-cancel", argv, kind="worker", timeout_seconds=None)
        bg = self.spawn("run", "--spec", path)
        running = self.wait_running("no-deadline-cancel")
        wait_for(pidfile.exists)
        pids = [int(value) for value in pidfile.read_text().split()]
        self.pids_to_kill.extend(pids)
        wait_for(lambda: set(pids) <= {
            process["pid"] for process in self.status("no-deadline-cancel")[1]["task"]["processes"]})
        code, payload = self.guard("run", "--spec", path)
        self.assertEqual((code, payload["reason"]), (3, "already_active"), payload)
        self.assertEqual(payload["record"]["child_pid"], running["child_pid"])
        code, payload = self.guard("run", "--spec", self.spec(
            "no-deadline-contender", self.long_argv("unlimited-contender.marker", 0),
            kind="worker", timeout_seconds=None), repo=worktree)
        self.assertEqual((code, payload["reason"]), (3, "slot_busy"), payload)
        self.assertEqual(self.marker_lines("unlimited-contender.marker"), [])
        code, payload = self.guard("integrate", "--manifest", manifest)
        self.assertEqual((code, payload["reason"]), (3, "slot_busy"), payload)
        self.assertEqual((self.repo / "a.txt").read_bytes(), b"old\n")
        self.assertFalse((self.repo / "sub/b.txt").exists())
        self.assertEqual(self.cancel("no-deadline-cancel")[0], 0)
        code, payload = self.finish(bg)
        record = payload["record"]
        self.assertEqual((code, record["state"]), (6, "cancelled"), payload)
        self.assertEqual(record["result"]["detail"], "request-cancel")
        self.assertEqual(record["result"]["remaining_after_cleanup"], {})
        self.assertIsNone(record["spec"]["timeout_seconds"])
        wait_for(lambda: not any(alive(pid) for pid in pids))
        self.assertIsNone(self.status()[1]["active"])
        self.assertEqual(self.status("no-deadline-cancel")[1]["task"]["state"], "cancelled")

    def test_unlimited_worker_output_remains_bounded(self):
        argv = [sys.executable, "-c",
                "import sys; sys.stdout.write('x' * 200000); sys.stderr.write('y' * 200000)"]
        code, payload = self.guard("run", "--spec", self.spec(
            "no-deadline-output", argv, kind="worker", timeout_seconds=None, max_output_bytes=1000))
        self.assertEqual(code, 0, payload)
        record = payload["record"]
        for stream, expected in (("stdout", b"x" * 1000), ("stderr", b"y" * 1000)):
            self.assertEqual(Path(record[stream + "_path"]).read_bytes(), expected)
            self.assertEqual((record[stream + "_bytes"], record[stream + "_truncated"]), (1000, 1))
        self.assertIsNone(record["spec"]["timeout_seconds"])
        self.assertIsNone(self.status()[1]["active"])


class IntegrationFixture:
    def __init__(self, case: GuardTestCase, task_id="integrate-1"):
        self.case = case
        self.task_id = task_id
        self.cand = case.scratch / "candidate"
        (self.cand / "sub").mkdir(parents=True, exist_ok=True)
        (self.cand / "a.txt").write_bytes(b"new\n")
        (self.cand / "sub" / "b.txt").write_bytes(b"b\n")
        if not (case.repo / "u.txt").exists():
            (case.repo / "u.txt").write_bytes(b"unrelated uncommitted\n")
        self.files = [
            {"path": "a.txt", "before_sha256": sha(b"old\n"), "after_sha256": sha(b"new\n")},
            {"path": "sub/b.txt", "before_sha256": None, "after_sha256": sha(b"b\n")},
        ]
        self.review_path = case.scratch / "review.json"
        self.write_review()

    def write_review(self, **overrides):
        body = {"task_id": self.task_id, "decision": "accepted", "reviewer": "claude-reviewer",
                "expected_head": self.case.head,
                "before_files": {f["path"]: f["before_sha256"] for f in self.files},
                "files": {f["path"]: f["after_sha256"] for f in self.files}}
        body.update(overrides)
        self.review_path.write_text(json.dumps(body))
        return sha(self.review_path.read_bytes())

    def manifest(self, **overrides):
        body = {"task_id": self.task_id, "expected_head": self.case.head, "candidate_root": str(self.cand),
                "candidate_author": "claude-worker", "allowed_paths": ["a.txt", "sub"],
                "files": self.files,
                "review": {"path": str(self.review_path), "sha256": sha(self.review_path.read_bytes()),
                           "reviewer": "claude-reviewer"}}
        body.update(overrides)
        return body

    def manifest_path(self, **overrides):
        self.case.counter += 1
        path = self.case.scratch / ("manifest-%d.json" % self.case.counter)
        path.write_text(json.dumps(self.manifest(**overrides)))
        return str(path)


class LG06IntegrationRejections(GuardTestCase):
    def assert_untouched(self, task_id="integrate-1"):
        self.assertEqual((self.repo / "a.txt").read_bytes(), b"old\n")
        self.assertFalse((self.repo / "sub" / "b.txt").exists())
        self.assertEqual((self.repo / "u.txt").read_bytes(), b"unrelated uncommitted\n")
        self.assertIsNone(self.status(task_id)[1].get("task"))

    def test_rejections_before_any_write(self):
        fx = IntegrationFixture(self)
        good_files = fx.files
        cases = {
            "stale_head": dict(expected_head="0" * 40),
            "destination_changed": dict(files=[dict(good_files[0], before_sha256=sha(b"other\n")),
                                               good_files[1]]),
            "destination_exists": dict(files=[dict(good_files[0], before_sha256=None), good_files[1]]),
            "destination_absent": dict(files=[good_files[0], dict(good_files[1], before_sha256=sha(b"x"))]),
            "review_changed": dict(review={"path": str(fx.review_path), "sha256": "0" * 64,
                                           "reviewer": "claude-reviewer"}),
            "self_review": dict(candidate_author="claude-reviewer"),
            "path_not_allowed": dict(allowed_paths=["a.txt"]),
            "unsafe_traversal": dict(files=[dict(good_files[0], path="../a.txt")]),
            "unsafe_absolute": dict(files=[dict(good_files[0], path="/etc/hosts")]),
            "protected_git": dict(files=[dict(good_files[0], path=".git/config")],
                                  allowed_paths=[".git"]),
            "dotdot_inner": dict(files=[dict(good_files[0], path="sub/../a.txt")]),
        }
        for name, override in cases.items():
            with self.subTest(case=name):
                code, payload = self.guard("integrate", "--manifest", fx.manifest_path(**override))
                self.assertIn(code, (2, 4), payload)
                self.assert_untouched()

    def test_unlisted_and_unreviewed_paths_refused(self):
        fx = IntegrationFixture(self)
        digest = fx.write_review(files={"a.txt": sha(b"new\n")})
        code, payload = self.guard("integrate", "--manifest", fx.manifest_path(
            review={"path": str(fx.review_path), "sha256": digest, "reviewer": "claude-reviewer"}))
        self.assertEqual((code, payload["reason"]), (4, "unreviewed_or_unlisted_path"))
        self.assert_untouched()
        extra = {f["path"]: f["after_sha256"] for f in fx.files}
        extra["keep.txt"] = sha(b"evil\n")
        digest = fx.write_review(files=extra)
        code, payload = self.guard("integrate", "--manifest", fx.manifest_path(
            review={"path": str(fx.review_path), "sha256": digest, "reviewer": "claude-reviewer"}))
        self.assertEqual((code, payload["unlisted"]), (4, ["keep.txt"]))
        self.assert_untouched()

    def test_candidate_changed_after_review_refused(self):
        fx = IntegrationFixture(self)
        manifest = fx.manifest_path()
        (fx.cand / "a.txt").write_bytes(b"changed after review\n")
        code, payload = self.guard("integrate", "--manifest", manifest)
        self.assertEqual((code, payload["reason"]), (4, "candidate_changed"))
        self.assert_untouched()

    def test_review_inside_candidate_and_symlink_destination_refused(self):
        fx = IntegrationFixture(self)
        inside = fx.cand / "review.json"
        shutil.copy(fx.review_path, inside)
        code, payload = self.guard("integrate", "--manifest", fx.manifest_path(
            review={"path": str(inside), "sha256": sha(inside.read_bytes()), "reviewer": "claude-reviewer"}))
        self.assertEqual((code, payload["reason"]), (2, "review_inside_candidate"))
        outside = self.scratch / "outside"
        outside.mkdir()
        os.symlink(outside, self.repo / "sub")
        code, payload = self.guard("integrate", "--manifest", fx.manifest_path())
        self.assertEqual((code, payload["reason"]), (2, "symlink_in_path"))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual((self.repo / "a.txt").read_bytes(), b"old\n")


class LG07IntegrationOnce(GuardTestCase):
    def test_applies_once_and_preserves_unrelated_bytes(self):
        fx = IntegrationFixture(self)
        manifest = fx.manifest_path()
        code, payload = self.guard("integrate", "--manifest", manifest)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["record"]["state"], "integrated")
        self.assertEqual((self.repo / "a.txt").read_bytes(), b"new\n")
        self.assertEqual((self.repo / "sub" / "b.txt").read_bytes(), b"b\n")
        self.assertEqual((self.repo / "u.txt").read_bytes(), b"unrelated uncommitted\n")
        self.assertEqual((self.repo / "keep.txt").read_bytes(), b"keep\n")
        steps = [(j["seq"], j["step"]) for j in payload["record"]["journal"]]
        for seq in (0, 1):
            for step in ("planned", "writing", "written"):
                self.assertIn((seq, step), steps)
        code, payload = self.guard("integrate", "--manifest", manifest)
        self.assertEqual((code, payload["reason"]), (4, "terminal_replay"))
        code, payload = self.guard("integrate", "--manifest", fx.manifest_path(allowed_paths=["a.txt", "sub", "x"]))
        self.assertEqual((code, payload["reason"]), (4, "spec_changed_for_task_id"))

    def test_interrupted_integration_stays_uncertain(self):
        # FIXTURE: simulates supervisor SIGKILL after journaling "writing" for the first file.
        fx = IntegrationFixture(self)
        manifest_path = fx.manifest_path()
        manifest = json.loads(Path(manifest_path).read_text())
        conn = self.fixture_db()
        stamp = guard_module.now()
        conn.execute("INSERT INTO runs (task_id, kind, fingerprint, spec_json, state, created_at, updated_at, "
                     "supervisor_pid, supervisor_start) VALUES (?, 'integration', ?, ?, 'integrating', ?, ?, ?, ?)",
                     (fx.task_id, guard_module.fingerprint(manifest), guard_module.canonical_json(manifest),
                      stamp, stamp, dead_pid(), "fixture-start"))
        conn.execute("INSERT INTO active_slot (slot, task_id, claimed_at) VALUES (1, ?, ?)", (fx.task_id, stamp))
        for seq, entry in enumerate(fx.files):
            conn.execute("INSERT INTO journal VALUES (?, ?, ?, ?, ?, 'planned', ?)",
                         (fx.task_id, seq, entry["path"], entry["before_sha256"], entry["after_sha256"], stamp))
        conn.execute("INSERT INTO journal VALUES (?, 0, 'a.txt', ?, ?, 'writing', ?)",
                     (fx.task_id, fx.files[0]["before_sha256"], fx.files[0]["after_sha256"], stamp))
        conn.close()
        code, payload = self.guard("integrate", "--manifest", manifest_path)
        self.assertEqual((code, payload["reason"]), (5, "active_uncertain"))
        self.assertEqual((self.repo / "a.txt").read_bytes(), b"old\n")
        code, payload = self.status(fx.task_id)
        self.assertEqual(code, 5)
        self.assertIn((0, "writing"), [(j["seq"], j["step"]) for j in payload["task"]["journal"]])
        code, payload = self.guard("run", "--spec", self.spec("after-int", self.long_argv("ai.marker", 0)))
        self.assertEqual(code, 3)
        path, digest = self.evidence(fx.task_id, integration_files={"a.txt": sha(b"old\n"), "sub/b.txt": None})
        code, payload = self.reconcile(fx.task_id, path, digest)
        self.assertEqual(code, 0, payload)
        code, payload = self.guard("integrate", "--manifest", manifest_path)
        self.assertEqual((code, payload["reason"]), (4, "terminal_replay"))
        self.assertEqual((self.repo / "a.txt").read_bytes(), b"old\n")

    def test_registry_records_are_immutable(self):
        code, payload = self.guard("run", "--spec", self.spec(
            "imm-1", [sys.executable, "-c", "pass"]))
        self.assertEqual(code, 0, payload)
        conn = self.fixture_db()
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("UPDATE runs SET state = 'running' WHERE task_id = 'imm-1'")
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM runs WHERE task_id = 'imm-1'")
        conn.close()


if __name__ == "__main__":
    unittest.main()
