"""Residual pins for #4/#9/#10 paths that #13 did not cover.

#13 pinned registry list-shape, missing-script, matching-prefix, ledger empty-provider /
uppercase-sha256 / invalid-JSON / SECRET / blank-line, snapshot write --root, check --precall,
root-flag-wins, and unclosed-fence fail-closed. These cases are the remaining production
branches with meaningful blast radius: JSON CI, the ledger authorization rule, the other
two fail-closed blanking strategies, a corrupt precall snapshot, and prompt-source hashing.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.ci import checks  # noqa: E402
from tools.egress import record  # noqa: E402


class JsonChecks(unittest.TestCase):
    """#4 added check_json; the existing suite never called it."""

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_invalid_json_is_reported_not_raised(self):
        self.write("broken.json", "{not json\n")
        failures = checks.check_json(["broken.json"], root=self.root)
        self.assertEqual(len(failures), 1)
        self.assertTrue(failures[0].startswith("json: broken.json:"), failures)

    def test_valid_json_and_non_json_paths_are_silent(self):
        self.write("ok.json", '{"a": 1}\n')
        self.write("notes.md", "not json\n")
        self.assertEqual(checks.check_json(["ok.json", "notes.md"], root=self.root), [])


class LedgerRuleAndHashTypes(unittest.TestCase):
    """The rule is what authorized the submission; source_hashes must stay a list."""

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
        payload = {
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
        payload.update(overrides)
        return json.dumps(payload)

    def test_a_blank_rule_is_reported(self):
        pd = {
            "outcome": "allow",
            "rule": "   ",
            "policy": "docs/policies/egress.md",
            "policy_sha256": "0" * 64,
            "decision": "docs/decisions/d.md",
            "decision_sha256": "0" * 64,
        }
        self.write("docs/runs/egress/index.jsonl", self.line(policy_decision=pd) + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertTrue(any("no rule recorded" in item for item in failures), failures)

    def test_a_missing_rule_is_reported(self):
        pd = {
            "outcome": "allow",
            "policy": "docs/policies/egress.md",
            "policy_sha256": "0" * 64,
            "decision": "docs/decisions/d.md",
            "decision_sha256": "0" * 64,
        }
        self.write("docs/runs/egress/index.jsonl", self.line(policy_decision=pd) + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertTrue(any("no rule recorded" in item for item in failures), failures)

    def test_source_hashes_as_a_string_fail(self):
        self.write("docs/runs/egress/index.jsonl", self.line(source_hashes="checkout-tree:sha1:abc") + "\n")
        failures = checks.check_egress_ledger(root=self.root)
        self.assertTrue(any("non-empty list" in item for item in failures), failures)


class TheOtherFailClosedBlankingStrategies(unittest.TestCase):
    """#13 pinned an unclosed fence. Comments and tab-indented blocks are the other two."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs"))

    def failures(self, body):
        with open(os.path.join(self.root, "docs/d.md"), "w", encoding="utf-8") as handle:
            handle.write(body)
        return record.approval_failures(self.root, "docs/d.md")

    def test_an_unclosed_html_comment_does_not_let_a_later_signature_count(self):
        body = (
            "# Draft\n\n<!-- this example never closes\n\n"
            "**Status:** APPROVED\n\n**Decided by:** Bekim Bukolla **Date:** 2026-09-03\n"
        )
        self.assertTrue(self.failures(body), "an unclosed HTML comment let a later signature open the gate")

    def test_a_tab_indented_example_does_not_sign_the_record(self):
        body = (
            "# Draft\n\nExample:\n\n"
            "\t**Status:** APPROVED\n"
            "\t**Decided by:** Someone **Date:** x\n\n"
            "## 6. Decision\n\n**Status:** Draft\n\n**Decided by:** ____ **Date:** ____\n"
        )
        self.assertTrue(self.failures(body), "a tab-indented example opened the gate")


class PrecallAndPromptSource(unittest.TestCase):
    """A corrupt snapshot must degrade the same way a missing one does; prompt sources are hashed."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs", "policies"))
        os.makedirs(os.path.join(self.root, "docs", "decisions"))
        with open(os.path.join(self.root, "docs/policies/egress.md"), "w", encoding="utf-8") as handle:
            handle.write("policy\n")
        with open(os.path.join(self.root, "docs/decisions/d.md"), "w", encoding="utf-8") as handle:
            handle.write("**Status:** APPROVED\n\n**Decided by:** Bekim Bukolla **Date:** 2026-09-03\n")

    def write_mode(self, extra):
        out = os.path.join(self.root, "egress.jsonl")
        argv = [
            "write", "--provider", "anthropic", "--purpose", "p", "--data-class", "INTERNAL",
            "--decision", "docs/decisions/d.md", "--rule", "r", "--out", out,
        ] + extra
        code = record.main(argv, env={}, root=self.root)
        with open(out, encoding="utf-8") as handle:
            written = json.loads(handle.read().strip())
        return code, written

    def test_a_corrupt_precall_snapshot_is_marked_rather_than_used(self):
        precall = os.path.join(self.root, "precall.json")
        with open(precall, "w", encoding="utf-8") as handle:
            handle.write("{not json\n")
        code, written = self.write_mode(["--precall", precall])
        self.assertEqual(code, 0)
        self.assertIn("precall-snapshot:absent", written["source_hashes"])
        self.assertNotIn("not json", json.dumps(written))

    def test_an_empty_precall_hash_list_is_marked_absent(self):
        precall = os.path.join(self.root, "precall.json")
        with open(precall, "w", encoding="utf-8") as handle:
            json.dump({"source_hashes": []}, handle)
        _code, written = self.write_mode(["--precall", precall])
        self.assertIn("precall-snapshot:absent", written["source_hashes"])

    def test_a_present_prompt_source_is_named_by_hash(self):
        body = "the prompt the provider was shown\n"
        with open(os.path.join(self.root, "prompt.md"), "w", encoding="utf-8") as handle:
            handle.write(body)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        hashes = record.source_hashes(self.root, {}, prompt_source="prompt.md")
        self.assertIn("prompt-source:sha256:" + digest, hashes)


if __name__ == "__main__":
    unittest.main()
