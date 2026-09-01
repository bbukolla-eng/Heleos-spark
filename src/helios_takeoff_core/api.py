"""A small loopback JSON API; app clients never touch P0 tables directly."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from .db import Database
from .errors import (
    AuthorizationError,
    ConflictError,
    HeliosTakeoffError,
    ImmutableStateError,
    NotFoundError,
    PreconditionError,
    ValidationError,
)
from .repository import TakeoffRepository
from .services import TakeoffService
from .agentic.artifacts import ArtifactRecord, ArtifactStore
from .agentic.service import AgentExecutionService


JsonPayload = dict[str, Any]
Operation = Callable[[], tuple[str, str, JsonPayload]]


class HeliosApi:
    """WSGI application exposing only explicit, versioned resource commands."""

    def __init__(self, database_path: str | Path, *, artifact_root: str | Path | None = None) -> None:
        database = Database(database_path)
        database.initialize()
        self.repository = TakeoffRepository(database)
        self.service = TakeoffService(self.repository)
        self.agent_service = AgentExecutionService(self.repository)
        self.artifact_store = ArtifactStore(Path(artifact_root)) if artifact_root is not None else None

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        try:
            method = str(environ.get("REQUEST_METHOD", "GET")).upper()
            path = str(environ.get("PATH_INFO", "/"))
            if path.startswith("/v1/agent-") and not self._is_loopback(environ.get("REMOTE_ADDR")):
                return self._respond(
                    start_response,
                    "403 Forbidden",
                    self._error("LOOPBACK_REQUIRED", "P1A routes accept loopback clients only"),
                )
            if method in {"PUT", "PATCH", "DELETE"}:
                return self._respond(start_response, "405 Method Not Allowed", self._error("METHOD_NOT_ALLOWED", "immutable resources do not support this method"))
            if method == "GET" and path == "/health":
                return self._respond(start_response, "200 OK", {"status": "ok", "api_version": "v1"})
            if method == "GET" and (match := re.fullmatch(r"/v1/takeoff-versions/([^/]+)", path)):
                return self._respond(start_response, "200 OK", self.repository.get_takeoff_version(match.group(1)))
            if method == "GET" and (match := re.fullmatch(r"/v1/bid-releases/([^/]+)", path)):
                return self._respond(start_response, "200 OK", self.repository.get_bid_release(match.group(1)))
            if method == "GET" and (match := re.fullmatch(r"/v1/agent-jobs/([^/]+)", path)):
                return self._respond(start_response, "200 OK", self.agent_service.get_job(match.group(1)))
            if method == "GET" and (match := re.fullmatch(r"/v1/agent-artifacts/([^/]+)", path)):
                return self._respond(start_response, "200 OK", self._agent_artifact(match.group(1)))
            if method != "POST":
                return self._respond(start_response, "404 Not Found", self._error("NOT_FOUND", "route was not found"))
            payload = self._read_json(environ)
            status, response = self._dispatch_post(path, payload, environ)
            return self._respond(start_response, status, response)
        except HeliosTakeoffError as error:
            return self._respond(start_response, self._status_for(error), self._error(self._code_for(error), str(error)))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._respond(start_response, "400 Bad Request", self._error("INVALID_JSON", "request body must be valid UTF-8 JSON"))
        except Exception:  # Deliberately do not expose internal exception text through an app boundary.
            return self._respond(start_response, "500 Internal Server Error", self._error("INTERNAL_ERROR", "unexpected server error"))

    def _dispatch_post(self, path: str, body: JsonPayload, environ: dict[str, Any]) -> tuple[str, JsonPayload]:
        if path == "/v1/agent-jobs":
            return self._mutation(path, body, environ, lambda: self._agent_job(body))
        if path == "/v1/projects":
            return self._mutation(path, body, environ, lambda: self._project(body))
        if path == "/v1/actors":
            return self._mutation(path, body, environ, lambda: self._actor(body))
        if match := re.fullmatch(r"/v1/projects/([^/]+)/document-revisions", path):
            project_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._document_revision(project_id, body))
        if match := re.fullmatch(r"/v1/projects/([^/]+)/revision-sets", path):
            project_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._revision_set(project_id, body))
        if match := re.fullmatch(r"/v1/revision-sets/([^/]+)/document-revisions", path):
            revision_set_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._include_document(revision_set_id, body))
        if match := re.fullmatch(r"/v1/revision-sets/([^/]+)/freeze", path):
            revision_set_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._freeze_revision_set(revision_set_id, body))
        if match := re.fullmatch(r"/v1/document-revisions/([^/]+)/evidence-items", path):
            document_revision_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._evidence(document_revision_id, body))
        if path == "/v1/extraction-runs":
            return self._mutation(path, body, environ, lambda: self._extraction_run(body))
        if match := re.fullmatch(r"/v1/evidence-items/([^/]+)/claims", path):
            evidence_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._claim(evidence_id, body))
        if match := re.fullmatch(r"/v1/revision-sets/([^/]+)/quantity-assertions", path):
            revision_set_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._quantity_assertion(revision_set_id, body))
        if match := re.fullmatch(r"/v1/quantity-assertions/([^/]+)/review-decisions", path):
            assertion_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._quantity_review(assertion_id, body))
        if match := re.fullmatch(r"/v1/revision-sets/([^/]+)/takeoff-versions", path):
            revision_set_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._takeoff_version(revision_set_id, body))
        if match := re.fullmatch(r"/v1/takeoff-versions/([^/]+)/approvals", path):
            takeoff_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._takeoff_approval(takeoff_id, body))
        if match := re.fullmatch(r"/v1/revision-sets/([^/]+)/successors", path):
            predecessor_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._successor_revision_set(predecessor_id, body))
        if match := re.fullmatch(r"/v1/revision-sets/([^/]+)/conflicts", path):
            revision_set_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._conflict(revision_set_id, body))
        if match := re.fullmatch(r"/v1/revision-sets/([^/]+)/rfis", path):
            revision_set_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._rfi(revision_set_id, body))
        if match := re.fullmatch(r"/v1/rfis/([^/]+)/responses", path):
            rfi_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._rfi_response(rfi_id, body))
        if match := re.fullmatch(r"/v1/conflicts/([^/]+)/dispositions", path):
            conflict_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._conflict_disposition(conflict_id, body))
        if match := re.fullmatch(r"/v1/projects/([^/]+)/suppliers", path):
            project_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._supplier(project_id, body))
        if match := re.fullmatch(r"/v1/projects/([^/]+)/quote-revisions", path):
            project_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._quote_revision(project_id, body))
        if match := re.fullmatch(r"/v1/quote-revisions/([^/]+)/lines", path):
            quote_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._quote_line(quote_id, body))
        if match := re.fullmatch(r"/v1/takeoff-versions/([^/]+)/estimate-versions", path):
            takeoff_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._estimate_version(takeoff_id, body))
        if match := re.fullmatch(r"/v1/estimate-versions/([^/]+)/approvals", path):
            estimate_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._estimate_approval(estimate_id, body))
        if path == "/v1/bid-releases":
            return self._mutation(path, body, environ, lambda: self._bid_release(body))
        if match := re.fullmatch(r"/v1/bid-releases/([^/]+)/void", path):
            bid_release_id = match.group(1)
            return self._mutation(path, body, environ, lambda: self._void_bid_release(bid_release_id, body))
        return "404 Not Found", self._error("NOT_FOUND", "route was not found")

    def _agent_job(self, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        artifact_store = self._require_artifact_store()
        submitted = self.agent_service.submit_baseline_audit(
            artifact_store=artifact_store,
            **self._only_fields(
                body,
                "project_id",
                "revision_set_id",
                "requested_by_actor_id",
                "worker_code",
            ),
        )
        response = {
            key: value
            for key, value in submitted.items()
            if key != "artifact"
        }
        response["input_artifact"] = self._artifact_payload(
            submitted["input_artifact_id"],
            submitted["artifact"],
        )
        return "agent_job", submitted["job_id"], response

    def _agent_artifact(self, artifact_id: str) -> JsonPayload:
        artifact_store = self._require_artifact_store()
        with self.repository.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM agent_artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError("agent artifact was not found")
        record = ArtifactRecord(
            sha256=row["sha256"],
            byte_size=row["byte_size"],
            media_type=row["media_type"],
            schema_version=row["schema_version"],
            store_key=row["store_key"],
        )
        payload = self._artifact_payload(artifact_id, record)
        valid = artifact_store.verify(record)
        payload["integrity_valid"] = valid
        payload["integrity_status"] = "VERIFIED" if valid else "TAMPERED"
        return payload

    def _require_artifact_store(self) -> ArtifactStore:
        if self.artifact_store is None:
            raise PreconditionError("P1A artifact storage is not configured for this API process")
        return self.artifact_store

    @staticmethod
    def _artifact_payload(artifact_id: str, record: ArtifactRecord) -> JsonPayload:
        return {
            "id": artifact_id,
            "sha256": record.sha256,
            "byte_size": record.byte_size,
            "media_type": record.media_type,
            "schema_version": record.schema_version,
            "store_key": record.store_key,
        }

    @staticmethod
    def _is_loopback(value: Any) -> bool:
        try:
            return ipaddress.ip_address(str(value)).is_loopback
        except ValueError:
            return False

    def _mutation(self, path: str, body: JsonPayload, environ: dict[str, Any], operation: Operation) -> tuple[str, JsonPayload]:
        key = str(environ.get("HTTP_IDEMPOTENCY_KEY", "")).strip()
        if not key:
            raise ValidationError("Idempotency-Key header is required for POST requests")
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        with self.repository.database.transaction():
            existing = self.repository.get_idempotency_request(operation_scope=path, idempotency_key=key)
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    raise ConflictError("idempotency key is already bound to a different request")
                return "201 Created", existing["result_payload"]
            resource_type, resource_id, response = operation()
            self.repository.record_idempotency_request(
                operation_scope=path,
                idempotency_key=key,
                request_fingerprint=fingerprint,
                result_resource_type=resource_type,
                result_resource_id=resource_id,
                result_payload=response,
            )
            return "201 Created", response

    def _project(self, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        project_id = self.repository.create_project(**self._only_fields(body, "code", "name"))
        return "project", project_id, {"id": project_id}

    def _actor(self, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        actor_id = self.repository.create_actor(**self._only_fields(body, "display_name", "actor_type"))
        return "actor", actor_id, {"id": actor_id}

    def _document_revision(self, project_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        document_id = self.repository.register_document_revision(
            project_id=project_id,
            **self._only_fields(
                body,
                "document_type",
                "document_number",
                "title",
                "sha256",
                "issue_date",
                "supersedes_document_revision_id",
            ),
        )
        return "document_revision", document_id, {"id": document_id}

    def _revision_set(self, project_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        revision_set_id = self.repository.create_revision_set(project_id=project_id, **self._only_fields(body, "name"))
        return "revision_set", revision_set_id, {"id": revision_set_id}

    def _include_document(self, revision_set_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        self.repository.include_document_revision(revision_set_id, **self._only_fields(body, "document_revision_id"))
        return "revision_set_document", revision_set_id, {"revision_set_id": revision_set_id}

    def _freeze_revision_set(self, revision_set_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        self._only_fields(body)
        self.repository.freeze_revision_set(revision_set_id)
        return "revision_set", revision_set_id, self.repository.get_revision_set(revision_set_id)

    def _evidence(self, document_revision_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        evidence_id = self.repository.record_evidence_item(
            document_revision_id=document_revision_id,
            **self._only_fields(body, "sheet_id", "page_number", "geometry", "text_span", "content_sha256"),
        )
        return "evidence_item", evidence_id, {"id": evidence_id}

    def _extraction_run(self, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        run_id = self.repository.start_extraction_run(
            **self._only_fields(
                body,
                "project_id",
                "plugin_id",
                "plugin_version",
                "configuration_sha256",
                "input_manifest_sha256",
            )
        )
        return "extraction_run", run_id, {"id": run_id}

    def _claim(self, evidence_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        fields = self._only_fields(
            body,
            "extraction_run_id",
            "evidence_item_ids",
            "subject_kind",
            "subject_key",
            "claim_type",
            "payload",
            "confidence",
        )
        evidence_item_ids = fields.pop("evidence_item_ids")
        if evidence_item_ids is None:
            evidence_item_ids = []
        if not isinstance(evidence_item_ids, list):
            raise ValidationError("evidence_item_ids must be a list")
        if evidence_id not in evidence_item_ids:
            evidence_item_ids.insert(0, evidence_id)
        claim_id = self.repository.record_extraction_claim(evidence_item_ids=evidence_item_ids, **fields)
        return "extraction_claim", claim_id, {"id": claim_id}

    def _quantity_assertion(self, revision_set_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        assertion_id = self.service.create_quantity_assertion(
            revision_set_id=revision_set_id,
            **self._only_fields(
                body,
                "subject_kind",
                "subject_key",
                "uom",
                "quantity",
                "scope_state",
                "evidence_allocations",
                "supersedes_assertion_id",
            ),
        )
        return "quantity_assertion", assertion_id, {"id": assertion_id}

    def _quantity_review(self, assertion_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        review_id = self.service.record_quantity_review(
            assertion_id=assertion_id,
            **self._only_fields(body, "actor_id", "outcome", "rationale"),
        )
        return "quantity_review", review_id, {"id": review_id}

    def _takeoff_version(self, revision_set_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        self._only_fields(body)
        takeoff_id = self.service.create_takeoff_version(revision_set_id=revision_set_id)
        return "takeoff_version", takeoff_id, {"id": takeoff_id}

    def _takeoff_approval(self, takeoff_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        approval_id = self.service.approve_takeoff_version(
            takeoff_version_id=takeoff_id,
            **self._only_fields(body, "actor_id", "rationale"),
        )
        return "takeoff_approval", approval_id, {"id": approval_id}

    def _successor_revision_set(self, predecessor_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        successor_id = self.service.create_successor_revision_set(
            predecessor_revision_set_id=predecessor_id,
            **self._only_fields(body, "triggering_document_revision_id", "name"),
        )
        return "revision_set", successor_id, {"id": successor_id}

    def _conflict(self, revision_set_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        conflict_id = self.service.create_conflict(
            revision_set_id=revision_set_id,
            **self._only_fields(body, "conflict_type", "severity", "description"),
        )
        return "conflict", conflict_id, {"id": conflict_id}

    def _rfi(self, revision_set_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        rfi_id = self.service.create_rfi(revision_set_id=revision_set_id, **self._only_fields(body, "question"))
        return "rfi", rfi_id, {"id": rfi_id}

    def _rfi_response(self, rfi_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        response_id = self.service.record_rfi_response(
            rfi_id=rfi_id,
            **self._only_fields(body, "response_document_revision_id", "response_text"),
        )
        return "rfi_response", response_id, {"id": response_id}

    def _conflict_disposition(self, conflict_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        disposition_id = self.service.disposition_conflict(
            conflict_id=conflict_id,
            **self._only_fields(body, "actor_id", "rfi_response_id", "rationale"),
        )
        return "conflict_disposition", disposition_id, {"id": disposition_id}

    def _supplier(self, project_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        supplier_id = self.service.create_supplier(project_id=project_id, **self._only_fields(body, "name"))
        return "supplier", supplier_id, {"id": supplier_id}

    def _quote_revision(self, project_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        quote_id = self.service.create_quote_revision(
            project_id=project_id,
            **self._only_fields(
                body,
                "supplier_id",
                "source_document_revision_id",
                "quote_number",
                "issued_at",
                "valid_through",
                "currency",
                "terms",
            ),
        )
        return "quote_revision", quote_id, {"id": quote_id}

    def _quote_line(self, quote_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        quote_line_id = self.service.add_quote_line(
            quote_revision_id=quote_id,
            **self._only_fields(body, "supplier_part_number", "description", "uom", "unit_price"),
        )
        return "quote_line", quote_line_id, {"id": quote_line_id}

    def _estimate_version(self, takeoff_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        estimate_id = self.service.create_estimate_version(
            takeoff_version_id=takeoff_id,
            **self._only_fields(body, "line_inputs"),
        )
        return "estimate_version", estimate_id, {"id": estimate_id}

    def _estimate_approval(self, estimate_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        approval_id = self.service.approve_estimate_version(
            estimate_version_id=estimate_id,
            **self._only_fields(body, "actor_id", "rationale"),
        )
        return "estimate_approval", approval_id, {"id": approval_id}

    def _bid_release(self, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        release_id = self.service.release_bid(
            **self._only_fields(
                body,
                "takeoff_version_id",
                "estimate_version_id",
                "authorized_bidder_actor_id",
                "submitter_actor_id",
                "rationale",
            )
        )
        return "bid_release", release_id, {"id": release_id}

    def _void_bid_release(self, bid_release_id: str, body: JsonPayload) -> tuple[str, str, JsonPayload]:
        void_id = self.service.void_bid_release(
            bid_release_id=bid_release_id,
            **self._only_fields(body, "actor_id", "rationale"),
        )
        return "bid_release_void", void_id, {"id": void_id}

    @staticmethod
    def _only_fields(body: JsonPayload, *allowed_fields: str) -> JsonPayload:
        allowed = set(allowed_fields)
        unknown = sorted(set(body).difference(allowed))
        if unknown:
            raise ValidationError(f"unexpected request field: {unknown[0]}")
        return {field_name: body.get(field_name) for field_name in allowed_fields}

    @staticmethod
    def _read_json(environ: dict[str, Any]) -> JsonPayload:
        content_type = str(environ.get("CONTENT_TYPE", "")).split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValidationError("Content-Type must be application/json")
        length_text = str(environ.get("CONTENT_LENGTH", "0") or "0")
        try:
            length = int(length_text)
        except ValueError as error:
            raise ValidationError("Content-Length must be an integer") from error
        if length < 0 or length > 1_000_000:
            raise ValidationError("request body size is invalid")
        raw = environ["wsgi.input"].read(length)
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(payload, dict):
            raise ValidationError("JSON request body must be an object")
        return payload

    @staticmethod
    def _respond(start_response: Callable[..., Any], status: str, payload: JsonPayload) -> list[bytes]:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(encoded)))])
        return [encoded]

    @staticmethod
    def _error(code: str, message: str) -> JsonPayload:
        return {"error": {"code": code, "message": message}}

    @staticmethod
    def _status_for(error: HeliosTakeoffError) -> str:
        if isinstance(error, ValidationError):
            return "400 Bad Request"
        if isinstance(error, NotFoundError):
            return "404 Not Found"
        if isinstance(error, (ConflictError, ImmutableStateError, PreconditionError)):
            return "409 Conflict"
        if isinstance(error, AuthorizationError):
            return "403 Forbidden"
        return "400 Bad Request"

    @staticmethod
    def _code_for(error: HeliosTakeoffError) -> str:
        if isinstance(error, ConflictError):
            return "IDEMPOTENCY_CONFLICT"
        if isinstance(error, ValidationError):
            return "VALIDATION_ERROR"
        if isinstance(error, NotFoundError):
            return "NOT_FOUND"
        if isinstance(error, ImmutableStateError):
            return "IMMUTABLE_STATE"
        if isinstance(error, PreconditionError):
            return "PRECONDITION_FAILED"
        if isinstance(error, AuthorizationError):
            return "FORBIDDEN"
        return "REQUEST_ERROR"


def create_wsgi_app(
    database_path: str | Path,
    *,
    artifact_root: str | Path | None = None,
) -> HeliosApi:
    """Build the local API application and apply migrations before serving requests."""
    return HeliosApi(database_path, artifact_root=artifact_root)
