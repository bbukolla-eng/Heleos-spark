#!/usr/bin/env python3
"""Compatibility wrapper for the installed P1A acceptance command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from helios_takeoff_core.agentic.acceptance import DEFAULT_METADATA, build_baseline_audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        result = build_baseline_audit(arguments.work_root, DEFAULT_METADATA)
    except (FileExistsError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
