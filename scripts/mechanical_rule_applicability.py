"""Deterministic, evidence-linked applicability for compiled mechanical rules.

This module only reports rule-to-object scope.  It does not admit rules, choose
contract precedence, approve decisions, or emit takeoff quantities.
"""
import copy
import hashlib
import json


VERSION = "mechanical-rule-applicability-1"
MAX_RULES = 2_000
MAX_OBJECTS = 20_000
MAX_COMPARISONS = 1_000_000
MAX_EVALUATIONS = 100_000
MAX_CONFLICTS = 25_000
MAX_TEXT = 10_000
MAX_STRUCTURED_ITEMS = 20_000
MAX_DEPTH = 20

_DIMENSIONS = (
    ("tags", "tags"),
    ("categories", "category"),
    ("term_ids", "term_ids"),
    ("system_ids", "system_ids"),
)
_RULE_STATES = ("supported", "needs_context", "unsupported")
_POLARITIES = ("require", "prohibit", "unknown")


class ApplicabilityError(ValueError):
    """A stable, caller-readable applicability failure."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code, message):
    raise ApplicabilityError(code, message)


def _identity(value, field, code):
    if (not isinstance(value, str) or not value.strip() or
            len(value) > 512 or value != value.strip()):
        _fail(code, "%s must be a nonempty bounded string without outer whitespace." % field)
    return value


def _text(value, field, code, allow_empty=False):
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        _fail(code, "%s must be a bounded string." % field)
    if not allow_empty and not value.strip():
        _fail(code, "%s must not be empty." % field)
    return value


def _json_value(value, field, code, depth=0, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_STRUCTURED_ITEMS or depth > MAX_DEPTH:
        _fail(code, "%s exceeds the structured-data limit." % field)
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            _fail(code, "%s contains a non-finite number." % field)
        return
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            _fail(code, "%s contains an oversized string." % field)
        return
    if isinstance(value, list):
        if len(value) > MAX_STRUCTURED_ITEMS:
            _fail(code, "%s contains too many items." % field)
        for item in value:
            _json_value(item, field, code, depth + 1, count)
        return
    if isinstance(value, dict):
        if len(value) > MAX_STRUCTURED_ITEMS:
            _fail(code, "%s contains too many fields." % field)
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                _fail(code, "%s contains an invalid field name." % field)
            _json_value(item, field, code, depth + 1, count)
        return
    _fail(code, "%s must contain only JSON values." % field)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _string_list(value, field, code, allow_none=False):
    if value is None and allow_none:
        return None
    if not isinstance(value, list):
        _fail(code, "%s must be a list%s." %
              (field, " or null" if allow_none else ""))
    if len(value) > MAX_STRUCTURED_ITEMS:
        _fail("size_limit", "%s exceeds the item limit." % field)
    result = []
    seen = set()
    for index, item in enumerate(value):
        item = _text(item, "%s[%d]" % (field, index), code)
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(sorted(result))


def _reasons(value, field, code):
    if not isinstance(value, list):
        _fail(code, "%s must be a list." % field)
    return tuple(_text(item, "%s[%d]" % (field, index), code)
                 for index, item in enumerate(value))


def _normalize_rule(rule):
    code = "invalid_rule"
    if not isinstance(rule, dict):
        _fail(code, "Each rule must be a dictionary.")
    required = ("id", "source_rule_id", "requirement_id", "project_id",
                "statement", "source", "citations", "selector", "effect",
                "conditions", "state", "reasons", "source_issues")
    missing = [field for field in required if field not in rule]
    if missing:
        _fail(code, "Rule is missing required fields: %s." % ", ".join(missing))

    normalized = {
        "id": _identity(rule["id"], "rule.id", code),
        "source_rule_id": _identity(rule["source_rule_id"], "rule.source_rule_id", code),
        "requirement_id": _identity(rule["requirement_id"], "rule.requirement_id", code),
        "project_id": _identity(rule["project_id"], "rule.project_id", code),
        "statement": _text(rule["statement"], "rule.statement", code),
    }
    _json_value(rule["source"], "rule.source", code)
    normalized["source"] = copy.deepcopy(rule["source"])
    if not isinstance(rule["citations"], list):
        _fail(code, "rule.citations must be a list.")
    _json_value(rule["citations"], "rule.citations", code)
    normalized["citations"] = copy.deepcopy(rule["citations"])

    selector = rule["selector"]
    if not isinstance(selector, dict):
        _fail(code, "rule.selector must be a dictionary.")
    if set(selector) != {dimension for dimension, unused in _DIMENSIONS}:
        _fail(code, "rule.selector must contain exactly tags, categories, term_ids, and system_ids.")
    normalized["selector"] = {
        dimension: _string_list(selector[dimension], "rule.selector." + dimension, code)
        for dimension, unused in _DIMENSIONS
    }

    effect = rule["effect"]
    if not isinstance(effect, dict) or not all(key in effect for key in ("key", "polarity", "text")):
        _fail(code, "rule.effect must contain key, polarity, and text.")
    polarity = effect["polarity"]
    if polarity not in _POLARITIES:
        _fail(code, "rule.effect.polarity must be require, prohibit, or unknown.")
    normalized["effect"] = {
        "key": _identity(effect["key"], "rule.effect.key", code),
        "polarity": polarity,
        "text": _text(effect["text"], "rule.effect.text", code),
    }

    conditions = rule["conditions"]
    if not isinstance(conditions, list):
        _fail(code, "rule.conditions must be a list.")
    normalized_conditions = []
    for index, condition in enumerate(conditions):
        if (not isinstance(condition, dict) or
                not all(field in condition for field in ("kind", "text", "source"))):
            _fail(code, "rule.conditions[%d] must contain kind, text, and source." % index)
        _json_value(condition["source"], "rule.conditions[%d].source" % index, code)
        normalized_conditions.append({
            "kind": _identity(condition["kind"], "rule.conditions[%d].kind" % index, code),
            "text": _text(condition["text"], "rule.conditions[%d].text" % index, code),
            "source": copy.deepcopy(condition["source"]),
        })
    normalized["conditions"] = tuple(sorted(
        normalized_conditions,
        key=lambda item: (item["kind"], item["text"], _canonical(item["source"]))))

    if rule["state"] not in _RULE_STATES:
        _fail(code, "rule.state must be supported, needs_context, or unsupported.")
    normalized["state"] = rule["state"]
    normalized["reasons"] = _reasons(rule["reasons"], "rule.reasons", code)
    if not isinstance(rule["source_issues"], list):
        _fail(code, "rule.source_issues must be a list.")
    _json_value(rule["source_issues"], "rule.source_issues", code)
    normalized["source_issues"] = tuple(copy.deepcopy(rule["source_issues"]))
    return normalized


def _normalize_object(obj):
    code = "invalid_object"
    if not isinstance(obj, dict):
        _fail(code, "Each object must be a dictionary.")
    required = ("id", "project_id", "label", "kind", "category", "tags",
                "term_ids", "system_ids", "source_refs", "fact_sources")
    missing = [field for field in required if field not in obj]
    if missing:
        _fail(code, "Object is missing required fields: %s." % ", ".join(missing))
    category = obj["category"]
    if category is not None:
        category = _text(category, "object.category", code)
    if not isinstance(obj["source_refs"], list):
        _fail(code, "object.source_refs must be a list.")
    if not isinstance(obj["fact_sources"], dict):
        _fail(code, "object.fact_sources must be a dictionary.")
    _json_value(obj["source_refs"], "object.source_refs", code)
    _json_value(obj["fact_sources"], "object.fact_sources", code)
    return {
        "id": _identity(obj["id"], "object.id", code),
        "project_id": _identity(obj["project_id"], "object.project_id", code),
        "label": _text(obj["label"], "object.label", code),
        "kind": _identity(obj["kind"], "object.kind", code),
        "category": category,
        "tags": _string_list(obj["tags"], "object.tags", code, allow_none=True),
        "term_ids": _string_list(obj["term_ids"], "object.term_ids", code, allow_none=True),
        "system_ids": _string_list(obj["system_ids"], "object.system_ids", code, allow_none=True),
        "source_refs": copy.deepcopy(obj["source_refs"]),
        "fact_sources": copy.deepcopy(obj["fact_sources"]),
    }


def _source_issue_text(issue):
    if isinstance(issue, str):
        return issue
    return _canonical(issue)


def _append_evidence(values, value):
    if value is None:
        return
    if isinstance(value, list):
        for item in value:
            _append_evidence(values, item)
        return
    values[_canonical(value)] = copy.deepcopy(value)


def _evidence_sources(obj, dimensions):
    values = {}
    for source in obj["source_refs"]:
        _append_evidence(values, source)
    keys = set(dimensions)
    if "categories" in keys:
        keys.add("category")
    for key in sorted(keys):
        if key in obj["fact_sources"]:
            _append_evidence(values, obj["fact_sources"][key])
    return [values[key] for key in sorted(values)]


def _selector_result(rule, obj):
    unknown = []
    constrained = []
    for selector_name, object_name in _DIMENSIONS:
        wanted = rule["selector"][selector_name]
        if not wanted:
            continue
        constrained.append(selector_name)
        known = obj[object_name]
        if known is None:
            unknown.append(selector_name)
            continue
        available = (known,) if selector_name == "categories" else known
        if not set(wanted).intersection(available):
            return "no", (), tuple(constrained)
    if unknown:
        return "unknown", tuple(unknown), tuple(constrained)
    return "yes", (), tuple(constrained)


def _evaluation_id(rule_id, object_id):
    digest = hashlib.sha256(_canonical([rule_id, object_id]).encode("utf-8")).hexdigest()
    return "applicability_evaluation_" + digest


def _conflict_id(object_id, effect_key, rule_ids):
    digest = hashlib.sha256(
        _canonical([object_id, effect_key, rule_ids]).encode("utf-8")).hexdigest()
    return "applicability_conflict_" + digest


def _unassigned(rule, status, reasons):
    return {
        "rule_id": rule["id"],
        "requirement_id": rule["requirement_id"],
        "source": copy.deepcopy(rule["source"]),
        "status": status,
        "reasons": sorted(set(reasons)),
    }


def evaluate(rules, objects, project_id):
    """Evaluate compiled rules against project objects without quantity authority."""
    project_id = _identity(project_id, "project_id", "invalid_project")
    if not isinstance(rules, list):
        _fail("invalid_input", "rules must be a list.")
    if not isinstance(objects, list):
        _fail("invalid_input", "objects must be a list.")
    if len(rules) > MAX_RULES:
        _fail("size_limit", "rules exceeds the %d-rule limit." % MAX_RULES)
    if len(objects) > MAX_OBJECTS:
        _fail("size_limit", "objects exceeds the %d-object limit." % MAX_OBJECTS)
    if len(rules) * len(objects) > MAX_COMPARISONS:
        _fail("size_limit", "Rule/object comparisons exceed the %d-comparison limit." %
              MAX_COMPARISONS)

    normalized_rules = [_normalize_rule(rule) for rule in rules]
    normalized_objects = [_normalize_object(obj) for obj in objects]
    rule_ids = [rule["id"] for rule in normalized_rules]
    object_ids = [obj["id"] for obj in normalized_objects]
    if len(rule_ids) != len(set(rule_ids)):
        _fail("duplicate_identity", "Rule IDs must be unique.")
    if len(object_ids) != len(set(object_ids)):
        _fail("duplicate_identity", "Object IDs must be unique.")
    for rule in normalized_rules:
        if rule["project_id"] != project_id:
            _fail("project_mismatch", "Rule %s belongs to another project." % rule["id"])
    for obj in normalized_objects:
        if obj["project_id"] != project_id:
            _fail("project_mismatch", "Object %s belongs to another project." % obj["id"])

    normalized_rules.sort(key=lambda item: (item["id"], item["source_rule_id"],
                                             item["requirement_id"]))
    normalized_objects.sort(key=lambda item: item["id"])
    evaluations = []
    unassigned = []

    for rule in normalized_rules:
        has_selector = any(rule["selector"][name] for name, unused in _DIMENSIONS)
        if rule["state"] == "unsupported" or not has_selector:
            reasons = list(rule["reasons"])
            if rule["state"] == "unsupported" and not reasons:
                reasons.append("The compiled rule is unsupported.")
            if not has_selector:
                reasons.append("The rule selector has no constrained dimensions.")
            unassigned.append(_unassigned(rule, "unsupported", reasons))
            continue

        possible = 0
        for obj in normalized_objects:
            selector_state, unknown, constrained = _selector_result(rule, obj)
            if selector_state == "no":
                continue
            possible += 1
            reasons = list(rule["reasons"])
            if selector_state == "unknown":
                reasons.extend("Object fact %s is unknown." % name for name in unknown)
            if rule["state"] == "needs_context":
                reasons.append("The compiled rule requires context.")
            if rule["conditions"]:
                reasons.extend("Unresolved condition %s: %s" %
                               (item["kind"], item["text"])
                               for item in rule["conditions"])
            if rule["source_issues"]:
                reasons.extend("Source issue: %s" % _source_issue_text(issue)
                               for issue in rule["source_issues"])

            if rule["source_issues"]:
                status = "source_blocked"
            elif (selector_state == "unknown" or rule["state"] == "needs_context" or
                  rule["conditions"] or rule["effect"]["polarity"] == "unknown"):
                status = "needs_context"
                if rule["effect"]["polarity"] == "unknown":
                    reasons.append("The rule effect polarity is unknown.")
            else:
                status = "matched"
            if len(evaluations) >= MAX_EVALUATIONS:
                _fail("size_limit", "Applicability output exceeds the %d-evaluation limit." %
                      MAX_EVALUATIONS)
            evaluations.append({
                "id": _evaluation_id(rule["id"], obj["id"]),
                "rule_id": rule["id"],
                "source_rule_id": rule["source_rule_id"],
                "requirement_id": rule["requirement_id"],
                "object_id": obj["id"],
                "object_label": obj["label"],
                "status": status,
                "reasons": sorted(set(reasons)),
                "source": copy.deepcopy(rule["source"]),
                "evidence_sources": _evidence_sources(obj, constrained),
                "effect": copy.deepcopy(rule["effect"]),
                "quantity_authority": "none",
            })
        if possible == 0:
            reasons = list(rule["reasons"])
            reasons.append("No project object can satisfy the rule selector from known facts.")
            unassigned.append(_unassigned(rule, "unassigned", reasons))

    by_rule = {rule["id"]: rule for rule in normalized_rules}
    groups = {}
    for evaluation in evaluations:
        if evaluation["status"] != "matched":
            continue
        rule = by_rule[evaluation["rule_id"]]
        if rule["effect"]["polarity"] not in ("require", "prohibit"):
            continue
        key = (evaluation["object_id"], rule["effect"]["key"])
        groups.setdefault(key, []).append(evaluation)

    conflicts = []
    for (object_id, effect_key), group in sorted(groups.items()):
        polarities = {item["effect"]["polarity"] for item in group}
        if polarities != {"require", "prohibit"}:
            continue
        if len(conflicts) >= MAX_CONFLICTS:
            _fail("size_limit", "Conflict output exceeds the %d-conflict limit." % MAX_CONFLICTS)
        rule_ids = sorted(item["rule_id"] for item in group)
        ordered_group = sorted(group, key=lambda item: item["rule_id"])
        for item in ordered_group:
            item["status"] = "conflict"
            item["reasons"] = sorted(set(item["reasons"] + [
                "Opposing supported rules select this object and effect."
            ]))
        label = group[0]["object_label"]
        conflicts.append({
            "id": _conflict_id(object_id, effect_key, rule_ids),
            "object_id": object_id,
            "object_label": label,
            "effect_key": effect_key,
            "rule_ids": rule_ids,
            "sources": [copy.deepcopy(item["source"]) for item in ordered_group],
            "message": "Opposing require and prohibit rules need an evidence-linked decision.",
        })

    evaluations.sort(key=lambda item: (item["rule_id"], item["object_id"], item["id"]))
    unassigned.sort(key=lambda item: (item["rule_id"], item["requirement_id"]))
    summary = {
        "matched": sum(item["status"] == "matched" for item in evaluations),
        "needs_context": sum(item["status"] == "needs_context" for item in evaluations),
        "source_blocked": sum(item["status"] == "source_blocked" for item in evaluations),
        "unsupported": sum(item["status"] == "unsupported" for item in unassigned),
        "conflicts": len(conflicts),
        "unassigned_rules": len(unassigned),
    }
    return {
        "version": VERSION,
        "evaluations": evaluations,
        "conflicts": conflicts,
        "unassigned_rules": unassigned,
        "summary": summary,
        "quantity_authority": "none",
    }
