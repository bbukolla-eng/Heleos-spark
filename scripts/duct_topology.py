"""Explicit undirected same-sheet duct path-pair topology diagnostics.

This module canonicalises retained path-pair assertions and compares an
independent truth list with a prediction list. It is pure: it performs no I/O,
no model call, no geometry calculation and no quantity calculation, and it
imports nothing local. Only pairs that an author stated explicitly exist here.

Boundaries
----------
An assertion is a proposal that cites its own evidence. Nothing in this file
infers a relation. An absent pair stays unknown and is never read as
``not_connected``; no transitive closure, snapping, port direction, cross-sheet
joining, fitting count or measured length follows from any assertion. Geometry
correspondence is supplied by the caller as an explicit object assignment and
is never guessed here: an indeterminate or ambiguous correspondence reports
indeterminate rather than a score. The returned counts are diagnostics only.
They add no pass/fail threshold, no acceptance tolerance and no quantity
authority, and they are reported separately from geometric acceptance criteria.

Identity binding
----------------
``normalize_assertions`` is the caller boundary: it binds assertions to the
known object and evidence identifiers of their source and rejects dangling
references. ``compare_assertions`` revalidates the structure of both lists
against the identifiers those lists themselves contain, so it never assumes a
caller normalised them, but it cannot and does not recheck that binding.
"""

MAX_ASSERTIONS = 1000
MAX_OBJECT_IDS = 250
MAX_EVIDENCE_IDS = 2000
MAX_ASSERTION_EVIDENCE = 128
MAX_ASSIGNMENT_PAIRS = 250
MAX_ID_LENGTH = 160
SCOPE = "explicit_same_sheet_path_pairs"
RELATIONS = ("connected", "not_connected", "uncertain")
MATCHING_STATES = ("definite", "indeterminate")
ASSERTION_FIELDS = frozenset({"id", "members", "relation", "evidence_ids"})
ROW_STATES = ("correct", "incorrect", "unresolved", "unmatched_geometry", "indeterminate")
COUNTED_STATES = ("correct", "incorrect", "unresolved", "unmatched_geometry")
_ASSERTIONS = "topology_invalid_assertions"
_OBJECTS = "topology_invalid_objects"
_EVIDENCE = "topology_invalid_evidence"
_ASSIGNMENT = "topology_invalid_assignment"
_OPTIONS = "topology_invalid_options"
_FIELD_LIST = ", ".join(sorted(ASSERTION_FIELDS))
_RELATION_LIST = ", ".join(RELATIONS)


class TopologyError(ValueError):
    """Stable codes: topology_invalid_assertions, topology_invalid_objects,
    topology_invalid_evidence, topology_invalid_assignment and
    topology_invalid_options.

    Messages name the offending position, field and type only. A rejected value
    is never copied into the message, so no source text leaves this boundary.
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _fail(code, message):
    raise TopologyError(code, message)


def _checked_id(value, code, where):
    """An identifier is an exact nonempty stripped string without surrogates."""
    if type(value) is not str:
        _fail(code, "%s must be an exact string, not %s." % (where, type(value).__name__))
    if not value or value.strip() != value:
        _fail(code, "%s must be a nonempty identifier without leading or trailing whitespace." % where)
    if len(value) > MAX_ID_LENGTH:
        _fail(code, "%s holds %d characters, above the limit of %d." % (where, len(value), MAX_ID_LENGTH))
    for character in value:
        if "\ud800" <= character <= "\udfff":
            _fail(code, "%s contains a surrogate code point." % where)
    return value


def _checked_list(value, limit, code, where):
    if type(value) is not list:
        _fail(code, "%s must be a list, not %s." % (where, type(value).__name__))
    if len(value) > limit:
        _fail(code, "%s holds %d entries, above the limit of %d." % (where, len(value), limit))
    return value


def _checked_flag(value, where):
    if type(value) is not bool:
        _fail(_OPTIONS, "%s must be a bool, not %s." % (where, type(value).__name__))
    return value


def _identifier_set(values, limit, code, where):
    _checked_list(values, limit, code, where)
    known = set()
    for index, value in enumerate(values):
        entry = _checked_id(value, code, "%s[%d]" % (where, index))
        if entry in known:
            _fail(code, "%s[%d] repeats an identifier." % (where, index))
        known.add(entry)
    return known


def _canonical_members(item, objects, code, where):
    members = _checked_list(item["members"], 2, code, "%s.members" % where)
    if len(members) != 2:
        _fail(code, "%s.members must hold exactly two object identifiers." % where)
    first = _checked_id(members[0], code, "%s.members[0]" % where)
    second = _checked_id(members[1], code, "%s.members[1]" % where)
    if first == second:
        _fail(code, "%s.members must name two distinct objects." % where)
    if objects is not None:
        for position, member in ((0, first), (1, second)):
            if member not in objects:
                _fail(code, "%s.members[%d] is not a known object identifier." % (where, position))
    return (first, second) if first < second else (second, first)


def _canonical_evidence(item, evidence, code, where):
    cited = _checked_list(item["evidence_ids"], MAX_ASSERTION_EVIDENCE, code, "%s.evidence_ids" % where)
    if not cited:
        _fail(code, "%s.evidence_ids must cite at least one evidence identifier." % where)
    seen = set()
    for position, value in enumerate(cited):
        at = "%s.evidence_ids[%d]" % (where, position)
        entry = _checked_id(value, code, at)
        if entry in seen:
            _fail(code, "%s repeats an evidence identifier." % at)
        if evidence is not None and entry not in evidence:
            _fail(code, "%s is not a known evidence identifier." % at)
        seen.add(entry)
    return seen


def _canonical_assertions(assertions, objects, evidence, allow_uncertain, code, where):
    """Validate and copy one assertion list into canonical order.

    ``objects`` and ``evidence`` are the known identifier sets, or None to take
    the identifiers contained in the list itself as the only known ones. The
    same bounds apply either way, so a derived list stays bounded too.
    """
    _checked_list(assertions, MAX_ASSERTIONS, code, where)
    result, identifiers, pairs, used_objects, used_evidence = [], set(), set(), set(), set()
    for index, item in enumerate(assertions):
        at = "%s[%d]" % (where, index)
        if type(item) is not dict:
            _fail(code, "%s must be a dictionary, not %s." % (at, type(item).__name__))
        if set(item) != ASSERTION_FIELDS:
            _fail(code, "%s must hold exactly the fields %s." % (at, _FIELD_LIST))
        identifier = _checked_id(item["id"], code, "%s.id" % at)
        if identifier in identifiers:
            _fail(code, "%s.id repeats an assertion identifier." % at)
        relation = item["relation"]
        if type(relation) is not str or relation not in RELATIONS:
            _fail(code, "%s.relation must be one of %s." % (at, _RELATION_LIST))
        if relation == "uncertain" and not allow_uncertain:
            _fail(code, "%s.relation may not be uncertain in this list." % at)
        pair = _canonical_members(item, objects, code, at)
        if pair in pairs:
            _fail(code, "%s repeats the undirected member pair of an earlier assertion." % at)
        cited = _canonical_evidence(item, evidence, code, at)
        identifiers.add(identifier)
        pairs.add(pair)
        used_objects.update(pair)
        used_evidence.update(cited)
        result.append({
            "id": identifier,
            "members": [pair[0], pair[1]],
            "relation": relation,
            "evidence_ids": sorted(cited),
        })
    if objects is None and len(used_objects) > MAX_OBJECT_IDS:
        _fail(code, "%s names %d objects, above the limit of %d."
              % (where, len(used_objects), MAX_OBJECT_IDS))
    if evidence is None and len(used_evidence) > MAX_EVIDENCE_IDS:
        _fail(code, "%s cites %d evidence identifiers, above the limit of %d."
              % (where, len(used_evidence), MAX_EVIDENCE_IDS))
    result.sort(key=lambda entry: entry["id"])
    return result


def _canonical_assignment(assignment):
    """Read the caller's one-to-one truth object to prediction object mapping."""
    _checked_list(assignment, MAX_ASSIGNMENT_PAIRS, _ASSIGNMENT, "assignment")
    mapping, taken = {}, set()
    for index, entry in enumerate(assignment):
        at = "assignment[%d]" % index
        _checked_list(entry, 2, _ASSIGNMENT, at)
        if len(entry) != 2:
            _fail(_ASSIGNMENT, "%s must hold exactly one truth identifier and one prediction identifier." % at)
        truth_id = _checked_id(entry[0], _ASSIGNMENT, "%s[0]" % at)
        prediction_id = _checked_id(entry[1], _ASSIGNMENT, "%s[1]" % at)
        if truth_id in mapping:
            _fail(_ASSIGNMENT, "%s repeats a truth object identifier." % at)
        if prediction_id in taken:
            _fail(_ASSIGNMENT, "%s repeats a prediction object identifier." % at)
        mapping[truth_id] = prediction_id
        taken.add(prediction_id)
    return mapping


def normalize_assertions(assertions, object_ids, evidence_ids, allow_uncertain=True):
    """Return a deep independent canonical copy of bound path-pair assertions.

    Every member must be a known object identifier and every citation a known
    evidence identifier, so a dangling reference is rejected before use. The
    inputs are never mutated and nothing is inferred, added or merged.
    """
    _checked_flag(allow_uncertain, "allow_uncertain")
    objects = _identifier_set(object_ids, MAX_OBJECT_IDS, _OBJECTS, "object_ids")
    evidence = _identifier_set(evidence_ids, MAX_EVIDENCE_IDS, _EVIDENCE, "evidence_ids")
    return _canonical_assertions(assertions, objects, evidence, allow_uncertain, _ASSERTIONS, "assertions")


def compare_assertions(truth, prediction, assignment, matching_state, assignment_ambiguous):
    """Score explicitly annotated truth pairs against explicit predictions.

    Both lists are revalidated here against the identifiers they contain; truth
    may not hold an uncertain relation. Only a definite and unambiguous
    geometry correspondence allows comparison. A truth pair whose objects do not
    both map is unmatched_geometry; a mapped pair with no explicit prediction,
    or with an uncertain one, is unresolved. No absent pair becomes a negative.
    """
    _checked_flag(assignment_ambiguous, "assignment_ambiguous")
    if type(matching_state) is not str or matching_state not in MATCHING_STATES:
        _fail(_OPTIONS, "matching_state must be one of %s." % ", ".join(MATCHING_STATES))
    truth_rows = _canonical_assertions(truth, None, None, False, _ASSERTIONS, "truth")
    predictions = _canonical_assertions(prediction, None, None, True, _ASSERTIONS, "prediction")
    mapping = _canonical_assignment(assignment)
    comparable = matching_state == "definite" and not assignment_ambiguous
    by_pair = {tuple(entry["members"]): entry for entry in predictions}
    counts = dict.fromkeys(COUNTED_STATES, 0)
    rows, associated = [], set()
    for entry in truth_rows:
        row = {
            "truth_id": entry["id"],
            "truth_members": list(entry["members"]),
            "expected": entry["relation"],
            "prediction_id": None,
            "prediction_members": None,
            "observed": None,
            "state": "indeterminate",
        }
        if comparable:
            first = mapping.get(entry["members"][0])
            second = mapping.get(entry["members"][1])
            if first is None or second is None:
                row["state"] = "unmatched_geometry"
            else:
                pair = (first, second) if first < second else (second, first)
                row["prediction_members"] = [pair[0], pair[1]]
                found = by_pair.get(pair)
                if found is None:
                    row["state"] = "unresolved"
                else:
                    associated.add(found["id"])
                    row["prediction_id"] = found["id"]
                    row["observed"] = found["relation"]
                    if found["relation"] == "uncertain":
                        row["state"] = "unresolved"
                    elif found["relation"] == entry["relation"]:
                        row["state"] = "correct"
                    else:
                        row["state"] = "incorrect"
            counts[row["state"]] += 1
        rows.append(row)
    truth_count = len(truth_rows)
    scored = comparable and truth_count > 0
    return {
        "state": "evaluated" if comparable else "indeterminate",
        "scope": SCOPE,
        "truth_count": truth_count,
        "correct": counts["correct"],
        "incorrect": counts["incorrect"],
        "unresolved": counts["unresolved"],
        "unmatched_geometry": counts["unmatched_geometry"],
        "known_pair_accuracy": counts["correct"] / truth_count if scored else None,
        "coverage": (counts["correct"] + counts["incorrect"]) / truth_count if scored else None,
        "rows": rows,
        "unscored_prediction_ids": sorted(
            entry["id"] for entry in predictions if entry["id"] not in associated),
    }
