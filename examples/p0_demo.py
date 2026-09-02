#!/usr/bin/env python3
"""Create a fresh HELIOS P0 demo database and print its release snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from helios_takeoff_core.demo import build_demo_release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "helios-p0-demo.sqlite3",
        help="new SQLite database path; existing paths are never overwritten",
    )
    arguments = parser.parse_args()
    try:
        result = build_demo_release(arguments.database)
    except FileExistsError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
