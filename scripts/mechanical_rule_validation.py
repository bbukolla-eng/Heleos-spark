"""Replay hand-written cases against one compiled mechanical rule.

Validation proves only the candidate rule's requirement-applicability scope.
It does not generate expected answers, admit rules, or calculate quantities.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path


VERSION = "mechanical-rule-validation-1"
SCHEMA = "mechanical-rule-validation/v1"
MIN_CASES = 3
MAX_CASES = 100
MAX_INPUT_BYTES = 4_000_000
MAX_OBJECT_BYTES = 500_000
MAX_TEXT = 10_000
MAX_STRUCTURED_ITEMS = 50_000
MAX_DEPTH = 24

_KINDS = ("positive", "negative", "unknown")
_EXPECTED_BY_KIND = {
    "positive": "matched",
    "negative": "not_applicable",
    "unknown": "needs_context",
}
_STATUSES = frozenset(("matched", "not_applicable", "needs_context",
                       "source_blocked", "unsupported"))


class ValidationError(ValueError):
    """A stable, caller-readable validation failure."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code, message):
    raise ValidationError(code, message)


def _module(name):
    spec = importlib.util.spec_from_file_location(
        "heleos_validation_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


matcher = _module("mechanical_rule_applicability")


def _file_hash(name):
    return hashlib.sha256(Path(__file__).with_name(name + ".py").read_bytes()).hexdigest()


IMPLEMENTATION = {
    name: _file_hash(name)
    for name in ("mechanical_rule_scope", "mechanical_rule_applicability",
                 "mechanical_rule_validation")
}


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        _fail("invalid_input", "Validation inputs must be JSON-compatible: %s" % error)


def _json_value(value, field, depth=0, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if depth > MAX_DEPTH or count[0] > MAX_STRUCTURED_ITEMS:
        _fail("size_limit", "%s exceeds the structured-data limit." % field)
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            _fail("invalid_input", "%s contains a non-finite number." % field)
        return
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            _fail("size_limit", "%s contains an oversized string." % field)
        return
    if isinstance(value, list):
        if len(value) > MAX_STRUCTURED_ITEMS:
            _fail("size_limit", "%s contains too many items." % field)
        for item in value:
            _json_value(item, field, depth + 1, count)
        return
    if isinstance(value, dict):
        if len(value) > MAX_STRUCTURED_ITEMS:
            _fail("size_limit", "%s contains too many fields." % field)
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                _fail("invalid_input", "%s contains an invalid field name." % field)
            _json_value(item, field, depth + 1, count)
        return
    _fail("invalid_input", "%s must contain only JSON values." % field)


def _identity(value, field):
    if (not isinstance(value, str) or not value.strip() or value != value.strip() or
            len(value) > 512):
        _fail("invalid_case", "%s must be a nonempty bounded string without outer whitespace."
              % field)
    return value


def _normalize_cases(cases):
    if not isinstance(cases, list):
        _fail("invalid_cases", "cases must be a list.")
    if len(cases) < MIN_CASES or len(cases) > MAX_CASES:
        _fail("size_limit", "cases must contain between %d and %d entries." %
              (MIN_CASES, MAX_CASES))
    _json_value(cases, "cases")
    if len(_canonical(cases)) > MAX_INPUT_BYTES:
        _fail("size_limit", "cases exceeds the input byte limit.")

    normalized = []
    seen = set()
    coverage = {kind: 0 for kind in _KINDS}
    required = {"id", "kind", "object", "expected_status"}
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or set(case) != required:
            _fail("invalid_case", "cases[%d] must contain exactly id, kind, object, and "
                  "expected_status." % index)
        case_id = _identity(case["id"], "cases[%d].id" % index)
        if case_id in seen:
            _fail("duplicate_identity", "Case IDs must be unique.")
        seen.add(case_id)
        kind = case["kind"]
        if kind not in _KINDS:
            _fail("invalid_case", "cases[%d].kind must be positive, negative, or unknown."
                  % index)
        expected = case["expected_status"]
        if expected not in _STATUSES:
            _fail("invalid_case", "cases[%d].expected_status is not a validation status."
                  % index)
        mandated = _EXPECTED_BY_KIND[kind]
        if expected != mandated:
            _fail("invalid_case", "%s cases require expected_status %s." %
                  (kind, mandated))
        if not isinstance(case["object"], dict):
            _fail("invalid_case", "cases[%d].object must be a dictionary." % index)
        if len(_canonical(case["object"])) > MAX_OBJECT_BYTES:
            _fail("size_limit", "cases[%d].object exceeds the byte limit." % index)
        coverage[kind] += 1
        normalized.append(copy.deepcopy(case))

    missing = [kind for kind in _KINDS if not coverage[kind]]
    if missing:
        _fail("invalid_cases", "cases must include every kind: %s missing." %
              ", ".join(missing))
    normalized.sort(key=lambda item: item["id"])
    return normalized, coverage


def _actual_status(output, compiled_rule, case):
    evaluations = [row for row in output.get("evaluations", [])
                   if row.get("rule_id") == compiled_rule["id"] and
                   row.get("object_id") == case["object"]["id"]]
    if len(evaluations) > 1:
        _fail("invalid_matcher_result", "Matcher returned duplicate evaluations for one case.")
    if evaluations:
        status = evaluations[0].get("status")
        if status not in _STATUSES:
            _fail("invalid_matcher_result", "Matcher returned an unsupported case status.")
        return status

    unassigned = [row for row in output.get("unassigned_rules", [])
                  if row.get("rule_id") == compiled_rule["id"]]
    if any(row.get("status") == "unsupported" for row in unassigned):
        return "unsupported"
    return "not_applicable"


def evaluate_cases(compiled_rule, cases):
    """Evaluate fixed expected cases and return a canonical, replayable report."""
    if not isinstance(compiled_rule, dict):
        _fail("invalid_rule", "compiled_rule must be a dictionary.")
    _json_value(compiled_rule, "compiled_rule")
    compiled_bytes = _canonical(compiled_rule)
    if len(compiled_bytes) > MAX_INPUT_BYTES:
        _fail("size_limit", "compiled_rule exceeds the input byte limit.")
    for field in ("id", "source_rule_id", "project_id"):
        if field not in compiled_rule:
            _fail("invalid_rule", "compiled_rule is missing %s." % field)
        _identity(compiled_rule[field], "compiled_rule." + field)

    normalized_cases, coverage = _normalize_cases(cases)
    original_rule = compiled_bytes
    original_cases = _canonical(cases)
    results = []
    for case in normalized_cases:
        try:
            output = matcher.evaluate(
                [copy.deepcopy(compiled_rule)], [copy.deepcopy(case["object"])],
                compiled_rule["project_id"])
        except matcher.ApplicabilityError as error:
            raise ValidationError(error.code, error.message)
        actual = _actual_status(output, compiled_rule, case)
        results.append({
            "case_id": case["id"],
            "actual_status": actual,
            "expected_status": case["expected_status"],
            "passed": actual == case["expected_status"],
        })

    if _canonical(compiled_rule) != original_rule or _canonical(cases) != original_cases:
        _fail("input_mutated", "Rule validation changed its input.")
    report = {
        "schema": SCHEMA,
        "rule_id": compiled_rule["source_rule_id"],
        "compiled_rule_id": compiled_rule["id"],
        "compiled_sha256": hashlib.sha256(compiled_bytes).hexdigest(),
        "implementation": copy.deepcopy(IMPLEMENTATION),
        "cases": normalized_cases,
        "results": results,
        "passed": (compiled_rule.get("state") == "supported" and
                   all(result["passed"] for result in results)),
        "coverage": coverage,
        "quantity_authority": "none",
    }
    report["sha256"] = hashlib.sha256(_canonical(report)).hexdigest()
    return report
