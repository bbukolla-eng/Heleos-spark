#!/usr/bin/env python3
"""Local drawing browser backed by the Foundation CLI; Python 3.9+ stdlib only.

The JSON file holds display labels and the selected project ID, never evidence.
All Foundation mutations and inspections go through the supplied CLI. The server
handles one request at a time and also holds an OS lock for its whole lifetime.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit
import uuid

MAX_UPLOAD = 256 * 1024 * 1024
CLI_TIMEOUT = 150
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
ASSETS = Path(__file__).resolve().parents[1] / "apps" / "drawing-workspace"
ASSET_TYPES = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "styles.css": "text/css; charset=utf-8"}


class WorkspaceError(Exception):
    def __init__(self, code, message, status=500):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def integrity():
    return WorkspaceError("integrity", "The drawing evidence could not be verified. Its original bytes were not served.", 409)


def display_name(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 240
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or "/" in value or "\\" in value or value in (".", "..")):
        raise WorkspaceError("invalid_name", "Choose a PDF with a simple file name of at most 240 characters.", 400)
    return value.strip()


class Workspace:
    def __init__(self, cli, root, cli_timeout=CLI_TIMEOUT, renderer=None, render_timeout=30):
        self.cli = list(cli)
        self.renderer = list(renderer) if renderer else None
        self.render_timeout = render_timeout
        self.root = Path(root).expanduser().resolve()
        self.cli_timeout = cli_timeout
        self.operation_lock = threading.RLock()
        self.lock_file = None
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.database = self.root / "foundation.sqlite3"
        self.vault = self.root / "vault"
        self.metadata_file = self.root / "workspace.json"
        try:
            self._lock()
            if self.metadata_file.exists():
                self.metadata = json.loads(self.metadata_file.read_text(encoding="utf-8"))
                if self.metadata.get("version") != 1:
                    raise ValueError("unsupported metadata")
                uuid.UUID(self.metadata["project"]["id"])
                if not isinstance(self.metadata["names"], dict):
                    raise ValueError("invalid names")
                for revision, name in self.metadata["names"].items():
                    if not DIGEST.fullmatch(revision):
                        raise ValueError("invalid revision")
                    display_name(name)
                if not self.database.is_file() or not self.vault.is_dir():
                    raise WorkspaceError("workspace_incomplete", "The existing workspace database or vault is missing. Restore the original workspace before reopening it.", 409)
            else:
                # Never adopt an arbitrary populated directory or overwrite a
                # partial previous initialization. Its files remain untouched.
                if any(path.name != ".drawing-workspace.lock" for path in self.root.iterdir()):
                    raise WorkspaceError("workspace_not_empty", "Use a new empty directory or an existing drawing workspace.", 409)
                project_id = str(uuid.uuid4())
                self.run_cli(["db", "migrate", "--database", str(self.database)])
                self.vault.mkdir(mode=0o700)
                result = self.run_cli(["project", "create", "--database", str(self.database), "--id", project_id, "--name", "Drawing workspace", "--actor", "drawing-workspace"])
                if result.get("project_id") != project_id:
                    raise integrity()
                self.metadata = {"version": 1, "project": {"id": project_id, "name": "Drawing workspace"}, "names": {}}
                self._save_metadata()
            # Reopen is verified against the core, not trusted display metadata.
            self.state()
        except WorkspaceError:
            self.close()
            raise
        except (OSError, ValueError, KeyError, TypeError):
            self.close()
            raise WorkspaceError("workspace_invalid", "The workspace could not be opened. Check its local files and permissions.", 409) from None

    def _lock(self):
        self.lock_file = (self.root / ".drawing-workspace.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.lock_file.seek(0, os.SEEK_END)
                if self.lock_file.tell() == 0:
                    self.lock_file.write(b"\0")
                    self.lock_file.flush()
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            self.lock_file = None
            raise WorkspaceError("workspace_busy", "This drawing workspace is already open in another server.", 409) from None

    def close(self):
        if self.lock_file is not None:
            # Closing the handle releases its OS lock, including after a crash.
            self.lock_file.close()
            self.lock_file = None

    def _save_metadata(self):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, prefix=".labels-", delete=False) as output:
                temporary = Path(output.name)
                json.dump(self.metadata, output, ensure_ascii=False, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(str(temporary), str(self.metadata_file))
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def run_cli(self, arguments):
        with self.operation_lock:
            try:
                # No shell; subprocess.run kills and reaps the child on timeout
                # or interruption. stderr is deliberately never shown or logged.
                result = subprocess.run(self.cli + arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self.cli_timeout, check=False)
            except subprocess.TimeoutExpired:
                raise WorkspaceError("cli_timeout", "The Foundation operation timed out. Check the workspace state before retrying the import.", 504) from None
            except OSError:
                raise WorkspaceError("cli_unavailable", "The Foundation executable could not be started.", 502) from None
            if result.returncode == 21:
                raise WorkspaceError("quarantined", "Foundation quarantined this PDF. Its evidence is retained locally, and it was not accepted as a drawing.", 422)
            if result.returncode == 20:
                raise integrity()
            if result.returncode:
                raise WorkspaceError("cli_failed", "The Foundation operation failed. The workspace was not reported as successful.", 502)
            try:
                envelope = json.loads(result.stdout)
                data = envelope["result"]
                if not isinstance(data, dict):
                    raise ValueError("not an object")
                return data
            except (ValueError, KeyError, TypeError):
                raise WorkspaceError("cli_response", "Foundation returned an unreadable result.", 502) from None

    def _foundation_args(self):
        return ["--database", str(self.database), "--vault", str(self.vault)]

    def state(self):
        with self.operation_lock:
            result = self.run_cli(["inspect", "foundation", "--project", self.metadata["project"]["id"]] + self._foundation_args())
            try:
                if result["project_id"] != self.metadata["project"]["id"]:
                    raise ValueError("wrong project")
                revisions = result["revision_ids"]
                if not isinstance(revisions, list) or len(set(revisions)) != len(revisions):
                    raise ValueError("invalid revisions")
                grouped = {}
                for revision in revisions:
                    if not isinstance(revision, str) or not DIGEST.fullmatch(revision):
                        raise ValueError("invalid revision")
                    grouped[revision] = []
                for sheet in result["sheets"]:
                    revision = sheet["revision_id"]
                    fields = ("sheet_id", "index", "width_micropoints", "height_micropoints", "rotation_degrees")
                    projected = {field: sheet[field] for field in fields}
                    if not isinstance(projected["sheet_id"], str) or not DIGEST.fullmatch(projected["sheet_id"]):
                        raise ValueError("invalid sheet")
                    if any(type(projected[field]) is not int for field in fields[1:]):
                        raise ValueError("invalid sheet dimensions")
                    if projected["width_micropoints"] <= 0 or projected["height_micropoints"] <= 0 or projected["rotation_degrees"] not in (0, 90, 180, 270):
                        raise ValueError("invalid dimensions")
                    grouped[revision].append(projected)
                documents = []
                for revision in sorted(grouped):
                    sheets = sorted(grouped[revision], key=lambda sheet: sheet["index"])
                    if not sheets or [sheet["index"] for sheet in sheets] != list(range(len(sheets))):
                        raise ValueError("invalid sheet sequence")
                    documents.append({"revision_id": revision, "name": self.metadata["names"].get(revision, "Drawing " + revision[:12] + ".pdf"), "page_count": len(sheets), "sheets": sheets})
                return {"project": dict(self.metadata["project"]), "documents": documents, "scale_status": "unverified", "preview_mode": "image" if self.renderer else "pdf"}
            except (ValueError, KeyError, TypeError):
                raise WorkspaceError("cli_response", "Foundation returned inconsistent drawing metadata.", 502) from None

    def import_pdf(self, stream, length, name):
        name = display_name(name)
        with self.operation_lock:
            # Close before invoking the CLI so the PDF can be opened on Windows.
            with tempfile.TemporaryDirectory(prefix="drawing-intake-") as directory:
                intake = Path(directory).resolve() / "original.pdf"
                digest = hashlib.sha256()
                with intake.open("xb") as output:
                    remaining = length
                    while remaining:
                        block = stream.read(min(1024 * 1024, remaining))
                        if not block:
                            raise WorkspaceError("incomplete_upload", "The PDF upload ended before all bytes arrived.", 400)
                        output.write(block)
                        digest.update(block)
                        remaining -= len(block)
                revision = digest.hexdigest()
                receipt = self.run_cli(["ingest", str(intake), "--project", self.metadata["project"]["id"], "--idempotency-key", str(uuid.uuid4()), "--actor", "drawing-workspace"] + self._foundation_args())
                if receipt.get("quarantine") is not None or str(receipt.get("outcome", "")).startswith("quarantined"):
                    raise WorkspaceError("quarantined", "Foundation quarantined this PDF. It was not accepted as a drawing.", 422)
                if receipt.get("revision_id") != revision or receipt.get("outcome") not in ("accepted_new", "accepted_duplicate", "idempotent_replay"):
                    raise WorkspaceError("import_incomplete", "Foundation did not confirm an accepted drawing. Check its local job state before retrying.", 409)
                state = self.state()
                if revision not in {document["revision_id"] for document in state["documents"]}:
                    raise integrity()
                self.metadata["names"].setdefault(revision, name)
                self._save_metadata()
                for document in state["documents"]:
                    if document["revision_id"] == revision:
                        document["name"] = self.metadata["names"][revision]
                return state

    def verified_pdf(self, revision):
        """Return a private, verified byte snapshot; caller must close it."""
        with self.operation_lock:
            if not DIGEST.fullmatch(revision):
                raise WorkspaceError("not_found", "The drawing was not found in this project.", 404)
            if revision not in {document["revision_id"] for document in self.state()["documents"]}:
                raise WorkspaceError("not_found", "The drawing was not found in this project.", 404)
            result = self.run_cli(["verify", "--revision", revision] + self._foundation_args())
            snapshot = None
            try:
                verified = result["revision"]
                evidence = verified["evidence"]
                manifest = evidence["manifest"]
                original = manifest["original"]
                expected_key = "objects/sha256/" + revision[:2] + "/" + revision[2:4] + "/" + revision
                if (result["database"]["clean"] is not True or result["audit_chain"]["valid"] is not True
                        or manifest["revision_id"] != revision or original["sha256"] != revision
                        or original["media_type"] != "application/pdf" or evidence["original_vault_key"] != expected_key
                        or verified["original"]["kind"] != "verified" or verified["manifest"]["kind"] != "verified"
                        or verified["original"]["digest"] != revision or verified["original"]["vault_key"] != expected_key
                        or not any(lineage["project_id"] == self.metadata["project"]["id"] for lineage in evidence["lineages"])):
                    raise ValueError("unverified")
                length = original["byte_length"]
                if type(length) is not int or not 0 < length <= MAX_UPLOAD or verified["original"]["byte_length"] != length:
                    raise ValueError("invalid size")
                path = self.vault
                for component in expected_key.split("/"):
                    path = path / component
                    if path.is_symlink():
                        raise ValueError("linked evidence")
                if path.resolve().parent != (self.vault / "objects" / "sha256" / revision[:2] / revision[2:4]).resolve():
                    raise ValueError("escaped evidence")
                snapshot = tempfile.TemporaryFile()
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as source:
                    metadata = os.fstat(source.fileno())
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != length:
                        raise ValueError("wrong size")
                    while True:
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        size += len(block)
                        if size > length:
                            raise ValueError("changed size")
                        digest.update(block)
                        snapshot.write(block)
                if size != length or digest.hexdigest() != revision:
                    raise ValueError("changed content")
                snapshot.seek(0)
                return snapshot, length
            except (OSError, ValueError, KeyError, TypeError):
                if snapshot is not None:
                    snapshot.close()
                raise integrity() from None


    def rendered_page(self, revision, index):
        """Render a bounded display image from core-verified original bytes."""
        with self.operation_lock:
            if type(index) is not int or index < 0:
                raise WorkspaceError("not_found", "This page is not in the drawing.", 404)
            documents = self.state()["documents"]
            drawing = next((item for item in documents if item["revision_id"] == revision), None)
            if drawing is None or not any(sheet["index"] == index for sheet in drawing["sheets"]):
                raise WorkspaceError("not_found", "This page is not in the drawing.", 404)
            if self.renderer is None:
                raise WorkspaceError("preview_unavailable", "Image preview requires a configured local PDF renderer. Open the original PDF instead.", 409)
            snapshot, _ = self.verified_pdf(revision)
            with snapshot, tempfile.TemporaryDirectory(prefix="drawing-page-") as directory:
                prefix = Path(directory).resolve() / "page"
                arguments = self.renderer + ["-f", str(index + 1), "-l", str(index + 1),
                                             "-singlefile", "-scale-to", "2000", "-png", "-", str(prefix)]
                try:
                    # Renderer diagnostics can repeat without bound; they are not
                    # exposed to the browser and must not accumulate in memory.
                    result = subprocess.run(arguments, stdin=snapshot, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, timeout=self.render_timeout, check=False)
                    output = prefix.with_suffix(".png")
                    if result.returncode or output.is_symlink() or not output.is_file():
                        raise WorkspaceError("preview_failed", "The local renderer could not display this page. The original PDF is still available.", 422)
                    if not 24 <= output.stat().st_size <= 20 * 1024 * 1024:
                        raise WorkspaceError("preview_failed", "The rendered page exceeded the preview limit.", 422)
                    with output.open("rb") as rendered:
                        image = rendered.read(20 * 1024 * 1024 + 1)
                    if len(image) > 20 * 1024 * 1024 or not image.startswith(b"\x89PNG\r\n\x1a\n"):
                        raise WorkspaceError("preview_failed", "The local renderer returned an invalid page image.", 422)
                    return image
                except subprocess.TimeoutExpired:
                    raise WorkspaceError("preview_timeout", "This page took too long to render. Open the original PDF instead.", 504) from None
                except OSError:
                    raise WorkspaceError("preview_failed", "The configured local PDF renderer could not be run.", 502) from None


class DrawingServer(HTTPServer):
    def __init__(self, workspace, port):
        self.workspace = workspace
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), DrawingHandler)
        self.origin = "http://127.0.0.1:%d" % self.server_port

    @property
    def workspace_url(self):
        return self.origin + "/" + self.token + "/"


class DrawingHandler(BaseHTTPRequestHandler):
    server_version = "HeleosDrawingWorkspace"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, format, *args):
        pass  # Capability paths and drawing names must not enter access logs.

    def _headers(self, status, content_type, length, pdf=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
        if pdf:
            self.send_header("Content-Disposition", 'inline; filename="drawing.pdf"')
        self.end_headers()

    def _json(self, value, status=200):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(payload))
        self.wfile.write(payload)

    def _route(self, write=False):
        if self.headers.get_all("Host") != [self.server.origin.removeprefix("http://")]:
            raise WorkspaceError("forbidden", "Only the local workspace address is allowed.", 403)
        origins = self.headers.get_all("Origin", [])
        if ((write and origins != [self.server.origin]) or (origins and origins != [self.server.origin])
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            raise WorkspaceError("forbidden", "Open this operation from the local drawing workspace.", 403)
        url = urlsplit(self.path)
        prefix = "/" + self.server.token + "/"
        if url.scheme or url.netloc or not url.path.startswith(prefix):
            raise WorkspaceError("not_found", "This local workspace route was not found.", 404)
        return url.path[len(prefix):], parse_qs(url.query, keep_blank_values=True)

    def _handle(self, write=False):
        try:
            route, query = self._route(write)
            if write:
                if route != "api/import":
                    raise WorkspaceError("not_found", "This local workspace route was not found.", 404)
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/pdf":
                    raise WorkspaceError("media_type", "Upload raw PDF bytes with application/pdf content type.", 415)
                lengths = self.headers.get_all("Content-Length", [])
                if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,12}", lengths[0]):
                    raise WorkspaceError("upload_length", "The PDF upload needs one valid content length.", 411)
                length = int(lengths[0])
                if length > MAX_UPLOAD:
                    raise WorkspaceError("upload_too_large", "PDF imports are limited to 256 MiB.", 413)
                if length == 0:
                    raise WorkspaceError("empty_upload", "Choose a PDF containing drawing pages.", 400)
                if set(query) != {"name"} or len(query["name"]) != 1:
                    raise WorkspaceError("invalid_name", "The PDF upload needs one file name.", 400)
                self._json(self.server.workspace.import_pdf(self.rfile, length, query["name"][0]))
            elif route == "api/state":
                self._json(self.server.workspace.state())
            elif route.startswith("api/pdf/"):
                snapshot, length = self.server.workspace.verified_pdf(route[len("api/pdf/"):])
                with snapshot:
                    self._headers(200, "application/pdf", length, pdf=True)
                    shutil.copyfileobj(snapshot, self.wfile, 1024 * 1024)
            elif route.startswith("api/page/"):
                selected = re.fullmatch(r"api/page/([0-9a-f]{64})/([0-9]{1,6})\.png", route)
                if selected is None:
                    raise WorkspaceError("not_found", "This page is not in the drawing.", 404)
                payload = self.server.workspace.rendered_page(selected[1], int(selected[2]))
                self._headers(200, "image/png", len(payload))
                self.wfile.write(payload)
            elif route in ("", "index.html", "app.js", "styles.css"):
                asset = route or "index.html"
                payload = (ASSETS / asset).read_bytes()
                self._headers(200, ASSET_TYPES[asset], len(payload))
                self.wfile.write(payload)
            else:
                raise WorkspaceError("not_found", "This local workspace route was not found.", 404)
        except WorkspaceError as error:
            self._json({"error": {"code": error.code, "message": error.message}}, error.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (socket.timeout, TimeoutError):
            self._json({"error": {"code": "upload_timeout", "message": "The upload timed out before all PDF bytes arrived."}}, 408)
        except (OSError, ValueError, TypeError):
            self._json({"error": {"code": "local_io", "message": "A local workspace file operation failed."}}, 500)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle(write=True)


def create_server(workspace, port=0):
    return DrawingServer(workspace, port)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--heleos", required=True, type=Path, help="Path to the existing Foundation CLI executable")
    parser.add_argument("--workspace", required=True, type=Path, help="New empty directory or existing drawing workspace")
    parser.add_argument("--port", type=int, default=0, help="Local port; 0 chooses an unused port")
    parser.add_argument("--pdftoppm", type=Path, help="Optional existing Poppler executable for local page images")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    workspace = None
    server = None
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        renderer = [str(args.pdftoppm.expanduser().resolve())] if args.pdftoppm else None
        workspace = Workspace([str(args.heleos.expanduser().resolve())], args.workspace, renderer=renderer)
        server = create_server(workspace, args.port)
        print("WORKSPACE_URL=" + server.workspace_url, flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        return 0
    except WorkspaceError as error:
        print(error.code + ": " + error.message, file=sys.stderr)
        return 1
    except OSError:
        print("local_io: The local server could not open its files or listening socket.", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server.server_close()
        if workspace is not None:
            workspace.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
