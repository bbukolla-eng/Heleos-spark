"""Behaviour checks for the pure qualified air-device attribute helper.

Expectations are written independently of the implementation: every imperial
inch value is stated as an exact fraction or an exact decimal derived from the
definitions 1 in = 1, 1 ft = 12 in, 1 in = 25.4 mm and 1 m = 1000 mm, and every
grouping expectation is stated as an equality or inequality between whole
records. No product, model or native import is used.
"""
from decimal import Decimal, Inexact, Rounded, ROUND_UP, localcontext
from fractions import Fraction
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / "scripts/air_device_attributes.py"
spec = importlib.util.spec_from_file_location("air_device_attributes_test", PATH)
air = importlib.util.module_from_spec(spec)
spec.loader.exec_module(air)

EVIDENCE = ["plan-M1.1-symbol-7", "schedule-SD-1-row", "legend-SD1"]


def entry(state="unknown", value=None, evidence=()):
    return {"state": state, "value": value, "evidence_ids": list(evidence)}


def known(value, evidence=("plan-M1.1-symbol-7",)):
    return entry("known", value, evidence)


def size(shape, dimensions, unit, text="24x24 face"):
    return {"shape": shape, "dimensions": list(dimensions), "unit": unit, "original_text": text}


def length(value, unit, text="6'-0\" assembly"):
    return {"value": value, "unit": unit, "original_text": text}


def record(**overrides):
    """A record with every field present; overrides replace whole field entries."""
    built = dict((name, entry()) for name in air.FIELDS)
    built.update(overrides)
    return built


def diffuser():
    """AC01/AC09 shaped record: a supported new supply diffuser."""
    return record(
        family=known("diffuser"),
        type_tag=known("SD-1"),
        system=known("AHU-1", ("schedule-SD-1-row",)),
        service=known("supply"),
        work_status=known("new_install"),
        face_size=known(size("rectangular", ["24", "24"], "in", '24"x24" face')),
        neck_size=known(size("round", ["8"], "in", '8" round neck')),
        opening_size=entry("not_supplied"),
        assembly_length=entry("not_applicable", None, ("schedule-SD-1-row",)),
        slot_count=entry("not_applicable", None, ("schedule-SD-1-row",)),
    )


class ContractTests(unittest.TestCase):
    def normalize(self, attributes, evidence=None):
        return air.normalize_attributes(attributes, list(EVIDENCE if evidence is None else evidence))

    def rejects(self, code, call, *args):
        with self.assertRaises(air.AirDeviceAttributeError) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertIn(code, air.ERROR_CODES)
        self.assertTrue(str(caught.exception))
        return caught.exception

    def test_version_and_declared_field_order(self):
        self.assertEqual(air.VERSION, "air-device-attributes-1")
        self.assertEqual(air.FIELDS, ("family", "type_tag", "system", "service", "work_status",
                                      "face_size", "neck_size", "opening_size",
                                      "assembly_length", "slot_count"))
        self.assertEqual(list(air.canonical_attributes(diffuser())), list(air.FIELDS))
        self.assertEqual(list(air.imperial_attributes(diffuser())), list(air.FIELDS))

    def test_equivalent_units_are_exactly_equal_after_canonicalisation(self):
        inches = self.normalize(record(face_size=known(size("round", ["1"], "in", 'one inch'))), EVIDENCE)
        millimetres = record(face_size=known(size("round", ["25.4"], "mm", '25.4 mm')))
        feet = record(assembly_length=known(length("1", "ft", "one foot")))
        metres = record(assembly_length=known(length("0.3048", "m", "0.3048 m")))
        self.assertEqual(air.canonical_attributes(inches)["face_size"]["value"]["dimensions"],
                         [{"n": "1", "d": "1"}])
        self.assertEqual(air.canonical_attributes(millimetres)["face_size"]["value"]["dimensions"],
                         [{"n": "1", "d": "1"}])
        self.assertEqual(air.canonical_attributes(feet)["assembly_length"]["value"],
                         {"value": {"n": "12", "d": "1"}, "unit": "in"})
        self.assertEqual(air.canonical_attributes(metres)["assembly_length"]["value"],
                         {"value": {"n": "12", "d": "1"}, "unit": "in"})
        self.assertEqual(air.canonical_attributes(inches)["face_size"],
                         air.canonical_attributes(millimetres)["face_size"])
        self.assertEqual(air.canonical_attributes(feet), air.canonical_attributes(metres))

    def test_metric_inputs_keep_their_exact_rational_inches(self):
        one_mm = record(neck_size=known(size("round", ["1"], "mm", "1 mm neck")))
        canonical = air.canonical_attributes(one_mm)["neck_size"]["value"]
        self.assertEqual(canonical, {"shape": "round", "dimensions": [{"n": "5", "d": "127"}], "unit": "in"})
        # 5/127 in is 0.0393700787... so the six place display loses information.
        self.assertEqual(air.imperial_attributes(one_mm)["neck_size"]["value"]["dimensions"], ["0.039370"])
        self.assertNotEqual(Fraction(5, 127), Fraction("0.039370"))

    def test_unequal_dimensions_may_share_one_display(self):
        exact = record(face_size=known(size("round", ["1"], "in", 'exactly 1"')))
        nearly = record(face_size=known(size("round", ["1.0000004"], "in", 'nearly 1"')))
        self.assertEqual(air.imperial_attributes(exact)["face_size"]["value"]["dimensions"], ["1.000000"])
        self.assertEqual(air.imperial_attributes(nearly)["face_size"]["value"]["dimensions"], ["1.000000"])
        self.assertNotEqual(air.canonical_attributes(exact)["face_size"],
                            air.canonical_attributes(nearly)["face_size"])

    def test_display_uses_half_even_rounding_at_six_places(self):
        cases = {"0.0000005": "0.000000", "0.0000015": "0.000002", "0.0000025": "0.000002",
                 "0.0000006": "0.000001", "24": "24.000000", "1000000000000": "1000000000000.000000"}
        for source, shown in cases.items():
            built = record(face_size=known(size("round", [source], "in", "source " + source)))
            self.assertEqual(air.imperial_attributes(built)["face_size"]["value"]["dimensions"], [shown])
            self.assertEqual(Decimal(shown).as_tuple().exponent, -6)

    def test_size_roles_and_dimension_order_stay_distinct(self):
        face = record(face_size=known(size("rectangular", ["24", "12"], "in", "24x12")))
        swapped = record(face_size=known(size("rectangular", ["12", "24"], "in", "12x24")))
        neck = record(neck_size=known(size("rectangular", ["24", "12"], "in", "24x12")))
        self.assertNotEqual(air.canonical_attributes(face), air.canonical_attributes(swapped))
        self.assertNotEqual(air.canonical_attributes(face), air.canonical_attributes(neck))
        self.assertEqual(air.canonical_attributes(face)["face_size"]["value"]["dimensions"],
                         [{"n": "24", "d": "1"}, {"n": "12", "d": "1"}])
        self.assertIsNone(air.canonical_attributes(face)["neck_size"]["value"])

    def test_unicode_source_spaces_survive_and_group_without_rounding(self):
        for space in ("\u00a0", "\u202f", "\u2007"):
            source = "6" + space + "ft"
            built = record(assembly_length=known(length("6", "ft", source)),
                           service=known("Supply" + space + "Air"))
            self.assertEqual(self.normalize(built)["assembly_length"]["value"]["original_text"], source)
            self.assertEqual(air.imperial_attributes(built)["assembly_length"]["value"]["original_text"], source)
            self.assertEqual(air.canonical_attributes(built)["service"]["value"], "supply air")
        for control in ("\x00", "\x1f", "\x7f", "\x85", "\ud800"):
            self.rejects("value_invalid", self.normalize, record(service=known("Supply" + control + "Air")))

    def test_revalidation_enforces_reference_budget_before_scanning(self):
        from unittest.mock import patch
        built = record(family=known("grille", ["note"] * 129))
        for call in (air.canonical_attributes, air.imperial_attributes):
            with patch.object(air, "_evidence_id", side_effect=AssertionError("oversized references scanned")):
                self.rejects("evidence_invalid", call, built)

    def test_system_and_service_are_separate_fields(self):
        one = record(system=known("AHU-1"), service=known("supply"))
        other = record(system=known("supply"), service=known("AHU-1"))
        self.assertNotEqual(air.canonical_attributes(one), air.canonical_attributes(other))
        self.assertEqual(air.canonical_attributes(one)["system"]["value"], "ahu-1")
        self.assertEqual(air.canonical_attributes(one)["service"]["value"], "supply")

    def test_text_grouping_collapses_case_and_whitespace_without_touching_the_source(self):
        source = "  SD   1b  "
        built = record(type_tag=known(source))
        self.assertEqual(self.normalize(built)["type_tag"]["value"], source)
        self.assertEqual(air.canonical_attributes(built)["type_tag"]["value"], "sd 1b")
        self.assertEqual(air.canonical_attributes(record(type_tag=known("SD 1B")))["type_tag"]["value"],
                         "sd 1b")
        self.assertNotEqual(air.canonical_attributes(built)["type_tag"]["value"],
                            air.canonical_attributes(record(type_tag=known("SD-1B")))["type_tag"]["value"])

    def test_the_four_missingness_states_remain_distinct(self):
        seen = []
        for state in air.STATES:
            evidence = ("legend-SD1",) if state in air.EVIDENCE_REQUIRED_STATES else ()
            value = "diffuser" if state == "known" else None
            built = record(family=entry(state, value, evidence))
            canonical = air.canonical_attributes(built)["family"]
            self.assertEqual(canonical["state"], state)
            self.assertNotIn(canonical, seen)
            seen.append(canonical)
        self.assertEqual(len(seen), 4)
        for state in ("unknown", "not_supplied", "not_applicable"):
            self.assertIsNone(air.canonical_attributes(record(
                family=entry(state, None, ("legend-SD1",))))["family"]["value"])

    def test_non_known_states_must_carry_a_null_value(self):
        for state in ("unknown", "not_supplied", "not_applicable"):
            built = record(family=entry(state, "diffuser", ("legend-SD1",)))
            self.rejects("state_invalid", self.normalize, built)
        self.rejects("state_invalid", self.normalize, record(family=entry("missing")))
        self.rejects("state_invalid", self.normalize, record(family=entry(None)))

    def test_evidence_presence_rules_by_state(self):
        for state in ("known", "not_applicable"):
            value = "grille" if state == "known" else None
            self.rejects("evidence_invalid", self.normalize, record(family=entry(state, value, ())))
            self.normalize(record(family=entry(state, value, ("legend-SD1",))))
        self.normalize(record(family=entry("unknown")))
        self.normalize(record(family=entry("not_supplied", None, ("legend-SD1",))))

    def test_evidence_identifiers_must_be_supplied_unique_and_bounded(self):
        self.rejects("evidence_invalid", self.normalize,
                     record(family=known("grille", ("not-supplied-id",))))
        self.rejects("evidence_invalid", self.normalize,
                     record(family=known("grille", ("legend-SD1", "legend-SD1"))))
        many = [str(index) for index in range(129)]
        self.rejects("evidence_invalid", air.normalize_attributes,
                     record(family=known("grille", many)), many)
        for bad in [None, 7, True, b"id", ["id"], "", " ", "a\nb", "a\tb", "x" * 161]:
            self.rejects("evidence_invalid", air.normalize_attributes,
                         record(family=known("grille", ("legend-SD1",))), ["legend-SD1", bad])
        self.rejects("evidence_invalid", air.normalize_attributes, record(), ["a", "a"])
        self.rejects("evidence_invalid", air.normalize_attributes, record(),
                     [str(index) for index in range(2001)])
        self.rejects("evidence_invalid", air.normalize_attributes, record(), ("legend-SD1",))
        self.rejects("evidence_invalid", self.normalize,
                     record(family={"state": "known", "value": "grille", "evidence_ids": None}))
        self.assertEqual(len(air.normalize_attributes(record(), [str(i) for i in range(2000)])),
                         len(air.FIELDS))

    def test_one_identifier_may_support_several_fields(self):
        shared = record(family=known("grille", ("legend-SD1",)), service=known("return", ("legend-SD1",)))
        normalized = self.normalize(shared)
        self.assertEqual(normalized["family"]["evidence_ids"], ["legend-SD1"])
        self.assertEqual(normalized["service"]["evidence_ids"], ["legend-SD1"])
        self.assertEqual(air.imperial_attributes(shared)["service"]["evidence_ids"], ["legend-SD1"])

    def test_caller_objects_are_neither_read_back_nor_mutated(self):
        source = diffuser()
        dimensions = source["face_size"]["value"]["dimensions"]
        evidence = source["family"]["evidence_ids"]
        normalized = self.normalize(source)
        canonical = air.canonical_attributes(source)
        dimensions.append("99")
        evidence.append("legend-SD1")
        source["family"]["state"] = "unknown"
        self.assertEqual(normalized["face_size"]["value"]["dimensions"], ["24", "24"])
        self.assertEqual(normalized["family"]["evidence_ids"], ["plan-M1.1-symbol-7"])
        self.assertEqual(normalized["family"]["state"], "known")
        self.assertEqual(canonical["face_size"]["value"]["dimensions"],
                         [{"n": "24", "d": "1"}, {"n": "24", "d": "1"}])
        normalized["face_size"]["value"]["dimensions"][0] = "999"
        self.assertEqual(self.normalize(diffuser())["face_size"]["value"]["dimensions"], ["24", "24"])

    def test_dimension_syntax_and_bounds(self):
        bad = [24, 24.0, Fraction(24), Decimal("24"), True, None, ["24"], {"value": "24"},
               "+24", "-24", "24.", ".5", "1e3", "1E3", "24 ", " 24", "0", "0.0", "24,5",
               "inf", "NaN", "١٢", "1" * 41, "1." + "0" * 19, "1000000000001"]
        for value in bad:
            self.rejects("dimension_invalid", self.normalize,
                         record(face_size=known(size("round", [value], "in", "hostile"))))
            self.rejects("dimension_invalid", self.normalize,
                         record(assembly_length=known(length(value, "in", "hostile"))))
        for value in ["1000000000000", "0.000000000000000001", "0024.50", "1"]:
            self.normalize(record(assembly_length=known(length(value, "in", "accepted"))))

    def test_shape_unit_and_original_text_contracts(self):
        self.rejects("value_invalid", self.normalize,
                     record(face_size=known(size("round", ["8", "8"], "in"))))
        self.rejects("value_invalid", self.normalize,
                     record(face_size=known(size("rectangular", ["8"], "in"))))
        self.rejects("value_invalid", self.normalize,
                     record(face_size=known(size("square", ["8", "8"], "in"))))
        self.rejects("value_invalid", self.normalize,
                     record(face_size=known(size("oval", ["8"], "in"))))
        self.normalize(record(face_size=known(size("oval", ["8", "4"], "in"))))
        for unit in ["IN", "inch", "cm", "", None, 1]:
            self.rejects("value_invalid", self.normalize,
                         record(neck_size=known(size("round", ["8"], unit))))
            self.rejects("value_invalid", self.normalize,
                         record(assembly_length=known(length("8", unit))))
        for text in ["", " ", "a\nb", "x" * 513, None, 5]:
            self.rejects("value_invalid", self.normalize,
                         record(neck_size=known(size("round", ["8"], "in", text))))
            self.rejects("value_invalid", self.normalize,
                         record(assembly_length=known(length("8", "in", text))))

    def test_enumerated_and_integer_values(self):
        for family in air.FAMILIES:
            self.normalize(record(family=known(family)))
        for value in ["Diffuser", "duct", "", None, 1, ["diffuser"]]:
            self.rejects("value_invalid", self.normalize, record(family=known(value)))
        for status in air.WORK_STATUSES:
            self.normalize(record(work_status=known(status)))
        for value in ["unknown", "New_Install", None]:
            self.rejects("value_invalid", self.normalize, record(work_status=known(value)))
        for value in [0, -1, 1000001, True, False, "2", 2.0, Fraction(2), None]:
            self.rejects("value_invalid", self.normalize, record(slot_count=known(value)))
        for value in [1, 2, 1000000]:
            self.assertEqual(self.normalize(record(slot_count=known(value)))["slot_count"]["value"], value)
        for value in ["", " ", "a\rb", "x" * 257, 5, None]:
            self.rejects("value_invalid", self.normalize, record(type_tag=known(value)))

    def test_hostile_structures_and_subclasses(self):
        class Mapping(dict):
            pass

        class Text(str):
            pass

        broken = record()
        broken["extra"] = entry()
        self.rejects("structure_invalid", self.normalize, broken)
        missing = record()
        del missing["slot_count"]
        self.rejects("structure_invalid", self.normalize, missing)
        overfull = record()
        overfull["family"] = {"state": "unknown", "value": None, "evidence_ids": [], "note": "x"}
        self.rejects("structure_invalid", self.normalize, overfull)
        self.rejects("structure_invalid", self.normalize, record(family={"state": "unknown"}))
        self.rejects("structure_invalid", self.normalize, Mapping(record()))
        self.rejects("structure_invalid", self.normalize, record(family=Mapping(entry())))
        self.rejects("structure_invalid", self.normalize, list(air.FIELDS))
        self.rejects("structure_invalid", self.normalize, None)
        self.rejects("value_invalid", self.normalize, record(family=known(Text("diffuser"))))
        self.rejects("structure_invalid", self.normalize,
                     record(face_size=known({"shape": "round", "dimensions": ["8"], "unit": "in"})))
        self.rejects("structure_invalid", self.normalize,
                     record(assembly_length=known({"value": "8", "unit": "in"})))
        for dimensions in ["8", ("8",), {"0": "8"}, [["8"]], [{"n": "8"}]]:
            built = record(face_size={"state": "known",
                                      "value": {"shape": "round", "dimensions": dimensions,
                                                "unit": "in", "original_text": "hostile"},
                                      "evidence_ids": ["legend-SD1"]})
            with self.assertRaises(air.AirDeviceAttributeError) as caught:
                self.normalize(built)
            self.assertIn(caught.exception.code, {"value_invalid", "dimension_invalid"})

    def test_canonical_and_imperial_revalidate_without_a_supplied_list(self):
        self.rejects("evidence_invalid", air.canonical_attributes,
                     record(family=entry("known", "grille", ())))
        self.rejects("evidence_invalid", air.imperial_attributes,
                     record(family=entry("known", "grille", ("a\nb",))))
        self.rejects("state_invalid", air.canonical_attributes, record(family=entry("Known")))
        self.rejects("dimension_invalid", air.imperial_attributes,
                     record(face_size=known(size("round", ["-8"], "in"))))
        self.rejects("structure_invalid", air.canonical_attributes, {"family": entry()})
        canonical = air.canonical_attributes(diffuser())
        self.assertEqual(set(canonical["family"]), {"state", "value"})
        self.assertNotIn("evidence_ids", canonical["face_size"])
        self.assertNotIn("original_text", canonical["face_size"]["value"])

    def test_imperial_keeps_evidence_original_text_and_non_dimensional_values(self):
        imperial = air.imperial_attributes(diffuser())
        self.assertEqual(imperial["face_size"]["value"],
                         {"shape": "rectangular", "dimensions": ["24.000000", "24.000000"],
                          "unit": "in", "original_text": '24"x24" face'})
        self.assertEqual(imperial["face_size"]["evidence_ids"], ["plan-M1.1-symbol-7"])
        self.assertEqual(imperial["system"]["value"], "AHU-1")
        self.assertEqual(imperial["type_tag"]["value"], "SD-1")
        self.assertEqual(imperial["family"]["value"], "diffuser")
        self.assertEqual(imperial["opening_size"], {"state": "not_supplied", "value": None,
                                                    "evidence_ids": []})
        self.assertEqual(imperial["assembly_length"]["evidence_ids"], ["schedule-SD-1-row"])

    def test_length_and_slots_remain_attributes_not_quantities(self):
        # AC07: one installed linear diffuser assembly, two slots, stated 6 ft.
        assembly = record(
            family=known("linear_diffuser"),
            work_status=known("new_install"),
            assembly_length=known(length("6", "ft", "6'-0\" LD-1")),
            slot_count=known(2),
        )
        canonical = air.canonical_attributes(assembly)
        self.assertEqual(canonical["assembly_length"]["value"],
                         {"value": {"n": "72", "d": "1"}, "unit": "in"})
        self.assertEqual(canonical["slot_count"]["value"], 2)
        self.assertEqual(air.imperial_attributes(assembly)["assembly_length"]["value"],
                         {"value": "72.000000", "unit": "in", "original_text": "6'-0\" LD-1"})
        self.assertEqual(list(canonical), list(air.FIELDS))
        for name in ("count", "each", "quantity", "total", "linear_feet"):
            self.assertNotIn(name, canonical)

    def test_results_do_not_depend_on_the_ambient_decimal_context(self):
        built = record(face_size=known(size("rectangular", ["25.4", "1"], "mm", "25.4 x 1 mm")),
                       assembly_length=known(length("0.3048", "m", "0.3048 m")))
        expected_canonical = air.canonical_attributes(built)
        expected_imperial = air.imperial_attributes(built)
        with localcontext() as context:
            context.prec = 1
            context.rounding = ROUND_UP
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            self.assertEqual(air.canonical_attributes(built), expected_canonical)
            self.assertEqual(air.imperial_attributes(built), expected_imperial)
        self.assertEqual(expected_imperial["face_size"]["value"]["dimensions"],
                         ["1.000000", "0.039370"])
        self.assertEqual(expected_imperial["assembly_length"]["value"]["value"], "12.000000")

    def test_two_identical_records_group_together_without_becoming_one_device(self):
        first, second = diffuser(), diffuser()
        second["family"]["evidence_ids"] = ["legend-SD1"]
        second["type_tag"]["evidence_ids"] = ["schedule-SD-1-row"]
        # Same canonical grouping, deliberately different evidence: identity and
        # correspondence belong to the count engine, not to this helper.
        self.assertEqual(air.canonical_attributes(first), air.canonical_attributes(second))
        self.assertNotEqual(air.imperial_attributes(first), air.imperial_attributes(second))


if __name__ == "__main__":
    unittest.main()
