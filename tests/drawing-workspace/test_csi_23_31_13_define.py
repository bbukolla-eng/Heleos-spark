import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFINE = ROOT / "docs/engineering/division23/csi-23-31-13/define"
RULES = "docs/superpowers/specs/2026-09-13-duct-measurement-rules.md"
RULES_SHA256 = "6395b248f4ae06eaf9c59af1369bc96ca5a2f4d111560394c6a43bdae6d454d6"
EXAMPLES = "tests/fixtures/duct-takeoff/2026-09-13-rule-examples.json"
EXAMPLES_SHA256 = "08ce9d1b0348b96de0b00dbc71a55a94e962c58f4b17239a61f3cf64e846e6ad"
CHILDREN = ("CSI-23-31-13.13", "CSI-23-31-13.16", "CSI-23-31-13.19")
CRITERIA = (
    "CSI-23-31-13-D01",
    "CSI-23-31-13-D02",
    "CSI-23-31-13-D03",
    "CSI-23-31-13-D04",
    "CSI-23-31-13-D05",
    "CSI-23-31-13-D06",
)


def load(name):
    return json.loads((DEFINE / name).read_text())


class MetalDuctDefineTest(unittest.TestCase):
    def test_catalogue_identity_matches_the_register_row(self):
        scope = load("scope.json")
        self.assertEqual(scope["section_id"], "CSI-23-31-13")
        self.assertEqual(scope["number"], "23 31 13")
        self.assertEqual(scope["title"], "Metal Ducts")
        self.assertEqual(scope["parent_id"], "CSI-23-31-00")
        self.assertEqual(scope["catalogue_edition"], "MasterFormat 2016 Numbers & Titles, April 2016")
        self.assertEqual(scope["catalogue_page"], 88)
        self.assertEqual(tuple(child["id"] for child in scope["children"]), CHILDREN)

    def test_approved_length_rules_are_bound_and_children_stay_gaps(self):
        bindings = load("rule-bindings.json")
        self.assertEqual(
            [item["rule_id"] for item in bindings["admitted_rules"]],
            [f"D{index:02d}" for index in range(1, 11)],
        )
        self.assertEqual(bindings["approved_packet"]["path"], RULES)
        self.assertEqual(bindings["approved_packet"]["sha256"], RULES_SHA256)
        gaps = load("input-gaps.json")
        affected = " ".join(gap["affected_scope"] for gap in gaps["gaps"])
        for child in ("23 31 13.13", "23 31 13.16", "23 31 13.19"):
            self.assertIn(child, affected)
        self.assertTrue(all(gap["closes_behavior"] is False for gap in gaps["gaps"]))

    def test_expected_cases_cite_the_admitted_examples(self):
        cases = load("expected-cases.json")
        self.assertEqual([case["case_id"] for case in cases["cases"]], [f"E{index:02d}" for index in range(1, 10)])
        self.assertEqual(cases["source"]["path"], EXAMPLES)
        self.assertEqual(cases["source"]["sha256"], EXAMPLES_SHA256)
        self.assertEqual(cases["unit_system"], "imperial")
        e01 = cases["cases"][0]["expected"]
        self.assertEqual(e01["group_lengths_ft"], ["8.00", "4.00"])
        self.assertEqual(e01["total_ft"], "12.00")

    def test_definition_is_not_accepted(self):
        acceptance = load("acceptance.json")
        packet = load("implementation-packet.json")
        self.assertEqual(acceptance["outcome"], "in_progress")
        self.assertIsNone(acceptance["review"])
        self.assertEqual(packet["readiness"], "not_ready")
        self.assertEqual(
            [item["criterion_id"] for item in acceptance["criteria"]],
            list(CRITERIA),
        )
        self.assertTrue(all(item["status"] == "blocked" for item in acceptance["criteria"]))


if __name__ == "__main__":
    unittest.main()
