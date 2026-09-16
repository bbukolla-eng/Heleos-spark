"""Bind saved duct experiments to original project inputs without running a model."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path


TASK = "duct-evaluation-1"


class EvaluationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _fail(code, message):
    raise EvaluationError(code, message)


def _load(name):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("evaluation_" + name, path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def _packed(value):
    return _load("mechanical_model_baseline").packed(value)


def _digest(value):
    return hashlib.sha256(_packed(value)).hexdigest()


def _records(flow):
    view = flow.model_baseline.view(flow.data["project_id"])
    return {record["id"]: record for key in ("datasets", "models", "plans", "runs") for record in view[key]}


def _dependency(records, identifier, kind):
    if not isinstance(identifier, str) or not identifier or len(identifier) > 512:
        _fail("invalid_payload", "Evaluation dependency IDs must be bounded strings.")
    record = records.get(identifier)
    if record is None or record["kind"] != kind:
        _fail("duct_evaluation_dependency", "Choose a saved %s in this project." % kind)
    if record["issues"]:
        _fail("stale_dependency", "The frozen evaluation dependency has unresolved source or scorer issues.")
    return record


def _context(flow, data, sheets, sample, scorer):
    inputs = [item for item in flow.model_inference._records("inputs")
              if item["source_id"] == sample.get("source_id") and item["input_sha256"] == sample.get("input_sha256")]
    if len(inputs) != 1:
        _fail("duct_evaluation_input", "Each sample must identify one prepared original project PNG input.")
    prepared = inputs[0]
    flow.model_inference._lineage(prepared)
    key = prepared["revision_id"] + ":" + str(prepared["index"])
    sheet = sheets.get(key)
    if sheet is None or sheet["sheet_id"] != prepared["sheet_id"]:
        _fail("duct_evaluation_source", "The prepared input no longer identifies this original page.")
    role = data.get("pages", {}).get(key, {}).get("role", "plan")
    if role != prepared["role"]:
        _fail("duct_evaluation_role", "The prepared input belongs to an earlier page-role assignment.")
    geometry = _load("sheet_geometry").sheet_geometry(sheet, prepared["revision_id"])
    source = {k: geometry[k] for k in ("revision_id", "index", "sheet_id")}
    source["geometry_fingerprint"] = geometry["fingerprint"]
    scale = scorer.project_scale_context({"facts": data.get("calibrations", []),
        "decisions": data.get("scale_decisions", [])}, source, geometry)
    return {"source": source, "geometry": geometry, "scale_context": scale}


def _dataset(flow, data, sheets, payload, scorer, inject=False):
    value = copy.deepcopy(payload)
    if not isinstance(value.get("samples"), list) or not 1 <= len(value["samples"]) <= 200:
        _fail("invalid_payload", "Duct datasets require a bounded sample collection.")
    for sample in value["samples"]:
        if not isinstance(sample, dict):
            _fail("invalid_payload", "Each duct sample must be an object.")
        context = _context(flow, data, sheets, sample, scorer)
        if "context" not in sample and inject:
            sample["context"] = context
        elif sample.get("context") != context:
            _fail("duct_evaluation_context", "The frozen source, geometry or scale context differs from this project.")
    return value


def _model(payload, observed=None):
    identity = payload.get("preprocessing", {}).get("execution_identity")
    try:
        _load("duct_source_producer")._identity(identity)
    except (ValueError, TypeError, KeyError):
        _fail("duct_evaluation_model", "Declare the complete retained duct execution identity in model preprocessing.")
    expected_runtime = {"name": identity["runtime"], "version": identity["runtime_version"],
                        "sha256": identity["runtime_sha256"]}
    if (payload.get("runtime") != expected_runtime or
            not any(item.get("sha256") == identity["model_sha256"] for item in payload.get("artifacts", [])) or
            observed is not None and observed != identity):
        _fail("duct_evaluation_model", "Model, runtime and model artifact identities must match the retained duct producer.")


def _prediction(flow, prediction, sample, model, scorer):
    if not isinstance(prediction, dict) or set(prediction) not in (
            {"sample_id", "producer_id"},
            {"sample_id", "producer_id", "producer_result", "producer_result_sha256"}):
        _fail("invalid_payload", "Duct run predictions name one sample and its retained producer ID.")
    producer = getattr(flow.workspace, "duct_producer", None)
    if producer is None:
        _fail("duct_evaluation_producer", "The retained duct producer store is unavailable.")
    record = producer.result(prediction["producer_id"], verify=True)
    if record["state"] != "completed" or record["project_id"] != flow.data["project_id"]:
        _fail("duct_evaluation_producer", "Evaluation requires a completed original duct producer in this project.")
    _model(model["payload"], record["model_identity"])
    expanded = {"sample_id": prediction["sample_id"], "producer_id": record["id"],
                "producer_result": record, "producer_result_sha256": _digest(record)}
    if "producer_result" in prediction and prediction != expanded:
        _fail("duct_evaluation_producer", "Submitted result differs from the original immutable producer evidence.")
    scorer.normalize_predictions([expanded], [sample])
    return expanded


def bind_payload(flow, data, sheets, kind, payload):
    """Resolve untrusted import identifiers before the baseline transaction."""
    if not isinstance(payload, dict) or payload.get("task") != TASK:
        return payload
    _packed(payload)
    value = copy.deepcopy(payload)
    try:
        scorer = _load("duct_path_scoring")
    except Exception:
        _fail("scorer_unavailable", "The local duct evaluation scorer is unavailable.")
    if kind == "dataset":
        return _dataset(flow, data, sheets, value, scorer, inject=True)
    if kind not in ("plan", "run"):
        return value
    records = _records(flow)
    plan = value if kind == "plan" else _dependency(records, value.get("plan_id"), "plan")["payload"]
    if plan.get("task") != TASK:
        _fail("task_mismatch", "Duct runs require a duct evaluation plan.")
    dataset = _dependency(records, plan.get("dataset_id"), "dataset")
    if dataset["payload"].get("task") != TASK:
        _fail("task_mismatch", "Duct plans require a duct evaluation dataset.")
    _dataset(flow, data, sheets, dataset["payload"], scorer)
    model = _dependency(records, plan.get("model_id"), "model")
    _model(model["payload"])
    if kind == "run":
        if not isinstance(value.get("predictions"), list) or not 1 <= len(value["predictions"]) <= 200:
            _fail("invalid_payload", "Duct runs require a bounded prediction collection.")
        selected = {s["id"]: s for s in dataset["payload"]["samples"] if s["split"] == plan["split"]}
        incoming = [p.get("sample_id") if isinstance(p, dict) else None for p in value["predictions"]]
        if (any(not isinstance(identifier, str) or not identifier or len(identifier) > 512 for identifier in incoming) or
                len(set(incoming)) != len(incoming) or set(incoming) != set(selected)):
            _fail("sample_mismatch", "Predictions must name each frozen selected sample exactly once.")
        value["predictions"] = [_prediction(flow, p, selected[p["sample_id"]], model, scorer) for p in value["predictions"]]
    return value


def current_view(flow, data, sheets, baseline):
    """Annotate current project applicability without changing stored reports."""
    if not any(d["payload"].get("task") == TASK for d in baseline["datasets"]):
        return baseline
    view = copy.deepcopy(baseline)
    records = {r["id"]: r for key in ("datasets", "models", "plans", "runs") for r in view[key]}
    try:
        scorer = _load("duct_path_scoring")
    except Exception:
        for key in ("datasets", "plans", "runs"):
            for record in view[key]:
                if record["payload"].get("task") == TASK:
                    record["issues"] = sorted(set(record["issues"]) | {"scorer_unavailable"})
        view["duct_coverage"][0]["evaluated_samples"] = 0
        view["state_fingerprint"] = _digest({k: v for k, v in view.items() if k != "state_fingerprint"})
        return view
    for record in view["datasets"]:
        if record["payload"].get("task") != TASK:
            continue
        try:
            _dataset(flow, data, sheets, record["payload"], scorer)
        except (ValueError, OSError, KeyError, TypeError) as error:
            record["issues"] = sorted(set(record["issues"]) | {"duct_context:" + getattr(error, "code", "unavailable")})
    for kind in ("plans", "runs"):
        for record in view[kind]:
            if record["payload"].get("task") != TASK:
                continue
            payload = record["payload"]
            dependencies = [records[payload["dataset_id"]], records[payload["model_id"]]] if kind == "plans" else [records[payload["plan_id"]]]
            extra = {"dependency_issue:" + dep["id"] + ":" + issue for dep in dependencies for issue in dep["issues"]}
            if kind == "runs":
                plan = records[payload["plan_id"]]["payload"]
                dataset, model = records[plan["dataset_id"]], records[plan["model_id"]]
                samples = {s["id"]: s for s in dataset["payload"]["samples"]}
                for prediction in payload["predictions"]:
                    try:
                        _prediction(flow, prediction, samples[prediction["sample_id"]], model, scorer)
                    except (ValueError, OSError, KeyError, TypeError) as error:
                        extra.add("duct_producer:" + getattr(error, "code", "unavailable"))
            record["issues"] = sorted(set(record["issues"]) | extra)
    evaluated = set()
    for run in view["runs"]:
        if run["payload"].get("task") == TASK and not run["issues"]:
            plan = records[run["payload"]["plan_id"]]["payload"]
            dataset = records[plan["dataset_id"]]["payload"]
            evaluated.update(s["input_sha256"] for s in dataset["samples"] if s["split"] == plan["split"])
    view["duct_coverage"][0]["evaluated_samples"] = len(evaluated)
    view["state_fingerprint"] = _digest({k: v for k, v in view.items() if k != "state_fingerprint"})
    return view


def export_records(flow, baseline):
    """Verify original artifacts even for stale experiments and unused producers."""
    records = {}
    for run in baseline["runs"]:
        if run["payload"].get("task") != TASK:
            continue
        for prediction in run["payload"]["predictions"]:
            actual = flow.workspace.duct_producer.result(prediction["producer_id"], verify=True)
            if actual != prediction["producer_result"] or _digest(actual) != prediction["producer_result_sha256"]:
                _fail("duct_evaluation_producer", "Frozen evaluation producer evidence changed; export is blocked.")
            records[actual["id"]] = actual
    return [records[key] for key in sorted(records)]
