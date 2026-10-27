"""Connect current source evidence, item scope and saved project decisions.

Project applicability decisions preserve exceptions and their evidence. They do
not admit global engineering rules, change source facts or approve quantities.
"""
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path


def _module(name):
    spec = importlib.util.spec_from_file_location("heleos_" + name, Path(__file__).with_name(name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


storage = _module("mechanical_rule_decision_store")
resolution = _module("mechanical_rule_resolution")
VERSION = "project-rule-resolution-1"
IMPLEMENTATION = {name: hashlib.sha256(Path(__file__).with_name(name + ".py").read_bytes()).hexdigest()
                  for name in ("project_rule_resolution", "mechanical_rule_decision_store",
                               "mechanical_rule_resolution")}


class ProjectDecisionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(packed(value)).hexdigest()


def locator(source):
    value = "revision:%s/page:%s" % (source["revision_id"], source["index"] + 1)
    if source.get("bbox") is not None:
        value += "/bbox:" + json.dumps(source["bbox"], separators=(",", ":"))
    return value


def source_map(knowledge):
    return {source["metadata"]["locator"]: source for source in knowledge["sources"]}


def page_source(source, sources):
    return sources.get(locator({key: source[key] for key in ("revision_id", "index")}))


def source_refs(value):
    if isinstance(value, dict):
        if "revision_id" in value and type(value.get("index")) is int:
            yield value
        else:
            for part in value.values():
                yield from source_refs(part)
    elif isinstance(value, list):
        for part in value:
            yield from source_refs(part)


def evidence_options(reading, knowledge):
    sources, options = source_map(knowledge), {}

    def add(source, label):
        original = page_source(source, sources)
        if original is None:
            return
        citation = {"source_id": original["id"], "locator": locator(source)}
        identity = "evidence_" + digest(citation)
        options.setdefault(identity, {"id": identity, "label": label,
                                      "source": copy.deepcopy(source), "citation": citation,
                                      "available": original["state"] == "current"})

    for requirement in reading["requirements"]:
        add(requirement["source"], requirement["text"][:180])
        for context in requirement.get("context", []):
            add(context["source"], context["text"][:180])
    for row in reading["schedule_rows"]:
        for field in row["fields"]:
            if field.get("source") and field.get("value"):
                add(field["source"], "%s — %s: %s" % (row["tag"], field["header"], field["value"]))
    for page in reading["pages"]:
        if page.get("state") == "read":
            source = {key: page[key] for key in ("revision_id", "index", "sheet_id")}
            add(source, "%s, page %d" % (page["role"].replace("_", " ").title(), page["index"] + 1))
    return sorted(options.values(), key=lambda option: (option["source"]["revision_id"],
                                                       option["source"]["index"], option["label"], option["id"]))


def historical_citation_source(knowledge_store, citation, project_id):
    """Resolve a saved citation from its immutable page, even after rereading."""
    source = knowledge_store.get_source(citation["source_id"])
    metadata = source["metadata"]
    if metadata["snapshot_kind"] != "positioned_page_text":
        return None
    if metadata["project_id"] != project_id:
        raise ProjectDecisionError("decision_project", "A decision source belongs to another project.")
    page = json.loads(knowledge_store.source_bytes(citation["source_id"]))
    reference = {key: page[key] for key in ("revision_id", "index", "sheet_id")}
    prefix = locator(reference)
    if prefix != metadata["locator"] or page["revision_id"] != metadata["revision_id"]:
        raise ProjectDecisionError("decision_source", "The saved decision page does not match its source.")
    location = citation["locator"]
    if location == prefix:
        return reference
    if not location.startswith(prefix + "/bbox:"):
        raise ProjectDecisionError("decision_source", "A decision citation does not identify its saved page.")
    box = json.loads(location[len(prefix + "/bbox:"):])
    if (not isinstance(box, list) or len(box) != 4 or
            any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1 for value in box) or
            box[0] >= box[2] or box[1] >= box[3]):
        raise ProjectDecisionError("decision_source", "The saved decision location is invalid.")
    return dict(reference, bbox=box)


def build_view(knowledge_store, reading, knowledge, applicability):
    if applicability is None or knowledge is None or reading is None or reading.get("stale"):
        return None, []
    try:
        decisions = storage.DecisionStore(knowledge_store)
        records = decisions.list(reading["project_id"])
        sources = source_map(knowledge)
        vocabulary = knowledge["taxonomy_source_id"]
        pins = {pin["source_id"]: pin for pin in decisions.source_pins(
            sorted({source["id"] for source in knowledge["sources"]} | {vocabulary}))}
        compiled = {rule["id"]: rule for rule in applicability["compiled_rules"]}
        objects = {obj["id"]: obj for obj in applicability["objects"]}
        heads = {}
        for record in records:
            payload = record["payload"]
            key = payload["source_rule_id"], payload["object_id"]
            if key not in heads or heads[key]["sequence"] < record["sequence"]:
                heads[key] = record
        targets = []
        for evaluation in applicability["evaluations"]:
            rule, obj = compiled[evaluation["rule_id"]], objects[evaluation["object_id"]]
            relevant = {citation["source_id"] for citation in rule["citations"]} | {vocabulary}
            missing = False
            for reference in source_refs([obj["source_refs"], obj["fact_sources"]]):
                source = page_source(reference, sources)
                if source is None:
                    missing = True
                else:
                    relevant.add(source["id"])
            target = resolution.make_target(evaluation, rule, obj, reading["id"],
                [pins[key] for key in sorted(relevant)],
                dict(applicability["implementation"], **IMPLEMENTATION))
            target["latest_decision_id"] = heads.get((rule["source_rule_id"], obj["id"]), {}).get("id")
            target["can_decide"] = bool(target["can_decide"] and not missing)
            targets.append(target)
        result = resolution.resolve(applicability, targets, records)
    except (storage.DecisionError, resolution.ResolutionError) as error:
        raise ProjectDecisionError(error.code, error.message) from None
    options = evidence_options(reading, knowledge)
    by_citation = {packed(option["citation"]): option["source"] for option in options}
    try:
        for record in result["decision_records"]:
            payload = record["payload"]
            rule = knowledge_store.get_rule(payload["source_rule_id"])
            record["requirement_text"] = rule["payload"]["statement"]
            record["object_label"] = objects.get(payload["object_id"], {}).get("label", payload["object_id"])
            record["citation_sources"] = []
            for citation in payload["citations"]:
                source = by_citation.get(packed(citation))
                if source is None:
                    source = historical_citation_source(knowledge_store, citation, reading["project_id"])
                if source is not None:
                    record["citation_sources"].append(copy.deepcopy(source))
    except (ValueError, KeyError, TypeError) as error:
        raise ProjectDecisionError(getattr(error, "code", "decision_source"),
                                   getattr(error, "message", "The saved decision evidence could not be read.")) from None
    result["resolution_implementation"] = copy.deepcopy(IMPLEMENTATION)
    result["state_fingerprint"] = digest(result)
    return result, options


def record_decision(knowledge_store, reading, knowledge, applicability, values, actor, reason):
    if set(values) != {"evaluation_id", "binding_sha256", "disposition", "evidence_ids", "supersedes"}:
        raise ProjectDecisionError("decision_fields", "Provide the item decision, current context and supporting evidence.")
    current, options = build_view(knowledge_store, reading, knowledge, applicability)
    if current is None:
        raise ProjectDecisionError("decision_reading", "Read the current document set before resolving an item requirement.")
    target = next((target for target in current["targets"] if target["evaluation_id"] == values["evaluation_id"]), None)
    if target is None:
        raise ProjectDecisionError("decision_target", "This requirement/item result is no longer current.")
    if values["binding_sha256"] != target["binding_sha256"]:
        raise ProjectDecisionError("decision_stale", "The requirement, source or item changed. Refresh before saving.")
    if values["supersedes"] != target["latest_decision_id"]:
        raise ProjectDecisionError("decision_stale", "A newer decision exists for this item. Refresh before saving.")
    disposition = values["disposition"]
    if disposition not in ("applies", "excludes", "defer", "withdraw"):
        raise ProjectDecisionError("decision_disposition", "Choose applies, does not apply, keep unresolved, or withdraw.")
    if disposition in ("applies", "excludes") and not target["can_decide"]:
        raise ProjectDecisionError("decision_unsupported", "Resolve the missing source or unsupported wording before confirming this requirement.")
    evidence_ids = values["evidence_ids"]
    if (not isinstance(evidence_ids, list) or len(evidence_ids) > 32 or
            any(not isinstance(key, str) for key in evidence_ids) or len(set(evidence_ids)) != len(evidence_ids)):
        raise ProjectDecisionError("decision_evidence", "Choose up to 32 distinct evidence locations from this reading.")
    available = {option["id"]: option for option in options}
    if any(key not in available for key in evidence_ids):
        raise ProjectDecisionError("decision_evidence", "The selected evidence is not in this document reading.")
    citations = [available[key]["citation"] for key in sorted(evidence_ids)]
    try:
        decisions = storage.DecisionStore(knowledge_store)
        extra = decisions.source_pins(sorted({citation["source_id"] for citation in citations}))
        pins = {pin["source_id"]: pin for pin in target["source_pins"]}
        for pin in extra:
            if pin["source_id"] in pins and pin != pins[pin["source_id"]]:
                raise ProjectDecisionError("decision_stale", "A source changed while saving. Refresh before trying again.")
            pins[pin["source_id"]] = pin
        payload = {key: target[key] for key in ("project_id", "document_run_id", "requirement_id",
            "source_rule_id", "compiled_rule_id", "object_id", "binding_sha256")}
        payload.update(kind="project_applicability", disposition=disposition, actor=actor, reason=reason,
                       citations=citations, source_pins=[pins[key] for key in sorted(pins)],
                       supersedes=values["supersedes"])
        return decisions.record(payload)
    except storage.DecisionError as error:
        raise ProjectDecisionError(error.code, error.message) from None
