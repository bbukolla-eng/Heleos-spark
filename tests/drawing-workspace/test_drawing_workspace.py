"""Real HTTP and subprocess boundary tests; the controller also uses real Rust CLI."""
import hashlib
import base64
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "drawing-workspace.py"
PDF = b"%PDF-1.7\nfixture accepted bytes\n%%EOF\n"
PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWoQAAAAASUVORK5CYII="
PNG = base64.b64decode(PNG_BASE64)

# A subprocess fixture, not a replacement parser or production dependency.
FIXTURE = r'''
import hashlib, json, pathlib, sys, time
a = sys.argv[1:]
def arg(name): return a[a.index(name)+1]
db = pathlib.Path(arg('--database'))
root = db.parent
log = root / 'calls.jsonl'
with log.open('a') as f: f.write(json.dumps(a)+'\n')
if (root/'timeout').exists(): time.sleep(2)
if (root/'failure').exists():
    print('/private/secret/path from CLI', file=sys.stderr); sys.exit(70)
if (root/'malformed').exists(): print('not json'); sys.exit(0)
statefile = root/'fixture-state.json'
state = json.loads(statefile.read_text()) if statefile.exists() else {'revisions': []}
result = {}
if a[:2] == ['db', 'migrate']:
    db.touch()
elif a[:2] == ['project', 'create']:
    state['project_id'] = arg('--id'); result = {'project_id': state['project_id']}
elif a[0] == 'ingest':
    data = pathlib.Path(a[1]).read_bytes()
    vault = pathlib.Path(arg('--vault'))
    if data.startswith(b'BAD'):
        (vault/'quarantined.pdf').write_bytes(data); sys.exit(21)
    digest = hashlib.sha256(data).hexdigest()
    key = 'objects/sha256/'+digest[:2]+'/'+digest[2:4]+'/'+digest
    target = vault/key; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    if digest not in state['revisions']: state['revisions'].append(digest)
    result = {'revision_id': digest, 'outcome': 'accepted_new', 'quarantine': None}
elif a[:2] == ['inspect', 'foundation']:
    if not pathlib.Path(arg('--vault')).is_dir(): sys.exit(23)
    result = {'project_id': state['project_id'], 'revision_ids': state['revisions'], 'sheets': []}
    for rev in state['revisions']:
        for index in range(2):
            result['sheets'].append({'revision_id': rev, 'sheet_id': hashlib.sha256((rev+str(index)).encode()).hexdigest(), 'index': index, 'width_micropoints': 612000000, 'height_micropoints': 792000000, 'rotation_degrees': 0, 'unit': 'pt', 'parent_content_sha256': rev, 'transform': {'m11': 1, 'm12': 0, 'm21': 0, 'm22': -1, 'tx_micropoints': 0, 'ty_micropoints': 792000000}})
elif a[0] == 'verify':
    if (root/'verify-failure').exists(): sys.exit(20)
    rev = arg('--revision'); key = 'objects/sha256/'+rev[:2]+'/'+rev[2:4]+'/'+rev
    original = {'sha256': rev, 'byte_length': len(b'%PDF-1.7\nfixture accepted bytes\n%%EOF\n'), 'media_type': 'application/pdf'}
    evidence = {'manifest': {'revision_id': rev, 'original': original}, 'original_vault_key': key, 'lineages': [{'project_id': state['project_id']}]}
    result = {'database': {'clean': True}, 'audit_chain': {'valid': True}, 'revision': {'evidence': evidence, 'original': {'kind': 'verified', 'digest': rev, 'byte_length': original['byte_length'], 'vault_key': key}, 'manifest': {'kind': 'verified'}}}
statefile.write_text(json.dumps(state))
print(json.dumps({'command': '.'.join(a[:2]), 'result': result}))
'''

# The renderer runs as a child process and records the bytes actually received
# on stdin. It emits one original tiny PNG, with explicit failure/limit modes.
RENDERER_FIXTURE = r'''
import base64, hashlib, json, pathlib, sys, time
root = pathlib.Path(sys.argv[1])
args = sys.argv[2:]
data = sys.stdin.buffer.read()
(root/'renderer-call.json').write_text(json.dumps({'args': args, 'sha256': hashlib.sha256(data).hexdigest(), 'byte_length': len(data)}))
if (root/'renderer-timeout').exists():
    time.sleep(2)
    (root/'renderer-survived-timeout').touch()
if (root/'renderer-failure').exists():
    print('/private/secret/renderer-detail', file=sys.stderr)
    sys.exit(4)
output = pathlib.Path(args[-1]).with_suffix('.png')
if (root/'renderer-invalid').exists():
    output.write_bytes(b'not a PNG image' * 4)
elif (root/'renderer-oversized').exists():
    with output.open('wb') as image:
        image.write(b'\x89PNG\r\n\x1a\n')
        image.truncate(20*1024*1024+1)
else:
    output.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWoQAAAAASUVORK5CYII='))
'''


class DrawingWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), "the CLI-backed drawing workspace service is missing")
        spec = importlib.util.spec_from_file_location("drawing_workspace", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = self.root / "fixture.py"
        self.fixture.write_text(FIXTURE)
        self.workspace_path = self.root / "workspace"
        self.start()

    def start(self):
        self.workspace = self.module.Workspace([sys.executable, str(self.fixture)], self.workspace_path, cli_timeout=0.4)
        self.server = self.module.create_server(self.workspace, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02})
        self.thread.start()
        self.addCleanup(self.stop)
        self.origin = "http://127.0.0.1:%s" % self.server.server_port
        self.prefix = "/%s/" % self.server.token

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.thread.join(timeout=3)
            self.server.server_close()
            self.workspace.close()
            self.server = None

    def request(self, path="api/state", method="GET", data=None, headers=None, absolute=False):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        merged = {"Origin": self.origin, "Content-Type": "application/pdf"}
        if headers:
            merged.update(headers)
        connection.request(method, path if absolute else self.prefix + path, body=data, headers=merged)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def import_pdf(self, name="Mechanical%20plan.pdf", data=PDF):
        return self.request("api/import?name=" + name, "POST", data)

    def enable_renderer(self):
        renderer = self.root / "renderer.py"
        renderer.write_text(RENDERER_FIXTURE)
        self.workspace.renderer = [sys.executable, str(renderer), str(self.root)]
        self.workspace.render_timeout = 0.4

    def page(self, index=0, revision=None):
        revision = revision or hashlib.sha256(PDF).hexdigest()
        return self.request("api/page/%s/%s.png" % (revision, index))

    def test_empty_state_and_import_duplicate_labels_persist(self):
        status, body, _ = self.request()
        self.assertEqual(status, 200)
        empty = json.loads(body)
        self.assertEqual(empty["documents"], [])
        self.assertEqual(empty["scale_status"], "unverified")
        self.assertEqual(self.import_pdf()[0], 200)
        status, body, _ = self.import_pdf()
        document, = json.loads(body)["documents"]
        self.assertEqual(document["name"], "Mechanical plan.pdf")
        self.assertEqual(document["page_count"], 2)
        self.assertEqual([s["index"] for s in document["sheets"]], [0, 1])
        for sheet in document["sheets"]:
            self.assertEqual(sheet["unit"], "pt")
            self.assertEqual(sheet["parent_content_sha256"], hashlib.sha256(PDF).hexdigest())
            self.assertEqual(sheet["transform"], {"m11": 1, "m12": 0, "m21": 0,
                                                "m22": -1, "tx_micropoints": 0,
                                                "ty_micropoints": 792000000})
        self.stop()
        self.start()
        self.assertEqual(json.loads(self.request()[1])["documents"], [document])

    def test_pdf_requires_membership_and_verification_then_returns_exact_bytes(self):
        self.assertEqual(self.request("api/pdf/" + "a" * 64)[0], 404)
        self.import_pdf()
        revision = hashlib.sha256(PDF).hexdigest()
        status, body, headers = self.request("api/pdf/" + revision)
        self.assertEqual((status, body), (200, PDF))
        self.assertEqual(headers["Content-Type"], "application/pdf")
        (self.workspace_path / "verify-failure").touch()
        self.assertEqual(self.request("api/pdf/" + revision)[0], 409)

    def test_changed_original_is_never_served_even_if_cli_fixture_claims_verified(self):
        self.import_pdf()
        revision = hashlib.sha256(PDF).hexdigest()
        path = self.workspace_path / "vault" / "objects" / "sha256" / revision[:2] / revision[2:4] / revision
        path.write_bytes(b"x" * len(PDF))
        status, body, _ = self.request("api/pdf/" + revision)
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "integrity")

    def test_quarantine_stays_error_and_evidence_is_retained(self):
        status, body, _ = self.import_pdf(data=b"BAD corrupt original")
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(body)["error"]["code"], "quarantined")
        self.assertEqual((self.workspace_path / "vault" / "quarantined.pdf").read_bytes(), b"BAD corrupt original")
        self.assertEqual(json.loads(self.request()[1])["documents"], [])

    def test_host_origin_token_traversal_and_media_type_boundaries(self):
        self.assertEqual(self.request(headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("/wrong/api/state", absolute=True)[0], 404)
        self.assertEqual(self.request("../workspace.json")[0], 404)
        self.assertEqual(self.request("%2e%2e/workspace.json")[0], 404)
        self.assertEqual(self.request("api/import?name=x.pdf", "POST", PDF, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("api/import?name=x.pdf", "POST", PDF, {"Origin": "null"})[0], 403)

    def test_oversize_and_invalid_name_rejected_before_cli_intake(self):
        status, _, _ = self.request("api/import?name=x.pdf", "POST", b"", {"Content-Length": str(256 * 1024 * 1024 + 1)})
        self.assertEqual(status, 413)
        self.assertEqual(self.import_pdf(name="..%2Fsecret.pdf")[0], 400)
        calls = (self.workspace_path / "calls.jsonl").read_text().splitlines()
        self.assertFalse(any(json.loads(line)[0] == "ingest" for line in calls))

    def test_safe_cli_errors_and_timeout_do_not_expose_output(self):
        for marker, expected in [("failure", 502), ("malformed", 502), ("timeout", 504)]:
            path = self.workspace_path / marker
            path.touch()
            status, body, _ = self.request()
            path.unlink()
            self.assertEqual(status, expected)
            self.assertIn("error", json.loads(body))
            self.assertNotIn(b"secret", body)
        self.assertEqual(self.request()[0], 200)

    def test_second_server_cannot_open_same_workspace(self):
        with self.assertRaises(self.module.WorkspaceError):
            self.module.Workspace([sys.executable, str(self.fixture)], self.workspace_path)

    def test_reopen_missing_vault_fails_instead_of_recreating_empty_state(self):
        self.stop()
        (self.workspace_path / "vault").rmdir()
        with self.assertRaises(self.module.WorkspaceError):
            self.module.Workspace([sys.executable, str(self.fixture)], self.workspace_path)

    def test_renderer_receives_verified_original_and_exact_selected_page(self):
        self.enable_renderer()
        self.assertEqual(self.import_pdf()[0], 200)
        state = json.loads(self.request()[1])
        self.assertEqual(state["preview_mode"], "image")
        self.assertEqual(state["scale_status"], "unverified")
        status, body, headers = self.page(index=1)
        self.assertEqual((status, body), (200, PNG))
        self.assertEqual(headers["Content-Type"], "image/png")
        call = json.loads((self.root / "renderer-call.json").read_text())
        self.assertEqual(call["sha256"], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(call["byte_length"], len(PDF))
        self.assertEqual(call["args"][:-1], ["-f", "2", "-l", "2", "-singlefile", "-cropbox", "-scale-to", "2000", "-png", "-"])
        output_prefix = Path(call["args"][-1])
        self.assertTrue(output_prefix.is_absolute())
        self.assertFalse(output_prefix.parent.exists(), "page rendering scratch must be cleaned before response")
        # Enabling raster preview never removes access to the original evidence.
        self.assertEqual(self.request("api/pdf/" + hashlib.sha256(PDF).hexdigest())[:2], (200, PDF))

    def test_unknown_page_and_revision_never_launch_renderer(self):
        self.enable_renderer()
        self.assertEqual(self.import_pdf()[0], 200)
        for index in (-1, 2, "not-a-page", "../0", 9999999):
            with self.subTest(index=index):
                self.assertEqual(self.page(index=index)[0], 404)
        self.assertEqual(self.page(revision="b" * 64)[0], 404)
        self.assertFalse((self.root / "renderer-call.json").exists())

    def test_renderer_failure_and_timeout_are_safe_errors(self):
        self.enable_renderer()
        self.assertEqual(self.import_pdf()[0], 200)
        for marker, expected_status, expected_code in [("renderer-failure", 422, "preview_failed"), ("renderer-timeout", 504, "preview_timeout")]:
            with self.subTest(marker=marker):
                path = self.root / marker
                path.touch()
                status, body, headers = self.page()
                path.unlink()
                self.assertEqual(status, expected_status)
                self.assertEqual(json.loads(body)["error"]["code"], expected_code)
                self.assertNotIn(b"secret", body)
                self.assertNotEqual(headers["Content-Type"], "image/png")
                call = json.loads((self.root / "renderer-call.json").read_text())
                self.assertFalse(Path(call["args"][-1]).parent.exists())
        self.assertEqual(self.page()[:2], (200, PNG), "renderer failure must not poison later requests")
        self.assertFalse((self.root / "renderer-survived-timeout").exists())

    def test_invalid_or_oversized_renderer_output_is_never_served(self):
        self.enable_renderer()
        self.assertEqual(self.import_pdf()[0], 200)
        for marker in ("renderer-invalid", "renderer-oversized"):
            with self.subTest(marker=marker):
                path = self.root / marker
                path.touch()
                status, body, headers = self.page()
                path.unlink()
                self.assertEqual(status, 422)
                self.assertEqual(json.loads(body)["error"]["code"], "preview_failed")
                self.assertNotEqual(headers["Content-Type"], "image/png")

    def test_corrupted_original_is_not_given_to_renderer(self):
        self.enable_renderer()
        self.assertEqual(self.import_pdf()[0], 200)
        revision = hashlib.sha256(PDF).hexdigest()
        path = self.workspace_path / "vault" / "objects" / "sha256" / revision[:2] / revision[2:4] / revision
        path.write_bytes(b"x" * len(PDF))
        status, body, _ = self.page()
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "integrity")
        self.assertFalse((self.root / "renderer-call.json").exists())

    def test_without_renderer_original_pdf_remains_available(self):
        self.assertEqual(self.import_pdf()[0], 200)
        self.assertEqual(json.loads(self.request()[1])["preview_mode"], "pdf")
        status, body, _ = self.page()
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"]["code"], "preview_unavailable")
        self.assertEqual(self.request("api/pdf/" + hashlib.sha256(PDF).hexdigest())[:2], (200, PDF))


if __name__ == "__main__":
    unittest.main()
