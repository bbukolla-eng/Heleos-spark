"""Read explicit equipment schedule cells from positioned document text.

This is a bounded table reader, not a vision model or quantity approval engine.
It supports horizontal schedules with a labelled tag/mark column, separated
column headings, and aligned data rows. Nearby wrapped headings and cells are
retained. Rotated, merged/hierarchical or insufficiently separated layouts stay
unresolved. The caller preserves original text artifacts; every extracted field
also carries its own page location.
"""
import hashlib
import json
import math
import re
import statistics


PARSER_VERSION = 1
MAX_WORDS = 40000
MAX_ROWS = 1500
MAX_COLUMNS = 64
MAX_TABLES = 64
_DASHES = str.maketrans({chr(number): "-" for number in range(0x2010, 0x2016)})
_UNKNOWN = {"", "-", "--", "N/A", "NA", "TBD", "T.B.D.", "UNKNOWN",
            "NOT SHOWN", "SEE PLAN", "SEE PLANS", "VARIES"}
_TAG_HEADERS = {"TAG", "TAG NO", "TAG NUMBER", "EQUIPMENT TAG", "EQUIP TAG",
                "EQUIPMENT NO", "EQUIP NO", "EQUIPMENT NUMBER",
                "EQUIPMENT ID", "UNIT TAG", "UNIT NO", "UNIT NUMBER",
                "MARK", "MARK NO", "DESIGNATION", "IDENTIFICATION", "ID"}
_TAG_EXCLUDED = _UNKNOWN | {"TAG", "QTY", "TYPE", "SEE", "NOTE", "NOTES",
                            "TOTAL", "TOTALS", "MARK", "UNIT", "END"}
_UNIT_WORDS = {"CFM", "L/S", "LPS", "GPM", "GPH", "KW", "W", "HP", "BHP",
               "MBH", "BTU/H", "BTUH", "TON", "TONS", "V", "PH", "HZ", "AMPS",
               "RPM", "F", "C", "IN", "IN.", "W.G.", "WG", "FT", "MM", "KPA",
               "PA", "PSI", "PSIG", "LBS", "LB", "KG", "EA", "EACH"}


def _normal(value):
    return re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()


def _name(header):
    value = _normal(header)
    if value in _TAG_HEADERS:
        return "tag"
    words = set(value.split())
    if value in {"QTY", "QUANTITY", "QUANT", "COUNT", "NO OF UNITS",
                 "NUMBER OF UNITS", "UNIT COUNT", "QTY EA", "QUANTITY EA"}:
        return "quantity"
    if value in {"TYPE", "EQUIPMENT TYPE", "UNIT TYPE", "DESCRIPTION"}:
        return "equipment_type"
    if "CFM" in words or value in {"AIRFLOW", "AIR FLOW", "AIR VOLUME", "L S", "LPS"}:
        return "airflow"
    if words & {"CAPACITY", "MBH", "BTUH", "TONS", "TON"} or "BTU H" in value:
        return "capacity"
    if words & {"LOCATION", "LOC", "ROOM", "AREA", "LEVEL", "FLOOR"}:
        return "location"
    if words & {"CONNECTION", "CONNECTIONS", "CONN", "INLET", "OUTLET"}:
        return "connections"
    if words & {"NOTES", "NOTE", "REMARKS"}:
        return "notes"
    if words & {"SIZE", "DIMENSIONS", "DIMENSION"}:
        return "size"
    if words & {"SYSTEM", "SERVICE"}:
        return "system"
    if words & {"MANUFACTURER", "MFR", "MAKE"}:
        return "manufacturer"
    if words & {"MODEL"}:
        return "model"
    if words & {"VOLTAGE", "VOLT", "VOLTS", "PHASE", "ELECTRICAL"}:
        return "electrical"
    if words & {"GPM", "GPH"} or value in {"WATER FLOW", "FLOW RATE"}:
        return "flow"
    if words & {"ESP", "PRESSURE", "SP"}:
        return "pressure"
    if words & {"WEIGHT", "MASS"}:
        return "weight"
    if words & {"HP", "BHP", "POWER", "KW"}:
        return "power"
    return None


def _bbox(words):
    return [round(min(word["bbox"][0] for word in words), 7),
            round(min(word["bbox"][1] for word in words), 7),
            round(max(word["bbox"][2] for word in words), 7),
            round(max(word["bbox"][3] for word in words), 7)]


def _source(page, bbox):
    return {key: page.get(key) for key in ("revision_id", "index", "sheet_id")} | {
        "bbox": list(bbox)}


def _issue(page, code, message, bbox, raw_text=None):
    result = {"code": code, "message": message, "source": _source(page, bbox)}
    if raw_text is not None:
        result["raw_text"] = raw_text
    return result


def _valid_box(value):
    return (isinstance(value, (list, tuple)) and len(value) == 4 and
            all(type(number) in (int, float) and math.isfinite(number) for number in value) and
            0 <= value[0] < value[2] <= 1 and 0 <= value[1] < value[3] <= 1)


def _words(page):
    result = []
    if not isinstance(page.get("lines"), list) or len(page["lines"]) > MAX_WORDS:
        raise ValueError("Unsupported line collection.")
    for line in page["lines"]:
        if not isinstance(line, dict) or not isinstance(line.get("words"), list):
            raise ValueError("Positioned words are required.")
        for word in line["words"]:
            if (not isinstance(word, dict) or not isinstance(word.get("text"), str) or
                    not word["text"].strip() or len(word["text"]) > 2000 or
                    not _valid_box(word.get("bbox"))):
                raise ValueError("A positioned word is invalid.")
            result.append({"text": word["text"], "bbox": list(word["bbox"])})
            if len(result) > MAX_WORDS:
                raise ValueError("Too many positioned words.")
    return result


def _bands(words):
    """Regroup cells split into separate Poppler lines by their text baselines."""
    result = []
    for word in sorted(words, key=lambda item: ((item["bbox"][1] + item["bbox"][3]) / 2,
                                               item["bbox"][0])):
        y0, y1 = word["bbox"][1], word["bbox"][3]
        center = (y0 + y1) / 2
        target = None
        for band in reversed(result[-4:]):
            overlap = min(y1, band["bottom"]) - max(y0, band["top"])
            if overlap >= min(y1 - y0, band["bottom"] - band["top"]) * 0.6:
                target = band
                break
            if center - band["center"] > max(y1 - y0, band["bottom"] - band["top"]):
                break
        if target is None:
            target = {"words": [], "top": y0, "bottom": y1, "center": center}
            result.append(target)
        target["words"].append(word)
        target["top"] = min(target["top"], y0)
        target["bottom"] = max(target["bottom"], y1)
        target["center"] = (target["top"] + target["bottom"]) / 2
    for band in result:
        band["words"].sort(key=lambda item: item["bbox"][0])
    return sorted(result, key=lambda band: band["center"])


def _segments(words):
    if not words:
        return []
    words = sorted(words, key=lambda item: item["bbox"][0])
    char_width = statistics.median((word["bbox"][2] - word["bbox"][0]) /
                                   max(len(word["text"]), 1) for word in words)
    split_gap = max(0.002, char_width * 3.5)
    result = [[words[0]]]
    for word in words[1:]:
        if word["bbox"][0] - result[-1][-1]["bbox"][2] > split_gap:
            result.append([])
        result[-1].append(word)
    return result


def _text(words):
    return " ".join(word["text"] for word in
                    sorted(words, key=lambda word: (round(word["bbox"][1], 4),
                                                   word["bbox"][0])))


def _tag(raw):
    value = re.sub(r"\s*([-_.])\s*", r"\1", raw.translate(_DASHES).upper().strip())
    if (value in _TAG_EXCLUDED or len(value) > 64 or
            not re.fullmatch(r"[A-Z0-9]+(?:[-_.][A-Z0-9]+)*", value)):
        return None
    # A tag header permits unfamiliar prefixes and numeric marks. Long words
    # alone are more likely a notes/footer row than a discrete equipment mark.
    if not (any(char.isdigit() for char in value) or "-" in value or len(value) <= 3):
        return None
    return value


def _title_segments(band):
    return [part for part in _segments(band["words"])
            if re.search(r"\bSCHEDULE(?:S)?\b", _text(part), re.IGNORECASE) and
            not re.search(r"\bNOTES?\b", _text(part), re.IGNORECASE)]


def _column_bounds(columns, left, right):
    centers = [(column["bbox"][0] + column["bbox"][2]) / 2 for column in columns]
    return [left] + [(one + two) / 2 for one, two in zip(centers, centers[1:])] + [right]


def _column_for(word, boundaries):
    center = (word["bbox"][0] + word["bbox"][2]) / 2
    for index in range(len(boundaries) - 1):
        if boundaries[index] <= center < boundaries[index + 1]:
            return index
    return None


def _header_extensions(bands, index, columns, left, right, height):
    """Add nearby wrapped header text; never consume an equipment data row."""
    boundaries = _column_bounds(columns, left, right)
    additions = []
    for other_index in (index - 1, index + 1, index + 2):
        if other_index < 0 or other_index >= len(bands):
            continue
        if other_index == index + 2 and index + 1 not in additions:
            continue
        band = bands[other_index]
        near = bands[index] if other_index != index + 2 else bands[index + 1]
        distance = max(band["top"] - near["bottom"], near["top"] - band["bottom"], 0)
        if distance > height * 1.1 or _title_segments(band):
            continue
        words = [word for word in band["words"]
                 if left <= (word["bbox"][0] + word["bbox"][2]) / 2 < right]
        if not words:
            continue
        by_column = [[] for _ in columns]
        for word in words:
            column_index = _column_for(word, boundaries)
            if column_index is not None:
                by_column[column_index].append(word)
        # An explicit data tag starts a body row, including a generic prefix.
        tag_column = next(i for i, column in enumerate(columns) if column["name"] == "tag")
        tag_words = by_column[tag_column]
        if tag_words and _tag(_text(tag_words)):
            continue
        recognizable = 0
        safe = True
        for i, cell in enumerate(by_column):
            if not cell:
                continue
            cell_text = _text(cell)
            stripped = cell_text.strip("()[]").upper()
            combined = (_text(cell + columns[i]["words"]) if other_index < index
                        else _text(columns[i]["words"] + cell))
            if re.search(r"\d", cell_text) and stripped not in _UNIT_WORDS:
                safe = False
                break
            if (_name(cell_text) or _name(combined) or stripped in _UNIT_WORDS or
                    stripped in {"AIR", "FLOW", "EQUIPMENT", "EQUIP", "NO.", "NUMBER"}):
                recognizable += 1
            else:
                safe = False
                break
        if not safe or not recognizable:
            continue
        additions.append(other_index)
        for i, cell in enumerate(by_column):
            columns[i]["words"].extend(cell)
            columns[i]["bbox"] = _bbox(columns[i]["words"])
            columns[i]["header"] = _text(columns[i]["words"])
            columns[i]["name"] = _name(columns[i]["header"])
    return additions


def _tables(page, bands, height):
    tables, issues = [], []
    consumed = set()
    for band_index, band in enumerate(bands):
        if band_index in consumed:
            continue
        segments = _segments(band["words"])
        anchors = [i for i, segment in enumerate(segments)
                   if _name(_text(segment)) == "tag"]
        if not anchors:
            # A tag word packed together with other column names is not enough
            # evidence to choose their boundaries.
            if any(_normal(word["text"]) in {"TAG", "MARK"} for word in band["words"]):
                if sum(_name(word["text"]) is not None for word in band["words"]) >= 2:
                    issues.append(_issue(page, "ambiguous_schedule_columns",
                                         "Schedule headings are not spatially separated.",
                                         _bbox(band["words"])))
            continue
        if any(re.search(r"\d", _text(segment)) and _name(_text(segment)) is None
               for segment in segments):
            # A staggered adjacent table can put an equipment row on the same
            # baseline as this header. Treating those cells as extra headings
            # would silently mix the two schedules. This layout needs a reader
            # with explicit table-region evidence.
            issues.append(_issue(page, "mixed_schedule_header",
                                 "Headings share a baseline with numeric or equipment text; table regions are unresolved.",
                                 _bbox(band["words"])))
            continue
        partitions = [0]
        x_bounds = [0.0]
        ambiguous = False
        for prior, anchor in zip(anchors, anchors[1:]):
            gaps = [(segments[i + 1][0]["bbox"][0] - segments[i][-1]["bbox"][2], i + 1)
                    for i in range(prior, anchor)]
            gap, split = max(gaps)
            # Without a visible gutter, multiple tag headings might represent
            # one merged/grouped table. Do not assign its data to guessed tables.
            remaining = [value for value, _ in gaps if value != gap]
            if gap <= 0.012 or (remaining and gap < statistics.median(remaining) * 1.5):
                ambiguous = True
                break
            partitions.append(split)
            x_bounds.append((segments[split - 1][-1]["bbox"][2] +
                             segments[split][0]["bbox"][0]) / 2)
        if ambiguous:
            issues.append(_issue(page, "ambiguous_schedule_columns",
                                 "Multiple tag columns have no clear table boundary.",
                                 _bbox(band["words"])))
            continue
        if len(anchors) + len(tables) > MAX_TABLES:
            issues.append(_issue(page, "schedule_table_limit",
                                 "The bounded schedule table limit was reached.",
                                 _bbox(band["words"])))
            break
        partitions.append(len(segments))
        x_bounds.append(1.0)
        for part_index, (start, end) in enumerate(zip(partitions, partitions[1:])):
            selected = segments[start:end]
            columns = [{"words": list(segment), "bbox": _bbox(segment),
                        "header": _text(segment), "name": _name(_text(segment))}
                       for segment in selected]
            if len(columns) > MAX_COLUMNS:
                issues.append(_issue(page, "schedule_column_limit",
                                     "The header exceeds the bounded schedule column limit.",
                                     _bbox([word for segment in selected for word in segment])))
                continue
            if len(columns) < 2 or sum(column["name"] == "tag" for column in columns) != 1:
                continue
            left, right = x_bounds[part_index:part_index + 2]
            extensions = _header_extensions(bands, band_index, columns, left, right, height)
            consumed.update(extensions)
            if (sum(column["name"] is not None for column in columns) < 2 or
                    sum(column["name"] == "tag" for column in columns) != 1):
                issues.append(_issue(page, "unsupported_schedule_header",
                                     "The schedule has no supported tag and field headings.",
                                     _bbox([word for column in columns for word in column["words"]])))
                continue
            for column in columns:
                if column["name"] is None:
                    column["name"] = "other_" + (_normal(column["header"]).lower().replace(" ", "_") or "field")
            header_words = [word for column in columns for word in column["words"]]
            header_box = _bbox(header_words)
            table = {"columns": columns, "left": left, "right": right,
                     "start": max([band_index] + extensions), "header": header_box,
                     "title": None, "issues": []}
            boundaries = _column_bounds(columns, left, right)
            if any(boundaries[i] >= boundaries[i + 1] for i in range(len(boundaries) - 1)):
                issues.append(_issue(page, "ambiguous_schedule_columns",
                                     "Wrapped schedule columns overlap.",
                                     header_box))
                continue
            table["boundaries"] = boundaries
            for previous in reversed(bands[:band_index]):
                if header_box[1] - previous["bottom"] > max(0.08, height * 7):
                    break
                titles = [title for title in _title_segments(previous)
                          if left <= (_bbox(title)[0] + _bbox(title)[2]) / 2 < right]
                if titles:
                    if len(titles) == 1:
                        table["title"] = titles[0]
                    else:
                        table["issues"].append("ambiguous_schedule_title")
                        issues.append(_issue(page, "ambiguous_schedule_title",
                                             "More than one schedule title matches this header.",
                                             header_box))
                    break
            tables.append(table)
    return tables, issues


def _assigned(words, table):
    cells = [[] for _ in table["columns"]]
    ambiguous = set()
    for word in words:
        column = _column_for(word, table["boundaries"])
        if column is None:
            continue
        cells[column].append(word)
        x0, _, x1, _ = word["bbox"]
        left, right = table["boundaries"][column:column + 2]
        outside = max(left - x0, 0) + max(x1 - right, 0)
        if outside > (x1 - x0) * 0.2:
            ambiguous.add(column)
            if x0 < left and column:
                ambiguous.add(column - 1)
            if x1 > right and column + 1 < len(cells):
                ambiguous.add(column + 1)
    return cells, ambiguous


def _is_note_start(words, table):
    parts = _segments(words)
    if not parts:
        return False
    value = _normal(_text(parts[0]))
    first_column_right = table["boundaries"][1]
    return (parts[0][0]["bbox"][0] < first_column_right and
            (value in {"NOTES", "NOTE", "SCHEDULE NOTES", "GENERAL NOTES", "TOTAL", "TOTALS"} or
             value.startswith("NOTES ") or value.startswith("GENERAL NOTES ")))


def _rows_for_table(page, table, all_tables, bands, height):
    rows, issues = [], []
    tag_column = next(i for i, column in enumerate(table["columns"]) if column["name"] == "tag")
    pending = None
    lower_limit = 1.0
    for other in all_tables:
        overlap = min(table["right"], other["right"]) - max(table["left"], other["left"])
        if other["header"][1] > table["header"][3] and overlap > 0:
            lower_limit = min(lower_limit, other["header"][1])

    def finish():
        nonlocal pending
        if pending is None:
            return
        row_words = [word for cell in pending["cells"] for word in cell]
        row_issues = list(table["issues"])
        fields = []
        for index, (column, words) in enumerate(zip(table["columns"], pending["cells"])):
            raw_value = _text(words) if words else None
            field = {"name": column["name"], "header": column["header"],
                     "value": raw_value,
                     "source": _source(page, _bbox(words)) if words else None,
                     "header_source": _source(page, column["bbox"])}
            if index in pending["ambiguous"]:
                field["value"] = None
                field["raw_value"] = raw_value
                field["ambiguous"] = True
            fields.append(field)
        if pending["ambiguous"]:
            row_issues.append("ambiguous_cell_columns")
            issues.append(_issue(page, "ambiguous_cell_columns",
                                 "A cell crosses inferred column boundaries; its field stays unresolved.",
                                 _bbox(row_words)))
        quantity_fields = [field for field in fields if field["name"] == "quantity"]
        quantity = quantity_fields[0]["value"] if len(quantity_fields) == 1 else None
        if len(quantity_fields) > 1:
            row_issues.append("ambiguous_quantity_columns")
        if quantity is not None and quantity.strip().translate(_DASHES).upper() in _UNKNOWN:
            quantity = None
        if quantity is not None and not re.fullmatch(
                r"\d+(?:\.\d+)?(?:\s*(?:EA\.?|EACH|UNITS?|NOS?\.?))?",
                quantity.strip(), flags=re.IGNORECASE):
            # Keep the complete written cell in fields, but a note, range or
            # expression is not a resolved quantity just because it is present.
            quantity = None
            row_issues.append("quantity_expression_unresolved")
        type_fields = [field for field in fields if field["name"] == "equipment_type" and field["value"]]
        equipment_type = type_fields[0]["value"] if len(type_fields) == 1 else None
        if len(type_fields) > 1:
            row_issues.append("ambiguous_equipment_type")
        if table["title"]:
            title_text = _text(table["title"])
            title_source = _source(page, _bbox(table["title"]))
            fields.append({"name": "schedule_title", "header": "SCHEDULE TITLE",
                           "value": title_text, "source": title_source})
            if equipment_type is None and len(type_fields) < 2:
                equipment_type = re.sub(r"\s*\bSCHEDULE(?:S)?\b\s*", " ", title_text,
                                        flags=re.IGNORECASE).strip(" -:")
                if not equipment_type or equipment_type.upper() in {"EQUIPMENT", "MECHANICAL"}:
                    equipment_type = None
        if quantity is None:
            row_issues.append("quantity_unknown")
        row = {"tag": pending["tag"], "equipment_type": equipment_type,
               "quantity": quantity, "source": _source(page, _bbox(row_words)),
               "fields": fields, "issues": row_issues, "status": "candidate"}
        identity = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        row["id"] = hashlib.sha256(identity).hexdigest()
        rows.append(row)
        pending = None

    for band in bands[table["start"] + 1:]:
        if band["top"] >= lower_limit:
            break
        words = [word for word in band["words"]
                 if table["left"] <= (word["bbox"][0] + word["bbox"][2]) / 2 < table["right"]]
        if not words:
            continue
        if _title_segments({"words": words}) or _is_note_start(words, table):
            break
        cells, ambiguous = _assigned(words, table)
        tag_text = _text(cells[tag_column]) if cells[tag_column] else ""
        tag = _tag(tag_text) if tag_column not in ambiguous else None
        if tag:
            finish()
            pending = {"tag": tag, "cells": cells, "ambiguous": ambiguous, "bottom": band["bottom"]}
        elif tag_text:
            finish()
            issues.append(_issue(page, "unsupported_tag_expression",
                                 "The tag cell cannot be read as one discrete equipment tag.",
                                 _bbox(words), tag_text))
        elif pending is not None and band["top"] - pending["bottom"] <= height * 1.8:
            for index, cell in enumerate(cells):
                pending["cells"][index].extend(cell)
            pending["ambiguous"].update(ambiguous)
            pending["bottom"] = band["bottom"]
        elif any(cells):
            finish()
            issues.append(_issue(page, "unassociated_schedule_text",
                                 "Text below the schedule header has no safely associated equipment tag.",
                                 _bbox(words), _text(words)))
        if len(rows) >= MAX_ROWS:
            issues.append(_issue(page, "schedule_row_limit",
                                 "The bounded schedule row limit was reached.", table["header"]))
            pending = None
            break
    finish()
    return rows, issues


def parse_schedules(page):
    """Return candidate rows and explicit unresolved layout issues for one page."""
    if not isinstance(page, dict):
        raise ValueError("A positioned page object is required.")
    try:
        words = _words(page)
    except ValueError as error:
        return {"rows": [], "issues": [_issue(page, "invalid_schedule_layout", str(error), [0, 0, 1, 1])]}
    if not words:
        return {"rows": [], "issues": [_issue(page, "schedule_text_missing",
                                             "The page has no positioned text to read.", [0, 0, 1, 1])]}
    height = statistics.median(word["bbox"][3] - word["bbox"][1] for word in words)
    bands = _bands(words)
    tables, issues = _tables(page, bands, height)
    if any(issue["code"] == "mixed_schedule_header" for issue in issues):
        # Other headers on this page may belong to the same staggered layout.
        # Without explicit regions, none has a safe association to body rows.
        return {"rows": [], "issues": issues}
    rows = []
    for table in tables:
        found, table_issues = _rows_for_table(page, table, tables, bands, height)
        rows.extend(found)
        issues.extend(table_issues)
        if len(rows) > MAX_ROWS:
            rows = rows[:MAX_ROWS]
            issues.append(_issue(page, "schedule_row_limit",
                                 "The bounded schedule row limit was reached.", table["header"]))
            break
    by_tag = {}
    for row in rows:
        by_tag.setdefault(row["tag"], []).append(row)
    for tag_rows in by_tag.values():
        if len(tag_rows) > 1:
            for row in tag_rows:
                row["issues"].append("duplicate_schedule_tag")
            issues.append(_issue(page, "duplicate_schedule_tag",
                                 "This tag occurs in multiple schedule rows; rows remain separate.",
                                 tag_rows[0]["source"]["bbox"], tag_rows[0]["tag"]))
    if not tables:
        issues.append(_issue(page, "schedule_header_missing",
                             "No separated tag/mark and field headings were found.", [0, 0, 1, 1]))
    elif not rows:
        issues.append(_issue(page, "schedule_rows_missing",
                             "A schedule header was found but no supported equipment rows were read.",
                             [0, 0, 1, 1]))
    return {"rows": rows, "issues": issues}
