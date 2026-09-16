"""Saved mechanical takeoff workflow; local draft records, Python 3.9+ stdlib.

Foundation owns originals. This module owns editable project records and never
writes accepted quantities, source PDFs, the Foundation database or release state.
"""
import copy
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import importlib.util
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import uuid
import zipfile

_scope_spec = importlib.util.spec_from_file_location("heleos_mechanical_rule_context",
                                                   Path(__file__).with_name("mechanical_rule_context.py"))
rule_context = importlib.util.module_from_spec(_scope_spec)
_scope_spec.loader.exec_module(rule_context)

_decision_spec = importlib.util.spec_from_file_location("heleos_project_rule_resolution",
                                                      Path(__file__).with_name("project_rule_resolution.py"))
rule_decisions = importlib.util.module_from_spec(_decision_spec)
_decision_spec.loader.exec_module(rule_decisions)

_admission_spec = importlib.util.spec_from_file_location("heleos_project_rule_admission",
                                                       Path(__file__).with_name("project_rule_admission.py"))
rule_admission = importlib.util.module_from_spec(_admission_spec)
_admission_spec.loader.exec_module(rule_admission)

_baseline_spec = importlib.util.spec_from_file_location("heleos_mechanical_model_baseline",
                                                      Path(__file__).with_name("mechanical_model_baseline.py"))
model_baseline = importlib.util.module_from_spec(_baseline_spec)
_baseline_spec.loader.exec_module(model_baseline)

_inference_spec = importlib.util.spec_from_file_location("heleos_mechanical_model_inference",
                                                       Path(__file__).with_name("mechanical_model_inference.py"))
model_inference = importlib.util.module_from_spec(_inference_spec)
_inference_spec.loader.exec_module(model_inference)
_vision_spec = importlib.util.spec_from_file_location("heleos_local_mechanical_vision",
                                                    Path(__file__).with_name("local_mechanical_vision.py"))
mechanical_vision = importlib.util.module_from_spec(_vision_spec)
_vision_spec.loader.exec_module(mechanical_vision)

def _sheet_module(name):
    spec = importlib.util.spec_from_file_location("heleos_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _air_module(name):
    # Count/source identities must describe the bytes actually executed.
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("heleos_" + name, path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


air_takeoff = _air_module("project_air_device_takeoff")
air_source = _air_module("air_device_source_producer")
air_vision = _air_module("local_air_device_vision")
sheet_geometry = _sheet_module("sheet_geometry")
sheet_scale = _sheet_module("sheet_scale")
scale_labels = _sheet_module("drawing_scale_labels")
schedule_reconciliation = _sheet_module("project_schedule_reconciliation")
duct_takeoff = _sheet_module("project_duct_takeoff")
duct_source = _sheet_module("duct_source_producer")
duct_evaluation = _sheet_module("project_duct_evaluation")
duct_vision = _sheet_module("local_duct_vision_v3")
takeoff_workbook = _sheet_module("takeoff_workbook")
mechanical_scope = _sheet_module("mechanical_scope")

STAGES = ("setup", "documents", "equipment", "measurements", "takeoff", "exceptions", "export")
TITLES = ("Project setup", "Document review", "Equipment", "Measurements",
          "Mechanical takeoff", "Unresolved items", "Export")
SCOPES = ("ductwork", "air_devices", "piping", "fittings", "insulation", "controls",
          "accessories", "demolition")
ROLES = ("plan", "schedule", "specification", "detail", "riser", "legend", "addendum", "excluded")
CLASSES = ("drawn", "document_required", "derived", "allowance", "unmeasurable")
UNITS = {"m": Decimal(1), "mm": Decimal("0.001"), "ft": Decimal("0.3048"),
         "in": Decimal("0.0254")}
MAX_FILE = 16 * 1024 * 1024


class WorkflowError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(packed(value)).hexdigest()


def text(value, limit=500, required=True):
    if (not isinstance(value, str) or len(value) > limit or
            any(ord(c) < 32 for c in value)):
        raise WorkflowError("invalid_text", "Use plain text within the field's length limit.")
    value = value.strip()
    if required and not value:
        raise WorkflowError("missing_field", "Complete the required field.")
    return value


def number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)) or len(str(value)) > 80:
        raise WorkflowError("invalid_number", "Enter a finite nonnegative number.")
    try:
        result = Decimal(str(value))
        if (not result.is_finite() or result < 0 or result > 10**12 or
                (result != 0 and result.adjusted() < -9) or (positive and not result)):
            raise InvalidOperation()
        return result
    except (InvalidOperation, ValueError):
        raise WorkflowError("invalid_number", "Enter a finite number in the supported range.") from None


def points(value, minimum=2, maximum=128):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise WorkflowError("invalid_points", "Select the required points on the drawing.")
    result = []
    for pair in value:
        if (not isinstance(pair, list) or len(pair) != 2 or
                any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in pair)):
            raise WorkflowError("invalid_points", "Drawing points must be inside the page.")
        result.append([round(float(v), 8) for v in pair])
    return result


def region(value):
    if not isinstance(value, list) or len(value) != 4:
        raise WorkflowError("invalid_view", "Select the drawing view to calibrate.")
    pair = points([value[:2], value[2:]], maximum=2)
    x0, y0 = pair[0]
    x1, y1 = pair[1]
    if not x0 < x1 or not y0 < y1:
        raise WorkflowError("invalid_view", "The drawing view needs a nonzero area.")
    return [x0, y0, x1, y1]


def contained(path, bounds):
    if not all(bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3] for x, y in path):
        raise WorkflowError("outside_view", "Keep all measurement points inside the calibrated drawing view.")


def paper_length(path, sheet):
    width, height = sheet["width_micropoints"] / 1e6, sheet["height_micropoints"] / 1e6
    value = sum(math.hypot((b[0] - a[0]) * width, (b[1] - a[1]) * height)
                for a, b in zip(path, path[1:]))
    if not math.isfinite(value) or value <= 0:
        raise WorkflowError("zero_length", "Select two different drawing points.")
    return Decimal(str(value))


def stamp():
    return datetime.now(timezone.utc).isoformat()


def decimal_text(value):
    if not value.is_finite() or abs(value) > 10**12:
        raise WorkflowError("quantity_range", "The calculated quantity exceeds the supported range.")
    rounded = value.quantize(Decimal("0.000001"))
    if value > 0 and rounded == 0:
        raise WorkflowError("quantity_resolution", "The length is below the supported measurement resolution.")
    return format(rounded, "f")


def csv_bytes(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    for row in rows:
        cells = []
        for value in row:
            cell = "UNKNOWN" if value is None else str(value)
            if cell.lstrip().startswith(("=", "+", "-", "@")):
                cell = "'" + cell
            cells.append(cell)
        writer.writerow(cells)
    return stream.getvalue().encode("utf-8-sig")


class TakeoffWorkflow:
    def __init__(self, workspace):
        self.workspace = workspace
        self.path = workspace.root / "takeoff-workflow.json"
        self.lock = threading.RLock()
        project_id = workspace.metadata["project"]["id"]
        if self.path.exists():
            try:
                if self.path.is_symlink() or self.path.stat().st_size > MAX_FILE:
                    raise ValueError()
                envelope = json.loads(self.path.read_text(encoding="utf-8"))
                data = envelope["data"]
                if (data["schema"] != 1 or data["project_id"] != project_id or
                        digest(data) != envelope["sha256"] or type(data["version"]) is not int):
                    raise ValueError()
                self.data = data
            except (OSError, ValueError, KeyError, TypeError):
                raise WorkflowError("workflow_invalid", "The saved workflow could not be verified. Preserve it before recovery.", 409) from None
        else:
            self.data = {"schema": 1, "project_id": project_id, "version": 0,
                         "selected_stage": "setup", "project": {"name": "", "location": "",
                         "units": "ft", "scopes": list(SCOPES)}, "pages": {},
                         "equipment_run": None, "calibrations": [], "measurements": [],
                         "items": [], "issues": [], "allowance_reviews": {}, "scope_reviews": {}, "reviews": {}, "history": []}
        # Additive local schema migration: preserve earlier edits and receipts.
        self.data.setdefault("document_run", None)
        self.data.setdefault("requirement_reviews", {})
        self.data.setdefault("rule_decision_refs", {})
        self.data.setdefault("rule_admission_refs", {})
        self.data.setdefault("model_baseline_refs", {})
        self.data.setdefault("model_source_refs", [])
        self.data.setdefault("model_inference_refs", {})
        self.data.setdefault("model_inference_identity", None)
        self.data.setdefault("scale_candidates", [])
        self.data.setdefault("scale_decisions", [])
        self.data.setdefault("scale_reading_issues", [])
        self.data.setdefault("measurement_replacements", {})
        schedule_reconciliation.initialize(self.data)
        duct_takeoff.initialize(self.data)
        air_takeoff.initialize(self.data)
        self.model_baseline = model_baseline.BaselineStore(workspace.documents.knowledge_store)
        configured_vision = getattr(workspace.equipment, "vision", None)
        adapter = (mechanical_vision.MechanicalVision(configured_vision,
                   runtime_executable=getattr(workspace, "vision_runtime_executable", None))
                   if configured_vision is not None else None)
        self.model_inference = model_inference.InferenceEngine(workspace, self.model_baseline, adapter=adapter)
        self.duct_producer = getattr(workspace, "duct_producer", None)
        if self.duct_producer is None or getattr(self.duct_producer, "closed", False):
            duct_adapter = (duct_vision.DuctVision(configured_vision,
                runtime_executable=getattr(workspace, "vision_runtime_executable", None))
                if configured_vision is not None else None)
            self.duct_producer = duct_source.DuctSourceProducer(workspace, self.model_inference, duct_adapter)
            workspace.duct_producer = self.duct_producer
        self.air_device_producer = getattr(workspace, "air_device_producer", None)
        if self.air_device_producer is None or getattr(self.air_device_producer, "closed", False):
            air_adapter = (air_vision.AirDeviceVision(configured_vision,
                runtime_executable=getattr(workspace, "vision_runtime_executable", None))
                if configured_vision is not None else None)
            self.air_device_producer = air_source.AirDeviceSourceProducer(
                workspace, self.model_inference, air_adapter)
            workspace.air_device_producer = self.air_device_producer

    def close(self):
        self.air_device_producer.close()
        self.duct_producer.close()
        self.model_inference.close()

    def _save(self, data):
        payload = packed({"data": data, "sha256": digest(data)}) + b"\n"
        if len(payload) > MAX_FILE or len(data["history"]) > 10000:
            raise WorkflowError("workflow_limit", "This workflow has reached its saved-record limit.", 409)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.workspace.root, prefix=".workflow-", delete=False) as out:
                temporary = Path(out.name)
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
            os.replace(str(temporary), str(self.path))
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _inventory(self):
        state = self.workspace.state()
        sheets = {}
        for doc in state["documents"]:
            for page in doc["sheets"]:
                sheets[doc["revision_id"] + ":" + str(page["index"])] = dict(
                    page, revision_id=doc["revision_id"], document_name=doc["name"])
        return state, sheets

    def _source(self, value, sheets):
        if (not isinstance(value, dict) or set(value) != {"revision_id", "index"} or
                not isinstance(value["revision_id"], str) or type(value["index"]) is not int):
            raise WorkflowError("invalid_source", "Choose a page from the project.")
        key = value["revision_id"] + ":" + str(value["index"])
        if key not in sheets:
            raise WorkflowError("source_missing", "The referenced page is not in this project.", 409)
        return dict(value, sheet_id=sheets[key]["sheet_id"]), sheets[key], key

    def _equipment(self, data):
        if not data["equipment_run"]:
            return None
        run = self.workspace.equipment.result(data["equipment_run"])
        run["stale"] = False
        if run.get("document_run_id"):
            run["stale"] = run["document_run_id"] != data.get("document_run")
            if not run["stale"]:
                reading = self.workspace.documents.result(data["document_run"])
                run["stale"] = run.get("document_result_sha256") != digest(reading)
        return run

    def _document_selections(self, data, sheets):
        selections = []
        for key, sheet in sheets.items():
            role = data["pages"].get(key, {}).get("role", "unassigned")
            if role != "excluded":
                selections.append({"revision_id": sheet["revision_id"], "index": sheet["index"], "role": role})
        return sorted(selections, key=lambda v: (v["revision_id"], v["index"]))

    def _documents(self, data, sheets):
        if not data.get("document_run"):
            return None
        run = self.workspace.documents.result(data["document_run"])
        expected = self._document_selections(data, sheets)
        actual = [{k: v[k] for k in ("revision_id", "index", "role")} for v in run["sources"]]
        run["stale"] = actual != expected
        run["coordinate_issues"] = []
        if run.get("reader", {}).get("kind") == "poppler_bbox":
            read_pages = {(page["revision_id"], page["index"]) for page in run["pages"] if page["state"] == "read"}
            for source in run["sources"]:
                if (source["revision_id"], source["index"]) not in read_pages:
                    continue
                try:
                    geometry = self._geometry_for(source, sheets)
                    if source.get("geometry") != geometry:
                        raise ValueError("unbound or changed coordinates")
                except (ValueError, WorkflowError):
                    run["coordinate_issues"].append({"source": {k: source[k] for k in ("revision_id", "index", "sheet_id")},
                        "message": "This saved reading lacks current page coordinates. Read the document set again."})
            run["stale"] = run["stale"] or bool(run["coordinate_issues"])
        return run

    def _scope_fingerprint(self, data, scope, duct=None, air=None):
        if scope == "ductwork" and duct is None:
            _, sheets = self._inventory()
            duct = duct_takeoff.view(self.workspace, data, sheets)
        if scope == "air_devices" and air is None:
            _, sheets = self._inventory()
            air = air_takeoff.view(self.workspace, data, sheets)
        basis = {"items": [v for v in data["items"] if v["scope"] == scope],
                       "measurements": [v for v in data["measurements"] if v["scope"] == scope],
                       "duct_state": duct["state_fingerprint"] if scope == "ductwork" and duct else None}
        if scope == "air_devices":
            basis["air_device_state"] = air["fingerprint"] if air else None
        return digest(basis)

    @staticmethod
    def _geometry_for(source, sheets):
        sheet = sheets.get(source["revision_id"] + ":" + str(source["index"]))
        if sheet is None or sheet["sheet_id"] != source.get("sheet_id", sheet["sheet_id"]):
            raise WorkflowError("source_missing", "The measurement's original sheet is unavailable.", 409)
        return sheet_geometry.sheet_geometry(sheet, source["revision_id"])

    def _scale_state(self, data, sheets):
        """Derive current quantities without rewriting saved measurements/history."""
        result = copy.deepcopy(data)
        geometries = {}
        for key, sheet in sheets.items():
            try:
                geometries[key] = sheet_geometry.sheet_geometry(sheet, sheet["revision_id"])
            except ValueError:
                # Older draft/test inventories may lack full Foundation geometry.
                # They remain viewable; no scale-dependent result is usable.
                geometries[key] = None
        def geometry(source):
            return geometries.get(source["revision_id"] + ":" + str(source["index"]))
        reading = self._documents(data, sheets) if data["scale_candidates"] else None
        for candidate in result["scale_candidates"]:
            issues = []
            current_geometry = geometry(candidate["source"])
            if current_geometry is None or candidate["geometry_fingerprint"] != current_geometry["fingerprint"]:
                issues.append("The candidate's sheet coordinates changed or are unavailable.")
            if candidate["document_run_id"] != data["document_run"] or not reading or reading["stale"]:
                issues.append("This scale label belongs to an earlier document reading.")
            if candidate["reader_sha256"] != hashlib.sha256(Path(scale_labels.__file__).read_bytes()).hexdigest():
                issues.append("Read scale labels again with the current reader.")
            candidate.update(validity="blocked" if issues else "current", issues=issues)
        result["calibrations"] = [sheet_scale.fact_view(fact, data["scale_decisions"], geometry(fact["source"]))
                                  for fact in data["calibrations"]]
        result["measurements"] = []
        for record in data["measurements"]:
            current = sheet_scale.measurement_view(record, data["calibrations"], data["scale_decisions"],
                                                   geometry(record["source"]))
            replacement = data["measurement_replacements"].get(record["id"])
            if replacement:
                current.update(validity="superseded", meters=None, replacement_id=replacement,
                               block_reason="measurement_superseded",
                               block_message="Replaced by an explicitly recalculated measurement.")
            result["measurements"].append(current)
        measurements = {row["id"]: row for row in result["measurements"]}
        for item in result["items"]:
            if not item.get("measurement_id"):
                continue
            measurement = measurements.get(item["measurement_id"])
            item["stored_quantity"] = item["quantity"]
            item["validity"] = "current" if measurement and measurement["validity"] == "current" else "blocked"
            if item["validity"] == "current":
                item["quantity"] = decimal_text(Decimal(measurement["meters"]) / UNITS[item["unit"]])
            else:
                item["quantity"] = None
                item["block_reason"] = (measurement["block_reason"] if measurement else "Linked measurement is missing.")
                item["block_message"] = (measurement["block_message"] if measurement else "Linked measurement is missing.")
        return result, geometries

    def _problems(self, data, sheets, run, documents=None):
        found = []
        def add(key, message, source=None):
            found.append({"id": key, "message": message, "source": source, "state": "open", "automatic": True})
        if not sheets:
            add("documents:none", "No accepted documents have been added to the project.")
        for key, sheet in sheets.items():
            if key not in data["pages"]:
                add("page:" + key, "Classify page %d of %s." % (sheet["index"] + 1, sheet["document_name"]),
                    {"revision_id": sheet["revision_id"], "index": sheet["index"]})
        assigned = [v for k, v in data["pages"].items() if k in sheets]
        for role, label in [("plan", "plan"), ("schedule", "equipment schedule"), ("specification", "specification")]:
            if not any(v["role"] == role for v in assigned):
                add("role:" + role, "No %s has been identified; its requirements remain unchecked." % label)
        if not run:
            add("equipment:none", "Equipment extraction and reconciliation have not been completed.")
        elif run["state"] != "completed":
            add("equipment:state", "Equipment extraction is " + run["state"] + ".")
        elif run.get("stale"):
            add("equipment:stale", "These equipment records belong to an earlier reading. Build records from the current document reading.")
        else:
            if not run["rows"]:
                add("equipment:empty", "No equipment was found. This does not establish that the job has none.")
            for row in run["rows"]:
                if row["pending"] or row["issues"] or row["reviewed_quantity"] is None:
                    add("equipment:" + row["tag"], row["tag"] + ": " +
                        ", ".join(row["issues"] + (["review required"] if row["pending"] else []) +
                                  (["quantity unknown"] if row["reviewed_quantity"] is None else [])))
        for item in data["items"]:
            if item["quantity"] is None or item["evidence_class"] in ("allowance", "unmeasurable"):
                add("item:" + item["id"], item["description"] + ": " +
                    ("quantity unknown" if item["quantity"] is None else "allowance requires review"), item["source"])
                if item["quantity"] is not None and item["evidence_class"] == "allowance":
                    found[-1]["allowance_id"] = item["id"]
                    review = data.get("allowance_reviews", {}).get(item["id"])
                    if review and review["fingerprint"] == digest(item):
                        found[-1].update(state="acknowledged", resolution=review["reason"])
        for issue in data["issues"]:
            found.append(dict(issue, automatic=False))
        if documents is None:
            add("reading:none", "Document content has not been read into the project records.")
        elif documents["stale"]:
            add("reading:stale", "Document pages or roles changed. Read the current document set.")
        elif documents["state"] != "completed":
            add("reading:state", "Document reading is " + documents["state"] + ".")
        else:
            for i, issue in enumerate(documents["issues"]):
                add("reading:issue:" + str(i), issue["message"], issue.get("source"))
            for requirement in documents["requirements"]:
                review = data["requirement_reviews"].get(requirement["id"])
                if not review or review["fingerprint"] != digest(requirement):
                    add("requirement:" + requirement["id"], "Written requirement needs review: " + requirement["text"][:180], requirement["source"])
            for record in documents["equipment_register"]:
                if record["issues"] and not (run and run.get("document_run_id") == documents["id"]):
                    add("schedule:" + record["tag"], record["tag"] + ": " + ", ".join(record["issues"]))
        return found

    def _snapshot(self, data, state, sheets, run):
        evaluation_data = data
        data, geometries = self._scale_state(data, sheets)
        try:
            baseline = self.model_baseline.view(data["project_id"])
            baseline = duct_evaluation.current_view(self, evaluation_data, sheets, baseline)
            inference = self.model_inference.view()
            model_sources = [self.workspace.documents.knowledge_store.get_source(key)
                             for key in data["model_source_refs"]]
            if any(source["metadata"].get("project_id") != data["project_id"] for source in model_sources):
                raise WorkflowError("model_source_project", "Model evidence belongs to another project.", 409)
        except ValueError as error:
            if not hasattr(error, "code") or not hasattr(error, "message"):
                raise
            raise WorkflowError(error.code, error.message, 409) from None
        documents = self._documents(data, sheets)
        duct = duct_takeoff.view(self.workspace, data, sheets, documents)
        air = air_takeoff.view(self.workspace, data, sheets)
        if run and run.get("document_run_id") and (not documents or documents.get("stale")):
            run = dict(run, stale=True)
        try:
            reconciliation = schedule_reconciliation.view(self.workspace, data, sheets, documents)
        except schedule_reconciliation.core.ReconciliationError as error:
            raise WorkflowError(error.code, error.message, 409) from None
        knowledge = (self.workspace.documents.knowledge_for(documents)
                     if documents and documents["state"] == "completed" else None)
        try:
            applicability = rule_context.evaluate_project(documents, knowledge, data["items"]) if documents else None
            applicability, decision_evidence = rule_decisions.build_view(
                self.workspace.documents.knowledge_store, documents, knowledge, applicability)
            admission = rule_admission.build_view(
                self.workspace.documents.knowledge_store, documents, knowledge, applicability)
        except (rule_context.ContextError, rule_decisions.ProjectDecisionError, rule_admission.AdmissionError) as error:
            raise WorkflowError(error.code, error.message, 409) from None
        document_identity = None if documents is None else {k: documents.get(k) for k in ("id", "state", "fingerprint", "stale")}
        if document_identity is not None:
            document_identity["knowledge_state"] = knowledge["state_fingerprint"] if knowledge else None
        inventory = [{"key": k, "sheet_id": v["sheet_id"], "geometry": geometries[k]} for k, v in sorted(sheets.items())]
        run_identity = None if run is None else {k: run.get(k) for k in ("id", "state", "review_version", "findings_sha256", "stale")}
        inputs = {
            "setup": data["project"],
            "documents": [inventory, data["pages"], document_identity, data["requirement_reviews"]],
            "equipment": [inventory, data["pages"], run_identity, document_identity, reconciliation["state_fingerprint"]],
            "measurements": [inventory, data["calibrations"], data["measurements"], duct["state_fingerprint"]],
            "takeoff": [data["project"]["scopes"], data["items"], data["scope_reviews"], data["measurements"],
                        duct["state_fingerprint"], air["fingerprint"],
                        applicability["state_fingerprint"] if applicability else None,
                        admission["state_fingerprint"] if admission else None],
        }
        problems = self._problems(data, sheets, run, documents)
        resolved_reading = schedule_reconciliation.resolved_reading_issue_ids(reconciliation, documents)
        resolved_reading.update("schedule:" + group["tag"] for group in reconciliation["groups"]
                                if group["validity"] == "current" and group["status"] in ("matched", "resolved", "excluded"))
        problems = [problem for problem in problems if problem["id"] not in resolved_reading]
        problems.extend(schedule_reconciliation.issues(reconciliation))
        problems.extend(duct_takeoff.issues(duct))
        if "air_devices" in data["project"]["scopes"]:
            for index, issue in enumerate(air["issues"]):
                problems.append({"id": "air-device:" + str(index),
                    "message": issue if isinstance(issue, str) else issue.get("message", issue.get("code", "Air-device review required")),
                    "source": issue.get("source") if isinstance(issue, dict) else None,
                    "state": "open", "automatic": True})
        for measurement in data["measurements"]:
            if measurement["validity"] == "blocked":
                problems.append({"id": "measurement:" + measurement["id"], "message": measurement["label"] + ": " + measurement["block_message"],
                                 "source": measurement["source"], "state": "open", "automatic": True})
        problems.extend(rule_context.unresolved_issues(applicability))
        if documents and documents["state"] == "completed" and not documents["stale"]:
            if knowledge is None:
                problems.append({"id": "knowledge:missing", "message": "Build the mechanical knowledge from this saved document reading.",
                                 "source": None, "state": "open", "automatic": True})
            else:
                requirements = {r.get("knowledge", {}).get("rule_id"): r for r in documents["requirements"]}
                for rule in knowledge["rules"]:
                    if rule["issues"]:
                        problems.append({"id": "knowledge:rule:" + rule["id"],
                                         "message": "A mechanical requirement has unresolved source status: " + rule["payload"]["statement"][:180],
                                         "source": requirements.get(rule["id"], {}).get("source"),
                                         "state": "open", "automatic": True})
        fingerprints = {key: digest(value) for key, value in inputs.items() if key != "exceptions"}
        fingerprints["exceptions"] = digest([problems, fingerprints])
        stages = []
        rows = [] if run is None else run["rows"]
        if run and run.get("stale"):
            rows = [dict(row, stored_reviewed_quantity=row["reviewed_quantity"], reviewed_quantity=None,
                         status="stale", issues=row["issues"] + ["earlier_document_reading"]) for row in rows]
        active_scopes = data["project"]["scopes"]
        scope_status = {}
        for scope in active_scopes:
            review = data["scope_reviews"].get(scope)
            scope_status[scope] = {"count": sum(v["scope"] == scope for v in data["items"]),
                                   "reviewed": bool(review and review["fingerprint"] == self._scope_fingerprint(data, scope, duct, air)),
                                   "disposition": review["disposition"] if review else None}
            if scope == "ductwork" and duct["available"]:
                scope_status[scope]["count"] += len(duct["segments"])
                scope_status[scope]["reviewed"] = scope_status[scope]["reviewed"] and duct["complete"]
            if scope == "air_devices":
                # Generic manually entered items are not deterministic each counts.
                scope_status[scope]["count"] = len((air.get("result") or {}).get("rows", []))
                excluded_empty = bool(review and review["disposition"] == "not_applicable" and
                    not air["available"] and not any(v["scope"] == scope for v in data["items"]))
                scope_status[scope]["reviewed"] = bool(scope_status[scope]["reviewed"] and
                    (excluded_empty or (air["available"] and not air["stale"] and
                     (air.get("result") or {}).get("complete"))))
        ready = {
            "setup": bool(data["project"]["name"]),
            "documents": bool(sheets) and all(k in data["pages"] for k in sheets) and
                bool(documents and documents["state"] == "completed" and not documents["stale"] and
                     all(p["state"] == "read" for p in documents["pages"])),
            "equipment": bool(run and not run.get("stale") and run["state"] == "completed" and rows and
                              (not reconciliation["available"] or
                               (not reconciliation["needs_refresh"] and
                                all(group["validity"] == "current" and group["status"] != "blocked" for group in reconciliation["groups"]))) and
                              all(not v["pending"] and not v["issues"] and v["reviewed_quantity"] is not None for v in rows)),
            "measurements": (any(v["validity"] == "current" for v in data["measurements"]) or
                             any(v["status"] == "current" for v in duct["segments"])) and
                            all(v["validity"] != "blocked" for v in data["measurements"]) and
                            all(v["status"] not in ("stale", "unresolved") for v in duct["segments"]),
            "takeoff": bool(active_scopes) and all(v["reviewed"] for v in scope_status.values()),
            "exceptions": not any(v["state"] == "open" for v in problems),
        }
        counts = {"setup": int(bool(data["project"]["name"])), "documents": len(data["pages"]),
                  "equipment": len(rows), "measurements": len(data["measurements"]) + len(duct["segments"]),
                  "takeoff": len(data["items"]) + len(duct["segments"]) + len((air.get("result") or {}).get("rows", [])),
                  "exceptions": sum(v["state"] == "open" for v in problems)}
        for key, title in zip(STAGES[:-1], TITLES[:-1]):
            review = data["reviews"].get(key)
            reviewed = bool(ready[key] and review and review["fingerprint"] == fingerprints[key])
            status = "reviewed" if reviewed else ("ready_for_review" if ready[key] else "incomplete")
            if review and not reviewed:
                status = "needs_review" if ready[key] else "incomplete"
            stages.append({"id": key, "title": title, "status": status, "count": counts[key], "can_review": ready[key]})
        stages.append({"id": "export", "title": "Export", "status": "draft_available", "can_review": False})
        result = copy.deepcopy(data)
        result.update(stages=stages, issues_view=problems, scopes=scope_status,
                      inventory=[dict(v, key=k, assignment=data["pages"].get(k)) for k, v in sheets.items()],
                      equipment=run_identity, equipment_rows=rows, document_reading=documents, project_knowledge=knowledge,
                      mechanical_scope=mechanical_scope.build_view(documents, data["requirement_reviews"]),
                      schedule_reconciliation=reconciliation,
                      duct_takeoff=duct,
                      duct_producer=self.workspace.duct_producer.view(),
                      air_device_takeoff=air,
                      air_device_producer=self.workspace.air_device_producer.view(),
                      rule_applicability=applicability,
                      rule_decision_evidence=decision_evidence,
                      rule_admission=admission,
                      model_baseline=baseline, model_sources=model_sources, model_inference=inference,
                      sheet_geometries=geometries,
                      next_stage=next((v["id"] for v in stages if v["status"] != "reviewed"), "export"),
                      review_complete=all(v["status"] == "reviewed" for v in stages[:-1]),
                      quantity_authority="draft_only", preview_mode=state.get("preview_mode"),
                      capability_notes=["PDF text supplies schedule fields and written requirement candidates with source locations. Scans and ambiguous layouts need further reading support.",
                                        "Lengths require a current verified scale for their drawing view. Changed or conflicting scales block dependent quantities; automatic tracing and distorted-scan calibration remain unfinished.",
                                        "Schedule-to-plan tag links and explicit requirements populate project records. Automatic symbol recognition, system tracing, pricing and labor remain unfinished."])
        return result, fingerprints

    def view(self):
        with self.lock:
            state, sheets = self._inventory()
            return self._snapshot(self.data, state, sheets, self._equipment(self.data))[0]

    def command(self, action, payload):
        if not isinstance(payload, dict) or set(payload) != {"version", "actor", "reason", "values"}:
            raise WorkflowError("invalid_request", "Provide the workflow version, actor, reason and values.")
        actor, reason = text(payload["actor"], 100), text(payload["reason"], 500)
        values = payload["values"]
        if not isinstance(values, dict):
            raise WorkflowError("invalid_request", "Workflow values must be an object.")
        with self.lock:
            if type(payload["version"]) is not int or payload["version"] != self.data["version"]:
                raise WorkflowError("stale_edit", "The workflow changed. Refresh before saving this edit.", 409)
            state, sheets = self._inventory()
            data = copy.deepcopy(self.data)
            before = copy.deepcopy({k: v for k, v in data.items() if k != "history"})
            try:
                self._apply(data, action, values, sheets, state, actor, reason)
            except ValueError as error:
                if hasattr(error, "code") and hasattr(error, "message"):
                    raise WorkflowError(error.code, error.message, 409) from None
                raise
            after = {k: v for k, v in data.items() if k != "history"}
            changed = {}
            for key in before:
                old, new = before[key], after[key]
                if old == new:
                    continue
                if isinstance(old, list) and all(isinstance(v, dict) and "id" in v for v in old + new):
                    old = {v["id"]: v for v in old}
                    new = {v["id"]: v for v in new}
                if isinstance(old, dict) and isinstance(new, dict):
                    changed[key] = {k: {"before": old.get(k), "after": new.get(k)}
                                    for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)}
                else:
                    changed[key] = {"before": old, "after": new}
            data["version"] += 1
            data["history"].append({"version": data["version"], "at": stamp(), "actor": actor,
                                    "reason": reason, "action": action, "changes": copy.deepcopy(changed)})
            self._save(data)
            self.data = data
            return self._snapshot(data, state, sheets, self._equipment(data))[0]

    def _apply(self, data, action, value, sheets, state, actor, reason):
        def exact(*fields):
            if set(value) != set(fields):
                raise WorkflowError("invalid_fields", "The workflow action has missing or unexpected fields.")
        if action == "project":
            exact("name", "location", "units", "scopes")
            scopes = value["scopes"]
            if (value["units"] not in UNITS or not isinstance(scopes, list) or not scopes or
                    any(v not in SCOPES for v in scopes) or len(scopes) != len(set(scopes))):
                raise WorkflowError("invalid_scope", "Choose the project's units and mechanical scope.")
            if (any(v["scope"] not in scopes for v in data["items"] + data["measurements"]) or
                    (data["duct_generations"] and "ductwork" not in scopes) or
                    (data.get("air_device_readings") and "air_devices" not in scopes)):
                raise WorkflowError("scope_has_records", "A scope with saved records must stay included.", 409)
            data["project"] = {"name": text(value["name"], 160), "location": text(value["location"], 200, False),
                               "units": value["units"], "scopes": scopes}
        elif action == "navigate":
            exact("stage")
            if value["stage"] not in STAGES:
                raise WorkflowError("invalid_stage", "Choose a takeoff stage.")
            data["selected_stage"] = value["stage"]
        elif action == "model_source":
            exact("metadata", "snapshot_text")
            if not isinstance(value["metadata"], dict):
                raise WorkflowError("model_source_metadata", "Provide the source metadata.")
            metadata = dict(value["metadata"])
            if metadata.get("project_id", data["project_id"]) != data["project_id"]:
                raise WorkflowError("model_source_project", "The source must belong to this project.")
            snapshot_text = value["snapshot_text"]
            if (not isinstance(snapshot_text, str) or not snapshot_text.strip() or
                    any(ord(char) < 32 and char not in "\n\r\t" for char in snapshot_text)):
                raise WorkflowError("model_source_text", "Provide the source text with its original line breaks.")
            try:
                raw = snapshot_text.encode("utf-8")
            except UnicodeEncodeError:
                raise WorkflowError("model_source_text", "The source text must be valid UTF-8.") from None
            if len(raw) > 48000:
                raise WorkflowError("model_source_limit", "This source excerpt exceeds the local import limit.")
            metadata["project_id"] = data["project_id"]
            if metadata.get("source_artifact_sha256", hashlib.sha256(raw).hexdigest()) != hashlib.sha256(raw).hexdigest():
                raise WorkflowError("model_source_hash", "The source text does not match its declared digest.")
            metadata["source_artifact_sha256"] = hashlib.sha256(raw).hexdigest()
            try:
                source = self.workspace.documents.knowledge_store.add_source(metadata, raw)
            except ValueError as error:
                if not hasattr(error, "code") or not hasattr(error, "message"):
                    raise
                raise WorkflowError(error.code, error.message) from None
            if source["id"] not in data["model_source_refs"]:
                data["model_source_refs"].append(source["id"])
        elif action == "model_baseline":
            exact("kind", "payload")
            try:
                payload = duct_evaluation.bind_payload(self, data, sheets, value["kind"], value["payload"])
                record = self.model_baseline.register(data["project_id"], value["kind"], payload, actor, reason)
            except ValueError as error:
                raise WorkflowError(getattr(error, "code", "invalid_payload"), str(error), 409) from None
            data["model_baseline_refs"][record["id"]] = value["kind"]
        elif action in ("model_input", "model_identity", "model_infer", "model_infer_cancel", "model_infer_resume"):
            try:
                if action == "model_input":
                    exact("source")
                    source, _, _ = self._source(value["source"], sheets)
                    record = self.model_inference.prepare_input(
                        {key: source[key] for key in ("revision_id", "index")}, actor, reason)
                    data["model_inference_refs"][record["id"]] = "input"
                elif action == "model_identity":
                    exact()
                    data["model_inference_identity"] = self.model_inference.identity()
                else:
                    exact("plan_id" if action == "model_infer" else "job_id")
                    if action == "model_infer":
                        job = self.model_inference.start(value["plan_id"], actor, reason)
                    elif action == "model_infer_cancel":
                        job = self.model_inference.cancel(value["job_id"])
                    else:
                        job = self.model_inference.resume(value["job_id"], actor, reason)
                    data["model_inference_refs"][job["id"]] = "job"
            except (model_inference.InferenceError, mechanical_vision.VisionError) as error:
                raise WorkflowError(error.code, error.message, 409) from None
        elif action == "page":
            exact("source", "role", "label", "building", "level", "note")
            source, _, key = self._source(value["source"], sheets)
            if value["role"] not in ROLES:
                raise WorkflowError("invalid_role", "Choose the page's role.")
            data["pages"][key] = {"source": source, "role": value["role"], "label": text(value["label"], 100, False),
                                  "building": text(value["building"], 100, False), "level": text(value["level"], 100, False),
                                  "note": text(value["note"], required=value["role"] == "excluded")}
        elif action == "read_documents":
            exact()
            reader = getattr(self.workspace, "documents", None)
            if reader is None:
                raise WorkflowError("reader_missing", "The document reader is unavailable in this workspace.", 409)
            run = reader.start(self._document_selections(data, sheets))
            data["document_run"] = run["id"]
        elif action == "build_knowledge":
            exact()
            documents = self._documents(data, sheets)
            if not documents or documents["state"] != "completed" or documents["stale"]:
                raise WorkflowError("reading_incomplete", "Finish reading the current documents before building their mechanical knowledge.", 409)
            data["document_run"] = self.workspace.documents.build_knowledge(documents["id"])["id"]
        elif action == "rule_decision":
            exact("evaluation_id", "binding_sha256", "disposition", "evidence_ids", "supersedes")
            documents = self._documents(data, sheets)
            if not documents or documents["state"] != "completed" or documents["stale"]:
                raise WorkflowError("decision_reading", "Read the current document set before resolving item requirements.", 409)
            knowledge = self.workspace.documents.knowledge_for(documents)
            try:
                applicability = rule_context.evaluate_project(documents, knowledge, data["items"])
                decision = rule_decisions.record_decision(self.workspace.documents.knowledge_store,
                    documents, knowledge, applicability, value, actor, reason)
            except (rule_context.ContextError, rule_decisions.ProjectDecisionError) as error:
                raise WorkflowError(error.code, error.message, 409) from None
            data["rule_decision_refs"][value["evaluation_id"]] = decision["id"]
        elif action in ("rule_prepare", "rule_lifecycle"):
            documents = self._documents(data, sheets)
            if not documents or documents["state"] != "completed" or documents["stale"]:
                raise WorkflowError("rule_reading", "Read the current document set before changing rule versions.", 409)
            knowledge = self.workspace.documents.knowledge_for(documents)
            try:
                applicability = rule_context.evaluate_project(documents, knowledge, data["items"])
                operation = rule_admission.prepare_version if action == "rule_prepare" else rule_admission.record_event
                record = operation(self.workspace.documents.knowledge_store, documents, knowledge,
                                   applicability, value, actor, reason)
            except (rule_context.ContextError, rule_admission.AdmissionError) as error:
                raise WorkflowError(error.code, error.message, 409) from None
            data["rule_admission_refs"][value["rule_id"]] = record["id"]
        elif action == "requirement_review":
            exact("requirement_id", "disposition")
            if value["disposition"] not in ("applicable", "excluded", "pending"):
                raise WorkflowError("requirement_review", "Choose applicable, excluded or pending.")
            documents = self._documents(data, sheets)
            if not documents or documents["state"] != "completed" or documents["stale"]:
                raise WorkflowError("reading_incomplete", "Read the current document set before reviewing its requirements.", 409)
            requirement = next((v for v in documents["requirements"] if v["id"] == value["requirement_id"]), None)
            if requirement is None:
                raise WorkflowError("requirement_missing", "This requirement is not in the current reading.", 404)
            if value["disposition"] == "pending":
                data["requirement_reviews"].pop(requirement["id"], None)
            else:
                data["requirement_reviews"][requirement["id"]] = {"fingerprint": digest(requirement),
                    "disposition": value["disposition"], "actor": actor, "reason": reason, "at": stamp()}
        elif action == "reconcile":
            exact()
            schedule_reconciliation.refresh(self.workspace, data, sheets)
        elif action == "duct_capture_evidence":
            duct_takeoff.capture_evidence(self.workspace, data, sheets, value, actor, reason)
        elif action in ("duct_prepare", "duct_correct", "duct_recalculate", "duct_review_context", "duct_add", "duct_split",
                        "duct_refresh_review", "duct_refresh_reject", "duct_reading_review", "duct_reading_withdraw",
                        "duct_parent_readings_correct", "duct_parent_graphics_correct", "duct_arc_repartition",
                        "duct_revision_stage", "duct_revision_review", "duct_revision_reject", "duct_revision_check_review"):
            operation = {"duct_prepare": duct_takeoff.prepare, "duct_correct": duct_takeoff.correct,
                         "duct_recalculate": duct_takeoff.recalculate, "duct_review_context": duct_takeoff.review_context,
                         "duct_add": duct_takeoff.add, "duct_split": duct_takeoff.split,
                         "duct_arc_repartition": duct_takeoff._module("project_duct_arc_repartition").review,
                         "duct_refresh_review": duct_takeoff.review_refresh,
                         "duct_refresh_reject": duct_takeoff.reject_refresh,
                         "duct_reading_review": duct_takeoff._module('project_duct_reading_links').review,
                         "duct_reading_withdraw": duct_takeoff._module('project_duct_reading_links').withdraw,
                         "duct_parent_readings_correct": duct_takeoff._module('project_duct_reading_links').correct_parent_readings,
                         "duct_parent_graphics_correct": duct_takeoff._module('project_duct_reading_links').correct_parent_graphics,
                         "duct_revision_check_review": duct_takeoff._module("project_duct_revision_checks").review,
                         "duct_revision_stage": duct_takeoff._module('project_duct_revision').stage,
                         "duct_revision_review": duct_takeoff._module('project_duct_revision').review,
                         "duct_revision_reject": duct_takeoff._module('project_duct_revision').reject}[action]
            operation(self.workspace, data, sheets, value, actor, reason)
            data["selected_stage"] = "takeoff"
        elif action == "air_device_find":
            exact("source")
            source, _, key = self._source(value["source"], sheets)
            if data["pages"].get(key, {}).get("role") not in ("plan", "detail", "schedule", "legend"):
                raise WorkflowError("air_device_source_role", "Assign this page as a plan, detail, schedule or legend before reading air devices.", 409)
            if "air_devices" not in data["project"]["scopes"]:
                raise WorkflowError("air_device_scope_excluded", "Include air devices in the project scope before reading them.", 409)
            self.air_device_producer.start({key: source[key] for key in ("revision_id", "index")}, actor, reason)
            data["selected_stage"] = "takeoff"
        elif action == "air_device_find_cancel":
            exact("job_id")
            self.air_device_producer.cancel(text(value["job_id"], 100))
        elif action.startswith("air_device_"):
            air_takeoff.apply(self.workspace, data, sheets, action, value, actor, reason)
            data["selected_stage"] = "takeoff"
        elif action == "duct_find":
            exact("source")
            source, _, key = self._source(value["source"], sheets)
            if data["pages"].get(key, {}).get("role") not in ("plan", "detail", "riser"):
                raise WorkflowError("duct_source_role", "Assign the selected page as a plan, detail or riser before finding ductwork.", 409)
            if "ductwork" not in data["project"]["scopes"]:
                raise WorkflowError("duct_scope_excluded", "Include ductwork in the project scope before finding it.", 409)
            self.workspace.duct_producer.start({key: source[key] for key in ("revision_id", "index")}, actor, reason)
        elif action == "duct_find_cancel":
            exact("job_id")
            self.workspace.duct_producer.cancel(text(value["job_id"], 100))
        elif action == "reconciliation_decision":
            exact("group_id", "action", "group_fingerprint", "schedule_id", "plan_ids", "exclusions", "supersedes")
            schedule_reconciliation.decide(self.workspace, data, sheets, value, actor, reason)
        elif action == "equipment":
            exact("run_id")
            run = self.workspace.equipment.result(text(value["run_id"], 32))
            for source in run["sources"]:
                self._source({k: source[k] for k in ("revision_id", "index")}, sheets)
            data["equipment_run"] = run["id"]
        elif action == "extract_equipment":
            exact("mode")
            if value["mode"] == "text":
                documents = self._documents(data, sheets)
                if not documents or documents["state"] != "completed" or documents["stale"]:
                    raise WorkflowError("documents_first", "Read the current project documents first, so equipment uses their schedule fields and plan links.", 409)
                run = self.workspace.equipment.from_documents(documents["id"])
                data["equipment_run"] = run["id"]
                data["selected_stage"] = "equipment"
                return
            selections = [{"revision_id": v["source"]["revision_id"], "index": v["source"]["index"], "role": v["role"]}
                          for k, v in data["pages"].items() if k in sheets and v["role"] in ("plan", "schedule")]
            if not all(any(v["role"] == role for v in selections) for role in ("plan", "schedule")):
                raise WorkflowError("equipment_pages", "Identify a plan and an equipment schedule in Document review first.", 409)
            run = self.workspace.equipment.start(selections, value["mode"])
            data["equipment_run"] = run["id"]
            data["selected_stage"] = "equipment"
        elif action == "detect_scales":
            exact()
            reading = self._documents(data, sheets)
            if not reading or reading["state"] != "completed" or reading["stale"]:
                raise WorkflowError("reading_required", "Read the current document set before finding its scale labels.", 409)
            reading = self.workspace.documents.verified_result(reading["id"])
            existing = {candidate["id"] for candidate in data["scale_candidates"]}
            issues = []
            for page in reading["pages"]:
                if page["role"] not in ("plan", "detail", "riser", "legend", "unassigned"):
                    continue
                if page["state"] != "read" or "layout" not in page:
                    issues.append({"source": {k: page[k] for k in ("revision_id", "index", "sheet_id")},
                                   "message": "This page has no readable positioned text for scale labels."})
                    continue
                source = {k: page[k] for k in ("revision_id", "index", "sheet_id")}
                try:
                    geometry = self._geometry_for(source, sheets)
                    layout = json.loads(self.workspace.documents._artifact_bytes(page["layout"]))
                    sheet_geometry.check_layout(layout, geometry)
                    candidates = scale_labels.parse_scale_labels(layout)
                except (ValueError, WorkflowError) as error:
                    issues.append({"source": source, "message": getattr(error, "message", "The positioned page cannot supply verified scale-label coordinates.")})
                    continue
                for candidate in candidates:
                    candidate["reader_candidate_id"] = candidate["id"]
                    candidate.update(document_run_id=reading["id"], layout_ref=copy.deepcopy(page["layout"]),
                                     geometry_fingerprint=geometry["fingerprint"],
                                     reader_sha256=hashlib.sha256(Path(scale_labels.__file__).read_bytes()).hexdigest())
                    candidate["id"] = digest(candidate)
                    if candidate["id"] not in existing:
                        data["scale_candidates"].append(candidate)
                        existing.add(candidate["id"])
            data["scale_reading_issues"] = issues
        elif action == "scale_declared":
            exact("candidate_id", "view", "label", "uniform_scale_confirmed")
            candidate = next((row for row in data["scale_candidates"] if row["id"] == value["candidate_id"]), None)
            if candidate is None:
                raise WorkflowError("scale_candidate_missing", "Read and select a scale label from the project.", 409)
            effective, _ = self._scale_state(data, sheets)
            current = next(row for row in effective["scale_candidates"] if row["id"] == candidate["id"])
            reading = self._documents(data, sheets)
            if current["validity"] != "current" or not reading or reading["stale"]:
                raise WorkflowError("scale_candidate_stale", "Read current scale evidence before assigning this label.", 409)
            self.workspace.documents.verified_result(candidate["document_run_id"])
            layout = json.loads(self.workspace.documents._artifact_bytes(candidate["layout_ref"]))
            parsed = next((row for row in scale_labels.parse_scale_labels(layout)
                           if row["id"] == candidate["reader_candidate_id"]), None)
            if parsed is None or any(parsed[key] != candidate[key] for key in parsed if key != "id"):
                raise WorkflowError("scale_candidate_changed", "The saved scale label does not match its original page.", 409)
            geometry = self._geometry_for(candidate["source"], sheets)
            sheet_geometry.check_layout(layout, geometry)
            data["calibrations"].append(sheet_scale.make_declared(candidate["source"], geometry, candidate,
                region(value["view"]), text(value["label"], 120), value["uniform_scale_confirmed"], actor, reason))
        elif action == "scale_decision":
            exact("fact_id", "state")
            fact = next((row for row in data["calibrations"] if row["id"] == value["fact_id"]), None)
            if fact is None:
                raise WorkflowError("scale_missing", "The saved scale fact was not found.", 409)
            try:
                geometry = self._geometry_for(fact["source"], sheets)
            except (ValueError, WorkflowError):
                if value["state"] == "verified":
                    raise
                geometry = None
            data["scale_decisions"].append(sheet_scale.decide(data["calibrations"], data["scale_decisions"],
                fact["id"], value["state"], geometry, actor, reason))
        elif action == "calibrate":
            exact("source", "view", "points", "known_length", "unit", "uniform_scale_confirmed", "label")
            source, sheet, _ = self._source(value["source"], sheets)
            geometry = sheet_geometry.sheet_geometry(sheet, source["revision_id"])
            data["calibrations"].append(sheet_scale.make_calibration(source, geometry, value, actor, reason))
        elif action == "measure":
            exact("calibration_id", "points", "scope", "label")
            calibration = next((v for v in data["calibrations"] if v["id"] == value["calibration_id"]), None)
            if calibration is None:
                raise WorkflowError("scale_missing", "Calibrate the drawing view before measuring.", 409)
            if value["scope"] not in data["project"]["scopes"]:
                raise WorkflowError("invalid_scope", "Choose an included mechanical scope.")
            source, sheet, _ = self._source({k: calibration["source"][k] for k in ("revision_id", "index")}, sheets)
            path = points(value["points"])
            geometry = sheet_geometry.sheet_geometry(sheet, source["revision_id"])
            resolution = sheet_scale.resolve(data["calibrations"], data["scale_decisions"], calibration["id"], geometry, path)
            calculation = sheet_scale.measure_path(path, geometry, resolution)
            data["measurements"].append(dict(calculation, id=uuid.uuid4().hex, source=source,
                calibration_id=calibration["id"], points=path, scope=value["scope"],
                label=text(value["label"], 160), actor=actor, reason=reason, at=stamp()))
        elif action == "measure_recalculate":
            exact("measurement_id", "calibration_id")
            original = next((row for row in data["measurements"] if row["id"] == value["measurement_id"]), None)
            if original is None or original["id"] in data["measurement_replacements"]:
                raise WorkflowError("measurement_missing", "Choose an active saved measurement to recalculate.", 409)
            geometry = self._geometry_for(original["source"], sheets)
            resolution = sheet_scale.resolve(data["calibrations"], data["scale_decisions"], value["calibration_id"], geometry, original["points"])
            calculation = sheet_scale.measure_path(original["points"], geometry, resolution)
            replacement = dict(original, **calculation)
            replacement.update(id=uuid.uuid4().hex, calibration_id=value["calibration_id"],
                               actor=actor, reason=reason, at=stamp(), replaces=original["id"])
            data["measurements"].append(replacement)
            data["measurement_replacements"][original["id"]] = replacement["id"]
            for item in data["items"]:
                if item.get("measurement_id") == original["id"]:
                    item["measurement_id"] = replacement["id"]
                    item["quantity"] = decimal_text(Decimal(replacement["meters"]) / UNITS[item["unit"]])
        elif action == "item":
            exact("id", "scope", "description", "system", "size", "material", "quantity", "unit", "evidence_class", "source", "measurement_id", "note")
            if value["scope"] not in data["project"]["scopes"] or value["evidence_class"] not in CLASSES:
                raise WorkflowError("invalid_item", "Choose an included scope and evidence class.")
            source, _, _ = self._source(value["source"], sheets)
            unit = value["unit"]
            if unit not in ("each", "m", "ft", "m2", "ft2", "kg", "lb", "lot"):
                raise WorkflowError("invalid_unit", "Choose a supported takeoff unit.")
            quantity = None if value["quantity"] is None else str(number(value["quantity"]))
            measurement_id = value["measurement_id"]
            if measurement_id:
                effective, _ = self._scale_state(data, sheets)
                measurement = next((v for v in effective["measurements"] if v["id"] == measurement_id), None)
                if (measurement is None or measurement["source"] != source or measurement["scope"] != value["scope"]
                        or unit not in ("m", "ft") or value["evidence_class"] != "derived"):
                    raise WorkflowError("measurement_mismatch", "Use a measurement from this scope and page, with a length unit and derived evidence.")
                if measurement["validity"] != "current":
                    raise WorkflowError("measurement_blocked", "Verify the drawing scale and recalculate this measurement before using its quantity.", 409)
                quantity = decimal_text(Decimal(measurement["meters"]) / UNITS[unit])
            elif value["evidence_class"] == "derived":
                raise WorkflowError("measurement_required", "Link a saved measurement for a derived length.")
            if value["evidence_class"] == "unmeasurable" and quantity is not None:
                raise WorkflowError("quantity_unknown", "An unmeasurable quantity must remain unknown.")
            identifier = value["id"]
            existing = next((v for v in data["items"] if v["id"] == identifier), None) if identifier else None
            if identifier and existing is None:
                raise WorkflowError("item_missing", "The takeoff item was not found.", 404)
            item = {"id": identifier or uuid.uuid4().hex, "scope": value["scope"], "source": source,
                    "quantity": quantity, "unit": unit, "evidence_class": value["evidence_class"],
                    "measurement_id": measurement_id or None}
            for key in ("description", "system", "size", "material", "note"):
                item[key] = text(value[key], 500, key in ("description", "note"))
            if existing:
                data["items"][data["items"].index(existing)] = item
            else:
                data["items"].append(item)
        elif action == "scope_review":
            exact("scope", "disposition")
            scope = value["scope"]
            if scope not in data["project"]["scopes"] or value["disposition"] not in ("reviewed", "not_applicable"):
                raise WorkflowError("invalid_scope", "Choose a scope and review disposition.")
            effective, _ = self._scale_state(data, sheets)
            items = [v for v in effective["items"] if v["scope"] == scope]
            duct = duct_takeoff.view(self.workspace, data, sheets) if scope == "ductwork" else None
            duct_available = bool(duct and duct["available"])
            air = air_takeoff.view(self.workspace, data, sheets) if scope == "air_devices" else None
            if air is not None:
                excluded_empty = (value["disposition"] == "not_applicable" and
                    not data.get("air_device_readings") and not air["available"] and not items)
                if (not excluded_empty and (value["disposition"] != "reviewed" or not air["available"] or air["stale"] or
                        not (air.get("result") or {}).get("complete"))):
                    raise WorkflowError("scope_incomplete", "Finish the source-linked air-device review, including coverage, before reviewing this scope.", 409)
                data["scope_reviews"][scope] = {"disposition": value["disposition"], "reason": reason,
                    "actor": actor, "fingerprint": self._scope_fingerprint(effective, scope, air=air)}
                return
            if ((value["disposition"] == "reviewed" and
                 ((not items and not duct_available) or any(v["quantity"] is None for v in items) or
                  (duct_available and (not duct["complete"] or
                    any(r["state"] == "pending" for r in duct["refreshes"] + duct["revisions"])))))
                    or (value["disposition"] == "not_applicable" and (items or duct_available))):
                raise WorkflowError("scope_incomplete", "Review known quantities, or explain why an empty scope does not apply.", 409)
            data["scope_reviews"][scope] = {"disposition": value["disposition"], "reason": reason,
                "actor": actor, "fingerprint": self._scope_fingerprint(effective, scope, duct)}
        elif action == "allowance_review":
            exact("item_id")
            item = next((v for v in data["items"] if v["id"] == value["item_id"]), None)
            if not item or item["evidence_class"] != "allowance" or item["quantity"] is None:
                raise WorkflowError("allowance_unknown", "Only an explicit allowance with a known quantity can be acknowledged.", 409)
            data.setdefault("allowance_reviews", {})[item["id"]] = {
                "fingerprint": digest(item), "reason": reason, "actor": actor, "at": stamp()}
        elif action == "issue":
            exact("id", "message", "state", "resolution", "source")
            source = self._source(value["source"], sheets)[0] if value["source"] is not None else None
            if value["state"] not in ("open", "resolved"):
                raise WorkflowError("invalid_issue", "Choose an open or resolved issue state.")
            existing = next((v for v in data["issues"] if v["id"] == value["id"]), None) if value["id"] else None
            if value["id"] and existing is None:
                raise WorkflowError("issue_missing", "The issue was not found.", 404)
            issue = {"id": value["id"] or uuid.uuid4().hex, "source": source, "message": text(value["message"]),
                     "state": value["state"], "resolution": text(value["resolution"], required=value["state"] == "resolved")}
            if existing:
                data["issues"][data["issues"].index(existing)] = issue
            else:
                data["issues"].append(issue)
        elif action == "review":
            exact("stage")
            view, fingerprints = self._snapshot(data, state, sheets, self._equipment(data))
            stage = next((v for v in view["stages"] if v["id"] == value["stage"]), None)
            if not stage or not stage["can_review"]:
                raise WorkflowError("stage_incomplete", "Finish this stage's required records before reviewing it.", 409)
            data["reviews"][stage["id"]] = {"fingerprint": fingerprints[stage["id"]], "actor": actor, "reason": reason, "at": stamp()}
        else:
            raise WorkflowError("unknown_action", "This workflow action was not found.", 404)

    def export(self):
        with self.lock:
            view = self.view()
            _, sheets = self._inventory()
            duct_source_records = duct_takeoff.verify_export(self.workspace, self.data, sheets)
            air_files = (air_takeoff.export_files(self.workspace, self.data, sheets)
                         if self.data.get("air_device_readings") else {})
            evaluation_sources = duct_evaluation.export_records(self, view["model_baseline"])
            all_duct_sources = {record["id"]: record for record in duct_source_records}
            for record in evaluation_sources:
                if record["id"] in all_duct_sources and all_duct_sources[record["id"]] != record:
                    raise WorkflowError("duct_evaluation_producer", "Duct producer evidence is inconsistent.", 409)
                all_duct_sources[record["id"]] = record
            revisions = {v["revision_id"] for v in view["inventory"]}
            for revision in sorted(revisions):
                snapshot, _ = self.workspace.verified_pdf(revision)
                snapshot.close()
            equipment_csv = (self.workspace.equipment.export(view["equipment_run"])
                             if view["equipment"] and view["equipment"]["state"] == "completed" else None)
            equipment_history_csv = None
            if equipment_csv is not None and view["equipment"].get("stale"):
                equipment_history_csv = equipment_csv
                original_csv = csv.DictReader(io.StringIO(equipment_csv.decode("utf-8-sig")))
                headers = original_csv.fieldnames + ["Stored historical count"]
                current_rows = []
                for row in original_csv:
                    row["Stored historical count"] = row["Reviewed equipment count"]
                    row["Reviewed equipment count"] = "UNKNOWN"
                    row["Status"] = "BLOCKED - earlier document reading"
                    current_rows.append([row[key] for key in headers])
                equipment_csv = csv_bytes(headers, current_rows)
            reading = view["document_reading"]
            if reading and reading["state"] == "completed":
                self.workspace.documents.verified_result(reading["id"])
            try:
                workbook = takeoff_workbook.workbook_bytes(view)
            except ValueError as error:
                raise WorkflowError("workbook_projection", str(error), 409) from None
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("README.txt", "HELEOS DRAFT TAKEOFF\nThis package contains the saved workflow, source references, operator measurements, mechanical records and unresolved work. It is not a bid or an approved estimate.\nIncomplete stages: " + ", ".join(v["title"] for v in view["stages"][:-1] if v["status"] != "reviewed") + "\n")
                archive.writestr("workflow.json", packed(view))
                archive.writestr("takeoff.xlsx", workbook)
                archive.writestr("WORKBOOK.txt", "HELEOS DRAFT WORKBOOK\nOpen takeoff.xlsx for supported duct lengths and air-device counts, formula-linked subtotals, conditional complete totals, saved history and exact source references. UNKNOWN means unresolved, not zero. Other Division 23 quantities remain outstanding.\nThe workbook is a snapshot. Edits in Excel do not correct Heleos or update source evidence. Correct and review in Heleos, then regenerate this package. Live formulas support inspection; the deterministic application remains quantity authority.\nSources names JSON records in this extracted package. Complete original-source identities and retained evidence remain there. This is not a bid or approved estimate.\n")
                for name, payload in air_files.items():
                    archive.writestr(name, payload)
                if view["duct_takeoff"]["available"]:
                    duct = view["duct_takeoff"]
                    archive.writestr("duct-takeoff.json", packed(duct))
                    archive.writestr("duct-evidence.json", packed(self.data["duct_evidence"]))
                    archive.writestr("duct-revisions.json", packed(self.data["duct_revisions"]))
                    archive.writestr("duct-revision-decisions.json", packed(self.data["duct_revision_decisions"]))
                    archive.writestr("duct-refreshes.json", packed(self.data["duct_refreshes"]))
                    archive.writestr("duct-refresh-decisions.json", packed(self.data["duct_refresh_decisions"]))
                    archive.writestr("duct-reading-relationships.json", packed({
                        "relationships": duct_takeoff._saved(self.data).get("reading_relationships", []),
                        "events": self.data["duct_reading_relationship_events"]}))
                    archive.writestr("duct-parent-reading-events.json", packed(self.data['duct_parent_reading_events']))
                    archive.writestr("duct-arc-repartition-events.json", packed(self.data["duct_arc_repartition_events"]))
                    archive.writestr("duct-revision-check-events.json", packed(self.data["duct_revision_check_events"]))
                    archive.writestr("duct-parent-graphic-events.json", packed(self.data['duct_parent_graphic_events']))
                    archive.writestr("duct-lengths.csv", csv_bytes(
                        ["Group", "System", "Material", "Work status", "Size", "Known subtotal ft", "Complete total ft", "Complete", "Rows"],
                        [[group["id"], group["group"].get("system"), group["group"].get("material"),
                          group["group"].get("work_status"), json.dumps(group["group"].get("size"), sort_keys=True),
                          group["known_subtotal_ft"], group["total_ft"], group["complete"], "; ".join(group["row_ids"])]
                         for group in duct["groups"]]))
                    archive.writestr("duct-segments.csv", csv_bytes(
                        ["Segment", "Status", "Meters", "Feet", "Stored historical meters", "Revision", "Page", "Issues"],
                        [[segment["id"], segment["status"], segment["meters"], segment["feet"], segment["stored_meters"],
                          segment["source"]["revision_id"], segment["source"]["index"] + 1, "; ".join(segment["issues"])]
                         for segment in duct["segments"]]))
                duct_responses = set()
                for record in all_duct_sources.values():
                    archive.writestr("duct-producers/" + record["id"] + ".json", packed(record))
                    if record.get("response") and record["response"]["sha256"] not in duct_responses:
                        raw = self.workspace.duct_producer.artifact_bytes(record["response"])
                        archive.writestr("duct-responses/" + record["response"]["sha256"] + ".json", raw)
                        duct_responses.add(record["response"]["sha256"])
                evaluation_images = set()
                for record in evaluation_sources:
                    prepared = record["input"]
                    if prepared["input_sha256"] not in evaluation_images:
                        raw = self.model_inference.knowledge.source_bytes(prepared["source_id"])
                        archive.writestr("duct-evaluation-inputs/" + prepared["input_sha256"] + ".png", raw)
                        evaluation_images.add(prepared["input_sha256"])
                correspondence = view["schedule_reconciliation"]
                if correspondence["available"]:
                    for generation in correspondence["generations"]:
                        self.workspace.documents.verified_result(generation["reading_id"])
                    archive.writestr("schedule-reconciliation.json", packed(correspondence))
                    groups = {group["id"]: group for group in correspondence["groups"]}
                    archive.writestr("schedule-plan-links.csv", csv_bytes(
                        ["Group", "Tag", "Schedule node", "Plan node", "Status", "Validity", "Decision", "Physical quantity"],
                        [[edge["group_id"], groups[edge["group_id"]]["tag"], edge["schedule_id"], edge["plan_id"],
                          edge["status"], groups[edge["group_id"]]["validity"], groups[edge["group_id"]]["decision_id"], None]
                         for edge in correspondence["edges"]]))
                    archive.writestr("schedule-plan-groups.csv", csv_bytes(
                        ["Group", "Tag", "Status", "Validity", "Schedule nodes", "Plan nodes", "Issues", "Physical quantity"],
                        [[group["id"], group["tag"], group["status"], group["validity"],
                          "; ".join(group["schedule_ids"]), "; ".join(group["plan_ids"]),
                          "; ".join(group["issues"]), None] for group in correspondence["groups"]]))
                if view["calibrations"] or view["scale_candidates"] or view["measurements"]:
                    archive.writestr("sheet-scales.json", packed({
                        "geometries": view["sheet_geometries"], "facts": self.data["calibrations"],
                        "fact_states": view["calibrations"], "candidates": view["scale_candidates"],
                        "decisions": self.data["scale_decisions"], "reading_issues": view["scale_reading_issues"],
                        "stored_measurements": self.data["measurements"], "current_measurements": view["measurements"],
                        "replacements": self.data["measurement_replacements"],
                        "stored_items": self.data["items"]}))
                if view["model_inference"]["inputs"] or view["model_inference"]["jobs"]:
                    archive.writestr("model-inference.json", packed(view["model_inference"]))
                    included = set()
                    for prepared in view["model_inference"]["inputs"]:
                        source = self.workspace.documents.knowledge_store.source_bytes(prepared["source_id"])
                        if hashlib.sha256(source).hexdigest() != prepared["input_sha256"]:
                            raise WorkflowError("model_input_changed", "The frozen model input no longer matches its evidence.", 409)
                        if prepared["input_sha256"] not in included:
                            archive.writestr("model-inputs/" + prepared["input_sha256"] + ".png", source)
                            included.add(prepared["input_sha256"])
                    included = set()
                    for job in view["model_inference"]["jobs"]:
                        references = [sample.get("response") for sample in job["samples"]]
                        references.extend(attempt["response"] for attempt in job["attempts"])
                        references.append(job.get("failed_response"))
                        for reference in references:
                            if reference and reference["sha256"] not in included:
                                payload = self.model_inference.artifact_bytes(reference)
                                archive.writestr("model-responses/" + reference["sha256"] + ".json", payload)
                                included.add(reference["sha256"])
                if any(view["model_baseline"][key] for key in ("datasets", "models", "plans", "runs")) or view["model_sources"]:
                    archive.writestr("model-baseline.json", packed(dict(view["model_baseline"], sources=view["model_sources"])))
                    archive.writestr("model-evaluations.csv", csv_bytes(
                        ["Run", "Plan", "Precision", "Recall", "Mean latency ms", "Peak memory MB", "Thresholds met", "Issues", "Evidence"],
                        [[entry["id"], entry["payload"]["plan_id"],
                          entry["payload"]["report"]["metrics"]["precision"], entry["payload"]["report"]["metrics"]["recall"],
                          entry["payload"]["report"]["metrics"]["mean_latency_ms"], entry["payload"]["report"]["metrics"]["peak_memory_mb"],
                          entry["payload"]["report"]["thresholds_met"], "; ".join(entry["issues"]),
                          "Supplied saved outputs; model execution and hardware measurements not independently verified"]
                         for entry in view["model_baseline"]["runs"] if "task" not in entry["payload"]]))
                    duct_runs = [entry for entry in view["model_baseline"]["runs"]
                                 if entry["payload"].get("task") == duct_evaluation.TASK]
                    if duct_runs:
                        names = ["matching_state", "true_positive", "false_positive", "false_negative", "precision", "recall",
                                 "size_accuracy", "work_status_accuracy", "length_coverage", "max_absolute_length_error_ft"]
                        archive.writestr("duct-evaluations.csv", csv_bytes(
                            ["Run", "Plan"] + names + ["Thresholds met", "Issues", "Topology", "Resource use", "Evidence", "Scorer version", "Geometry scope", "Topology accuracy", "Topology coverage", "Threshold scope"],
                            [[entry["id"], entry["payload"]["plan_id"]] +
                             [entry["payload"]["report"]["metrics"][name] for name in names] +
                             [entry["payload"]["report"]["thresholds_met"], "; ".join(entry["issues"]),
                              (entry["payload"]["report"]["topology"].get("state") if isinstance(entry["payload"]["report"].get("topology"), dict) else "unavailable"),
                              "unavailable", "Retained uncorrected producer results; diagnostic evaluation only",
                              entry["payload"]["report"]["version"],
                              "; ".join(entry["payload"]["report"].get("geometry_scope", ["planar"])),
                              (entry["payload"]["report"]["topology"].get("known_pair_accuracy") if isinstance(entry["payload"]["report"].get("topology"), dict) else None),
                              (entry["payload"]["report"]["topology"].get("coverage") if isinstance(entry["payload"]["report"].get("topology"), dict) else None),
                              entry["payload"]["report"].get("threshold_scope", "geometry_attributes_and_length_only")]
                             for entry in duct_runs]))
                        topology_rows = []
                        for entry in duct_runs:
                            for sample in entry['payload']['report']['samples']:
                                topology = sample.get('topology', {})
                                if not isinstance(topology, dict):
                                    continue
                                assertions = {a['id']: a for a in topology.get('truth_assertions', [])}
                                predictions = {a['id']: a for a in topology.get('prediction_assertions', [])}
                                rows = topology.get('rows', [])
                                for row in rows:
                                    truth = assertions.get(row['truth_id'], {})
                                    observed = predictions.get(row['prediction_id'], {})
                                    topology_rows.append([entry['id'], sample['sample_id'],
                                        sample['source']['revision_id'], sample['source']['index'] + 1,
                                        row['truth_id'], '; '.join(row['truth_members']), row['expected'],
                                        row['prediction_id'], '; '.join(row['prediction_members'] or []),
                                        row['observed'], row['state'], '; '.join(truth.get('evidence_ids', [])),
                                        '; '.join(observed.get('evidence_ids', [])),
                                        'model-baseline.json; source-linked evidence in retained report; no quantity authority'])
                                if topology.get('state') == 'unavailable':
                                    for assertion in assertions.values():
                                        topology_rows.append([entry['id'], sample['sample_id'],
                                            sample['source']['revision_id'], sample['source']['index'] + 1,
                                            assertion['id'], '; '.join(assertion['members']), assertion['relation'],
                                            None, None, None, 'unavailable', '; '.join(assertion['evidence_ids']), None,
                                            topology.get('reason', 'unavailable') + '; retained truth in model-baseline.json; no quantity authority'])
                                used = {row['prediction_id'] for row in rows}
                                for assertion in predictions.values():
                                    if assertion['id'] not in used:
                                        topology_rows.append([entry['id'], sample['sample_id'],
                                            sample['source']['revision_id'], sample['source']['index'] + 1,
                                            None, None, None, assertion['id'], '; '.join(assertion['members']),
                                            assertion['relation'], 'unscored', None, '; '.join(assertion['evidence_ids']),
                                            'No independently paired truth; no quantity authority'])
                        archive.writestr('duct-topology.csv', csv_bytes(
                            ['Run', 'Sample', 'Revision', 'Page', 'Truth assertion', 'Truth paths',
                             'Expected', 'Prediction assertion', 'Prediction paths', 'Observed', 'State',
                             'Truth evidence', 'Prediction evidence', 'Evidence'], topology_rows))
                archive.writestr("takeoff.csv", csv_bytes(
                    ["Scope", "Description", "System", "Size", "Material", "Quantity", "Unit", "Evidence", "Revision", "Page", "Measurement", "Note", "Status"],
                    [[v["scope"], v["description"], v["system"], v["size"], v["material"], v["quantity"], v["unit"], v["evidence_class"], v["source"]["revision_id"], v["source"]["index"] + 1, v["measurement_id"] or "", v["note"], "BLOCKED" if v.get("validity") == "blocked" else "DRAFT"] for v in view["items"]]))
                archive.writestr("measurements.csv", csv_bytes(
                    ["Description", "Scope", "Meters", "Calibration", "Revision", "Page", "Status", "Stored historical meters", "Reason"],
                    [[v["label"], v["scope"], v["meters"], v["calibration_id"], v["source"]["revision_id"], v["source"]["index"] + 1, v["validity"], v["stored_meters"], v["block_message"] or ""] for v in view["measurements"]]))
                archive.writestr("unresolved-items.csv", csv_bytes(
                    ["Issue", "State", "Resolution"], [[v["message"], v["state"], v.get("resolution", "")] for v in view["issues_view"]]))
                if equipment_csv is not None:
                    archive.writestr("equipment.csv", equipment_csv)
                if equipment_history_csv is not None:
                    archive.writestr("equipment-history.csv", equipment_history_csv)
                if reading and reading["state"] == "completed":
                    archive.writestr("document-reading.json", packed(reading))
                    coverage = view["mechanical_scope"]
                    archive.writestr("mechanical-scope.json", packed(coverage))
                    archive.writestr("mechanical-sections.csv", csv_bytes(
                        ["Section", "Headings", "Requirements", "Applicable", "Excluded", "Pending", "Stale reviews", "Review state", "Sources", "Coverage"],
                        [[row["section"], " | ".join(h["text"] for h in row["headings"]),
                          row["counts"]["requirements"], row["counts"]["applicable"],
                          row["counts"]["excluded"], row["counts"]["pending"], row["counts"]["stale"],
                          row["review_state"], json.dumps([h["source"] for h in row["headings"]], ensure_ascii=False),
                          "SUPPLIED SECTIONS ONLY - COMPLETENESS UNVERIFIED"]
                         for row in coverage["sections"]]))
                    scope_requirements = [(row["section"], requirement) for row in coverage["sections"]
                                          for requirement in row["requirements"]]
                    scope_requirements.extend(("UNASSIGNED", r) for r in coverage["unassigned_requirements"])
                    archive.writestr("mechanical-requirements.csv", csv_bytes(
                        ["Section", "Requirement", "Qualifiers", "Disposition", "Review state", "Stored disposition", "Reason", "Revision", "Page", "Location"],
                        [[section, r["text"], "; ".join(r["qualifiers"]), r["disposition"],
                          r["review_state"], r["stored_disposition"], r["reason"],
                          r["source"]["revision_id"], r["source"]["index"] + 1,
                          json.dumps(r["source"].get("bbox"))] for section, r in scope_requirements]))
                    knowledge = view["project_knowledge"]
                    if knowledge:
                        archive.writestr("mechanical-knowledge.json", packed(knowledge))
                        archive.writestr("mechanical-rule-candidates.csv", csv_bytes(
                            ["Candidate", "Statement", "Categories", "Conditions", "Sources", "Source issues", "Status"],
                            [[r["id"], r["payload"]["statement"], "; ".join(r["payload"]["categories"]),
                              "; ".join(r["payload"]["qualifiers"]),
                              "; ".join(c["source_id"] + " " + c["locator"] for c in r["payload"]["citations"]),
                              json.dumps(r["issues"], ensure_ascii=False), "CANDIDATE - NO QUANTITY AUTHORITY"]
                             for r in knowledge["rules"]]))
                    applicability = view["rule_applicability"]
                    admission = view["rule_admission"]
                    if admission:
                        archive.writestr("rule-admission.json", packed(admission))
                        archive.writestr("rule-versions.csv", csv_bytes(
                            ["Rule", "Version", "Purpose", "State", "Author", "Reason", "Validation passed", "Recorded", "Issues"],
                            [[version["payload"]["rule_id"], version["id"], version["payload"]["scope"]["purpose"],
                              version["status"], version["payload"]["author"], version["payload"]["reason"],
                              version["payload"]["validation"]["passed"], version["recorded_at"],
                              "; ".join(version["issues"])] for version in admission["versions"]]))
                    if applicability:
                        archive.writestr("rule-applicability.json", packed(applicability))
                        archive.writestr("requirement-decisions.csv", csv_bytes(
                            ["Item", "Requirement", "Disposition", "State", "Actor", "Reason", "Recorded", "Evidence"],
                            [[decision["object_label"], decision["requirement_text"],
                              decision["payload"]["disposition"], decision["resolution_state"],
                              decision["payload"]["actor"], decision["payload"]["reason"], decision["recorded_at"],
                              "; ".join(citation["locator"] for citation in decision["payload"]["citations"])]
                             for decision in applicability["decision_records"]]))
                        archive.writestr("rule-scope-matches.csv", csv_bytes(
                            ["Item", "Requirement", "Effect", "Polarity", "Status", "Reasons", "Revision", "Page"],
                            [[row["object_label"], row["requirement_id"], row["effect"]["text"],
                              row["effect"]["polarity"], row["status"], "; ".join(row["reasons"]),
                              row["source"].get("revision_id", ""),
                              row["source"]["index"] + 1 if "index" in row["source"] else ""]
                             for row in applicability["evaluations"]]))
                    archive.writestr("schedule-fields.csv", csv_bytes(
                        ["Tag", "Schedule", "Column", "Field", "Read value", "Revision", "Page", "Location", "Status"],
                        [[r["tag"], r["equipment_type"] or "", f["header"], f["name"], f["value"],
                          r["source"]["revision_id"], r["source"]["index"] + 1,
                          json.dumps(f["source"].get("bbox")) if f.get("source") else "", "EXTRACTED DRAFT"]
                         for r in reading["schedule_rows"] for f in r["fields"]]))
                    archive.writestr("requirements.csv", csv_bytes(
                        ["Requirement", "Categories", "Section", "Tags", "Qualifiers", "Revision", "Page", "Disposition", "Reason", "Status"],
                        [[r["text"], "; ".join(r["categories"]), r["section"] or "", "; ".join(r["tags"]),
                          "; ".join(r["qualifiers"]), r["source"]["revision_id"], r["source"]["index"] + 1,
                          view["requirement_reviews"].get(r["id"], {}).get("disposition", "pending"),
                          view["requirement_reviews"].get(r["id"], {}).get("reason", ""), "EXTRACTED DRAFT"]
                         for r in reading["requirements"]]))
                    archive.writestr("equipment-register.csv", csv_bytes(
                        ["Tag", "Schedule rows", "Plan references", "Written requirements", "Physical quantity", "Issues", "Status"],
                        [[r["tag"], "; ".join(r["schedule_row_ids"]), len(r["plan_occurrences"]),
                          "; ".join(r["requirement_ids"]), r["quantity"], "; ".join(r["issues"]), "EXTRACTED DRAFT"]
                         for r in reading["equipment_register"]]))
            return output.getvalue()
