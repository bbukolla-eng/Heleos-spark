import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.ci import checks  # noqa: E402


class LinkAndProseChecks(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs"))
        self.write("docs/target.md", "# target\n")

    def write(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_broken_relative_link_is_reported_and_good_link_is_not(self):
        self.write("README.md", "[ok](docs/target.md) [bad](docs/missing.md) [web](https://example.org)\n")
        failures = checks.check_links(["README.md"], root=self.root)
        self.assertEqual(failures, ["link: README.md: docs/missing.md"])

    def test_broken_image_destination_is_reported(self):
        self.write("docs/diagram.png", "stand-in for a binary image\n")
        self.write("README.md", "![ok](docs/diagram.png)\n![bad](docs/missing.png)\n")
        failures = checks.check_links(["README.md"], root=self.root)
        self.assertEqual(failures, ["link: README.md: docs/missing.png"])

    def test_dash_rule_applies_only_to_repository_documents(self):
        self.write("README.md", "plain sentence\nwith an em dash — here\n")
        os.makedirs(os.path.join(self.root, "docs", "superpowers"))
        self.write("docs/superpowers/spec.md", "a spec – out of scope\n")
        failures = checks.check_prose(["README.md", "docs/superpowers/spec.md"], root=self.root)
        self.assertEqual(failures, ["prose: README.md:2: em or en dash"])

    def test_registry_hash_mismatch_is_reported(self):
        os.makedirs(os.path.join(self.root, ".claude", "workflows"))
        self.write(".claude/workflows/demo.js", "export const meta = {}\n")
        self.write(".claude/workflows/REGISTRY.json", '{"workflows": {"demo": {"sha256": "000000000000"}}}')
        failures = checks.check_registry(root=self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("does not match", failures[0])


class RetainedDocumentLinks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = temp.name
        self.write("docs/source.md", "Current document may have different content.\n")
        self.write("docs/target.md", "Target\n")
        self.write("archives/retained.md", "[retained reference](target.md)\n")
        self.entry = {
            "retained_path": "archives/retained.md",
            "source_path": "docs/source.md",
            "sha256": hashlib.sha256(b"[retained reference](target.md)\n").hexdigest(),
            "basis": "Recorded original link location, not an equivalence claim.",
        }

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def registry(self, entries=None):
        self.write("docs/operations/retained-document-origins.json", json.dumps({
            "schema_version": 1,
            "documents": [self.entry] if entries is None else entries,
        }))

    def check(self, files=None):
        return checks.check_links(["archives/retained.md"] if files is None else files, root=self.root)

    def test_only_exact_retained_bytes_use_original_link_location(self):
        self.registry()
        self.assertEqual(self.check(), [])
        self.write("archives/retained.md", "[modified](target.md)\n")
        self.assertTrue(any("sha256" in failure for failure in self.check()))

    def test_missing_target_still_fails_in_a_mapped_document(self):
        self.registry()
        os.unlink(os.path.join(self.root, "docs/target.md"))
        self.assertIn("link: archives/retained.md: target.md", self.check())

    def test_unmapped_live_document_keeps_normal_relative_resolution(self):
        self.registry()
        self.write("live.md", "[bad](target.md) [good](docs/target.md)\n")
        self.assertEqual(self.check(["live.md"]), ["link: live.md: target.md"])

    def test_registry_entries_are_validated_even_when_copy_is_not_selected(self):
        self.entry["sha256"] = "0" * 64
        self.registry()
        self.assertTrue(any("sha256" in failure for failure in self.check([])))

    def test_origin_paths_cannot_escape_or_use_ambiguous_separators(self):
        for field in ("retained_path", "source_path"):
            for value in ("../outside.md", "/absolute.md", "docs/../source.md", "docs\\source.md", "docs//source.md", "docs/./source.md", "docs/\x00source.md"):
                with self.subTest(field=field, value=value):
                    entry = dict(self.entry, **{field: value})
                    self.registry([entry])
                    self.assertTrue(any("retained origins:" in f for f in self.check([])))

    def test_symlink_escape_is_rejected(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        with open(os.path.join(outside.name, "source.md"), "w", encoding="utf-8") as handle:
            handle.write("Outside\n")
        os.symlink(outside.name, os.path.join(self.root, "escape"))
        self.entry["source_path"] = "escape/source.md"
        self.registry()
        self.assertTrue(any("retained origins:" in f for f in self.check([])))

    def test_duplicate_copies_and_malformed_records_fail_without_raising(self):
        for entries in ([self.entry, dict(self.entry)], [None], [dict(self.entry, sha256="abc")],
                        [dict(self.entry, basis="")], [dict(self.entry, source_path=[])],
                        [dict(self.entry, retained_path=None)]):
            with self.subTest(entries=entries):
                self.registry(entries)
                self.assertTrue(any("retained origins:" in f for f in self.check([])))

    def test_malformed_registry_reports_failure_without_raising(self):
        for data in ("{", "[]", '{"schema_version": 2, "documents": []}',
                     '{"schema_version": true, "documents": []}',
                     '{"schema_version": 1, "documents": {}}'):
            with self.subTest(data=data):
                self.write("docs/operations/retained-document-origins.json", data)
                self.assertTrue(any("retained origins:" in f for f in self.check([])))

    def test_registry_and_mapped_paths_must_be_regular_files(self):
        self.registry()
        for field in ("retained_path", "source_path"):
            with self.subTest(field=field):
                self.registry([dict(self.entry, **{field: "docs"})])
                self.assertTrue(any("retained origins:" in f for f in self.check([])))
        os.unlink(os.path.join(self.root, "docs/operations/retained-document-origins.json"))
        os.mkdir(os.path.join(self.root, "docs/operations/retained-document-origins.json"))
        self.assertTrue(any("retained origins:" in f for f in self.check([])))

    def test_main_checks_the_selected_repository_and_its_origin_registry(self):
        self.registry()
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        self.assertEqual(checks.main(root=self.root), 0)
        self.write("archives/retained.md", "Changed retained content\n")
        self.assertEqual(checks.main(root=self.root), 1)


if __name__ == "__main__":
    unittest.main()


class EgressLedgerChecks(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs", "runs", "egress"))
        os.makedirs(os.path.join(self.root, "docs", "policies"))
        os.makedirs(os.path.join(self.root, "docs", "decisions"))
        self.write("docs/policies/egress.md", "policy\n")
        self.write("docs/decisions/d.md", "decision\n")

    def write(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as handle:
            handle.write(text)

    def line(self, **overrides):
        record = {
            "provider": "anthropic",
            "purpose": "review",
            "data_class": "INTERNAL",
            "source_hashes": ["checkout-tree:sha1:abc"],
            "policy_decision": {
                "outcome": "allow",
                "rule": "claude-review-anthropic",
                "policy": "docs/policies/egress.md",
                "policy_sha256": "0" * 64,
                "decision": "docs/decisions/d.md",
                "decision_sha256": "0" * 64,
            },
            "time": "2026-09-03T00:00:00Z",
            "result_ref": {"conclusion": "success"},
        }
        record.update(overrides)
        return json.dumps(record)

    def test_absent_ledger_is_not_a_failure(self):
        self.assertEqual(checks.check_egress_ledger(root=self.root), [])

    def test_a_real_recorder_line_passes(self):
        """The recorder serialises with sort_keys, so the validator must not compare key order."""
        from tools.egress import record
        rec = record.build_record(
            self.root, {}, "anthropic", "review", "INTERNAL",
            "docs/policies/egress.md", "docs/decisions/d.md", "claude-review-anthropic",
        )
        self.write("docs/runs/egress/index.jsonl", record.serialise(rec) + "\n")
        self.assertEqual(checks.check_egress_ledger(root=self.root), [])

    def test_a_null_policy_decision_is_reported_not_raised(self):
        self.write("docs/runs/egress/index.jsonl", self.line(policy_decision=None) + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("must be an object", failures[0])

    def test_an_unknown_data_class_fails(self):
        self.write("docs/runs/egress/index.jsonl", self.line(data_class="WHATEVER") + "\n")
        self.assertTrue(any("data class not one of" in f for f in checks.check_egress_ledger(root=self.root)))

    def test_empty_source_hashes_fail(self):
        self.write("docs/runs/egress/index.jsonl", self.line(source_hashes=[]) + "\n")
        self.assertTrue(any("non-empty list" in f for f in checks.check_egress_ledger(root=self.root)))

    def test_well_formed_record_passes(self):
        self.write("docs/runs/egress/index.jsonl", self.line() + "\n")
        self.assertEqual(checks.check_egress_ledger(root=self.root), [])

    def test_refused_outcome_fails(self):
        pd = {"outcome": "refused", "policy": "docs/policies/egress.md", "decision": "docs/decisions/d.md"}
        self.write("docs/runs/egress/index.jsonl", self.line(policy_decision=pd) + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertTrue(any("not allow" in f for f in failures), failures)

    def test_record_naming_a_missing_decision_fails(self):
        pd = {"outcome": "allow", "policy": "docs/policies/egress.md", "decision": "docs/decisions/gone.md"}
        self.write("docs/runs/egress/index.jsonl", self.line(policy_decision=pd) + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertTrue(any("not in the tree" in f for f in failures), failures)

    def test_record_missing_a_spec_field_fails(self):
        record = json.loads(self.line())
        del record["time"]
        self.write("docs/runs/egress/index.jsonl", json.dumps(record) + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("fields must be exactly", failures[0])
