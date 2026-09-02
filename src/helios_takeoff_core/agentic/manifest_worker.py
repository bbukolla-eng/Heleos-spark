"""Deterministic subprocess worker for frozen P0 manifest metadata."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .contracts import BASELINE_AUDIT_REPORT_KIND, BASELINE_AUDIT_REPORT_SCHEMA, WORKER_RESULT_PROTOCOL


WORKER_INPUT_PROTOCOL = "helios.p1a.worker-input/v1"
MANIFEST_SCHEMA = "helios.p0.revision-set-manifest/v1"
MANIFEST_MEDIA_TYPE = "application/vnd.helios.revision-set-manifest+json"
REQUIRED_DOCUMENT_TYPES = frozenset({"DRAWING", "SPECIFICATION", "SCHEDULE"})
DOCUMENT_TYPES = frozenset({"DRAWING", "SPECIFICATION", "SCHEDULE", "ADDENDUM", "RFI_RESPONSE", "OTHER"})
DOCUMENT_FIELDS = {
    "document_revision_id",
    "document_type",
    "document_number",
    "title",
    "sha256",
    "issue_date",
    "supersedes_document_revision_id",
    "evidence_count",
}


def _require_object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _require_exact_fields(value: dict[str, Any], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{name} fields are invalid")


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-blank text")
    return value


def _require_sha256(value: object, name: str) -> str:
    value = _require_text(value, name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _load_envelope() -> dict[str, Any]:
    try:
        envelope = json.loads(sys.stdin.buffer.read())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("stdin must contain exactly one JSON input envelope") from error
    envelope = _require_object(envelope, "input envelope")
    _require_exact_fields(envelope, {"protocol", "job_id", "work_item_id", "attempt_id", "input"}, "input envelope")
    if envelope["protocol"] != WORKER_INPUT_PROTOCOL:
        raise ValueError("input protocol is unsupported")
    for field in ("job_id", "work_item_id", "attempt_id"):
        _require_text(envelope[field], field)
    input_descriptor = _require_object(envelope["input"], "input descriptor")
    _require_exact_fields(
        input_descriptor,
        {"path", "sha256", "media_type", "schema_version"},
        "input descriptor",
    )
    input_path = _require_text(input_descriptor["path"], "input path")
    if input_path in {".", ".."} or Path(input_path).is_absolute() or len(Path(input_path).parts) != 1:
        raise ValueError("input path must name one file in the private attempt directory")
    _require_sha256(input_descriptor["sha256"], "input sha256")
    if input_descriptor["media_type"] != MANIFEST_MEDIA_TYPE:
        raise ValueError("input media type is unsupported")
    if input_descriptor["schema_version"] != MANIFEST_SCHEMA:
        raise ValueError("input schema version is unsupported")
    return envelope


def _load_manifest(envelope: dict[str, Any]) -> dict[str, Any]:
    input_descriptor = envelope["input"]
    manifest_bytes = _read_private_file(input_descriptor["path"])
    if hashlib.sha256(manifest_bytes).hexdigest() != input_descriptor["sha256"]:
        raise ValueError("input manifest digest does not match the envelope")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("input manifest must contain exactly one JSON object") from error
    manifest = _require_object(manifest, "manifest")
    _require_exact_fields(manifest, {"project_id", "revision_set_id", "documents"}, "manifest")
    _require_text(manifest["project_id"], "manifest project_id")
    _require_text(manifest["revision_set_id"], "manifest revision_set_id")
    documents = manifest["documents"]
    if not isinstance(documents, list):
        raise ValueError("manifest documents must be an array")
    document_ids: set[str] = set()
    for index, document_value in enumerate(documents):
        document = _require_object(document_value, f"document {index}")
        _require_exact_fields(document, DOCUMENT_FIELDS, f"document {index}")
        document_id = _require_text(document["document_revision_id"], f"document {index} id")
        if document_id in document_ids:
            raise ValueError("manifest document IDs must be unique")
        document_ids.add(document_id)
        if document["document_type"] not in DOCUMENT_TYPES:
            raise ValueError(f"document {index} type is unsupported")
        _require_text(document["document_number"], f"document {index} number")
        _require_text(document["title"], f"document {index} title")
        _require_sha256(document["sha256"], f"document {index} sha256")
        issue_date = document["issue_date"]
        if issue_date is not None and not isinstance(issue_date, str):
            raise ValueError(f"document {index} issue_date must be text or null")
        superseded_id = document["supersedes_document_revision_id"]
        if superseded_id is not None:
            _require_text(superseded_id, f"document {index} superseded id")
        evidence_count = document["evidence_count"]
        if not isinstance(evidence_count, int) or isinstance(evidence_count, bool) or evidence_count < 0:
            raise ValueError(f"document {index} evidence_count must be a non-negative integer")
    return manifest


def _read_private_file(name: str) -> bytes:
    required_flags = (getattr(os, "O_DIRECTORY", None), getattr(os, "O_NOFOLLOW", None), getattr(os, "O_CLOEXEC", None))
    if any(not isinstance(flag, int) or isinstance(flag, bool) or flag <= 0 for flag in required_flags):
        raise ValueError("secure private manifest reading is unavailable")
    directory_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise ValueError("input manifest must be a regular file")
            chunks: list[bytes] = []
            while chunk := os.read(file_fd, 1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(file_fd)
    except OSError as error:
        raise ValueError("input manifest cannot be read securely") from error
    finally:
        os.close(directory_fd)


def _audit(manifest: dict[str, Any], input_sha256: str) -> dict[str, Any]:
    documents = manifest["documents"]
    included_ids = {document["document_revision_id"] for document in documents}
    type_counts = Counter(document["document_type"] for document in documents)
    missing_superseded = [
        {
            "document_revision_id": document["document_revision_id"],
            "supersedes_document_revision_id": document["supersedes_document_revision_id"],
        }
        for document in documents
        if document["supersedes_document_revision_id"] is not None
        and document["supersedes_document_revision_id"] not in included_ids
    ]
    return {
        "project_id": manifest["project_id"],
        "revision_set_id": manifest["revision_set_id"],
        "input_sha256": input_sha256,
        "document_count": len(documents),
        "document_counts_by_type": dict(sorted(type_counts.items())),
        "required_document_types_absent": sorted(REQUIRED_DOCUMENT_TYPES - set(type_counts)),
        "superseded_document_references_not_included": missing_superseded,
        "missing_issue_date_document_ids": sorted(
            document["document_revision_id"]
            for document in documents
            if document["issue_date"] is None or not document["issue_date"].strip()
        ),
        "limitation": (
            "Audited frozen manifest metadata only, not drawing/spec contents or quantities. "
            "This is not PDF ingestion, drawing interpretation, takeoff, pricing, or external provider acceptance."
        ),
    }


def main() -> int:
    try:
        envelope = _load_envelope()
        manifest = _load_manifest(envelope)
        input_sha256 = envelope["input"]["sha256"]
        result = {
            "protocol": WORKER_RESULT_PROTOCOL,
            "status": "SUCCEEDED",
            "input_sha256": input_sha256,
            "report": {
                "kind": BASELINE_AUDIT_REPORT_KIND,
                "schema_version": BASELINE_AUDIT_REPORT_SCHEMA,
                "payload": _audit(manifest, input_sha256),
            },
        }
        sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        return 0
    except (OSError, TypeError, ValueError) as error:
        sys.stderr.write(f"manifest worker error: {error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
