#!/usr/bin/env python3
"""Pass bounded UTF-8 stdin directly to Cursor Agent 2026.09.02-c22c1a3 without a shell.

Usage: python3 cursor-stdin.py --cursor-node ABSOLUTE_REGULAR_EXECUTABLE
       --cursor-entrypoint ABSOLUTE_REGULAR_FILE

Adapter failures: 64 invalid CLI/executable, 65 invalid prompt, 70 launch
failure, 71 provider/adapter signal, 74 stdin read failure. Ordinary provider
exits and stdout/stderr pass through. The outer guarded runner owns timeouts,
descendant cleanup, output bounds, containment, and authorization. Fixed Cursor
flags are fixture-covered; this adapter does not authenticate or attest a live
model run, installed binary version, internal actions, or cost.

Fixed --force permits writes by bypassing provider tool confirmations. It is
not containment; --sandbox enabled is explicit, and the outer runner retains
its own task/path policy. Cursor trims surrounding stdin whitespace internally.
The model is fixed to the controller-admitted gpt-5.6-sol-high account model.
No authentication or live write is performed by this adapter's tests.
"""

import os
import signal
import stat
import subprocess
import sys


MAX_PROMPT_BYTES = 65_536


class Interrupted(BaseException):
    """Unwind subprocess.run, which kills and reaps its direct child."""


def interrupted(signum, frame):
    raise Interrupted()


def fail(status: int, diagnostic: str) -> int:
    """Only fixed diagnostics reach stderr; never paths or exception text."""
    sys.stderr.write(f"cursor-stdin: {diagnostic}\n")
    return status


def main() -> int:
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), interrupted)
    try:
        arguments = sys.argv[1:]
        valid = (
            len(arguments) == 4
            and arguments[0] == "--cursor-node"
            and arguments[2] == "--cursor-entrypoint"
            and all(os.path.isabs(path) and "\x00" not in path for path in (arguments[1], arguments[3]))
        )
        if valid:
            try:
                valid = all(
                    stat.S_ISREG(os.lstat(path).st_mode)
                    and not os.lstat(path).st_mode & (stat.S_IWGRP | stat.S_IWOTH)
                    for path in (arguments[1], arguments[3])
                ) and os.access(arguments[1], os.X_OK) and os.access(arguments[3], os.R_OK)
            except (OSError, ValueError):
                valid = False
        if not valid:
            return fail(64, "usage: --cursor-node ABSOLUTE_REGULAR_EXECUTABLE --cursor-entrypoint ABSOLUTE_REGULAR_FILE")

        prompt = bytearray()
        if sys.stdin is None:
            return fail(74, "prompt read failed")
        try:
            # Bound actual OS reads to the maximum plus a single overflow byte;
            # buffered reads can prefetch beyond the requested size.
            while len(prompt) < MAX_PROMPT_BYTES + 1:
                chunk = os.read(sys.stdin.fileno(), MAX_PROMPT_BYTES + 1 - len(prompt))
                if not chunk:
                    break
                prompt.extend(chunk)
        except (OSError, ValueError):
            return fail(74, "prompt read failed")
        if len(prompt) > MAX_PROMPT_BYTES:
            return fail(65, "prompt exceeds 65536 bytes")
        try:
            prompt.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return fail(65, "prompt is not valid UTF-8")
        if b"\x00" in prompt:
            return fail(65, "prompt contains NUL")

        # HOME may point at the authorized credential store. Only these three
        # runtime locations are redirected; the outer runner controls access.
        environment = os.environ.copy()
        runtime_root = environment.get("TMPDIR", environment.get("HOME", ""))
        try:
            if not os.path.isabs(runtime_root) or not stat.S_ISDIR(os.lstat(os.path.normpath(runtime_root)).st_mode):
                return fail(64, "runtime root unavailable")
            runtime_root = os.path.realpath(runtime_root)
            # Cursor's installed runtime helper falls back to /tmp/.cursor
            # for long roots. ASCII <=75 keeps root + '/projects' <=84.
            if not runtime_root.isascii() or len(runtime_root) > 75:
                return fail(64, "runtime root unavailable")
            environment["CURSOR_DATA_DIR"] = runtime_root
            for name, child in (("NODE_COMPILE_CACHE", "node-compile-cache"),
                                ("CURSOR_CONFIG_DIR", "cursor-config")):
                path = os.path.join(runtime_root, child)
                try:
                    os.mkdir(path, 0o700)
                except FileExistsError:
                    pass
                metadata = os.lstat(path)
                if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                    return fail(64, "runtime root unavailable")
                environment[name] = path
        except (OSError, ValueError):
            return fail(64, "runtime root unavailable")

        try:
            result = subprocess.run(
                [
                    arguments[1], arguments[3], "--disable-project-configs",
                    "--exclude-workspace-context", "--print", "--force", "--sandbox", "enabled",
                    "--output-format", "stream-json", "--disable-auto-update",
                    "--model", "gpt-5.6-sol-high",
                ],
                input=bytes(prompt),
                env=environment,
                shell=False,
                check=False,
            )
        except (OSError, ValueError):
            return fail(70, "provider launch failed")
        if result.returncode < 0:
            return fail(71, "terminated by signal")
        return result.returncode
    except Interrupted:
        return fail(71, "terminated by signal")


if __name__ == "__main__":
    raise SystemExit(main())
