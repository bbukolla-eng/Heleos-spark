"""Apply compiled source requirements to the project's known mechanical context.

Equipment records are not physical counts. Candidate scope matches are not rule
admission or quantities. Every known selector fact retains where it came from.
"""
import copy
import hashlib
import importlib.util
import json
import re
from pathlib import Path


def _module(name):
    spec = importlib.util.spec_from_file_location("heleos_" + name, Path(__file__).with_name(name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


scope = _module("mechanical_rule_scope")
matcher = _module("mechanical_rule_applicability")
taxonomy = _module("mechanical_taxonomy")
VERSION = "mechanical-rule-context-1"
IMPLEMENTATION = {name: hashlib.sha256(Path(__file__).with_name(name + ".py").read_bytes()).hexdigest()
                  for name in ("mechanical_rule_context", "mechanical_rule_scope",
                               "mechanical_rule_applicability", "mechanical_taxonomy")}


class ContextError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(packed(value)).hexdigest()


def _sources(values):
    by_identity = {packed(value): copy.deepcopy(value) for value in values if value}
    return [by_identity[key] for key in sorted(by_identity)]


def _known_ids(value, catalogue, category=None):
    """A field supplies a fact only when its entire value names one saved term.

    Lexical matches are useful reading hints, but 'not a fan' and 'supply or
    exhaust air' must not supply confident selector facts. Read saved aliases,
    never the live vocabulary, so reopened readings keep their original meaning.
    """
    normalize = lambda text: re.sub(r"[\s-]+", " ", text.strip()).casefold()
    value = normalize(value or "")
    entries = catalogue["terms"] if category else catalogue["systems"]
    found = {entry["id"] for entry in entries
             if (category is None or entry["category"] == category) and value and
             value in {normalize(alias) for alias in entry.get("aliases", []) + [entry["label"]]}}
    return sorted(found) if len(found) == 1 else None


def _terms(value, category, catalogue):
    terms = set(_known_ids(value, catalogue, category) or [])
    # Specific duct/pipe forms retain their generic parent for a generic selector.
    available = {term["id"] for term in catalogue["terms"] if term["category"] == category}
    if any(term.startswith("ductwork.") for term in terms) and "ductwork.duct" in available:
        terms.add("ductwork.duct")
    if any(term.startswith("piping.") for term in terms) and "piping.pipe" in available:
        terms.add("piping.pipe")
    return sorted(terms) or None


def _agree(values):
    """Conflicting or missing schedule facts cannot become a union of answers."""
    if not values or any(value is None for value in values):
        return None
    return copy.deepcopy(values[0]) if all(value == values[0] for value in values) else None


def build_objects(reading, items, catalogue):
    project_id = reading["project_id"]
    known_revisions = {source["revision_id"] for source in reading["sources"]}
    rows = {row["id"]: row for row in reading["schedule_rows"]}
    objects = []
    for record in reading["equipment_register"]:
        schedules = [rows[key] for key in record["schedule_row_ids"] if key in rows]
        plan_sources = [hit["source"] for hit in record["plan_occurrences"]]
        source_refs = _sources(plan_sources + [row["source"] for row in schedules])
        type_facts, type_sources, system_facts, system_sources = [], [], [], []
        for row in schedules:
            type_facts.append(_terms(row.get("equipment_type"), "equipment", catalogue))
            explicit_type = [field for field in row["fields"]
                             if field["name"] == "equipment_type" and field.get("value")]
            type_sources.extend(field.get("source") for field in (explicit_type or [
                field for field in row["fields"] if field["name"] == "schedule_title"]))
            services = [field for field in row["fields"] if field["name"] == "system" and field.get("value")]
            service_values = [_known_ids(field["value"], catalogue) for field in services]
            system_facts.append(_agree(service_values))
            system_sources.extend(field.get("source") for field in services)
        objects.append({
            "id": "equipment:" + record["tag"], "project_id": project_id,
            "label": record["tag"], "kind": "equipment", "category": "equipment",
            "tags": [record["tag"]], "term_ids": _agree(type_facts),
            "system_ids": _agree(system_facts), "source_refs": source_refs,
            "fact_sources": {"category": source_refs, "tags": source_refs,
                             "term_ids": _sources(type_sources), "system_ids": _sources(system_sources)},
            "context_issues": list(record["issues"]),
        })
    for item in items:
        if item["source"]["revision_id"] not in known_revisions:
            continue
        source = copy.deepcopy(item["source"])
        fact = dict(source=source, basis="saved_project_item", item_id=item["id"])
        objects.append({
            "id": "item:" + item["id"], "project_id": project_id,
            "label": item["description"], "kind": "takeoff_item", "category": item["scope"],
            "tags": None, "term_ids": _terms(item["description"], item["scope"], catalogue),
            "system_ids": _known_ids(item.get("system"), catalogue), "source_refs": [source],
            "fact_sources": {"category": [dict(fact, field="scope")],
                             "term_ids": [dict(fact, field="description")],
                             "system_ids": [dict(fact, field="system")], "tags": []},
            "context_issues": [],
        })
    return sorted(objects, key=lambda obj: obj["id"])


def evaluate_project(reading, knowledge, items):
    if (reading["state"] != "completed" or reading.get("stale") or knowledge is None):
        return None
    requirements = {requirement["id"]: requirement for requirement in reading["requirements"]}
    compiled = []
    for rule in knowledge["rules"]:
        requirement = requirements.get(rule["payload"]["source_requirement_id"])
        if requirement is None:
            raise ContextError("rule_source_missing", "A mechanical rule is missing its source requirement.")
        if rule["payload"].get("project_id") != reading["project_id"]:
            raise ContextError("rule_project_mismatch", "A mechanical rule belongs to another project.")
        try:
            compiled_rule = scope.compile_rule(rule, requirement, knowledge["catalogue"])
        except scope.ScopeError as error:
            raise ContextError(error.code, error.message) from None
        compiled_rule["source_issues"] = list(rule["issues"])
        if knowledge["vocabulary_source"]["state"] != "current":
            compiled_rule["source_issues"].append("vocabulary_source_not_current")
        compiled.append(compiled_rule)
    objects = build_objects(reading, items, knowledge["catalogue"])
    try:
        result = matcher.evaluate(compiled, objects, reading["project_id"])
    except matcher.ApplicabilityError as error:
        raise ContextError(error.code, error.message) from None
    result.update(context_version=VERSION, objects=objects, compiled_rules=compiled,
                  implementation=copy.deepcopy(IMPLEMENTATION),
                  document_run_id=reading["id"], knowledge_state=knowledge["state_fingerprint"])
    result["state_fingerprint"] = digest(result)
    return result


def unresolved_issues(result):
    if result is None:
        return []
    issues = []
    for conflict in result["conflicts"]:
        issues.append({"id": "rule-conflict:" + conflict["id"],
                       "message": conflict["object_label"] + ": " + conflict["message"],
                       "source": next(iter(conflict["sources"]), None),
                       "state": "open", "automatic": True})
    groups = {}
    for row in result["evaluations"]:
        if row["status"] not in ("matched", "conflict", "confirmed_applies", "confirmed_excluded"):
            key = row["requirement_id"], row["status"]
            groups.setdefault(key, []).append(row)
    for (requirement_id, status), rows in sorted(groups.items()):
        message = ("Requirement source is unavailable for " if status == "source_blocked"
                   else "Requirement needs more context for ")
        issues.append({"id": "rule-context:" + requirement_id + ":" + status,
                       "message": message +
                                  ", ".join(row["object_label"] for row in rows[:3]) +
                                  (" and other items" if len(rows) > 3 else "") +
                                  ". Inspect the requirement source and item details.",
                       "source": rows[0]["source"], "state": "open", "automatic": True})
    for row in result["unassigned_rules"]:
        message = ("Requirement wording is not supported yet; its item scope remains unresolved."
                   if row["status"] == "unsupported" else
                   "No matching mechanical item was found for this requirement; its scope remains unresolved.")
        issues.append({"id": "rule-unassigned:" + row["rule_id"],
                       "message": message,
                       "source": row["source"], "state": "open", "automatic": True})
    return issues
