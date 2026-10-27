"""Project-supplied mechanical sections and current written-requirement review.

This is a derived review projection. It does not contain a CSI catalogue,
infer missing sections, establish contract precedence or calculate quantities.
"""
import copy
import hashlib
import json
import re

VERSION = "mechanical-scope-1"
_NUMBER = re.compile(r"(\d{2})[ .-](\d{2})[ .-](\d{2})(\.\d{2}(?:\.\d{2})*(?: \d{2})?)?\Z")


def canonical_section(value):
    if not isinstance(value, str):
        return None
    match = _NUMBER.fullmatch(" ".join(value.split()))
    if not match:
        return None
    return " ".join(match.group(i) for i in (1, 2, 3)) + (match.group(4) or "")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def _requirement(value, reviews, stale, ready):
    saved = reviews.get(value["id"])
    current = bool(saved and ready and not stale and
                   saved.get("fingerprint") == _digest(value) and
                   saved.get("disposition") in ("applicable", "excluded"))
    old = bool(saved and not current)
    return {"id": value["id"], "text": value["text"],
            "source": copy.deepcopy(value["source"]),
            "qualifiers": list(value.get("qualifiers", [])),
            "categories": list(value.get("categories", [])),
            "disposition": saved["disposition"] if current else "pending",
            "review_state": "current" if current else "stale" if old else "pending",
            "stored_disposition": saved.get("disposition") if saved else None,
            "reason": saved.get("reason", "") if saved else ""}


def build_view(reading, reviews):
    result = {"version": VERSION, "available": reading is not None,
              "reading_id": reading.get("id") if reading else None,
              "state": "unavailable", "stale": False,
              "section_index_available": False, "sections": [],
              "unassigned_requirements": [], "other_division_requirement_count": 0,
              "unread_pages": [], "issues": [],
              "project_coverage_verified": False, "product_coverage_verified": False}
    if reading is None:
        return result
    stale = bool(reading.get("stale"))
    ready = reading.get("state") == "completed"
    state = reading.get("state")
    result.update(state="stale" if stale else "current" if ready else state if state in ("failed", "interrupted") else "reading",
                  stale=stale, section_index_available=isinstance(reading.get("sections"), list))
    groups = {}

    def group(number):
        return groups.setdefault(number, {"section": number, "headings": [], "requirements": []})

    for heading in reading.get("sections", []):
        number = canonical_section(heading.get("section"))
        if number and number.startswith("23 "):
            row = {"text": heading["text"], "source": copy.deepcopy(heading["source"])}
            headings = group(number)["headings"]
            if row not in headings:
                headings.append(row)
    for requirement in reading.get("requirements", []):
        number = canonical_section(requirement.get("section"))
        row = _requirement(requirement, reviews, stale, ready)
        if number and number.startswith("23 "):
            group(number)["requirements"].append(row)
        elif number:
            result["other_division_requirement_count"] += 1
        else:
            result["unassigned_requirements"].append(row)
    for number in sorted(groups):
        row = groups[number]
        records = row["requirements"]
        row["counts"] = {"requirements": len(records),
                         **{key: sum(r["disposition"] == key for r in records)
                            for key in ("applicable", "excluded", "pending")},
                         "stale": sum(r["review_state"] == "stale" for r in records)}
        row["review_state"] = ("stale" if stale else "no_requirements_identified" if not records
                               else "needs_review" if row["counts"]["pending"] or not ready
                               else "requirements_reviewed")
        result["sections"].append(row)
    result["unread_pages"] = [{"source": {k: p[k] for k in ("revision_id", "index", "sheet_id") if k in p},
                               "state": "unassigned" if p.get("role") == "unassigned" else p.get("state", "unknown")}
                              for p in reading.get("pages", [])
                              if p.get("state") != "read" or p.get("role") == "unassigned"]
    result["issues"] = copy.deepcopy(reading.get("issues", []))
    if not result["section_index_available"]:
        result["issues"].append({"code": "section_index_missing",
                                 "message": "Refresh the document reading to include section headings without extracted requirements."})
    return result
