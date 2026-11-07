#!/usr/bin/env python3
"""Check CSI Division 23 delivery routing without changing repository files."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import sys


STAGES = ("DEFINE", "RESULT", "CONNECT", "QUALIFY")
ROOT_ID = "CSI-23-00-00"
IDENTITY = re.compile(r"CSI-23-([0-9]{2})-([0-9]{2}(?:\.[0-9]{2})?)\Z")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _load(path, errors):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RuntimeError) as exc:
        errors.append(f"{path.name}: cannot read JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path.name}: document must be an object")
        return {}
    if type(value.get("schema_version")) is not int or value["schema_version"] != 2:
        errors.append(f"{path.name}: schema_version must be 2")
    return value


def _card(root, value, label, errors):
    if not _text(value):
        errors.append(f"{label}: delivery_plan must be a relative Markdown path")
        return
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or "\\" in value
            or ":" in value or "\x00" in value):
        errors.append(f"{label}: unsafe delivery_plan path")
        return
    if path.suffix.lower() != ".md":
        errors.append(f"{label}: delivery_plan must identify Markdown")
        return
    try:
        candidate = (root / value).resolve()
        if not candidate.is_relative_to(root):
            errors.append(f"{label}: delivery_plan escapes root")
        elif not candidate.is_file():
            errors.append(f"{label}: delivery_plan file is missing")
    except (OSError, ValueError, RuntimeError) as exc:
        errors.append(f"{label}: delivery_plan cannot be resolved: {exc}")


def _sections(root, value, errors):
    if not isinstance(value, list):
        errors.append("register sections must be an array")
        return {}
    sections = {}
    for index, section in enumerate(value):
        label = f"section[{index}]"
        if not isinstance(section, dict):
            errors.append(f"{label}: must be an object")
            continue
        identity = section.get("id")
        match = IDENTITY.fullmatch(identity) if isinstance(identity, str) else None
        if not match:
            errors.append(f"{label}: invalid CSI identity {identity!r}")
            continue
        label = identity
        if identity in sections:
            errors.append(f"duplicate section {identity}")
            continue
        sections[identity] = section
        if section.get("number") != f"23 {match[1]} {match[2]}":
            errors.append(f"{label}: number does not match CSI identity")
        if not _text(section.get("title")):
            errors.append(f"{label}: title must be a nonempty string")
        if "parent_id" not in section:
            errors.append(f"{label}: parent_id is required")
        parent = section.get("parent_id")
        if parent is not None and not isinstance(parent, str):
            errors.append(f"{label}: parent_id must be an existing CSI ID or null")
        source = section.get("source")
        page = source.get("page") if isinstance(source, dict) else None
        if type(page) is not int or page <= 0:
            errors.append(f"{label}: source.page must be a positive integer")
        _card(root, section.get("delivery_plan"), label, errors)
        if section.get("task_ids") != [f"{identity}-{stage}" for stage in STAGES]:
            errors.append(f"{label}: task_ids must list exactly the four matching stages")
    return sections


def _hierarchy(sections, errors):
    roots = [key for key, section in sections.items() if section.get("parent_id") is None]
    if roots != [ROOT_ID]:
        errors.append(f"hierarchy requires exactly one root {ROOT_ID}; found {roots!r}")
    inverse = {identity: [] for identity in sections}
    for identity, section in sections.items():
        parent = section.get("parent_id")
        if parent is None:
            continue
        if not isinstance(parent, str) or parent not in sections:
            errors.append(f"{identity}: parent_id does not identify a registered section")
        else:
            inverse[parent].append(identity)
    for identity, section in sections.items():
        children = section.get("children")
        if (not isinstance(children, list) or not all(isinstance(c, str) for c in children)
                or len(children) != len(set(children)) or set(children) != set(inverse[identity])):
            errors.append(f"{identity}: children must exactly invert parent_id relationships")

    # Follow parent pointers without recursion, including disconnected components.
    checked = set()
    for identity in sections:
        current, active = identity, set()
        while isinstance(current, str) and current in sections and current not in checked:
            if current in active:
                errors.append(f"hierarchy cycle includes {current}")
                break
            active.add(current)
            current = sections[current].get("parent_id")
        checked.update(active)
    reachable, pending = set(), [ROOT_ID] if ROOT_ID in sections else []
    while pending:
        current = pending.pop()
        if current not in reachable:
            reachable.add(current)
            pending.extend(inverse[current])
    missing = sorted(set(sections) - reachable)
    if missing:
        errors.append(f"sections unreachable from {ROOT_ID}: {', '.join(missing)}")


def _templates(value, errors):
    result = {}
    if not isinstance(value, dict):
        errors.append("contracts templates must be an object")
        return result
    if set(value) != set(STAGES):
        errors.append("contracts templates must contain exactly DEFINE, RESULT, CONNECT, QUALIFY")
    for stage in STAGES:
        template = value.get(stage)
        criteria = template.get("criteria") if isinstance(template, dict) else None
        if not isinstance(criteria, list) or not criteria:
            errors.append(f"template {stage}: criteria must be a nonempty array")
            continue
        identifiers = []
        for index, criterion in enumerate(criteria):
            if not isinstance(criterion, dict) or any(
                    not _text(criterion.get(field)) for field in ("id", "expected", "check")):
                errors.append(f"template {stage}: criteria[{index}] requires nonempty id, expected and check")
                continue
            identity = criterion["id"]
            if identity in identifiers:
                errors.append(f"template {stage}: duplicate criterion {identity}")
            identifiers.append(identity)
        result[stage] = identifiers
    return result


def _tasks(value, sections, templates, errors):
    if not isinstance(value, list):
        errors.append("contracts tasks must be an array")
        return
    seen = set()
    expected = {f"{identity}-{stage}" for identity in sections for stage in STAGES}
    for index, task in enumerate(value):
        label = f"task[{index}]"
        if not isinstance(task, dict):
            errors.append(f"{label}: must be an object")
            continue
        identity = task.get("id")
        if not _text(identity):
            errors.append(f"{label}: task id must be a nonempty string")
        else:
            label = identity
            if identity in seen:
                errors.append(f"duplicate task {identity}")
            seen.add(identity)
            if identity not in expected:
                errors.append(f"{label}: task id has no matching registered stage")
        section_id, stage = task.get("section_id"), task.get("stage")
        valid_section = isinstance(section_id, str) and section_id in sections
        valid_stage = isinstance(stage, str) and stage in STAGES
        if not valid_section:
            errors.append(f"{label}: section_id must identify a registered section")
        if not valid_stage:
            errors.append(f"{label}: stage must be DEFINE, RESULT, CONNECT or QUALIFY")
        if valid_section and valid_stage:
            if identity != f"{section_id}-{stage}":
                errors.append(f"{label}: task id does not match section_id and stage")
            expected_criteria = [f"{section_id}-{criterion}" for criterion in templates.get(stage, [])]
            if task.get("criteria_ids") != expected_criteria:
                errors.append(f"{label}: criteria_ids do not match the ordered stage template")
        if not _text(task.get("state")):
            errors.append(f"{label}: state must be a nonempty string")
    for identity in sorted(expected - seen):
        errors.append(f"missing task {identity}")


def validate(root: Path) -> list[str]:
    """Return structure errors; source authority and mechanical correctness are separate."""
    errors = []
    try:
        root = Path(root).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        return [f"cannot resolve root: {exc}"]
    plans = root / "docs/plans"
    register = _load(plans / "division-23-section-register.json", errors)
    contracts = _load(plans / "division-23-task-contracts.json", errors)
    sections = _sections(root, register.get("sections"), errors)
    _hierarchy(sections, errors)
    templates = _templates(contracts.get("templates"), errors)
    _tasks(contracts.get("tasks"), sections, templates, errors)
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    errors = validate(args.root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("CSI Division 23 delivery structure: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
