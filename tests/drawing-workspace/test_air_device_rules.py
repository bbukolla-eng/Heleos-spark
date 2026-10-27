"""Approval binding must survive package relocation and fail on policy drift."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("air_rules_test", ROOT / "scripts/air_device_rules.py")
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


class ApprovedRulesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Application with spaces"
        manifest = json.loads((ROOT / rules.MANIFEST).read_text())
        self.paths = [rules.MANIFEST] + list(manifest["files"])
        for relative in self.paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)

    def test_relocated_policy_has_same_approved_identity(self):
        binding = rules.load_binding(self.root)
        self.assertEqual(binding, rules.load_binding(ROOT))
        self.assertEqual(binding["rules"], ["A%02d" % i for i in range(1, 13)])
        self.assertEqual(binding["examples"], ["AC%02d" % i for i in range(1, 18)])
        self.assertEqual(binding["unit"], "each")

    def test_each_policy_file_is_bound_to_exact_bytes(self):
        for relative in self.paths:
            with self.subTest(relative=relative):
                path = self.root / relative
                raw = path.read_bytes()
                path.write_bytes(raw + b" ")
                with self.assertRaises(rules.AirDeviceRuleError) as error:
                    rules.load_binding(self.root)
                self.assertEqual(error.exception.code, "rule_changed")
                path.write_bytes(raw)

    def test_missing_policy_is_not_treated_as_no_rules(self):
        (self.root / self.paths[-1]).unlink()
        with self.assertRaises(rules.AirDeviceRuleError) as error:
            rules.load_binding(self.root)
        self.assertEqual(error.exception.code, "rule_unavailable")

    def test_unapproved_external_policy_link_is_not_loaded(self):
        path = self.root / self.paths[-1]
        path.unlink()
        path.symlink_to(ROOT / self.paths[-1])
        with self.assertRaises(rules.AirDeviceRuleError) as error:
            rules.load_binding(self.root)
        self.assertEqual(error.exception.code, "rule_unavailable")

    def test_current_presentation_and_scale_do_not_change_authority(self):
        before = rules.load_binding(self.root)
        presentation = self.root / "docs/superpowers/specs/2026-09-14-air-device-counting-rules.md"
        presentation.parent.mkdir(parents=True)
        presentation.write_text("Unrelated presentation heading")
        (self.root / "scale.json").write_text('{"status":"withdrawn"}')
        self.assertEqual(before, rules.load_binding(self.root))

    def test_returned_collections_are_independent(self):
        binding = rules.load_binding(self.root)
        binding["rules"].clear()
        self.assertEqual(len(rules.load_binding(self.root)["rules"]), 12)


if __name__ == "__main__":
    unittest.main()
