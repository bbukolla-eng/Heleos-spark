"""Durable local air-device proposals, original evidence and immutable job history.

No count, admission, coverage, scale or calibration authority lives here. Replays
verify frozen raw responses and source bytes without executing any model.
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
import tempfile
import threading
import uuid


def _load(name):
    path = Path(__file__).with_name(name + ".py")
    raw = path.read_bytes()
    spec = importlib.util.spec_from_file_location("air_device_source_" + name, path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    module._SOURCE_SHA256 = hashlib.sha256(raw).hexdigest()
    return module


vision = _load("local_air_device_vision")
sheet_geometry = _load("sheet_geometry")
VERSION = "air-device-source-producer-1"
MAX_RECORD = 16 * 1024 * 1024
MAX_RESPONSE = vision.transport.MAX_RESPONSE_BYTES + 1
MAX_JOBS = 1000
ID = re.compile(r"air_device_source_[0-9a-f]{32}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
STATES = {"queued", "running", "completed", "failed", "cancelled", "interrupted"}
TERMINAL = STATES - {"queued", "running"}
COLLECTIONS = ("observations", "evidence", "current_sources", "proposed_correspondences",
               "proposed_multiplicities", "proposed_relocations", "schedule_declarations")
RESULT_FIELDS = {"id", "project_id", "state", "source", "geometry", "role", "input", "model_identity",
                 "producer_identity", "source_refs", "unreadable", "error", "response", "actor", "reason",
                 "input_geometry_binding"} | set(COLLECTIONS)
EVENT_FIELDS = {"id", "project_id", "state", "source", "actor", "reason", "error", "sequence", "at",
                "previous_sha256", "result_sha256"}


class ProducerError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def packed(value):
    try:
        vision.transport._bounded_json(value)
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ProducerError("integrity_error", "Air-device source records must contain finite bounded JSON.") from None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(packed(value))


IMPLEMENTATION_SHA256 = sha(Path(__file__).read_bytes())
_MODULES = (vision, vision.transport, vision.attributes, sheet_geometry)
# Capture exact loaded bytes; the typed adapter uses the same source loader.
_DEPENDENCIES = {Path(module.__file__).name: module._SOURCE_SHA256 for module in _MODULES}


def _implementation():
    if sha(Path(__file__).read_bytes()) != IMPLEMENTATION_SHA256:
        raise ProducerError("engine_changed", "Reload after an air-device producer update.")
    for module in _MODULES:
        if sha(Path(module.__file__).read_bytes()) != _DEPENDENCIES[Path(module.__file__).name]:
            raise ProducerError("engine_changed", "Reload after an air-device dependency update.")
    return {"version": VERSION, "sha256": IMPLEMENTATION_SHA256,
            "dependencies": copy.deepcopy(_DEPENDENCIES)}


def _fields(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ProducerError("integrity_error", "Air-device source record fields do not match their contract.")


def _text(value, maximum=1000):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum or
            any(ord(c) < 32 or 127 <= ord(c) < 160 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ProducerError("invalid_payload", "Use nonempty bounded plain text.")
    return value


def _hash(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ProducerError("integrity_error", "Air-device source hash is invalid.")


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ProducerError("not_found", "The air-device source record identifier is invalid.")
    return value


def _error(error):
    return {"code": str(getattr(error, "code", "producer_failed"))[:100],
            "message": str(getattr(error, "message", "The local air-device reading failed; retained evidence is available."))[:2000]}


def _source(value):
    _fields(value, ("revision_id", "index"))
    _hash(value["revision_id"])
    if type(value["index"]) is not int or not 0 <= value["index"] <= 4294967295:
        raise ProducerError("invalid_payload", "Choose an original page index.")
    return copy.deepcopy(value)


def _identity(value):
    _fields(value, ("kind", "model", "model_sha256", "runtime", "runtime_version", "runtime_sha256",
                    "prompt_sha256", "schema_sha256", "image_max_dimension", "temperature", "seed"))
    for key in ("model_sha256", "runtime_sha256", "prompt_sha256", "schema_sha256"):
        _hash(value[key])
    for key in ("model", "runtime", "runtime_version"):
        _text(value[key])
    if (value["kind"] != vision.AirDeviceVision.KIND or value["runtime"] != "ollama"
            or type(value["image_max_dimension"]) is not int or value["image_max_dimension"] != 2000
            or type(value["temperature"]) not in (int, float) or value["temperature"] != 0.6
            or type(value["seed"]) is not int or value["seed"] != 0
            or value["prompt_sha256"] != sha(vision.PROMPT.encode("utf-8"))
            or value["schema_sha256"] != digest(vision.SCHEMA)):
        raise ProducerError("model_mismatch", "The adapter identity does not match the typed air-device contract.")
    return copy.deepcopy(value)


class AirDeviceSourceProducer:
    """One bounded page job at a time; input_engine is the existing PNG preparer."""

    def __init__(self, workspace, input_engine, adapter=None):
        self.workspace, self.inputs, self.adapter = workspace, input_engine, adapter
        self.project_id = workspace.metadata["project"]["id"]
        self.root = Path(workspace.root) / "air-device-source"
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.thread = None
        self.running_job_id = None
        self.closed = False
        # An unavailable terminal write cannot establish durable history. Keep
        # the stopped worker's failure visible in this process without rewriting
        # its last retained event or publishing an unreferenced result.
        self.terminal_failures = {}
        for path in (self.root, self.root / "jobs", self.root / "results", self.root / "responses",
                     self.root / "input-bindings"):
            if path.is_symlink():
                raise ProducerError("integrity_error", "Air-device source storage cannot be a symbolic link.")
            path.mkdir(exist_ok=True)
        # This new instance owns no live worker. Never infer a running process
        # from durable state, and never silently resume one after reopening.
        for job in self._jobs():
            if job["state"] not in TERMINAL:
                self._event(job, "interrupted", {"code": "interrupted",
                    "message": "Previous process has no live worker; start a new reading explicitly."})

    @staticmethod
    def _read_bytes(path, limit):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
                raise OSError("missing or linked evidence")
            with path.open("rb") as stream:
                raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise OSError("oversized evidence")
            return raw
        except OSError:
            raise ProducerError("integrity_error", "Air-device evidence is missing, linked or oversized.") from None

    def _write(self, path, raw):
        if len(raw) > MAX_RECORD or path.is_symlink() or path.parent.is_symlink():
            raise ProducerError("integrity_error", "Air-device evidence cannot be safely retained.")
        if path.exists():
            if self._read_bytes(path, MAX_RECORD) != raw:
                raise ProducerError("integrity_error", "Immutable air-device evidence already exists with different bytes.")
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pending-", delete=False) as stream:
                temporary = stream.name
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # This class has one writer under its lock; never replace an existing
            # named evidence artifact. A rename publishes the complete bytes.
            if path.exists():
                raise ProducerError("integrity_error", "Air-device evidence appeared during publication.")
            os.rename(temporary, path)
            temporary = None
        except OSError:
            raise ProducerError("evidence_write_failed", "Air-device evidence could not be saved.") from None
        finally:
            if temporary is not None:
                os.unlink(temporary)

    def _save(self, path, data):
        self._write(path, packed({"schema": 1, "data": data, "sha256": digest(data)}))

    def _read(self, path):
        try:
            raw = self._read_bytes(path, MAX_RECORD)
            envelope = vision.transport._decode(raw)
            _fields(envelope, ("schema", "data", "sha256"))
            if (type(envelope["schema"]) is not int or envelope["schema"] != 1
                    or envelope["sha256"] != digest(envelope["data"]) or packed(envelope) != raw):
                raise ValueError("envelope changed")
            return envelope["data"]
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
            raise ProducerError("integrity_error", "Air-device source evidence failed envelope verification.") from None

    def _job(self, identifier):
        folder = self.root / "jobs" / _identifier(identifier)
        if folder.is_symlink() or not folder.is_dir():
            raise ProducerError("not_found", "The air-device source job is unavailable.")
        paths = sorted(path for path in folder.iterdir() if not path.name.startswith(".pending-"))
        if not 1 <= len(paths) <= 8:
            raise ProducerError("integrity_error", "Air-device job history is missing or unbounded.")
        previous = None
        for sequence, path in enumerate(paths, 1):
            if path.name != "%04d.json" % sequence:
                raise ProducerError("integrity_error", "Air-device job history sequence is invalid.")
            event = self._read(path)
            _fields(event, EVENT_FIELDS)
            if (event["id"] != identifier or event["project_id"] != self.project_id
                    or event["sequence"] != sequence or type(event["sequence"]) is not int
                    or event["state"] not in STATES or event["previous_sha256"] != (digest(previous) if previous else None)):
                raise ProducerError("integrity_error", "Air-device job history identity changed.")
            _source(event["source"])
            _text(event["actor"])
            _text(event["reason"])
            _text(event["at"])
            if previous is None and event["state"] != "queued":
                raise ProducerError("integrity_error", "Air-device job must begin queued.")
            if previous is not None:
                if previous["state"] in TERMINAL or any(event[k] != previous[k] for k in ("source", "actor", "reason")):
                    raise ProducerError("integrity_error", "Terminal air-device jobs cannot be rewritten.")
            if event["result_sha256"] is not None:
                _hash(event["result_sha256"])
                if event["state"] not in TERMINAL:
                    raise ProducerError("integrity_error", "Only terminal jobs can reference a result.")
            previous = event
        return previous

    def _jobs(self):
        paths = sorted((self.root / "jobs").iterdir())
        if len(paths) > MAX_JOBS:
            raise ProducerError("size_limit", "The air-device source job collection is full.")
        return [self._job(path.name) for path in paths]

    def _event(self, previous, state, error=None, result_sha256=None):
        event = {key: copy.deepcopy(previous[key]) for key in ("id", "project_id", "source", "actor", "reason")}
        event.update(state=state, error=error, sequence=previous.get("sequence", 0) + 1,
                     previous_sha256=digest(previous) if "sequence" in previous else None,
                     result_sha256=result_sha256, at=datetime.now(timezone.utc).isoformat())
        folder = self.root / "jobs" / event["id"]
        if folder.is_symlink():
            raise ProducerError("integrity_error", "Air-device job storage cannot be linked.")
        folder.mkdir(exist_ok=True)
        self._save(folder / ("%04d.json" % event["sequence"]), event)
        return event

    def _page_geometry(self, source):
        try:
            sheet = self.inputs._page(source["revision_id"], source["index"])
            return sheet_geometry.sheet_geometry(sheet, source["revision_id"])
        except Exception as error:
            raise ProducerError(**_error(error)) from None

    def start(self, source, actor, reason):
        source = _source(source)
        _text(actor)
        _text(reason)
        with self.lock:
            if self.closed:
                raise ProducerError("closed", "The air-device source producer is closed.")
            if self.thread is not None and self.thread.is_alive():
                raise ProducerError("producer_busy", "A local air-device reading is already running.")
            if self.adapter is None:
                raise ProducerError("model_unavailable", "Configure a pinned local vision model before finding air devices.")
            self._page_geometry(source)
            if len(self._jobs()) >= MAX_JOBS:
                raise ProducerError("size_limit", "The air-device source job collection is full.")
            _implementation()
            self.stopping.clear()
            self.adapter.reset_cancel()
            seed = {"id": "air_device_source_" + uuid.uuid4().hex, "project_id": self.project_id,
                    "source": source, "actor": actor, "reason": reason}
            job = self._event(seed, "queued")
            self.running_job_id = job["id"]
            self.thread = threading.Thread(target=self._execute, args=(copy.deepcopy(job),),
                                           name="air-device-source", daemon=True)
            self.thread.start()
            return copy.deepcopy(job)

    def _check_stop(self):
        if self.stopping.is_set() or self.closed:
            raise ProducerError("cancelled", "The local air-device reading was cancelled.")

    def _retain_response(self, raw):
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_RESPONSE:
            raise ProducerError("size_limit", "The original response exceeds its retained byte limit.")
        ref = {"key": "responses/" + sha(raw) + ".response", "sha256": sha(raw), "bytes": len(raw)}
        # Failed bounded transport responses may retain one excess byte to prove
        # the size failure without keeping an unbounded stream.
        path = self.root / ref["key"]
        if len(raw) > MAX_RECORD:
            if path.exists():
                self.artifact_bytes(ref)
            else:
                with path.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
        else:
            self._write(path, raw)
        return ref

    def artifact_bytes(self, ref):
        _fields(ref, ("key", "sha256", "bytes"))
        _hash(ref["sha256"])
        if (ref["key"] != "responses/" + ref["sha256"] + ".response"
                or type(ref["bytes"]) is not int or not 0 < ref["bytes"] <= MAX_RESPONSE):
            raise ProducerError("integrity_error", "Air-device response reference is invalid.")
        raw = self._read_bytes(self.root / ref["key"], MAX_RESPONSE)
        if sha(raw) != ref["sha256"] or len(raw) != ref["bytes"]:
            raise ProducerError("integrity_error", "Original air-device response bytes changed.")
        return raw

    @staticmethod
    def _bind(proposal, job_id, response_sha256, source, image_sha256, role):
        proposal = vision.validate_proposal(proposal)
        seed = {"reading_id": job_id, "response_sha256": response_sha256,
                "source": source, "image_sha256": image_sha256}

        def identifier(kind, index, local_id):
            return "air_device_" + kind + "_" + digest(dict(seed, kind=kind,
                response_index=index, local_id=local_id))

        evidence_map = {row["id"]: identifier("evidence", index, row["id"])
                        for index, row in enumerate(proposal["evidence"])}
        evidence = [{"id": evidence_map[row["id"]], "source": copy.deepcopy(source),
            "bbox": copy.deepcopy(row["bbox"]), "kind": "graphic" if row["text"] is None else "text",
            "text": row["text"], "artifact_sha256": image_sha256} for row in proposal["evidence"]]

        def rewrite_attrs(values):
            result = copy.deepcopy(values)
            for attribute in result.values():
                attribute["evidence_ids"] = [evidence_map[eid] for eid in attribute["evidence_ids"]]
            return result

        observations, observation_map = [], {}
        for index, row in enumerate(proposal["observations"]):
            observation = copy.deepcopy(row)
            observation.update(schema="air-device-observation-1", source=copy.deepcopy(source),
                id=identifier("observation", index, row["id"]), attributes=rewrite_attrs(row["attributes"]),
                evidence_ids=[evidence_map[eid] for eid in row["evidence_ids"]])
            vision.attributes.normalize_attributes(observation["attributes"], observation["evidence_ids"])
            observations.append(observation)
            observation_map[row["id"]] = observation

        bound = {"observations": observations, "evidence": evidence,
            "current_sources": [{"source": copy.deepcopy(source), "artifact_sha256": image_sha256, "role": role}]}
        for kind in ("correspondences", "multiplicities", "relocations"):
            records = []
            for index, row in enumerate(proposal[kind]):
                record = copy.deepcopy(row)
                record.update(id=identifier(kind, index, row["id"]),
                    members=[{"observation_id": observation_map[local]["id"],
                              "observation_sha256": digest(observation_map[local])} for local in row["members"]],
                    evidence_ids=[evidence_map[eid] for eid in row["evidence_ids"]])
                if kind == "multiplicities":
                    record["representative"] = observation_map[row["representative"]]["id"]
                if kind == "relocations":
                    for field in ("remove_members", "reinstall_members"):
                        record[field] = [observation_map[local]["id"] for local in row[field]]
                    for field in ("remove_operation_id", "reinstall_operation_id"):
                        record[field] = identifier(field, index, row[field])
                records.append(record)
            bound["proposed_" + kind] = records
        bound["schedule_declarations"] = [dict(copy.deepcopy(row),
            id=identifier("schedule", index, row["id"]), source=copy.deepcopy(source),
            attributes=rewrite_attrs(row["attributes"]),
            evidence_ids=[evidence_map[eid] for eid in row["evidence_ids"]])
            for index, row in enumerate(proposal["schedule_declarations"])]
        return bound

    def _producer_identity(self):
        _identity(self.adapter.identity())
        return _implementation()

    def _prepared(self, prepared, source, geometry=None):
        if self.inputs._read("inputs", prepared["id"]) != prepared:
            raise ProducerError("integrity_error", "The frozen prepared input changed.")
        self.inputs._validate_input(prepared)
        if (prepared["project_id"] != self.project_id or
                any(prepared[key] != source[key] for key in ("revision_id", "index")) or
                (geometry is not None and prepared["sheet_id"] != geometry["sheet_id"])):
            raise ProducerError("integrity_error", "Prepared input and original page identities differ.")
        self.inputs._lineage(prepared)
        image = self.inputs.knowledge.source_bytes(prepared["source_id"])
        if sha(image) != prepared["input_sha256"]:
            raise ProducerError("integrity_error", "Prepared input image bytes changed.")
        return image

    def _input_geometry_binding(self, prepared, geometry, establish=False):
        """Pin geometry at an actual fresh rendering; never relabel cached PNGs.

        Shared prepared inputs predate this contract and have no geometry field.
        Their first air-device use proves byte equality with a fresh rendering.
        Replays consume only this immutable receipt, never another rendering.
        """
        if not re.fullmatch(r"input_[0-9a-f]{64}", prepared["id"]):
            raise ProducerError("integrity_error", "Prepared input identifier is invalid.")
        source = {key: geometry[key] for key in ("revision_id", "index", "sheet_id")}
        source["geometry_fingerprint"] = geometry["fingerprint"]
        expected = {"input_id": prepared["id"], "input_record_sha256": digest(prepared),
            "source": source, "artifact_sha256": prepared["input_sha256"],
            "geometry": copy.deepcopy(geometry)}
        path = self.root / "input-bindings" / (prepared["id"] + ".json")
        if path.exists() or path.is_symlink():
            if self._read(path) != expected:
                raise ProducerError("input_geometry_changed", "The retained image belongs to different page coordinates; preserve it and prepare a current source.")
            return expected
        if not establish:
            raise ProducerError("integrity_error", "The prepared image has no retained geometry proof.")
        selection = {key: source[key] for key in ("revision_id", "index")}
        if (geometry != self._page_geometry(selection) or
                prepared["renderer"] != self.inputs._renderer_identity()):
            raise ProducerError("source_changed", "Source or renderer changed before image geometry verification.")
        current = self.workspace.rendered_page(source["revision_id"], source["index"])
        if sha(current) != prepared["input_sha256"]:
            raise ProducerError("input_image_changed", "The cached image differs from a fresh original-page rendering.")
        if (geometry != self._page_geometry(selection) or
                prepared["renderer"] != self.inputs._renderer_identity()):
            raise ProducerError("source_changed", "Source or renderer changed during image geometry verification.")
        self._check_stop()
        self._save(path, expected)
        return expected

    def _execute(self, job):
        raw = None
        result = {"id": job["id"], "project_id": self.project_id, "state": "failed",
            "source": None, "geometry": None, "role": None, "input": None, "model_identity": None,
            "producer_identity": None, "source_refs": [], "unreadable": None, "error": None,
            "response": None, "actor": job["actor"], "reason": job["reason"], "input_geometry_binding": None}
        result.update({key: [] for key in COLLECTIONS})
        try:
            with self.lock:
                self._check_stop()
                job = self._event(job, "running")
            result["producer_identity"] = self._producer_identity()
            geometry = self._page_geometry(job["source"])
            prepared = self.inputs.prepare_input(job["source"], job["actor"], job["reason"])
            result["input"], result["role"] = prepared, prepared["role"]
            if geometry != self._page_geometry(job["source"]):
                raise ProducerError("source_changed", "Page coordinates changed during image preparation.")
            result["geometry"] = geometry
            source = {key: geometry[key] for key in ("revision_id", "index", "sheet_id")}
            source["geometry_fingerprint"] = geometry["fingerprint"]
            result["source"], result["source_refs"] = source, [source]
            image = self._prepared(prepared, job["source"], geometry)
            result["input_geometry_binding"] = self._input_geometry_binding(prepared, geometry, establish=True)
            self._check_stop()
            identity = _identity(self.adapter.identity())
            result["model_identity"] = identity
            proposal, raw = self.adapter.read(image, prepared["role"], identity)
            result["response"] = self._retain_response(raw)
            self._check_stop()
            parsed = vision.parse_response(raw, identity["model"], image)
            if packed(parsed) != packed(proposal):
                raise ProducerError("integrity_error", "Typed proposals disagree with their original response.")
            if _identity(self.adapter.identity()) != identity:
                raise ProducerError("model_changed", "The local model identity changed during reading.")
            if geometry != self._page_geometry(job["source"]):
                raise ProducerError("source_changed", "Page coordinates changed during reading.")
            self._prepared(prepared, job["source"], geometry)
            if result["producer_identity"] != _implementation():
                raise ProducerError("engine_changed", "Producer dependencies changed during reading.")
            result.update(self._bind(parsed, job["id"], result["response"]["sha256"], source,
                                     prepared["input_sha256"], prepared["role"]))
            result.update(unreadable=parsed["unreadable"], state="completed")
            self._check_stop()
        except Exception as error:
            result.update(state="cancelled" if self.stopping.is_set() or self.closed else "failed",
                          error=_error(error), unreadable=None)
            result.update({key: [] for key in COLLECTIONS})
            raw = raw if raw is not None else getattr(error, "raw_response", None)
            if isinstance(raw, bytes) and raw and result["response"] is None:
                try:
                    result["response"] = self._retain_response(raw)
                except Exception:
                    result["error"] = {"code": "evidence_write_failed", "message": "Failed raw response could not be retained."}
        finally:
            with self.lock:
                try:
                    if result["state"] == "completed" and (self.stopping.is_set() or self.closed):
                        result.update(state="cancelled", unreadable=None,
                            error={"code": "cancelled", "message": "Reading cancelled before publication."})
                        result.update({key: [] for key in COLLECTIONS})
                    try:
                        self._save(self.root / "results" / (job["id"] + ".json"), result)
                    except Exception:
                        self._event(job, "failed", {"code": "evidence_write_failed",
                            "message": "Terminal evidence could not be retained; repair storage before retrying."})
                    else:
                        self._event(job, result["state"], result["error"], digest(result))
                except Exception:
                    self.terminal_failures[job["id"]] = {"code": "evidence_write_failed",
                        "message": "Reading stopped but terminal state could not be saved. No result is available; repair storage and reopen."}
                finally:
                    self.running_job_id = None

    def result(self, identifier, verify=True):
        with self.lock:
            _identifier(identifier)
            if identifier in self.terminal_failures:
                raise ProducerError(**self.terminal_failures[identifier])
            job = self._job(identifier)
            if job["state"] not in TERMINAL or job["result_sha256"] is None:
                raise ProducerError("result_unavailable", "This air-device reading has no terminal result.")
            result = self._read(self.root / "results" / (identifier + ".json"))
            _fields(result, RESULT_FIELDS)
            if (digest(result) != job["result_sha256"] or result["id"] != identifier
                    or result["project_id"] != self.project_id or result["state"] != job["state"]
                    or any(result[key] != job[key] for key in ("actor", "reason", "error"))):
                raise ProducerError("integrity_error", "Result differs from immutable job history.")
            if verify:
                self._verify_result(result, job)
            return copy.deepcopy(result)

    def _verify_result(self, result, job):
        try:
            identity = result["producer_identity"]
            if identity is not None and identity != _implementation():
                raise ValueError("producer dependency mismatch")
            raw = self.artifact_bytes(result["response"]) if result["response"] is not None else None
            prepared = result["input"]
            image = self._prepared(prepared, job["source"], result["geometry"]) if prepared is not None else None
            if prepared is not None and result["role"] != prepared["role"]:
                raise ValueError("prepared source role mismatch")
            if result["source"] is not None:
                geometry = sheet_geometry._geometry(result["geometry"])
                source = {key: geometry[key] for key in ("revision_id", "index", "sheet_id")}
                source["geometry_fingerprint"] = geometry["fingerprint"]
                if (source != result["source"] or result["source_refs"] != [source]
                        or geometry != self._page_geometry(job["source"])):
                    raise ValueError("source coordinate mismatch")
            elif result["geometry"] is not None or result["source_refs"]:
                raise ValueError("unbound source geometry")
            if result["model_identity"] is not None:
                _identity(result["model_identity"])
            if result["input_geometry_binding"] is not None:
                if result["input_geometry_binding"] != self._input_geometry_binding(prepared, result["geometry"]):
                    raise ValueError("input geometry proof mismatch")
            if result["state"] == "completed":
                if any(value is None for value in (raw, image, identity, result["source"], result["model_identity"],
                                                  result["input_geometry_binding"])):
                    raise ValueError("missing completed evidence")
                parsed = vision.parse_response(raw, result["model_identity"]["model"], image)
                expected = self._bind(parsed, job["id"], result["response"]["sha256"], result["source"],
                    prepared["input_sha256"], prepared["role"])
                if (any(result[key] != expected[key] for key in COLLECTIONS)
                        or result["unreadable"] != parsed["unreadable"] or result["error"] is not None):
                    raise ValueError("response interpretation mismatch")
            elif any(result[key] for key in COLLECTIONS) or result["unreadable"] is not None:
                raise ValueError("unsuccessful reading has proposals")
        except Exception as error:
            if isinstance(error, ProducerError):
                raise
            raise ProducerError("integrity_error", "Result no longer matches its original source evidence.") from None

    def cancel(self, identifier):
        _identifier(identifier)
        with self.lock:
            if identifier == self.running_job_id:
                self.stopping.set()
                if self.adapter is not None:
                    self.adapter.cancel()
            return self._visible_job(self._job(identifier))

    def _visible_job(self, job):
        visible = copy.deepcopy(job)
        if job["id"] in self.terminal_failures:
            visible.update(state="failed", result_sha256=None,
                error=copy.deepcopy(self.terminal_failures[job["id"]]), durable_state=job["state"], terminal_retained=False)
        return visible

    def view(self):
        with self.lock:
            return {"configured": self.adapter is not None,
                "jobs": [self._visible_job(job) for job in self._jobs()],
                "running_job_id": self.running_job_id if self.thread is not None and self.thread.is_alive() else None}

    def close(self):
        self.closed = True
        self.stopping.set()
        if self.adapter is not None:
            self.adapter.cancel()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
