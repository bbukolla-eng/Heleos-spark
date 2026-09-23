"""Compile explicit written requirements into conservative mechanical scope.

This parser recognizes only the bounded clause forms documented below.  It does
not promote candidate rules, infer contract precedence, or calculate quantities.
Unknown or unconsumed wording remains visible and cannot produce a match-all
selector.
"""
import copy
import hashlib
import importlib.util
import json
import re
from pathlib import Path


VERSION = "mechanical-rule-scope-1"


class ScopeError(ValueError):
    """A stored rule and its source requirement cannot be compiled safely."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _module(name):
    spec = importlib.util.spec_from_file_location(
        "heleos_scope_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


taxonomy = _module("mechanical_taxonomy")

_SPACE_DASH = re.compile(r"[\s\u00a0\u2000-\u200a\u202f\u205f\u3000\-\u2010-\u2015]+")
_TAG_TEXT = r"[A-Z][A-Z0-9]{0,5}-\d{1,5}[A-Z]?"
_TAG_LIST = re.compile(
    r"^(?P<first>" + _TAG_TEXT + r")"
    r"(?P<middle>(?:\s*,\s*" + _TAG_TEXT + r")*)"
    r"(?:\s*,?\s+and\s+(?P<last>" + _TAG_TEXT + r"))?$"
)
_TAG = re.compile(_TAG_TEXT)
_LIST_LABEL = re.compile(r"^(?P<marker>[1-9]\d{0,2}[.)][ \t]+)(?P<body>\S.*)$", re.DOTALL)
_NEUTRAL_HEADINGS = frozenset({"MECHANICAL REQUIREMENTS"})
_CONDITION_START = re.compile(
    r"(?:,\s*|\s+)(?P<condition>"
    r"where\b|unless\b|if\b|except\b|when\b|subject\s+to\b|"
    r"as\s+(?:required|indicated|shown|noted)\b|"
    r"(?:see|refer\s+to)\b|per\s+(?:note|detail|section|drawing|sheet|schedule)\b|"
    r"in\s+accordance\s+with\b|"
    r"(?:in|at|on|within|inside|outside|above|below)\b)",
    re.IGNORECASE,
)
_REFERENCE_START = re.compile(
    r"^(?:see\b|refer\s+to\b|per\s+(?:note|detail|section|drawing|sheet|schedule)\b|"
    r"in\s+accordance\s+with\b)", re.IGNORECASE)
_CONDITIONAL_START = re.compile(
    r"^(?:where\b|unless\b|if\b|except\b|when\b|subject\s+to\b|"
    r"as\s+(?:required|indicated|shown|noted)\b)", re.IGNORECASE)
_LOCATION_START = re.compile(
    r"^(?:in|at|on|within|inside|outside|above|below)\b", re.IGNORECASE)


def _packed(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ScopeError("invalid_input", "Rule scope inputs must be JSON-compatible: %s" % error)


def _text(value, field, code="invalid_input"):
    if not isinstance(value, str) or not value:
        raise ScopeError(code, "%s must be a nonempty string." % field)
    return value


def _source(value, field="source"):
    if not isinstance(value, dict):
        raise ScopeError("source_mismatch", "%s must be a source dictionary." % field)
    return copy.deepcopy(value)


def _locator(source):
    if not isinstance(source.get("revision_id"), str):
        raise ScopeError("source_mismatch", "Requirement source has no revision identity.")
    index = source.get("index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ScopeError("source_mismatch", "Requirement source has no page identity.")
    location = "revision:%s/page:%s" % (source["revision_id"], index + 1)
    if source.get("bbox") is not None:
        location += "/bbox:" + json.dumps(source["bbox"], separators=(",", ":"))
    return location


def _validate_source_identity(requirement):
    source = _source(requirement.get("source"), "requirement.source")
    lines = requirement.get("source_lines")
    if not isinstance(lines, list) or not lines:
        raise ScopeError("source_mismatch", "Requirement source lines are missing.")
    if any(not isinstance(line, dict) or not isinstance(line.get("text"), str)
           or not isinstance(line.get("source"), dict) for line in lines):
        raise ScopeError("source_mismatch", "Requirement source lines are invalid.")
    if requirement["text"] != "\n".join(line["text"] for line in lines):
        raise ScopeError("source_statement_mismatch",
                         "Requirement text no longer matches its source lines.")
    for line in lines:
        line_source = line["source"]
        for field in ("revision_id", "index", "sheet_id"):
            if line_source.get(field) != source.get(field):
                raise ScopeError("source_mismatch",
                                 "Requirement source lines belong to another source.")
    boxes = [line["source"].get("bbox") for line in lines]
    if all(isinstance(box, (list, tuple)) and len(box) == 4 for box in boxes):
        union = [min(box[0] for box in boxes), min(box[1] for box in boxes),
                 max(box[2] for box in boxes), max(box[3] for box in boxes)]
        if source.get("bbox") != union:
            raise ScopeError("source_mismatch",
                             "Requirement source does not enclose its source lines.")
    matches = requirement.get("knowledge", {}).get("matches", [])
    if not isinstance(matches, list):
        raise ScopeError("source_mismatch", "Requirement knowledge matches must be a list.")
    for match in matches:
        if not isinstance(match, dict):
            raise ScopeError("source_mismatch", "Requirement knowledge match is invalid.")
        start, end = match.get("start"), match.get("end")
        if (isinstance(start, bool) or isinstance(end, bool) or
                not isinstance(start, int) or not isinstance(end, int) or
                start < 0 or end <= start or end > len(requirement["text"]) or
                requirement["text"][start:end] != match.get("text")):
            raise ScopeError("source_mismatch",
                             "Requirement knowledge positions no longer match its text.")
    return source


def _validate_identity(rule, requirement):
    if not isinstance(rule, dict) or not isinstance(requirement, dict):
        raise ScopeError("invalid_input", "Rule and requirement must be dictionaries.")
    rule_id = _text(rule.get("id"), "rule.id")
    requirement_id = _text(requirement.get("id"), "requirement.id")
    statement = _text(requirement.get("text"), "requirement.text")
    payload = rule.get("payload")
    if not isinstance(payload, dict):
        raise ScopeError("invalid_rule", "Stored rule payload must be a dictionary.")
    if payload.get("statement") != statement:
        raise ScopeError("statement_mismatch",
                         "Stored rule statement does not match the source requirement.")
    if payload.get("source_requirement_id") != requirement_id:
        raise ScopeError("requirement_mismatch",
                         "Stored rule belongs to another source requirement.")
    conditions = payload.get("conditions", {})
    if not isinstance(conditions, dict):
        raise ScopeError("invalid_rule", "Stored rule conditions must be a dictionary.")
    original = conditions.get("original_statement")
    if original is not None and original != statement:
        raise ScopeError("source_statement_mismatch",
                         "Stored original statement does not match the source requirement.")
    knowledge = requirement.get("knowledge")
    if not isinstance(knowledge, dict) or knowledge.get("rule_id") != rule_id:
        raise ScopeError("requirement_mismatch",
                         "Requirement knowledge does not identify the stored rule.")
    applicability = payload.get("applicability")
    if not isinstance(applicability, dict):
        raise ScopeError("invalid_rule", "Stored rule applicability must be a dictionary.")
    project_id = payload.get("project_id") or applicability.get("project_id")
    project_id = _text(project_id, "rule project_id", "invalid_rule")
    applicability_project = applicability.get("project_id")
    if applicability_project is not None and applicability_project != project_id:
        raise ScopeError("project_mismatch", "Stored rule project identities disagree.")
    if requirement.get("project_id") is not None and requirement["project_id"] != project_id:
        raise ScopeError("project_mismatch", "Requirement belongs to another project.")
    source_tags = requirement.get("tags", [])
    linked_tags = requirement.get("schedule_tag_links", [])
    if (not isinstance(source_tags, list) or not isinstance(linked_tags, list) or
            any(not isinstance(value, str) for value in source_tags + linked_tags)):
        raise ScopeError("source_mismatch", "Requirement equipment tags are invalid.")
    stored_tags = applicability.get("equipment_tags")
    if stored_tags is not None:
        if (not isinstance(stored_tags, list) or
                any(not isinstance(value, str) for value in stored_tags)):
            raise ScopeError("invalid_rule", "Stored rule equipment tags are invalid.")
        if sorted(set(stored_tags)) != sorted(set(source_tags + linked_tags)):
            raise ScopeError("source_mismatch",
                             "Stored rule equipment tags do not match the source requirement.")
    citations = payload.get("citations")
    if not isinstance(citations, list) or not citations:
        raise ScopeError("invalid_rule", "Stored rule citations must be a nonempty list.")
    if any(not isinstance(citation, dict) or
           not isinstance(citation.get("source_id"), str) or
           not isinstance(citation.get("locator"), str) for citation in citations):
        raise ScopeError("invalid_rule", "Stored rule citation is invalid.")
    source = _validate_source_identity(requirement)
    if citations[0]["locator"] != _locator(source):
        raise ScopeError("source_mismatch",
                         "Stored rule citation does not locate the source requirement.")
    source_ids = knowledge.get("source_ids")
    if source_ids is not None:
        if not isinstance(source_ids, list) or any(not isinstance(value, str) for value in source_ids):
            raise ScopeError("source_mismatch", "Requirement source identities are invalid.")
        if any(citation["source_id"] not in source_ids for citation in citations):
            raise ScopeError("source_mismatch", "Stored rule cites another source.")
    return rule_id, requirement_id, project_id, statement, source, copy.deepcopy(citations)


def _normalize_alias(value):
    return _SPACE_DASH.sub(" ", value.strip()).casefold()


def _catalogue(value):
    current = taxonomy.catalogue()
    data = current if value is None else copy.deepcopy(value)
    if not isinstance(data, dict) or not isinstance(data.get("terms"), list) or not isinstance(
            data.get("systems"), list):
        raise ScopeError("invalid_catalogue", "Mechanical catalogue must contain terms and systems.")
    terms = []
    for term in data["terms"]:
        if (not isinstance(term, dict) or not isinstance(term.get("id"), str) or
                not isinstance(term.get("category"), str) or
                not isinstance(term.get("aliases", []), list)):
            raise ScopeError("invalid_catalogue", "Mechanical catalogue contains an invalid term.")
        aliases = list(term.get("aliases", []))
        if isinstance(term.get("label"), str):
            aliases.append(term["label"])
        if any(not isinstance(alias, str) or not alias for alias in aliases):
            raise ScopeError("invalid_catalogue", "Mechanical catalogue contains an invalid alias.")
        configured_systems = term.get("system_ids", [])
        if (not isinstance(configured_systems, list) or
                any(not isinstance(system_id, str) for system_id in configured_systems)):
            raise ScopeError("invalid_catalogue",
                             "Mechanical catalogue contains invalid term system identities.")
        terms.append(dict(term, _aliases={_normalize_alias(alias) for alias in aliases}))
    system_ids = set()
    for system in data["systems"]:
        if not isinstance(system, dict) or not isinstance(system.get("id"), str):
            raise ScopeError("invalid_catalogue", "Mechanical catalogue contains an invalid system.")
        system_ids.add(system["id"])
    return terms, system_ids, _packed(data) == _packed(current)


def _term(text, category, terms):
    normalized = _normalize_alias(text)
    found = sorted({term["id"] for term in terms
                    if term["category"] == category and normalized in term["_aliases"]})
    return found[0] if len(found) == 1 else None


def _systems(text, term_id, terms, system_ids, current_catalogue):
    explicit = set()
    for term in terms:
        if term["id"] == term_id:
            configured = term.get("system_ids", [])
            if isinstance(configured, list):
                explicit.update(value for value in configured if value in system_ids)
    if explicit:
        return sorted(explicit), False
    if not current_catalogue:
        # A stored catalogue without term -> system relationships cannot borrow
        # mappings from changed live code.  The parsed term remains exact, while
        # service applicability stays unresolved.
        return [], True
    classified = taxonomy.classify(text)
    if term_id in classified["term_ids"]:
        explicit.update(value for value in classified["system_ids"] if value in system_ids)
    return sorted(explicit), False


def _condition_kind(text):
    if _REFERENCE_START.match(text):
        return "reference"
    if _LOCATION_START.match(text):
        return "location"
    if _CONDITIONAL_START.match(text):
        return "conditional"
    return "qualification"


def _split_condition(statement):
    match = _CONDITION_START.search(statement)
    if match is None:
        return statement.rstrip(".; "), None
    start = match.start("condition")
    base = statement[:match.start()].rstrip(" ,.;")
    condition = statement[start:].strip()
    return base, condition


def _parse_tags(value):
    match = _TAG_LIST.fullmatch(value.strip())
    if match is None:
        return None
    tags = _TAG.findall(value.upper())
    return list(dict.fromkeys(tags)) if tags else None


def _unknown(statement, reason, source_context=None):
    return ({"tags": [], "categories": [], "term_ids": [], "system_ids": []},
            {"key": "unknown", "polarity": "unknown", "text": statement},
            "unsupported", [reason],
            [{"kind": "unconsumed", "text": statement}], list(source_context or []))


def _compile_statement(statement, terms, system_ids, current_catalogue):
    body = statement.strip()
    source_context = []
    labelled = _LIST_LABEL.fullmatch(body)
    if labelled:
        source_context.append({"kind": "list_marker", "text": labelled.group("marker")})
        body = labelled.group("body")
    base, condition = _split_condition(body)
    condition_rows = [] if condition is None else [
        {"kind": _condition_kind(condition), "text": condition}]

    match = re.fullmatch(r"(Provide|Install|Furnish)\s+(.+?)\s+for\s+(.+)",
                         base, re.IGNORECASE | re.DOTALL)
    if match:
        accessory = match.group(2).strip()
        tags = _parse_tags(match.group(3))
        term_id = _term(accessory, "accessories", terms)
        if term_id and tags:
            return ({"tags": tags, "categories": [], "term_ids": [], "system_ids": []},
                    {"key": term_id, "polarity": "require", "text": accessory},
                    "needs_context" if condition else "supported",
                    ["condition_requires_context"] if condition else [], condition_rows,
                    source_context)
        return _unknown(statement, "unconsumed_text", source_context)

    match = re.fullmatch(r"(?P<negative>Do\s+not\s+)?(?P<verb>Insulate)\s+(?P<scope>.+)",
                         base, re.IGNORECASE | re.DOTALL)
    if match:
        scope = match.group("scope").strip()
        candidates = [(category, _term(scope, category, terms))
                      for category in ("ductwork", "piping")]
        candidates = [(category, term_id) for category, term_id in candidates if term_id]
        if len(candidates) == 1:
            category, term_id = candidates[0]
            systems, mapping_unresolved = _systems(
                scope, term_id, terms, system_ids, current_catalogue)
            needs_context = bool(condition or mapping_unresolved)
            compile_reasons = []
            if condition:
                compile_reasons.append("condition_requires_context")
            if mapping_unresolved:
                compile_reasons.append("catalogue_system_mapping_requires_context")
            return ({"tags": [], "categories": [category], "term_ids": [term_id],
                     "system_ids": systems},
                    {"key": "insulation",
                     "polarity": "prohibit" if match.group("negative") else "require",
                     "text": match.group("verb")},
                    "needs_context" if needs_context else "supported",
                    compile_reasons, condition_rows, source_context)
        return _unknown(statement, "unconsumed_text", source_context)

    match = re.fullmatch(
        r"All\s+(?P<equipment>.+?)\s+shall\s+(?P<negative>not\s+)?have\s+"
        r"(?P<accessory>.+)", base, re.IGNORECASE | re.DOTALL)
    if match:
        equipment = match.group("equipment").strip()
        accessory = match.group("accessory").strip()
        equipment_id = _term(equipment, "equipment", terms)
        accessory_id = _term(accessory, "accessories", terms)
        if equipment_id and accessory_id:
            return ({"tags": [], "categories": ["equipment"],
                     "term_ids": [equipment_id], "system_ids": []},
                    {"key": accessory_id,
                     "polarity": "prohibit" if match.group("negative") else "require",
                     "text": accessory},
                    "needs_context" if condition else "supported",
                    ["condition_requires_context"] if condition else [], condition_rows,
                    source_context)
        return _unknown(statement, "unconsumed_text", source_context)

    reason = "ambiguous_clause" if re.search(r"\bor\b|;", body, re.IGNORECASE) else "unsupported_grammar"
    return _unknown(statement, reason, source_context)


def compile_rule(rule, requirement, catalogue=None):
    """Compile one stored candidate and its exact requirement into JSON data."""
    original_rule = _packed(rule)
    original_requirement = _packed(requirement)
    original_catalogue = None if catalogue is None else _packed(catalogue)
    (rule_id, requirement_id, project_id, statement, source,
     citations) = _validate_identity(rule, requirement)
    terms, system_ids, current_catalogue = _catalogue(catalogue)
    selector, effect, state, reasons, conditions, source_context = _compile_statement(
        statement, terms, system_ids, current_catalogue)

    for condition in conditions:
        condition["source"] = copy.deepcopy(source)
    for item in source_context:
        item["source"] = copy.deepcopy(source)
    context = requirement.get("context", [])
    if not isinstance(context, list):
        raise ScopeError("source_mismatch", "Requirement context must be a list.")
    unresolved_context = False
    for item in context:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ScopeError("source_mismatch", "Requirement context is invalid.")
        item_source = _source(item.get("source"), "requirement.context.source")
        if item["text"] in _NEUTRAL_HEADINGS:
            source_context.append({"kind": "neutral_heading", "text": item["text"],
                                   "source": item_source})
        else:
            unresolved_context = True
            conditions.append({"kind": "context", "text": item["text"],
                               "source": item_source})
    qualifiers = requirement.get("qualifiers", [])
    if not isinstance(qualifiers, list) or any(not isinstance(value, str) for value in qualifiers):
        raise ScopeError("invalid_requirement", "Requirement qualifiers must be strings.")
    rule_issues = rule.get("issues", [])
    if not isinstance(rule_issues, list) or any(not isinstance(value, str) for value in rule_issues):
        raise ScopeError("invalid_rule", "Stored rule issues must be strings.")
    if unresolved_context:
        reasons.append("source_context_requires_resolution")
    unresolved_qualifiers = [
        value for value in qualifiers
        if not (value == "negative" and effect["polarity"] == "prohibit")
    ]
    if unresolved_qualifiers:
        reasons.extend("requirement_qualifier:" + value for value in unresolved_qualifiers)
    if rule.get("status") != "candidate":
        reasons.append("rule_status_not_candidate")
        state = "unsupported"
    # Source lifecycle issues do not change the compiled grammar.  The project
    # context adapter passes these same bytes to the matcher as source_issues,
    # where they yield source_blocked without erasing selector/effect evidence.
    if state == "supported" and (unresolved_context or unresolved_qualifiers):
        state = "needs_context"
    reasons = list(dict.fromkeys(reasons))

    result = {
        "source_rule_id": rule_id,
        "requirement_id": requirement_id,
        "project_id": project_id,
        "statement": statement,
        "source": source,
        "citations": citations,
        "selector": selector,
        "effect": effect,
        "conditions": conditions,
        "source_context": source_context,
        "state": state,
        "reasons": reasons,
    }
    result["id"] = "scope_" + hashlib.sha256(
        ("heleos-mechanical-rule-scope-v1\0").encode("ascii") + _packed(result)).hexdigest()
    if (_packed(rule) != original_rule or _packed(requirement) != original_requirement or
            (catalogue is not None and _packed(catalogue) != original_catalogue)):
        raise ScopeError("input_mutated", "Rule scope compilation changed its input.")
    return result
