"""PUBLIC original PNG observations; no runtime, socket, or model is invoked."""
import copy
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

import test_mechanical_model_baseline as fixtures


engine_module = fixtures.load("mechanical_model_inference")
IDENTITY = {"kind": "local_mechanical_vision", "model": "original-fixture:1",
            "model_sha256": "a" * 64, "runtime": "synthetic", "runtime_version": "1",
            "runtime_sha256": "b" * 64, "prompt_sha256": "c" * 64, "schema_sha256": "d" * 64,
            "image_max_dimension": 2000, "temperature": 0, "seed": 0}
PDF = b"%PDF-1.4\nOriginal PUBLIC local software fixture; no private project data.\n%%EOF"
REVISION = hashlib.sha256(PDF).hexdigest()


def png(index=0, width=12):
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, 12, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress((b"\0" + bytes([index, 128, 255]) * width) * 12)) + chunk(b"IEND", b""))


class Workspace:
    def __init__(self, root):
        self.root = root
        self.metadata = {"project": {"id": "project-a"}}
        self.documents = SimpleNamespace(knowledge_store=fixtures.knowledge.KnowledgeStore(root / "knowledge"))
        self.script = root / "original-renderer-fixture.py"
        self.script.write_text("# Original PUBLIC renderer identity fixture, never executed.\n", encoding="utf-8")
        self.renderer = [sys.executable, str(self.script)]
        self.workflow = SimpleNamespace(data={"pages": {}})
        self.renders = []
        self.verified = []
        self.available = True

    def state(self):
        return {"documents": [{"revision_id": REVISION, "sheets": [
            {"index": index, "sheet_id": hashlib.sha256(str(index).encode()).hexdigest()} for index in range(3)]}]}

    def verified_pdf(self, revision):
        if revision != REVISION or not self.available:
            raise engine_module.InferenceError("original_missing", "Original fixture PDF unavailable.")
        self.verified.append(revision)
        return io.BytesIO(PDF), len(PDF)

    def rendered_page(self, revision, index):
        stream, unused = self.verified_pdf(revision)
        stream.close()
        self.renders.append(index)
        return png(index)


class Adapter:
    def __init__(self):
        self.observed = copy.deepcopy(IDENTITY)
        self.calls, self.raws = [], []
        self.identities = 0
        self.fail = set()
        self.unreadable = set()
        self.entered, self.release = threading.Event(), threading.Event()
        self.block = False
        self.hook = None

    def identity(self):
        self.identities += 1
        return copy.deepcopy(self.observed)

    def read(self, image, role, identity):
        # This exact call signature contains no sample labels or expected boxes.
        self.calls.append((image, role, copy.deepcopy(identity)))
        self.entered.set()
        if self.block:
            if not self.release.wait(5):
                raise AssertionError("Fixture release did not arrive")
        raw = json.dumps({"fixture_response": len(self.calls), "model": identity["model"],
                          "unreadable": image in self.unreadable}, sort_keys=True).encode()
        self.raws.append(raw)
        if image in self.fail:
            error = engine_module.InferenceError("fixture_failure", "Original synthetic response failure.")
            error.raw_response = raw
            raise error
        if self.hook:
            self.hook()
        objects = [] if image in self.unreadable else [fixtures.obj("observed-1"), fixtures.obj("observed-2")]
        return {"objects": objects, "unreadable": image in self.unreadable}, raw

    def cancel(self):
        self.release.set()

    def reset_cancel(self):
        self.release.clear()


class InferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Workspace(Path(temporary.name))
        self.knowledge = self.workspace.documents.knowledge_store
        self.baseline = fixtures.baseline.BaselineStore(self.knowledge)
        self.adapter = Adapter()
        self.engine = engine_module.InferenceEngine(self.workspace, self.baseline, self.adapter)
        self.addCleanup(self.engine.close)

    def prepare(self, index=0):
        return self.engine.prepare_input({"revision_id": REVISION, "index": index}, "Fixture author", "Original local PNG input")

    def register(self, kind, payload):
        return self.baseline.register("project-a", kind, payload, "Independent fixture author", "Original PUBLIC synthetic baseline")

    def plan(self, count=1, split="test"):
        self.inputs = [self.prepare(index) for index in range(count)]
        metadata = {"source_class": "original_fixture", "title": "Original fixture model identity declaration",
                    "rights_basis": "Original PUBLIC synthetic software fixture.", "jurisdiction": "unspecified",
                    "edition": "1", "retrieved_at": "2026-09-11T00:00:00Z", "applicability": "Local software fixture only.",
                    "locator": "fixture/model", "snapshot_kind": "local_text_evidence", "project_id": "project-a"}
        self.model_source = self.knowledge.add_source(metadata, b"Original fixture model declaration")
        model = self.register("model", {"name": IDENTITY["model"], "version": "1", "source_ids": [self.model_source["id"]],
            "artifacts": [{"name": "original-fictional-model", "sha256": IDENTITY["model_sha256"], "bytes": 1}],
            "runtime": {"name": IDENTITY["runtime"], "version": IDENTITY["runtime_version"], "sha256": IDENTITY["runtime_sha256"]},
            "preprocessing": {"execution_identity": copy.deepcopy(IDENTITY)}})
        samples = [{"id": "page-%d" % index, "source_id": item["source_id"], "input_sha256": item["input_sha256"],
                    "group_id": "original-fixture-job", "split": "test", "objects": [fixtures.obj()]}
                   for index, item in enumerate(self.inputs)]
        data = self.register("dataset", {"name": "Original PNG dataset", "version": "1", "scope": list(fixtures.baseline.CATEGORIES),
            "annotation_basis": "Independent fixture object positions, never sent to the adapter.", "samples": samples})
        self.saved_plan = self.register("plan", {"dataset_id": data["id"], "model_id": model["id"], "split": split,
            "criteria": {"iou_threshold": .5, "minimum_precision": .9, "minimum_recall": .9,
                         "max_mean_latency_ms": 1000, "max_peak_memory_mb": 4096}})
        return self.saved_plan

    def start(self):
        return self.engine.start(self.saved_plan["id"], "Original operator", "Run original PUBLIC fixture")

    def finish(self, job):
        self.engine.thread.join(8)
        self.assertFalse(self.engine.thread.is_alive())
        return next(row for row in self.engine.view()["jobs"] if row["id"] == job["id"])

    def edit(self, kind, identifier, edit):
        path = self.engine._path(kind, identifier)
        envelope = json.loads(path.read_bytes())
        edit(envelope["data"])
        envelope["sha256"] = engine_module.digest(envelope["data"])
        path.write_bytes(engine_module.packed(envelope))

    def test_preparation_preserves_exact_png_original_lineage_and_renderer_script(self):
        record = self.prepare()
        self.assertEqual(self.knowledge.source_bytes(record["source_id"]), png())
        self.assertEqual(record["input_sha256"], hashlib.sha256(png()).hexdigest())
        source = self.knowledge.get_source(record["source_id"])
        self.assertEqual(source["metadata"]["snapshot_kind"], "rendered_page_png")
        self.assertEqual(source["metadata"]["locator"], "revision:%s/page:1" % REVISION)
        self.assertEqual(record["image"]["width"], 12)
        self.assertIn(str(self.workspace.script.resolve()), [item["path"] for item in record["renderer"]["artifacts"]])
        self.assertEqual(self.prepare(), record)
        self.assertEqual(self.workspace.renders, [0])
        self.engine.view()
        self.assertEqual(self.adapter.identities, 0)
        self.assertEqual(self.adapter.calls, [])

    def test_invalid_png_header_dimensions_checksums_and_truncation_are_rejected(self):
        for invalid in (b"not png", png(width=2001), png()[:-1], png()[:40] + b"wrong" + png()[45:]):
            with self.subTest(raw_size=len(invalid)):
                with mock.patch.object(self.workspace, "rendered_page", return_value=invalid):
                    with self.assertRaises(engine_module.InferenceError) as caught:
                        self.prepare()
                    self.assertEqual(caught.exception.code, "image_invalid")
        self.assertEqual(self.engine.view()["inputs"], [])

    def test_complete_run_uses_original_bytes_retains_duplicates_raw_and_unknown_memory(self):
        self.plan()
        job = self.finish(self.start())
        self.assertEqual(job["state"], "completed", job["error"])
        self.assertEqual(self.adapter.calls, [(png(), "plan", IDENTITY)])
        self.assertEqual(self.engine.artifact_bytes(job["samples"][0]["response"]), self.adapter.raws[0])
        self.assertEqual(len(job["samples"][0]["objects"]), 2)
        self.assertGreaterEqual(job["samples"][0]["latency_ms"], 0)
        self.assertIsNone(job["samples"][0]["peak_memory_mb"])
        run = self.baseline.view("project-a")["runs"][0]
        self.assertEqual(run["id"], job["baseline_run_id"])
        self.assertEqual(run["payload"]["report"]["metrics"]["false_positive"], 1)
        self.assertIsNone(run["payload"]["report"]["metrics"]["peak_memory_mb"])
        self.assertFalse(run["payload"]["report"]["thresholds_met"])
        self.assertEqual(run["payload"]["report"]["execution_proof"], "saved_outputs_only")
        self.assertEqual(self.start()["id"], job["id"])
        self.assertEqual(len(self.adapter.calls), 1)
        self.assertEqual(len(self.baseline.view("project-a")["runs"]), 1)

    def test_unreadable_page_is_empty_observations_and_counts_missed_truth(self):
        self.plan(2)
        self.adapter.unreadable.add(png(1))
        job = self.finish(self.start())
        self.assertEqual(job["state"], "completed", job["error"])
        self.assertEqual(job["samples"][1]["objects"], [])
        self.assertTrue(job["samples"][1]["unreadable"])
        self.assertEqual(self.baseline.view("project-a")["runs"][0]["payload"]["report"]["metrics"]["false_negative"], 1)

    def test_failed_response_is_retained_and_resume_reuses_completed_page(self):
        self.plan(2)
        self.adapter.fail.add(png(1))
        job = self.finish(self.start())
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["completed_samples"], 1)
        self.assertEqual(self.baseline.view("project-a")["runs"], [])
        self.assertEqual(self.engine.artifact_bytes(job["failed_response"]), self.adapter.raws[1])
        with self.assertRaises(engine_module.InferenceError) as caught:
            self.start()
        self.assertEqual(caught.exception.code, "resume_required")
        first_page = copy.deepcopy(job["samples"][0])
        self.adapter.fail.clear()
        self.engine.resume(job["id"], "Resume operator", "Explicitly resume original fixture")
        finished = self.finish(job)
        self.assertEqual(finished["state"], "completed", finished["error"])
        self.assertEqual(finished["samples"][0], first_page)
        self.assertEqual([call[0] for call in self.adapter.calls], [png(), png(1), png(1)])
        self.assertEqual(self.workspace.renders, [0, 1])

    def test_model_and_runtime_drift_are_rejected_before_any_image_is_sent(self):
        self.plan()
        for field, value in (("model_sha256", "e" * 64), ("runtime_version", "2"), ("runtime_sha256", "f" * 64)):
            self.adapter.observed = dict(IDENTITY, **{field: value})
            with self.assertRaises(engine_module.InferenceError) as caught:
                self.start()
            self.assertEqual(caught.exception.code, "model_mismatch")
        self.assertEqual(self.adapter.calls, [])

    def test_source_lifecycle_change_blocks_resume_without_repeating_saved_page(self):
        self.plan(2)
        self.adapter.fail.add(png(1))
        job = self.finish(self.start())
        self.knowledge.set_source_state(self.inputs[0]["source_id"], "retired", "Original author", "Fixture source changed")
        with self.assertRaises(engine_module.InferenceError) as caught:
            self.engine.resume(job["id"], "Original operator", "Resume fixture")
        self.assertEqual(caught.exception.code, "stale_dependency")
        self.assertEqual(len(self.adapter.calls), 2)
        self.assertEqual(self.engine.view()["jobs"][0]["samples"], job["samples"])

    def test_source_change_during_last_call_blocks_complete_run_registration(self):
        self.plan()
        self.adapter.hook = lambda: self.knowledge.set_source_state(self.model_source["id"], "retired", "Author", "Fixture changed")
        job = self.finish(self.start())
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error"]["code"], "stale_dependency")
        self.assertEqual(job["completed_samples"], 1)
        self.assertEqual(self.baseline.view("project-a")["runs"], [])

    def test_cancel_inflight_result_preserves_raw_without_registering_run(self):
        self.plan()
        self.adapter.block = True
        job = self.start()
        self.assertTrue(self.adapter.entered.wait(3))
        before = self.adapter.identities
        for operation in (self.engine.identity, self.start):
            with self.assertRaises(engine_module.InferenceError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "inference_busy")
        self.assertEqual(self.adapter.identities, before)
        self.engine.cancel(job["id"])
        finished = self.finish(job)
        self.assertEqual(finished["state"], "cancelled")
        self.assertEqual(finished["completed_samples"], 0)
        self.assertEqual(self.engine.artifact_bytes(finished["failed_response"]), self.adapter.raws[0])
        self.assertEqual(self.baseline.view("project-a")["runs"], [])
        self.assertEqual(self.prepare(1)["index"], 1)

    def test_close_waits_for_worker_and_never_allows_late_run_registration(self):
        self.plan()
        self.adapter.block = True
        job = self.start()
        self.assertTrue(self.adapter.entered.wait(3))
        self.engine.close()
        self.assertFalse(self.engine.thread.is_alive())
        self.assertEqual(self.finish(job)["state"], "cancelled")
        self.assertEqual(self.baseline.view("project-a")["runs"], [])

    def test_close_cancels_and_waits_for_synchronous_identity(self):
        errors = []

        def identity():
            self.adapter.entered.set()
            self.adapter.release.wait(5)
            return copy.deepcopy(IDENTITY)

        def observe():
            try:
                self.engine.identity()
            except engine_module.InferenceError as error:
                errors.append(error.code)

        self.adapter.identity = identity
        caller = threading.Thread(target=observe)
        caller.start()
        self.assertTrue(self.adapter.entered.wait(3))
        self.engine.close()
        caller.join(1)
        self.assertFalse(caller.is_alive())
        self.assertEqual(errors, ["cancelled"])

    def test_reopen_after_baseline_commit_reuses_all_saved_pages_and_existing_run(self):
        self.plan(2)
        job = self.finish(self.start())
        self.engine.close()
        # Simulate process loss after baseline commit but before final envelope.
        def before_final_save(data):
            data.update(state="running", baseline_run_id=None)
            data["history"].pop()
        self.edit("jobs", job["id"], before_final_save)
        self.engine = engine_module.InferenceEngine(self.workspace, self.baseline, self.adapter)
        self.addCleanup(self.engine.close)
        interrupted = self.engine.view()["jobs"][0]
        self.assertEqual(interrupted["state"], "interrupted")
        self.assertIsNone(self.engine.thread)
        self.engine.resume(job["id"], "Original operator", "Recover after original fixture process loss")
        recovered = self.finish(job)
        self.assertEqual(recovered["state"], "completed", recovered["error"])
        self.assertEqual(recovered["baseline_run_id"], job["baseline_run_id"])
        self.assertEqual(len(self.adapter.calls), 2)
        self.assertEqual(len(self.baseline.view("project-a")["runs"]), 1)

    def test_changed_response_bytes_block_view_and_resume(self):
        self.plan(2)
        self.adapter.fail.add(png(1))
        job = self.finish(self.start())
        response = self.engine.root / job["samples"][0]["response"]["key"]
        response.write_bytes(b"corrupt original response")
        for operation in (self.engine.view, lambda: self.engine.resume(job["id"], "Author", "Resume fixture")):
            with self.assertRaises(engine_module.InferenceError) as caught:
                operation()
            self.assertEqual(caught.exception.code, "integrity_error")
        self.assertEqual(len(self.adapter.calls), 2)

    def test_unknown_fields_and_reordered_samples_are_rejected_even_with_new_envelope_hash(self):
        self.plan(2)
        job = self.finish(self.start())
        path = self.engine._path("jobs", job["id"])
        original = path.read_bytes()
        for change in (lambda data: data.update(arbitrary_execution_proof=True),
                       lambda data: data["samples"].reverse(),
                       lambda data: data["history"].reverse()):
            path.write_bytes(original)
            self.edit("jobs", job["id"], change)
            with self.assertRaises(engine_module.InferenceError):
                self.engine.view()

    def test_missing_original_pdf_blocks_start_and_does_not_rerender(self):
        self.plan()
        self.workspace.available = False
        with self.assertRaises(engine_module.InferenceError) as caught:
            self.start()
        self.assertEqual(caught.exception.code, "original_missing")
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(self.workspace.renders, [0])

    def test_engine_identity_change_blocks_resuming_previous_checkpoint(self):
        self.plan(2)
        self.adapter.fail.add(png(1))
        job = self.finish(self.start())
        with mock.patch.object(engine_module, "IMPLEMENTATION_SHA256", "f" * 64):
            with self.assertRaises(engine_module.InferenceError) as caught:
                self.engine.resume(job["id"], "Author", "Resume fixture")
            self.assertEqual(caught.exception.code, "engine_changed")
        self.assertEqual(len(self.adapter.calls), 2)


if __name__ == "__main__":
    unittest.main()
