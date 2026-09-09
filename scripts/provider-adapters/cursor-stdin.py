#!/usr/bin/env python3
"""Pass bounded UTF-8 stdin directly to Cursor Agent 2026.09.02-c22c1a3 without a shell.

Usage: python3 cursor-stdin.py --cursor-executable ABSOLUTE_REGULAR_EXECUTABLE

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
            len(arguments) == 2
            and arguments[0] == "--cursor-executable"
            and os.path.isabs(arguments[1])
            and "\x00" not in arguments[1]
        )
        if valid:
            try:
                valid = stat.S_ISREG(os.stat(arguments[1]).st_mode) and os.access(
                    arguments[1], os.X_OK
                )
            except (OSError, ValueError):
                valid = False
        if not valid:
            return fail(64, "usage: --cursor-executable ABSOLUTE_REGULAR_EXECUTABLE")

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

        try:
            result = subprocess.run(
                [
                    arguments[1], "--print", "--force", "--sandbox", "enabled",
                    "--output-format", "stream-json", "--disable-auto-update",
                    "--model", "gpt-5.6-sol-high",
                ],
                input=bytes(prompt),
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
