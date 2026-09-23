"""Immutable dataset/model declarations and reproducible saved-output experiments.

Source rights are retained provenance statements. Neither declarations nor scores
authorize training, install models, establish execution proof, or grant quantity
authority. All source checks and record writes share one SQLite transaction.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re


VERSION = "mechanical-model-baseline-1"
SCHEMA_TABLE = "mechanical_model_baseline_schema"
RECORD_TABLE = "mechanical_model_baseline_records"
CATEGORIES = ("equipment", "air_devices", "ductwork", "piping", "fittings",
              "accessories", "controls", "insulation", "demolition")
KINDS = ("dataset", "model", "plan", "run")
DUCT_TASK = "duct-evaluation-1"
MAX_BYTES = 16 * 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
FIELDS = {
    "dataset": {"name", "version", "annotation_basis", "scope", "samples"},
    "model": {"name", "version", "source_ids", "artifacts", "runtime", "preprocessing"},
    "plan": {"dataset_id", "model_id", "split", "criteria"},
    "run": {"plan_id", "model_manifest_sha256", "predictions"},
}
PIN_FIELDS = {"source_id", "snapshot_sha256", "lifecycle_sequence", "state",
              "lifecycle_sha256", "rights_basis", "project_id", "revision_id",
              "source_artifact_sha256"}
BODY_FIELDS = {"kind", "project_id", "payload", "actor", "reason", "source_pins",
               "scorer_identity", "recorded_at"}


class BaselineError(ValueError):
    """A stable, caller-readable baseline failure."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _json(value, depth=0, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if depth > 24 or count[0] > 1_000_000:
        raise BaselineError("size_limit", "Record exceeds the structured-data limit.")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BaselineError("invalid_payload", "Numbers must be finite.")
        return
    if isinstance(value, str):
        if len(value) > 10_000:
            raise BaselineError("size_limit", "Text exceeds the record limit.")
        return
    if isinstance(value, list):
        for item in value:
            _json(item, depth + 1, count)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise BaselineError("invalid_payload", "JSON keys must be bounded nonempty strings.")
            _json(item, depth + 1, count)
        return
    raise BaselineError("invalid_payload", "Records must contain only JSON values.")


def packed(value):
    _json(value)
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise BaselineError("invalid_payload", "Record is not encodable JSON: %s" % error)
    if len(data) > MAX_BYTES:
        raise BaselineError("size_limit", "Record exceeds the saved-byte limit.")
    return data


def _digest(value):
    return hashlib.sha256(packed(value)).hexdigest()


def _fields(value, expected, field):
    if not isinstance(value, dict) or set(value) != expected:
        raise BaselineError("invalid_payload", "%s has missing or unsupported fields." % field)


def _text(value, field, identifier=False):
    if not isinstance(value, str) or not value.strip() or len(value) > 10_000:
        raise BaselineError("invalid_payload", "%s must be nonempty bounded text." % field)
    if identifier and (len(value) > 512 or value != value.strip()):
        raise BaselineError("invalid_payload", "%s must be an exact bounded identifier." % field)
    return value


def _hash(value, field):
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise BaselineError("invalid_payload", "%s must be a complete lowercase SHA-256." % field)
    return value


def _version(value, field):
    _text(value, field, identifier=True)
    if value.casefold() in {"latest", "current", "unspecified", "unknown", "*"}:
        raise BaselineError("invalid_payload", "%s must pin a fixed version." % field)
    return value


def _number(value, field, minimum=0, maximum=None, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BaselineError("invalid_payload", "%s must be a finite number." % field)
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if (not finite or value < minimum or (positive and value <= 0) or
            (maximum is not None and value > maximum)):
        raise BaselineError("invalid_payload", "%s is outside its allowed range." % field)
    return value


def _list(value, field, minimum=0, maximum=200):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise BaselineError("invalid_payload", "%s has an invalid item count." % field)
    return value


def _objects(values, scope=None):
    _list(values, "objects", maximum=250)
    seen = set()
    for item in values:
        _fields(item, {"id", "category", "label", "bbox"}, "object")
        _text(item["id"], "object.id", identifier=True)
        if item["id"] in seen:
            raise BaselineError("duplicate_object", "Object identifiers must be unique within a sample.")
        seen.add(item["id"])
        if item["category"] not in CATEGORIES or (scope is not None and item["category"] not in scope):
            raise BaselineError("invalid_category", "Object category is unsupported or outside dataset scope.")
        # Keep independently authored truth and saved output spelling intact;
        # the scorer normalizes whitespace/case solely for matching.
        _text(item["label"], "object.label")
        box = _list(item["bbox"], "bbox", minimum=4, maximum=4)
        for number in box:
            _number(number, "bbox", maximum=1)
        if box[0] >= box[2] or box[1] >= box[3]:
            raise BaselineError("invalid_payload", "Bounding boxes require positive normalized extents.")
    return sorted(values, key=lambda item: item["id"])


def _normalize(kind, payload, stored=False):
    task = _task(payload)
    allowed = FIELDS[kind] | ({"report"} if kind == "run" and stored else set())
    if task is not None and kind != "model":
        allowed = allowed | {"task"}
    _fields(payload, allowed, kind)
    value = json.loads(packed(payload))
    if kind == "dataset":
        _text(value["name"], "name")
        _version(value["version"], "version")
        _text(value["annotation_basis"], "annotation_basis")
        scope = _list(value["scope"], "scope", minimum=1, maximum=len(CATEGORIES))
        if any(category not in CATEGORIES for category in scope) or len(set(scope)) != len(scope):
            raise BaselineError("invalid_category", "Dataset scope must contain unique supported categories.")
        value["scope"] = sorted(scope)
        if task == DUCT_TASK:
            if value["scope"] != ["ductwork"]:
                raise BaselineError("invalid_category", "Duct evaluation truth has ductwork scope only.")
            value["samples"] = _duct_call("normalize_samples", value["samples"])
            if not any(sample["split"] == "test" for sample in value["samples"]):
                raise BaselineError("invalid_payload", "A dataset requires at least one test sample.")
            return value
        samples = _list(value["samples"], "samples", minimum=1)
        ids, inputs = set(), set()
        for sample in samples:
            _fields(sample, {"id", "source_id", "input_sha256", "group_id", "split", "objects"}, "sample")
            for key in ("id", "source_id", "group_id"):
                _text(sample[key], "sample." + key, identifier=True)
            _hash(sample["input_sha256"], "input_sha256")
            if sample["split"] not in ("train", "validation", "test"):
                raise BaselineError("invalid_payload", "Dataset split must be train, validation, or test.")
            if sample["id"] in ids:
                raise BaselineError("duplicate_sample", "Sample identifiers must be unique.")
            if sample["input_sha256"] in inputs:
                raise BaselineError("duplicate_input", "A source snapshot cannot be counted twice in one dataset.")
            ids.add(sample["id"])
            inputs.add(sample["input_sha256"])
            sample["objects"] = _objects(sample["objects"], scope)
        if not any(sample["split"] == "test" for sample in samples):
            raise BaselineError("invalid_payload", "A dataset requires at least one test sample.")
        value["samples"] = sorted(samples, key=lambda sample: sample["id"])
    elif kind == "model":
        _text(value["name"], "name")
        _version(value["version"], "version")
        sources = _list(value["source_ids"], "source_ids", minimum=1)
        for source_id in sources:
            _text(source_id, "source_id", identifier=True)
        if len(sources) != len(set(sources)):
            raise BaselineError("invalid_payload", "Model source identifiers must be unique.")
        value["source_ids"] = sorted(sources)
        artifacts = _list(value["artifacts"], "artifacts", minimum=1)
        names = set()
        for artifact in artifacts:
            _fields(artifact, {"name", "sha256", "bytes"}, "artifact")
            _text(artifact["name"], "artifact.name", identifier=True)
            _hash(artifact["sha256"], "artifact.sha256")
            size = artifact["bytes"]
            if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= 2**63 - 1:
                raise BaselineError("invalid_payload", "Artifact bytes must be a positive bounded integer.")
            if artifact["name"] in names:
                raise BaselineError("invalid_payload", "Artifact logical names must be unique.")
            names.add(artifact["name"])
        value["artifacts"] = sorted(artifacts, key=lambda artifact: artifact["name"])
        _fields(value["runtime"], {"name", "version", "sha256"}, "runtime")
        _text(value["runtime"]["name"], "runtime.name")
        _version(value["runtime"]["version"], "runtime.version")
        _hash(value["runtime"]["sha256"], "runtime.sha256")
        preprocessing = value["preprocessing"]
        if not isinstance(preprocessing, dict) or not preprocessing or len(packed(preprocessing)) > 65536:
            raise BaselineError("invalid_payload", "Preprocessing must be a nonempty bounded JSON object.")
    elif kind == "plan":
        for key in ("dataset_id", "model_id"):
            _text(value[key], key, identifier=True)
        if value["split"] not in ("validation", "test"):
            raise BaselineError("invalid_payload", "Evaluation plans use test or validation data.")
        criteria = value["criteria"]
        if task == DUCT_TASK:
            value["criteria"] = _duct_call("normalize_criteria", criteria)
            return value
        _fields(criteria, {"iou_threshold", "minimum_precision", "minimum_recall",
                           "max_mean_latency_ms", "max_peak_memory_mb"}, "criteria")
        for key in ("iou_threshold", "minimum_precision", "minimum_recall"):
            _number(criteria[key], key, maximum=1, positive=key == "iou_threshold")
        for key in ("max_mean_latency_ms", "max_peak_memory_mb"):
            _number(criteria[key], key, positive=True)
    else:
        _text(value["plan_id"], "plan_id", identifier=True)
        _hash(value["model_manifest_sha256"], "model_manifest_sha256")
        if task == DUCT_TASK:
            value["predictions"] = _duct_call("normalize_predictions", value["predictions"])
            return value
        predictions = _list(value["predictions"], "predictions", minimum=1)
        seen = set()
        for prediction in predictions:
            _fields(prediction, {"sample_id", "objects", "latency_ms", "peak_memory_mb"}, "prediction")
            _text(prediction["sample_id"], "sample_id", identifier=True)
            if prediction["sample_id"] in seen:
                raise BaselineError("duplicate_prediction", "A run requires exactly one output per selected sample.")
            seen.add(prediction["sample_id"])
            prediction["objects"] = _objects(prediction["objects"])
            _number(prediction["latency_ms"], "latency_ms")
            if prediction["peak_memory_mb"] is not None:
                _number(prediction["peak_memory_mb"], "peak_memory_mb")
        value["predictions"] = sorted(predictions, key=lambda prediction: prediction["sample_id"])
    return value


def _task(payload):
    if not isinstance(payload, dict):
        raise BaselineError("invalid_payload", "Baseline payload must be an object.")
    if "task" not in payload:
        return None
    if payload["task"] != DUCT_TASK:
        raise BaselineError("invalid_task", "Unsupported explicit evaluation task.")
    return DUCT_TASK


def _same_task(*payloads):
    if len({_task(payload) for payload in payloads}) != 1:
        raise BaselineError("task_mismatch", "Dataset, plan and run evaluation tasks must agree.")


def _duct_scoring():
    path = Path(__file__).with_name("duct_path_scoring.py")
    try:
        spec = importlib.util.spec_from_file_location("heleos_duct_path_scoring", path)
        module = importlib.util.module_from_spec(spec)
        source = path.read_bytes()
        exec(compile(source, str(path), "exec"), module.__dict__)
    except Exception as error:
        # Import-time failures cannot make already sealed mixed history unreadable.
        # Identity checks and actual validation/scoring retain their own errors.
        raise BaselineError("scorer_unavailable", "Local duct scorer could not load: %s" % error) from None
    try:
        identity = module.identity()
        if hashlib.sha256(source).hexdigest() != module.SOURCE_SHA256 or path.read_bytes() != source:
            raise BaselineError("scorer_changed", "Duct scorer bytes changed while loading.")
        return module, identity
    except (OSError, ImportError, SyntaxError, AttributeError) as error:
        raise BaselineError("scorer_unavailable", "Local duct scorer is unavailable: %s" % error)


def _duct_call(name, *args):
    try:
        module, identity = _duct_scoring()
        result = getattr(module, name)(*args)
        if module.identity() != identity:
            raise BaselineError("scorer_changed", "Duct scorer dependencies changed during validation.")
        return result
    except ValueError as error:
        raise BaselineError(getattr(error, "code", "invalid_payload"), str(error)) from None


def _task_scorer(task, cache):
    if task not in cache:
        cache[task] = _duct_scoring() if task == DUCT_TASK else _scoring()
    return cache[task]


def _stored_duct_shape(kind, payload):
    """Read bounded sealed history when its scorer is unavailable; never admit it."""
    _fields(payload, FIELDS[kind] | {"task"} | ({"report"} if kind == "run" else set()), kind)
    if kind == "dataset":
        samples = _list(payload["samples"], "samples", minimum=1)
        ids, hashes = [], []
        for sample in samples:
            _fields(sample, {"id", "source_id", "input_sha256", "group_id", "split", "objects", "context"}
                    | ({"topology"} if isinstance(sample, dict) and "topology" in sample else set()), "sample")
            for key in ("id", "source_id", "group_id"):
                _text(sample[key], key, identifier=True)
            _hash(sample["input_sha256"], "input_sha256")
            if sample["split"] not in ("train", "validation", "test"):
                raise BaselineError("integrity_error", "Invalid historical sample split.")
            for item in _list(sample["objects"], "objects", maximum=250):
                if isinstance(item, dict) and item.get("schema") == "duct-path-truth-2":
                    _fields(item, {"schema", "id", "category", "geometry", "group", "expected_meters"}, "truth object")
                    shape = item["geometry"]
                    if not isinstance(shape, dict) or shape.get("kind") not in ("planar", "circular_arc", "circular_arc_span"):
                        raise BaselineError("integrity_error", "Invalid historical centerline kind.")
                    if shape["kind"] == "circular_arc_span":
                        _fields(shape, {"kind", "base_points", "start_ray", "end_ray"}, "truth geometry")
                        _list(shape["base_points"], "base_points", minimum=3, maximum=3)
                        for key in ("start_ray", "end_ray"):
                            _list(shape[key], key, minimum=2, maximum=2)
                    else:
                        _fields(shape, {"kind", "points"}, "truth geometry")
                        _list(shape["points"], "points", minimum=3 if shape["kind"] == "circular_arc" else 2,
                              maximum=3 if shape["kind"] == "circular_arc" else 128)
                else:
                    _fields(item, {"id", "category", "points", "group", "expected_meters"}, "truth object")
                    _list(item["points"], "points", minimum=2, maximum=128)
                _text(item["id"], "truth id", identifier=True)
            if 'topology' in sample:
                topology = sample['topology']
                _fields(topology, {'schema', 'evidence', 'assertions'}, 'topology truth')
                if topology['schema'] != 'duct-topology-truth-1':
                    raise BaselineError('integrity_error', 'Invalid historical topology version.')
                for evidence in _list(topology['evidence'], 'topology evidence', maximum=2000):
                    _fields(evidence, {'id', 'bbox', 'text'}, 'topology evidence')
                    _text(evidence['id'], 'evidence id', identifier=True)
                    _list(evidence['bbox'], 'evidence bounds', minimum=4, maximum=4)
                for assertion in _list(topology['assertions'], 'topology assertions', maximum=1000):
                    _fields(assertion, {'id', 'members', 'relation', 'evidence_ids'}, 'topology assertion')
                    _list(assertion['members'], 'members', minimum=2, maximum=2)
                    _list(assertion['evidence_ids'], 'evidence ids', minimum=1, maximum=128)
                    if assertion['relation'] not in ('connected', 'not_connected'):
                        raise BaselineError('integrity_error', 'Invalid historical truth relation.')
            _fields(sample["context"], {"source", "geometry", "scale_context"}, "context")
            ids.append(sample["id"])
            hashes.append(sample["input_sha256"])
        if ids != sorted(set(ids)) or len(hashes) != len(set(hashes)):
            raise BaselineError("integrity_error", "Historical samples must remain unique and ordered.")
    elif kind == "run":
        ids = []
        for prediction in _list(payload["predictions"], "predictions", minimum=1):
            _fields(prediction, {"sample_id", "producer_id", "producer_result", "producer_result_sha256"}, "prediction")
            for key in ("sample_id", "producer_id"):
                _text(prediction[key], key, identifier=True)
            _hash(prediction["producer_result_sha256"], "producer_result_sha256")
            if _digest(prediction["producer_result"]) != prediction["producer_result_sha256"]:
                raise BaselineError("integrity_error", "Historical producer content changed.")
            ids.append(prediction["sample_id"])
        if ids != sorted(set(ids)):
            raise BaselineError("integrity_error", "Historical predictions must remain unique and ordered.")
    return payload


def _scoring():
    path = Path(__file__).with_name("mechanical_model_scoring.py")
    spec = importlib.util.spec_from_file_location("heleos_mechanical_model_scoring", path)
    module = importlib.util.module_from_spec(spec)
    try:
        source = path.read_bytes()
        # Compile exactly the pinned repository bytes, without stale pyc reuse.
        exec(compile(source, str(path), "exec"), module.__dict__)
    except (OSError, ImportError, SyntaxError) as error:
        raise BaselineError("scorer_unavailable", "Local baseline scorer is unavailable: %s" % error)
    actual = hashlib.sha256(source).hexdigest()
    if actual != module.IMPLEMENTATION_SHA256 or path.read_bytes() != source:
        raise BaselineError("scorer_changed", "Scorer bytes changed while its identity was loaded.")
    return module, {"version": module.VERSION, "sha256": actual}


class BaselineStore:
    """Append immutable baseline records inside an existing KnowledgeStore."""

    def __init__(self, knowledge_store):
        if not isinstance(getattr(knowledge_store, "database", None), Path):
            raise BaselineError("invalid_store", "BaselineStore requires an existing KnowledgeStore.")
        self.knowledge = knowledge_store
        self.database = knowledge_store.database
        connection = self.knowledge._connect()
        try:
            self._transaction(connection, lambda: self._initialize(connection), write=True)
        finally:
            connection.close()

    @staticmethod
    def _transaction(connection, operation, write=False):
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            result = operation()
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _initialize(connection):
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if SCHEMA_TABLE in tables:
            versions = [row[0] for row in connection.execute("SELECT version FROM " + SCHEMA_TABLE)]
            if versions != [1] or RECORD_TABLE not in tables:
                raise BaselineError("schema_version", "Unsupported model-baseline schema.")
            return
        if RECORD_TABLE in tables:
            raise BaselineError("schema_version", "Baseline records have no schema authority.")
        connection.execute("CREATE TABLE mechanical_model_baseline_schema (version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO mechanical_model_baseline_schema VALUES (1)")
        connection.execute("""CREATE TABLE mechanical_model_baseline_records (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            request_sha256 TEXT NOT NULL UNIQUE,
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('dataset','model','plan','run')),
            body_json TEXT NOT NULL
        )""")
        connection.execute("""CREATE INDEX mechanical_model_baseline_project
            ON mechanical_model_baseline_records(project_id, sequence)""")

    def _source_pin(self, connection, source_id, project_id):
        try:
            row, metadata, unused = self.knowledge._source_row(connection, source_id)
            decision = self.knowledge._latest_source_decision(connection, source_id)
        except ValueError as error:
            raise BaselineError(getattr(error, "code", "source_error"), str(error))
        if metadata.get("project_id") not in ("unspecified", project_id):
            raise BaselineError("project_mismatch", "Baseline source belongs to another project.")
        _text(metadata.get("rights_basis"), "source.rights_basis")
        artifact = metadata.get("source_artifact_sha256", "unspecified")
        if artifact != "unspecified":
            _hash(artifact, "source_artifact_sha256")
        if decision["state"] not in ("current", "retired", "superseded"):
            raise BaselineError("integrity_error", "Source lifecycle state is invalid.")
        return {
            "source_id": source_id, "snapshot_sha256": row["snapshot_sha256"],
            "lifecycle_sequence": decision["sequence"], "state": decision["state"],
            "lifecycle_sha256": _digest(dict(decision)), "rights_basis": metadata["rights_basis"],
            "project_id": metadata["project_id"], "revision_id": metadata.get("revision_id", "unspecified"),
            "source_artifact_sha256": artifact,
        }

    @staticmethod
    def _validate_pins(pins):
        _list(pins, "source_pins", minimum=1, maximum=400)
        ids = []
        for pin in pins:
            _fields(pin, PIN_FIELDS, "source pin")
            for key in ("source_id", "project_id", "revision_id"):
                _text(pin[key], key, identifier=True)
            for key in ("snapshot_sha256", "lifecycle_sha256"):
                _hash(pin[key], key)
            if pin["source_artifact_sha256"] != "unspecified":
                _hash(pin["source_artifact_sha256"], "source_artifact_sha256")
            _text(pin["rights_basis"], "rights_basis")
            sequence = pin["lifecycle_sequence"]
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                raise BaselineError("integrity_error", "Invalid stored source lifecycle sequence.")
            if pin["state"] not in ("current", "retired", "superseded"):
                raise BaselineError("integrity_error", "Invalid stored source state.")
            ids.append(pin["source_id"])
        if ids != sorted(set(ids)):
            raise BaselineError("integrity_error", "Stored source pins must be unique and ordered.")

    def _pin_issues(self, connection, pins, project_id):
        issues = set()
        for pin in pins:
            source_id = pin["source_id"]
            try:
                current = self._source_pin(connection, source_id, project_id)
            except BaselineError as error:
                issues.add("source_" + error.code + ":" + source_id)
                continue
            if current != pin:
                issues.add("source_pin_changed:" + source_id)
            if current["state"] != "current":
                issues.add("source_not_current:" + source_id)
        return issues

    @staticmethod
    def _merge_pins(records):
        pins = {}
        for record in records:
            for pin in record["source_pins"]:
                if pin["source_id"] in pins and pins[pin["source_id"]] != pin:
                    raise BaselineError("stale_dependency", "Dependencies pin different source lifecycle states.")
                pins[pin["source_id"]] = pin
        return [pins[key] for key in sorted(pins)]

    @staticmethod
    def _selected(dataset, plan):
        selected = [sample for sample in dataset["payload"]["samples"] if sample["split"] == plan["split"]]
        if not selected or not any(sample["objects"] for sample in selected):
            raise BaselineError("empty_evaluation", "The selected split needs at least one expected object.")
        return selected

    @staticmethod
    def _run_inputs(payload, model, selected):
        if payload["model_manifest_sha256"] != _digest(model["payload"]):
            raise BaselineError("model_mismatch", "Run model manifest does not match the frozen plan.")
        if {prediction["sample_id"] for prediction in payload["predictions"]} != {sample["id"] for sample in selected}:
            raise BaselineError("sample_mismatch", "Run predictions must cover exactly the selected samples.")

    @staticmethod
    def _leakage_entries(payload, pins):
        by_source = {pin["source_id"]: pin for pin in pins}
        for sample in payload["samples"]:
            pin = by_source[sample["source_id"]]
            keys = [("input", sample["input_sha256"]),
                    ("group", " ".join(sample["group_id"].split()).casefold())]
            if pin["source_artifact_sha256"] != "unspecified":
                keys.append(("artifact", pin["source_artifact_sha256"]))
            if pin["revision_id"] != "unspecified":
                keys.append(("revision", pin["project_id"], pin["revision_id"]))
            yield sample["split"], keys

    def _check_leakage(self, connection, project_id, payload, pins, cache):
        assignments = {}
        records = [self._record(connection, row["id"], cache) for row in connection.execute(
            "SELECT id FROM mechanical_model_baseline_records WHERE project_id=? AND kind='dataset' ORDER BY sequence",
            (project_id,))]
        pairs = [(record["payload"], record["source_pins"]) for record in records] + [(payload, pins)]
        for data, source_pins in pairs:
            for split, keys in self._leakage_entries(data, source_pins):
                for key in keys:
                    if key in assignments and assignments[key] != split:
                        raise BaselineError("split_leakage", "Input, originating group, artifact, or revision crosses dataset splits.")
                    assignments[key] = split

    def _record(self, connection, record_id, cache, scorer=None):
        scorers = scorer if isinstance(scorer, dict) else ({None: scorer} if scorer is not None else {})
        normalization_issue = None
        if record_id in cache:
            return cache[record_id]
        row = connection.execute("SELECT * FROM mechanical_model_baseline_records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise BaselineError("record_not_found", "Baseline dependency was not found.")
        try:
            body = json.loads(row["body_json"])
            _fields(body, BODY_FIELDS, "stored record")
            if body["kind"] not in KINDS or body["kind"] != row["kind"] or body["project_id"] != row["project_id"]:
                raise BaselineError("integrity_error", "Stored baseline index does not match its content.")
            for key in ("project_id", "actor", "reason", "recorded_at"):
                _text(body[key], key, identifier=key == "project_id")
            if packed(body).decode("utf-8") != row["body_json"]:
                raise BaselineError("integrity_error", "Stored baseline record is not canonical.")
            expected_id = "baseline_" + body["kind"] + "_" + _digest(body)
            request = {key: value for key, value in body.items() if key != "recorded_at"}
            if expected_id != row["id"] or _digest(request) != row["request_sha256"]:
                raise BaselineError("integrity_error", "Stored baseline identity does not match its content.")
            try:
                normalized = _normalize(body["kind"], body["payload"], stored=True)
            except BaselineError as error:
                if error.code != "scorer_unavailable" or _task(body["payload"]) != DUCT_TASK:
                    raise
                normalized = _stored_duct_shape(body["kind"], body["payload"])
                normalization_issue = error.code
            if normalized != body["payload"]:
                raise BaselineError("integrity_error", "Stored baseline payload is not normalized.")
            self._validate_pins(body["source_pins"])
            identity = body["scorer_identity"]
            if body["kind"] in ("plan", "run"):
                _fields(identity, {"version", "sha256"}, "scorer_identity")
                _text(identity["version"], "scorer.version")
                _hash(identity["sha256"], "scorer.sha256")
            elif identity is not None:
                raise BaselineError("integrity_error", "Only plans and runs own scorer identities.")
        except (ValueError, TypeError, KeyError) as error:
            raise BaselineError("integrity_error", "Invalid stored baseline record: %s" % error)
        result = {key: value for key, value in body.items() if key != "scorer_identity"}
        result["id"] = record_id
        issues = self._pin_issues(connection, body["source_pins"], body["project_id"])
        if normalization_issue is not None:
            issues.add(normalization_issue)
        payload, kind = body["payload"], body["kind"]
        if kind in ("dataset", "model"):
            source_ids = (sorted({sample["source_id"] for sample in payload["samples"]}) if kind == "dataset"
                          else payload["source_ids"])
            if source_ids != [pin["source_id"] for pin in body["source_pins"]]:
                raise BaselineError("integrity_error", "Source pins do not cover the declared sources.")
            if kind == "dataset":
                by_source = {pin["source_id"]: pin for pin in body["source_pins"]}
                if any(sample["input_sha256"] != by_source[sample["source_id"]]["snapshot_sha256"] for sample in payload["samples"]):
                    raise BaselineError("integrity_error", "Dataset input hashes do not match pinned snapshots.")
            else:
                result["model_manifest_sha256"] = _digest(payload)
        else:
            def dependency(identifier, expected_kind):
                record = self._record(connection, identifier, cache, scorers)
                dep_sequence = connection.execute("SELECT sequence FROM mechanical_model_baseline_records WHERE id=?", (identifier,)).fetchone()[0]
                if (record["kind"] != expected_kind or record["project_id"] != body["project_id"] or dep_sequence >= row["sequence"]):
                    raise BaselineError("integrity_error", "Dependency kind, project, or chronology is invalid.")
                issues.update("dependency_issue:" + identifier + ":" + issue for issue in record["issues"])
                return record
            if kind == "plan":
                dataset = dependency(payload["dataset_id"], "dataset")
                model = dependency(payload["model_id"], "model")
                _same_task(payload, dataset["payload"])
                self._selected(dataset, payload)
                expected_pins = self._merge_pins([dataset, model])
                result["scorer_identity"] = identity
            else:
                plan = dependency(payload["plan_id"], "plan")
                dataset = dependency(plan["payload"]["dataset_id"], "dataset")
                model = dependency(plan["payload"]["model_id"], "model")
                _same_task(payload, plan["payload"], dataset["payload"])
                selected = self._selected(dataset, plan["payload"])
                self._run_inputs(payload, model, selected)
                expected_pins = plan["source_pins"]
                if identity != plan["scorer_identity"]:
                    raise BaselineError("integrity_error", "Run scorer is not the frozen plan scorer.")
                report = payload["report"]
                if not isinstance(report, dict) or "sha256" not in report:
                    raise BaselineError("integrity_error", "Run report is missing its identity.")
                if _digest({key: value for key, value in report.items() if key != "sha256"}) != report["sha256"]:
                    raise BaselineError("integrity_error", "Run report digest does not match its saved content.")
                if report.get("version") != identity["version"] or report.get("scorer_sha256") != identity["sha256"]:
                    raise BaselineError("integrity_error", "Run report uses a different scorer identity.")
            if expected_pins != body["source_pins"]:
                raise BaselineError("integrity_error", "Baseline pins do not match its frozen dependencies.")
            try:
                scorer = _task_scorer(_task(payload), scorers)
            except BaselineError as error:
                scorer = None
                issues.add(error.code)
            if scorer is not None:
                if scorer[1] != identity:
                    issues.add("scorer_changed")
                elif kind == "run":
                    if self._score(scorer[0], selected, payload["predictions"], plan["payload"]["criteria"]) != payload["report"]:
                        raise BaselineError("integrity_error", "Saved evaluation report is not reproducible.")
        result["issues"] = sorted(issues)
        cache[record_id] = result
        return result

    @staticmethod
    def _score(module, samples, predictions, criteria):
        try:
            identity = module.identity() if hasattr(module, "identity") else None
            result = module.score(samples, predictions, criteria)
            if identity is not None and module.identity() != identity:
                raise BaselineError("scorer_changed", "Duct scorer dependencies changed during evaluation.")
            return result
        except ValueError as error:
            raise BaselineError(getattr(error, "code", "scoring_error"), str(error))

    def register(self, project_id, kind, payload, actor, reason):
        _text(project_id, "project_id", identifier=True)
        _text(actor, "actor")
        _text(reason, "reason")
        if kind not in KINDS:
            raise BaselineError("invalid_kind", "Baseline kind must be dataset, model, plan, or run.")
        payload = _normalize(kind, payload)
        connection = self.knowledge._connect()
        try:
            def write():
                cache, identity, scorers = {}, None, {}
                if kind in ("dataset", "model"):
                    ids = (sorted({sample["source_id"] for sample in payload["samples"]}) if kind == "dataset"
                           else payload["source_ids"])
                    pins = [self._source_pin(connection, source_id, project_id) for source_id in ids]
                    if kind == "dataset":
                        by_id = {pin["source_id"]: pin for pin in pins}
                        if any(sample["input_sha256"] != by_id[sample["source_id"]]["snapshot_sha256"] for sample in payload["samples"]):
                            raise BaselineError("input_mismatch", "Dataset input SHA-256 must equal actual source snapshot bytes.")
                        self._check_leakage(connection, project_id, payload, pins, cache)
                else:
                    scorer = _task_scorer(_task(payload), scorers)
                    identity = scorer[1]

                    def dependency(identifier, expected_kind):
                        record = self._record(connection, identifier, cache, scorers)
                        if record["project_id"] != project_id:
                            raise BaselineError("project_mismatch", "Baseline dependency belongs to another project.")
                        if record["kind"] != expected_kind:
                            raise BaselineError("invalid_dependency", "Baseline dependency has the wrong kind.")
                        if record["issues"]:
                            raise BaselineError("stale_dependency", "Baseline dependency has unresolved integrity or lifecycle issues.")
                        return record

                    if kind == "plan":
                        dataset = dependency(payload["dataset_id"], "dataset")
                        model = dependency(payload["model_id"], "model")
                        _same_task(payload, dataset["payload"])
                        self._selected(dataset, payload)
                        pins = self._merge_pins([dataset, model])
                    else:
                        plan = dependency(payload["plan_id"], "plan")
                        dataset = dependency(plan["payload"]["dataset_id"], "dataset")
                        model = dependency(plan["payload"]["model_id"], "model")
                        _same_task(payload, plan["payload"], dataset["payload"])
                        selected = self._selected(dataset, plan["payload"])
                        self._run_inputs(payload, model, selected)
                        if plan["scorer_identity"] != identity:
                            raise BaselineError("scorer_changed", "New runs require the scorer frozen by the plan.")
                        pins = plan["source_pins"]
                        payload["report"] = self._score(scorer[0], selected, payload["predictions"], plan["payload"]["criteria"])
                request = {"kind": kind, "project_id": project_id, "payload": payload,
                           "actor": actor, "reason": reason, "source_pins": pins,
                           "scorer_identity": identity}
                request_hash = _digest(request)
                existing = connection.execute("SELECT id FROM mechanical_model_baseline_records WHERE request_sha256=?", (request_hash,)).fetchone()
                if existing is not None:
                    return self._record(connection, existing["id"], cache, scorers)
                body = dict(request, recorded_at=datetime.now(timezone.utc).isoformat())
                record_id = "baseline_" + kind + "_" + _digest(body)
                connection.execute("""INSERT INTO mechanical_model_baseline_records
                    (id, request_sha256, project_id, kind, body_json) VALUES (?, ?, ?, ?, ?)""",
                                   (record_id, request_hash, project_id, kind, packed(body).decode("utf-8")))
                return self._record(connection, record_id, cache, scorers)
            return self._transaction(connection, write, write=True)
        finally:
            connection.close()

    def view(self, project_id):
        _text(project_id, "project_id", identifier=True)
        connection = self.knowledge._connect()
        try:
            def read():
                result = {kind + "s": [] for kind in KINDS}
                cache = {}
                scorers = {}
                for row in connection.execute("SELECT id FROM mechanical_model_baseline_records WHERE project_id=? ORDER BY sequence", (project_id,)):
                    record = self._record(connection, row["id"], cache, scorers)
                    result[record["kind"] + "s"].append(record)
                sample_sets = {category: set() for category in CATEGORIES}
                evaluated_sets = {category: set() for category in CATEGORIES}
                for dataset in result["datasets"]:
                    if _task(dataset["payload"]) is not None:
                        continue
                    for sample in dataset["payload"]["samples"]:
                        for category in {item["category"] for item in sample["objects"]}:
                            sample_sets[category].add(sample["input_sha256"])
                for run in result["runs"]:
                    if run["issues"] or _task(run["payload"]) is not None:
                        continue
                    plan = cache[run["payload"]["plan_id"]]
                    dataset = cache[plan["payload"]["dataset_id"]]
                    for sample in self._selected(dataset, plan["payload"]):
                        for category in {item["category"] for item in sample["objects"]}:
                            evaluated_sets[category].add(sample["input_sha256"])
                result["coverage"] = [{"category": category, "samples": len(sample_sets[category]),
                                       "evaluated_samples": len(evaluated_sets[category])} for category in CATEGORIES]
                duct_datasets = [d for d in result["datasets"] if _task(d["payload"]) == DUCT_TASK]
                if duct_datasets:
                    duct_samples = {s["input_sha256"] for d in duct_datasets for s in d["payload"]["samples"]}
                    evaluated = set()
                    for run in result["runs"]:
                        if _task(run["payload"]) == DUCT_TASK and not run["issues"]:
                            plan = cache[run["payload"]["plan_id"]]
                            dataset = cache[plan["payload"]["dataset_id"]]
                            evaluated.update(s["input_sha256"] for s in self._selected(dataset, plan["payload"]))
                    result["duct_coverage"] = [{"category": "ductwork", "samples": len(duct_samples),
                                                 "evaluated_samples": len(evaluated)}]
                result["quantity_authority"] = "none"
                result["execution_proof"] = "saved_outputs_only"
                result["state_fingerprint"] = _digest(result)
                return result
            return self._transaction(connection, read)
        finally:
            connection.close()
