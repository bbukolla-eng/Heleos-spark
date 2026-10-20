"""Qualified air-device attributes: validation, exact grouping and imperial display.

This helper is pure. It has no local imports, no I/O, no model call and no
quantity of any kind. It validates one air-device attribute record, preserves
the original units and text that record supplied, derives an exact rational
grouping form in inches, and separately renders a six-place imperial display.

Three separations are deliberate and load bearing under the approved rules:

* Grouping identity comes only from ``canonical_attributes``. The rounded
  display of ``imperial_attributes`` is never a grouping or equality basis
  (rule A03: no rounding-based equivalence).
* Face, neck and opening sizes, assembly length and slot count stay distinct
  attributes. Nothing here converts a length or a slot count into an each
  quantity (rules A03 and A07).
* A family value classifies an observation; it does not admit that observation
  into a count. Inclusion remains the count engine's application of rule A01.

Rounding uses integer arithmetic only, so the display is stable under any
ambient decimal precision, rounding mode or trap setting.
"""
from fractions import Fraction
import re

VERSION = "air-device-attributes-1"

FIELDS = ("family", "type_tag", "system", "service", "work_status",
          "face_size", "neck_size", "opening_size", "assembly_length", "slot_count")

STATES = ("known", "unknown", "not_supplied", "not_applicable")
EVIDENCE_REQUIRED_STATES = ("known", "not_applicable")
FAMILIES = ("diffuser", "register", "grille", "linear_diffuser", "mechanical_louver",
            "architectural_louver", "equipment", "accessory", "other")
WORK_STATUSES = ("new_install", "existing_to_remain", "demolition", "relocated")
SHAPES = {"rectangular": 2, "oval": 2, "round": 1}
# Exact inch factors; mm and m stay rational, never 0.0393700787.
UNITS = {"in": Fraction(1), "ft": Fraction(12), "mm": Fraction(5, 127), "m": Fraction(5000, 127)}
ERROR_CODES = frozenset({"structure_invalid", "state_invalid", "value_invalid",
                         "dimension_invalid", "evidence_invalid"})

TEXT_FIELDS = ("type_tag", "system", "service")
SIZE_FIELDS = ("face_size", "neck_size", "opening_size")
DISPLAY_PLACES = 6
MAX_EVIDENCE_IDS = 2000
MAX_FIELD_EVIDENCE = 128
MAX_EVIDENCE_ID_CHARS = 160
MAX_TEXT_CHARS = 256
MAX_ORIGINAL_TEXT_CHARS = 512
MAX_SLOT_COUNT = 1000000
MAX_DIMENSION_CHARS = 40
MAX_FRACTIONAL_PLACES = 18
MAX_DIMENSION_VALUE = Fraction(1000000000000)

_FIELD_KEYS = ("state", "value", "evidence_ids")
_SIZE_KEYS = ("shape", "dimensions", "unit", "original_text")
_LENGTH_KEYS = ("value", "unit", "original_text")
_DECIMAL_SYNTAX = re.compile(r"\A[0-9]+(?:\.[0-9]+)?\Z")
_SCALE = 10 ** DISPLAY_PLACES


class AirDeviceAttributeError(ValueError):
    """Stable machine codes are the members of ERROR_CODES."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _reject(code, message):
    raise AirDeviceAttributeError(code, message)


def _require_mapping(value, keys, label):
    """Containers are exactly dict; a subclass cannot override the checks below."""
    if type(value) is not dict:
        _reject("structure_invalid", "The " + label + " must be exactly a dict.")
    if len(value) != len(keys) or set(value) != set(keys):
        _reject("structure_invalid",
                "The " + label + " requires exactly these keys: " + ", ".join(keys) + ".")
    return value


def _has_controls(value):
    # Ordinary Unicode spaces, including NBSP in extracted PDF dimensions, are
    # source text. Keep them; canonical grouping handles whitespace separately.
    return any(ord(c) < 32 or 127 <= ord(c) < 160 or 0xD800 <= ord(c) <= 0xDFFF
               for c in value)


def _printable_text(value, limit, code, label):
    if type(value) is not str:
        _reject(code, "The " + label + " must be exactly a str.")
    if not 1 <= len(value) <= limit:
        _reject(code, "The " + label + " must hold 1 to " + str(limit) + " characters.")
    if _has_controls(value):
        _reject(code, "The " + label + " must not contain control characters or surrogates.")
    if not value.strip():
        _reject(code, "The " + label + " must not be whitespace only.")
    return value


def _dimension(value, label):
    """Return the validated decimal string; floats and coercions are refused."""
    if type(value) is not str:
        _reject("dimension_invalid",
                "The " + label + " must be a decimal string, not " + type(value).__name__ + ".")
    if not 1 <= len(value) <= MAX_DIMENSION_CHARS:
        _reject("dimension_invalid",
                "The " + label + " must hold 1 to " + str(MAX_DIMENSION_CHARS) + " characters.")
    if not _DECIMAL_SYNTAX.match(value):
        _reject("dimension_invalid",
                "The " + label + " must match [0-9]+(.[0-9]+)? with no sign or exponent.")
    if "." in value and len(value.split(".")[1]) > MAX_FRACTIONAL_PLACES:
        _reject("dimension_invalid",
                "The " + label + " must not exceed " + str(MAX_FRACTIONAL_PLACES) + " fractional places.")
    number = Fraction(value)
    if number <= 0:
        _reject("dimension_invalid", "The " + label + " must be positive.")
    if number > MAX_DIMENSION_VALUE:
        _reject("dimension_invalid", "The " + label + " exceeds the bounded maximum value.")
    return value


def _unit(value, label):
    if type(value) is not str or value not in UNITS:
        _reject("value_invalid", "The " + label + " unit must be one of: " + ", ".join(sorted(UNITS)) + ".")
    return value


def _known_size(field, value):
    _require_mapping(value, _SIZE_KEYS, field + " value")
    shape = value["shape"]
    if type(shape) is not str or shape not in SHAPES:
        _reject("value_invalid",
                "The " + field + " shape must be one of: " + ", ".join(sorted(SHAPES)) + ".")
    dimensions = value["dimensions"]
    if type(dimensions) is not list or len(dimensions) != SHAPES[shape]:
        _reject("value_invalid", "A " + shape + " " + field + " requires exactly "
                + str(SHAPES[shape]) + " ordered dimensions.")
    ordered = [_dimension(item, field + " dimension " + str(index))
               for index, item in enumerate(dimensions)]
    return {"shape": shape,
            "dimensions": ordered,
            "unit": _unit(value["unit"], field),
            "original_text": _printable_text(value["original_text"], MAX_ORIGINAL_TEXT_CHARS,
                                             "value_invalid", field + " original_text")}


def _known_length(value):
    _require_mapping(value, _LENGTH_KEYS, "assembly_length value")
    return {"value": _dimension(value["value"], "assembly_length value"),
            "unit": _unit(value["unit"], "assembly_length"),
            "original_text": _printable_text(value["original_text"], MAX_ORIGINAL_TEXT_CHARS,
                                             "value_invalid", "assembly_length original_text")}


def _known_value(field, value):
    """Validate one known field value and return an independent copy of it."""
    if field == "family":
        if type(value) is not str or value not in FAMILIES:
            _reject("value_invalid", "The family must be one of: " + ", ".join(FAMILIES) + ".")
        return value
    if field == "work_status":
        if type(value) is not str or value not in WORK_STATUSES:
            _reject("value_invalid", "The work_status must be one of: " + ", ".join(WORK_STATUSES)
                    + "; an unestablished status uses state=unknown.")
        return value
    if field in TEXT_FIELDS:
        return _printable_text(value, MAX_TEXT_CHARS, "value_invalid", field)
    if field in SIZE_FIELDS:
        return _known_size(field, value)
    if field == "assembly_length":
        return _known_length(value)
    if type(value) is not int:  # bool is a subclass and is refused here
        _reject("value_invalid", "The slot_count must be exactly an int.")
    if not 1 <= value <= MAX_SLOT_COUNT:
        _reject("value_invalid", "The slot_count must be 1 to " + str(MAX_SLOT_COUNT) + ".")
    return value


def _evidence_id(value, label):
    if type(value) is not str:
        _reject("evidence_invalid", "An evidence identifier in " + label + " must be exactly a str.")
    if not 1 <= len(value) <= MAX_EVIDENCE_ID_CHARS:
        _reject("evidence_invalid", "An evidence identifier in " + label + " must hold 1 to "
                + str(MAX_EVIDENCE_ID_CHARS) + " characters.")
    if _has_controls(value) or not value.strip():
        _reject("evidence_invalid",
                "An evidence identifier in " + label + " must not be blank or hold control characters.")
    return value


def _field_evidence(field, state, raw, supplied):
    if type(raw) is not list:
        _reject("evidence_invalid", "The " + field + " evidence_ids must be exactly a list.")
    if len(raw) > MAX_FIELD_EVIDENCE:
        _reject("evidence_invalid", "The " + field + " carries more than "
                + str(MAX_FIELD_EVIDENCE) + " evidence references.")
    ordered = [_evidence_id(item, field) for item in raw]
    if len(set(ordered)) != len(ordered):
        _reject("evidence_invalid", "The " + field + " repeats an evidence reference.")
    if state in EVIDENCE_REQUIRED_STATES and not ordered:
        _reject("evidence_invalid",
                "A " + state + " " + field + " requires at least one evidence reference.")
    for item in ordered:
        if item not in supplied:
            _reject("evidence_invalid",
                    "The " + field + " references evidence that was not supplied: " + item + ".")
    return ordered


def _supplied_evidence(evidence_ids):
    """Validate the caller's evidence list and return it as a lookup set."""
    if type(evidence_ids) is not list:
        _reject("evidence_invalid", "The evidence_ids argument must be exactly a list.")
    if len(evidence_ids) > MAX_EVIDENCE_IDS:
        _reject("evidence_invalid", "At most " + str(MAX_EVIDENCE_IDS) + " evidence identifiers are accepted.")
    ordered = [_evidence_id(item, "evidence_ids") for item in evidence_ids]
    supplied = set(ordered)
    if len(supplied) != len(ordered):
        _reject("evidence_invalid", "The evidence_ids argument repeats an identifier.")
    return supplied


def _contained_evidence(attributes):
    """The union of the record's own references, for revalidation without a list.

    Callers bind evidence identifiers to actual sources separately; this union
    only lets the structural contract be rechecked on an already normalized
    record. The same identifier may back several fields.
    """
    _require_mapping(attributes, FIELDS, "attributes")
    supplied = set()
    for field in FIELDS:
        entry = _require_mapping(attributes[field], _FIELD_KEYS, "field " + field)
        raw = entry["evidence_ids"]
        if type(raw) is not list:
            _reject("evidence_invalid", "The " + field + " evidence_ids must be exactly a list.")
        if len(raw) > MAX_FIELD_EVIDENCE:
            _reject("evidence_invalid", "The " + field + " carries more than "
                    + str(MAX_FIELD_EVIDENCE) + " evidence references.")
        for item in raw:
            supplied.add(_evidence_id(item, field))
    if len(supplied) > MAX_EVIDENCE_IDS:
        _reject("evidence_invalid", "The record references more than "
                + str(MAX_EVIDENCE_IDS) + " distinct evidence identifiers.")
    return supplied


def _validated(attributes, supplied):
    """Return a fresh, fully validated record; the caller's object is untouched."""
    _require_mapping(attributes, FIELDS, "attributes")
    result = {}
    for field in FIELDS:
        entry = _require_mapping(attributes[field], _FIELD_KEYS, "field " + field)
        state = entry["state"]
        if type(state) is not str or state not in STATES:
            _reject("state_invalid", "The " + field + " state must be one of: " + ", ".join(STATES) + ".")
        value = entry["value"]
        if state == "known":
            value = _known_value(field, value)
        elif value is not None:
            _reject("state_invalid", "A " + state + " " + field + " must carry a null value.")
        result[field] = {"state": state,
                         "value": value,
                         "evidence_ids": _field_evidence(field, state, entry["evidence_ids"], supplied)}
    return result


def _inches(text, unit):
    """Exact rational inches; the decimal string was already validated."""
    return Fraction(text) * UNITS[unit]


def _fraction_form(number):
    return {"n": str(number.numerator), "d": str(number.denominator)}


def _display(number):
    """Six-place half-even rendering by integer arithmetic on a positive rational."""
    scaled, remainder = divmod(number.numerator * _SCALE, number.denominator)
    doubled = remainder * 2
    if doubled > number.denominator or (doubled == number.denominator and scaled % 2):
        scaled += 1
    whole, part = divmod(scaled, _SCALE)
    return "{0}.{1:0{2}d}".format(whole, part, DISPLAY_PLACES)


def _canonical_value(field, entry):
    if entry["state"] != "known":
        return None
    value = entry["value"]
    if field in TEXT_FIELDS:
        # Collapse whitespace and casefold for grouping only; system and service
        # remain separate fields and a service name never supplies a system.
        return " ".join(value.split()).casefold()
    if field in SIZE_FIELDS:
        return {"shape": value["shape"],
                "dimensions": [_fraction_form(_inches(item, value["unit"]))
                               for item in value["dimensions"]],
                "unit": "in"}
    if field == "assembly_length":
        return {"value": _fraction_form(_inches(value["value"], value["unit"])), "unit": "in"}
    return value


def _imperial_value(field, entry):
    if entry["state"] != "known":
        return None
    value = entry["value"]
    if field in SIZE_FIELDS:
        return {"shape": value["shape"],
                "dimensions": [_display(_inches(item, value["unit"])) for item in value["dimensions"]],
                "unit": "in",
                "original_text": value["original_text"]}
    if field == "assembly_length":
        return {"value": _display(_inches(value["value"], value["unit"])),
                "unit": "in",
                "original_text": value["original_text"]}
    return value


def normalize_attributes(attributes, evidence_ids):
    """Validate one attribute record against supplied evidence identifiers.

    Returns an independent deep copy that preserves the source units and text
    exactly as given. Nothing is coerced, rounded or reordered.
    """
    return _validated(attributes, _supplied_evidence(evidence_ids))


def canonical_attributes(attributes):
    """Return the exact semantic grouping form, one {state, value} per field.

    Dimensional values become exact reduced rational inches. This is a grouping
    key, never a display and never a physical identity: two observations with
    the same canonical form are still separate physical assemblies (rule A02).
    """
    validated = _validated(attributes, _contained_evidence(attributes))
    return {field: {"state": validated[field]["state"],
                    "value": _canonical_value(field, validated[field])}
            for field in FIELDS}


def imperial_attributes(attributes):
    """Return a display copy with known dimensional values shown in inches.

    Evidence identifiers and original_text are retained. The six-place decimal
    strings are display only; unequal values can render identically, so they
    must never be compared, grouped or summed.
    """
    validated = _validated(attributes, _contained_evidence(attributes))
    return {field: {"state": validated[field]["state"],
                    "value": _imperial_value(field, validated[field]),
                    "evidence_ids": list(validated[field]["evidence_ids"])}
            for field in FIELDS}
