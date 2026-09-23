"""Durable asynchronous duct image observations from explicitly configured local AI.

Original PDFs and prepared PNGs remain with the existing Foundation/input store.
Append-only job events point to immutable terminal records and original response
bytes. Reads and reopening never execute a model. Quantity policy stays in the
workflow and duct calculation engine. Python 3.9+, standard library only.
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
    spec = importlib.util.spec_from_file_location("duct_source_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vision_v1 = _load("local_duct_vision")
vision = _load("local_duct_vision_v2")
vision_v3 = _load("local_duct_vision_v3")
sheet_geometry = _load("sheet_geometry")
sheet_scale = _load("sheet_scale")
calculation = _load("duct_calculation")
LEGACY_VERSION = "duct-source-producer-1"
ARC_VERSION = "duct-source-producer-2"
VERSION = "duct-source-producer-3"
MAX_RECORD = 16 * 1024 * 1024
MAX_RESPONSE = vision.transport.MAX_RESPONSE_BYTES + 1
MAX_JOBS = 1000
ID = re.compile(r"duct_source_[0-9a-f]{32}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
STATES = {"queued", "running", "completed", "failed", "cancelled", "interrupted"}
TERMINAL = STATES - {"queued", "running"}
RESULT_FIELDS = {"id", "project_id", "state", "source", "geometry", "role", "input", "model_identity",
                 "producer_identity", "observations", "current_sources", "source_refs", "unreadable",
                 "error", "response", "actor", "reason", "scale_context"}
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
        raise ProducerError("integrity_error", "Duct source records must contain bounded finite JSON.") from None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(packed(value))


IMPLEMENTATION_SHA256 = sha(Path(__file__).read_bytes())


def _fields(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ProducerError("integrity_error", "Duct source record fields do not match its contract.")


def _text(value, maximum=1000):
    try:
        return vision.transport._text(value, maximum, True)
    except (ValueError, UnicodeError):
        raise ProducerError("invalid_payload", "Use nonempty bounded plain text.") from None


def _hash(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ProducerError("integrity_error", "Duct source hash is invalid.")


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ProducerError("not_found", "The duct source record identifier is invalid.")
    return value


def _error(error):
    return {"code": str(getattr(error, "code", "producer_failed"))[:100],
            "message": str(getattr(error, "message", "The local duct reading failed; retained evidence is available."))[:2000]}


def _source(value):
    _fields(value, ("revision_id", "index"))
    _hash(value["revision_id"])
    if type(value["index"]) is not int or not 0 <= value["index"] <= 4294967295:
        raise ProducerError("invalid_payload", "Choose an original page index.")
    return copy.deepcopy(value)


def _adapter(version):
    if version == LEGACY_VERSION:
        return vision_v1
    if version == ARC_VERSION:
        return vision
    if version == VERSION:
        return vision_v3
    raise ProducerError("integrity_error", "Unsupported duct producer version.")


def _identity_version(value):
    kind = value.get("kind") if isinstance(value, dict) else None
    if kind == vision_v1.DuctVision.KIND:
        return LEGACY_VERSION
    if kind == vision.DuctVision.KIND:
        return ARC_VERSION
    if kind == vision_v3.DuctVision.KIND:
        return VERSION
    raise ProducerError("model_mismatch", "Unknown typed duct adapter identity.")


def _identity(value, version=None):
    _fields(value, ("kind", "model", "model_sha256", "runtime", "runtime_version", "runtime_sha256",
                    "prompt_sha256", "schema_sha256", "image_max_dimension", "temperature", "seed"))
    for key in ("model_sha256", "runtime_sha256", "prompt_sha256", "schema_sha256"):
        _hash(value[key])
    for key in ("model", "runtime", "runtime_version"):
        _text(value[key])
    adapter = _adapter(version if version is not None else _identity_version(value))
    if (value["kind"] != adapter.DuctVision.KIND or value["runtime"] != "ollama"
            or value["image_max_dimension"] != 2000 or type(value["image_max_dimension"]) is not int
            or type(value["temperature"]) is not int or value["temperature"] != 0
            or type(value["seed"]) is not int or value["seed"] != 0
            or value["prompt_sha256"] != sha(adapter.PROMPT.encode("utf-8"))
            or value["schema_sha256"] != digest(adapter.SCHEMA)):
        raise ProducerError("model_mismatch", "The adapter identity does not match the typed local duct contract.")
    return copy.deepcopy(value)


class DuctSourceProducer:
    """One bounded page job at a time; input_engine is the existing PNG preparer."""

    def __init__(self, workspace, input_engine, adapter=None):
        self.workspace, self.inputs, self.adapter = workspace, input_engine, adapter
        self.project_id = workspace.metadata["project"]["id"]
        self.root = Path(workspace.root) / "duct-source"
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.thread = None
        self.running_job_id = None
        self.closed = False
        # An unavailable terminal write cannot establish durable history. Keep
        # the stopped worker's failure visible in this process without rewriting
        # its last retained event or publishing an unreferenced result.
        self.terminal_failures = {}
        for path in (self.root, self.root / "jobs", self.root / "results", self.root / "responses"):
            if path.is_symlink():
                raise ProducerError("integrity_error", "Duct source storage cannot be a symbolic link.")
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
            raise ProducerError("integrity_error", "Duct evidence is missing, linked or oversized.") from None

    def _write(self, path, raw):
        if len(raw) > MAX_RECORD or path.is_symlink() or path.parent.is_symlink():
            raise ProducerError("integrity_error", "Duct evidence cannot be safely retained.")
        if path.exists():
            if self._read_bytes(path, MAX_RECORD) != raw:
                raise ProducerError("integrity_error", "Immutable duct evidence already exists with different bytes.")
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
                raise ProducerError("integrity_error", "Duct evidence appeared during publication.")
            os.rename(temporary, path)
            temporary = None
        except OSError:
            raise ProducerError("evidence_write_failed", "Duct evidence could not be saved.") from None
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
            raise ProducerError("integrity_error", "Duct source evidence failed envelope verification.") from None

    def _job(self, identifier):
        folder = self.root / "jobs" / _identifier(identifier)
        if folder.is_symlink() or not folder.is_dir():
            raise ProducerError("not_found", "The duct source job is unavailable.")
        paths = sorted(path for path in folder.iterdir() if not path.name.startswith(".pending-"))
        if not 1 <= len(paths) <= 8:
            raise ProducerError("integrity_error", "Duct job history is missing or unbounded.")
        previous = None
        for sequence, path in enumerate(paths, 1):
            if path.name != "%04d.json" % sequence:
                raise ProducerError("integrity_error", "Duct job history sequence is invalid.")
            event = self._read(path)
            _fields(event, EVENT_FIELDS)
            if (event["id"] != identifier or event["project_id"] != self.project_id
                    or event["sequence"] != sequence or type(event["sequence"]) is not int
                    or event["state"] not in STATES or event["previous_sha256"] != (digest(previous) if previous else None)):
                raise ProducerError("integrity_error", "Duct job history identity changed.")
            _source(event["source"])
            _text(event["actor"])
            _text(event["reason"])
            _text(event["at"])
            if previous is None and event["state"] != "queued":
                raise ProducerError("integrity_error", "Duct job must begin queued.")
            if previous is not None:
                if previous["state"] in TERMINAL or any(event[k] != previous[k] for k in ("source", "actor", "reason")):
                    raise ProducerError("integrity_error", "Terminal duct jobs cannot be rewritten.")
            if event["result_sha256"] is not None:
                _hash(event["result_sha256"])
                if event["state"] not in TERMINAL:
                    raise ProducerError("integrity_error", "Only terminal jobs can reference a result.")
            previous = event
        return previous

    def _jobs(self):
        paths = sorted((self.root / "jobs").iterdir())
        if len(paths) > MAX_JOBS:
            raise ProducerError("size_limit", "The duct source job collection is full.")
        return [self._job(path.name) for path in paths]

    def _event(self, previous, state, error=None, result_sha256=None):
        event = {key: copy.deepcopy(previous[key]) for key in ("id", "project_id", "source", "actor", "reason")}
        event.update(state=state, error=error, sequence=previous.get("sequence", 0) + 1,
                     previous_sha256=digest(previous) if "sequence" in previous else None,
                     result_sha256=result_sha256, at=datetime.now(timezone.utc).isoformat())
        folder = self.root / "jobs" / event["id"]
        if folder.is_symlink():
            raise ProducerError("integrity_error", "Duct job storage cannot be linked.")
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
                raise ProducerError("closed", "The duct source producer is closed.")
            if self.thread is not None and self.thread.is_alive():
                raise ProducerError("producer_busy", "A local duct reading is already running.")
            if self.adapter is None:
                raise ProducerError("model_unavailable", "Configure a pinned local vision model before finding ductwork.")
            self._page_geometry(source)
            if len(self._jobs()) >= MAX_JOBS:
                raise ProducerError("size_limit", "The duct source job collection is full.")
            if sha(Path(__file__).read_bytes()) != IMPLEMENTATION_SHA256:
                raise ProducerError("engine_changed", "Reload the application after a duct producer update.")
            self.stopping.clear()
            self.adapter.reset_cancel()
            seed = {"id": "duct_source_" + uuid.uuid4().hex, "project_id": self.project_id,
                    "source": source, "actor": actor, "reason": reason}
            job = self._event(seed, "queued")
            self.running_job_id = job["id"]
            self.thread = threading.Thread(target=self._execute, args=(copy.deepcopy(job),),
                                           name="duct-source", daemon=True)
            self.thread.start()
            return copy.deepcopy(job)

    def _check_stop(self):
        if self.stopping.is_set() or self.closed:
            raise ProducerError("cancelled", "The local duct reading was cancelled.")

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
            raise ProducerError("integrity_error", "Duct response reference is invalid.")
        raw = self._read_bytes(self.root / ref["key"], MAX_RESPONSE)
        if sha(raw) != ref["sha256"] or len(raw) != ref["bytes"]:
            raise ProducerError("integrity_error", "Original duct response bytes changed.")
        return raw

    @staticmethod
    def _bind(proposal, source, geometry, image_sha256, scale_context, version=ARC_VERSION):
        if version == VERSION:
            proposal = vision_v3.validate_proposal(proposal)
            return DuctSourceProducer._bind(vision_v3.without_connections(proposal), source, geometry,
                                            image_sha256, scale_context, ARC_VERSION)
        proposal = _adapter(version).validate_proposal(proposal)
        facts, decisions = scale_context["facts"], scale_context["decisions"]
        evidence, mapping = [], {}
        for region in proposal["evidence"]:
            identifier = "duct_evidence_" + digest({"source": source, "image_sha256": image_sha256, "region": region})
            mapping[region["id"]] = identifier
            evidence.append({"id": identifier, "source": copy.deepcopy(source), "bbox": region["bbox"],
                             "text": region["text"], "artifact_sha256": image_sha256})
        observations = []
        for index, portion in enumerate(proposal["portions"]):
            observation = copy.deepcopy(portion)
            identifier = "duct_observation_" + digest({"source": source, "image_sha256": image_sha256,
                                                      "index": index, "portion": portion})
            reading_ids = {reading["id"]: "duct_reading_" + digest({"observation_id": identifier, "reading": reading})
                           for reading in observation["readings"]}
            observation.update(schema=("duct-observation-2" if portion["geometry"]["kind"] == "circular_arc"
                                       else "duct-observation-1"), id=identifier, source=copy.deepcopy(source))
            observation["evidence_ids"] = [mapping[ref] for ref in observation["evidence_ids"]]
            observation["group"]["evidence_ids"] = [mapping[ref] for ref in observation["group"]["evidence_ids"]]
            for reading in observation["readings"]:
                reading["id"] = reading_ids[reading["id"]]
                reading["evidence_ids"] = [mapping[ref] for ref in reading["evidence_ids"]]
                if reading["datum_id"] is not None:
                    reading["datum_id"] = mapping[reading["datum_id"]]
            shape = observation["geometry"]
            for key in ("dimension_check_ids", "radius_check_ids", "elevation_ids"):
                if key in shape:
                    shape[key] = [reading_ids[ref] for ref in shape[key]]
            if "support_ids" in shape:
                shape["support_ids"] = [mapping[ref] for ref in shape["support_ids"]]
            if shape.get("rise_reading_id") is not None:
                shape["rise_reading_id"] = reading_ids[shape["rise_reading_id"]]
            projection = shape.get("projection")
            if isinstance(projection, dict) and projection["kind"] == "dimension":
                projection["reading_id"] = reading_ids[projection["reading_id"]]
            scaled = shape if shape["kind"] in ("planar", "circular_arc") else projection
            if isinstance(scaled, dict) and scaled["kind"] in ("planar", "circular_arc", "scaled_path"):
                resolver = sheet_scale.resolve
                if scaled["kind"] == "circular_arc":
                    sheet_scale.validate_arc(scaled["points"], geometry)
                    resolver = sheet_scale.resolve_arc
                usable = []
                for fact in facts:
                    try:
                        resolver(facts, decisions, fact["id"], geometry, scaled["points"])
                        usable.append(fact["id"])
                    except (ValueError, KeyError, TypeError):
                        continue
                scaled["scale_fact_id"] = usable[0] if len(usable) == 1 else "unresolved-scale"
                if len(usable) != 1:
                    observation["issues"].append("No unique verified scale supports this path.")
            observations.append(calculation.validate_observation(observation))
        return observations, {source["sheet_id"]: {"geometry": copy.deepcopy(geometry), "evidence": evidence}}

    @staticmethod
    def _bind_connections(proposal, observations, source, image_sha256):
        members = {p['id']: o['id'] for p, o in zip(proposal['portions'], observations)}
        evidence = {e['id']: 'duct_evidence_' + digest({'source': source,
            'image_sha256': image_sha256, 'region': e}) for e in proposal['evidence']}
        assertions = [{'id': 'duct_connection_' + digest({'source': source,
            'image_sha256': image_sha256, 'assertion': a}),
            'members': [members[m] for m in a['members']], 'relation': a['relation'],
            'evidence_ids': [evidence[e] for e in a['evidence_ids']]} for a in proposal['connections']]
        return vision_v3.topology.normalize_assertions(assertions, list(members.values()), list(evidence.values()))

    def _producer_identity(self):
        # Explicitly supplied legacy adapters remain legacy jobs. The application
        # default is v3, while retained records select their own parser below.
        version = _identity_version(_identity(self.adapter.identity()))
        adapter = _adapter(version)
        identity = {"version": version, "sha256": IMPLEMENTATION_SHA256,
                    "adapter_sha256": sha(Path(adapter.__file__).read_bytes()),
                    "transport_sha256": sha(Path(adapter.transport.__file__).read_bytes())}
        if version in (ARC_VERSION, VERSION):
            legacy_adapter = adapter.legacy.legacy if version == VERSION else adapter.legacy
            identity["legacy_adapter_sha256"] = sha(Path(legacy_adapter.__file__).read_bytes())
        if version == VERSION:
            identity["arc_adapter_sha256"] = sha(Path(adapter.legacy.__file__).read_bytes())
            identity["topology_sha256"] = sha(Path(adapter.topology.__file__).read_bytes())
        return identity

    def _execute(self, job):
        raw = None
        result = {"id": job["id"], "project_id": self.project_id, "state": "failed",
                  "source": None, "geometry": None, "role": None, "input": None, "model_identity": None,
                  "producer_identity": None, "observations": [], "current_sources": {},
                  "source_refs": [], "unreadable": None, "error": None, "response": None,
                  "actor": job["actor"], "reason": job["reason"], "scale_context": {"facts": [], "decisions": []}}
        try:
            with self.lock:
                self._check_stop()
                job = self._event(job, "running")
            result["producer_identity"] = self._producer_identity()
            version = result["producer_identity"]["version"]
            if version == VERSION:
                result["connections"] = []
            parser = _adapter(version)
            prepared = self.inputs.prepare_input(job["source"], job["actor"], job["reason"])
            result["input"] = prepared
            result["role"] = prepared["role"]
            geometry = self._page_geometry(job["source"])
            result["geometry"] = geometry
            source = {key: geometry[key] for key in ("revision_id", "index", "sheet_id")}
            source["geometry_fingerprint"] = geometry["fingerprint"]
            result["source"] = source
            result["source_refs"] = [source]
            data = self.workspace.workflow.data
            result["scale_context"] = copy.deepcopy({"facts": data.get("calibrations", []),
                                                      "decisions": data.get("scale_decisions", [])})
            image = self.inputs.knowledge.source_bytes(prepared["source_id"])
            self._check_stop()
            identity = _identity(self.adapter.identity(), version)
            result["model_identity"] = identity
            self._check_stop()
            proposal, raw = self.adapter.read(image, prepared["role"], identity)
            result["response"] = self._retain_response(raw)
            self._check_stop()
            parsed = parser.parse_response(raw, identity["model"], image)
            if packed(parsed) != packed(proposal):
                raise ProducerError("integrity_error", "Typed duct proposals disagree with their original response.")
            if _identity(self.adapter.identity(), version) != identity:
                raise ProducerError("model_changed", "The local model identity changed during duct reading.")
            if geometry != self._page_geometry(job["source"]):
                raise ProducerError("source_changed", "The page coordinate identity changed during duct reading.")
            self.inputs._lineage(prepared)
            observations, current_sources = self._bind(parsed, source, geometry, prepared["input_sha256"],
                                                       result["scale_context"], version)
            if version == VERSION:
                result["connections"] = self._bind_connections(parsed, observations, source, prepared["input_sha256"])
            result.update(observations=observations, current_sources=current_sources,
                          unreadable=parsed["unreadable"], state="completed")
            self._check_stop()
        except Exception as error:
            result.update(state="cancelled" if self.stopping.is_set() or self.closed else "failed",
                          error=_error(error), observations=[], current_sources={}, unreadable=None)
            raw = raw if raw is not None else getattr(error, "raw_response", None)
            if isinstance(raw, bytes) and raw and result["response"] is None:
                try:
                    result["response"] = self._retain_response(raw)
                except Exception:
                    result["error"] = {"code": "evidence_write_failed", "message": "The failed raw response could not be retained."}
        finally:
            with self.lock:
                try:
                    if result["state"] == "completed" and (self.stopping.is_set() or self.closed):
                        result.update(state="cancelled", observations=[], current_sources={}, unreadable=None,
                                      error={"code": "cancelled", "message": "The duct reading was cancelled before publication."})
                    if result["state"] != "completed" and "connections" in result:
                        result["connections"] = []
                    try:
                        self._save(self.root / "results" / (job["id"] + ".json"), result)
                    except Exception:
                        self._event(job, "failed", {"code": "evidence_write_failed",
                            "message": "Terminal duct evidence could not be retained; start a new reading after repairing storage."})
                    else:
                        self._event(job, result["state"], result["error"], digest(result))
                except Exception:
                    self.terminal_failures[job["id"]] = {
                        "code": "evidence_write_failed",
                        "message": "The duct reading stopped, but its terminal state could not be saved. "
                                   "No result is available; repair storage and reopen before starting a new reading."}
                finally:
                    self.running_job_id = None

    def result(self, identifier, verify=True):
        with self.lock:
            _identifier(identifier)
            if identifier in self.terminal_failures:
                raise ProducerError(**self.terminal_failures[identifier])
            job = self._job(identifier)
            if job["state"] not in TERMINAL or job["result_sha256"] is None:
                raise ProducerError("result_unavailable", "This duct reading has no terminal result.")
            result = self._read(self.root / "results" / (identifier + ".json"))
            _fields(result, RESULT_FIELDS | ({"connections"} if isinstance(result.get("producer_identity"), dict)
                and result["producer_identity"].get("version") == VERSION else set()))
            if (digest(result) != job["result_sha256"] or result["id"] != identifier
                    or result["project_id"] != self.project_id or result["state"] != job["state"]
                    or any(result[key] != job[key] for key in ("actor", "reason", "error"))):
                raise ProducerError("integrity_error", "Duct result differs from its immutable job history.")
            if verify:
                self._verify_result(result, job)
            return copy.deepcopy(result)

    def _verify_result(self, result, job):
        try:
            identity = result["producer_identity"]
            version = None
            if identity is None:
                if result["state"] == "completed":
                    raise ValueError("missing completed producer identity")
            else:
                keys = ("version", "sha256", "adapter_sha256", "transport_sha256")
                if isinstance(identity, dict) and identity.get("version") in (ARC_VERSION, VERSION):
                    keys += ("legacy_adapter_sha256",)
                if isinstance(identity, dict) and identity.get("version") == VERSION:
                    keys += ("arc_adapter_sha256", "topology_sha256")
                _fields(identity, keys)
                version = identity["version"]
                _adapter(version)
                for key in keys[1:]:
                    _hash(identity[key])
            raw = self.artifact_bytes(result["response"]) if result["response"] is not None else None
            image = None
            prepared = result["input"]
            if prepared is not None:
                if self.inputs._read("inputs", prepared["id"]) != prepared:
                    raise ValueError("prepared input mismatch")
                if any(prepared[key] != job["source"][key] for key in ("revision_id", "index")):
                    raise ValueError("input source mismatch")
                self.inputs._lineage(prepared)
                image = self.inputs.knowledge.source_bytes(prepared["source_id"])
                if result["role"] != prepared["role"]:
                    raise ValueError("source role mismatch")
            if result["source"] is not None:
                geometry = sheet_geometry._geometry(result["geometry"])
                source = {key: geometry[key] for key in ("revision_id", "index", "sheet_id")}
                source["geometry_fingerprint"] = geometry["fingerprint"]
                if (source != result["source"] or result["source_refs"] != [source]
                        or geometry != self._page_geometry(job["source"])):
                    raise ValueError("source coordinate mismatch")
            if result["model_identity"] is not None:
                _identity(result["model_identity"], version)
            _fields(result["scale_context"], ("facts", "decisions"))
            if not all(isinstance(result["scale_context"][key], list) for key in ("facts", "decisions")):
                raise ValueError("invalid scale context")
            if result["state"] == "completed":
                if raw is None or image is None or result["model_identity"] is None or result["source"] is None:
                    raise ValueError("missing completed evidence")
                proposal = _adapter(version).parse_response(raw, result["model_identity"]["model"], image)
                observations, sources = self._bind(proposal, result["source"], result["geometry"],
                                                    prepared["input_sha256"], result["scale_context"], version)
                if version == VERSION and self._bind_connections(proposal, observations, result["source"],
                        prepared["input_sha256"]) != result["connections"]:
                    raise ValueError("connection interpretation mismatch")
                if (observations != result["observations"] or sources != result["current_sources"]
                        or proposal["unreadable"] != result["unreadable"] or result["error"] is not None):
                    raise ValueError("response interpretation mismatch")
            elif result["observations"] or result["current_sources"] or result["unreadable"] is not None or result.get("connections"):
                raise ValueError("failed result has observations")
        except Exception as error:
            if isinstance(error, ProducerError):
                raise
            raise ProducerError("integrity_error", "Duct result no longer matches its original source evidence.") from None

    def cancel(self, identifier):
        _identifier(identifier)
        if identifier == self.running_job_id:
            self.stopping.set()
            if self.adapter is not None:
                self.adapter.cancel()
        with self.lock:
            return self._visible_job(self._job(identifier))

    def _visible_job(self, job):
        visible = copy.deepcopy(job)
        if job["id"] in self.terminal_failures:
            visible.update(state="failed", result_sha256=None,
                           error=copy.deepcopy(self.terminal_failures[job["id"]]),
                           durable_state=job["state"], terminal_retained=False)
        return visible

    def view(self):
        with self.lock:
            return {"configured": self.adapter is not None, "jobs": [self._visible_job(job) for job in self._jobs()],
                    "running_job_id": self.running_job_id if self.thread is not None and self.thread.is_alive() else None}

    def close(self):
        self.closed = True
        self.stopping.set()
        if self.adapter is not None:
            self.adapter.cancel()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
