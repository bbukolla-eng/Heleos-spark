"""Black-box tests for the read-only aggregate candidate builder."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "governance/agents/build-source-registry.py"
VALIDATOR = ROOT / "governance/agents/validate-sources.py"


def source(source_id, url):
    return {
        "id": source_id,
        "canonical_url": url,
        "publisher": "Repository synthetic publisher",
        "title": "Synthetic public source",
        "edition_or_version": "1",
        "effective_date": "2026-09-09",
        "retrieved_at": "2026-09-09T15:00:00Z",
        "cache_status": "not_cached",
        "license_or_rights": "Repository-authored synthetic metadata",
        "data_class": "PUBLIC",
        "locators": ["Synthetic section 1"],
        "claims_supported": ["Builder test metadata only"],
        "applicability": "Synthetic test only",
        "supersession_state": "unknown",
        "notebooklm_permission": "denied",
        "notebooklm_rights_basis": "No provider upload authorization",
        "contradictions": [],
        "gaps": ["No real source content"],
    }


def lane(entries):
    raw = 'schema_version = 1\nstatus = "research_only"\n'
    for entry in entries:
        raw += "\n[[source]]\n"
        for key, value in entry.items():
            raw += key + " = " + json.dumps(value, ensure_ascii=True) + "\n"
    return raw.encode()


class BuildSourceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="heleos-aggregate-builder-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def write(self, name, entries):
        path = self.root / name
        path.write_bytes(lane(entries))
        return path

    def run_builder(self, *paths):
        return subprocess.run(
            [sys.executable, "-B", str(BUILDER), *map(str, paths)],
            cwd=self.root,
            capture_output=True,
            timeout=5,
        )

    def test_build_is_deterministic_sorted_read_only_and_round_trips(self):
        second = self.write("z.toml", [source("zeta", "https://example.org/zeta")])
        first = self.write("a.toml", [
            source("beta", "https://example.org/beta"),
            source("alpha", "https://example.org/alpha"),
        ])
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}

        result = self.run_builder(second, first)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.stdout, self.run_builder(first, second).stdout)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})
        text = result.stdout.decode()
        lane_hashes = sorted(hashlib.sha256(raw).hexdigest() for raw in before.values())
        self.assertIn("lane_manifest_sha256 = " + json.dumps(lane_hashes), text)
        self.assertLess(text.index('id = "alpha"'), text.index('id = "beta"'))
        self.assertLess(text.index('id = "beta"'), text.index('id = "zeta"'))

        aggregate = self.root / "aggregate.toml"
        aggregate.write_bytes(result.stdout)
        validation = subprocess.run(
            [sys.executable, "-B", str(VALIDATOR), str(first), str(second), str(aggregate)],
            cwd=self.root,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(validation.returncode, 0, validation.stdout)
        report = json.loads(validation.stdout)
        self.assertEqual(report["source_count"], 3)
        self.assertEqual(report["lane_manifest_count"], 2)
        self.assertEqual(report["aggregate_manifest_count"], 1)
        self.assertFalse(report["production_authority"])

    def test_empty_duplicate_invalid_and_aggregate_inputs_fail_closed(self):
        self.assert_rejected([], "invalid_manifest_count")
        duplicate_a = self.write("duplicate-a.toml", [source("same", "https://example.org/a")])
        duplicate_b = self.write("duplicate-b.toml", [source("same", "https://example.org/b")])
        self.assert_rejected([duplicate_a, duplicate_b], "duplicate_source_id")
        invalid = self.root / "invalid.toml"
        invalid.write_text('schema_version = 1\nstatus = "approved"\nsource = []\n')
        self.assert_rejected([invalid], "invalid_manifest")

        valid = self.write("valid.toml", [source("valid", "https://example.org/valid")])
        generated = self.run_builder(valid)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        aggregate = self.root / "already-aggregate.toml"
        aggregate.write_bytes(generated.stdout)
        self.assert_rejected([aggregate], "lane_required")
        self.assert_rejected([self.root], "input_unavailable")

    def assert_rejected(self, paths, code):
        result = self.run_builder(*paths)
        self.assertEqual(result.returncode, 1, result)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(json.loads(result.stderr), {
            "error": code,
            "production_authority": False,
            "status": "REJECTED",
        })


if __name__ == "__main__":
    unittest.main()
