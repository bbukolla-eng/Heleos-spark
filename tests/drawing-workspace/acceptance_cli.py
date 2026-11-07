#!/usr/bin/env python3
"""Exercise the complete local workspace with an explicitly supplied real CLI.

python3 tests/drawing-workspace/acceptance_cli.py --heleos /path/to/heleos
Uses temporary synthetic data; never downloads or builds dependencies.
"""

import argparse
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import queue
import struct
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]


class LiveServer:
    def __init__(self, binary, workspace, renderer=None):
        arguments = [sys.executable, str(ROOT / "scripts/drawing-workspace.py"),
                     "--heleos", str(binary), "--workspace", str(workspace), "--port", "0"]
        if renderer is not None:
            arguments += ["--pdftoppm", str(renderer)]
        self.process = subprocess.Popen(
            arguments,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        ready = queue.Queue()

        def collect():
            for line in self.process.stdout:
                if line.startswith("WORKSPACE_URL="):
                    ready.put(line.strip().partition("=")[2])
                    return
            ready.put(None)

        threading.Thread(target=collect, daemon=True).start()
        try:
            url = ready.get(timeout=30)
            if not url:
                _, diagnostic = self.process.communicate(timeout=10)
                raise RuntimeError("Workspace exited before declaring its local URL: " + diagnostic.strip())
            self.url = urlsplit(url)
        except Exception:
            self.stop()
            raise

    def request(self, method, route, body=None, headers=None):
        connection = http.client.HTTPConnection(self.url.hostname, self.url.port, timeout=180)
        request_headers = dict(headers or {})
        if method == "POST":
            request_headers.setdefault("Origin", "http://{}:{}".format(self.url.hostname, self.url.port))
        try:
            connection.request(method, self.url.path + route, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate(timeout=10)


def run(binary, renderer=None):
    spec = importlib.util.spec_from_file_location("workspace_demo", ROOT / "scripts/create-drawing-workspace-demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    pdf = demo.build_pdf()
    digest = hashlib.sha256(pdf).hexdigest()
    checks = []
    with tempfile.TemporaryDirectory(prefix="heleos-workspace-acceptance-") as temporary:
        workspace = Path(temporary).resolve() / "workspace"
        server = LiveServer(binary, workspace, renderer)
        try:
            status, _, raw = server.request("GET", "api/state")
            state = json.loads(raw)
            assert status == 200 and state["documents"] == [], (status, raw)
            checks.append("empty_workspace_uses_real_core")
            status, _, raw = server.request("POST", "api/import?name=mechanical-demo.pdf", pdf,
                                            {"Content-Type": "application/pdf"})
            assert status == 200, (status, raw)
            state = json.loads(raw)
            assert len(state["documents"]) == 1, state
            document = state["documents"][0]
            assert document["revision_id"] == digest and document["page_count"] == 2, document
            pages = document["sheets"]
            assert [(p["index"], p["width_micropoints"], p["height_micropoints"], p["rotation_degrees"])
                    for p in pages] == [(0, 2592000000, 1728000000, 0), (1, 1296000000, 1728000000, 90)], pages
            assert state["scale_status"] == "unverified", state
            checks.append("real_pdf_import_exact_two_page_geometry_and_rotation")
            status, headers, original = server.request("GET", "api/pdf/" + digest)
            assert status == 200 and original == pdf, (status, len(original))
            assert headers.get("Content-Type", "").startswith("application/pdf"), headers
            checks.append("preview_matches_original_bytes")
            if renderer is not None:
                assert state["preview_mode"] == "image", state
                for index, expected_width, expected_height in [(0, 2000, 1334), (1, 1500, 2000)]:
                    status, headers, rendered = server.request("GET", "api/page/{}/{}.png".format(digest, index))
                    assert status == 200 and rendered.startswith(b"\x89PNG\r\n\x1a\n"), (status, rendered[:100])
                    width, height = struct.unpack(">II", rendered[16:24])
                    assert (width, height) == (expected_width, expected_height), (index, width, height)
                status, _, raw = server.request("GET", "api/page/{}/2.png".format(digest))
                assert status == 404 and "error" in json.loads(raw), (status, raw)
                checks.append("real_renderer_shows_both_pages_at_bounded_size_and_rejects_unknown_page")
            status, _, raw = server.request("POST", "api/import?name=duplicate.pdf", pdf,
                                            {"Content-Type": "application/pdf"})
            duplicate = json.loads(raw)
            assert status == 200 and len(duplicate["documents"]) == 1, (status, duplicate)
            assert duplicate["documents"][0]["page_count"] == 2, duplicate
            assert [p["sheet_id"] for p in duplicate["documents"][0]["sheets"]] == [p["sheet_id"] for p in pages]
            checks.append("duplicate_import_preserves_single_revision_and_sheet_identity")
            status, _, raw = server.request("GET", "api/pdf/" + "0" * 64)
            assert status == 404 and "error" in json.loads(raw), (status, raw)
            checks.append("foreign_revision_is_not_served")
            status, _, raw = server.request("POST", "api/import?name=broken.pdf", b"%PDF-1.7\nnot a valid document",
                                            {"Content-Type": "application/pdf"})
            assert status >= 400 and "error" in json.loads(raw), (status, raw)
            status, _, raw = server.request("GET", "api/state")
            assert status == 200 and len(json.loads(raw)["documents"]) == 1, (status, raw)
            checks.append("real_core_quarantine_is_error_and_never_a_drawing")
            status, _, raw = server.request("POST", "api/import?name=foreign.pdf", pdf,
                                            {"Content-Type": "application/pdf", "Origin": "https://foreign.invalid"})
            assert status == 403, (status, raw)
            checks.append("foreign_origin_write_is_denied")
            project = state["project"]["id"]
        finally:
            server.stop()
        server = LiveServer(binary, workspace, renderer)
        try:
            status, _, raw = server.request("GET", "api/state")
            reopened = json.loads(raw)
            assert status == 200 and reopened["project"]["id"] == project, (status, reopened)
            assert len(reopened["documents"]) == 1 and reopened["documents"][0]["revision_id"] == digest, reopened
            checks.append("restart_reopens_same_project_and_drawing")
            original_path = workspace / "vault" / "objects" / "sha256" / digest[:2] / digest[2:4] / digest
            original_path.write_bytes(b"corruption in disposable test vault")
            status, _, raw = server.request("GET", "api/pdf/" + digest)
            assert status >= 400 and "error" in json.loads(raw), (status, raw)
            checks.append("corrupt_vault_bytes_are_not_previewed")
        finally:
            server.stop()
    return {"status": "PASS", "real_cli_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "synthetic_pdf_sha256": digest, "checks": checks, "native_windows_execution": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--heleos", type=Path, required=True)
    parser.add_argument("--pdftoppm", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(args.heleos.resolve(strict=True), args.pdftoppm.resolve(strict=True) if args.pdftoppm else None)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as output:
            output.write(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
