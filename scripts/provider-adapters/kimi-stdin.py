#!/usr/bin/env python3
"""Retired provider entrypoint retained solely to refuse old dispatch commands.

Owner direction, 2026-09-22: Kimi is no longer used by Heleos. Do not read
prompts, configuration, credentials or executable bytes, or launch a provider.
"""
import sys


def main() -> int:
    sys.stderr.write("kimi-stdin: provider retired by owner; dispatch disabled\n")
    return 78


if __name__ == "__main__":
    raise SystemExit(main())
