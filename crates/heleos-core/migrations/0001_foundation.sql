CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    sha256 TEXT NOT NULL CHECK (
        length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
    )
) STRICT;

CREATE TABLE projects (
    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
    name TEXT NOT NULL CHECK (heleos_valid_text(name, 256, 0)),
    created_at_ms INTEGER NOT NULL CHECK (
        created_at_ms BETWEEN 0 AND 9007199254740991
    ),
    created_by TEXT NOT NULL CHECK (heleos_valid_text(created_by, 128, 0)),
    data_class TEXT NOT NULL CHECK (
        data_class IN ('PUBLIC', 'INTERNAL', 'PROJECT_CONFIDENTIAL', 'SECRET')
    )
) STRICT;

CREATE TABLE content_objects (
    sha256 TEXT PRIMARY KEY CHECK (
        length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
    media_type TEXT NOT NULL DEFAULT 'application/pdf' CHECK (
        media_type IN (
            'application/pdf',
            'application/vnd.heleos.evidence-manifest+json;version=1'
        )
    ),
    admission_state TEXT NOT NULL CHECK (
        admission_state IN ('accepted', 'quarantined')
    ),
    vault_key TEXT NOT NULL CHECK (length(vault_key) > 0),
    created_at_ms INTEGER NOT NULL CHECK (
        created_at_ms BETWEEN 0 AND 9007199254740991
    ),
    created_by TEXT NOT NULL CHECK (heleos_valid_text(created_by, 128, 0)),
    quarantine_reason TEXT,
    CHECK (
        (admission_state = 'accepted' AND quarantine_reason IS NULL)
        OR (
            admission_state = 'quarantined'
            AND quarantine_reason IS NOT NULL
            AND length(CAST(quarantine_reason AS BLOB)) BETWEEN 1 AND 1048576
            AND json_valid(quarantine_reason)
            AND heleos_is_jcs(quarantine_reason)
        )
    )
) STRICT;

CREATE TABLE documents (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    created_at_ms INTEGER NOT NULL CHECK (
        created_at_ms BETWEEN 0 AND 9007199254740991
    ),
    created_by TEXT NOT NULL CHECK (heleos_valid_text(created_by, 128, 0))
) STRICT;

CREATE TABLE document_revisions (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    document_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    created_at_ms INTEGER NOT NULL CHECK (
        created_at_ms BETWEEN 0 AND 9007199254740991
    ),
    created_by TEXT NOT NULL CHECK (heleos_valid_text(created_by, 128, 0)),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE RESTRICT,
    FOREIGN KEY (content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
) STRICT;

CREATE TABLE project_documents (
    project_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    linked_at_ms INTEGER NOT NULL CHECK (
        linked_at_ms BETWEEN 0 AND 9007199254740991
    ),
    linked_by TEXT NOT NULL CHECK (heleos_valid_text(linked_by, 128, 0)),
    PRIMARY KEY (project_id, document_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE job_runs (
    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind = 'pdf_ingest'),
    idempotency_key TEXT NOT NULL CHECK (
        heleos_valid_text(idempotency_key, 128, 0)
    ),
    state TEXT NOT NULL CHECK (
        state IN ('queued', 'running', 'interrupted', 'succeeded', 'failed', 'cancelled')
    ),
    attempt INTEGER NOT NULL CHECK (attempt BETWEEN 0 AND 16),
    lease_owner TEXT CHECK (lease_owner IS NULL OR heleos_is_uuid(lease_owner)),
    lease_expires_at_ms INTEGER CHECK (
        lease_expires_at_ms IS NULL
        OR lease_expires_at_ms BETWEEN 0 AND 9007199254740991
    ),
    deadline_at_ms INTEGER NOT NULL CHECK (
        deadline_at_ms BETWEEN 0 AND 9007199254740991
    ),
    budget_json TEXT NOT NULL CHECK (
        length(CAST(budget_json AS BLOB)) BETWEEN 1 AND 1048576
        AND json_valid(budget_json) AND heleos_is_jcs(budget_json)
    ),
    input_json TEXT NOT NULL CHECK (
        length(CAST(input_json AS BLOB)) BETWEEN 1 AND 1048576
        AND json_valid(input_json) AND heleos_is_jcs(input_json)
    ),
    checkpoint_json TEXT NOT NULL CHECK (
        length(CAST(checkpoint_json AS BLOB)) BETWEEN 1 AND 8388608
        AND json_valid(checkpoint_json) AND heleos_is_jcs(checkpoint_json)
    ),
    terminal_reason TEXT,
    created_at_ms INTEGER NOT NULL CHECK (
        created_at_ms BETWEEN 0 AND 9007199254740991
    ),
    updated_at_ms INTEGER NOT NULL CHECK (
        updated_at_ms BETWEEN 0 AND 9007199254740991
    ),
    UNIQUE (project_id, kind, idempotency_key),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    CHECK (updated_at_ms >= created_at_ms),
    CHECK (deadline_at_ms >= created_at_ms),
    CHECK (
        (state = 'queued' AND attempt = 0
            AND lease_owner IS NULL AND lease_expires_at_ms IS NULL
            AND terminal_reason IS NULL)
        OR (state = 'running' AND attempt BETWEEN 1 AND 16
            AND lease_owner IS NOT NULL AND lease_expires_at_ms IS NOT NULL
            AND terminal_reason IS NULL)
        OR (state = 'interrupted' AND attempt BETWEEN 1 AND 15
            AND lease_owner IS NULL AND lease_expires_at_ms IS NULL
            AND terminal_reason IS NULL)
        OR (state = 'succeeded' AND attempt BETWEEN 1 AND 16
            AND lease_owner IS NULL AND lease_expires_at_ms IS NULL
            AND terminal_reason IS NOT NULL
            AND terminal_reason = 'completed')
        OR (state = 'failed' AND attempt BETWEEN 1 AND 16
            AND lease_owner IS NULL AND lease_expires_at_ms IS NULL
            AND terminal_reason IS NOT NULL
            AND terminal_reason IN ('deadline_expired', 'attempt_limit', 'internal_failure'))
        OR (state = 'cancelled' AND attempt BETWEEN 0 AND 16
            AND lease_owner IS NULL AND lease_expires_at_ms IS NULL
            AND terminal_reason IS NOT NULL
            AND terminal_reason = 'cancelled')
    )
) STRICT;

CREATE TABLE ingest_events (
    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
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
    attempt INTEGER CHECK (attempt IS NULL OR attempt BETWEEN 1 AND 16),
    source_name TEXT NOT NULL CHECK (
        heleos_valid_text(source_name, 4096, 0)
    ),
    source_path TEXT NOT NULL CHECK (source_path = '<redacted>'),
    idempotency_key TEXT NOT NULL CHECK (
        heleos_valid_text(idempotency_key, 128, 0)
    ),
    actor TEXT NOT NULL CHECK (heleos_valid_text(actor, 128, 0)),
    terminal_at_ms INTEGER NOT NULL CHECK (
        terminal_at_ms BETWEEN 0 AND 9007199254740991
    ),
    details_json TEXT NOT NULL CHECK (
        length(CAST(details_json AS BLOB)) BETWEEN 1 AND 1048576
        AND json_valid(details_json) AND heleos_is_jcs(details_json)
    ),
    UNIQUE (job_id, attempt),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    FOREIGN KEY (job_id) REFERENCES job_runs(id) ON DELETE RESTRICT,
    FOREIGN KEY (content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT,
    CHECK (
        (outcome IN ('idempotent_replay', 'denied_conflict') AND attempt IS NULL)
        OR (
            outcome IN (
                'accepted_new', 'accepted_duplicate', 'quarantined_corrupt',
                'quarantined_encrypted', 'quarantined_unsupported',
                'quarantined_suspicious', 'quarantined_limit', 'interrupted'
            )
            AND attempt IS NOT NULL
            AND attempt BETWEEN 1 AND 16
            AND job_id IS NOT NULL
        )
    )
) STRICT;

CREATE TABLE sheets (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    revision_id TEXT NOT NULL,
    zero_based_page_index INTEGER NOT NULL CHECK (
        zero_based_page_index BETWEEN 0 AND 9999
    ),
    width_micropoints INTEGER NOT NULL CHECK (
        width_micropoints BETWEEN 1 AND 9007199254740991
    ),
    height_micropoints INTEGER NOT NULL CHECK (
        height_micropoints BETWEEN 1 AND 9007199254740991
    ),
    rotation_degrees INTEGER NOT NULL CHECK (rotation_degrees IN (0, 90, 180, 270)),
    unit TEXT NOT NULL CHECK (unit = 'pt'),
    parent_content_sha256 TEXT NOT NULL,
    transform_json TEXT NOT NULL CHECK (
        length(CAST(transform_json AS BLOB)) BETWEEN 1 AND 1048576
        AND json_valid(transform_json) AND heleos_is_jcs(transform_json)
    ),
    UNIQUE (revision_id, zero_based_page_index),
    FOREIGN KEY (revision_id) REFERENCES document_revisions(id) ON DELETE RESTRICT,
    FOREIGN KEY (parent_content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
) STRICT;

CREATE TABLE scales (
    id TEXT PRIMARY KEY CHECK (length(id) > 0),
    sheet_id TEXT NOT NULL,
    numerator INTEGER NOT NULL CHECK (numerator > 0),
    denominator INTEGER NOT NULL CHECK (denominator > 0),
    source TEXT NOT NULL CHECK (heleos_valid_text(source, 256, 0)),
    created_at_ms INTEGER NOT NULL CHECK (
        created_at_ms BETWEEN 0 AND 9007199254740991
    ),
    created_by TEXT NOT NULL CHECK (heleos_valid_text(created_by, 128, 0)),
    FOREIGN KEY (sheet_id) REFERENCES sheets(id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE source_records (
    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
    project_id TEXT NOT NULL,
    job_id TEXT,
    source_name TEXT NOT NULL CHECK (heleos_valid_text(source_name, 4096, 0)),
    source_path TEXT NOT NULL CHECK (source_path = '<redacted>'),
    content_sha256 TEXT,
    metadata_json TEXT NOT NULL CHECK (
        length(CAST(metadata_json AS BLOB)) BETWEEN 1 AND 1048576
        AND json_valid(metadata_json) AND heleos_is_jcs(metadata_json)
    ),
    created_at_ms INTEGER NOT NULL CHECK (
        created_at_ms BETWEEN 0 AND 9007199254740991
    ),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    FOREIGN KEY (job_id) REFERENCES job_runs(id) ON DELETE RESTRICT,
    FOREIGN KEY (content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
) STRICT;

CREATE TABLE evidence_objects (
    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
    project_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    document_revision_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    parent_content_sha256 TEXT NOT NULL,
    extraction_method TEXT NOT NULL CHECK (
        heleos_valid_text(extraction_method, 256, 0)
    ),
    parameters_json TEXT NOT NULL CHECK (
        length(CAST(parameters_json AS BLOB)) BETWEEN 1 AND 1048576
        AND json_valid(parameters_json) AND heleos_is_jcs(parameters_json)
    ),
    review_state TEXT NOT NULL CHECK (
        review_state IN ('unreviewed', 'accepted', 'rejected')
    ),
    created_at_ms INTEGER NOT NULL CHECK (
        created_at_ms BETWEEN 0 AND 9007199254740991
    ),
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
    actor TEXT NOT NULL CHECK (heleos_valid_text(actor, 128, 0)),
    reason TEXT NOT NULL CHECK (heleos_valid_text(reason, 1024, 1)),
    before_json TEXT NOT NULL CHECK (
        length(CAST(before_json AS BLOB)) BETWEEN 1 AND 1048576
        AND json_valid(before_json) AND heleos_is_jcs(before_json)
    ),
    after_json TEXT NOT NULL CHECK (
        length(CAST(after_json AS BLOB)) BETWEEN 1 AND 1048576
        AND json_valid(after_json) AND heleos_is_jcs(after_json)
    ),
    created_at_ms INTEGER NOT NULL CHECK (
        created_at_ms BETWEEN 0 AND 9007199254740991
    ),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
    FOREIGN KEY (evidence_id) REFERENCES evidence_objects(id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
    sequence INTEGER NOT NULL UNIQUE CHECK (
        sequence BETWEEN 1 AND 9007199254740991
    ),
    project_id TEXT,
    actor TEXT NOT NULL CHECK (heleos_valid_text(actor, 128, 0)),
    action TEXT NOT NULL CHECK (
        action IN (
            'project_created', 'job_created', 'job_started', 'job_checkpointed',
            'job_interrupted', 'job_resumed', 'job_succeeded', 'job_failed',
            'ingest_accepted', 'ingest_quarantined', 'ingest_replayed',
            'ingest_conflict_denied', 'evidence_created'
        )
    ),
    subject_type TEXT NOT NULL CHECK (
        subject_type IN ('project', 'job', 'ingest_attempt', 'evidence')
    ),
    subject_id TEXT NOT NULL CHECK (heleos_valid_text(subject_id, 256, 0)),
    before_json TEXT NOT NULL CHECK (
        length(CAST(before_json AS BLOB)) BETWEEN 1 AND 1048576
        AND json_valid(before_json) AND heleos_is_jcs(before_json)
    ),
    after_json TEXT NOT NULL CHECK (
        length(CAST(after_json AS BLOB)) BETWEEN 1 AND 1048576
        AND json_valid(after_json) AND heleos_is_jcs(after_json)
    ),
    reason TEXT NOT NULL CHECK (heleos_valid_text(reason, 1024, 1)),
    occurred_at_ms INTEGER NOT NULL CHECK (
        occurred_at_ms BETWEEN 0 AND 9007199254740991
    ),
    previous_hash TEXT NOT NULL CHECK (
        length(previous_hash) = 64 AND previous_hash NOT GLOB '*[^0-9a-f]*'
    ),
    event_hash TEXT NOT NULL UNIQUE CHECK (
        length(event_hash) = 64 AND event_hash NOT GLOB '*[^0-9a-f]*'
    ),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT
) STRICT;

CREATE INDEX audit_events_subject_sequence ON audit_events(subject_type, subject_id, sequence DESC);

CREATE TRIGGER document_revisions_require_accepted_content
BEFORE INSERT ON document_revisions
WHEN NOT EXISTS (
    SELECT 1
    FROM content_objects
    WHERE sha256 = NEW.content_sha256
      AND admission_state = 'accepted'
      AND media_type = 'application/pdf'
)
BEGIN
    SELECT RAISE(ABORT, 'document revisions require accepted content');
END;

CREATE TRIGGER sheets_require_accepted_revision_content
BEFORE INSERT ON sheets
WHEN NOT EXISTS (
    SELECT 1
    FROM document_revisions AS revision
    JOIN content_objects AS content
      ON content.sha256 = revision.content_sha256
    WHERE revision.id = NEW.revision_id
      AND revision.content_sha256 = NEW.parent_content_sha256
      AND content.admission_state = 'accepted'
      AND content.media_type = 'application/pdf'
)
BEGIN
    SELECT RAISE(ABORT, 'sheets require their accepted revision content');
END;

CREATE TRIGGER accepted_evidence_requires_accepted_revision_lineage
BEFORE INSERT ON evidence_objects
WHEN NEW.review_state = 'accepted' AND NOT EXISTS (
    SELECT 1
    FROM document_revisions AS revision
    JOIN content_objects AS derivative
      ON derivative.sha256 = NEW.content_sha256
    JOIN content_objects AS parent
      ON parent.sha256 = NEW.parent_content_sha256
    WHERE revision.id = NEW.document_revision_id
      AND revision.content_sha256 = NEW.parent_content_sha256
      AND derivative.admission_state = 'accepted'
      AND derivative.media_type = 'application/vnd.heleos.evidence-manifest+json;version=1'
      AND parent.admission_state = 'accepted'
      AND parent.media_type = 'application/pdf'
)
BEGIN
    SELECT RAISE(ABORT, 'accepted evidence requires accepted revision lineage');
END;

CREATE TRIGGER job_runs_no_delete
BEFORE DELETE ON job_runs BEGIN
    SELECT RAISE(ABORT, 'job_runs rows cannot be deleted');
END;

CREATE TRIGGER job_runs_frozen_identity
BEFORE UPDATE ON job_runs
WHEN NEW.id IS NOT OLD.id
  OR NEW.project_id IS NOT OLD.project_id
  OR NEW.kind IS NOT OLD.kind
  OR NEW.idempotency_key IS NOT OLD.idempotency_key
  OR NEW.deadline_at_ms IS NOT OLD.deadline_at_ms
  OR NEW.budget_json IS NOT OLD.budget_json
  OR NEW.input_json IS NOT OLD.input_json
  OR NEW.created_at_ms IS NOT OLD.created_at_ms
BEGIN
    SELECT RAISE(ABORT, 'job_runs frozen identity cannot change');
END;

CREATE TRIGGER job_runs_legal_transition
BEFORE UPDATE ON job_runs
WHEN NEW.updated_at_ms < OLD.updated_at_ms
  OR NOT (
      (OLD.state = 'queued' AND NEW.state = 'running'
        AND OLD.attempt = 0 AND NEW.attempt = 1
        AND NEW.checkpoint_json IS OLD.checkpoint_json)
      OR (OLD.state = 'queued' AND NEW.state = 'cancelled'
        AND NEW.attempt = 0)
      OR (OLD.state = 'running' AND NEW.state = 'running'
        AND NEW.attempt = OLD.attempt
        AND NEW.lease_owner IS OLD.lease_owner
        AND NEW.lease_expires_at_ms IS OLD.lease_expires_at_ms
        AND NEW.terminal_reason IS OLD.terminal_reason
        AND NEW.checkpoint_json IS NOT OLD.checkpoint_json)
      OR (OLD.state = 'running' AND NEW.state = 'interrupted'
        AND NEW.attempt = OLD.attempt
        AND NEW.checkpoint_json IS OLD.checkpoint_json)
      OR (OLD.state = 'running'
        AND NEW.state IN ('succeeded', 'failed', 'cancelled')
        AND NEW.attempt = OLD.attempt)
      OR (OLD.state = 'interrupted' AND NEW.state = 'running'
        AND NEW.attempt = OLD.attempt + 1
        AND NEW.checkpoint_json IS OLD.checkpoint_json)
      OR (OLD.state = 'interrupted' AND NEW.state = 'cancelled'
        AND NEW.attempt = OLD.attempt)
  )
BEGIN
    SELECT RAISE(ABORT, 'illegal job_runs state transition');
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
