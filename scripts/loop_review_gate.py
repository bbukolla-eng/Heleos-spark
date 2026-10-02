#!/usr/bin/env python3
"""Copy one independently supplied, exact candidate review into a guard output."""

import argparse
import json
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--reviewer", required=True)
    args = parser.parse_args(argv)
    source, output = Path(args.source), Path(args.output)
    if (not source.is_file() or source.is_symlink() or output.exists() or output.is_symlink()
            or not args.reviewer.strip()):
        raise SystemExit("review source or output is unsafe")
    raw = source.read_bytes()
    if len(raw) > 65_536:
        raise SystemExit("review source is too large")
    try:
        report = json.loads(raw)
    except ValueError as exc:
        raise SystemExit("review source is invalid JSON") from exc
    if (not isinstance(report, dict) or set(report) !=
            {"decision", "reviewer", "candidate_sha256", "checks", "runner_evidence"}
            or report["decision"] != "accepted" or report["reviewer"] != args.reviewer):
        raise SystemExit("independent review does not accept this candidate")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(output, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == "__main__":
    main()
