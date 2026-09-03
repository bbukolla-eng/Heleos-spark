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
                "policy": "docs/policies/egress.md",
                "decision": "docs/decisions/d.md",
            },
            "time": "2026-09-03T00:00:00Z",
            "result_ref": {"conclusion": "success"},
        }
        record.update(overrides)
        return json.dumps(record)

    def test_absent_ledger_is_not_a_failure(self):
        self.assertEqual(checks.check_egress_ledger(root=self.root), [])

    def test_well_formed_record_passes(self):
        self.write("docs/runs/egress/index.jsonl", self.line() + "\n")
        self.assertEqual(checks.check_egress_ledger(root=self.root), [])

    def test_refused_outcome_fails(self):
        pd = {"outcome": "refused", "policy": "docs/policies/egress.md", "decision": "docs/decisions/d.md"}
        self.write("docs/runs/egress/index.jsonl", self.line(policy_decision=pd) + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("not allow", failures[0])

    def test_record_naming_a_missing_decision_fails(self):
        pd = {"outcome": "allow", "policy": "docs/policies/egress.md", "decision": "docs/decisions/gone.md"}
        self.write("docs/runs/egress/index.jsonl", self.line(policy_decision=pd) + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("not in the tree", failures[0])

    def test_record_missing_a_spec_field_fails(self):
        record = json.loads(self.line())
        del record["time"]
        self.write("docs/runs/egress/index.jsonl", json.dumps(record) + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertEqual(len(failures), 1)
        self.assertIn("fields must be exactly", failures[0])
