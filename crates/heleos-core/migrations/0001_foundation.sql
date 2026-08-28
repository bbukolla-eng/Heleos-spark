CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    sha256 TEXT NOT NULL CHECK (
        length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
    )
) STRICT;

CREATE TABLE projects (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    name TEXT NOT NULL CHECK (length(name) > 0),
    created_at_ms INTEGER NOT NULL,
    created_by TEXT NOT NULL CHECK (length(created_by) > 0),
    data_class TEXT NOT NULL CHECK (
        data_class IN ('PUBLIC', 'INTERNAL', 'PROJECT_CONFIDENTIAL', 'SECRET')
    )
) STRICT;

CREATE TABLE content_objects (
    sha256 TEXT PRIMARY KEY CHECK (
        length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
    admission_state TEXT NOT NULL CHECK (
        admission_state IN ('accepted', 'quarantined')
    ),
    vault_key TEXT NOT NULL CHECK (length(vault_key) > 0),
    created_at_ms INTEGER NOT NULL,
    created_by TEXT NOT NULL CHECK (length(created_by) > 0),
    quarantine_reason TEXT,
    CHECK (
        (admission_state = 'accepted' AND quarantine_reason IS NULL)
        OR admission_state = 'quarantined'
    )
) STRICT;

CREATE TABLE documents (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    created_at_ms INTEGER NOT NULL,
    created_by TEXT NOT NULL CHECK (length(created_by) > 0)
) STRICT;

CREATE TABLE document_revisions (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    document_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    created_at_ms INTEGER NOT NULL,
    created_by TEXT NOT NULL CHECK (length(created_by) > 0),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE RESTRICT,
    FOREIGN KEY (content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
) STRICT;

CREATE TABLE project_documents (
    project_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    linked_at_ms INTEGER NOT NULL,
    linked_by TEXT NOT NULL CHECK (length(linked_by) > 0),
    PRIMARY KEY (project_id, document_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE job_runs (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (length(kind) > 0),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) > 0),
    state TEXT NOT NULL CHECK (
        state IN ('queued', 'running', 'interrupted', 'succeeded', 'failed', 'cancelled')
    ),
    attempt INTEGER NOT NULL CHECK (attempt >= 0),
    lease_owner TEXT,
    lease_expires_at_ms INTEGER,
    deadline_at_ms INTEGER,
    budget_json TEXT NOT NULL CHECK (json_valid(budget_json) AND heleos_is_jcs(budget_json)),
    input_json TEXT NOT NULL CHECK (json_valid(input_json) AND heleos_is_jcs(input_json)),
    checkpoint_json TEXT NOT NULL CHECK (json_valid(checkpoint_json) AND heleos_is_jcs(checkpoint_json)),
    terminal_reason TEXT,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    UNIQUE (project_id, kind, idempotency_key),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CHECK (
        (lease_owner IS NULL AND lease_expires_at_ms IS NULL)
        OR (lease_owner IS NOT NULL AND lease_expires_at_ms IS NOT NULL)
    )
) STRICT;

CREATE TABLE ingest_events (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    project_id TEXT NOT NULL,
    job_id TEXT,
    content_sha256 TEXT,
    outcome TEXT NOT NULL CHECK (
        outcome IN (
            'accepted_new',
            'accepted_duplicate',
            'idempotent_replay',
            'quarantined_corrupt',
            'quarantined_encrypted',
            'quarantined_unsupported',
            'quarantined_suspicious',
            'quarantined_limit',
            'interrupted',
            'denied_conflict'
        )
    ),
    source_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) > 0),
    actor TEXT NOT NULL CHECK (length(actor) > 0),
    terminal_at_ms INTEGER NOT NULL,
    details_json TEXT NOT NULL CHECK (json_valid(details_json) AND heleos_is_jcs(details_json)),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    FOREIGN KEY (job_id) REFERENCES job_runs(id) ON DELETE RESTRICT,
    FOREIGN KEY (content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
) STRICT;

CREATE TABLE sheets (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    revision_id TEXT NOT NULL,
    zero_based_page_index INTEGER NOT NULL CHECK (zero_based_page_index >= 0),
    width_micropoints INTEGER NOT NULL CHECK (width_micropoints > 0),
    height_micropoints INTEGER NOT NULL CHECK (height_micropoints > 0),
    rotation_degrees INTEGER NOT NULL CHECK (rotation_degrees IN (0, 90, 180, 270)),
    unit TEXT NOT NULL CHECK (unit = 'pt'),
    parent_content_sha256 TEXT NOT NULL,
    transform_json TEXT NOT NULL CHECK (json_valid(transform_json) AND heleos_is_jcs(transform_json)),
    UNIQUE (revision_id, zero_based_page_index),
    FOREIGN KEY (revision_id) REFERENCES document_revisions(id) ON DELETE RESTRICT,
    FOREIGN KEY (parent_content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
) STRICT;

CREATE TABLE scales (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    sheet_id TEXT NOT NULL,
    numerator INTEGER NOT NULL CHECK (numerator > 0),
    denominator INTEGER NOT NULL CHECK (denominator > 0),
    source TEXT NOT NULL CHECK (length(source) > 0),
    created_at_ms INTEGER NOT NULL,
    created_by TEXT NOT NULL CHECK (length(created_by) > 0),
    FOREIGN KEY (sheet_id) REFERENCES sheets(id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE source_records (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    project_id TEXT NOT NULL,
    job_id TEXT,
    source_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_sha256 TEXT,
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND heleos_is_jcs(metadata_json)),
    created_at_ms INTEGER NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    FOREIGN KEY (job_id) REFERENCES job_runs(id) ON DELETE RESTRICT,
    FOREIGN KEY (content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
) STRICT;

CREATE TABLE evidence_objects (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    project_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    document_revision_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    parent_content_sha256 TEXT NOT NULL,
    extraction_method TEXT NOT NULL CHECK (length(extraction_method) > 0),
    parameters_json TEXT NOT NULL CHECK (json_valid(parameters_json) AND heleos_is_jcs(parameters_json)),
    review_state TEXT NOT NULL CHECK (
        review_state IN ('unreviewed', 'accepted', 'rejected')
    ),
    created_at_ms INTEGER NOT NULL,
    UNIQUE (project_id, document_revision_id, content_sha256),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    FOREIGN KEY (job_id) REFERENCES job_runs(id) ON DELETE RESTRICT,
    FOREIGN KEY (document_revision_id) REFERENCES document_revisions(id) ON DELETE RESTRICT,
    FOREIGN KEY (content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT,
    FOREIGN KEY (parent_content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
) STRICT;

CREATE TABLE corrections (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    project_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (length(actor) > 0),
    reason TEXT NOT NULL CHECK (length(reason) > 0),
    before_json TEXT NOT NULL CHECK (json_valid(before_json) AND heleos_is_jcs(before_json)),
    after_json TEXT NOT NULL CHECK (json_valid(after_json) AND heleos_is_jcs(after_json)),
    created_at_ms INTEGER NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    FOREIGN KEY (evidence_id) REFERENCES evidence_objects(id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    sequence INTEGER NOT NULL UNIQUE CHECK (sequence > 0),
    project_id TEXT,
    actor TEXT NOT NULL CHECK (length(actor) > 0),
    action TEXT NOT NULL CHECK (length(action) > 0),
    subject_type TEXT NOT NULL CHECK (length(subject_type) > 0),
    subject_id TEXT NOT NULL CHECK (length(subject_id) > 0),
    before_json TEXT NOT NULL CHECK (json_valid(before_json) AND heleos_is_jcs(before_json)),
    after_json TEXT NOT NULL CHECK (json_valid(after_json) AND heleos_is_jcs(after_json)),
    reason TEXT NOT NULL,
    occurred_at_ms INTEGER NOT NULL,
    previous_hash TEXT NOT NULL CHECK (
        length(previous_hash) = 64 AND previous_hash NOT GLOB '*[^0-9a-f]*'
    ),
    event_hash TEXT NOT NULL UNIQUE CHECK (
        length(event_hash) = 64 AND event_hash NOT GLOB '*[^0-9a-f]*'
    ),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT
) STRICT;

CREATE TRIGGER document_revisions_require_accepted_content
BEFORE INSERT ON document_revisions
WHEN NOT EXISTS (
    SELECT 1
    FROM content_objects
    WHERE sha256 = NEW.content_sha256 AND admission_state = 'accepted'
)
BEGIN
    SELECT RAISE(ABORT, 'document revisions require accepted content');
END;

CREATE TRIGGER content_objects_no_update
BEFORE UPDATE ON content_objects BEGIN
    SELECT RAISE(ABORT, 'content_objects rows are immutable');
END;
CREATE TRIGGER content_objects_no_delete
BEFORE DELETE ON content_objects BEGIN
    SELECT RAISE(ABORT, 'content_objects rows are immutable');
END;

CREATE TRIGGER document_revisions_no_update
BEFORE UPDATE ON document_revisions BEGIN
    SELECT RAISE(ABORT, 'document_revisions rows are immutable');
END;
CREATE TRIGGER document_revisions_no_delete
BEFORE DELETE ON document_revisions BEGIN
    SELECT RAISE(ABORT, 'document_revisions rows are immutable');
END;

CREATE TRIGGER sheets_no_update
BEFORE UPDATE ON sheets BEGIN
    SELECT RAISE(ABORT, 'sheets rows are immutable');
END;
CREATE TRIGGER sheets_no_delete
BEFORE DELETE ON sheets BEGIN
    SELECT RAISE(ABORT, 'sheets rows are immutable');
END;

CREATE TRIGGER evidence_objects_no_update
BEFORE UPDATE ON evidence_objects BEGIN
    SELECT RAISE(ABORT, 'evidence_objects rows are immutable');
END;
CREATE TRIGGER evidence_objects_no_delete
BEFORE DELETE ON evidence_objects BEGIN
    SELECT RAISE(ABORT, 'evidence_objects rows are immutable');
END;

CREATE TRIGGER ingest_events_no_update
BEFORE UPDATE ON ingest_events BEGIN
    SELECT RAISE(ABORT, 'ingest_events rows are immutable');
END;
CREATE TRIGGER ingest_events_no_delete
BEFORE DELETE ON ingest_events BEGIN
    SELECT RAISE(ABORT, 'ingest_events rows are immutable');
END;

CREATE TRIGGER audit_events_no_update
BEFORE UPDATE ON audit_events BEGIN
    SELECT RAISE(ABORT, 'audit_events rows are immutable');
END;
CREATE TRIGGER audit_events_no_delete
BEFORE DELETE ON audit_events BEGIN
    SELECT RAISE(ABORT, 'audit_events rows are immutable');
END;
