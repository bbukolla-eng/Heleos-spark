"""Original synthetic positioned-page checks for the written requirement reader."""
import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("document_requirements", ROOT / "scripts/document_requirements.py")
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


def line(text, y, left=0.05, right=0.45):
    return {"text": text, "bbox": [left, y, right, y + 0.012], "words": []}


def page(lines, role="specification"):
    return {"revision_id": "a" * 64, "index": 3, "sheet_id": "b" * 64,
            "role": role, "width": 600, "height": 800, "lines": lines}


class RequirementReaderTests(unittest.TestCase):
    def test_agency_suffix_remains_part_of_explicit_section_identity(self):
        result = reader.parse_requirements(page([
            line("SECTION 23 21 13.00 20 - WATER HEATING", .02),
            line("Provide piping where shown.", .07),
            line("SECTION 23 21 13.00 10 - PROJECT VARIANT", .15),
            line("Submit a piping report.", .20),
        ]))
        self.assertEqual([r["section"] for r in result["requirements"]], ["23 21 13.00 20", "23 21 13.00 10"])
        self.assertEqual([s["section"] for s in result["sections"]], ["23 21 13.00 20", "23 21 13.00 10"])

    def test_three_line_reference_retains_its_directive_context(self):
        result = reader.parse_requirements(page([
            line("SECTION 23 21 13 - HYDRONIC PIPING", .02),
            line("Provide pipe insulation", .07),
            line("in accordance with", .087),
            line("SECTION 23 07 19", .104),
            line("Install isolation valves.", .18),
        ]))
        self.assertEqual([s["section"] for s in result["sections"]], ["23 21 13"])
        self.assertIn("SECTION 23 07 19", result["requirements"][0]["text"])
        self.assertEqual(result["requirements"][1]["section"], "23 21 13")

    def test_bare_other_division_heading_resets_mechanical_section(self):
        result = reader.parse_requirements(page([
            line("23 21 13 - HYDRONIC PIPING", .02),
            line("Provide isolation valves.", .07),
            line("26 05 00 - COMMON ELECTRICAL REQUIREMENTS", .15),
            line("Provide power wiring.", .20),
        ]))
        self.assertEqual([r["section"] for r in result["requirements"]], ["23 21 13", "26 05 00"])
        self.assertEqual([s["section"] for s in result["sections"]], ["23 21 13", "26 05 00"])

    def test_wrapped_section_references_do_not_change_supplied_section(self):
        for reference in ("Section 23 07 19 for piping insulation.",
                          "SECTION 23 07 19 FOR PIPING INSULATION.", "Section 23 07 19"):
            with self.subTest(reference=reference):
                result = reader.parse_requirements(page([
                    line("SECTION 23 21 13 - HYDRONIC PIPING", .02),
                    line("Provide insulation in accordance with", .07),
                    line(reference, .087),
                    line("Install isolation valves where shown.", .15),
                    line("SECTION 23 25 00 - HVAC WATER TREATMENT", .22),
                ]))
                self.assertEqual([x["section"] for x in result["sections"]], ["23 21 13", "23 25 00"])
                self.assertEqual(result["sections"], reader.section_headings(page([
                    line("SECTION 23 21 13 - HYDRONIC PIPING", .02),
                    line("Provide insulation in accordance with", .07), line(reference, .087),
                    line("Install isolation valves where shown.", .15),
                    line("SECTION 23 25 00 - HVAC WATER TREATMENT", .22)])))
                self.assertEqual(len(result["requirements"]), 2)
                self.assertIn(reference, result["requirements"][0]["text"])
                self.assertEqual(result["requirements"][1]["section"], "23 21 13")

    def test_decimal_section_identity_and_heading_only_scope_survive(self):
        result = reader.parse_requirements(page([
            line("SECTION 23 05 93.01 - TESTING AND BALANCING", .02),
            line("Submit the final balancing report.", .07),
            line("SECTION 23.09.23.13 - CONTROL NETWORK", .15),
        ]))
        self.assertEqual(result["requirements"][0]["section"], "23 05 93.01")
        self.assertEqual([x["section"] for x in result.get("sections", [])],
                         ["23 05 93.01", "23.09.23.13"])
        self.assertEqual(result["sections"][1]["source"]["bbox"], [.05, .15, .45, .162])

    def test_section_reference_does_not_create_supplied_section_and_non23_resets(self):
        result = reader.parse_requirements(page([
            line("SECTION 23 21 13 - HYDRONIC PIPING", .02),
            line("See Section 23 07 19 for insulation.", .07),
            line("SECTION 26 05 00.01 - ELECTRICAL", .15),
            line("Provide wiring where indicated.", .20),
        ]))
        self.assertEqual([x["section"] for x in result.get("sections", [])],
                         ["23 21 13", "26 05 00.01"])
        self.assertEqual(result["requirements"][1]["section"], "26 05 00.01")

    def test_wrapped_requirement_preserves_negation_conditions_and_exact_lines(self):
        first = "A. Do not insulate the condensate pipe"
        second = "unless it is exposed to freezing conditions."
        source = page([line("SECTION 23 07 19 - PIPING INSULATION", .02),
                       line(first, .06), line(second, .077)])
        untouched = copy.deepcopy(source)
        result = reader.parse_requirements(source)
        self.assertEqual(source, untouched)
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(result["requirements"]), 1)
        requirement = result["requirements"][0]
        self.assertEqual(requirement["text"], first + "\n" + second)
        self.assertEqual(requirement["section"], "23 07 19")
        self.assertEqual(requirement["categories"], ["piping", "insulation"])
        self.assertEqual(requirement["qualifiers"], ["negative", "conditional"])
        self.assertEqual(requirement["source_lines"][1]["text"], second)
        self.assertEqual(requirement["source"]["bbox"], [.05, .06, .45, .089])
        self.assertEqual(requirement["status"], "candidate")
        self.assertNotIn("quantity", requirement)

    def test_two_columns_and_separate_numbered_notes_never_merge(self):
        result = reader.parse_requirements(page([
            line("1. Provide AHU-01 with a factory", .1),
            line("1. Remove the existing fan", .1, .55, .95),
            line("mounted control panel.", .117, .065),
            line("and disconnect its controls.", .117, .565, .95),
            line("2. Do not remove AHU-1.", .134),
            line("2. See detail 4/M-401.", .134, .55, .95),
        ], "plan"))
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(result["requirements"]), 4)
        self.assertEqual(result["requirements"][0]["tags"], ["AHU-01"])
        self.assertIn("mounted control panel", result["requirements"][0]["text"])
        self.assertNotIn("disconnect", result["requirements"][0]["text"])
        removed = result["requirements"][1]
        self.assertIn("demolition", removed["categories"])
        self.assertIn("controls", removed["categories"])
        self.assertEqual(result["requirements"][2]["tags"], ["AHU-1"])
        reference = result["requirements"][3]
        self.assertIn("reference_only", reference["qualifiers"])
        self.assertEqual(reference["tags"], [])

    def test_uppercase_wrapped_notes_keep_their_continuation(self):
        result = reader.parse_requirements(page([
            line("MECHANICAL NOTES", .02),
            line("1. PROVIDE EF-1 WITH A FACTORY", .08),
            line("MOUNTED CONTROL PANEL", .097),
            line("AND SUPPORTS WHERE SHOWN.", .114),
        ], "plan"))
        self.assertEqual(len(result["requirements"]), 1)
        requirement = result["requirements"][0]
        self.assertIn("MOUNTED CONTROL PANEL\nAND SUPPORTS", requirement["text"])
        self.assertIn("conditional", requirement["qualifiers"])
        self.assertIn("controls", requirement["categories"])
        self.assertIn("accessories", requirement["categories"])

    def test_full_mechanical_scope_and_explicit_references_are_candidates(self):
        statements = [
            "Provide a boiler and pump.",
            "Seal ductwork at all joints.",
            "Install grilles and diffusers where shown.",
            "Replace the refrigerant piping.",
            "Provide elbows and transitions.",
            "Insulate all chilled water piping.",
            "Connect thermostats to the BAS.",
            "Provide valves and hangers per Section 23 21 13.",
            "Demolish the abandoned steam pipe.",
            "Submit product data before fabrication.",
        ]
        result = reader.parse_requirements(page([line(value, .02 + i * .04)
                                                 for i, value in enumerate(statements)]))
        categories = {category for row in result["requirements"] for category in row["categories"]}
        self.assertEqual(categories, {"equipment", "ductwork", "air_devices", "piping", "fittings",
                                      "insulation", "controls", "accessories", "demolition", "general"})
        valve = result["requirements"][7]
        self.assertIsNone(valve["section"])
        self.assertEqual(valve["qualifiers"], ["reference"])

    def test_heading_context_does_not_turn_descriptions_into_requirements(self):
        result = reader.parse_requirements(page([
            line("SECTION 23 07 13 - DUCT INSULATION", .02),
            line("INSTALLATION", .06),
            line("A. Provide thickness indicated in the schedule.", .1),
            line("The following figure illustrates the arrangement.", .15),
            line("TEMPERATURE CONTROL", .2),
            line("B. All devices to be individually labelled.", .24),
        ]))
        self.assertEqual(len(result["requirements"]), 2)
        self.assertIn("insulation", result["requirements"][0]["categories"])
        self.assertEqual(len(result["requirements"][0]["context"]), 2)
        self.assertIn("controls", result["requirements"][1]["categories"])

    def test_explicit_full_width_section_applies_to_both_columns(self):
        result = reader.parse_requirements(page([
            line("SECTION 23 09 00 - CONTROLS", .02, .05, .95),
            line("A. Provide labels.", .08),
            line("B. Do not reuse existing sensors.", .08, .55, .95),
            line("SECTION 23 31 00 - DUCTWORK", .3, .05, .95),
            line("C. Provide hangers.", .36, .55, .95),
        ]))
        self.assertEqual([row["section"] for row in result["requirements"]],
                         ["23 09 00", "23 09 00", "23 31 00"])
        self.assertNotIn("controls", result["requirements"][2]["categories"])

    def test_ambiguous_internal_gap_is_preserved_and_flagged(self):
        merged = line("Provide duct insulation. Do not insulate pipe.", .1, .05, .95)
        merged["words"] = [{"text": "Provide duct insulation.", "bbox": [.05, .1, .3, .112]},
                           {"text": "Do not insulate pipe.", "bbox": [.65, .1, .95, .112]}]
        result = reader.parse_requirements(page([merged]))
        self.assertEqual(result["issues"][0]["code"], "ambiguous_line_columns")
        self.assertIn("ambiguous_layout", result["requirements"][0]["qualifiers"])
        self.assertEqual(result["requirements"][0]["text"], merged["text"])

    def test_unattached_exception_is_visible_and_not_silently_discarded(self):
        result = reader.parse_requirements(page([
            line("Provide insulation on supply ductwork.", .1),
            line("Except where existing insulation remains.", .14),
        ]))
        self.assertEqual(len(result["requirements"]), 1)
        self.assertEqual(result["issues"][0]["code"], "unattached_qualifier")
        self.assertEqual(result["issues"][0]["source"]["bbox"], [.05, .14, .45, .15200000000000002])

    def test_clause_split_and_stable_distinct_sources(self):
        source = page([line("Provide EF-01", .1), line("Install RF-2", .117),
                       line("Provide EF-01", .3)])
        first = reader.parse_requirements(source)
        second = reader.parse_requirements(copy.deepcopy(source))
        self.assertEqual(first, second)
        self.assertEqual(len(first["requirements"]), 3)
        self.assertEqual(len({row["id"] for row in first["requirements"]}), 3)
        self.assertEqual(first["requirements"][0]["tags"], ["EF-01"])

    def test_invalid_coordinates_do_not_produce_unsourced_requirements(self):
        invalid = line("Provide valves.", .1)
        invalid["bbox"] = [0, 0, float("nan"), 1]
        result = reader.parse_requirements(page([invalid]))
        self.assertEqual(result["requirements"], [])
        self.assertEqual(result["issues"][0]["code"], "invalid_line_source")
        self.assertEqual(reader.parse_requirements(page([line("Provide valves.", .1)], "excluded")),
                         {"requirements": [], "sections": [], "issues": []})


if __name__ == "__main__":
    unittest.main()
