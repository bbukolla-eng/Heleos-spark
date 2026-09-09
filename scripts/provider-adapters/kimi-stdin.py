#!/usr/bin/env python3
"""Pass a bounded UTF-8 stdin prompt to Kimi Code 0.34 without a shell.

Usage: python3 kimi-stdin.py --kimi-executable /absolute/path/to/kimi

Adapter failures: 64 invalid CLI, 65 invalid prompt, 70 launch failure,
71 provider signal, 74 stdin read failure. Ordinary provider exits propagate;
provider stdout/stderr are inherited without inspecting or rewriting them.
The outer guarded runner owns timeouts, process-group cleanup, output limits,
and provider authorization. Kimi 0.34 prompt mode supplies its own noninteractive
permission policy and rejects explicit `--auto` or `--yolo`, so the adapter adds
neither flag. Kimi receives the prompt as one argv value, which may be visible to
local process inspection; never submit secrets.
"""

import os
import subprocess
import sys


MAX_PROMPT_BYTES = 65_536


def fail(status: int, diagnostic: str) -> int:
    """Emit only a caller-supplied fixed diagnostic, never exception details."""
    sys.stderr.write(f"kimi-stdin: {diagnostic}\n")
    return status


def main() -> int:
    arguments = sys.argv[1:]
    if (
        len(arguments) != 2
        or arguments[0] != "--kimi-executable"
        or not os.path.isabs(arguments[1])
        or "\x00" in arguments[1]
    ):
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
