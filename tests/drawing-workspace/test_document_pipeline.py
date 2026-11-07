"""Document content feeds saved equipment and requirements, then review/export.

Inputs here are original positioned-text fixtures, not labelled job drawings.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from xml.sax.saxutils import escape
import zipfile

import test_equipment_takeoff as equipment
import test_document_schedule as schedule

drawing = equipment.drawing
pipeline = drawing._document_module
REVISION = equipment.REVISION


def xhtml(lines):
    return ('<html><doc><page width="2400" height="1800">' + ''.join(
        '<line>' + ''.join('<word xMin="%s" yMin="%s" xMax="%s" yMax="%s">%s</word>' %
            (w["bbox"][0] * 2400, w["bbox"][1] * 1800,
             w["bbox"][2] * 2400, w["bbox"][3] * 1800, escape(w["text"]))
            for w in line["words"]) + '</line>' for line in lines) + '</page></doc></html>').encode()


class Reader:
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.quantity = "1"

    def identity(self):
        return {"kind": "original-positioned-text-fixture", "version": 1}

    def read(self, snapshot, index):
        self.calls.append(index)
        if index in self.fail:
            raise ValueError("Synthetic failed page")
        if snapshot.read() != b"synthetic verified PDF":
            raise AssertionError("Wrong verified source")
        line = schedule.line
        if index == 0:
            return xhtml([line(.15, [(.10, "CUSTOM-01")]), line(.25, [(.60, "CUSTOM-01")]),
                          line(.35, [(.10, "EF-7")])])
        if index == 1:
            return xhtml([line(.03, [(.10, "FAN SCHEDULE")]),
                line(.08, [(.05, "TAG"), (.25, "QTY"), (.45, "CFM"), (.65, "LOCATION")]),
                line(.13, [(.05, "CUSTOM-01"), (.25, self.quantity), (.45, "1200"), (.65, "ROOF")]),
                line(.18, [(.05, "CUSTOM-001"), (.45, "600"), (.65, "LEVEL 1")])])
        if index == 2:
            return xhtml([line(.05, [(.05, "SECTION 23 31 00 - HVAC DUCTS")]),
                line(.12, [(.05, "1. Provide isolation dampers for CUSTOM-01 where shown.")]),
                line(.20, [(.05, "2. Do not insulate exhaust ductwork unless noted.")])])
        return xhtml([])


class Workspace(equipment.Workspace):
    def state(self):
        return {"project": self.metadata["project"], "preview_mode": "image", "documents": [{
            "revision_id": REVISION, "name": "Original document reader fixture.pdf", "page_count": 4,
            "sheets": [{"index": i, "sheet_id": hashlib.sha256(str(i).encode()).hexdigest(),
                "width_micropoints": 2400000000, "height_micropoints": 1800000000, "rotation_degrees": 0}
                for i in range(4)]}]}


class DocumentPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(Path(self.temp.name))
        self.reader = Reader()
        self.engine = drawing.DocumentPipeline(self.workspace, self.reader)
        self.workspace.documents = self.engine
        self.addCleanup(self.engine.close)
        self.workspace.equipment = drawing.EquipmentEngine(self.workspace, self.reader)
        self.addCleanup(self.workspace.equipment.close)
        self.flow = drawing.TakeoffWorkflow(self.workspace)
        self.workspace.workflow = self.flow

    def command(self, action, values):
        return self.flow.command(action, {"version": self.flow.data["version"],
            "actor": "Fixture operator", "reason": "Checked original fixture", "values": values})

    def register(self):
        for i, role in enumerate(("plan", "schedule", "specification", "excluded")):
            self.command("page", {"source": {"revision_id": REVISION, "index": i}, "role": role,
                "label": "M-" + str(i), "building": "", "level": "", "note": "Fixture page"})

    def read(self):
        self.register()
        view = self.command("read_documents", {})
        self.engine.thread.join(timeout=5)
        self.assertFalse(self.engine.thread.is_alive())
        run = self.engine.result(view["document_run"])
        self.assertEqual(run["state"], "completed", run.get("error"))
        return run

    def test_content_builds_register_and_requirements_without_manual_answers(self):
        run = self.read()
        self.assertEqual(run["readable_pages"], 3)
        rows = {v["tag"]: v for v in run["schedule_rows"]}
        self.assertEqual(rows["CUSTOM-01"]["quantity"], "1")
        self.assertIsNone(rows["CUSTOM-001"]["quantity"])
        self.assertEqual(next(f["value"] for f in rows["CUSTOM-01"]["fields"] if f["name"] == "airflow"), "1200")
        register = {v["tag"]: v for v in run["equipment_register"]}
        self.assertEqual(len(register["CUSTOM-01"]["plan_occurrences"]), 2)
        self.assertIsNone(register["CUSTOM-01"]["quantity"])
        self.assertIn("no_schedule_row_match", register["EF-7"]["issues"])
        self.assertEqual(len(run["requirements"]), 2)
        self.assertEqual(len(register["CUSTOM-01"]["requirement_ids"]), 1)
        self.assertIn("Do not", run["requirements"][1]["text"])
        calls = list(self.reader.calls)
        view = self.command("extract_equipment", {"mode": "text"})
        reviewed = self.workspace.equipment.result(view["equipment_run"])
        self.assertEqual(self.reader.calls, calls, "Equipment must consume reading outputs, not scan the PDFs again")
        self.assertIn("CUSTOM-01", {v["tag"] for v in reviewed["rows"]})
        self.assertEqual(reviewed["document_run_id"], run["id"])
        self.assertTrue(all(v["reviewed_quantity"] is None for v in reviewed["rows"]))
        self.assertEqual(drawing.TakeoffWorkflow(self.workspace).view()["document_reading"]["id"], run["id"])
        self.assertEqual(self.command("read_documents", {})["document_run"], run["id"])
        self.assertEqual(self.reader.calls, calls)
        with zipfile.ZipFile(io.BytesIO(self.flow.export())) as archive:
            self.assertTrue({"schedule-fields.csv", "requirements.csv", "equipment-register.csv", "document-reading.json"} <= set(archive.namelist()))
            self.assertIn("1200", archive.read("schedule-fields.csv").decode("utf-8-sig"))
            self.assertIn("UNKNOWN", archive.read("equipment-register.csv").decode("utf-8-sig"))

    def test_mechanical_sections_review_reopen_export_and_source_change(self):
        original_read = self.reader.read
        statements = [
            ["SECTION 23 21 13 - HYDRONIC PIPING", "Provide isolation valves where shown."],
            ["SECTION 23 09 23 - CONTROLS", "Submit the controls validation report."],
            ["SECTION 23 07 19 - PIPING INSULATION", "Do not insulate existing piping unless noted."],
            ["SECTION 23 05 93.01 - TESTING AND BALANCING", "Submit final balancing results.",
             "SECTION 23 25 00 - WATER TREATMENT"],
        ]
        def read_scope(snapshot, index):
            if snapshot.read() != b"synthetic verified PDF":
                raise AssertionError("Wrong verified source")
            return xhtml([schedule.line(.05 + i * .08, [(.05, value)])
                          for i, value in enumerate(statements[index])])
        self.reader.read = read_scope
        self.addCleanup(setattr, self.reader, "read", original_read)
        for index in range(4):
            self.command("page", {"source": {"revision_id": REVISION, "index": index},
                "role": "specification", "label": "SPEC-" + str(index),
                "building": "", "level": "", "note": "Original scope fixture"})
        view = self.command("read_documents", {})
        self.engine.thread.join(timeout=5)
        self.assertFalse(self.engine.thread.is_alive())
        view = self.flow.view()
        coverage = view.get("mechanical_scope", {})
        self.assertEqual([s["section"] for s in coverage.get("sections", [])],
                         ["23 05 93.01", "23 07 19", "23 09 23", "23 21 13", "23 25 00"])
        self.assertEqual(coverage["sections"][-1]["review_state"], "no_requirements_identified")
        requirement = next(r for r in view["document_reading"]["requirements"] if r["section"] == "23 21 13")
        reviewed = self.command("requirement_review", {"requirement_id": requirement["id"], "disposition": "applicable"})
        row = next(r for r in reviewed["mechanical_scope"]["sections"] if r["section"] == "23 21 13")
        self.assertEqual(row["counts"]["applicable"], 1)
        reopened = drawing.TakeoffWorkflow(self.workspace).view()
        self.assertEqual(reopened["mechanical_scope"], reviewed["mechanical_scope"])
        with zipfile.ZipFile(io.BytesIO(self.flow.export())) as archive:
            exported = json.loads(archive.read("mechanical-scope.json"))
            self.assertEqual(exported, reviewed["mechanical_scope"])
            self.assertIn("23 05 93.01", archive.read("mechanical-sections.csv").decode("utf-8-sig"))
            self.assertIn("controls validation report", archive.read("mechanical-requirements.csv").decode("utf-8-sig"))
        self.command("page", {"source": {"revision_id": REVISION, "index": 0},
            "role": "addendum", "label": "Changed", "building": "", "level": "", "note": "Role changed"})
        stale = self.flow.view()["mechanical_scope"]
        row = next(r for r in stale["sections"] if r["section"] == "23 21 13")
        self.assertEqual(row["counts"]["applicable"], 0)
        self.assertEqual(row["requirements"][0]["review_state"], "stale")
        self.assertFalse(stale["project_coverage_verified"])

    def test_section_record_must_match_retained_original_lines(self):
        run = self.read()
        self.assertTrue(run.get("sections"), "Explicit section headings must be retained")
        run["sections"][0]["text"] = "SECTION 23 21 13 - FABRICATED HEADING"
        self.engine._save(run)
        with self.assertRaises(pipeline.DocumentError):
            self.engine.verified_result(run["id"])

    def test_reading_is_a_prerequisite_and_changes_make_it_stale(self):
        self.register()
        with self.assertRaisesRegex(drawing.WorkflowError, "Read the current project documents first"):
            self.command("extract_equipment", {"mode": "text"})
        run = self.read()
        requirement = run["requirements"][0]
        original = copy.deepcopy(requirement)
        view = self.command("requirement_review", {"requirement_id": requirement["id"], "disposition": "applicable"})
        self.assertEqual(view["requirement_reviews"][requirement["id"]]["disposition"], "applicable")
        self.assertEqual(self.engine.result(run["id"])["requirements"][0], original)
        self.command("page", {"source": {"revision_id": REVISION, "index": 2}, "role": "addendum",
            "label": "A", "building": "", "level": "", "note": "Changed source role"})
        self.assertTrue(self.flow.view()["document_reading"]["stale"])
        with self.assertRaises(drawing.WorkflowError):
            self.command("extract_equipment", {"mode": "text"})

    def test_failed_and_scanned_pages_preserve_unaffected_work(self):
        self.reader.fail = {0}
        run = self.engine.start([{"revision_id": REVISION, "index": i, "role": role}
            for i, role in enumerate(("plan", "schedule", "specification", "detail"))])
        self.engine.thread.join(timeout=5)
        result = self.engine.result(run["id"])
        self.assertEqual([v["state"] for v in result["pages"]], ["failed", "read", "read", "needs_ocr"])
        self.assertEqual(len(result["schedule_rows"]), 2)
        self.assertEqual(len(result["requirements"]), 2)
        self.assertTrue(all(v["quantity"] is None for v in result["equipment_register"]))

    def test_retry_failed_pages_and_reconcile_explicit_schedule_quantity(self):
        self.reader.fail = {0}
        first = self.read()
        self.reader.fail.clear()
        self.reader.quantity = "2"
        second = self.read()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["pages"][0]["state"], "read")
        view = self.command("extract_equipment", {"mode": "text"})
        engine = self.workspace.equipment
        run = engine.result(view["equipment_run"])
        occurrence = next(r for r in run["rows"] if r["tag"] == "CUSTOM-01")["plan"][0]
        result = engine.review(run["id"], occurrence["id"], 0, "include", "CUSTOM-01", "One physical unit on fixture", "Operator")
        row = next(r for r in result["rows"] if r["tag"] == "CUSTOM-01")
        self.assertEqual(row["schedule_quantity"], 2)
        self.assertIn("schedule_quantity_mismatch", row["issues"])

    def test_export_refuses_changed_reading_evidence(self):
        run = self.read()
        artifact = self.engine.root / "artifacts" / run["pages"][0]["layout"]["key"]
        artifact.write_bytes(b"changed")
        with self.assertRaises(pipeline.DocumentError):
            self.flow.export()

    def test_layout_rejects_bad_coordinates_and_preserves_full_words(self):
        source = {"revision_id": REVISION, "index": 0, "sheet_id": "b" * 64, "role": "plan"}
        payload = xhtml([schedule.line(.1, [(.1, "Duct 24 x 12 and EF-1")])])
        layout = pipeline.layout_reader.parse_layout(payload, source)
        self.assertEqual(layout["lines"][0]["text"], "Duct 24 x 12 and EF-1")
        for invalid in (payload.replace(b'width="2400"', b'width="NaN"'),
                        b'<!DOCTYPE x [<!ENTITY e "bad">]><x>&e;</x>'):
            with self.assertRaises(ValueError):
                pipeline.layout_reader.parse_layout(invalid, source)


if __name__ == "__main__":
    unittest.main()
