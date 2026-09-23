"""Original positioned-text cases for the implemented schedule reader."""
import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("document_schedule", ROOT / "scripts/document_schedule.py")
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)


def line(y, cells, height=0.012, char_width=0.0025):
    words = []
    for x, text in cells:
        for token in text.split():
            width = len(token) * char_width
            words.append({"text": token, "bbox": [x, y, x + width, y + height]})
            x += width + char_width
    return {"text": " ".join(word["text"] for word in words),
            "bbox": schedule._bbox(words), "words": words}


def page(*lines):
    return {"revision_id": "a" * 64, "index": 3, "sheet_id": "b" * 64,
            "role": "schedule", "width": 2400, "height": 1800, "lines": list(lines)}


def fields(row):
    return {item["name"]: item for item in row["fields"]}


class ScheduleReaderTests(unittest.TestCase):
    def test_explicit_fields_generic_tags_unknowns_and_evidence(self):
        document = page(
            line(0.05, [(0.10, "EXHAUST FAN SCHEDULE")]),
            line(0.09, [(0.04, "TAG"), (0.15, "QTY"), (0.25, "CFM"), (0.36, "CAPACITY"),
                        (0.49, "LOCATION"), (0.62, "CONNECTIONS"), (0.82, "NOTES")]),
            line(0.13, [(0.04, "CUSTOM-001"), (0.15, "2"), (0.25, "1200"), (0.36, "5 KW"),
                        (0.49, "ROOF"), (0.62, "12 IN"), (0.82, "1, 3")]),
            line(0.16, [(0.04, "CUSTOM-01"), (0.25, "TBD"), (0.49, "LEVEL 2")]),
            line(0.19, [(0.04, "CUSTOM-1"), (0.15, "0"), (0.25, "800")]),
        )
        original = copy.deepcopy(document)
        result = schedule.parse_schedules(document)
        self.assertEqual(document, original)
        self.assertEqual([row["tag"] for row in result["rows"]],
                         ["CUSTOM-001", "CUSTOM-01", "CUSTOM-1"])
        one, two, three = result["rows"]
        self.assertEqual((one["quantity"], two["quantity"], three["quantity"]), ("2", None, "0"))
        self.assertEqual(one["equipment_type"], "EXHAUST FAN")
        self.assertEqual(fields(one)["capacity"]["value"], "5 KW")
        self.assertEqual(fields(one)["connections"]["value"], "12 IN")
        self.assertEqual(fields(two)["quantity"]["value"], None)
        self.assertIn("quantity_unknown", two["issues"])
        self.assertEqual(fields(one)["airflow"]["source"],
                         {"revision_id": "a" * 64, "index": 3, "sheet_id": "b" * 64,
                          "bbox": [0.25, 0.13, 0.26, 0.142]})
        self.assertEqual(one["status"], "candidate")
        self.assertEqual(schedule.parse_schedules(document), result)

    def test_poppler_cell_lines_regroup_and_wrapped_header_and_notes(self):
        # PDF readers often emit each cell as a separate line. Wrapping must
        # neither lose cells nor create equipment from the continuation text.
        document = page(
            line(0.03, [(0.08, "AIR HANDLER SCHEDULE")]),
            line(0.065, [(0.04, "EQUIPMENT"), (0.30, "AIR")]),
            line(0.080, [(0.04, "TAG")]), line(0.080, [(0.19, "QTY")]),
            line(0.080, [(0.30, "FLOW")]), line(0.080, [(0.43, "NOTES")]),
            line(0.095, [(0.30, "(CFM)")]),
            line(0.125, [(0.04, "MAU - 001")]), line(0.125, [(0.19, "1")]),
            line(0.125, [(0.30, "4500")]), line(0.125, [(0.43, "FACTORY CONTROLS")]),
            line(0.140, [(0.43, "SEE NOTE 3")]),
            line(0.170, [(0.04, "MAU-002"), (0.19, "1"), (0.30, "4800")]),
            line(0.205, [(0.04, "SCHEDULE NOTES")]),
            line(0.225, [(0.04, "1"), (0.19, "PROVIDE ISOLATORS")]),
        )
        result = schedule.parse_schedules(document)
        self.assertEqual([row["tag"] for row in result["rows"]], ["MAU-001", "MAU-002"])
        self.assertEqual(fields(result["rows"][0])["tag"]["header"], "EQUIPMENT TAG")
        self.assertEqual(fields(result["rows"][0])["airflow"]["header"], "AIR FLOW (CFM)")
        self.assertEqual(fields(result["rows"][0])["notes"]["value"],
                         "FACTORY CONTROLS SEE NOTE 3")
        self.assertEqual(fields(result["rows"][0])["notes"]["source"]["bbox"],
                         [0.43, 0.125, 0.47, 0.152])

    def test_side_by_side_tables_do_not_mix_rows_or_titles(self):
        document = page(
            line(0.03, [(0.06, "FAN SCHEDULE"), (0.62, "PUMP SCHEDULE")]),
            line(0.07, [(0.04, "TAG"), (0.17, "QTY"), (0.29, "CFM"),
                        (0.60, "TAG"), (0.72, "QTY"), (0.84, "GPM")]),
            line(0.11, [(0.04, "EF-1"), (0.17, "2"), (0.29, "700"),
                        (0.60, "CHWP-1"), (0.72, "1"), (0.84, "40")]),
            line(0.14, [(0.04, "EF-2"), (0.17, "1"), (0.29, "400"),
                        (0.60, "CHWP-2"), (0.72, "2"), (0.84, "50")]),
        )
        result = schedule.parse_schedules(document)
        rows = {row["tag"]: row for row in result["rows"]}
        self.assertEqual(set(rows), {"EF-1", "EF-2", "CHWP-1", "CHWP-2"})
        self.assertEqual(rows["CHWP-1"]["equipment_type"], "PUMP")
        self.assertEqual(fields(rows["CHWP-1"])["flow"]["value"], "40")
        self.assertEqual(rows["EF-1"]["equipment_type"], "FAN")
        self.assertNotIn("flow", fields(rows["EF-1"]))
        self.assertNotIn("airflow", fields(rows["CHWP-1"]))
        self.assertLess(rows["EF-1"]["source"]["bbox"][2], 0.5)

    def test_multiple_tables_and_repeated_tags_keep_distinct_records(self):
        document = page(
            line(0.03, [(0.08, "FAN SCHEDULE")]),
            line(0.07, [(0.04, "TAG"), (0.18, "QTY"), (0.32, "CFM")]),
            line(0.11, [(0.04, "EF-01"), (0.18, "1"), (0.32, "300")]),
            line(0.24, [(0.08, "PUMP SCHEDULE")]),
            line(0.28, [(0.04, "MARK"), (0.18, "QTY"), (0.32, "GPM")]),
            line(0.32, [(0.04, "EF-01"), (0.18, "2"), (0.32, "40")]),
        )
        result = schedule.parse_schedules(document)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual([row["equipment_type"] for row in result["rows"]], ["FAN", "PUMP"])
        self.assertNotEqual(result["rows"][0]["id"], result["rows"][1]["id"])
        for row in result["rows"]:
            self.assertIn("duplicate_schedule_tag", row["issues"])
        self.assertIn("duplicate_schedule_tag", [issue["code"] for issue in result["issues"]])

    def test_quantity_is_explicit_only_and_unknown_columns_are_retained(self):
        document = page(
            line(0.04, [(0.04, "MARK"), (0.22, "TYPE"), (0.42, "ACCESSORY PACKAGE")]),
            line(0.08, [(0.04, "LVR-A"), (0.22, "LOUVER"), (0.42, "AP-7")]),
            line(0.11, [(0.04, "101"), (0.22, "DIFFUSER")]),
        )
        result = schedule.parse_schedules(document)
        self.assertEqual(len(result["rows"]), 2)
        self.assertTrue(all(row["quantity"] is None for row in result["rows"]))
        self.assertEqual(result["rows"][0]["equipment_type"], "LOUVER")
        self.assertEqual(fields(result["rows"][0])["other_accessory_package"]["value"], "AP-7")

    def test_missing_and_ambiguous_columns_are_unresolved(self):
        no_header = schedule.parse_schedules(page(line(0.1, [(0.1, "EF-1 1200 CFM")])))
        self.assertEqual(no_header["rows"], [])
        self.assertIn("schedule_header_missing", [issue["code"] for issue in no_header["issues"]])
        packed = schedule.parse_schedules(page(
            line(0.1, [(0.1, "TAG QTY CFM")]),
            line(0.15, [(0.1, "EF-1 1 1200")]),
        ))
        self.assertEqual(packed["rows"], [])
        self.assertIn("ambiguous_schedule_columns", [issue["code"] for issue in packed["issues"]])
        no_gutter = schedule.parse_schedules(page(
            line(0.1, [(0.1, "TAG"), (0.2, "QTY"), (0.3, "TAG"), (0.4, "QTY")]),
            line(0.15, [(0.1, "EF-1"), (0.2, "1"), (0.3, "P-1"), (0.4, "2")]),
        ))
        self.assertEqual(no_gutter["rows"], [])
        self.assertIn("ambiguous_schedule_columns", [issue["code"] for issue in no_gutter["issues"]])

    def test_ambiguous_cell_is_preserved_but_not_used_as_field_value(self):
        document = page(
            line(0.1, [(0.05, "TAG"), (0.2, "QTY"), (0.4, "LOCATION")]),
            line(0.15, [(0.05, "EF-1"), (0.29, "2-STANDBY"), (0.40, "ROOF")]),
        )
        result = schedule.parse_schedules(document)
        self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertIsNone(row["quantity"])
        self.assertIn("ambiguous_cell_columns", row["issues"])
        self.assertEqual(fields(row)["quantity"]["raw_value"], "2-STANDBY")
        self.assertTrue(fields(row)["quantity"]["ambiguous"])

    def test_tag_ranges_are_not_expanded_and_duplicates_are_not_counted(self):
        result = schedule.parse_schedules(page(
            line(0.05, [(0.05, "TAG"), (0.3, "QTY"), (0.5, "CFM")]),
            line(0.10, [(0.05, "EF-1 THRU EF-4"), (0.3, "4"), (0.5, "600")]),
            line(0.15, [(0.05, "EF-5"), (0.3, "-"), (0.5, "500")]),
            line(0.20, [(0.05, "EF-5"), (0.3, "TBD"), (0.5, "500")]),
        ))
        self.assertEqual(len(result["rows"]), 2)
        self.assertTrue(all(row["quantity"] is None for row in result["rows"]))
        self.assertTrue(all("duplicate_schedule_tag" in row["issues"] for row in result["rows"]))
        issue = next(issue for issue in result["issues"] if issue["code"] == "unsupported_tag_expression")
        self.assertEqual(issue["raw_text"], "EF-1 THRU EF-4")

    def test_staggered_header_and_body_cells_are_not_mixed(self):
        result = schedule.parse_schedules(page(
            line(0.03, [(0.04, "FAN SCHEDULE")]),
            line(0.07, [(0.04, "TAG"), (0.17, "QTY"), (0.29, "CFM")]),
            line(0.11, [(0.04, "EF-1"), (0.17, "2"), (0.29, "700"),
                        (0.60, "TAG"), (0.72, "QTY"), (0.84, "GPM")]),
            line(0.14, [(0.04, "EF-2"), (0.17, "1"), (0.29, "400"),
                        (0.60, "P-1"), (0.72, "1"), (0.84, "40")]),
        ))
        self.assertEqual(result["rows"], [])
        self.assertIn("mixed_schedule_header", [issue["code"] for issue in result["issues"]])

    def test_quantity_notes_remain_raw_cells_and_do_not_become_quantities(self):
        result = schedule.parse_schedules(page(
            line(0.05, [(0.05, "TAG"), (0.3, "QTY"), (0.5, "CFM")]),
            line(0.10, [(0.05, "EF-1"), (0.3, "SEE NOTE 1"), (0.5, "600")]),
            line(0.15, [(0.05, "EF-2"), (0.3, "2 EA"), (0.5, "500")]),
        ))
        self.assertIsNone(result["rows"][0]["quantity"])
        self.assertEqual(fields(result["rows"][0])["quantity"]["value"], "SEE NOTE 1")
        self.assertIn("quantity_expression_unresolved", result["rows"][0]["issues"])
        self.assertEqual(result["rows"][1]["quantity"], "2 EA")

    def test_malformed_coordinates_do_not_yield_records(self):
        for invalid in ([0.1, 0.1, float("nan"), 0.2], [0.2, 0.1, 0.1, 0.2],
                        [0.1, 0.1, 1.1, 0.2], [True, 0.1, 0.2, 0.2]):
            document = page(line(0.05, [(0.05, "TAG"), (0.3, "QTY")]))
            document["lines"][0]["words"][0]["bbox"] = invalid
            result = schedule.parse_schedules(document)
            self.assertEqual(result["rows"], [])
            self.assertEqual(result["issues"][0]["code"], "invalid_schedule_layout")


if __name__ == "__main__":
    unittest.main()
