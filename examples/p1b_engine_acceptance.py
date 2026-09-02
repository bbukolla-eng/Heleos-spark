#!/usr/bin/env python3
"""Compatibility wrapper for the installed HELIOS engine acceptance command."""

from __future__ import annotations

import argparse
from pathlib import Path

from helios_takeoff_core.engine_cli import main as engine_main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    arguments = parser.parse_args()
    return engine_main(["acceptance", "--work-root", str(arguments.work_root)])


if __name__ == "__main__":
    raise SystemExit(main())
