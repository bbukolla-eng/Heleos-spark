"""PUBLIC original synthetic checks for immutable saved-output baselines."""
from contextlib import closing
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


knowledge = load("mechanical_knowledge")
baseline = load("mechanical_model_baseline")


def digest(value):
    return hashlib.sha256(baseline.packed(value)).hexdigest()


def obj(identifier="truth-a", category="equipment", label="AHU", bbox=None):
    return {"id": identifier, "category": category, "label": label,
            "bbox": bbox or [0.1, 0.1, 0.4, 0.5]}


class BaselineStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "knowledge"
        self.knowledge = knowledge.KnowledgeStore(self.directory)
        self.store = baseline.BaselineStore(self.knowledge)
        self.source = self.source_record("positive")
        self.origin = self.source_record("model-origin")

    def source_record(self, name, project="project-a", snapshot=None, **metadata):
        values = {
            "source_class": "synthetic_fixture", "title": name,
            "rights_basis": "Original PUBLIC synthetic fixture; recorded provenance only.",
            "jurisdiction": "unspecified", "edition": "fixture-v1",
            "retrieved_at": "2026-09-11T05:00:00Z", "applicability": "Synthetic local test only",
            "locator": "fixture/" + name, "snapshot_kind": "synthetic_page", "project_id": project,
        }
        values.update(metadata)
        return self.knowledge.add_source(values, snapshot or ("synthetic bytes " + name).encode())

    def sample(self, source=None, identifier="sample-a", split="test", group="originating-job-a", objects=None):
        source = source or self.source
        return {"id": identifier, "source_id": source["id"], "input_sha256": source["snapshot_sha256"],
                "group_id": group, "split": split, "objects": [obj()] if objects is None else objects}

    def dataset(self, samples=None):
        return {"name": "Original synthetic annotated pages", "version": "fixture-v1",
                "annotation_basis": "Fixture author independently specified visible boxes and categories before saved outputs.",
                "scope": ["equipment"], "samples": samples if samples is not None else [self.sample()]}

    def model(self):
        return {"name": "Saved fixture output producer", "version": "fixture-v1",
                "source_ids": [self.origin["id"]],
                "artifacts": [{"name": "declared-weights.bin", "sha256": "a" * 64, "bytes": 512}],
                "runtime": {"name": "Uninstalled declared fixture runtime", "version": "fixture-v1", "sha256": "b" * 64},
                "preprocessing": {"normalization": "identity", "prompt": "synthetic fixture only"}}

    def register(self, kind, payload, project="project-a"):
        return self.store.register(project, kind, payload, " Synthetic author ", " Original local fixture evidence. ")

    def prepared(self, dataset=None):
        data = self.register("dataset", dataset or self.dataset())
        model = self.register("model", self.model())
        plan = self.register("plan", {
            "dataset_id": data["id"], "model_id": model["id"], "split": "test",
            "criteria": {"iou_threshold": 0.5, "minimum_precision": 0.95, "minimum_recall": 0.95,
                         "max_mean_latency_ms": 100, "max_peak_memory_mb": 2048},
        })
        return data, model, plan

    def run_payload(self, model, plan, objects=None):
        return {"plan_id": plan["id"], "model_manifest_sha256": model["model_manifest_sha256"],
                "predictions": [{"sample_id": "sample-a", "objects": [obj("prediction-a")] if objects is None else objects,
                                 "latency_ms": 10, "peak_memory_mb": 128}]}

    def assert_rejected(self, kind, payload, code="invalid_payload", project="project-a"):
        before = self.count()
        with self.assertRaises(baseline.BaselineError) as caught:
            self.register(kind, payload, project)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.count(), before)

    def count(self):
        with closing(sqlite3.connect(str(self.store.database))) as connection:
            return connection.execute("SELECT count(*) FROM " + baseline.RECORD_TABLE).fetchone()[0]

    def test_empty_view_has_all_scope_categories_and_stable_reopen(self):
        view = self.store.view("project-a")
        self.assertEqual(view["datasets"], [])
        self.assertEqual(view["models"], [])
        self.assertEqual(view["plans"], [])
        self.assertEqual(view["runs"], [])
        self.assertEqual([row["category"] for row in view["coverage"]], list(baseline.CATEGORIES))
        self.assertTrue(all(row["samples"] == row["evaluated_samples"] == 0 for row in view["coverage"]))
        self.assertEqual(view["quantity_authority"], "none")
        self.assertEqual(view["execution_proof"], "saved_outputs_only")
        self.assertEqual(view, baseline.BaselineStore(knowledge.KnowledgeStore(self.directory)).view("project-a"))

    def test_round_trip_replay_and_audit_labels_preserve_input(self):
        original = self.dataset()
        original["samples"][0]["objects"][0]["label"] = "  Air   Handling UNIT  "
        copy_before = copy.deepcopy(original)
        first = self.register("dataset", original)
        self.assertEqual(original, copy_before)
        self.assertEqual(first, self.register("dataset", original))
        self.assertEqual(first["actor"], " Synthetic author ")
        self.assertEqual(first["payload"]["samples"][0]["objects"][0]["label"], "  Air   Handling UNIT  ")
        self.assertEqual(first["issues"], [])
        pin = first["source_pins"][0]
        self.assertEqual(pin["rights_basis"], self.source["metadata"]["rights_basis"])
        self.assertEqual(pin["snapshot_sha256"], self.source["snapshot_sha256"])
        self.assertEqual(pin["source_id"], self.source["id"])
        self.assertEqual(self.count(), 1)
        view = self.store.view("project-a")
        reopened = baseline.BaselineStore(knowledge.KnowledgeStore(self.directory))
        self.assertEqual(view, reopened.view("project-a"))
        first["payload"]["samples"].clear()
        self.assertEqual(len(reopened.view("project-a")["datasets"][0]["payload"]["samples"]), 1)

    def test_manifest_is_pinned_declaration_without_artifact_execution(self):
        manifest = self.model()
        manifest["preprocessing"]["code"] = "raise AssertionError('JSON must not execute')"
        record = self.register("model", manifest)
        self.assertEqual(record["model_manifest_sha256"], digest(record["payload"]))
        self.assertEqual(record["issues"], [])
        self.assertFalse((self.directory / "declared-weights.bin").exists())
        self.assertEqual(record["payload"]["runtime"], manifest["runtime"])
        for field, value in (("source_ids", []), ("preprocessing", {}), ("version", "latest")):
            invalid = self.model()
            invalid[field] = value
            self.assert_rejected("model", invalid)
        for field, value in (("bytes", True), ("bytes", 0), ("bytes", 1.2), ("sha256", "short")):
            invalid = self.model()
            invalid["artifacts"][0][field] = value
            self.assert_rejected("model", invalid)
        invalid = self.model()
        invalid["runtime"]["version"] = "current"
        self.assert_rejected("model", invalid)
        invalid = self.model()
        invalid["artifacts"] *= 2
        self.assert_rejected("model", invalid)

    def test_reject_unknown_fields_and_non_json_values_without_rows(self):
        for extra in ("approved", "train_permission", "score", "pass", "quantity", "report"):
            value = self.dataset()
            value[extra] = True
            self.assert_rejected("dataset", value)
        for unsupported in (float("nan"), float("inf"), object(), b"bytes", (1, 2)):
            value = self.model()
            value["preprocessing"]["bad"] = unsupported
            self.assert_rejected("model", value)
        value = self.dataset()
        value["samples"][0]["objects"][0]["bbox"][0] = True
        self.assert_rejected("dataset", value)

    def test_dataset_boxes_ids_and_scope_are_strict(self):
        for box in ([0.5, 0.1, 0.4, 0.5], [0, 0, 0, 1], [-1, 0, 1, 1], [0, 0, 1, 2], [0, 0, 1]):
            value = self.dataset()
            value["samples"][0]["objects"][0]["bbox"] = box
            self.assert_rejected("dataset", value)
        value = self.dataset()
        value["samples"][0]["objects"] *= 2
        self.assert_rejected("dataset", value, "duplicate_object")
        value = self.dataset()
        value["samples"][0]["objects"][0]["category"] = "controls"
        self.assert_rejected("dataset", value, "invalid_category")
        value = self.dataset()
        value["scope"] *= 2
        self.assert_rejected("dataset", value, "invalid_category")
        value = self.dataset()
        value["samples"][0]["group_id"] = ""
        self.assert_rejected("dataset", value)
        value = self.dataset()
        value["samples"][0].pop("objects")
        self.assert_rejected("dataset", value)

    def test_snapshot_hash_and_project_bindings_are_authoritative(self):
        value = self.dataset()
        value["samples"][0]["input_sha256"] = "f" * 64
        self.assert_rejected("dataset", value, "input_mismatch")
        self.assert_rejected("dataset", self.dataset(), "project_mismatch", project="project-b")
        value = self.model()
        value["source_ids"] = ["source_missing"]
        self.assert_rejected("model", value, "source_not_found")
        shared = self.source_record("shared", project="unspecified")
        value["source_ids"] = [shared["id"]]
        self.assertEqual(self.register("model", value, "project-b")["issues"], [])

    def test_aliasing_same_source_or_equal_snapshot_is_rejected_within_split(self):
        alias = self.source_record("alias", snapshot=b"synthetic bytes positive")
        for source in (self.source, alias):
            value = self.dataset([self.sample(), self.sample(source, "alias-sample", group="other-job")])
            self.assert_rejected("dataset", value, "duplicate_input")

    def test_duplicate_ids_and_required_test_split(self):
        second = self.source_record("second")
        value = self.dataset([self.sample(), self.sample(second, "sample-a")])
        self.assert_rejected("dataset", value, "duplicate_sample")
        value = self.dataset([self.sample(split="train")])
        self.assert_rejected("dataset", value)
        value = self.dataset([self.sample(objects=[])])
        self.assertEqual(self.register("dataset", value)["issues"], [])

    def test_group_split_leakage_and_cross_version_input_assignment(self):
        second = self.source_record("second")
        value = self.dataset([self.sample(), self.sample(second, "train", split="train", group="ORIGINATING-JOB-A")])
        self.assert_rejected("dataset", value, "split_leakage")
        self.register("dataset", self.dataset())
        fresh_test = self.source_record("fresh-test")
        value = self.dataset([self.sample(split="train", group="changed-group"),
                              self.sample(fresh_test, "new-test", group="fresh-job")])
        value["version"] = "fixture-v2"
        self.assert_rejected("dataset", value, "split_leakage")

    def test_same_artifact_or_revision_cannot_cross_splits_with_different_groups(self):
        for name, metadata in (("artifact", {"source_artifact_sha256": "a" * 64}),
                               ("revision", {"revision_id": "revision-fixture"})):
            with self.subTest(name=name):
                first = self.source_record(name + "-page1", **metadata)
                second = self.source_record(name + "-page2", **metadata)
                test_sample = self.sample(first, "test-" + name, group="test-" + name)
                train_sample = self.sample(second, "train-" + name, split="train", group="train-" + name)
                self.assert_rejected("dataset", self.dataset([test_sample, train_sample]), "split_leakage")
                train_sample["split"] = "test"
                self.assertEqual(self.register("dataset", self.dataset([test_sample, train_sample]))["issues"], [])

    def test_plan_requires_existing_same_project_dependencies_and_positive_truth(self):
        dataset = self.register("dataset", self.dataset([self.sample(objects=[])]))
        model = self.register("model", self.model())
        plan = {"dataset_id": dataset["id"], "model_id": model["id"], "split": "test",
                "criteria": {"iou_threshold": 0.5, "minimum_precision": 0.9, "minimum_recall": 0.9,
                             "max_mean_latency_ms": 10, "max_peak_memory_mb": 100}}
        self.assert_rejected("plan", plan, "empty_evaluation")
        plan["dataset_id"] = "missing-dataset"
        self.assert_rejected("plan", plan, "record_not_found")
        plan["dataset_id"] = model["id"]
        self.assert_rejected("plan", plan, "invalid_dependency")
        data, model, valid_plan = self.prepared()
        self.assert_rejected("plan", valid_plan["payload"], "project_mismatch", project="project-b")
        for field, invalid_value in (("iou_threshold", 0), ("minimum_recall", 1.1),
                                     ("max_mean_latency_ms", 0), ("max_peak_memory_mb", True)):
            invalid = copy.deepcopy(valid_plan["payload"])
            invalid["criteria"][field] = invalid_value
            self.assert_rejected("plan", invalid)
        self.assertEqual(valid_plan["scorer_identity"]["sha256"], baseline._scoring()[1]["sha256"])

    def test_server_report_recomputes_and_reopen_preserves_saved_bytes(self):
        data, model, plan = self.prepared()
        payload = self.run_payload(model, plan)
        original = copy.deepcopy(payload)
        run = self.register("run", payload)
        self.assertEqual(payload, original)
        self.assertEqual(run["payload"]["report"]["metrics"]["true_positive"], 1)
        self.assertTrue(run["payload"]["report"]["thresholds_met"])
        self.assertEqual(run["source_pins"], plan["source_pins"])
        self.assertEqual(run["issues"], [])
        self.assertEqual(run, self.register("run", payload))
        before = self.store.view("project-a")
        reopened = baseline.BaselineStore(knowledge.KnowledgeStore(self.directory))
        self.assertEqual(before, reopened.view("project-a"))
        self.assertEqual(self.count(), 4)
        report = run["payload"]["report"]
        scorer = baseline._scoring()[0]
        self.assertEqual(report, scorer.score(data["payload"]["samples"], run["payload"]["predictions"], plan["payload"]["criteria"]))

    def test_unknown_memory_round_trip_preserves_predictions_and_replay(self):
        negative = self.source_record("negative-memory")
        data, model, plan = self.prepared(self.dataset([
            self.sample(), self.sample(negative, "negative", objects=[])]))
        payload = self.run_payload(model, plan)
        payload["predictions"][0]["objects"][0]["label"] = "  AHU  "
        payload["predictions"].append({"sample_id": "negative", "objects": [],
                                       "latency_ms": 30, "peak_memory_mb": None})
        original = copy.deepcopy(payload)
        run = self.register("run", payload)
        self.assertEqual(payload, original)
        report = run["payload"]["report"]
        self.assertEqual(report["metrics"]["true_positive"], 1)
        self.assertEqual(report["metrics"]["mean_latency_ms"], 20)
        self.assertIsNone(report["metrics"]["peak_memory_mb"])
        self.assertFalse(report["thresholds_met"])
        saved = {row["sample_id"]: row for row in run["payload"]["predictions"]}
        self.assertIsNone(saved["negative"]["peak_memory_mb"])
        self.assertEqual(saved["sample-a"]["objects"][0]["label"], "  AHU  ")
        self.assertEqual(run, self.register("run", payload))
        reopened = baseline.BaselineStore(knowledge.KnowledgeStore(self.directory))
        self.assertEqual(reopened.view("project-a")["runs"], [run])
        self.assertEqual(report, baseline._scoring()[0].score(
            data["payload"]["samples"], run["payload"]["predictions"], plan["payload"]["criteria"]))
        self.assertEqual(self.count(), 4)

    def test_measured_zero_and_unknown_memory_remain_distinct_saved_runs(self):
        unused, model, plan = self.prepared()
        payload = self.run_payload(model, plan)
        payload["predictions"][0]["peak_memory_mb"] = 0
        measured = self.register("run", payload)
        payload["predictions"][0]["peak_memory_mb"] = None
        unknown = self.register("run", payload)
        self.assertNotEqual(measured["id"], unknown["id"])
        self.assertEqual(measured["payload"]["report"]["metrics"]["peak_memory_mb"], 0)
        self.assertTrue(measured["payload"]["report"]["thresholds_met"])
        self.assertIsNone(unknown["payload"]["report"]["metrics"]["peak_memory_mb"])
        self.assertFalse(unknown["payload"]["report"]["thresholds_met"])

    def test_resource_values_remain_finite_and_latency_is_required(self):
        unused, model, plan = self.prepared()
        for field in ("latency_ms", "peak_memory_mb"):
            for value in (-1, float("nan"), float("inf"), float("-inf"), True, "0"):
                with self.subTest(field=field, value=repr(value)):
                    payload = self.run_payload(model, plan)
                    payload["predictions"][0][field] = value
                    self.assert_rejected("run", payload)
        payload = self.run_payload(model, plan)
        payload["predictions"][0].update(latency_ms=None, peak_memory_mb=None)
        self.assert_rejected("run", payload)

    def test_failing_evaluation_is_persisted_as_result_and_counts_evaluated_scope(self):
        unused, model, plan = self.prepared()
        run = self.register("run", self.run_payload(model, plan, objects=[]))
        report = run["payload"]["report"]
        self.assertFalse(report["thresholds_met"])
        self.assertEqual(report["metrics"]["false_negative"], 1)
        self.assertIsNone(report["metrics"]["precision"])
        self.assertEqual(run["issues"], [])
        coverage = {row["category"]: row for row in self.store.view("project-a")["coverage"]}
        self.assertEqual(coverage["equipment"], {"category": "equipment", "samples": 1, "evaluated_samples": 1})
        self.assertEqual(coverage["controls"]["evaluated_samples"], 0)

    def test_negative_page_false_positive_is_included_and_outputs_are_exact(self):
        negative = self.source_record("negative")
        data = self.dataset([self.sample(), self.sample(negative, "negative", objects=[])])
        unused, model, plan = self.prepared(data)
        payload = self.run_payload(model, plan)
        self.assert_rejected("run", payload, "sample_mismatch")
        payload["predictions"].append({"sample_id": "negative", "objects": [obj("extra")], "latency_ms": 30, "peak_memory_mb": 256})
        run = self.register("run", payload)
        metrics = run["payload"]["report"]["metrics"]
        self.assertEqual((metrics["true_positive"], metrics["false_positive"], metrics["false_negative"]), (1, 1, 0))
        self.assertEqual(metrics["mean_latency_ms"], 20)
        self.assertFalse(run["payload"]["report"]["thresholds_met"])
        self.assertEqual(run["issues"], [])
        duplicate = copy.deepcopy(payload)
        duplicate["predictions"].append(duplicate["predictions"][0])
        self.assert_rejected("run", duplicate, "duplicate_prediction")
        extra = copy.deepcopy(payload)
        extra["predictions"][1]["sample_id"] = "unknown"
        self.assert_rejected("run", extra, "sample_mismatch")

    def test_run_cannot_supply_score_change_model_or_retrofit_plan(self):
        unused, model, plan = self.prepared()
        for field in ("report", "criteria", "score", "thresholds_met"):
            invalid = self.run_payload(model, plan)
            invalid[field] = {}
            self.assert_rejected("run", invalid)
        invalid = self.run_payload(model, plan)
        invalid["model_manifest_sha256"] = "f" * 64
        self.assert_rejected("run", invalid, "model_mismatch")
        invalid = self.run_payload(model, plan)
        invalid["plan_id"] = "not-yet-recorded"
        self.assert_rejected("run", invalid, "record_not_found")
        invalid = self.run_payload(model, plan)
        invalid["predictions"][0]["latency_ms"] = -1
        self.assert_rejected("run", invalid)
        original = copy.deepcopy(plan["payload"])
        plan["payload"]["criteria"]["minimum_recall"] = 0
        stored = self.store.view("project-a")["plans"][0]
        self.assertEqual(stored["payload"], original)

    def test_lifecycle_drift_blocks_new_plans_runs_and_survives_reactivation(self):
        data, model, plan = self.prepared()
        self.register("run", self.run_payload(model, plan))
        before = self.store.view("project-a")
        self.knowledge.set_source_state(self.source["id"], "retired", "Fixture owner", "Synthetic lifecycle check")
        view = self.store.view("project-a")
        self.assertNotEqual(before["state_fingerprint"], view["state_fingerprint"])
        self.assertTrue(view["datasets"][0]["issues"])
        self.assertTrue(view["plans"][0]["issues"])
        self.assertTrue(view["runs"][0]["issues"])
        self.assertEqual(before["runs"][0]["payload"], view["runs"][0]["payload"])
        self.assertEqual(view["coverage"][0]["evaluated_samples"], 0)
        self.assert_rejected("plan", plan["payload"], "stale_dependency")
        self.assert_rejected("run", self.run_payload(model, plan), "stale_dependency")
        self.knowledge.set_source_state(self.source["id"], "current", "Fixture owner", "Synthetic reactivation")
        self.assert_rejected("run", self.run_payload(model, plan), "stale_dependency")
        fresh = self.register("dataset", data["payload"])
        self.assertNotEqual(fresh["id"], data["id"])
        self.assertEqual(fresh["issues"], [])
        next_plan = dict(plan["payload"], dataset_id=fresh["id"])
        self.assertEqual(self.register("plan", next_plan)["issues"], [])

    def test_model_origin_lifecycle_and_retired_declarations_remain_inspectable(self):
        data, unused, plan = self.prepared()
        self.knowledge.set_source_state(self.origin["id"], "retired", "Fixture owner", "Synthetic retirement")
        self.assert_rejected("plan", plan["payload"], "stale_dependency")
        record = self.register("model", self.model())
        self.assertTrue(any("source_not_current" in issue for issue in record["issues"]))
        self.assertEqual(self.store.view("project-a")["datasets"][0]["id"], data["id"])

    def test_snapshot_corruption_is_visible_without_discarding_history(self):
        unused, model, plan = self.prepared()
        run = self.register("run", self.run_payload(model, plan))
        with closing(sqlite3.connect(str(self.store.database))) as connection:
            connection.execute("UPDATE sources SET snapshot=? WHERE id=?", (b"corrupt", self.source["id"]))
            connection.commit()
        view = self.store.view("project-a")
        self.assertTrue(any("source_integrity_error:" in issue for issue in view["datasets"][0]["issues"]))
        self.assertEqual(view["runs"][0]["payload"], run["payload"])
        self.assert_rejected("run", self.run_payload(model, plan), "stale_dependency")

    def test_scorer_drift_marks_plans_and_runs_without_erasing_report(self):
        unused, model, plan = self.prepared()
        run = self.register("run", self.run_payload(model, plan))
        unknown_payload = self.run_payload(model, plan)
        unknown_payload["predictions"][0]["peak_memory_mb"] = None
        unknown = self.register("run", unknown_payload)
        scorer, identity = baseline._scoring()
        changed_identity = dict(identity, sha256="c" * 64)
        with mock.patch.object(baseline, "_scoring", return_value=(scorer, changed_identity)):
            view = self.store.view("project-a")
            self.assertIn("scorer_changed", view["plans"][0]["issues"])
            history = {row["id"]: row for row in view["runs"]}
            for saved in (run, unknown):
                self.assertIn("scorer_changed", history[saved["id"]]["issues"])
                self.assertEqual(history[saved["id"]]["payload"], saved["payload"])
            self.assert_rejected("run", self.run_payload(model, plan), "stale_dependency")
            self.assert_rejected("run", unknown_payload, "stale_dependency")

    def test_tampered_record_content_and_timestamp_fail_identity_check(self):
        dataset = self.register("dataset", self.dataset())
        with closing(sqlite3.connect(str(self.store.database))) as connection:
            original = connection.execute("SELECT body_json FROM " + baseline.RECORD_TABLE + " WHERE id=?", (dataset["id"],)).fetchone()[0]
            for field, value in (("actor", "forged author"), ("recorded_at", "forged time")):
                body = json.loads(original)
                body[field] = value
                connection.execute("UPDATE " + baseline.RECORD_TABLE + " SET body_json=? WHERE id=?", (baseline.packed(body).decode(), dataset["id"]))
                connection.commit()
                with self.assertRaises(baseline.BaselineError) as caught:
                    self.store.view("project-a")
                self.assertEqual(caught.exception.code, "integrity_error")

    def test_rehashed_false_report_is_detected_by_recomputation(self):
        unused, model, plan = self.prepared()
        run = self.register("run", self.run_payload(model, plan))
        with closing(sqlite3.connect(str(self.store.database))) as connection:
            body = json.loads(connection.execute("SELECT body_json FROM " + baseline.RECORD_TABLE + " WHERE id=?", (run["id"],)).fetchone()[0])
            report = body["payload"]["report"]
            report["metrics"]["true_positive"] = 900
            report["sha256"] = digest({key: value for key, value in report.items() if key != "sha256"})
            request_hash = digest({key: value for key, value in body.items() if key != "recorded_at"})
            new_id = "baseline_run_" + digest(body)
            connection.execute("UPDATE " + baseline.RECORD_TABLE + " SET id=?, request_sha256=?, body_json=? WHERE id=?",
                               (new_id, request_hash, baseline.packed(body).decode(), run["id"]))
            connection.commit()
        with self.assertRaises(baseline.BaselineError) as caught:
            self.store.view("project-a")
        self.assertEqual(caught.exception.code, "integrity_error")
        self.assertIn("not reproducible", str(caught.exception))

    def test_source_pin_check_and_write_hold_one_immediate_transaction(self):
        original = self.store._source_pin
        attempts = []

        def observe(connection, source_id, project_id):
            pin = original(connection, source_id, project_id)
            self.assertTrue(connection.in_transaction)
            with closing(sqlite3.connect(str(self.store.database), timeout=0)) as competitor:
                with self.assertRaises(sqlite3.OperationalError) as caught:
                    competitor.execute("BEGIN IMMEDIATE")
                self.assertIn("locked", str(caught.exception))
            attempts.append(source_id)
            return pin

        with mock.patch.object(self.store, "_source_pin", side_effect=observe):
            self.register("dataset", self.dataset())
        self.assertTrue(attempts)
        self.assertEqual(self.count(), 1)

    def test_failed_scoring_rolls_back_without_a_partial_run(self):
        unused, model, plan = self.prepared()
        count = self.count()
        with mock.patch.object(self.store, "_score", side_effect=baseline.BaselineError("fixture_error", "Synthetic scorer failure")):
            self.assert_rejected("run", self.run_payload(model, plan), "fixture_error")
        self.assertEqual(count, self.count())
        self.assertEqual(self.store.view("project-a")["runs"], [])

    def test_schema_drift_is_rejected_on_reopen(self):
        with closing(sqlite3.connect(str(self.store.database))) as connection:
            connection.execute("UPDATE " + baseline.SCHEMA_TABLE + " SET version=99")
            connection.commit()
        with self.assertRaises(baseline.BaselineError) as caught:
            baseline.BaselineStore(self.knowledge)
        self.assertEqual(caught.exception.code, "schema_version")


if __name__ == "__main__":
    unittest.main()
