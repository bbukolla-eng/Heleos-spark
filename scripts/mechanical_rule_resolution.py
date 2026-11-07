"""Apply project applicability decisions as a deterministic, reversible overlay.

The functions in this module do not read storage, approve engineering rules, or
create quantities. Callers supply verified immutable decision records and source
pins; every effective decision stays bound to its exact rule, object, and run.
"""
import copy
import hashlib
import json
import re


VERSION = "mechanical-rule-resolution-1"
MAX_TARGETS = 100_000
MAX_DECISIONS = 20_000
MAX_ITEMS = 100_000
MAX_STRUCTURED_ITEMS = 200_000
MAX_DEPTH = 24
MAX_TEXT = 10_000

_SHA256 = re.compile(r"[0-9a-f]{64}")
_BASE_STATUSES = ("matched", "needs_context", "source_blocked", "unsupported", "conflict")
_DISPOSITIONS = ("applies", "excludes", "defer", "withdraw")
_PIN_STATES = ("current", "superseded", "retired")
_TARGET_FIELDS = {
    "evaluation_id", "source_rule_id", "compiled_rule_id", "requirement_id",
    "object_id", "object_label", "project_id", "document_run_id",
    "binding_sha256", "source_pins", "base_status", "can_decide",
}
_TARGET_OPTIONAL_FIELDS = {"latest_decision_id"}
_PAYLOAD_FIELDS = {
    "kind", "project_id", "document_run_id", "requirement_id",
    "source_rule_id", "compiled_rule_id", "object_id", "binding_sha256",
    "disposition", "actor", "reason", "citations", "source_pins", "supersedes",
}


class ResolutionError(ValueError):
    """A stable, caller-readable resolution failure."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _fail(code, message):
    raise ResolutionError(code, message)


def _text(value, field, code="invalid_input", limit=MAX_TEXT):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail(code, "%s must be a nonempty bounded string." % field)
    return value


def _identity(value, field, code="invalid_input"):
    value = _text(value, field, code, 512)
    if value != value.strip():
        _fail(code, "%s must not have outer whitespace." % field)
    return value


def _hash(value, field, code="invalid_input"):
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(code, "%s must be a lowercase SHA-256 digest." % field)
    return value


def _json_value(value, field, code="invalid_input", depth=0, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_STRUCTURED_ITEMS or depth > MAX_DEPTH:
        _fail("size_limit", "%s exceeds the structured-data limit." % field)
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
            _fail("size_limit", "%s contains too many items." % field)
        for item in value:
            _json_value(item, field, code, depth + 1, count)
        return
    if isinstance(value, dict):
        if len(value) > MAX_STRUCTURED_ITEMS:
            _fail("size_limit", "%s contains too many fields." % field)
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                _fail(code, "%s contains an invalid field name." % field)
            _json_value(item, field, code, depth + 1, count)
        return
    _fail(code, "%s must contain only JSON values." % field)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sort_json(values):
    return sorted((copy.deepcopy(value) for value in values), key=_canonical)


def _pins(value, field, code):
    if not isinstance(value, list):
        _fail(code, "%s must be a list." % field)
    if len(value) > MAX_ITEMS:
        _fail("size_limit", "%s contains too many pins." % field)
    pins = []
    seen = set()
    for index, pin in enumerate(value):
        name = "%s[%d]" % (field, index)
        if not isinstance(pin, dict) or set(pin) != {
                "source_id", "snapshot_sha256", "lifecycle_sequence", "state"}:
            _fail(code, "%s must contain source_id, snapshot_sha256, lifecycle_sequence, and state." % name)
        source_id = _identity(pin["source_id"], name + ".source_id", code)
        if source_id in seen:
            _fail("duplicate_identity", "%s contains duplicate source pins." % field)
        seen.add(source_id)
        sequence = pin["lifecycle_sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            _fail(code, "%s.lifecycle_sequence must be a positive integer." % name)
        if pin["state"] not in _PIN_STATES:
            _fail(code, "%s.state is invalid." % name)
        pins.append({
            "source_id": source_id,
            "snapshot_sha256": _hash(pin["snapshot_sha256"], name + ".snapshot_sha256", code),
            "lifecycle_sequence": sequence,
            "state": pin["state"],
        })
    return sorted(pins, key=lambda pin: pin["source_id"])


def _unordered_binding_inputs(compiled_rule, obj, evaluation):
    """Copy binding inputs while normalizing only set-like JSON collections."""
    compiled = copy.deepcopy(compiled_rule)
    selector = compiled.get("selector")
    if isinstance(selector, dict):
        for field in ("tags", "categories", "term_ids", "system_ids"):
            if isinstance(selector.get(field), list):
                selector[field] = _sort_json(selector[field])
    for field in ("citations", "conditions", "source_context", "reasons", "source_issues"):
        if isinstance(compiled.get(field), list):
            compiled[field] = _sort_json(compiled[field])

    bound_object = copy.deepcopy(obj)
    for field in ("tags", "term_ids", "system_ids", "source_refs", "context_issues"):
        if isinstance(bound_object.get(field), list):
            bound_object[field] = _sort_json(bound_object[field])
    facts = bound_object.get("fact_sources")
    if isinstance(facts, dict):
        for field, values in facts.items():
            if isinstance(values, list):
                facts[field] = _sort_json(values)

    bound_evaluation = copy.deepcopy(evaluation)
    for field in ("reasons", "evidence_sources"):
        if isinstance(bound_evaluation.get(field), list):
            bound_evaluation[field] = _sort_json(bound_evaluation[field])
    return compiled, bound_object, bound_evaluation


def _validate_evaluation(evaluation):
    code = "invalid_evaluation"
    if not isinstance(evaluation, dict):
        _fail(code, "evaluation must be a dictionary.")
    required = ("id", "rule_id", "source_rule_id", "requirement_id", "object_id",
                "object_label", "status", "source", "effect", "quantity_authority")
    missing = [field for field in required if field not in evaluation]
    if missing:
        _fail(code, "Evaluation is missing required fields: %s." % ", ".join(missing))
    for field in ("id", "rule_id", "source_rule_id", "requirement_id", "object_id"):
        _identity(evaluation[field], "evaluation." + field, code)
    _text(evaluation["object_label"], "evaluation.object_label", code)
    if evaluation["status"] not in _BASE_STATUSES:
        _fail(code, "evaluation.status is not a raw applicability status.")
    if evaluation["quantity_authority"] != "none":
        _fail(code, "An applicability evaluation must have no quantity authority.")
    effect = evaluation["effect"]
    if not isinstance(effect, dict) or not all(field in effect for field in ("key", "polarity", "text")):
        _fail(code, "evaluation.effect must contain key, polarity, and text.")
    _identity(effect["key"], "evaluation.effect.key", code)
    if effect["polarity"] not in ("require", "prohibit", "unknown"):
        _fail(code, "evaluation.effect.polarity is invalid.")
    _text(effect["text"], "evaluation.effect.text", code)
    _json_value(evaluation, "evaluation", code)


def _validate_rule(compiled_rule):
    code = "invalid_rule"
    if not isinstance(compiled_rule, dict):
        _fail(code, "compiled_rule must be a dictionary.")
    required = ("id", "source_rule_id", "requirement_id", "project_id", "source",
                "selector", "effect", "state", "source_issues")
    missing = [field for field in required if field not in compiled_rule]
    if missing:
        _fail(code, "Compiled rule is missing required fields: %s." % ", ".join(missing))
    for field in ("id", "source_rule_id", "requirement_id", "project_id"):
        _identity(compiled_rule[field], "compiled_rule." + field, code)
    if compiled_rule["state"] not in ("supported", "needs_context", "unsupported"):
        _fail(code, "compiled_rule.state is invalid.")
    selector = compiled_rule["selector"]
    if not isinstance(selector, dict) or set(selector) != {
            "tags", "categories", "term_ids", "system_ids"}:
        _fail(code, "compiled_rule.selector is invalid.")
    for field, values in selector.items():
        if not isinstance(values, list) or any(not isinstance(value, str) or not value
                                               for value in values):
            _fail(code, "compiled_rule.selector.%s must contain strings." % field)
    effect = compiled_rule["effect"]
    if not isinstance(effect, dict) or not all(field in effect for field in ("key", "polarity", "text")):
        _fail(code, "compiled_rule.effect is invalid.")
    _identity(effect["key"], "compiled_rule.effect.key", code)
    if effect["polarity"] not in ("require", "prohibit", "unknown"):
        _fail(code, "compiled_rule.effect.polarity is invalid.")
    _text(effect["text"], "compiled_rule.effect.text", code)
    if not isinstance(compiled_rule["source_issues"], list):
        _fail(code, "compiled_rule.source_issues must be a list.")
    _json_value(compiled_rule, "compiled_rule", code)


def _validate_object(obj):
    code = "invalid_object"
    if not isinstance(obj, dict):
        _fail(code, "obj must be a dictionary.")
    required = ("id", "project_id", "label", "kind", "category", "tags", "term_ids",
                "system_ids", "source_refs", "fact_sources")
    missing = [field for field in required if field not in obj]
    if missing:
        _fail(code, "Object is missing required fields: %s." % ", ".join(missing))
    for field in ("id", "project_id", "kind"):
        _identity(obj[field], "object." + field, code)
    _text(obj["label"], "object.label", code)
    if obj["category"] is not None:
        _text(obj["category"], "object.category", code)
    for field in ("tags", "term_ids", "system_ids"):
        values = obj[field]
        if values is not None and (not isinstance(values, list) or any(
                not isinstance(value, str) or not value for value in values)):
            _fail(code, "object.%s must be a string list or null." % field)
    if not isinstance(obj["source_refs"], list) or not isinstance(obj["fact_sources"], dict):
        _fail(code, "Object source_refs and fact_sources are invalid.")
    _json_value(obj, "object", code)


def make_target(evaluation, compiled_rule, obj, document_run_id, source_pins,
                implementation):
    """Bind a raw applicability row to its exact current evidence and code."""
    _validate_evaluation(evaluation)
    _validate_rule(compiled_rule)
    _validate_object(obj)
    document_run_id = _identity(document_run_id, "document_run_id")
    pins = _pins(source_pins, "source_pins", "invalid_source_pins")
    if not isinstance(implementation, dict) or not implementation:
        _fail("invalid_implementation", "implementation must be a nonempty dictionary.")
    _json_value(implementation, "implementation", "invalid_implementation")

    agreements = (
        (evaluation["rule_id"], compiled_rule["id"], "compiled rule"),
        (evaluation["source_rule_id"], compiled_rule["source_rule_id"], "source rule"),
        (evaluation["requirement_id"], compiled_rule["requirement_id"], "requirement"),
        (evaluation["object_id"], obj["id"], "object"),
        (evaluation["object_label"], obj["label"], "object label"),
    )
    for left, right, name in agreements:
        if left != right:
            _fail("identity_mismatch", "Evaluation and binding %s identities disagree." % name)
    if compiled_rule["project_id"] != obj["project_id"]:
        _fail("project_mismatch", "Compiled rule and object belong to different projects.")
    if _canonical(evaluation["effect"]) != _canonical(compiled_rule["effect"]):
        _fail("identity_mismatch", "Evaluation and compiled rule effects disagree.")
    if _canonical(evaluation["source"]) != _canonical(compiled_rule["source"]):
        _fail("identity_mismatch", "Evaluation and compiled rule sources disagree.")
    if compiled_rule["source_issues"] and evaluation["status"] != "source_blocked":
        _fail("identity_mismatch", "A rule with source issues must be source-blocked.")

    effect = compiled_rule["effect"]
    known_effect = (effect.get("polarity") in ("require", "prohibit") and
                    effect.get("key") != "unknown")
    can_decide = (compiled_rule["state"] in ("supported", "needs_context") and
                  evaluation["status"] in ("matched", "needs_context", "conflict") and
                  not compiled_rule["source_issues"] and known_effect)
    compiled, bound_object, bound_evaluation = _unordered_binding_inputs(
        compiled_rule, obj, evaluation)
    binding = {
        "compiled_rule": compiled,
        "object": bound_object,
        "evaluation": bound_evaluation,
        "document_run_id": document_run_id,
        "source_pins": pins,
        "implementation": copy.deepcopy(implementation),
    }
    return {
        "evaluation_id": evaluation["id"],
        "source_rule_id": compiled_rule["source_rule_id"],
        "compiled_rule_id": compiled_rule["id"],
        "requirement_id": compiled_rule["requirement_id"],
        "object_id": obj["id"],
        "object_label": obj["label"],
        "project_id": obj["project_id"],
        "document_run_id": document_run_id,
        "binding_sha256": _digest(binding),
        "source_pins": copy.deepcopy(pins),
        "base_status": evaluation["status"],
        "can_decide": can_decide,
    }


def _validate_target(target):
    code = "invalid_target"
    if (not isinstance(target, dict) or not _TARGET_FIELDS.issubset(target) or
            set(target) - _TARGET_FIELDS - _TARGET_OPTIONAL_FIELDS):
        _fail(code, "Each target must contain exactly the resolution target fields.")
    for field in ("evaluation_id", "source_rule_id", "compiled_rule_id", "requirement_id",
                  "object_id", "project_id", "document_run_id"):
        _identity(target[field], "target." + field, code)
    _text(target["object_label"], "target.object_label", code)
    _hash(target["binding_sha256"], "target.binding_sha256", code)
    target["source_pins"] = _pins(target["source_pins"], "target.source_pins", code)
    if target["base_status"] not in _BASE_STATUSES:
        _fail(code, "target.base_status is invalid.")
    if type(target["can_decide"]) is not bool:
        _fail(code, "target.can_decide must be a boolean.")
    if "latest_decision_id" in target and target["latest_decision_id"] is not None:
        _identity(target["latest_decision_id"], "target.latest_decision_id", code)
    return target


def _citations(value, field, code):
    if not isinstance(value, list):
        _fail(code, "%s must be a list." % field)
    result = []
    for index, citation in enumerate(value):
        if not isinstance(citation, dict) or set(citation) != {"source_id", "locator"}:
            _fail(code, "%s[%d] must contain source_id and locator." % (field, index))
        result.append({
            "source_id": _identity(citation["source_id"], "%s[%d].source_id" % (field, index), code),
            "locator": _text(citation["locator"], "%s[%d].locator" % (field, index), code),
        })
    return _sort_json(result)


def _validate_decision(record):
    code = "invalid_decision"
    if not isinstance(record, dict) or set(record) != {
            "id", "payload", "sequence", "recorded_at", "issues"}:
        _fail(code, "Each decision must contain id, payload, sequence, recorded_at, and issues.")
    decision = copy.deepcopy(record)
    _identity(decision["id"], "decision.id", code)
    sequence = decision["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        _fail(code, "decision.sequence must be a positive integer.")
    _text(decision["recorded_at"], "decision.recorded_at", code)
    if not isinstance(decision["issues"], list) or any(
            not isinstance(issue, str) or not issue for issue in decision["issues"]):
        _fail(code, "decision.issues must contain strings.")
    payload = decision["payload"]
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_FIELDS:
        _fail(code, "decision.payload must contain exactly the project applicability fields.")
    if payload["kind"] != "project_applicability":
        _fail(code, "decision.payload.kind is invalid.")
    for field in ("project_id", "document_run_id", "requirement_id", "source_rule_id",
                  "compiled_rule_id", "object_id", "actor"):
        _identity(payload[field], "decision.payload." + field, code)
    _text(payload["reason"], "decision.payload.reason", code)
    _hash(payload["binding_sha256"], "decision.payload.binding_sha256", code)
    if payload["disposition"] not in _DISPOSITIONS:
        _fail(code, "decision.payload.disposition is invalid.")
    payload["citations"] = _citations(payload["citations"], "decision.payload.citations", code)
    payload["source_pins"] = _pins(payload["source_pins"], "decision.payload.source_pins", code)
    if payload["disposition"] in ("applies", "excludes") and not payload["citations"]:
        _fail(code, "Applies and excludes decisions require a citation.")
    pinned_sources = {pin["source_id"] for pin in payload["source_pins"]}
    unpinned_citations = sorted({citation["source_id"] for citation in payload["citations"]}
                                - pinned_sources)
    if unpinned_citations:
        _fail(code, "Every cited decision source must have a source pin: %s." %
              ", ".join(unpinned_citations))
    if payload["supersedes"] is not None:
        _identity(payload["supersedes"], "decision.payload.supersedes", code)
    if payload["disposition"] == "withdraw" and payload["supersedes"] is None:
        _fail(code, "A withdrawal must supersede the current decision.")
    _json_value(decision, "decision", code)
    return decision


def _decision_key(record):
    payload = record["payload"]
    return payload["project_id"], payload["source_rule_id"], payload["object_id"]


def _decision_staleness(record, target):
    payload = record["payload"]
    reasons = list(record["issues"])
    comparisons = (
        (payload["binding_sha256"], target["binding_sha256"], "binding_changed"),
        (payload["compiled_rule_id"], target["compiled_rule_id"], "compiled_rule_changed"),
        (payload["requirement_id"], target["requirement_id"], "requirement_changed"),
        (payload["document_run_id"], target["document_run_id"], "document_run_changed"),
    )
    reasons.extend(reason for left, right, reason in comparisons if left != right)
    recorded_pins = {pin["source_id"]: pin for pin in payload["source_pins"]}
    target_pins = {pin["source_id"]: pin for pin in target["source_pins"]}
    for source_id, required in sorted(target_pins.items()):
        recorded = recorded_pins.get(source_id)
        if recorded is None:
            reasons.append("required_source_pin_missing:" + source_id)
        elif _canonical(recorded) != _canonical(required):
            reasons.append("required_source_pin_changed:" + source_id)
    for source_id, recorded in sorted(recorded_pins.items()):
        if source_id not in target_pins and recorded["state"] != "current":
            reasons.append("additional_source_not_current:" + source_id)
    if payload["disposition"] in ("applies", "excludes") and not target["can_decide"]:
        reasons.append("target_not_decidable")
    return sorted(set(reasons))


def _conflict_id(object_id, effect_key, rule_ids):
    return "applicability_conflict_" + _digest([object_id, effect_key, rule_ids])


def _project_identity(applicability, targets, decisions):
    projects = {target["project_id"] for target in targets}
    projects.update(record["payload"]["project_id"] for record in decisions)
    for rule in applicability.get("compiled_rules", []):
        if isinstance(rule, dict) and isinstance(rule.get("project_id"), str):
            projects.add(rule["project_id"])
    for obj in applicability.get("objects", []):
        if isinstance(obj, dict) and isinstance(obj.get("project_id"), str):
            projects.add(obj["project_id"])
    if len(projects) > 1:
        _fail("project_mismatch", "Resolution inputs span multiple projects.")
    return next(iter(projects), None)


def resolve(applicability, targets, decisions):
    """Return effective applicability plus complete, status-labelled history."""
    if not isinstance(applicability, dict):
        _fail("invalid_input", "applicability must be a dictionary.")
    if not isinstance(targets, list) or not isinstance(decisions, list):
        _fail("invalid_input", "targets and decisions must be lists.")
    if len(targets) > MAX_TARGETS or len(decisions) > MAX_DECISIONS:
        _fail("size_limit", "Resolution input exceeds the target or decision limit.")
    _json_value(applicability, "applicability")
    if applicability.get("quantity_authority") != "none":
        _fail("invalid_input", "Applicability must have no quantity authority.")
    evaluations = applicability.get("evaluations")
    if not isinstance(evaluations, list) or len(evaluations) > MAX_ITEMS:
        _fail("invalid_input", "applicability.evaluations must be a bounded list.")

    normalized_targets = [_validate_target(copy.deepcopy(target)) for target in targets]
    normalized_decisions = [_validate_decision(record) for record in decisions]
    target_ids = [target["evaluation_id"] for target in normalized_targets]
    target_keys = [(target["project_id"], target["source_rule_id"], target["object_id"])
                   for target in normalized_targets]
    decision_ids = [record["id"] for record in normalized_decisions]
    sequences = [record["sequence"] for record in normalized_decisions]
    if len(target_ids) != len(set(target_ids)) or len(target_keys) != len(set(target_keys)):
        _fail("duplicate_identity", "Resolution targets must have unique evaluation and target identities.")
    if len(decision_ids) != len(set(decision_ids)) or len(sequences) != len(set(sequences)):
        _fail("duplicate_identity", "Decision IDs and sequences must be unique.")
    _project_identity(applicability, normalized_targets, normalized_decisions)

    raw_by_id = {}
    for raw in evaluations:
        _validate_evaluation(raw)
        if raw["id"] in raw_by_id:
            _fail("duplicate_identity", "Applicability evaluation IDs must be unique.")
        raw_by_id[raw["id"]] = raw
    if set(target_ids) != set(raw_by_id):
        _fail("identity_mismatch", "Each applicability evaluation must have exactly one current target.")
    targets_by_evaluation = {target["evaluation_id"]: target for target in normalized_targets}
    targets_by_key = {key: target for key, target in zip(target_keys, normalized_targets)}
    for evaluation_id, target in targets_by_evaluation.items():
        raw = raw_by_id[evaluation_id]
        comparisons = (
            (raw["rule_id"], target["compiled_rule_id"]),
            (raw["source_rule_id"], target["source_rule_id"]),
            (raw["requirement_id"], target["requirement_id"]),
            (raw["object_id"], target["object_id"]),
            (raw["object_label"], target["object_label"]),
            (raw["status"], target["base_status"]),
        )
        if any(left != right for left, right in comparisons):
            _fail("identity_mismatch", "A resolution target no longer identifies its evaluation.")

    histories = {}
    for record in sorted(normalized_decisions, key=lambda item: (item["sequence"], item["id"])):
        histories.setdefault(_decision_key(record), []).append(record)
    for history in histories.values():
        for index, record in enumerate(history):
            expected = None if index == 0 else history[index - 1]["id"]
            if record["payload"]["supersedes"] != expected:
                _fail("inconsistent_head", "Decision history does not form one current head per target.")
    for key, target in targets_by_key.items():
        if "latest_decision_id" not in target:
            continue
        history = histories.get(key, [])
        actual = history[-1]["id"] if history else None
        if target["latest_decision_id"] != actual:
            _fail("inconsistent_head", "A target's persisted decision head disagrees with its history.")

    decorated = []
    decorated_by_id = {}
    latest_by_target = {}
    stale_latest = 0
    deferred_latest = 0
    for key, history in histories.items():
        target = targets_by_key.get(key)
        for index, record in enumerate(history):
            current = index == len(history) - 1
            enriched = copy.deepcopy(record)
            if target is None:
                status = "orphaned"
                resolution_issues = ["current_target_missing"]
            elif not current:
                status = "superseded"
                resolution_issues = []
            else:
                resolution_issues = _decision_staleness(record, target)
                if resolution_issues:
                    status = "stale"
                    stale_latest += 1
                elif record["payload"]["disposition"] == "defer":
                    status = "deferred"
                    deferred_latest += 1
                elif record["payload"]["disposition"] == "withdraw":
                    status = "withdrawn"
                else:
                    status = "current"
                latest_by_target[key] = enriched
            enriched["resolution_state"] = status
            enriched["resolution_issues"] = resolution_issues
            decorated.append(enriched)
            decorated_by_id[enriched["id"]] = enriched
            if target is not None and current:
                latest_by_target[key] = enriched
    decorated.sort(key=lambda item: (item["sequence"], item["id"]))

    effective = []
    for raw in evaluations:
        target = targets_by_evaluation[raw["id"]]
        row = copy.deepcopy(raw)
        row["base_status"] = target["base_status"]
        row["decision_id"] = None
        row["decision"] = None
        provisional = "matched" if target["base_status"] == "conflict" else target["base_status"]
        current = latest_by_target.get(
            (target["project_id"], target["source_rule_id"], target["object_id"]))
        if current is not None:
            row["decision_id"] = current["id"]
            row["decision"] = copy.deepcopy(decorated_by_id[current["id"]])
            disposition = current["payload"]["disposition"]
            if current["resolution_state"] == "stale":
                if target["base_status"] not in ("source_blocked", "unsupported"):
                    provisional = "needs_context"
                row["reasons"] = sorted(set(row.get("reasons", []) + [
                    "Stored applicability decision is stale: " +
                    ", ".join(current["resolution_issues"])
                ]))
            elif current["resolution_state"] == "deferred":
                if target["base_status"] not in ("source_blocked", "unsupported"):
                    provisional = "needs_context"
                row["reasons"] = sorted(set(row.get("reasons", []) + [
                    "Applicability decision deferred: " + current["payload"]["reason"]
                ]))
            elif current["resolution_state"] == "withdrawn":
                row["reasons"] = sorted(set(row.get("reasons", []) + [
                    "Prior applicability decision withdrawn: " + current["payload"]["reason"]
                ]))
            elif disposition == "applies":
                provisional = "confirmed_applies"
                row["reasons"] = sorted(set(row.get("reasons", []) + [
                    "Applicability confirmed: " + current["payload"]["reason"]
                ]))
            elif disposition == "excludes":
                provisional = "confirmed_excluded"
                row["reasons"] = sorted(set(row.get("reasons", []) + [
                    "Applicability excluded: " + current["payload"]["reason"]
                ]))
        row["status"] = provisional
        effective.append(row)

    groups = {}
    for row in effective:
        if row["status"] not in ("matched", "confirmed_applies"):
            continue
        effect = row["effect"]
        if effect.get("polarity") not in ("require", "prohibit"):
            continue
        groups.setdefault((row["object_id"], effect["key"]), []).append(row)
    conflicts = []
    for (object_id, effect_key), rows in sorted(groups.items()):
        if {row["effect"]["polarity"] for row in rows} != {"require", "prohibit"}:
            continue
        rows.sort(key=lambda row: (row["rule_id"], row["id"]))
        rule_ids = [row["rule_id"] for row in rows]
        decision_ids = sorted(row["decision_id"] for row in rows if row["decision_id"])
        for row in rows:
            row["status"] = "conflict"
            row["reasons"] = sorted(set(row.get("reasons", []) + [
                "Opposing non-excluded rules select this object and effect."
            ]))
        conflicts.append({
            "id": _conflict_id(object_id, effect_key, rule_ids),
            "object_id": object_id,
            "object_label": rows[0]["object_label"],
            "effect_key": effect_key,
            "rule_ids": rule_ids,
            "sources": [copy.deepcopy(row["source"]) for row in rows],
            "decision_ids": decision_ids,
            "message": "Opposing require and prohibit rules need an evidence-linked decision.",
        })

    effective.sort(key=lambda row: (row["rule_id"], row["object_id"], row["id"]))
    normalized_targets.sort(key=lambda target: (
        target["source_rule_id"], target["object_id"], target["evaluation_id"]))
    result = copy.deepcopy(applicability)
    base_fingerprint = result.pop("state_fingerprint", None)
    if base_fingerprint is not None:
        result["base_state_fingerprint"] = base_fingerprint
    result["resolution_version"] = VERSION
    result["evaluations"] = effective
    result["conflicts"] = conflicts
    result["targets"] = normalized_targets
    result["decision_records"] = decorated
    for field, key in (("objects", "id"), ("compiled_rules", "id"),
                       ("unassigned_rules", "rule_id")):
        if isinstance(result.get(field), list):
            result[field] = sorted(result[field], key=lambda item: item.get(key, ""))
    original_summary = applicability.get("summary")
    if not isinstance(original_summary, dict):
        _fail("invalid_input", "applicability.summary must be a dictionary.")
    summary = copy.deepcopy(original_summary)
    current_applies = sum(
        row["decision"] is not None and
        row["decision"]["resolution_state"] == "current" and
        row["decision"]["payload"]["disposition"] == "applies"
        for row in effective)
    current_excludes = sum(
        row["decision"] is not None and
        row["decision"]["resolution_state"] == "current" and
        row["decision"]["payload"]["disposition"] == "excludes"
        for row in effective)
    summary.update({
        "matched": sum(row["status"] == "matched" for row in effective),
        "needs_context": sum(row["status"] == "needs_context" for row in effective),
        "source_blocked": sum(row["status"] == "source_blocked" for row in effective),
        "conflicts": len(conflicts),
        "unassigned_rules": len(result.get("unassigned_rules", [])),
        "confirmed_applies": current_applies,
        "confirmed_excluded": current_excludes,
        "stale_decisions": stale_latest,
        "deferred_decisions": deferred_latest,
    })
    result["summary"] = summary
    result["quantity_authority"] = "none"
    result["state_fingerprint"] = _digest(result)
    return result
