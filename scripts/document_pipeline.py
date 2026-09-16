"""Local document-to-register reading. Extracted facts remain draft proposals.

Consumes verified originals through the existing PDF reader. Saves the positioned
text and parser outputs, then links schedules, plan tags and written requirements.
No model download, cloud call, invented quantity or source-file edit occurs here.
"""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid


def module(name):
    spec = importlib.util.spec_from_file_location("heleos_" + name, Path(__file__).with_name(name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


layout_reader = module("document_layout")
schedule_reader = module("document_schedule")
requirement_reader = module("document_requirements")
equipment_hints = module("equipment_takeoff")
project_knowledge = module("project_knowledge")
VERSION = "document-reading-3"
MAX_PAGES = 256
MAX_RUN = 64 * 1024 * 1024
MAX_EVIDENCE = 256 * 1024 * 1024
RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
ROLES = ("plan", "schedule", "specification", "detail", "riser", "legend", "addendum", "unassigned")


class DocumentError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def source_box(page, bounds):
    return dict({k: page[k] for k in ("revision_id", "index", "sheet_id")}, bbox=bounds)


def tag_occurrences(page, tags):
    """Find schedule-declared tags, including prefixes outside the old hint list."""
    if not tags:
        return []
    # Match all candidates once per line, then look them up. Avoid N regex scans
    # per page and retain the distinction between EF-1 and EF-01.
    pattern = re.compile(r"(?<![A-Z0-9])([A-Z]{1,12})\s*[-\u2010-\u2015]?\s*([0-9]{1,5}[A-Z]?)(?![A-Z0-9])", re.I)
    result, seen = [], set()
    for line in page["lines"]:
        offsets, cursor = [], 0
        for word in line["words"]:
            offsets.append((cursor, cursor + len(word["text"]), word["bbox"]))
            cursor += len(word["text"]) + 1
        for match in pattern.finditer(line["text"]):
            tag = match[1].upper() + "-" + match[2].upper()
            if tag not in tags:
                continue
            boxes = [b for start, end, b in offsets if end > match.start() and start < match.end()]
            bounds = layout_reader.union(boxes)
            identity = (tag, tuple(bounds))
            if identity in seen:
                continue
            seen.add(identity)
            occurrence = {"tag": tag, "source": source_box(page, bounds), "text": match[0]}
            occurrence["id"] = digest(packed(occurrence))
            result.append(occurrence)
    return result


def build_register(schedule_rows, requirements, occurrences):
    tags = sorted({r["tag"] for r in schedule_rows} | {r["tag"] for r in occurrences})
    result = []
    for tag in tags:
        schedules = [r for r in schedule_rows if r["tag"] == tag]
        plans = [r for r in occurrences if r["tag"] == tag]
        needs = [r["id"] for r in requirements if tag in r["tags"] or tag in r.get("schedule_tag_links", [])]
        issues = []
        if not schedules:
            issues.append("no_schedule_row_match")
        if len(schedules) > 1:
            issues.append("multiple_schedule_rows")
        if not plans:
            issues.append("no_plan_tag_match")
        elif len(plans) > 1:
            issues.append("repeated_plan_tag_not_a_physical_count")
        if any(r.get("issues") for r in schedules):
            issues.append("schedule_fields_need_review")
        result.append({"tag": tag, "schedule_row_ids": [r["id"] for r in schedules],
                       "plan_occurrences": plans, "requirement_ids": needs, "issues": issues,
                       "quantity": None, "status": "candidate"})
    return result


def requirement_layout(page, rows):
    """Route prose and note cells to the requirements reader, not table data."""
    excluded, notes = [], []
    for row in rows:
        excluded.append(row["source"]["bbox"])
        for field in row["fields"]:
            if field.get("header_source"):
                excluded.append(field["header_source"]["bbox"])
            if field["name"] == "notes" and field.get("source"):
                notes.append(field["source"]["bbox"])
    def inside(bounds, areas):
        return any(a[0] <= bounds[0] and a[1] <= bounds[1] and
                   a[2] >= bounds[2] and a[3] >= bounds[3] for a in areas)
    lines = []
    for line in page["lines"]:
        words = [w for w in line["words"] if not inside(w["bbox"], excluded) or inside(w["bbox"], notes)]
        if words:
            lines.append({"text": " ".join(w["text"] for w in words), "words": words,
                          "bbox": layout_reader.union([w["bbox"] for w in words])})
    return dict(page, lines=lines)


class DocumentPipeline:
    def __init__(self, workspace, text_reader=None):
        self.workspace, self.reader = workspace, text_reader
        self.root = workspace.root / "document-reading"
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.knowledge_store = project_knowledge.storage.KnowledgeStore(workspace.root / "mechanical-knowledge")
        self.lock, self.stopping = threading.RLock(), threading.Event()
        self.thread = None
        for path in self.root.glob("*.json"):
            if RUN_ID.fullmatch(path.stem):
                run = self.result(path.stem)
                if run["state"] in ("queued", "running"):
                    run.update(state="interrupted", finished_at=now(), error="Reading stopped before completion. Start reading again to finish.")
                    self._save(run)

    def _save(self, run):
        payload = packed({"data": run, "sha256": digest(packed(run))})
        if len(payload) > MAX_RUN:
            raise DocumentError("reading_limit", "This document run exceeds the saved-record limit.", 422)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".reading-", delete=False) as out:
                temporary = Path(out.name)
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
            os.replace(str(temporary), str(self.root / (run["id"] + ".json")))
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def result(self, run_id):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise DocumentError("reading_missing", "Choose a saved document reading.", 404)
        with self.lock:
            path = self.root / (run_id + ".json")
            try:
                if path.is_symlink() or path.stat().st_size > MAX_RUN:
                    raise ValueError()
                envelope = json.loads(path.read_bytes())
                run = envelope["data"]
                if (digest(packed(run)) != envelope["sha256"] or run["id"] != run_id or
                        run["project_id"] != self.workspace.metadata["project"]["id"]):
                    raise ValueError()
                return run
            except FileNotFoundError:
                raise DocumentError("reading_missing", "The saved document reading was not found.", 404) from None
            except (OSError, ValueError, KeyError, TypeError):
                raise DocumentError("reading_invalid", "Saved document-reading records could not be verified.", 409) from None

    def artifact(self, payload, suffix):
        key = digest(payload) + suffix
        directory = self.root / "artifacts"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / key
        try:
            with path.open("xb") as out:
                os.chmod(path, 0o600)
                out.write(payload)
        except FileExistsError:
            if path.is_symlink() or digest(path.read_bytes()) != key[:64]:
                raise DocumentError("reading_evidence", "Saved page-reading evidence changed.", 409)
        return {"key": key, "sha256": key[:64], "bytes": len(payload)}

    def _artifact_bytes(self, artifact):
        key = artifact["key"]
        if (not HEX.fullmatch(artifact["sha256"]) or
                key not in (artifact["sha256"] + ".xhtml", artifact["sha256"] + ".json")):
            raise DocumentError("reading_evidence", "Invalid page-reading evidence reference.", 409)
        path = self.root / "artifacts" / key
        if path.is_symlink() or not path.is_file() or path.stat().st_size != artifact["bytes"]:
            raise DocumentError("reading_evidence", "Saved page-reading evidence is missing or changed.", 409)
        payload = path.read_bytes()
        if digest(payload) != artifact["sha256"]:
            raise DocumentError("reading_evidence", "Saved page-reading evidence changed.", 409)
        return payload

    def start(self, selections):
        if self.reader is None:
            raise DocumentError("reader_missing", "Configure the local PDF text reader before reading documents.", 409)
        if not isinstance(selections, list) or not 1 <= len(selections) <= MAX_PAGES:
            raise DocumentError("reading_pages", "Choose between 1 and 256 registered pages.")
        inventory = {(d["revision_id"], p["index"]): p for d in self.workspace.state()["documents"] for p in d["sheets"]}
        identity = self.reader.identity()
        uses_geometry = identity.get("kind") == "poppler_bbox"
        sources, seen = [], set()
        for selected in selections:
            if (not isinstance(selected, dict) or set(selected) != {"revision_id", "index", "role"}
                    or not isinstance(selected["revision_id"], str) or type(selected["index"]) is not int
                    or selected["role"] not in ROLES):
                raise DocumentError("reading_pages", "Each page needs a valid document role.")
            key = (selected["revision_id"], selected["index"])
            if key not in inventory or key in seen:
                raise DocumentError("reading_pages", "Choose each accepted page only once.")
            seen.add(key)
            source = dict(selected, sheet_id=inventory[key]["sheet_id"])
            if uses_geometry:
                try:
                    source["geometry"] = layout_reader.geometry_tools.sheet_geometry(
                        inventory[key], selected["revision_id"])
                except layout_reader.geometry_tools.GeometryError as error:
                    # Missing geometry affects this page only; it never triggers
                    # a guessed unrotated fallback on real Poppler readings.
                    source["geometry_error"] = {"code": error.code, "message": error.message}
            sources.append(source)
        sources.sort(key=lambda v: (v["revision_id"], v["index"]))
        implementation = {name: digest(Path(__file__).with_name(name + ".py").read_bytes()) for name in
                          ("document_pipeline", "document_layout", "document_schedule", "document_requirements",
                           "equipment_takeoff", "project_knowledge", "mechanical_taxonomy", "mechanical_knowledge")}
        if uses_geometry:
            implementation["sheet_geometry"] = digest(Path(__file__).with_name("sheet_geometry.py").read_bytes())
        fingerprint = digest(packed([VERSION, sources, identity, implementation]))
        with self.lock:
            if self.stopping.is_set():
                raise DocumentError("reading_stopping", "The workspace is closing.", 409)
            for path in self.root.glob("*.json"):
                if RUN_ID.fullmatch(path.stem):
                    previous = self.result(path.stem)
                    if (previous["fingerprint"] == fingerprint and
                            (previous["state"] in ("queued", "running") or
                             (previous["state"] == "completed" and not any(p["state"] == "failed" for p in previous["pages"])))):
                        return previous
            if self.thread is not None and self.thread.is_alive():
                raise DocumentError("reading_busy", "The current document reading is still running.", 409)
            run = {"id": uuid.uuid4().hex, "project_id": self.workspace.metadata["project"]["id"],
                   "version": VERSION, "created_at": now(), "state": "queued", "fingerprint": fingerprint,
                   "reader": identity, "implementation": implementation, "sources": sources,
                   "pages": [], "progress": 0, "total_pages": len(sources), "schedule_rows": [],
                   "requirements": [], "equipment_register": [], "issues": [], "quantity_authority": "draft_only"}
            self._save(run)
            initial = copy.deepcopy(run)
            self.thread = threading.Thread(target=self._execute, args=(run,), name="document-reading", daemon=True)
            self.thread.start()
            return initial

    def _current_geometry(self, source):
        if source.get("geometry_error"):
            error = source["geometry_error"]
            raise DocumentError(error["code"], error["message"], 409)
        inventory = {(d["revision_id"], p["index"]): p
                     for d in self.workspace.state()["documents"] for p in d["sheets"]}
        sheet = inventory.get((source["revision_id"], source["index"]))
        current = layout_reader.geometry_tools.sheet_geometry(sheet, source["revision_id"])
        if current != source.get("geometry") or current["sheet_id"] != source["sheet_id"]:
            raise DocumentError("geometry_stale", "Sheet geometry changed during document reading.", 409)
        return current

    def _execute(self, run):
        try:
            run["state"] = "running"
            evidence_bytes = 0
            for source in run["sources"]:
                if self.stopping.is_set():
                    raise DocumentError("reading_interrupted", "Reading stopped before completion.", 409)
                try:
                    geometry = self._current_geometry(source) if run["reader"].get("kind") == "poppler_bbox" else None
                    snapshot, _ = self.workspace.verified_pdf(source["revision_id"])
                    with snapshot:
                        payload = self.reader.read(snapshot, source["index"])
                    if geometry is not None:
                        self._current_geometry(source)
                    page = layout_reader.parse_layout(payload, source, geometry=geometry)
                    layout_bytes = packed(page)
                    evidence_bytes += len(payload) + len(layout_bytes)
                    if evidence_bytes > MAX_EVIDENCE:
                        raise DocumentError("reading_limit", "Selected pages exceed the reading evidence limit.", 422)
                    record = dict(source, state="read" if page["word_count"] else "needs_ocr",
                                  words=page["word_count"], artifact=self.artifact(payload, ".xhtml"),
                                  layout=self.artifact(layout_bytes, ".json"))
                    schedules = schedule_reader.parse_schedules(page)
                    if page["role"] != "schedule":
                        schedules["issues"] = [v for v in schedules["issues"] if v["code"] != "schedule_header_missing"]
                    requirements = requirement_reader.parse_requirements(requirement_layout(page, schedules["rows"]))
                    run["schedule_rows"].extend(schedules["rows"])
                    run["requirements"].extend(requirements["requirements"])
                    run["issues"].extend(schedules["issues"] + requirements["issues"])
                    if not page["word_count"]:
                        run["issues"].append({"code": "needs_ocr", "message": "This page needs image reading; no embedded text was found.", "source": source})
                    run["pages"].append(record)
                except Exception as error:
                    # A failed page cannot erase successful unrelated work. The
                    # error is visible and the page never counts as read.
                    code = getattr(error, "code", "page_read_failed")
                    if code == "reading_limit":
                        raise
                    run["pages"].append(dict(source, state="failed", code=code))
                    run["issues"].append({"code": code, "message": "This page could not be interpreted; its scope remains unchecked.", "source": source})
                run["progress"] += 1
                with self.lock:
                    self._save(run)
            tags = {r["tag"] for r in run["schedule_rows"]}
            occurrences = []
            for record in run["pages"]:
                if record["state"] == "read" and record["role"] in ("plan", "detail", "riser"):
                    page = json.loads(self._artifact_bytes(record["layout"]))
                    page_tags = set(tags)
                    for line in page["lines"]:
                        page_tags.update(equipment_hints.normalize_tag(hit[0]) for hit in equipment_hints.TAG_PATTERN.finditer(line["text"]))
                    rows_on_page = [v for v in run["schedule_rows"] if all(v["source"][k] == page[k] for k in ("revision_id", "index"))]
                    for hit in tag_occurrences(page, page_tags):
                        b = hit["source"]["bbox"]
                        # A schedule printed on a plan is not a physical plan
                        # occurrence of its equipment label.
                        if any(r["source"]["bbox"][0] <= b[0] and r["source"]["bbox"][1] <= b[1] and
                               r["source"]["bbox"][2] >= b[2] and r["source"]["bbox"][3] >= b[3] for r in rows_on_page):
                            continue
                        occurrences.append(hit)
            for requirement in run["requirements"]:
                # Header-declared tags extend the generic hints without treating
                # arbitrary drawing/detail references as equipment identities.
                requirement["schedule_tag_links"] = [tag for tag in sorted(tags) if
                    re.search(r"(?<![A-Z0-9])" + re.escape(tag).replace(r"\-", r"\s*[-\u2010-\u2015]?\s*") +
                              r"(?![A-Z0-9])", requirement["text"], re.I)]
            run["equipment_register"] = build_register(run["schedule_rows"], run["requirements"], occurrences)
            run["knowledge"] = project_knowledge.compile_reading(run, self._artifact_bytes, self.knowledge_store)
            if self.stopping.is_set():
                raise DocumentError("reading_interrupted", "Reading stopped before completion.", 409)
            run.update(state="completed", finished_at=now(),
                       readable_pages=sum(p["state"] == "read" for p in run["pages"]))
        except Exception as error:
            if getattr(error, "code", None) == "reading_limit":
                # Retain the last bounded checkpoint so recording the terminal
                # failure cannot itself overflow the saved-record limit.
                run = self.result(run["id"])
            run.update(state="interrupted" if self.stopping.is_set() else "failed", finished_at=now(),
                       error=getattr(error, "message", "Document reading failed; saved partial work remains draft."))
        finally:
            with self.lock:
                self._save(run)

    def verified_result(self, run_id):
        run = self.result(run_id)
        if run["state"] != "completed":
            raise DocumentError("reading_incomplete", "Wait for document reading to finish before exporting its records.", 409)
        for revision in sorted({v["revision_id"] for v in run["sources"]}):
            snapshot, _ = self.workspace.verified_pdf(revision)
            snapshot.close()
        for page in run["pages"]:
            for key in ("artifact", "layout"):
                if key in page:
                    self._artifact_bytes(page[key])
        self.knowledge_for(run)
        return run

    def knowledge_for(self, run):
        try:
            return project_knowledge.inspect_reading(run, self.knowledge_store)
        except project_knowledge.storage.KnowledgeError as error:
            raise DocumentError("knowledge_invalid", error.message, 409) from None

    def build_knowledge(self, run_id):
        """Extend a saved reading without overwriting it or running PDF extraction."""
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise DocumentError("reading_busy", "Wait for the current document reading to finish.", 409)
            original = self.verified_result(run_id)
            if original.get("knowledge"):
                return original
            implementation = {name: digest(Path(__file__).with_name(name + ".py").read_bytes())
                              for name in ("project_knowledge", "mechanical_taxonomy", "mechanical_knowledge")}
            fingerprint = digest(packed(["knowledge-extension-1", digest(packed(original)), implementation]))
            for path in self.root.glob("*.json"):
                if RUN_ID.fullmatch(path.stem):
                    previous = self.result(path.stem)
                    if previous["fingerprint"] == fingerprint and previous["state"] == "completed":
                        self.knowledge_for(previous)
                        return previous
            run = copy.deepcopy(original)
            run.update(id=uuid.uuid4().hex, derived_from_reading_id=original["id"],
                       source_read_at=original["created_at"], created_at=now(),
                       fingerprint=fingerprint, knowledge_implementation=implementation)
            try:
                run["knowledge"] = project_knowledge.compile_reading(run, self._artifact_bytes, self.knowledge_store)
            except project_knowledge.storage.KnowledgeError as error:
                raise DocumentError("knowledge_invalid", error.message, 409) from None
            run["finished_at"] = now()
            self._save(run)
            return run

    def close(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join()
