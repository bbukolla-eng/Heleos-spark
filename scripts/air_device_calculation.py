"""Deterministic source-bound air-device each counts under approved rules A01-A12.

This kernel is pure. It reads no drawing, image, model, database, network or
scale artifact. It accepts one fully explicit request of source contexts,
evidence, observations, admissions and explicit relationships, and returns the
deterministic accounting rows, groups, declarations and totals that the approved
rules allow. The caller persists the exact request and result; nothing here is
saved, and no final quantity is ever taken from the caller.

Four separations are load bearing:

* Physical occurrence, unresolved correspondence, grouping information and scope
  coverage are distinct states. A null ``physical_each`` means the physical
  identity is not established; it never claims zero devices (rule A11).
* An each count depends on source-bound instance identity, never on drawing
  scale, rounding or rendering. No scale value exists in this contract (A08).
* Declarations describe; they never manufacture instances or completeness. A
  schedule quantity stays a separate declared value beside the observed one (A06).
* Grouping identity comes only from the attribute helper's canonical form. The
  six-place imperial rendering is presentation and is never compared (A03).

Only the two accepted sibling helpers are imported, through an explicit path
loader, so the package can be relocated without a package installation.
"""
import hashlib
import importlib.util
import json
import math
from pathlib import Path

VERSION = "air-device-calculation-1"
REQUEST_SCHEMA = "air-device-calculation-request-1"
OBSERVATION_SCHEMA = "air-device-observation-1"
RESULT_SCHEMA = "air-device-calculation-result-1"

_HERE = Path(__file__).resolve().parent
_MAX_MODULE_BYTES = 1024 * 1024
_KERNEL_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _load_sibling(name):
    """Load an accepted sibling helper by exact path, without a package install."""
    path = _HERE / (name + ".py")
    with path.open("rb") as stream:
        raw = stream.read(_MAX_MODULE_BYTES + 1)
    if len(raw) > _MAX_MODULE_BYTES:
        raise ValueError("The helper implementation exceeds its bounded maximum.")
    spec = importlib.util.spec_from_file_location("air_device_kernel_" + name, path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    module._SOURCE_SHA256 = hashlib.sha256(raw).hexdigest()
    return module


attributes = _load_sibling("air_device_attributes")
rules = _load_sibling("air_device_rules")
_LOADED_DIGESTS = {"air_device_calculation.py": _KERNEL_SOURCE_SHA256,
                   "air_device_attributes.py": attributes._SOURCE_SHA256,
                   "air_device_rules.py": rules._SOURCE_SHA256}

FIELDS = attributes.FIELDS
ELIGIBLE_FAMILIES = ("diffuser", "register", "grille", "linear_diffuser", "mechanical_louver")
ROUTED_FAMILIES = ("architectural_louver", "equipment", "accessory", "other")
SOURCE_ROLES = ("plan", "enlarged_plan", "detail", "legend", "schedule",
                "specification", "catalog", "other")
ELIGIBLE_ROLES = ("plan", "enlarged_plan", "detail")
DEPICTIONS = ("physical", "legend", "schedule", "generic_detail", "tag_only", "unknown", "excluded")
NON_INSTALLED_DEPICTIONS = ("legend", "schedule", "generic_detail", "excluded")
ADMISSION_ACTIONS = ("include", "exclude", "unresolved")
CORRESPONDENCE_STATES = ("same", "distinct", "unresolved")
COVERAGE_STATES = ("complete", "partial", "unknown")
EVIDENCE_KINDS = ("graphic", "text")

REQUEST_KEYS = ("schema", "binding", "scope", "sources", "evidence", "observations", "decisions",
                "correspondences", "multiplicities", "relocations", "schedules", "coverage")
SCOPE_KEYS = ("id", "version", "source_keys", "group_by", "required_fields", "work_statuses")
SOURCE_KEYS = ("revision_id", "index", "sheet_id", "geometry_fingerprint")
CONTEXT_KEYS = ("source", "artifact_sha256", "role")
EVIDENCE_KEYS = ("id", "source", "bbox", "kind", "text", "artifact_sha256")
OBSERVATION_KEYS = ("schema", "id", "source", "bbox", "depiction", "attributes", "evidence_ids", "issues")
DECISION_KEYS = ("observation_id", "observation_sha256", "action", "reason", "evidence_ids")
CORRESPONDENCE_KEYS = ("id", "members", "state", "evidence_ids")
MULTIPLICITY_KEYS = ("id", "members", "representative", "each", "scope_text", "evidence_ids")
RELOCATION_KEYS = ("id", "members", "remove_members", "reinstall_members", "remove_operation_id",
                   "reinstall_operation_id", "reused", "evidence_ids")
SCHEDULE_KEYS = ("id", "members", "declared_each", "attributes", "evidence_ids")
BINDING_KEYS = ("observation_id", "observation_sha256")
COVERAGE_KEYS = ("state", "basis_sha256", "evidence_ids", "unresolved_requirements")
ROW_KEYS = ("row_id", "member_ids", "relationship_ids", "source_keys", "evidence_ids", "attributes",
            "canonical_attributes", "conflicts", "issues", "physical_each", "requested",
            "operations", "new_purchase_each", "dependency_sha256")
GROUP_KEYS = ("group_id", "key", "row_ids", "known_subtotal_each", "physical_known_each",
              "total_each", "complete", "issues")
DECLARATION_KEYS = ("id", "declared_each", "observed_each", "state", "row_ids", "member_ids", "issues")
RESULT_KEYS = ("schema", "version", "request_sha256", "binding", "scope", "rows", "groups",
               "declarations", "issues", "complete", "known_subtotal_each", "total_each",
               "physical_known_each")

MAX_DEPTH = 24
MAX_COLLECTION = 20000
MAX_STRING_CHARS = 32768
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024
MAX_LIST = 2000
MAX_MEMBERS = 128
MAX_ID_CHARS = 160
MAX_TEXT_CHARS = 4000
MAX_REASON_CHARS = 1000
MAX_SCOPE_TEXT_CHARS = 1000
MAX_ISSUE_CHARS = 160
MAX_REQUIREMENT_CHARS = 1000
MAX_QUANTITY = 1000000
MAX_SOURCE_INDEX = 4294967295
INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1

ERROR_CODES = frozenset({"structure_invalid", "value_invalid", "identifier_invalid",
                         "reference_invalid", "limit_exceeded", "binding_invalid",
                         "attribute_invalid"})

# Issue codes are a closed vocabulary. Attribute problems append ":<field>".
ISSUE_CODES = frozenset({
    "outside_selected_scope", "source_stale", "non_installed_depiction", "excluded_depiction",
    "depiction_unresolved", "physical_instance_unresolved", "family_unresolved", "class_excluded",
    "graphic_evidence_missing", "admission_missing", "admission_unresolved", "admission_stale",
    "admission_evidence_stale", "admission_excluded", "correspondence_unresolved",
    "correspondence_contradictory", "multiplicity_unresolved", "multiplicity_overlap",
    "multiplicity_total_conflict", "relocation_unresolved", "relocation_overlap",
    "combined_multiplicity_relocation_unsupported", "work_status_unresolved",
    "attribute_conflict", "schedule_attribute_conflict", "required_field_unresolved",
    "schedule_quantity_mismatch", "schedule_group_partial", "schedule_applicability_unresolved",
    "schedule_support_unresolved", "schedule_observed_unresolved", "schedule_contradictory_links",
    "scope_source_unavailable",
    "coverage_state_not_complete", "coverage_basis_stale", "coverage_requirements_unresolved",
    "coverage_source_uncovered",
})
# Codes that describe a resolved, non-contributing row; they never block finality.
RESOLVED_ISSUES = frozenset({"outside_selected_scope", "non_installed_depiction",
                             "excluded_depiction", "class_excluded", "admission_excluded"})

_PRECEDENCE = {"known": 3, "not_applicable": 2, "not_supplied": 1, "unknown": 0}


class AirDeviceCalculationError(ValueError):
    """Stable machine codes are the members of ERROR_CODES."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _reject(code, message):
    raise AirDeviceCalculationError(code, message)


# --------------------------------------------------------------------------
# Canonical fingerprint
# --------------------------------------------------------------------------

def _has_surrogates(value):
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _check_json(value, depth, label):
    if depth > MAX_DEPTH:
        _reject("limit_exceeded", "The " + label + " nests deeper than " + str(MAX_DEPTH) + " levels.")
    kind = type(value)
    if value is None or kind is bool:
        return
    if kind is int:
        if not INT64_MIN <= value <= INT64_MAX:
            _reject("limit_exceeded", "The " + label + " holds an integer outside the signed 64-bit range.")
        return
    if kind is float:
        if not math.isfinite(value):
            _reject("value_invalid", "The " + label + " holds a non-finite number.")
        return
    if kind is str:
        if len(value) > MAX_STRING_CHARS:
            _reject("limit_exceeded", "The " + label + " holds a string longer than "
                    + str(MAX_STRING_CHARS) + " characters.")
        if _has_surrogates(value):
            _reject("value_invalid", "The " + label + " holds an unpaired Unicode surrogate.")
        return
    if kind is dict:
        if len(value) > MAX_COLLECTION:
            _reject("limit_exceeded", "The " + label + " holds more than " + str(MAX_COLLECTION) + " keys.")
        for key in value:
            if type(key) is not str:
                _reject("structure_invalid", "The " + label + " uses a non-string key.")
            _check_json(key, depth, label + " key")
            _check_json(value[key], depth + 1, label + "." + key)
        return
    if kind is list:
        if len(value) > MAX_COLLECTION:
            _reject("limit_exceeded", "The " + label + " holds more than " + str(MAX_COLLECTION) + " items.")
        for index, item in enumerate(value):
            _check_json(item, depth + 1, label + "[" + str(index) + "]")
        return
    _reject("structure_invalid",
            "The " + label + " holds an unsupported type: " + kind.__name__ + ".")


def fingerprint(value):
    """Return the canonical SHA-256 of sorted-key, compact, finite UTF-8 JSON.

    Nothing is coerced. Objects that are not exactly dict, list, str, int, float,
    bool or None are refused rather than rendered through repr, so an accidental
    set, tuple, Decimal or Fraction cannot silently change a dependency hash.
    Arrays keep their given order; semantic ordering is the caller's decision.
    """
    _check_json(value, 1, "value")
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    raw = text.encode("utf-8")
    if len(raw) > MAX_PAYLOAD_BYTES:
        _reject("limit_exceeded", "The canonical payload exceeds " + str(MAX_PAYLOAD_BYTES) + " bytes.")
    return hashlib.sha256(raw).hexdigest()


_IMPLEMENTATION = None


def implementation_identity():
    """Fingerprint the exact kernel, attribute helper and rule loader bytes.

    Row dependency hashes include this identity so a changed calculation is
    never mistaken for an unchanged one. Reading these own code bytes is the
    only file access this module performs besides the rule loader's policy read.
    """
    global _IMPLEMENTATION
    for name, expected in _LOADED_DIGESTS.items():
        try:
            with (_HERE / name).open("rb") as stream:
                raw = stream.read(_MAX_MODULE_BYTES + 1)
        except OSError:
            _reject("structure_invalid", "The kernel implementation bytes are unreadable.")
        if len(raw) > _MAX_MODULE_BYTES:
            _reject("limit_exceeded", "The kernel implementation bytes exceed the bounded maximum.")
        if hashlib.sha256(raw).hexdigest() != expected:
            _reject("structure_invalid", "Reload after an air-device implementation update.")
    if _IMPLEMENTATION is None:
        _IMPLEMENTATION = fingerprint({"schema": "air-device-implementation-1", "files": _LOADED_DIGESTS})
    return _IMPLEMENTATION


# --------------------------------------------------------------------------
# Structural validation
# --------------------------------------------------------------------------

def _mapping(value, keys, label):
    if type(value) is not dict:
        _reject("structure_invalid", "The " + label + " must be exactly a dict.")
    if len(value) != len(keys) or set(value) != set(keys):
        _reject("structure_invalid",
                "The " + label + " requires exactly these keys: " + ", ".join(keys) + ".")
    return value


def _sequence(value, limit, label):
    if type(value) is not list:
        _reject("structure_invalid", "The " + label + " must be exactly a list.")
    if len(value) > limit:
        _reject("limit_exceeded", "The " + label + " holds more than " + str(limit) + " items.")
    return value


def _plain_text(value, limit, label):
    if type(value) is not str:
        _reject("value_invalid", "The " + label + " must be exactly a str.")
    if not 1 <= len(value) <= limit:
        _reject("value_invalid", "The " + label + " must hold 1 to " + str(limit) + " characters.")
    if any(ord(c) < 32 or 127 <= ord(c) < 160 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        _reject("value_invalid", "The " + label + " must not hold control characters or surrogates.")
    if not value.strip():
        _reject("value_invalid", "The " + label + " must not be whitespace only.")
    return value


def _identifier(value, label):
    if type(value) is not str:
        _reject("identifier_invalid", "The " + label + " must be exactly a str.")
    try:
        return _plain_text(value, MAX_ID_CHARS, label)
    except AirDeviceCalculationError as error:
        _reject("identifier_invalid", error.message)


def _sha256(value, label):
    if type(value) is not str or len(value) != 64 or value.strip("0123456789abcdef"):
        _reject("value_invalid", "The " + label + " must be 64 lowercase hexadecimal characters.")
    return value


def _integer(value, label, minimum, maximum):
    if type(value) is not int:  # bool is a subclass and is refused here
        _reject("value_invalid", "The " + label + " must be exactly an int.")
    if not minimum <= value <= maximum:
        _reject("value_invalid", "The " + label + " must be " + str(minimum) + " to " + str(maximum) + ".")
    return value


def _unique(values, label, code="reference_invalid"):
    if len(set(values)) != len(values):
        _reject(code, "The " + label + " repeats an entry.")
    return list(values)


def _enum(value, allowed, label):
    if type(value) is not str or value not in allowed:
        _reject("value_invalid", "The " + label + " must be one of: " + ", ".join(allowed) + ".")
    return value


def _coordinate(value, label):
    if type(value) is bool or type(value) not in (int, float):
        _reject("value_invalid", "The " + label + " must be a finite JSON number, not a bool.")
    number = float(value)
    if not math.isfinite(number):
        _reject("value_invalid", "The " + label + " must be finite.")
    if not 0.0 <= number <= 1.0:
        _reject("value_invalid", "The " + label + " must lie inside normalized [0, 1].")
    return number + 0.0  # a negative zero becomes the single canonical zero


def _bbox(value, label):
    if type(value) is not list or len(value) != 4:
        _reject("structure_invalid", "The " + label + " must be a list of exactly four numbers.")
    x0, y0, x1, y1 = [_coordinate(item, label + " coordinate") for item in value]
    if not x0 < x1 or not y0 < y1:
        _reject("value_invalid", "The " + label + " must enclose a strictly positive area as [x0,y0,x1,y1].")
    return [x0, y0, x1, y1]


def _source_tuple(value, label):
    _mapping(value, SOURCE_KEYS, label)
    return {"revision_id": _sha256(value["revision_id"], label + " revision_id"),
            "index": _integer(value["index"], label + " index", 0, MAX_SOURCE_INDEX),
            "sheet_id": _sha256(value["sheet_id"], label + " sheet_id"),
            "geometry_fingerprint": _sha256(value["geometry_fingerprint"], label + " geometry_fingerprint")}


def _refs(value, limit, label):
    _sequence(value, limit, label)
    return _unique(sorted(_identifier(item, label + " reference") for item in value), label)


def _deep_copy(value):
    if type(value) is dict:
        return {key: _deep_copy(item) for key, item in value.items()}
    if type(value) is list:
        return [_deep_copy(item) for item in value]
    return value


def _normalized_attributes(raw, evidence_ids, label):
    """Validate one attribute record through the accepted helper, then canonicalize refs."""
    try:
        validated = attributes.normalize_attributes(raw, list(evidence_ids))
    except attributes.AirDeviceAttributeError as error:
        _reject("attribute_invalid", "The " + label + " is invalid: " + error.message)
    return {field: {"state": validated[field]["state"],
                    "value": validated[field]["value"],
                    "evidence_ids": sorted(validated[field]["evidence_ids"])}
            for field in FIELDS}


def validate_observation(value):
    """Validate one observation proposal and return an independent canonical copy.

    Set-like lists (evidence and issue references) are sorted so that a reordered
    proposal keeps one identity. Bounding boxes become canonical floats. Nothing
    else is changed: source units, original text and issue text are preserved.
    """
    _mapping(value, OBSERVATION_KEYS, "observation")
    if value["schema"] != OBSERVATION_SCHEMA:
        _reject("value_invalid", "The observation schema must be " + OBSERVATION_SCHEMA + ".")
    identifier = _identifier(value["id"], "observation id")
    evidence_ids = _refs(value["evidence_ids"], MAX_LIST, "observation " + identifier + " evidence_ids")
    issues = _sequence(value["issues"], MAX_LIST, "observation " + identifier + " issues")
    issues = _unique(sorted(_plain_text(item, MAX_ISSUE_CHARS, "observation issue") for item in issues),
                     "observation " + identifier + " issues")
    return {"schema": OBSERVATION_SCHEMA,
            "id": identifier,
            "source": _source_tuple(value["source"], "observation " + identifier + " source"),
            "bbox": _bbox(value["bbox"], "observation " + identifier + " bbox"),
            "depiction": _enum(value["depiction"], DEPICTIONS, "observation " + identifier + " depiction"),
            "attributes": _normalized_attributes(value["attributes"], evidence_ids,
                                                 "observation " + identifier + " attributes"),
            "evidence_ids": evidence_ids,
            "issues": issues}


def _member_bindings(value, label, minimum):
    _sequence(value, MAX_MEMBERS, label)
    if len(value) < minimum:
        _reject("structure_invalid", "The " + label + " requires at least " + str(minimum) + " members.")
    bindings = []
    for item in value:
        _mapping(item, BINDING_KEYS, label + " member")
        bindings.append({"observation_id": _identifier(item["observation_id"], label + " member id"),
                         "observation_sha256": _sha256(item["observation_sha256"],
                                                       label + " member observation_sha256")})
    _unique([binding["observation_id"] for binding in bindings], label + " member identifiers")
    return sorted(bindings, key=lambda binding: binding["observation_id"])


# --------------------------------------------------------------------------
# Request model
# --------------------------------------------------------------------------

class _Model(object):
    """Validated canonical request plus the keyed indexes the kernel needs."""

    def __init__(self):
        self.binding = None
        self.scope = None
        self.sources = {}
        self.evidence = {}
        self.observations = {}
        self.observation_sha = {}
        self.decisions = {}
        self.correspondences = {}
        self.multiplicities = {}
        self.relocations = {}
        self.schedules = {}
        self.coverage = None
        self.evidence_current = {}
        self.evidence_source = {}
        self.observation_source = {}
        self.canonical_attributes = {}
        self.current_attributes = {}

    def source_role(self, key):
        context = self.sources.get(key)
        return context["role"] if context else None


def _validate_scope(value, source_keys):
    _mapping(value, SCOPE_KEYS, "scope")
    identifier = _identifier(value["id"], "scope id")
    version = _integer(value["version"], "scope version", 1, MAX_QUANTITY)
    selected = _sequence(value["source_keys"], MAX_LIST, "scope source_keys")
    if not selected:
        _reject("structure_invalid", "The scope requires at least one selected source key.")
    selected = _unique(sorted(_sha256(item, "scope source key") for item in selected), "scope source_keys")
    group_by = _sequence(value["group_by"], len(FIELDS), "scope group_by")
    if not group_by:
        _reject("structure_invalid", "The scope requires at least one group_by field.")
    for field in group_by:
        _enum(field, FIELDS, "scope group_by field")
    _unique(group_by, "scope group_by")
    required = _sequence(value["required_fields"], len(FIELDS), "scope required_fields")
    for field in required:
        if field not in group_by:
            _reject("reference_invalid", "The scope required field " + str(field)
                    + " is not part of group_by.")
    required = _unique(sorted(required), "scope required_fields")
    statuses = _sequence(value["work_statuses"], len(attributes.WORK_STATUSES), "scope work_statuses")
    if not statuses:
        _reject("structure_invalid", "The scope requires at least one requested work status.")
    for status in statuses:
        _enum(status, attributes.WORK_STATUSES, "scope work status")
    statuses = _unique(sorted(statuses), "scope work_statuses")
    for key in selected:
        role = source_keys.get(key)
        if role is not None and role not in ELIGIBLE_ROLES:
            _reject("value_invalid", "A selected present source context must hold one of these roles: "
                    + ", ".join(ELIGIBLE_ROLES) + ".")
    return {"id": identifier, "version": version, "source_keys": selected, "group_by": list(group_by),
            "required_fields": required, "work_statuses": statuses}


def _validate_sources(value):
    records = {}
    for item in _sequence(value, MAX_LIST, "sources"):
        _mapping(item, CONTEXT_KEYS, "source context")
        source = _source_tuple(item["source"], "source context source")
        identity = fingerprint(source)
        if identity in records:
            _reject("identifier_invalid", "Two source contexts share one source identity.")
        records[identity] = {"source": source,
                             "artifact_sha256": _sha256(item["artifact_sha256"],
                                                        "source artifact_sha256"),
                             "role": _enum(item["role"], SOURCE_ROLES, "source role")}
    return records


def _validate_evidence(value):
    records = {}
    for item in _sequence(value, MAX_LIST, "evidence"):
        _mapping(item, EVIDENCE_KEYS, "evidence record")
        identifier = _identifier(item["id"], "evidence id")
        if identifier in records:
            _reject("identifier_invalid", "Two evidence records share the identifier " + identifier + ".")
        kind = _enum(item["kind"], EVIDENCE_KINDS, "evidence " + identifier + " kind")
        text = item["text"]
        if kind == "graphic":
            if text is not None:
                _reject("value_invalid", "Graphic evidence " + identifier + " must carry a null text.")
        else:
            text = _plain_text(text, MAX_TEXT_CHARS, "evidence " + identifier + " text")
        records[identifier] = {"id": identifier,
                               "source": _source_tuple(item["source"], "evidence " + identifier + " source"),
                               "bbox": _bbox(item["bbox"], "evidence " + identifier + " bbox"),
                               "kind": kind,
                               "text": text,
                               "artifact_sha256": _sha256(item["artifact_sha256"],
                                                          "evidence " + identifier + " artifact_sha256")}
    return records


def _validate_decisions(value, observations):
    records = {}
    for item in _sequence(value, MAX_LIST, "decisions"):
        _mapping(item, DECISION_KEYS, "decision record")
        observation_id = _identifier(item["observation_id"], "decision observation_id")
        if observation_id not in observations:
            _reject("reference_invalid", "The decision names an unknown observation: " + observation_id + ".")
        if observation_id in records:
            _reject("identifier_invalid",
                    "At most one current decision may pin the observation " + observation_id + ".")
        records[observation_id] = {
            "observation_id": observation_id,
            "observation_sha256": _sha256(item["observation_sha256"], "decision observation_sha256"),
            "action": _enum(item["action"], ADMISSION_ACTIONS, "decision action"),
            "reason": _plain_text(item["reason"], MAX_REASON_CHARS, "decision reason"),
            "evidence_ids": _refs(item["evidence_ids"], MAX_LIST, "decision evidence_ids")}
    return records


def _validate_correspondences(value):
    records = {}
    for item in _sequence(value, MAX_LIST, "correspondences"):
        _mapping(item, CORRESPONDENCE_KEYS, "correspondence record")
        identifier = _identifier(item["id"], "correspondence id")
        if identifier in records:
            _reject("identifier_invalid", "Two correspondences share the identifier " + identifier + ".")
        evidence_ids = _refs(item["evidence_ids"], MAX_MEMBERS, "correspondence evidence_ids")
        if not evidence_ids:
            _reject("structure_invalid", "The correspondence " + identifier + " requires evidence.")
        records[identifier] = {
            "id": identifier,
            "members": _member_bindings(item["members"], "correspondence " + identifier, 2),
            "state": _enum(item["state"], CORRESPONDENCE_STATES, "correspondence state"),
            "evidence_ids": evidence_ids}
    return records


def _validate_multiplicities(value):
    records = {}
    for item in _sequence(value, MAX_LIST, "multiplicities"):
        _mapping(item, MULTIPLICITY_KEYS, "multiplicity record")
        identifier = _identifier(item["id"], "multiplicity id")
        if identifier in records:
            _reject("identifier_invalid", "Two multiplicities share the identifier " + identifier + ".")
        members = _member_bindings(item["members"], "multiplicity " + identifier, 1)
        representative = _identifier(item["representative"], "multiplicity representative")
        if representative not in [member["observation_id"] for member in members]:
            _reject("reference_invalid",
                    "The multiplicity representative must itself be a bound member.")
        evidence_ids = _refs(item["evidence_ids"], MAX_MEMBERS, "multiplicity evidence_ids")
        if not evidence_ids:
            _reject("structure_invalid", "The multiplicity " + identifier + " requires evidence.")
        records[identifier] = {
            "id": identifier,
            "members": members,
            "representative": representative,
            "each": _integer(item["each"], "multiplicity each", 1, MAX_QUANTITY),
            "scope_text": _plain_text(item["scope_text"], MAX_SCOPE_TEXT_CHARS, "multiplicity scope_text"),
            "evidence_ids": evidence_ids}
    return records


def _validate_relocations(value, observations):
    records, operation_ids = {}, set()
    for item in _sequence(value, MAX_LIST, "relocations"):
        _mapping(item, RELOCATION_KEYS, "relocation record")
        identifier = _identifier(item["id"], "relocation id")
        if identifier in records:
            _reject("identifier_invalid", "Two relocations share the identifier " + identifier + ".")
        members = _member_bindings(item["members"], "relocation " + identifier, 2)
        member_ids = set(member["observation_id"] for member in members)
        removed = _refs(item["remove_members"], MAX_MEMBERS, "relocation remove_members")
        reinstalled = _refs(item["reinstall_members"], MAX_MEMBERS, "relocation reinstall_members")
        if not removed or not reinstalled:
            _reject("structure_invalid",
                    "The relocation " + identifier + " requires nonempty removal and reinstallation sides.")
        if set(removed) & set(reinstalled):
            _reject("structure_invalid", "The relocation operation sides must be disjoint.")
        if set(removed) | set(reinstalled) != member_ids:
            _reject("structure_invalid",
                    "The relocation operation sides must exactly cover the bound members.")
        remove_operation = _identifier(item["remove_operation_id"], "relocation remove_operation_id")
        reinstall_operation = _identifier(item["reinstall_operation_id"], "relocation reinstall_operation_id")
        if remove_operation == reinstall_operation:
            _reject("identifier_invalid", "Each relocation operation requires its own identifier.")
        for operation in (remove_operation, reinstall_operation):
            if operation in operation_ids:
                _reject("identifier_invalid", "Operation identifiers must be globally unique.")
            operation_ids.add(operation)
        if item["reused"] is not True:
            _reject("value_invalid", "A verified relocation record requires reused=true.")
        evidence_ids = _refs(item["evidence_ids"], MAX_MEMBERS, "relocation evidence_ids")
        if not evidence_ids:
            _reject("structure_invalid", "The relocation " + identifier + " requires evidence.")
        old = set(_location(observations, member) for member in removed)
        new = set(_location(observations, member) for member in reinstalled)
        if old & new:
            _reject("value_invalid",
                    "The relocation old and new locations must be distinct source and bbox identities.")
        records[identifier] = {"id": identifier, "members": members, "remove_members": removed,
                               "reinstall_members": reinstalled,
                               "remove_operation_id": remove_operation,
                               "reinstall_operation_id": reinstall_operation,
                               "reused": True, "evidence_ids": evidence_ids}
    return records


def _location(observations, observation_id):
    observation = observations.get(observation_id)
    if observation is None:
        _reject("reference_invalid", "A relationship names an unknown observation: " + observation_id + ".")
    return (fingerprint(observation["source"]), tuple(observation["bbox"]))


def _validate_schedules(value):
    records = {}
    for item in _sequence(value, MAX_LIST, "schedules"):
        _mapping(item, SCHEDULE_KEYS, "schedule record")
        identifier = _identifier(item["id"], "schedule id")
        if identifier in records:
            _reject("identifier_invalid", "Two schedules share the identifier " + identifier + ".")
        evidence_ids = _refs(item["evidence_ids"], MAX_MEMBERS, "schedule evidence_ids")
        if not evidence_ids:
            _reject("structure_invalid", "The schedule " + identifier + " requires evidence.")
        declared = item["declared_each"]
        if declared is not None:
            declared = _integer(declared, "schedule declared_each", 0, MAX_QUANTITY)
        partial = item["attributes"]
        if type(partial) is not dict:
            _reject("structure_invalid", "The schedule attributes must be exactly a dict.")
        for field in partial:
            _enum(field, FIELDS, "schedule attribute field")
        if not partial and declared is None:
            _reject("structure_invalid",
                    "The schedule " + identifier + " requires at least one attribute or a declared quantity.")
        # Reuse the accepted helper contract by completing the partial mapping.
        complete = {field: {"state": "unknown", "value": None, "evidence_ids": []} for field in FIELDS}
        for field in partial:
            entry = partial[field]
            if type(entry) is not dict:
                _reject("structure_invalid", "The schedule attribute " + field + " must be exactly a dict.")
            complete[field] = _deep_copy(entry)
        normalized = _normalized_attributes(complete, evidence_ids,
                                            "schedule " + identifier + " attributes")
        records[identifier] = {"id": identifier,
                               "members": _member_bindings(item["members"], "schedule " + identifier, 1),
                               "declared_each": declared,
                               "attributes": {field: normalized[field] for field in sorted(partial)},
                               "evidence_ids": evidence_ids}
    return records


def _validate_coverage(value):
    _mapping(value, COVERAGE_KEYS, "coverage")
    basis = value["basis_sha256"]
    if basis is not None:
        basis = _sha256(basis, "coverage basis_sha256")
    requirements = _sequence(value["unresolved_requirements"], MAX_LIST, "coverage unresolved_requirements")
    requirements = _unique(sorted(_plain_text(item, MAX_REQUIREMENT_CHARS, "coverage requirement")
                                  for item in requirements), "coverage unresolved_requirements")
    return {"state": _enum(value["state"], COVERAGE_STATES, "coverage state"),
            "basis_sha256": basis,
            "evidence_ids": _refs(value["evidence_ids"], MAX_LIST, "coverage evidence_ids"),
            "unresolved_requirements": requirements}


def _require_evidence(model, references, label):
    for reference in references:
        if reference not in model.evidence:
            _reject("reference_invalid", "The " + label + " names unknown evidence: " + reference + ".")


def _require_members(model, members, label):
    for member in members:
        if member["observation_id"] not in model.observations:
            _reject("reference_invalid",
                    "The " + label + " names an unknown observation: " + member["observation_id"] + ".")


def _prepare(request, require_coverage):
    """Validate the whole request and return the canonical keyed model."""
    implementation_identity()
    if type(request) is not dict:
        _reject("structure_invalid", "The request must be exactly a dict.")
    has_coverage = "coverage" in request
    if require_coverage and not has_coverage:
        _reject("structure_invalid", "The request requires an explicit coverage decision.")
    expected = REQUEST_KEYS if has_coverage else tuple(key for key in REQUEST_KEYS if key != "coverage")
    _mapping(request, expected, "request")
    if request["schema"] != REQUEST_SCHEMA:
        _reject("value_invalid", "The request schema must be " + REQUEST_SCHEMA + ".")

    model = _Model()
    try:
        approved = rules.load_binding()
    except rules.AirDeviceRuleError as error:
        _reject("binding_invalid", "The approved air-device policy is unavailable: " + str(error))
    if request["binding"] != approved:
        _reject("binding_invalid", "The request binding does not equal the verified approved policy.")
    model.binding = _deep_copy(approved)

    model.sources = _validate_sources(request["sources"])
    model.scope = _validate_scope(request["scope"],
                                  {key: model.sources[key]["role"] for key in model.sources})
    model.evidence = _validate_evidence(request["evidence"])

    observations = {}
    for item in _sequence(request["observations"], MAX_LIST, "observations"):
        observation = validate_observation(item)
        if observation["id"] in observations:
            _reject("identifier_invalid",
                    "Two observations share the identifier " + observation["id"] + ".")
        observations[observation["id"]] = observation
    model.observations = observations

    model.decisions = _validate_decisions(request["decisions"], observations)
    model.correspondences = _validate_correspondences(request["correspondences"])
    model.multiplicities = _validate_multiplicities(request["multiplicities"])
    model.relocations = _validate_relocations(request["relocations"], observations)
    model.schedules = _validate_schedules(request["schedules"])
    model.coverage = _validate_coverage(request["coverage"]) if has_coverage else None

    for identifier, observation in observations.items():
        _require_evidence(model, observation["evidence_ids"], "observation " + identifier)
        model.observation_sha[identifier] = fingerprint(observation)
        model.observation_source[identifier] = fingerprint(observation["source"])
    for identifier, decision in model.decisions.items():
        _require_evidence(model, decision["evidence_ids"], "decision for " + identifier)
    for group in (model.correspondences, model.multiplicities, model.relocations, model.schedules):
        for identifier, record in group.items():
            _require_members(model, record["members"], "relationship " + identifier)
            _require_evidence(model, record["evidence_ids"], "relationship " + identifier)
    if model.coverage is not None:
        _require_evidence(model, model.coverage["evidence_ids"], "coverage")

    for identifier, evidence in model.evidence.items():
        key = fingerprint(evidence["source"])
        model.evidence_source[identifier] = key
        context = model.sources.get(key)
        model.evidence_current[identifier] = bool(
            context is not None and context["artifact_sha256"] == evidence["artifact_sha256"])
    # Keep original source proposals untouched for hashes and review. Only the
    # current, fully supported interpretation may supply eligibility/grouping.
    for identifier, observation in observations.items():
        current = _deep_copy(observation["attributes"])
        for entry in current.values():
            if entry["state"] in ("known", "not_applicable") and not all(
                    model.evidence_current[reference] for reference in entry["evidence_ids"]):
                entry.update(state="unknown", value=None)
        model.current_attributes[identifier] = current
        model.canonical_attributes[identifier] = attributes.canonical_attributes(current)
    return model


def _canonical_request(model, include_coverage):
    canonical = {
        "schema": REQUEST_SCHEMA,
        "binding": model.binding,
        "scope": model.scope,
        "sources": [dict(model.sources[key], source_key=key) for key in sorted(model.sources)],
        "evidence": [model.evidence[key] for key in sorted(model.evidence)],
        "observations": [model.observations[key] for key in sorted(model.observations)],
        "decisions": [model.decisions[key] for key in sorted(model.decisions)],
        "correspondences": [model.correspondences[key] for key in sorted(model.correspondences)],
        "multiplicities": [model.multiplicities[key] for key in sorted(model.multiplicities)],
        "relocations": [model.relocations[key] for key in sorted(model.relocations)],
        "schedules": [model.schedules[key] for key in sorted(model.schedules)],
    }
    if include_coverage:
        canonical["coverage"] = model.coverage
    return canonical


def coverage_basis(request):
    """Return the fingerprint of the semantically ordered request without coverage.

    This is the exact pin a recorded scope-completeness review must carry. It
    certifies nothing by itself: it only says which request bytes the reviewer
    saw. No scale value participates, because none exists in this contract.
    """
    model = _prepare(request, require_coverage=False)
    return fingerprint(_canonical_request(model, include_coverage=False))


# --------------------------------------------------------------------------
# Observation state
# --------------------------------------------------------------------------

class _State(object):
    """One observation's resolved contribution before relationships are applied."""

    def __init__(self):
        self.issues = set()
        self.physical_each = None
        self.supported = False
        self.in_scope = False

    @property
    def blocking(self):
        return any(issue.split(":")[0] not in RESOLVED_ISSUES for issue in self.issues)


def _own_graphic_evidence(model, observation):
    """A supported physical depiction cites its own current graphic at its own box."""
    key = model.observation_source[observation["id"]]
    for reference in observation["evidence_ids"]:
        evidence = model.evidence[reference]
        if (evidence["kind"] == "graphic" and model.evidence_current[reference]
                and model.evidence_source[reference] == key
                and evidence["bbox"] == observation["bbox"]):
            return True
    return False


def _observation_state(model, observation):
    identifier = observation["id"]
    key = model.observation_source[identifier]
    state = _State()
    selected = key in model.scope["source_keys"]
    state.in_scope = selected

    if observation["depiction"] in NON_INSTALLED_DEPICTIONS:
        # A legend, schedule view, generic detail or confirmed excluded item is a
        # resolved zero contribution, not an unknown one (rules A01 and A04).
        state.physical_each = 0
        state.issues.add("excluded_depiction" if observation["depiction"] == "excluded"
                         else "non_installed_depiction")
        return state
    if not selected:
        state.issues.add("outside_selected_scope")
        return state
    if key not in model.sources:
        state.issues.add("source_stale")
        return state
    decision = model.decisions.get(identifier)
    # A reviewed false positive need not first become a classifiable physical
    # device. Exclusion still binds the exact observation and current evidence.
    if decision is not None and decision["action"] == "exclude":
        if decision["observation_sha256"] != model.observation_sha[identifier]:
            state.issues.add("admission_stale")
        elif not decision["evidence_ids"] or not all(model.evidence_current[reference]
                                                      for reference in decision["evidence_ids"]):
            state.issues.add("admission_evidence_stale")
        else:
            state.physical_each = 0
            state.issues.add("admission_excluded")
        return state
    if observation["depiction"] == "unknown":
        state.issues.add("depiction_unresolved")
        return state
    if observation["depiction"] == "tag_only":
        state.issues.add("physical_instance_unresolved")
        return state

    family = model.current_attributes[identifier]["family"]
    if family["state"] == "known" and family["value"] in ROUTED_FAMILIES:
        state.physical_each = 0
        state.issues.add("class_excluded")
        return state
    if family["state"] != "known" or family["value"] not in ELIGIBLE_FAMILIES:
        state.issues.add("family_unresolved")
        if not _own_graphic_evidence(model, observation):
            state.issues.add("graphic_evidence_missing")
        return state
    if not _own_graphic_evidence(model, observation):
        state.issues.add("graphic_evidence_missing")
        return state

    if decision is None:
        state.issues.add("admission_missing")
        return state
    if decision["observation_sha256"] != model.observation_sha[identifier]:
        state.issues.add("admission_stale")
        return state
    if decision["action"] == "unresolved":
        state.issues.add("admission_unresolved")
        return state
    state.physical_each = 1
    state.supported = True
    return state


# --------------------------------------------------------------------------
# Explicit relationships
# --------------------------------------------------------------------------

class _Union(object):
    """Keyed connected components; no pairwise scan of the observation set."""

    def __init__(self, items):
        self.parent = dict((item, item) for item in items)

    def find(self, item):
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[max(left, right)] = min(left, right)


def _supported_evidence(model, references, kind=None, role=None):
    """True when at least one cited reference is current and of the wanted shape."""
    for reference in references:
        if not model.evidence_current[reference]:
            continue
        evidence = model.evidence[reference]
        if kind is not None and evidence["kind"] != kind:
            continue
        if role is not None and model.source_role(model.evidence_source[reference]) != role:
            continue
        return True
    return False


def _all_evidence_current(model, references):
    return bool(references) and all(model.evidence_current[item] for item in references)


def _members_bound(model, record):
    return all(member["observation_sha256"] == model.observation_sha[member["observation_id"]]
               for member in record["members"])


def _mark(states, member_ids, issue):
    for identifier in member_ids:
        states[identifier].issues.add(issue)
        states[identifier].physical_each = None
        states[identifier].supported = False


def _relationships(model, states):
    """Classify every explicit relationship and union the accounting components."""
    union = _Union(model.observations)
    valid = {"correspondence": set(), "multiplicity": set(), "relocation": set()}

    for identifier in sorted(model.correspondences):
        record = model.correspondences[identifier]
        members = [member["observation_id"] for member in record["members"]]
        supported = (record["state"] != "unresolved" and _members_bound(model, record)
                     and _all_evidence_current(model, record["evidence_ids"]))
        if not supported:
            _mark(states, members, "correspondence_unresolved")
            continue
        valid["correspondence"].add(identifier)
        if record["state"] == "same":
            for member in members[1:]:
                union.union(members[0], member)

    same_root = dict((identifier, union.find(identifier)) for identifier in model.observations)
    multiplicity_roots = {}
    for identifier in sorted(model.multiplicities):
        record = model.multiplicities[identifier]
        members = [member["observation_id"] for member in record["members"]]
        supported = (_members_bound(model, record)
                     and _all_evidence_current(model, record["evidence_ids"])
                     and _supported_evidence(model, record["evidence_ids"], kind="text"))
        if not supported:
            _mark(states, members, "multiplicity_unresolved")
            continue
        roots = set(same_root[member] for member in members)
        if any(root in multiplicity_roots for root in roots):
            overlapping = set(members)
            for root in roots:
                overlapping |= set(multiplicity_roots.get(root, ()))
            _mark(states, sorted(overlapping), "multiplicity_overlap")
            for root in roots:
                multiplicity_roots.setdefault(root, set()).update(members)
            continue
        for root in roots:
            multiplicity_roots[root] = set(members)
        valid["multiplicity"].add(identifier)
        for member in members[1:]:
            union.union(members[0], member)

    relocation_roots = {}
    for identifier in sorted(model.relocations):
        record = model.relocations[identifier]
        members = [member["observation_id"] for member in record["members"]]
        supported = (_members_bound(model, record)
                     and _all_evidence_current(model, record["evidence_ids"]))
        if not supported:
            _mark(states, members, "relocation_unresolved")
            continue
        roots = set(same_root[member] for member in members)
        if any(root in relocation_roots for root in roots):
            # Two independent records claim the same assembly's operations (A10).
            overlapping = set(members)
            for root in roots:
                overlapping |= set(relocation_roots.get(root, ()))
            _mark(states, sorted(overlapping), "relocation_overlap")
            for root in roots:
                relocation_roots.setdefault(root, set()).update(members)
            continue
        for root in roots:
            relocation_roots[root] = set(members)
        valid["relocation"].add(identifier)
        for member in members[1:]:
            union.union(members[0], member)

    # Same depictions and relocation assert physical equivalence. Multiplicity
    # combines accounting coverage only; distinct covered devices are valid.
    physical = _Union(model.observations)
    for member, root in same_root.items():
        physical.union(member, root)
    for identifier in valid["relocation"]:
        members = [member["observation_id"] for member in model.relocations[identifier]["members"]]
        for member in members[1:]:
            physical.union(members[0], member)
    for identifier in sorted(valid["correspondence"]):
        record = model.correspondences[identifier]
        if record["state"] != "distinct":
            continue
        members = [member["observation_id"] for member in record["members"]]
        roots = [physical.find(member) for member in members]
        if len(set(roots)) != len(roots):
            affected = [member for member in model.observations if physical.find(member) in set(roots)]
            _mark(states, affected, "correspondence_contradictory")

    # An explicit multiplicity of relocation operations is not established work.
    for identifier in sorted(valid["relocation"]):
        members = [member["observation_id"] for member in model.relocations[identifier]["members"]]
        touched = set()
        for other in sorted(valid["multiplicity"]):
            other_members = [member["observation_id"]
                             for member in model.multiplicities[other]["members"]]
            if set(union.find(item) for item in members) & set(union.find(item) for item in other_members):
                touched |= set(other_members)
        if touched:
            valid["relocation"].discard(identifier)
            _mark(states, sorted(set(members) | touched),
                  "combined_multiplicity_relocation_unsupported")
    for identifier in sorted(valid["multiplicity"]):
        members = [member["observation_id"] for member in model.multiplicities[identifier]["members"]]
        if any("combined_multiplicity_relocation_unsupported" in states[member].issues
               for member in members):
            valid["multiplicity"].discard(identifier)
    return union, valid, same_root


# --------------------------------------------------------------------------
# Attribute merge
# --------------------------------------------------------------------------

def _merge_field(model, field, member_ids):
    """Merge one field across the depictions of a single physical assembly."""
    entries = [(identifier, model.current_attributes[identifier][field],
                model.canonical_attributes[identifier][field]) for identifier in member_ids]
    known = [item for item in entries if item[1]["state"] == "known"]
    if known:
        values = set(fingerprint(item[2]["value"]) for item in known)
        if len(values) > 1:
            references = sorted(set(reference for item in known
                                    for reference in item[1]["evidence_ids"]))
            return ({"state": "unknown", "value": None, "evidence_ids": references[:MAX_MEMBERS]},
                    sorted(item[0] for item in known))
        chosen = min(known, key=lambda item: item[0])
        return ({"state": "known", "value": _deep_copy(chosen[1]["value"]),
                 "evidence_ids": list(chosen[1]["evidence_ids"])}, None)
    ranked = sorted(entries, key=lambda item: (-_PRECEDENCE[item[1]["state"]], item[0]))
    chosen = ranked[0][1]
    return ({"state": chosen["state"], "value": None,
             "evidence_ids": list(chosen["evidence_ids"])}, None)


def _merged_attributes(model, member_ids, relocation):
    record, conflicts = {}, []
    for field in FIELDS:
        if relocation is not None and field == "work_status":
            # The removal and reinstallation depictions legitimately differ; the
            # verified relocation itself carries the status (rules A09 and A10).
            record[field] = {"state": "known", "value": "relocated",
                             "evidence_ids": list(relocation["evidence_ids"])}
            continue
        merged, conflicted = _merge_field(model, field, member_ids)
        record[field] = merged
        if conflicted:
            conflicts.append({"field": field, "observation_ids": conflicted, "schedule_ids": []})
    return record, conflicts


def _rendered(record, label):
    try:
        return attributes.imperial_attributes(record), attributes.canonical_attributes(record)
    except attributes.AirDeviceAttributeError as error:
        _reject("attribute_invalid", "The merged " + label + " attributes are invalid: " + error.message)


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

def _row_dependency(model, member_ids, relationship_ids, evidence_ids):
    # A relation may leave its members in separate accounting rows (distinct
    # correspondence and schedule groups). Its live peer pins still determine
    # this row's result, so retain those consumed inputs without pulling in
    # unrelated rows or same-spelled IDs in another relationship namespace.
    attached = []
    consumed_members = set(member_ids)
    for kind, group in (("correspondence", model.correspondences), ("multiplicity", model.multiplicities),
                        ("relocation", model.relocations), ("schedule", model.schedules)):
        for identifier in sorted(relationship_ids):
            if identifier not in group:
                continue
            record = group[identifier]
            peers = {member["observation_id"] for member in record["members"]}
            if not peers.intersection(member_ids):
                continue
            attached.append({"kind": kind, "record": record})
            consumed_members.update(peers)
    consumed_evidence = set(evidence_ids)
    for identifier in consumed_members:
        consumed_evidence.update(model.observations[identifier]["evidence_ids"])
        if identifier in model.decisions:
            consumed_evidence.update(model.decisions[identifier]["evidence_ids"])
    consumed = {
        "schema": "air-device-row-dependency-1",
        "implementation_sha256": implementation_identity(),
        "binding": model.binding,
        "scope": model.scope,
        "member_ids": sorted(member_ids),
        "observations": [model.observations[identifier] for identifier in sorted(consumed_members)],
        "decisions": [model.decisions[identifier] for identifier in sorted(consumed_members)
                      if identifier in model.decisions],
        "relationships": attached,
        "evidence": [model.evidence[identifier] for identifier in sorted(consumed_evidence)],
        "sources": [],
    }
    keys = set(model.observation_source[identifier] for identifier in consumed_members)
    keys |= set(model.evidence_source[identifier] for identifier in consumed_evidence)
    for key in sorted(keys):
        if key in model.sources:
            consumed["sources"].append(dict(model.sources[key], source_key=key))
    return fingerprint(consumed)


def _component_rows(model, states, union, valid, same_root):
    components = {}
    for identifier in sorted(model.observations):
        components.setdefault(union.find(identifier), []).append(identifier)

    groups = {"correspondence": model.correspondences, "multiplicity": model.multiplicities,
              "relocation": model.relocations, "schedule": model.schedules}
    relationship_of = {}
    for kind in sorted(groups):
        for identifier in groups[kind]:
            for member in groups[kind][identifier]["members"]:
                relationship_of.setdefault(member["observation_id"], []).append((kind, identifier))

    rows = []
    for root in sorted(components):
        member_ids = sorted(components[root])
        issues, relationship_ids = set(), set()
        multiplicity, relocation = None, None
        for identifier in member_ids:
            issues |= states[identifier].issues
            for kind, record_id in relationship_of.get(identifier, ()):
                relationship_ids.add(record_id)
                if kind == "multiplicity" and record_id in valid[kind]:
                    multiplicity = model.multiplicities[record_id]
                elif kind == "relocation" and record_id in valid[kind]:
                    relocation = model.relocations[record_id]

        blocking = any(issue.split(":")[0] not in RESOLVED_ISSUES for issue in issues)
        supported = [identifier for identifier in member_ids if states[identifier].supported]
        zeroed = all(states[identifier].physical_each == 0 for identifier in member_ids)
        physical_each = None
        if not blocking:
            if relocation is not None:
                if supported:
                    physical_each = 1
                else:
                    issues.add("relocation_unresolved")
                    blocking = True
            elif multiplicity is not None:
                distinct = len(set(same_root[identifier] for identifier in supported))
                if distinct > multiplicity["each"]:
                    # A scoped note cannot cover more devices than it names (A05).
                    issues.add("multiplicity_total_conflict")
                    blocking = True
                elif supported:
                    physical_each = multiplicity["each"]
                elif zeroed:
                    physical_each = 0
                else:
                    issues.add("multiplicity_unresolved")
                    blocking = True
            elif supported:
                physical_each = 1
            elif zeroed:
                physical_each = 0

        record, conflicts = _merged_attributes(model, member_ids, relocation)
        for conflict in conflicts:
            # A conflicting attribute is an exception for review; it never
            # withdraws the supported physical quantity (rule A03).
            issues.add("attribute_conflict:" + conflict["field"])
        evidence_ids = set()
        for identifier in member_ids:
            evidence_ids |= set(model.observations[identifier]["evidence_ids"])
            decision = model.decisions.get(identifier)
            if decision is not None:
                evidence_ids |= set(decision["evidence_ids"])
        for record_id in relationship_ids:
            for group in groups.values():
                if record_id in group:
                    evidence_ids |= set(group[record_id]["evidence_ids"])

        rows.append({
            "row_id": fingerprint({"schema": "air-device-row-1", "member_ids": member_ids}),
            "member_ids": member_ids,
            "relationship_ids": sorted(relationship_ids),
            "source_keys": sorted(set(model.observation_source[item] for item in member_ids)),
            "evidence_ids": sorted(evidence_ids),
            "raw_attributes": record,
            "conflicts": conflicts,
            "issues": issues,
            "physical_each": physical_each,
            "supported": bool(supported),
            "relocation": relocation,
            "in_scope": any(states[item].in_scope for item in member_ids),
        })
    return rows


def _apply_status(model, row):
    """Set the requested flag and the blocking state from the current attributes.

    An unestablished work status is never treated as new, existing or removal
    work: it keeps the requested total incomplete while the physical quantity
    stays visible (rules A09 and A11).
    """
    issues = set(row["issues"]) - {"work_status_unresolved"}
    requested = False
    if row["physical_each"] is not None and row["_supported"]:
        status = row["attributes"]["work_status"]
        if status["state"] != "known":
            issues.add("work_status_unresolved")
            requested = None
        else:
            requested = status["value"] in model.scope["work_statuses"]
    blocking = any(issue.split(":")[0] not in RESOLVED_ISSUES for issue in issues)
    if row["physical_each"] is None and blocking:
        requested = None
    row["issues"] = sorted(issues)
    row["requested"] = requested
    row["_blocking"] = blocking


def _finish_rows(model, rows):
    """Apply rendered attributes, operations, status and dependency hashes."""
    finished = []
    for row in rows:
        imperial, canonical = _rendered(row["raw_attributes"], "row")
        if row["physical_each"] is None:
            operations, new_purchase = {"remove": None, "reinstall": None}, None
        elif row["relocation"] is not None:
            # One reused assembly with two separate operations and no purchase (A10).
            operations, new_purchase = {"remove": 1, "reinstall": 1}, 0
        else:
            operations, new_purchase = {"remove": 0, "reinstall": 0}, None
        finished.append({
            "row_id": row["row_id"],
            "member_ids": row["member_ids"],
            "relationship_ids": row["relationship_ids"],
            "source_keys": row["source_keys"],
            "evidence_ids": row["evidence_ids"],
            "attributes": imperial,
            "canonical_attributes": canonical,
            "conflicts": row["conflicts"],
            "issues": sorted(row["issues"]),
            "physical_each": row["physical_each"],
            "requested": False,
            "operations": operations,
            "new_purchase_each": new_purchase,
            "dependency_sha256": _row_dependency(model, row["member_ids"], row["relationship_ids"],
                                                 row["evidence_ids"]),
            "_raw": row["raw_attributes"],
            "_blocking": False,
            "_in_scope": row["in_scope"],
            "_supported": row["supported"],
        })
        _apply_status(model, finished[-1])
    return sorted(finished, key=lambda row: row["row_id"])


# --------------------------------------------------------------------------
# Schedules
# --------------------------------------------------------------------------

def _apply_schedules(model, rows):
    """Bind declarations to rows: attributes may be supplied, instances never."""
    by_member = {}
    for row in rows:
        for identifier in row["member_ids"]:
            by_member[identifier] = row
    declarations, scope_issues = [], set()

    supply = {}
    for identifier in sorted(model.schedules):
        schedule = model.schedules[identifier]
        member_ids = [member["observation_id"] for member in schedule["members"]]
        issues = set()
        supported = (_members_bound(model, schedule)
                     and _all_evidence_current(model, schedule["evidence_ids"])
                     and _supported_evidence(model, schedule["evidence_ids"], kind="text", role="schedule"))
        touched, seen = [], set()
        for member in member_ids:
            row = by_member.get(member)
            if row is not None and row["row_id"] not in seen:
                seen.add(row["row_id"])
                touched.append(row)
        touched.sort(key=lambda row: row["row_id"])
        row_ids = [row["row_id"] for row in touched]

        if not supported:
            issues.add("schedule_support_unresolved")
            scope_issues.add("schedule_support_unresolved")
            declarations.append({"id": identifier, "declared_each": schedule["declared_each"],
                                 "observed_each": None, "state": "unresolved", "row_ids": row_ids,
                                 "member_ids": sorted(member_ids), "issues": sorted(issues)})
            continue

        for row in touched:
            supply.setdefault(row["row_id"], []).append(identifier)

        if not any(row["_supported"] for row in touched):
            issues.add("schedule_applicability_unresolved")
            scope_issues.add("schedule_applicability_unresolved")

        observed = 0
        complete_group = True
        for row in touched:
            # A partially named accounting group cannot support a complete
            # reconciliation, even when every named member is resolved (A06).
            if set(row["member_ids"]) - set(member_ids):
                complete_group = False
            if row["physical_each"] is None:
                observed = None
                break
            observed += row["physical_each"]

        state = "attributes_only"
        if schedule["declared_each"] is not None:
            if observed is None:
                state = "unresolved"
                issues.add("schedule_observed_unresolved")
            elif not complete_group:
                state = "unresolved"
                issues.add("schedule_group_partial")
            elif observed != schedule["declared_each"]:
                state = "conflict"
                issues.add("schedule_quantity_mismatch")
            else:
                state = "reconciled"
        scope_issues |= issues
        declarations.append({"id": identifier, "declared_each": schedule["declared_each"],
                             "observed_each": observed, "state": state, "row_ids": row_ids,
                             "member_ids": sorted(member_ids), "issues": sorted(issues)})

    for row in rows:
        linked = supply.get(row["row_id"], [])
        if linked and _supply_attributes(model, row, linked):
            row["attributes"], row["canonical_attributes"] = _rendered(row["_raw"], "row")
            _apply_status(model, row)
    return declarations, scope_issues


def _supply_attributes(model, row, linked):
    """Supply missing known attributes; a different known value stays a conflict."""
    raw = row["_raw"]
    changed = False
    issues = set(row["issues"])
    for field in FIELDS:
        offered = []
        for identifier in linked:
            entry = model.schedules[identifier]["attributes"].get(field)
            if entry is not None and entry["state"] == "known":
                offered.append((identifier, entry))
        if not offered:
            continue
        canonical_values = set()
        for identifier, entry in offered:
            complete = {name: {"state": "unknown", "value": None, "evidence_ids": []} for name in FIELDS}
            complete[field] = _deep_copy(entry)
            canonical_values.add(fingerprint(attributes.canonical_attributes(complete)[field]["value"]))
        current = raw[field]
        if len(canonical_values) > 1:
            issues.add("schedule_contradictory_links")
            issues.add("schedule_attribute_conflict:" + field)
            raw[field] = {"state": "unknown", "value": None,
                          "evidence_ids": sorted(set(reference for _, entry in offered
                                                     for reference in entry["evidence_ids"]))[:MAX_MEMBERS]}
            row["conflicts"].append({"field": field, "observation_ids": list(row["member_ids"]),
                                     "schedule_ids": sorted(identifier for identifier, _ in offered)})
            changed = True
            continue
        if current["state"] == "known":
            complete = {name: {"state": "unknown", "value": None, "evidence_ids": []} for name in FIELDS}
            complete[field] = _deep_copy(current)
            observed = fingerprint(attributes.canonical_attributes(complete)[field]["value"])
            if observed not in canonical_values:
                issues.add("schedule_attribute_conflict:" + field)
                raw[field] = {"state": "unknown", "value": None,
                              "evidence_ids": sorted(set(current["evidence_ids"])
                                                     | set(reference for _, entry in offered
                                                           for reference in entry["evidence_ids"]))[:MAX_MEMBERS]}
                row["conflicts"].append({"field": field, "observation_ids": list(row["member_ids"]),
                                         "schedule_ids": sorted(identifier for identifier, _ in offered)})
                changed = True
            continue
        identifier, entry = min(offered, key=lambda item: item[0])
        raw[field] = _deep_copy(entry)
        changed = True
    row["issues"] = sorted(issues)
    return changed


# --------------------------------------------------------------------------
# Coverage, groups and result
# --------------------------------------------------------------------------

def _coverage_state(model, request_basis):
    issues = set()
    coverage = model.coverage
    if coverage["state"] != "complete":
        issues.add("coverage_state_not_complete")
    if coverage["unresolved_requirements"]:
        issues.add("coverage_requirements_unresolved")
    if coverage["basis_sha256"] != request_basis:
        issues.add("coverage_basis_stale")
    for key in model.scope["source_keys"]:
        if key not in model.sources:
            issues.add("scope_source_unavailable")
            continue
        witnessed = any(model.evidence_current[reference] and model.evidence_source[reference] == key
                        for reference in coverage["evidence_ids"])
        if not witnessed:
            issues.add("coverage_source_uncovered")
    return not issues, issues


def _required_satisfied(model, row, field):
    entry = row["attributes"][field]
    if entry["state"] == "known":
        return True
    if entry["state"] != "not_applicable":
        return False
    return any(model.evidence_current.get(reference, False) for reference in entry["evidence_ids"])


def _declaration_blocks(declarations):
    """Split declaration problems into affected rows and scope-level requirements."""
    blocked, scope_blocking = {}, False
    for declaration in declarations:
        if not declaration["issues"]:
            continue
        for row_id in declaration["row_ids"]:
            blocked.setdefault(row_id, set()).update(declaration["issues"])
        if not declaration["row_ids"] or set(declaration["issues"]) & {
                "schedule_support_unresolved", "schedule_applicability_unresolved"}:
            # An unlinked or unsupported declaration is an open scope requirement.
            scope_blocking = True
    return blocked, scope_blocking


def _groups(model, rows, coverage_ok, blocked_rows, scope_blocking):
    grouped = {}
    for row in rows:
        key = [{"field": field,
                "state": row["canonical_attributes"][field]["state"],
                "value": row["canonical_attributes"][field]["value"]}
               for field in model.scope["group_by"]]
        group_id = fingerprint({"schema": "air-device-group-1", "key": key})
        entry = grouped.setdefault(group_id, {"group_id": group_id, "key": key, "rows": []})
        entry["rows"].append(row)

    groups = []
    for group_id in sorted(grouped):
        entry = grouped[group_id]
        issues = set()
        known_subtotal, physical_known = 0, 0
        complete = coverage_ok and not scope_blocking
        for row in entry["rows"]:
            if row["row_id"] in blocked_rows:
                complete = False
                issues |= blocked_rows[row["row_id"]]
            if row["_blocking"]:
                complete = False
                issues |= set(issue for issue in row["issues"]
                              if issue.split(":")[0] not in RESOLVED_ISSUES)
            if row["physical_each"] is None:
                continue
            if row["requested"] is True:
                known_subtotal += row["physical_each"]
            if row["_in_scope"] and row["_supported"]:
                physical_known += row["physical_each"]
            if row["_supported"]:
                for field in model.scope["required_fields"]:
                    if not _required_satisfied(model, row, field):
                        issues.add("required_field_unresolved:" + field)
                        complete = False
        groups.append({"group_id": group_id,
                       "key": entry["key"],
                       "row_ids": sorted(row["row_id"] for row in entry["rows"]),
                       "known_subtotal_each": known_subtotal,
                       "physical_known_each": physical_known,
                       "total_each": known_subtotal if complete else None,
                       "complete": complete,
                       "issues": sorted(issues)})
    return groups


def calculate(request):
    """Return the deterministic air-device result for one fully explicit request.

    The request is never mutated and never persisted here. Unknown states stay
    unknown: a null total says the approved rules do not yet support a final
    quantity, and it is never a zero-device claim (rules A11 and A12).
    """
    model = _prepare(request, require_coverage=True)
    basis = fingerprint(_canonical_request(model, include_coverage=False))
    request_sha256 = fingerprint(_canonical_request(model, include_coverage=True))

    states = dict((identifier, _observation_state(model, model.observations[identifier]))
                  for identifier in model.observations)
    union, valid, same_root = _relationships(model, states)
    for identifier in model.observations:
        if states[identifier].blocking:
            states[identifier].physical_each = None
            states[identifier].supported = False

    rows = _finish_rows(model, _component_rows(model, states, union, valid, same_root))
    declarations, declaration_issues = _apply_schedules(model, rows)
    declaration_blocking = any(declaration["issues"] for declaration in declarations)

    blocked_rows, scope_blocking = _declaration_blocks(declarations)
    coverage_ok, coverage_issues = _coverage_state(model, basis)
    groups = _groups(model, rows, coverage_ok, blocked_rows, scope_blocking)

    known_subtotal = sum(row["physical_each"] for row in rows
                         if row["requested"] is True and row["physical_each"] is not None)
    physical_known = sum(row["physical_each"] for row in rows
                         if row["physical_each"] is not None and row["_supported"] and row["_in_scope"])
    blocking_rows = [row for row in rows if row["_blocking"]]
    complete = (coverage_ok and not blocking_rows and not declaration_blocking
                and all(group["complete"] for group in groups))

    issues = set(coverage_issues) | set(declaration_issues)
    for row in rows:
        issues |= set(issue for issue in row["issues"] if issue.split(":")[0] not in RESOLVED_ISSUES)
    for group in groups:
        issues |= set(group["issues"])

    return {"schema": RESULT_SCHEMA,
            "version": VERSION,
            "request_sha256": request_sha256,
            "binding": _deep_copy(model.binding),
            "scope": _deep_copy(model.scope),
            "rows": [dict((key, _deep_copy(row[key])) for key in ROW_KEYS) for row in rows],
            "groups": groups,
            "declarations": declarations,
            "issues": sorted(issues),
            "complete": complete,
            "known_subtotal_each": known_subtotal,
            "total_each": known_subtotal if complete else None,
            "physical_known_each": physical_known}
