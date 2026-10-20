#!/usr/bin/env python3
"""Assemble the explicit local workspace preview without running its application."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--zip", dest="archive", type=Path)
    args = parser.parse_args()
    path = Path(__file__).with_name("workspace_package.py")
    spec = importlib.util.spec_from_file_location("workspace_package_builder", path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    try:
        manifest = module.build_package(args.source, args.output, args.archive)
    except module.PackageError as error:
        print("package_error: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
