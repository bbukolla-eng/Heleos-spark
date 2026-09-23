"""Equipment workflow checks at extraction, persistence and HTTP boundaries.

Fixtures are original synthetic content. Model doubles verify adapter behavior,
not recognition accuracy. Live model/native platform acceptance is separate.
"""
import base64
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("drawing", ROOT / "scripts/drawing-workspace.py")
drawing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drawing)
eq = drawing._equipment_module
REVISION = "a" * 64
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWoQAAAAASUVORK5CYII=")


def xml(tags):
    lines = []
    for i, tag in enumerate(tags):
        words = []
        x = 10
        for word in tag.split():
            width = len(word) * 6
            words.append('<word xMin="%s" yMin="%s" xMax="%s" yMax="%s">%s</word>' %
                         (x, 20 + i * 20, x + width, 30 + i * 20, word))
            x += width + 3
        lines.append("<line>" + "".join(words) + "</line>")
    return ('<html><body><doc><page width="600" height="800"><flow><block>' +
            "".join(lines) + '</block></flow></page></doc></body></html>').encode()


class Reader:
    def identity(self):
        return {"kind": "synthetic-reader", "version": 1}

    def read(self, snapshot, index):
        if snapshot.read() != b"synthetic verified PDF":
            raise AssertionError("wrong input bytes")
        return xml(["EF - 1", "EF-1", "AHU-01"] if index == 0 else ["EF-1", "AHU-1", "SF-3"])


class Workspace:
    def __init__(self, root):
        self.root = root
        self.metadata = {"project": {"id": "synthetic-project"}}
        self.renderer = ["synthetic-renderer"]

    def state(self):
        return {"documents": [{"revision_id": REVISION, "sheets": [
            {"index": i, "sheet_id": hashlib.sha256(str(i).encode()).hexdigest()} for i in range(2)]}]}

    def verified_pdf(self, revision):
        if revision != REVISION:
            raise eq.TakeoffError("integrity", "Wrong revision.", 409)
        stream = tempfile.TemporaryFile()
        stream.write(b"synthetic verified PDF")
        stream.seek(0)
        return stream, 22

    def rendered_page(self, revision, index):
        return PNG


class EquipmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(Path(self.temp.name))
        self.engine = eq.EquipmentEngine(self.workspace, text_reader=Reader())
        self.workspace.equipment = self.engine
        self.addCleanup(self.engine.close)
        self.sources = [{"revision_id": REVISION, "index": 0, "role": "plan"},
                        {"revision_id": REVISION, "index": 1, "role": "schedule"}]

    def completed(self):
        run = self.engine.start(self.sources, "text")
        self.engine.thread.join(timeout=5)
        result = self.engine.result(run["id"])
        self.assertEqual(result["state"], "completed", result.get("error"))
        return result

    def test_split_tags_locations_and_leading_zero_identity(self):
        findings, words, dimensions = eq.parse_text_page(xml(["EF - 1", "AHU-01", "AHU-1"]))
        self.assertEqual([f["tag"] for f in findings], ["EF-1", "AHU-01", "AHU-1"])
        self.assertEqual(findings[0]["bbox"], [round(10/600, 7), 0.025, round(40/600, 7), 0.0375])
        self.assertEqual((words, dimensions), (5, [600, 800]))
        for value in ("=SUM(A1)", "EF-1<script>", "EF-1/2"):
            with self.assertRaises(eq.TakeoffError):
                eq.normalize_tag(value)
        for value in (xml(["EF-1"]).replace(b'width="600"', b'width="NaN"'),
                      b'<!DOCTYPE x [<!ENTITY a "x">]><x>&a;</x>'):
            with self.assertRaises(eq.TakeoffError):
                eq.parse_text_page(value)

    def test_extract_reconcile_review_export_and_replay(self):
        run = self.completed()
        rows = {row["tag"]: row for row in run["rows"]}
        self.assertEqual(set(rows), {"EF-1", "AHU-01", "AHU-1", "SF-3"})
        self.assertIn("repeated_plan_tag", rows["EF-1"]["issues"])
        self.assertIn("plan_only", rows["AHU-01"]["issues"])
        self.assertIn("schedule_only", rows["SF-3"]["issues"])
        self.assertTrue(all(row["reviewed_quantity"] is None for row in rows.values()))
        self.assertIn(b"UNKNOWN", self.engine.export(run["id"]))
        first, second = rows["EF-1"]["plan"]
        corrected = self.engine.review(run["id"], first["id"], 0, "include", "EF-1", "Checked physical fan", "Estimator")
        self.assertEqual(next(row for row in corrected["rows"] if row["tag"] == "EF-1")["reviewed_quantity"], 1)
        with self.assertRaises(eq.TakeoffError) as stale:
            self.engine.review(run["id"], second["id"], 0, "include", "EF-1", "Duplicate view", "Estimator")
        self.assertEqual(stale.exception.code, "stale_review")
        conflict = self.engine.review(run["id"], second["id"], 1, "include", "EF-1", "Checked second occurrence", "Estimator")
        row = next(row for row in conflict["rows"] if row["tag"] == "EF-1")
        self.assertIsNone(row["reviewed_quantity"])
        self.assertIn("count_conflict", row["issues"])
        corrected = self.engine.review(run["id"], second["id"], 2, "exclude", "EF-1", "Same physical fan in note", "Estimator")
        row = next(row for row in corrected["rows"] if row["tag"] == "EF-1")
        self.assertEqual(row["reviewed_quantity"], 1)
        self.assertNotIn("repeated_plan_tag", row["issues"])
        self.assertIn(b"EF-1,1,1,1", self.engine.export(run["id"]))
        replay = self.engine.start(list(reversed(self.sources)), "text")
        self.assertEqual((replay["id"], replay["review_version"]), (run["id"], 3))
        self.assertEqual(corrected["findings"], run["findings"])

    def test_correction_merges_only_the_explicitly_corrected_identity(self):
        run = self.completed()
        finding = next(item for item in run["findings"] if item["tag"] == "AHU-01")
        updated = self.engine.review(run["id"], finding["id"], 0, "include", "AHU-1", "Verified tag reading on source", "Estimator")
        self.assertNotIn("AHU-01", {row["tag"] for row in updated["rows"]})
        self.assertEqual(next(row for row in updated["rows"] if row["tag"] == "AHU-1")["issues"], [])
        self.assertEqual(next(item for item in updated["findings"] if item["id"] == finding["id"])["tag"], "AHU-01")
        self.assertEqual(len(updated["history"]), 1)

    def test_empty_text_fails_and_no_failed_export_is_allowed(self):
        self.engine.text_reader.read = lambda snapshot, index: xml([])
        started = self.engine.start(self.sources, "text")
        self.engine.thread.join(timeout=5)
        run = self.engine.result(started["id"])
        self.assertEqual(run["state"], "failed")
        self.assertEqual(run["findings"], [])
        with self.assertRaises(eq.TakeoffError):
            self.engine.export(run["id"])

    def test_evidence_change_blocks_export_and_findings_change_blocks_load(self):
        run = self.completed()
        artifact = self.engine.root / run["pages"][0]["artifact"]["key"]
        artifact.write_bytes(b"changed")
        with self.assertRaises(eq.TakeoffError) as changed:
            self.engine.export(run["id"])
        self.assertEqual(changed.exception.code, "artifact_invalid")
        path = self.engine.root / (run["id"] + ".json")
        data = json.loads(path.read_text())
        data["findings"][0]["tag"] = "EF-99"
        path.write_text(json.dumps(data))
        with self.assertRaises(eq.TakeoffError):
            self.engine.result(run["id"])

    def test_manual_region_restart_and_interrupted_run_recovery(self):
        run = self.completed()
        added = self.engine.add(run["id"], 0, REVISION, 0, [0.1, 0.2, 0.2, 0.3], "EF-9", "Missed tag", "Estimator")
        self.assertEqual(added["findings"], run["findings"])
        self.assertEqual(next(row for row in added["rows"] if row["tag"] == "EF-9")["reviewed_quantity"], 1)
        self.engine.close()
        reopened = eq.EquipmentEngine(self.workspace, Reader())
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.result(run["id"])["review_version"], 1)
        # A persisted nonterminal record from an exited process must not remain
        # labelled running when the workspace is reopened.
        saved = reopened._load(run["id"])
        saved["state"] = "running"
        reopened._save(saved)
        reopened.close()
        recovered = eq.EquipmentEngine(self.workspace, Reader())
        self.addCleanup(recovered.close)
        self.assertEqual(recovered.result(run["id"])["state"], "interrupted")

    def test_invalid_page_selections_are_rejected_before_work_starts(self):
        for sources in (self.sources + self.sources, [{"revision_id": REVISION, "index": True, "role": "plan"}],
                        [{"revision_id": "b" * 64, "index": 0, "role": "plan"}]):
            with self.assertRaises(eq.TakeoffError):
                self.engine.start(sources, "text")
        self.assertIsNone(self.engine.thread)

    def http(self, route, body=None, origin=True):
        # Exercise the actual HTTP handler over local IPC without opening a TCP
        # listening port. This is transport testing, not live browser acceptance.
        client, server_socket = socket.socketpair()
        server = SimpleNamespace(workspace=self.workspace, origin="http://127.0.0.1:54321",
                                 token="synthetic-token")
        thread = threading.Thread(target=drawing.DrawingHandler,
                                  args=(server_socket, ("127.0.0.1", 0), server))
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", 54321, timeout=5)
            connection.sock = client
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = server.origin
            connection.request("POST" if body is not None else "GET", "/synthetic-token/" + route,
                               body=json.dumps(body) if body is not None else None, headers=headers)
            response = connection.getresponse()
            result = response.status, response.read()
            connection.close()
            return result
        finally:
            client.close()
            thread.join(timeout=5)
            server_socket.close()

    def test_actual_http_routes_json_validation_and_origin(self):
        status, data = self.http("api/equipment")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)["text_reader"])
        status, data = self.http("api/equipment/runs", {"selections": self.sources, "mode": "text"})
        self.assertEqual(status, 202, data)
        self.engine.thread.join(timeout=5)
        run_id = json.loads(data)["id"]
        status, data = self.http("api/equipment/runs/" + run_id)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["state"], "completed")
        status, csv = self.http("api/equipment/runs/" + run_id + "/export.csv")
        self.assertEqual(status, 200)
        self.assertIn(b"UNKNOWN", csv)
        status, _ = self.http("api/equipment/runs", {"selections": self.sources, "mode": "text"}, origin=False)
        self.assertEqual(status, 403)
        status, _ = self.http("api/equipment/runs", {"selections": self.sources})
        self.assertEqual(status, 400)


class VisionTests(unittest.TestCase):
    def model(self):
        model = eq.LocalVision("qwen3-vl:8b", "d" * 64)
        calls = []
        replies = {
            "/api/tags": {"models": [{"name": model.model, "digest": model.digest, "details": {"format": "gguf"}}]},
            "/api/show": {"capabilities": ["vision"], "model_info": {"architecture": "qwen3vl"}},
            "/api/version": {"version": "fixture-1"},
            "/api/chat": {"model": model.model, "done": True, "done_reason": "stop",
                          "message": {"content": json.dumps({"items": [
                              {"tag": "EF-1", "source_text": "EF-1", "bbox": [100, 200, 200, 300]}]})}}}
        def request(path, body=None, timeout=None):
            calls.append((path, body))
            return replies[path]
        model.request = request
        return model, calls, replies

    def test_local_digest_and_cloud_checks_precede_image_submission(self):
        model, calls, replies = self.model()
        identity = model.identity()
        items, _ = model.read(PNG, "plan", identity)
        self.assertEqual(items[0]["bbox"], [0.1, 0.2, 0.2, 0.3])
        chat = next(body for path, body in calls if path == "/api/chat")
        self.assertNotIn("tools", chat)
        self.assertFalse(chat["stream"])
        self.assertEqual(base64.b64decode(chat["messages"][1]["images"][0]), PNG)
        for cloud_key in ("remote_host", "remote_model"):
            replies["/api/show"][cloud_key] = "cloud"
            calls.clear()
            with self.assertRaises(eq.TakeoffError):
                model.read(PNG, "plan", identity)
            self.assertNotIn("/api/chat", [path for path, _ in calls])
            del replies["/api/show"][cloud_key]
        replies["/api/tags"]["models"][0]["digest"] = "e" * 64
        with self.assertRaises(eq.TakeoffError):
            model.identity()

    def test_truncated_or_invalid_model_output_is_not_a_completed_takeoff(self):
        model, _, replies = self.model()
        identity = model.identity()
        replies["/api/chat"]["done_reason"] = "length"
        with self.assertRaises(eq.TakeoffError):
            model.read(PNG, "plan", identity)
        replies["/api/chat"]["done_reason"] = "stop"
        for bbox in ([100, 200, 1001, 300], [True, 200, 300, 400], [100, 200, float("nan"), 300]):
            replies["/api/chat"]["message"]["content"] = json.dumps({"items": [
                {"tag": "EF-1", "source_text": "EF-1", "bbox": bbox}]})
            with self.assertRaises(eq.TakeoffError):
                model.read(PNG, "plan", identity)
        for name in ("qwen3-vl", "qwen3-vl:latest", "anything:cloud"):
            with self.assertRaises(eq.TakeoffError):
                eq.LocalVision(name, "d" * 64)


if __name__ == "__main__":
    unittest.main()
