#!/usr/bin/env python3
"""Local, PUBLIC synthetic provider for disposable guarded-runner checkouts."""

import argparse
import sys
import time
from pathlib import Path


SCENARIOS = (
    "noop",
    "tracked",
    "untracked",
    "ignored",
    "delete",
    "rename",
    "forbidden",
    "nonzero",
    "timeout",
    "large-log",
)
MAX_PROMPT_BYTES = 65_536
SYNTHETIC_CONTENT = "PUBLIC synthetic guarded-runner fixture.\n"


def write_synthetic(relative_path):
    path = Path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SYNTHETIC_CONTENT, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    args = parser.parse_args()

    if len(sys.stdin.buffer.read(MAX_PROMPT_BYTES + 1)) > MAX_PROMPT_BYTES:
        print("PUBLIC synthetic prompt exceeds fixture limit.", file=sys.stderr)
        return 2

    print("PUBLIC synthetic provider started.", flush=True)
    print("PUBLIC synthetic diagnostic.", file=sys.stderr, flush=True)

    if args.scenario == "tracked":
        write_synthetic("allowed/tracked.txt")
    elif args.scenario == "untracked":
        write_synthetic("allowed/new.txt")
    elif args.scenario == "ignored":
        write_synthetic("allowed/ignored.txt")
    elif args.scenario == "delete":
        Path("allowed/tracked.txt").unlink()
    elif args.scenario == "rename":
        Path("forbidden").mkdir(exist_ok=True)
        Path("allowed/tracked.txt").rename("forbidden/moved.txt")
    elif args.scenario == "forbidden":
        write_synthetic("forbidden/new.txt")
    elif args.scenario == "nonzero":
        print("PUBLIC synthetic requested failure.", file=sys.stderr)
        return 7
    elif args.scenario == "timeout":
        time.sleep(5)
    elif args.scenario == "large-log":
        block = b"PUBLIC synthetic bounded-log exercise.\n"
        payload = (block * ((1_048_576 // len(block)) + 1))[:1_048_576]
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(payload)
        sys.stderr.buffer.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
