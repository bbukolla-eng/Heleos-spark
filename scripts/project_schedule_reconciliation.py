"""Connect immutable schedule/plan correspondence to the saved project workflow.

The workflow's single writer persists these records atomically with its history.
Reading, reopening, and deriving currency launch no jobs or model calls.
"""
import copy
import hashlib
import importlib.util
from pathlib import Path


def _module(name):
    spec = importlib.util.spec_from_file_location("heleos_" + name, Path(__file__).with_name(name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


core = _module("schedule_reconciliation")
geometry = _module("sheet_geometry")


def initialize(data):
    data.setdefault("reconciliation_generations", [])
    data.setdefault("reconciliation_current", None)
    data.setdefault("reconciliation_decisions", [])


def assignments(data, sheets):
    result = {}
    implementation = hashlib.sha256(Path(__file__).with_name("schedule_reconciliation.py").read_bytes()).hexdigest()
    for key in sorted(set(sheets) | set(data["pages"])):
        sheet = sheets.get(key)
        entry = copy.deepcopy(data["pages"].get(key, {}))
        entry["available"] = sheet is not None
        entry["current_role"] = entry.get("role", "unassigned")
        entry["reconciliation_implementation_sha256"] = implementation
        try:
            entry["current_geometry"] = geometry.sheet_geometry(sheet, sheet["revision_id"]) if sheet else None
        except ValueError:
            entry["current_geometry"] = None
        result[key] = entry
    return result


def current(workspace, data, sheets, verify=False):
    if not data.get("document_run"):
        return None
    reading = workspace.documents.result(data["document_run"])
    if reading["state"] != "completed":
        return None
    if verify:
        reading = workspace.documents.verified_result(reading["id"])
    return core.build(reading, assignments(data, sheets))


def _retain(data, generation):
    previous = data["reconciliation_current"]
    if not any(value["id"] == generation["id"] for value in data["reconciliation_generations"]):
        generation = copy.deepcopy(generation)
        generation["previous_generation_id"] = previous
        data["reconciliation_generations"].append(generation)
    data["reconciliation_current"] = generation["id"]


def refresh(workspace, data, sheets):
    generation = current(workspace, data, sheets, verify=True)
    if generation is None:
        raise core.ReconciliationError("reading_required", "Finish reading the project documents before matching schedules and plans.")
    _retain(data, generation)
    return generation


def decide(workspace, data, sheets, values, actor, reason):
    if not data["reconciliation_current"]:
        raise core.ReconciliationError("reconciliation_required", "Match the saved schedules and plans before resolving an exception.")
    generation = current(workspace, data, sheets, verify=True)
    if generation is None:
        raise core.ReconciliationError("reading_required", "Finish reading the project documents before resolving their matches.")
    event = core.decide(generation, data["reconciliation_decisions"], values["group_id"],
                        {key: value for key, value in values.items() if key != "group_id"}, actor, reason)
    _retain(data, generation)
    data["reconciliation_decisions"].append(event)
    return event


def view(workspace, data, sheets, documents):
    saved = next((value for value in data["reconciliation_generations"]
                  if value["id"] == data["reconciliation_current"]), None)
    if data["reconciliation_current"] and saved is None:
        raise core.ReconciliationError("reconciliation_missing", "The saved matching graph is missing. Preserve the workflow before recovery.")
    fresh = current(workspace, data, sheets) if saved is not None else None
    if saved is None:
        result = {"generation_id": None, "current_generation_id": fresh["id"] if fresh else None,
                  "project_id": data["project_id"], "state_fingerprint": None,
                  "nodes": [], "edges": [], "groups": [], "issues": [], "decision_states": [],
                  "summary": {key: 0 for key in ("matched", "resolved", "excluded", "blocked", "stale")}}
    else:
        result = core.view(saved, data["reconciliation_decisions"], fresh)
    result.update(available=saved is not None, can_build=bool(documents and documents["state"] == "completed"),
                  reading_stale=bool(documents and documents.get("stale")),
                  needs_refresh=bool(fresh and (saved is None or saved["id"] != fresh["id"])),
                  generations=copy.deepcopy(data["reconciliation_generations"]),
                  decisions=copy.deepcopy(data["reconciliation_decisions"]))
    return result


def issues(view):
    result = []
    if not view["available"]:
        return result
    if view["needs_refresh"]:
        result.append({"id": "reconciliation:refresh", "message": "Document evidence or page assignments changed. Update the saved schedule and plan matches.",
                       "source": None, "state": "open", "automatic": True})
    for group in view["groups"]:
        if group["status"] == "blocked" or group["validity"] != "current":
            result.append({"id": "reconciliation:" + group["id"],
                           "message": group["tag"] + ": schedule and plan correspondence needs attention (" +
                                      ", ".join(group["issues"] or [group.get("block_reason") or "unresolved"]).replace("_", " ") + ").",
                           "source": next((n["source"] for n in view["nodes"] if n["id"] in group["node_ids"]), None),
                           "state": "open", "automatic": True})
    for index, issue in enumerate(view["issues"]):
        if issue.get("state") == "resolved":
            continue
        result.append({"id": "reconciliation:source:" + str(index),
                       "message": issue.get("message", issue.get("code", "Source reading remains incomplete")).replace("_", " "),
                       "source": issue.get("source"), "state": "open", "automatic": True})
    return result


def resolved_reading_issue_ids(view, documents):
    """A resolved correspondence warning keeps its original reading evidence."""
    if not documents or not view["available"]:
        return set()
    resolved = [issue for issue in view["issues"] if issue.get("state") == "resolved"
                and issue.get("code") == "duplicate_schedule_tag"]
    return {"reading:issue:" + str(index) for index, issue in enumerate(documents.get("issues", []))
            if any(issue.get("code") == record.get("code") and issue.get("source") == record.get("source")
                   for record in resolved)}
