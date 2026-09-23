#!/usr/bin/env python3
"""Send bounded UTF-8 stdin to Grok Build through a private prompt file.

Usage: python3 grok-stdin.py --grok-executable /absolute/path/to/grok

Adapter failures: 64 invalid CLI, 65 invalid prompt, 70 launch failure,
71 provider/adapter signal, 74 input or temporary-file failure. Ordinary
provider exits and stdout/stderr pass through. Catchable SIGINT/SIGTERM/SIGHUP
remove the file; SIGKILL, power loss, and filesystem failure cannot guarantee
cleanup. The outer runner owns containment, descendant cleanup and timeouts.

Grok 0.2.111 only honors bypassPermissions as a CLI permission override. The
fixed Write-only tool list and the outer runner's exact-path containment are
therefore required; this adapter is not itself a filesystem sandbox.
"""

import os
import signal
import subprocess
import sys
import tempfile


MAX_PROMPT_BYTES = 65_536
HANDLED_SIGNALS = tuple(
    getattr(signal, name)
    for name in ("SIGINT", "SIGTERM", "SIGHUP")
    if hasattr(signal, name)
)


class Interrupted(BaseException):
    """Unwind subprocess.run and file cleanup without exception text."""


def interrupted(signum, frame):
    raise Interrupted()


def block_signals():
    # Defer POSIX handlers while registering or removing file ownership. This
    # closes the gap between mkstemp creating the file and returning its name.
    if hasattr(signal, "pthread_sigmask"):
        return signal.pthread_sigmask(signal.SIG_BLOCK, HANDLED_SIGNALS)
    return None


def restore_signals(mask):
    if mask is not None:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def fail(status: int, diagnostic: str) -> int:
    """Only fixed diagnostics reach stderr; never paths or exception text."""
    sys.stderr.write(f"grok-stdin: {diagnostic}\n")
    return status


def run_provider(executable: str, prompt_bytes: bytes) -> int:
    # Explicit dir avoids tempfile's silent fallback for an invalid TMPDIR.
    directory = os.environ.get("TMPDIR")
    if directory is None:
        directory = tempfile.gettempdir()
    if not os.path.isabs(directory):
        return fail(74, "prompt file operation failed")

    prompt_path = None
    descriptor = None
    try:
        mask = block_signals()
        try:
            # mkstemp creates atomically with O_EXCL and mode 0600. The prompt
            # never enters argv, a shell, or a predictable temporary filename.
            descriptor, prompt_path = tempfile.mkstemp(prefix="grok-prompt-", dir=directory)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(prompt_bytes)
        finally:
            try:
                if descriptor is not None:
                    os.close(descriptor)
            finally:
                restore_signals(mask)

        try:
            result = subprocess.run(
                [
                    executable, "--prompt-file", prompt_path,
                    "--model", "grok-4.6",
                    "--output-format", "streaming-json",
                    "--permission-mode", "bypassPermissions",
                    "--tools", "Write", "--disable-web-search",
                    "--no-subagents", "--no-memory", "--no-plan",
                    "--verbatim", "--no-auto-update", "--max-turns", "8",
                ],
                # The accepted input stream is at EOF. Inheriting it avoids
                # opening /dev/null, which the enclosing sandbox may deny.
                stdin=None,
                shell=False,
                check=False,
            )
        except (OSError, ValueError):
            return fail(70, "provider launch failed")
        if result.returncode < 0:
            return fail(71, "terminated by signal")
        return result.returncode
    finally:
        mask = block_signals()
        try:
            if prompt_path is not None:
                try:
                    os.unlink(prompt_path)
                except FileNotFoundError:
                    pass
        finally:
            restore_signals(mask)


def main() -> int:
    arguments = sys.argv[1:]
    if (
        len(arguments) != 2
        or arguments[0] != "--grok-executable"
        or not os.path.isabs(arguments[1])
        or "\x00" in arguments[1]
    ):
        return fail(64, "usage: --grok-executable ABSOLUTE_PATH")

    for signum in HANDLED_SIGNALS:
        signal.signal(signum, interrupted)
    try:
        prompt_bytes = bytearray()
        try:
            # Bound actual OS reads, including the single overflow byte;
            # buffered reads can prefetch more than their requested length.
            while len(prompt_bytes) < MAX_PROMPT_BYTES + 1:
                chunk = os.read(sys.stdin.fileno(), MAX_PROMPT_BYTES + 1 - len(prompt_bytes))
                if not chunk:
                    break
                prompt_bytes.extend(chunk)
        except OSError:
            return fail(74, "prompt read failed")
        if len(prompt_bytes) > MAX_PROMPT_BYTES:
            return fail(65, "prompt exceeds 65536 bytes")
        try:
            prompt_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return fail(65, "prompt is not valid UTF-8")
        if b"\x00" in prompt_bytes:
            return fail(65, "prompt contains NUL")

        try:
            return run_provider(arguments[1], prompt_bytes)
        except (OSError, ValueError):
            return fail(74, "prompt file operation failed")
    except Interrupted:
        return fail(71, "terminated by signal")


if __name__ == "__main__":
    raise SystemExit(main())
