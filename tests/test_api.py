"""Tests that catch an unsafe or non-idempotent local API boundary."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from wsgiref.util import setup_testing_defaults

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.api import create_wsgi_app


def invoke(app, method: str, path: str, body: dict | None = None, headers: dict[str, str] | None = None):
    """Exercise the actual WSGI boundary, not a mocked router."""
    raw_body = b"" if body is None else json.dumps(body).encode("utf-8")
    environ: dict[str, object] = {}
    setup_testing_defaults(environ)
    environ["REQUEST_METHOD"] = method
    environ["PATH_INFO"] = path
    environ["CONTENT_TYPE"] = "application/json"
    environ["CONTENT_LENGTH"] = str(len(raw_body))
    environ["wsgi.input"] = io.BytesIO(raw_body)
    for name, value in (headers or {}).items():
        environ[f"HTTP_{name.upper().replace('-', '_')}"] = value
    captured: dict[str, object] = {}

    def start_response(status: str, response_headers: list[tuple[str, str]], _exception_info=None):
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    response_body = b"".join(app(environ, start_response))
    return str(captured["status"]), json.loads(response_body.decode("utf-8"))


class ApiTests(unittest.TestCase):
    """The production changes caught are route mutation, missing validation, and duplicate POST side effects."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.app = create_wsgi_app(Path(self.temporary_directory.name) / "p0.sqlite3")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_health_and_idempotent_project_creation(self) -> None:
        health_status, health = invoke(self.app, "GET", "/health")
        self.assertEqual(health_status, "200 OK")
        self.assertEqual(health["status"], "ok")

        headers = {"Idempotency-Key": "project-p700-v1"}
        first_status, first = invoke(
            self.app,
            "POST",
            "/v1/projects",
            {"code": "P-700", "name": "Terminal Renovation"},
            headers,
        )
        second_status, second = invoke(
            self.app,
            "POST",
            "/v1/projects",
            {"code": "P-700", "name": "Terminal Renovation"},
            headers,
        )

        self.assertEqual(first_status, "201 Created")
        self.assertEqual(second_status, "201 Created")
        self.assertEqual(first["id"], second["id"])

    def test_api_returns_structured_errors_for_mutation_and_idempotency_mismatch(self) -> None:
        headers = {"Idempotency-Key": "project-p701-v1"}
        invoke(
            self.app,
            "POST",
            "/v1/projects",
            {"code": "P-701", "name": "Clinic"},
            headers,
        )
        mismatch_status, mismatch = invoke(
            self.app,
            "POST",
            "/v1/projects",
            {"code": "P-701", "name": "Different Clinic"},
            headers,
        )
        invalid_status, invalid = invoke(self.app, "POST", "/v1/projects", {"name": "No Code"})
        method_status, method_error = invoke(self.app, "PATCH", "/v1/projects", {"code": "P-701"})

        self.assertEqual(mismatch_status, "409 Conflict")
        self.assertEqual(mismatch["error"]["code"], "IDEMPOTENCY_CONFLICT")
        self.assertEqual(invalid_status, "400 Bad Request")
        self.assertEqual(invalid["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(method_status, "405 Method Not Allowed")
        self.assertEqual(method_error["error"]["code"], "METHOD_NOT_ALLOWED")

    def test_api_rejects_unknown_or_mistyped_fields_without_leaking_internal_errors(self) -> None:
        headers = {"Idempotency-Key": "project-p702-v1"}
        project_status, project = invoke(
            self.app,
            "POST",
            "/v1/projects",
            {"code": "P-702", "name": "Control Upgrade"},
            headers,
        )
        self.assertEqual(project_status, "201 Created")

        document_status, document_error = invoke(
            self.app,
            "POST",
            f"/v1/projects/{project['id']}/document-revisions",
            {
                "document_type": "DRAWING",
                "document_number": "M-702",
                "title": "Controls Plan",
                "sha256": "a" * 64,
                "issue_date": "2026-08-27",
                "project_id": "attempted-path-override",
            },
            {"Idempotency-Key": "document-p702-v1"},
        )
        self.assertEqual(document_status, "400 Bad Request")
        self.assertEqual(document_error["error"]["code"], "VALIDATION_ERROR")

        claim_status, claim_error = invoke(
            self.app,
            "POST",
            "/v1/evidence-items/not-a-real-evidence-id/claims",
            {
                "extraction_run_id": "not-a-real-run",
                "evidence_item_ids": "not-a-list",
                "subject_kind": "EQUIPMENT",
                "subject_key": "EF-1",
                "claim_type": "COUNT",
                "payload": {},
                "confidence": "0.9",
            },
            {"Idempotency-Key": "claim-p702-v1"},
        )
        self.assertEqual(claim_status, "400 Bad Request")
        self.assertEqual(claim_error["error"]["code"], "VALIDATION_ERROR")

    def test_failed_idempotency_recording_rolls_back_the_underlying_command(self) -> None:
        with mock.patch.object(
            self.app.repository,
            "record_idempotency_request",
            side_effect=RuntimeError("simulated ledger failure"),
        ):
            failed_status, failed = invoke(
                self.app,
                "POST",
                "/v1/projects",
                {"code": "P-ROLLBACK", "name": "Must Not Persist"},
                {"Idempotency-Key": "project-rollback-v1"},
            )

        retry_status, retry = invoke(
            self.app,
            "POST",
            "/v1/projects",
            {"code": "P-ROLLBACK", "name": "Must Not Persist"},
            {"Idempotency-Key": "project-rollback-v2"},
        )

        self.assertEqual(failed_status, "500 Internal Server Error")
        self.assertEqual(failed["error"]["code"], "INTERNAL_ERROR")
        self.assertEqual(retry_status, "201 Created")
        self.assertIn("id", retry)

    def test_http_api_does_not_expose_role_grant_or_revocation_commands(self) -> None:
        actor_status, actor = invoke(
            self.app,
            "POST",
            "/v1/actors",
            {"display_name": "Untrusted API Caller", "actor_type": "USER"},
            {"Idempotency-Key": "actor-untrusted-v1"},
        )
        self.assertEqual(actor_status, "201 Created")

        grant_status, grant_error = invoke(
            self.app,
            "POST",
            f"/v1/actors/{actor['id']}/role-grants",
            {"role": "AUTHORIZED_BIDDER"},
            {"Idempotency-Key": "grant-untrusted-v1"},
        )

        self.assertEqual(grant_status, "404 Not Found")
        self.assertEqual(grant_error["error"]["code"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
