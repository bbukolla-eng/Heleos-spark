#!/usr/bin/env python3
"""Pass a bounded UTF-8 stdin prompt to Kimi Code 0.34 without a shell.

Usage: python3 kimi-stdin.py --kimi-executable /absolute/path/to/kimi
       [--probe-state-layout | --worker-state-root /absolute/path]

Adapter failures: 64 invalid CLI, 65 invalid prompt, 70 launch failure,
71 provider signal, 74 stdin read failure, 78 state capability unavailable.
The state probe reads executable bytes only, emits JSON, and exits 78. It never
executes a version/help command, reads stdin, or opens authentication/config
files. The reviewed Kimi build combines auth and runtime state; unknown builds
are unverified. All explicit worker-state-root requests refuse before input or
launch. No writable state capability or authentication migration is admitted.
Ordinary provider exits propagate;
provider stdout/stderr are inherited without inspecting or rewriting them.
The outer guarded runner owns timeouts, process-group cleanup, output limits,
and provider authorization. Kimi 0.34 prompt mode supplies its own noninteractive
permission policy and rejects explicit `--auto` or `--yolo`, so the adapter adds
neither flag. Kimi receives the prompt as one argv value, which may be visible to
local process inspection; never submit secrets.
"""

import hashlib
import json
import os
import stat
import subprocess
import sys


MAX_PROMPT_BYTES = 65_536
MAX_EXECUTABLE_BYTES = 268_435_456
# Capability classification of the exact macOS arm64 executable inspected with
# --help/--version under denied writes/network on 2026-09-09. Version text alone
# cannot admit a replacement. This is a negative classification, not support.
REVIEWED_COMBINED_STATE_BUILDS = {
    "9f4337e10da47843f6b550474012a53ba8b30dd665f83b176a5cd479c5f7e859": "0.34.0",
}


def fail(status: int, diagnostic: str) -> int:
    """Emit only a caller-supplied fixed diagnostic, never exception details."""
    sys.stderr.write(f"kimi-stdin: {diagnostic}\n")
    return status


def state_capability(digest: str) -> dict:
    result = {
        "schema": "heleos.kimi-state-capability/v1",
        "available": False,
        "reason": "unverified_executable",
        "read_only_auth_writable_runtime": False,
    }
    if digest in REVIEWED_COMBINED_STATE_BUILDS:
        result["reason"] = "combined_auth_runtime_root"
        result["reviewed_version"] = REVIEWED_COMBINED_STATE_BUILDS[digest]
    return result


def probe_state_layout(executable: str) -> int:
    # A nonblocking, no-follow descriptor avoids hanging on a replaced FIFO or
    # following a final symlink. Unsupported platforms refuse this probe.
    try:
        with os.fdopen(os.open(
            executable, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
        ), "rb", buffering=0) as binary:
            before = os.fstat(binary.fileno())
            if (not stat.S_ISREG(before.st_mode) or not before.st_mode & 0o111
                    or before.st_size > MAX_EXECUTABLE_BYTES):
                return fail(78, "executable capability probe failed")
            digest = hashlib.sha256()
            length = 0
            while length <= MAX_EXECUTABLE_BYTES:
                chunk = binary.read(min(1_048_576, MAX_EXECUTABLE_BYTES + 1 - length))
                if not chunk:
                    break
                length += len(chunk)
                digest.update(chunk)
            after = os.fstat(binary.fileno())
            named = os.lstat(executable)
            identity = lambda item: (
                item.st_dev, item.st_ino, item.st_mode, item.st_size,
                item.st_mtime_ns, item.st_ctime_ns,
            )
            if (length > MAX_EXECUTABLE_BYTES or length != before.st_size
                    or identity(before) != identity(after)
                    or identity(after) != identity(named)):
                return fail(78, "executable capability probe failed")
    except (AttributeError, OSError, ValueError):
        return fail(78, "executable capability probe failed")
    sys.stdout.write(json.dumps(state_capability(digest.hexdigest()), sort_keys=True) + "\n")
    return 78


def main() -> int:
    arguments = sys.argv[1:]
    if (
        len(arguments) < 2
        or arguments[0] != "--kimi-executable"
        or not os.path.isabs(arguments[1])
        or "\x00" in arguments[1]
    ):
        return fail(64, "usage: --kimi-executable ABSOLUTE_PATH")

    mode = arguments[2:]
    if mode == ["--probe-state-layout"]:
        return probe_state_layout(arguments[1])
    if (len(mode) == 2 and mode[0] == "--worker-state-root"
            and os.path.isabs(mode[1]) and "\x00" not in mode[1]):
        return fail(78, "read-only authentication/runtime separation unavailable")
    if mode:
        return fail(64, "usage: --kimi-executable ABSOLUTE_PATH")

    prompt_bytes = bytearray()
    try:
        # BufferedReader.read(size) may prefetch beyond size. Bound the actual
        # OS reads as well as the retained bytes, including the over-limit byte.
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
        prompt = prompt_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return fail(65, "prompt is not valid UTF-8")
    if "\x00" in prompt:
        return fail(65, "prompt contains NUL")

    try:
        result = subprocess.run(
            [arguments[1], "--output-format", "stream-json", "--prompt", prompt],
            # Accepted prompts have drained stdin to EOF; inherit that stream
            # without opening /dev/null, which the outer sandbox may deny.
            stdin=None,
            shell=False,
            check=False,
        )
    except (OSError, ValueError):
        return fail(70, "provider launch failed")
    if result.returncode < 0:
        return fail(71, "provider terminated by signal")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
