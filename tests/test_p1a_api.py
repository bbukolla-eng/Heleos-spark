"""Loopback API tests for bounded P1A submission and read-only inspection."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from wsgiref.util import setup_testing_defaults

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.agentic.service import AgentExecutionService
from helios_takeoff_core.agentic.artifacts import ArtifactStore
from helios_takeoff_core.api import create_wsgi_app
from helios_takeoff_core.db import Database
from helios_takeoff_core.repository import TakeoffRepository


BID_IMPACTING_TABLES = (
    "evidence_items",
    "extraction_claims",
    "quantity_assertions",
    "quantity_review_events",
    "takeoff_versions",
    "takeoff_approval_events",
    "quote_revisions",
    "estimate_versions",
    "estimate_approval_events",
    "bid_releases",
)


def invoke(
    app,
    method: str,
    path: str,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    *,
    remote_addr: str = "127.0.0.1",
):
    raw_body = b"" if body is None else json.dumps(body).encode("utf-8")
    environ: dict[str, object] = {}
    setup_testing_defaults(environ)
    environ["REQUEST_METHOD"] = method
    environ["PATH_INFO"] = path
    environ["CONTENT_TYPE"] = "application/json"
    environ["CONTENT_LENGTH"] = str(len(raw_body))
    environ["wsgi.input"] = io.BytesIO(raw_body)
    environ["REMOTE_ADDR"] = remote_addr
    for name, value in (headers or {}).items():
        environ[f"HTTP_{name.upper().replace('-', '_')}"] = value
    captured: dict[str, object] = {}

    def start_response(status: str, response_headers: list[tuple[str, str]], _exception_info=None):
        captured["status"] = status

    response_body = b"".join(app(environ, start_response))
    return str(captured["status"]), json.loads(response_body.decode("utf-8"))


class P1aApiTests(unittest.TestCase):
    """Catch P1A route authority expansion and unverified artifact metadata."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "helios.sqlite3"
        self.artifact_root = self.root / "artifacts"
        self.database = Database(self.database_path)
        self.database.initialize()
        self.repository = TakeoffRepository(self.database)
        AgentExecutionService(self.repository).bootstrap_roster()
        self.project_id = self.repository.create_project(code="P-600", name="Lab Renovation")
        self.actor_id = self.repository.create_actor(display_name="Estimator", actor_type="USER")
        document_id = self.repository.register_document_revision(
            project_id=self.project_id,
            document_type="DRAWING",
            document_number="M-601",
            title="Mechanical Plan",
            sha256="6" * 64,
            issue_date="2026-08-27",
        )
        self.revision_set_id = self.repository.create_revision_set(project_id=self.project_id, name="BID-01")
        self.repository.include_document_revision(self.revision_set_id, document_id)
        self.repository.freeze_revision_set(self.revision_set_id)
        self.app = create_wsgi_app(self.database_path, artifact_root=self.artifact_root)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _counts(self) -> dict[str, int]:
        with self.database.connection() as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in BID_IMPACTING_TABLES
            }

    def _submit(self):
        return invoke(
            self.app,
            "POST",
            "/v1/agent-jobs",
            {
                "project_id": self.project_id,
                "revision_set_id": self.revision_set_id,
                "requested_by_actor_id": self.actor_id,
                "worker_code": "helios-baseline-auditor",
            },
            {"Idempotency-Key": "baseline-audit-p600-v1"},
        )

    def test_api_creates_real_queued_job_and_reads_state_and_verified_artifact_metadata(self) -> None:
        before = self._counts()

        create_status, created = self._submit()
        replay_status, replayed = self._submit()
        job_status, job = invoke(self.app, "GET", f"/v1/agent-jobs/{created['job_id']}")
        artifact_status, artifact = invoke(
            self.app, "GET", f"/v1/agent-artifacts/{created['input_artifact_id']}"
        )

        self.assertEqual(create_status, "201 Created")
        self.assertEqual(replay_status, "201 Created")
        self.assertEqual(replayed, created)
        self.assertEqual(job_status, "200 OK")
        self.assertEqual(job["state"], "QUEUED")
        self.assertEqual(job["event_types"], ["QUEUED"])
        self.assertEqual(job["project_id"], self.project_id)
        self.assertEqual(job["revision_set_id"], self.revision_set_id)
        self.assertEqual(artifact_status, "200 OK")
        self.assertEqual(artifact["id"], created["input_artifact_id"])
        self.assertTrue(artifact["integrity_valid"])
        self.assertEqual(artifact["integrity_status"], "VERIFIED")
        self.assertNotIn("path", artifact)
        self.assertEqual(before, self._counts())

    def test_api_rejects_raw_completion_and_p1a_bid_authority_routes(self) -> None:
        forbidden_suffixes = ("complete", "succeed", "retry", "approve", "price", "estimate", "release")
        before = self._counts()

        for suffix in forbidden_suffixes:
            with self.subTest(suffix=suffix):
                status, error = invoke(
                    self.app,
                    "POST",
                    f"/v1/agent-jobs/job-id/{suffix}",
                    {},
                    {"Idempotency-Key": f"forbidden-{suffix}"},
                )
                self.assertEqual(status, "404 Not Found")
                self.assertEqual(error["error"]["code"], "NOT_FOUND")
        put_status, _ = invoke(self.app, "PUT", "/v1/agent-jobs/job-id", {})
        self.assertEqual(put_status, "405 Method Not Allowed")
        self.assertEqual(before, self._counts())

    def test_p1a_routes_reject_non_loopback_clients(self) -> None:
        status, error = invoke(
            self.app,
            "POST",
            "/v1/agent-jobs",
            {
                "project_id": self.project_id,
                "revision_set_id": self.revision_set_id,
                "requested_by_actor_id": self.actor_id,
                "worker_code": "helios-baseline-auditor",
            },
            {"Idempotency-Key": "remote-baseline-audit"},
            remote_addr="203.0.113.10",
        )

        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(error["error"]["code"], "LOOPBACK_REQUIRED")

    def test_api_accepts_registered_external_worker_then_claim_blocks_unconfigured(self) -> None:
        create_status, created = invoke(
            self.app,
            "POST",
            "/v1/agent-jobs",
            {
                "project_id": self.project_id,
                "revision_set_id": self.revision_set_id,
                "requested_by_actor_id": self.actor_id,
                "worker_code": "codex",
            },
            {"Idempotency-Key": "external-codex-audit"},
        )

        claimed = AgentExecutionService(self.repository).claim_next_work_item()
        job_status, job = invoke(self.app, "GET", f"/v1/agent-jobs/{created['job_id']}")

        self.assertEqual(create_status, "201 Created")
        self.assertEqual(created["worker_code"], "codex")
        self.assertEqual(created["state"], "QUEUED")
        self.assertEqual(claimed["state"], "BLOCKED")
        self.assertEqual(claimed["job_id"], created["job_id"])
        self.assertEqual(job_status, "200 OK")
        self.assertEqual(job["state"], "BLOCKED")
        self.assertEqual(job["latest_reason_code"], "UNCONFIGURED")

    def test_get_artifact_without_configured_store_returns_documented_precondition(self) -> None:
        app_without_artifacts = create_wsgi_app(self.database_path)

        status, error = invoke(app_without_artifacts, "GET", "/v1/agent-artifacts/artifact-id")

        self.assertEqual(status, "409 Conflict")
        self.assertEqual(error["error"]["code"], "PRECONDITION_FAILED")

    def test_completed_job_api_exposes_report_artifact_and_execution_projection(self) -> None:
        from helios_takeoff_core.agentic.runner import AgentRunner

        create_status, created = invoke(
            self.app,
            "POST",
            "/v1/agent-jobs",
            {
                "project_id": self.project_id,
                "revision_set_id": self.revision_set_id,
                "requested_by_actor_id": self.actor_id,
                "worker_code": "helios-baseline-auditor",
            },
            {"Idempotency-Key": "completed-job-projection"},
        )
        service = AgentExecutionService(self.repository)
        outcome = AgentRunner(
            service,
            artifact_store=ArtifactStore(self.artifact_root),
            attempt_root=self.root / "attempts",
        ).run_once()

        status, job = invoke(self.app, "GET", f"/v1/agent-jobs/{created['job_id']}")

        self.assertEqual(create_status, "201 Created")
        self.assertEqual(status, "200 OK")
        self.assertEqual(job["state"], "SUCCEEDED")
        self.assertEqual(job["attempts"][0]["id"], outcome["attempt_id"])
        self.assertEqual(job["report_artifact_id"], job["artifacts"]["REPORT"]["id"])
        self.assertEqual(job["worker"]["code"], "helios-baseline-auditor")
        self.assertEqual(job["adapter"]["kind"], "BUILTIN")
        self.assertEqual([event["type"] for event in job["events"]], job["event_types"])

    def test_openapi_defines_completed_job_report_artifact_contract(self) -> None:
        contract = json.loads(
            (Path(__file__).resolve().parents[1] / "docs" / "openapi" / "p1a-openapi.yaml").read_text()
        )

        response = contract["paths"]["/v1/agent-jobs/{job_id}"]["get"]["responses"]["200"]
        schema = response["content"]["application/json"]["schema"]
        self.assertEqual(schema["$ref"], "#/components/schemas/AgentJob")
        job_schema = contract["components"]["schemas"]["AgentJob"]
        self.assertIn("report_artifact_id", job_schema["required"])
        self.assertIn("attempts", job_schema["required"])
        self.assertIn("events", job_schema["required"])

    def test_p1a_openapi_is_valid_json_yaml_and_lists_only_implemented_p1a_routes(self) -> None:
        contract_path = Path(__file__).resolve().parents[1] / "docs" / "openapi" / "p1a-openapi.yaml"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))

        self.assertEqual(contract["openapi"], "3.1.0")
        self.assertEqual(
            set(contract["paths"]),
            {"/v1/agent-jobs", "/v1/agent-jobs/{job_id}", "/v1/agent-artifacts/{artifact_id}"},
        )
        self.assertEqual(set(contract["paths"]["/v1/agent-jobs"]), {"post"})
        self.assertEqual(set(contract["paths"]["/v1/agent-jobs/{job_id}"]), {"get"})
        self.assertEqual(set(contract["paths"]["/v1/agent-artifacts/{artifact_id}"]), {"get"})

    def test_openapi_allows_exact_registered_worker_roster(self) -> None:
        contract_path = Path(__file__).resolve().parents[1] / "docs" / "openapi" / "p1a-openapi.yaml"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))

        worker_codes = contract["components"]["schemas"]["AgentJobRequest"]["properties"]["worker_code"]

        self.assertEqual(
            worker_codes,
            {
                "type": "string",
                "enum": [
                    "codex",
                    "claude",
                    "kimi",
                    "grok",
                    "cursor",
                    "grokbot",
                    "helios-baseline-auditor",
                ],
                "description": (
                    "External workers queue normally but block unless an actual preflight "
                    "recorded AVAILABLE for the job's exact immutable adapter revision and "
                    "normalized configuration hash."
                ),
            },
        )

    def test_openapi_documents_unconfigured_artifact_store_precondition(self) -> None:
        contract_path = Path(__file__).resolve().parents[1] / "docs" / "openapi" / "p1a-openapi.yaml"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))

        responses = contract["paths"]["/v1/agent-artifacts/{artifact_id}"]["get"]["responses"]

        self.assertIn("409", responses)
        response = responses["409"]
        self.assertEqual(
            response["content"]["application/json"]["example"]["error"]["code"],
            "PRECONDITION_FAILED",
        )


if __name__ == "__main__":
    unittest.main()
