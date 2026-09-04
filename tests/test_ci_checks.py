import hashlib
import json
import os
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


class RegistryPins(unittest.TestCase):
    """The committed REGISTRY.json is a list of workflow objects, not a name-keyed map.

    The only existing registry test feeds the dict shape and a mismatch. A parser that only
    understood that shape would silently skip every pin on main.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, ".claude", "workflows"))

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def digest_for(self, name="demo.js", body="export const meta = {}\n"):
        self.write(f".claude/workflows/{name}", body)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def test_absent_registry_is_not_a_failure(self):
        self.assertEqual(checks.check_registry(root=self.root), [])

    def test_the_committed_list_shape_with_a_matching_hash_passes(self):
        digest = self.digest_for()
        self.write(".claude/workflows/REGISTRY.json", json.dumps({
            "workflows": [{"name": "demo", "script": "demo.js", "sha256": digest}],
        }))
        self.assertEqual(checks.check_registry(root=self.root), [])

    def test_a_matching_prefix_is_accepted(self):
        digest = self.digest_for()
        self.write(".claude/workflows/REGISTRY.json", json.dumps({
            "workflows": {"demo": {"script": "demo.js", "sha256": digest[:12]}},
        }))
        self.assertEqual(checks.check_registry(root=self.root), [])

    def test_a_missing_script_is_reported(self):
        self.write(".claude/workflows/REGISTRY.json", json.dumps({
            "workflows": [{"name": "demo", "script": "gone.js", "sha256": "0" * 64}],
        }))
        failures = checks.check_registry(root=self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("script missing", failures[0])


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

    def test_an_empty_provider_fails(self):
        self.write("docs/runs/egress/index.jsonl", self.line(provider="  ") + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertTrue(any("provider must be a non-empty string" in f for f in failures), failures)

    def test_an_uppercase_sha256_fails(self):
        pd = {
            "outcome": "allow",
            "rule": "r",
            "policy": "docs/policies/egress.md",
            "policy_sha256": "0" * 64,
            "decision": "docs/decisions/d.md",
            "decision_sha256": "A" * 64,
        }
        self.write("docs/runs/egress/index.jsonl", self.line(policy_decision=pd) + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertTrue(any("decision_sha256 is not a sha256 digest" in f for f in failures), failures)

    def test_an_invalid_json_line_is_reported_not_raised(self):
        self.write("docs/runs/egress/index.jsonl", "{not json\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("not valid JSON", failures[0])

    def test_blank_lines_are_ignored(self):
        self.write("docs/runs/egress/index.jsonl", "\n" + self.line() + "\n\n")
        self.assertEqual(checks.check_egress_ledger(root=self.root), [])

    def test_secret_is_not_an_allowed_ledger_data_class(self):
        self.write("docs/runs/egress/index.jsonl", self.line(data_class="SECRET") + "\n")
        self.assertTrue(any("data class not one of" in f for f in checks.check_egress_ledger(root=self.root)))


if __name__ == "__main__":
    unittest.main()
