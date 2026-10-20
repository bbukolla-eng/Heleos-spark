#!/usr/bin/env python3
"""Build deterministic research-only aggregate TOML on stdout.

This command is a byte-stable assembly helper, not a source verifier or an
admission path. It accepts only explicit SourceEntryV1 lane manifests, performs
the existing offline metadata validation, and never writes the aggregate
registry, fetches a source, or grants production authority.
"""

import importlib.util
import json
from pathlib import Path
import sys


VALIDATOR_PATH = Path(__file__).with_name("validate-sources.py")


def load_validator():
    spec = importlib.util.spec_from_file_location("heleos_validate_sources", VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("validator_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(paths, validator):
    lanes = []
    for path in paths:
        value, digest = validator.read_manifest(path)
        validator.require("source" in value, "lane_required")
        lanes.append((value, digest))

    # Reuse the complete existing contract for field, URL, date, rights, cache,
    # private-path, count, and cross-manifest identity validation.
    validator.validate_manifests(paths)

    lane_hashes = sorted({digest for _, digest in lanes})
    references = sorted(
        (
            {"id": entry["id"], "lane_manifest_sha256": digest}
            for value, digest in lanes
            for entry in value["source"]
        ),
        key=lambda reference: reference["id"],
    )

    lines = [
        "schema_version = 1",
        'status = "research_only"',
        "lane_manifest_sha256 = " + json.dumps(lane_hashes, ensure_ascii=True),
    ]
    for reference in references:
        lines.extend((
            "",
            "[[source_ref]]",
            "id = " + json.dumps(reference["id"], ensure_ascii=True),
            "lane_manifest_sha256 = "
            + json.dumps(reference["lane_manifest_sha256"], ensure_ascii=True),
        ))
    return "\n".join(lines) + "\n"


def reject(code):
    report = {
        "error": code,
        "production_authority": False,
        "status": "REJECTED",
    }
    sys.stderr.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    return 1


def main(argv):
    try:
        validator = load_validator()
        output = build(argv, validator)
    except Exception as error:
        if "validator" in locals() and isinstance(error, validator.InvalidCandidate):
            return reject(str(error))
        if isinstance(error, RuntimeError) and str(error) == "validator_unavailable":
            return reject("validator_unavailable")
        return reject("builder_failed")
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
