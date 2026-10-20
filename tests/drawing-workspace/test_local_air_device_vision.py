"""Public original image and bounded fake local transport; no model/socket calls."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import test_local_mechanical_vision as fixtures

air = fixtures.load("local_air_device_vision")


def known(value, refs=None):
    return {"state": "known", "value": value, "evidence_ids": ["label"] if refs is None else refs}


def attrs():
    value = {key: {"state": "not_supplied", "value": None, "evidence_ids": []}
             for key in air.attributes.FIELDS}
    value.update(family=known("diffuser"), type_tag=known("SD-1"),
                 work_status=known("new_install"), service=known("supply"),
                 neck_size=known({"shape": "round", "dimensions": ["8"],
                                  "unit": "in", "original_text": '8" neck'}))
    return value


def proposal():
    value = {key: [] for key in air.COLLECTIONS}
    value.update(unreadable=False, evidence=[
        {"id": "graphic", "bbox": [.1, .2, .2, .3], "text": None},
        {"id": "label", "bbox": [.1, .31, .4, .35], "text": 'SD-1 8" NEW SUPPLY'}],
        observations=[{"id": "device-1", "bbox": [.1, .2, .2, .3], "depiction": "physical",
                       "attributes": attrs(), "evidence_ids": ["graphic", "label"], "issues": []}])
    return value


def completion(value):
    return {"model": "fixture-vision:1", "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(value)}}


def second(value):
    item = copy.deepcopy(value["observations"][0])
    item.update(id="device-2", bbox=[.5, .2, .6, .3], evidence_ids=["graphic-2", "label"])
    value["evidence"].append({"id": "graphic-2", "bbox": item["bbox"], "text": None})
    value["observations"].append(item)
    return value


class AirDeviceVisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name) / "original-runtime"
        self.runtime.write_bytes(b"PUBLIC original runtime identity fixture")
        self.transport = fixtures.Transport()
        self.local = fixtures.equipment.LocalVision("fixture-vision:1", "d" * 64)
        self.adapter = air.AirDeviceVision(self.local, self.runtime, connection_factory=self.transport)
        self.transport.replies["/api/chat"] = completion(proposal())
        no_network = mock.patch.object(air.transport.http.client, "HTTPConnection",
                                      side_effect=AssertionError("No live network in fixtures"))
        no_network.start()
        self.addCleanup(no_network.stop)

    def read(self):
        return self.adapter.read(fixtures.PNG, "plan", self.adapter.identity())

    def reject(self, value):
        raw = json.dumps(completion(value)).encode()
        self.transport.replies["/api/chat"] = raw
        with self.assertRaises(air.VisionError) as error:
            self.read()
        self.assertEqual(error.exception.code, "model_output")
        self.assertEqual(error.exception.raw_response, raw)

    def test_retains_original_response_and_submits_only_original_image_and_contract(self):
        raw = json.dumps(completion(proposal()), indent=3).encode() + b"\n\n"
        self.transport.replies["/api/chat"] = raw
        result, response = self.read()
        self.assertEqual(response, raw)
        self.assertEqual(result, proposal())
        self.assertEqual(air.parse_response(raw, "fixture-vision:1", fixtures.PNG), result)
        call = next(x for x in self.transport.calls if x["path"] == "/api/chat")
        self.assertEqual(call["host"], "127.0.0.1")
        self.assertEqual(base64.b64decode(call["body"]["messages"][1]["images"][0]), fixtures.PNG)
        self.assertEqual(call["body"]["options"], {"temperature": 0.6, "seed": 0,
            "num_predict": 8192, "top_p": 0.8, "repeat_penalty": 1.1, "repeat_last_n": 256})
        self.assertEqual(call["body"]["format"], air.SCHEMA)
        self.assertEqual(call["body"]["messages"][0]["content"], air.PROMPT)
        self.assertNotIn("AC01", str(call["body"]))
        identity = self.adapter.identity()
        self.assertEqual(identity["kind"], "local_air_device_vision_v1")
        self.assertEqual(identity["prompt_sha256"], air.transport._sha(air.PROMPT.encode()))
        self.assertEqual(identity["schema_sha256"], air.transport._sha(air.transport._packed(air.SCHEMA)))

    def test_repeated_type_tags_and_duplicate_boxes_are_preserved_without_deduplication(self):
        value = second(proposal())
        value["observations"][1]["bbox"] = value["observations"][0]["bbox"][:]
        value["evidence"][2]["bbox"] = value["observations"][0]["bbox"][:]
        self.transport.replies["/api/chat"] = completion(value)
        result, _ = self.read()
        self.assertEqual([v["id"] for v in result["observations"]], ["device-1", "device-2"])
        self.assertEqual(result["correspondences"], [])

    def test_linear_length_and_slots_stay_attributes_and_no_total_is_allowed(self):
        value = proposal()
        value["observations"][0]["attributes"].update(
            family=known("linear_diffuser"), slot_count=known(2),
            assembly_length=known({"value": "6", "unit": "ft", "original_text": "6\u00a0ft assembly"}))
        result = air.validate_proposal(value)
        self.assertEqual(result["observations"][0]["attributes"]["slot_count"]["value"], 2)
        for key in ("total_each", "coverage", "scale", "admissions", "current_sources"):
            data = copy.deepcopy(value)
            data[key] = 1
            self.reject(data)
        for key in ("quantity", "source", "admitted", "scale_fact_id"):
            data = copy.deepcopy(value)
            data["observations"][0][key] = 1
            self.reject(data)

    def test_unreadable_and_readable_empty_are_distinct_without_zero_claims(self):
        value = {key: [] for key in air.COLLECTIONS}
        for unreadable in (True, False):
            value["unreadable"] = unreadable
            self.transport.replies["/api/chat"] = completion(value)
            self.assertEqual(self.read()[0], value)
        value = proposal()
        value["unreadable"] = True
        self.reject(value)

    def test_physical_graphic_evidence_is_distinct_from_tag_text(self):
        value = proposal()
        value["evidence"][0]["text"] = "SD-1"
        self.reject(value)
        value["observations"][0]["depiction"] = "tag_only"
        self.assertEqual(air.validate_proposal(value)["observations"][0]["depiction"], "tag_only")
        value = proposal()
        value["evidence"][0]["bbox"][2] += .01
        self.reject(value)

    def test_untagged_and_unknown_grouping_fields_remain_explicit(self):
        value = proposal()
        value["observations"][0]["attributes"]["type_tag"] = {
            "state": "not_supplied", "value": None, "evidence_ids": []}
        value["observations"][0]["attributes"]["system"] = {
            "state": "unknown", "value": None, "evidence_ids": []}
        result = air.validate_proposal(value)
        self.assertEqual(result, value)
        self.assertEqual(result["observations"][0]["attributes"]["service"]["value"], "supply")

    def test_explicit_multiplicity_is_a_note_proposal_covering_its_representative(self):
        value = second(proposal())
        value["evidence"].append({"id": "note", "bbox": [.2, .6, .8, .7],
                                  "text": "4 SD-1 TOTAL IN ROOM A"})
        value["multiplicities"] = [{"id": "group-note", "members": ["device-1", "device-2"],
                                    "representative": "device-1", "each": 4,
                                    "scope_text": "Room A", "evidence_ids": ["note"]}]
        self.assertEqual(air.validate_proposal(value)["multiplicities"][0]["each"], 4)
        for bad in (False, -1, 0, 2.5, "4", 1000001):
            data = copy.deepcopy(value)
            data["multiplicities"][0]["each"] = bad
            self.reject(data)
        data = copy.deepcopy(value)
        data["multiplicities"][0]["evidence_ids"] = ["graphic"]
        self.reject(data)
        data = copy.deepcopy(value)
        data["multiplicities"][0]["representative"] = "unrepresented"
        self.reject(data)

    def test_correspondence_is_explicit_unresolved_or_distinct_not_tag_derived(self):
        value = second(proposal())
        for state in ("same", "distinct", "unresolved"):
            value["correspondences"] = [{"id": "pair", "members": ["device-1", "device-2"],
                                         "state": state, "evidence_ids": ["label"]}]
            self.assertEqual(air.validate_proposal(value)["correspondences"][0]["state"], state)
        value["correspondences"][0]["members"] = ["device-1", "outside-image"]
        self.reject(value)

    def test_relocation_retains_distinct_member_sides_and_operation_identifiers(self):
        value = second(proposal())
        value["relocations"] = [{"id": "reuse", "members": ["device-1", "device-2"],
                                  "remove_members": ["device-1"], "reinstall_members": ["device-2"],
                                  "remove_operation_id": "remove", "reinstall_operation_id": "reinstall",
                                  "reused": True, "evidence_ids": ["label"]}]
        self.assertEqual(air.validate_proposal(value), value)
        for key, bad in (("reused", 1), ("reused", False), ("remove_members", ["device-2"]),
                         ("reinstall_operation_id", "remove"), ("evidence_ids", [])):
            data = copy.deepcopy(value)
            data["relocations"][0][key] = bad
            self.reject(data)
        data = copy.deepcopy(value)
        data["observations"][1]["bbox"] = data["observations"][0]["bbox"][:]
        data["evidence"][2]["bbox"] = data["observations"][0]["bbox"][:]
        self.reject(data)

    def test_schedule_declarations_never_create_or_link_physical_instances(self):
        value = {key: [] for key in air.COLLECTIONS}
        value.update(unreadable=False, evidence=[{"id": "schedule-row", "bbox": [.1, .1, .9, .2],
                                                    "text": "SD-1 4 EA 8 IN NECK"}])
        value["schedule_declarations"] = [{"id": "row-1", "bbox": [.1, .1, .9, .2],
                                           "declared_each": 4,
                                           "attributes": {"type_tag": known("SD-1", ["schedule-row"])},
                                           "evidence_ids": ["schedule-row"]}]
        self.transport.replies["/api/chat"] = completion(value)
        result, _ = self.read()
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["schedule_declarations"][0]["declared_each"], 4)
        data = copy.deepcopy(value)
        data["schedule_declarations"][0]["members"] = ["device-1"]
        self.reject(data)
        data = copy.deepcopy(value)
        data["schedule_declarations"][0]["attributes"]["system"] = known("AHU-1", ["missing"])
        self.reject(data)

    def test_invalid_coordinates_references_and_ids_fail_with_raw_evidence(self):
        mutations = [lambda v: v["observations"][0]["bbox"].__setitem__(0, True),
                     lambda v: v["evidence"][0]["bbox"].__setitem__(2, 1.1),
                     lambda v: v["observations"][0]["evidence_ids"].append("missing"),
                     lambda v: v["evidence"].append(copy.deepcopy(v["evidence"][0])),
                     lambda v: v["observations"].append(copy.deepcopy(v["observations"][0])),
                     lambda v: v["observations"][0]["attributes"]["family"].__setitem__("evidence_ids", []),
                     lambda v: v["observations"][0]["issues"].extend(["ambiguous", "ambiguous"])]
        for mutate in mutations:
            value = proposal()
            mutate(value)
            self.reject(value)

    def test_schema_limits_and_caller_immutability(self):
        value = proposal()
        before = copy.deepcopy(value)
        result = air.validate_proposal(value)
        result["observations"][0]["attributes"]["neck_size"]["value"]["dimensions"].append("12")
        self.assertEqual(value, before)
        for key, limit in (("observations", 250), ("evidence", 2000), ("multiplicities", 250)):
            data = proposal()
            data[key] = [None] * (limit + 1)
            self.reject(data)

    def test_incomplete_wrong_model_duplicate_json_or_tool_calls_never_validate(self):
        for key, value in (("done", False), ("done_reason", "length"), ("model", "wrong-model")):
            envelope = completion(proposal())
            envelope[key] = value
            self.transport.replies["/api/chat"] = json.dumps(envelope).encode()
            with self.assertRaises(air.VisionError):
                self.read()
        envelope = completion(proposal())
        envelope["message"]["tool_calls"] = []
        self.transport.replies["/api/chat"] = json.dumps(envelope).encode()
        with self.assertRaises(air.VisionError):
            self.read()
        envelope = completion(proposal())
        envelope["message"]["content"] = '{"unreadable":false,"unreadable":true}'
        with self.assertRaises(ValueError):
            air.parse_response(json.dumps(envelope).encode(), "fixture-vision:1", fixtures.PNG)

    def compact(self):
        return {"format": "air-device-image-2", "unreadable": False, "items": [{
            "bbox": [100, 200, 200, 300], "depiction": "physical", "family": "diffuser",
            "text": None, "claims": [
                {"field": "type_tag", "state": "known", "value": "SD-1", "text": "SD-1",
                 "bbox": [100, 310, 250, 350]},
                {"field": "work_status", "state": "known", "value": "new_install", "text": "NEW",
                 "bbox": [100, 350, 250, 390]}], "declared_each": None, "issues": []}]}

    def test_compact_inline_claims_expand_and_replay_without_inventing_missing_attributes(self):
        value = self.compact()
        self.transport.replies["/api/chat"] = completion(value)
        result, raw = self.read()
        self.assertEqual(air.parse_response(raw, "fixture-vision:1", fixtures.PNG), result)
        obs = result["observations"][0]
        self.assertEqual(obs["bbox"], [.1, .2, .2, .3])
        self.assertEqual(obs["attributes"]["type_tag"], known("SD-1", ["item-1-type_tag"]))
        self.assertEqual(obs["attributes"]["neck_size"], {"state": "unknown", "value": None, "evidence_ids": []})
        self.assertTrue(set(obs["attributes"]["type_tag"]["evidence_ids"]) <= set(obs["evidence_ids"]))
        self.assertEqual(result["evidence"][0], {"id": "item-1-graphic", "bbox": obs["bbox"], "text": None})
        self.assertTrue(all(not result[key] for key in air.COLLECTIONS[2:]))

    def test_compact_source_line_breaks_preserve_raw_and_normalize_only_layout(self):
        value = self.compact()
        value["items"][0]["claims"][0]["text"] = "TYPE\nSD-1\tNEW"
        self.transport.replies["/api/chat"] = completion(value)
        result, raw = self.read()
        self.assertEqual(json.loads(json.loads(raw)["message"]["content"]), value)
        self.assertEqual(result["evidence"][1]["text"], "TYPE SD-1 NEW")

    def test_compact_identical_boxes_stay_distinct_and_never_create_relationships(self):
        value = self.compact()
        value["items"].append(copy.deepcopy(value["items"][0]))
        result = air.expand_image_proposal(value)
        self.assertEqual([obs["id"] for obs in result["observations"]], ["item-1", "item-2"])
        self.assertEqual(result["correspondences"], [])

    def test_compact_source_units_and_slot_attributes_preserved(self):
        value = self.compact()
        item = value["items"][0]
        item["family"] = "linear_diffuser"
        length = {"value": "6", "unit": "ft", "original_text": "6 FT LONG"}
        size = {"shape": "round", "dimensions": ["200"], "unit": "mm", "original_text": "200 MM NECK"}
        for field, val, text in (("assembly_length", length, "6 FT LONG"),
                                  ("slot_count", 2, "2 SLOTS"), ("neck_size", size, "200 MM NECK")):
            item["claims"].append({"field": field, "state": "known", "value": val,
                                   "text": text, "bbox": [100, 400, 300, 450]})
        result = air.expand_image_proposal(value)
        self.assertEqual(result["observations"][0]["attributes"]["assembly_length"]["value"], length)
        self.assertEqual(result["observations"][0]["attributes"]["neck_size"]["value"], size)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["multiplicities"], [])

    def test_compact_notes_require_review_without_inferred_multiplicity(self):
        value = self.compact()
        value["items"].append({"bbox": [300, 400, 600, 500], "depiction": "note", "family": None,
            "text": "4 TOTAL THIS ROOM", "claims": [], "declared_each": None, "issues": []})
        result = air.expand_image_proposal(value)
        self.assertEqual(result["observations"][1]["depiction"], "unknown")
        self.assertEqual(result["observations"][1]["issues"], ["Source note needs review"])
        self.assertEqual(result["multiplicities"], [])

    def test_compact_schedule_quantity_is_an_unlinked_declaration_only(self):
        value = self.compact()
        value["items"][0].update(depiction="schedule", text="SD-1 8 EA", declared_each=8)
        result = air.expand_image_proposal(value)
        self.assertEqual(result["observations"][0]["depiction"], "schedule")
        self.assertEqual(result["schedule_declarations"][0]["declared_each"], 8)
        self.assertNotIn("members", result["schedule_declarations"][0])

    def test_compact_malformed_output_rejected_atomically_with_original_raw(self):
        mutations = [
            lambda v: v.update(format="unknown"),
            lambda v: v.update(total_each=1),
            lambda v: v.update(unreadable=True),
            lambda v: v["items"][0].update(bbox=[.1, .2, .3, .4]),
            lambda v: v["items"][0].update(bbox=[100, 200, 1001, 400]),
            lambda v: v["items"][0].update(bbox=[False, 200, 300, 400]),
            lambda v: v["items"][0].update(bbox=[300, 200, 100, 400]),
            lambda v: v["items"][0].update(declared_each=4),
            lambda v: v["items"][0]["claims"].append(copy.deepcopy(v["items"][0]["claims"][0])),
            lambda v: v["items"][0]["claims"][0].update(text=None),
            lambda v: v["items"][0]["claims"][0].update(text=None, bbox=None),
            lambda v: v["items"][0]["claims"][0].update(value=True),
            lambda v: v["items"][0]["claims"][0].update(text="A\x00B"),
            lambda v: v["items"][0]["claims"][0].update(field="family", value="grille"),
            lambda v: v["items"][0].update(depiction="note", text=None),
            lambda v: v.update(items=[None] * 251),
        ]
        for change in mutations:
            value = self.compact()
            change(value)
            self.reject(value)


    def test_cancellation_closes_the_existing_bounded_transport(self):
        identity = self.adapter.identity()
        self.transport.entered.clear()
        self.transport.block_path = "/api/chat"
        errors = []
        def run():
            try:
                self.adapter.read(fixtures.PNG, "plan", identity)
            except air.VisionError as error:
                errors.append(error.code)
        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(self.transport.entered.wait(2))
        self.adapter.cancel()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ["cancelled"])


if __name__ == "__main__":
    unittest.main()
