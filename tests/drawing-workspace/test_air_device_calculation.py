"""Behaviour checks for the pure source-bound air-device count kernel.

Every expectation is written from the approved rules A01-A12 and the seventeen
approved literal examples, independently of the implementation: quantities are
stated as whole numbers, unknown states are asserted as null rather than zero,
and grouping expectations are stated as equalities between whole canonical
records. The answer records themselves are never fed to the kernel as input.

These are kernel checks. They do not claim persisted append-only history,
original source capture, rendering, live model execution, representative project
accuracy or native Windows acceptance; those remain separate connected work.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name + "_check", ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kernel = _load("air_device_calculation")
air = _load("air_device_attributes")
rules = _load("air_device_rules")
BINDING = rules.load_binding(ROOT)

DEFAULT_GROUP = ("family", "face_size", "neck_size", "work_status")


# --------------------------------------------------------------------------
# Request fixtures
# --------------------------------------------------------------------------

def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source(name, index=0):
    return {"revision_id": digest("rev/" + name), "index": index,
            "sheet_id": digest("sheet/" + name), "geometry_fingerprint": digest("geom/" + name)}


def key(name, index=0):
    return kernel.fingerprint(source(name, index))


def context(name, role="plan", index=0, artifact=None):
    return {"source": source(name, index),
            "artifact_sha256": digest(artifact or ("art/" + name)), "role": role}


def box(number):
    base = 0.01 * (number % 80)
    return [base, base, base + 0.05, base + 0.05]


def graphic(identifier, name, number, index=0, artifact=None):
    return {"id": identifier, "source": source(name, index), "bbox": box(number),
            "kind": "graphic", "text": None,
            "artifact_sha256": digest(artifact or ("art/" + name))}


def note(identifier, name, number, text, index=0, artifact=None):
    return {"id": identifier, "source": source(name, index), "bbox": box(number),
            "kind": "text", "text": text,
            "artifact_sha256": digest(artifact or ("art/" + name))}


def entry(state="unknown", value=None, evidence=()):
    return {"state": state, "value": value, "evidence_ids": list(evidence)}


def known(value, evidence):
    return entry("known", value, evidence)


def size(shape, dimensions, unit, text):
    return {"shape": shape, "dimensions": list(dimensions), "unit": unit, "original_text": text}


def blank_attributes():
    return dict((field, entry()) for field in air.FIELDS)


def device(evidence_id, family="diffuser", tag="SD-1", status="new_install", system=None,
           face=("24", "24"), neck=("8",), unit="in", service="supply"):
    """A supported air device: family, size and work status are source backed."""
    reference = (evidence_id,)
    record = blank_attributes()
    record["family"] = known(family, reference)
    record["type_tag"] = known(tag, reference) if tag is not None else entry("not_supplied")
    record["system"] = known(system, reference) if system is not None else entry("not_supplied")
    record["service"] = known(service, reference)
    record["work_status"] = (known(status, reference) if status is not None
                             else entry("unknown"))
    record["face_size"] = (known(size("rectangular", face, unit, "face " + str(face)), reference)
                           if face is not None else entry("not_supplied"))
    record["neck_size"] = (known(size("round", neck, unit, "neck " + str(neck)), reference)
                           if neck is not None else entry("not_supplied"))
    record["opening_size"] = entry("not_supplied")
    record["assembly_length"] = entry("not_applicable", None, reference)
    record["slot_count"] = entry("not_applicable", None, reference)
    return record


def observation(identifier, name, number, depiction="physical", attributes=None,
                evidence=(), index=0):
    return {"schema": kernel.OBSERVATION_SCHEMA, "id": identifier, "source": source(name, index),
            "bbox": box(number), "depiction": depiction,
            "attributes": attributes if attributes is not None else blank_attributes(),
            "evidence_ids": list(evidence), "issues": []}


def pin(record):
    return kernel.fingerprint(kernel.validate_observation(record))


def member(record):
    return {"observation_id": record["id"], "observation_sha256": pin(record)}


def admit(record, action="include", reason="reviewed against the source", evidence=()):
    return {"observation_id": record["id"], "observation_sha256": pin(record),
            "action": action, "reason": reason, "evidence_ids": list(evidence)}


def scope(keys, group_by=DEFAULT_GROUP, required=None, statuses=("new_install",), version=1):
    return {"id": "scope-air-1", "version": version, "source_keys": list(keys),
            "group_by": list(group_by),
            "required_fields": list(group_by if required is None else required),
            "work_statuses": list(statuses)}


def request(sources, evidence, observations, decisions=(), scope_record=None,
            correspondences=(), multiplicities=(), relocations=(), schedules=()):
    return {"schema": kernel.REQUEST_SCHEMA,
            "binding": copy.deepcopy(BINDING),
            "scope": scope_record if scope_record is not None else scope([key("M1.1")]),
            "sources": list(sources),
            "evidence": list(evidence),
            "observations": list(observations),
            "decisions": list(decisions),
            "correspondences": list(correspondences),
            "multiplicities": list(multiplicities),
            "relocations": list(relocations),
            "schedules": list(schedules)}


def reviewed(payload, witnesses, state="complete", requirements=(), basis=True):
    """Attach an explicitly recorded scope-completeness decision to a request."""
    payload = dict((name, value) for name, value in payload.items() if name != "coverage")
    pinned = kernel.coverage_basis(payload) if basis else None
    payload["coverage"] = {"state": state, "basis_sha256": pinned,
                           "evidence_ids": list(witnesses),
                           "unresolved_requirements": list(requirements)}
    return payload


def row_for(result, observation_id):
    for row in result["rows"]:
        if observation_id in row["member_ids"]:
            return row
    raise AssertionError("no accounting row holds " + observation_id)


def group_for(result, row_id):
    for group in result["groups"]:
        if row_id in group["row_ids"]:
            return group
    raise AssertionError("no group holds " + row_id)


def room_a():
    """The AC01 fixture: three separate SD-1 symbols and one SD-1 legend."""
    sources = [context("M1.1", "plan"), context("M0.1", "legend")]
    evidence = [graphic("g-1", "M1.1", 1), graphic("g-2", "M1.1", 2), graphic("g-3", "M1.1", 3),
                graphic("g-legend", "M0.1", 4),
                note("w-plan", "M1.1", 5, "Room A air distribution reviewed in full")]
    observations = []
    for index in (1, 2, 3):
        observations.append(observation("A-SD1-" + str(index), "M1.1", index,
                                        attributes=device("g-" + str(index)),
                                        evidence=["g-" + str(index)]))
    legend_attributes = device("g-legend", status=None)
    legend_attributes["work_status"] = entry("not_applicable", None, ("g-legend",))
    observations.append(observation("legend-SD1", "M0.1", 4, depiction="legend",
                                    attributes=legend_attributes, evidence=["g-legend"]))
    decisions = [admit(record, evidence=[record["evidence_ids"][0]])
                 for record in observations[:3]]
    payload = request(sources, evidence, observations, decisions, scope([key("M1.1")]))
    return reviewed(payload, ["w-plan"])


# --------------------------------------------------------------------------
# The seventeen approved examples
# --------------------------------------------------------------------------

class ApprovedExampleTests(unittest.TestCase):
    """AC01-AC17 as semantic kernel checks under the approved rules."""

    def test_ac01_three_symbols_count_three_and_a_legend_counts_nothing(self):
        result = kernel.calculate(room_a())
        self.assertEqual(result["known_subtotal_each"], 3)
        self.assertEqual(result["total_each"], 3)
        self.assertEqual(result["physical_known_each"], 3)
        self.assertTrue(result["complete"])
        self.assertEqual(row_for(result, "legend-SD1")["physical_each"], 0)
        self.assertIs(row_for(result, "legend-SD1")["requested"], False)
        for index in (1, 2, 3):
            row = row_for(result, "A-SD1-" + str(index))
            self.assertEqual(row["physical_each"], 1)
            self.assertEqual(row["member_ids"], ["A-SD1-" + str(index)])
        # One shared type tag never merges three distinct physical devices.
        self.assertEqual(len(result["rows"]), 4)
        device_group = group_for(result, row_for(result, "A-SD1-1")["row_id"])
        self.assertEqual(device_group["known_subtotal_each"], 3)
        self.assertEqual(device_group["total_each"], 3)
        self.assertEqual(len(device_group["row_ids"]), 3)

    def test_ac02_verified_same_depictions_collapse_to_one_assembly(self):
        sources = [context("M1.2", "plan"), context("M4.1", "enlarged_plan")]
        evidence = [graphic("g-plan", "M1.2", 6), graphic("g-detail", "M4.1", 7),
                    note("w-plan", "M1.2", 8, "sheet reviewed"),
                    note("w-detail", "M4.1", 9, "enlarged plan reviewed"),
                    note("c-note", "M4.1", 10, "Enlarged view of the same SD-2 terminal")]
        plan = observation("B-plan-SD2", "M1.2", 6, attributes=device("g-plan", tag="SD-2"),
                           evidence=["g-plan"])
        detail = observation("B-detail-SD2", "M4.1", 7, attributes=device("g-detail", tag="SD-2"),
                             evidence=["g-detail"])
        correspondence = [{"id": "corr-1", "members": [member(plan), member(detail)],
                           "state": "same", "evidence_ids": ["c-note"]}]
        payload = request(sources, evidence, [plan, detail],
                          [admit(plan, evidence=["g-plan"]), admit(detail, evidence=["g-detail"])],
                          scope([key("M1.2"), key("M4.1")]), correspondences=correspondence)
        result = kernel.calculate(reviewed(payload, ["w-plan", "w-detail"]))
        self.assertEqual(result["known_subtotal_each"], 1)
        self.assertEqual(result["total_each"], 1)
        self.assertTrue(result["complete"])
        row = row_for(result, "B-plan-SD2")
        self.assertEqual(row["member_ids"], ["B-detail-SD2", "B-plan-SD2"])
        self.assertEqual(len(row["member_ids"]), 2)  # both depictions stay retained
        self.assertEqual(len(result["rows"]), 1)

    def test_ac03_unresolved_correspondence_blocks_the_affected_total(self):
        sources = [context("M1.2", "plan"), context("M4.1", "enlarged_plan")]
        evidence = [graphic("g-plan", "M1.2", 6), graphic("g-detail", "M4.1", 7),
                    note("w-plan", "M1.2", 8, "sheet reviewed"),
                    note("c-note", "M4.1", 10, "relationship not decided")]
        plan = observation("B-plan-SD2", "M1.2", 6, attributes=device("g-plan", tag="SD-2"),
                           evidence=["g-plan"])
        detail = observation("B-detail-SD2", "M4.1", 7, attributes=device("g-detail", tag="SD-2"),
                             evidence=["g-detail"])
        correspondence = [{"id": "corr-1", "members": [member(plan), member(detail)],
                           "state": "unresolved", "evidence_ids": ["c-note"]}]
        payload = request(sources, evidence, [plan, detail],
                          [admit(plan, evidence=["g-plan"]), admit(detail, evidence=["g-detail"])],
                          scope([key("M1.2"), key("M4.1")]), correspondences=correspondence)
        result = kernel.calculate(reviewed(payload, ["w-plan"], state="partial", basis=False))
        self.assertEqual(result["known_subtotal_each"], 0)
        self.assertIsNone(result["total_each"])
        self.assertFalse(result["complete"])
        self.assertIn("correspondence_unresolved", result["issues"])
        for identifier in ("B-plan-SD2", "B-detail-SD2"):
            # Zero known is not a zero-device claim; the quantity is unknown.
            self.assertIsNone(row_for(result, identifier)["physical_each"])

    def test_ac04_scoped_multiplicity_covers_its_representative_once(self):
        sources = [context("M1.3", "plan")]
        evidence = [graphic("g-rg1", "M1.3", 11),
                    note("n-typ", "M1.3", 12, "(4) RG-1 NEW GRILLES IN ROOM C TOTAL"),
                    note("w-plan", "M1.3", 13, "Room C reviewed in full")]
        representative = observation("C-RG1", "M1.3", 11,
                                     attributes=device("g-rg1", family="grille", tag="RG-1"),
                                     evidence=["g-rg1"])
        multiplicity = [{"id": "mult-1", "members": [member(representative)],
                         "representative": "C-RG1", "each": 4,
                         "scope_text": "Room C only", "evidence_ids": ["n-typ"]}]
        payload = request(sources, evidence, [representative],
                          [admit(representative, evidence=["g-rg1"])], scope([key("M1.3")]),
                          multiplicities=multiplicity)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["known_subtotal_each"], 4)
        self.assertEqual(result["total_each"], 4)
        self.assertTrue(result["complete"])
        row = row_for(result, "C-RG1")
        self.assertEqual(row["physical_each"], 4)
        self.assertEqual(row["member_ids"], ["C-RG1"])  # no extra representative each

    def test_ac05_work_statuses_stay_separate_and_are_never_netted(self):
        sources = [context("M1.4", "plan")]
        evidence = [graphic("g-new", "M1.4", 14), graphic("g-old", "M1.4", 15),
                    graphic("g-out", "M1.4", 16), note("w-plan", "M1.4", 17, "sheet reviewed")]
        records = [
            observation("D-new", "M1.4", 14, attributes=device("g-new", tag="SD-3"),
                        evidence=["g-new"]),
            observation("D-existing", "M1.4", 15,
                        attributes=device("g-old", tag="SD-3", status="existing_to_remain"),
                        evidence=["g-old"]),
            observation("D-remove", "M1.4", 16,
                        attributes=device("g-out", tag="SD-3", status="demolition"),
                        evidence=["g-out"])]
        decisions = [admit(item, evidence=[item["evidence_ids"][0]]) for item in records]
        payload = request(sources, evidence, records, decisions,
                          scope([key("M1.4")], statuses=("new_install",)))
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["known_subtotal_each"], 1)
        self.assertEqual(result["total_each"], 1)
        self.assertTrue(result["complete"])
        self.assertEqual(result["physical_known_each"], 3)
        self.assertIs(row_for(result, "D-new")["requested"], True)
        for identifier in ("D-existing", "D-remove"):
            row = row_for(result, identifier)
            self.assertIs(row["requested"], False)
            self.assertEqual(row["physical_each"], 1)  # visible, never netted away
            self.assertEqual(group_for(result, row["row_id"])["physical_known_each"], 1)

    def test_ac06_schedule_quantity_conflict_keeps_the_observed_subtotal(self):
        sources = [context("M1.5", "plan"), context("M10.1", "schedule")]
        evidence = [graphic("g-1", "M1.5", 18), graphic("g-2", "M1.5", 19),
                    graphic("g-3", "M1.5", 20), note("w-plan", "M1.5", 21, "sheet reviewed"),
                    note("s-row", "M10.1", 22, "SD-4 schedule row, quantity 4")]
        records = [observation("E-SD4-" + str(n), "M1.5", 17 + n,
                               attributes=device("g-" + str(n), tag="SD-4"),
                               evidence=["g-" + str(n)]) for n in (1, 2, 3)]
        decisions = [admit(item, evidence=[item["evidence_ids"][0]]) for item in records]
        schedules = [{"id": "sched-SD4", "members": [member(item) for item in records],
                      "declared_each": 4, "attributes": {}, "evidence_ids": ["s-row"]}]
        payload = request(sources, evidence, records, decisions, scope([key("M1.5")]),
                          schedules=schedules)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        declaration = result["declarations"][0]
        self.assertEqual(declaration["observed_each"], 3)
        self.assertEqual(declaration["declared_each"], 4)
        self.assertEqual(declaration["state"], "conflict")
        self.assertIn("schedule_quantity_mismatch", declaration["issues"])
        self.assertEqual(result["known_subtotal_each"], 3)
        self.assertIsNone(result["total_each"])
        self.assertFalse(result["complete"])

    def test_ac07_one_linear_assembly_counts_one_each_with_its_attributes(self):
        sources = [context("M1.6", "plan")]
        evidence = [graphic("g-ld1", "M1.6", 23), note("w-plan", "M1.6", 24, "sheet reviewed")]
        attributes = device("g-ld1", family="linear_diffuser", tag="LD-1", face=None, neck=None)
        attributes["assembly_length"] = known(
            {"value": "6", "unit": "ft", "original_text": "6'-0\" assembly"}, ("g-ld1",))
        attributes["slot_count"] = known(2, ("g-ld1",))
        record = observation("F-LD1", "M1.6", 23, attributes=attributes, evidence=["g-ld1"])
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-ld1"])],
                          scope([key("M1.6")], group_by=("family", "work_status")))
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["known_subtotal_each"], 1)
        self.assertEqual(result["total_each"], 1)
        self.assertTrue(result["complete"])
        row = row_for(result, "F-LD1")
        self.assertEqual(row["attributes"]["slot_count"]["value"], 2)
        self.assertEqual(row["attributes"]["assembly_length"]["value"]["value"], "72.000000")
        self.assertEqual(row["attributes"]["assembly_length"]["value"]["original_text"], "6'-0\" assembly")
        self.assertEqual(row["physical_each"], 1)

        # Neither the stated length nor the slot count is ever a quantity.
        longer = copy.deepcopy(attributes)
        longer["assembly_length"] = known(
            {"value": "20", "unit": "ft", "original_text": "20'-0\" assembly"}, ("g-ld1",))
        longer["slot_count"] = known(8, ("g-ld1",))
        stretched = observation("F-LD1", "M1.6", 23, attributes=longer, evidence=["g-ld1"])
        payload = request(sources, evidence, [stretched], [admit(stretched, evidence=["g-ld1"])],
                          scope([key("M1.6")], group_by=("family", "work_status")))
        stretched_result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(stretched_result["total_each"], 1)
        self.assertIsNone(row_for(stretched_result, "F-LD1")["new_purchase_each"])

    def test_ac08_unresolved_grouping_keeps_the_known_physical_subtotal(self):
        sources = [context("M1.7", "plan")]
        evidence = [graphic("g-x", "M1.7", 25), note("w-plan", "M1.7", 26, "sheet reviewed")]
        record = observation("G-unsized", "M1.7", 25,
                             attributes=device("g-x", face=None, neck=None), evidence=["g-x"])
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-x"])],
                          scope([key("M1.7")]))
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["known_subtotal_each"], 1)
        self.assertIsNone(result["total_each"])
        self.assertFalse(result["complete"])
        self.assertEqual(row_for(result, "G-unsized")["physical_each"], 1)
        group = group_for(result, row_for(result, "G-unsized")["row_id"])
        self.assertFalse(group["complete"])
        self.assertIn("required_field_unresolved:face_size", group["issues"])
        self.assertIsNone(group["total_each"])

    def test_ac09_a_missing_tag_alone_never_prevents_a_supported_count(self):
        sources = [context("M1.8", "plan")]
        evidence = [graphic("g-y", "M1.8", 27), note("w-plan", "M1.8", 28, "sheet reviewed")]
        record = observation("H-untagged", "M1.8", 27, attributes=device("g-y", tag=None),
                             evidence=["g-y"])
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-y"])],
                          scope([key("M1.8")]))
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["total_each"], 1)
        self.assertTrue(result["complete"])
        row = row_for(result, "H-untagged")
        self.assertEqual(row["attributes"]["type_tag"]["state"], "not_supplied")
        self.assertIsNone(row["attributes"]["type_tag"]["value"])  # no invented tag

    def test_ac10_an_each_count_has_no_scale_dependency(self):
        payload = room_a()
        first = kernel.calculate(payload)
        second = kernel.calculate(copy.deepcopy(payload))
        self.assertEqual(first, second)
        self.assertEqual(first["total_each"], 3)
        self.assertEqual(second["total_each"], 3)
        self.assertNotIn("scale", json.dumps(first).lower())
        withdrawn = copy.deepcopy(payload)
        withdrawn["scale"] = {"status": "withdrawn", "units_per_inch": "96"}
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(withdrawn)
        self.assertEqual(error.exception.code, "structure_invalid")

    def test_ac11_duplicate_proposals_of_one_symbol_count_once(self):
        sources = [context("M1.9", "plan")]
        evidence = [graphic("g-sym", "M1.9", 29), note("w-plan", "M1.9", 30, "sheet reviewed"),
                    note("c-dup", "M1.9", 31, "Both proposals read the same plan symbol")]
        first = observation("duplicate-proposal-1", "M1.9", 29, attributes=device("g-sym"),
                            evidence=["g-sym"])
        second = observation("duplicate-proposal-2", "M1.9", 29, attributes=device("g-sym"),
                             evidence=["g-sym"])
        correspondence = [{"id": "corr-dup", "members": [member(first), member(second)],
                           "state": "same", "evidence_ids": ["c-dup"]}]
        payload = request(sources, evidence, [first, second],
                          [admit(first, evidence=["g-sym"]), admit(second, evidence=["g-sym"])],
                          scope([key("M1.9")]), correspondences=correspondence)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["total_each"], 1)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["member_ids"],
                         ["duplicate-proposal-1", "duplicate-proposal-2"])

    def test_ac12_verified_relocation_is_one_assembly_with_two_operations(self):
        sources = [context("M2.1", "plan")]
        evidence = [graphic("g-old", "M2.1", 32), graphic("g-new", "M2.1", 33),
                    note("w-plan", "M2.1", 34, "sheet reviewed"),
                    note("r-note", "M2.1", 35, "Remove and reinstall the existing diffuser")]
        old = observation("room-old", "M2.1", 32,
                          attributes=device("g-old", status="demolition"), evidence=["g-old"])
        new = observation("room-new", "M2.1", 33,
                          attributes=device("g-new", status="new_install"), evidence=["g-new"])
        relocations = [{"id": "reloc-1", "members": [member(old), member(new)],
                        "remove_members": ["room-old"], "reinstall_members": ["room-new"],
                        "remove_operation_id": "op-remove-1",
                        "reinstall_operation_id": "op-reinstall-1",
                        "reused": True, "evidence_ids": ["r-note"]}]
        payload = request(sources, evidence, [old, new],
                          [admit(old, evidence=["g-old"]), admit(new, evidence=["g-new"])],
                          scope([key("M2.1")], statuses=("relocated",)),
                          relocations=relocations)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        row = row_for(result, "room-old")
        self.assertEqual(row["physical_each"], 1)
        self.assertEqual(row["operations"], {"remove": 1, "reinstall": 1})
        self.assertEqual(row["new_purchase_each"], 0)
        self.assertEqual(row["attributes"]["work_status"]["value"], "relocated")
        self.assertEqual(row["member_ids"], ["room-new", "room-old"])
        self.assertEqual(result["total_each"], 1)
        self.assertTrue(result["complete"])
        # The differing member statuses are expected, not an attribute conflict.
        self.assertEqual(row["conflicts"], [])
        self.assertEqual(row["issues"], [])

    def test_ac13_empty_detection_is_unknown_and_a_reviewed_empty_scope_is_zero(self):
        sources = [context("M3.1", "plan")]
        evidence = [note("w-plan", "M3.1", 36, "Selected scope reviewed; no air devices present")]
        payload = request(sources, evidence, [], [], scope([key("M3.1")]))
        empty_response = kernel.calculate(reviewed(payload, [], state="unknown", basis=False))
        self.assertEqual(empty_response["known_subtotal_each"], 0)
        self.assertIsNone(empty_response["total_each"])
        self.assertFalse(empty_response["complete"])
        verified_empty = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(verified_empty["known_subtotal_each"], 0)
        self.assertEqual(verified_empty["total_each"], 0)
        self.assertTrue(verified_empty["complete"])

    def test_ac14_a_correction_preserves_unrelated_rows_and_replays_identically(self):
        sources = [context("M4.2", "plan"), context("M4.3", "plan")]
        evidence = [graphic("g-1", "M4.2", 37), graphic("g-2", "M4.2", 38),
                    graphic("g-3", "M4.2", 39), graphic("g-h", "M4.3", 40),
                    note("w-1", "M4.2", 41, "sheet reviewed"),
                    note("w-2", "M4.3", 42, "sheet reviewed"),
                    note("correction", "M4.2", 43, "G-3 is furniture, not an air device")]
        affected = [observation("G-" + str(n), "M4.2", 36 + n, attributes=device("g-" + str(n)),
                                evidence=["g-" + str(n)]) for n in (1, 2, 3)]
        unrelated = observation("H-1", "M4.3", 40, attributes=device("g-h"), evidence=["g-h"])
        records = affected + [unrelated]
        decisions = [admit(item, evidence=[item["evidence_ids"][0]]) for item in records]
        selected = scope([key("M4.2"), key("M4.3")])
        before = kernel.calculate(reviewed(request(sources, evidence, records, decisions, selected),
                                           ["w-1", "w-2"]))
        self.assertEqual(sum(row_for(before, "G-" + str(n))["physical_each"] for n in (1, 2, 3)), 3)
        self.assertEqual(row_for(before, "H-1")["physical_each"], 1)

        corrected = [admit(affected[2], action="exclude", reason="confirmed unrelated furniture",
                           evidence=["correction"])]
        corrected += [admit(item, evidence=[item["evidence_ids"][0]])
                      for item in [affected[0], affected[1], unrelated]]
        after = kernel.calculate(reviewed(
            request(sources, evidence, records, corrected, selected), ["w-1", "w-2"]))
        self.assertEqual(sum(row_for(after, "G-" + str(n))["physical_each"] for n in (1, 2, 3)), 2)
        self.assertEqual(after["total_each"], 3)
        self.assertEqual(before["total_each"], 4)
        # The unrelated row keeps its exact identity and dependency.
        self.assertEqual(row_for(before, "H-1")["row_id"], row_for(after, "H-1")["row_id"])
        self.assertEqual(row_for(before, "H-1")["dependency_sha256"],
                         row_for(after, "H-1")["dependency_sha256"])
        self.assertNotEqual(row_for(before, "G-3")["dependency_sha256"],
                            row_for(after, "G-3")["dependency_sha256"])
        replay = kernel.calculate(reviewed(
            request(sources, evidence, records, corrected, selected), ["w-1", "w-2"]))
        self.assertEqual(after, replay)

    def test_ac15_a_type_tag_text_box_alone_is_not_one_each(self):
        sources = [context("M5.1", "plan")]
        evidence = [note("t-tag", "M5.1", 44, "SD-1"), note("w-plan", "M5.1", 45, "partial read")]
        attributes = blank_attributes()
        attributes["type_tag"] = known("SD-1", ("t-tag",))
        record = observation("tag-only-SD1", "M5.1", 44, depiction="tag_only",
                             attributes=attributes, evidence=["t-tag"])
        payload = request(sources, evidence, [record], [admit(record, evidence=["t-tag"])],
                          scope([key("M5.1")]))
        result = kernel.calculate(reviewed(payload, ["w-plan"], state="partial", basis=False))
        self.assertEqual(result["known_subtotal_each"], 0)
        self.assertIsNone(result["total_each"])
        self.assertFalse(result["complete"])
        self.assertIsNone(row_for(result, "tag-only-SD1")["physical_each"])
        self.assertIn("physical_instance_unresolved", result["issues"])

    def test_ac16_an_architectural_louver_is_retained_but_never_counted(self):
        sources = [context("M6.1", "plan")]
        evidence = [graphic("g-mech", "M6.1", 46), graphic("g-arch", "M6.1", 47),
                    note("w-plan", "M6.1", 48, "sheet reviewed")]
        mechanical = observation("L-mech", "M6.1", 46,
                                 attributes=device("g-mech", family="mechanical_louver", tag="ML-1"),
                                 evidence=["g-mech"])
        architectural = observation("L-arch", "M6.1", 47,
                                    attributes=device("g-arch", family="architectural_louver",
                                                      tag="AL-1"),
                                    evidence=["g-arch"])
        payload = request(sources, evidence, [mechanical, architectural],
                          [admit(mechanical, evidence=["g-mech"]),
                           admit(architectural, evidence=["g-arch"])], scope([key("M6.1")]))
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["known_subtotal_each"], 1)
        self.assertEqual(result["total_each"], 1)
        self.assertTrue(result["complete"])
        architectural_row = row_for(result, "L-arch")
        self.assertEqual(architectural_row["physical_each"], 0)
        self.assertEqual(architectural_row["issues"], ["class_excluded"])
        self.assertEqual(architectural_row["attributes"]["family"]["value"], "architectural_louver")

    def test_ac17_a_requested_system_breakdown_waits_for_its_correction(self):
        sources = [context("M7.1", "plan")]
        evidence = [graphic("g-sys", "M7.1", 49), note("w-plan", "M7.1", 50, "sheet reviewed"),
                    note("sys-note", "M7.1", 51, "Served by AHU-1 per the riser")]
        grouping = ("system", "family", "face_size", "neck_size", "work_status")
        initial = observation("S-1", "M7.1", 49, attributes=device("g-sys"), evidence=["g-sys"])
        payload = request(sources, evidence, [initial], [admit(initial, evidence=["g-sys"])],
                          scope([key("M7.1")], group_by=grouping))
        before = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(before["known_subtotal_each"], 1)
        self.assertIsNone(before["total_each"])
        self.assertFalse(before["complete"])
        self.assertIn("required_field_unresolved:system", before["issues"])

        corrected_attributes = device("g-sys")
        corrected_attributes["system"] = known("AHU-1", ("sys-note",))
        corrected = observation("S-1", "M7.1", 49, attributes=corrected_attributes,
                                evidence=["g-sys", "sys-note"])
        payload = request(sources, evidence, [corrected],
                          [admit(corrected, evidence=["g-sys"])],
                          scope([key("M7.1")], group_by=grouping))
        after = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(after["total_each"], 1)
        self.assertTrue(after["complete"])
        row = row_for(after, "S-1")
        self.assertEqual(row["attributes"]["system"]["value"], "AHU-1")
        self.assertEqual(row["canonical_attributes"]["system"]["value"], "ahu-1")
        self.assertEqual(group_for(after, row["row_id"])["key"][0],
                         {"field": "system", "state": "known", "value": "ahu-1"})
        # A service description never supplies the system identity.
        self.assertEqual(row["attributes"]["service"]["value"], "supply")


# --------------------------------------------------------------------------
# Stated edge cases
# --------------------------------------------------------------------------

class EdgeCaseTests(unittest.TestCase):
    """The contract's additional required checks beyond the literal examples."""

    def test_a_stale_complete_review_cannot_produce_a_final_zero(self):
        sources = [context("M3.1", "plan")]
        evidence = [note("w-plan", "M3.1", 36, "Selected scope reviewed; no air devices present")]
        payload = reviewed(request(sources, evidence, [], [], scope([key("M3.1")])), ["w-plan"])
        payload["coverage"]["basis_sha256"] = digest("an older request")
        result = kernel.calculate(payload)
        self.assertIsNone(result["total_each"])
        self.assertFalse(result["complete"])
        self.assertIn("coverage_basis_stale", result["issues"])

    def test_a_reviewed_scope_must_witness_every_selected_source(self):
        sources = [context("M4.2", "plan"), context("M4.3", "plan")]
        evidence = [graphic("g-h", "M4.3", 40), note("w-1", "M4.2", 41, "sheet reviewed")]
        record = observation("H-1", "M4.3", 40, attributes=device("g-h"), evidence=["g-h"])
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-h"])],
                          scope([key("M4.2"), key("M4.3")]))
        result = kernel.calculate(reviewed(payload, ["w-1"]))
        self.assertIn("coverage_source_uncovered", result["issues"])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["total_each"])
        self.assertEqual(result["known_subtotal_each"], 1)  # the known subtotal survives

    def test_an_unavailable_selected_source_never_shrinks_the_scope(self):
        sources = [context("M4.2", "plan")]
        evidence = [graphic("g-1", "M4.2", 37), graphic("g-9", "M9.9", 44),
                    note("w-1", "M4.2", 41, "sheet reviewed")]
        record = observation("G-1", "M4.2", 37, attributes=device("g-1"), evidence=["g-1"])
        stale = observation("G-9", "M9.9", 44, attributes=device("g-9"), evidence=["g-9"])
        selected = scope([key("M4.2"), key("M9.9")])
        payload = request(sources, evidence, [record, stale],
                          [admit(record, evidence=["g-1"]), admit(stale, evidence=["g-9"])],
                          selected)
        result = kernel.calculate(reviewed(payload, ["w-1"]))
        self.assertIn("scope_source_unavailable", result["issues"])
        self.assertIn("source_stale", result["issues"])
        self.assertIsNone(row_for(result, "G-9")["physical_each"])
        self.assertEqual(result["known_subtotal_each"], 1)
        self.assertFalse(result["complete"])
        self.assertIsNone(result["total_each"])
        self.assertEqual(result["scope"]["source_keys"], sorted([key("M4.2"), key("M9.9")]))

    def test_evidence_bound_to_a_superseded_image_cannot_support_a_count(self):
        sources = [context("M4.2", "plan", artifact="revision-b")]
        evidence = [graphic("g-1", "M4.2", 37, artifact="revision-a"),
                    note("w-1", "M4.2", 41, "sheet reviewed", artifact="revision-b")]
        record = observation("G-1", "M4.2", 37, attributes=device("g-1"), evidence=["g-1"])
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-1"])],
                          scope([key("M4.2")]))
        result = kernel.calculate(reviewed(payload, ["w-1"]))
        self.assertIsNone(row_for(result, "G-1")["physical_each"])
        self.assertIn("graphic_evidence_missing", result["issues"])
        self.assertIsNone(result["total_each"])

    def test_an_uncertain_pair_does_not_disturb_an_unrelated_known_device(self):
        sources = [context("M1.2", "plan"), context("M4.1", "enlarged_plan")]
        evidence = [graphic("g-plan", "M1.2", 6), graphic("g-detail", "M4.1", 7),
                    graphic("g-other", "M1.2", 52), note("w-plan", "M1.2", 8, "reviewed"),
                    note("w-detail", "M4.1", 9, "reviewed"),
                    note("c-note", "M4.1", 10, "relationship not decided")]
        plan = observation("P-1", "M1.2", 6, attributes=device("g-plan"), evidence=["g-plan"])
        detail = observation("P-2", "M4.1", 7, attributes=device("g-detail"), evidence=["g-detail"])
        other = observation("P-3", "M1.2", 52,
                            attributes=device("g-other", family="register", tag="RG-9"),
                            evidence=["g-other"])
        correspondence = [{"id": "corr-1", "members": [member(plan), member(detail)],
                           "state": "unresolved", "evidence_ids": ["c-note"]}]
        payload = request(sources, evidence, [plan, detail, other],
                          [admit(item, evidence=[item["evidence_ids"][0]])
                           for item in (plan, detail, other)],
                          scope([key("M1.2"), key("M4.1")]), correspondences=correspondence)
        result = kernel.calculate(reviewed(payload, ["w-plan", "w-detail"]))
        self.assertEqual(result["known_subtotal_each"], 1)
        self.assertEqual(row_for(result, "P-3")["physical_each"], 1)
        self.assertIsNone(result["total_each"])
        self.assertTrue(group_for(result, row_for(result, "P-3")["row_id"])["complete"])

    def test_duplicate_depictions_inside_a_multiplicity_are_collapsed_first(self):
        sources = [context("M1.3", "plan")]
        evidence = [graphic("g-a", "M1.3", 11), graphic("g-b", "M1.3", 53),
                    note("n-typ", "M1.3", 12, "(3) RG-1 TOTAL IN ROOM C"),
                    note("c-same", "M1.3", 54, "Both readings show one grille"),
                    note("w-plan", "M1.3", 13, "reviewed")]
        first = observation("M-1", "M1.3", 11, attributes=device("g-a", family="grille"),
                            evidence=["g-a"])
        second = observation("M-2", "M1.3", 11, attributes=device("g-a", family="grille"),
                             evidence=["g-a"])
        correspondence = [{"id": "corr-same", "members": [member(first), member(second)],
                           "state": "same", "evidence_ids": ["c-same"]}]
        multiplicity = [{"id": "mult-1", "members": [member(first), member(second)],
                         "representative": "M-1", "each": 3, "scope_text": "Room C",
                         "evidence_ids": ["n-typ"]}]
        payload = request(sources, evidence, [first, second],
                          [admit(first, evidence=["g-a"]), admit(second, evidence=["g-a"])],
                          scope([key("M1.3")]), correspondences=correspondence,
                          multiplicities=multiplicity)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["total_each"], 3)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["member_ids"], ["M-1", "M-2"])

    def test_a_group_total_below_its_supported_members_stays_unresolved(self):
        sources = [context("M1.3", "plan")]
        evidence = [graphic("g-a", "M1.3", 11), graphic("g-b", "M1.3", 53),
                    graphic("g-c", "M1.3", 55), note("n-typ", "M1.3", 12, "(2) RG-1 TOTAL"),
                    note("w-plan", "M1.3", 13, "reviewed")]
        records = [observation("N-" + str(n), "M1.3", box_number,
                               attributes=device("g-" + letter, family="grille"),
                               evidence=["g-" + letter])
                   for n, letter, box_number in ((1, "a", 11), (2, "b", 53), (3, "c", 55))]
        multiplicity = [{"id": "mult-1", "members": [member(item) for item in records],
                         "representative": "N-1", "each": 2, "scope_text": "Room C",
                         "evidence_ids": ["n-typ"]}]
        payload = request(sources, evidence, records,
                          [admit(item, evidence=[item["evidence_ids"][0]]) for item in records],
                          scope([key("M1.3")]), multiplicities=multiplicity)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertIn("multiplicity_total_conflict", result["issues"])
        self.assertIsNone(result["total_each"])
        self.assertIsNone(row_for(result, "N-1")["physical_each"])

    def test_overlapping_multiplicity_groups_leave_the_quantity_unresolved(self):
        sources = [context("M1.3", "plan")]
        evidence = [graphic("g-a", "M1.3", 11), note("n-1", "M1.3", 12, "(4) RG-1 TOTAL"),
                    note("n-2", "M1.3", 56, "(6) RG-1 TOTAL"), note("w-plan", "M1.3", 13, "ok")]
        record = observation("O-1", "M1.3", 11, attributes=device("g-a", family="grille"),
                             evidence=["g-a"])
        multiplicities = [{"id": "mult-1", "members": [member(record)], "representative": "O-1",
                           "each": 4, "scope_text": "Room C", "evidence_ids": ["n-1"]},
                          {"id": "mult-2", "members": [member(record)], "representative": "O-1",
                           "each": 6, "scope_text": "Room C", "evidence_ids": ["n-2"]}]
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-a"])],
                          scope([key("M1.3")]), multiplicities=multiplicities)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertIn("multiplicity_overlap", result["issues"])
        self.assertIsNone(result["total_each"])
        self.assertIsNone(row_for(result, "O-1")["physical_each"])

    def test_a_relocation_without_current_evidence_stays_unresolved(self):
        sources = [context("M2.1", "plan")]
        evidence = [graphic("g-old", "M2.1", 32), graphic("g-new", "M2.1", 33),
                    note("w-plan", "M2.1", 34, "reviewed"),
                    note("r-note", "M2.1", 35, "relocation note", artifact="withdrawn")]
        old = observation("room-old", "M2.1", 32, attributes=device("g-old", status="demolition"),
                          evidence=["g-old"])
        new = observation("room-new", "M2.1", 33, attributes=device("g-new"), evidence=["g-new"])
        relocations = [{"id": "reloc-1", "members": [member(old), member(new)],
                        "remove_members": ["room-old"], "reinstall_members": ["room-new"],
                        "remove_operation_id": "op-r", "reinstall_operation_id": "op-i",
                        "reused": True, "evidence_ids": ["r-note"]}]
        payload = request(sources, evidence, [old, new],
                          [admit(old, evidence=["g-old"]), admit(new, evidence=["g-new"])],
                          scope([key("M2.1")], statuses=("relocated", "new_install")),
                          relocations=relocations)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertIn("relocation_unresolved", result["issues"])
        self.assertIsNone(result["total_each"])
        for identifier in ("room-old", "room-new"):
            row = row_for(result, identifier)
            self.assertIsNone(row["physical_each"])
            self.assertEqual(row["operations"], {"remove": None, "reinstall": None})
            self.assertIsNone(row["new_purchase_each"])

    def test_overlapping_relocation_records_conflict(self):
        sources = [context("M2.1", "plan")]
        evidence = [graphic("g-old", "M2.1", 32), graphic("g-new", "M2.1", 33),
                    graphic("g-alt", "M2.1", 57), note("w-plan", "M2.1", 34, "reviewed"),
                    note("r-1", "M2.1", 35, "relocate to room B"),
                    note("r-2", "M2.1", 58, "relocate to room C")]
        old = observation("room-old", "M2.1", 32, attributes=device("g-old", status="demolition"),
                          evidence=["g-old"])
        new = observation("room-new", "M2.1", 33, attributes=device("g-new"), evidence=["g-new"])
        alternate = observation("room-alt", "M2.1", 57, attributes=device("g-alt"),
                                evidence=["g-alt"])
        relocations = [{"id": "reloc-1", "members": [member(old), member(new)],
                        "remove_members": ["room-old"], "reinstall_members": ["room-new"],
                        "remove_operation_id": "op-r1", "reinstall_operation_id": "op-i1",
                        "reused": True, "evidence_ids": ["r-1"]},
                       {"id": "reloc-2", "members": [member(old), member(alternate)],
                        "remove_members": ["room-old"], "reinstall_members": ["room-alt"],
                        "remove_operation_id": "op-r2", "reinstall_operation_id": "op-i2",
                        "reused": True, "evidence_ids": ["r-2"]}]
        payload = request(sources, evidence, [old, new, alternate],
                          [admit(item, evidence=[item["evidence_ids"][0]])
                           for item in (old, new, alternate)],
                          scope([key("M2.1")], statuses=("relocated",)), relocations=relocations)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertIn("relocation_overlap", result["issues"])
        self.assertIsNone(result["total_each"])

    def test_a_multiplicity_of_relocation_operations_remains_an_exception(self):
        sources = [context("M2.1", "plan")]
        evidence = [graphic("g-old", "M2.1", 32), graphic("g-new", "M2.1", 33),
                    note("w-plan", "M2.1", 34, "reviewed"),
                    note("r-note", "M2.1", 35, "remove and reinstall"),
                    note("n-typ", "M2.1", 59, "(3) TYPICAL RELOCATED DEVICES")]
        old = observation("room-old", "M2.1", 32, attributes=device("g-old", status="demolition"),
                          evidence=["g-old"])
        new = observation("room-new", "M2.1", 33, attributes=device("g-new"), evidence=["g-new"])
        relocations = [{"id": "reloc-1", "members": [member(old), member(new)],
                        "remove_members": ["room-old"], "reinstall_members": ["room-new"],
                        "remove_operation_id": "op-r", "reinstall_operation_id": "op-i",
                        "reused": True, "evidence_ids": ["r-note"]}]
        multiplicity = [{"id": "mult-1", "members": [member(new)], "representative": "room-new",
                         "each": 3, "scope_text": "Level 2 rooms", "evidence_ids": ["n-typ"]}]
        payload = request(sources, evidence, [old, new],
                          [admit(old, evidence=["g-old"]), admit(new, evidence=["g-new"])],
                          scope([key("M2.1")], statuses=("relocated",)),
                          relocations=relocations, multiplicities=multiplicity)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertIn("combined_multiplicity_relocation_unsupported", result["issues"])
        self.assertIsNone(result["total_each"])
        # No operation count is invented for the unsupported combination.
        self.assertIsNone(row_for(result, "room-new")["physical_each"])
        self.assertEqual(row_for(result, "room-new")["operations"],
                         {"remove": None, "reinstall": None})

    def test_a_schedule_supplies_a_missing_attribute_but_never_an_instance(self):
        sources = [context("M7.2", "plan"), context("M10.1", "schedule")]
        evidence = [graphic("g-s", "M7.2", 60), note("w-plan", "M7.2", 61, "reviewed"),
                    note("s-row", "M10.1", 62, "SD-1 schedule row: served by AHU-1")]
        grouping = ("system", "family", "work_status")
        record = observation("T-1", "M7.2", 60, attributes=device("g-s", face=None, neck=None),
                             evidence=["g-s"])
        schedules = [{"id": "sched-1", "members": [member(record)], "declared_each": None,
                      "attributes": {"system": known("AHU-1", ("s-row",))},
                      "evidence_ids": ["s-row"]}]
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-s"])],
                          scope([key("M7.2")], group_by=grouping), schedules=schedules)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        row = row_for(result, "T-1")
        self.assertEqual(row["attributes"]["system"]["value"], "AHU-1")
        self.assertEqual(row["physical_each"], 1)
        self.assertEqual(result["total_each"], 1)
        self.assertTrue(result["complete"])
        self.assertEqual(result["declarations"][0]["state"], "attributes_only")
        self.assertEqual(result["declarations"][0]["issues"], [])

    def test_stale_schedule_evidence_cannot_fill_attributes(self):
        sources = [context("M7.2", "plan"), context("M10.1", "schedule")]
        evidence = [graphic("g-s", "M7.2", 60), note("w-plan", "M7.2", 61, "reviewed"),
                    note("s-row", "M10.1", 62, "superseded schedule row", artifact="old")]
        grouping = ("system", "family", "work_status")
        record = observation("T-1", "M7.2", 60, attributes=device("g-s", face=None, neck=None),
                             evidence=["g-s"])
        schedules = [{"id": "sched-1", "members": [member(record)], "declared_each": None,
                      "attributes": {"system": known("AHU-1", ("s-row",))},
                      "evidence_ids": ["s-row"]}]
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-s"])],
                          scope([key("M7.2")], group_by=grouping), schedules=schedules)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        row = row_for(result, "T-1")
        self.assertEqual(row["attributes"]["system"]["state"], "not_supplied")
        self.assertEqual(row["physical_each"], 1)
        self.assertIsNone(result["total_each"])
        self.assertIn("schedule_support_unresolved", result["issues"])

    def test_a_schedule_text_must_come_from_a_schedule_source(self):
        sources = [context("M7.2", "plan")]
        evidence = [graphic("g-s", "M7.2", 60), note("w-plan", "M7.2", 61, "reviewed"),
                    note("s-row", "M7.2", 63, "SD-1 row copied onto the plan")]
        record = observation("T-1", "M7.2", 60, attributes=device("g-s"), evidence=["g-s"])
        schedules = [{"id": "sched-1", "members": [member(record)], "declared_each": 1,
                      "attributes": {}, "evidence_ids": ["s-row"]}]
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-s"])],
                          scope([key("M7.2")]), schedules=schedules)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["declarations"][0]["state"], "unresolved")
        self.assertIn("schedule_support_unresolved", result["issues"])
        self.assertIsNone(result["total_each"])

    def test_contradictory_schedule_links_leave_the_attribute_unresolved(self):
        sources = [context("M7.2", "plan"), context("M10.1", "schedule")]
        evidence = [graphic("g-s", "M7.2", 60), note("w-plan", "M7.2", 61, "reviewed"),
                    note("s-1", "M10.1", 62, "SD-1 served by AHU-1"),
                    note("s-2", "M10.1", 64, "SD-1 served by AHU-2")]
        grouping = ("system", "family", "work_status")
        record = observation("T-1", "M7.2", 60, attributes=device("g-s", face=None, neck=None),
                             evidence=["g-s"])
        schedules = [{"id": "sched-1", "members": [member(record)], "declared_each": None,
                      "attributes": {"system": known("AHU-1", ("s-1",))},
                      "evidence_ids": ["s-1"]},
                     {"id": "sched-2", "members": [member(record)], "declared_each": None,
                      "attributes": {"system": known("AHU-2", ("s-2",))},
                      "evidence_ids": ["s-2"]}]
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-s"])],
                          scope([key("M7.2")], group_by=grouping), schedules=schedules)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        row = row_for(result, "T-1")
        self.assertIn("schedule_contradictory_links", row["issues"])
        self.assertEqual(row["attributes"]["system"]["state"], "unknown")
        self.assertEqual(row["physical_each"], 1)
        self.assertIsNone(result["total_each"])
        self.assertEqual(sorted(row["conflicts"][0]["schedule_ids"]), ["sched-1", "sched-2"])

    def test_a_schedule_value_that_differs_from_the_plan_stays_a_conflict(self):
        sources = [context("M7.2", "plan"), context("M10.1", "schedule")]
        evidence = [graphic("g-s", "M7.2", 60), note("w-plan", "M7.2", 61, "reviewed"),
                    note("s-1", "M10.1", 62, "SD-1 face 22x22")]
        record = observation("T-1", "M7.2", 60, attributes=device("g-s"), evidence=["g-s"])
        schedules = [{"id": "sched-1", "members": [member(record)], "declared_each": None,
                      "attributes": {"face_size": known(
                          size("rectangular", ["22", "22"], "in", "22x22"), ("s-1",))},
                      "evidence_ids": ["s-1"]}]
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-s"])],
                          scope([key("M7.2")]), schedules=schedules)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        row = row_for(result, "T-1")
        self.assertIn("schedule_attribute_conflict:face_size", row["issues"])
        self.assertEqual(row["physical_each"], 1)  # the supported subtotal survives
        self.assertEqual(result["known_subtotal_each"], 1)
        self.assertIsNone(result["total_each"])

    def test_a_partially_named_schedule_group_cannot_reconcile(self):
        sources = [context("M1.5", "plan"), context("M10.1", "schedule")]
        evidence = [graphic("g-1", "M1.5", 18), graphic("g-2", "M1.5", 19),
                    note("w-plan", "M1.5", 21, "reviewed"),
                    note("c-same", "M1.5", 65, "one terminal read twice"),
                    note("s-row", "M10.1", 22, "SD-4 quantity 1")]
        first = observation("E-1", "M1.5", 18, attributes=device("g-1", tag="SD-4"),
                            evidence=["g-1"])
        second = observation("E-2", "M1.5", 19, attributes=device("g-2", tag="SD-4"),
                             evidence=["g-2"])
        correspondence = [{"id": "corr-1", "members": [member(first), member(second)],
                           "state": "same", "evidence_ids": ["c-same"]}]
        schedules = [{"id": "sched-1", "members": [member(first)], "declared_each": 1,
                      "attributes": {}, "evidence_ids": ["s-row"]}]
        payload = request(sources, evidence, [first, second],
                          [admit(first, evidence=["g-1"]), admit(second, evidence=["g-2"])],
                          scope([key("M1.5")]), correspondences=correspondence,
                          schedules=schedules)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["declarations"][0]["state"], "unresolved")
        self.assertIn("schedule_group_partial", result["declarations"][0]["issues"])
        self.assertEqual(result["known_subtotal_each"], 1)
        self.assertIsNone(result["total_each"])

    def test_exact_unit_equivalence_groups_metric_and_imperial_sizes(self):
        sources = [context("M8.1", "plan")]
        evidence = [graphic("g-in", "M8.1", 66), graphic("g-mm", "M8.1", 67),
                    note("w-plan", "M8.1", 68, "reviewed")]
        imperial = observation("U-in", "M8.1", 66, attributes=device("g-in"), evidence=["g-in"])
        metric = observation("U-mm", "M8.1", 67,
                             attributes=device("g-mm", face=("609.6", "609.6"), neck=("203.2",),
                                               unit="mm"),
                             evidence=["g-mm"])
        payload = request(sources, evidence, [imperial, metric],
                          [admit(imperial, evidence=["g-in"]), admit(metric, evidence=["g-mm"])],
                          scope([key("M8.1")]))
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["total_each"], 2)
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["known_subtotal_each"], 2)
        metric_row = row_for(result, "U-mm")
        self.assertEqual(metric_row["attributes"]["face_size"]["value"]["dimensions"],
                         ["24.000000", "24.000000"])
        self.assertEqual(metric_row["attributes"]["face_size"]["value"]["original_text"],
                         "face ('609.6', '609.6')")

    def test_an_unrequested_system_does_not_invalidate_a_resolved_count(self):
        sources = [context("M8.2", "plan")]
        evidence = [graphic("g-a", "M8.2", 69), note("w-plan", "M8.2", 70, "reviewed")]
        record = observation("V-1", "M8.2", 69, attributes=device("g-a"), evidence=["g-a"])
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-a"])],
                          scope([key("M8.2")]))
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["total_each"], 1)
        self.assertTrue(result["complete"])
        self.assertEqual(row_for(result, "V-1")["attributes"]["system"]["state"], "not_supplied")

    def test_an_unknown_work_status_keeps_the_requested_total_incomplete(self):
        sources = [context("M8.3", "plan")]
        evidence = [graphic("g-a", "M8.3", 71), note("w-plan", "M8.3", 72, "reviewed")]
        record = observation("W-1", "M8.3", 71, attributes=device("g-a", status=None),
                             evidence=["g-a"])
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-a"])],
                          scope([key("M8.3")], group_by=("family",)))
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        row = row_for(result, "W-1")
        self.assertEqual(row["physical_each"], 1)
        self.assertIsNone(row["requested"])
        self.assertIn("work_status_unresolved", row["issues"])
        self.assertEqual(result["known_subtotal_each"], 0)
        self.assertEqual(result["physical_known_each"], 1)
        self.assertIsNone(result["total_each"])

    def test_contradictory_same_and_distinct_relations_stay_unresolved(self):
        sources = [context("M9.1", "plan")]
        evidence = [graphic("g-a", "M9.1", 73), graphic("g-b", "M9.1", 74),
                    note("c-same", "M9.1", 75, "same terminal"),
                    note("c-distinct", "M9.1", 76, "two terminals"),
                    note("w-plan", "M9.1", 77, "reviewed")]
        first = observation("X-1", "M9.1", 73, attributes=device("g-a"), evidence=["g-a"])
        second = observation("X-2", "M9.1", 74, attributes=device("g-b"), evidence=["g-b"])
        correspondences = [{"id": "corr-same", "members": [member(first), member(second)],
                            "state": "same", "evidence_ids": ["c-same"]},
                           {"id": "corr-distinct", "members": [member(first), member(second)],
                            "state": "distinct", "evidence_ids": ["c-distinct"]}]
        payload = request(sources, evidence, [first, second],
                          [admit(first, evidence=["g-a"]), admit(second, evidence=["g-b"])],
                          scope([key("M9.1")]), correspondences=correspondences)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertIn("correspondence_contradictory", result["issues"])
        self.assertEqual(result["known_subtotal_each"], 0)
        self.assertIsNone(result["total_each"])

    def test_a_distinct_relation_alone_never_merges_two_devices(self):
        sources = [context("M9.2", "plan")]
        evidence = [graphic("g-a", "M9.2", 78), graphic("g-b", "M9.2", 79),
                    note("c-distinct", "M9.2", 76, "two separate terminals"),
                    note("w-plan", "M9.2", 77, "reviewed")]
        first = observation("Y-1", "M9.2", 78, attributes=device("g-a"), evidence=["g-a"])
        second = observation("Y-2", "M9.2", 79, attributes=device("g-b"), evidence=["g-b"])
        correspondences = [{"id": "corr-distinct", "members": [member(first), member(second)],
                            "state": "distinct", "evidence_ids": ["c-distinct"]}]
        payload = request(sources, evidence, [first, second],
                          [admit(first, evidence=["g-a"]), admit(second, evidence=["g-b"])],
                          scope([key("M9.2")]), correspondences=correspondences)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(result["total_each"], 2)
        self.assertEqual(len(result["rows"]), 2)

    def test_conflicting_merged_attributes_preserve_the_physical_quantity(self):
        sources = [context("M1.2", "plan"), context("M4.1", "enlarged_plan")]
        evidence = [graphic("g-plan", "M1.2", 6), graphic("g-detail", "M4.1", 7),
                    note("w-plan", "M1.2", 8, "reviewed"), note("w-detail", "M4.1", 9, "reviewed"),
                    note("c-note", "M4.1", 10, "same terminal")]
        plan = observation("Z-plan", "M1.2", 6, attributes=device("g-plan"), evidence=["g-plan"])
        detail = observation("Z-detail", "M4.1", 7,
                             attributes=device("g-detail", face=("22", "22")), evidence=["g-detail"])
        correspondence = [{"id": "corr-1", "members": [member(plan), member(detail)],
                           "state": "same", "evidence_ids": ["c-note"]}]
        payload = request(sources, evidence, [plan, detail],
                          [admit(plan, evidence=["g-plan"]), admit(detail, evidence=["g-detail"])],
                          scope([key("M1.2"), key("M4.1")]), correspondences=correspondence)
        result = kernel.calculate(reviewed(payload, ["w-plan", "w-detail"]))
        row = result["rows"][0]
        self.assertEqual(row["physical_each"], 1)
        self.assertEqual(result["known_subtotal_each"], 1)
        self.assertIn("attribute_conflict:face_size", row["issues"])
        self.assertEqual(row["conflicts"][0]["observation_ids"], ["Z-detail", "Z-plan"])
        self.assertEqual(row["attributes"]["face_size"]["state"], "unknown")
        self.assertIsNone(result["total_each"])

    def test_an_observation_outside_the_selected_scope_does_not_block(self):
        sources = [context("M4.2", "plan"), context("M4.3", "plan")]
        evidence = [graphic("g-1", "M4.2", 37), graphic("g-out", "M4.3", 40),
                    note("w-1", "M4.2", 41, "reviewed")]
        inside = observation("G-1", "M4.2", 37, attributes=device("g-1"), evidence=["g-1"])
        outside = observation("G-out", "M4.3", 40, attributes=device("g-out"), evidence=["g-out"])
        payload = request(sources, evidence, [inside, outside],
                          [admit(inside, evidence=["g-1"]), admit(outside, evidence=["g-out"])],
                          scope([key("M4.2")]))
        result = kernel.calculate(reviewed(payload, ["w-1"]))
        self.assertEqual(result["total_each"], 1)
        self.assertTrue(result["complete"])
        row = row_for(result, "G-out")
        self.assertIsNone(row["physical_each"])
        self.assertEqual(row["issues"], ["outside_selected_scope"])
        self.assertIs(row["requested"], False)

    def test_a_stale_admission_hash_is_not_approval_of_new_bytes(self):
        sources = [context("M4.2", "plan")]
        evidence = [graphic("g-1", "M4.2", 37), note("w-1", "M4.2", 41, "reviewed")]
        record = observation("G-1", "M4.2", 37, attributes=device("g-1"), evidence=["g-1"])
        decision = admit(record, evidence=["g-1"])
        decision["observation_sha256"] = digest("an earlier observation")
        payload = request(sources, evidence, [record], [decision], scope([key("M4.2")]))
        result = kernel.calculate(reviewed(payload, ["w-1"]))
        self.assertIn("admission_stale", result["issues"])
        self.assertIsNone(row_for(result, "G-1")["physical_each"])
        self.assertIsNone(result["total_each"])

    def test_a_missing_admission_prevents_a_known_contribution(self):
        sources = [context("M4.2", "plan")]
        evidence = [graphic("g-1", "M4.2", 37), note("w-1", "M4.2", 41, "reviewed")]
        record = observation("G-1", "M4.2", 37, attributes=device("g-1"), evidence=["g-1"])
        payload = request(sources, evidence, [record], [], scope([key("M4.2")]))
        result = kernel.calculate(reviewed(payload, ["w-1"]))
        self.assertIn("admission_missing", result["issues"])
        self.assertIsNone(result["total_each"])
        self.assertEqual(result["known_subtotal_each"], 0)

    def test_an_exclusion_requires_current_evidence(self):
        sources = [context("M4.2", "plan")]
        evidence = [graphic("g-1", "M4.2", 37), note("w-1", "M4.2", 41, "reviewed"),
                    note("stale", "M4.2", 80, "old exclusion note", artifact="old")]
        record = observation("G-1", "M4.2", 37, attributes=device("g-1"), evidence=["g-1"])
        decision = admit(record, action="exclude", reason="not an air device",
                         evidence=["stale"])
        payload = request(sources, evidence, [record], [decision], scope([key("M4.2")]))
        result = kernel.calculate(reviewed(payload, ["w-1"]))
        self.assertIn("admission_evidence_stale", result["issues"])
        self.assertIsNone(row_for(result, "G-1")["physical_each"])

    def test_an_unknown_family_is_unresolved_rather_than_excluded(self):
        sources = [context("M4.2", "plan")]
        evidence = [graphic("g-1", "M4.2", 37), note("w-1", "M4.2", 41, "reviewed")]
        attributes = device("g-1")
        attributes["family"] = entry("unknown")
        record = observation("G-1", "M4.2", 37, attributes=attributes, evidence=["g-1"])
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-1"])],
                          scope([key("M4.2")]))
        result = kernel.calculate(reviewed(payload, ["w-1"]))
        row = row_for(result, "G-1")
        self.assertIsNone(row["physical_each"])  # unknown, never a zero-device claim
        self.assertIn("family_unresolved", row["issues"])
        self.assertIsNone(result["total_each"])

    def test_reordered_set_like_inputs_do_not_change_any_identity(self):
        payload = room_a()
        shuffled = copy.deepcopy(payload)
        for name in ("sources", "evidence", "observations", "decisions"):
            shuffled[name].reverse()
        for record in shuffled["observations"]:
            record["evidence_ids"].reverse()
            for field in record["attributes"].values():
                field["evidence_ids"].reverse()
        shuffled["scope"]["source_keys"].reverse()
        shuffled["scope"]["required_fields"].reverse()
        shuffled["scope"]["work_statuses"].reverse()
        shuffled["coverage"]["evidence_ids"].reverse()
        self.assertEqual(kernel.calculate(payload), kernel.calculate(shuffled))
        self.assertEqual(kernel.coverage_basis(payload), kernel.coverage_basis(shuffled))

    def test_a_row_identity_comes_only_from_its_accounting_members(self):
        changed = copy.deepcopy(room_a())
        target = [item for item in changed["observations"] if item["id"] == "A-SD1-1"][0]
        target["attributes"]["type_tag"] = known("SD-9", ("g-1",))
        for decision in changed["decisions"]:
            if decision["observation_id"] == "A-SD1-1":
                decision["observation_sha256"] = pin(target)
        before = kernel.calculate(room_a())
        after = kernel.calculate(reviewed(changed, ["w-plan"]))
        self.assertEqual(row_for(before, "A-SD1-1")["row_id"], row_for(after, "A-SD1-1")["row_id"])
        self.assertNotEqual(row_for(before, "A-SD1-1")["dependency_sha256"],
                            row_for(after, "A-SD1-1")["dependency_sha256"])
        self.assertEqual(row_for(before, "A-SD1-2")["dependency_sha256"],
                         row_for(after, "A-SD1-2")["dependency_sha256"])
        self.assertEqual(after["total_each"], 3)

    def test_the_caller_request_is_never_mutated(self):
        payload = room_a()
        preserved = copy.deepcopy(payload)
        kernel.calculate(payload)
        kernel.coverage_basis(payload)
        self.assertEqual(payload, preserved)

    def test_the_returned_result_is_independent_of_the_next_call(self):
        payload = room_a()
        first = kernel.calculate(payload)
        first["rows"][0]["physical_each"] = 99
        first["binding"]["rules"].clear()
        second = kernel.calculate(payload)
        self.assertEqual(second["total_each"], 3)
        self.assertEqual(len(second["binding"]["rules"]), 12)

    def test_corrupt_policy_authority_fails_closed(self):
        original = kernel.rules.load_binding

        def broken(root=None):
            raise kernel.rules.AirDeviceRuleError("rule_changed", "policy bytes do not match")

        kernel.rules.load_binding = broken
        try:
            with self.assertRaises(kernel.AirDeviceCalculationError) as error:
                kernel.calculate(room_a())
            self.assertEqual(error.exception.code, "binding_invalid")
        finally:
            kernel.rules.load_binding = original
        self.assertEqual(kernel.calculate(room_a())["total_each"], 3)

    def test_a_request_binding_must_equal_the_verified_policy(self):
        payload = room_a()
        payload["binding"] = copy.deepcopy(BINDING)
        payload["binding"]["rules"] = ["A01"]
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "binding_invalid")

    def test_the_coverage_basis_excludes_the_coverage_decision_itself(self):
        payload = room_a()
        without = dict((name, value) for name, value in payload.items() if name != "coverage")
        self.assertEqual(kernel.coverage_basis(without), payload["coverage"]["basis_sha256"])
        other = copy.deepcopy(payload)
        other["coverage"] = {"state": "unknown", "basis_sha256": None, "evidence_ids": [],
                             "unresolved_requirements": ["scope review outstanding"]}
        self.assertEqual(kernel.coverage_basis(payload), kernel.coverage_basis(other))

    def test_an_unresolved_requirement_prevents_a_complete_review(self):
        payload = reviewed(room_a(), ["w-plan"], requirements=["mechanical room not read"])
        result = kernel.calculate(payload)
        self.assertIn("coverage_requirements_unresolved", result["issues"])
        self.assertFalse(result["complete"])
        self.assertIsNone(result["total_each"])
        self.assertEqual(result["known_subtotal_each"], 3)


# --------------------------------------------------------------------------
# Canonical form and bounded, fail-closed validation
# --------------------------------------------------------------------------

class FingerprintTests(unittest.TestCase):

    def test_key_order_and_whitespace_do_not_change_the_fingerprint(self):
        self.assertEqual(kernel.fingerprint({"a": 1, "b": [1, 2]}),
                         kernel.fingerprint({"b": [1, 2], "a": 1}))
        self.assertNotEqual(kernel.fingerprint([1, 2]), kernel.fingerprint([2, 1]))

    def test_unsupported_objects_are_refused_rather_than_coerced(self):
        for value in ({1, 2}, (1, 2), object(), b"bytes", 1 + 2j):
            with self.subTest(value=repr(value)):
                with self.assertRaises(kernel.AirDeviceCalculationError):
                    kernel.fingerprint({"value": value})

    def test_non_string_keys_and_non_finite_numbers_are_refused(self):
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.fingerprint({1: "one"})
        self.assertEqual(error.exception.code, "structure_invalid")
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(kernel.AirDeviceCalculationError) as error:
                kernel.fingerprint([value])
            self.assertEqual(error.exception.code, "value_invalid")

    def test_hostile_size_and_nesting_stay_bounded(self):
        deep = current = {}
        for _ in range(kernel.MAX_DEPTH + 4):
            current["next"] = {}
            current = current["next"]
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.fingerprint(deep)
        self.assertEqual(error.exception.code, "limit_exceeded")
        with self.assertRaises(kernel.AirDeviceCalculationError):
            kernel.fingerprint([0] * (kernel.MAX_COLLECTION + 1))
        with self.assertRaises(kernel.AirDeviceCalculationError):
            kernel.fingerprint("x" * (kernel.MAX_STRING_CHARS + 1))
        with self.assertRaises(kernel.AirDeviceCalculationError):
            kernel.fingerprint(2 ** 63)

    def test_surrogates_are_refused(self):
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.fingerprint(["ok\ud800"])
        self.assertEqual(error.exception.code, "value_invalid")

    def test_the_implementation_identity_covers_the_three_accepted_modules(self):
        expected = kernel.fingerprint({
            "schema": "air-device-implementation-1",
            "files": dict((name, hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest())
                          for name in ("air_device_calculation.py", "air_device_attributes.py",
                                       "air_device_rules.py"))})
        self.assertEqual(kernel.implementation_identity(), expected)


class LoadedImplementationTests(unittest.TestCase):
    def test_stale_bytecode_cannot_impersonate_current_helper_bytes(self):
        import os
        import py_compile
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            for name in ("air_device_calculation.py", "air_device_attributes.py", "air_device_rules.py"):
                shutil.copyfile(ROOT / "scripts" / name, target / name)
            helper = target / "air_device_attributes.py"
            original = helper.read_bytes()
            helper.write_bytes(original + b'\nSTALE_PROBE = "old"\n')
            metadata = helper.stat()
            py_compile.compile(str(helper), doraise=True)
            helper.write_bytes(original + b'\nSTALE_PROBE = "new"\n')
            os.utime(helper, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            spec = importlib.util.spec_from_file_location("kernel_stale_probe", target / "air_device_calculation.py")
            loaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(loaded)
            self.assertEqual(loaded.attributes.STALE_PROBE, "new")
            loaded.implementation_identity()
            helper.write_bytes(helper.read_bytes() + b"\n# changed after load\n")
            with self.assertRaises(loaded.AirDeviceCalculationError):
                loaded.implementation_identity()


class ObservationValidationTests(unittest.TestCase):

    def valid(self):
        return observation("V-1", "M1.1", 1, attributes=device("g-1"), evidence=["g-1"])

    def test_a_validated_observation_is_an_independent_canonical_copy(self):
        record = self.valid()
        record["evidence_ids"] = ["g-1", "a-second"]
        record["attributes"]["opening_size"] = entry("unknown", None, [])
        copied = kernel.validate_observation(record)
        self.assertEqual(copied["evidence_ids"], ["a-second", "g-1"])
        self.assertEqual(kernel.fingerprint(copied),
                         kernel.fingerprint(kernel.validate_observation(copied)))
        copied["evidence_ids"].append("mutated")
        copied["attributes"]["family"]["value"] = "equipment"
        self.assertEqual(len(record["evidence_ids"]), 2)
        self.assertEqual(record["attributes"]["family"]["value"], "diffuser")

    def test_reordered_evidence_references_keep_one_observation_identity(self):
        record = self.valid()
        record["evidence_ids"] = ["g-1", "b-2", "a-3"]
        shuffled = copy.deepcopy(record)
        shuffled["evidence_ids"] = ["a-3", "g-1", "b-2"]
        self.assertEqual(pin(record), pin(shuffled))

    def test_bounding_boxes_reject_bools_and_empty_areas(self):
        for value in ([True, 0.0, 1.0, 1.0], [0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 1.5, 1.0],
                      [0.5, 0.5, 0.1, 0.9], [0.0, 0.0, 1.0], "0,0,1,1"):
            with self.subTest(value=repr(value)):
                record = self.valid()
                record["bbox"] = value
                with self.assertRaises(kernel.AirDeviceCalculationError):
                    kernel.validate_observation(record)

    def test_identifiers_reject_control_characters_and_blank_text(self):
        for value in ("with\nnewline", "   ", "", "x" * (kernel.MAX_ID_CHARS + 1), 7):
            with self.subTest(value=repr(value)):
                record = self.valid()
                record["id"] = value
                with self.assertRaises(kernel.AirDeviceCalculationError) as error:
                    kernel.validate_observation(record)
                self.assertEqual(error.exception.code, "identifier_invalid")

    def test_attribute_references_must_belong_to_the_observation(self):
        record = self.valid()
        record["attributes"]["family"] = known("diffuser", ("not-cited",))
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.validate_observation(record)
        self.assertEqual(error.exception.code, "attribute_invalid")

    def test_a_float_dimension_is_refused_by_the_accepted_helper(self):
        record = self.valid()
        record["attributes"]["face_size"] = known(
            {"shape": "rectangular", "dimensions": [24.0, 24.0], "unit": "in",
             "original_text": "24x24"}, ("g-1",))
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.validate_observation(record)
        self.assertEqual(error.exception.code, "attribute_invalid")

    def test_graphic_evidence_must_not_carry_text(self):
        payload = room_a()
        payload["evidence"][0]["text"] = "SD-1"
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "value_invalid")


class RequestStructureTests(unittest.TestCase):

    def test_missing_and_unknown_request_keys_are_refused(self):
        payload = room_a()
        for mutate in (lambda item: item.pop("observations"),
                       lambda item: item.update({"extra": 1})):
            with self.subTest(mutate=mutate):
                broken = copy.deepcopy(payload)
                mutate(broken)
                with self.assertRaises(kernel.AirDeviceCalculationError) as error:
                    kernel.calculate(broken)
                self.assertEqual(error.exception.code, "structure_invalid")

    def test_a_calculation_requires_an_explicit_coverage_decision(self):
        payload = dict((name, value) for name, value in room_a().items() if name != "coverage")
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "structure_invalid")
        self.assertEqual(len(kernel.coverage_basis(payload)), 64)

    def test_duplicate_identifiers_are_refused(self):
        payload = room_a()
        payload["observations"].append(copy.deepcopy(payload["observations"][0]))
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "identifier_invalid")

    def test_two_decisions_may_not_pin_one_observation(self):
        payload = room_a()
        payload["decisions"].append(copy.deepcopy(payload["decisions"][0]))
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "identifier_invalid")

    def test_references_must_exist(self):
        payload = room_a()
        payload["coverage"]["evidence_ids"] = ["absent"]
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "reference_invalid")

    def test_a_selected_source_must_hold_a_countable_role(self):
        payload = room_a()
        payload["scope"] = scope([key("M0.1")])
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "value_invalid")

    def test_required_fields_must_be_requested_group_fields(self):
        payload = room_a()
        payload["scope"] = scope([key("M1.1")], group_by=("family",), required=("system",))
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "reference_invalid")

    def test_quantities_refuse_bools_fractions_and_negative_values(self):
        sources = [context("M1.3", "plan")]
        evidence = [graphic("g-a", "M1.3", 11), note("n-typ", "M1.3", 12, "(4) RG-1 TOTAL"),
                    note("w-plan", "M1.3", 13, "reviewed")]
        record = observation("Q-1", "M1.3", 11, attributes=device("g-a", family="grille"),
                             evidence=["g-a"])
        for each in (True, 1.5, "4", 0, -1):
            with self.subTest(each=repr(each)):
                multiplicity = [{"id": "mult-1", "members": [member(record)],
                                 "representative": "Q-1", "each": each, "scope_text": "Room C",
                                 "evidence_ids": ["n-typ"]}]
                payload = request(sources, evidence, [record], [admit(record, evidence=["g-a"])],
                                  scope([key("M1.3")]), multiplicities=multiplicity)
                with self.assertRaises(kernel.AirDeviceCalculationError) as error:
                    kernel.calculate(reviewed(payload, ["w-plan"]))
                self.assertEqual(error.exception.code, "value_invalid")

    def test_a_declared_quantity_refuses_a_bool(self):
        sources = [context("M1.5", "plan"), context("M10.1", "schedule")]
        evidence = [graphic("g-1", "M1.5", 18), note("w-plan", "M1.5", 21, "reviewed"),
                    note("s-row", "M10.1", 22, "SD-4 row")]
        record = observation("E-1", "M1.5", 18, attributes=device("g-1"), evidence=["g-1"])
        schedules = [{"id": "sched-1", "members": [member(record)], "declared_each": True,
                      "attributes": {}, "evidence_ids": ["s-row"]}]
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-1"])],
                          scope([key("M1.5")]), schedules=schedules)
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(error.exception.code, "value_invalid")

    def test_relocation_structure_is_enforced(self):
        sources = [context("M2.1", "plan")]
        evidence = [graphic("g-old", "M2.1", 32), graphic("g-new", "M2.1", 33),
                    note("w-plan", "M2.1", 34, "reviewed"), note("r-note", "M2.1", 35, "relocate")]
        old = observation("room-old", "M2.1", 32, attributes=device("g-old", status="demolition"),
                          evidence=["g-old"])
        new = observation("room-new", "M2.1", 33, attributes=device("g-new"), evidence=["g-new"])
        base = {"id": "reloc-1", "members": [member(old), member(new)],
                "remove_members": ["room-old"], "reinstall_members": ["room-new"],
                "remove_operation_id": "op-r", "reinstall_operation_id": "op-i",
                "reused": True, "evidence_ids": ["r-note"]}
        broken = [
            dict(base, reused=False),
            dict(base, reinstall_operation_id="op-r"),
            dict(base, remove_members=["room-old", "room-new"]),
            dict(base, members=[member(old)]),
            dict(base, reinstall_members=["room-new", "absent"]),
        ]
        for index, record in enumerate(broken):
            with self.subTest(case=index):
                payload = request(sources, evidence, [old, new],
                                  [admit(old, evidence=["g-old"]), admit(new, evidence=["g-new"])],
                                  scope([key("M2.1")], statuses=("relocated",)),
                                  relocations=[record])
                with self.assertRaises(kernel.AirDeviceCalculationError):
                    kernel.calculate(reviewed(payload, ["w-plan"]))

    def test_relocation_sides_must_hold_distinct_locations(self):
        sources = [context("M2.1", "plan")]
        evidence = [graphic("g-old", "M2.1", 32), note("w-plan", "M2.1", 34, "reviewed"),
                    note("r-note", "M2.1", 35, "relocate")]
        old = observation("room-old", "M2.1", 32, attributes=device("g-old", status="demolition"),
                          evidence=["g-old"])
        new = observation("room-new", "M2.1", 32, attributes=device("g-old"), evidence=["g-old"])
        relocations = [{"id": "reloc-1", "members": [member(old), member(new)],
                        "remove_members": ["room-old"], "reinstall_members": ["room-new"],
                        "remove_operation_id": "op-r", "reinstall_operation_id": "op-i",
                        "reused": True, "evidence_ids": ["r-note"]}]
        payload = request(sources, evidence, [old, new],
                          [admit(old, evidence=["g-old"]), admit(new, evidence=["g-old"])],
                          scope([key("M2.1")], statuses=("relocated",)), relocations=relocations)
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertEqual(error.exception.code, "value_invalid")

    def test_a_multiplicity_needs_current_text_evidence(self):
        sources = [context("M1.3", "plan")]
        evidence = [graphic("g-a", "M1.3", 11), note("w-plan", "M1.3", 13, "reviewed")]
        record = observation("Q-1", "M1.3", 11, attributes=device("g-a", family="grille"),
                             evidence=["g-a"])
        multiplicity = [{"id": "mult-1", "members": [member(record)], "representative": "Q-1",
                         "each": 4, "scope_text": "Room C", "evidence_ids": ["g-a"]}]
        payload = request(sources, evidence, [record], [admit(record, evidence=["g-a"])],
                          scope([key("M1.3")]), multiplicities=multiplicity)
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertIn("multiplicity_unresolved", result["issues"])
        self.assertIsNone(result["total_each"])

    def test_a_relationship_member_must_exist(self):
        payload = room_a()
        payload["correspondences"] = [{"id": "corr-1",
                                       "members": [{"observation_id": "A-SD1-1",
                                                    "observation_sha256": digest("a")},
                                                   {"observation_id": "absent",
                                                    "observation_sha256": digest("b")}],
                                       "state": "same", "evidence_ids": ["w-plan"]}]
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "reference_invalid")

    def test_a_relationship_requires_evidence(self):
        payload = room_a()
        payload["correspondences"] = [{"id": "corr-1",
                                       "members": [member(payload["observations"][0]),
                                                   member(payload["observations"][1])],
                                       "state": "same", "evidence_ids": []}]
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "structure_invalid")

    def test_oversized_collections_are_refused(self):
        payload = room_a()
        payload["observations"][0]["issues"] = ["issue"] * (kernel.MAX_LIST + 1)
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "limit_exceeded")

    def test_source_contexts_must_be_unique(self):
        payload = room_a()
        payload["sources"].append(copy.deepcopy(payload["sources"][0]))
        with self.assertRaises(kernel.AirDeviceCalculationError) as error:
            kernel.calculate(payload)
        self.assertEqual(error.exception.code, "identifier_invalid")

    def test_result_and_row_key_sets_are_exact(self):
        result = kernel.calculate(room_a())
        self.assertEqual(set(result), set(kernel.RESULT_KEYS))
        self.assertEqual(result["schema"], kernel.RESULT_SCHEMA)
        self.assertEqual(result["version"], kernel.VERSION)
        for row in result["rows"]:
            self.assertEqual(set(row), set(kernel.ROW_KEYS))
        for group in result["groups"]:
            self.assertEqual(set(group), set(kernel.GROUP_KEYS))
        self.assertEqual(result["binding"], BINDING)
        self.assertEqual(result["scope"]["group_by"], list(DEFAULT_GROUP))


class SourceSupportRegressionTests(unittest.TestCase):
    def test_distinct_and_relocation_facts_cannot_both_establish_one_assembly(self):
        payload = room_a()
        first, second = payload["observations"][:2]
        payload["scope"]["work_statuses"].append("relocated")
        payload["correspondences"] = [{"id": "distinct-pair", "members": [member(first), member(second)],
                                        "state": "distinct", "evidence_ids": ["w-plan"]}]
        payload["relocations"] = [{"id": "reuse-pair", "members": [member(first), member(second)],
            "remove_members": [first["id"]], "reinstall_members": [second["id"]],
            "remove_operation_id": "remove", "reinstall_operation_id": "reinstall",
            "reused": True, "evidence_ids": ["w-plan"]}]
        result = kernel.calculate(reviewed(payload, ["w-plan"]))
        self.assertIsNone(result["total_each"])
        self.assertEqual(result["known_subtotal_each"], 1)
        self.assertIn("correspondence_contradictory", result["issues"])
        self.assertIsNone(row_for(result, first["id"])["physical_each"])
        self.assertEqual(row_for(result, first["id"])["operations"], {"remove": None, "reinstall": None})

    def test_peer_changes_invalidate_attached_relationship_rows_only(self):
        for relationship in ("correspondences", "schedules"):
            payload = room_a()
            first, second = payload["observations"][:2]
            common = {"id": "linked-pair", "members": [member(first), member(second)], "evidence_ids": ["w-plan"]}
            if relationship == "correspondences":
                payload[relationship] = [dict(common, state="distinct")]
            else:
                payload["sources"].append(context("S1", "schedule"))
                payload["evidence"].append(note("schedule-note", "S1", 32, "Two supported devices"))
                common["evidence_ids"] = ["schedule-note"]
                payload[relationship] = [dict(common, declared_each=2, attributes={})]
            before = kernel.calculate(reviewed(payload, ["w-plan"]))
            second["attributes"]["type_tag"]["value"] = "SD-2"
            payload["decisions"][1] = admit(second, evidence=["g-2"])
            after = kernel.calculate(reviewed(payload, ["w-plan"]))
            self.assertEqual(row_for(before, first["id"])["row_id"], row_for(after, first["id"])["row_id"])
            self.assertNotEqual(row_for(before, first["id"])["dependency_sha256"],
                                row_for(after, first["id"])["dependency_sha256"])
            self.assertEqual(row_for(before, "A-SD1-3")["dependency_sha256"],
                             row_for(after, "A-SD1-3")["dependency_sha256"])

    def stale_attribute(self, field, mixed=False):
        payload = room_a()
        record = payload["observations"][0]
        payload["evidence"].append(note("retired-label", "retired-page", 30, "Old source label"))
        record["evidence_ids"].append("retired-label")
        record["attributes"][field]["evidence_ids"] = (["g-1"] if mixed else []) + ["retired-label"]
        payload["decisions"][0] = admit(record, evidence=["g-1"])
        return reviewed(payload, ["w-plan"])

    def test_stale_required_size_cannot_supply_current_grouping(self):
        for mixed in (False, True):
            payload = self.stale_attribute("neck_size", mixed)
            before = copy.deepcopy(payload)
            result = kernel.calculate(payload)
            self.assertEqual(result["physical_known_each"], 3)
            self.assertEqual(result["known_subtotal_each"], 3)
            self.assertIsNone(result["total_each"])
            self.assertIn("required_field_unresolved:neck_size", result["issues"])
            self.assertEqual(row_for(result, "A-SD1-1")["attributes"]["neck_size"]["state"], "unknown")
            self.assertEqual(payload, before)

    def test_stale_family_or_status_cannot_establish_count_eligibility(self):
        for mixed in (False, True):
            for field, physical in (("family", 2), ("work_status", 3)):
                result = kernel.calculate(self.stale_attribute(field, mixed))
                self.assertEqual(result["physical_known_each"], physical)
                self.assertEqual(result["known_subtotal_each"], 2)
                self.assertIsNone(result["total_each"])

    def test_stale_unrequested_attribute_does_not_invent_a_requirement(self):
        result = kernel.calculate(self.stale_attribute("service"))
        self.assertEqual(result["total_each"], 3)
        self.assertEqual(row_for(result, "A-SD1-1")["attributes"]["service"]["state"], "unknown")

    def test_explicit_current_exclusion_resolves_semantically_unknown_item(self):
        for depiction in ("physical", "unknown", "tag_only"):
            payload = room_a()
            record = payload["observations"][0]
            record["depiction"] = depiction
            record["attributes"]["family"] = entry()
            payload["decisions"][0] = admit(record, action="exclude", reason="Reviewed false positive", evidence=["g-1"])
            result = kernel.calculate(reviewed(payload, ["w-plan"]))
            self.assertEqual(result["total_each"], 2)
            self.assertEqual(row_for(result, record["id"])["issues"], ["admission_excluded"])


if __name__ == "__main__":
    unittest.main()
