"""Deterministic detection scores for independently supplied saved outputs.

The report does not establish model execution, hardware performance, topology,
length, engineering understanding, or final takeoff accuracy. Timings and memory
are declarations from the saved output, and no quantity authority is granted.
"""
from collections import deque
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re


VERSION = "mechanical-model-scoring-1"
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CATEGORIES = (
    "equipment", "air_devices", "ductwork", "piping", "fittings",
    "accessories", "controls", "insulation", "demolition",
)
MAX_SAMPLES = 200
MAX_OBJECTS = 250
MAX_TEXT = 512
MAX_JSON_BYTES = 32_000_000
MAX_JSON_ITEMS = 2_000_000
MAX_JSON_DEPTH = 24
_SAMPLE_KEYS = {"id", "source_id", "input_sha256", "group_id", "split", "objects"}
_PREDICTION_KEYS = {"sample_id", "objects", "latency_ms", "peak_memory_mb"}
_OBJECT_KEYS = {"id", "category", "label", "bbox"}
_CRITERIA_KEYS = {
    "iou_threshold", "minimum_precision", "minimum_recall",
    "max_mean_latency_ms", "max_peak_memory_mb",
}


class ScoringError(ValueError):
    """A stable, caller-readable scoring failure."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code, message):
    raise ScoringError(code, message)


def _json_value(value, depth=0, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if depth > MAX_JSON_DEPTH or count[0] > MAX_JSON_ITEMS:
        _fail("size_limit", "JSON exceeds the depth or item limit.")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if value.bit_length() > 1024:
            _fail("size_limit", "JSON integer exceeds the supported size.")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("invalid_input", "JSON numbers must be finite.")
        return
    if isinstance(value, str):
        if len(value) > MAX_JSON_BYTES:
            _fail("size_limit", "JSON text exceeds the byte limit.")
        return
    if isinstance(value, list):
        if len(value) > MAX_JSON_ITEMS:
            _fail("size_limit", "JSON exceeds the item limit.")
        for item in value:
            _json_value(item, depth + 1, count)
        return
    if isinstance(value, dict):
        if len(value) > MAX_JSON_ITEMS:
            _fail("size_limit", "JSON exceeds the item limit.")
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("invalid_input", "JSON object keys must be strings.")
            _json_value(key, depth + 1, count)
            _json_value(item, depth + 1, count)
        return
    _fail("invalid_input", "Inputs must contain only JSON values.")


def packed(value):
    """Return bounded canonical UTF-8 JSON bytes, rejecting nonfinite values."""
    _json_value(value)
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True,
                            allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("invalid_input", "Inputs must be encodable JSON values.")
    if len(result) > MAX_JSON_BYTES:
        _fail("size_limit", "JSON exceeds the byte limit.")
    return result


def _exact(value, keys, field):
    if not isinstance(value, dict) or set(value) != keys:
        _fail("invalid_schema", "%s must contain exactly %s." %
              (field, ", ".join(sorted(keys))))


def _text(value, field, normalize=False):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        _fail("invalid_input", "%s must be nonempty bounded text." % field)
    if normalize:
        return " ".join(value.split()).casefold()
    if value != value.strip():
        _fail("invalid_input", "%s must not have outer whitespace." % field)
    return value


def _number(value, field, minimum=0, maximum=None, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("invalid_number", "%s must be a finite number." % field)
    try:
        number = float(value)
    except (OverflowError, ValueError):
        _fail("invalid_number", "%s must be a finite number." % field)
    if (not math.isfinite(number) or number < minimum or
            (maximum is not None and number > maximum) or
            (positive and number <= 0)):
        _fail("invalid_number", "%s is outside its finite numeric bounds." % field)
    return number


def _objects(objects, field):
    if not isinstance(objects, list):
        _fail("invalid_schema", "%s must be an array." % field)
    if len(objects) > MAX_OBJECTS:
        _fail("size_limit", "%s exceeds 250 objects." % field)
    result = []
    seen = set()
    for raw in objects:
        _exact(raw, _OBJECT_KEYS, field + " object")
        identity = _text(raw["id"], field + ".id")
        if identity in seen:
            _fail("duplicate_object", "%s has duplicate object IDs." % field)
        seen.add(identity)
        category = raw["category"]
        if category not in CATEGORIES:
            _fail("invalid_category", "%s has an unsupported category." % field)
        label = _text(raw["label"], field + ".label", normalize=True)
        if not isinstance(raw["bbox"], list) or len(raw["bbox"]) != 4:
            _fail("invalid_bbox", "%s bbox must be [x0,y0,x1,y1]." % field)
        bbox = [_number(value, field + ".bbox", maximum=1) for value in raw["bbox"]]
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            _fail("invalid_bbox", "%s bbox must have positive extents." % field)
        result.append({"id": identity, "category": category, "label": label,
                       "bbox": bbox})
    return sorted(result, key=lambda row: row["id"])


def _samples(samples):
    if not isinstance(samples, list):
        _fail("invalid_schema", "samples must be an array.")
    if not 1 <= len(samples) <= MAX_SAMPLES:
        _fail("size_limit", "samples must contain 1..200 entries.")
    result = {}
    for sample in samples:
        _exact(sample, _SAMPLE_KEYS, "sample")
        identity = _text(sample["id"], "sample.id")
        if identity in result:
            _fail("duplicate_sample", "Sample IDs must be unique.")
        _text(sample["source_id"], "sample.source_id")
        _text(sample["group_id"], "sample.group_id")
        sha = sample["input_sha256"]
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            _fail("invalid_input", "sample.input_sha256 must be a complete lowercase SHA256.")
        if sample["split"] not in ("train", "validation", "test"):
            _fail("invalid_input", "sample.split must be train, validation, or test.")
        result[identity] = _objects(sample["objects"], "sample.objects")
    return result


def _predictions(predictions, samples):
    if not isinstance(predictions, list):
        _fail("invalid_schema", "predictions must be an array.")
    if len(predictions) > MAX_SAMPLES:
        _fail("size_limit", "predictions exceeds 200 entries.")
    result = {}
    for prediction in predictions:
        _exact(prediction, _PREDICTION_KEYS, "prediction")
        identity = _text(prediction["sample_id"], "prediction.sample_id")
        if identity in result:
            _fail("duplicate_sample", "Prediction sample IDs must be unique.")
        if identity not in samples:
            _fail("extra_sample", "Prediction references an unselected sample.")
        result[identity] = {
            "objects": _objects(prediction["objects"], "prediction.objects"),
            "latency_ms": _number(prediction["latency_ms"], "prediction.latency_ms"),
            "peak_memory_mb": (None if prediction["peak_memory_mb"] is None else
                               _number(prediction["peak_memory_mb"], "prediction.peak_memory_mb")),
        }
    if set(result) != set(samples):
        _fail("missing_sample", "Predictions must include every selected sample, including abstentions.")
    return result


def _criteria(criteria):
    _exact(criteria, _CRITERIA_KEYS, "criteria")
    return {
        key: _number(value, "criteria." + key,
                     maximum=1 if key in ("iou_threshold", "minimum_precision",
                                          "minimum_recall") else None,
                     positive=key not in ("minimum_precision", "minimum_recall"))
        for key, value in criteria.items()
    }


def _iou_at_least(a, b, threshold):
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    if width <= 0 or height <= 0:
        return False
    aw, ah, bw, bh = a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]
    # Scale both axes to avoid underflow for equally tiny but valid boxes.
    sx, sy = max(aw, bw), max(ah, bh)
    area_a, area_b = (aw / sx) * (ah / sy), (bw / sx) * (bh / sy)
    intersection = (width / sx) * (height / sy)
    union = area_a + area_b - intersection
    if union > 0 and intersection > 0 and area_a > 0 and area_b > 0:
        return intersection / union >= threshold
    # Extreme opposing aspect ratios can still underflow; use exact arithmetic
    # only in that rare case. Inputs remain bounded, finite floating values.
    fa, fb = [Fraction(value) for value in a], [Fraction(value) for value in b]
    intersection = ((min(fa[2], fb[2]) - max(fa[0], fb[0])) *
                    (min(fa[3], fb[3]) - max(fa[1], fb[1])))
    union = ((fa[2] - fa[0]) * (fa[3] - fa[1]) +
             (fb[2] - fb[0]) * (fb[3] - fb[1]) - intersection)
    return intersection / union >= Fraction(threshold)


def _maximum_matches(expected, detected, threshold):
    """Hopcroft-Karp matching; sorted vertices give reproducible traversal."""
    edges = [[index for index, truth in enumerate(expected)
              if detection["category"] == truth["category"] and
              detection["label"] == truth["label"] and
              _iou_at_least(detection["bbox"], truth["bbox"], threshold)]
             for detection in detected]
    left = [-1] * len(detected)
    right = [-1] * len(expected)
    infinity = len(detected) + len(expected) + 1
    distance = []

    def augment(vertex, terminal):
        for neighbor in edges[vertex]:
            previous = right[neighbor]
            if ((previous == -1 and distance[vertex] + 1 == terminal) or
                    (previous != -1 and distance[previous] == distance[vertex] + 1
                     and augment(previous, terminal))):
                left[vertex] = neighbor
                right[neighbor] = vertex
                return True
        distance[vertex] = infinity
        return False

    while True:
        distance = [0 if match == -1 else infinity for match in left]
        queue = deque(index for index, match in enumerate(left) if match == -1)
        terminal = infinity
        while queue:
            vertex = queue.popleft()
            if distance[vertex] >= terminal:
                continue
            for neighbor in edges[vertex]:
                previous = right[neighbor]
                if previous == -1:
                    terminal = distance[vertex] + 1
                elif distance[previous] == infinity:
                    distance[previous] = distance[vertex] + 1
                    queue.append(previous)
        if terminal == infinity:
            break
        for vertex, match in enumerate(left):
            if match == -1:
                augment(vertex, terminal)
    return sum(match != -1 for match in left)


def _ratios(row):
    tp, fp, fn = row["true_positive"], row["false_positive"], row["false_negative"]
    row["precision"] = tp / (tp + fp) if tp + fp else None
    row["recall"] = tp / (tp + fn) if tp + fn else None


def score(samples, predictions, criteria):
    """Score saved detections without mutating inputs or executing any model.

    Object labels use whitespace collapse and casefold; categories match exactly.
    Category samples count pages with expected or detected category instances.
    Every absent category is retained with zero samples and undefined ratios.
    Criteria apply to aggregate detection metrics and supplied resource measures.
    Missing memory measurements remain unknown and cannot satisfy resource limits.
    """
    truth_by_sample = _samples(samples)
    output_by_sample = _predictions(predictions, truth_by_sample)
    limits = _criteria(criteria)
    # Schema and individual bounds are checked before aggregate encoding.
    packed([samples, predictions, criteria])
    counts = ("true_positive", "false_positive", "false_negative")
    categories = {category: dict(category=category, samples=0,
                                  **{key: 0 for key in counts})
                  for category in CATEGORIES}
    sample_rows = []
    for sample_id in sorted(truth_by_sample):
        expected = truth_by_sample[sample_id]
        detected = output_by_sample[sample_id]["objects"]
        sample_row = dict(sample_id=sample_id, **{key: 0 for key in counts})
        for category in CATEGORIES:
            truth = [row for row in expected if row["category"] == category]
            output = [row for row in detected if row["category"] == category]
            tp = _maximum_matches(truth, output, limits["iou_threshold"])
            values = {"true_positive": tp, "false_positive": len(output) - tp,
                      "false_negative": len(truth) - tp}
            categories[category]["samples"] += int(bool(truth or output))
            for key, value in values.items():
                sample_row[key] += value
                categories[category][key] += value
        sample_rows.append(sample_row)
    metrics = {key: sum(row[key] for row in sample_rows) for key in counts}
    _ratios(metrics)
    for row in categories.values():
        _ratios(row)
    # An exact rational mean avoids overflow and input-order rounding drift.
    latency_sum = sum((Fraction(row["latency_ms"]) for row in output_by_sample.values()),
                      Fraction())
    metrics["mean_latency_ms"] = float(latency_sum / len(output_by_sample))
    memory = [row["peak_memory_mb"] for row in output_by_sample.values()]
    metrics["peak_memory_mb"] = None if None in memory else max(memory)
    thresholds_met = (
        metrics["precision"] is not None and metrics["recall"] is not None and
        metrics["precision"] >= limits["minimum_precision"] and
        metrics["recall"] >= limits["minimum_recall"] and
        metrics["mean_latency_ms"] <= limits["max_mean_latency_ms"] and
        metrics["peak_memory_mb"] is not None and
        metrics["peak_memory_mb"] <= limits["max_peak_memory_mb"]
    )
    report = {"version": VERSION, "scorer_sha256": IMPLEMENTATION_SHA256,
              "metrics": metrics, "categories": list(categories.values()),
              "samples": sample_rows, "thresholds_met": thresholds_met,
              "quantity_authority": "none", "execution_proof": "saved_outputs_only"}
    report["sha256"] = hashlib.sha256(packed(report)).hexdigest()
    return report
