"""Exercise delivery routing against small, independently specified CSI trees."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify-csi-division23.py"
SPEC = importlib.util.spec_from_file_location("csi_delivery_structure", SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class DeliveryStructureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plans = self.root / "docs/plans"
        self.plans.mkdir(parents=True)
        self.register = {
            "schema_version": 2,
            "sections": [
                {
                    "id": "CSI-23-00-00", "number": "23 00 00", "title": "HVAC",
                    "parent_id": None, "children": ["CSI-23-05-00"],
                    "source": {"page": 1}, "delivery_plan": "docs/plans/root.md",
                    "task_ids": ["CSI-23-00-00-DEFINE", "CSI-23-00-00-RESULT",
                                 "CSI-23-00-00-CONNECT", "CSI-23-00-00-QUALIFY"],
                },
                {
                    "id": "CSI-23-05-00", "number": "23 05 00", "title": "Common work",
                    "parent_id": "CSI-23-00-00", "children": [],
                    "source": {"page": 2}, "delivery_plan": "docs/plans/common.md",
                    "task_ids": ["CSI-23-05-00-DEFINE", "CSI-23-05-00-RESULT",
                                 "CSI-23-05-00-CONNECT", "CSI-23-05-00-QUALIFY"],
                },
            ],
        }
        self.contracts = {
            "schema_version": 2,
            "templates": {
                "DEFINE": {"criteria": [{"id": "D01", "expected": "Scope pinned", "check": "Inspect scope"}]},
                "RESULT": {"criteria": [{"id": "R01", "expected": "Result supported", "check": "Compare result"}]},
                "CONNECT": {"criteria": [{"id": "C01", "expected": "Connection works", "check": "Exercise workflow"}]},
                "QUALIFY": {"criteria": [{"id": "Q01", "expected": "Cases pass", "check": "Inspect evidence"}]},
            },
            "tasks": [],
        }
        for section in self.register["sections"]:
            (self.root / section["delivery_plan"]).write_text("# Section plan\n")
            for stage, criterion in (("DEFINE", "D01"), ("RESULT", "R01"),
                                     ("CONNECT", "C01"), ("QUALIFY", "Q01")):
                self.contracts["tasks"].append({
                    "id": section["id"] + "-" + stage,
                    "section_id": section["id"], "stage": stage, "state": "planned",
                    "criteria_ids": [section["id"] + "-" + criterion],
                })

    def save(self):
        (self.plans / "division-23-section-register.json").write_text(json.dumps(self.register))
        (self.plans / "division-23-task-contracts.json").write_text(json.dumps(self.contracts))

    def errors(self):
        self.save()
        return VALIDATOR.validate(self.root)

    def assert_invalid(self, fragment):
        errors = self.errors()
        self.assertTrue(errors, "Invalid fixture was accepted")
        self.assertIn(fragment, "\n".join(errors).lower())

    def test_valid_tree_and_cli_leave_inputs_unchanged(self):
        self.save()
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual([], VALIDATOR.validate(self.root))
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_decimal_section_identity_is_supported(self):
        old = "CSI-23-05-00"
        new = "CSI-23-05-48.13"
        self.register = json.loads(json.dumps(self.register).replace(old, new).replace("23 05 00", "23 05 48.13"))
        self.contracts = json.loads(json.dumps(self.contracts).replace(old, new))
        self.assertEqual([], self.errors())

    def test_duplicate_section_is_rejected(self):
        self.register["sections"].append(copy.deepcopy(self.register["sections"][1]))
        self.assert_invalid("duplicate section")

    def test_duplicate_task_is_rejected(self):
        self.contracts["tasks"].append(copy.deepcopy(self.contracts["tasks"][0]))
        self.assert_invalid("duplicate task")

    def test_stage_must_match_task_identity(self):
        self.contracts["tasks"][0]["stage"] = "RESULT"
        self.assert_invalid("task id")

    def test_criteria_must_match_section_template(self):
        self.contracts["tasks"][0]["criteria_ids"] = ["CSI-23-05-00-D01"]
        self.assert_invalid("criteria_ids")

    def test_criteria_order_is_preserved(self):
        self.contracts["templates"]["DEFINE"]["criteria"].append({
            "id": "D02", "expected": "Cases frozen", "check": "Inspect cases"})
        for task in self.contracts["tasks"]:
            if task["stage"] == "DEFINE":
                task["criteria_ids"] = [task["section_id"] + "-D02", task["section_id"] + "-D01"]
        self.assert_invalid("criteria_ids")

    def test_parent_orphan_is_rejected(self):
        self.register["sections"][1]["parent_id"] = "CSI-23-99-99"
        self.assert_invalid("parent")

    def test_task_orphan_is_rejected(self):
        self.contracts["tasks"][0]["section_id"] = "CSI-23-99-99"
        self.assert_invalid("section_id")

    def test_missing_task_is_rejected(self):
        self.contracts["tasks"].pop()
        self.assert_invalid("missing task")

    def test_missing_card_is_rejected(self):
        (self.plans / "common.md").unlink()
        self.assert_invalid("delivery_plan")

    def test_self_cycle_is_rejected_even_with_inverse_children(self):
        self.register["sections"][0]["children"] = []
        self.register["sections"][1]["parent_id"] = "CSI-23-05-00"
        self.register["sections"][1]["children"] = ["CSI-23-05-00"]
        self.assert_invalid("cycle")

    def test_children_must_be_inverse_of_parent(self):
        self.register["sections"][0]["children"] = []
        self.assert_invalid("children")

    def test_duplicate_children_are_rejected(self):
        self.register["sections"][0]["children"] *= 2
        self.assert_invalid("children")

    def test_agency_identity_is_rejected(self):
        self.register["sections"][1]["id"] = "UFGS-230500"
        self.assert_invalid("csi identity")

    def test_number_must_match_identity(self):
        self.register["sections"][1]["number"] = "23 05 13"
        self.assert_invalid("number")

    def test_path_traversal_is_rejected_even_if_target_exists(self):
        self.register["sections"][1]["delivery_plan"] = "docs/plans/../plans/common.md"
        self.assert_invalid("unsafe")

    def test_absolute_card_path_is_rejected(self):
        self.register["sections"][1]["delivery_plan"] = str(self.plans / "common.md")
        self.assert_invalid("unsafe")

    def test_card_symlink_cannot_escape_root(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "outside.md"
            target.write_text("# Outside\n")
            card = self.plans / "common.md"
            card.unlink()
            card.symlink_to(target)
            self.assert_invalid("escapes")

    def test_non_markdown_card_is_rejected(self):
        self.register["sections"][1]["delivery_plan"] = "docs/plans/common.json"
        (self.plans / "common.json").write_text("{}")
        self.assert_invalid("markdown")

    def test_exactly_one_designated_root_is_required(self):
        self.register["sections"][1]["parent_id"] = None
        self.assert_invalid("root")

    def test_register_task_list_cannot_omit_stage(self):
        self.register["sections"][0]["task_ids"].pop()
        self.assert_invalid("task_ids")

    def test_source_page_must_be_positive_integer_not_boolean(self):
        for value in (0, -1, True, "2", None):
            with self.subTest(value=value):
                self.register["sections"][1]["source"]["page"] = value
                self.assert_invalid("source.page")

    def test_invalid_template_fields_are_rejected(self):
        for field in ("id", "expected", "check"):
            with self.subTest(field=field):
                original = copy.deepcopy(self.contracts)
                self.contracts["templates"]["DEFINE"]["criteria"][0][field] = " "
                self.assert_invalid("criteria")
                self.contracts = original

    def test_duplicate_template_criteria_are_rejected(self):
        criteria = self.contracts["templates"]["DEFINE"]["criteria"]
        criteria.append(copy.deepcopy(criteria[0]))
        self.assert_invalid("duplicate criterion")

    def test_blank_task_state_is_rejected(self):
        self.contracts["tasks"][0]["state"] = " "
        self.assert_invalid("state")

    def test_malformed_shapes_return_errors_instead_of_raising(self):
        fixtures = [
            ([], {}), ({"schema_version": 1, "sections": []}, {}),
            ({"schema_version": 2, "sections": [None]}, self.contracts),
            (self.register, {"schema_version": 2, "templates": [], "tasks": [None]}),
        ]
        for register, contracts in fixtures:
            with self.subTest(register=register):
                self.register, self.contracts = register, contracts
                self.assertTrue(self.errors())

    def test_missing_and_unreadable_json_are_clean_cli_failures(self):
        for content in (None, "{broken", "[]"):
            with self.subTest(content=content):
                self.save()
                path = self.plans / "division-23-section-register.json"
                if content is None:
                    path.unlink()
                else:
                    path.write_text(content)
                result = subprocess.run([sys.executable, str(SCRIPT), "--root", str(self.root)],
                                        capture_output=True, text=True)
                self.assertEqual(1, result.returncode)
                self.assertNotIn("Traceback", result.stderr + result.stdout)
                self.assertTrue(VALIDATOR.validate(self.root))


if __name__ == "__main__":
    unittest.main()
