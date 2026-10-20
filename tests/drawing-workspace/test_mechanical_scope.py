"""Source section coverage is independent of symbol/counting capabilities."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / "scripts/mechanical_scope.py"
spec = importlib.util.spec_from_file_location("mechanical_scope", PATH)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode()).hexdigest()


def source(index=0):
    return {"revision_id": "a" * 64, "index": index,
            "sheet_id": str(index) * 64, "bbox": [.1, .1, .8, .2]}


def requirement(key, section, text):
    return {"id": key, "section": section, "text": text, "source": source(),
            "qualifiers": ["conditional"] if "where" in text else [],
            "categories": ["general"], "context": [], "source_lines": []}


def reading():
    return {"id": "reading-1", "state": "completed", "stale": False,
            "sections": [
                {"section": "23 21 13", "text": "SECTION 23 21 13 HYDRONIC PIPING", "source": source()},
                {"section": "23.09.23.13", "text": "SECTION 23.09.23.13 CONTROLS", "source": source(1)},
                {"section": "23 07 19", "text": "SECTION 23 07 19 INSULATION", "source": source(2)}],
            "requirements": [requirement("r1", "23 21 13", "Provide isolation valves where shown."),
                             requirement("r2", "23 09 23.13", "Submit the controls test report.")],
            "pages": [dict(source(i), role="specification", state="read") for i in range(3)],
            "issues": []}


class MechanicalScopeTests(unittest.TestCase):
    def test_agency_suffixes_stay_separate_without_inferred_parentage(self):
        value = reading()
        value["sections"] = [{"section": n, "text": "SECTION " + n, "source": source(i)}
                             for i, n in enumerate(("23 21 13.00 20", "23 21 13.00 10"))]
        value["requirements"] = []
        self.assertEqual([s["section"] for s in self.view(value)["sections"]],
                         ["23 21 13.00 10", "23 21 13.00 20"])

    def test_terminal_reading_failure_is_not_reported_as_running(self):
        for state in ("failed", "interrupted"):
            value = reading(); value["state"] = state
            self.assertEqual(self.view(value)["state"], state)

    def test_readable_but_unclassified_pages_remain_visible(self):
        value = reading()
        value["pages"][1]["role"] = "unassigned"
        view = self.view(value)
        self.assertEqual([(p["source"]["index"], p["state"]) for p in view["unread_pages"]], [(1, "unassigned")])

    def test_stale_source_does_not_invent_an_earlier_review(self):
        value = reading(); value["stale"] = True
        view = self.view(value)
        row = self.row(view, "23 21 13")
        self.assertEqual(row["review_state"], "stale")
        self.assertEqual(row["counts"]["stale"], 0)
        self.assertEqual(row["requirements"][0]["review_state"], "pending")

    def view(self, value, reviews=None):
        return core.build_view(value, reviews or {})

    def row(self, view, section):
        return next((r for r in view.get("sections", []) if r["section"] == section), {})

    def test_all_supplied_sections_include_heading_only_and_decimal_subsection(self):
        value = reading()
        before = copy.deepcopy(value)
        view = self.view(value)
        self.assertEqual([s["section"] for s in view.get("sections", [])],
                         ["23 07 19", "23 09 23.13", "23 21 13"])
        self.assertEqual(self.row(view, "23 07 19").get("review_state"), "no_requirements_identified")
        self.assertIs(view.get("project_coverage_verified"), False)
        self.assertIs(view.get("product_coverage_verified"), False)
        self.assertEqual(value, before)

    def test_current_review_is_invalidated_by_changed_clause_and_stale_reading(self):
        value = reading(); r = value["requirements"][0]
        reviews = {"r1": {"fingerprint": digest(r), "disposition": "applicable", "reason": "Project clause"}}
        view = self.view(value, reviews)
        self.assertEqual(self.row(view, "23 21 13").get("review_state"), "requirements_reviewed")
        r["text"] = "Provide replacement valves where shown."
        view = self.view(value, reviews)
        self.assertEqual(self.row(view, "23 21 13").get("counts", {}).get("stale"), 1)
        row = self.row(view, "23 21 13")["requirements"][0]
        self.assertEqual((row["disposition"], row["stored_disposition"]), ("pending", "applicable"))
        value["stale"] = True
        self.assertEqual(self.view(value, reviews).get("state"), "stale")

    def test_non23_requirements_and_unknown_section_are_not_fabricated_into_23(self):
        value = reading()
        value["requirements"] += [requirement("r3", "26 05 00.01", "Provide power wiring."),
                                  requirement("r4", None, "Submit commissioning records.")]
        view = self.view(value)
        self.assertEqual(view.get("other_division_requirement_count"), 1)
        self.assertEqual([r["id"] for r in view.get("unassigned_requirements", [])], ["r4"])
        self.assertEqual(len(view.get("sections", [])), 3)

    def test_legacy_and_unread_pages_keep_gaps_visible(self):
        value = reading(); del value["sections"]
        value["pages"][1]["state"] = "needs_ocr"
        view = self.view(value)
        self.assertIs(view.get("section_index_available"), False)
        self.assertEqual([p["state"] for p in view.get("unread_pages", [])], ["needs_ocr"])
        self.assertIn("section_index_missing", [x["code"] for x in view.get("issues", [])])
        self.assertEqual([s["section"] for s in view.get("sections", [])], ["23 09 23.13", "23 21 13"])

    def test_all_extracted_reviews_do_not_claim_complete_project(self):
        value = reading()
        reviews = {r["id"]: {"fingerprint": digest(r), "disposition": "excluded", "reason": "Outside selected scope"}
                   for r in value["requirements"]}
        view = self.view(value, reviews)
        self.assertEqual(self.row(view, "23 09 23.13").get("counts", {}).get("excluded"), 1)
        self.assertIs(view.get("project_coverage_verified"), False)
        self.assertIs(self.view(None).get("available"), False)


if __name__ == "__main__":
    unittest.main()
