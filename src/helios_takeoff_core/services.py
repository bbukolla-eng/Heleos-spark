"""Lifecycle services that keep workers from mutating canonical takeoff decisions."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from .contracts import require_decimal_string, require_text
from .errors import AuthorizationError, ImmutableStateError, NotFoundError, PreconditionError, ValidationError
from .repository import TakeoffRepository


QUANTITY_STATES = {"CANDIDATE", "VERIFIED", "APPROVED", "REJECTED"}
SCOPE_STATES = {"NEW", "EXISTING", "DEMOLITION", "RELOCATE", "UNKNOWN"}


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TakeoffService:
    """Creates and evaluates quantity assertions using append-only events."""

    def __init__(self, repository: TakeoffRepository) -> None:
        self.repository = repository
        self.database = repository.database

    def create_quantity_assertion(
        self,
        *,
        revision_set_id: str,
        subject_kind: str,
        subject_key: str,
        uom: str,
        quantity: str,
        scope_state: str,
        evidence_allocations: list[dict[str, Any]],
        supersedes_assertion_id: str | None = None,
    ) -> str:
        if not isinstance(evidence_allocations, list) or not evidence_allocations:
            raise ValidationError("evidence_allocations must contain at least one allocation")
        quantity = require_decimal_string(quantity, "quantity", minimum=Decimal("0.0000001"))
        scope_state = require_text(scope_state, "scope_state").upper()
        if scope_state not in SCOPE_STATES:
            raise ValidationError("unsupported scope_state")
        assertion_id = _new_id()
        with self.database.transaction() as connection:
            revision_set = connection.execute(
                "SELECT project_id, status FROM revision_sets WHERE id = ?", (revision_set_id,)
            ).fetchone()
            if revision_set is None:
                raise NotFoundError("revision set was not found")
            if revision_set["status"] != "FROZEN":
                raise PreconditionError("quantity assertions require a frozen revision set")
            if supersedes_assertion_id is not None:
                superseded = connection.execute(
                    "SELECT revision_set_id FROM quantity_assertions WHERE id = ?",
                    (supersedes_assertion_id,),
                ).fetchone()
                if superseded is None:
                    raise NotFoundError("superseded quantity assertion was not found")
                if superseded["revision_set_id"] != revision_set_id:
                    raise ValidationError("a quantity assertion can only supersede one in the same revision set")
            connection.execute(
                """
                INSERT INTO quantity_assertions(
                    id, revision_set_id, subject_kind, subject_key, uom, quantity,
                    scope_state, supersedes_assertion_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assertion_id,
                    revision_set_id,
                    require_text(subject_kind, "subject_kind"),
                    require_text(subject_key, "subject_key"),
                    require_text(uom, "uom").upper(),
                    quantity,
                    scope_state,
                    supersedes_assertion_id,
                    _now(),
                ),
            )
            for allocation in evidence_allocations:
                self._insert_evidence_allocation(connection, assertion_id, quantity, allocation)
        return assertion_id

    def record_quantity_review(
        self,
        *,
        assertion_id: str,
        actor_id: str,
        outcome: str,
        rationale: str,
    ) -> str:
        outcome = require_text(outcome, "outcome").upper()
        if outcome not in {"VERIFIED", "APPROVED", "REJECTED"}:
            raise ValidationError("outcome must be VERIFIED, APPROVED, or REJECTED")
        review_id = _new_id()
        with self.database.transaction() as connection:
            assertion = connection.execute(
                """
                SELECT assertion.id, revision_set.project_id
                FROM quantity_assertions AS assertion
                JOIN revision_sets AS revision_set ON revision_set.id = assertion.revision_set_id
                WHERE assertion.id = ?
                """,
                (assertion_id,),
            ).fetchone()
            if assertion is None:
                raise NotFoundError("quantity assertion was not found")
            grant_id = self._require_active_role_grant(
                connection,
                actor_id=actor_id,
                project_id=assertion["project_id"],
                role="ESTIMATOR_REVIEWER",
            )
            current_state = self._quantity_state(connection, assertion_id)
            if current_state in {"APPROVED", "REJECTED"}:
                raise ImmutableStateError("terminal quantity assertions cannot receive further reviews")
            if current_state == "CANDIDATE" and outcome not in {"VERIFIED", "REJECTED"}:
                raise PreconditionError("a candidate must be VERIFIED before it can be APPROVED")
            if current_state == "VERIFIED" and outcome not in {"APPROVED", "REJECTED"}:
                raise PreconditionError("a verified assertion can only be APPROVED or REJECTED")
            connection.execute(
                """
                INSERT INTO quantity_review_events(
                    id, quantity_assertion_id, actor_id, role_grant_id, outcome, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (review_id, assertion_id, actor_id, grant_id, outcome, require_text(rationale, "rationale"), _now()),
            )
        return review_id

    def create_takeoff_version(self, *, revision_set_id: str) -> str:
        """Snapshot one frozen baseline's approved assertions into immutable takeoff lines."""
        takeoff_version_id = _new_id()
        with self.database.transaction() as connection:
            revision_set = connection.execute(
                "SELECT id, project_id, status FROM revision_sets WHERE id = ?", (revision_set_id,)
            ).fetchone()
            if revision_set is None:
                raise NotFoundError("revision set was not found")
            if revision_set["status"] != "FROZEN":
                raise PreconditionError("takeoff versions require a frozen revision set")
            unresolved = connection.execute(
                """
                SELECT conflict.id
                FROM conflicts AS conflict
                LEFT JOIN conflict_dispositions AS disposition ON disposition.conflict_id = conflict.id
                WHERE conflict.revision_set_id = ? AND disposition.id IS NULL
                LIMIT 1
                """,
                (revision_set_id,),
            ).fetchone()
            if unresolved is not None:
                raise PreconditionError("unresolved conflicts block takeoff creation")
            assertions = connection.execute(
                """
                SELECT id, subject_kind, subject_key, uom, quantity, scope_state
                FROM quantity_assertions
                WHERE revision_set_id = ?
                ORDER BY created_at, rowid
                """,
                (revision_set_id,),
            ).fetchall()
            if not assertions:
                raise PreconditionError("takeoff versions require at least one approved quantity assertion")
            approved_assertions: list[Any] = []
            for assertion in assertions:
                state = self._quantity_state(connection, assertion["id"])
                if state in {"CANDIDATE", "VERIFIED"}:
                    raise PreconditionError("all quantity assertions must be approved or rejected before snapshot")
                if state != "APPROVED":
                    continue
                approved_successor = connection.execute(
                    """
                    SELECT successor.id
                    FROM quantity_assertions AS successor
                    WHERE successor.supersedes_assertion_id = ?
                    """,
                    (assertion["id"],),
                ).fetchone()
                if approved_successor is not None and self._quantity_state(connection, approved_successor["id"]) == "APPROVED":
                    continue
                evidence_count = connection.execute(
                    "SELECT COUNT(*) FROM quantity_evidence_allocations WHERE quantity_assertion_id = ?",
                    (assertion["id"],),
                ).fetchone()[0]
                if evidence_count == 0:
                    raise PreconditionError("approved quantity assertion lacks evidence allocations")
                approved_assertions.append(assertion)
            if not approved_assertions:
                raise PreconditionError("takeoff versions require at least one approved quantity assertion")
            connection.execute(
                """
                INSERT INTO takeoff_versions(id, project_id, revision_set_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (takeoff_version_id, revision_set["project_id"], revision_set_id, _now()),
            )
            for assertion in approved_assertions:
                connection.execute(
                    """
                    INSERT INTO takeoff_lines(
                        id, takeoff_version_id, source_quantity_assertion_id, subject_kind,
                        subject_key, uom, quantity, scope_state, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id(),
                        takeoff_version_id,
                        assertion["id"],
                        assertion["subject_kind"],
                        assertion["subject_key"],
                        assertion["uom"],
                        assertion["quantity"],
                        assertion["scope_state"],
                        _now(),
                    ),
                )
        return takeoff_version_id

    def approve_takeoff_version(self, *, takeoff_version_id: str, actor_id: str, rationale: str) -> str:
        approval_id = _new_id()
        with self.database.transaction() as connection:
            takeoff = connection.execute(
                "SELECT project_id, revision_set_id FROM takeoff_versions WHERE id = ?",
                (takeoff_version_id,),
            ).fetchone()
            if takeoff is None:
                raise NotFoundError("takeoff version was not found")
            existing = connection.execute(
                "SELECT id FROM takeoff_approval_events WHERE takeoff_version_id = ?",
                (takeoff_version_id,),
            ).fetchone()
            if existing is not None:
                raise ImmutableStateError("takeoff version is already approved")
            unresolved = connection.execute(
                """
                SELECT conflict.id
                FROM conflicts AS conflict
                LEFT JOIN conflict_dispositions AS disposition ON disposition.conflict_id = conflict.id
                WHERE conflict.revision_set_id = ? AND disposition.id IS NULL
                LIMIT 1
                """,
                (takeoff["revision_set_id"],),
            ).fetchone()
            if unresolved is not None:
                raise PreconditionError("unresolved conflicts block takeoff approval")
            grant_id = self._require_active_role_grant(
                connection,
                actor_id=actor_id,
                project_id=takeoff["project_id"],
                role="ESTIMATOR_REVIEWER",
            )
            connection.execute(
                """
                INSERT INTO takeoff_approval_events(
                    id, takeoff_version_id, actor_id, role_grant_id, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (approval_id, takeoff_version_id, actor_id, grant_id, require_text(rationale, "rationale"), _now()),
            )
        return approval_id

    def create_successor_revision_set(
        self,
        *,
        predecessor_revision_set_id: str,
        triggering_document_revision_id: str,
        name: str | None = None,
    ) -> str:
        """Create a new open baseline; the predecessor and its approvals remain unchanged."""
        successor_id = _new_id()
        with self.database.transaction() as connection:
            predecessor = connection.execute(
                "SELECT id, project_id, name, status FROM revision_sets WHERE id = ?",
                (predecessor_revision_set_id,),
            ).fetchone()
            if predecessor is None:
                raise NotFoundError("predecessor revision set was not found")
            if predecessor["status"] != "FROZEN":
                raise PreconditionError("a successor requires a frozen predecessor revision set")
            triggering_document = connection.execute(
                """
                SELECT id, project_id, document_number, supersedes_document_revision_id
                FROM document_revisions WHERE id = ?
                """,
                (triggering_document_revision_id,),
            ).fetchone()
            if triggering_document is None:
                raise NotFoundError("triggering document revision was not found")
            if triggering_document["project_id"] != predecessor["project_id"]:
                raise ValidationError("triggering document must belong to the predecessor project")
            successor_name = name or f"{predecessor['name']}::{triggering_document['document_number']}"
            connection.execute(
                """
                INSERT INTO revision_sets(id, project_id, name, status, created_at)
                VALUES (?, ?, ?, 'OPEN', ?)
                """,
                (successor_id, predecessor["project_id"], require_text(successor_name, "name"), _now()),
            )
            prior_documents = connection.execute(
                """
                SELECT document_revision_id FROM revision_set_documents
                WHERE revision_set_id = ?
                """,
                (predecessor_revision_set_id,),
            ).fetchall()
            for prior_document in prior_documents:
                if prior_document["document_revision_id"] == triggering_document["supersedes_document_revision_id"]:
                    continue
                connection.execute(
                    """
                    INSERT INTO revision_set_documents(revision_set_id, document_revision_id, included_at)
                    VALUES (?, ?, ?)
                    """,
                    (successor_id, prior_document["document_revision_id"], _now()),
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO revision_set_documents(revision_set_id, document_revision_id, included_at)
                VALUES (?, ?, ?)
                """,
                (successor_id, triggering_document_revision_id, _now()),
            )
            connection.execute(
                """
                INSERT INTO revision_set_impacts(
                    successor_revision_set_id, predecessor_revision_set_id,
                    triggering_document_revision_id, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (successor_id, predecessor_revision_set_id, triggering_document_revision_id, _now()),
            )
        return successor_id

    def create_conflict(
        self,
        *,
        revision_set_id: str,
        conflict_type: str,
        severity: str,
        description: str,
    ) -> str:
        severity = require_text(severity, "severity").upper()
        if severity not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            raise ValidationError("unsupported conflict severity")
        conflict_id = _new_id()
        with self.database.transaction() as connection:
            revision_set = connection.execute("SELECT id FROM revision_sets WHERE id = ?", (revision_set_id,)).fetchone()
            if revision_set is None:
                raise NotFoundError("revision set was not found")
            connection.execute(
                """
                INSERT INTO conflicts(id, revision_set_id, conflict_type, severity, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict_id,
                    revision_set_id,
                    require_text(conflict_type, "conflict_type"),
                    severity,
                    require_text(description, "description"),
                    _now(),
                ),
            )
        return conflict_id

    def create_rfi(self, *, revision_set_id: str, question: str) -> str:
        rfi_id = _new_id()
        with self.database.transaction() as connection:
            revision_set = connection.execute("SELECT id FROM revision_sets WHERE id = ?", (revision_set_id,)).fetchone()
            if revision_set is None:
                raise NotFoundError("revision set was not found")
            connection.execute(
                "INSERT INTO rfis(id, revision_set_id, question, created_at) VALUES (?, ?, ?, ?)",
                (rfi_id, revision_set_id, require_text(question, "question"), _now()),
            )
        return rfi_id

    def record_rfi_response(
        self,
        *,
        rfi_id: str,
        response_document_revision_id: str,
        response_text: str,
    ) -> str:
        response_id = _new_id()
        with self.database.transaction() as connection:
            rfi = connection.execute(
                """
                SELECT rfi.id, revision_set.project_id
                FROM rfis AS rfi
                JOIN revision_sets AS revision_set ON revision_set.id = rfi.revision_set_id
                WHERE rfi.id = ?
                """,
                (rfi_id,),
            ).fetchone()
            if rfi is None:
                raise NotFoundError("RFI was not found")
            response_document = connection.execute(
                "SELECT project_id, document_type FROM document_revisions WHERE id = ?",
                (response_document_revision_id,),
            ).fetchone()
            if response_document is None:
                raise NotFoundError("RFI response document revision was not found")
            if response_document["project_id"] != rfi["project_id"]:
                raise ValidationError("RFI response document must belong to the same project")
            if response_document["document_type"] not in {"RFI_RESPONSE", "ADDENDUM"}:
                raise ValidationError("formal RFI responses must reference RFI_RESPONSE or ADDENDUM documents")
            connection.execute(
                """
                INSERT INTO rfi_responses(id, rfi_id, response_document_revision_id, response_text, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (response_id, rfi_id, response_document_revision_id, require_text(response_text, "response_text"), _now()),
            )
        return response_id

    def disposition_conflict(
        self,
        *,
        conflict_id: str,
        actor_id: str,
        rfi_response_id: str,
        rationale: str,
    ) -> str:
        disposition_id = _new_id()
        with self.database.transaction() as connection:
            conflict = connection.execute(
                """
                SELECT conflict.id, conflict.revision_set_id, revision_set.project_id
                FROM conflicts AS conflict
                JOIN revision_sets AS revision_set ON revision_set.id = conflict.revision_set_id
                WHERE conflict.id = ?
                """,
                (conflict_id,),
            ).fetchone()
            if conflict is None:
                raise NotFoundError("conflict was not found")
            existing = connection.execute(
                "SELECT id FROM conflict_dispositions WHERE conflict_id = ?", (conflict_id,)
            ).fetchone()
            if existing is not None:
                raise ImmutableStateError("conflict is already dispositioned")
            response = connection.execute(
                """
                SELECT response.id
                FROM rfi_responses AS response
                JOIN rfis AS rfi ON rfi.id = response.rfi_id
                WHERE response.id = ? AND rfi.revision_set_id = ?
                """,
                (rfi_response_id, conflict["revision_set_id"]),
            ).fetchone()
            if response is None:
                raise ValidationError("RFI response must belong to the same revision set as the conflict")
            grant_id = self._require_active_role_grant(
                connection,
                actor_id=actor_id,
                project_id=conflict["project_id"],
                role="ESTIMATOR_REVIEWER",
            )
            connection.execute(
                """
                INSERT INTO conflict_dispositions(
                    id, conflict_id, rfi_response_id, actor_id, role_grant_id, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    disposition_id,
                    conflict_id,
                    rfi_response_id,
                    actor_id,
                    grant_id,
                    require_text(rationale, "rationale"),
                    _now(),
                ),
            )
        return disposition_id

    def create_supplier(self, *, project_id: str, name: str) -> str:
        supplier_id = _new_id()
        with self.database.transaction() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
            if project is None:
                raise NotFoundError("project was not found")
            connection.execute(
                "INSERT INTO suppliers(id, project_id, name, created_at) VALUES (?, ?, ?, ?)",
                (supplier_id, project_id, require_text(name, "name"), _now()),
            )
        return supplier_id

    def create_quote_revision(
        self,
        *,
        project_id: str,
        supplier_id: str,
        source_document_revision_id: str,
        quote_number: str,
        issued_at: str,
        valid_through: str,
        currency: str,
        terms: str,
    ) -> str:
        quote_id = _new_id()
        try:
            issued_date = date.fromisoformat(issued_at)
            expiry_date = date.fromisoformat(valid_through)
        except (TypeError, ValueError) as error:
            raise ValidationError("issued_at and valid_through must use ISO YYYY-MM-DD dates") from error
        if expiry_date < issued_date:
            raise ValidationError("valid_through cannot precede issued_at")
        currency = require_text(currency, "currency").upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValidationError("currency must be a three-letter ISO code")
        with self.database.transaction() as connection:
            supplier = connection.execute(
                "SELECT project_id FROM suppliers WHERE id = ?", (supplier_id,)
            ).fetchone()
            if supplier is None:
                raise NotFoundError("supplier was not found")
            document = connection.execute(
                "SELECT project_id FROM document_revisions WHERE id = ?",
                (source_document_revision_id,),
            ).fetchone()
            if document is None:
                raise NotFoundError("quote source document revision was not found")
            if supplier["project_id"] != project_id or document["project_id"] != project_id:
                raise ValidationError("supplier and source document must belong to the quote project")
            connection.execute(
                """
                INSERT INTO quote_revisions(
                    id, project_id, supplier_id, source_document_revision_id, quote_number,
                    issued_at, valid_through, currency, terms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quote_id,
                    project_id,
                    supplier_id,
                    source_document_revision_id,
                    require_text(quote_number, "quote_number"),
                    issued_at,
                    valid_through,
                    currency,
                    require_text(terms, "terms"),
                    _now(),
                ),
            )
        return quote_id

    def add_quote_line(
        self,
        *,
        quote_revision_id: str,
        supplier_part_number: str,
        description: str,
        uom: str,
        unit_price: str,
    ) -> str:
        quote_line_id = _new_id()
        unit_price = require_decimal_string(unit_price, "unit_price", minimum=Decimal("0"))
        with self.database.transaction() as connection:
            quote = connection.execute("SELECT id FROM quote_revisions WHERE id = ?", (quote_revision_id,)).fetchone()
            if quote is None:
                raise NotFoundError("quote revision was not found")
            connection.execute(
                """
                INSERT INTO quote_lines(
                    id, quote_revision_id, supplier_part_number, description, uom, unit_price, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quote_line_id,
                    quote_revision_id,
                    require_text(supplier_part_number, "supplier_part_number"),
                    require_text(description, "description"),
                    require_text(uom, "uom").upper(),
                    unit_price,
                    _now(),
                ),
            )
        return quote_line_id

    def create_estimate_version(
        self,
        *,
        takeoff_version_id: str,
        line_inputs: list[dict[str, Any]],
    ) -> str:
        if not isinstance(line_inputs, list) or not line_inputs:
            raise ValidationError("line_inputs must contain one quote selection per takeoff line")
        estimate_version_id = _new_id()
        with self.database.transaction() as connection:
            takeoff = connection.execute(
                "SELECT id, project_id FROM takeoff_versions WHERE id = ?", (takeoff_version_id,)
            ).fetchone()
            if takeoff is None:
                raise NotFoundError("takeoff version was not found")
            if connection.execute(
                "SELECT 1 FROM takeoff_approval_events WHERE takeoff_version_id = ?", (takeoff_version_id,)
            ).fetchone() is None:
                raise PreconditionError("estimate versions require an approved takeoff version")
            takeoff_lines = {
                row["id"]: row
                for row in connection.execute(
                    "SELECT id, uom, quantity FROM takeoff_lines WHERE takeoff_version_id = ?",
                    (takeoff_version_id,),
                ).fetchall()
            }
            if set(item.get("takeoff_line_id") for item in line_inputs if isinstance(item, dict)) != set(takeoff_lines):
                raise ValidationError("line_inputs must select exactly one quote line for every takeoff line")
            connection.execute(
                "INSERT INTO estimate_versions(id, project_id, takeoff_version_id, created_at) VALUES (?, ?, ?, ?)",
                (estimate_version_id, takeoff["project_id"], takeoff_version_id, _now()),
            )
            for line_input in line_inputs:
                if not isinstance(line_input, dict):
                    raise ValidationError("each estimate line input must be an object")
                takeoff_line_id = line_input.get("takeoff_line_id")
                quote_line_id = line_input.get("quote_line_id")
                if not isinstance(takeoff_line_id, str) or not isinstance(quote_line_id, str):
                    raise ValidationError("takeoff_line_id and quote_line_id are required")
                quote_line = connection.execute(
                    """
                    SELECT line.id, line.uom, line.unit_price, quote.project_id
                    FROM quote_lines AS line
                    JOIN quote_revisions AS quote ON quote.id = line.quote_revision_id
                    WHERE line.id = ?
                    """,
                    (quote_line_id,),
                ).fetchone()
                if quote_line is None:
                    raise NotFoundError("quote line was not found")
                takeoff_line = takeoff_lines[takeoff_line_id]
                if quote_line["project_id"] != takeoff["project_id"]:
                    raise ValidationError("quote line must belong to the takeoff project")
                if quote_line["uom"] != takeoff_line["uom"]:
                    raise ValidationError("quote line UOM must match takeoff line UOM")
                extended_price = format(Decimal(takeoff_line["quantity"]) * Decimal(quote_line["unit_price"]), "f")
                connection.execute(
                    """
                    INSERT INTO estimate_lines(
                        id, estimate_version_id, takeoff_line_id, quote_line_id, uom,
                        quantity, unit_price, extended_price, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id(),
                        estimate_version_id,
                        takeoff_line_id,
                        quote_line_id,
                        takeoff_line["uom"],
                        takeoff_line["quantity"],
                        quote_line["unit_price"],
                        extended_price,
                        _now(),
                    ),
                )
        return estimate_version_id

    def approve_estimate_version(self, *, estimate_version_id: str, actor_id: str, rationale: str) -> str:
        approval_id = _new_id()
        with self.database.transaction() as connection:
            estimate = connection.execute(
                "SELECT project_id FROM estimate_versions WHERE id = ?", (estimate_version_id,)
            ).fetchone()
            if estimate is None:
                raise NotFoundError("estimate version was not found")
            if connection.execute(
                "SELECT id FROM estimate_approval_events WHERE estimate_version_id = ?", (estimate_version_id,)
            ).fetchone() is not None:
                raise ImmutableStateError("estimate version is already approved")
            grant_id = self._require_active_role_grant(
                connection,
                actor_id=actor_id,
                project_id=estimate["project_id"],
                role="ESTIMATOR_REVIEWER",
            )
            connection.execute(
                """
                INSERT INTO estimate_approval_events(
                    id, estimate_version_id, actor_id, role_grant_id, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (approval_id, estimate_version_id, actor_id, grant_id, require_text(rationale, "rationale"), _now()),
            )
        return approval_id

    def release_bid(
        self,
        *,
        takeoff_version_id: str,
        estimate_version_id: str,
        authorized_bidder_actor_id: str,
        submitter_actor_id: str,
        rationale: str,
    ) -> str:
        """Atomically record the two role decisions that make an estimate an as-bid release."""
        release_id = _new_id()
        with self.database.transaction() as connection:
            takeoff = connection.execute(
                "SELECT project_id FROM takeoff_versions WHERE id = ?", (takeoff_version_id,)
            ).fetchone()
            if takeoff is None:
                raise NotFoundError("takeoff version was not found")
            if connection.execute(
                "SELECT 1 FROM takeoff_approval_events WHERE takeoff_version_id = ?", (takeoff_version_id,)
            ).fetchone() is None:
                raise PreconditionError("bid release requires an approved takeoff version")
            estimate = connection.execute(
                """
                SELECT id, project_id, takeoff_version_id FROM estimate_versions WHERE id = ?
                """,
                (estimate_version_id,),
            ).fetchone()
            if estimate is None:
                raise NotFoundError("estimate version was not found")
            if estimate["project_id"] != takeoff["project_id"] or estimate["takeoff_version_id"] != takeoff_version_id:
                raise ValidationError("estimate version must belong to the same approved takeoff version")
            if connection.execute(
                "SELECT 1 FROM estimate_approval_events WHERE estimate_version_id = ?", (estimate_version_id,)
            ).fetchone() is None:
                raise PreconditionError("bid release requires an approved estimate version")
            today = date.today().isoformat()
            expired_quote = connection.execute(
                """
                SELECT quote.id
                FROM estimate_lines AS line
                JOIN quote_lines AS quote_line ON quote_line.id = line.quote_line_id
                JOIN quote_revisions AS quote ON quote.id = quote_line.quote_revision_id
                WHERE line.estimate_version_id = ? AND quote.valid_through < ?
                LIMIT 1
                """,
                (estimate_version_id, today),
            ).fetchone()
            if expired_quote is not None:
                raise PreconditionError("bid release requires all selected quote inputs to remain valid")
            bidder_grant_id = self._require_active_role_grant(
                connection,
                actor_id=authorized_bidder_actor_id,
                project_id=takeoff["project_id"],
                role="AUTHORIZED_BIDDER",
            )
            submitter_grant_id = self._require_active_role_grant(
                connection,
                actor_id=submitter_actor_id,
                project_id=takeoff["project_id"],
                role="SUBMITTER",
            )
            release_rationale = require_text(rationale, "rationale")
            connection.execute(
                """
                INSERT INTO bid_releases(id, project_id, takeoff_version_id, estimate_version_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (release_id, takeoff["project_id"], takeoff_version_id, estimate_version_id, _now()),
            )
            for actor_id, role_grant_id in (
                (authorized_bidder_actor_id, bidder_grant_id),
                (submitter_actor_id, submitter_grant_id),
            ):
                connection.execute(
                    """
                    INSERT INTO bid_release_approval_events(
                        id, bid_release_id, actor_id, role_grant_id, rationale, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (_new_id(), release_id, actor_id, role_grant_id, release_rationale, _now()),
                )
        return release_id

    def void_bid_release(self, *, bid_release_id: str, actor_id: str, rationale: str) -> str:
        void_id = _new_id()
        with self.database.transaction() as connection:
            release = connection.execute("SELECT project_id FROM bid_releases WHERE id = ?", (bid_release_id,)).fetchone()
            if release is None:
                raise NotFoundError("bid release was not found")
            if connection.execute("SELECT id FROM bid_release_void_events WHERE bid_release_id = ?", (bid_release_id,)).fetchone() is not None:
                raise ImmutableStateError("bid release is already voided")
            grant_id = self._require_active_role_grant(
                connection,
                actor_id=actor_id,
                project_id=release["project_id"],
                role="AUTHORIZED_BIDDER",
            )
            connection.execute(
                """
                INSERT INTO bid_release_void_events(
                    id, bid_release_id, actor_id, role_grant_id, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (void_id, bid_release_id, actor_id, grant_id, require_text(rationale, "rationale"), _now()),
            )
        return void_id

    def _insert_evidence_allocation(
        self,
        connection: Any,
        assertion_id: str,
        assertion_quantity: str,
        allocation: dict[str, Any],
    ) -> None:
        if not isinstance(allocation, dict):
            raise ValidationError("each evidence allocation must be an object")
        evidence_item_id = allocation.get("evidence_item_id")
        if not isinstance(evidence_item_id, str) or not evidence_item_id:
            raise ValidationError("evidence_item_id is required")
        allocation_quantity = require_decimal_string(
            allocation.get("allocation_quantity", assertion_quantity),
            "allocation_quantity",
            minimum=Decimal("0.0000001"),
        )
        claim_id = allocation.get("claim_id")
        if claim_id is not None and (not isinstance(claim_id, str) or not claim_id):
            raise ValidationError("claim_id must be a UUID string when provided")
        evidence = connection.execute(
            "SELECT document_revision_id FROM evidence_items WHERE id = ?", (evidence_item_id,)
        ).fetchone()
        if evidence is None:
            raise NotFoundError("evidence item was not found")
        baseline_match = connection.execute(
            """
            SELECT 1
            FROM quantity_assertions AS assertion
            JOIN revision_set_documents AS baseline ON baseline.revision_set_id = assertion.revision_set_id
            WHERE assertion.id = ? AND baseline.document_revision_id = ?
            """,
            (assertion_id, evidence["document_revision_id"]),
        ).fetchone()
        if baseline_match is None:
            raise PreconditionError("quantity evidence must belong to the frozen revision set baseline")
        if claim_id is not None:
            claim_link = connection.execute(
                """
                SELECT 1 FROM claim_evidence
                WHERE claim_id = ? AND evidence_item_id = ?
                """,
                (claim_id, evidence_item_id),
            ).fetchone()
            if claim_link is None:
                raise ValidationError("claim_id must cite the same evidence_item_id")
        connection.execute(
            """
            INSERT INTO quantity_evidence_allocations(
                quantity_assertion_id, evidence_item_id, extraction_claim_id, allocation_quantity
            ) VALUES (?, ?, ?, ?)
            """,
            (assertion_id, evidence_item_id, claim_id, allocation_quantity),
        )

    @staticmethod
    def _quantity_state(connection: Any, assertion_id: str) -> str:
        row = connection.execute(
            """
            SELECT outcome FROM quantity_review_events
            WHERE quantity_assertion_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (assertion_id,),
        ).fetchone()
        return "CANDIDATE" if row is None else row["outcome"]

    @staticmethod
    def _require_active_role_grant(connection: Any, *, actor_id: str, project_id: str, role: str) -> str:
        now = _now()
        actor = connection.execute("SELECT id FROM actors WHERE id = ?", (actor_id,)).fetchone()
        if actor is None:
            raise NotFoundError("actor was not found")
        row = connection.execute(
            """
            SELECT grant.id
            FROM role_grants AS grant
            LEFT JOIN role_grant_revocations AS revocation ON revocation.role_grant_id = grant.id
            WHERE grant.actor_id = ?
              AND grant.role = ?
              AND (grant.project_id = ? OR grant.project_id IS NULL)
              AND grant.valid_from <= ?
              AND (grant.valid_until IS NULL OR grant.valid_until >= ?)
              AND revocation.id IS NULL
            ORDER BY CASE WHEN grant.project_id = ? THEN 0 ELSE 1 END, grant.created_at DESC
            LIMIT 1
            """,
            (actor_id, role, project_id, now, now, project_id),
        ).fetchone()
        if row is None:
            raise AuthorizationError(f"actor lacks an active {role} role grant for this project")
        return row["id"]
