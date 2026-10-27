"""Read written scale declarations without assigning or verifying a drawing view.

Only labelled numeric ratios and explicit imperial scale expressions are read.
Unlabelled imperial expressions must be a complete fractional-inch-to-feet line;
ordinary dimensions, unit conversions and equipment ratios are not scale facts.
The exact source line and normalized page rectangle remain the evidence.
"""
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
import hashlib
import json
import math
import re


PARSER_VERSION = 1
MAX_LINES = 100000
MAX_TEXT = 2000000
MAX_PARSE = 4096
MAX_CANDIDATES = 1000
_MAX_NUMBER = Fraction(10 ** 12)
_MAX_RESULT = Fraction(10 ** 12)
_METERS_PER_INCH = Fraction(127, 5000)
_SCALE = re.compile(r"\bSCALE(?=\b|[0-9])", re.IGNORECASE)
_NTS = re.compile(r"(?<![A-Z0-9_.-])N\.?\s*T\.?\s*S\.?(?![A-Z0-9_.-])", re.IGNORECASE)
_NOT_TO_SCALE = re.compile(r"\bNOT\s+TO\s+SCALE\b", re.IGNORECASE)
_DECIMAL = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
_FRACTION = r"(?:[0-9]+\s*[- ]\s*)?[0-9]+\s*/\s*[0-9]+"
_INCH_NUMBER = r"(?:" + _FRACTION + r"|" + _DECIMAL + r")"
_INCH_UNIT = r'(?:"|IN(?:CH(?:ES)?)?\.?)'
_FOOT_UNIT = r"(?:'|FT\.?|FEET|FOOT)"
_RATIO = re.compile(r"(?P<paper>" + _DECIMAL + r")\s*:\s*(?P<real>" + _DECIMAL + r")",
                    re.IGNORECASE)
_IMPERIAL = re.compile(
    r"(?P<paper>" + _INCH_NUMBER + r")\s*" + _INCH_UNIT +
    r"\s*=\s*(?P<feet>[0-9]+)\s*" + _FOOT_UNIT +
    r"(?:\s*-?\s*(?P<inches>" + _INCH_NUMBER + r")\s*" + _INCH_UNIT + r")?",
    re.IGNORECASE)
_CHARACTERS = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u2033": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    "\u2044": "/", "\u2236": ":",
})
_VULGAR = {
    "\u00bc": "1/4", "\u00bd": "1/2", "\u00be": "3/4",
    "\u2150": "1/7", "\u2151": "1/9", "\u2152": "1/10",
    "\u2153": "1/3", "\u2154": "2/3", "\u2155": "1/5",
    "\u2156": "2/5", "\u2157": "3/5", "\u2158": "4/5",
    "\u2159": "1/6", "\u215a": "5/6", "\u215b": "1/8",
    "\u215c": "3/8", "\u215d": "5/8", "\u215e": "7/8",
}


def _normalized(text):
    # Add a separator for mixed-number glyphs (1½ becomes 1 1/2, not 11/2).
    result = "".join((" " + _VULGAR[char]) if char in _VULGAR else char for char in text)
    return " ".join(result.translate(_CHARACTERS).split())


def _number(text):
    compact = text.strip()
    if len(compact) > 48 or sum(char.isdigit() for char in compact) > 24:
        raise ValueError("Scale number exceeds the supported size.")
    if "/" in compact:
        mixed = re.fullmatch(r"(?:(?P<whole>[0-9]+)\s*[- ]\s*)?"
                             r"(?P<num>[0-9]+)\s*/\s*(?P<den>[0-9]+)", compact)
        if not mixed:
            raise ValueError("Unsupported scale fraction.")
        denominator = int(mixed["den"])
        numerator = int(mixed["num"])
        whole = int(mixed["whole"] or "0")
        if denominator == 0 or denominator > 10 ** 12:
            raise ValueError("Unsupported scale fraction denominator.")
        if mixed["whole"] is not None and numerator >= denominator:
            raise ValueError("Ambiguous mixed scale fraction.")
        value = Fraction(whole) + Fraction(numerator, denominator)
    else:
        if not re.fullmatch(_DECIMAL, compact) or len(compact.partition(".")[2]) > 12:
            raise ValueError("Unsupported scale number.")
        value = Fraction(Decimal(compact.replace(",", "")))
    if value < 0 or value > _MAX_NUMBER:
        raise ValueError("Scale number is outside the supported range.")
    return value


def _decimal_string(value):
    if value <= 0 or value > _MAX_RESULT:
        raise ValueError("The declared scale is outside the supported range.")
    # Reduce exact rational units before the one rounded division. Equivalent
    # ratio and imperial declarations therefore produce identical decimals.
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        converted = Decimal(value.numerator) / Decimal(value.denominator)
        result = format(converted, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result


def _ratio_value(match):
    paper, real = _number(match["paper"]), _number(match["real"])
    if paper == 0 or real == 0:
        raise ValueError("Scale ratio must be positive.")
    return _decimal_string(real * _METERS_PER_INCH / (paper * 72))


def _imperial_value(match):
    paper = _number(match["paper"])
    feet = _number(match["feet"])
    inches = _number(match["inches"]) if match["inches"] else Fraction(0)
    if paper == 0 or inches >= 12 or feet * 12 + inches == 0:
        raise ValueError("Scale lengths must be positive, with inches below twelve.")
    return _decimal_string((feet * 12 + inches) * _METERS_PER_INCH / (paper * 72))


def _read(text):
    """Return notation/value, or None for text that is not a scale declaration."""
    marker = list(_SCALE.finditer(text))
    nts = list(_NTS.finditer(text)) + list(_NOT_TO_SCALE.finditer(text))
    if len(text) > MAX_PARSE:
        return ("unknown", None) if marker or nts else None
    if nts:
        # NTS and a numeric declaration on one line cannot silently select a
        # usable scale. A second scale/nts declaration is unresolved as well.
        has_numeric = _RATIO.search(text) is not None or bool(re.search(r"[0-9].*=", text))
        extra_markers = [value for value in marker
                         if not any(item.start() <= value.start() < item.end()
                                    for item in nts)]
        if has_numeric or len(nts) != 1 or len(extra_markers) > 1:
            return "unknown", None
        return "nts", None
    if len(marker) > 1:
        return "unknown", None
    if marker:
        tail = text[marker[0].end():].strip()
        # The colon/equals after the word SCALE is a label separator, not a
        # ratio or length operator. No suffix/second value is guessed away.
        tail = re.sub(r"^[:=]\s*", "", tail, count=1)
        match = _RATIO.fullmatch(tail)
        if match:
            try:
                return "ratio", _ratio_value(match)
            except (ValueError, ArithmeticError):
                return "unknown", None
        match = _IMPERIAL.fullmatch(tail)
        if match:
            try:
                return "imperial", _imperial_value(match)
            except (ValueError, ArithmeticError):
                return "unknown", None
        return "unknown", None
    match = _IMPERIAL.fullmatch(text)
    # A complete fractional inch = feet label is a conventional architectural
    # scale expression. Unlabelled integer unit conversions remain ordinary text.
    if match and "/" in match["paper"]:
        try:
            return "imperial", _imperial_value(match)
        except (ValueError, ArithmeticError):
            return "unknown", None
    return None


def _source(page):
    result = {key: page.get(key) for key in ("revision_id", "index", "sheet_id")}
    if (type(result["index"]) is not int or not 0 <= result["index"] <= 10 ** 9 or
            any(not isinstance(result[key], str) or not result[key] or len(result[key]) > 128
                for key in ("revision_id", "sheet_id"))):
        raise ValueError("Scale labels require complete page source identities.")
    return result


def _line(line):
    if (not isinstance(line, dict) or not isinstance(line.get("text"), str) or
            not isinstance(line.get("bbox"), list) or len(line["bbox"]) != 4):
        raise ValueError("Scale labels require positioned text lines.")
    bounds = line["bbox"]
    if (any(type(number) not in (int, float) or not math.isfinite(number) for number in bounds) or
            not (0 <= bounds[0] < bounds[2] <= 1 and 0 <= bounds[1] < bounds[3] <= 1)):
        raise ValueError("A scale-label text rectangle is outside the page.")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in line["text"]):
        raise ValueError("A scale-label line has unsupported control characters.")
    return line["text"], list(bounds)


def parse_scale_labels(page):
    """Return unverified evidence candidates; never infer a view or quantity.

    Invalid positioned-page structure raises ValueError. Unsupported, conflicting
    or malformed written scale declarations remain unknown evidence candidates.
    """
    if not isinstance(page, dict):
        raise ValueError("A positioned page is required.")
    source = _source(page)
    lines = page.get("lines")
    if not isinstance(lines, list) or len(lines) > MAX_LINES:
        raise ValueError("The positioned line collection exceeds the supported format.")
    candidates, seen = [], set()
    total = 0
    for line in lines:
        raw, bounds = _line(line)
        total += len(raw)
        if total > MAX_TEXT:
            raise ValueError("The positioned page text exceeds the scale-reader limit.")
        normalized = _normalized(raw)
        reading = _read(normalized)
        if reading is None:
            continue
        notation, meters_per_point = reading
        candidate = {"source": dict(source), "evidence": {"text": raw, "bbox": bounds},
                     "label": raw, "meters_per_point": meters_per_point,
                     "notation": notation, "origin": "declared"}
        identity = {"reader_version": PARSER_VERSION, "candidate": candidate}
        candidate["id"] = hashlib.sha256(json.dumps(
            identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        if candidate["id"] in seen:
            continue
        candidates.append(candidate)
        seen.add(candidate["id"])
        if len(candidates) > MAX_CANDIDATES:
            raise ValueError("The scale-label candidate limit was exceeded.")
    return candidates
