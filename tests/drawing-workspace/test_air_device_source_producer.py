"""Public synthetic air-device readings; fake transport, no model or listener."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import test_mechanical_model_inference as fixtures


ROOT = fixtures.fixtures.ROOT
PATH = ROOT / "scripts" / "air_device_source_producer.py"
producer_module = fixtures.fixtures.load("air_device_source_producer") if PATH.exists() else None


class Workspace(fixtures.Workspace):
    changed_geometry = False

    def state(self):
        state = super().state()
        for sheet in state["documents"][0]["sheets"]:
            sheet.update(width_micropoints=720000000, height_micropoints=720000000,
                unit="pt", rotation_degrees=0, parent_content_sha256=fixtures.REVISION,
                transform={"m11": 1, "m12": 0, "m21": 0, "m22": -1,
                    "tx_micropoints": 1000 if self.changed_geometry else 0,
                    "ty_micropoints": 720000000})
        return state

    def rendered_page(self, revision, index):
        stream, unused = self.verified_pdf(revision)
        stream.close()
        self.renders.append(index)
        return fixtures.png()  # Deliberately identical images on distinct sheets.


def attributes():
    value = {name: {"state": "not_supplied", "value": None, "evidence_ids": []}
        for name in ("family", "type_tag", "system", "service", "work_status",
                     "face_size", "neck_size", "opening_size", "assembly_length", "slot_count")}
    for name, text in (("family", "diffuser"), ("type_tag", "SD-1"), ("work_status", "new_install")):
        value[name] = {"state": "known", "value": text, "evidence_ids": ["note"]}
    return value


def proposal():
    return {"unreadable": False, "evidence": [
        {"id": "symbol", "bbox": [0.1, 0.2, 0.2, 0.3], "text": None},
        {"id": "note", "bbox": [0.1, 0.1, 0.6, 0.2], "text": "SD-1 new diffuser; four total in Room A"}],
        "observations": [{"id": "device-a", "bbox": [0.1, 0.2, 0.2, 0.3],
            "depiction": "physical", "attributes": attributes(), "evidence_ids": ["symbol", "note"], "issues": []},
            {"id": "device-b", "bbox": [0.1, 0.2, 0.2, 0.3],
            "depiction": "physical", "attributes": attributes(), "evidence_ids": ["symbol", "note"], "issues": []}],
        "correspondences": [{"id": "same-symbol", "members": ["device-a", "device-b"],
            "state": "same", "evidence_ids": ["symbol"]}],
        "multiplicities": [{"id": "room-a", "members": ["device-a"], "representative": "device-a",
            "each": 4, "scope_text": "Room A only", "evidence_ids": ["note"]}],
        "relocations": [], "schedule_declarations": [{"id": "schedule-a", "bbox": [0.3, 0.4, 0.8, 0.5],
            "declared_each": 4, "attributes": {"type_tag": attributes()["type_tag"]}, "evidence_ids": ["note"]}]}


class Adapter:
    def __init__(self):
        self.value = proposal()
        self.calls = []
        self.raw = None
        self.failure = False
        self.block = False
        self.hook = None
        self.entered, self.release = threading.Event(), threading.Event()
        self.observed = dict(fixtures.IDENTITY, kind="local_air_device_vision_v1", runtime="ollama",
            model="fixture-vision:1", temperature=0.6,
            prompt_sha256=hashlib.sha256(producer_module.vision.PROMPT.encode()).hexdigest(),
            schema_sha256=hashlib.sha256(json.dumps(producer_module.vision.SCHEMA,
                sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest())

    def identity(self):
        return copy.deepcopy(self.observed)

    def reset_cancel(self):
        self.release.clear()
        self.entered.clear()

    def cancel(self):
        self.release.set()

    def read(self, image, role, identity):
        self.calls.append((image, role, copy.deepcopy(identity)))
        self.entered.set()
        if self.block and not self.release.wait(5):
            raise AssertionError("Unreleased fixture transport")
        self.raw = json.dumps({"model": identity["model"], "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(self.value)}}, indent=2).encode() + b"\n"
        if self.failure:
            raise producer_module.vision.VisionError("model_output", "Original invalid fixture", self.raw)
        if self.hook:
            self.hook()
        return copy.deepcopy(self.value), self.raw


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(producer_module, "Durable air-device producer not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(Path(self.temp.name))
        self.inputs = fixtures.engine_module.InferenceEngine(self.workspace,
            fixtures.fixtures.baseline.BaselineStore(self.workspace.documents.knowledge_store))
        self.addCleanup(self.inputs.close)
        self.adapter = Adapter()
        self.producer = producer_module.AirDeviceSourceProducer(self.workspace, self.inputs, self.adapter)
        self.addCleanup(self.producer.close)

    def start(self, index=0):
        return self.producer.start({"revision_id": fixtures.REVISION, "index": index},
            "Fixture author", "Read original synthetic air devices")

    def finish(self, job, verify=True):
        self.producer.thread.join(3)
        self.assertFalse(self.producer.thread.is_alive())
        return self.producer.result(job["id"], verify=verify)

    def rehash_result(self, result):
        """Simulate self-consistent envelope tampering, not an ordinary caller edit."""
        path = self.producer.root / "results" / (result["id"] + ".json")
        path.write_bytes(producer_module.packed({"schema": 1, "data": result, "sha256": producer_module.digest(result)}))
        events = sorted((self.producer.root / "jobs" / result["id"]).glob("*.json"))
        event = json.loads(events[-1].read_bytes())["data"]
        event["result_sha256"] = producer_module.digest(result)
        events[-1].write_bytes(producer_module.packed({"schema": 1, "data": event, "sha256": producer_module.digest(event)}))

    def test_bound_duplicates_attributes_and_proposed_relationships_remain_separate(self):
        result = self.finish(self.start())
        self.assertEqual(result["state"], "completed")
        self.assertEqual(len(result["observations"]), 2)
        a, b = result["observations"]
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(a["schema"], "air-device-observation-1")
        self.assertEqual(a["source"], result["source"])
        self.assertEqual(a["bbox"], [0.1, 0.2, 0.2, 0.3])
        self.assertEqual(a["attributes"]["type_tag"]["value"], "SD-1")
        evidence = result["evidence"]
        self.assertEqual(result["current_sources"], [{"source": result["source"],
            "artifact_sha256": result["input"]["input_sha256"], "role": "plan"}])
        self.assertEqual({e["kind"] for e in evidence}, {"text", "graphic"})
        self.assertTrue(set(a["evidence_ids"]) <= {e["id"] for e in evidence})
        self.assertNotIn("note", a["attributes"]["family"]["evidence_ids"])
        relation = result["proposed_correspondences"][0]
        self.assertEqual(relation["members"], [{"observation_id": o["id"],
            "observation_sha256": producer_module.digest(o)} for o in (a, b)])
        self.assertEqual(result["proposed_multiplicities"][0]["representative"], a["id"])
        self.assertEqual(result["proposed_multiplicities"][0]["each"], 4)
        self.assertNotIn("members", result["schedule_declarations"][0])
        self.assertEqual(result["schedule_declarations"][0]["source"], result["source"])
        self.assertEqual(self.producer.artifact_bytes(result["response"]), self.adapter.raw)
        for key in ("coverage", "decisions", "admissions", "total_each", "scale_context"):
            self.assertNotIn(key, result)

    def test_jobs_and_identical_images_on_different_pages_never_share_observation_ids(self):
        results = [self.finish(self.start(index)) for index in (0, 0, 1)]
        self.assertEqual(len({r["input"]["input_sha256"] for r in results}), 1)
        self.assertEqual(len({r["observations"][0]["id"] for r in results}), 3)
        for result in results:
            self.assertEqual(self.producer.result(result["id"]), result)

    def test_scale_only_changes_do_not_change_saved_result(self):
        result = self.finish(self.start())
        self.workspace.workflow.data.update(calibrations=[{"id": "withdrawn"}], scale_decisions=[{"state": "rejected"}])
        self.assertEqual(self.producer.result(result["id"]), result)

    def test_reopen_replays_raw_without_model_calls(self):
        result = self.finish(self.start())
        self.producer.close()
        reopened = producer_module.AirDeviceSourceProducer(self.workspace, self.inputs)
        self.addCleanup(reopened.close)
        with mock.patch.object(self.workspace, "rendered_page", side_effect=AssertionError("Replay must not render")):
            self.assertEqual(reopened.result(result["id"]), result)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_rehashed_interpretation_and_raw_tamper_are_rejected(self):
        result = self.finish(self.start())
        result["observations"][0]["bbox"][0] = 0.05
        self.rehash_result(result)
        with self.assertRaises(producer_module.ProducerError):
            self.producer.result(result["id"])
        fresh = self.finish(self.start())
        (self.producer.root / fresh["response"]["key"]).write_bytes(b"tampered")
        with self.assertRaises(producer_module.ProducerError):
            self.producer.result(fresh["id"])

    def test_injected_authoritative_total_fails_and_retains_original_raw(self):
        self.adapter.value["total_each"] = 4
        result = self.finish(self.start())
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["observations"], [])
        self.assertEqual(self.producer.artifact_bytes(result["response"]), self.adapter.raw)

    def test_failed_and_cancelled_reads_publish_no_relationships(self):
        self.adapter.failure = True
        failed = self.finish(self.start())
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(self.producer.artifact_bytes(failed["response"]), self.adapter.raw)
        self.adapter.failure = False
        self.adapter.block = True
        job = self.start()
        self.assertTrue(self.adapter.entered.wait(2))
        self.producer.cancel(job["id"])
        cancelled = self.finish(job)
        self.assertEqual(cancelled["state"], "cancelled")
        for result in (failed, cancelled):
            for key in ("observations", "proposed_correspondences", "proposed_multiplicities",
                        "proposed_relocations", "schedule_declarations"):
                self.assertEqual(result[key], [])

    def test_original_missing_or_geometry_changed_invalidates_current_read(self):
        result = self.finish(self.start())
        self.workspace.available = False
        with self.assertRaises(producer_module.ProducerError):
            self.producer.result(result["id"])
        self.workspace.available = True
        self.workspace.changed_geometry = True
        with self.assertRaises(producer_module.ProducerError):
            self.producer.result(result["id"])

    def test_source_or_model_change_during_read_cannot_publish_success(self):
        self.adapter.hook = lambda: setattr(self.workspace, "changed_geometry", True)
        result = self.finish(self.start(), verify=False)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["code"], "source_changed")
        self.workspace.changed_geometry = False
        self.adapter.hook = lambda: self.adapter.observed.update(model_sha256="e" * 64)
        result = self.finish(self.start())
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["code"], "model_changed")

    def test_mismatched_adapter_identity_prevents_read(self):
        self.adapter.observed["kind"] = "local_mechanical_vision"
        result = self.finish(self.start())
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.adapter.calls, [])

    def test_result_write_failure_is_terminal_without_usable_result(self):
        original = self.producer._save
        def fail(path, data):
            if path.parent.name == "results":
                raise OSError("fixture result write failure")
            return original(path, data)
        with mock.patch.object(self.producer, "_save", side_effect=fail):
            job = self.start()
            self.producer.thread.join(3)
        self.assertFalse(self.producer.thread.is_alive())
        self.assertEqual(self.producer.view()["jobs"][0]["state"], "failed")
        with self.assertRaises(producer_module.ProducerError):
            self.producer.result(job["id"])

    def test_terminal_event_failure_is_visible_and_reopen_marks_interrupted(self):
        original = self.producer._event
        def fail(previous, state, *args, **kwargs):
            if state in producer_module.TERMINAL:
                raise OSError("fixture event publication failure")
            return original(previous, state, *args, **kwargs)
        with mock.patch.object(self.producer, "_event", side_effect=fail):
            job = self.start()
            self.producer.thread.join(3)
        self.assertEqual(self.producer.view()["jobs"][0]["state"], "failed")
        with self.assertRaises(producer_module.ProducerError):
            self.producer.result(job["id"])
        self.producer.close()
        reopened = producer_module.AirDeviceSourceProducer(self.workspace, self.inputs)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.view()["jobs"][0]["state"], "interrupted")
        with self.assertRaises(producer_module.ProducerError):
            reopened.result(job["id"])

    def test_relocation_members_and_operation_ids_are_reading_scoped_proposals(self):
        value = self.adapter.value
        value["observations"][1]["bbox"] = [0.6, 0.2, 0.7, 0.3]
        value["evidence"].append({"id": "new-symbol", "bbox": [0.6, 0.2, 0.7, 0.3], "text": None})
        value["observations"][1]["evidence_ids"] = ["new-symbol", "note"]
        value["correspondences"], value["multiplicities"] = [], []
        value["relocations"] = [{"id": "move-a", "members": ["device-a", "device-b"],
            "remove_members": ["device-a"], "reinstall_members": ["device-b"],
            "remove_operation_id": "remove", "reinstall_operation_id": "reinstall",
            "reused": True, "evidence_ids": ["note"]}]
        first, second = self.finish(self.start()), self.finish(self.start())
        relation = first["proposed_relocations"][0]
        self.assertEqual(relation["remove_members"], [first["observations"][0]["id"]])
        self.assertEqual(relation["reinstall_members"], [first["observations"][1]["id"]])
        self.assertEqual({m["observation_id"] for m in relation["members"]},
                         {o["id"] for o in first["observations"]})
        self.assertEqual(len({relation["remove_operation_id"], relation["reinstall_operation_id"],
            second["proposed_relocations"][0]["remove_operation_id"],
            second["proposed_relocations"][0]["reinstall_operation_id"]}), 4)
        self.assertNotIn("physical_each", relation)
        self.assertNotIn("operations", relation)

    def test_empty_readable_and_unreadable_responses_never_certify_zero(self):
        for unreadable in (False, True):
            with self.subTest(unreadable=unreadable):
                self.adapter.value = {key: [] for key in self.adapter.value if key != "unreadable"}
                self.adapter.value["unreadable"] = unreadable
                result = self.finish(self.start())
                self.assertEqual(result["state"], "completed")
                self.assertEqual(result["unreadable"], unreadable)
                self.assertEqual(result["observations"], [])
                self.assertEqual(len(result["current_sources"]), 1)
                self.assertNotIn("coverage", result)
                self.assertNotIn("total_each", result)

    def test_dependency_change_prevents_new_execution_and_saved_result_reinterpretation(self):
        result = self.finish(self.start())
        module = producer_module.vision.attributes
        altered = Path(self.temp.name) / Path(module.__file__).name
        altered.write_bytes(Path(module.__file__).read_bytes() + b"\n# Changed fixture dependency\n")
        with mock.patch.object(module, "__file__", str(altered)):
            with self.assertRaises(producer_module.ProducerError) as changed:
                self.start()
            self.assertEqual(changed.exception.code, "engine_changed")
            with self.assertRaises(producer_module.ProducerError):
                self.producer.result(result["id"])
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(self.producer.result(result["id"]), result)

    def test_raw_response_write_failure_cannot_publish_observations(self):
        with mock.patch.object(self.producer, "_retain_response", side_effect=OSError("raw write failure")):
            result = self.finish(self.start())
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["code"], "evidence_write_failed")
        self.assertIsNone(result["response"])
        self.assertTrue(all(not result[key] for key in producer_module.COLLECTIONS))

    def test_prepared_input_tamper_and_rehashed_proposed_relation_are_rejected(self):
        result = self.finish(self.start())
        result["proposed_multiplicities"][0]["each"] = 5
        self.rehash_result(result)
        with self.assertRaises(producer_module.ProducerError):
            self.producer.result(result["id"])
        second = self.finish(self.start())
        second["input"]["role"] = "schedule"
        second["role"] = "schedule"
        self.rehash_result(second)
        with self.assertRaises(producer_module.ProducerError):
            self.producer.result(second["id"])

    def test_running_job_rejects_concurrent_start_and_close_cancels_it(self):
        self.adapter.block = True
        job = self.start()
        self.assertTrue(self.adapter.entered.wait(2))
        with self.assertRaises(producer_module.ProducerError) as busy:
            self.start()
        self.assertEqual(busy.exception.code, "producer_busy")
        self.producer.close()
        self.assertFalse(self.producer.thread.is_alive())
        self.assertEqual(self.producer.result(job["id"])["state"], "cancelled")
        with self.assertRaises(producer_module.ProducerError):
            self.start()

    def test_result_and_event_identity_collisions_never_overwrite_retained_bytes(self):
        result = self.finish(self.start())
        retained = (self.producer.root / "results" / (result["id"] + ".json")).read_bytes()
        changed = copy.deepcopy(result)
        changed["reason"] = "Different content at the same immutable identity"
        with self.assertRaises(producer_module.ProducerError):
            self.producer._save(self.producer.root / "results" / (result["id"] + ".json"), changed)
        self.assertEqual((self.producer.root / "results" / (result["id"] + ".json")).read_bytes(), retained)

    def test_new_job_cannot_bind_cached_image_to_changed_geometry(self):
        self.finish(self.start())
        self.workspace.changed_geometry = True
        result = self.finish(self.start(), verify=False)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["code"], "input_geometry_changed")
        self.assertEqual(len(self.adapter.calls), 1)

    def test_unbound_shared_cached_image_needs_current_render_proof(self):
        self.inputs.prepare_input({"revision_id": fixtures.REVISION, "index": 0}, "Fixture", "Earlier image")
        with mock.patch.object(self.workspace, "rendered_page", return_value=fixtures.png(1)):
            result = self.finish(self.start(), verify=False)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"]["code"], "input_image_changed")
        self.assertEqual(self.adapter.calls, [])

    def test_geometry_proof_cannot_be_rehashed_or_deleted_for_replay(self):
        result = self.finish(self.start())
        path = self.producer.root / "input-bindings" / (result["input"]["id"] + ".json")
        original = path.read_bytes()
        changed = json.loads(original)["data"]
        changed["geometry"]["transform"]["tx_micropoints"] = 999
        path.write_bytes(producer_module.packed({"schema": 1, "data": changed,
            "sha256": producer_module.digest(changed)}))
        with self.assertRaises(producer_module.ProducerError):
            self.producer.result(result["id"])
        path.unlink()
        with self.assertRaises(producer_module.ProducerError):
            self.producer.result(result["id"])
        path.write_bytes(original)
        self.assertEqual(self.producer.result(result["id"]), result)

    def test_old_cancel_cannot_signal_a_new_job_at_turnover(self):
        # Release A while cancel(A) is paused at its cancellation signal. With
        # the required lock, B cannot start until A's signal transition ends.
        self.adapter.block = True
        first = self.start()
        self.assertTrue(self.adapter.entered.wait(2))
        entered, release = threading.Event(), threading.Event()
        original_set = self.producer.stopping.set
        def paused_set():
            entered.set()
            if not release.wait(3):
                raise AssertionError("fixture cancellation signal release missing")
            return original_set()
        errors, later = [], []
        def cancel():
            try:
                self.producer.cancel(first["id"])
            except Exception as error:
                errors.append(error)
        def start_next():
            try:
                self.producer.thread.join(2)
                later.append(self.start())
            except Exception as error:
                errors.append(error)
        with mock.patch.object(self.producer.stopping, "set", side_effect=paused_set):
            cancelling = threading.Thread(target=cancel)
            cancelling.start()
            self.assertTrue(entered.wait(2))
            self.adapter.release.set()
            starting = threading.Thread(target=start_next)
            starting.start()
            # A correct implementation holds the lock across this signal.
            acquired = self.producer.lock.acquire(timeout=0.1)
            if acquired:
                self.producer.lock.release()
            release.set()
            cancelling.join(3)
            starting.join(3)
        self.assertFalse(cancelling.is_alive())
        self.assertFalse(starting.is_alive())
        self.assertFalse(acquired, "Cancellation target and signal must be atomic with job turnover")
        self.assertEqual(errors, [])
        self.adapter.release.set()
        self.assertEqual(self.finish(later[0])["state"], "completed")


if __name__ == "__main__":
    unittest.main()
