CREATE TABLE evidence_items (
    id TEXT PRIMARY KEY,
    document_revision_id TEXT NOT NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
    sheet_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK(page_number > 0),
    geometry_json TEXT,
    text_span TEXT,
    content_sha256 TEXT,
    created_at TEXT NOT NULL,
    CHECK(geometry_json IS NOT NULL OR text_span IS NOT NULL)
);

CREATE TRIGGER evidence_items_are_immutable
BEFORE UPDATE ON evidence_items
BEGIN
    SELECT RAISE(ABORT, 'evidence items are immutable');
END;

CREATE TRIGGER evidence_items_cannot_be_deleted
BEFORE DELETE ON evidence_items
BEGIN
    SELECT RAISE(ABORT, 'evidence items cannot be deleted');
END;

CREATE TABLE extraction_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    plugin_id TEXT NOT NULL,
    plugin_version TEXT NOT NULL,
    configuration_sha256 TEXT NOT NULL,
    input_manifest_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER extraction_runs_are_immutable
BEFORE UPDATE ON extraction_runs
BEGIN
    SELECT RAISE(ABORT, 'extraction runs are immutable');
END;

CREATE TRIGGER extraction_runs_cannot_be_deleted
BEFORE DELETE ON extraction_runs
BEGIN
    SELECT RAISE(ABORT, 'extraction runs cannot be deleted');
END;

CREATE TABLE extraction_claims (
    id TEXT PRIMARY KEY,
    extraction_run_id TEXT NOT NULL REFERENCES extraction_runs(id) ON DELETE RESTRICT,
    subject_kind TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    confidence TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER extraction_claims_are_immutable
BEFORE UPDATE ON extraction_claims
BEGIN
    SELECT RAISE(ABORT, 'extraction claims are immutable');
END;

CREATE TRIGGER extraction_claims_cannot_be_deleted
BEFORE DELETE ON extraction_claims
BEGIN
    SELECT RAISE(ABORT, 'extraction claims cannot be deleted');
END;

CREATE TABLE claim_evidence (
    claim_id TEXT NOT NULL REFERENCES extraction_claims(id) ON DELETE RESTRICT,
    evidence_item_id TEXT NOT NULL REFERENCES evidence_items(id) ON DELETE RESTRICT,
    is_primary INTEGER NOT NULL CHECK(is_primary IN (0, 1)),
    PRIMARY KEY(claim_id, evidence_item_id)
);

CREATE UNIQUE INDEX claim_evidence_one_primary ON claim_evidence(claim_id) WHERE is_primary = 1;

CREATE TRIGGER claim_evidence_is_immutable
BEFORE UPDATE ON claim_evidence
BEGIN
    SELECT RAISE(ABORT, 'claim evidence links are immutable');
END;

CREATE TRIGGER claim_evidence_cannot_be_deleted
BEFORE DELETE ON claim_evidence
BEGIN
    SELECT RAISE(ABORT, 'claim evidence links cannot be deleted');
END;

CREATE TABLE quantity_assertions (
    id TEXT PRIMARY KEY,
    revision_set_id TEXT NOT NULL REFERENCES revision_sets(id) ON DELETE RESTRICT,
    subject_kind TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    uom TEXT NOT NULL,
    quantity TEXT NOT NULL,
    scope_state TEXT NOT NULL CHECK(scope_state IN ('NEW', 'EXISTING', 'DEMOLITION', 'RELOCATE', 'UNKNOWN')),
    supersedes_assertion_id TEXT REFERENCES quantity_assertions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    CHECK(supersedes_assertion_id IS NULL OR supersedes_assertion_id <> id)
);

CREATE TRIGGER quantity_assertions_keep_supersession_in_revision_set
BEFORE INSERT ON quantity_assertions
WHEN NEW.supersedes_assertion_id IS NOT NULL
 AND (SELECT revision_set_id FROM quantity_assertions WHERE id = NEW.supersedes_assertion_id) <> NEW.revision_set_id
BEGIN
    SELECT RAISE(ABORT, 'quantity assertion supersession must stay in one revision set');
END;

CREATE TRIGGER quantity_assertions_are_immutable
BEFORE UPDATE ON quantity_assertions
BEGIN
    SELECT RAISE(ABORT, 'quantity assertions are immutable');
END;

CREATE TRIGGER quantity_assertions_cannot_be_deleted
BEFORE DELETE ON quantity_assertions
BEGIN
    SELECT RAISE(ABORT, 'quantity assertions cannot be deleted');
END;

CREATE TABLE quantity_evidence_allocations (
    quantity_assertion_id TEXT NOT NULL REFERENCES quantity_assertions(id) ON DELETE RESTRICT,
    evidence_item_id TEXT NOT NULL REFERENCES evidence_items(id) ON DELETE RESTRICT,
    extraction_claim_id TEXT REFERENCES extraction_claims(id) ON DELETE RESTRICT,
    allocation_quantity TEXT NOT NULL,
    PRIMARY KEY(quantity_assertion_id, evidence_item_id, extraction_claim_id)
);

CREATE TRIGGER quantity_allocations_require_baseline_evidence
BEFORE INSERT ON quantity_evidence_allocations
WHEN NOT EXISTS (
    SELECT 1
    FROM quantity_assertions AS assertion
    JOIN revision_set_documents AS baseline ON baseline.revision_set_id = assertion.revision_set_id
    JOIN evidence_items AS evidence ON evidence.id = NEW.evidence_item_id
    WHERE assertion.id = NEW.quantity_assertion_id
      AND baseline.document_revision_id = evidence.document_revision_id
)
BEGIN
    SELECT RAISE(ABORT, 'quantity evidence must be in its revision set baseline');
END;

CREATE TRIGGER quantity_allocations_are_immutable
BEFORE UPDATE ON quantity_evidence_allocations
BEGIN
    SELECT RAISE(ABORT, 'quantity evidence allocations are immutable');
END;

CREATE TRIGGER quantity_allocations_cannot_be_deleted
BEFORE DELETE ON quantity_evidence_allocations
BEGIN
    SELECT RAISE(ABORT, 'quantity evidence allocations cannot be deleted');
END;

CREATE TABLE quantity_review_events (
    id TEXT PRIMARY KEY,
    quantity_assertion_id TEXT NOT NULL REFERENCES quantity_assertions(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
    role_grant_id TEXT NOT NULL REFERENCES role_grants(id) ON DELETE RESTRICT,
    outcome TEXT NOT NULL CHECK(outcome IN ('VERIFIED', 'APPROVED', 'REJECTED')),
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER quantity_review_events_are_immutable
BEFORE UPDATE ON quantity_review_events
BEGIN
    SELECT RAISE(ABORT, 'quantity review events are immutable');
END;

CREATE TRIGGER quantity_review_events_cannot_be_deleted
BEFORE DELETE ON quantity_review_events
BEGIN
    SELECT RAISE(ABORT, 'quantity review events cannot be deleted');
END;
