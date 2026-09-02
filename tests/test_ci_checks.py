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
