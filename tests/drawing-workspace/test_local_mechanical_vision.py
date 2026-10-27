"""PUBLIC original image/transport fixtures; no live model or socket calls."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vision = load("local_mechanical_vision")
equipment = load("equipment_takeoff")


def png(width=1, height=1):
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b""))


PNG = png()


def obj(category="equipment", label="AHU-01"):
    return {"category": category, "label": label, "source_text": "Original visible fixture " + label,
            "bbox": [100, 200, 400, 500]}


def completion(objects=None, unreadable=False):
    return {"model": "fixture-vision:1", "done": True, "done_reason": "stop",
            "created_at": "2026-09-11T00:00:00Z", "total_duration": 125000,
            "load_duration": 100, "prompt_eval_count": 5, "prompt_eval_duration": 200,
            "eval_count": 3, "eval_duration": 1000,
            "message": {"role": "assistant", "thinking": "", "content": json.dumps({
                "objects": [obj()] if objects is None else objects, "unreadable": unreadable})}}


class Transport:
    def __init__(self):
        self.replies = {
            "/api/version": {"version": "fixture-1"},
            "/api/tags": {"models": [{"name": "fixture-vision:1", "digest": "d" * 64,
                                      "details": {"format": "gguf"}}]},
            "/api/show": {"capabilities": ["vision"], "model_info": {"architecture": "fixture"}},
            "/api/chat": completion(),
        }
        self.calls, self.connections = [], []
        self.status = 200
        self.length = None
        self.entered, self.release = threading.Event(), threading.Event()
        self.block_path = None
        self.on_read = None

    def __call__(self, host, port, timeout):
        transport = self

        class Connection:
            sock = None
            closed = False

            def request(self, method, path, body=None, headers=None):
                self.path = path
                transport.calls.append({"host": host, "port": port, "timeout": timeout,
                                        "method": method, "path": path,
                                        "body": json.loads(body) if body is not None else None,
                                        "headers": headers})

            def getresponse(self):
                class Response:
                    status = transport.status
                    length = transport.length

                    def read(self, maximum):
                        if connection.path == transport.block_path:
                            transport.entered.set()
                            if not transport.release.wait(5):
                                raise AssertionError("Fixture was not released")
                        reply = transport.replies[connection.path]
                        if callable(reply):
                            reply = reply()
                        if isinstance(reply, Exception):
                            raise reply
                        data = reply if isinstance(reply, bytes) else json.dumps(reply, indent=2).encode()
                        if transport.on_read:
                            transport.on_read(connection.path)
                        return data[:maximum]
                return Response()

            def close(self):
                self.closed = True
                if self.path == transport.block_path:
                    transport.release.set()

        connection = Connection()
        transport.connections.append(connection)
        return connection


class MechanicalVisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name) / "original-runtime-fixture"
        self.runtime.write_bytes(b"PUBLIC original executable identity fixture")
        self.transport = Transport()
        self.local = equipment.LocalVision("fixture-vision:1", "d" * 64)
        self.adapter = vision.MechanicalVision(self.local, self.runtime, connection_factory=self.transport)
        self.no_network = mock.patch.object(vision.http.client, "HTTPConnection",
                                            side_effect=AssertionError("No real connections in worker checks"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def error(self, code, callback):
        with self.assertRaises(vision.VisionError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        self.assertTrue(raised.exception.message)
        return raised.exception

    def test_construction_has_no_identity_io_and_missing_runtime_is_clear(self):
        with mock.patch.object(vision.shutil, "which", return_value=None), mock.patch.object(
                Path, "open", side_effect=AssertionError("Construction must not read runtime")):
            adapter = vision.MechanicalVision(self.local)
        self.error("model_config", adapter.identity)
        self.error("model_config", vision.MechanicalVision(self.local, "").identity)
        self.error("model_config", vision.MechanicalVision(self.local, self.runtime.parent / "missing").identity)
        self.assertEqual(self.transport.calls, [])
        self.assertNotIn("request", self.local.__dict__)

    def test_identity_pins_actual_runtime_prompt_schema_and_local_model(self):
        identity = self.adapter.identity()
        self.assertEqual(identity, {
            "kind": "local_mechanical_vision", "model": "fixture-vision:1", "model_sha256": "d" * 64,
            "runtime": "ollama", "runtime_version": "fixture-1",
            "runtime_sha256": hashlib.sha256(self.runtime.read_bytes()).hexdigest(),
            "prompt_sha256": hashlib.sha256(vision.PROMPT.encode()).hexdigest(),
            "schema_sha256": hashlib.sha256(vision._packed(vision.SCHEMA)).hexdigest(),
            "image_max_dimension": 2000, "temperature": 0, "seed": 0})
        self.assertNotEqual(identity["prompt_sha256"], hashlib.sha256(equipment.VISION_PROMPT.encode()).hexdigest())
        self.assertEqual([call["path"] for call in self.transport.calls],
                         ["/api/version", "/api/tags", "/api/show", "/api/version"])
        self.assertTrue(all(call["host"] == "127.0.0.1" and call["timeout"] == 4 for call in self.transport.calls))
        self.assertTrue(all(connection.closed for connection in self.transport.connections))

    def test_identity_is_exact_and_registered_page_roles_are_supported(self):
        identity = self.adapter.identity()
        for field, value in (("temperature", False), ("seed", False), ("image_max_dimension", 2000.0),
                             ("runtime_sha256", "e" * 64), ("unknown", "extra")):
            changed = dict(identity)
            changed[field] = value
            self.transport.calls.clear()
            self.error("model_changed", lambda: self.adapter.read(PNG, "plan", changed))
            self.assertNotIn("/api/chat", [call["path"] for call in self.transport.calls])
        for role in ("plan", "schedule", "specification", "detail", "riser", "legend", "addendum", "excluded"):
            self.adapter.read(PNG, role, identity)
        self.transport.replies["/api/show"]["capabilities"] = "vision"
        self.error("model_response", self.adapter.identity)

    def test_all_categories_duplicates_exact_pixels_and_complete_raw_response(self):
        categories = ("equipment", "air_devices", "ductwork", "piping", "fittings",
                      "accessories", "controls", "insulation", "demolition")
        objects = [obj(category, " Visible " + category + "  tag ") for category in categories]
        objects.append(copy.deepcopy(objects[0]))
        raw = json.dumps(completion(objects), ensure_ascii=False, indent=3).encode() + b"\n\n"
        self.transport.replies["/api/chat"] = raw
        identity = self.adapter.identity()
        result, retained = self.adapter.read(PNG, "plan", identity)
        self.assertEqual(retained, raw)
        self.assertEqual(set(result), {"objects", "unreadable"})
        self.assertFalse(result["unreadable"])
        self.assertEqual([item["category"] for item in result["objects"]], list(categories) + ["equipment"])
        self.assertEqual([item["label"] for item in result["objects"]], [item["label"] for item in objects])
        self.assertEqual(len({item["id"] for item in result["objects"]}), 10)
        self.assertTrue(all(set(item) == {"id", "category", "label", "bbox"} for item in result["objects"]))
        self.assertEqual(result["objects"][0]["bbox"], [0.1, 0.2, 0.4, 0.5])
        self.assertEqual(self.adapter.read(PNG, "plan", identity), (result, raw))
        chat = next(call["body"] for call in self.transport.calls if call["path"] == "/api/chat")
        self.assertEqual(base64.b64decode(chat["messages"][1]["images"][0]), PNG)
        self.assertEqual(chat["options"], {"temperature": 0, "seed": 0, "num_predict": 8192})
        self.assertFalse(chat["stream"])
        self.assertFalse(chat["think"])
        self.assertNotIn("tools", chat)
        self.assertNotIn("objects", chat["messages"][1])
        self.assertIn("untrusted data", chat["messages"][0]["content"])

    def test_unreadable_and_readable_empty_pages_are_explicit(self):
        identity = self.adapter.identity()
        for unreadable in (True, False):
            self.transport.replies["/api/chat"] = completion([], unreadable)
            self.assertEqual(self.adapter.read(PNG, "plan", identity)[0],
                             {"objects": [], "unreadable": unreadable})
        self.transport.replies["/api/chat"] = completion([obj()], True)
        self.error("model_output", lambda: self.adapter.read(PNG, "plan", identity))

    def test_model_identity_rejections_precede_image_submission(self):
        identity = self.adapter.identity()
        original = copy.deepcopy(self.transport.replies)
        changes = [
            ("/api/show", "remote_host", "cloud", "model_not_local_vision"),
            ("/api/show", "remote_model", "remote", "model_not_local_vision"),
            ("/api/show", "capabilities", [], "model_not_local_vision"),
            ("/api/show", "model_info", None, "model_not_local_vision"),
        ]
        for path, key, value, code in changes:
            with self.subTest(key=key):
                self.transport.replies = copy.deepcopy(original)
                self.transport.replies[path][key] = value
                self.transport.calls.clear()
                self.error(code, lambda: self.adapter.read(PNG, "plan", identity))
                self.assertNotIn("/api/chat", [call["path"] for call in self.transport.calls])
        for field, value, code in (("digest", "e" * 64, "model_changed"),
                                   ("details", {"format": "remote"}, "model_not_local_vision")):
            self.transport.replies = copy.deepcopy(original)
            self.transport.replies["/api/tags"]["models"][0][field] = value
            self.error(code, self.adapter.identity)

    def test_runtime_or_model_drift_before_and_after_image_is_rejected(self):
        identity = self.adapter.identity()
        self.runtime.write_bytes(b"changed configured runtime")
        self.transport.calls.clear()
        self.error("model_changed", lambda: self.adapter.read(PNG, "plan", identity))
        self.assertNotIn("/api/chat", [call["path"] for call in self.transport.calls])
        self.runtime.write_bytes(b"PUBLIC original executable identity fixture")
        raw = json.dumps(completion()).encode()
        self.transport.replies["/api/chat"] = raw
        def changed(path):
            if path == "/api/chat":
                self.transport.replies["/api/version"] = {"version": "fixture-2"}
        self.transport.on_read = changed
        error = self.error("model_changed", lambda: self.adapter.read(PNG, "plan", identity))
        self.assertEqual(error.raw_response, raw)

    def test_identity_detects_runtime_file_and_version_change_during_check(self):
        versions = iter([{"version": "fixture-1"}, {"version": "fixture-2"}])
        self.transport.replies["/api/version"] = lambda: next(versions)
        self.error("model_changed", self.adapter.identity)
        self.transport.replies["/api/version"] = {"version": "fixture-1"}
        def changed(path):
            if path == "/api/show":
                self.runtime.write_bytes(b"new runtime during identity")
        self.transport.on_read = changed
        self.error("model_changed", self.adapter.identity)

    def test_strict_completion_and_message_fields(self):
        identity = self.adapter.identity()
        variants = []
        for key, value in (("done", False), ("done_reason", "length"), ("model", "other:1"),
                           ("quantity", 10), ("total_duration", float("inf"))):
            response = completion()
            response[key] = value
            variants.append(response)
        for key, value in (("tool_calls", []), ("tool_calls", [{"function": "read_file"}]),
                           ("role", "tool"), ("quantity", 2)):
            response = completion()
            response["message"][key] = value
            variants.append(response)
        response = completion()
        del response["message"]["role"]
        variants.append(response)
        for response in variants:
            with self.subTest(response=response):
                self.transport.replies["/api/chat"] = response
                with self.assertRaises(vision.VisionError) as raised:
                    self.adapter.read(PNG, "plan", identity)
                self.assertIn(raised.exception.code, {"model_output", "model_response"})
                self.assertEqual(raised.exception.raw_response, json.dumps(response, indent=2).encode())

    def test_strict_inner_schema_rejects_ids_quantities_categories_and_bad_boxes(self):
        identity = self.adapter.identity()
        variants = []
        for key, value in (("id", "model-id"), ("quantity", 5), ("category", "architecture"),
                           ("label", " "), ("label", "\x00tag"), ("source_text", 10)):
            item = obj()
            item[key] = value
            variants.append({"objects": [item], "unreadable": False})
        for box in ([0, 0, 0, 1], [0, 2, 3, 1], [0, 0, 1001, 1000],
                    [True, 0, 2, 1], [0, 0, float("nan"), 1], [0, 0, 1],
                    [0, 0, 1e-323, 1e-323], [0, 0, 10 ** 500, 1]):
            item = obj()
            item["bbox"] = box
            variants.append({"objects": [item], "unreadable": False})
        variants.extend([{"objects": [], "unreadable": 1}, {"objects": [], "unreadable": False, "approval": True},
                         {"objects": [obj()] * 251, "unreadable": False}, {"objects": []}])
        for value in variants:
            with self.subTest(value=str(value)[:160]):
                response = completion()
                response["message"]["content"] = json.dumps(value)
                self.transport.replies["/api/chat"] = response
                self.error("model_output", lambda: self.adapter.read(PNG, "plan", identity))

    def test_duplicate_keys_nonfinite_truncation_and_nested_json_are_rejected(self):
        identity = self.adapter.identity()
        for raw in (b'{"done": true, "done": false}', b'{"x": 1e400}', b'{"x": NaN}',
                    b'{"x": ', b'\xff', b'{"x":' + b'[' * 30 + b'0' + b']' * 30 + b'}'):
            with self.subTest(raw=raw[:100]):
                self.transport.replies["/api/chat"] = raw
                error = self.error("model_response", lambda: self.adapter.read(PNG, "plan", identity))
                self.assertEqual(error.raw_response, raw)
        for content in ('{"objects":[],"objects":[],"unreadable":false}',
                        '{"objects":[],"unreadable":false} trailing',
                        '{"objects":[{"category":"equipment","label":"\\ud800",'
                        '"source_text":"","bbox":[0,0,1,1]}],"unreadable":false}'):
            response = completion()
            response["message"]["content"] = content
            self.transport.replies["/api/chat"] = response
            self.error("model_output", lambda: self.adapter.read(PNG, "plan", identity))

    def test_invalid_image_or_role_never_contacts_runtime(self):
        for image in (b"%PDF-original fixture", PNG[:30], PNG + b"tail", png(2001, 1),
                      png(0, 1), PNG[:-5] + b"wrong", bytearray(PNG)):
            with self.subTest(image=image[:12]):
                self.error("image_invalid", lambda: self.adapter.read(image, "plan", {}))
        self.error("image_invalid", lambda: self.adapter.read(PNG, "ignore policy", {}))
        self.assertEqual(self.transport.calls, [])
        with mock.patch.object(vision, "MAX_IMAGE_BYTES", 10):
            self.error("image_invalid", lambda: self.adapter.read(PNG, "plan", {}))

    def test_io_status_size_and_malformed_identity_errors_are_stable(self):
        self.transport.replies["/api/version"] = PermissionError(1, "fixture denied")
        self.error("model_unavailable", self.adapter.identity)
        self.transport.replies["/api/version"] = {"version": "fixture-1"}
        for value in (None, {}, [], "", "x" * 101):
            self.transport.replies["/api/version"] = {"version": value}
            self.error("model_response", self.adapter.identity)
        self.transport.replies["/api/version"] = {"version": "fixture-1"}
        self.transport.status = 302
        self.error("model_response", self.adapter.identity)
        self.assertTrue(all(connection.closed for connection in self.transport.connections))
        self.transport.status = 200
        with mock.patch.object(vision, "MAX_RESPONSE_BYTES", 10):
            error = self.error("model_response", self.adapter.identity)
            self.assertEqual(len(error.raw_response), 11)

    def test_partial_http_body_is_retained_and_truncation_is_rejected(self):
        identity = self.adapter.identity()
        partial = b'{"model":"fixture-vision:1","done":'
        self.transport.replies["/api/chat"] = vision.http.client.IncompleteRead(partial, 100)
        error = self.error("model_response", lambda: self.adapter.read(PNG, "plan", identity))
        self.assertEqual(error.raw_response, partial)
        self.transport.replies["/api/chat"] = completion()
        self.transport.length = 100000
        error = self.error("model_response", self.adapter.identity)
        self.assertEqual(error.raw_response, json.dumps({"version": "fixture-1"}, indent=2).encode())

    def test_identity_and_read_cancellation_close_connection_and_require_explicit_reset(self):
        for path in ("/api/version", "/api/chat"):
            with self.subTest(path=path):
                self.adapter.reset_cancel()
                identity = self.adapter.identity()
                self.transport.block_path = path
                self.transport.entered.clear()
                self.transport.release.clear()
                errors = []
                def operation():
                    try:
                        self.adapter.read(PNG, "plan", identity)
                    except Exception as error:
                        errors.append(error)
                thread = threading.Thread(target=operation)
                thread.start()
                self.assertTrue(self.transport.entered.wait(2))
                self.error("model_busy", self.adapter.identity)
                self.error("model_busy", self.adapter.reset_cancel)
                self.adapter.cancel()
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], vision.VisionError)
                self.assertEqual(errors[0].code, "cancelled")
                self.assertTrue(self.transport.connections[-1].closed)
                if path == "/api/chat":
                    self.assertEqual(errors[0].raw_response, json.dumps(completion(), indent=2).encode())
                count = len(self.transport.calls)
                self.error("cancelled", self.adapter.identity)
                self.error("cancelled", lambda: self.adapter.read(PNG, "plan", identity))
                self.assertEqual(len(self.transport.calls), count)
                self.transport.block_path = None
                self.adapter.reset_cancel()
                self.assertEqual(self.adapter.read(PNG, "plan", identity)[0]["objects"][0]["label"], "AHU-01")

    def test_chat_time_is_bounded_even_when_existing_timeout_is_larger(self):
        self.local.timeout = 10000
        adapter = vision.MechanicalVision(self.local, self.runtime, connection_factory=self.transport)
        adapter.read(PNG, "plan", adapter.identity())
        self.assertEqual(next(call["timeout"] for call in self.transport.calls if call["path"] == "/api/chat"), 180)


if __name__ == "__main__":
    unittest.main()
