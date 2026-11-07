"""Bind tested rule versions and explicit lifecycle decisions to this project.

Admission here covers the compiled requirement applicability only. Quantity
calculations require their own implemented and validated rule scope.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path


def _module(name):
    spec = importlib.util.spec_from_file_location("heleos_" + name, Path(__file__).with_name(name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


storage = _module("mechanical_rule_admission_store")
pins_store = _module("mechanical_rule_decision_store")
validation = _module("mechanical_rule_validation")
VERSION = "project-rule-admission-1"


class AdmissionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(packed(value)).hexdigest()


def _scope(compiled):
    return dict(purpose="requirement_applicability", compiled_rule_id=compiled["id"],
                compiled_sha256=digest(compiled), status=compiled["state"],
                **{key: copy.deepcopy(compiled[key]) for key in ("selector", "effect", "conditions")})


def _targets(knowledge_store, reading, knowledge, applicability):
    sources = pins_store.DecisionStore(knowledge_store)
    candidates = {rule["id"]: rule for rule in knowledge["rules"]}
    requirements = {row["id"]: row for row in reading["requirements"]}
    all_sources = {knowledge["taxonomy_source_id"]}
    all_sources.update(citation["source_id"] for rule in candidates.values() for citation in rule["payload"]["citations"])
    pin_map = {pin["source_id"]: pin for pin in sources.source_pins(sorted(all_sources))}
    result = {}
    for compiled in applicability["compiled_rules"]:
        rule = candidates[compiled["source_rule_id"]]
        source_ids = {citation["source_id"] for citation in rule["payload"]["citations"]}
        source_ids.add(knowledge["taxonomy_source_id"])
        pins = [pin_map[source_id] for source_id in sorted(source_ids)]
        scope = _scope(compiled)
        binding = digest([rule, scope, pins, validation.IMPLEMENTATION])
        result[rule["id"]] = {
            "rule_id": rule["id"], "requirement_id": compiled["requirement_id"],
            "statement": rule["payload"]["statement"],
            "source": copy.deepcopy(requirements[compiled["requirement_id"]]["source"]),
            "project_id": reading["project_id"], "scope": scope,
            "source_pins": pins, "implementation": copy.deepcopy(validation.IMPLEMENTATION),
            "binding_sha256": binding, "compiled": copy.deepcopy(compiled),
        }
    return result


def build_view(knowledge_store, reading, knowledge, applicability):
    if applicability is None or knowledge is None or reading is None or reading.get("stale"):
        return None
    try:
        store = storage.RuleAdmissionStore(knowledge_store)
        versions, events = store.versions(reading["project_id"]), store.events(reading["project_id"])
        targets = _targets(knowledge_store, reading, knowledge, applicability)
        by_rule, events_by_rule = {}, {}
        for record in versions:
            by_rule.setdefault(record["payload"]["rule_id"], []).append(record)
        for record in events:
            events_by_rule.setdefault(record["payload"]["rule_id"], []).append(record)
        rules, decorated = [], []
        for rule_id, target in sorted(targets.items()):
            history = sorted(events_by_rule.get(rule_id, []), key=lambda row: row["sequence"])
            lifecycle = [row for row in history if row["payload"]["action"] in ("approve", "withdraw")]
            active_id = (lifecycle[-1]["payload"]["version_id"]
                         if lifecycle and lifecycle[-1]["payload"]["action"] == "approve" else None)
            current_versions = []
            for record in sorted(by_rule.get(rule_id, []), key=lambda row: row["sequence"]):
                version = copy.deepcopy(record)
                payload = version["payload"]
                issues = list(version["issues"])
                for key in ("scope", "source_pins", "implementation"):
                    if packed(payload[key]) != packed(target[key]):
                        issues.append(key + "_changed")
                current = not any(issue != "validation_failed" and not issue.startswith("scope_not_supported:")
                                  for issue in issues)
                reviewed = [event for event in history if event["payload"]["version_id"] == version["id"]
                            and event["payload"]["action"] in ("review_pass", "review_reject")]
                passed = payload["validation"]["passed"] and payload["scope"]["status"] == "supported"
                status = "validated" if passed else "validation_failed"
                if payload["scope"]["status"] != "supported":
                    status = "unsupported"
                if reviewed:
                    status = "review_passed" if reviewed[-1]["payload"]["action"] == "review_pass" else "review_rejected"
                promoted = any(event["payload"]["action"] == "approve" and
                               event["payload"]["version_id"] == version["id"] for event in history)
                if promoted:
                    status = "approved" if version["id"] == active_id else "superseded"
                    if lifecycle and lifecycle[-1]["payload"]["action"] == "withdraw" and lifecycle[-1]["payload"]["version_id"] == version["id"]:
                        status = "withdrawn"
                if not current:
                    status = "stale"
                allowed = []
                if current and passed and version["id"] != active_id:
                    allowed.extend(("review_pass", "review_reject"))
                    if reviewed and reviewed[-1]["payload"]["action"] == "review_pass":
                        allowed.append("approve")
                if version["id"] == active_id:
                    allowed.append("withdraw")
                version.update(status=status, issues=sorted(set(issues)), current=current, allowed_actions=allowed)
                current_versions.append(version)
                decorated.append(version)
            active = next((version for version in current_versions
                           if version["id"] == active_id and version["status"] == "approved"), None)
            status = "approved" if active else (current_versions[-1]["status"] if current_versions else "candidate")
            if not current_versions and (target["scope"]["status"] != "supported" or
                                        any(pin["state"] != "current" for pin in target["source_pins"])):
                status = "unsupported" if target["scope"]["status"] != "supported" else "stale"
            row = {key: copy.deepcopy(value) for key, value in target.items() if key != "compiled"}
            row.update(status=status, latest_event_id=history[-1]["id"] if history else None,
                       active_version_id=active["id"] if active else None, versions=current_versions, events=history)
            rules.append(row)
        known = {version["id"] for version in decorated}
        for record in versions:
            if record["id"] not in known:
                decorated.append(dict(copy.deepcopy(record), status="outside_current_reading", current=False, allowed_actions=[]))
        result = {"schema": VERSION, "rules": rules,
                  "versions": sorted(decorated, key=lambda row: row["sequence"]), "events": events,
                  "summary": {state: sum(row["status"] == state for row in rules)
                              for state in ("candidate", "validated", "approved", "stale")},
                  "approved_rule_ids": sorted(row["rule_id"] for row in rules if row["active_version_id"]),
                  "quantity_authority": "none", "purpose": "requirement_applicability"}
        result["state_fingerprint"] = digest(result)
        return result
    except (storage.RuleAdmissionError, pins_store.DecisionError, validation.ValidationError) as error:
        raise AdmissionError(error.code, error.message) from None


def _current(knowledge_store, reading, knowledge, applicability, values):
    current = build_view(knowledge_store, reading, knowledge, applicability)
    if current is None:
        raise AdmissionError("rule_reading", "Read the current documents before changing a rule version.")
    rule = next((rule for rule in current["rules"] if rule["rule_id"] == values["rule_id"]), None)
    if rule is None:
        raise AdmissionError("rule_missing", "This rule is no longer in the current reading.")
    if values["binding_sha256"] != rule["binding_sha256"]:
        raise AdmissionError("rule_stale", "The rule, source or implementation changed. Refresh before saving.")
    return rule


def prepare_version(knowledge_store, reading, knowledge, applicability, values, actor, reason):
    if set(values) != {"rule_id", "binding_sha256", "cases"}:
        raise AdmissionError("rule_fields", "Provide the current rule and independently specified check cases.")
    rule = _current(knowledge_store, reading, knowledge, applicability, values)
    compiled = next(row for row in applicability["compiled_rules"] if row["source_rule_id"] == rule["rule_id"])
    try:
        report = validation.evaluate_cases(compiled, values["cases"])
        payload = {key: rule[key] for key in ("project_id", "rule_id", "scope", "source_pins", "implementation")}
        payload.update(author=actor, reason=reason, validation=report, rollback={"action": "disable_rule"})
        return storage.RuleAdmissionStore(knowledge_store).prepare(payload)
    except (storage.RuleAdmissionError, validation.ValidationError) as error:
        raise AdmissionError(error.code, error.message) from None


def record_event(knowledge_store, reading, knowledge, applicability, values, actor, reason):
    if set(values) != {"rule_id", "binding_sha256", "version_id", "action", "supersedes"}:
        raise AdmissionError("rule_fields", "Provide the current rule version, lifecycle action and history head.")
    rule = _current(knowledge_store, reading, knowledge, applicability, values)
    if values["supersedes"] != rule["latest_event_id"]:
        raise AdmissionError("rule_stale", "A newer rule decision exists. Refresh before saving.")
    version = next((version for version in rule["versions"] if version["id"] == values["version_id"]), None)
    if version is None or values["action"] not in version["allowed_actions"]:
        raise AdmissionError("rule_action", "This rule version is not ready for the selected lifecycle action.")
    payload = {key: values[key] for key in ("rule_id", "version_id", "action", "supersedes")}
    payload.update(project_id=rule["project_id"], actor=actor, reason=reason)
    try:
        return storage.RuleAdmissionStore(knowledge_store).record(payload)
    except storage.RuleAdmissionError as error:
        raise AdmissionError(error.code, error.message) from None
