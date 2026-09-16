"""Resumable local image observations feeding the saved-output baseline.

Execution evidence belongs to these jobs, not to user-imported baseline runs.
No quantity, scope approval, model promotion, or external transport is provided.
Python 3.9+, standard library only.
"""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile
import threading
import time
import uuid
import zlib


VERSION = "mechanical-model-inference-1"
MAX_RECORD = 16 * 1024 * 1024
MAX_IMAGE = 20 * 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024 + 1
MAX_RECORDS = 1000
HEX = re.compile(r"[0-9a-f]{64}\Z")
INPUT_ID = re.compile(r"input_[0-9a-f]{64}\Z")
JOB_ID = re.compile(r"inference_[0-9a-f]{32}\Z")
STATES = {"queued", "running", "completed", "failed", "cancelled", "interrupted"}
INPUT_FIELDS = {"id", "project_id", "source_id", "input_sha256", "revision_id", "index",
                "sheet_id", "image", "renderer", "role", "actor", "reason"}
JOB_FIELDS = {"id", "project_id", "plan_id", "state", "completed_samples", "total_samples",
              "baseline_run_id", "error", "observed_identity", "samples", "bindings",
              "fingerprint", "actor", "reason", "history", "attempts", "failed_response"}
IDENTITY_FIELDS = {"kind", "model", "model_sha256", "runtime", "runtime_version",
                   "runtime_sha256", "prompt_sha256", "schema_sha256",
                   "image_max_dimension", "temperature", "seed"}


class InferenceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _load_baseline():
    path = Path(__file__).with_name("mechanical_model_baseline.py")
    spec = importlib.util.spec_from_file_location("inference_baseline_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = _load_baseline()


def packed(value):
    try:
        return baseline.packed(value)
    except ValueError as error:
        raise InferenceError(getattr(error, "code", "invalid_payload"), str(error)) from None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(packed(value))


IMPLEMENTATION_SHA256 = sha(Path(__file__).read_bytes())


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fields(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise InferenceError("integrity_error", "Inference record has missing or unsupported fields.")


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 10000:
        raise InferenceError("invalid_payload", "Provide nonempty bounded actor, reason, and identifiers.")
    return value


def _hash(value):
    if not isinstance(value, str) or HEX.fullmatch(value) is None:
        raise InferenceError("integrity_error", "Inference SHA-256 is invalid.")


def _error(error):
    return {"code": str(getattr(error, "code", "inference_failed"))[:100],
            "message": str(getattr(error, "message", "Local inference failed; saved checkpoints remain available."))[:2000]}


def _image(raw):
    if (not isinstance(raw, bytes) or not 45 <= len(raw) <= MAX_IMAGE or
            raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[8:16] != b"\0\0\0\rIHDR"):
        raise InferenceError("image_invalid", "A bounded rendered PNG with an IHDR header is required.")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", raw[16:29])
    depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
    if (not 0 < width <= 2000 or not 0 < height <= 2000 or depth not in depths.get(color, set()) or
            compression != 0 or filtering != 0 or interlace not in (0, 1)):
        raise InferenceError("image_invalid", "PNG dimensions or encoding are unsupported.")
    position, data_seen, ended = 8, False, False
    while position < len(raw):
        if position + 12 > len(raw):
            raise InferenceError("image_invalid", "PNG chunk is truncated.")
        size = struct.unpack(">I", raw[position:position + 4])[0]
        end = position + 12 + size
        if end > len(raw):
            raise InferenceError("image_invalid", "PNG chunk is truncated.")
        kind = raw[position + 4:position + 8]
        crc = struct.unpack(">I", raw[end - 4:end])[0]
        if zlib.crc32(raw[position + 4:end - 4]) & 0xffffffff != crc:
            raise InferenceError("image_invalid", "PNG chunk checksum is invalid.")
        if kind == b"IHDR" and position != 8:
            raise InferenceError("image_invalid", "PNG has repeated headers.")
        data_seen = data_seen or kind == b"IDAT"
        if kind == b"IEND":
            ended = size == 0 and end == len(raw)
            break
        position = end
    if not data_seen or not ended:
        raise InferenceError("image_invalid", "PNG data or final chunk is missing.")
    return {"sha256": sha(raw), "bytes": len(raw), "width": width, "height": height}


def _file_identity(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise InferenceError("renderer_unavailable", "Configured renderer artifact is not a file.")
    hasher, count = hashlib.sha256(), 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            count += len(block)
            if count > 512 * 1024 * 1024:
                raise InferenceError("size_limit", "Renderer artifact exceeds the identity limit.")
            hasher.update(block)
    return {"path": str(path), "sha256": hasher.hexdigest(), "bytes": count}


class InferenceEngine:
    def __init__(self, workspace, baseline_store, adapter=None):
        self.workspace, self.baseline, self.adapter = workspace, baseline_store, adapter
        self.knowledge = workspace.documents.knowledge_store
        self.project_id = workspace.metadata["project"]["id"]
        self.root = Path(workspace.root) / "model-inference"
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.thread = None
        self.running_job_id = None
        self.closed = False
        for directory in (self.root, self.root / "inputs", self.root / "jobs", self.root / "responses"):
            if directory.is_symlink():
                raise InferenceError("integrity_error", "Inference storage cannot be a symbolic link.")
            directory.mkdir(exist_ok=True)
        # No auto execution after process loss. Completed evidence stays intact.
        for job in self._records("jobs"):
            if job["state"] in ("queued", "running"):
                self._transition(job, "interrupted", "system", "Previous execution has no live worker.",
                                 {"code": "interrupted", "message": "Explicit resume is required after reopening."})
                self._save("jobs", job)

    def _path(self, kind, identifier):
        pattern = INPUT_ID if kind == "inputs" else JOB_ID
        if not isinstance(identifier, str) or pattern.fullmatch(identifier) is None:
            raise InferenceError("not_found", "The inference record identifier is invalid.")
        return self.root / kind / (identifier + ".json")

    @staticmethod
    def _read_bytes(path, limit):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise InferenceError("integrity_error", "Inference evidence is missing, linked, or oversized.")
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise InferenceError("size_limit", "Inference evidence exceeds the byte limit.")
        return raw

    def _read(self, kind, identifier):
        try:
            raw = self._read_bytes(self._path(kind, identifier), MAX_RECORD)
            envelope = json.loads(raw)
            _fields(envelope, {"schema", "data", "sha256"})
            data = envelope["data"]
            if envelope["schema"] != 1 or digest(data) != envelope["sha256"] or packed(envelope) != raw:
                raise InferenceError("integrity_error", "Inference record envelope failed verification.")
            _fields(data, INPUT_FIELDS if kind == "inputs" else JOB_FIELDS)
            if data["id"] != identifier or data["project_id"] != self.project_id:
                raise InferenceError("integrity_error", "Inference record belongs to another identity or project.")
            if kind == "inputs":
                self._validate_input(data)
            else:
                self._validate_job(data)
            return data
        except InferenceError:
            raise
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            raise InferenceError("integrity_error", "Saved inference evidence could not be verified.") from None

    def _records(self, kind):
        paths = sorted((self.root / kind).iterdir())
        if len(paths) > MAX_RECORDS:
            raise InferenceError("size_limit", "Too many saved inference records.")
        records = []
        for path in paths:
            if path.name.startswith(".pending-"):
                continue
            if path.suffix != ".json":
                raise InferenceError("integrity_error", "Unknown file in inference records.")
            records.append(self._read(kind, path.stem))
        return records

    @staticmethod
    def _atomic(path, raw):
        if path.is_symlink():
            raise InferenceError("integrity_error", "Inference evidence cannot replace a symbolic link.")
        handle, temporary = tempfile.mkstemp(prefix=".pending-", dir=str(path.parent))
        try:
            with os.fdopen(handle, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _save(self, kind, data):
        raw = packed({"schema": 1, "data": data, "sha256": digest(data)})
        self._atomic(self._path(kind, data["id"]), raw)

    def _validate_input(self, record):
        expected = "input_" + digest({key: value for key, value in record.items() if key != "id"})
        if record["id"] != expected or type(record["index"]) is not int or record["index"] < 0:
            raise InferenceError("integrity_error", "Prepared image identity is invalid.")
        for key in ("actor", "reason", "sheet_id", "source_id"):
            _text(record[key])
        _hash(record["revision_id"])
        _hash(record["input_sha256"])
        if record["role"] not in ("plan", "schedule", "specification", "detail", "riser", "legend", "addendum", "excluded"):
            raise InferenceError("integrity_error", "Prepared image role is unsupported.")
        _fields(record["image"], {"sha256", "bytes", "width", "height"})
        if record["image"] != _image(self.knowledge.source_bytes(record["source_id"])):
            raise InferenceError("integrity_error", "Prepared PNG bytes differ from their frozen image identity.")
        source = self.knowledge.get_source(record["source_id"])
        metadata = source["metadata"]
        if (source["snapshot_sha256"] != record["input_sha256"] or
                record["input_sha256"] != record["image"]["sha256"] or
                metadata["snapshot_kind"] != "rendered_page_png" or
                metadata["project_id"] != self.project_id or metadata["revision_id"] != record["revision_id"] or
                metadata["source_artifact_sha256"] != record["input_sha256"] or
                metadata["locator"] != "revision:%s/page:%s" % (record["revision_id"], record["index"] + 1)):
            raise InferenceError("integrity_error", "Prepared image no longer matches its original source record.")
        renderer = record["renderer"]
        _fields(renderer, {"argv", "artifacts"})
        if (not isinstance(renderer["argv"], list) or not 1 <= len(renderer["argv"]) <= 100 or
                not isinstance(renderer["artifacts"], list) or not 1 <= len(renderer["artifacts"]) <= 100):
            raise InferenceError("integrity_error", "Renderer identity is missing or unbounded.")
        for arg in renderer["argv"]:
            _text(arg)
        for artifact in renderer["artifacts"]:
            _fields(artifact, {"path", "sha256", "bytes"})
            _text(artifact["path"])
            _hash(artifact["sha256"])
            if type(artifact["bytes"]) is not int or artifact["bytes"] < 0:
                raise InferenceError("integrity_error", "Renderer artifact byte count is invalid.")
        if renderer["artifacts"] != sorted(renderer["artifacts"], key=lambda item: item["path"]):
            raise InferenceError("integrity_error", "Renderer artifacts are not in canonical order.")

    def _renderer_identity(self):
        argv = getattr(self.workspace, "renderer", None)
        if not isinstance(argv, list) or not 1 <= len(argv) <= 100:
            raise InferenceError("renderer_unavailable", "Configure a local renderer before preparing model inputs.")
        argv = [_text(str(argument)) for argument in argv]
        executable = shutil.which(argv[0])
        if executable is None:
            raise InferenceError("renderer_unavailable", "The configured renderer executable is unavailable.")
        try:
            paths = {Path(executable).resolve(strict=True)}
            paths.update(Path(arg).resolve(strict=True) for arg in argv[1:] if Path(arg).is_file())
            return {"argv": argv, "artifacts": [_file_identity(path) for path in sorted(paths, key=str)]}
        except OSError:
            raise InferenceError("renderer_unavailable", "Configured renderer artifacts could not be identified.") from None

    def _page(self, revision_id, index):
        for document in self.workspace.state()["documents"]:
            if document["revision_id"] == revision_id:
                for sheet in document["sheets"]:
                    if sheet["index"] == index:
                        return sheet
        raise InferenceError("source_missing", "The original page is not available in this project.")

    def _lineage(self, record):
        sheet = self._page(record["revision_id"], record["index"])
        if sheet["sheet_id"] != record["sheet_id"]:
            raise InferenceError("source_changed", "Original page identity changed.")
        snapshot, unused_length = self.workspace.verified_pdf(record["revision_id"])
        snapshot.close()

    def prepare_input(self, source, actor, reason):
        _fields(source, {"revision_id", "index"})
        _text(actor)
        _text(reason)
        _hash(source["revision_id"])
        if type(source["index"]) is not int or source["index"] < 0:
            raise InferenceError("invalid_payload", "Choose a valid original page index.")
        with self.lock:
            self._available()
            self.stopping.clear()
            sheet = self._page(source["revision_id"], source["index"])
            renderer = self._renderer_identity()
            pages = getattr(getattr(self.workspace, "workflow", None), "data", {}).get("pages", {})
            role = pages.get(source["revision_id"] + ":" + str(source["index"]), {}).get("role", "plan")
            for record in self._records("inputs"):
                if (all(record[key] == source[key] for key in source) and record["renderer"] == renderer and
                        record["role"] == role and record["sheet_id"] == sheet["sheet_id"]):
                    self._lineage(record)
                    return copy.deepcopy(record)
            if len(self._records("inputs")) >= MAX_RECORDS:
                raise InferenceError("size_limit", "Prepared input collection is full.")
            raw = self.workspace.rendered_page(source["revision_id"], source["index"])
            image = _image(raw)
            self._check_stop()
            if renderer != self._renderer_identity():
                raise InferenceError("renderer_changed", "Renderer bytes changed during page preparation.")
            metadata = {"source_class": "project_document", "title": "Model input, page %d" % (source["index"] + 1),
                        "rights_basis": "Owner supplied for local project takeoff; no external upload or redistribution authorized.",
                        "jurisdiction": "unspecified", "edition": source["revision_id"],
                        "retrieved_at": _now(), "applicability": "Local image observations only; no quantity or approval authority.",
                        "locator": "revision:%s/page:%s" % (source["revision_id"], source["index"] + 1),
                        "snapshot_kind": "rendered_page_png", "project_id": self.project_id,
                        "revision_id": source["revision_id"], "source_artifact_sha256": image["sha256"]}
            registered = self.knowledge.add_source(metadata, raw)
            record = dict(source, project_id=self.project_id, sheet_id=sheet["sheet_id"], role=role,
                          source_id=registered["id"], input_sha256=image["sha256"], image=image,
                          renderer=renderer, actor=actor, reason=reason)
            record["id"] = "input_" + digest(record)
            self._validate_input(record)
            self._lineage(record)
            self._save("inputs", record)
            return copy.deepcopy(record)

    @staticmethod
    def _validate_identity(identity):
        _fields(identity, IDENTITY_FIELDS)
        for key in ("model_sha256", "runtime_sha256", "prompt_sha256", "schema_sha256"):
            _hash(identity[key])
        for key in ("model", "runtime", "runtime_version"):
            _text(identity[key])
        if (identity["kind"] != "local_mechanical_vision" or type(identity["image_max_dimension"]) is not int or
                identity["image_max_dimension"] != 2000 or type(identity["temperature"]) is not int or
                type(identity["seed"]) is not int or identity["temperature"] != 0 or identity["seed"] != 0):
            raise InferenceError("model_mismatch", "Adapter identity does not match the local mechanical image contract.")

    def identity(self):
        with self.lock:
            self._available()
            self.stopping.clear()
            if self.adapter is not None:
                self.adapter.reset_cancel()
            return self._observe()

    def _observe(self):
        if self.closed or self.adapter is None:
            raise InferenceError("model_unavailable", "Configure the local mechanical vision adapter first.")
        try:
            observed = self.adapter.identity()
            self._validate_identity(observed)
            self._check_stop()
            return copy.deepcopy(observed)
        except InferenceError:
            raise
        except Exception as error:
            raise InferenceError(**_error(error)) from None

    def _available(self):
        if self.closed:
            raise InferenceError("closed", "The inference engine is closed.")
        if self.thread is not None and self.thread.is_alive():
            raise InferenceError("inference_busy", "One local inference job is already running.")

    def _check_stop(self):
        if self.stopping.is_set() or self.closed:
            raise InferenceError("cancelled", "Local inference was cancelled; completed checkpoints are retained.")

    def _bindings(self, plan_id, observed):
        records = self.baseline.view(self.project_id)
        indexed = {row["id"]: row for kind in ("plans", "models", "datasets") for row in records[kind]}
        plan = indexed.get(plan_id)
        if plan is None or plan["kind"] != "plan":
            raise InferenceError("plan_not_found", "Choose a saved plan in this project.")
        if "task" in plan["payload"]:
            raise InferenceError("task_mismatch", "Duct evaluation plans use retained duct producers, not box inference.")
        model = indexed[plan["payload"]["model_id"]]
        dataset = indexed[plan["payload"]["dataset_id"]]
        if any(record["issues"] or record["project_id"] != self.project_id for record in (plan, model, dataset)):
            raise InferenceError("stale_dependency", "Plan, model, or dataset has changed source or scorer evidence.")
        payload = model["payload"]
        if (payload["runtime"] != {"name": observed["runtime"], "version": observed["runtime_version"],
                                  "sha256": observed["runtime_sha256"]} or
                packed(payload["preprocessing"].get("execution_identity")) != packed(observed) or
                not any(artifact["sha256"] == observed["model_sha256"] for artifact in payload["artifacts"])):
            raise InferenceError("model_mismatch", "The frozen model manifest differs from the observed local execution identity.")
        if sha(Path(__file__).read_bytes()) != IMPLEMENTATION_SHA256:
            raise InferenceError("engine_changed", "Inference implementation changed; reload before creating a new job.")
        prepared = {record["source_id"]: record for record in self._records("inputs")}
        selected = [sample for sample in dataset["payload"]["samples"] if sample["split"] == plan["payload"]["split"]]
        inputs = []
        for sample in selected:
            record = prepared.get(sample["source_id"])
            if record is None or record["input_sha256"] != sample["input_sha256"]:
                raise InferenceError("input_not_prepared", "Every selected dataset sample must reference a prepared original PNG source.")
            self._lineage(record)
            inputs.append({"sample_id": sample["id"], "input_id": record["id"],
                           "input_source_id": record["source_id"], "image_sha256": record["input_sha256"]})
        return {"plan_id": plan_id, "model_id": model["id"], "dataset_id": dataset["id"],
                "model_manifest_sha256": model["model_manifest_sha256"], "source_pins": plan["source_pins"],
                "scorer_identity": plan["scorer_identity"], "observed_identity": observed,
                "engine_identity": {"version": VERSION, "sha256": IMPLEMENTATION_SHA256}, "inputs": inputs}

    def start(self, plan_id, actor, reason):
        _text(plan_id)
        _text(actor)
        _text(reason)
        with self.lock:
            self._available()
            # Reject an incompatible task before observing or contacting a model.
            plans = self.baseline.view(self.project_id)["plans"]
            selected_plan = next((p for p in plans if p["id"] == plan_id), None)
            if selected_plan is not None and "task" in selected_plan["payload"]:
                raise InferenceError("task_mismatch", "Duct evaluation plans use retained duct producers, not box inference.")
            self.stopping.clear()
            if self.adapter is not None:
                self.adapter.reset_cancel()
            observed = self._observe()
            bindings = self._bindings(plan_id, observed)
            fingerprint = digest(bindings)
            jobs = self._records("jobs")
            for job in jobs:
                if job["fingerprint"] == fingerprint:
                    if job["state"] == "completed":
                        return copy.deepcopy(job)
                    raise InferenceError("resume_required", "An identical job exists; explicitly resume its saved checkpoints.")
            if len(jobs) >= MAX_RECORDS:
                raise InferenceError("size_limit", "Inference job collection is full.")
            job = {"id": "inference_" + uuid.uuid4().hex, "project_id": self.project_id, "plan_id": plan_id,
                   "state": "queued", "completed_samples": 0, "total_samples": len(bindings["inputs"]),
                   "baseline_run_id": None, "error": None, "observed_identity": observed, "samples": [],
                   "bindings": bindings, "fingerprint": fingerprint, "actor": actor, "reason": reason,
                   "history": [], "attempts": [], "failed_response": None}
            self._transition(job, "queued", actor, reason)
            self._save("jobs", job)
            self._launch(job)
            return copy.deepcopy(job)

    def _transition(self, job, state, actor, reason, error=None):
        if len(job["history"]) >= 1000:
            raise InferenceError("size_limit", "Inference job history is full.")
        job.update(state=state, error=error)
        job["history"].append({"sequence": len(job["history"]) + 1, "state": state,
                               "actor": actor, "reason": reason, "at": _now()})

    def _launch(self, job):
        self.running_job_id = job["id"]
        self.thread = threading.Thread(target=self._execute, args=(copy.deepcopy(job),),
                                       name="mechanical-inference", daemon=True)
        self.thread.start()

    def resume(self, job_id, actor, reason):
        _text(actor)
        _text(reason)
        with self.lock:
            self._available()
            job = self._read("jobs", job_id)
            if job["state"] == "completed":
                return copy.deepcopy(job)
            self.stopping.clear()
            if self.adapter is not None:
                self.adapter.reset_cancel()
            observed = self._observe()
            if self._bindings(job["plan_id"], observed) != job["bindings"]:
                raise InferenceError("binding_changed", "Original job bindings changed; saved pages cannot be resumed.")
            self._check_stop()
            self._transition(job, "queued", actor, reason)
            self._save("jobs", job)
            self._launch(job)
            return copy.deepcopy(job)

    def _response(self, raw):
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RESPONSE:
            raise InferenceError("size_limit", "Raw model response is empty or exceeds its saved-byte limit.")
        ref = {"key": "responses/" + sha(raw) + ".response", "sha256": sha(raw), "bytes": len(raw)}
        path = self.root / ref["key"]
        if path.exists():
            if self.artifact_bytes(ref) != raw:
                raise InferenceError("integrity_error", "Saved response artifact changed.")
        else:
            if sum(1 for unused in path.parent.iterdir()) >= MAX_RECORDS * 400:
                raise InferenceError("size_limit", "Response artifact collection is full.")
            self._atomic(path, raw)
        return ref

    def artifact_bytes(self, ref):
        _fields(ref, {"key", "sha256", "bytes"})
        _hash(ref["sha256"])
        if (ref["key"] != "responses/" + ref["sha256"] + ".response" or
                type(ref["bytes"]) is not int or not 0 < ref["bytes"] <= MAX_RESPONSE):
            raise InferenceError("integrity_error", "Response artifact reference is invalid.")
        try:
            raw = self._read_bytes(self.root / ref["key"], MAX_RESPONSE)
        except OSError:
            raise InferenceError("integrity_error", "Response artifact is unavailable.") from None
        if sha(raw) != ref["sha256"] or len(raw) != ref["bytes"]:
            raise InferenceError("integrity_error", "Response artifact bytes failed verification.")
        return raw

    def _validate_job(self, job):
        bindings = job["bindings"]
        _fields(bindings, {"plan_id", "model_id", "dataset_id", "model_manifest_sha256", "source_pins",
                           "scorer_identity", "observed_identity", "engine_identity", "inputs"})
        if (digest(bindings) != job["fingerprint"] or bindings["plan_id"] != job["plan_id"] or
                bindings["observed_identity"] != job["observed_identity"] or job["state"] not in STATES):
            raise InferenceError("integrity_error", "Job bindings or state failed verification.")
        self._validate_identity(job["observed_identity"])
        baseline.BaselineStore._validate_pins(bindings["source_pins"])
        for key in ("engine_identity", "scorer_identity"):
            _fields(bindings[key], {"version", "sha256"})
            _text(bindings[key]["version"])
            _hash(bindings[key]["sha256"])
        _hash(bindings["model_manifest_sha256"])
        for key in ("actor", "reason", "plan_id"):
            _text(job[key])
        pins = bindings["inputs"]
        if not isinstance(pins, list) or not 1 <= len(pins) <= 200:
            raise InferenceError("integrity_error", "Job selected inputs are empty or oversized.")
        for pin in pins:
            _fields(pin, {"sample_id", "input_id", "input_source_id", "image_sha256"})
            record = self._read("inputs", pin["input_id"])
            if record["source_id"] != pin["input_source_id"] or record["input_sha256"] != pin["image_sha256"]:
                raise InferenceError("integrity_error", "Job input no longer matches its prepared image.")
        identifiers = [pin["sample_id"] for pin in pins]
        if identifiers != sorted(set(identifiers)):
            raise InferenceError("integrity_error", "Job selected inputs must be unique and ordered.")
        samples = job["samples"]
        if (not isinstance(samples, list) or len(samples) > len(pins) or
                type(job["total_samples"]) is not int or job["total_samples"] != len(pins) or
                type(job["completed_samples"]) is not int or job["completed_samples"] != len(samples)):
            raise InferenceError("integrity_error", "Saved job progress does not match page checkpoints.")
        for sample, pin in zip(samples, pins):
            _fields(sample, {"sample_id", "input_source_id", "image_sha256", "objects", "unreadable",
                             "latency_ms", "peak_memory_mb", "response"})
            if any(sample[key] != pin[key] for key in ("sample_id", "input_source_id", "image_sha256")):
                raise InferenceError("integrity_error", "Page checkpoint order or source changed.")
            self._result({"objects": sample["objects"], "unreadable": sample["unreadable"]})
            baseline._number(sample["latency_ms"], "latency_ms")
            if sample["peak_memory_mb"] is not None:
                raise InferenceError("integrity_error", "Inference memory must remain explicitly unmeasured.")
            self.artifact_bytes(sample["response"])
        if (job["state"] == "completed") != (isinstance(job["baseline_run_id"], str)):
            raise InferenceError("integrity_error", "Completed job is missing its server-generated baseline run.")
        if job["state"] == "completed" and len(samples) != len(pins):
            raise InferenceError("integrity_error", "Completed job has missing page checkpoints.")
        history = job["history"]
        if not isinstance(history, list) or not 1 <= len(history) <= 1000:
            raise InferenceError("integrity_error", "Job history is missing or oversized.")
        for sequence, event in enumerate(history, 1):
            _fields(event, {"sequence", "state", "actor", "reason", "at"})
            if type(event["sequence"]) is not int or event["sequence"] != sequence or event["state"] not in STATES:
                raise InferenceError("integrity_error", "Job history is not in its original order.")
            for key in ("actor", "reason", "at"):
                _text(event[key])
        if history[-1]["state"] != job["state"]:
            raise InferenceError("integrity_error", "Job state differs from its final history event.")
        if job["error"] is not None:
            _fields(job["error"], {"code", "message"})
            _text(job["error"]["code"])
            _text(job["error"]["message"])
        if not isinstance(job["attempts"], list) or len(job["attempts"]) > 200:
            raise InferenceError("integrity_error", "Failed response attempts exceed the saved limit.")
        for attempt in job["attempts"]:
            _fields(attempt, {"sample_id", "response", "error", "at"})
            if attempt["sample_id"] not in identifiers:
                raise InferenceError("integrity_error", "Failed response belongs to an unknown sample.")
            _fields(attempt["error"], {"code", "message"})
            self.artifact_bytes(attempt["response"])
        if job["failed_response"] != (job["attempts"][-1]["response"] if job["attempts"] else None):
            raise InferenceError("integrity_error", "Latest failed response does not match retained attempts.")

    @staticmethod
    def _result(result):
        _fields(result, {"objects", "unreadable"})
        baseline._objects(result["objects"])
        if type(result["unreadable"]) is not bool or (result["unreadable"] and result["objects"]):
            raise InferenceError("model_output", "Unreadable pages require an empty observation list.")

    def _execute(self, job):
        current_sample, raw = None, None
        try:
            with self.lock:
                self._transition(job, "running", "system", "Explicit local inference execution started.")
                self._save("jobs", job)
            for pin in job["bindings"]["inputs"][len(job["samples"]):]:
                current_sample, raw = pin["sample_id"], None
                self._check_stop()
                record = self._read("inputs", pin["input_id"])
                self._lineage(record)
                image = self.knowledge.source_bytes(record["source_id"])
                started = time.monotonic()
                result, raw = self.adapter.read(image, record["role"], copy.deepcopy(job["observed_identity"]))
                elapsed = (time.monotonic() - started) * 1000
                self._result(result)
                response = self._response(raw)
                self._check_stop()
                sample = {key: pin[key] for key in ("sample_id", "input_source_id", "image_sha256")}
                sample.update(objects=copy.deepcopy(result["objects"]), unreadable=result["unreadable"],
                              latency_ms=elapsed, peak_memory_mb=None, response=response)
                with self.lock:
                    self._check_stop()
                    job["samples"].append(sample)
                    job["completed_samples"] = len(job["samples"])
                    self._save("jobs", job)
                current_sample, raw = None, None
            self._check_stop()
            observed = self._observe()
            if self._bindings(job["plan_id"], observed) != job["bindings"]:
                raise InferenceError("binding_changed", "Inference evidence changed before complete-run registration.")
            # Re-read persisted responses before registering the exact checkpoints.
            saved = self._read("jobs", job["id"])
            if saved["samples"] != job["samples"]:
                raise InferenceError("integrity_error", "Saved checkpoints changed during inference.")
            payload = {"plan_id": job["plan_id"], "model_manifest_sha256": job["bindings"]["model_manifest_sha256"],
                       "predictions": [{key: sample[key] for key in ("sample_id", "objects", "latency_ms", "peak_memory_mb")}
                                       for sample in job["samples"]]}
            with self.lock:
                self._check_stop()
                # The original actor/reason and job ID remain fixed on resume.
                # Baseline request deduplication covers process loss after this
                # transaction commits but before the job envelope is replaced.
                run = self.baseline.register(self.project_id, "run", payload, job["actor"],
                                             job["reason"] + " [local inference " + job["id"] + "]")
                job["baseline_run_id"] = run["id"]
                self._transition(job, "completed", "system", "All images checkpointed; generated baseline run registered.")
                self._save("jobs", job)
        except Exception as error:
            failure = _error(error)
            raw = raw if raw is not None else getattr(error, "raw_response", None)
            if current_sample is not None and isinstance(raw, bytes) and raw and len(job["attempts"]) < 200:
                try:
                    job["attempts"].append({"sample_id": current_sample, "response": self._response(raw),
                                            "error": failure, "at": _now()})
                    job["failed_response"] = job["attempts"][-1]["response"]
                except Exception:
                    failure = {"code": "evidence_write_failed", "message": "Failed response evidence could not be retained."}
            with self.lock:
                if job["baseline_run_id"] is None:
                    state = "cancelled" if self.stopping.is_set() else "failed"
                    self._transition(job, state, "system", "Execution stopped; completed image checkpoints retained.", failure)
                self._save("jobs", job)
        finally:
            with self.lock:
                self.running_job_id = None

    def cancel(self, job_id):
        # Adapter cancellation must not wait for a thread holding the record lock.
        if job_id == self.running_job_id:
            self.stopping.set()
            if self.adapter is not None:
                self.adapter.cancel()
        with self.lock:
            job = self._read("jobs", job_id)
            if job_id != self.running_job_id and job["state"] in ("queued", "running"):
                self._transition(job, "interrupted", "system", "No live worker owns this saved job.")
                self._save("jobs", job)
            return copy.deepcopy(job)

    def view(self):
        with self.lock:
            return {"inputs": self._records("inputs"), "jobs": self._records("jobs"),
                    "running_job_id": self.running_job_id if self.thread is not None and self.thread.is_alive() else None,
                    "configured": self.adapter is not None}

    def close(self):
        self.closed = True
        self.stopping.set()
        if self.adapter is not None:
            self.adapter.cancel()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            # Adapter I/O is bounded and cancellation closes its connection.
            # Renderer calls are synchronous; their existing timeout still applies.
            thread.join()
        # Explicit identity and preparation run synchronously under this lock.
        # Closing their adapter does not itself wait for the caller to unwind.
        with self.lock:
            pass
