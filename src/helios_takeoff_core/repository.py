"""Transactional persistence operations for the immutable P0 document baseline."""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from .contracts import require_decimal_string, require_text
from .db import Database
from .errors import ConflictError, ImmutableStateError, NotFoundError, PreconditionError, ValidationError


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DOCUMENT_TYPES = frozenset({"DRAWING", "SPECIFICATION", "SCHEDULE", "ADDENDUM", "RFI_RESPONSE", "OTHER"})


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _non_empty(value: str, field_name: str) -> str:
    return require_text(value, field_name)


class TakeoffRepository:
    """The only P0 component allowed to write canonical document-baseline records."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_project(self, *, code: str, name: str) -> str:
        project_id = _new_id()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO projects(id, code, name, created_at) VALUES (?, ?, ?, ?)",
                (project_id, _non_empty(code, "code"), _non_empty(name, "name"), _now()),
            )
        return project_id

    def create_actor(self, *, display_name: str, actor_type: str) -> str:
        actor_type = _non_empty(actor_type, "actor_type").upper()
        if actor_type not in {"USER", "SERVICE"}:
            raise ValidationError("actor_type must be USER or SERVICE")
        actor_id = _new_id()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO actors(id, display_name, actor_type, created_at) VALUES (?, ?, ?, ?)",
                (actor_id, _non_empty(display_name, "display_name"), actor_type, _now()),
            )
        return actor_id

    def grant_role(
        self,
        *,
        actor_id: str,
        project_id: str | None,
        role: str,
        valid_from: str | None = None,
        valid_until: str | None = None,
    ) -> str:
        role = _non_empty(role, "role").upper()
        if role not in {"ESTIMATOR_REVIEWER", "AUTHORIZED_BIDDER", "SUBMITTER"}:
            raise ValidationError("unsupported role")
        grant_id = _new_id()
        with self.database.transaction() as connection:
            self._require_actor(connection, actor_id)
            if project_id is not None:
                self._require_project(connection, project_id)
            connection.execute(
                """
                INSERT INTO role_grants(
                    id, actor_id, project_id, role, valid_from, valid_until, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (grant_id, actor_id, project_id, role, valid_from or _now(), valid_until, _now()),
            )
        return grant_id

    def revoke_role_grant(self, *, role_grant_id: str, revoked_by_actor_id: str, rationale: str) -> str:
        revocation_id = _new_id()
        with self.database.transaction() as connection:
            grant = connection.execute("SELECT id FROM role_grants WHERE id = ?", (role_grant_id,)).fetchone()
            if grant is None:
                raise NotFoundError("role grant was not found")
            self._require_actor(connection, revoked_by_actor_id)
            existing = connection.execute(
                "SELECT id FROM role_grant_revocations WHERE role_grant_id = ?", (role_grant_id,)
            ).fetchone()
            if existing is not None:
                raise ImmutableStateError("role grant is already revoked")
            connection.execute(
                """
                INSERT INTO role_grant_revocations(id, role_grant_id, revoked_by_actor_id, rationale, revoked_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (revocation_id, role_grant_id, revoked_by_actor_id, _non_empty(rationale, "rationale"), _now()),
            )
        return revocation_id

    def register_document_revision(
        self,
        *,
        project_id: str,
        document_type: str,
        document_number: str,
        title: str,
        sha256: str,
        issue_date: str,
        supersedes_document_revision_id: str | None = None,
    ) -> str:
        document_type = _non_empty(document_type, "document_type").upper()
        if document_type not in DOCUMENT_TYPES:
            raise ValidationError(f"unsupported document_type: {document_type}")
        normalized_hash = _non_empty(sha256, "sha256").lower()
        if not SHA256_PATTERN.fullmatch(normalized_hash):
            raise ValidationError("sha256 must be a 64-character lowercase hexadecimal SHA-256 digest")
        document_id = _new_id()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            if supersedes_document_revision_id is not None:
                prior = connection.execute(
                    "SELECT project_id, document_type FROM document_revisions WHERE id = ?",
                    (supersedes_document_revision_id,),
                ).fetchone()
                if prior is None:
                    raise NotFoundError("superseded document revision was not found")
                if prior["project_id"] != project_id or prior["document_type"] != document_type:
                    raise ValidationError("a document revision can only supersede the same project and document type")
            connection.execute(
                """
                INSERT INTO document_revisions(
                    id, project_id, document_type, document_number, title, sha256,
                    issue_date, received_at, supersedes_document_revision_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    project_id,
                    document_type,
                    _non_empty(document_number, "document_number"),
                    _non_empty(title, "title"),
                    normalized_hash,
                    _non_empty(issue_date, "issue_date"),
                    _now(),
                    supersedes_document_revision_id,
                    _now(),
                ),
            )
        return document_id

    def create_revision_set(self, *, project_id: str, name: str) -> str:
        revision_set_id = _new_id()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            connection.execute(
                """
                INSERT INTO revision_sets(id, project_id, name, status, created_at)
                VALUES (?, ?, ?, 'OPEN', ?)
                """,
                (revision_set_id, project_id, _non_empty(name, "name"), _now()),
            )
        return revision_set_id

    def include_document_revision(self, revision_set_id: str, document_revision_id: str) -> None:
        with self.database.transaction() as connection:
            revision_set = self._require_revision_set(connection, revision_set_id)
            if revision_set["status"] != "OPEN":
                raise ImmutableStateError("a frozen revision set cannot receive new documents")
            document = connection.execute(
                "SELECT project_id FROM document_revisions WHERE id = ?", (document_revision_id,)
            ).fetchone()
            if document is None:
                raise NotFoundError("document revision was not found")
            if document["project_id"] != revision_set["project_id"]:
                raise ValidationError("a revision set can only include document revisions from its own project")
            connection.execute(
                """
                INSERT OR IGNORE INTO revision_set_documents(revision_set_id, document_revision_id, included_at)
                VALUES (?, ?, ?)
                """,
                (revision_set_id, document_revision_id, _now()),
            )

    def freeze_revision_set(self, revision_set_id: str) -> None:
        with self.database.transaction() as connection:
            revision_set = self._require_revision_set(connection, revision_set_id)
            if revision_set["status"] != "OPEN":
                raise ImmutableStateError("revision set is already frozen")
            included_count = connection.execute(
                "SELECT COUNT(*) FROM revision_set_documents WHERE revision_set_id = ?",
                (revision_set_id,),
            ).fetchone()[0]
            if included_count == 0:
                raise PreconditionError("a revision set requires at least one document before it can freeze")
            connection.execute(
                "UPDATE revision_sets SET status = 'FROZEN', frozen_at = ? WHERE id = ?",
                (_now(), revision_set_id),
            )

    def get_revision_set(self, revision_set_id: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            return dict(self._require_revision_set(connection, revision_set_id))

    def export_revision_set_manifest(self, revision_set_id: str) -> dict[str, Any]:
        """Return canonical document/evidence metadata only for one frozen P0 baseline."""
        with self.database.connection() as connection:
            revision_set = self._require_revision_set(connection, revision_set_id)
            if revision_set["status"] != "FROZEN":
                raise PreconditionError("revision set must be frozen before its manifest can be exported")
            documents = connection.execute(
                """
                SELECT document.id AS document_revision_id,
                       document.document_type,
                       document.document_number,
                       document.title,
                       document.sha256,
                       document.issue_date,
                       document.supersedes_document_revision_id,
                       COUNT(evidence.id) AS evidence_count
                FROM revision_set_documents AS baseline
                JOIN document_revisions AS document
                  ON document.id = baseline.document_revision_id
                LEFT JOIN evidence_items AS evidence
                  ON evidence.document_revision_id = document.id
                WHERE baseline.revision_set_id = ?
                GROUP BY document.id, document.document_type, document.document_number,
                         document.title, document.sha256, document.issue_date,
                         document.supersedes_document_revision_id
                ORDER BY document.document_number, document.document_type, document.id
                """,
                (revision_set_id,),
            ).fetchall()
            return {
                "revision_set_id": revision_set["id"],
                "project_id": revision_set["project_id"],
                "documents": [dict(document) for document in documents],
            }

    def record_evidence_item(
        self,
        *,
        document_revision_id: str,
        sheet_id: str,
        page_number: int,
        geometry: dict[str, Any] | None = None,
        text_span: str | None = None,
        content_sha256: str | None = None,
    ) -> str:
        if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
            raise ValidationError("page_number must be a positive integer")
        if geometry is None and (not isinstance(text_span, str) or not text_span.strip()):
            raise ValidationError("evidence requires geometry or text_span")
        geometry_json: str | None = None
        if geometry is not None:
            self._validate_geometry(geometry)
            geometry_json = json.dumps(geometry, sort_keys=True, separators=(",", ":"))
        normalized_content_hash = None
        if content_sha256 is not None:
            normalized_content_hash = _non_empty(content_sha256, "content_sha256").lower()
            if not SHA256_PATTERN.fullmatch(normalized_content_hash):
                raise ValidationError("content_sha256 must be a 64-character lowercase hexadecimal SHA-256 digest")
        evidence_id = _new_id()
        with self.database.transaction() as connection:
            document = connection.execute(
                "SELECT id FROM document_revisions WHERE id = ?", (document_revision_id,)
            ).fetchone()
            if document is None:
                raise NotFoundError("document revision was not found")
            connection.execute(
                """
                INSERT INTO evidence_items(
                    id, document_revision_id, sheet_id, page_number, geometry_json,
                    text_span, content_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    document_revision_id,
                    _non_empty(sheet_id, "sheet_id"),
                    page_number,
                    geometry_json,
                    text_span.strip() if isinstance(text_span, str) and text_span.strip() else None,
                    normalized_content_hash,
                    _now(),
                ),
            )
        return evidence_id

    def start_extraction_run(
        self,
        *,
        project_id: str,
        plugin_id: str,
        plugin_version: str,
        configuration_sha256: str,
        input_manifest_sha256: str,
    ) -> str:
        run_id = _new_id()
        normalized_configuration_hash = self._validate_sha256(configuration_sha256, "configuration_sha256")
        normalized_manifest_hash = self._validate_sha256(input_manifest_sha256, "input_manifest_sha256")
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            connection.execute(
                """
                INSERT INTO extraction_runs(
                    id, project_id, plugin_id, plugin_version, configuration_sha256,
                    input_manifest_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    project_id,
                    _non_empty(plugin_id, "plugin_id"),
                    _non_empty(plugin_version, "plugin_version"),
                    normalized_configuration_hash,
                    normalized_manifest_hash,
                    _now(),
                ),
            )
        return run_id

    def record_extraction_claim(
        self,
        *,
        extraction_run_id: str,
        evidence_item_ids: list[str],
        subject_kind: str,
        subject_key: str,
        claim_type: str,
        payload: dict[str, Any],
        confidence: str,
    ) -> str:
        if not isinstance(evidence_item_ids, list) or not evidence_item_ids:
            raise ValidationError("evidence_item_ids must contain at least one evidence item")
        if not isinstance(payload, dict):
            raise ValidationError("payload must be an object")
        confidence = require_decimal_string(confidence, "confidence", minimum=__import__("decimal").Decimal("0"), maximum=__import__("decimal").Decimal("1"))
        claim_id = _new_id()
        unique_evidence_ids = list(dict.fromkeys(evidence_item_ids))
        with self.database.transaction() as connection:
            run = connection.execute(
                "SELECT project_id FROM extraction_runs WHERE id = ?", (extraction_run_id,)
            ).fetchone()
            if run is None:
                raise NotFoundError("extraction run was not found")
            evidence_rows = connection.execute(
                f"""
                SELECT evidence.id, document.project_id
                FROM evidence_items AS evidence
                JOIN document_revisions AS document ON document.id = evidence.document_revision_id
                WHERE evidence.id IN ({','.join('?' for _ in unique_evidence_ids)})
                """,
                unique_evidence_ids,
            ).fetchall()
            if len(evidence_rows) != len(unique_evidence_ids):
                raise NotFoundError("at least one evidence item was not found")
            if any(row["project_id"] != run["project_id"] for row in evidence_rows):
                raise ValidationError("claim evidence must belong to the extraction run project")
            connection.execute(
                """
                INSERT INTO extraction_claims(
                    id, extraction_run_id, subject_kind, subject_key, claim_type,
                    payload_json, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    extraction_run_id,
                    _non_empty(subject_kind, "subject_kind"),
                    _non_empty(subject_key, "subject_key"),
                    _non_empty(claim_type, "claim_type"),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    confidence,
                    _now(),
                ),
            )
            for index, evidence_id in enumerate(unique_evidence_ids):
                connection.execute(
                    "INSERT INTO claim_evidence(claim_id, evidence_item_id, is_primary) VALUES (?, ?, ?)",
                    (claim_id, evidence_id, 1 if index == 0 else 0),
                )
        return claim_id

    def get_extraction_claim(self, claim_id: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT claim.id, claim.subject_kind, claim.subject_key, claim.claim_type,
                       claim.payload_json, claim.confidence, run.plugin_id, run.plugin_version,
                       evidence.sheet_id, evidence.page_number, evidence.geometry_json, evidence.text_span,
                       document.sha256 AS document_sha256
                FROM extraction_claims AS claim
                JOIN extraction_runs AS run ON run.id = claim.extraction_run_id
                JOIN claim_evidence AS link ON link.claim_id = claim.id AND link.is_primary = 1
                JOIN evidence_items AS evidence ON evidence.id = link.evidence_item_id
                JOIN document_revisions AS document ON document.id = evidence.document_revision_id
                WHERE claim.id = ?
                """,
                (claim_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("extraction claim was not found")
            result = dict(row)
            result["payload"] = json.loads(result.pop("payload_json"))
            result["geometry"] = json.loads(result.pop("geometry_json")) if result["geometry_json"] else None
            result.pop("geometry_json", None)
            return result

    def get_quantity_assertion(self, assertion_id: str) -> dict[str, Any]:
        """Return an immutable assertion with its state derived from review events."""
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT id, revision_set_id, subject_kind, subject_key, uom, quantity,
                       scope_state, supersedes_assertion_id, created_at
                FROM quantity_assertions
                WHERE id = ?
                """,
                (assertion_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("quantity assertion was not found")
            result = dict(row)
            review = connection.execute(
                """
                SELECT outcome FROM quantity_review_events
                WHERE quantity_assertion_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (assertion_id,),
            ).fetchone()
            result["state"] = "CANDIDATE" if review is None else review["outcome"]
            return result

    def get_takeoff_version(self, takeoff_version_id: str) -> dict[str, Any]:
        """Return a takeoff snapshot and derive status from immutable approval events."""
        with self.database.connection() as connection:
            version = connection.execute(
                "SELECT id, project_id, revision_set_id, created_at FROM takeoff_versions WHERE id = ?",
                (takeoff_version_id,),
            ).fetchone()
            if version is None:
                raise NotFoundError("takeoff version was not found")
            approved = connection.execute(
                "SELECT 1 FROM takeoff_approval_events WHERE takeoff_version_id = ?",
                (takeoff_version_id,),
            ).fetchone()
            lines = connection.execute(
                """
                SELECT id, source_quantity_assertion_id, subject_kind, subject_key,
                       uom, quantity, scope_state, created_at
                FROM takeoff_lines
                WHERE takeoff_version_id = ?
                ORDER BY rowid
                """,
                (takeoff_version_id,),
            ).fetchall()
            result = dict(version)
            result["status"] = "APPROVED" if approved is not None else "DRAFT"
            result["lines"] = [dict(line) for line in lines]
            return result

    def get_revision_set_impact(self, successor_revision_set_id: str) -> dict[str, Any]:
        with self.database.connection() as connection:
            impact = connection.execute(
                """
                SELECT successor_revision_set_id, predecessor_revision_set_id,
                       triggering_document_revision_id, created_at
                FROM revision_set_impacts
                WHERE successor_revision_set_id = ?
                """,
                (successor_revision_set_id,),
            ).fetchone()
            if impact is None:
                raise NotFoundError("revision set impact was not found")
            return dict(impact)

    def get_bid_release(self, bid_release_id: str) -> dict[str, Any]:
        """Return the immutable release snapshot and its recorded role decisions."""
        with self.database.connection() as connection:
            release = connection.execute(
                """
                SELECT id, project_id, takeoff_version_id, estimate_version_id, created_at
                FROM bid_releases WHERE id = ?
                """,
                (bid_release_id,),
            ).fetchone()
            if release is None:
                raise NotFoundError("bid release was not found")
            approvals = connection.execute(
                """
                SELECT event.id, grant.role, event.actor_id, event.rationale, event.created_at
                FROM bid_release_approval_events AS event
                JOIN role_grants AS grant ON grant.id = event.role_grant_id
                WHERE event.bid_release_id = ?
                ORDER BY event.created_at, event.rowid
                """,
                (bid_release_id,),
            ).fetchall()
            estimate_lines = connection.execute(
                """
                SELECT line.id, line.takeoff_line_id, line.quote_line_id, line.quantity,
                       line.unit_price, line.extended_price, line.uom
                FROM estimate_lines AS line
                JOIN bid_releases AS release_ref ON release_ref.estimate_version_id = line.estimate_version_id
                WHERE release_ref.id = ?
                ORDER BY line.rowid
                """,
                (bid_release_id,),
            ).fetchall()
            voided = connection.execute(
                "SELECT 1 FROM bid_release_void_events WHERE bid_release_id = ?",
                (bid_release_id,),
            ).fetchone()
            roles = {row["role"] for row in approvals}
            result = dict(release)
            result["status"] = "VOIDED" if voided is not None else (
                "RELEASED" if {"AUTHORIZED_BIDDER", "SUBMITTER"}.issubset(roles) else "PENDING"
            )
            result["approval_events"] = [dict(approval) for approval in approvals]
            result["estimate_lines"] = [dict(line) for line in estimate_lines]
            return result

    def get_idempotency_request(self, *, operation_scope: str, idempotency_key: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT request_fingerprint, result_resource_type, result_resource_id, result_payload_json
                FROM idempotency_requests
                WHERE operation_scope = ? AND idempotency_key = ?
                """,
                (_non_empty(operation_scope, "operation_scope"), _non_empty(idempotency_key, "idempotency_key")),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["result_payload"] = json.loads(result.pop("result_payload_json"))
            return result

    def record_idempotency_request(
        self,
        *,
        operation_scope: str,
        idempotency_key: str,
        request_fingerprint: str,
        result_resource_type: str,
        result_resource_id: str,
        result_payload: dict[str, Any],
    ) -> None:
        normalized_fingerprint = self._validate_sha256(request_fingerprint, "request_fingerprint")
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT request_fingerprint FROM idempotency_requests
                WHERE operation_scope = ? AND idempotency_key = ?
                """,
                (_non_empty(operation_scope, "operation_scope"), _non_empty(idempotency_key, "idempotency_key")),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != normalized_fingerprint:
                    raise ConflictError("idempotency key is already bound to a different request")
                return
            connection.execute(
                """
                INSERT INTO idempotency_requests(
                    id, operation_scope, idempotency_key, request_fingerprint,
                    result_resource_type, result_resource_id, result_payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _new_id(),
                    _non_empty(operation_scope, "operation_scope"),
                    _non_empty(idempotency_key, "idempotency_key"),
                    normalized_fingerprint,
                    _non_empty(result_resource_type, "result_resource_type"),
                    _non_empty(result_resource_id, "result_resource_id"),
                    json.dumps(result_payload, sort_keys=True, separators=(",", ":")),
                    _now(),
                ),
            )

    @staticmethod
    def _require_project(connection: Any, project_id: str) -> Any:
        row = connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise NotFoundError("project was not found")
        return row

    @staticmethod
    def _require_actor(connection: Any, actor_id: str) -> Any:
        row = connection.execute("SELECT id FROM actors WHERE id = ?", (actor_id,)).fetchone()
        if row is None:
            raise NotFoundError("actor was not found")
        return row

    @staticmethod
    def _require_revision_set(connection: Any, revision_set_id: str) -> Any:
        row = connection.execute(
            "SELECT id, project_id, name, status, created_at, frozen_at FROM revision_sets WHERE id = ?",
            (revision_set_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("revision set was not found")
        return row

    @staticmethod
    def _validate_geometry(geometry: dict[str, Any]) -> None:
        if not isinstance(geometry, dict):
            raise ValidationError("geometry must be an object")
        bbox = geometry.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise ValidationError("geometry.bbox must contain four numeric coordinates")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in bbox):
            raise ValidationError("geometry.bbox must contain finite numeric coordinates")
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise ValidationError("geometry.bbox must have positive width and height")

    @staticmethod
    def _validate_sha256(value: str, field_name: str) -> str:
        normalized_value = _non_empty(value, field_name).lower()
        if not SHA256_PATTERN.fullmatch(normalized_value):
            raise ValidationError(f"{field_name} must be a 64-character lowercase hexadecimal SHA-256 digest")
        return normalized_value
