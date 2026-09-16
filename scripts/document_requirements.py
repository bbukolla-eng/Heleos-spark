"""Read explicit written mechanical requirements from positioned page text.

This module produces evidence-backed candidates, not construction rules or
approved scope. It does not infer quantities, accessories, or contractual
precedence. Original line text is retained, including negation and conditions.
"""
import hashlib
import json
import math
import re


PARSER_VERSION = 1
MAX_LINES = 10000
MAX_TEXT = 2 * 1024 * 1024
SUPPORTED_ROLES = frozenset({"specification", "plan", "detail", "riser", "legend",
                             "addendum", "schedule"})

_NUMBER = re.compile(r"^\s*(?:(?:\d+(?:\.\d+)*|[A-Z])[.)]|[\u2022\u2023\u25e6-])\s+")
_SECTION = re.compile(r"^\s*(?:SECTION\s+)?(23(?:[ .-]\d{2}){2})(?=\s|$|[:\u2013\u2014-])", re.I)
_SECTION_ANY = re.compile(r"^\s*SECTION\s+(\d{2}(?:[ .-]\d{2}){2})(?=\s|$|[:\u2013\u2014-])", re.I)
_ACTION = re.compile(
    r"\b(?:shall|must|shall\s+not|must\s+not|is\s+required|are\s+required|"
    r"not\s+required|is\s+prohibited|are\s+prohibited)\b|"
    r"^(?:provide|install|connect|furnish|supply|insulate|seal|test|balance|"
    r"remove|demolish|disconnect|relocate|retain|reuse|protect|coordinate|"
    r"verify|include|exclude|ensure|maintain|submit|label|identify|extend|"
    r"replace|repair|support|route|terminate|do\s+not|do\s+no)\b|"
    r"^(?:all|each)\b.{0,180}\bto\s+be\b|"
    r"^no\b.{0,180}\b(?:permitted|allowed)\b", re.I)
_REFERENCE = re.compile(r"\b(?:refer\s+to|see\s+(?:note|detail|section|drawing|sheet|schedule)|"
                        r"per\s+(?:note|detail|section|drawing|sheet|schedule)|"
                        r"in\s+accordance\s+with)\b", re.I)
_REFERENCE_START = re.compile(r"^(?:refer\s+to|see\b)", re.I)
_NEGATIVE = re.compile(r"\b(?:not|never|without|exclude|excluding|excluded|prohibited)\b|"
                       r"^no\b", re.I)
_CONDITIONAL = re.compile(r"\b(?:if|unless|where|when|wherever|whenever|except|"
                          r"subject\s+to|as\s+(?:required|indicated|shown|noted))\b", re.I)
_CONTINUATION = re.compile(r"^(?:unless|except|provided\s+that|subject\s+to|"
                           r"including|excluding|and|or)\b", re.I)
# Bounded equipment-tag hints, shared conceptually with the existing tag reader.
# Drawing references such as M-401 are not equipment links. Unknown tag forms
# remain in the exact statement for review; this list does not establish scope.
_TAG_PREFIXES = ("AHU", "RTU", "FCU", "VAV", "ERV", "HRV", "MAU", "DOAS", "EF",
                 "SF", "RF", "CU", "AC", "HP", "UH", "TUH", "ATU", "CHWP", "HWP",
                 "CWP", "P", "CH", "B", "WH", "EWH", "HWCP", "FPT", "FPB")
_TAG = re.compile(r"(?<![A-Z0-9])(" + "|".join(sorted(_TAG_PREFIXES, key=len, reverse=True)) +
                  r")\s*[-\u2010-\u2015]?\s*(\d{1,5}[A-Z]?)(?![A-Z0-9])")
_CATEGORY_PATTERNS = (
    ("equipment", r"\b(?:equipment|air\s+handl(?:er|ing)|AHU|RTU|DOAS|ERV|HRV|"
     r"VAV|FCU|EF|SF|RF|MAU|boilers?|chillers?|pumps?|fans?|furnaces?|"
     r"heat\s+pumps?|unit\s+heaters?|heat\s+exchangers?)\b"),
    ("ductwork", r"\b(?:duct(?:work|s)?|plenum(?:s)?|flexible\s+duct|sheet\s+metal)\b"),
    ("air_devices", r"\b(?:diffusers?|grilles?|registers?|louvers?|air\s+devices?|"
     r"air\s+terminals?|air\s+outlets?)\b"),
    ("piping", r"\b(?:pip(?:e|es|ing)|hydronic|refrigerant|condensate|steam|"
     r"chilled\s+water|heating\s+water|hot\s+water|glycol|natural\s+gas)\b"),
    ("fittings", r"\b(?:fittings?|elbows?|tees?|reducers?|transitions?|"
     r"couplings?|flanges?|branches|branch\s+connections?)\b"),
    ("insulation", r"\b(?:insulat(?:e|ed|ing|ion)|jacketing|vapo[ur]+\s+barriers?|"
     r"thermal\s+barriers?|duct\s+liner|acoustic\s+liner)\b"),
    ("controls", r"\b(?:controls?|BAS|BMS|DDC|thermostats?|sensors?|actuators?|"
     r"interlocks?|sequences?\s+of\s+operation|control\s+wiring)\b"),
    ("accessories", r"\b(?:accessor(?:y|ies)|valves?|dampers?|strainers?|"
     r"hangers?|supports?|sleeves?|access\s+doors?|access\s+panels?|"
     r"vibration\s+isolators?|gauges?|thermometers?|vents?|drains?)\b"),
    ("demolition", r"\b(?:demoli(?:sh|tion)|remove|removal|disconnect|abandon|"
     r"relocate|existing\s+to\s+remain)\b"),
)
_CATEGORIES = [(name, re.compile(pattern, re.I)) for name, pattern in _CATEGORY_PATTERNS]


def _bbox(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
           not math.isfinite(v) or v < 0 or v > 1 for v in value):
        return None
    if value[0] >= value[2] or value[1] >= value[3]:
        return None
    return list(value)


def _source(page, bbox):
    return {"revision_id": page.get("revision_id"), "index": page.get("index"),
            "sheet_id": page.get("sheet_id"), "bbox": bbox}


def _issue(page, code, message, bbox=None):
    return {"code": code, "message": message, "source": _source(page, bbox)}


def _body(text):
    return _NUMBER.sub("", text, count=1).strip()


def _is_directive(text):
    value = _body(text)
    return bool(_ACTION.search(value) or _REFERENCE_START.search(value))


def _heading(text):
    value = text.strip()
    if _SECTION.match(value) or _SECTION_ANY.match(value):
        return True
    if _is_directive(value) or _CONTINUATION.match(_body(value)) or len(value) > 180:
        return False
    letters = [c for c in value if c.isalpha()]
    if not letters or len(letters) < 3:
        return False
    return (all(c.isupper() for c in letters) and not value.endswith((".", ";", ",")))


def _union(lines):
    return [min(line["bbox"][0] for line in lines),
            min(line["bbox"][1] for line in lines),
            max(line["bbox"][2] for line in lines),
            max(line["bbox"][3] for line in lines)]


def _normal_lines(page, issues):
    raw = page.get("lines", [])
    if not isinstance(raw, list):
        issues.append(_issue(page, "invalid_lines", "Positioned page lines must be a list."))
        return []
    if len(raw) > MAX_LINES:
        issues.append(_issue(page, "line_limit", "Page exceeds the requirement reader line limit."))
        return []
    total = 0
    result = []
    for index, line in enumerate(raw):
        if not isinstance(line, dict) or not isinstance(line.get("text"), str):
            issues.append(_issue(page, "invalid_line", "A page line has no readable text."))
            continue
        if not line["text"].strip():
            continue
        total += len(line["text"])
        if total > MAX_TEXT:
            issues.append(_issue(page, "text_limit", "Page exceeds the requirement reader text limit."))
            return []
        bounds = _bbox(line.get("bbox"))
        if bounds is None:
            issues.append(_issue(page, "invalid_line_source", "A text line has invalid source coordinates."))
            continue
        result.append({"text": line["text"], "bbox": bounds, "words": line.get("words", []),
                       "ordinal": index})
    return result


def _lanes(lines):
    """Group similar left margins, leaving distant drawing/spec columns apart."""
    lanes = []
    for line in sorted(lines, key=lambda item: (item["bbox"][0], item["bbox"][1], item["ordinal"])):
        left = line["bbox"][0]
        matches = [lane for lane in lanes if abs(lane["left"] - left) <= 0.035]
        if matches:
            lane = min(matches, key=lambda value: abs(value["left"] - left))
            lane["lines"].append(line)
        else:
            lanes.append({"left": left, "lines": [line]})
    for lane in lanes:
        lane["lines"].sort(key=lambda item: (item["bbox"][1], item["bbox"][0], item["ordinal"]))
    return lanes


def _has_wide_gap(line):
    """Recognize a likely merged-column line without guessing its reading order."""
    words = line.get("words")
    if not isinstance(words, list):
        return False
    boxes = [_bbox(word.get("bbox")) for word in words if isinstance(word, dict)]
    boxes = sorted([value for value in boxes if value is not None], key=lambda value: value[0])
    if len(boxes) < 2:
        return False
    # A large gap inside a single line is unsafe for paragraph assembly. Retain
    # that line alone and expose the issue; never fabricate the missing text.
    height = line["bbox"][3] - line["bbox"][1]
    return any(right[0] - left[2] > max(0.08, height * 6)
               for left, right in zip(boxes, boxes[1:]))


def _record(page, lines, section, context, issues):
    text = "\n".join(line["text"] for line in lines)
    searchable = " ".join(line["text"].strip() for line in lines)
    if not _is_directive(searchable):
        if _CONTINUATION.match(_body(searchable)):
            issues.append(_issue(page, "unattached_qualifier",
                                 "A condition or continuation could not be safely attached to a requirement.",
                                 _union(lines)))
        return None
    category_text = searchable + " " + " ".join(item["text"] for item in context)
    categories = [name for name, pattern in _CATEGORIES if pattern.search(category_text)]
    qualifiers = []
    if _NEGATIVE.search(searchable):
        qualifiers.append("negative")
    if _CONDITIONAL.search(searchable):
        qualifiers.append("conditional")
    if _REFERENCE.search(searchable) or _REFERENCE_START.search(_body(searchable)):
        qualifiers.append("reference")
    if _REFERENCE_START.search(_body(searchable)) and not _ACTION.search(_body(searchable)):
        qualifiers.append("reference_only")
    if any(_has_wide_gap(line) for line in lines):
        qualifiers.append("ambiguous_layout")
    tags = []
    for match in _TAG.finditer(searchable.upper()):
        tag = match.group(1) + "-" + match.group(2)
        if tag not in tags:
            tags.append(tag)
    source = _source(page, _union(lines))
    identity = {"text": text, "source": source, "line_ordinals": [line["ordinal"] for line in lines]}
    result = {"id": hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"),
                                             ensure_ascii=False).encode("utf-8")).hexdigest(),
              "text": text, "categories": categories or ["general"], "section": section,
              "tags": tags, "source": source, "qualifiers": qualifiers, "status": "candidate",
              "source_lines": [{"text": line["text"], "source": _source(page, line["bbox"])}
                               for line in lines],
              "context": [{"text": item["text"], "source": _source(page, item["bbox"])}
                          for item in context]}
    return result


def parse_requirements(page):
    """Return explicit requirement/reference proposals and unresolved layout issues.

    Input line and word boxes use normalized top-left coordinates. Section
    numbers are taken only from explicit headings on this page. A nearby heading
    supplies category context, never an invented requirement or quantity.
    """
    if not isinstance(page, dict):
        raise ValueError("Positioned page must be an object.")
    issues = []
    requirements = []
    if page.get("role") not in SUPPORTED_ROLES:
        return {"requirements": [], "issues": []}
    lines = _normal_lines(page, issues)
    # Full-width explicit section headings apply to both columns below them.
    shared_sections = [line for line in lines if (_SECTION.match(line["text"]) or
                       _SECTION_ANY.match(line["text"])) and line["bbox"][2] - line["bbox"][0] >= 0.6]
    for lane in _lanes(lines):
        section = None
        section_line = None
        heading_lines = []
        paragraph = []

        def flush():
            if paragraph:
                context = ([section_line] if section_line is not None else []) + heading_lines
                record = _record(page, paragraph, section, context, issues)
                if record is not None:
                    requirements.append(record)
                paragraph.clear()

        for line in lane["lines"]:
            text = line["text"]
            available_sections = [item for item in shared_sections if item["bbox"][3] <= line["bbox"][1]]
            if available_sections:
                candidate = max(available_sections, key=lambda item: item["bbox"][1])
                if section_line is None or candidate["bbox"][1] > section_line["bbox"][1]:
                    flush()
                    section_line = candidate
                    section = (_SECTION.match(candidate["text"]) or _SECTION_ANY.match(candidate["text"])).group(1)
                    heading_lines = []
            match = _SECTION.match(text) or _SECTION_ANY.match(text)
            if match:
                flush()
                section = match.group(1)
                section_line = line
                heading_lines = []
                continue
            close_unfinished = False
            if paragraph:
                previous = paragraph[-1]
                height = max(previous["bbox"][3] - previous["bbox"][1],
                             line["bbox"][3] - line["bbox"][1])
                gap = line["bbox"][1] - previous["bbox"][3]
                close_unfinished = (-height * 0.2 <= gap <= height * 1.5 and
                                    not previous["text"].rstrip().endswith((".", ";", "!", "?")) and
                                    not _NUMBER.match(text))
            if _heading(text) and not close_unfinished:
                flush()
                heading_lines = [line]
                continue
            wide_gap = _has_wide_gap(line)
            if wide_gap:
                flush()
                issues.append(_issue(page, "ambiguous_line_columns",
                                     "A text line spans a large internal gap; reading order needs review.",
                                     line["bbox"]))
            if paragraph:
                previous = paragraph[-1]
                height = max(previous["bbox"][3] - previous["bbox"][1],
                             line["bbox"][3] - line["bbox"][1])
                gap = line["bbox"][1] - previous["bbox"][3]
                separated = (gap < -height * 0.2 or gap > height * 1.5 or
                             previous["text"].rstrip().endswith((".", ";", "!", "?")) or
                             bool(_NUMBER.match(text)) or wide_gap)
                # A second independent imperative is a separate clause, even
                # when the PDF omitted sentence punctuation.
                independent = _is_directive(text) and _is_directive(" ".join(p["text"] for p in paragraph))
                if separated or independent:
                    flush()
            paragraph.append(line)
            if wide_gap:
                flush()
        flush()
    requirements.sort(key=lambda item: (item["source"]["bbox"][1], item["source"]["bbox"][0], item["id"]))
    return {"requirements": requirements, "issues": issues}
