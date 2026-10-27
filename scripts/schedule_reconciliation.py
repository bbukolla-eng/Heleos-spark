"""Pure, source-bound schedule/plan correspondence and exception decisions.

The caller verifies saved reading artifacts and persists returned generations
and events. Correspondence is never a physical count or quantity approval.
"""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import uuid


VERSION = "schedule-reconciliation-1"
MAX_NODES = 10000
MAX_EDGES = 50000
MAX_DECISIONS = 10000
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_EVENT = re.compile(r"[0-9a-f]{32}\Z")
_IDENTITY = re.compile(r"([A-Z]{1,12})-?([0-9]{1,5}[A-Z]?)\Z")
_DASHES = str.maketrans({chr(number): "-" for number in range(0x2010, 0x2016)})
_VALUE_KEYS = {"action", "group_fingerprint", "schedule_id", "plan_ids", "exclusions", "supersedes"}
_EVENT_KEYS = _VALUE_KEYS | {"id", "project_id", "generation_id", "group_id",
                             "actor", "reason", "at", "fingerprint"}
_UNKNOWN = {"", "-", "--", "TBD", "UNKNOWN", "N/A", "NA", "NOT SHOWN"}
_NON_CORRESPONDENCE_ISSUES = {"quantity_unknown", "quantity_expression_unresolved"}
_READING_IMPLEMENTATIONS = {"document_pipeline", "document_layout", "document_schedule",
                            "equipment_takeoff", "sheet_geometry"}
_GEOMETRY_SPEC = importlib.util.spec_from_file_location(
    "heleos_reconciliation_geometry", Path(__file__).with_name("sheet_geometry.py"))
_GEOMETRY = importlib.util.module_from_spec(_GEOMETRY_SPEC)
_GEOMETRY_SPEC.loader.exec_module(_GEOMETRY)


class ReconciliationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _packed(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise ReconciliationError("reconciliation_invalid", "Reconciliation data must be finite JSON.") from None


def _hash(value):
    return hashlib.sha256(_packed(value)).hexdigest()


def _text(value, label, limit=500, required=False):
    if (not isinstance(value, str) or len(value) > limit or
            any(ord(char) < 32 and char not in "\r\n\t" for char in value)):
        raise ReconciliationError("reconciliation_invalid", "Invalid " + label + ".")
    result = " ".join(value.split())
    if required and not result:
        raise ReconciliationError("reconciliation_invalid", "Provide " + label + ".")
    return result


def _source(value, bounds=False):
    if not isinstance(value, dict):
        raise ReconciliationError("source_invalid", "An endpoint has no page source.")
    source = {key: value.get(key) for key in ("revision_id", "index", "sheet_id")}
    if (any(not isinstance(source[key], str) or not _HEX.fullmatch(source[key])
            for key in ("revision_id", "sheet_id")) or
            type(source["index"]) is not int or not 0 <= source["index"] <= 10 ** 9):
        raise ReconciliationError("source_invalid", "An endpoint has invalid page identities.")
    if bounds:
        box = value.get("bbox")
        if (not isinstance(box, list) or len(box) != 4 or
                any(type(number) not in (int, float) or not math.isfinite(number) for number in box) or
                not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)):
            raise ReconciliationError("source_invalid", "An endpoint has an invalid source rectangle.")
        source["bbox"] = list(box)
    return source


def _key(source):
    return source["revision_id"] + ":" + str(source["index"])


def _tag(value):
    original = _text(value, "equipment tag", 64, required=True)
    normalized = re.sub(r"\s*([-_.])\s*", r"\1", original.upper().translate(_DASHES))
    matched = _IDENTITY.fullmatch(normalized)
    if matched:
        return original, matched[1] + "-" + matched[2], True
    # Preserve unfamiliar declared marks as their own unresolved groups. Never
    # scan numeric page text or silently merge arbitrary marks into known tags.
    return original, normalized, False


def _geometry_matches(geometry, source):
    if not isinstance(geometry, dict):
        return False
    try:
        rebuilt = _GEOMETRY.sheet_geometry(geometry, source["revision_id"])
        return (all(rebuilt[key] == source[key] for key in ("revision_id", "index", "sheet_id")) and
                geometry.get("fingerprint") == rebuilt["fingerprint"] and
                geometry.get("coordinate_space") == rebuilt["coordinate_space"])
    except _GEOMETRY.GeometryError:
        return False


def _artifact_valid(artifact):
    return (isinstance(artifact, dict) and isinstance(artifact.get("sha256"), str) and
            _HEX.fullmatch(artifact["sha256"]) and
            artifact.get("key") == artifact["sha256"] + ".json" and
            type(artifact.get("bytes")) is int and artifact["bytes"] > 0)


def _page_bindings(reading, assignments):
    saved_sources = {}
    for source in reading.get("sources", []):
        identity = _source(source)
        key = _key(identity)
        if key in saved_sources:
            raise ReconciliationError("source_duplicate", "The reading repeats a selected page.")
        saved_sources[key] = copy.deepcopy(source)
    saved_pages = {}
    for page in reading.get("pages", []):
        identity = _source(page)
        key = _key(identity)
        if key in saved_pages:
            raise ReconciliationError("source_duplicate", "The reading repeats a page result.")
        saved_pages[key] = copy.deepcopy(page)
    result = {}
    for key in sorted(set(saved_sources) | set(saved_pages)):
        source, record = saved_sources.get(key), saved_pages.get(key)
        identity = _source(source or record)
        issues = []
        if source is None or record is None:
            issues.append("page_result_missing")
        if record and source and any(record.get(k) != source.get(k)
                                    for k in ("revision_id", "index", "sheet_id", "role")):
            issues.append("page_identity_changed")
        assigned = assignments.get(key)
        if assigned is not None and not isinstance(assigned, dict):
            raise ReconciliationError("assignment_invalid", "Page assignments must be objects.")
        assigned = copy.deepcopy(assigned) if assigned is not None else {}
        available = assigned.get("available", key in assignments)
        if type(available) is not bool:
            raise ReconciliationError("assignment_invalid", "Page availability must be explicit.")
        if not available:
            issues.append("source_unavailable")
        saved_role = (source or record).get("role")
        current_role = assigned.get("current_role", assigned.get("role", saved_role))
        if not isinstance(saved_role, str) or current_role != saved_role:
            issues.append("source_role_changed")
        saved_geometry = (source or {}).get("geometry", (record or {}).get("geometry"))
        current_geometry = assigned.get("current_geometry", saved_geometry)
        if not _geometry_matches(saved_geometry, identity):
            issues.append("source_geometry_unbound")
        if not _geometry_matches(current_geometry, identity):
            issues.append("current_geometry_unavailable")
        elif _packed(saved_geometry) != _packed(current_geometry):
            issues.append("source_geometry_changed")
        layout = (record or {}).get("layout")
        if not _artifact_valid(layout):
            issues.append("layout_evidence_missing")
        if (record or {}).get("state") != "read":
            issues.append("page_not_read")
        metadata = {field: _text(assigned.get(field, ""), "page " + field, 500)
                    for field in ("building", "level", "label")}
        core_identity = assigned.get("reconciliation_implementation_sha256")
        if core_identity is not None and (not isinstance(core_identity, str) or not _HEX.fullmatch(core_identity)):
            raise ReconciliationError("assignment_invalid", "The reconciliation implementation identity is invalid.")
        binding = {
            "source": identity, "saved_role": saved_role, "current_role": current_role,
            "available": available, "geometry": copy.deepcopy(saved_geometry),
            "current_geometry": copy.deepcopy(current_geometry),
            "layout": copy.deepcopy(layout), "artifact": copy.deepcopy((record or {}).get("artifact")),
            "state": (record or {}).get("state"), "assignment": metadata,
            "extraction": {"reader": copy.deepcopy(reading.get("reader")),
                           "implementation": {key: value for key, value in reading.get("implementation", {}).items()
                                              if key in _READING_IMPLEMENTATIONS}},
            "reconciliation_implementation_sha256": core_identity,
            "issues": sorted(set(issues)),
        }
        binding["fingerprint"] = _hash(binding)
        result[key] = binding
    return result


def _node(kind, record, pages, register_tag=None):
    if not isinstance(record, dict):
        raise ReconciliationError("endpoint_invalid", "Reading endpoints must be objects.")
    record_id = _text(record.get("id"), "source record ID", 128, required=True)
    original_tag, tag, supported = _tag(record.get("tag"))
    source = _source(record.get("source"), bounds=True)
    page = pages.get(_key(source))
    if page is None:
        page = {"source": {key: source[key] for key in ("revision_id", "index", "sheet_id")},
                "issues": ["page_result_missing"], "assignment": {}}
    else:
        page = copy.deepcopy(page)
        if source["sheet_id"] != page["source"]["sheet_id"]:
            page["issues"] = sorted(set(page["issues"] + ["page_identity_changed"]))
            page["fingerprint"] = _hash({key: value for key, value in page.items() if key != "fingerprint"})
    issues = list(page["issues"])
    if not supported:
        issues.append("unsupported_tag_identity")
    if register_tag is not None and _tag(register_tag)[1] != tag:
        issues.append("register_tag_mismatch")
    if kind == "plan" and page.get("saved_role") not in ("plan", "detail", "riser"):
        issues.append("plan_source_role_invalid")
    fields = copy.deepcopy(record.get("fields", [])) if kind == "schedule" else []
    if not isinstance(fields, list):
        raise ReconciliationError("endpoint_invalid", "Schedule fields must be a list.")
    for field in fields:
        if not isinstance(field, dict) or not isinstance(field.get("name"), str):
            raise ReconciliationError("endpoint_invalid", "A schedule field is invalid.")
        for key in ("source", "header_source"):
            if field.get(key) is not None:
                located = _source(field[key], bounds=True)
                if any(located[k] != source[k] for k in ("revision_id", "index", "sheet_id")):
                    issues.append("field_source_mismatch")
    if kind == "schedule":
        original_issues = record.get("issues", [])
        if not isinstance(original_issues, list) or any(not isinstance(v, str) for v in original_issues):
            raise ReconciliationError("endpoint_invalid", "Schedule issues must be strings.")
        if any(issue not in _NON_CORRESPONDENCE_ISSUES and issue != "duplicate_schedule_tag"
               for issue in original_issues):
            issues.append("schedule_fields_ambiguous")
        text = " | ".join(str(field.get("header", field["name"])) + ": " +
                          str(field.get("value") if field.get("value") is not None else "UNKNOWN")
                          for field in fields)
    else:
        text = _text(record.get("text", ""), "plan source text", 10000)
    node = {"id": _hash([VERSION, kind, record_id]), "record_id": record_id, "kind": kind,
            "tag": tag, "original_tag": original_tag, "source": source, "text": text,
            "fields": fields, "original": copy.deepcopy(record), "page": page,
            "validity": "blocked" if page["issues"] or "field_source_mismatch" in issues else "current",
            "issues": sorted(set(issues))}
    node["fingerprint"] = _hash(node)
    return node


def _field_conflicts(nodes):
    values = {}
    for node in nodes:
        fields = node["fields"] + [{"name": "equipment_type", "value": node["original"].get("equipment_type")}]
        for field in fields:
            if field["name"] in ("tag", "schedule_title") or field.get("ambiguous"):
                continue
            value = field.get("value")
            if not isinstance(value, str):
                continue
            value = " ".join(value.upper().split())
            if value in _UNKNOWN:
                continue
            if field["name"] == "quantity":
                value = re.sub(r"\s*(?:EA\.?|EACH|UNITS?|NOS?\.?)\Z", "", value).strip()
            values.setdefault(field["name"], set()).add(value)
    return any(len(values) > 1 for values in values.values())


def _group_issues(schedule, plans):
    result = []
    if not schedule:
        result.append("missing_schedule")
    if not plans:
        result.append("missing_plan")
    if len(schedule) > 1:
        result.append("duplicate_schedule")
    if len(plans) > 1:
        result.append("repeated_plan")
    nodes = schedule + plans
    for node in nodes:
        result.extend(node["issues"])
    if len(schedule) > 1 and _field_conflicts(schedule):
        result.append("conflicting_schedule_fields")
    logical = {}
    for node in nodes:
        page, source = node["page"], node["source"]
        assignment = page.get("assignment", {})
        label = assignment.get("label", "").strip().upper()
        if label:
            key = (label, assignment.get("building", "").strip().upper(),
                   assignment.get("level", "").strip().upper(), page.get("saved_role"))
            logical.setdefault(key, set()).add(source["revision_id"])
    if any(len(revisions) > 1 for revisions in logical.values()):
        result.append("logical_sheet_revision_conflict")
    buildings = {node["page"].get("assignment", {}).get("building", "").strip().upper()
                 for node in nodes}
    if len(buildings - {""}) > 1:
        result.append("assignment_context_conflict")
    return sorted(set(result))


def _generation_body(value):
    return {key: item for key, item in value.items()
            if key not in ("id", "fingerprint", "previous_generation_id")}


def _unread_assignments(assignments, pages):
    issues = []
    for key, assignment in sorted(assignments.items()):
        if key in pages:
            continue
        if not isinstance(assignment, dict):
            raise ReconciliationError("assignment_invalid", "Page assignments must be objects.")
        if assignment.get("available", True) is False:
            continue
        if assignment.get("current_role", assignment.get("role", "unassigned")) == "excluded":
            continue
        source = assignment.get("current_geometry") or assignment.get("source")
        try:
            source = _source(source)
        except ReconciliationError:
            source = None
            if isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}:[0-9]{1,10}", key):
                revision, index = key.split(":")
                source = {"revision_id": revision, "index": int(index)}
        issue = {"code": "current_page_unread",
                 "message": "A current included page is absent from the saved document reading.",
                 "source": source, "page_key": key}
        issues.append(issue)
    return issues


def _verify_generation(value):
    if (not isinstance(value, dict) or value.get("schema") != 1 or value.get("version") != VERSION or
            value.get("id") != value.get("fingerprint") or
            value.get("fingerprint") != _hash(_generation_body(value)) or
            any(not isinstance(value.get(key), list) for key in ("nodes", "edges", "groups", "issues"))):
        raise ReconciliationError("generation_invalid", "The saved reconciliation generation changed.")
    return value


def build(reading, assignments, previous=None):
    """Generate source-linked candidate edges from a verified saved reading."""
    if not isinstance(reading, dict) or reading.get("state") != "completed":
        raise ReconciliationError("reading_incomplete", "Finish document reading before reconciliation.")
    if not isinstance(assignments, dict):
        raise ReconciliationError("assignment_invalid", "Provide current page assignments.")
    project_id = _text(reading.get("project_id"), "project identity", 128, required=True)
    reading_id = _text(reading.get("id"), "document reading identity", 128, required=True)
    fingerprint = reading.get("fingerprint")
    if not isinstance(fingerprint, str) or not _HEX.fullmatch(fingerprint):
        raise ReconciliationError("reading_invalid", "The reading fingerprint is invalid.")
    if previous is not None and _verify_generation(previous)["project_id"] != project_id:
        raise ReconciliationError("project_mismatch", "The earlier generation belongs to another project.")
    pages = _page_bindings(reading, assignments)
    raw_nodes = [_node("schedule", row, pages) for row in reading.get("schedule_rows", [])]
    for register in reading.get("equipment_register", []):
        if not isinstance(register, dict) or not isinstance(register.get("plan_occurrences"), list):
            raise ReconciliationError("reading_invalid", "The document equipment register is invalid.")
        raw_nodes.extend(_node("plan", occurrence, pages, register.get("tag"))
                         for occurrence in register["plan_occurrences"])
        if len(raw_nodes) > MAX_NODES:
            raise ReconciliationError("reconciliation_limit", "The reading exceeds the endpoint limit.")
    if len(raw_nodes) > MAX_NODES:
        raise ReconciliationError("reconciliation_limit", "The reading exceeds the endpoint limit.")
    nodes = {}
    for node in raw_nodes:
        if node["id"] in nodes and node["fingerprint"] != nodes[node["id"]]["fingerprint"]:
            raise ReconciliationError("endpoint_identity_conflict",
                                      "One source record identity has conflicting contents.")
        nodes[node["id"]] = node
    governing = {"version": VERSION, "reading_version": reading.get("version"),
                 "reader": copy.deepcopy(reading.get("reader")),
                 "implementation": {key: value for key, value in reading.get("implementation", {}).items()
                                    if key in _READING_IMPLEMENTATIONS}}
    grouped = {}
    for node in nodes.values():
        grouped.setdefault(node["tag"], []).append(node)
    groups, edges = [], []
    for tag, members in sorted(grouped.items()):
        members.sort(key=lambda node: node["id"])
        schedule = [node for node in members if node["kind"] == "schedule"]
        plans = [node for node in members if node["kind"] == "plan"]
        group_id = _hash([VERSION, project_id, tag])
        issues = _group_issues(schedule, plans)
        group_edges = []
        if len(schedule) * len(plans) + len(edges) > MAX_EDGES:
            issues.append("edge_limit")
        else:
            for row in schedule:
                for plan in plans:
                    edge = {"id": _hash([VERSION, row["id"], plan["id"]]), "group_id": group_id,
                            "schedule_id": row["id"], "plan_id": plan["id"], "kind": "exact_tag",
                            "status": "candidate", "issues": []}
                    group_edges.append(edge)
        status = "matched" if len(schedule) == 1 and len(plans) == 1 and not issues else "blocked"
        for edge in group_edges:
            edge["status"] = "matched" if status == "matched" else "candidate"
            edge["issues"] = list(issues)
        group = {"id": group_id, "tag": tag, "schedule_ids": [node["id"] for node in schedule],
                 "plan_ids": [node["id"] for node in plans], "node_ids": [node["id"] for node in members],
                 "edge_ids": [edge["id"] for edge in group_edges], "status": status, "issues": issues}
        group["fingerprint"] = _hash({"group": group, "nodes": [node["fingerprint"] for node in members],
                                      "governing": governing})
        groups.append(group)
        edges.extend(group_edges)
    issues = copy.deepcopy(reading.get("issues", []))
    if not isinstance(issues, list):
        raise ReconciliationError("reading_invalid", "Reading issues must be a list.")
    issues.extend(_unread_assignments(assignments, pages))
    for binding in pages.values():
        if binding["issues"]:
            issues.append({"code": "page_correspondence_unchecked",
                           "message": "This page has unavailable or changed correspondence evidence.",
                           "source": binding["source"], "issues": binding["issues"]})
    generation = {"schema": 1, "version": VERSION, "project_id": project_id,
                  "reading_id": reading_id, "reading_fingerprint": fingerprint,
                  "reading_content_sha256": _hash(reading), "nodes": sorted(nodes.values(), key=lambda node: node["id"]),
                  "edges": sorted(edges, key=lambda edge: edge["id"]), "groups": groups, "issues": issues}
    generation["fingerprint"] = _hash(generation)
    generation["id"] = generation["fingerprint"]
    generation["previous_generation_id"] = (previous["id"] if previous is not None and
                                            previous["id"] != generation["id"] else None)
    return generation


def _decision_events(decisions, project_id):
    if not isinstance(decisions, list) or len(decisions) > MAX_DECISIONS:
        raise ReconciliationError("decisions_invalid", "The decision history exceeds the supported format.")
    latest, ids = {}, set()
    for event in decisions:
        if (not isinstance(event, dict) or set(event) != _EVENT_KEYS or
                event.get("project_id") != project_id or not isinstance(event.get("id"), str) or
                not _EVENT.fullmatch(event["id"]) or event["id"] in ids or
                event.get("fingerprint") != _hash({key: value for key, value in event.items() if key != "fingerprint"})):
            raise ReconciliationError("decision_invalid", "A saved reconciliation decision changed.")
        group = event.get("group_id")
        if (not isinstance(group, str) or not _HEX.fullmatch(group) or
                not isinstance(event.get("group_fingerprint"), str) or not _HEX.fullmatch(event["group_fingerprint"]) or
                event["action"] not in ("resolve", "exclude_group", "withdraw")):
            raise ReconciliationError("decision_invalid", "A saved decision has invalid identities or action.")
        prior = latest.get(group)
        if event["supersedes"] != (prior["id"] if prior else None):
            raise ReconciliationError("decision_chain_invalid", "Decision history has an unexplained replacement.")
        if event["action"] == "withdraw" and prior is None:
            raise ReconciliationError("decision_chain_invalid", "A withdrawal has no earlier decision.")
        ids.add(event["id"])
        latest[group] = event
    return latest


def _selection(group, nodes, values):
    action = values["action"]
    schedule_id, plans, exclusions = values["schedule_id"], values["plan_ids"], values["exclusions"]
    if (not isinstance(plans, list) or any(not isinstance(item, str) for item in plans) or
            len(plans) != len(set(plans)) or not isinstance(exclusions, list)):
        raise ReconciliationError("decision_selection", "Select each endpoint only once.")
    if action != "resolve":
        if schedule_id is not None or plans or exclusions:
            raise ReconciliationError("decision_selection", "Group exclusion and withdrawal do not select endpoints.")
        return None, [], []
    if not isinstance(schedule_id, str) or schedule_id not in group["schedule_ids"] or not plans:
        raise ReconciliationError("decision_selection", "Select one schedule row and at least one plan reference.")
    if any(plan not in group["plan_ids"] for plan in plans):
        raise ReconciliationError("decision_selection", "Selected references must belong to this tag group.")
    chosen = {schedule_id} | set(plans)
    if any(nodes[node_id]["validity"] != "current" for node_id in chosen):
        raise ReconciliationError("source_not_current", "Selected correspondence evidence is unavailable or changed.")
    if "edge_limit" in group["issues"]:
        raise ReconciliationError("reconciliation_limit", "Narrow the reading before resolving this oversized group.")
    omitted = set(group["node_ids"]) - chosen
    accounted, normalized = set(), []
    for exclusion in exclusions:
        if (not isinstance(exclusion, dict) or set(exclusion) != {"node_id", "disposition", "reason"} or
                not isinstance(exclusion["node_id"], str) or exclusion["node_id"] not in omitted or
                exclusion["node_id"] in accounted or
                exclusion["disposition"] not in ("excluded", "superseded", "reading_error")):
            raise ReconciliationError("decision_exclusion", "Account for each omitted endpoint explicitly.")
        reason = _text(exclusion["reason"], "endpoint exclusion reason", 500, required=True)
        accounted.add(exclusion["node_id"])
        normalized.append(dict(exclusion, reason=reason))
    if accounted != omitted:
        raise ReconciliationError("decision_exclusion", "Every omitted endpoint needs a disposition and reason.")
    return schedule_id, sorted(plans), sorted(normalized, key=lambda value: value["node_id"])


def decide(generation, decisions, group_id, values, actor, reason):
    """Return one append-only exception event, bound to this exact tag group."""
    _verify_generation(generation)
    latest = _decision_events(decisions, generation["project_id"])
    if not isinstance(values, dict) or set(values) != _VALUE_KEYS:
        raise ReconciliationError("decision_invalid", "Provide the complete reconciliation decision values.")
    group = next((value for value in generation["groups"] if value["id"] == group_id), None)
    if group is None:
        raise ReconciliationError("group_missing", "The reconciliation group is not in this generation.")
    if values["group_fingerprint"] != group["fingerprint"]:
        raise ReconciliationError("group_stale", "This tag group changed. Refresh before deciding.")
    if values["action"] not in ("resolve", "exclude_group", "withdraw"):
        raise ReconciliationError("decision_invalid", "Choose resolve, exclude_group or withdraw.")
    previous = latest.get(group_id)
    if values["supersedes"] != (previous["id"] if previous else None):
        raise ReconciliationError("decision_stale", "Name the latest decision being replaced.")
    if values["action"] == "withdraw" and previous is None:
        raise ReconciliationError("decision_missing", "There is no earlier decision to withdraw.")
    selected, plans, exclusions = _selection(group, {node["id"]: node for node in generation["nodes"]}, values)
    event = {"id": uuid.uuid4().hex, "project_id": generation["project_id"], "generation_id": generation["id"],
             "group_id": group_id, "group_fingerprint": group["fingerprint"], "action": values["action"],
             "schedule_id": selected, "plan_ids": plans, "exclusions": exclusions,
             "supersedes": values["supersedes"], "actor": _text(actor, "decision actor", 100, required=True),
             "reason": _text(reason, "decision reason", 500, required=True),
             "at": datetime.now(timezone.utc).isoformat()}
    event["fingerprint"] = _hash(event)
    return event


def _effective_issues(issues, current_generation, groups):
    result = copy.deepcopy(issues)
    effective = {group["id"]: group for group in groups}
    node_groups = {}
    current_nodes = {}
    if current_generation is not None:
        for group in current_generation["groups"]:
            for node_id in group["schedule_ids"]:
                node_groups[node_id] = group["id"]
        current_nodes = {node["id"]: node for node in current_generation["nodes"] if node["kind"] == "schedule"}
    for issue in result:
        if not isinstance(issue, dict):
            continue
        issue["state"] = "open"
        issue.pop("resolution_group_ids", None)
        if issue.get("code") != "duplicate_schedule_tag" or current_generation is None:
            continue
        try:
            located = _source(issue.get("source"), bounds=True)
        except ReconciliationError:
            continue
        matches = {node_groups[node_id] for node_id, node in current_nodes.items()
                   if node["source"] == located}
        if matches and all(group_id in effective and effective[group_id]["validity"] == "current" and
                           effective[group_id]["status"] in ("resolved", "excluded") for group_id in matches):
            issue["state"] = "resolved"
            issue["resolution_group_ids"] = sorted(matches)
    return result


def view(generation, decisions, current_generation=None):
    """Derive the saved graph's current meaning, preserving all original data."""
    _verify_generation(generation)
    if current_generation is not None:
        _verify_generation(current_generation)
        if current_generation["project_id"] != generation["project_id"]:
            raise ReconciliationError("project_mismatch", "Current correspondence belongs to another project.")
    latest = _decision_events(decisions, generation["project_id"])
    current = {} if current_generation is None else {group["id"]: group for group in current_generation["groups"]}
    nodes = copy.deepcopy(generation["nodes"])
    node_map = {node["id"]: node for node in nodes}
    edges = copy.deepcopy(generation["edges"])
    edge_map = {edge["id"]: edge for edge in edges}
    groups, states = [], []
    for original in generation["groups"]:
        group = copy.deepcopy(original)
        event = latest.get(group["id"])
        counterpart = current.get(group["id"])
        validity = ("unavailable" if current_generation is None else
                    "current" if counterpart and counterpart["fingerprint"] == group["fingerprint"] else "stale")
        group.update(validity=validity, decision_id=event["id"] if event else None,
                     decision_state="none", selected_schedule_id=None, selected_plan_ids=[],
                     exclusions=[], block_reason=None)
        if validity != "current":
            group.update(status="blocked", block_reason="current_generation_missing" if validity == "unavailable" else "group_stale")
            if event:
                group["decision_state"] = "withdrawn" if event["action"] == "withdraw" else "stale"
            for node_id in group["node_ids"]:
                node_map[node_id]["validity"] = "blocked"
        elif event is not None and event["action"] == "withdraw":
            group["decision_state"] = "withdrawn"
        elif event is not None and event["group_fingerprint"] != group["fingerprint"]:
            group.update(status="blocked", decision_state="stale", block_reason="decision_stale")
        elif event is not None:
            # Revalidate semantics as well as event bytes before deriving active
            # edges; a stored decision cannot authorize a malformed selection.
            selected, plans, excluded = _selection(group, node_map, event)
            group.update(decision_state="current", selected_schedule_id=selected,
                         selected_plan_ids=plans, exclusions=excluded)
            if event["action"] == "exclude_group":
                group["status"] = "excluded"
                group["exclusions"] = [{"node_id": node_id, "disposition": "excluded", "reason": event["reason"]}
                                       for node_id in group["node_ids"]]
            else:
                group["status"] = "resolved"
        if group["status"] == "matched":
            group["selected_schedule_id"] = group["schedule_ids"][0]
            group["selected_plan_ids"] = list(group["plan_ids"])
        if group["status"] == "blocked" and group["block_reason"] is None:
            group["block_reason"] = group["issues"][0] if group["issues"] else "correspondence_unresolved"
        selected_pairs = {(group["selected_schedule_id"], plan) for plan in group["selected_plan_ids"]}
        for edge_id in group["edge_ids"]:
            edge = edge_map[edge_id]
            if group["status"] in ("matched", "resolved"):
                edge["status"] = group["status"] if (edge["schedule_id"], edge["plan_id"]) in selected_pairs else "excluded"
            elif group["status"] == "excluded":
                edge["status"] = "excluded"
            else:
                edge["status"] = "blocked"
        groups.append(group)
    effective = {group["id"]: group for group in groups}
    for event in decisions:
        if latest[event["group_id"]]["id"] != event["id"]:
            state = "superseded"
        elif event["action"] == "withdraw":
            state = "withdrawn"
        else:
            group = effective.get(event["group_id"])
            state = ("current" if group and group["validity"] == "current" and
                     group["fingerprint"] == event["group_fingerprint"] else "stale")
        states.append({"event_id": event["id"], "group_id": event["group_id"], "state": state})
    summary = {status: sum(group["status"] == status for group in groups)
               for status in ("matched", "resolved", "excluded", "blocked")}
    summary["stale"] = sum(group["validity"] == "stale" for group in groups)
    current_issues = _effective_issues(current_generation["issues"] if current_generation is not None else generation["issues"],
                                      current_generation, groups)
    if current_generation is None:
        current_issues.append({"code": "current_generation_missing",
                               "message": "Current document evidence is unavailable; correspondence is not current."})
    result = {"generation_id": generation["id"],
              "current_generation_id": current_generation["id"] if current_generation else None,
              "project_id": generation["project_id"], "nodes": nodes, "edges": edges, "groups": groups,
              "issues": current_issues, "decision_states": states, "summary": summary}
    result["state_fingerprint"] = _hash(result)
    return result
