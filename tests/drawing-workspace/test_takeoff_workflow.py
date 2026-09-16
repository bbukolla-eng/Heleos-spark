"""Executable project flow and persistence checks using original synthetic inputs.

The real workflow, equipment module and HTTP handler run here. Only Foundation
file access and the installed PDF reader are replaced by bounded synthetic data.
This is not mechanical-recognition or live-browser acceptance.
"""
import copy
import hashlib
import http.client
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
from types import SimpleNamespace
import unittest
import zipfile

import test_equipment_takeoff as equipment
from test_document_pipeline import xhtml
from test_document_schedule import line

drawing = equipment.drawing
wf = drawing._workflow_module
REVISION = equipment.REVISION


class ProjectReader(equipment.Reader):
    def read(self, snapshot, index):
        if index == 0:
            return super().read(snapshot, index)
        if snapshot.read() != b"synthetic verified PDF":
            raise AssertionError("Wrong fixture bytes")
        if index == 1:
            return xhtml([line(.03, [(.08, "EQUIPMENT SCHEDULE")]),
                line(.07, [(.05, "TAG"), (.3, "QTY"), (.5, "CFM")]),
                line(.12, [(.05, "EF-1"), (.3, "1"), (.5, "600")]),
                line(.17, [(.05, "AHU-1"), (.3, "1"), (.5, "2000")]),
                line(.22, [(.05, "SF-3"), (.3, "1"), (.5, "900")])])
        return xhtml([line(.05, [(.05, "MECHANICAL SPECIFICATIONS")])])


class Workspace(equipment.Workspace):
    def state(self):
        return {"project": self.metadata["project"], "preview_mode": "image", "documents": [
            {"revision_id": REVISION, "name": "Original synthetic plan and schedule.pdf", "page_count": 3,
             "sheets": [{"index": i, "sheet_id": hashlib.sha256(str(i).encode()).hexdigest(),
                         "width_micropoints": 200_000_000, "height_micropoints": 100_000_000,
                         "rotation_degrees": 0, "unit": "pt", "parent_content_sha256": REVISION,
                         "transform": {"m11": 1, "m12": 0, "m21": 0, "m22": -1,
                                       "tx_micropoints": 0, "ty_micropoints": 100_000_000}} for i in range(3)]}]}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(Path(self.temp.name))
        reader = ProjectReader()
        self.engine = drawing.EquipmentEngine(self.workspace, text_reader=reader)
        self.workspace.equipment = self.engine
        self.addCleanup(self.engine.close)
        self.workspace.documents = drawing.DocumentPipeline(self.workspace, reader)
        self.addCleanup(self.workspace.documents.close)
        self.flow = drawing.TakeoffWorkflow(self.workspace)
        self.workspace.workflow = self.flow

    def command(self, action, values, reason="Synthetic operator action"):
        return self.flow.command(action, {"version": self.flow.data["version"],
            "actor": "Fixture operator", "reason": reason, "values": values})

    def source(self, index=0):
        return {"revision_id": REVISION, "index": index}

    def project(self):
        return self.command("project", {"name": "Workflow fixture", "location": "Synthetic",
            "units": "ft", "scopes": list(wf.SCOPES)})

    def register(self):
        for i, role in enumerate(("plan", "schedule", "specification")):
            self.command("page", {"source": self.source(i), "role": role, "label": "M-" + str(i),
                                 "building": "A", "level": "1", "note": "Synthetic page role"})

    def calibrate(self, bounds=None):
        view = self.command("calibrate", {"source": self.source(), "view": bounds or [0, 0, 1, 1],
            "points": [[0.1, 0.2], [0.6, 0.2]], "known_length": "10", "unit": "m",
            "uniform_scale_confirmed": True, "label": "Known 10 m"})
        fact = view["calibrations"][-1]
        view = self.command("scale_decision", {"fact_id": fact["id"], "state": "verified"})
        return next(row for row in view["calibrations"] if row["id"] == fact["id"])

    def measure(self):
        cal = self.calibrate()
        view = self.command("measure", {"calibration_id": cal["id"], "points": [[0.2, 0.2], [0.2, 0.7]],
            "scope": "ductwork", "label": "Supply segment"})
        return view["measurements"][-1]

    def item(self, measurement=None, **extra):
        value = {"id": None, "scope": "ductwork", "description": "Supply duct", "system": "SA",
                 "size": "24x12", "material": "Sheet metal", "quantity": None, "unit": "ft",
                 "evidence_class": "derived" if measurement else "unmeasurable", "source": self.source(),
                 "measurement_id": measurement["id"] if measurement else None, "note": "Original fixture"}
        value.update(extra)
        return self.command("item", value)

    def complete_equipment(self):
        self.command("read_documents", {})
        self.workspace.documents.thread.join(timeout=5)
        view = self.command("extract_equipment", {"mode": "text"})
        run_id = view["equipment_run"]
        if self.engine.thread:
            self.engine.thread.join(timeout=5)
        run = self.engine.result(run_id)
        self.assertEqual(run["state"], "completed")
        seen_ef = False
        for finding in run["findings"]:
            tag = finding["tag"]
            state = "include"
            if finding["role"] == "plan" and tag == "EF-1":
                if seen_ef:
                    state = "exclude"
                seen_ef = True
            if tag == "SF-3":
                state = "exclude"
            if tag == "AHU-1":
                tag = "AHU-01"
            run = self.engine.review(run_id, finding_id=finding["id"], version=run["review_version"],
                                    state=state, tag=tag, reason="Synthetic reconciliation", actor="Fixture operator")
        return run

    def test_complete_saved_project_flow_has_real_records_and_draft_export(self):
        self.project()
        self.register()
        run = self.complete_equipment()
        measurement = self.measure()
        self.assertEqual(measurement["meters"], "5.000000", "Page aspect ratio must enter length calculation")
        view = self.item(measurement)
        self.assertEqual(view["items"][0]["quantity"], "16.404199")
        for scope in wf.SCOPES:
            self.command("scope_review", {"scope": scope, "disposition":
                "reviewed" if scope == "ductwork" else "not_applicable"}, "Fixture scope reviewed")
        for stage in wf.STAGES[:-1]:
            self.command("review", {"stage": stage}, "Checked saved fixture records")
        view = self.flow.view()
        self.assertTrue(view["review_complete"])
        self.assertEqual(view["quantity_authority"], "draft_only")
        self.assertEqual(view["next_stage"], "export")
        restored = drawing.TakeoffWorkflow(self.workspace)
        self.assertEqual(restored.view()["items"], view["items"])
        self.assertTrue(restored.view()["review_complete"])
        with zipfile.ZipFile(io.BytesIO(self.flow.export())) as archive:
            self.assertEqual(set(archive.namelist()), {"README.txt", "WORKBOOK.txt", "takeoff.xlsx", "workflow.json", "equipment.csv",
                "measurements.csv", "takeoff.csv", "unresolved-items.csv", "document-reading.json", "sheet-scales.json",
        "schedule-fields.csv", "requirements.csv", "equipment-register.csv",
        "mechanical-knowledge.json", "mechanical-rule-candidates.csv",
        "mechanical-scope.json", "mechanical-sections.csv", "mechanical-requirements.csv",
        "rule-applicability.json", "rule-scope-matches.csv", "requirement-decisions.csv",
        "rule-admission.json", "rule-versions.csv"})
            self.assertIn("16.404199", archive.read("takeoff.csv").decode("utf-8-sig"))
            self.assertEqual(json.loads(archive.read("workflow.json"))["equipment_run"], run["id"])
            self.assertIn("DRAFT", archive.read("README.txt").decode())

    def test_navigation_never_claims_completion_and_persists_without_documents(self):
        self.command("navigate", {"stage": "export"})
        self.assertFalse(self.flow.view()["review_complete"])
        self.assertEqual(drawing.TakeoffWorkflow(self.workspace).view()["selected_stage"], "export")
        with self.assertRaisesRegex(wf.WorkflowError, "required records"):
            self.command("review", {"stage": "setup"})

    def test_unknown_quantity_stays_unknown_and_blocks_scope_review(self):
        self.project()
        view = self.item()
        self.assertIsNone(view["items"][0]["quantity"])
        self.assertTrue(any(v["id"].startswith("item:") for v in view["issues_view"]))
        with self.assertRaisesRegex(wf.WorkflowError, "known quantities"):
            self.command("scope_review", {"scope": "ductwork", "disposition": "reviewed"})
        with zipfile.ZipFile(io.BytesIO(self.flow.export())) as archive:
            self.assertIn("UNKNOWN", archive.read("takeoff.csv").decode("utf-8-sig"))

    def test_calibration_is_required_and_view_bounds_are_enforced(self):
        self.project()
        with self.assertRaisesRegex(wf.WorkflowError, "Calibrate"):
            self.command("measure", {"calibration_id": "missing", "points": [[0, 0], [1, 1]],
                "scope": "ductwork", "label": "No scale"})
        cal = self.calibrate([0, 0, 0.7, 0.8])
        with self.assertRaisesRegex(wf.WorkflowError, "inside"):
            self.command("measure", {"calibration_id": cal["id"], "points": [[0.2, 0.2], [0.9, 0.7]],
                "scope": "ductwork", "label": "Outside view"})
        value = {"source": self.source(), "view": [0, 0, 1, 1], "points": [[0, 0], [0.5, 0]],
                 "known_length": "10", "unit": "m", "uniform_scale_confirmed": False, "label": "NTS"}
        with self.assertRaisesRegex(wf.WorkflowError, "uniform scale"):
            self.command("calibrate", value)
        value.update(uniform_scale_confirmed=True, points=[[0.5, 0.5], [0.5, 0.5]])
        with self.assertRaisesRegex(wf.WorkflowError, "different drawing points"):
            self.command("calibrate", value)
        for bad in ("NaN", "Infinity", "1e-100000"):
            value.update(points=[[0, 0], [0.5, 0]], known_length=bad)
            with self.assertRaises(wf.WorkflowError):
                self.command("calibrate", value)

    def test_linked_measurement_cannot_be_reassigned_to_another_page_or_scope(self):
        self.project()
        measurement = self.measure()
        with self.assertRaisesRegex(wf.WorkflowError, "measurement from this scope and page"):
            self.item(measurement, source=self.source(1))
        with self.assertRaisesRegex(wf.WorkflowError, "measurement from this scope and page"):
            self.item(measurement, scope="piping")

    def test_edits_preserve_history_reject_stale_version_and_reopen_review(self):
        self.project()
        self.command("review", {"stage": "setup"}, "Checked original name")
        first_history = copy.deepcopy(self.flow.data["history"])
        stale = self.flow.data["version"]
        self.command("project", dict(self.flow.data["project"], name="Updated fixture"))
        self.assertEqual(self.flow.data["history"][:len(first_history)], first_history)
        self.assertEqual(self.flow.view()["stages"][0]["status"], "needs_review")
        with self.assertRaisesRegex(wf.WorkflowError, "workflow changed"):
            self.flow.command("navigate", {"version": stale, "actor": "Fixture", "reason": "Old browser",
                                         "values": {"stage": "export"}})

    def test_equipment_review_changes_invalidate_only_dependent_stage_review(self):
        self.project()
        self.register()
        run = self.complete_equipment()
        # Legacy tag review does not establish physical assemblies. This fixture
        # explicitly excludes the empty physical scope before stage review.
        self.command("scope_review", {"scope": "equipment", "disposition": "not_applicable"})
        for stage in ("setup", "documents", "equipment"):
            self.command("review", {"stage": stage})
        finding = run["findings"][0]
        self.engine.review(run["id"], finding_id=finding["id"], version=run["review_version"], state="pending",
                           tag=finding["tag"], reason="Recheck source", actor="Fixture operator")
        status = {v["id"]: v["status"] for v in self.flow.view()["stages"]}
        self.assertEqual(status["setup"], "reviewed")
        self.assertEqual(status["documents"], "reviewed")
        self.assertNotEqual(status["equipment"], "reviewed")

    def test_scope_review_cannot_hide_existing_records_and_corrections_are_audited(self):
        self.project()
        self.item(quantity="1", evidence_class="drawn", unit="each")
        original_history = copy.deepcopy(self.flow.data["history"])
        item = self.flow.data["items"][0]
        self.item(id=item["id"], quantity="2", evidence_class="drawn", unit="each")
        self.assertEqual(self.flow.data["history"][:len(original_history)], original_history)
        self.assertEqual(self.flow.data["history"][-1]["changes"]["items"][item["id"]]["before"]["quantity"], "1")
        with self.assertRaisesRegex(wf.WorkflowError, "stay included"):
            self.command("project", dict(self.flow.data["project"], scopes=["piping"]))
        with self.assertRaises(wf.WorkflowError):
            self.command("scope_review", {"scope": "ductwork", "disposition": "not_applicable"})

    def test_corrupt_workflow_and_unverified_original_block_reopen_or_export(self):
        self.project()
        envelope = json.loads(self.flow.path.read_text())
        envelope["data"]["project"]["name"] = "Changed bytes"
        self.flow.path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(wf.WorkflowError, "could not be verified"):
            drawing.TakeoffWorkflow(self.workspace)
        def corrupt(_):
            raise drawing.TakeoffError("integrity", "Original changed", 409)
        self.workspace.verified_pdf = corrupt
        with self.assertRaisesRegex(drawing.TakeoffError, "Original changed"):
            self.flow.export()

    def test_issue_resolution_preserves_prior_state_and_csv_formula_is_escaped(self):
        self.project()
        self.item(description="=unsafe-formula()", quantity="1", evidence_class="drawn", unit="each")
        view = self.command("issue", {"id": None, "message": "Confirm service", "state": "open",
                                     "resolution": "", "source": self.source()})
        issue = view["issues"][0]
        self.command("issue", {"id": issue["id"], "message": issue["message"], "state": "resolved",
                              "resolution": "Confirmed in fixture note", "source": self.source()})
        self.assertEqual(self.flow.data["history"][-1]["changes"]["issues"][issue["id"]]["before"]["state"], "open")
        with zipfile.ZipFile(io.BytesIO(self.flow.export())) as archive:
            self.assertIn("'=unsafe-formula()", archive.read("takeoff.csv").decode("utf-8-sig"))

    def test_explicit_allowance_review_stays_separate_and_reopens_after_correction(self):
        self.project()
        view = self.item(quantity="3", unit="each", evidence_class="allowance")
        item = view["items"][0]
        view = self.command("allowance_review", {"item_id": item["id"]}, "Explicit fixture allowance")
        issue = next(v for v in view["issues_view"] if v["id"] == "item:" + item["id"])
        self.assertEqual(issue["state"], "acknowledged")
        self.assertEqual(view["items"][0]["evidence_class"], "allowance")
        view = self.item(id=item["id"], quantity="4", unit="each", evidence_class="allowance")
        issue = next(v for v in view["issues_view"] if v["id"] == "item:" + item["id"])
        self.assertEqual(issue["state"], "open")

    def http(self, route, body=None, origin=True):
        client, server_socket = socket.socketpair()
        server = SimpleNamespace(workspace=self.workspace, origin="http://127.0.0.1:54321", token="fixture")
        thread = threading.Thread(target=drawing.DrawingHandler,
                                  args=(server_socket, ("127.0.0.1", 1234), server))
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", 54321, timeout=5)
        connection.sock = client
        try:
            headers = {"Host": "127.0.0.1:54321"}
            if origin: headers["Origin"] = server.origin
            if body is not None: headers["Content-Type"] = "application/json"
            connection.request("POST" if body is not None else "GET", "/fixture/" + route,
                               body=json.dumps(body) if body is not None else None, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()
            server_socket.close()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    def test_actual_http_routes_store_records_export_and_enforce_origin(self):
        body = {"version": 0, "actor": "Fixture", "reason": "Project setup", "values": {
            "name": "HTTP workflow", "location": "", "units": "ft", "scopes": list(wf.SCOPES)}}
        status, _ = self.http("api/workflow/project", body, origin=False)
        self.assertEqual(status, 403)
        status, result = self.http("api/workflow/project", body)
        self.assertEqual(status, 200, result)
        self.assertEqual(json.loads(result)["project"]["name"], "HTTP workflow")
        status, result = self.http("api/workflow")
        self.assertEqual(json.loads(result)["version"], 1)
        status, result = self.http("api/workflow/export.zip")
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(result)) as archive:
            self.assertIn("workflow.json", archive.namelist())
        status, _ = self.http("workflow.js")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
