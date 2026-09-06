# Review package: HEAD..e032b14195b9b382be49c2d19f5bada4f7ef072e

## Commits
e032b14 review snapshot: task6 store inventory

## Files changed
 crates/heleos-core/migrations/0001_foundation.sql |  328 +-
 crates/heleos-core/src/error.rs                   |    6 +
 crates/heleos-core/src/ingest/audit.rs            | 1604 +++++
 crates/heleos-core/src/ingest/evidence.rs         |  606 ++
 crates/heleos-core/src/ingest/fault.rs            |   27 +
 crates/heleos-core/src/ingest/inspection.rs       | 1332 +++++
 crates/heleos-core/src/ingest/job.rs              | 4185 +++++++++++++
 crates/heleos-core/src/ingest/mod.rs              |  463 ++
 crates/heleos-core/src/lib.rs                     |   14 +
 crates/heleos-core/src/pdf/geometry.rs            |   49 +-
 crates/heleos-core/src/pdf/mod.rs                 |   79 +-
 crates/heleos-core/src/pdf/wasi_host.rs           |   42 +-
 crates/heleos-core/src/store/ingest_repository.rs | 6535 +++++++++++++++++++++
 crates/heleos-core/src/store/mod.rs               |  156 +-
 crates/heleos-core/tests/ingest.rs                | 1549 +++++
 crates/heleos-core/tests/migrations.rs            | 3639 ++++++++----
 16 files changed, 19443 insertions(+), 1171 deletions(-)

## Diff
diff --git a/crates/heleos-core/migrations/0001_foundation.sql b/crates/heleos-core/migrations/0001_foundation.sql
index f3db656..5b6f835 100644
--- a/crates/heleos-core/migrations/0001_foundation.sql
+++ b/crates/heleos-core/migrations/0001_foundation.sql
@@ -1,266 +1,460 @@
 CREATE TABLE schema_migrations (
     version INTEGER PRIMARY KEY CHECK (version > 0),
     sha256 TEXT NOT NULL CHECK (
         length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
     )
 ) STRICT;
 
 CREATE TABLE projects (
-    id TEXT PRIMARY KEY CHECK (length(id) > 0),
-    name TEXT NOT NULL CHECK (length(name) > 0),
-    created_at_ms INTEGER NOT NULL,
-    created_by TEXT NOT NULL CHECK (length(created_by) > 0),
+    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
+    name TEXT NOT NULL CHECK (heleos_valid_text(name, 256, 0)),
+    created_at_ms INTEGER NOT NULL CHECK (
+        created_at_ms BETWEEN 0 AND 9007199254740991
+    ),
+    created_by TEXT NOT NULL CHECK (heleos_valid_text(created_by, 128, 0)),
     data_class TEXT NOT NULL CHECK (
         data_class IN ('PUBLIC', 'INTERNAL', 'PROJECT_CONFIDENTIAL', 'SECRET')
     )
 ) STRICT;
 
 CREATE TABLE content_objects (
     sha256 TEXT PRIMARY KEY CHECK (
         length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'
     ),
     byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
+    media_type TEXT NOT NULL DEFAULT 'application/pdf' CHECK (
+        media_type IN (
+            'application/pdf',
+            'application/vnd.heleos.evidence-manifest+json;version=1'
+        )
+    ),
     admission_state TEXT NOT NULL CHECK (
         admission_state IN ('accepted', 'quarantined')
     ),
     vault_key TEXT NOT NULL CHECK (length(vault_key) > 0),
-    created_at_ms INTEGER NOT NULL,
-    created_by TEXT NOT NULL CHECK (length(created_by) > 0),
+    created_at_ms INTEGER NOT NULL CHECK (
+        created_at_ms BETWEEN 0 AND 9007199254740991
+    ),
+    created_by TEXT NOT NULL CHECK (heleos_valid_text(created_by, 128, 0)),
     quarantine_reason TEXT,
     CHECK (
         (admission_state = 'accepted' AND quarantine_reason IS NULL)
-        OR admission_state = 'quarantined'
+        OR (
+            admission_state = 'quarantined'
+            AND quarantine_reason IS NOT NULL
+            AND length(CAST(quarantine_reason AS BLOB)) BETWEEN 1 AND 1048576
+            AND json_valid(quarantine_reason)
+            AND heleos_is_jcs(quarantine_reason)
+        )
     )
 ) STRICT;
 
 CREATE TABLE documents (
     id TEXT PRIMARY KEY CHECK (length(id) > 0),
-    created_at_ms INTEGER NOT NULL,
-    created_by TEXT NOT NULL CHECK (length(created_by) > 0)
+    created_at_ms INTEGER NOT NULL CHECK (
+        created_at_ms BETWEEN 0 AND 9007199254740991
+    ),
+    created_by TEXT NOT NULL CHECK (heleos_valid_text(created_by, 128, 0))
 ) STRICT;
 
 CREATE TABLE document_revisions (
     id TEXT PRIMARY KEY CHECK (length(id) > 0),
     document_id TEXT NOT NULL,
     content_sha256 TEXT NOT NULL UNIQUE,
-    created_at_ms INTEGER NOT NULL,
-    created_by TEXT NOT NULL CHECK (length(created_by) > 0),
+    created_at_ms INTEGER NOT NULL CHECK (
+        created_at_ms BETWEEN 0 AND 9007199254740991
+    ),
+    created_by TEXT NOT NULL CHECK (heleos_valid_text(created_by, 128, 0)),
     FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE RESTRICT,
     FOREIGN KEY (content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
 ) STRICT;
 
 CREATE TABLE project_documents (
     project_id TEXT NOT NULL,
     document_id TEXT NOT NULL,
-    linked_at_ms INTEGER NOT NULL,
-    linked_by TEXT NOT NULL CHECK (length(linked_by) > 0),
+    linked_at_ms INTEGER NOT NULL CHECK (
+        linked_at_ms BETWEEN 0 AND 9007199254740991
+    ),
+    linked_by TEXT NOT NULL CHECK (heleos_valid_text(linked_by, 128, 0)),
     PRIMARY KEY (project_id, document_id),
     FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
     FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE RESTRICT
 ) STRICT;
 
 CREATE TABLE job_runs (
-    id TEXT PRIMARY KEY CHECK (length(id) > 0),
+    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
     project_id TEXT NOT NULL,
-    kind TEXT NOT NULL CHECK (length(kind) > 0),
-    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) > 0),
+    kind TEXT NOT NULL CHECK (kind = 'pdf_ingest'),
+    idempotency_key TEXT NOT NULL CHECK (
+        heleos_valid_text(idempotency_key, 128, 0)
+    ),
     state TEXT NOT NULL CHECK (
         state IN ('queued', 'running', 'interrupted', 'succeeded', 'failed', 'cancelled')
     ),
-    attempt INTEGER NOT NULL CHECK (attempt >= 0),
-    lease_owner TEXT,
-    lease_expires_at_ms INTEGER,
-    deadline_at_ms INTEGER,
-    budget_json TEXT NOT NULL CHECK (json_valid(budget_json) AND heleos_is_jcs(budget_json)),
-    input_json TEXT NOT NULL CHECK (json_valid(input_json) AND heleos_is_jcs(input_json)),
-    checkpoint_json TEXT NOT NULL CHECK (json_valid(checkpoint_json) AND heleos_is_jcs(checkpoint_json)),
+    attempt INTEGER NOT NULL CHECK (attempt BETWEEN 0 AND 16),
+    lease_owner TEXT CHECK (lease_owner IS NULL OR heleos_is_uuid(lease_owner)),
+    lease_expires_at_ms INTEGER CHECK (
+        lease_expires_at_ms IS NULL
+        OR lease_expires_at_ms BETWEEN 0 AND 9007199254740991
+    ),
+    deadline_at_ms INTEGER NOT NULL CHECK (
+        deadline_at_ms BETWEEN 0 AND 9007199254740991
+    ),
+    budget_json TEXT NOT NULL CHECK (
+        length(CAST(budget_json AS BLOB)) BETWEEN 1 AND 1048576
+        AND json_valid(budget_json) AND heleos_is_jcs(budget_json)
+    ),
+    input_json TEXT NOT NULL CHECK (
+        length(CAST(input_json AS BLOB)) BETWEEN 1 AND 1048576
+        AND json_valid(input_json) AND heleos_is_jcs(input_json)
+    ),
+    checkpoint_json TEXT NOT NULL CHECK (
+        length(CAST(checkpoint_json AS BLOB)) BETWEEN 1 AND 8388608
+        AND json_valid(checkpoint_json) AND heleos_is_jcs(checkpoint_json)
+    ),
     terminal_reason TEXT,
-    created_at_ms INTEGER NOT NULL,
-    updated_at_ms INTEGER NOT NULL,
+    created_at_ms INTEGER NOT NULL CHECK (
+        created_at_ms BETWEEN 0 AND 9007199254740991
+    ),
+    updated_at_ms INTEGER NOT NULL CHECK (
+        updated_at_ms BETWEEN 0 AND 9007199254740991
+    ),
     UNIQUE (project_id, kind, idempotency_key),
     FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
+    CHECK (updated_at_ms >= created_at_ms),
+    CHECK (deadline_at_ms >= created_at_ms),
     CHECK (
-        (lease_owner IS NULL AND lease_expires_at_ms IS NULL)
-        OR (lease_owner IS NOT NULL AND lease_expires_at_ms IS NOT NULL)
+        (state = 'queued' AND attempt = 0
+            AND lease_owner IS NULL AND lease_expires_at_ms IS NULL
+            AND terminal_reason IS NULL)
+        OR (state = 'running' AND attempt BETWEEN 1 AND 16
+            AND lease_owner IS NOT NULL AND lease_expires_at_ms IS NOT NULL
+            AND terminal_reason IS NULL)
+        OR (state = 'interrupted' AND attempt BETWEEN 1 AND 15
+            AND lease_owner IS NULL AND lease_expires_at_ms IS NULL
+            AND terminal_reason IS NULL)
+        OR (state = 'succeeded' AND attempt BETWEEN 1 AND 16
+            AND lease_owner IS NULL AND lease_expires_at_ms IS NULL
+            AND terminal_reason IS NOT NULL
+            AND terminal_reason = 'completed')
+        OR (state = 'failed' AND attempt BETWEEN 1 AND 16
+            AND lease_owner IS NULL AND lease_expires_at_ms IS NULL
+            AND terminal_reason IS NOT NULL
+            AND terminal_reason IN ('deadline_expired', 'attempt_limit', 'internal_failure'))
+        OR (state = 'cancelled' AND attempt BETWEEN 0 AND 16
+            AND lease_owner IS NULL AND lease_expires_at_ms IS NULL
+            AND terminal_reason IS NOT NULL
+            AND terminal_reason = 'cancelled')
     )
 ) STRICT;
 
 CREATE TABLE ingest_events (
-    id TEXT PRIMARY KEY CHECK (length(id) > 0),
+    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
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
-    source_name TEXT NOT NULL,
-    source_path TEXT NOT NULL,
-    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) > 0),
-    actor TEXT NOT NULL CHECK (length(actor) > 0),
-    terminal_at_ms INTEGER NOT NULL,
-    details_json TEXT NOT NULL CHECK (json_valid(details_json) AND heleos_is_jcs(details_json)),
+    attempt INTEGER CHECK (attempt IS NULL OR attempt BETWEEN 1 AND 16),
+    source_name TEXT NOT NULL CHECK (
+        heleos_valid_text(source_name, 4096, 0)
+    ),
+    source_path TEXT NOT NULL CHECK (source_path = '<redacted>'),
+    idempotency_key TEXT NOT NULL CHECK (
+        heleos_valid_text(idempotency_key, 128, 0)
+    ),
+    actor TEXT NOT NULL CHECK (heleos_valid_text(actor, 128, 0)),
+    terminal_at_ms INTEGER NOT NULL CHECK (
+        terminal_at_ms BETWEEN 0 AND 9007199254740991
+    ),
+    details_json TEXT NOT NULL CHECK (
+        length(CAST(details_json AS BLOB)) BETWEEN 1 AND 1048576
+        AND json_valid(details_json) AND heleos_is_jcs(details_json)
+    ),
+    UNIQUE (job_id, attempt),
     FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
     FOREIGN KEY (job_id) REFERENCES job_runs(id) ON DELETE RESTRICT,
-    FOREIGN KEY (content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
+    FOREIGN KEY (content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT,
+    CHECK (
+        (outcome IN ('idempotent_replay', 'denied_conflict') AND attempt IS NULL)
+        OR (
+            outcome IN (
+                'accepted_new', 'accepted_duplicate', 'quarantined_corrupt',
+                'quarantined_encrypted', 'quarantined_unsupported',
+                'quarantined_suspicious', 'quarantined_limit', 'interrupted'
+            )
+            AND attempt IS NOT NULL
+            AND attempt BETWEEN 1 AND 16
+            AND job_id IS NOT NULL
+        )
+    )
 ) STRICT;
 
 CREATE TABLE sheets (
     id TEXT PRIMARY KEY CHECK (length(id) > 0),
     revision_id TEXT NOT NULL,
-    zero_based_page_index INTEGER NOT NULL CHECK (zero_based_page_index >= 0),
-    width_micropoints INTEGER NOT NULL CHECK (width_micropoints > 0),
-    height_micropoints INTEGER NOT NULL CHECK (height_micropoints > 0),
+    zero_based_page_index INTEGER NOT NULL CHECK (
+        zero_based_page_index BETWEEN 0 AND 9999
+    ),
+    width_micropoints INTEGER NOT NULL CHECK (
+        width_micropoints BETWEEN 1 AND 9007199254740991
+    ),
+    height_micropoints INTEGER NOT NULL CHECK (
+        height_micropoints BETWEEN 1 AND 9007199254740991
+    ),
     rotation_degrees INTEGER NOT NULL CHECK (rotation_degrees IN (0, 90, 180, 270)),
     unit TEXT NOT NULL CHECK (unit = 'pt'),
     parent_content_sha256 TEXT NOT NULL,
-    transform_json TEXT NOT NULL CHECK (json_valid(transform_json) AND heleos_is_jcs(transform_json)),
+    transform_json TEXT NOT NULL CHECK (
+        length(CAST(transform_json AS BLOB)) BETWEEN 1 AND 1048576
+        AND json_valid(transform_json) AND heleos_is_jcs(transform_json)
+    ),
     UNIQUE (revision_id, zero_based_page_index),
     FOREIGN KEY (revision_id) REFERENCES document_revisions(id) ON DELETE RESTRICT,
     FOREIGN KEY (parent_content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
 ) STRICT;
 
 CREATE TABLE scales (
     id TEXT PRIMARY KEY CHECK (length(id) > 0),
     sheet_id TEXT NOT NULL,
     numerator INTEGER NOT NULL CHECK (numerator > 0),
     denominator INTEGER NOT NULL CHECK (denominator > 0),
-    source TEXT NOT NULL CHECK (length(source) > 0),
-    created_at_ms INTEGER NOT NULL,
-    created_by TEXT NOT NULL CHECK (length(created_by) > 0),
+    source TEXT NOT NULL CHECK (heleos_valid_text(source, 256, 0)),
+    created_at_ms INTEGER NOT NULL CHECK (
+        created_at_ms BETWEEN 0 AND 9007199254740991
+    ),
+    created_by TEXT NOT NULL CHECK (heleos_valid_text(created_by, 128, 0)),
     FOREIGN KEY (sheet_id) REFERENCES sheets(id) ON DELETE RESTRICT
 ) STRICT;
 
 CREATE TABLE source_records (
-    id TEXT PRIMARY KEY CHECK (length(id) > 0),
+    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
     project_id TEXT NOT NULL,
     job_id TEXT,
-    source_name TEXT NOT NULL,
-    source_path TEXT NOT NULL,
+    source_name TEXT NOT NULL CHECK (heleos_valid_text(source_name, 4096, 0)),
+    source_path TEXT NOT NULL CHECK (source_path = '<redacted>'),
     content_sha256 TEXT,
-    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json) AND heleos_is_jcs(metadata_json)),
-    created_at_ms INTEGER NOT NULL,
+    metadata_json TEXT NOT NULL CHECK (
+        length(CAST(metadata_json AS BLOB)) BETWEEN 1 AND 1048576
+        AND json_valid(metadata_json) AND heleos_is_jcs(metadata_json)
+    ),
+    created_at_ms INTEGER NOT NULL CHECK (
+        created_at_ms BETWEEN 0 AND 9007199254740991
+    ),
     FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
     FOREIGN KEY (job_id) REFERENCES job_runs(id) ON DELETE RESTRICT,
     FOREIGN KEY (content_sha256) REFERENCES content_objects(sha256) ON DELETE RESTRICT
 ) STRICT;
 
 CREATE TABLE evidence_objects (
-    id TEXT PRIMARY KEY CHECK (length(id) > 0),
+    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
     project_id TEXT NOT NULL,
     job_id TEXT NOT NULL,
     document_revision_id TEXT NOT NULL,
     content_sha256 TEXT NOT NULL,
     parent_content_sha256 TEXT NOT NULL,
-    extraction_method TEXT NOT NULL CHECK (length(extraction_method) > 0),
-    parameters_json TEXT NOT NULL CHECK (json_valid(parameters_json) AND heleos_is_jcs(parameters_json)),
+    extraction_method TEXT NOT NULL CHECK (
+        heleos_valid_text(extraction_method, 256, 0)
+    ),
+    parameters_json TEXT NOT NULL CHECK (
+        length(CAST(parameters_json AS BLOB)) BETWEEN 1 AND 1048576
+        AND json_valid(parameters_json) AND heleos_is_jcs(parameters_json)
+    ),
     review_state TEXT NOT NULL CHECK (
         review_state IN ('unreviewed', 'accepted', 'rejected')
     ),
-    created_at_ms INTEGER NOT NULL,
+    created_at_ms INTEGER NOT NULL CHECK (
+        created_at_ms BETWEEN 0 AND 9007199254740991
+    ),
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
-    actor TEXT NOT NULL CHECK (length(actor) > 0),
-    reason TEXT NOT NULL CHECK (length(reason) > 0),
-    before_json TEXT NOT NULL CHECK (json_valid(before_json) AND heleos_is_jcs(before_json)),
-    after_json TEXT NOT NULL CHECK (json_valid(after_json) AND heleos_is_jcs(after_json)),
-    created_at_ms INTEGER NOT NULL,
+    actor TEXT NOT NULL CHECK (heleos_valid_text(actor, 128, 0)),
+    reason TEXT NOT NULL CHECK (heleos_valid_text(reason, 1024, 1)),
+    before_json TEXT NOT NULL CHECK (
+        length(CAST(before_json AS BLOB)) BETWEEN 1 AND 1048576
+        AND json_valid(before_json) AND heleos_is_jcs(before_json)
+    ),
+    after_json TEXT NOT NULL CHECK (
+        length(CAST(after_json AS BLOB)) BETWEEN 1 AND 1048576
+        AND json_valid(after_json) AND heleos_is_jcs(after_json)
+    ),
+    created_at_ms INTEGER NOT NULL CHECK (
+        created_at_ms BETWEEN 0 AND 9007199254740991
+    ),
     FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT,
     FOREIGN KEY (evidence_id) REFERENCES evidence_objects(id) ON DELETE RESTRICT
 ) STRICT;
 
 CREATE TABLE audit_events (
-    id TEXT PRIMARY KEY CHECK (length(id) > 0),
-    sequence INTEGER NOT NULL UNIQUE CHECK (sequence > 0),
+    id TEXT PRIMARY KEY CHECK (heleos_valid_text(id, 256, 0)),
+    sequence INTEGER NOT NULL UNIQUE CHECK (
+        sequence BETWEEN 1 AND 9007199254740991
+    ),
     project_id TEXT,
-    actor TEXT NOT NULL CHECK (length(actor) > 0),
-    action TEXT NOT NULL CHECK (length(action) > 0),
-    subject_type TEXT NOT NULL CHECK (length(subject_type) > 0),
-    subject_id TEXT NOT NULL CHECK (length(subject_id) > 0),
-    before_json TEXT NOT NULL CHECK (json_valid(before_json) AND heleos_is_jcs(before_json)),
-    after_json TEXT NOT NULL CHECK (json_valid(after_json) AND heleos_is_jcs(after_json)),
-    reason TEXT NOT NULL,
-    occurred_at_ms INTEGER NOT NULL,
+    actor TEXT NOT NULL CHECK (heleos_valid_text(actor, 128, 0)),
+    action TEXT NOT NULL CHECK (
+        action IN (
+            'project_created', 'job_created', 'job_started', 'job_checkpointed',
+            'job_interrupted', 'job_resumed', 'job_succeeded', 'job_failed',
+            'ingest_accepted', 'ingest_quarantined', 'ingest_replayed',
+            'ingest_conflict_denied', 'evidence_created'
+        )
+    ),
+    subject_type TEXT NOT NULL CHECK (
+        subject_type IN ('project', 'job', 'ingest_attempt', 'evidence')
+    ),
+    subject_id TEXT NOT NULL CHECK (heleos_valid_text(subject_id, 256, 0)),
+    before_json TEXT NOT NULL CHECK (
+        length(CAST(before_json AS BLOB)) BETWEEN 1 AND 1048576
+        AND json_valid(before_json) AND heleos_is_jcs(before_json)
+    ),
+    after_json TEXT NOT NULL CHECK (
+        length(CAST(after_json AS BLOB)) BETWEEN 1 AND 1048576
+        AND json_valid(after_json) AND heleos_is_jcs(after_json)
+    ),
+    reason TEXT NOT NULL CHECK (heleos_valid_text(reason, 1024, 1)),
+    occurred_at_ms INTEGER NOT NULL CHECK (
+        occurred_at_ms BETWEEN 0 AND 9007199254740991
+    ),
     previous_hash TEXT NOT NULL CHECK (
         length(previous_hash) = 64 AND previous_hash NOT GLOB '*[^0-9a-f]*'
     ),
     event_hash TEXT NOT NULL UNIQUE CHECK (
         length(event_hash) = 64 AND event_hash NOT GLOB '*[^0-9a-f]*'
     ),
     FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE RESTRICT
 ) STRICT;
 
+CREATE INDEX audit_events_subject_sequence ON audit_events(subject_type, subject_id, sequence DESC);
+
 CREATE TRIGGER document_revisions_require_accepted_content
 BEFORE INSERT ON document_revisions
 WHEN NOT EXISTS (
     SELECT 1
     FROM content_objects
-    WHERE sha256 = NEW.content_sha256 AND admission_state = 'accepted'
+    WHERE sha256 = NEW.content_sha256
+      AND admission_state = 'accepted'
+      AND media_type = 'application/pdf'
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
+      AND content.media_type = 'application/pdf'
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
+      AND derivative.media_type = 'application/vnd.heleos.evidence-manifest+json;version=1'
       AND parent.admission_state = 'accepted'
+      AND parent.media_type = 'application/pdf'
 )
 BEGIN
     SELECT RAISE(ABORT, 'accepted evidence requires accepted revision lineage');
 END;
 
+CREATE TRIGGER job_runs_no_delete
+BEFORE DELETE ON job_runs BEGIN
+    SELECT RAISE(ABORT, 'job_runs rows cannot be deleted');
+END;
+
+CREATE TRIGGER job_runs_frozen_identity
+BEFORE UPDATE ON job_runs
+WHEN NEW.id IS NOT OLD.id
+  OR NEW.project_id IS NOT OLD.project_id
+  OR NEW.kind IS NOT OLD.kind
+  OR NEW.idempotency_key IS NOT OLD.idempotency_key
+  OR NEW.deadline_at_ms IS NOT OLD.deadline_at_ms
+  OR NEW.budget_json IS NOT OLD.budget_json
+  OR NEW.input_json IS NOT OLD.input_json
+  OR NEW.created_at_ms IS NOT OLD.created_at_ms
+BEGIN
+    SELECT RAISE(ABORT, 'job_runs frozen identity cannot change');
+END;
+
+CREATE TRIGGER job_runs_legal_transition
+BEFORE UPDATE ON job_runs
+WHEN NEW.updated_at_ms < OLD.updated_at_ms
+  OR NOT (
+      (OLD.state = 'queued' AND NEW.state = 'running'
+        AND OLD.attempt = 0 AND NEW.attempt = 1
+        AND NEW.checkpoint_json IS OLD.checkpoint_json)
+      OR (OLD.state = 'queued' AND NEW.state = 'cancelled'
+        AND NEW.attempt = 0)
+      OR (OLD.state = 'running' AND NEW.state = 'running'
+        AND NEW.attempt = OLD.attempt
+        AND NEW.lease_owner IS OLD.lease_owner
+        AND NEW.lease_expires_at_ms IS OLD.lease_expires_at_ms
+        AND NEW.terminal_reason IS OLD.terminal_reason
+        AND NEW.checkpoint_json IS NOT OLD.checkpoint_json)
+      OR (OLD.state = 'running' AND NEW.state = 'interrupted'
+        AND NEW.attempt = OLD.attempt
+        AND NEW.checkpoint_json IS OLD.checkpoint_json)
+      OR (OLD.state = 'running'
+        AND NEW.state IN ('succeeded', 'failed', 'cancelled')
+        AND NEW.attempt = OLD.attempt)
+      OR (OLD.state = 'interrupted' AND NEW.state = 'running'
+        AND NEW.attempt = OLD.attempt + 1
+        AND NEW.checkpoint_json IS OLD.checkpoint_json)
+      OR (OLD.state = 'interrupted' AND NEW.state = 'cancelled'
+        AND NEW.attempt = OLD.attempt)
+  )
+BEGIN
+    SELECT RAISE(ABORT, 'illegal job_runs state transition');
+END;
+
 CREATE TRIGGER content_objects_no_update
 BEFORE UPDATE ON content_objects BEGIN
     SELECT RAISE(ABORT, 'content_objects rows are immutable');
 END;
 CREATE TRIGGER content_objects_no_delete
 BEFORE DELETE ON content_objects BEGIN
     SELECT RAISE(ABORT, 'content_objects rows are immutable');
 END;
 
 CREATE TRIGGER document_revisions_no_update
diff --git a/crates/heleos-core/src/error.rs b/crates/heleos-core/src/error.rs
index a815fd8..9e38bcd 100644
--- a/crates/heleos-core/src/error.rs
+++ b/crates/heleos-core/src/error.rs
@@ -31,20 +31,26 @@ pub enum HeleosError {
     #[error("resource limit exceeded")]
     ResourceLimit,
     #[error("quota exceeded")]
     Quota,
     #[error("operation timed out")]
     Timeout,
     #[error("invalid job state transition")]
     InvalidStateTransition,
     #[error("idempotency conflict")]
     IdempotencyConflict,
+    #[error("job lease is unavailable")]
+    LeaseUnavailable,
+    #[error("fault injected")]
+    FaultInjected,
+    #[error("transaction commit outcome is unknown")]
+    CommitOutcomeUnknown,
     #[error("content quarantined")]
     Quarantine,
     #[error("requested item was not found")]
     NotFound,
     #[error("backup decryption failed")]
     BackupDecryption,
     #[error("backup integrity verification failed")]
     BackupIntegrity,
     #[error("invalid backup container")]
     InvalidBackupContainer,
diff --git a/crates/heleos-core/src/ingest/audit.rs b/crates/heleos-core/src/ingest/audit.rs
new file mode 100644
index 0000000..e8073b3
--- /dev/null
+++ b/crates/heleos-core/src/ingest/audit.rs
@@ -0,0 +1,1604 @@
+use std::str::FromStr;
+
+use serde::{Deserialize, Deserializer, Serialize, de};
+use serde_json::Value as JsonValue;
+use sha2::{Digest, Sha256};
+use uuid::Uuid;
+
+use crate::{ActorId, HeleosError, ProjectId, Result, Sha256Digest, canonical_json};
+
+pub const AUDIT_CHAIN_REPORT_SCHEMA_V1: &str = "heleos.audit-chain-report/v1";
+pub(crate) const MAX_AUDIT_FINDINGS: usize = 128;
+pub(crate) const JCS_SAFE_INTEGER_MAX: u64 = 9_007_199_254_740_991;
+const AUDIT_DOMAIN_V1: &[u8] = b"heleos-audit-event-v1\0";
+
+#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
+#[serde(transparent)]
+pub struct AuditEventId(Uuid);
+
+impl AuditEventId {
+    pub const fn from_uuid(value: Uuid) -> Self {
+        Self(value)
+    }
+
+    pub const fn as_uuid(&self) -> &Uuid {
+        &self.0
+    }
+}
+
+impl From<Uuid> for AuditEventId {
+    fn from(value: Uuid) -> Self {
+        Self::from_uuid(value)
+    }
+}
+
+impl FromStr for AuditEventId {
+    type Err = HeleosError;
+
+    fn from_str(value: &str) -> Result<Self> {
+        let parsed = Uuid::parse_str(value).map_err(|_| HeleosError::InvalidId)?;
+        if parsed.hyphenated().to_string() != value {
+            return Err(HeleosError::InvalidId);
+        }
+        Ok(Self::from_uuid(parsed))
+    }
+}
+
+impl<'de> Deserialize<'de> for AuditEventId {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        let value = String::deserialize(deserializer)?;
+        Self::from_str(&value).map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
+#[serde(rename_all = "snake_case")]
+pub enum AuditAction {
+    ProjectCreated,
+    JobCreated,
+    JobStarted,
+    JobCheckpointed,
+    JobInterrupted,
+    JobResumed,
+    JobSucceeded,
+    JobFailed,
+    IngestAccepted,
+    IngestQuarantined,
+    IngestReplayed,
+    IngestConflictDenied,
+    EvidenceCreated,
+}
+
+impl AuditAction {
+    pub(crate) const fn as_str(self) -> &'static str {
+        match self {
+            Self::ProjectCreated => "project_created",
+            Self::JobCreated => "job_created",
+            Self::JobStarted => "job_started",
+            Self::JobCheckpointed => "job_checkpointed",
+            Self::JobInterrupted => "job_interrupted",
+            Self::JobResumed => "job_resumed",
+            Self::JobSucceeded => "job_succeeded",
+            Self::JobFailed => "job_failed",
+            Self::IngestAccepted => "ingest_accepted",
+            Self::IngestQuarantined => "ingest_quarantined",
+            Self::IngestReplayed => "ingest_replayed",
+            Self::IngestConflictDenied => "ingest_conflict_denied",
+            Self::EvidenceCreated => "evidence_created",
+        }
+    }
+
+    fn parse(value: &str) -> Option<Self> {
+        Some(match value {
+            "project_created" => Self::ProjectCreated,
+            "job_created" => Self::JobCreated,
+            "job_started" => Self::JobStarted,
+            "job_checkpointed" => Self::JobCheckpointed,
+            "job_interrupted" => Self::JobInterrupted,
+            "job_resumed" => Self::JobResumed,
+            "job_succeeded" => Self::JobSucceeded,
+            "job_failed" => Self::JobFailed,
+            "ingest_accepted" => Self::IngestAccepted,
+            "ingest_quarantined" => Self::IngestQuarantined,
+            "ingest_replayed" => Self::IngestReplayed,
+            "ingest_conflict_denied" => Self::IngestConflictDenied,
+            "evidence_created" => Self::EvidenceCreated,
+            _ => return None,
+        })
+    }
+}
+
+#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
+#[serde(rename_all = "snake_case")]
+pub enum AuditSubjectType {
+    Project,
+    Job,
+    IngestAttempt,
+    Evidence,
+}
+
+impl AuditSubjectType {
+    pub(crate) const fn as_str(self) -> &'static str {
+        match self {
+            Self::Project => "project",
+            Self::Job => "job",
+            Self::IngestAttempt => "ingest_attempt",
+            Self::Evidence => "evidence",
+        }
+    }
+
+    fn parse(value: &str) -> Option<Self> {
+        Some(match value {
+            "project" => Self::Project,
+            "job" => Self::Job,
+            "ingest_attempt" => Self::IngestAttempt,
+            "evidence" => Self::Evidence,
+            _ => return None,
+        })
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct AuditEvent {
+    pub id: AuditEventId,
+    pub sequence: u64,
+    pub project_id: Option<ProjectId>,
+    pub actor: ActorId,
+    pub action: AuditAction,
+    pub subject_type: AuditSubjectType,
+    pub subject_id: String,
+    pub before: JsonValue,
+    pub after: JsonValue,
+    pub reason: String,
+    pub occurred_at_ms: i64,
+    pub previous_hash: Sha256Digest,
+    pub event_hash: Sha256Digest,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawAuditEvent {
+    id: AuditEventId,
+    sequence: u64,
+    project_id: Option<ProjectId>,
+    actor: ActorId,
+    action: AuditAction,
+    subject_type: AuditSubjectType,
+    #[serde(deserialize_with = "deserialize_subject_id")]
+    subject_id: String,
+    before: JsonValue,
+    after: JsonValue,
+    #[serde(deserialize_with = "deserialize_reason")]
+    reason: String,
+    occurred_at_ms: i64,
+    previous_hash: Sha256Digest,
+    event_hash: Sha256Digest,
+}
+
+fn deserialize_subject_id<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<String, D::Error> {
+    super::deserialize_bounded_string::<D, 256>(deserializer)
+}
+
+fn deserialize_reason<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<String, D::Error> {
+    super::deserialize_bounded_string::<D, 1024>(deserializer)
+}
+
+impl TryFrom<RawAuditEvent> for AuditEvent {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawAuditEvent) -> Result<Self> {
+        let stored_hash = raw.event_hash;
+        let event = Self::build(AuditEventInput {
+            id: raw.id,
+            sequence: raw.sequence,
+            project_id: raw.project_id,
+            actor: raw.actor,
+            action: raw.action,
+            subject_type: raw.subject_type,
+            subject_id: raw.subject_id,
+            before: raw.before,
+            after: raw.after,
+            reason: raw.reason,
+            occurred_at_ms: raw.occurred_at_ms,
+            previous_hash: raw.previous_hash,
+        })?;
+        if event.event_hash != stored_hash {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(event)
+    }
+}
+
+impl<'de> Deserialize<'de> for AuditEvent {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        let raw = RawAuditEvent::deserialize(deserializer)?;
+        Self::try_from(raw).map_err(de::Error::custom)
+    }
+}
+
+#[derive(Serialize)]
+struct AuditHashDocumentV1<'a> {
+    action: AuditAction,
+    actor: &'a ActorId,
+    after: &'a JsonValue,
+    before: &'a JsonValue,
+    event_id: AuditEventId,
+    occurred_at_ms: i64,
+    previous_hash: Sha256Digest,
+    project_id: Option<ProjectId>,
+    reason: &'a str,
+    sequence: u64,
+    subject_id: &'a str,
+    subject_type: AuditSubjectType,
+}
+
+impl AuditEvent {
+    pub(crate) fn build(input: AuditEventInput) -> Result<Self> {
+        validate_audit_text(&input.subject_id, 256, false)?;
+        validate_audit_text(&input.reason, 1024, true)?;
+        validate_timestamp(input.occurred_at_ms)?;
+        if input.sequence == 0 || input.sequence > JCS_SAFE_INTEGER_MAX {
+            return Err(HeleosError::Integrity);
+        }
+        ensure_canonical_value_size(&input.before)?;
+        ensure_canonical_value_size(&input.after)?;
+        let mut event = Self {
+            id: input.id,
+            sequence: input.sequence,
+            project_id: input.project_id,
+            actor: input.actor,
+            action: input.action,
+            subject_type: input.subject_type,
+            subject_id: input.subject_id,
+            before: input.before,
+            after: input.after,
+            reason: input.reason,
+            occurred_at_ms: input.occurred_at_ms,
+            previous_hash: input.previous_hash,
+            event_hash: Sha256Digest::from_bytes([0; 32]),
+        };
+        event.event_hash = event.recomputed_hash()?;
+        Ok(event)
+    }
+
+    pub(crate) fn recomputed_hash(&self) -> Result<Sha256Digest> {
+        let document = AuditHashDocumentV1 {
+            action: self.action,
+            actor: &self.actor,
+            after: &self.after,
+            before: &self.before,
+            event_id: self.id,
+            occurred_at_ms: self.occurred_at_ms,
+            previous_hash: self.previous_hash,
+            project_id: self.project_id,
+            reason: &self.reason,
+            sequence: self.sequence,
+            subject_id: &self.subject_id,
+            subject_type: self.subject_type,
+        };
+        let bytes = canonical_json(&document)?;
+        let mut hasher = Sha256::new();
+        hasher.update(AUDIT_DOMAIN_V1);
+        hasher.update(bytes);
+        Ok(Sha256Digest::from_bytes(hasher.finalize().into()))
+    }
+}
+
+pub(crate) struct AuditEventInput {
+    pub id: AuditEventId,
+    pub sequence: u64,
+    pub project_id: Option<ProjectId>,
+    pub actor: ActorId,
+    pub action: AuditAction,
+    pub subject_type: AuditSubjectType,
+    pub subject_id: String,
+    pub before: JsonValue,
+    pub after: JsonValue,
+    pub reason: String,
+    pub occurred_at_ms: i64,
+    pub previous_hash: Sha256Digest,
+}
+
+#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
+#[serde(rename_all = "snake_case")]
+pub enum AuditInvalidField {
+    Id,
+    Sequence,
+    ProjectId,
+    Actor,
+    Action,
+    SubjectType,
+    SubjectId,
+    Before,
+    After,
+    Reason,
+    OccurredAt,
+    PreviousHash,
+    EventHash,
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+#[serde(tag = "kind", content = "detail", rename_all = "snake_case")]
+pub enum AuditChainFinding {
+    InvalidRow {
+        row_ordinal: u64,
+        event_id: Option<AuditEventId>,
+        sequence: Option<u64>,
+        field: AuditInvalidField,
+    },
+    SequenceMismatch {
+        event_id: AuditEventId,
+        expected: u64,
+        observed: u64,
+    },
+    PreviousHashMismatch {
+        event_id: AuditEventId,
+        sequence: u64,
+        expected: Sha256Digest,
+        observed: Sha256Digest,
+    },
+    EventHashMismatch {
+        event_id: AuditEventId,
+        sequence: u64,
+        expected: Sha256Digest,
+        observed: Sha256Digest,
+    },
+    NonCanonicalBefore {
+        event_id: AuditEventId,
+        sequence: u64,
+    },
+    NonCanonicalAfter {
+        event_id: AuditEventId,
+        sequence: u64,
+    },
+}
+
+#[derive(Deserialize)]
+#[serde(
+    tag = "kind",
+    content = "detail",
+    rename_all = "snake_case",
+    deny_unknown_fields
+)]
+enum RawAuditChainFinding {
+    InvalidRow(RawInvalidRowFinding),
+    SequenceMismatch(RawSequenceMismatchFinding),
+    PreviousHashMismatch(RawHashMismatchFinding),
+    EventHashMismatch(RawHashMismatchFinding),
+    NonCanonicalBefore(RawCanonicalFinding),
+    NonCanonicalAfter(RawCanonicalFinding),
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawInvalidRowFinding {
+    row_ordinal: u64,
+    event_id: Option<AuditEventId>,
+    sequence: Option<u64>,
+    field: AuditInvalidField,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawSequenceMismatchFinding {
+    event_id: AuditEventId,
+    expected: u64,
+    observed: u64,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawHashMismatchFinding {
+    event_id: AuditEventId,
+    sequence: u64,
+    expected: Sha256Digest,
+    observed: Sha256Digest,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawCanonicalFinding {
+    event_id: AuditEventId,
+    sequence: u64,
+}
+
+impl TryFrom<RawAuditChainFinding> for AuditChainFinding {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawAuditChainFinding) -> Result<Self> {
+        Ok(match raw {
+            RawAuditChainFinding::InvalidRow(raw) => {
+                validate_positive_jcs(raw.row_ordinal)?;
+                if let Some(sequence) = raw.sequence {
+                    validate_positive_jcs(sequence)?;
+                }
+                if (raw.field == AuditInvalidField::Id && raw.event_id.is_some())
+                    || (raw.field == AuditInvalidField::Sequence && raw.sequence.is_some())
+                {
+                    return Err(HeleosError::Integrity);
+                }
+                Self::InvalidRow {
+                    row_ordinal: raw.row_ordinal,
+                    event_id: raw.event_id,
+                    sequence: raw.sequence,
+                    field: raw.field,
+                }
+            }
+            RawAuditChainFinding::SequenceMismatch(raw) => {
+                validate_positive_jcs(raw.expected)?;
+                validate_positive_jcs(raw.observed)?;
+                if raw.expected == raw.observed {
+                    return Err(HeleosError::Integrity);
+                }
+                Self::SequenceMismatch {
+                    event_id: raw.event_id,
+                    expected: raw.expected,
+                    observed: raw.observed,
+                }
+            }
+            RawAuditChainFinding::PreviousHashMismatch(raw) => {
+                validate_positive_jcs(raw.sequence)?;
+                if raw.expected == raw.observed {
+                    return Err(HeleosError::Integrity);
+                }
+                Self::PreviousHashMismatch {
+                    event_id: raw.event_id,
+                    sequence: raw.sequence,
+                    expected: raw.expected,
+                    observed: raw.observed,
+                }
+            }
+            RawAuditChainFinding::EventHashMismatch(raw) => {
+                validate_positive_jcs(raw.sequence)?;
+                if raw.expected == raw.observed {
+                    return Err(HeleosError::Integrity);
+                }
+                Self::EventHashMismatch {
+                    event_id: raw.event_id,
+                    sequence: raw.sequence,
+                    expected: raw.expected,
+                    observed: raw.observed,
+                }
+            }
+            RawAuditChainFinding::NonCanonicalBefore(raw) => {
+                validate_positive_jcs(raw.sequence)?;
+                Self::NonCanonicalBefore {
+                    event_id: raw.event_id,
+                    sequence: raw.sequence,
+                }
+            }
+            RawAuditChainFinding::NonCanonicalAfter(raw) => {
+                validate_positive_jcs(raw.sequence)?;
+                Self::NonCanonicalAfter {
+                    event_id: raw.event_id,
+                    sequence: raw.sequence,
+                }
+            }
+        })
+    }
+}
+
+impl<'de> Deserialize<'de> for AuditChainFinding {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        let raw = RawAuditChainFinding::deserialize(deserializer)?;
+        Self::try_from(raw).map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct AuditChainReport {
+    pub schema: String,
+    pub valid: bool,
+    pub checked_event_count: u64,
+    pub first_sequence: Option<u64>,
+    pub last_sequence: Option<u64>,
+    pub head_hash: Sha256Digest,
+    pub findings: Vec<AuditChainFinding>,
+    pub findings_truncated: bool,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawAuditChainReport {
+    schema: String,
+    valid: bool,
+    checked_event_count: u64,
+    first_sequence: Option<u64>,
+    last_sequence: Option<u64>,
+    head_hash: Sha256Digest,
+    #[serde(deserialize_with = "deserialize_findings")]
+    findings: Vec<AuditChainFinding>,
+    findings_truncated: bool,
+}
+
+fn deserialize_findings<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<Vec<AuditChainFinding>, D::Error> {
+    super::deserialize_bounded_vec::<D, AuditChainFinding, MAX_AUDIT_FINDINGS>(deserializer)
+}
+
+impl TryFrom<RawAuditChainReport> for AuditChainReport {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawAuditChainReport) -> Result<Self> {
+        let report = Self {
+            schema: raw.schema,
+            valid: raw.valid,
+            checked_event_count: raw.checked_event_count,
+            first_sequence: raw.first_sequence,
+            last_sequence: raw.last_sequence,
+            head_hash: raw.head_hash,
+            findings: raw.findings,
+            findings_truncated: raw.findings_truncated,
+        };
+        validate_report(&report)?;
+        Ok(report)
+    }
+}
+
+impl<'de> Deserialize<'de> for AuditChainReport {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        let raw = RawAuditChainReport::deserialize(deserializer)?;
+        Self::try_from(raw).map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Debug)]
+pub(crate) enum RawAuditValue {
+    Null,
+    Integer(i64),
+    Text(String),
+    Invalid,
+}
+
+#[derive(Clone, Debug)]
+pub(crate) struct RawAuditRow {
+    pub id: RawAuditValue,
+    pub sequence: RawAuditValue,
+    pub project_id: RawAuditValue,
+    pub actor: RawAuditValue,
+    pub action: RawAuditValue,
+    pub subject_type: RawAuditValue,
+    pub subject_id: RawAuditValue,
+    pub before_json: RawAuditValue,
+    pub after_json: RawAuditValue,
+    pub reason: RawAuditValue,
+    pub occurred_at_ms: RawAuditValue,
+    pub previous_hash: RawAuditValue,
+    pub event_hash: RawAuditValue,
+}
+
+pub(crate) struct AuditVerifier {
+    report: AuditChainReport,
+    expected_sequence: u64,
+    expected_previous: Sha256Digest,
+}
+
+impl AuditVerifier {
+    pub(crate) fn new() -> Self {
+        let zero = Sha256Digest::from_bytes([0; 32]);
+        Self {
+            report: AuditChainReport {
+                schema: AUDIT_CHAIN_REPORT_SCHEMA_V1.to_owned(),
+                valid: true,
+                checked_event_count: 0,
+                first_sequence: None,
+                last_sequence: None,
+                head_hash: zero,
+                findings: Vec::new(),
+                findings_truncated: false,
+            },
+            expected_sequence: 1,
+            expected_previous: zero,
+        }
+    }
+
+    pub(crate) fn push(&mut self, row: RawAuditRow) -> Result<()> {
+        self.report.checked_event_count = self
+            .report
+            .checked_event_count
+            .checked_add(1)
+            .ok_or(HeleosError::Integrity)?;
+        let ordinal = self.report.checked_event_count;
+        let id = value_text(&row.id).and_then(|value| AuditEventId::from_str(value).ok());
+        let sequence = value_positive_u64(&row.sequence);
+        if let Some(sequence) = sequence {
+            self.report.first_sequence.get_or_insert(sequence);
+            self.report.last_sequence = Some(sequence);
+        }
+
+        let mut invalid = Vec::new();
+        if id.is_none() {
+            invalid.push(AuditInvalidField::Id);
+        }
+        if sequence.is_none() {
+            invalid.push(AuditInvalidField::Sequence);
+        }
+        let project_id = match &row.project_id {
+            RawAuditValue::Null => Some(None),
+            value => value_text(value)
+                .and_then(|text| ProjectId::from_str(text).ok())
+                .map(Some),
+        };
+        if project_id.is_none() {
+            invalid.push(AuditInvalidField::ProjectId);
+        }
+        let actor = value_text(&row.actor).and_then(|value| ActorId::from_str(value).ok());
+        if actor.is_none() {
+            invalid.push(AuditInvalidField::Actor);
+        }
+        let action = value_text(&row.action).and_then(AuditAction::parse);
+        if action.is_none() {
+            invalid.push(AuditInvalidField::Action);
+        }
+        let subject_type = value_text(&row.subject_type).and_then(AuditSubjectType::parse);
+        if subject_type.is_none() {
+            invalid.push(AuditInvalidField::SubjectType);
+        }
+        let subject_id = value_text(&row.subject_id)
+            .filter(|value| validate_audit_text(value, 256, false).is_ok());
+        if subject_id.is_none() {
+            invalid.push(AuditInvalidField::SubjectId);
+        }
+        let before = parse_json_field(&row.before_json);
+        if before.is_none() {
+            invalid.push(AuditInvalidField::Before);
+        }
+        let after = parse_json_field(&row.after_json);
+        if after.is_none() {
+            invalid.push(AuditInvalidField::After);
+        }
+        let reason =
+            value_text(&row.reason).filter(|value| validate_audit_text(value, 1024, true).is_ok());
+        if reason.is_none() {
+            invalid.push(AuditInvalidField::Reason);
+        }
+        let occurred_at_ms = value_nonnegative_i64(&row.occurred_at_ms);
+        if occurred_at_ms.is_none() {
+            invalid.push(AuditInvalidField::OccurredAt);
+        }
+        let previous_hash =
+            value_text(&row.previous_hash).and_then(|value| Sha256Digest::from_str(value).ok());
+        if previous_hash.is_none() {
+            invalid.push(AuditInvalidField::PreviousHash);
+        }
+        let stored_hash =
+            value_text(&row.event_hash).and_then(|value| Sha256Digest::from_str(value).ok());
+        if stored_hash.is_none() {
+            invalid.push(AuditInvalidField::EventHash);
+        }
+
+        for field in invalid {
+            push_finding(
+                &mut self.report,
+                AuditChainFinding::InvalidRow {
+                    row_ordinal: ordinal,
+                    event_id: id,
+                    sequence,
+                    field,
+                },
+            );
+        }
+
+        if let Some(sequence) = sequence {
+            if let Some(id) = id
+                && sequence != self.expected_sequence
+            {
+                push_finding(
+                    &mut self.report,
+                    AuditChainFinding::SequenceMismatch {
+                        event_id: id,
+                        expected: self.expected_sequence,
+                        observed: sequence,
+                    },
+                );
+            }
+            self.expected_sequence = sequence.checked_add(1).ok_or(HeleosError::Integrity)?;
+            if let (Some(id), Some(previous_hash)) = (id, previous_hash) {
+                if previous_hash != self.expected_previous {
+                    push_finding(
+                        &mut self.report,
+                        AuditChainFinding::PreviousHashMismatch {
+                            event_id: id,
+                            sequence,
+                            expected: self.expected_previous,
+                            observed: previous_hash,
+                        },
+                    );
+                }
+            }
+        }
+
+        let before_noncanonical = before.as_ref().is_some_and(|(_, canonical)| !canonical);
+        let after_noncanonical = after.as_ref().is_some_and(|(_, canonical)| !canonical);
+
+        let (
+            Some(id),
+            Some(sequence),
+            Some(project_id),
+            Some(actor),
+            Some(action),
+            Some(subject_type),
+            Some(subject_id),
+            Some((before, _)),
+            Some((after, _)),
+            Some(reason),
+            Some(occurred_at_ms),
+            Some(previous_hash),
+            Some(stored_hash),
+        ) = (
+            id,
+            sequence,
+            project_id,
+            actor,
+            action,
+            subject_type,
+            subject_id,
+            before,
+            after,
+            reason,
+            occurred_at_ms,
+            previous_hash,
+            stored_hash,
+        )
+        else {
+            if let (Some(id), Some(sequence)) = (id, sequence) {
+                if before_noncanonical {
+                    push_finding(
+                        &mut self.report,
+                        AuditChainFinding::NonCanonicalBefore {
+                            event_id: id,
+                            sequence,
+                        },
+                    );
+                }
+                if after_noncanonical {
+                    push_finding(
+                        &mut self.report,
+                        AuditChainFinding::NonCanonicalAfter {
+                            event_id: id,
+                            sequence,
+                        },
+                    );
+                }
+            }
+            return Ok(());
+        };
+        let event = AuditEvent {
+            id,
+            sequence,
+            project_id,
+            actor,
+            action,
+            subject_type,
+            subject_id: subject_id.to_owned(),
+            before,
+            after,
+            reason: reason.to_owned(),
+            occurred_at_ms,
+            previous_hash,
+            event_hash: stored_hash,
+        };
+        let recomputed = event.recomputed_hash()?;
+        if recomputed != stored_hash {
+            push_finding(
+                &mut self.report,
+                AuditChainFinding::EventHashMismatch {
+                    event_id: id,
+                    sequence,
+                    expected: recomputed,
+                    observed: stored_hash,
+                },
+            );
+        }
+        if before_noncanonical {
+            push_finding(
+                &mut self.report,
+                AuditChainFinding::NonCanonicalBefore {
+                    event_id: id,
+                    sequence,
+                },
+            );
+        }
+        if after_noncanonical {
+            push_finding(
+                &mut self.report,
+                AuditChainFinding::NonCanonicalAfter {
+                    event_id: id,
+                    sequence,
+                },
+            );
+        }
+        self.report.head_hash = recomputed;
+        self.expected_previous = recomputed;
+        Ok(())
+    }
+
+    pub(crate) fn finish(mut self) -> Result<AuditChainReport> {
+        self.report.valid = self.report.findings.is_empty() && !self.report.findings_truncated;
+        validate_report(&self.report)?;
+        Ok(self.report)
+    }
+}
+
+#[cfg(test)]
+pub(crate) fn verify_rows(rows: impl IntoIterator<Item = RawAuditRow>) -> Result<AuditChainReport> {
+    let mut verifier = AuditVerifier::new();
+    for row in rows {
+        verifier.push(row)?;
+    }
+    verifier.finish()
+}
+
+fn push_finding(report: &mut AuditChainReport, finding: AuditChainFinding) {
+    if report.findings.len() < MAX_AUDIT_FINDINGS {
+        report.findings.push(finding);
+    } else {
+        report.findings_truncated = true;
+    }
+}
+
+fn value_text(value: &RawAuditValue) -> Option<&str> {
+    match value {
+        RawAuditValue::Text(value) => Some(value),
+        _ => None,
+    }
+}
+
+fn value_positive_u64(value: &RawAuditValue) -> Option<u64> {
+    match value {
+        RawAuditValue::Integer(value) if *value > 0 => u64::try_from(*value)
+            .ok()
+            .filter(|value| *value <= JCS_SAFE_INTEGER_MAX),
+        _ => None,
+    }
+}
+
+fn value_nonnegative_i64(value: &RawAuditValue) -> Option<i64> {
+    match value {
+        RawAuditValue::Integer(value) if *value >= 0 && (*value as u64) <= JCS_SAFE_INTEGER_MAX => {
+            Some(*value)
+        }
+        _ => None,
+    }
+}
+
+fn parse_json_field(value: &RawAuditValue) -> Option<(JsonValue, bool)> {
+    let text = value_text(value)?;
+    if text.len() > 1024 * 1024 {
+        return None;
+    }
+    let parsed: JsonValue = serde_json::from_str(text).ok()?;
+    let canonical = canonical_json(&parsed).ok()? == text.as_bytes();
+    Some((parsed, canonical))
+}
+
+fn validate_positive_jcs(value: u64) -> Result<()> {
+    if value == 0 || value > JCS_SAFE_INTEGER_MAX {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+fn validate_report(report: &AuditChainReport) -> Result<()> {
+    if report.schema != AUDIT_CHAIN_REPORT_SCHEMA_V1
+        || report.checked_event_count > JCS_SAFE_INTEGER_MAX
+        || report.findings.len() > MAX_AUDIT_FINDINGS
+        || (report.findings_truncated && report.findings.len() != MAX_AUDIT_FINDINGS)
+        || report.valid != (report.findings.is_empty() && !report.findings_truncated)
+    {
+        return Err(HeleosError::Integrity);
+    }
+    if let Some(first) = report.first_sequence {
+        validate_positive_jcs(first)?;
+    }
+    if let Some(last) = report.last_sequence {
+        validate_positive_jcs(last)?;
+    }
+    if matches!((report.first_sequence, report.last_sequence), (Some(first), Some(last)) if first > last)
+        || matches!(
+            (report.first_sequence, report.last_sequence),
+            (Some(_), None) | (None, Some(_))
+        )
+    {
+        return Err(HeleosError::Integrity);
+    }
+    let zero = Sha256Digest::from_bytes([0; 32]);
+    if report.checked_event_count == 0
+        && (report.first_sequence.is_some()
+            || report.last_sequence.is_some()
+            || report.head_hash != zero
+            || !report.findings.is_empty()
+            || report.findings_truncated)
+    {
+        return Err(HeleosError::Integrity);
+    }
+    if report.valid && report.checked_event_count > 0 {
+        if report.first_sequence != Some(1)
+            || report.last_sequence != Some(report.checked_event_count)
+        {
+            return Err(HeleosError::Integrity);
+        }
+    }
+    validate_finding_bounds_and_order(report)?;
+    Ok(())
+}
+
+fn validate_finding_bounds_and_order(report: &AuditChainReport) -> Result<()> {
+    let bounds = report.first_sequence.zip(report.last_sequence);
+    let mut previous_invalid = None;
+    let mut previous_row_key = None;
+    let mut segment_key = None;
+    let mut segment_rank = None;
+    let mut segment_invalid_row = None;
+    let mut inferred_segment_count = 0_u64;
+    for finding in &report.findings {
+        let invalid_row = if let AuditChainFinding::InvalidRow {
+            row_ordinal,
+            event_id,
+            sequence,
+            field,
+        } = finding
+        {
+            if *row_ordinal > report.checked_event_count
+                || previous_invalid.is_some_and(
+                    |(previous_row, previous_field, previous_identity)| {
+                        *row_ordinal < previous_row
+                            || (*row_ordinal == previous_row
+                                && ((*event_id, *sequence) != previous_identity
+                                    || *field <= previous_field))
+                    },
+                )
+            {
+                return Err(HeleosError::Integrity);
+            }
+            Some((*row_ordinal, *field, (*event_id, *sequence)))
+        } else {
+            None
+        };
+
+        let sequence = finding_observed_sequence(finding);
+        if let Some(sequence) = sequence {
+            if !matches!(bounds, Some((first, last)) if (first..=last).contains(&sequence)) {
+                return Err(HeleosError::Integrity);
+            }
+        }
+
+        let row_key = sequence.zip(finding_event_id(finding));
+        if let Some(row_key) = row_key {
+            if previous_row_key.is_some_and(|previous| previous > row_key) {
+                return Err(HeleosError::Integrity);
+            }
+            previous_row_key = Some(row_key);
+        }
+
+        let rank = finding_rank(finding);
+        let current_segment_key = match (row_key, invalid_row) {
+            (Some(row_key), _) => FindingSegmentKey::Row(row_key),
+            (None, Some((row_ordinal, _, _))) => FindingSegmentKey::InvalidOnly(row_ordinal),
+            (None, None) => return Err(HeleosError::Integrity),
+        };
+        let starts_new_segment = match (segment_key, current_segment_key) {
+            (None, _) => true,
+            (Some(FindingSegmentKey::Row(previous)), FindingSegmentKey::Row(current)) => {
+                if current > previous {
+                    true
+                } else if current < previous {
+                    return Err(HeleosError::Integrity);
+                } else if let Some((row_ordinal, _, _)) = invalid_row {
+                    if segment_rank == Some(0) && segment_invalid_row == Some(row_ordinal) {
+                        false
+                    } else if previous_invalid
+                        .is_none_or(|(previous_row, _, _)| row_ordinal > previous_row)
+                    {
+                        true
+                    } else {
+                        return Err(HeleosError::Integrity);
+                    }
+                } else if rank == 1 && segment_rank.is_some_and(|previous| previous >= 1) {
+                    true
+                } else if segment_rank.is_some_and(|previous| rank > previous) {
+                    false
+                } else {
+                    return Err(HeleosError::Integrity);
+                }
+            }
+            (
+                Some(FindingSegmentKey::InvalidOnly(previous_row)),
+                FindingSegmentKey::InvalidOnly(current_row),
+            ) => {
+                if current_row > previous_row {
+                    true
+                } else if current_row == previous_row && segment_rank == Some(0) {
+                    false
+                } else {
+                    return Err(HeleosError::Integrity);
+                }
+            }
+            (Some(_), _) => true,
+        };
+
+        if starts_new_segment {
+            inferred_segment_count = inferred_segment_count
+                .checked_add(1)
+                .ok_or(HeleosError::Integrity)?;
+            if inferred_segment_count > report.checked_event_count {
+                return Err(HeleosError::Integrity);
+            }
+            segment_key = Some(current_segment_key);
+            segment_invalid_row = invalid_row.map(|(row_ordinal, _, _)| row_ordinal);
+        } else if rank == 0
+            && segment_invalid_row != invalid_row.map(|(row_ordinal, _, _)| row_ordinal)
+        {
+            return Err(HeleosError::Integrity);
+        }
+        segment_rank = Some(rank);
+        if let Some(invalid) = invalid_row {
+            previous_invalid = Some(invalid);
+        }
+    }
+    Ok(())
+}
+
+#[derive(Clone, Copy)]
+enum FindingSegmentKey {
+    Row((u64, AuditEventId)),
+    InvalidOnly(u64),
+}
+
+fn finding_rank(finding: &AuditChainFinding) -> u8 {
+    match finding {
+        AuditChainFinding::InvalidRow { .. } => 0,
+        AuditChainFinding::SequenceMismatch { .. } => 1,
+        AuditChainFinding::PreviousHashMismatch { .. } => 2,
+        AuditChainFinding::EventHashMismatch { .. } => 3,
+        AuditChainFinding::NonCanonicalBefore { .. } => 4,
+        AuditChainFinding::NonCanonicalAfter { .. } => 5,
+    }
+}
+
+fn finding_observed_sequence(finding: &AuditChainFinding) -> Option<u64> {
+    match finding {
+        AuditChainFinding::InvalidRow { sequence, .. } => *sequence,
+        AuditChainFinding::SequenceMismatch { observed, .. } => Some(*observed),
+        AuditChainFinding::PreviousHashMismatch { sequence, .. }
+        | AuditChainFinding::EventHashMismatch { sequence, .. }
+        | AuditChainFinding::NonCanonicalBefore { sequence, .. }
+        | AuditChainFinding::NonCanonicalAfter { sequence, .. } => Some(*sequence),
+    }
+}
+
+fn finding_event_id(finding: &AuditChainFinding) -> Option<AuditEventId> {
+    match finding {
+        AuditChainFinding::InvalidRow { event_id, .. } => *event_id,
+        AuditChainFinding::SequenceMismatch { event_id, .. }
+        | AuditChainFinding::PreviousHashMismatch { event_id, .. }
+        | AuditChainFinding::EventHashMismatch { event_id, .. }
+        | AuditChainFinding::NonCanonicalBefore { event_id, .. }
+        | AuditChainFinding::NonCanonicalAfter { event_id, .. } => Some(*event_id),
+    }
+}
+
+fn validate_timestamp(value: i64) -> Result<()> {
+    if value < 0 || (value as u64) > JCS_SAFE_INTEGER_MAX {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+fn validate_audit_text(value: &str, max_bytes: usize, ordinary_whitespace: bool) -> Result<()> {
+    let valid_control =
+        |character: char| ordinary_whitespace && matches!(character, '\n' | '\r' | '\t');
+    if value.is_empty()
+        || value.len() > max_bytes
+        || value
+            .chars()
+            .any(|character| character.is_control() && !valid_control(character))
+    {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+fn ensure_canonical_value_size(value: &JsonValue) -> Result<()> {
+    if canonical_json(value)?.len() > 1024 * 1024 {
+        return Err(HeleosError::ResourceLimit);
+    }
+    Ok(())
+}
+
+#[cfg(test)]
+mod tests {
+    use super::*;
+
+    fn text(value: impl Into<String>) -> RawAuditValue {
+        RawAuditValue::Text(value.into())
+    }
+
+    fn row_with_id(id: &str) -> RawAuditRow {
+        RawAuditRow {
+            id: text(id),
+            sequence: RawAuditValue::Integer(1),
+            project_id: RawAuditValue::Null,
+            actor: text("tester"),
+            action: text("project_created"),
+            subject_type: text("project"),
+            subject_id: text("subject"),
+            before_json: text("null"),
+            after_json: text("null"),
+            reason: text("reason"),
+            occurred_at_ms: RawAuditValue::Integer(0),
+            previous_hash: text("0".repeat(64)),
+            event_hash: text("0".repeat(64)),
+        }
+    }
+
+    #[test]
+    fn same_row_findings_follow_the_frozen_variant_order() {
+        // Break caught: canonical-JSON findings emitted before chain mismatches for one row.
+        let mut row = row_with_id("00000000-0000-4000-8000-000000000001");
+        row.sequence = RawAuditValue::Integer(2);
+        row.previous_hash = text("b".repeat(64));
+        row.event_hash = text("c".repeat(64));
+        row.before_json = text(" null");
+        row.after_json = text("null ");
+
+        let report = verify_rows([row]).expect("verify hostile row");
+        assert!(matches!(
+            report.findings.as_slice(),
+            [
+                AuditChainFinding::SequenceMismatch { .. },
+                AuditChainFinding::PreviousHashMismatch { .. },
+                AuditChainFinding::EventHashMismatch { .. },
+                AuditChainFinding::NonCanonicalBefore { .. },
+                AuditChainFinding::NonCanonicalAfter { .. },
+            ]
+        ));
+    }
+
+    #[test]
+    fn public_findings_and_reports_reject_impossible_cross_field_shapes() {
+        // Break caught: externally constructed findings claiming a mismatch that did not occur.
+        for finding in [
+            serde_json::json!({
+                "kind": "sequence_mismatch",
+                "detail": {
+                    "event_id": "00000000-0000-4000-8000-000000000001",
+                    "expected": 1,
+                    "observed": 1
+                }
+            }),
+            serde_json::json!({
+                "kind": "previous_hash_mismatch",
+                "detail": {
+                    "event_id": "00000000-0000-4000-8000-000000000001",
+                    "sequence": 1,
+                    "expected": "1111111111111111111111111111111111111111111111111111111111111111",
+                    "observed": "1111111111111111111111111111111111111111111111111111111111111111"
+                }
+            }),
+            serde_json::json!({
+                "kind": "event_hash_mismatch",
+                "detail": {
+                    "event_id": "00000000-0000-4000-8000-000000000001",
+                    "sequence": 1,
+                    "expected": "2222222222222222222222222222222222222222222222222222222222222222",
+                    "observed": "2222222222222222222222222222222222222222222222222222222222222222"
+                }
+            }),
+            serde_json::json!({
+                "kind": "invalid_row",
+                "detail": {
+                    "row_ordinal": 1,
+                    "event_id": "00000000-0000-4000-8000-000000000001",
+                    "sequence": 1,
+                    "field": "id"
+                }
+            }),
+            serde_json::json!({
+                "kind": "invalid_row",
+                "detail": {
+                    "row_ordinal": 1,
+                    "event_id": null,
+                    "sequence": 1,
+                    "field": "sequence"
+                }
+            }),
+        ] {
+            assert!(serde_json::from_value::<AuditChainFinding>(finding).is_err());
+        }
+
+        let finding = serde_json::json!({
+            "kind": "non_canonical_before",
+            "detail": {
+                "event_id": "00000000-0000-4000-8000-000000000001",
+                "sequence": 1
+            }
+        });
+        let short_truncated = serde_json::json!({
+            "schema": AUDIT_CHAIN_REPORT_SCHEMA_V1,
+            "valid": false,
+            "checked_event_count": 1,
+            "first_sequence": 1,
+            "last_sequence": 1,
+            "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
+            "findings": [finding.clone()],
+            "findings_truncated": true
+        });
+        assert!(serde_json::from_value::<AuditChainReport>(short_truncated).is_err());
+
+        let exact_truncated = serde_json::json!({
+            "schema": AUDIT_CHAIN_REPORT_SCHEMA_V1,
+            "valid": false,
+            "checked_event_count": 129,
+            "first_sequence": 1,
+            "last_sequence": 129,
+            "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
+            "findings": (1..=MAX_AUDIT_FINDINGS).map(|ordinal| serde_json::json!({
+                "kind": "invalid_row",
+                "detail": {
+                    "row_ordinal": ordinal,
+                    "event_id": null,
+                    "sequence": ordinal,
+                    "field": "id"
+                }
+            })).collect::<Vec<_>>(),
+            "findings_truncated": true
+        });
+        serde_json::from_value::<AuditChainReport>(exact_truncated)
+            .expect("truncation at the exact published cap is representable");
+    }
+
+    #[test]
+    fn verifier_emits_each_finding_from_only_its_required_fields() {
+        // Break caught: one unrelated invalid field suppressing independently provable defects.
+        let mut row = row_with_id("00000000-0000-4000-8000-000000000001");
+        row.sequence = RawAuditValue::Integer(2);
+        row.actor = RawAuditValue::Invalid;
+        row.previous_hash = text("b".repeat(64));
+        row.before_json = text(" null");
+
+        let report = verify_rows([row]).expect("verify partially malformed row");
+        assert!(matches!(
+            report.findings.as_slice(),
+            [
+                AuditChainFinding::InvalidRow {
+                    field: AuditInvalidField::Actor,
+                    ..
+                },
+                AuditChainFinding::SequenceMismatch { .. },
+                AuditChainFinding::PreviousHashMismatch { .. },
+                AuditChainFinding::NonCanonicalBefore { .. },
+            ]
+        ));
+    }
+
+    #[test]
+    fn verifier_resynchronizes_sequence_after_an_unrelated_invalid_field() {
+        // Break caught: an invalid actor at sequence 1 making valid sequence 2 look like a gap.
+        let mut first = row_with_id("00000000-0000-4000-8000-000000000001");
+        first.actor = RawAuditValue::Invalid;
+        let mut second = row_with_id("00000000-0000-4000-8000-000000000002");
+        second.sequence = RawAuditValue::Integer(2);
+
+        let report = verify_rows([first, second]).expect("verify two-row sequence");
+        assert!(report.findings.iter().any(|finding| matches!(
+            finding,
+            AuditChainFinding::InvalidRow {
+                field: AuditInvalidField::Actor,
+                ..
+            }
+        )));
+        assert!(
+            !report
+                .findings
+                .iter()
+                .any(|finding| matches!(finding, AuditChainFinding::SequenceMismatch { .. }))
+        );
+    }
+
+    #[test]
+    fn duplicate_hostile_row_keys_produce_an_invalid_report_not_an_error() {
+        // Break caught: public ordering mistaking a new duplicate row for a variant permutation.
+        let id = "00000000-0000-4000-8000-000000000001";
+        let mut first = row_with_id(id);
+        first.event_hash = text("c".repeat(64));
+        first.before_json = text(" null");
+        let mut second = row_with_id(id);
+        second.event_hash = text("c".repeat(64));
+
+        let report = verify_rows([first, second]).expect("verify duplicate hostile row keys");
+        assert_eq!(report.checked_event_count, 2);
+        assert!(!report.valid);
+        assert!(
+            report
+                .findings
+                .iter()
+                .any(|finding| matches!(finding, AuditChainFinding::SequenceMismatch { .. }))
+        );
+        let encoded = serde_json::to_value(&report).expect("serialize generated duplicate report");
+        let decoded = serde_json::from_value::<AuditChainReport>(encoded)
+            .expect("generated duplicate report must round trip");
+        assert_eq!(decoded, report);
+    }
+
+    #[test]
+    fn public_report_binds_finding_bounds_and_canonical_order() {
+        // Break caught: externally supplied reports moving findings to nonexistent rows/sequences.
+        let event_one = "00000000-0000-4000-8000-000000000001";
+        let event_two = "00000000-0000-4000-8000-000000000002";
+        let report = |checked_event_count: u64,
+                      first_sequence: Option<u64>,
+                      last_sequence: Option<u64>,
+                      findings: Vec<serde_json::Value>| {
+            serde_json::json!({
+                "schema": AUDIT_CHAIN_REPORT_SCHEMA_V1,
+                "valid": false,
+                "checked_event_count": checked_event_count,
+                "first_sequence": first_sequence,
+                "last_sequence": last_sequence,
+                "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
+                "findings": findings,
+                "findings_truncated": false
+            })
+        };
+        let invalid = |row_ordinal: u64, field: &str, sequence: Option<u64>| {
+            serde_json::json!({
+                "kind": "invalid_row",
+                "detail": {
+                    "row_ordinal": row_ordinal,
+                    "event_id": event_one,
+                    "sequence": sequence,
+                    "field": field
+                }
+            })
+        };
+        let noncanonical = |event_id: &str, sequence: u64| {
+            serde_json::json!({
+                "kind": "non_canonical_before",
+                "detail": {"event_id": event_id, "sequence": sequence}
+            })
+        };
+        let mismatch = serde_json::json!({
+            "kind": "event_hash_mismatch",
+            "detail": {
+                "event_id": event_one,
+                "sequence": 1,
+                "expected": "1111111111111111111111111111111111111111111111111111111111111111",
+                "observed": "2222222222222222222222222222222222222222222222222222222222222222"
+            }
+        });
+        let sequence_mismatch = |expected: u64| {
+            serde_json::json!({
+                "kind": "sequence_mismatch",
+                "detail": {
+                    "event_id": event_one,
+                    "expected": expected,
+                    "observed": 1
+                }
+            })
+        };
+
+        for hostile in [
+            report(1, Some(1), Some(1), vec![invalid(2, "actor", Some(1))]),
+            report(1, Some(1), Some(1), vec![invalid(1, "actor", Some(2))]),
+            report(1, Some(1), Some(1), vec![noncanonical(event_one, 2)]),
+            report(
+                2,
+                Some(1),
+                Some(2),
+                vec![noncanonical(event_two, 2), noncanonical(event_one, 1)],
+            ),
+            report(
+                1,
+                Some(1),
+                Some(1),
+                vec![invalid(1, "action", Some(1)), invalid(1, "actor", Some(1))],
+            ),
+            report(
+                1,
+                Some(1),
+                Some(1),
+                vec![noncanonical(event_one, 1), mismatch.clone()],
+            ),
+            report(
+                1,
+                Some(1),
+                Some(1),
+                vec![sequence_mismatch(2), sequence_mismatch(3)],
+            ),
+            report(
+                2,
+                Some(1),
+                Some(1),
+                vec![mismatch.clone(), mismatch.clone()],
+            ),
+        ] {
+            assert!(serde_json::from_value::<AuditChainReport>(hostile).is_err());
+        }
+
+        serde_json::from_value::<AuditChainReport>(report(
+            2,
+            Some(1),
+            Some(1),
+            vec![sequence_mismatch(2), sequence_mismatch(3)],
+        ))
+        .expect("two visible duplicate-row segments fit two checked rows");
+
+        serde_json::from_value::<AuditChainReport>(report(
+            1,
+            Some(1),
+            Some(1),
+            vec![
+                invalid(1, "actor", Some(1)),
+                invalid(1, "action", Some(1)),
+                mismatch,
+                noncanonical(event_one, 1),
+            ],
+        ))
+        .expect("canonical same-row finding order");
+    }
+
+    #[test]
+    fn public_report_binds_each_invalid_row_ordinal_to_one_identity() {
+        // Break caught: a later visible row reusing an ordinal with another event identity.
+        let event_one = "00000000-0000-4000-8000-000000000001";
+        let event_two = "00000000-0000-4000-8000-000000000002";
+        let report =
+            |checked_event_count: u64, last_sequence: u64, findings: Vec<serde_json::Value>| {
+                serde_json::json!({
+                    "schema": AUDIT_CHAIN_REPORT_SCHEMA_V1,
+                    "valid": false,
+                    "checked_event_count": checked_event_count,
+                    "first_sequence": 1,
+                    "last_sequence": last_sequence,
+                    "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
+                    "findings": findings,
+                    "findings_truncated": false
+                })
+            };
+        let invalid = |event_id: &str, sequence: u64, field: &str| {
+            serde_json::json!({
+                "kind": "invalid_row",
+                "detail": {
+                    "row_ordinal": 1,
+                    "event_id": event_id,
+                    "sequence": sequence,
+                    "field": field
+                }
+            })
+        };
+
+        serde_json::from_value::<AuditChainReport>(report(
+            1,
+            1,
+            vec![
+                invalid(event_one, 1, "actor"),
+                invalid(event_one, 1, "action"),
+            ],
+        ))
+        .expect("multiple invalid fields from one exact row identity");
+
+        for hostile in [
+            report(
+                2,
+                1,
+                vec![
+                    invalid(event_one, 1, "actor"),
+                    invalid(event_two, 1, "action"),
+                ],
+            ),
+            report(
+                2,
+                2,
+                vec![
+                    invalid(event_one, 1, "actor"),
+                    invalid(event_one, 2, "action"),
+                ],
+            ),
+        ] {
+            assert!(serde_json::from_value::<AuditChainReport>(hostile).is_err());
+        }
+    }
+
+    #[test]
+    fn the_129th_finding_sets_truncation_without_being_published() {
+        // Break caught: an attacker growing audit findings beyond the fixed public cap.
+        let rows = (0..129).map(|index| {
+            let mut row = row_with_id("not-a-uuid");
+            row.sequence = RawAuditValue::Integer(i64::from(index + 1));
+            row
+        });
+        let report = verify_rows(rows).expect("verify bounded hostile rows");
+        assert_eq!(report.checked_event_count, 129);
+        assert_eq!(report.findings.len(), 128);
+        assert!(report.findings_truncated);
+        assert!(!report.valid);
+    }
+
+    #[test]
+    fn public_report_deserialization_stops_at_the_128_finding_cap() {
+        // Break caught: derived Vec deserialization allocated every hostile finding first.
+        let findings = (1..=MAX_AUDIT_FINDINGS)
+            .map(|ordinal| {
+                serde_json::json!({
+                    "kind": "invalid_row",
+                    "detail": {
+                        "row_ordinal": ordinal,
+                        "event_id": null,
+                        "sequence": ordinal,
+                        "field": "id"
+                    }
+                })
+            })
+            .collect::<Vec<_>>();
+        let at_cap = serde_json::json!({
+            "schema": AUDIT_CHAIN_REPORT_SCHEMA_V1,
+            "valid": false,
+            "checked_event_count": 128,
+            "first_sequence": 1,
+            "last_sequence": 128,
+            "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
+            "findings": findings,
+            "findings_truncated": false
+        });
+        serde_json::from_value::<AuditChainReport>(at_cap.clone())
+            .expect("the exact finding cap is accepted");
+        let mut over_cap = at_cap;
+        over_cap["findings"]
+            .as_array_mut()
+            .expect("findings array")
+            .push(serde_json::json!({
+                "kind": "invalid_row",
+                "detail": {
+                    "row_ordinal": 129,
+                    "event_id": null,
+                    "sequence": 129,
+                    "field": "id"
+                }
+            }));
+        let error = serde_json::from_value::<AuditChainReport>(over_cap)
+            .expect_err("the 129th finding must fail during sequence decoding");
+        assert!(error.to_string().contains("maximum of 128"));
+    }
+
+    #[test]
+    fn public_event_deserialization_checks_subject_and_reason_bytes_before_copy() {
+        // Break caught: oversized audit scalars allocated before their public DTO caps ran.
+        let event = AuditEvent::build(AuditEventInput {
+            id: AuditEventId::from_uuid(
+                Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("audit UUID"),
+            ),
+            sequence: 1,
+            project_id: None,
+            actor: ActorId::from_str("tester").expect("actor"),
+            action: AuditAction::ProjectCreated,
+            subject_type: AuditSubjectType::Project,
+            subject_id: "s".repeat(256),
+            before: JsonValue::Null,
+            after: JsonValue::Null,
+            reason: "r".repeat(1024),
+            occurred_at_ms: 0,
+            previous_hash: Sha256Digest::from_bytes([0; 32]),
+        })
+        .expect("event at exact string caps");
+        let at_cap = serde_json::to_value(event).expect("event JSON");
+        serde_json::from_value::<AuditEvent>(at_cap.clone()).expect("exact string caps round-trip");
+
+        let mut subject_over = at_cap.clone();
+        subject_over["subject_id"] = serde_json::Value::String("s".repeat(257));
+        let error = serde_json::from_value::<AuditEvent>(subject_over)
+            .expect_err("subject N+1 must fail during string decoding");
+        assert!(error.to_string().contains("maximum of 256 bytes"));
+
+        let mut reason_over = at_cap;
+        reason_over["reason"] = serde_json::Value::String("r".repeat(1025));
+        let error = serde_json::from_value::<AuditEvent>(reason_over)
+            .expect_err("reason N+1 must fail during string decoding");
+        assert!(error.to_string().contains("maximum of 1024 bytes"));
+    }
+}
diff --git a/crates/heleos-core/src/ingest/evidence.rs b/crates/heleos-core/src/ingest/evidence.rs
new file mode 100644
index 0000000..edc2506
--- /dev/null
+++ b/crates/heleos-core/src/ingest/evidence.rs
@@ -0,0 +1,606 @@
+use std::io::{self, Write};
+
+use serde::{Deserialize, Deserializer, Serialize, de};
+
+use crate::pdf::geometry::validate_page_metadata;
+use crate::{
+    DocumentId, EvidenceId, HeleosError, JobId, PageMetadata, PdfLimits, PdfProbeProvenance,
+    ProjectId, Result, RevisionId, Sha256Digest, canonical_document_ids,
+};
+
+use super::audit::JCS_SAFE_INTEGER_MAX;
+
+pub const EVIDENCE_MANIFEST_SCHEMA_V1: &str = "heleos.evidence-manifest/v1";
+pub const PDF_MEDIA_TYPE: &str = "application/pdf";
+pub const EVIDENCE_MANIFEST_MEDIA_TYPE: &str =
+    "application/vnd.heleos.evidence-manifest+json;version=1";
+pub const MAX_EVIDENCE_MANIFEST_BYTES: usize = 16 * 1024 * 1024;
+pub(crate) const MAX_EVIDENCE_LINEAGES: usize = 100_000;
+pub(crate) const EVIDENCE_PARAMETERS_SCHEMA_V1: &str = "heleos.evidence-parameters/v1";
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub(crate) struct EvidenceParametersV1 {
+    pub schema: String,
+    pub original_media_type: String,
+    pub manifest_media_type: String,
+    pub probe_provenance: PdfProbeProvenance,
+    pub requested_limits: EvidencePdfLimitsV1,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawEvidenceParametersV1 {
+    schema: String,
+    original_media_type: String,
+    manifest_media_type: String,
+    probe_provenance: PdfProbeProvenance,
+    requested_limits: EvidencePdfLimitsV1,
+}
+
+impl<'de> Deserialize<'de> for EvidenceParametersV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        let raw = RawEvidenceParametersV1::deserialize(deserializer)?;
+        let value = Self {
+            schema: raw.schema,
+            original_media_type: raw.original_media_type,
+            manifest_media_type: raw.manifest_media_type,
+            probe_provenance: raw.probe_provenance,
+            requested_limits: raw.requested_limits,
+        };
+        value.validate().map_err(de::Error::custom)?;
+        Ok(value)
+    }
+}
+
+impl EvidenceParametersV1 {
+    pub(crate) fn new(
+        probe_provenance: PdfProbeProvenance,
+        requested_limits: EvidencePdfLimitsV1,
+    ) -> Result<Self> {
+        let value = Self {
+            schema: EVIDENCE_PARAMETERS_SCHEMA_V1.to_owned(),
+            original_media_type: PDF_MEDIA_TYPE.to_owned(),
+            manifest_media_type: EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
+            probe_provenance,
+            requested_limits,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+
+    pub(crate) fn validate(&self) -> Result<()> {
+        validate_provenance(&self.probe_provenance)?;
+        if self.schema != EVIDENCE_PARAMETERS_SCHEMA_V1
+            || self.original_media_type != PDF_MEDIA_TYPE
+            || self.manifest_media_type != EVIDENCE_MANIFEST_MEDIA_TYPE
+            || self.requested_limits != EvidencePdfLimitsV1::from(PdfLimits::default())
+        {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct EvidenceContentV1 {
+    pub sha256: Sha256Digest,
+    pub byte_length: u64,
+    pub media_type: String,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawEvidenceContentV1 {
+    sha256: Sha256Digest,
+    byte_length: u64,
+    media_type: String,
+}
+
+impl TryFrom<RawEvidenceContentV1> for EvidenceContentV1 {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawEvidenceContentV1) -> Result<Self> {
+        if raw.byte_length > JCS_SAFE_INTEGER_MAX
+            || !matches!(
+                raw.media_type.as_str(),
+                PDF_MEDIA_TYPE | EVIDENCE_MANIFEST_MEDIA_TYPE
+            )
+        {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(Self {
+            sha256: raw.sha256,
+            byte_length: raw.byte_length,
+            media_type: raw.media_type,
+        })
+    }
+}
+
+impl<'de> Deserialize<'de> for EvidenceContentV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawEvidenceContentV1::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
+pub struct EvidencePdfLimitsV1 {
+    pub max_input_bytes: u64,
+    pub max_pages: u32,
+    pub max_indirect_objects: u32,
+    pub max_nested_references: u32,
+    pub max_metadata_bytes: u64,
+    pub max_page_axis_points: u32,
+    pub max_guest_memory_bytes: u64,
+    pub max_instances: u32,
+    pub max_tables: u32,
+    pub max_fuel: u64,
+    pub timeout_seconds: u64,
+    pub max_protocol_output_bytes: u64,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawEvidencePdfLimitsV1 {
+    max_input_bytes: u64,
+    max_pages: u32,
+    max_indirect_objects: u32,
+    max_nested_references: u32,
+    max_metadata_bytes: u64,
+    max_page_axis_points: u32,
+    max_guest_memory_bytes: u64,
+    max_instances: u32,
+    max_tables: u32,
+    max_fuel: u64,
+    timeout_seconds: u64,
+    max_protocol_output_bytes: u64,
+}
+
+impl TryFrom<RawEvidencePdfLimitsV1> for EvidencePdfLimitsV1 {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawEvidencePdfLimitsV1) -> Result<Self> {
+        let value = Self {
+            max_input_bytes: raw.max_input_bytes,
+            max_pages: raw.max_pages,
+            max_indirect_objects: raw.max_indirect_objects,
+            max_nested_references: raw.max_nested_references,
+            max_metadata_bytes: raw.max_metadata_bytes,
+            max_page_axis_points: raw.max_page_axis_points,
+            max_guest_memory_bytes: raw.max_guest_memory_bytes,
+            max_instances: raw.max_instances,
+            max_tables: raw.max_tables,
+            max_fuel: raw.max_fuel,
+            timeout_seconds: raw.timeout_seconds,
+            max_protocol_output_bytes: raw.max_protocol_output_bytes,
+        };
+        if value != Self::from(PdfLimits::default()) {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for EvidencePdfLimitsV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawEvidencePdfLimitsV1::deserialize(deserializer)?)
+            .map_err(de::Error::custom)
+    }
+}
+
+impl From<PdfLimits> for EvidencePdfLimitsV1 {
+    fn from(value: PdfLimits) -> Self {
+        Self {
+            max_input_bytes: value.max_input_bytes,
+            max_pages: value.max_pages,
+            max_indirect_objects: value.max_indirect_objects,
+            max_nested_references: value.max_nested_references,
+            max_metadata_bytes: value.max_metadata_bytes,
+            max_page_axis_points: value.max_page_axis_points,
+            max_guest_memory_bytes: value.max_guest_memory_bytes,
+            max_instances: value.max_instances,
+            max_tables: value.max_tables,
+            max_fuel: value.max_fuel,
+            timeout_seconds: value.timeout_seconds,
+            max_protocol_output_bytes: value.max_protocol_output_bytes,
+        }
+    }
+}
+
+impl From<EvidencePdfLimitsV1> for PdfLimits {
+    fn from(value: EvidencePdfLimitsV1) -> Self {
+        Self {
+            max_input_bytes: value.max_input_bytes,
+            max_pages: value.max_pages,
+            max_indirect_objects: value.max_indirect_objects,
+            max_nested_references: value.max_nested_references,
+            max_metadata_bytes: value.max_metadata_bytes,
+            max_page_axis_points: value.max_page_axis_points,
+            max_guest_memory_bytes: value.max_guest_memory_bytes,
+            max_instances: value.max_instances,
+            max_tables: value.max_tables,
+            max_fuel: value.max_fuel,
+            timeout_seconds: value.timeout_seconds,
+            max_protocol_output_bytes: value.max_protocol_output_bytes,
+        }
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct EvidenceManifestV1 {
+    pub schema: String,
+    pub original: EvidenceContentV1,
+    pub document_id: DocumentId,
+    pub revision_id: RevisionId,
+    pub pages: Vec<PageMetadata>,
+    pub probe_provenance: PdfProbeProvenance,
+    pub requested_limits: EvidencePdfLimitsV1,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawEvidenceManifestV1 {
+    schema: String,
+    original: EvidenceContentV1,
+    document_id: DocumentId,
+    revision_id: RevisionId,
+    #[serde(deserialize_with = "deserialize_pages")]
+    pages: Vec<PageMetadata>,
+    probe_provenance: PdfProbeProvenance,
+    requested_limits: EvidencePdfLimitsV1,
+}
+
+impl TryFrom<RawEvidenceManifestV1> for EvidenceManifestV1 {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawEvidenceManifestV1) -> Result<Self> {
+        let value = Self {
+            schema: raw.schema,
+            original: raw.original,
+            document_id: raw.document_id,
+            revision_id: raw.revision_id,
+            pages: raw.pages,
+            probe_provenance: raw.probe_provenance,
+            requested_limits: raw.requested_limits,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for EvidenceManifestV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawEvidenceManifestV1::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+impl EvidenceManifestV1 {
+    pub(crate) fn new(
+        original_sha256: Sha256Digest,
+        original_byte_length: u64,
+        pages: Vec<PageMetadata>,
+        probe_provenance: PdfProbeProvenance,
+    ) -> Result<Self> {
+        let (document_id, revision_id) = canonical_document_ids(original_sha256);
+        let value = Self {
+            schema: EVIDENCE_MANIFEST_SCHEMA_V1.to_owned(),
+            original: EvidenceContentV1 {
+                sha256: original_sha256,
+                byte_length: original_byte_length,
+                media_type: PDF_MEDIA_TYPE.to_owned(),
+            },
+            document_id,
+            revision_id,
+            pages,
+            probe_provenance,
+            requested_limits: PdfLimits::default().into(),
+        };
+        value.validate()?;
+        Ok(value)
+    }
+
+    pub(crate) fn validate(&self) -> Result<()> {
+        if self.schema != EVIDENCE_MANIFEST_SCHEMA_V1
+            || self.original.media_type != PDF_MEDIA_TYPE
+            || self.original.byte_length > JCS_SAFE_INTEGER_MAX
+            || self.requested_limits != EvidencePdfLimitsV1::from(PdfLimits::default())
+            || self.original.byte_length > self.requested_limits.max_input_bytes
+            || self.pages.is_empty()
+        {
+            return Err(HeleosError::Integrity);
+        }
+        let (document_id, revision_id) = canonical_document_ids(self.original.sha256);
+        if self.document_id != document_id || self.revision_id != revision_id {
+            return Err(HeleosError::Integrity);
+        }
+        validate_provenance(&self.probe_provenance)?;
+        validate_page_metadata(&self.pages, self.original.sha256, PdfLimits::default())
+    }
+
+    pub(crate) fn canonical_bytes(&self) -> Result<Vec<u8>> {
+        self.validate()?;
+        bounded_canonical_json(self, MAX_EVIDENCE_MANIFEST_BYTES)
+    }
+}
+
+#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
+#[serde(deny_unknown_fields)]
+pub struct EvidenceManifestLineage {
+    pub project_id: ProjectId,
+    pub evidence_id: EvidenceId,
+    pub originating_job_id: JobId,
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct EvidenceManifestReceipt {
+    pub manifest: EvidenceManifestV1,
+    pub manifest_content_sha256: Sha256Digest,
+    pub manifest_byte_length: u64,
+    pub manifest_media_type: String,
+    pub manifest_vault_key: String,
+    pub original_vault_key: String,
+    pub lineages: Vec<EvidenceManifestLineage>,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawEvidenceManifestReceipt {
+    manifest: EvidenceManifestV1,
+    manifest_content_sha256: Sha256Digest,
+    manifest_byte_length: u64,
+    manifest_media_type: String,
+    manifest_vault_key: String,
+    original_vault_key: String,
+    #[serde(deserialize_with = "deserialize_lineages")]
+    lineages: Vec<EvidenceManifestLineage>,
+}
+
+fn deserialize_pages<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<Vec<PageMetadata>, D::Error> {
+    super::deserialize_bounded_vec::<D, PageMetadata, 10_000>(deserializer)
+}
+
+fn deserialize_lineages<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<Vec<EvidenceManifestLineage>, D::Error> {
+    super::deserialize_bounded_vec::<D, EvidenceManifestLineage, MAX_EVIDENCE_LINEAGES>(
+        deserializer,
+    )
+}
+
+impl TryFrom<RawEvidenceManifestReceipt> for EvidenceManifestReceipt {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawEvidenceManifestReceipt) -> Result<Self> {
+        let value = Self {
+            manifest: raw.manifest,
+            manifest_content_sha256: raw.manifest_content_sha256,
+            manifest_byte_length: raw.manifest_byte_length,
+            manifest_media_type: raw.manifest_media_type,
+            manifest_vault_key: raw.manifest_vault_key,
+            original_vault_key: raw.original_vault_key,
+            lineages: raw.lineages,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for EvidenceManifestReceipt {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawEvidenceManifestReceipt::deserialize(deserializer)?)
+            .map_err(de::Error::custom)
+    }
+}
+
+impl EvidenceManifestReceipt {
+    pub(crate) fn validate(&self) -> Result<()> {
+        self.manifest.validate()?;
+        let bytes = self.manifest.canonical_bytes()?;
+        let byte_length = u64::try_from(bytes.len()).map_err(|_| HeleosError::Integrity)?;
+        let digest = Sha256Digest::hash_reader(bytes.as_slice())?;
+        if self.manifest_content_sha256 != digest
+            || self.manifest_byte_length != byte_length
+            || self.manifest_byte_length > JCS_SAFE_INTEGER_MAX
+            || self.manifest_media_type != EVIDENCE_MANIFEST_MEDIA_TYPE
+            || self.manifest_vault_key != crate::Vault::object_key(digest)
+            || self.original_vault_key != crate::Vault::object_key(self.manifest.original.sha256)
+            || self.lineages.is_empty()
+            || self.lineages.len() > MAX_EVIDENCE_LINEAGES
+        {
+            return Err(HeleosError::Integrity);
+        }
+        let mut previous = None;
+        for lineage in &self.lineages {
+            let key = (
+                *lineage.project_id.as_uuid().as_bytes(),
+                *lineage.evidence_id.as_uuid().as_bytes(),
+                *lineage.originating_job_id.as_uuid().as_bytes(),
+            );
+            if previous.as_ref().is_some_and(|previous| previous >= &key) {
+                return Err(HeleosError::Integrity);
+            }
+            previous = Some(key);
+        }
+        Ok(())
+    }
+}
+
+pub(crate) fn validate_provenance(value: &PdfProbeProvenance) -> Result<()> {
+    value.validate()
+}
+
+struct BoundedWriter {
+    bytes: Vec<u8>,
+    maximum: usize,
+}
+
+impl Write for BoundedWriter {
+    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
+        let next = self
+            .bytes
+            .len()
+            .checked_add(buffer.len())
+            .ok_or_else(|| io::Error::other("canonical JSON exceeds bound"))?;
+        if next > self.maximum {
+            return Err(io::Error::other("canonical JSON exceeds bound"));
+        }
+        self.bytes.extend_from_slice(buffer);
+        Ok(buffer.len())
+    }
+
+    fn flush(&mut self) -> io::Result<()> {
+        Ok(())
+    }
+}
+
+pub(crate) fn bounded_canonical_json<T: Serialize>(value: &T, maximum: usize) -> Result<Vec<u8>> {
+    let mut writer = BoundedWriter {
+        bytes: Vec::new(),
+        maximum,
+    };
+    serde_jcs::to_writer(&mut writer, value).map_err(|_| HeleosError::ResourceLimit)?;
+    if writer.bytes.is_empty() {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(writer.bytes)
+}
+
+#[cfg(test)]
+mod tests {
+    use uuid::Uuid;
+
+    use super::*;
+    use crate::{PageTransform, PageUnit, page_id};
+
+    fn provenance() -> PdfProbeProvenance {
+        PdfProbeProvenance {
+            parser_name: "fixture-parser".to_owned(),
+            parser_version: "1.0.0".to_owned(),
+            guest_wasm_sha256: Sha256Digest::from_bytes([2; 32]),
+            guest_source_tree_sha256: Sha256Digest::from_bytes([3; 32]),
+            guest_dependency_graph_sha256: Sha256Digest::from_bytes([4; 32]),
+            protocol_version: "heleos.pdf-probe/v1".to_owned(),
+        }
+    }
+
+    fn pages(content: Sha256Digest, count: u32) -> Vec<PageMetadata> {
+        (0..count)
+            .map(|index| PageMetadata {
+                index,
+                page_id: page_id(content, index),
+                width_micropoints: 612_000_000,
+                height_micropoints: 792_000_000,
+                unit: PageUnit::Point,
+                rotation_degrees: 0,
+                transform: PageTransform {
+                    m11: 1,
+                    m12: 0,
+                    m21: 0,
+                    m22: -1,
+                    tx_micropoints: 0,
+                    ty_micropoints: 792_000_000,
+                },
+            })
+            .collect()
+    }
+
+    fn one_page_manifest() -> EvidenceManifestV1 {
+        let content = Sha256Digest::from_bytes([1; 32]);
+        EvidenceManifestV1::new(content, 7, pages(content, 1), provenance())
+            .expect("one-page manifest")
+    }
+
+    #[test]
+    fn manifest_deserialization_stops_at_the_10_000_page_cap() {
+        // Break caught: hostile public JSON allocating every page before validation.
+        let content = Sha256Digest::from_bytes([1; 32]);
+        let manifest = EvidenceManifestV1::new(content, 7, pages(content, 10_000), provenance())
+            .expect("manifest at exact page cap");
+        let at_cap = serde_json::to_value(manifest).expect("manifest JSON");
+        serde_json::from_value::<EvidenceManifestV1>(at_cap.clone())
+            .expect("exact page cap round-trips");
+        let mut over_cap = at_cap;
+        let page = over_cap["pages"][9_999].clone();
+        over_cap["pages"]
+            .as_array_mut()
+            .expect("pages array")
+            .push(page);
+        let error = serde_json::from_value::<EvidenceManifestV1>(over_cap)
+            .expect_err("10,001st page must fail during sequence decoding");
+        assert!(error.to_string().contains("maximum of 10000"));
+    }
+
+    #[test]
+    fn public_evidence_limits_reject_nondefault_profiles() {
+        // Break caught: a nested/public limit DTO bypassing Task 6's fixed default profile.
+        let mut value = serde_json::to_value(EvidencePdfLimitsV1::from(PdfLimits::default()))
+            .expect("limits JSON");
+        value["max_pages"] = serde_json::json!(9_999);
+        assert!(serde_json::from_value::<EvidencePdfLimitsV1>(value).is_err());
+    }
+
+    #[test]
+    fn manifest_original_length_is_bounded_by_its_requested_input_limit() {
+        // Break caught: an evidence manifest claiming an accepted over-limit original.
+        let content = Sha256Digest::from_bytes([1; 32]);
+        let maximum = PdfLimits::default().max_input_bytes;
+        let at_maximum = EvidenceManifestV1::new(content, maximum, pages(content, 1), provenance())
+            .expect("manifest at exact input cap");
+        serde_json::from_value::<EvidenceManifestV1>(
+            serde_json::to_value(&at_maximum).expect("manifest JSON"),
+        )
+        .expect("exact input cap round-trips");
+
+        assert!(matches!(
+            EvidenceManifestV1::new(content, maximum + 1, pages(content, 1), provenance(),),
+            Err(HeleosError::Integrity)
+        ));
+        let mut over_limit = serde_json::to_value(at_maximum).expect("manifest JSON");
+        over_limit["original"]["byte_length"] = serde_json::json!(maximum + 1);
+        assert!(serde_json::from_value::<EvidenceManifestV1>(over_limit).is_err());
+    }
+
+    #[test]
+    fn receipt_deserialization_stops_at_the_100_000_lineage_cap() {
+        // Break caught: hostile reader/public JSON allocating unbounded evidence lineages.
+        let manifest = one_page_manifest();
+        let manifest_bytes = manifest.canonical_bytes().expect("canonical manifest");
+        let manifest_digest =
+            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest");
+        let project_id = ProjectId::from_uuid(Uuid::from_u128(1));
+        let job_id = JobId::from_uuid(Uuid::from_u128(2));
+        let lineages = (1..=MAX_EVIDENCE_LINEAGES)
+            .map(|index| EvidenceManifestLineage {
+                project_id,
+                evidence_id: EvidenceId::from_uuid(Uuid::from_u128(
+                    u128::try_from(index).expect("lineage ordinal fits") + 10,
+                )),
+                originating_job_id: job_id,
+            })
+            .collect();
+        let receipt = EvidenceManifestReceipt {
+            original_vault_key: crate::Vault::object_key(manifest.original.sha256),
+            manifest,
+            manifest_content_sha256: manifest_digest,
+            manifest_byte_length: u64::try_from(manifest_bytes.len())
+                .expect("manifest length fits"),
+            manifest_media_type: EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
+            manifest_vault_key: crate::Vault::object_key(manifest_digest),
+            lineages,
+        };
+        receipt.validate().expect("receipt at exact lineage cap");
+        let at_cap = serde_json::to_value(receipt).expect("receipt JSON");
+        serde_json::from_value::<EvidenceManifestReceipt>(at_cap.clone())
+            .expect("exact lineage cap round-trips");
+        let mut over_cap = at_cap;
+        let lineage = over_cap["lineages"][MAX_EVIDENCE_LINEAGES - 1].clone();
+        over_cap["lineages"]
+            .as_array_mut()
+            .expect("lineages array")
+            .push(lineage);
+        let error = serde_json::from_value::<EvidenceManifestReceipt>(over_cap)
+            .expect_err("100,001st lineage must fail during sequence decoding");
+        assert!(error.to_string().contains("maximum of 100000"));
+    }
+}
diff --git a/crates/heleos-core/src/ingest/fault.rs b/crates/heleos-core/src/ingest/fault.rs
new file mode 100644
index 0000000..bb5852f
--- /dev/null
+++ b/crates/heleos-core/src/ingest/fault.rs
@@ -0,0 +1,27 @@
+use serde::{Deserialize, Serialize};
+
+use crate::Result;
+
+#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
+#[serde(rename_all = "snake_case")]
+pub enum FaultPoint {
+    AfterVaultPublish,
+    AfterJobStart,
+    AfterCheckpointCommit,
+    BeforeAuthoritativeCommit,
+    AfterAuthoritativeCommit,
+}
+
+pub trait FaultInjector: Send + Sync {
+    fn inject(&self, point: FaultPoint) -> Result<()>;
+}
+
+pub(crate) struct NoFaults;
+
+impl FaultInjector for NoFaults {
+    fn inject(&self, _: FaultPoint) -> Result<()> {
+        Ok(())
+    }
+}
+
+pub(crate) static NO_FAULTS: NoFaults = NoFaults;
diff --git a/crates/heleos-core/src/ingest/inspection.rs b/crates/heleos-core/src/ingest/inspection.rs
new file mode 100644
index 0000000..c0cc009
--- /dev/null
+++ b/crates/heleos-core/src/ingest/inspection.rs
@@ -0,0 +1,1332 @@
+use std::{
+    collections::{BTreeMap, BTreeSet},
+    fmt,
+    marker::PhantomData,
+};
+
+use serde::{
+    Deserialize, Deserializer, Serialize,
+    de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor},
+};
+
+use crate::{
+    DocumentId, EvidenceId, HeleosError, IngestEventId, IngestOutcome, JobId, JobState,
+    PageMetadata, PageTransform, PageUnit, PdfLimits, PdfProbeProvenance, ProjectId, Result,
+    RevisionId, Sha256Digest, SheetId, Vault, canonical_document_ids, page_id,
+};
+
+use super::{
+    IntakeQuarantineReasonV1, IntakeQuarantineV1,
+    audit::JCS_SAFE_INTEGER_MAX,
+    evidence::{
+        EVIDENCE_MANIFEST_MEDIA_TYPE, EvidencePdfLimitsV1, MAX_EVIDENCE_MANIFEST_BYTES,
+        PDF_MEDIA_TYPE, bounded_canonical_json,
+    },
+};
+
+pub const FOUNDATION_INSPECTION_SCHEMA_V1: &str = "heleos.foundation-inspection/v1";
+pub(crate) const MAX_FOUNDATION_INSPECTION_ROWS: usize = 100_000;
+pub(crate) const MAX_FOUNDATION_INSPECTION_BYTES: usize = 16 * 1024 * 1024;
+
+#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
+#[serde(rename_all = "snake_case")]
+pub enum FoundationAdmissionState {
+    Accepted,
+    Quarantined,
+}
+
+#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
+#[serde(rename_all = "snake_case")]
+pub enum FoundationJobTerminalReason {
+    Completed,
+    DeadlineExpired,
+    AttemptLimit,
+    InternalFailure,
+    Cancelled,
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct FoundationCounts {
+    pub content_objects: u64,
+    pub documents: u64,
+    pub revisions: u64,
+    pub project_documents: u64,
+    pub sheets: u64,
+    pub evidence_objects: u64,
+    pub ingest_events: u64,
+    pub jobs: u64,
+    pub audit_events: u64,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawFoundationCounts {
+    content_objects: u64,
+    documents: u64,
+    revisions: u64,
+    project_documents: u64,
+    sheets: u64,
+    evidence_objects: u64,
+    ingest_events: u64,
+    jobs: u64,
+    audit_events: u64,
+}
+
+impl FoundationCounts {
+    pub(crate) fn total_rows(&self) -> Result<u64> {
+        [
+            self.content_objects,
+            self.documents,
+            self.revisions,
+            self.project_documents,
+            self.sheets,
+            self.evidence_objects,
+            self.ingest_events,
+            self.jobs,
+            self.audit_events,
+        ]
+        .into_iter()
+        .try_fold(0_u64, |total, count| {
+            if count > JCS_SAFE_INTEGER_MAX {
+                return Err(HeleosError::Integrity);
+            }
+            total.checked_add(count).ok_or(HeleosError::Integrity)
+        })
+    }
+
+    pub(crate) fn validate(&self) -> Result<()> {
+        if self.total_rows()?
+            > u64::try_from(MAX_FOUNDATION_INSPECTION_ROWS).map_err(|_| HeleosError::Integrity)?
+        {
+            return Err(HeleosError::ResourceLimit);
+        }
+        Ok(())
+    }
+}
+
+impl TryFrom<RawFoundationCounts> for FoundationCounts {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawFoundationCounts) -> Result<Self> {
+        let value = Self {
+            content_objects: raw.content_objects,
+            documents: raw.documents,
+            revisions: raw.revisions,
+            project_documents: raw.project_documents,
+            sheets: raw.sheets,
+            evidence_objects: raw.evidence_objects,
+            ingest_events: raw.ingest_events,
+            jobs: raw.jobs,
+            audit_events: raw.audit_events,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for FoundationCounts {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawFoundationCounts::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct FoundationContentObject {
+    pub sha256: Sha256Digest,
+    pub byte_length: u64,
+    pub media_type: String,
+    pub admission_state: FoundationAdmissionState,
+    pub vault_key: String,
+    pub quarantine: Option<IntakeQuarantineV1>,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawFoundationContentObject {
+    sha256: Sha256Digest,
+    byte_length: u64,
+    #[serde(deserialize_with = "deserialize_text_256")]
+    media_type: String,
+    admission_state: FoundationAdmissionState,
+    #[serde(deserialize_with = "deserialize_text_256")]
+    vault_key: String,
+    quarantine: Option<IntakeQuarantineV1>,
+}
+
+impl FoundationContentObject {
+    pub(crate) fn validate(&self) -> Result<()> {
+        let byte_limit = match self.media_type.as_str() {
+            PDF_MEDIA_TYPE => PdfLimits::default().max_input_bytes,
+            EVIDENCE_MANIFEST_MEDIA_TYPE => {
+                u64::try_from(MAX_EVIDENCE_MANIFEST_BYTES).map_err(|_| HeleosError::Integrity)?
+            }
+            _ => return Err(HeleosError::Integrity),
+        };
+        let shape = match self.admission_state {
+            FoundationAdmissionState::Accepted => self.quarantine.is_none(),
+            FoundationAdmissionState::Quarantined => match &self.quarantine {
+                Some(quarantine) if self.media_type == PDF_MEDIA_TYPE => {
+                    quarantine.validate()?;
+                    matches!(
+                        &quarantine.reason,
+                        IntakeQuarantineReasonV1::Pdf(_)
+                            | IntakeQuarantineReasonV1::EvidenceManifestQuota
+                    )
+                }
+                Some(_) | None => false,
+            },
+        };
+        if self.byte_length > JCS_SAFE_INTEGER_MAX
+            || self.byte_length > byte_limit
+            || self.vault_key != Vault::object_key(self.sha256)
+            || self.vault_key.len() > 256
+            || !shape
+        {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+}
+
+impl TryFrom<RawFoundationContentObject> for FoundationContentObject {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawFoundationContentObject) -> Result<Self> {
+        let value = Self {
+            sha256: raw.sha256,
+            byte_length: raw.byte_length,
+            media_type: raw.media_type,
+            admission_state: raw.admission_state,
+            vault_key: raw.vault_key,
+            quarantine: raw.quarantine,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for FoundationContentObject {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawFoundationContentObject::deserialize(deserializer)?)
+            .map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct FoundationSheet {
+    pub sheet_id: SheetId,
+    pub revision_id: RevisionId,
+    pub index: u32,
+    pub width_micropoints: u64,
+    pub height_micropoints: u64,
+    pub unit: PageUnit,
+    pub rotation_degrees: u16,
+    pub transform: PageTransform,
+    pub parent_content_sha256: Sha256Digest,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawFoundationSheet {
+    sheet_id: SheetId,
+    revision_id: RevisionId,
+    index: u32,
+    width_micropoints: u64,
+    height_micropoints: u64,
+    unit: PageUnit,
+    rotation_degrees: u16,
+    transform: PageTransform,
+    parent_content_sha256: Sha256Digest,
+}
+
+impl FoundationSheet {
+    fn validate(&self) -> Result<()> {
+        let limits = PdfLimits::default();
+        let axis_cap = u64::from(limits.max_page_axis_points)
+            .checked_mul(1_000_000)
+            .ok_or(HeleosError::Integrity)?;
+        let transform = self.transform;
+        if self.index >= limits.max_pages
+            || self.revision_id != RevisionId::from(self.parent_content_sha256)
+            || self.sheet_id != page_id(self.parent_content_sha256, self.index)
+            || self.width_micropoints == 0
+            || self.height_micropoints == 0
+            || self.width_micropoints > axis_cap
+            || self.height_micropoints > axis_cap
+            || self.width_micropoints > JCS_SAFE_INTEGER_MAX
+            || self.height_micropoints > JCS_SAFE_INTEGER_MAX
+            || transform.tx_micropoints.unsigned_abs() > JCS_SAFE_INTEGER_MAX
+            || transform.ty_micropoints.unsigned_abs() > JCS_SAFE_INTEGER_MAX
+            || crate::pdf::geometry::expected_rotation_matrix(self.rotation_degrees)
+                != Some((transform.m11, transform.m12, transform.m21, transform.m22))
+        {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+}
+
+impl TryFrom<RawFoundationSheet> for FoundationSheet {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawFoundationSheet) -> Result<Self> {
+        let value = Self {
+            sheet_id: raw.sheet_id,
+            revision_id: raw.revision_id,
+            index: raw.index,
+            width_micropoints: raw.width_micropoints,
+            height_micropoints: raw.height_micropoints,
+            unit: raw.unit,
+            rotation_degrees: raw.rotation_degrees,
+            transform: raw.transform,
+            parent_content_sha256: raw.parent_content_sha256,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for FoundationSheet {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawFoundationSheet::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct FoundationEvidenceContent {
+    pub sha256: Sha256Digest,
+    pub byte_length: u64,
+    pub vault_key: String,
+    pub media_type: String,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawFoundationEvidenceContent {
+    sha256: Sha256Digest,
+    byte_length: u64,
+    #[serde(deserialize_with = "deserialize_text_256")]
+    vault_key: String,
+    #[serde(deserialize_with = "deserialize_text_256")]
+    media_type: String,
+}
+
+impl FoundationEvidenceContent {
+    fn validate(&self) -> Result<()> {
+        let maximum = match self.media_type.as_str() {
+            PDF_MEDIA_TYPE => PdfLimits::default().max_input_bytes,
+            EVIDENCE_MANIFEST_MEDIA_TYPE => {
+                u64::try_from(MAX_EVIDENCE_MANIFEST_BYTES).map_err(|_| HeleosError::Integrity)?
+            }
+            _ => return Err(HeleosError::Integrity),
+        };
+        if self.byte_length > maximum {
+            return Err(HeleosError::ResourceLimit);
+        }
+        self.validate_media(&self.media_type)
+    }
+
+    fn validate_media(&self, expected_media_type: &str) -> Result<()> {
+        if self.byte_length > JCS_SAFE_INTEGER_MAX
+            || self.vault_key != Vault::object_key(self.sha256)
+            || self.vault_key.len() > 256
+            || self.media_type != expected_media_type
+        {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+}
+
+impl TryFrom<RawFoundationEvidenceContent> for FoundationEvidenceContent {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawFoundationEvidenceContent) -> Result<Self> {
+        let value = Self {
+            sha256: raw.sha256,
+            byte_length: raw.byte_length,
+            vault_key: raw.vault_key,
+            media_type: raw.media_type,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for FoundationEvidenceContent {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawFoundationEvidenceContent::deserialize(deserializer)?)
+            .map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct FoundationEvidenceLineage {
+    pub evidence_id: EvidenceId,
+    pub originating_job_id: JobId,
+    pub document_id: DocumentId,
+    pub revision_id: RevisionId,
+    pub original: FoundationEvidenceContent,
+    pub manifest: FoundationEvidenceContent,
+    pub extraction_method: String,
+    pub requested_limits: EvidencePdfLimitsV1,
+    pub probe_provenance: PdfProbeProvenance,
+    pub review_state: String,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawFoundationEvidenceLineage {
+    evidence_id: EvidenceId,
+    originating_job_id: JobId,
+    document_id: DocumentId,
+    revision_id: RevisionId,
+    original: FoundationEvidenceContent,
+    manifest: FoundationEvidenceContent,
+    #[serde(deserialize_with = "deserialize_text_256")]
+    extraction_method: String,
+    requested_limits: EvidencePdfLimitsV1,
+    probe_provenance: PdfProbeProvenance,
+    #[serde(deserialize_with = "deserialize_text_16")]
+    review_state: String,
+}
+
+impl FoundationEvidenceLineage {
+    pub(crate) fn validate(&self) -> Result<()> {
+        self.original.validate_media(PDF_MEDIA_TYPE)?;
+        self.manifest.validate_media(EVIDENCE_MANIFEST_MEDIA_TYPE)?;
+        let (document_id, revision_id) = canonical_document_ids(self.original.sha256);
+        if self.document_id != document_id
+            || self.revision_id != revision_id
+            || self.extraction_method != "heleos.pdf-probe/v1"
+            || self.review_state != "accepted"
+            || self.requested_limits != EvidencePdfLimitsV1::from(PdfLimits::default())
+        {
+            return Err(HeleosError::Integrity);
+        }
+        self.probe_provenance.validate()
+    }
+}
+
+impl TryFrom<RawFoundationEvidenceLineage> for FoundationEvidenceLineage {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawFoundationEvidenceLineage) -> Result<Self> {
+        let value = Self {
+            evidence_id: raw.evidence_id,
+            originating_job_id: raw.originating_job_id,
+            document_id: raw.document_id,
+            revision_id: raw.revision_id,
+            original: raw.original,
+            manifest: raw.manifest,
+            extraction_method: raw.extraction_method,
+            requested_limits: raw.requested_limits,
+            probe_provenance: raw.probe_provenance,
+            review_state: raw.review_state,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for FoundationEvidenceLineage {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawFoundationEvidenceLineage::deserialize(deserializer)?)
+            .map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct FoundationIntakeEvent {
+    pub ingest_event_id: IngestEventId,
+    pub job_id: Option<JobId>,
+    pub content_sha256: Option<Sha256Digest>,
+    pub outcome: IngestOutcome,
+    pub attempt: Option<u32>,
+    pub terminal_at_ms: i64,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawFoundationIntakeEvent {
+    ingest_event_id: IngestEventId,
+    job_id: Option<JobId>,
+    content_sha256: Option<Sha256Digest>,
+    outcome: IngestOutcome,
+    attempt: Option<u32>,
+    terminal_at_ms: i64,
+}
+
+impl FoundationIntakeEvent {
+    pub(crate) fn validate(&self) -> Result<()> {
+        let authoritative = !matches!(
+            self.outcome,
+            IngestOutcome::IdempotentReplay | IngestOutcome::DeniedConflict
+        );
+        if self.terminal_at_ms < 0
+            || u64::try_from(self.terminal_at_ms).map_or(true, |value| value > JCS_SAFE_INTEGER_MAX)
+            || (authoritative && (self.job_id.is_none() || !matches!(self.attempt, Some(1..=16))))
+            || (!authoritative && self.attempt.is_some())
+        {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+}
+
+impl TryFrom<RawFoundationIntakeEvent> for FoundationIntakeEvent {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawFoundationIntakeEvent) -> Result<Self> {
+        let value = Self {
+            ingest_event_id: raw.ingest_event_id,
+            job_id: raw.job_id,
+            content_sha256: raw.content_sha256,
+            outcome: raw.outcome,
+            attempt: raw.attempt,
+            terminal_at_ms: raw.terminal_at_ms,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for FoundationIntakeEvent {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawFoundationIntakeEvent::deserialize(deserializer)?)
+            .map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct FoundationJob {
+    pub job_id: JobId,
+    pub kind: String,
+    pub state: JobState,
+    pub attempt: u32,
+    pub created_at_ms: i64,
+    pub updated_at_ms: i64,
+    pub terminal_reason: Option<FoundationJobTerminalReason>,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawFoundationJob {
+    job_id: JobId,
+    #[serde(deserialize_with = "deserialize_text_16")]
+    kind: String,
+    state: JobState,
+    attempt: u32,
+    created_at_ms: i64,
+    updated_at_ms: i64,
+    terminal_reason: Option<FoundationJobTerminalReason>,
+}
+
+impl FoundationJob {
+    pub(crate) fn validate(&self) -> Result<()> {
+        let state_shape = match self.state {
+            JobState::Queued => self.attempt == 0 && self.terminal_reason.is_none(),
+            JobState::Running => (1..=16).contains(&self.attempt) && self.terminal_reason.is_none(),
+            JobState::Interrupted => {
+                (1..16).contains(&self.attempt) && self.terminal_reason.is_none()
+            }
+            JobState::Succeeded => {
+                (1..=16).contains(&self.attempt)
+                    && self.terminal_reason == Some(FoundationJobTerminalReason::Completed)
+            }
+            JobState::Failed => {
+                (1..=16).contains(&self.attempt)
+                    && matches!(
+                        self.terminal_reason,
+                        Some(
+                            FoundationJobTerminalReason::DeadlineExpired
+                                | FoundationJobTerminalReason::AttemptLimit
+                                | FoundationJobTerminalReason::InternalFailure
+                        )
+                    )
+            }
+            JobState::Cancelled => {
+                self.attempt <= 16
+                    && self.terminal_reason == Some(FoundationJobTerminalReason::Cancelled)
+            }
+        };
+        if self.kind != "pdf_ingest"
+            || self.created_at_ms < 0
+            || self.updated_at_ms < self.created_at_ms
+            || u64::try_from(self.updated_at_ms).map_or(true, |value| value > JCS_SAFE_INTEGER_MAX)
+            || !state_shape
+        {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+}
+
+impl TryFrom<RawFoundationJob> for FoundationJob {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawFoundationJob) -> Result<Self> {
+        let value = Self {
+            job_id: raw.job_id,
+            kind: raw.kind,
+            state: raw.state,
+            attempt: raw.attempt,
+            created_at_ms: raw.created_at_ms,
+            updated_at_ms: raw.updated_at_ms,
+            terminal_reason: raw.terminal_reason,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for FoundationJob {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawFoundationJob::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct FoundationInspection {
+    pub schema: String,
+    pub project_id: ProjectId,
+    pub counts: FoundationCounts,
+    pub content_objects: Vec<FoundationContentObject>,
+    pub document_ids: Vec<DocumentId>,
+    pub revision_ids: Vec<RevisionId>,
+    pub sheets: Vec<FoundationSheet>,
+    pub evidence: Vec<FoundationEvidenceLineage>,
+    pub intake_events: Vec<FoundationIntakeEvent>,
+    pub jobs: Vec<FoundationJob>,
+}
+
+pub(crate) struct FoundationInspectionParts {
+    pub counts: FoundationCounts,
+    pub content_objects: Vec<FoundationContentObject>,
+    pub document_ids: Vec<DocumentId>,
+    pub revision_ids: Vec<RevisionId>,
+    pub sheets: Vec<FoundationSheet>,
+    pub evidence: Vec<FoundationEvidenceLineage>,
+    pub intake_events: Vec<FoundationIntakeEvent>,
+    pub jobs: Vec<FoundationJob>,
+}
+
+impl FoundationInspection {
+    pub(crate) fn new(project_id: ProjectId, parts: FoundationInspectionParts) -> Result<Self> {
+        let value = Self {
+            schema: FOUNDATION_INSPECTION_SCHEMA_V1.to_owned(),
+            project_id,
+            counts: parts.counts,
+            content_objects: parts.content_objects,
+            document_ids: parts.document_ids,
+            revision_ids: parts.revision_ids,
+            sheets: parts.sheets,
+            evidence: parts.evidence,
+            intake_events: parts.intake_events,
+            jobs: parts.jobs,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+
+    pub(crate) fn validate(&self) -> Result<()> {
+        self.counts.validate()?;
+        if self.schema != FOUNDATION_INSPECTION_SCHEMA_V1
+            || usize_count(self.counts.content_objects)? != self.content_objects.len()
+            || usize_count(self.counts.documents)? != self.document_ids.len()
+            || usize_count(self.counts.revisions)? != self.revision_ids.len()
+            || usize_count(self.counts.project_documents)? != self.document_ids.len()
+            || usize_count(self.counts.sheets)? != self.sheets.len()
+            || usize_count(self.counts.evidence_objects)? != self.evidence.len()
+            || usize_count(self.counts.ingest_events)? != self.intake_events.len()
+            || usize_count(self.counts.jobs)? != self.jobs.len()
+        {
+            return Err(HeleosError::Integrity);
+        }
+
+        let nested_rows = [
+            self.content_objects.len(),
+            self.document_ids.len(),
+            self.revision_ids.len(),
+            self.sheets.len(),
+            self.evidence.len(),
+            self.intake_events.len(),
+            self.jobs.len(),
+        ]
+        .into_iter()
+        .try_fold(0_usize, |total, count| {
+            total.checked_add(count).ok_or(HeleosError::Integrity)
+        })?;
+        if nested_rows > MAX_FOUNDATION_INSPECTION_ROWS {
+            return Err(HeleosError::ResourceLimit);
+        }
+
+        ensure_strictly_sorted_by(&self.content_objects, |value| value.sha256)?;
+        ensure_strictly_sorted_by(&self.document_ids, |value| *value.as_digest())?;
+        ensure_strictly_sorted_by(&self.revision_ids, |value| *value.as_digest())?;
+        ensure_strictly_sorted_by(&self.sheets, |value| {
+            (
+                *value.revision_id.as_digest(),
+                value.index,
+                *value.sheet_id.as_digest(),
+            )
+        })?;
+        ensure_strictly_sorted_by(&self.evidence, |value| {
+            (
+                *value.revision_id.as_digest(),
+                value.manifest.sha256,
+                *value.evidence_id.as_uuid().as_bytes(),
+            )
+        })?;
+        ensure_strictly_sorted_by(&self.intake_events, |value| {
+            (
+                value.terminal_at_ms,
+                *value.ingest_event_id.as_uuid().as_bytes(),
+            )
+        })?;
+        ensure_strictly_sorted_by(&self.jobs, |value| {
+            (value.created_at_ms, *value.job_id.as_uuid().as_bytes())
+        })?;
+
+        for content in &self.content_objects {
+            content.validate()?;
+        }
+        for evidence in &self.evidence {
+            evidence.validate()?;
+        }
+        for event in &self.intake_events {
+            event.validate()?;
+        }
+        for job in &self.jobs {
+            job.validate()?;
+        }
+
+        let documents = self
+            .document_ids
+            .iter()
+            .map(|value| *value.as_digest())
+            .collect::<Vec<_>>();
+        let revisions = self
+            .revision_ids
+            .iter()
+            .map(|value| *value.as_digest())
+            .collect::<Vec<_>>();
+        if documents != revisions {
+            return Err(HeleosError::Integrity);
+        }
+
+        let content_by_digest = self
+            .content_objects
+            .iter()
+            .map(|content| (content.sha256, content))
+            .collect::<BTreeMap<_, _>>();
+        let mut required_accepted = BTreeMap::new();
+        for revision in &self.revision_ids {
+            insert_required_media(
+                &mut required_accepted,
+                *revision.as_digest(),
+                PDF_MEDIA_TYPE,
+            )?;
+        }
+        let mut evidence_revisions = BTreeSet::new();
+        for evidence in &self.evidence {
+            if self
+                .document_ids
+                .binary_search_by(|value| value.as_digest().cmp(evidence.document_id.as_digest()))
+                .is_err()
+                || self
+                    .revision_ids
+                    .binary_search_by(|value| {
+                        value.as_digest().cmp(evidence.revision_id.as_digest())
+                    })
+                    .is_err()
+            {
+                return Err(HeleosError::Integrity);
+            }
+            if !evidence_revisions.insert(*evidence.revision_id.as_digest()) {
+                return Err(HeleosError::Integrity);
+            }
+            insert_required_media(
+                &mut required_accepted,
+                evidence.original.sha256,
+                PDF_MEDIA_TYPE,
+            )?;
+            insert_required_media(
+                &mut required_accepted,
+                evidence.manifest.sha256,
+                EVIDENCE_MANIFEST_MEDIA_TYPE,
+            )?;
+            let original = content_by_digest
+                .get(&evidence.original.sha256)
+                .ok_or(HeleosError::Integrity)?;
+            let manifest = content_by_digest
+                .get(&evidence.manifest.sha256)
+                .ok_or(HeleosError::Integrity)?;
+            if original.byte_length != evidence.original.byte_length
+                || original.vault_key != evidence.original.vault_key
+                || original.media_type != evidence.original.media_type
+                || manifest.byte_length != evidence.manifest.byte_length
+                || manifest.vault_key != evidence.manifest.vault_key
+                || manifest.media_type != evidence.manifest.media_type
+            {
+                return Err(HeleosError::Integrity);
+            }
+        }
+        let revision_digests = self
+            .revision_ids
+            .iter()
+            .map(|revision| *revision.as_digest())
+            .collect::<BTreeSet<_>>();
+        if evidence_revisions != revision_digests {
+            return Err(HeleosError::Integrity);
+        }
+
+        let mut grouped_pages: BTreeMap<(Sha256Digest, Sha256Digest), Vec<PageMetadata>> =
+            BTreeMap::new();
+        for sheet in &self.sheets {
+            if sheet.parent_content_sha256 != *sheet.revision_id.as_digest()
+                || self
+                    .revision_ids
+                    .binary_search_by(|value| value.as_digest().cmp(sheet.revision_id.as_digest()))
+                    .is_err()
+            {
+                return Err(HeleosError::Integrity);
+            }
+            grouped_pages
+                .entry((*sheet.revision_id.as_digest(), sheet.parent_content_sha256))
+                .or_default()
+                .push(PageMetadata {
+                    index: sheet.index,
+                    page_id: sheet.sheet_id,
+                    width_micropoints: sheet.width_micropoints,
+                    height_micropoints: sheet.height_micropoints,
+                    unit: sheet.unit,
+                    rotation_degrees: sheet.rotation_degrees,
+                    transform: sheet.transform,
+                });
+        }
+        if grouped_pages
+            .keys()
+            .map(|(revision, _)| *revision)
+            .collect::<BTreeSet<_>>()
+            != revision_digests
+        {
+            return Err(HeleosError::Integrity);
+        }
+        for ((_, parent), pages) in grouped_pages {
+            crate::pdf::geometry::validate_page_metadata(&pages, parent, PdfLimits::default())?;
+        }
+
+        let required_manifest_digests = self
+            .evidence
+            .iter()
+            .map(|evidence| evidence.manifest.sha256)
+            .collect::<BTreeSet<_>>();
+        for (digest, expected_media_type) in required_accepted {
+            let content = content_by_digest
+                .get(&digest)
+                .ok_or(HeleosError::Integrity)?;
+            if content.admission_state != FoundationAdmissionState::Accepted
+                || content.media_type != expected_media_type
+            {
+                return Err(HeleosError::Integrity);
+            }
+        }
+        for content in &self.content_objects {
+            if content.admission_state == FoundationAdmissionState::Accepted
+                && content.media_type == PDF_MEDIA_TYPE
+                && self
+                    .revision_ids
+                    .binary_search_by_key(&content.sha256, |value| *value.as_digest())
+                    .is_err()
+            {
+                return Err(HeleosError::Integrity);
+            }
+            if content.admission_state == FoundationAdmissionState::Accepted
+                && content.media_type == EVIDENCE_MANIFEST_MEDIA_TYPE
+                && !required_manifest_digests.contains(&content.sha256)
+            {
+                return Err(HeleosError::Integrity);
+            }
+        }
+
+        let jobs = self
+            .jobs
+            .iter()
+            .map(|job| *job.job_id.as_uuid().as_bytes())
+            .collect::<BTreeSet<_>>();
+        for event in &self.intake_events {
+            if let Some(job_id) = event.job_id
+                && !jobs.contains(job_id.as_uuid().as_bytes())
+            {
+                return Err(HeleosError::Integrity);
+            }
+            if let Some(content_sha256) = event.content_sha256
+                && !content_by_digest.contains_key(&content_sha256)
+            {
+                return Err(HeleosError::Integrity);
+            }
+        }
+        for evidence in &self.evidence {
+            if !jobs.contains(evidence.originating_job_id.as_uuid().as_bytes()) {
+                return Err(HeleosError::Integrity);
+            }
+        }
+        let event_content = self
+            .intake_events
+            .iter()
+            .filter_map(|event| event.content_sha256)
+            .collect::<BTreeSet<_>>();
+        for content in &self.content_objects {
+            if content.admission_state == FoundationAdmissionState::Quarantined
+                && !event_content.contains(&content.sha256)
+            {
+                return Err(HeleosError::Integrity);
+            }
+        }
+
+        bounded_canonical_json(self, MAX_FOUNDATION_INSPECTION_BYTES)?;
+        Ok(())
+    }
+}
+
+impl<'de> Deserialize<'de> for FoundationInspection {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        deserializer.deserialize_map(FoundationInspectionVisitor)
+    }
+}
+
+#[derive(Deserialize)]
+#[serde(field_identifier, rename_all = "snake_case")]
+enum FoundationInspectionField {
+    Schema,
+    ProjectId,
+    Counts,
+    ContentObjects,
+    DocumentIds,
+    RevisionIds,
+    Sheets,
+    Evidence,
+    IntakeEvents,
+    Jobs,
+}
+
+struct FoundationInspectionVisitor;
+
+impl<'de> Visitor<'de> for FoundationInspectionVisitor {
+    type Value = FoundationInspection;
+
+    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
+        formatter.write_str("a strict heleos.foundation-inspection/v1 object")
+    }
+
+    fn visit_map<A: MapAccess<'de>>(
+        self,
+        mut map: A,
+    ) -> std::result::Result<Self::Value, A::Error> {
+        let mut remaining = MAX_FOUNDATION_INSPECTION_ROWS;
+        let mut schema = None;
+        let mut project_id = None;
+        let mut counts = None;
+        let mut content_objects = None;
+        let mut document_ids = None;
+        let mut revision_ids = None;
+        let mut sheets = None;
+        let mut evidence = None;
+        let mut intake_events = None;
+        let mut jobs = None;
+
+        while let Some(field) = map.next_key::<FoundationInspectionField>()? {
+            match field {
+                FoundationInspectionField::Schema => {
+                    reject_duplicate(&schema, "schema")?;
+                    schema = Some(map.next_value_seed(BoundedStringSeed::<256>)?);
+                }
+                FoundationInspectionField::ProjectId => {
+                    reject_duplicate(&project_id, "project_id")?;
+                    project_id = Some(map.next_value()?);
+                }
+                FoundationInspectionField::Counts => {
+                    reject_duplicate(&counts, "counts")?;
+                    counts = Some(map.next_value()?);
+                }
+                FoundationInspectionField::ContentObjects => {
+                    reject_duplicate(&content_objects, "content_objects")?;
+                    content_objects =
+                        Some(map.next_value_seed(
+                            BoundedVecSeed::<FoundationContentObject>::new(&mut remaining),
+                        )?);
+                }
+                FoundationInspectionField::DocumentIds => {
+                    reject_duplicate(&document_ids, "document_ids")?;
+                    document_ids = Some(
+                        map.next_value_seed(BoundedVecSeed::<DocumentId>::new(&mut remaining))?,
+                    );
+                }
+                FoundationInspectionField::RevisionIds => {
+                    reject_duplicate(&revision_ids, "revision_ids")?;
+                    revision_ids = Some(
+                        map.next_value_seed(BoundedVecSeed::<RevisionId>::new(&mut remaining))?,
+                    );
+                }
+                FoundationInspectionField::Sheets => {
+                    reject_duplicate(&sheets, "sheets")?;
+                    sheets =
+                        Some(map.next_value_seed(BoundedVecSeed::<FoundationSheet>::new(
+                            &mut remaining,
+                        ))?);
+                }
+                FoundationInspectionField::Evidence => {
+                    reject_duplicate(&evidence, "evidence")?;
+                    evidence =
+                        Some(map.next_value_seed(
+                            BoundedVecSeed::<FoundationEvidenceLineage>::new(&mut remaining),
+                        )?);
+                }
+                FoundationInspectionField::IntakeEvents => {
+                    reject_duplicate(&intake_events, "intake_events")?;
+                    intake_events = Some(map.next_value_seed(BoundedVecSeed::<
+                        FoundationIntakeEvent,
+                    >::new(
+                        &mut remaining
+                    ))?);
+                }
+                FoundationInspectionField::Jobs => {
+                    reject_duplicate(&jobs, "jobs")?;
+                    jobs = Some(
+                        map.next_value_seed(BoundedVecSeed::<FoundationJob>::new(&mut remaining))?,
+                    );
+                }
+            }
+        }
+
+        let value = FoundationInspection {
+            schema: schema.ok_or_else(|| de::Error::missing_field("schema"))?,
+            project_id: project_id.ok_or_else(|| de::Error::missing_field("project_id"))?,
+            counts: counts.ok_or_else(|| de::Error::missing_field("counts"))?,
+            content_objects: content_objects
+                .ok_or_else(|| de::Error::missing_field("content_objects"))?,
+            document_ids: document_ids.ok_or_else(|| de::Error::missing_field("document_ids"))?,
+            revision_ids: revision_ids.ok_or_else(|| de::Error::missing_field("revision_ids"))?,
+            sheets: sheets.ok_or_else(|| de::Error::missing_field("sheets"))?,
+            evidence: evidence.ok_or_else(|| de::Error::missing_field("evidence"))?,
+            intake_events: intake_events
+                .ok_or_else(|| de::Error::missing_field("intake_events"))?,
+            jobs: jobs.ok_or_else(|| de::Error::missing_field("jobs"))?,
+        };
+        value.validate().map_err(de::Error::custom)?;
+        Ok(value)
+    }
+}
+
+struct BoundedVecSeed<'a, T> {
+    remaining: &'a mut usize,
+    marker: PhantomData<T>,
+}
+
+impl<'a, T> BoundedVecSeed<'a, T> {
+    const fn new(remaining: &'a mut usize) -> Self {
+        Self {
+            remaining,
+            marker: PhantomData,
+        }
+    }
+}
+
+impl<'de, T: Deserialize<'de>> DeserializeSeed<'de> for BoundedVecSeed<'_, T> {
+    type Value = Vec<T>;
+
+    fn deserialize<D: Deserializer<'de>>(
+        self,
+        deserializer: D,
+    ) -> std::result::Result<Self::Value, D::Error> {
+        deserializer.deserialize_seq(BoundedVecVisitor::<T> {
+            remaining: self.remaining,
+            marker: PhantomData,
+        })
+    }
+}
+
+struct BoundedVecVisitor<'a, T> {
+    remaining: &'a mut usize,
+    marker: PhantomData<T>,
+}
+
+impl<'de, T: Deserialize<'de>> Visitor<'de> for BoundedVecVisitor<'_, T> {
+    type Value = Vec<T>;
+
+    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
+        formatter.write_str("an inspection sequence within the shared 100000-row cap")
+    }
+
+    fn visit_seq<A: SeqAccess<'de>>(
+        self,
+        mut sequence: A,
+    ) -> std::result::Result<Self::Value, A::Error> {
+        if sequence
+            .size_hint()
+            .is_some_and(|hint| hint > *self.remaining)
+        {
+            return Err(de::Error::custom(
+                "inspection rows exceed maximum of 100000",
+            ));
+        }
+        let maximum = *self.remaining;
+        let mut values = Vec::with_capacity(sequence.size_hint().unwrap_or(0).min(maximum));
+        while values.len() < maximum {
+            match sequence.next_element()? {
+                Some(value) => {
+                    values.push(value);
+                    *self.remaining -= 1;
+                }
+                None => return Ok(values),
+            }
+        }
+        if sequence.next_element::<de::IgnoredAny>()?.is_some() {
+            return Err(de::Error::custom(
+                "inspection rows exceed maximum of 100000",
+            ));
+        }
+        Ok(values)
+    }
+}
+
+struct BoundedStringSeed<const MAXIMUM: usize>;
+
+impl<'de, const MAXIMUM: usize> DeserializeSeed<'de> for BoundedStringSeed<MAXIMUM> {
+    type Value = String;
+
+    fn deserialize<D: Deserializer<'de>>(
+        self,
+        deserializer: D,
+    ) -> std::result::Result<Self::Value, D::Error> {
+        super::deserialize_bounded_string::<D, MAXIMUM>(deserializer)
+    }
+}
+
+fn reject_duplicate<E: de::Error, T>(
+    value: &Option<T>,
+    field: &'static str,
+) -> std::result::Result<(), E> {
+    if value.is_some() {
+        return Err(E::duplicate_field(field));
+    }
+    Ok(())
+}
+
+fn ensure_strictly_sorted_by<T, K: Ord>(values: &[T], mut key: impl FnMut(&T) -> K) -> Result<()> {
+    let mut previous = None;
+    for value in values {
+        let current = key(value);
+        if previous
+            .as_ref()
+            .is_some_and(|previous| previous >= &current)
+        {
+            return Err(HeleosError::Integrity);
+        }
+        previous = Some(current);
+    }
+    Ok(())
+}
+
+fn insert_required_media<'a>(
+    required: &mut BTreeMap<Sha256Digest, &'a str>,
+    digest: Sha256Digest,
+    media_type: &'a str,
+) -> Result<()> {
+    if required
+        .insert(digest, media_type)
+        .is_some_and(|previous| previous != media_type)
+    {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+fn usize_count(value: u64) -> Result<usize> {
+    usize::try_from(value).map_err(|_| HeleosError::Integrity)
+}
+
+fn deserialize_text_16<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<String, D::Error> {
+    super::deserialize_bounded_string::<D, 16>(deserializer)
+}
+
+fn deserialize_text_256<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<String, D::Error> {
+    super::deserialize_bounded_string::<D, 256>(deserializer)
+}
+
+#[cfg(test)]
+mod tests {
+    use super::*;
+    use crate::{INTAKE_QUARANTINE_SCHEMA_V1, IntakeQuarantineReasonV1, PdfQuarantineReason};
+
+    fn provenance() -> PdfProbeProvenance {
+        PdfProbeProvenance {
+            parser_name: "fixture-parser".to_owned(),
+            parser_version: "1.0.0".to_owned(),
+            guest_wasm_sha256: Sha256Digest::from_bytes([2; 32]),
+            guest_source_tree_sha256: Sha256Digest::from_bytes([3; 32]),
+            guest_dependency_graph_sha256: Sha256Digest::from_bytes([4; 32]),
+            protocol_version: "heleos.pdf-probe/v1".to_owned(),
+        }
+    }
+
+    fn quarantine(reason: IntakeQuarantineReasonV1) -> IntakeQuarantineV1 {
+        let probe_provenance = match reason {
+            IntakeQuarantineReasonV1::Pdf(_) | IntakeQuarantineReasonV1::EvidenceManifestQuota => {
+                Some(provenance())
+            }
+            IntakeQuarantineReasonV1::InputBytes { .. }
+            | IntakeQuarantineReasonV1::OriginalRetentionQuota
+            | IntakeQuarantineReasonV1::ProjectAssociationQuota => None,
+        };
+        IntakeQuarantineV1 {
+            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+            reason,
+            probe_provenance,
+        }
+    }
+
+    fn null_array(count: usize) -> String {
+        let mut json = String::with_capacity(count.saturating_mul(5).saturating_add(2));
+        json.push('[');
+        for index in 0..count {
+            if index != 0 {
+                json.push(',');
+            }
+            json.push_str("null");
+        }
+        json.push(']');
+        json
+    }
+
+    #[test]
+    fn inspection_deserializer_enforces_one_shared_exact_row_cap() {
+        // Break caught: each inspection vector receiving an independent 100,000-row budget.
+        let mut remaining = MAX_FOUNDATION_INSPECTION_ROWS;
+        let first = null_array(50_000);
+        let second = null_array(50_000);
+        let mut deserializer = serde_json::Deserializer::from_str(&first);
+        let values = BoundedVecSeed::<serde_json::Value>::new(&mut remaining)
+            .deserialize(&mut deserializer)
+            .expect("first half of exact cap");
+        assert_eq!(values.len(), 50_000);
+        let mut deserializer = serde_json::Deserializer::from_str(&second);
+        let values = BoundedVecSeed::<serde_json::Value>::new(&mut remaining)
+            .deserialize(&mut deserializer)
+            .expect("second half of exact cap");
+        assert_eq!(values.len(), 50_000);
+        assert_eq!(remaining, 0);
+
+        let sentinel = null_array(1);
+        let mut deserializer = serde_json::Deserializer::from_str(&sentinel);
+        assert!(
+            BoundedVecSeed::<serde_json::Value>::new(&mut remaining)
+                .deserialize(&mut deserializer)
+                .is_err()
+        );
+    }
+
+    #[test]
+    fn inspection_writer_accepts_exactly_sixteen_mib_and_rejects_the_next_byte() {
+        // Break caught: an off-by-one bounded canonical writer or an untested oversized image.
+        let at_cap = "x".repeat(MAX_FOUNDATION_INSPECTION_BYTES - 2);
+        let bytes = bounded_canonical_json(&at_cap, MAX_FOUNDATION_INSPECTION_BYTES)
+            .expect("exact 16 MiB canonical value");
+        assert_eq!(bytes.len(), MAX_FOUNDATION_INSPECTION_BYTES);
+
+        let over_cap = "x".repeat(MAX_FOUNDATION_INSPECTION_BYTES - 1);
+        assert!(matches!(
+            bounded_canonical_json(&over_cap, MAX_FOUNDATION_INSPECTION_BYTES),
+            Err(HeleosError::ResourceLimit)
+        ));
+    }
+
+    #[test]
+    fn inspection_content_accepts_only_valid_sticky_quarantines() {
+        // Break caught: preflight-only, non-retained quarantine reasons entering inspection.
+        let digest = Sha256Digest::from_bytes([9; 32]);
+        let content = |reason| FoundationContentObject {
+            sha256: digest,
+            byte_length: 7,
+            media_type: PDF_MEDIA_TYPE.to_owned(),
+            admission_state: FoundationAdmissionState::Quarantined,
+            vault_key: Vault::object_key(digest),
+            quarantine: Some(quarantine(reason)),
+        };
+
+        for reason in [
+            IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic),
+            IntakeQuarantineReasonV1::EvidenceManifestQuota,
+        ] {
+            let encoded = serde_json::to_value(content(reason)).expect("sticky quarantine JSON");
+            serde_json::from_value::<FoundationContentObject>(encoded)
+                .expect("valid sticky quarantine");
+        }
+
+        for reason in [
+            IntakeQuarantineReasonV1::InputBytes {
+                limit_bytes: PdfLimits::default().max_input_bytes,
+                observed_bytes: PdfLimits::default().max_input_bytes + 1,
+            },
+            IntakeQuarantineReasonV1::OriginalRetentionQuota,
+            IntakeQuarantineReasonV1::ProjectAssociationQuota,
+        ] {
+            let encoded =
+                serde_json::to_value(content(reason)).expect("non-sticky quarantine JSON");
+            assert!(serde_json::from_value::<FoundationContentObject>(encoded).is_err());
+        }
+    }
+
+    #[test]
+    fn inspection_canonical_writer_rejects_sixteen_mib_plus_one_output() {
+        // Break caught: building an unbounded canonical inspection buffer after row validation.
+        let jobs = (1_u128..=100_000)
+            .map(|value| FoundationJob {
+                job_id: JobId::from_uuid(uuid::Uuid::from_u128(
+                    (4_u128 << 76) | (2_u128 << 62) | value,
+                )),
+                kind: "pdf_ingest".to_owned(),
+                state: JobState::Queued,
+                attempt: 0,
+                created_at_ms: i64::try_from(JCS_SAFE_INTEGER_MAX).expect("JCS time"),
+                updated_at_ms: i64::try_from(JCS_SAFE_INTEGER_MAX).expect("JCS time"),
+                terminal_reason: None,
+            })
+            .collect::<Vec<_>>();
+        let result = FoundationInspection::new(
+            ProjectId::from_uuid(uuid::Uuid::from_u128(
+                (4_u128 << 76) | (2_u128 << 62) | 100_001,
+            )),
+            FoundationInspectionParts {
+                counts: FoundationCounts {
+                    content_objects: 0,
+                    documents: 0,
+                    revisions: 0,
+                    project_documents: 0,
+                    sheets: 0,
+                    evidence_objects: 0,
+                    ingest_events: 0,
+                    jobs: 100_000,
+                    audit_events: 0,
+                },
+                content_objects: Vec::new(),
+                document_ids: Vec::new(),
+                revision_ids: Vec::new(),
+                sheets: Vec::new(),
+                evidence: Vec::new(),
+                intake_events: Vec::new(),
+                jobs,
+            },
+        );
+        assert!(
+            matches!(result, Err(HeleosError::ResourceLimit)),
+            "unexpected result: {:?}",
+            result.as_ref().err()
+        );
+    }
+}
diff --git a/crates/heleos-core/src/ingest/job.rs b/crates/heleos-core/src/ingest/job.rs
new file mode 100644
index 0000000..afb0094
--- /dev/null
+++ b/crates/heleos-core/src/ingest/job.rs
@@ -0,0 +1,4185 @@
+use std::fmt;
+use std::fs::{File, OpenOptions};
+use std::io::{Read, Seek, SeekFrom};
+use std::path::Path;
+
+use cap_fs_ext::MetadataExt as _;
+use serde::{Deserialize, Deserializer, Serialize, de};
+use sha2::{Digest, Sha256};
+
+use crate::{
+    ActorId, DocumentId, EvidenceId, HeleosError, IdempotencyKey, IngestEventId, IngestOutcome,
+    JobId, PdfLimits, PdfProbeProvenance, PdfQuarantineReason, ProjectId, Result, RevisionId,
+    Sha256Digest, SheetId, StoredObject,
+};
+
+use super::audit::JCS_SAFE_INTEGER_MAX;
+use super::evidence::{
+    EvidenceManifestReceipt, EvidenceManifestV1, EvidencePdfLimitsV1, validate_provenance,
+};
+
+pub(crate) const INGEST_KIND: &str = "pdf_ingest";
+pub(crate) const INGEST_INPUT_SCHEMA_V1: &str = "heleos.ingest-input/v1";
+pub(crate) const INGEST_BUDGET_SCHEMA_V1: &str = "heleos.ingest-budget/v1";
+pub(crate) const INGEST_CHECKPOINT_SCHEMA_V1: &str = "heleos.ingest-checkpoint/v1";
+pub(crate) const INGEST_EVENT_DETAILS_SCHEMA_V1: &str = "heleos.ingest-event-details/v1";
+pub(crate) const LEASE_DURATION_MS: i64 = 30_000;
+pub(crate) const JOB_DEADLINE_MS: i64 = 300_000;
+pub(crate) const MAX_JOB_ATTEMPTS: u32 = 16;
+pub(crate) const PROJECT_OVERALL_QUOTA_BYTES: u64 = 50 * 1024 * 1024 * 1024;
+pub(crate) const STORE_OVERALL_QUOTA_BYTES: u64 = 500 * 1024 * 1024 * 1024;
+pub(crate) const PROJECT_EVIDENCE_QUOTA_BYTES: u64 = 5 * 1024 * 1024 * 1024;
+pub(crate) const STORE_EVIDENCE_QUOTA_BYTES: u64 = 50 * 1024 * 1024 * 1024;
+pub(crate) const PROJECT_QUARANTINE_QUOTA_BYTES: u64 = 2 * 1024 * 1024 * 1024;
+pub(crate) const STORE_QUARANTINE_QUOTA_BYTES: u64 = 8 * 1024 * 1024 * 1024;
+pub(crate) const MAX_SOURCE_DISPLAY_BYTES: usize = 4 * 1024;
+
+#[derive(Clone, Copy, Debug, Eq, PartialEq)]
+struct SourceMarker {
+    device: u64,
+    inode: u64,
+    length: u64,
+}
+
+pub struct IntakeSource {
+    file: File,
+    marker: SourceMarker,
+    display_name: String,
+}
+
+impl fmt::Debug for IntakeSource {
+    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
+        formatter
+            .debug_struct("IntakeSource")
+            .field("display_name", &self.display_name)
+            .finish_non_exhaustive()
+    }
+}
+
+impl IntakeSource {
+    pub fn open(path: &Path) -> Result<Self> {
+        let mut options = OpenOptions::new();
+        options.read(true);
+        configure_source_open(&mut options);
+        let file = options.open(path).map_err(map_source_open_error)?;
+        let marker = source_marker(&file)?;
+        let display_name =
+            encode_source_display(path.file_name().ok_or(HeleosError::PolicyDenied)?)?;
+        Ok(Self {
+            file,
+            marker,
+            display_name,
+        })
+    }
+
+    pub(crate) fn fingerprint(&mut self, maximum: u64) -> Result<SourceFingerprint> {
+        if maximum == 0 {
+            return Err(HeleosError::PolicyDenied);
+        }
+        self.recheck()?;
+        self.file
+            .seek(SeekFrom::Start(0))
+            .map_err(HeleosError::Io)?;
+        let wanted = maximum.checked_add(1).ok_or(HeleosError::Integrity)?;
+        let mut remaining = wanted;
+        let mut observed = 0_u64;
+        let mut hasher = Sha256::new();
+        let mut buffer = [0_u8; 64 * 1024];
+        while remaining > 0 {
+            let amount = usize::try_from(remaining.min(buffer.len() as u64))
+                .map_err(|_| HeleosError::Integrity)?;
+            let read = self
+                .file
+                .read(&mut buffer[..amount])
+                .map_err(HeleosError::Io)?;
+            if read == 0 {
+                break;
+            }
+            hasher.update(&buffer[..read]);
+            let read = u64::try_from(read).map_err(|_| HeleosError::Integrity)?;
+            observed = observed.checked_add(read).ok_or(HeleosError::Integrity)?;
+            remaining = remaining.checked_sub(read).ok_or(HeleosError::Integrity)?;
+        }
+        self.recheck()?;
+        if self.marker.length > maximum {
+            if observed != wanted {
+                return Err(HeleosError::Integrity);
+            }
+            self.rewind()?;
+            return Ok(SourceFingerprint {
+                digest: None,
+                byte_length: self.marker.length,
+                display_name: self.display_name.clone(),
+            });
+        }
+        if observed != self.marker.length {
+            return Err(HeleosError::Integrity);
+        }
+        let mut trailing = [0_u8; 1];
+        if self.file.read(&mut trailing).map_err(HeleosError::Io)? != 0 {
+            return Err(HeleosError::Integrity);
+        }
+        let digest = Sha256Digest::from_bytes(hasher.finalize().into());
+        self.rewind()?;
+        Ok(SourceFingerprint {
+            digest: Some(digest),
+            byte_length: observed,
+            display_name: self.display_name.clone(),
+        })
+    }
+
+    pub(crate) fn publish(
+        &mut self,
+        vault: &crate::Vault,
+        budget: crate::VaultWriteBudget,
+        expected: Sha256Digest,
+        expected_length: u64,
+    ) -> Result<crate::PutOutcome> {
+        self.recheck()?;
+        self.rewind()?;
+        let outcome = vault.put_reader(&mut self.file, budget)?;
+        let (digest, byte_length) = match &outcome {
+            crate::PutOutcome::Stored(stored) => (stored.digest, stored.byte_length),
+            crate::PutOutcome::QuotaRejected {
+                digest,
+                byte_length,
+            } => (*digest, *byte_length),
+        };
+        if digest != expected || byte_length != expected_length {
+            return Err(HeleosError::Integrity);
+        }
+        self.recheck()?;
+        let repeated = self.fingerprint(PdfLimits::default().max_input_bytes)?;
+        if repeated.digest != Some(expected) || repeated.byte_length != expected_length {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(outcome)
+    }
+
+    pub(crate) fn verify_unchanged(&mut self, expected: &SourceFingerprint) -> Result<()> {
+        let repeated = self.fingerprint(PdfLimits::default().max_input_bytes)?;
+        if &repeated != expected {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+
+    fn rewind(&mut self) -> Result<()> {
+        self.file
+            .seek(SeekFrom::Start(0))
+            .map(|_| ())
+            .map_err(HeleosError::Io)
+    }
+
+    fn recheck(&self) -> Result<()> {
+        if source_marker(&self.file)? != self.marker {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+}
+
+#[cfg(unix)]
+fn configure_source_open(options: &mut OpenOptions) {
+    use std::os::unix::fs::OpenOptionsExt;
+
+    options.custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK);
+}
+
+#[cfg(windows)]
+fn configure_source_open(options: &mut OpenOptions) {
+    use std::os::windows::fs::OpenOptionsExt;
+
+    const GENERIC_READ: u32 = 0x8000_0000;
+    const READ_CONTROL: u32 = 0x0002_0000;
+    const FILE_SHARE_READ: u32 = 0x0000_0001;
+    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
+    options
+        .access_mode(GENERIC_READ | READ_CONTROL)
+        .share_mode(FILE_SHARE_READ)
+        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
+}
+
+#[cfg(not(any(unix, windows)))]
+fn configure_source_open(_: &mut OpenOptions) {}
+
+fn map_source_open_error(error: std::io::Error) -> HeleosError {
+    #[cfg(unix)]
+    if matches!(error.raw_os_error(), Some(code) if code == libc::ELOOP
+        || code == libc::EOPNOTSUPP
+        || code == libc::ENXIO
+        || code == libc::ENODEV)
+    {
+        return HeleosError::PolicyDenied;
+    }
+    if matches!(
+        error.kind(),
+        std::io::ErrorKind::NotFound | std::io::ErrorKind::PermissionDenied
+    ) {
+        HeleosError::PolicyDenied
+    } else {
+        HeleosError::Io(error)
+    }
+}
+
+fn source_marker(file: &File) -> Result<SourceMarker> {
+    let metadata = file.metadata().map_err(HeleosError::Io)?;
+    if !metadata.is_file() || metadata.file_type().is_symlink() {
+        return Err(HeleosError::PolicyDenied);
+    }
+    #[cfg(windows)]
+    {
+        use std::os::windows::fs::MetadataExt;
+
+        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
+        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
+            return Err(HeleosError::PolicyDenied);
+        }
+    }
+    #[cfg(not(any(unix, windows)))]
+    {
+        return Err(HeleosError::PolicyDenied);
+    }
+    #[cfg(any(unix, windows))]
+    {
+        let retained = cap_std::fs::File::from_std(file.try_clone().map_err(HeleosError::Io)?);
+        let retained_metadata = retained.metadata().map_err(HeleosError::Io)?;
+        Ok(SourceMarker {
+            device: retained_metadata.dev(),
+            inode: retained_metadata.ino(),
+            length: metadata.len(),
+        })
+    }
+}
+
+#[cfg(unix)]
+fn encode_source_display(name: &std::ffi::OsStr) -> Result<String> {
+    use std::os::unix::ffi::OsStrExt;
+
+    match name.to_str() {
+        Some(value) => encode_utf8_display(value.as_bytes()),
+        None => {
+            let bytes = name.as_bytes();
+            let mut value = String::with_capacity(11 + bytes.len().saturating_mul(2));
+            value.push_str("unix-bytes:");
+            for byte in bytes {
+                use std::fmt::Write as _;
+                write!(value, "{byte:02x}").map_err(|_| HeleosError::Integrity)?;
+            }
+            validate_display(value)
+        }
+    }
+}
+
+#[cfg(windows)]
+fn encode_source_display(name: &std::ffi::OsStr) -> Result<String> {
+    use std::os::windows::ffi::OsStrExt;
+
+    let units = name.encode_wide();
+    let (lower, _) = units.size_hint();
+    let mut value = String::with_capacity(14 + lower.saturating_mul(4));
+    value.push_str("windows-utf16:");
+    for unit in units {
+        use std::fmt::Write as _;
+        write!(value, "{unit:04x}").map_err(|_| HeleosError::Integrity)?;
+    }
+    validate_display(value)
+}
+
+#[cfg(not(any(unix, windows)))]
+fn encode_source_display(_: &std::ffi::OsStr) -> Result<String> {
+    Err(HeleosError::PolicyDenied)
+}
+
+fn encode_utf8_display(bytes: &[u8]) -> Result<String> {
+    let mut value = String::with_capacity(5 + bytes.len());
+    value.push_str("utf8:");
+    for byte in bytes {
+        if byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-') {
+            value.push(char::from(*byte));
+        } else {
+            use std::fmt::Write as _;
+            write!(value, "%{byte:02x}").map_err(|_| HeleosError::Integrity)?;
+        }
+    }
+    validate_display(value)
+}
+
+fn validate_display(value: String) -> Result<String> {
+    if value.len() > MAX_SOURCE_DISPLAY_BYTES || value.chars().any(char::is_control) {
+        return Err(HeleosError::ResourceLimit);
+    }
+    Ok(value)
+}
+
+#[derive(Clone, Debug, Eq, PartialEq)]
+pub(crate) struct SourceFingerprint {
+    pub digest: Option<Sha256Digest>,
+    pub byte_length: u64,
+    pub display_name: String,
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+#[serde(
+    tag = "kind",
+    content = "detail",
+    rename_all = "snake_case",
+    deny_unknown_fields
+)]
+pub enum IntakeQuarantineReasonV1 {
+    Pdf(PdfQuarantineReason),
+    InputBytes {
+        limit_bytes: u64,
+        observed_bytes: u64,
+    },
+    OriginalRetentionQuota,
+    ProjectAssociationQuota,
+    EvidenceManifestQuota,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawIntakeQuarantineReasonV1 {
+    kind: RawIntakeQuarantineReasonKind,
+    #[serde(default)]
+    detail: RawIntakeQuarantineReasonDetailField,
+}
+
+#[derive(Deserialize)]
+#[serde(rename_all = "snake_case")]
+enum RawIntakeQuarantineReasonKind {
+    Pdf,
+    InputBytes,
+    OriginalRetentionQuota,
+    ProjectAssociationQuota,
+    EvidenceManifestQuota,
+}
+
+#[derive(Deserialize)]
+#[serde(untagged)]
+enum RawIntakeQuarantineReasonDetail {
+    Pdf(PdfQuarantineReason),
+    InputBytes(RawInputBytesReason),
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawInputBytesReason {
+    limit_bytes: u64,
+    observed_bytes: u64,
+}
+
+#[derive(Default)]
+enum RawIntakeQuarantineReasonDetailField {
+    #[default]
+    Missing,
+    Present(RawIntakeQuarantineReasonDetail),
+}
+
+impl<'de> Deserialize<'de> for RawIntakeQuarantineReasonDetailField {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        RawIntakeQuarantineReasonDetail::deserialize(deserializer).map(Self::Present)
+    }
+}
+
+impl TryFrom<RawIntakeQuarantineReasonV1> for IntakeQuarantineReasonV1 {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawIntakeQuarantineReasonV1) -> Result<Self> {
+        let value = match (raw.kind, raw.detail) {
+            (
+                RawIntakeQuarantineReasonKind::Pdf,
+                RawIntakeQuarantineReasonDetailField::Present(
+                    RawIntakeQuarantineReasonDetail::Pdf(reason),
+                ),
+            ) => Self::Pdf(reason),
+            (
+                RawIntakeQuarantineReasonKind::InputBytes,
+                RawIntakeQuarantineReasonDetailField::Present(
+                    RawIntakeQuarantineReasonDetail::InputBytes(RawInputBytesReason {
+                        limit_bytes,
+                        observed_bytes,
+                    }),
+                ),
+            ) => Self::InputBytes {
+                limit_bytes,
+                observed_bytes,
+            },
+            (
+                RawIntakeQuarantineReasonKind::OriginalRetentionQuota,
+                RawIntakeQuarantineReasonDetailField::Missing,
+            ) => Self::OriginalRetentionQuota,
+            (
+                RawIntakeQuarantineReasonKind::ProjectAssociationQuota,
+                RawIntakeQuarantineReasonDetailField::Missing,
+            ) => Self::ProjectAssociationQuota,
+            (
+                RawIntakeQuarantineReasonKind::EvidenceManifestQuota,
+                RawIntakeQuarantineReasonDetailField::Missing,
+            ) => Self::EvidenceManifestQuota,
+            _ => return Err(HeleosError::Integrity),
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for IntakeQuarantineReasonV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawIntakeQuarantineReasonV1::deserialize(deserializer)?)
+            .map_err(de::Error::custom)
+    }
+}
+
+impl IntakeQuarantineReasonV1 {
+    fn validate(&self) -> Result<()> {
+        match self {
+            Self::InputBytes {
+                limit_bytes,
+                observed_bytes,
+            } if *limit_bytes == PdfLimits::default().max_input_bytes
+                && *observed_bytes > *limit_bytes
+                && *observed_bytes <= JCS_SAFE_INTEGER_MAX => {}
+            Self::Pdf(_)
+            | Self::OriginalRetentionQuota
+            | Self::ProjectAssociationQuota
+            | Self::EvidenceManifestQuota => {}
+            Self::InputBytes { .. } => return Err(HeleosError::Integrity),
+        }
+        Ok(())
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct IntakeQuarantineV1 {
+    pub schema: String,
+    pub reason: IntakeQuarantineReasonV1,
+    pub probe_provenance: Option<PdfProbeProvenance>,
+}
+
+pub const INTAKE_QUARANTINE_SCHEMA_V1: &str = "heleos.intake-quarantine/v1";
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawIntakeQuarantineV1 {
+    schema: String,
+    reason: IntakeQuarantineReasonV1,
+    probe_provenance: Option<PdfProbeProvenance>,
+}
+
+impl TryFrom<RawIntakeQuarantineV1> for IntakeQuarantineV1 {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawIntakeQuarantineV1) -> Result<Self> {
+        let value = Self {
+            schema: raw.schema,
+            reason: raw.reason,
+            probe_provenance: raw.probe_provenance,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for IntakeQuarantineV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawIntakeQuarantineV1::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+impl IntakeQuarantineV1 {
+    pub(crate) fn validate(&self) -> Result<()> {
+        if self.schema != INTAKE_QUARANTINE_SCHEMA_V1 {
+            return Err(HeleosError::Integrity);
+        }
+        self.reason.validate()?;
+        match (&self.reason, &self.probe_provenance) {
+            (IntakeQuarantineReasonV1::Pdf(_), Some(provenance))
+            | (IntakeQuarantineReasonV1::EvidenceManifestQuota, Some(provenance)) => {
+                validate_provenance(provenance)?;
+            }
+            (IntakeQuarantineReasonV1::InputBytes { .. }, None) => {}
+            (IntakeQuarantineReasonV1::OriginalRetentionQuota, None)
+            | (IntakeQuarantineReasonV1::ProjectAssociationQuota, None) => {}
+            _ => return Err(HeleosError::Integrity),
+        }
+        Ok(())
+    }
+}
+
+pub struct IngestRequest {
+    pub project_id: ProjectId,
+    pub source: IntakeSource,
+    pub idempotency_key: IdempotencyKey,
+    pub actor: ActorId,
+    pub pdf_limits: PdfLimits,
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct IngestReceipt {
+    pub ingest_event_id: IngestEventId,
+    pub authoritative_job_id: JobId,
+    pub attempt: u32,
+    pub outcome: IngestOutcome,
+    pub content_sha256: Option<Sha256Digest>,
+    pub byte_length: u64,
+    pub quarantine: Option<IntakeQuarantineV1>,
+    pub document_id: Option<DocumentId>,
+    pub revision_id: Option<RevisionId>,
+    pub sheet_ids: Vec<SheetId>,
+    pub evidence_manifest: Option<EvidenceManifestReceipt>,
+    pub preexisting_vault_digests: Vec<Sha256Digest>,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawIngestReceipt {
+    ingest_event_id: IngestEventId,
+    authoritative_job_id: JobId,
+    attempt: u32,
+    outcome: IngestOutcome,
+    content_sha256: Option<Sha256Digest>,
+    byte_length: u64,
+    quarantine: Option<IntakeQuarantineV1>,
+    document_id: Option<DocumentId>,
+    revision_id: Option<RevisionId>,
+    #[serde(deserialize_with = "deserialize_sheet_ids")]
+    sheet_ids: Vec<SheetId>,
+    evidence_manifest: Option<EvidenceManifestReceipt>,
+    #[serde(deserialize_with = "deserialize_preexisting_digests")]
+    preexisting_vault_digests: Vec<Sha256Digest>,
+}
+
+fn deserialize_sheet_ids<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<Vec<SheetId>, D::Error> {
+    super::deserialize_bounded_vec::<D, SheetId, 10_000>(deserializer)
+}
+
+fn deserialize_preexisting_digests<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<Vec<Sha256Digest>, D::Error> {
+    super::deserialize_bounded_vec::<D, Sha256Digest, 2>(deserializer)
+}
+
+impl TryFrom<RawIngestReceipt> for IngestReceipt {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawIngestReceipt) -> Result<Self> {
+        let value = Self {
+            ingest_event_id: raw.ingest_event_id,
+            authoritative_job_id: raw.authoritative_job_id,
+            attempt: raw.attempt,
+            outcome: raw.outcome,
+            content_sha256: raw.content_sha256,
+            byte_length: raw.byte_length,
+            quarantine: raw.quarantine,
+            document_id: raw.document_id,
+            revision_id: raw.revision_id,
+            sheet_ids: raw.sheet_ids,
+            evidence_manifest: raw.evidence_manifest,
+            preexisting_vault_digests: raw.preexisting_vault_digests,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for IngestReceipt {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawIngestReceipt::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+impl IngestReceipt {
+    pub(crate) fn validate(&self) -> Result<()> {
+        if !(1..=MAX_JOB_ATTEMPTS).contains(&self.attempt)
+            || self.byte_length > JCS_SAFE_INTEGER_MAX
+            || self.preexisting_vault_digests.len() > 2
+            || !is_strictly_sorted(&self.preexisting_vault_digests)
+        {
+            return Err(HeleosError::Integrity);
+        }
+        let accepted_shape = self.content_sha256.is_some()
+            && self.quarantine.is_none()
+            && self.document_id.is_some()
+            && self.revision_id.is_some()
+            && !self.sheet_ids.is_empty()
+            && self.sheet_ids.len() <= 10_000
+            && self.evidence_manifest.is_some();
+        let quarantine_shape = self.quarantine.is_some()
+            && self.document_id.is_none()
+            && self.revision_id.is_none()
+            && self.sheet_ids.is_empty()
+            && self.evidence_manifest.is_none();
+        match self.outcome {
+            IngestOutcome::AcceptedNew | IngestOutcome::AcceptedDuplicate if accepted_shape => {}
+            IngestOutcome::IdempotentReplay if accepted_shape || quarantine_shape => {}
+            IngestOutcome::QuarantinedCorrupt
+            | IngestOutcome::QuarantinedEncrypted
+            | IngestOutcome::QuarantinedUnsupported
+            | IngestOutcome::QuarantinedSuspicious
+            | IngestOutcome::QuarantinedLimit
+                if quarantine_shape => {}
+            _ => return Err(HeleosError::Integrity),
+        }
+        if let Some(quarantine) = &self.quarantine {
+            quarantine.validate()?;
+            let is_input_bytes = matches!(
+                quarantine.reason,
+                IntakeQuarantineReasonV1::InputBytes { .. }
+            );
+            if is_input_bytes != self.content_sha256.is_none()
+                || !quarantine_observed_length_matches(&quarantine.reason, self.byte_length)
+                || (self.outcome != IngestOutcome::IdempotentReplay
+                    && self.outcome != quarantine_ingest_outcome(&quarantine.reason))
+            {
+                return Err(HeleosError::Integrity);
+            }
+        }
+        if accepted_shape {
+            let content = self.content_sha256.ok_or(HeleosError::Integrity)?;
+            let (document, revision) = crate::canonical_document_ids(content);
+            if self.document_id != Some(document)
+                || self.revision_id != Some(revision)
+                || !is_unique(&self.sheet_ids)
+            {
+                return Err(HeleosError::Integrity);
+            }
+            let evidence = self
+                .evidence_manifest
+                .as_ref()
+                .ok_or(HeleosError::Integrity)?;
+            evidence.validate()?;
+            if evidence.manifest.original.sha256 != content
+                || evidence.manifest.original.byte_length != self.byte_length
+                || evidence.manifest.document_id != document
+                || evidence.manifest.revision_id != revision
+                || !self.sheet_ids.iter().copied().eq(evidence
+                    .manifest
+                    .pages
+                    .iter()
+                    .map(|page| page.page_id))
+            {
+                return Err(HeleosError::Integrity);
+            }
+        }
+        let manifest_digest = self
+            .evidence_manifest
+            .as_ref()
+            .map(|evidence| evidence.manifest_content_sha256);
+        if self
+            .preexisting_vault_digests
+            .iter()
+            .any(|digest| Some(*digest) != self.content_sha256 && Some(*digest) != manifest_digest)
+        {
+            return Err(HeleosError::Integrity);
+        }
+        if self.outcome == IngestOutcome::AcceptedDuplicate {
+            let mut expected = vec![
+                self.content_sha256.ok_or(HeleosError::Integrity)?,
+                manifest_digest.ok_or(HeleosError::Integrity)?,
+            ];
+            expected.sort_unstable();
+            expected.dedup();
+            if self.preexisting_vault_digests != expected {
+                return Err(HeleosError::Integrity);
+            }
+        }
+        if self.outcome != IngestOutcome::IdempotentReplay
+            && let Some(quarantine) = &self.quarantine
+        {
+            match quarantine.reason {
+                IntakeQuarantineReasonV1::ProjectAssociationQuota => {
+                    if self.preexisting_vault_digests
+                        != [self.content_sha256.ok_or(HeleosError::Integrity)?]
+                    {
+                        return Err(HeleosError::Integrity);
+                    }
+                }
+                IntakeQuarantineReasonV1::InputBytes { .. }
+                | IntakeQuarantineReasonV1::OriginalRetentionQuota
+                    if !self.preexisting_vault_digests.is_empty() =>
+                {
+                    return Err(HeleosError::Integrity);
+                }
+                _ => {}
+            }
+        }
+        if self.outcome == IngestOutcome::IdempotentReplay {
+            let mut expected = if accepted_shape {
+                vec![
+                    self.content_sha256.ok_or(HeleosError::Integrity)?,
+                    manifest_digest.ok_or(HeleosError::Integrity)?,
+                ]
+            } else if self.quarantine.as_ref().is_some_and(|quarantine| {
+                matches!(
+                    quarantine.reason,
+                    IntakeQuarantineReasonV1::Pdf(_)
+                        | IntakeQuarantineReasonV1::EvidenceManifestQuota
+                        | IntakeQuarantineReasonV1::ProjectAssociationQuota
+                )
+            }) {
+                vec![self.content_sha256.ok_or(HeleosError::Integrity)?]
+            } else {
+                Vec::new()
+            };
+            expected.sort_unstable();
+            expected.dedup();
+            if self.preexisting_vault_digests != expected {
+                return Err(HeleosError::Integrity);
+            }
+        }
+        Ok(())
+    }
+}
+
+pub(crate) fn quarantine_ingest_outcome(reason: &IntakeQuarantineReasonV1) -> IngestOutcome {
+    match reason {
+        IntakeQuarantineReasonV1::Pdf(reason) => match reason {
+            PdfQuarantineReason::BadMagic
+            | PdfQuarantineReason::Corrupt
+            | PdfQuarantineReason::InvalidGeometry => IngestOutcome::QuarantinedCorrupt,
+            PdfQuarantineReason::Encrypted => IngestOutcome::QuarantinedEncrypted,
+            PdfQuarantineReason::UnsupportedUserUnit => IngestOutcome::QuarantinedUnsupported,
+            PdfQuarantineReason::ActiveFeature(_)
+            | PdfQuarantineReason::SandboxTrap
+            | PdfQuarantineReason::ProtocolBreach => IngestOutcome::QuarantinedSuspicious,
+            PdfQuarantineReason::LimitExceeded(_) => IngestOutcome::QuarantinedLimit,
+        },
+        IntakeQuarantineReasonV1::InputBytes { .. }
+        | IntakeQuarantineReasonV1::OriginalRetentionQuota
+        | IntakeQuarantineReasonV1::ProjectAssociationQuota
+        | IntakeQuarantineReasonV1::EvidenceManifestQuota => IngestOutcome::QuarantinedLimit,
+    }
+}
+
+fn quarantine_observed_length_matches(reason: &IntakeQuarantineReasonV1, byte_length: u64) -> bool {
+    !matches!(
+        reason,
+        IntakeQuarantineReasonV1::InputBytes { observed_bytes, .. }
+            if *observed_bytes != byte_length
+    )
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct ResumeReceipt {
+    pub job_id: JobId,
+    pub resumed_attempt: Option<u32>,
+    pub interrupted_event_id: Option<IngestEventId>,
+    pub receipt: IngestReceipt,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawResumeReceipt {
+    job_id: JobId,
+    resumed_attempt: Option<u32>,
+    interrupted_event_id: Option<IngestEventId>,
+    receipt: IngestReceipt,
+}
+
+impl TryFrom<RawResumeReceipt> for ResumeReceipt {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawResumeReceipt) -> Result<Self> {
+        let value = Self {
+            job_id: raw.job_id,
+            resumed_attempt: raw.resumed_attempt,
+            interrupted_event_id: raw.interrupted_event_id,
+            receipt: raw.receipt,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for ResumeReceipt {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawResumeReceipt::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+impl ResumeReceipt {
+    fn validate(&self) -> Result<()> {
+        self.receipt.validate()?;
+        if self.job_id != self.receipt.authoritative_job_id
+            || self.receipt.outcome == IngestOutcome::IdempotentReplay
+        {
+            return Err(HeleosError::Integrity);
+        }
+        match (self.resumed_attempt, self.interrupted_event_id) {
+            (Some(1), None) if self.receipt.attempt == 1 => {}
+            (Some(attempt), Some(_))
+                if (2..=MAX_JOB_ATTEMPTS).contains(&attempt) && self.receipt.attempt == attempt => {
+            }
+            (None, None) => {}
+            _ => return Err(HeleosError::Integrity),
+        }
+        Ok(())
+    }
+}
+
+fn is_strictly_sorted(values: &[Sha256Digest]) -> bool {
+    values.windows(2).all(|pair| pair[0] < pair[1])
+}
+
+fn is_unique(values: &[SheetId]) -> bool {
+    let mut seen = std::collections::BTreeSet::new();
+    values.iter().all(|value| seen.insert(*value.as_digest()))
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub(crate) struct IngestInputV1 {
+    pub schema: String,
+    pub project_id: ProjectId,
+    pub kind: String,
+    pub idempotency_key: IdempotencyKey,
+    pub content_sha256: Option<Sha256Digest>,
+    pub byte_length: u64,
+    pub source_display: String,
+    pub requested_limits: EvidencePdfLimitsV1,
+    pub expected_probe_provenance: PdfProbeProvenance,
+    pub deadline_profile_ms: u64,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawIngestInputV1 {
+    schema: String,
+    project_id: ProjectId,
+    kind: String,
+    idempotency_key: IdempotencyKey,
+    content_sha256: Option<Sha256Digest>,
+    byte_length: u64,
+    #[serde(deserialize_with = "deserialize_source_display")]
+    source_display: String,
+    requested_limits: EvidencePdfLimitsV1,
+    expected_probe_provenance: PdfProbeProvenance,
+    deadline_profile_ms: u64,
+}
+
+fn deserialize_source_display<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<String, D::Error> {
+    super::deserialize_bounded_string::<D, MAX_SOURCE_DISPLAY_BYTES>(deserializer)
+}
+
+impl TryFrom<RawIngestInputV1> for IngestInputV1 {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawIngestInputV1) -> Result<Self> {
+        let value = Self {
+            schema: raw.schema,
+            project_id: raw.project_id,
+            kind: raw.kind,
+            idempotency_key: raw.idempotency_key,
+            content_sha256: raw.content_sha256,
+            byte_length: raw.byte_length,
+            source_display: raw.source_display,
+            requested_limits: raw.requested_limits,
+            expected_probe_provenance: raw.expected_probe_provenance,
+            deadline_profile_ms: raw.deadline_profile_ms,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for IngestInputV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawIngestInputV1::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+impl IngestInputV1 {
+    pub(crate) fn validate(&self) -> Result<()> {
+        let content_is_complete = self.content_sha256.is_some();
+        if self.schema != INGEST_INPUT_SCHEMA_V1
+            || self.kind != INGEST_KIND
+            || self.byte_length > JCS_SAFE_INTEGER_MAX
+            || self.requested_limits != EvidencePdfLimitsV1::from(PdfLimits::default())
+            || self.deadline_profile_ms != JOB_DEADLINE_MS as u64
+            || content_is_complete != (self.byte_length <= PdfLimits::default().max_input_bytes)
+            || !source_display_is_valid(&self.source_display)
+        {
+            return Err(HeleosError::Integrity);
+        }
+        validate_provenance(&self.expected_probe_provenance)
+    }
+
+    pub(crate) fn frozen_eq(&self, other: &Self) -> bool {
+        self.schema == other.schema
+            && self.project_id == other.project_id
+            && self.kind == other.kind
+            && self.idempotency_key == other.idempotency_key
+            && self.content_sha256.is_some()
+            && self.content_sha256 == other.content_sha256
+            && self.byte_length == other.byte_length
+            && self.requested_limits == other.requested_limits
+            && self.expected_probe_provenance == other.expected_probe_provenance
+            && self.deadline_profile_ms == other.deadline_profile_ms
+    }
+
+    pub(crate) fn frozen_mismatching_fields(
+        &self,
+        other: &Self,
+        budget_matches: bool,
+    ) -> Vec<String> {
+        let mut fields = Vec::with_capacity(FROZEN_MISMATCH_FIELDS.len());
+        if !budget_matches {
+            fields.push("budget".to_owned());
+        }
+        if self.byte_length != other.byte_length {
+            fields.push("byte_length".to_owned());
+        }
+        if self.content_sha256 != other.content_sha256 || self.content_sha256.is_none() {
+            fields.push("content_sha256".to_owned());
+        }
+        if self.deadline_profile_ms != other.deadline_profile_ms {
+            fields.push("deadline_profile_ms".to_owned());
+        }
+        if self.expected_probe_provenance != other.expected_probe_provenance {
+            fields.push("expected_probe_provenance".to_owned());
+        }
+        if self.requested_limits != other.requested_limits {
+            fields.push("requested_limits".to_owned());
+        }
+        fields
+    }
+}
+
+fn source_display_is_valid(value: &str) -> bool {
+    if value.is_empty()
+        || value.len() > MAX_SOURCE_DISPLAY_BYTES
+        || value.chars().any(char::is_control)
+    {
+        return false;
+    }
+    if let Some(encoded) = value.strip_prefix("unix-bytes:") {
+        let Some(bytes) = decode_lower_hex(encoded, 2) else {
+            return false;
+        };
+        return !bytes.is_empty()
+            && std::str::from_utf8(&bytes).is_err()
+            && !bytes.contains(&0)
+            && !bytes.contains(&b'/');
+    }
+    if let Some(encoded) = value.strip_prefix("windows-utf16:") {
+        let Some(bytes) = decode_lower_hex(encoded, 4) else {
+            return false;
+        };
+        let units: Vec<u16> = bytes
+            .chunks_exact(2)
+            .map(|pair| u16::from_be_bytes([pair[0], pair[1]]))
+            .collect();
+        return !units.is_empty()
+            && units != [u16::from(b'.')]
+            && units != [u16::from(b'.'), u16::from(b'.')]
+            && !units.iter().any(|unit| matches!(*unit, 0 | 0x2f | 0x5c));
+    }
+    let Some(encoded) = value.strip_prefix("utf8:") else {
+        return false;
+    };
+    let encoded_bytes = encoded.as_bytes();
+    let mut decoded = Vec::with_capacity(encoded_bytes.len());
+    let mut index = 0;
+    while index < encoded_bytes.len() {
+        let byte = encoded_bytes[index];
+        if byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-') {
+            decoded.push(byte);
+            index += 1;
+        } else if byte == b'%'
+            && index + 2 < encoded_bytes.len()
+            && lower_hex_value(encoded_bytes[index + 1]).is_some()
+            && lower_hex_value(encoded_bytes[index + 2]).is_some()
+        {
+            let high = lower_hex_value(encoded_bytes[index + 1]).unwrap_or(0);
+            let low = lower_hex_value(encoded_bytes[index + 2]).unwrap_or(0);
+            decoded.push((high << 4) | low);
+            index += 3;
+        } else {
+            return false;
+        }
+    }
+    !decoded.is_empty()
+        && decoded.as_slice() != b"."
+        && decoded.as_slice() != b".."
+        && std::str::from_utf8(&decoded).is_ok()
+        && !decoded.contains(&0)
+        && !decoded.contains(&b'/')
+        && encode_utf8_display(&decoded).is_ok_and(|canonical| canonical == value)
+}
+
+fn decode_lower_hex(value: &str, encoded_unit_width: usize) -> Option<Vec<u8>> {
+    if value.is_empty() || !value.len().is_multiple_of(encoded_unit_width) {
+        return None;
+    }
+    let bytes = value.as_bytes();
+    let mut decoded = Vec::with_capacity(bytes.len() / 2);
+    for pair in bytes.chunks_exact(2) {
+        decoded.push((lower_hex_value(pair[0])? << 4) | lower_hex_value(pair[1])?);
+    }
+    Some(decoded)
+}
+
+const fn lower_hex_value(byte: u8) -> Option<u8> {
+    match byte {
+        b'0'..=b'9' => Some(byte - b'0'),
+        b'a'..=b'f' => Some(byte - b'a' + 10),
+        _ => None,
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub(crate) struct IngestBudgetV1 {
+    pub schema: String,
+    pub project_overall_bytes: u64,
+    pub store_overall_bytes: u64,
+    pub project_evidence_bytes: u64,
+    pub store_evidence_bytes: u64,
+    pub project_quarantine_bytes: u64,
+    pub store_quarantine_bytes: u64,
+    pub lease_duration_ms: u64,
+    pub max_attempts: u32,
+    pub source_reservation_bytes: u64,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawIngestBudgetV1 {
+    schema: String,
+    project_overall_bytes: u64,
+    store_overall_bytes: u64,
+    project_evidence_bytes: u64,
+    store_evidence_bytes: u64,
+    project_quarantine_bytes: u64,
+    store_quarantine_bytes: u64,
+    lease_duration_ms: u64,
+    max_attempts: u32,
+    source_reservation_bytes: u64,
+}
+
+impl TryFrom<RawIngestBudgetV1> for IngestBudgetV1 {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawIngestBudgetV1) -> Result<Self> {
+        let value = Self {
+            schema: raw.schema,
+            project_overall_bytes: raw.project_overall_bytes,
+            store_overall_bytes: raw.store_overall_bytes,
+            project_evidence_bytes: raw.project_evidence_bytes,
+            store_evidence_bytes: raw.store_evidence_bytes,
+            project_quarantine_bytes: raw.project_quarantine_bytes,
+            store_quarantine_bytes: raw.store_quarantine_bytes,
+            lease_duration_ms: raw.lease_duration_ms,
+            max_attempts: raw.max_attempts,
+            source_reservation_bytes: raw.source_reservation_bytes,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for IngestBudgetV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawIngestBudgetV1::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+impl IngestBudgetV1 {
+    pub(crate) fn new(source_reservation_bytes: u64) -> Self {
+        Self {
+            schema: INGEST_BUDGET_SCHEMA_V1.to_owned(),
+            project_overall_bytes: PROJECT_OVERALL_QUOTA_BYTES,
+            store_overall_bytes: STORE_OVERALL_QUOTA_BYTES,
+            project_evidence_bytes: PROJECT_EVIDENCE_QUOTA_BYTES,
+            store_evidence_bytes: STORE_EVIDENCE_QUOTA_BYTES,
+            project_quarantine_bytes: PROJECT_QUARANTINE_QUOTA_BYTES,
+            store_quarantine_bytes: STORE_QUARANTINE_QUOTA_BYTES,
+            lease_duration_ms: LEASE_DURATION_MS as u64,
+            max_attempts: MAX_JOB_ATTEMPTS,
+            source_reservation_bytes,
+        }
+    }
+
+    pub(crate) fn validate(&self) -> Result<()> {
+        if self.schema != INGEST_BUDGET_SCHEMA_V1
+            || self.project_overall_bytes != PROJECT_OVERALL_QUOTA_BYTES
+            || self.store_overall_bytes != STORE_OVERALL_QUOTA_BYTES
+            || self.project_evidence_bytes != PROJECT_EVIDENCE_QUOTA_BYTES
+            || self.store_evidence_bytes != STORE_EVIDENCE_QUOTA_BYTES
+            || self.project_quarantine_bytes != PROJECT_QUARANTINE_QUOTA_BYTES
+            || self.store_quarantine_bytes != STORE_QUARANTINE_QUOTA_BYTES
+            || self.lease_duration_ms != LEASE_DURATION_MS as u64
+            || self.max_attempts != MAX_JOB_ATTEMPTS
+            || self.source_reservation_bytes > JCS_SAFE_INTEGER_MAX
+        {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+
+    pub(crate) fn validate_for_input(&self, input: &IngestInputV1) -> Result<()> {
+        self.validate()?;
+        if self.source_reservation_bytes != input.byte_length {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub(crate) struct StoredObjectV1 {
+    pub digest: Sha256Digest,
+    pub byte_length: u64,
+    pub vault_key: String,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawStoredObjectV1 {
+    digest: Sha256Digest,
+    byte_length: u64,
+    vault_key: String,
+}
+
+impl TryFrom<RawStoredObjectV1> for StoredObjectV1 {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawStoredObjectV1) -> Result<Self> {
+        let value = Self {
+            digest: raw.digest,
+            byte_length: raw.byte_length,
+            vault_key: raw.vault_key,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for StoredObjectV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawStoredObjectV1::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+impl StoredObjectV1 {
+    pub(crate) fn validate(&self) -> Result<()> {
+        if self.byte_length > JCS_SAFE_INTEGER_MAX
+            || self.vault_key != crate::Vault::object_key(self.digest)
+        {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+}
+
+impl From<&StoredObject> for StoredObjectV1 {
+    fn from(value: &StoredObject) -> Self {
+        Self {
+            digest: value.digest,
+            byte_length: value.byte_length,
+            vault_key: value.vault_key.clone(),
+        }
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+#[serde(
+    tag = "kind",
+    content = "detail",
+    rename_all = "snake_case",
+    deny_unknown_fields
+)]
+pub(crate) enum ProcessingCandidateV1 {
+    Accepted {
+        manifest: EvidenceManifestV1,
+        manifest_object: StoredObjectV1,
+    },
+    Quarantined {
+        outcome: IngestOutcome,
+        quarantine: IntakeQuarantineV1,
+    },
+}
+
+#[derive(Deserialize)]
+#[serde(
+    tag = "kind",
+    content = "detail",
+    rename_all = "snake_case",
+    deny_unknown_fields
+)]
+enum RawProcessingCandidateV1 {
+    Accepted {
+        manifest: EvidenceManifestV1,
+        manifest_object: StoredObjectV1,
+    },
+    Quarantined {
+        outcome: IngestOutcome,
+        quarantine: IntakeQuarantineV1,
+    },
+}
+
+impl TryFrom<RawProcessingCandidateV1> for ProcessingCandidateV1 {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawProcessingCandidateV1) -> Result<Self> {
+        let value = match raw {
+            RawProcessingCandidateV1::Accepted {
+                manifest,
+                manifest_object,
+            } => Self::Accepted {
+                manifest,
+                manifest_object,
+            },
+            RawProcessingCandidateV1::Quarantined {
+                outcome,
+                quarantine,
+            } => Self::Quarantined {
+                outcome,
+                quarantine,
+            },
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for ProcessingCandidateV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawProcessingCandidateV1::deserialize(deserializer)?)
+            .map_err(de::Error::custom)
+    }
+}
+
+impl ProcessingCandidateV1 {
+    pub(crate) fn validate(&self) -> Result<()> {
+        match self {
+            Self::Accepted {
+                manifest,
+                manifest_object,
+            } => {
+                manifest.validate()?;
+                manifest_object.validate()?;
+                let bytes = manifest.canonical_bytes()?;
+                let length = u64::try_from(bytes.len()).map_err(|_| HeleosError::Integrity)?;
+                let digest = Sha256Digest::hash_reader(bytes.as_slice())?;
+                if manifest_object.digest != digest || manifest_object.byte_length != length {
+                    return Err(HeleosError::Integrity);
+                }
+            }
+            Self::Quarantined {
+                outcome,
+                quarantine,
+            } => {
+                quarantine.validate()?;
+                if !matches!(
+                    quarantine.reason,
+                    IntakeQuarantineReasonV1::Pdf(_)
+                        | IntakeQuarantineReasonV1::EvidenceManifestQuota
+                ) || *outcome != quarantine_ingest_outcome(&quarantine.reason)
+                {
+                    return Err(HeleosError::Integrity);
+                }
+            }
+        }
+        Ok(())
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+#[serde(
+    tag = "phase",
+    content = "detail",
+    rename_all = "snake_case",
+    deny_unknown_fields
+)]
+pub(crate) enum IngestCheckpointPhaseV1 {
+    PreflightRejected {
+        content_sha256: Option<Sha256Digest>,
+        byte_length: u64,
+        quarantine: IntakeQuarantineV1,
+    },
+    VaultPublished {
+        original: StoredObjectV1,
+    },
+    ProcessingComplete {
+        original: StoredObjectV1,
+        candidate: ProcessingCandidateV1,
+    },
+    Terminal {
+        receipt: IngestReceipt,
+    },
+}
+
+#[derive(Deserialize)]
+#[serde(
+    tag = "phase",
+    content = "detail",
+    rename_all = "snake_case",
+    deny_unknown_fields
+)]
+enum RawIngestCheckpointPhaseV1 {
+    PreflightRejected {
+        content_sha256: Option<Sha256Digest>,
+        byte_length: u64,
+        quarantine: IntakeQuarantineV1,
+    },
+    VaultPublished {
+        original: StoredObjectV1,
+    },
+    ProcessingComplete {
+        original: StoredObjectV1,
+        candidate: ProcessingCandidateV1,
+    },
+    Terminal {
+        receipt: IngestReceipt,
+    },
+}
+
+impl From<RawIngestCheckpointPhaseV1> for IngestCheckpointPhaseV1 {
+    fn from(raw: RawIngestCheckpointPhaseV1) -> Self {
+        match raw {
+            RawIngestCheckpointPhaseV1::PreflightRejected {
+                content_sha256,
+                byte_length,
+                quarantine,
+            } => Self::PreflightRejected {
+                content_sha256,
+                byte_length,
+                quarantine,
+            },
+            RawIngestCheckpointPhaseV1::VaultPublished { original } => {
+                Self::VaultPublished { original }
+            }
+            RawIngestCheckpointPhaseV1::ProcessingComplete {
+                original,
+                candidate,
+            } => Self::ProcessingComplete {
+                original,
+                candidate,
+            },
+            RawIngestCheckpointPhaseV1::Terminal { receipt } => Self::Terminal { receipt },
+        }
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub(crate) struct IngestCheckpointV1 {
+    pub schema: String,
+    #[serde(flatten)]
+    pub phase: IngestCheckpointPhaseV1,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawIngestCheckpointV1 {
+    schema: String,
+    #[serde(flatten)]
+    phase: RawIngestCheckpointPhaseV1,
+}
+
+impl TryFrom<RawIngestCheckpointV1> for IngestCheckpointV1 {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawIngestCheckpointV1) -> Result<Self> {
+        let value = Self {
+            schema: raw.schema,
+            phase: raw.phase.into(),
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for IngestCheckpointV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawIngestCheckpointV1::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+impl IngestCheckpointV1 {
+    pub(crate) fn validate(&self) -> Result<()> {
+        if self.schema != INGEST_CHECKPOINT_SCHEMA_V1 {
+            return Err(HeleosError::Integrity);
+        }
+        match &self.phase {
+            IngestCheckpointPhaseV1::PreflightRejected {
+                content_sha256,
+                byte_length,
+                quarantine,
+            } => {
+                quarantine.validate()?;
+                if *byte_length > JCS_SAFE_INTEGER_MAX
+                    || !matches!(
+                        quarantine.reason,
+                        IntakeQuarantineReasonV1::InputBytes { .. }
+                            | IntakeQuarantineReasonV1::OriginalRetentionQuota
+                            | IntakeQuarantineReasonV1::ProjectAssociationQuota
+                    )
+                    || matches!(
+                        quarantine.reason,
+                        IntakeQuarantineReasonV1::InputBytes { .. }
+                    ) != content_sha256.is_none()
+                    || !quarantine_observed_length_matches(&quarantine.reason, *byte_length)
+                {
+                    return Err(HeleosError::Integrity);
+                }
+            }
+            IngestCheckpointPhaseV1::VaultPublished { original } => original.validate()?,
+            IngestCheckpointPhaseV1::ProcessingComplete {
+                original,
+                candidate,
+            } => {
+                original.validate()?;
+                candidate.validate()?;
+                if let ProcessingCandidateV1::Accepted { manifest, .. } = candidate
+                    && (manifest.original.sha256 != original.digest
+                        || manifest.original.byte_length != original.byte_length)
+                {
+                    return Err(HeleosError::Integrity);
+                }
+            }
+            IngestCheckpointPhaseV1::Terminal { receipt } => {
+                receipt.validate()?;
+                if receipt.outcome == IngestOutcome::IdempotentReplay {
+                    return Err(HeleosError::Integrity);
+                }
+            }
+        }
+        Ok(())
+    }
+
+    pub(crate) fn validate_for_input(&self, input: &IngestInputV1) -> Result<()> {
+        self.validate()?;
+        let matches_original = |original: &StoredObjectV1| {
+            input.content_sha256 == Some(original.digest)
+                && input.byte_length == original.byte_length
+        };
+        match &self.phase {
+            IngestCheckpointPhaseV1::PreflightRejected {
+                content_sha256,
+                byte_length,
+                ..
+            } if *content_sha256 == input.content_sha256 && *byte_length == input.byte_length => {}
+            IngestCheckpointPhaseV1::VaultPublished { original } if matches_original(original) => {}
+            IngestCheckpointPhaseV1::ProcessingComplete {
+                original,
+                candidate,
+            } if matches_original(original)
+                && candidate_provenance(candidate) == Some(&input.expected_probe_provenance) => {}
+            IngestCheckpointPhaseV1::Terminal { receipt }
+                if receipt.content_sha256 == input.content_sha256
+                    && receipt.byte_length == input.byte_length
+                    && receipt_provenance(receipt).is_none_or(|provenance| {
+                        provenance == &input.expected_probe_provenance
+                    }) => {}
+            _ => return Err(HeleosError::Integrity),
+        }
+        Ok(())
+    }
+}
+
+fn candidate_provenance(candidate: &ProcessingCandidateV1) -> Option<&PdfProbeProvenance> {
+    match candidate {
+        ProcessingCandidateV1::Accepted { manifest, .. } => Some(&manifest.probe_provenance),
+        ProcessingCandidateV1::Quarantined { quarantine, .. } => {
+            quarantine.probe_provenance.as_ref()
+        }
+    }
+}
+
+fn receipt_provenance(receipt: &IngestReceipt) -> Option<&PdfProbeProvenance> {
+    receipt
+        .evidence_manifest
+        .as_ref()
+        .map(|evidence| &evidence.manifest.probe_provenance)
+        .or_else(|| {
+            receipt
+                .quarantine
+                .as_ref()
+                .and_then(|quarantine| quarantine.probe_provenance.as_ref())
+        })
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+#[serde(
+    tag = "kind",
+    content = "detail",
+    rename_all = "snake_case",
+    deny_unknown_fields
+)]
+pub(crate) enum IngestEventDetailV1 {
+    Accepted {
+        schema: String,
+        attempt: u32,
+        document_id: DocumentId,
+        revision_id: RevisionId,
+        evidence_id: EvidenceId,
+    },
+    Quarantined {
+        schema: String,
+        attempt: u32,
+        content_sha256: Option<Sha256Digest>,
+        byte_length: u64,
+        quarantine: IntakeQuarantineV1,
+    },
+    Interrupted {
+        schema: String,
+        attempt: u32,
+        checkpoint_sha256: Sha256Digest,
+    },
+    Replay {
+        schema: String,
+        authoritative_job_id: JobId,
+        authoritative_attempt: u32,
+        authoritative_event_id: IngestEventId,
+    },
+    Conflict {
+        schema: String,
+        authoritative_job_id: JobId,
+        #[serde(deserialize_with = "deserialize_conflict_fields")]
+        mismatching_fields: Vec<String>,
+    },
+}
+
+#[derive(Deserialize)]
+#[serde(
+    tag = "kind",
+    content = "detail",
+    rename_all = "snake_case",
+    deny_unknown_fields
+)]
+enum RawIngestEventDetailV1 {
+    Accepted {
+        schema: String,
+        attempt: u32,
+        document_id: DocumentId,
+        revision_id: RevisionId,
+        evidence_id: EvidenceId,
+    },
+    Quarantined {
+        schema: String,
+        attempt: u32,
+        content_sha256: Option<Sha256Digest>,
+        byte_length: u64,
+        quarantine: IntakeQuarantineV1,
+    },
+    Interrupted {
+        schema: String,
+        attempt: u32,
+        checkpoint_sha256: Sha256Digest,
+    },
+    Replay {
+        schema: String,
+        authoritative_job_id: JobId,
+        authoritative_attempt: u32,
+        authoritative_event_id: IngestEventId,
+    },
+    Conflict {
+        schema: String,
+        authoritative_job_id: JobId,
+        #[serde(deserialize_with = "deserialize_conflict_fields")]
+        mismatching_fields: Vec<String>,
+    },
+}
+
+impl From<RawIngestEventDetailV1> for IngestEventDetailV1 {
+    fn from(raw: RawIngestEventDetailV1) -> Self {
+        match raw {
+            RawIngestEventDetailV1::Accepted {
+                schema,
+                attempt,
+                document_id,
+                revision_id,
+                evidence_id,
+            } => Self::Accepted {
+                schema,
+                attempt,
+                document_id,
+                revision_id,
+                evidence_id,
+            },
+            RawIngestEventDetailV1::Quarantined {
+                schema,
+                attempt,
+                content_sha256,
+                byte_length,
+                quarantine,
+            } => Self::Quarantined {
+                schema,
+                attempt,
+                content_sha256,
+                byte_length,
+                quarantine,
+            },
+            RawIngestEventDetailV1::Interrupted {
+                schema,
+                attempt,
+                checkpoint_sha256,
+            } => Self::Interrupted {
+                schema,
+                attempt,
+                checkpoint_sha256,
+            },
+            RawIngestEventDetailV1::Replay {
+                schema,
+                authoritative_job_id,
+                authoritative_attempt,
+                authoritative_event_id,
+            } => Self::Replay {
+                schema,
+                authoritative_job_id,
+                authoritative_attempt,
+                authoritative_event_id,
+            },
+            RawIngestEventDetailV1::Conflict {
+                schema,
+                authoritative_job_id,
+                mismatching_fields,
+            } => Self::Conflict {
+                schema,
+                authoritative_job_id,
+                mismatching_fields,
+            },
+        }
+    }
+}
+
+impl<'de> Deserialize<'de> for IngestEventDetailV1 {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        let value = Self::from(RawIngestEventDetailV1::deserialize(deserializer)?);
+        value.validate().map_err(de::Error::custom)?;
+        Ok(value)
+    }
+}
+
+impl IngestEventDetailV1 {
+    pub(crate) fn validate(&self) -> Result<()> {
+        let valid_attempt = |attempt: u32| (1..=MAX_JOB_ATTEMPTS).contains(&attempt);
+        match self {
+            Self::Accepted {
+                schema,
+                attempt,
+                document_id,
+                revision_id,
+                ..
+            } if schema == INGEST_EVENT_DETAILS_SCHEMA_V1
+                && valid_attempt(*attempt)
+                && document_id.as_digest() == revision_id.as_digest() => {}
+            Self::Quarantined {
+                schema,
+                attempt,
+                content_sha256,
+                byte_length,
+                quarantine,
+            } => {
+                quarantine.validate()?;
+                let is_input_bytes = matches!(
+                    quarantine.reason,
+                    IntakeQuarantineReasonV1::InputBytes { .. }
+                );
+                if schema != INGEST_EVENT_DETAILS_SCHEMA_V1
+                    || !valid_attempt(*attempt)
+                    || *byte_length > JCS_SAFE_INTEGER_MAX
+                    || is_input_bytes != content_sha256.is_none()
+                    || !quarantine_observed_length_matches(&quarantine.reason, *byte_length)
+                {
+                    return Err(HeleosError::Integrity);
+                }
+            }
+            Self::Interrupted {
+                schema, attempt, ..
+            } if schema == INGEST_EVENT_DETAILS_SCHEMA_V1 && valid_attempt(*attempt) => {}
+            Self::Replay {
+                schema,
+                authoritative_attempt,
+                ..
+            } if schema == INGEST_EVENT_DETAILS_SCHEMA_V1
+                && valid_attempt(*authoritative_attempt) => {}
+            Self::Conflict {
+                schema,
+                mismatching_fields,
+                ..
+            } if schema == INGEST_EVENT_DETAILS_SCHEMA_V1
+                && !mismatching_fields.is_empty()
+                && mismatching_fields.len() <= FROZEN_MISMATCH_FIELDS.len()
+                && mismatching_fields.windows(2).all(|pair| pair[0] < pair[1])
+                && mismatching_fields
+                    .iter()
+                    .all(|field| FROZEN_MISMATCH_FIELDS.contains(&field.as_str())) => {}
+            _ => return Err(HeleosError::Integrity),
+        }
+        Ok(())
+    }
+}
+
+const FROZEN_MISMATCH_FIELDS: [&str; 6] = [
+    "budget",
+    "byte_length",
+    "content_sha256",
+    "deadline_profile_ms",
+    "expected_probe_provenance",
+    "requested_limits",
+];
+
+fn deserialize_conflict_fields<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<Vec<String>, D::Error> {
+    super::deserialize_bounded_vec::<D, String, 6>(deserializer)
+}
+
+pub(crate) fn json_text<T: Serialize>(value: &T, maximum: usize) -> Result<String> {
+    let bytes = super::evidence::bounded_canonical_json(value, maximum)?;
+    String::from_utf8(bytes).map_err(|_| HeleosError::Integrity)
+}
+
+pub struct IngestEngine<'a> {
+    store: &'a mut crate::Store,
+    vault: &'a crate::Vault,
+    probe: &'a dyn crate::PdfProbe,
+    clock: &'a dyn crate::Clock,
+    ids: &'a dyn crate::IdGenerator,
+    faults: &'a dyn super::FaultInjector,
+    probe_provenance: PdfProbeProvenance,
+}
+
+impl<'a> IngestEngine<'a> {
+    pub fn new(
+        store: &'a mut crate::Store,
+        vault: &'a crate::Vault,
+        probe: &'a dyn crate::PdfProbe,
+        clock: &'a dyn crate::Clock,
+        ids: &'a dyn crate::IdGenerator,
+    ) -> Result<Self> {
+        Self::with_fault_injector(store, vault, probe, clock, ids, &super::fault::NO_FAULTS)
+    }
+
+    pub fn with_fault_injector(
+        store: &'a mut crate::Store,
+        vault: &'a crate::Vault,
+        probe: &'a dyn crate::PdfProbe,
+        clock: &'a dyn crate::Clock,
+        ids: &'a dyn crate::IdGenerator,
+        faults: &'a dyn super::FaultInjector,
+    ) -> Result<Self> {
+        store.require_writer_capability()?;
+        let probe_provenance = probe.provenance()?;
+        validate_provenance(&probe_provenance)?;
+        Ok(Self {
+            store,
+            vault,
+            probe,
+            clock,
+            ids,
+            faults,
+            probe_provenance,
+        })
+    }
+
+    pub fn ingest(&mut self, mut request: IngestRequest) -> Result<IngestReceipt> {
+        use crate::store::ingest_repository::{
+            ConflictAttemptCommand, IdempotencyLookup, ReplayAttemptCommand,
+        };
+
+        let idempotency = match self
+            .store
+            .idempotency_lookup(request.project_id, &request.idempotency_key)?
+        {
+            IdempotencyLookup::MissingProject => return Err(HeleosError::NotFound),
+            lookup => lookup,
+        };
+        if request.pdf_limits != PdfLimits::default() {
+            return Err(HeleosError::PolicyDenied);
+        }
+        let fingerprint = request
+            .source
+            .fingerprint(PdfLimits::default().max_input_bytes)?;
+        let input = IngestInputV1 {
+            schema: INGEST_INPUT_SCHEMA_V1.to_owned(),
+            project_id: request.project_id,
+            kind: INGEST_KIND.to_owned(),
+            idempotency_key: request.idempotency_key.clone(),
+            content_sha256: fingerprint.digest,
+            byte_length: fingerprint.byte_length,
+            source_display: fingerprint.display_name.clone(),
+            requested_limits: PdfLimits::default().into(),
+            expected_probe_provenance: self.probe_provenance.clone(),
+            deadline_profile_ms: JOB_DEADLINE_MS as u64,
+        };
+        input.validate()?;
+        let budget = IngestBudgetV1::new(fingerprint.byte_length);
+        match idempotency {
+            IdempotencyLookup::Existing(record) => {
+                if !record.input.frozen_eq(&input) || record.budget != budget {
+                    let now_ms = checked_jcs_time(self.clock.now_unix_ms())?;
+                    request.source.verify_unchanged(&fingerprint)?;
+                    self.store.conflict_attempt_commit(ConflictAttemptCommand {
+                        authoritative_job_id: record.job_id,
+                        project_id: request.project_id,
+                        submitted_input: input,
+                        submitted_budget: budget,
+                        actor: request.actor,
+                        source_display: fingerprint.display_name,
+                        ingest_event_id: IngestEventId::from_uuid(self.ids.next_uuid()),
+                        source_record_id: self.ids.next_uuid().hyphenated().to_string(),
+                        now_ms,
+                        audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
+                    })?;
+                    return Err(HeleosError::IdempotencyConflict);
+                }
+                if record.state != crate::JobState::Succeeded {
+                    return Err(HeleosError::InvalidStateTransition);
+                }
+                let IngestCheckpointPhaseV1::Terminal { receipt } = &record.checkpoint.phase else {
+                    return Err(HeleosError::Integrity);
+                };
+                let preexisting_vault_digests =
+                    verify_receipt_objects(self.store, self.vault, request.project_id, receipt)?;
+                let now_ms = checked_jcs_time(self.clock.now_unix_ms())?;
+                request.source.verify_unchanged(&fingerprint)?;
+                return self.store.replay_attempt_commit(ReplayAttemptCommand {
+                    authoritative_job_id: record.job_id,
+                    project_id: request.project_id,
+                    idempotency_key: request.idempotency_key,
+                    actor: request.actor,
+                    source_display: Some(fingerprint.display_name),
+                    ingest_event_id: Some(IngestEventId::from_uuid(self.ids.next_uuid())),
+                    source_record_id: Some(self.ids.next_uuid().hyphenated().to_string()),
+                    preexisting_vault_digests,
+                    now_ms,
+                    audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
+                });
+            }
+            IdempotencyLookup::Vacant => {}
+            IdempotencyLookup::MissingProject => unreachable!("missing project handled above"),
+        }
+
+        let Some(expected_digest) = fingerprint.digest else {
+            let quarantine = IntakeQuarantineV1 {
+                schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                reason: IntakeQuarantineReasonV1::InputBytes {
+                    limit_bytes: PdfLimits::default().max_input_bytes,
+                    observed_bytes: fingerprint.byte_length,
+                },
+                probe_provenance: None,
+            };
+            quarantine.validate()?;
+            request.source.verify_unchanged(&fingerprint)?;
+            return self.complete_preflight_quarantine(
+                input,
+                budget,
+                request.actor,
+                quarantine,
+                Vec::new(),
+            );
+        };
+        let quota = self
+            .store
+            .quota_snapshot(request.project_id, Some(expected_digest))?;
+        if let Some(admission) = quota.admission.clone() {
+            if admission.digest != expected_digest
+                || admission.byte_length != fingerprint.byte_length
+                || admission.media_type != super::evidence::PDF_MEDIA_TYPE
+                || admission.vault_key != crate::Vault::object_key(expected_digest)
+            {
+                return Err(HeleosError::Integrity);
+            }
+            let original = StoredObjectV1 {
+                digest: admission.digest,
+                byte_length: admission.byte_length,
+                vault_key: admission.vault_key,
+            };
+            drop(open_checkpoint_object(self.vault, &original)?);
+            match admission.admission_state.as_str() {
+                "quarantined" => {
+                    let quarantine = admission.quarantine.ok_or(HeleosError::Integrity)?;
+                    if !matches!(
+                        quarantine.reason,
+                        IntakeQuarantineReasonV1::Pdf(_)
+                            | IntakeQuarantineReasonV1::EvidenceManifestQuota
+                    ) {
+                        return Err(HeleosError::Integrity);
+                    }
+                    if quarantine.probe_provenance.as_ref()
+                        != Some(&input.expected_probe_provenance)
+                    {
+                        return Err(HeleosError::Integrity);
+                    }
+                    if (!quota.project_overall_accounted
+                        && !prospective_fits(
+                            quota.project_overall_bytes,
+                            original.byte_length,
+                            PROJECT_OVERALL_QUOTA_BYTES,
+                        )?)
+                        || (!quota.project_quarantine_accounted
+                            && !prospective_fits(
+                                quota.project_quarantine_bytes,
+                                original.byte_length,
+                                PROJECT_QUARANTINE_QUOTA_BYTES,
+                            )?)
+                    {
+                        request.source.verify_unchanged(&fingerprint)?;
+                        return self.complete_preflight_quarantine(
+                            input,
+                            budget,
+                            request.actor,
+                            IntakeQuarantineV1 {
+                                schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                                reason: IntakeQuarantineReasonV1::ProjectAssociationQuota,
+                                probe_provenance: None,
+                            },
+                            vec![original.digest],
+                        );
+                    }
+                    request.source.verify_unchanged(&fingerprint)?;
+                    let (job_id, attempt) = self.create_started_job(
+                        input.clone(),
+                        budget,
+                        IngestCheckpointV1 {
+                            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                            phase: IngestCheckpointPhaseV1::VaultPublished {
+                                original: original.clone(),
+                            },
+                        },
+                        &request.actor,
+                        true,
+                    )?;
+                    let failure_actor = request.actor.clone();
+                    let fresh_quota = self
+                        .store
+                        .quota_snapshot(input.project_id, Some(original.digest))?;
+                    let result = self.resume_admitted_original(
+                        job_id,
+                        attempt,
+                        &input,
+                        request.actor,
+                        original,
+                        fresh_quota,
+                        None,
+                    );
+                    return self.resolve_running_result(
+                        job_id,
+                        input.project_id,
+                        attempt,
+                        failure_actor,
+                        result,
+                    );
+                }
+                "accepted" => {
+                    if admission.quarantine.is_some() {
+                        return Err(HeleosError::Integrity);
+                    }
+                    let (_, revision_id) = crate::canonical_document_ids(original.digest);
+                    let evidence = super::FoundationReader::new(self.store, self.vault)
+                        .evidence_manifest_for_revision(revision_id)
+                        .map_err(|error| match error {
+                            HeleosError::NotFound => HeleosError::Integrity,
+                            other => other,
+                        })?;
+                    if evidence.manifest.original.sha256 != original.digest
+                        || evidence.manifest.original.byte_length != original.byte_length
+                        || evidence.original_vault_key != original.vault_key
+                        || evidence.manifest.probe_provenance != input.expected_probe_provenance
+                    {
+                        return Err(HeleosError::Integrity);
+                    }
+                    let manifest_object = StoredObjectV1 {
+                        digest: evidence.manifest_content_sha256,
+                        byte_length: evidence.manifest_byte_length,
+                        vault_key: evidence.manifest_vault_key.clone(),
+                    };
+                    let manifest_quota = self
+                        .store
+                        .quota_snapshot(request.project_id, Some(manifest_object.digest))?;
+                    let has_project_lineage = evidence
+                        .lineages
+                        .iter()
+                        .any(|lineage| lineage.project_id == request.project_id);
+                    let original_debit = if quota.project_overall_accounted {
+                        0
+                    } else {
+                        original.byte_length
+                    };
+                    let manifest_overall_debit =
+                        if manifest_quota.project_overall_accounted || has_project_lineage {
+                            0
+                        } else {
+                            manifest_object.byte_length
+                        };
+                    let manifest_evidence_debit =
+                        if manifest_quota.project_evidence_accounted || has_project_lineage {
+                            0
+                        } else {
+                            manifest_object.byte_length
+                        };
+                    let overall_debit = original_debit
+                        .checked_add(manifest_overall_debit)
+                        .ok_or(HeleosError::Integrity)?;
+                    if !prospective_fits(
+                        quota.project_overall_bytes,
+                        overall_debit,
+                        PROJECT_OVERALL_QUOTA_BYTES,
+                    )? || !prospective_fits(
+                        manifest_quota.project_evidence_bytes,
+                        manifest_evidence_debit,
+                        PROJECT_EVIDENCE_QUOTA_BYTES,
+                    )? {
+                        request.source.verify_unchanged(&fingerprint)?;
+                        return self.complete_preflight_quarantine(
+                            input,
+                            budget,
+                            request.actor,
+                            IntakeQuarantineV1 {
+                                schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                                reason: IntakeQuarantineReasonV1::ProjectAssociationQuota,
+                                probe_provenance: None,
+                            },
+                            vec![original.digest],
+                        );
+                    }
+                    request.source.verify_unchanged(&fingerprint)?;
+                    let (job_id, attempt) = self.create_started_job(
+                        input.clone(),
+                        budget,
+                        IngestCheckpointV1 {
+                            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                            phase: IngestCheckpointPhaseV1::VaultPublished {
+                                original: original.clone(),
+                            },
+                        },
+                        &request.actor,
+                        true,
+                    )?;
+                    let failure_actor = request.actor.clone();
+                    let fresh_quota = self
+                        .store
+                        .quota_snapshot(input.project_id, Some(original.digest))?;
+                    let result = self.resume_admitted_original(
+                        job_id,
+                        attempt,
+                        &input,
+                        request.actor,
+                        original,
+                        fresh_quota,
+                        None,
+                    );
+                    return self.resolve_running_result(
+                        job_id,
+                        input.project_id,
+                        attempt,
+                        failure_actor,
+                        result,
+                    );
+                }
+                _ => return Err(HeleosError::Integrity),
+            }
+        }
+        let orphan_preexisting = match self.vault.open_verified(expected_digest) {
+            Ok(verified) => {
+                if verified.byte_length() != fingerprint.byte_length
+                    || verified.vault_key() != crate::Vault::object_key(expected_digest)
+                {
+                    return Err(HeleosError::Integrity);
+                }
+                true
+            }
+            Err(HeleosError::NotFound) if !quota.store_overall_accounted => false,
+            Err(HeleosError::NotFound) => return Err(HeleosError::Integrity),
+            Err(error) => return Err(error),
+        };
+        let project_overall_debit = if quota.project_overall_accounted {
+            0
+        } else {
+            fingerprint.byte_length
+        };
+        let project_quarantine_debit = if quota.project_quarantine_accounted {
+            0
+        } else {
+            fingerprint.byte_length
+        };
+        let project_capacity = prospective_fits(
+            quota.project_overall_bytes,
+            project_overall_debit,
+            PROJECT_OVERALL_QUOTA_BYTES,
+        )? && prospective_fits(
+            quota.project_quarantine_bytes,
+            project_quarantine_debit,
+            PROJECT_QUARANTINE_QUOTA_BYTES,
+        )?;
+        if !project_capacity {
+            let (reason, preexisting) = if orphan_preexisting {
+                (
+                    IntakeQuarantineReasonV1::ProjectAssociationQuota,
+                    vec![expected_digest],
+                )
+            } else {
+                (IntakeQuarantineReasonV1::OriginalRetentionQuota, Vec::new())
+            };
+            request.source.verify_unchanged(&fingerprint)?;
+            return self.complete_preflight_quarantine(
+                input,
+                budget,
+                request.actor,
+                IntakeQuarantineV1 {
+                    schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                    reason,
+                    probe_provenance: None,
+                },
+                preexisting,
+            );
+        }
+        let original_store_debit = if orphan_preexisting {
+            0
+        } else {
+            fingerprint.byte_length
+        };
+        let original_quarantine_store_debit = if quota.store_quarantine_accounted {
+            0
+        } else {
+            fingerprint.byte_length
+        };
+        let store_capacity = prospective_fits(
+            quota.store_overall_bytes,
+            original_store_debit,
+            STORE_OVERALL_QUOTA_BYTES,
+        )? && prospective_fits(
+            quota.store_quarantine_bytes,
+            original_quarantine_store_debit,
+            STORE_QUARANTINE_QUOTA_BYTES,
+        )?;
+        if !store_capacity {
+            let (reason, preexisting) = if orphan_preexisting {
+                (
+                    IntakeQuarantineReasonV1::ProjectAssociationQuota,
+                    vec![expected_digest],
+                )
+            } else {
+                (IntakeQuarantineReasonV1::OriginalRetentionQuota, Vec::new())
+            };
+            request.source.verify_unchanged(&fingerprint)?;
+            return self.complete_preflight_quarantine(
+                input,
+                budget,
+                request.actor,
+                IntakeQuarantineV1 {
+                    schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                    reason,
+                    probe_provenance: None,
+                },
+                preexisting,
+            );
+        }
+        let original_retain_budget = remaining_min(&[
+            (PROJECT_OVERALL_QUOTA_BYTES, quota.project_overall_bytes),
+            (STORE_OVERALL_QUOTA_BYTES, quota.store_overall_bytes),
+            (
+                PROJECT_QUARANTINE_QUOTA_BYTES,
+                quota.project_quarantine_bytes,
+            ),
+            (STORE_QUARANTINE_QUOTA_BYTES, quota.store_quarantine_bytes),
+        ])?;
+        let put = request.source.publish(
+            self.vault,
+            crate::VaultWriteBudget::new(
+                PdfLimits::default().max_input_bytes,
+                original_retain_budget,
+            )?,
+            expected_digest,
+            fingerprint.byte_length,
+        )?;
+        let stored_original = match put {
+            crate::PutOutcome::Stored(stored) => stored,
+            crate::PutOutcome::QuotaRejected {
+                digest,
+                byte_length,
+            } => {
+                if digest != expected_digest || byte_length != fingerprint.byte_length {
+                    return Err(HeleosError::Integrity);
+                }
+                return self.complete_preflight_quarantine(
+                    input,
+                    budget,
+                    request.actor,
+                    IntakeQuarantineV1 {
+                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                        reason: IntakeQuarantineReasonV1::OriginalRetentionQuota,
+                        probe_provenance: None,
+                    },
+                    Vec::new(),
+                );
+            }
+        };
+        let original = StoredObjectV1::from(&stored_original);
+        let (job_id, attempt) = self.create_started_job(
+            input.clone(),
+            budget,
+            IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::VaultPublished {
+                    original: original.clone(),
+                },
+            },
+            &request.actor,
+            true,
+        )?;
+        let failure_actor = request.actor.clone();
+        let processing_result = (|| -> Result<IngestReceipt> {
+            let verified = open_checkpoint_object(self.vault, &original)?;
+            let (_, revision_id) = crate::canonical_document_ids(original.digest);
+            let inspection = match self
+                .probe
+                .probe(verified, revision_id, PdfLimits::default())?
+            {
+                crate::PdfProbeOutcome::Accepted(inspection) => inspection,
+                crate::PdfProbeOutcome::Quarantined(pdf_quarantine) => {
+                    if pdf_quarantine.content_sha256 != original.digest
+                        || pdf_quarantine.byte_length != original.byte_length
+                        || pdf_quarantine.provenance != self.probe_provenance
+                    {
+                        return Err(HeleosError::Integrity);
+                    }
+                    let outcome = pdf_quarantine.ingest_outcome();
+                    let quarantine = IntakeQuarantineV1 {
+                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                        reason: IntakeQuarantineReasonV1::Pdf(pdf_quarantine.reason),
+                        probe_provenance: Some(pdf_quarantine.provenance),
+                    };
+                    let preexisting = if stored_original.newly_published {
+                        Vec::new()
+                    } else {
+                        vec![original.digest]
+                    };
+                    return self.complete_quarantine(
+                        job_id,
+                        attempt,
+                        true,
+                        &input,
+                        request.actor,
+                        Some(original),
+                        quarantine,
+                        outcome,
+                        preexisting,
+                    );
+                }
+            };
+            if inspection.revision_id != revision_id
+                || inspection.content_sha256 != original.digest
+                || inspection.byte_length != original.byte_length
+                || inspection.provenance != self.probe_provenance
+            {
+                return Err(HeleosError::Integrity);
+            }
+            let manifest = EvidenceManifestV1::new(
+                original.digest,
+                original.byte_length,
+                inspection.pages,
+                inspection.provenance,
+            )?;
+            let manifest_bytes = manifest.canonical_bytes()?;
+            let manifest_byte_length =
+                u64::try_from(manifest_bytes.len()).map_err(|_| HeleosError::Integrity)?;
+            let manifest_digest = Sha256Digest::hash_reader(manifest_bytes.as_slice())?;
+            let manifest_quota = self
+                .store
+                .quota_snapshot(request.project_id, Some(manifest_digest))?;
+            if let Some(admission) = &manifest_quota.admission
+                && (admission.digest != manifest_digest
+                    || admission.byte_length != manifest_byte_length
+                    || admission.media_type != super::evidence::EVIDENCE_MANIFEST_MEDIA_TYPE
+                    || admission.admission_state != "accepted"
+                    || admission.vault_key != crate::Vault::object_key(manifest_digest)
+                    || admission.quarantine.is_some())
+            {
+                return Err(HeleosError::Integrity);
+            }
+            let manifest_preexisting = match self.vault.open_verified(manifest_digest) {
+                Ok(verified) => {
+                    if verified.byte_length() != manifest_byte_length
+                        || verified.vault_key() != crate::Vault::object_key(manifest_digest)
+                    {
+                        return Err(HeleosError::Integrity);
+                    }
+                    true
+                }
+                Err(HeleosError::NotFound)
+                    if manifest_quota.admission.is_none()
+                        && !manifest_quota.store_overall_accounted
+                        && !manifest_quota.store_evidence_accounted =>
+                {
+                    false
+                }
+                Err(HeleosError::NotFound) => return Err(HeleosError::Integrity),
+                Err(error) => return Err(error),
+            };
+            let original_project_debit = if quota.project_overall_accounted {
+                0
+            } else {
+                original.byte_length
+            };
+            let manifest_project_debit = if manifest_quota.project_overall_accounted {
+                0
+            } else {
+                manifest_byte_length
+            };
+            let manifest_project_evidence_debit = if manifest_quota.project_evidence_accounted {
+                0
+            } else {
+                manifest_byte_length
+            };
+            let project_debit = original_project_debit
+                .checked_add(manifest_project_debit)
+                .ok_or(HeleosError::Integrity)?;
+            let original_store_debit = if stored_original.newly_published {
+                original.byte_length
+            } else {
+                0
+            };
+            let manifest_store_overall_debit = if manifest_preexisting {
+                0
+            } else {
+                manifest_byte_length
+            };
+            let manifest_store_evidence_debit = if manifest_quota.store_evidence_accounted {
+                0
+            } else {
+                manifest_byte_length
+            };
+            let store_debit = original_store_debit
+                .checked_add(manifest_store_overall_debit)
+                .ok_or(HeleosError::Integrity)?;
+            let manifest_fits = prospective_fits(
+                quota.project_overall_bytes,
+                project_debit,
+                PROJECT_OVERALL_QUOTA_BYTES,
+            )? && prospective_fits(
+                manifest_quota.project_evidence_bytes,
+                manifest_project_evidence_debit,
+                PROJECT_EVIDENCE_QUOTA_BYTES,
+            )? && prospective_fits(
+                quota.store_overall_bytes,
+                store_debit,
+                STORE_OVERALL_QUOTA_BYTES,
+            )? && prospective_fits(
+                manifest_quota.store_evidence_bytes,
+                manifest_store_evidence_debit,
+                STORE_EVIDENCE_QUOTA_BYTES,
+            )?;
+            if !manifest_fits {
+                return self.complete_quarantine(
+                    job_id,
+                    attempt,
+                    true,
+                    &input,
+                    request.actor,
+                    Some(original.clone()),
+                    IntakeQuarantineV1 {
+                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                        reason: IntakeQuarantineReasonV1::EvidenceManifestQuota,
+                        probe_provenance: Some(self.probe_provenance.clone()),
+                    },
+                    IngestOutcome::QuarantinedLimit,
+                    if stored_original.newly_published {
+                        Vec::new()
+                    } else {
+                        vec![original.digest]
+                    },
+                );
+            }
+            let projected_project_overall = quota
+                .project_overall_bytes
+                .checked_add(original_project_debit)
+                .ok_or(HeleosError::Integrity)?;
+            let projected_store_overall = quota
+                .store_overall_bytes
+                .checked_add(original_store_debit)
+                .ok_or(HeleosError::Integrity)?;
+            let manifest_retain_budget = remaining_min(&[
+                (PROJECT_OVERALL_QUOTA_BYTES, projected_project_overall),
+                (STORE_OVERALL_QUOTA_BYTES, projected_store_overall),
+                (
+                    PROJECT_EVIDENCE_QUOTA_BYTES,
+                    manifest_quota.project_evidence_bytes,
+                ),
+                (
+                    STORE_EVIDENCE_QUOTA_BYTES,
+                    manifest_quota.store_evidence_bytes,
+                ),
+            ])?;
+            let manifest_put = self.vault.put_reader(
+                manifest_bytes.as_slice(),
+                crate::VaultWriteBudget::new(manifest_byte_length, manifest_retain_budget)?,
+            )?;
+            let stored_manifest = match manifest_put {
+                crate::PutOutcome::Stored(stored) => stored,
+                crate::PutOutcome::QuotaRejected {
+                    digest,
+                    byte_length,
+                } => {
+                    if digest != manifest_digest || byte_length != manifest_byte_length {
+                        return Err(HeleosError::Integrity);
+                    }
+                    return self.complete_quarantine(
+                        job_id,
+                        attempt,
+                        true,
+                        &input,
+                        request.actor,
+                        Some(original.clone()),
+                        IntakeQuarantineV1 {
+                            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                            reason: IntakeQuarantineReasonV1::EvidenceManifestQuota,
+                            probe_provenance: Some(self.probe_provenance.clone()),
+                        },
+                        IngestOutcome::QuarantinedLimit,
+                        if stored_original.newly_published {
+                            Vec::new()
+                        } else {
+                            vec![original.digest]
+                        },
+                    );
+                }
+            };
+            if stored_manifest.newly_published == manifest_preexisting {
+                return Err(HeleosError::Integrity);
+            }
+            let manifest_object = StoredObjectV1::from(&stored_manifest);
+            let mut preexisting_vault_digests = Vec::new();
+            if !stored_original.newly_published {
+                preexisting_vault_digests.push(stored_original.digest);
+            }
+            if !stored_manifest.newly_published {
+                preexisting_vault_digests.push(stored_manifest.digest);
+            }
+            self.complete_accepted(
+                job_id,
+                attempt,
+                true,
+                &input,
+                request.actor,
+                original,
+                manifest_object,
+                manifest,
+                preexisting_vault_digests,
+            )
+        })();
+        self.resolve_running_result(
+            job_id,
+            input.project_id,
+            attempt,
+            failure_actor,
+            processing_result,
+        )
+    }
+
+    fn create_started_job(
+        &mut self,
+        input: IngestInputV1,
+        budget: IngestBudgetV1,
+        checkpoint: IngestCheckpointV1,
+        actor: &ActorId,
+        fire_vault_boundary: bool,
+    ) -> Result<(JobId, u32)> {
+        use crate::store::ingest_repository::{JobCreateCommand, JobStartCommand};
+
+        let created_at_ms = checked_jcs_time(self.clock.now_unix_ms())?;
+        let deadline_at_ms = checked_jcs_add(created_at_ms, JOB_DEADLINE_MS)?;
+        let job_id = JobId::from_uuid(self.ids.next_uuid());
+        let project_id = input.project_id;
+        self.store
+            .job_create_with_checkpoint_and_audit(JobCreateCommand {
+                job_id,
+                project_id,
+                input,
+                budget,
+                checkpoint,
+                actor: actor.clone(),
+                created_at_ms,
+                deadline_at_ms,
+                audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
+            })?;
+        if fire_vault_boundary {
+            self.faults.inject(super::FaultPoint::AfterVaultPublish)?;
+        }
+        let now_ms = checked_jcs_time(self.clock.now_unix_ms())?;
+        let attempt = self.store.job_start_or_resume_with_audit(JobStartCommand {
+            job_id,
+            project_id,
+            actor: actor.clone(),
+            lease_owner: self.ids.next_uuid(),
+            now_ms,
+            lease_expires_at_ms: checked_jcs_add(now_ms, LEASE_DURATION_MS)?,
+            audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
+        })?;
+        self.faults.inject(super::FaultPoint::AfterJobStart)?;
+        Ok((job_id, attempt))
+    }
+
+    fn resolve_running_result<T>(
+        &mut self,
+        job_id: JobId,
+        project_id: ProjectId,
+        attempt: u32,
+        actor: ActorId,
+        result: Result<T>,
+    ) -> Result<T> {
+        use crate::store::ingest_repository::JobFailCommand;
+
+        let Err(error) = result else {
+            return result;
+        };
+        if matches!(
+            error,
+            HeleosError::FaultInjected
+                | HeleosError::CommitOutcomeUnknown
+                | HeleosError::Database
+                | HeleosError::WriterBusy
+                | HeleosError::Migration
+        ) {
+            return Err(error);
+        }
+        let Ok(now_ms) = checked_jcs_time(self.clock.now_unix_ms()) else {
+            return Err(error);
+        };
+        match self.store.job_fail_with_audit(JobFailCommand {
+            job_id,
+            project_id,
+            attempt,
+            actor,
+            now_ms,
+            audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
+        }) {
+            Ok(()) => Err(error),
+            Err(HeleosError::CommitOutcomeUnknown) => Err(HeleosError::CommitOutcomeUnknown),
+            Err(_) => Err(error),
+        }
+    }
+
+    fn complete_preflight_quarantine(
+        &mut self,
+        input: IngestInputV1,
+        budget: IngestBudgetV1,
+        actor: ActorId,
+        quarantine: IntakeQuarantineV1,
+        preexisting_vault_digests: Vec<Sha256Digest>,
+    ) -> Result<IngestReceipt> {
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::PreflightRejected {
+                content_sha256: input.content_sha256,
+                byte_length: input.byte_length,
+                quarantine: quarantine.clone(),
+            },
+        };
+        let (job_id, attempt) =
+            self.create_started_job(input.clone(), budget, checkpoint, &actor, false)?;
+        let failure_actor = actor.clone();
+        let result = self.complete_quarantine(
+            job_id,
+            attempt,
+            false,
+            &input,
+            actor,
+            None,
+            quarantine,
+            IngestOutcome::QuarantinedLimit,
+            preexisting_vault_digests,
+        );
+        self.resolve_running_result(job_id, input.project_id, attempt, failure_actor, result)
+    }
+
+    fn complete_quarantine(
+        &mut self,
+        job_id: JobId,
+        attempt: u32,
+        write_checkpoint: bool,
+        input: &IngestInputV1,
+        actor: ActorId,
+        retained: Option<StoredObjectV1>,
+        quarantine: IntakeQuarantineV1,
+        outcome: IngestOutcome,
+        preexisting_vault_digests: Vec<Sha256Digest>,
+    ) -> Result<IngestReceipt> {
+        use crate::store::ingest_repository::{JobCheckpointCommand, QuarantinedIntakeCommand};
+
+        if write_checkpoint && let Some(original) = &retained {
+            let checkpoint = IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::ProcessingComplete {
+                    original: original.clone(),
+                    candidate: ProcessingCandidateV1::Quarantined {
+                        outcome,
+                        quarantine: quarantine.clone(),
+                    },
+                },
+            };
+            self.store.job_checkpoint_with_audit(JobCheckpointCommand {
+                job_id,
+                project_id: input.project_id,
+                actor: actor.clone(),
+                attempt,
+                checkpoint,
+                now_ms: checked_jcs_time(self.clock.now_unix_ms())?,
+                audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
+            })?;
+            self.faults
+                .inject(super::FaultPoint::AfterCheckpointCommit)?;
+        }
+        self.faults
+            .inject(super::FaultPoint::BeforeAuthoritativeCommit)?;
+        let receipt = self
+            .store
+            .quarantined_intake_commit(QuarantinedIntakeCommand {
+                job_id,
+                project_id: input.project_id,
+                attempt,
+                actor,
+                idempotency_key: input.idempotency_key.clone(),
+                source_display: input.source_display.clone(),
+                content_sha256: input.content_sha256,
+                byte_length: input.byte_length,
+                retained,
+                quarantine,
+                outcome,
+                ingest_event_id: IngestEventId::from_uuid(self.ids.next_uuid()),
+                source_record_id: self.ids.next_uuid().hyphenated().to_string(),
+                now_ms: checked_jcs_time(self.clock.now_unix_ms())?,
+                preexisting_vault_digests,
+                audit_event_ids: [
+                    super::AuditEventId::from_uuid(self.ids.next_uuid()),
+                    super::AuditEventId::from_uuid(self.ids.next_uuid()),
+                ],
+            })?;
+        self.faults
+            .inject(super::FaultPoint::AfterAuthoritativeCommit)?;
+        Ok(receipt)
+    }
+
+    fn complete_accepted(
+        &mut self,
+        job_id: JobId,
+        attempt: u32,
+        write_checkpoint: bool,
+        input: &IngestInputV1,
+        actor: ActorId,
+        original: StoredObjectV1,
+        manifest_object: StoredObjectV1,
+        manifest: EvidenceManifestV1,
+        mut preexisting_vault_digests: Vec<Sha256Digest>,
+    ) -> Result<IngestReceipt> {
+        use crate::store::ingest_repository::{AcceptedIntakeCommand, JobCheckpointCommand};
+
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::ProcessingComplete {
+                original: original.clone(),
+                candidate: ProcessingCandidateV1::Accepted {
+                    manifest: manifest.clone(),
+                    manifest_object: manifest_object.clone(),
+                },
+            },
+        };
+        if write_checkpoint {
+            self.store.job_checkpoint_with_audit(JobCheckpointCommand {
+                job_id,
+                project_id: input.project_id,
+                actor: actor.clone(),
+                attempt,
+                checkpoint,
+                now_ms: checked_jcs_time(self.clock.now_unix_ms())?,
+                audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
+            })?;
+            self.faults
+                .inject(super::FaultPoint::AfterCheckpointCommit)?;
+        }
+        self.faults
+            .inject(super::FaultPoint::BeforeAuthoritativeCommit)?;
+        preexisting_vault_digests.sort_unstable();
+        preexisting_vault_digests.dedup();
+        let receipt = self.store.accepted_intake_commit(AcceptedIntakeCommand {
+            job_id,
+            project_id: input.project_id,
+            attempt,
+            actor,
+            idempotency_key: input.idempotency_key.clone(),
+            source_display: input.source_display.clone(),
+            original,
+            manifest_object,
+            manifest: manifest.clone(),
+            pages: manifest.pages.clone(),
+            ingest_event_id: IngestEventId::from_uuid(self.ids.next_uuid()),
+            source_record_id: self.ids.next_uuid().hyphenated().to_string(),
+            evidence_id: EvidenceId::from_uuid(self.ids.next_uuid()),
+            now_ms: checked_jcs_time(self.clock.now_unix_ms())?,
+            preexisting_vault_digests,
+            audit_event_ids: [
+                super::AuditEventId::from_uuid(self.ids.next_uuid()),
+                super::AuditEventId::from_uuid(self.ids.next_uuid()),
+                super::AuditEventId::from_uuid(self.ids.next_uuid()),
+            ],
+        })?;
+        self.faults
+            .inject(super::FaultPoint::AfterAuthoritativeCommit)?;
+        Ok(receipt)
+    }
+
+    pub fn resume(&mut self, job_id: JobId, actor: ActorId) -> Result<ResumeReceipt> {
+        use crate::store::ingest_repository::{
+            JobRecoverCommand, JobRecoveryOutcome, ReplayAttemptCommand,
+        };
+
+        let audit = self.verify_audit_chain()?;
+        if !audit.valid {
+            return Err(HeleosError::Integrity);
+        }
+        let now_ms = checked_jcs_time(self.clock.now_unix_ms())?;
+        let recovery = self
+            .store
+            .job_recover_expired_attempt_with_audit(JobRecoverCommand {
+                job_id,
+                actor: actor.clone(),
+                lease_owner: self.ids.next_uuid(),
+                now_ms,
+                expected_probe_provenance: self.probe_provenance.clone(),
+                interrupted_event_id: IngestEventId::from_uuid(self.ids.next_uuid()),
+                audit_event_ids: [
+                    super::AuditEventId::from_uuid(self.ids.next_uuid()),
+                    super::AuditEventId::from_uuid(self.ids.next_uuid()),
+                ],
+            })?;
+        match recovery {
+            JobRecoveryOutcome::DeadlineExpired => Err(HeleosError::Timeout),
+            JobRecoveryOutcome::AttemptLimit => Err(HeleosError::ResourceLimit),
+            JobRecoveryOutcome::Terminal(record) => {
+                let IngestCheckpointPhaseV1::Terminal { receipt } = &record.checkpoint.phase else {
+                    return Err(HeleosError::Integrity);
+                };
+                verify_receipt_objects(self.store, self.vault, record.input.project_id, receipt)?;
+                let receipt = self.store.replay_attempt_commit(ReplayAttemptCommand {
+                    authoritative_job_id: record.job_id,
+                    project_id: record.input.project_id,
+                    idempotency_key: record.input.idempotency_key,
+                    actor,
+                    source_display: None,
+                    ingest_event_id: None,
+                    source_record_id: None,
+                    preexisting_vault_digests: Vec::new(),
+                    now_ms,
+                    audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
+                })?;
+                Ok(ResumeReceipt {
+                    job_id,
+                    resumed_attempt: None,
+                    interrupted_event_id: None,
+                    receipt,
+                })
+            }
+            JobRecoveryOutcome::Started {
+                record,
+                interrupted_event_id,
+            } => {
+                let resumed_attempt = record.attempt;
+                let project_id = record.input.project_id;
+                let failure_actor = actor.clone();
+                let result = self
+                    .faults
+                    .inject(super::FaultPoint::AfterJobStart)
+                    .and_then(|()| self.resume_running_record(record, actor));
+                let receipt = self.resolve_running_result(
+                    job_id,
+                    project_id,
+                    resumed_attempt,
+                    failure_actor,
+                    result,
+                )?;
+                Ok(ResumeReceipt {
+                    job_id,
+                    resumed_attempt: Some(resumed_attempt),
+                    interrupted_event_id,
+                    receipt,
+                })
+            }
+        }
+    }
+
+    pub fn verify_audit_chain(&self) -> Result<super::AuditChainReport> {
+        super::FoundationReader::new(self.store, self.vault).verify_audit_chain()
+    }
+
+    pub fn evidence_manifest_for_revision(
+        &self,
+        revision: RevisionId,
+    ) -> Result<EvidenceManifestReceipt> {
+        super::FoundationReader::new(self.store, self.vault)
+            .evidence_manifest_for_revision(revision)
+    }
+
+    pub fn inspect_foundation(&self, project: ProjectId) -> Result<super::FoundationInspection> {
+        super::FoundationReader::new(self.store, self.vault).inspect_foundation(project)
+    }
+
+    fn resume_running_record(
+        &mut self,
+        record: crate::store::ingest_repository::JobRecord,
+        actor: ActorId,
+    ) -> Result<IngestReceipt> {
+        let job_id = record.job_id;
+        let attempt = record.attempt;
+        let input = record.input;
+        match record.checkpoint.phase {
+            IngestCheckpointPhaseV1::PreflightRejected {
+                content_sha256,
+                byte_length,
+                quarantine,
+            } => {
+                if content_sha256 != input.content_sha256 || byte_length != input.byte_length {
+                    return Err(HeleosError::Integrity);
+                }
+                let preexisting = match quarantine.reason {
+                    IntakeQuarantineReasonV1::ProjectAssociationQuota => {
+                        let digest = content_sha256.ok_or(HeleosError::Integrity)?;
+                        drop(open_checkpoint_object(
+                            self.vault,
+                            &StoredObjectV1 {
+                                digest,
+                                byte_length,
+                                vault_key: crate::Vault::object_key(digest),
+                            },
+                        )?);
+                        vec![digest]
+                    }
+                    IntakeQuarantineReasonV1::InputBytes { .. }
+                    | IntakeQuarantineReasonV1::OriginalRetentionQuota => Vec::new(),
+                    _ => return Err(HeleosError::Integrity),
+                };
+                self.complete_quarantine(
+                    job_id,
+                    attempt,
+                    false,
+                    &input,
+                    actor,
+                    None,
+                    quarantine,
+                    IngestOutcome::QuarantinedLimit,
+                    preexisting,
+                )
+            }
+            IngestCheckpointPhaseV1::VaultPublished { original } => {
+                self.resume_vault_published(job_id, attempt, &input, actor, original)
+            }
+            IngestCheckpointPhaseV1::ProcessingComplete {
+                original,
+                candidate,
+            } => {
+                drop(open_checkpoint_object(self.vault, &original)?);
+                let quota = self
+                    .store
+                    .quota_snapshot(input.project_id, Some(original.digest))?;
+                if quota.admission.is_some() {
+                    return self.resume_admitted_original(
+                        job_id,
+                        attempt,
+                        &input,
+                        actor,
+                        original,
+                        quota,
+                        Some(candidate),
+                    );
+                }
+                match candidate {
+                    ProcessingCandidateV1::Accepted {
+                        manifest,
+                        manifest_object,
+                    } => {
+                        if manifest.original.sha256 != original.digest
+                            || manifest.original.byte_length != original.byte_length
+                            || manifest.probe_provenance != input.expected_probe_provenance
+                            || Sha256Digest::hash_reader(manifest.canonical_bytes()?.as_slice())?
+                                != manifest_object.digest
+                        {
+                            return Err(HeleosError::Integrity);
+                        }
+                        drop(open_checkpoint_object(self.vault, &manifest_object)?);
+                        self.complete_accepted(
+                            job_id,
+                            attempt,
+                            false,
+                            &input,
+                            actor,
+                            original.clone(),
+                            manifest_object.clone(),
+                            manifest,
+                            vec![original.digest, manifest_object.digest],
+                        )
+                    }
+                    ProcessingCandidateV1::Quarantined {
+                        outcome,
+                        quarantine,
+                    } => {
+                        if quarantine.probe_provenance.as_ref()
+                            != Some(&input.expected_probe_provenance)
+                            || outcome != quarantine_ingest_outcome(&quarantine.reason)
+                        {
+                            return Err(HeleosError::Integrity);
+                        }
+                        self.complete_quarantine(
+                            job_id,
+                            attempt,
+                            false,
+                            &input,
+                            actor,
+                            Some(original.clone()),
+                            quarantine,
+                            outcome,
+                            vec![original.digest],
+                        )
+                    }
+                }
+            }
+            IngestCheckpointPhaseV1::Terminal { .. } => Err(HeleosError::Integrity),
+        }
+    }
+
+    fn resume_admitted_original(
+        &mut self,
+        job_id: JobId,
+        attempt: u32,
+        input: &IngestInputV1,
+        actor: ActorId,
+        original: StoredObjectV1,
+        quota: crate::store::ingest_repository::QuotaSnapshot,
+        existing_candidate: Option<ProcessingCandidateV1>,
+    ) -> Result<IngestReceipt> {
+        let admission = quota.admission.ok_or(HeleosError::Integrity)?;
+        if admission.digest != original.digest
+            || admission.byte_length != original.byte_length
+            || admission.media_type != super::evidence::PDF_MEDIA_TYPE
+            || admission.vault_key != original.vault_key
+        {
+            return Err(HeleosError::Integrity);
+        }
+        match admission.admission_state.as_str() {
+            "quarantined" => {
+                let quarantine = admission.quarantine.ok_or(HeleosError::Integrity)?;
+                if !matches!(
+                    quarantine.reason,
+                    IntakeQuarantineReasonV1::Pdf(_)
+                        | IntakeQuarantineReasonV1::EvidenceManifestQuota
+                ) || quarantine.probe_provenance.as_ref()
+                    != Some(&input.expected_probe_provenance)
+                {
+                    return Err(HeleosError::Integrity);
+                }
+                let overall_debit = if quota.project_overall_accounted {
+                    0
+                } else {
+                    original.byte_length
+                };
+                let quarantine_debit = if quota.project_quarantine_accounted {
+                    0
+                } else {
+                    original.byte_length
+                };
+                if !prospective_fits(
+                    quota.project_overall_bytes,
+                    overall_debit,
+                    PROJECT_OVERALL_QUOTA_BYTES,
+                )? || !prospective_fits(
+                    quota.project_quarantine_bytes,
+                    quarantine_debit,
+                    PROJECT_QUARANTINE_QUOTA_BYTES,
+                )? {
+                    return self.complete_running_project_association_quota(
+                        job_id,
+                        attempt,
+                        input,
+                        actor,
+                        original.digest,
+                    );
+                }
+                let outcome = quarantine_ingest_outcome(&quarantine.reason);
+                let desired_candidate = ProcessingCandidateV1::Quarantined {
+                    outcome,
+                    quarantine: quarantine.clone(),
+                };
+                self.complete_quarantine(
+                    job_id,
+                    attempt,
+                    existing_candidate.as_ref() != Some(&desired_candidate),
+                    input,
+                    actor,
+                    Some(original.clone()),
+                    quarantine,
+                    outcome,
+                    vec![original.digest],
+                )
+            }
+            "accepted" => {
+                if admission.quarantine.is_some() {
+                    return Err(HeleosError::Integrity);
+                }
+                let (_, revision_id) = crate::canonical_document_ids(original.digest);
+                let evidence = super::FoundationReader::new(self.store, self.vault)
+                    .evidence_manifest_for_revision(revision_id)
+                    .map_err(|error| match error {
+                        HeleosError::NotFound => HeleosError::Integrity,
+                        other => other,
+                    })?;
+                if evidence.manifest.original.sha256 != original.digest
+                    || evidence.manifest.original.byte_length != original.byte_length
+                    || evidence.original_vault_key != original.vault_key
+                    || evidence.manifest.probe_provenance != input.expected_probe_provenance
+                {
+                    return Err(HeleosError::Integrity);
+                }
+                let manifest_object = StoredObjectV1 {
+                    digest: evidence.manifest_content_sha256,
+                    byte_length: evidence.manifest_byte_length,
+                    vault_key: evidence.manifest_vault_key.clone(),
+                };
+                let manifest_quota = self
+                    .store
+                    .quota_snapshot(input.project_id, Some(manifest_object.digest))?;
+                let original_debit = if quota.project_overall_accounted {
+                    0
+                } else {
+                    original.byte_length
+                };
+                let manifest_overall_debit = if manifest_quota.project_overall_accounted {
+                    0
+                } else {
+                    manifest_object.byte_length
+                };
+                let manifest_evidence_debit = if manifest_quota.project_evidence_accounted {
+                    0
+                } else {
+                    manifest_object.byte_length
+                };
+                let overall_debit = original_debit
+                    .checked_add(manifest_overall_debit)
+                    .ok_or(HeleosError::Integrity)?;
+                if !prospective_fits(
+                    quota.project_overall_bytes,
+                    overall_debit,
+                    PROJECT_OVERALL_QUOTA_BYTES,
+                )? || !prospective_fits(
+                    manifest_quota.project_evidence_bytes,
+                    manifest_evidence_debit,
+                    PROJECT_EVIDENCE_QUOTA_BYTES,
+                )? {
+                    return self.complete_running_project_association_quota(
+                        job_id,
+                        attempt,
+                        input,
+                        actor,
+                        original.digest,
+                    );
+                }
+                let desired_candidate = ProcessingCandidateV1::Accepted {
+                    manifest: evidence.manifest.clone(),
+                    manifest_object: manifest_object.clone(),
+                };
+                self.complete_accepted(
+                    job_id,
+                    attempt,
+                    existing_candidate.as_ref() != Some(&desired_candidate),
+                    input,
+                    actor,
+                    original.clone(),
+                    manifest_object.clone(),
+                    evidence.manifest,
+                    vec![original.digest, manifest_object.digest],
+                )
+            }
+            _ => Err(HeleosError::Integrity),
+        }
+    }
+
+    fn complete_running_project_association_quota(
+        &mut self,
+        job_id: JobId,
+        attempt: u32,
+        input: &IngestInputV1,
+        actor: ActorId,
+        digest: Sha256Digest,
+    ) -> Result<IngestReceipt> {
+        use crate::store::ingest_repository::JobCheckpointCommand;
+
+        let quarantine = IntakeQuarantineV1 {
+            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+            reason: IntakeQuarantineReasonV1::ProjectAssociationQuota,
+            probe_provenance: None,
+        };
+        self.store.job_checkpoint_with_audit(JobCheckpointCommand {
+            job_id,
+            project_id: input.project_id,
+            actor: actor.clone(),
+            attempt,
+            checkpoint: IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::PreflightRejected {
+                    content_sha256: Some(digest),
+                    byte_length: input.byte_length,
+                    quarantine: quarantine.clone(),
+                },
+            },
+            now_ms: checked_jcs_time(self.clock.now_unix_ms())?,
+            audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
+        })?;
+        self.faults
+            .inject(super::FaultPoint::AfterCheckpointCommit)?;
+        self.complete_quarantine(
+            job_id,
+            attempt,
+            false,
+            input,
+            actor,
+            None,
+            quarantine,
+            IngestOutcome::QuarantinedLimit,
+            vec![digest],
+        )
+    }
+
+    fn resume_vault_published(
+        &mut self,
+        job_id: JobId,
+        attempt: u32,
+        input: &IngestInputV1,
+        actor: ActorId,
+        original: StoredObjectV1,
+    ) -> Result<IngestReceipt> {
+        let verified = open_checkpoint_object(self.vault, &original)?;
+        let original_quota = self
+            .store
+            .quota_snapshot(input.project_id, Some(original.digest))?;
+        if original_quota.admission.is_some() {
+            drop(verified);
+            return self.resume_admitted_original(
+                job_id,
+                attempt,
+                input,
+                actor,
+                original,
+                original_quota,
+                None,
+            );
+        }
+        let (_, revision_id) = crate::canonical_document_ids(original.digest);
+        let inspection = match self
+            .probe
+            .probe(verified, revision_id, PdfLimits::default())?
+        {
+            crate::PdfProbeOutcome::Accepted(inspection) => inspection,
+            crate::PdfProbeOutcome::Quarantined(pdf_quarantine) => {
+                if pdf_quarantine.content_sha256 != original.digest
+                    || pdf_quarantine.byte_length != original.byte_length
+                    || pdf_quarantine.provenance != self.probe_provenance
+                {
+                    return Err(HeleosError::Integrity);
+                }
+                let outcome = pdf_quarantine.ingest_outcome();
+                return self.complete_quarantine(
+                    job_id,
+                    attempt,
+                    true,
+                    input,
+                    actor,
+                    Some(original.clone()),
+                    IntakeQuarantineV1 {
+                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                        reason: IntakeQuarantineReasonV1::Pdf(pdf_quarantine.reason),
+                        probe_provenance: Some(pdf_quarantine.provenance),
+                    },
+                    outcome,
+                    vec![original.digest],
+                );
+            }
+        };
+        if inspection.revision_id != revision_id
+            || inspection.content_sha256 != original.digest
+            || inspection.byte_length != original.byte_length
+            || inspection.provenance != self.probe_provenance
+        {
+            return Err(HeleosError::Integrity);
+        }
+        let manifest = EvidenceManifestV1::new(
+            original.digest,
+            original.byte_length,
+            inspection.pages,
+            inspection.provenance,
+        )?;
+        let manifest_bytes = manifest.canonical_bytes()?;
+        let manifest_length =
+            u64::try_from(manifest_bytes.len()).map_err(|_| HeleosError::Integrity)?;
+        let manifest_digest = Sha256Digest::hash_reader(manifest_bytes.as_slice())?;
+        let manifest_quota = self
+            .store
+            .quota_snapshot(input.project_id, Some(manifest_digest))?;
+        if let Some(admission) = &manifest_quota.admission
+            && (admission.digest != manifest_digest
+                || admission.byte_length != manifest_length
+                || admission.media_type != super::evidence::EVIDENCE_MANIFEST_MEDIA_TYPE
+                || admission.admission_state != "accepted"
+                || admission.vault_key != crate::Vault::object_key(manifest_digest)
+                || admission.quarantine.is_some())
+        {
+            return Err(HeleosError::Integrity);
+        }
+        let manifest_preexisting = match self.vault.open_verified(manifest_digest) {
+            Ok(verified) => {
+                if verified.byte_length() != manifest_length
+                    || verified.vault_key() != crate::Vault::object_key(manifest_digest)
+                {
+                    return Err(HeleosError::Integrity);
+                }
+                true
+            }
+            Err(HeleosError::NotFound)
+                if manifest_quota.admission.is_none()
+                    && !manifest_quota.store_overall_accounted
+                    && !manifest_quota.store_evidence_accounted =>
+            {
+                false
+            }
+            Err(HeleosError::NotFound) => return Err(HeleosError::Integrity),
+            Err(error) => return Err(error),
+        };
+        let original_project_debit = if original_quota.project_overall_accounted {
+            0
+        } else {
+            original.byte_length
+        };
+        let manifest_project_debit = if manifest_quota.project_overall_accounted {
+            0
+        } else {
+            manifest_length
+        };
+        let manifest_project_evidence_debit = if manifest_quota.project_evidence_accounted {
+            0
+        } else {
+            manifest_length
+        };
+        let project_debit = original_project_debit
+            .checked_add(manifest_project_debit)
+            .ok_or(HeleosError::Integrity)?;
+        let manifest_store_overall_debit = if manifest_preexisting {
+            0
+        } else {
+            manifest_length
+        };
+        let manifest_store_evidence_debit = if manifest_quota.store_evidence_accounted {
+            0
+        } else {
+            manifest_length
+        };
+        if !prospective_fits(
+            original_quota.project_overall_bytes,
+            project_debit,
+            PROJECT_OVERALL_QUOTA_BYTES,
+        )? || !prospective_fits(
+            manifest_quota.project_evidence_bytes,
+            manifest_project_evidence_debit,
+            PROJECT_EVIDENCE_QUOTA_BYTES,
+        )? || !prospective_fits(
+            original_quota.store_overall_bytes,
+            manifest_store_overall_debit,
+            STORE_OVERALL_QUOTA_BYTES,
+        )? || !prospective_fits(
+            manifest_quota.store_evidence_bytes,
+            manifest_store_evidence_debit,
+            STORE_EVIDENCE_QUOTA_BYTES,
+        )? {
+            return self.complete_quarantine(
+                job_id,
+                attempt,
+                true,
+                input,
+                actor,
+                Some(original.clone()),
+                IntakeQuarantineV1 {
+                    schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                    reason: IntakeQuarantineReasonV1::EvidenceManifestQuota,
+                    probe_provenance: Some(self.probe_provenance.clone()),
+                },
+                IngestOutcome::QuarantinedLimit,
+                vec![original.digest],
+            );
+        }
+        let projected_project = original_quota
+            .project_overall_bytes
+            .checked_add(original_project_debit)
+            .ok_or(HeleosError::Integrity)?;
+        let retain_budget = remaining_min(&[
+            (PROJECT_OVERALL_QUOTA_BYTES, projected_project),
+            (
+                STORE_OVERALL_QUOTA_BYTES,
+                original_quota.store_overall_bytes,
+            ),
+            (
+                PROJECT_EVIDENCE_QUOTA_BYTES,
+                manifest_quota.project_evidence_bytes,
+            ),
+            (
+                STORE_EVIDENCE_QUOTA_BYTES,
+                manifest_quota.store_evidence_bytes,
+            ),
+        ])?;
+        let stored_manifest = match self.vault.put_reader(
+            manifest_bytes.as_slice(),
+            crate::VaultWriteBudget::new(manifest_length, retain_budget)?,
+        )? {
+            crate::PutOutcome::Stored(stored) => stored,
+            crate::PutOutcome::QuotaRejected {
+                digest,
+                byte_length,
+            } => {
+                if digest != manifest_digest || byte_length != manifest_length {
+                    return Err(HeleosError::Integrity);
+                }
+                return self.complete_quarantine(
+                    job_id,
+                    attempt,
+                    true,
+                    input,
+                    actor,
+                    Some(original.clone()),
+                    IntakeQuarantineV1 {
+                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                        reason: IntakeQuarantineReasonV1::EvidenceManifestQuota,
+                        probe_provenance: Some(self.probe_provenance.clone()),
+                    },
+                    IngestOutcome::QuarantinedLimit,
+                    vec![original.digest],
+                );
+            }
+        };
+        if stored_manifest.newly_published == manifest_preexisting {
+            return Err(HeleosError::Integrity);
+        }
+        let manifest_object = StoredObjectV1::from(&stored_manifest);
+        let mut preexisting = vec![original.digest];
+        if !stored_manifest.newly_published {
+            preexisting.push(stored_manifest.digest);
+        }
+        self.complete_accepted(
+            job_id,
+            attempt,
+            true,
+            input,
+            actor,
+            original,
+            manifest_object,
+            manifest,
+            preexisting,
+        )
+    }
+}
+
+fn verify_receipt_objects(
+    store: &crate::Store,
+    vault: &crate::Vault,
+    project_id: ProjectId,
+    receipt: &IngestReceipt,
+) -> Result<Vec<Sha256Digest>> {
+    receipt.validate()?;
+    let mut required = Vec::with_capacity(2);
+    if let Some(original) = receipt.content_sha256 {
+        let requires_original = receipt.evidence_manifest.is_some()
+            || receipt.quarantine.as_ref().is_some_and(|quarantine| {
+                matches!(
+                    quarantine.reason,
+                    IntakeQuarantineReasonV1::Pdf(_)
+                        | IntakeQuarantineReasonV1::EvidenceManifestQuota
+                        | IntakeQuarantineReasonV1::ProjectAssociationQuota
+                )
+            });
+        if requires_original {
+            let expected_original = StoredObjectV1 {
+                digest: original,
+                byte_length: receipt.byte_length,
+                vault_key: crate::Vault::object_key(original),
+            };
+            let verified = open_checkpoint_object(vault, &expected_original)?;
+            drop(verified);
+            if let Some(quarantine) = &receipt.quarantine
+                && matches!(
+                    quarantine.reason,
+                    IntakeQuarantineReasonV1::Pdf(_)
+                        | IntakeQuarantineReasonV1::EvidenceManifestQuota
+                )
+            {
+                let admission = store
+                    .quota_snapshot(project_id, Some(original))?
+                    .admission
+                    .ok_or(HeleosError::Integrity)?;
+                if admission.digest != original
+                    || admission.byte_length != receipt.byte_length
+                    || admission.media_type != super::evidence::PDF_MEDIA_TYPE
+                    || admission.admission_state != "quarantined"
+                    || admission.vault_key != expected_original.vault_key
+                    || admission.quarantine.as_ref() != Some(quarantine)
+                {
+                    return Err(HeleosError::Integrity);
+                }
+            }
+            required.push(original);
+        }
+    }
+    if let Some(expected) = &receipt.evidence_manifest {
+        let manifest_object = StoredObjectV1 {
+            digest: expected.manifest_content_sha256,
+            byte_length: expected.manifest_byte_length,
+            vault_key: expected.manifest_vault_key.clone(),
+        };
+        let verified = open_checkpoint_object(vault, &manifest_object)?;
+        drop(verified);
+        let revision = receipt.revision_id.ok_or(HeleosError::Integrity)?;
+        let current =
+            super::FoundationReader::new(store, vault).evidence_manifest_for_revision(revision)?;
+        if current.manifest != expected.manifest
+            || current.manifest_content_sha256 != expected.manifest_content_sha256
+            || current.manifest_byte_length != expected.manifest_byte_length
+            || current.manifest_media_type != expected.manifest_media_type
+            || current.manifest_vault_key != expected.manifest_vault_key
+            || current.original_vault_key != expected.original_vault_key
+        {
+            return Err(HeleosError::Integrity);
+        }
+        if !expected
+            .lineages
+            .iter()
+            .any(|lineage| lineage.project_id == project_id)
+            || !lineages_are_subset(&expected.lineages, &current.lineages)
+        {
+            return Err(HeleosError::Integrity);
+        }
+        let original = receipt.content_sha256.ok_or(HeleosError::Integrity)?;
+        let original_admission = store
+            .quota_snapshot(project_id, Some(original))?
+            .admission
+            .ok_or(HeleosError::Integrity)?;
+        if original_admission.digest != original
+            || original_admission.byte_length != receipt.byte_length
+            || original_admission.media_type != super::evidence::PDF_MEDIA_TYPE
+            || original_admission.admission_state != "accepted"
+            || original_admission.vault_key != expected.original_vault_key
+            || original_admission.quarantine.is_some()
+        {
+            return Err(HeleosError::Integrity);
+        }
+        let manifest_admission = store
+            .quota_snapshot(project_id, Some(expected.manifest_content_sha256))?
+            .admission
+            .ok_or(HeleosError::Integrity)?;
+        if manifest_admission.digest != expected.manifest_content_sha256
+            || manifest_admission.byte_length != expected.manifest_byte_length
+            || manifest_admission.media_type != super::evidence::EVIDENCE_MANIFEST_MEDIA_TYPE
+            || manifest_admission.admission_state != "accepted"
+            || manifest_admission.vault_key != expected.manifest_vault_key
+            || manifest_admission.quarantine.is_some()
+        {
+            return Err(HeleosError::Integrity);
+        }
+        required.push(expected.manifest_content_sha256);
+    }
+    required.sort_unstable();
+    required.dedup();
+    Ok(required)
+}
+
+fn lineages_are_subset(
+    expected: &[super::evidence::EvidenceManifestLineage],
+    current: &[super::evidence::EvidenceManifestLineage],
+) -> bool {
+    let key = |lineage: &super::evidence::EvidenceManifestLineage| {
+        (
+            *lineage.project_id.as_uuid().as_bytes(),
+            *lineage.evidence_id.as_uuid().as_bytes(),
+            *lineage.originating_job_id.as_uuid().as_bytes(),
+        )
+    };
+    let mut current_index = 0_usize;
+    for expected_lineage in expected {
+        let expected_key = key(expected_lineage);
+        while current_index < current.len() && key(&current[current_index]) < expected_key {
+            current_index += 1;
+        }
+        if current_index == current.len() || key(&current[current_index]) != expected_key {
+            return false;
+        }
+        current_index += 1;
+    }
+    true
+}
+
+fn open_checkpoint_object(
+    vault: &crate::Vault,
+    expected: &StoredObjectV1,
+) -> Result<crate::VerifiedObject> {
+    expected.validate()?;
+    let verified = match vault.open_verified(expected.digest) {
+        Ok(verified) => verified,
+        Err(HeleosError::NotFound | HeleosError::ResourceLimit) => {
+            return Err(HeleosError::Integrity);
+        }
+        Err(error) => return Err(error),
+    };
+    if verified.byte_length() != expected.byte_length || verified.vault_key() != expected.vault_key
+    {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(verified)
+}
+
+fn remaining_min(quotas: &[(u64, u64)]) -> Result<u64> {
+    quotas
+        .iter()
+        .map(|(limit, used)| limit.checked_sub(*used).ok_or(HeleosError::Integrity))
+        .collect::<Result<Vec<_>>>()?
+        .into_iter()
+        .min()
+        .ok_or(HeleosError::Integrity)
+}
+
+fn prospective_fits(used: u64, debit: u64, limit: u64) -> Result<bool> {
+    Ok(used.checked_add(debit).ok_or(HeleosError::Integrity)? <= limit)
+}
+
+fn checked_jcs_time(value: i64) -> Result<i64> {
+    if value < 0 || u64::try_from(value).map_err(|_| HeleosError::Integrity)? > JCS_SAFE_INTEGER_MAX
+    {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(value)
+}
+
+fn checked_jcs_add(value: i64, delta: i64) -> Result<i64> {
+    checked_jcs_time(value.checked_add(delta).ok_or(HeleosError::Integrity)?)
+}
+
+#[cfg(test)]
+mod tests {
+    use std::str::FromStr;
+
+    use uuid::Uuid;
+
+    use super::*;
+    use crate::{
+        EvidenceManifestLineage, PageMetadata, PageTransform, PageUnit, PdfProbeProvenance, page_id,
+    };
+
+    fn uuid(value: u128) -> Uuid {
+        Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)
+    }
+
+    fn provenance() -> PdfProbeProvenance {
+        PdfProbeProvenance {
+            parser_name: "fixture-parser".to_owned(),
+            parser_version: "1.0.0".to_owned(),
+            guest_wasm_sha256: Sha256Digest::from_bytes([2; 32]),
+            guest_source_tree_sha256: Sha256Digest::from_bytes([3; 32]),
+            guest_dependency_graph_sha256: Sha256Digest::from_bytes([4; 32]),
+            protocol_version: "heleos.pdf-probe/v1".to_owned(),
+        }
+    }
+
+    fn accepted_receipt() -> IngestReceipt {
+        accepted_receipt_with_pages(1)
+    }
+
+    fn accepted_receipt_with_pages(page_count: u32) -> IngestReceipt {
+        let content = Sha256Digest::from_bytes([1; 32]);
+        let manifest = EvidenceManifestV1::new(
+            content,
+            7,
+            (0..page_count)
+                .map(|index| PageMetadata {
+                    index,
+                    page_id: page_id(content, index),
+                    width_micropoints: 612_000_000,
+                    height_micropoints: 792_000_000,
+                    unit: PageUnit::Point,
+                    rotation_degrees: 0,
+                    transform: PageTransform {
+                        m11: 1,
+                        m12: 0,
+                        m21: 0,
+                        m22: -1,
+                        tx_micropoints: 0,
+                        ty_micropoints: 792_000_000,
+                    },
+                })
+                .collect(),
+            provenance(),
+        )
+        .expect("valid manifest");
+        let manifest_bytes = manifest.canonical_bytes().expect("canonical manifest");
+        let manifest_digest =
+            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest");
+        let (document_id, revision_id) = crate::canonical_document_ids(content);
+        let job_id = JobId::from_uuid(uuid(2));
+        IngestReceipt {
+            ingest_event_id: IngestEventId::from_uuid(uuid(1)),
+            authoritative_job_id: job_id,
+            attempt: 1,
+            outcome: IngestOutcome::AcceptedNew,
+            content_sha256: Some(content),
+            byte_length: 7,
+            quarantine: None,
+            document_id: Some(document_id),
+            revision_id: Some(revision_id),
+            sheet_ids: (0..page_count)
+                .map(|index| page_id(content, index))
+                .collect(),
+            evidence_manifest: Some(EvidenceManifestReceipt {
+                manifest,
+                manifest_content_sha256: manifest_digest,
+                manifest_byte_length: u64::try_from(manifest_bytes.len())
+                    .expect("manifest length fits"),
+                manifest_media_type: crate::EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
+                manifest_vault_key: crate::Vault::object_key(manifest_digest),
+                original_vault_key: crate::Vault::object_key(content),
+                lineages: vec![EvidenceManifestLineage {
+                    project_id: ProjectId::from_uuid(uuid(3)),
+                    evidence_id: EvidenceId::from_uuid(uuid(4)),
+                    originating_job_id: job_id,
+                }],
+            }),
+            preexisting_vault_digests: Vec::new(),
+        }
+    }
+
+    fn valid_input() -> IngestInputV1 {
+        IngestInputV1 {
+            schema: INGEST_INPUT_SCHEMA_V1.to_owned(),
+            project_id: ProjectId::from_uuid(uuid(3)),
+            kind: INGEST_KIND.to_owned(),
+            idempotency_key: IdempotencyKey::try_from("request-one").expect("key"),
+            content_sha256: Some(Sha256Digest::from_bytes([1; 32])),
+            byte_length: 7,
+            source_display: "utf8:file.pdf".to_owned(),
+            requested_limits: PdfLimits::default().into(),
+            expected_probe_provenance: provenance(),
+            deadline_profile_ms: JOB_DEADLINE_MS as u64,
+        }
+    }
+
+    #[test]
+    fn accepted_receipt_binds_sheets_length_and_preexisting_object_authority() {
+        // Break caught: valid-looking receipt scalars drifting from manifest/vault authority.
+        let mut receipt = accepted_receipt();
+        receipt.sheet_ids[0] = SheetId::from(Sha256Digest::from_bytes([8; 32]));
+        assert!(receipt.validate().is_err());
+
+        let mut receipt = accepted_receipt();
+        receipt.byte_length += 1;
+        assert!(receipt.validate().is_err());
+
+        let mut receipt = accepted_receipt();
+        receipt.preexisting_vault_digests = vec![Sha256Digest::from_bytes([9; 32])];
+        assert!(receipt.validate().is_err());
+
+        let mut duplicate = accepted_receipt();
+        duplicate.outcome = IngestOutcome::AcceptedDuplicate;
+        let original = duplicate.content_sha256.expect("accepted original");
+        let manifest = duplicate
+            .evidence_manifest
+            .as_ref()
+            .expect("accepted evidence")
+            .manifest_content_sha256;
+        duplicate.preexisting_vault_digests = vec![original, manifest];
+        duplicate.preexisting_vault_digests.sort_unstable();
+        duplicate.validate().expect("exact duplicate authority set");
+        duplicate.preexisting_vault_digests.pop();
+        assert!(matches!(duplicate.validate(), Err(HeleosError::Integrity)));
+    }
+
+    #[test]
+    fn replay_receipts_require_the_exact_call_relative_preexisting_object_set() {
+        // Break caught: a replay receipt omitting an already-published authoritative object.
+        let mut accepted = accepted_receipt();
+        accepted.outcome = IngestOutcome::IdempotentReplay;
+        let original = accepted.content_sha256.expect("accepted original digest");
+        let manifest = accepted
+            .evidence_manifest
+            .as_ref()
+            .expect("accepted manifest receipt")
+            .manifest_content_sha256;
+        accepted.preexisting_vault_digests = vec![original, manifest];
+        accepted.preexisting_vault_digests.sort_unstable();
+        accepted.validate().expect("exact accepted replay set");
+
+        for missing in [original, manifest] {
+            let mut receipt = accepted.clone();
+            receipt
+                .preexisting_vault_digests
+                .retain(|digest| *digest != missing);
+            assert!(matches!(receipt.validate(), Err(HeleosError::Integrity)));
+        }
+
+        for reason in [
+            IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic),
+            IntakeQuarantineReasonV1::EvidenceManifestQuota,
+        ] {
+            let mut retained = accepted_receipt();
+            let original = retained.content_sha256.expect("retained original digest");
+            retained.outcome = IngestOutcome::IdempotentReplay;
+            retained.quarantine = Some(IntakeQuarantineV1 {
+                schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                reason,
+                probe_provenance: Some(provenance()),
+            });
+            retained.document_id = None;
+            retained.revision_id = None;
+            retained.sheet_ids.clear();
+            retained.evidence_manifest = None;
+            retained.preexisting_vault_digests = vec![original];
+            retained.validate().expect("exact retained replay set");
+
+            retained.preexisting_vault_digests.clear();
+            assert!(matches!(retained.validate(), Err(HeleosError::Integrity)));
+        }
+
+        let mut association_denied = accepted_receipt();
+        let original = association_denied
+            .content_sha256
+            .expect("association-denied original digest");
+        association_denied.outcome = IngestOutcome::IdempotentReplay;
+        association_denied.quarantine = Some(IntakeQuarantineV1 {
+            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+            reason: IntakeQuarantineReasonV1::ProjectAssociationQuota,
+            probe_provenance: None,
+        });
+        association_denied.document_id = None;
+        association_denied.revision_id = None;
+        association_denied.sheet_ids.clear();
+        association_denied.evidence_manifest = None;
+        association_denied.preexisting_vault_digests = vec![original];
+        association_denied
+            .validate()
+            .expect("association-denied replay reports preexisting original");
+
+        association_denied.preexisting_vault_digests.clear();
+        assert!(matches!(
+            association_denied.validate(),
+            Err(HeleosError::Integrity)
+        ));
+
+        association_denied.outcome = IngestOutcome::QuarantinedLimit;
+        association_denied.preexisting_vault_digests = vec![original];
+        association_denied
+            .validate()
+            .expect("authoritative association denial reports existing original");
+        association_denied.preexisting_vault_digests.clear();
+        assert!(matches!(
+            association_denied.validate(),
+            Err(HeleosError::Integrity)
+        ));
+
+        association_denied.quarantine = Some(IntakeQuarantineV1 {
+            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+            reason: IntakeQuarantineReasonV1::OriginalRetentionQuota,
+            probe_provenance: None,
+        });
+        association_denied.preexisting_vault_digests = vec![original];
+        assert!(matches!(
+            association_denied.validate(),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn receipt_deserialization_stops_at_sheet_and_preexisting_digest_caps() {
+        // Break caught: public receipt arrays allocating beyond their finite authority sets.
+        let at_sheet_cap = accepted_receipt_with_pages(10_000);
+        let mut value = serde_json::to_value(&at_sheet_cap).expect("receipt JSON");
+        assert!(serde_json::from_value::<IngestReceipt>(value.clone()).is_ok());
+        let extra = value["sheet_ids"][9_999].clone();
+        value["sheet_ids"]
+            .as_array_mut()
+            .expect("sheet IDs array")
+            .push(extra);
+        let error = serde_json::from_value::<IngestReceipt>(value)
+            .expect_err("10,001st sheet ID must fail during sequence deserialization");
+        assert!(
+            error
+                .to_string()
+                .contains("sequence exceeds maximum of 10000")
+        );
+
+        let mut at_digest_cap = accepted_receipt();
+        let original = at_digest_cap.content_sha256.expect("original digest");
+        let manifest = at_digest_cap
+            .evidence_manifest
+            .as_ref()
+            .expect("manifest receipt")
+            .manifest_content_sha256;
+        at_digest_cap.preexisting_vault_digests = vec![original, manifest];
+        at_digest_cap.preexisting_vault_digests.sort_unstable();
+        let mut value = serde_json::to_value(&at_digest_cap).expect("receipt JSON");
+        assert!(serde_json::from_value::<IngestReceipt>(value.clone()).is_ok());
+        value["preexisting_vault_digests"]
+            .as_array_mut()
+            .expect("preexisting digest array")
+            .push(serde_json::json!(Sha256Digest::from_bytes([9; 32])));
+        let error = serde_json::from_value::<IngestReceipt>(value)
+            .expect_err("third preexisting digest must fail during sequence deserialization");
+        assert!(error.to_string().contains("sequence exceeds maximum of 2"));
+    }
+
+    #[test]
+    fn quarantine_receipt_binds_reason_outcome_and_digest_shape() {
+        // Break caught: authoritative quarantine reason disagreeing with event outcome/digest.
+        let mut receipt = accepted_receipt();
+        receipt.outcome = IngestOutcome::QuarantinedEncrypted;
+        receipt.quarantine = Some(IntakeQuarantineV1 {
+            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+            reason: IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic),
+            probe_provenance: Some(provenance()),
+        });
+        receipt.document_id = None;
+        receipt.revision_id = None;
+        receipt.sheet_ids.clear();
+        receipt.evidence_manifest = None;
+        assert!(receipt.validate().is_err());
+
+        let mut receipt = receipt;
+        receipt.outcome = IngestOutcome::QuarantinedLimit;
+        receipt.quarantine = Some(IntakeQuarantineV1 {
+            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+            reason: IntakeQuarantineReasonV1::InputBytes {
+                limit_bytes: PdfLimits::default().max_input_bytes,
+                observed_bytes: PdfLimits::default().max_input_bytes + 1,
+            },
+            probe_provenance: None,
+        });
+        assert!(receipt.validate().is_err());
+    }
+
+    #[test]
+    fn standalone_quarantine_reason_deserialization_enforces_input_bounds() {
+        // Break caught: bypassing the wrapper to deserialize an impossible input-cap reason.
+        let maximum = PdfLimits::default().max_input_bytes;
+        serde_json::from_value::<IntakeQuarantineReasonV1>(serde_json::json!({
+            "kind": "input_bytes",
+            "detail": {"limit_bytes": maximum, "observed_bytes": maximum + 1}
+        }))
+        .expect("exact standalone input-cap reason");
+
+        for hostile in [
+            serde_json::json!({
+                "kind": "input_bytes",
+                "detail": {"limit_bytes": maximum - 1, "observed_bytes": maximum + 1}
+            }),
+            serde_json::json!({
+                "kind": "input_bytes",
+                "detail": {"limit_bytes": maximum, "observed_bytes": maximum}
+            }),
+            serde_json::json!({
+                "kind": "input_bytes",
+                "detail": {"limit_bytes": maximum, "observed_bytes": JCS_SAFE_INTEGER_MAX + 1}
+            }),
+            serde_json::json!({
+                "kind": "original_retention_quota",
+                "detail": null
+            }),
+        ] {
+            assert!(serde_json::from_value::<IntakeQuarantineReasonV1>(hostile).is_err());
+        }
+    }
+
+    #[test]
+    fn accepted_receipt_rejects_an_original_larger_than_its_requested_input_limit() {
+        // Break caught: a receipt authorizing an accepted PDF impossible under its own limits.
+        let mut receipt = accepted_receipt();
+        let evidence = receipt
+            .evidence_manifest
+            .as_mut()
+            .expect("accepted evidence receipt");
+        let over_limit = PdfLimits::default().max_input_bytes + 1;
+        receipt.byte_length = over_limit;
+        evidence.manifest.original.byte_length = over_limit;
+        let bytes = crate::canonical_json(&evidence.manifest).expect("raw manifest JSON");
+        let digest = Sha256Digest::hash_reader(bytes.as_slice()).expect("manifest digest");
+        evidence.manifest_content_sha256 = digest;
+        evidence.manifest_byte_length = u64::try_from(bytes.len()).expect("manifest length");
+        evidence.manifest_vault_key = crate::Vault::object_key(digest);
+        assert!(matches!(receipt.validate(), Err(HeleosError::Integrity)));
+    }
+
+    #[test]
+    fn resume_receipt_binds_the_returned_receipt_to_the_resumed_attempt() {
+        // Break caught: resume claiming attempt N while returning attempt N-1 authority.
+        let receipt = accepted_receipt();
+        let resume = ResumeReceipt {
+            job_id: receipt.authoritative_job_id,
+            resumed_attempt: Some(2),
+            interrupted_event_id: Some(IngestEventId::from_uuid(uuid(9))),
+            receipt,
+        };
+        assert!(resume.validate().is_err());
+
+        for (resumed_attempt, interrupted_event_id, receipt_attempt) in [
+            (Some(1), None, 1),
+            (Some(2), Some(IngestEventId::from_uuid(uuid(10))), 2),
+            (None, None, 1),
+        ] {
+            let mut receipt = accepted_receipt();
+            receipt.outcome = IngestOutcome::IdempotentReplay;
+            receipt.attempt = receipt_attempt;
+            let resume = ResumeReceipt {
+                job_id: receipt.authoritative_job_id,
+                resumed_attempt,
+                interrupted_event_id,
+                receipt,
+            };
+            assert!(resume.validate().is_err());
+            let value = serde_json::to_value(&resume).expect("resume JSON");
+            assert!(serde_json::from_value::<ResumeReceipt>(value).is_err());
+        }
+    }
+
+    #[test]
+    fn nested_pdf_quarantine_reason_rejects_unknown_outer_fields() {
+        // Break caught: Task 6 strict wrapper inheriting permissive adjacent-enum decoding.
+        let value = serde_json::json!({
+            "schema": INTAKE_QUARANTINE_SCHEMA_V1,
+            "reason": {
+                "kind": "pdf",
+                "detail": {"kind": "bad_magic", "extra": true}
+            },
+            "probe_provenance": provenance(),
+        });
+        assert!(serde_json::from_value::<IntakeQuarantineV1>(value).is_err());
+    }
+
+    #[cfg(unix)]
+    #[test]
+    fn fifo_source_open_process_helper() {
+        let Some(path) = std::env::var_os("HELEOS_TEST_INTAKE_FIFO") else {
+            return;
+        };
+        assert!(matches!(
+            IntakeSource::open(Path::new(&path)),
+            Err(HeleosError::PolicyDenied)
+        ));
+    }
+
+    #[cfg(unix)]
+    #[test]
+    fn fifo_and_terminal_symlink_sources_fail_promptly_as_policy_denied() {
+        use std::os::unix::fs::symlink;
+        use std::process::Command;
+        use std::time::{Duration, Instant};
+
+        let root = tempfile::TempDir::new().expect("source fixture directory");
+        let fifo = root.path().join("source.fifo");
+        assert!(
+            Command::new("mkfifo")
+                .arg(&fifo)
+                .status()
+                .expect("run mkfifo")
+                .success()
+        );
+        let mut child = Command::new(std::env::current_exe().expect("test executable"))
+            .arg("--exact")
+            .arg("ingest::job::tests::fifo_source_open_process_helper")
+            .arg("--nocapture")
+            .env("HELEOS_TEST_INTAKE_FIFO", &fifo)
+            .spawn()
+            .expect("spawn FIFO open helper");
+        let deadline = Instant::now() + Duration::from_secs(2);
+        let status = loop {
+            if let Some(status) = child.try_wait().expect("poll FIFO helper") {
+                break Some(status);
+            }
+            if Instant::now() >= deadline {
+                child.kill().expect("kill blocked FIFO helper");
+                child.wait().expect("reap blocked FIFO helper");
+                break None;
+            }
+            std::thread::sleep(Duration::from_millis(10));
+        };
+        assert!(status.is_some_and(|status| status.success()));
+
+        let target = root.path().join("target.pdf");
+        std::fs::write(&target, b"%PDF").expect("write symlink target");
+        let link = root.path().join("link.pdf");
+        symlink(&target, &link).expect("create source symlink");
+        assert!(matches!(
+            IntakeSource::open(&link),
+            Err(HeleosError::PolicyDenied)
+        ));
+    }
+
+    #[cfg(unix)]
+    #[test]
+    fn unix_socket_source_fails_promptly_as_policy_denied() {
+        use std::os::unix::net::UnixListener;
+        use std::time::{Duration, Instant};
+
+        let root = tempfile::TempDir::new().expect("socket fixture directory");
+        let path = root.path().join("source.socket");
+        let _listener = UnixListener::bind(&path).expect("bind source socket");
+        let started = Instant::now();
+        assert!(matches!(
+            IntakeSource::open(&path),
+            Err(HeleosError::PolicyDenied)
+        ));
+        assert!(started.elapsed() < Duration::from_secs(2));
+    }
+
+    #[test]
+    fn private_persisted_dtos_reject_wrong_schema_and_object_keys() {
+        // Break caught: DB reconstruction accepting field-shaped but semantically alien JSON.
+        let budget = IngestBudgetV1::new(7);
+        let mut value = serde_json::to_value(budget).expect("budget JSON");
+        value["schema"] = serde_json::Value::String("wrong".to_owned());
+        assert!(serde_json::from_value::<IngestBudgetV1>(value).is_err());
+
+        let object = StoredObjectV1 {
+            digest: Sha256Digest::from_bytes([1; 32]),
+            byte_length: 7,
+            vault_key: "wrong".to_owned(),
+        };
+        let value = serde_json::to_value(object).expect("stored-object JSON");
+        assert!(serde_json::from_value::<StoredObjectV1>(value).is_err());
+
+        let event = IngestEventDetailV1::Conflict {
+            schema: "wrong".to_owned(),
+            authoritative_job_id: JobId::from_uuid(uuid(2)),
+            mismatching_fields: vec!["project_id".to_owned()],
+        };
+        let value = serde_json::to_value(event).expect("event JSON");
+        assert!(serde_json::from_value::<IngestEventDetailV1>(value).is_err());
+    }
+
+    #[test]
+    fn persisted_source_display_requires_the_exact_encoder_image() {
+        // Break caught: source-less resume accepting ambiguous/non-lossless display encodings.
+        for hostile in ["utf8:%61", "utf8:%ff", "utf8:.", "utf8:..", "unix-bytes:61"] {
+            let mut value = serde_json::to_value(valid_input()).expect("input JSON");
+            value["source_display"] = serde_json::Value::String(hostile.to_owned());
+            assert!(
+                serde_json::from_value::<IngestInputV1>(value).is_err(),
+                "accepted noncanonical safe display {hostile}"
+            );
+        }
+    }
+
+    #[test]
+    fn persisted_source_display_deserialization_enforces_the_4096_byte_cap() {
+        // Break caught: allocating an oversized persisted source marker before its field cap.
+        let mut at_cap = serde_json::to_value(valid_input()).expect("input JSON");
+        at_cap["source_display"] = serde_json::Value::String(format!("utf8:{}", "a".repeat(4091)));
+        assert!(serde_json::from_value::<IngestInputV1>(at_cap).is_ok());
+
+        let mut over_cap = serde_json::to_value(valid_input()).expect("input JSON");
+        over_cap["source_display"] =
+            serde_json::Value::String(format!("utf8:{}", "a".repeat(4092)));
+        let error = serde_json::from_value::<IngestInputV1>(over_cap)
+            .expect_err("4097-byte source marker must fail during string deserialization");
+        assert!(
+            error
+                .to_string()
+                .contains("string exceeds maximum of 4096 bytes")
+        );
+    }
+
+    #[test]
+    fn public_probe_provenance_rejects_unbounded_or_empty_text() {
+        // Break caught: nested probe identity bypassing strict public DTO validation.
+        let mut value = serde_json::to_value(provenance()).expect("provenance JSON");
+        value["parser_name"] = serde_json::Value::String(String::new());
+        assert!(serde_json::from_value::<PdfProbeProvenance>(value).is_err());
+        let mut value = serde_json::to_value(provenance()).expect("provenance JSON");
+        value["protocol_version"] = serde_json::Value::String("x".repeat(257));
+        assert!(serde_json::from_value::<PdfProbeProvenance>(value).is_err());
+    }
+
+    #[test]
+    fn task_six_evidence_rejects_zero_accepted_pages() {
+        // Break caught: applying Task 5's valid zero-page response to Task 6 accepted intake.
+        assert!(
+            EvidenceManifestV1::new(
+                Sha256Digest::from_bytes([1; 32]),
+                7,
+                Vec::new(),
+                provenance(),
+            )
+            .is_err()
+        );
+    }
+
+    #[test]
+    fn checkpoint_and_event_cross_fields_bind_lengths_provenance_and_terminal_authority() {
+        // Break caught: structurally valid persisted phases drifting from frozen job authority.
+        let quarantine = IntakeQuarantineV1 {
+            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+            reason: IntakeQuarantineReasonV1::InputBytes {
+                limit_bytes: PdfLimits::default().max_input_bytes,
+                observed_bytes: PdfLimits::default().max_input_bytes + 1,
+            },
+            probe_provenance: None,
+        };
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::PreflightRejected {
+                content_sha256: None,
+                byte_length: PdfLimits::default().max_input_bytes + 2,
+                quarantine: quarantine.clone(),
+            },
+        };
+        assert!(checkpoint.validate().is_err());
+
+        let event = IngestEventDetailV1::Quarantined {
+            schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
+            attempt: 1,
+            content_sha256: None,
+            byte_length: PdfLimits::default().max_input_bytes + 2,
+            quarantine,
+        };
+        assert!(event.validate().is_err());
+
+        let mut replay = accepted_receipt();
+        replay.outcome = IngestOutcome::IdempotentReplay;
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::Terminal { receipt: replay },
+        };
+        assert!(checkpoint.validate().is_err());
+
+        let receipt = accepted_receipt();
+        let evidence = receipt.evidence_manifest.expect("accepted evidence");
+        let original = StoredObjectV1 {
+            digest: evidence.manifest.original.sha256,
+            byte_length: evidence.manifest.original.byte_length,
+            vault_key: evidence.original_vault_key.clone(),
+        };
+        let manifest_object = StoredObjectV1 {
+            digest: evidence.manifest_content_sha256,
+            byte_length: evidence.manifest_byte_length,
+            vault_key: evidence.manifest_vault_key.clone(),
+        };
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::ProcessingComplete {
+                original,
+                candidate: ProcessingCandidateV1::Accepted {
+                    manifest: evidence.manifest,
+                    manifest_object,
+                },
+            },
+        };
+        let mut input = valid_input();
+        input.expected_probe_provenance.parser_version = "2.0.0".to_owned();
+        assert!(checkpoint.validate_for_input(&input).is_err());
+    }
+
+    #[test]
+    fn actor_fixture_remains_strict() {
+        assert!(ActorId::from_str("tester").is_ok());
+    }
+}
diff --git a/crates/heleos-core/src/ingest/mod.rs b/crates/heleos-core/src/ingest/mod.rs
new file mode 100644
index 0000000..eedc667
--- /dev/null
+++ b/crates/heleos-core/src/ingest/mod.rs
@@ -0,0 +1,463 @@
+pub(crate) mod audit;
+pub(crate) mod evidence;
+mod fault;
+pub(crate) mod inspection;
+pub(crate) mod job;
+
+use std::{fmt, io::Read, marker::PhantomData, str::FromStr};
+
+use serde::{Deserialize, Deserializer, Serialize, Serializer, de};
+
+use crate::store::ingest_repository::ProjectCreateCommand;
+use crate::{ActorId, Clock, DataClass, HeleosError, IdGenerator, ProjectId, Result, Store, Vault};
+
+pub use audit::{
+    AUDIT_CHAIN_REPORT_SCHEMA_V1, AuditAction, AuditChainFinding, AuditChainReport, AuditEvent,
+    AuditEventId, AuditInvalidField, AuditSubjectType,
+};
+pub use evidence::{
+    EVIDENCE_MANIFEST_MEDIA_TYPE, EVIDENCE_MANIFEST_SCHEMA_V1, EvidenceContentV1,
+    EvidenceManifestLineage, EvidenceManifestReceipt, EvidenceManifestV1, EvidencePdfLimitsV1,
+    MAX_EVIDENCE_MANIFEST_BYTES, PDF_MEDIA_TYPE,
+};
+pub use fault::{FaultInjector, FaultPoint};
+pub use inspection::{
+    FOUNDATION_INSPECTION_SCHEMA_V1, FoundationAdmissionState, FoundationContentObject,
+    FoundationCounts, FoundationEvidenceContent, FoundationEvidenceLineage, FoundationInspection,
+    FoundationIntakeEvent, FoundationJob, FoundationJobTerminalReason, FoundationSheet,
+};
+pub use job::{
+    INTAKE_QUARANTINE_SCHEMA_V1, IngestEngine, IngestReceipt, IngestRequest,
+    IntakeQuarantineReasonV1, IntakeQuarantineV1, IntakeSource, ResumeReceipt,
+};
+
+pub(crate) fn deserialize_bounded_vec<'de, D, T, const MAXIMUM: usize>(
+    deserializer: D,
+) -> std::result::Result<Vec<T>, D::Error>
+where
+    D: Deserializer<'de>,
+    T: Deserialize<'de>,
+{
+    struct BoundedVecVisitor<T, const MAXIMUM: usize>(PhantomData<T>);
+
+    impl<'de, T, const MAXIMUM: usize> de::Visitor<'de> for BoundedVecVisitor<T, MAXIMUM>
+    where
+        T: Deserialize<'de>,
+    {
+        type Value = Vec<T>;
+
+        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
+            write!(formatter, "a sequence with at most {MAXIMUM} elements")
+        }
+
+        fn visit_seq<A>(self, mut sequence: A) -> std::result::Result<Self::Value, A::Error>
+        where
+            A: de::SeqAccess<'de>,
+        {
+            if sequence.size_hint().is_some_and(|hint| hint > MAXIMUM) {
+                return Err(de::Error::custom(format_args!(
+                    "sequence exceeds maximum of {MAXIMUM}"
+                )));
+            }
+            let capacity = sequence.size_hint().unwrap_or(0).min(MAXIMUM);
+            let mut values = Vec::with_capacity(capacity);
+            while values.len() < MAXIMUM {
+                match sequence.next_element()? {
+                    Some(value) => values.push(value),
+                    None => return Ok(values),
+                }
+            }
+            if sequence.next_element::<de::IgnoredAny>()?.is_some() {
+                return Err(de::Error::custom(format_args!(
+                    "sequence exceeds maximum of {MAXIMUM}"
+                )));
+            }
+            Ok(values)
+        }
+    }
+
+    deserializer.deserialize_seq(BoundedVecVisitor::<T, MAXIMUM>(PhantomData))
+}
+
+pub(crate) fn deserialize_bounded_string<'de, D, const MAXIMUM: usize>(
+    deserializer: D,
+) -> std::result::Result<String, D::Error>
+where
+    D: Deserializer<'de>,
+{
+    struct BoundedStringVisitor<const MAXIMUM: usize>;
+
+    impl<'de, const MAXIMUM: usize> de::Visitor<'de> for BoundedStringVisitor<MAXIMUM> {
+        type Value = String;
+
+        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
+            write!(formatter, "a UTF-8 string of at most {MAXIMUM} bytes")
+        }
+
+        fn visit_borrowed_str<E>(self, value: &'de str) -> std::result::Result<Self::Value, E>
+        where
+            E: de::Error,
+        {
+            self.visit_str(value)
+        }
+
+        fn visit_str<E>(self, value: &str) -> std::result::Result<Self::Value, E>
+        where
+            E: de::Error,
+        {
+            if value.len() > MAXIMUM {
+                return Err(E::custom(format_args!(
+                    "string exceeds maximum of {MAXIMUM} bytes"
+                )));
+            }
+            Ok(value.to_owned())
+        }
+
+        fn visit_string<E>(self, value: String) -> std::result::Result<Self::Value, E>
+        where
+            E: de::Error,
+        {
+            if value.len() > MAXIMUM {
+                return Err(E::custom(format_args!(
+                    "string exceeds maximum of {MAXIMUM} bytes"
+                )));
+            }
+            Ok(value)
+        }
+    }
+
+    deserializer.deserialize_string(BoundedStringVisitor::<MAXIMUM>)
+}
+
+#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
+pub struct IdempotencyKey(String);
+
+impl IdempotencyKey {
+    pub const MAX_UTF8_BYTES: usize = 128;
+
+    pub fn as_str(&self) -> &str {
+        &self.0
+    }
+}
+
+impl TryFrom<&str> for IdempotencyKey {
+    type Error = HeleosError;
+
+    fn try_from(value: &str) -> Result<Self> {
+        if value.is_empty()
+            || value.len() > Self::MAX_UTF8_BYTES
+            || value.chars().any(char::is_control)
+        {
+            return Err(HeleosError::InvalidId);
+        }
+        Ok(Self(value.to_owned()))
+    }
+}
+
+impl TryFrom<String> for IdempotencyKey {
+    type Error = HeleosError;
+
+    fn try_from(value: String) -> Result<Self> {
+        Self::try_from(value.as_str())
+    }
+}
+
+impl FromStr for IdempotencyKey {
+    type Err = HeleosError;
+
+    fn from_str(value: &str) -> Result<Self> {
+        Self::try_from(value)
+    }
+}
+
+impl Serialize for IdempotencyKey {
+    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
+        serializer.serialize_str(&self.0)
+    }
+}
+
+impl<'de> Deserialize<'de> for IdempotencyKey {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        let value = deserialize_bounded_string::<D, { Self::MAX_UTF8_BYTES }>(deserializer)?;
+        Self::try_from(value).map_err(de::Error::custom)
+    }
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct ProjectCreateRequest {
+    pub project_id: Option<ProjectId>,
+    pub name: String,
+    pub actor: ActorId,
+    pub data_class: DataClass,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawProjectCreateRequest {
+    project_id: Option<ProjectId>,
+    #[serde(deserialize_with = "deserialize_project_name")]
+    name: String,
+    actor: ActorId,
+    data_class: DataClass,
+}
+
+fn deserialize_project_name<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<String, D::Error> {
+    deserialize_bounded_string::<D, 256>(deserializer)
+}
+
+impl<'de> Deserialize<'de> for ProjectCreateRequest {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        let raw = RawProjectCreateRequest::deserialize(deserializer)?;
+        if !project_name_is_valid(&raw.name) {
+            return Err(de::Error::custom("invalid project name"));
+        }
+        Ok(Self {
+            project_id: raw.project_id,
+            name: raw.name,
+            actor: raw.actor,
+            data_class: raw.data_class,
+        })
+    }
+}
+
+pub(crate) fn project_name_is_valid(value: &str) -> bool {
+    !value.is_empty() && value.len() <= 256 && !value.chars().any(char::is_control)
+}
+
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
+pub struct ProjectReceipt {
+    pub project_id: ProjectId,
+    pub created_at_ms: i64,
+    pub audit_event_id: AuditEventId,
+}
+
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawProjectReceipt {
+    project_id: ProjectId,
+    created_at_ms: i64,
+    audit_event_id: AuditEventId,
+}
+
+impl TryFrom<RawProjectReceipt> for ProjectReceipt {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawProjectReceipt) -> Result<Self> {
+        if raw.created_at_ms < 0 || (raw.created_at_ms as u64) > audit::JCS_SAFE_INTEGER_MAX {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(Self {
+            project_id: raw.project_id,
+            created_at_ms: raw.created_at_ms,
+            audit_event_id: raw.audit_event_id,
+        })
+    }
+}
+
+impl<'de> Deserialize<'de> for ProjectReceipt {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        let raw = RawProjectReceipt::deserialize(deserializer)?;
+        Self::try_from(raw).map_err(de::Error::custom)
+    }
+}
+
+pub struct ProjectService<'a> {
+    store: &'a mut Store,
+    clock: &'a dyn Clock,
+    ids: &'a dyn IdGenerator,
+}
+
+impl<'a> ProjectService<'a> {
+    pub fn new(
+        store: &'a mut Store,
+        clock: &'a dyn Clock,
+        ids: &'a dyn IdGenerator,
+    ) -> Result<Self> {
+        store.require_writer_capability()?;
+        Ok(Self { store, clock, ids })
+    }
+
+    pub fn create(&mut self, request: ProjectCreateRequest) -> Result<ProjectReceipt> {
+        if !project_name_is_valid(&request.name) {
+            return Err(HeleosError::InvalidId);
+        }
+        let created_at_ms = self.clock.now_unix_ms();
+        if created_at_ms < 0 || (created_at_ms as u64) > audit::JCS_SAFE_INTEGER_MAX {
+            return Err(HeleosError::Integrity);
+        }
+        let project_id = request
+            .project_id
+            .unwrap_or_else(|| ProjectId::from_uuid(self.ids.next_uuid()));
+        let audit_event_id = AuditEventId::from_uuid(self.ids.next_uuid());
+        self.store.project_create_with_audit(ProjectCreateCommand {
+            project_id,
+            name: request.name,
+            actor: request.actor,
+            data_class: request.data_class,
+            created_at_ms,
+            audit_event_id,
+        })
+    }
+}
+
+pub struct FoundationReader<'a> {
+    store: &'a Store,
+    vault: &'a Vault,
+}
+
+impl<'a> FoundationReader<'a> {
+    pub const fn new(store: &'a Store, vault: &'a Vault) -> Self {
+        Self { store, vault }
+    }
+
+    pub fn verify_audit_chain(&self) -> Result<AuditChainReport> {
+        let mut verifier = audit::AuditVerifier::new();
+        self.store.audit_chain_rows(|row| verifier.push(row))?;
+        verifier.finish()
+    }
+
+    pub fn evidence_manifest_for_revision(
+        &self,
+        revision: crate::RevisionId,
+    ) -> Result<EvidenceManifestReceipt> {
+        let rows = self.store.revision_evidence_rows(revision)?;
+        let anchor = rows.evidence.first().ok_or(HeleosError::Integrity)?;
+        for row in &rows.evidence {
+            if row.document_id != anchor.document_id
+                || row.revision_id != anchor.revision_id
+                || row.original != anchor.original
+                || row.manifest != anchor.manifest
+                || row.extraction_method != anchor.extraction_method
+                || row.parameters != anchor.parameters
+                || row.review_state != anchor.review_state
+            {
+                return Err(HeleosError::Integrity);
+            }
+        }
+        if anchor.revision_id != revision
+            || anchor.extraction_method != "heleos.pdf-probe/v1"
+            || anchor.review_state != "accepted"
+        {
+            return Err(HeleosError::Integrity);
+        }
+
+        let original = self
+            .vault
+            .open_verified(anchor.original.digest)
+            .map_err(committed_vault_error)?;
+        if original.digest() != anchor.original.digest
+            || original.byte_length() != anchor.original.byte_length
+            || original.vault_key() != anchor.original.vault_key
+        {
+            return Err(HeleosError::Integrity);
+        }
+
+        let manifest_object = self
+            .vault
+            .open_verified(anchor.manifest.digest)
+            .map_err(committed_vault_error)?;
+        if manifest_object.digest() != anchor.manifest.digest
+            || manifest_object.byte_length() != anchor.manifest.byte_length
+            || manifest_object.vault_key() != anchor.manifest.vault_key
+            || manifest_object.byte_length()
+                > u64::try_from(MAX_EVIDENCE_MANIFEST_BYTES).map_err(|_| HeleosError::Integrity)?
+        {
+            return Err(HeleosError::Integrity);
+        }
+        let read_limit = u64::try_from(MAX_EVIDENCE_MANIFEST_BYTES)
+            .map_err(|_| HeleosError::Integrity)?
+            .checked_add(1)
+            .ok_or(HeleosError::Integrity)?;
+        let mut manifest_bytes = Vec::with_capacity(
+            usize::try_from(manifest_object.byte_length()).map_err(|_| HeleosError::Integrity)?,
+        );
+        manifest_object
+            .take(read_limit)
+            .read_to_end(&mut manifest_bytes)
+            .map_err(HeleosError::Io)?;
+        if u64::try_from(manifest_bytes.len()).map_err(|_| HeleosError::Integrity)?
+            != anchor.manifest.byte_length
+        {
+            return Err(HeleosError::Integrity);
+        }
+        let manifest = serde_json::from_slice::<EvidenceManifestV1>(&manifest_bytes)
+            .map_err(|_| HeleosError::Integrity)?;
+        if manifest.canonical_bytes()? != manifest_bytes
+            || manifest.document_id != anchor.document_id
+            || manifest.revision_id != anchor.revision_id
+            || manifest.original.sha256 != anchor.original.digest
+            || manifest.original.byte_length != anchor.original.byte_length
+            || manifest.pages != rows.pages
+            || manifest.probe_provenance != anchor.parameters.probe_provenance
+            || manifest.requested_limits != anchor.parameters.requested_limits
+        {
+            return Err(HeleosError::Integrity);
+        }
+
+        let receipt = EvidenceManifestReceipt {
+            manifest,
+            manifest_content_sha256: anchor.manifest.digest,
+            manifest_byte_length: anchor.manifest.byte_length,
+            manifest_media_type: EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
+            manifest_vault_key: anchor.manifest.vault_key.clone(),
+            original_vault_key: anchor.original.vault_key.clone(),
+            lineages: rows.evidence.into_iter().map(|row| row.lineage).collect(),
+        };
+        receipt.validate()?;
+        Ok(receipt)
+    }
+
+    pub fn inspect_foundation(&self, project: ProjectId) -> Result<FoundationInspection> {
+        self.store.foundation_inspection_rows(project)
+    }
+}
+
+fn committed_vault_error(error: HeleosError) -> HeleosError {
+    if matches!(error, HeleosError::NotFound | HeleosError::ResourceLimit) {
+        HeleosError::Integrity
+    } else {
+        error
+    }
+}
+
+#[cfg(test)]
+mod tests {
+    use super::*;
+
+    #[test]
+    fn public_scalar_deserialization_enforces_utf8_byte_caps_before_validation() {
+        // Break caught: public Task 6 strings allocating beyond their documented field caps.
+        assert!(
+            serde_json::from_value::<IdempotencyKey>(serde_json::json!("a".repeat(128))).is_ok()
+        );
+        let error = serde_json::from_value::<IdempotencyKey>(serde_json::json!("a".repeat(129)))
+            .expect_err("129-byte key must fail in the bounded string visitor");
+        assert!(
+            error
+                .to_string()
+                .contains("string exceeds maximum of 128 bytes")
+        );
+
+        let request = |name: String| {
+            serde_json::json!({
+                "project_id": null,
+                "name": name,
+                "actor": "fixture-actor",
+                "data_class": "INTERNAL",
+            })
+        };
+        assert!(serde_json::from_value::<ProjectCreateRequest>(request("é".repeat(128))).is_ok());
+        let error = serde_json::from_value::<ProjectCreateRequest>(request(format!(
+            "{}a",
+            "é".repeat(128)
+        )))
+        .expect_err("257-byte project name must fail in the bounded string visitor");
+        assert!(
+            error
+                .to_string()
+                .contains("string exceeds maximum of 256 bytes")
+        );
+    }
+}
diff --git a/crates/heleos-core/src/lib.rs b/crates/heleos-core/src/lib.rs
index 03aa4ce..c61bba4 100644
--- a/crates/heleos-core/src/lib.rs
+++ b/crates/heleos-core/src/lib.rs
@@ -1,24 +1,38 @@
 #![forbid(unsafe_code)]
 
 pub mod domain;
 mod error;
+pub mod ingest;
 pub mod pdf;
 pub mod store;
 pub mod vault;
 
 pub use crate::domain::{
     ActorId, Clock, DataClass, DocumentId, EvidenceId, IdGenerator, IngestEventId, IngestOutcome,
     JobId, JobState, PdfLimits, ProjectId, RevisionId, Sha256Digest, SheetId, can_transition,
     canonical_document_ids, canonical_json, page_id,
 };
 pub use crate::error::{HeleosError, Result};
+pub use crate::ingest::{
+    AUDIT_CHAIN_REPORT_SCHEMA_V1, AuditAction, AuditChainFinding, AuditChainReport, AuditEvent,
+    AuditEventId, AuditInvalidField, AuditSubjectType, EVIDENCE_MANIFEST_MEDIA_TYPE,
+    EVIDENCE_MANIFEST_SCHEMA_V1, EvidenceContentV1, EvidenceManifestLineage,
+    EvidenceManifestReceipt, EvidenceManifestV1, EvidencePdfLimitsV1,
+    FOUNDATION_INSPECTION_SCHEMA_V1, FaultInjector, FaultPoint, FoundationAdmissionState,
+    FoundationContentObject, FoundationCounts, FoundationEvidenceContent,
+    FoundationEvidenceLineage, FoundationInspection, FoundationIntakeEvent, FoundationJob,
+    FoundationJobTerminalReason, FoundationReader, FoundationSheet, INTAKE_QUARANTINE_SCHEMA_V1,
+    IdempotencyKey, IngestEngine, IngestReceipt, IngestRequest, IntakeQuarantineReasonV1,
+    IntakeQuarantineV1, IntakeSource, MAX_EVIDENCE_MANIFEST_BYTES, PDF_MEDIA_TYPE,
+    ProjectCreateRequest, ProjectReceipt, ProjectService, ResumeReceipt,
+};
 pub use crate::pdf::{
     ApprovedPdfGuest, PageMetadata, PageTransform, PageUnit, PdfActiveFeature, PdfInspection,
     PdfLimitKind, PdfProbe, PdfProbeOutcome, PdfProbeProvenance, PdfQuarantine,
     PdfQuarantineReason, PdfSandboxConfig, WasiPdfProbe,
 };
 pub use crate::store::{
     FOUNDATION_SCHEMA_VERSION, INTEGRITY_VIOLATION_LIMIT, IntegrityReport, MigrationReport, Store,
     WriterLock, apply_private_permissions, verify_private_permissions,
 };
 pub use crate::vault::{
diff --git a/crates/heleos-core/src/pdf/geometry.rs b/crates/heleos-core/src/pdf/geometry.rs
index f36f5d6..234d5c6 100644
--- a/crates/heleos-core/src/pdf/geometry.rs
+++ b/crates/heleos-core/src/pdf/geometry.rs
@@ -1,13 +1,15 @@
 use serde::{Deserialize, Serialize};
 
-use crate::SheetId;
+use crate::{HeleosError, PdfLimits, Result, Sha256Digest, SheetId, page_id};
+
+const JCS_SAFE_INTEGER_MAX: u64 = 9_007_199_254_740_991;
 
 #[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
 pub enum PageUnit {
     #[serde(rename = "pt")]
     Point,
 }
 
 #[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
 #[serde(deny_unknown_fields)]
 pub struct PageTransform {
@@ -23,10 +25,55 @@ pub struct PageTransform {
 #[serde(deny_unknown_fields)]
 pub struct PageMetadata {
     pub index: u32,
     pub page_id: SheetId,
     pub width_micropoints: u64,
     pub height_micropoints: u64,
     pub unit: PageUnit,
     pub rotation_degrees: u16,
     pub transform: PageTransform,
 }
+
+pub(crate) const fn expected_rotation_matrix(rotation_degrees: u16) -> Option<(i8, i8, i8, i8)> {
+    match rotation_degrees {
+        0 => Some((1, 0, 0, -1)),
+        90 => Some((0, 1, 1, 0)),
+        180 => Some((-1, 0, 0, 1)),
+        270 => Some((0, -1, -1, 0)),
+        _ => None,
+    }
+}
+
+pub(crate) fn validate_page_metadata(
+    pages: &[PageMetadata],
+    content_sha256: Sha256Digest,
+    limits: PdfLimits,
+) -> Result<()> {
+    let page_count = u32::try_from(pages.len()).map_err(|_| HeleosError::Integrity)?;
+    if page_count > limits.max_pages {
+        return Err(HeleosError::Integrity);
+    }
+    let axis_cap = u64::from(limits.max_page_axis_points)
+        .checked_mul(1_000_000)
+        .ok_or(HeleosError::Integrity)?;
+    for (ordinal, page) in pages.iter().enumerate() {
+        let index = u32::try_from(ordinal).map_err(|_| HeleosError::Integrity)?;
+        let matrix =
+            expected_rotation_matrix(page.rotation_degrees).ok_or(HeleosError::Integrity)?;
+        let transform = page.transform;
+        if page.index != index
+            || page.page_id != page_id(content_sha256, index)
+            || page.width_micropoints == 0
+            || page.height_micropoints == 0
+            || page.width_micropoints > axis_cap
+            || page.height_micropoints > axis_cap
+            || page.width_micropoints > JCS_SAFE_INTEGER_MAX
+            || page.height_micropoints > JCS_SAFE_INTEGER_MAX
+            || transform.tx_micropoints.unsigned_abs() > JCS_SAFE_INTEGER_MAX
+            || transform.ty_micropoints.unsigned_abs() > JCS_SAFE_INTEGER_MAX
+            || (transform.m11, transform.m12, transform.m21, transform.m22) != matrix
+        {
+            return Err(HeleosError::Integrity);
+        }
+    }
+    Ok(())
+}
diff --git a/crates/heleos-core/src/pdf/mod.rs b/crates/heleos-core/src/pdf/mod.rs
index aceefb2..fc8f3a8 100644
--- a/crates/heleos-core/src/pdf/mod.rs
+++ b/crates/heleos-core/src/pdf/mod.rs
@@ -1,22 +1,26 @@
-mod geometry;
+pub(crate) mod geometry;
 mod wasi_host;
 
 use std::path::PathBuf;
 
-use serde::{Deserialize, Serialize};
+use serde::{Deserialize, Deserializer, Serialize, de};
 
-use crate::{IngestOutcome, PdfLimits, Result, RevisionId, Sha256Digest, VerifiedObject};
+use crate::{
+    HeleosError, IngestOutcome, PdfLimits, Result, RevisionId, Sha256Digest, VerifiedObject,
+};
 
 pub use geometry::{PageMetadata, PageTransform, PageUnit};
 
 pub trait PdfProbe: Send + Sync {
+    fn provenance(&self) -> Result<PdfProbeProvenance>;
+
     fn probe(
         &self,
         input: VerifiedObject,
         revision: RevisionId,
         limits: PdfLimits,
     ) -> Result<PdfProbeOutcome>;
 }
 
 #[derive(Clone, Debug, Eq, PartialEq)]
 pub struct PdfSandboxConfig {
@@ -58,33 +62,96 @@ impl PdfQuarantine {
             PdfQuarantineReason::Encrypted => IngestOutcome::QuarantinedEncrypted,
             PdfQuarantineReason::UnsupportedUserUnit => IngestOutcome::QuarantinedUnsupported,
             PdfQuarantineReason::ActiveFeature(_)
             | PdfQuarantineReason::SandboxTrap
             | PdfQuarantineReason::ProtocolBreach => IngestOutcome::QuarantinedSuspicious,
             PdfQuarantineReason::LimitExceeded(_) => IngestOutcome::QuarantinedLimit,
         }
     }
 }
 
-#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
-#[serde(deny_unknown_fields)]
+#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
 pub struct PdfProbeProvenance {
     pub parser_name: String,
     pub parser_version: String,
     pub guest_wasm_sha256: Sha256Digest,
     pub guest_source_tree_sha256: Sha256Digest,
     pub guest_dependency_graph_sha256: Sha256Digest,
     pub protocol_version: String,
 }
 
+#[derive(Deserialize)]
+#[serde(deny_unknown_fields)]
+struct RawPdfProbeProvenance {
+    #[serde(deserialize_with = "deserialize_provenance_text")]
+    parser_name: String,
+    #[serde(deserialize_with = "deserialize_provenance_text")]
+    parser_version: String,
+    guest_wasm_sha256: Sha256Digest,
+    guest_source_tree_sha256: Sha256Digest,
+    guest_dependency_graph_sha256: Sha256Digest,
+    #[serde(deserialize_with = "deserialize_provenance_text")]
+    protocol_version: String,
+}
+
+fn deserialize_provenance_text<'de, D: Deserializer<'de>>(
+    deserializer: D,
+) -> std::result::Result<String, D::Error> {
+    crate::ingest::deserialize_bounded_string::<D, 256>(deserializer)
+}
+
+impl TryFrom<RawPdfProbeProvenance> for PdfProbeProvenance {
+    type Error = HeleosError;
+
+    fn try_from(raw: RawPdfProbeProvenance) -> Result<Self> {
+        let value = Self {
+            parser_name: raw.parser_name,
+            parser_version: raw.parser_version,
+            guest_wasm_sha256: raw.guest_wasm_sha256,
+            guest_source_tree_sha256: raw.guest_source_tree_sha256,
+            guest_dependency_graph_sha256: raw.guest_dependency_graph_sha256,
+            protocol_version: raw.protocol_version,
+        };
+        value.validate()?;
+        Ok(value)
+    }
+}
+
+impl<'de> Deserialize<'de> for PdfProbeProvenance {
+    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
+        Self::try_from(RawPdfProbeProvenance::deserialize(deserializer)?).map_err(de::Error::custom)
+    }
+}
+
+impl PdfProbeProvenance {
+    pub(crate) fn validate(&self) -> Result<()> {
+        if [
+            self.parser_name.as_str(),
+            self.parser_version.as_str(),
+            self.protocol_version.as_str(),
+        ]
+        .iter()
+        .any(|field| field.is_empty() || field.len() > 256 || field.chars().any(char::is_control))
+        {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+}
+
 #[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
-#[serde(tag = "kind", content = "detail", rename_all = "snake_case")]
+#[serde(
+    tag = "kind",
+    content = "detail",
+    rename_all = "snake_case",
+    deny_unknown_fields
+)]
 pub enum PdfQuarantineReason {
     BadMagic,
     Corrupt,
     Encrypted,
     InvalidGeometry,
     UnsupportedUserUnit,
     ActiveFeature(PdfActiveFeature),
     LimitExceeded(PdfLimitKind),
     SandboxTrap,
     ProtocolBreach,
diff --git a/crates/heleos-core/src/pdf/wasi_host.rs b/crates/heleos-core/src/pdf/wasi_host.rs
index 0f2a7b9..0a50c45 100644
--- a/crates/heleos-core/src/pdf/wasi_host.rs
+++ b/crates/heleos-core/src/pdf/wasi_host.rs
@@ -20,20 +20,21 @@ use heleos_pdf_protocol::{
 };
 use serde::Deserialize;
 
 use crate::{
     HeleosError, PageMetadata, PageTransform, PageUnit, PdfActiveFeature, PdfInspection,
     PdfLimitKind, PdfLimits, PdfProbe, PdfProbeOutcome, PdfProbeProvenance, PdfQuarantine,
     PdfQuarantineReason, Result, RevisionId, Sha256Digest, VerifiedObject,
 };
 
 use super::PdfSandboxConfig;
+use super::geometry::validate_page_metadata;
 
 const MANIFEST_SCHEMA: &str = "heleos.pdf-guest-manifest/v1";
 const MANIFEST_MAX_BYTES: usize = 1024 * 1024;
 const GUEST_TARGET: &str = "wasm32-wasip1";
 const GUEST_PROFILE: &str = "release";
 const GUEST_RUSTC: &str = "1.96.1";
 const GUEST_RUST_PATH_REMAP: &str = "heleos-rust-path-remap/v1";
 const GUEST_BUILD_COMMAND: &str = concat!(
     "cargo +1.96.1 build --locked -p heleos-pdf-guest --target wasm32-wasip1 ",
     "--release --message-format=json-render-diagnostics --quiet"
@@ -1946,66 +1947,34 @@ fn map_guest_reason(reason: PdfGuestReasonV1) -> PdfQuarantineReason {
         PdfGuestReasonV1::UnsupportedUserUnit => PdfQuarantineReason::UnsupportedUserUnit,
         PdfGuestReasonV1::ActiveFeature(feature) => {
             PdfQuarantineReason::ActiveFeature(map_active_feature(feature))
         }
         PdfGuestReasonV1::LimitExceeded(limit) => {
             PdfQuarantineReason::LimitExceeded(map_document_limit(limit))
         }
     }
 }
 
-fn expected_matrix(rotation: u16) -> Option<(i8, i8, i8, i8)> {
-    match rotation {
-        0 => Some((1, 0, 0, -1)),
-        90 => Some((0, 1, 1, 0)),
-        180 => Some((-1, 0, 0, 1)),
-        270 => Some((0, -1, -1, 0)),
-        _ => None,
-    }
-}
-
 fn response_pages(
     pages: Vec<heleos_pdf_protocol::PdfPageV1>,
     content_sha256: Sha256Digest,
     limits: &ValidatedLimits,
 ) -> std::result::Result<Vec<PageMetadata>, ()> {
     if pages.len() > usize::try_from(limits.public.max_pages).map_err(|_| ())? {
         return Err(());
     }
-    let axis_cap = u64::from(limits.public.max_page_axis_points)
-        .checked_mul(1_000_000)
-        .ok_or(())?;
     let mut result = Vec::with_capacity(pages.len());
     for (expected, page) in pages.into_iter().enumerate() {
         let index = u32::try_from(expected).map_err(|_| ())?;
         let page_id = crate::page_id(content_sha256, index);
         let expected_page_id = page_id.as_digest().to_string();
-        let matrix = expected_matrix(page.rotation_degrees).ok_or(())?;
-        if page.index != index
-            || page.page_id != expected_page_id
-            || page.width_micropoints == 0
-            || page.height_micropoints == 0
-            || page.width_micropoints > axis_cap
-            || page.height_micropoints > axis_cap
-            || page.width_micropoints > heleos_pdf_protocol::JCS_SAFE_INTEGER_MAX
-            || page.height_micropoints > heleos_pdf_protocol::JCS_SAFE_INTEGER_MAX
-            || page.transform.tx_micropoints < heleos_pdf_protocol::JCS_SAFE_INTEGER_MIN
-            || page.transform.tx_micropoints > heleos_pdf_protocol::JCS_SAFE_INTEGER_MAX as i64
-            || page.transform.ty_micropoints < heleos_pdf_protocol::JCS_SAFE_INTEGER_MIN
-            || page.transform.ty_micropoints > heleos_pdf_protocol::JCS_SAFE_INTEGER_MAX as i64
-            || (
-                page.transform.m11,
-                page.transform.m12,
-                page.transform.m21,
-                page.transform.m22,
-            ) != matrix
-        {
+        if page.index != index || page.page_id != expected_page_id {
             return Err(());
         }
         let unit = match page.unit {
             PageUnitV1::Point => PageUnit::Point,
         };
         result.push(PageMetadata {
             index,
             page_id,
             width_micropoints: page.width_micropoints,
             height_micropoints: page.height_micropoints,
@@ -2014,20 +1983,21 @@ fn response_pages(
             transform: PageTransform {
                 m11: page.transform.m11,
                 m12: page.transform.m12,
                 m21: page.transform.m21,
                 m22: page.transform.m22,
                 tx_micropoints: page.transform.tx_micropoints,
                 ty_micropoints: page.transform.ty_micropoints,
             },
         });
     }
+    validate_page_metadata(&result, content_sha256, limits.public).map_err(|_| ())?;
     Ok(result)
 }
 
 fn translate_module_execution(
     execution: ModuleExecution,
     revision: RevisionId,
     content_sha256: Sha256Digest,
     byte_length: u64,
     provenance: PdfProbeProvenance,
     limits: ValidatedLimits,
@@ -2208,34 +2178,38 @@ impl WasiPdfProbe {
             manifest: guest.manifest,
             config,
             admission: Arc::new(Admission::new(2)),
             runtime: Some(runtime),
             ticker: Some(ticker),
         })
     }
 }
 
 impl PdfProbe for WasiPdfProbe {
+    fn provenance(&self) -> Result<PdfProbeProvenance> {
+        manifest_provenance(&self.manifest)
+    }
+
     fn probe(
         &self,
         mut input: VerifiedObject,
         revision: RevisionId,
         limits: PdfLimits,
     ) -> Result<PdfProbeOutcome> {
         if revision.as_digest() != &input.digest() {
             return Err(HeleosError::Integrity);
         }
         let limits = validate_limits(limits)?;
         verify_source_object(&mut input)?;
         let content_sha256 = input.digest();
         let byte_length = input.byte_length();
-        let provenance = manifest_provenance(&self.manifest)?;
+        let provenance = self.provenance()?;
         if byte_length > limits.public.max_input_bytes {
             return Ok(quarantine_outcome(
                 content_sha256,
                 byte_length,
                 provenance,
                 PdfQuarantineReason::LimitExceeded(PdfLimitKind::InputBytes),
             ));
         }
 
         let permit = self.admission.acquire()?;
diff --git a/crates/heleos-core/src/store/ingest_repository.rs b/crates/heleos-core/src/store/ingest_repository.rs
new file mode 100644
index 0000000..95c5a60
--- /dev/null
+++ b/crates/heleos-core/src/store/ingest_repository.rs
@@ -0,0 +1,6535 @@
+use std::{collections::BTreeMap, str::FromStr};
+
+use rusqlite::{Connection, Params, Row, Transaction, params, types::ValueRef};
+use serde::{Serialize, de::DeserializeOwned};
+use serde_json::{Value as JsonValue, json};
+use sha2::{Digest, Sha256};
+
+use crate::ingest::audit::{
+    AuditAction, AuditEvent, AuditEventId, AuditEventInput, AuditSubjectType, JCS_SAFE_INTEGER_MAX,
+    RawAuditRow, RawAuditValue,
+};
+use crate::ingest::evidence::{
+    EVIDENCE_MANIFEST_MEDIA_TYPE, EvidenceManifestLineage, EvidenceManifestReceipt,
+    EvidenceManifestV1, EvidenceParametersV1, PDF_MEDIA_TYPE,
+};
+use crate::ingest::inspection::{FoundationInspectionParts, MAX_FOUNDATION_INSPECTION_ROWS};
+use crate::ingest::job::{
+    INGEST_CHECKPOINT_SCHEMA_V1, INGEST_EVENT_DETAILS_SCHEMA_V1, IngestBudgetV1,
+    IngestCheckpointPhaseV1, IngestCheckpointV1, IngestEventDetailV1, IngestInputV1, IngestReceipt,
+    IntakeQuarantineV1, JOB_DEADLINE_MS, LEASE_DURATION_MS, MAX_JOB_ATTEMPTS, StoredObjectV1,
+    json_text,
+};
+use crate::ingest::{
+    FoundationAdmissionState, FoundationContentObject, FoundationCounts, FoundationEvidenceContent,
+    FoundationEvidenceLineage, FoundationInspection, FoundationIntakeEvent, FoundationJob,
+    FoundationJobTerminalReason, FoundationSheet, ProjectReceipt, project_name_is_valid,
+};
+use crate::{
+    ActorId, DataClass, DocumentId, EvidenceId, HeleosError, IdempotencyKey, IngestEventId,
+    IngestOutcome, JobId, JobState, PageMetadata, ProjectId, Result, RevisionId, Sha256Digest,
+    SheetId, Store, VaultInventory, VaultInventoryEntry,
+};
+
+const ONE_MIB: usize = 1024 * 1024;
+const EIGHT_MIB: usize = 8 * 1024 * 1024;
+const LATEST_JOB_AUDIT_SQL: &str = "SELECT project_id, action, after_json
+     FROM audit_events
+     WHERE subject_type = 'job' AND subject_id = ?1
+     ORDER BY sequence DESC
+     LIMIT 1";
+const VAULT_INVENTORY_COUNT_SQL: &str = "WITH inventory_candidates(digest) AS (
+         SELECT sha256 FROM content_objects
+         UNION ALL
+         SELECT CASE
+                    WHEN json_valid(checkpoint_json)
+                    THEN json_extract(checkpoint_json, '$.detail.original.digest')
+                    ELSE 'invalid-checkpoint:' || id
+                END
+         FROM job_runs
+         WHERE state IN ('queued', 'running', 'interrupted')
+           AND (
+               NOT json_valid(checkpoint_json)
+               OR CASE
+                      WHEN json_valid(checkpoint_json)
+                      THEN json_extract(checkpoint_json, '$.phase') IN (
+                          'vault_published', 'processing_complete'
+                      )
+                      ELSE 0
+                  END
+           )
+         UNION ALL
+         SELECT json_extract(
+                    checkpoint_json,
+                    '$.detail.candidate.detail.manifest_object.digest'
+                )
+         FROM job_runs
+         WHERE state IN ('queued', 'running', 'interrupted')
+           AND json_valid(checkpoint_json)
+           AND json_extract(checkpoint_json, '$.phase') = 'processing_complete'
+           AND json_extract(checkpoint_json, '$.detail.candidate.kind') = 'accepted'
+     )
+     SELECT COUNT(*)
+     FROM (SELECT digest FROM inventory_candidates GROUP BY digest)";
+
+pub(crate) enum IdempotencyLookup {
+    MissingProject,
+    Vacant,
+    Existing(JobRecord),
+}
+
+#[derive(Clone, Debug)]
+pub(crate) struct JobRecord {
+    pub job_id: JobId,
+    pub state: JobState,
+    pub attempt: u32,
+    pub lease_expires_at_ms: Option<i64>,
+    pub created_at_ms: i64,
+    pub deadline_at_ms: i64,
+    pub input: IngestInputV1,
+    pub budget: IngestBudgetV1,
+    pub checkpoint: IngestCheckpointV1,
+}
+
+impl JobRecord {
+    fn validate_for_scope(&self, project_id: ProjectId, key: &IdempotencyKey) -> Result<()> {
+        self.input.validate()?;
+        self.budget.validate_for_input(&self.input)?;
+        self.checkpoint.validate_for_input(&self.input)?;
+        if self.input.project_id != project_id || &self.input.idempotency_key != key {
+            return Err(HeleosError::Integrity);
+        }
+        if self.deadline_at_ms != checked_time_add(self.created_at_ms, JOB_DEADLINE_MS)? {
+            return Err(HeleosError::Integrity);
+        }
+        let terminal_receipt = match &self.checkpoint.phase {
+            IngestCheckpointPhaseV1::Terminal { receipt } => Some(receipt),
+            _ => None,
+        };
+        if terminal_receipt.is_some_and(|receipt| {
+            receipt.authoritative_job_id != self.job_id || receipt.attempt != self.attempt
+        }) {
+            return Err(HeleosError::Integrity);
+        }
+        let state_shape_is_valid = match self.state {
+            JobState::Queued => {
+                self.attempt == 0
+                    && self.lease_expires_at_ms.is_none()
+                    && terminal_receipt.is_none()
+            }
+            JobState::Running => {
+                (1..=MAX_JOB_ATTEMPTS).contains(&self.attempt)
+                    && self.lease_expires_at_ms.is_some()
+                    && terminal_receipt.is_none()
+            }
+            JobState::Interrupted => {
+                (1..MAX_JOB_ATTEMPTS).contains(&self.attempt)
+                    && self.lease_expires_at_ms.is_none()
+                    && terminal_receipt.is_none()
+            }
+            JobState::Succeeded => {
+                (1..=MAX_JOB_ATTEMPTS).contains(&self.attempt)
+                    && self.lease_expires_at_ms.is_none()
+                    && terminal_receipt.is_some()
+            }
+            JobState::Failed => {
+                (1..=MAX_JOB_ATTEMPTS).contains(&self.attempt)
+                    && self.lease_expires_at_ms.is_none()
+                    && terminal_receipt.is_none()
+            }
+            JobState::Cancelled => {
+                self.attempt <= MAX_JOB_ATTEMPTS
+                    && self.lease_expires_at_ms.is_none()
+                    && terminal_receipt.is_none()
+            }
+        };
+        if !state_shape_is_valid {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+}
+
+#[derive(Clone, Debug)]
+pub(crate) struct AdmissionRecord {
+    pub digest: Sha256Digest,
+    pub byte_length: u64,
+    pub media_type: String,
+    pub admission_state: String,
+    pub vault_key: String,
+    pub quarantine: Option<IntakeQuarantineV1>,
+}
+
+#[derive(Clone, Debug)]
+pub(crate) struct QuotaSnapshot {
+    pub store_overall_bytes: u64,
+    pub project_overall_bytes: u64,
+    pub store_evidence_bytes: u64,
+    pub project_evidence_bytes: u64,
+    pub store_quarantine_bytes: u64,
+    pub project_quarantine_bytes: u64,
+    pub admission: Option<AdmissionRecord>,
+    pub store_overall_accounted: bool,
+    pub project_overall_accounted: bool,
+    pub store_evidence_accounted: bool,
+    pub project_evidence_accounted: bool,
+    pub store_quarantine_accounted: bool,
+    pub project_quarantine_accounted: bool,
+}
+
+#[derive(Clone, Copy, Debug, Eq, PartialEq)]
+enum ReservedMedia {
+    Pdf,
+    EvidenceManifest,
+}
+
+#[derive(Clone, Copy, Debug, Eq, PartialEq)]
+struct ReservedObject {
+    byte_length: u64,
+    media: ReservedMedia,
+}
+
+#[derive(Default)]
+struct NonterminalReservations {
+    store_overall: BTreeMap<Sha256Digest, ReservedObject>,
+    project_overall: BTreeMap<Sha256Digest, ReservedObject>,
+    store_evidence: BTreeMap<Sha256Digest, ReservedObject>,
+    project_evidence: BTreeMap<Sha256Digest, ReservedObject>,
+    store_quarantine: BTreeMap<Sha256Digest, ReservedObject>,
+    project_quarantine: BTreeMap<Sha256Digest, ReservedObject>,
+}
+
+#[derive(Clone, Copy)]
+enum ProjectReservationCategory {
+    Overall,
+    Evidence,
+    Quarantine,
+}
+
+impl NonterminalReservations {
+    fn reserve_unadmitted_record(
+        &mut self,
+        project_id: ProjectId,
+        record: &JobRecord,
+    ) -> Result<()> {
+        let reserve_for_project = record.input.project_id == project_id;
+        match &record.checkpoint.phase {
+            IngestCheckpointPhaseV1::PreflightRejected { .. } => {}
+            IngestCheckpointPhaseV1::VaultPublished { original } => {
+                let reserved = ReservedObject {
+                    byte_length: original.byte_length,
+                    media: ReservedMedia::Pdf,
+                };
+                insert_reservation(&mut self.store_overall, original.digest, reserved)?;
+                insert_reservation(&mut self.store_quarantine, original.digest, reserved)?;
+                if reserve_for_project {
+                    insert_reservation(&mut self.project_overall, original.digest, reserved)?;
+                    insert_reservation(&mut self.project_quarantine, original.digest, reserved)?;
+                }
+            }
+            IngestCheckpointPhaseV1::ProcessingComplete {
+                original,
+                candidate,
+            } => match candidate {
+                crate::ingest::job::ProcessingCandidateV1::Accepted {
+                    manifest_object, ..
+                } => {
+                    let original = ReservedObject {
+                        byte_length: original.byte_length,
+                        media: ReservedMedia::Pdf,
+                    };
+                    let manifest = ReservedObject {
+                        byte_length: manifest_object.byte_length,
+                        media: ReservedMedia::EvidenceManifest,
+                    };
+                    insert_reservation(&mut self.store_evidence, manifest_object.digest, manifest)?;
+                    insert_reservation(
+                        &mut self.store_overall,
+                        record.input.content_sha256.ok_or(HeleosError::Integrity)?,
+                        original,
+                    )?;
+                    insert_reservation(&mut self.store_overall, manifest_object.digest, manifest)?;
+                    if reserve_for_project {
+                        insert_reservation(
+                            &mut self.project_overall,
+                            record.input.content_sha256.ok_or(HeleosError::Integrity)?,
+                            original,
+                        )?;
+                        insert_reservation(
+                            &mut self.project_overall,
+                            manifest_object.digest,
+                            manifest,
+                        )?;
+                        insert_reservation(
+                            &mut self.project_evidence,
+                            manifest_object.digest,
+                            manifest,
+                        )?;
+                    }
+                }
+                crate::ingest::job::ProcessingCandidateV1::Quarantined { .. } => {
+                    let reserved = ReservedObject {
+                        byte_length: original.byte_length,
+                        media: ReservedMedia::Pdf,
+                    };
+                    insert_reservation(&mut self.store_overall, original.digest, reserved)?;
+                    insert_reservation(&mut self.store_quarantine, original.digest, reserved)?;
+                    if reserve_for_project {
+                        insert_reservation(&mut self.project_overall, original.digest, reserved)?;
+                        insert_reservation(
+                            &mut self.project_quarantine,
+                            original.digest,
+                            reserved,
+                        )?;
+                    }
+                }
+            },
+            IngestCheckpointPhaseV1::Terminal { .. } => return Err(HeleosError::Integrity),
+        }
+        Ok(())
+    }
+
+    fn reserve_admitted_original(
+        &mut self,
+        project_id: ProjectId,
+        record: &JobRecord,
+        original: ReservedObject,
+        admission: &AdmissionRecord,
+        accepted_manifest: Option<(Sha256Digest, ReservedObject)>,
+    ) -> Result<()> {
+        insert_reservation(&mut self.store_overall, admission.digest, original)?;
+        match admission.admission_state.as_str() {
+            "quarantined" => {
+                if accepted_manifest.is_some() {
+                    return Err(HeleosError::Integrity);
+                }
+                insert_reservation(&mut self.store_quarantine, admission.digest, original)?;
+                if record.input.project_id == project_id {
+                    insert_reservation(&mut self.project_overall, admission.digest, original)?;
+                    insert_reservation(&mut self.project_quarantine, admission.digest, original)?;
+                }
+            }
+            "accepted" => {
+                let (manifest_digest, manifest) =
+                    accepted_manifest.ok_or(HeleosError::Integrity)?;
+                insert_reservation(&mut self.store_overall, manifest_digest, manifest)?;
+                insert_reservation(&mut self.store_evidence, manifest_digest, manifest)?;
+                if record.input.project_id == project_id {
+                    insert_reservation(&mut self.project_overall, admission.digest, original)?;
+                    insert_reservation(&mut self.project_overall, manifest_digest, manifest)?;
+                    insert_reservation(&mut self.project_evidence, manifest_digest, manifest)?;
+                }
+            }
+            _ => return Err(HeleosError::Integrity),
+        }
+        Ok(())
+    }
+}
+
+fn insert_reservation(
+    reservations: &mut BTreeMap<Sha256Digest, ReservedObject>,
+    digest: Sha256Digest,
+    value: ReservedObject,
+) -> Result<()> {
+    if let Some(existing) = reservations.insert(digest, value)
+        && existing != value
+    {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+fn validate_reserved_admission(
+    admission: &AdmissionRecord,
+    reserved: ReservedObject,
+) -> Result<()> {
+    let expected_media = match reserved.media {
+        ReservedMedia::Pdf => PDF_MEDIA_TYPE,
+        ReservedMedia::EvidenceManifest => EVIDENCE_MANIFEST_MEDIA_TYPE,
+    };
+    if admission.byte_length != reserved.byte_length
+        || admission.media_type != expected_media
+        || admission.vault_key != crate::Vault::object_key(admission.digest)
+        || (reserved.media == ReservedMedia::EvidenceManifest
+            && (admission.admission_state != "accepted" || admission.quarantine.is_some()))
+    {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+fn bounded_nonterminal_job_count(value: i64) -> Result<usize> {
+    let count = usize::try_from(value).map_err(|_| HeleosError::Integrity)?;
+    if count > MAX_FOUNDATION_INSPECTION_ROWS {
+        return Err(HeleosError::ResourceLimit);
+    }
+    Ok(count)
+}
+
+fn bounded_vault_inventory_count(value: i64) -> Result<usize> {
+    let count = usize::try_from(value).map_err(|_| HeleosError::Integrity)?;
+    if count > MAX_FOUNDATION_INSPECTION_ROWS {
+        return Err(HeleosError::ResourceLimit);
+    }
+    Ok(count)
+}
+
+fn require_latest_job_audit(
+    statement: &mut rusqlite::Statement<'_>,
+    job_id: JobId,
+    project_id: ProjectId,
+    state: JobState,
+    expected_after: &JsonValue,
+) -> Result<()> {
+    let mut rows = statement
+        .query([job_id.as_uuid().to_string()])
+        .map_err(|_| HeleosError::Database)?;
+    let row = rows
+        .next()
+        .map_err(|_| HeleosError::Database)?
+        .ok_or(HeleosError::Integrity)?;
+    let audit_project_id = parse_uuid_text::<ProjectId>(row, 0, 36)?;
+    let action = bounded_text(row, 1, 32)?;
+    let after: JsonValue = parse_bounded_json(row, 2, ONE_MIB)?;
+    let action_matches_state = match state {
+        JobState::Queued => action == "job_created",
+        JobState::Running => matches!(
+            action.as_str(),
+            "job_started" | "job_checkpointed" | "job_resumed"
+        ),
+        JobState::Interrupted => action == "job_interrupted",
+        JobState::Succeeded | JobState::Failed | JobState::Cancelled => false,
+    };
+    if audit_project_id != project_id || !action_matches_state || &after != expected_after {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+fn insert_vault_inventory_object(
+    inventory: &mut BTreeMap<Sha256Digest, (VaultInventoryEntry, ReservedMedia)>,
+    object: &StoredObjectV1,
+    media: ReservedMedia,
+    declared_count: usize,
+) -> Result<()> {
+    object.validate()?;
+    insert_vault_inventory_entry(
+        inventory,
+        VaultInventoryEntry {
+            digest: object.digest,
+            expected_byte_length: object.byte_length,
+            vault_key: object.vault_key.clone(),
+        },
+        media,
+        declared_count,
+    )
+}
+
+fn insert_vault_inventory_entry(
+    inventory: &mut BTreeMap<Sha256Digest, (VaultInventoryEntry, ReservedMedia)>,
+    entry: VaultInventoryEntry,
+    media: ReservedMedia,
+    declared_count: usize,
+) -> Result<()> {
+    if entry.expected_byte_length > JCS_SAFE_INTEGER_MAX
+        || entry.vault_key != crate::Vault::object_key(entry.digest)
+    {
+        return Err(HeleosError::Integrity);
+    }
+    if let Some(existing) = inventory.get(&entry.digest) {
+        return if existing == &(entry, media) {
+            Ok(())
+        } else {
+            Err(HeleosError::Integrity)
+        };
+    }
+    if inventory.len() >= declared_count {
+        return Err(HeleosError::Integrity);
+    }
+    inventory.insert(entry.digest, (entry, media));
+    Ok(())
+}
+
+pub(crate) struct JobCreateCommand {
+    pub job_id: JobId,
+    pub project_id: ProjectId,
+    pub input: IngestInputV1,
+    pub budget: IngestBudgetV1,
+    pub checkpoint: IngestCheckpointV1,
+    pub actor: ActorId,
+    pub created_at_ms: i64,
+    pub deadline_at_ms: i64,
+    pub audit_event_id: AuditEventId,
+}
+
+pub(crate) struct JobStartCommand {
+    pub job_id: JobId,
+    pub project_id: ProjectId,
+    pub actor: ActorId,
+    pub lease_owner: uuid::Uuid,
+    pub now_ms: i64,
+    pub lease_expires_at_ms: i64,
+    pub audit_event_id: AuditEventId,
+}
+
+pub(crate) struct JobRecoverCommand {
+    pub job_id: JobId,
+    pub actor: ActorId,
+    pub lease_owner: uuid::Uuid,
+    pub now_ms: i64,
+    pub expected_probe_provenance: crate::PdfProbeProvenance,
+    pub interrupted_event_id: IngestEventId,
+    pub audit_event_ids: [AuditEventId; 2],
+}
+
+pub(crate) enum JobRecoveryOutcome {
+    Started {
+        record: JobRecord,
+        interrupted_event_id: Option<IngestEventId>,
+    },
+    Terminal(JobRecord),
+    DeadlineExpired,
+    AttemptLimit,
+}
+
+pub(crate) struct JobFailCommand {
+    pub job_id: JobId,
+    pub project_id: ProjectId,
+    pub attempt: u32,
+    pub actor: ActorId,
+    pub now_ms: i64,
+    pub audit_event_id: AuditEventId,
+}
+
+pub(crate) struct JobCheckpointCommand {
+    pub job_id: JobId,
+    pub project_id: ProjectId,
+    pub actor: ActorId,
+    pub attempt: u32,
+    pub checkpoint: IngestCheckpointV1,
+    pub now_ms: i64,
+    pub audit_event_id: AuditEventId,
+}
+
+pub(crate) struct AcceptedIntakeCommand {
+    pub job_id: JobId,
+    pub project_id: ProjectId,
+    pub attempt: u32,
+    pub actor: ActorId,
+    pub idempotency_key: IdempotencyKey,
+    pub source_display: String,
+    pub original: StoredObjectV1,
+    pub manifest_object: StoredObjectV1,
+    pub manifest: EvidenceManifestV1,
+    pub pages: Vec<PageMetadata>,
+    pub ingest_event_id: IngestEventId,
+    pub source_record_id: String,
+    pub evidence_id: EvidenceId,
+    pub now_ms: i64,
+    pub preexisting_vault_digests: Vec<Sha256Digest>,
+    pub audit_event_ids: [AuditEventId; 3],
+}
+
+pub(crate) struct QuarantinedIntakeCommand {
+    pub job_id: JobId,
+    pub project_id: ProjectId,
+    pub attempt: u32,
+    pub actor: ActorId,
+    pub idempotency_key: IdempotencyKey,
+    pub source_display: String,
+    pub content_sha256: Option<Sha256Digest>,
+    pub byte_length: u64,
+    pub retained: Option<StoredObjectV1>,
+    pub quarantine: IntakeQuarantineV1,
+    pub outcome: IngestOutcome,
+    pub ingest_event_id: IngestEventId,
+    pub source_record_id: String,
+    pub now_ms: i64,
+    pub preexisting_vault_digests: Vec<Sha256Digest>,
+    pub audit_event_ids: [AuditEventId; 2],
+}
+
+pub(crate) struct ReplayAttemptCommand {
+    pub authoritative_job_id: JobId,
+    pub project_id: ProjectId,
+    pub idempotency_key: IdempotencyKey,
+    pub actor: ActorId,
+    pub source_display: Option<String>,
+    pub ingest_event_id: Option<IngestEventId>,
+    pub source_record_id: Option<String>,
+    pub preexisting_vault_digests: Vec<Sha256Digest>,
+    pub now_ms: i64,
+    pub audit_event_id: AuditEventId,
+}
+
+pub(crate) struct ConflictAttemptCommand {
+    pub authoritative_job_id: JobId,
+    pub project_id: ProjectId,
+    pub submitted_input: IngestInputV1,
+    pub submitted_budget: IngestBudgetV1,
+    pub actor: ActorId,
+    pub source_display: String,
+    pub ingest_event_id: IngestEventId,
+    pub source_record_id: String,
+    pub now_ms: i64,
+    pub audit_event_id: AuditEventId,
+}
+
+#[derive(Clone, Debug)]
+pub(crate) struct RevisionEvidenceRow {
+    pub lineage: EvidenceManifestLineage,
+    pub document_id: DocumentId,
+    pub revision_id: RevisionId,
+    pub original: StoredObjectV1,
+    pub manifest: StoredObjectV1,
+    pub extraction_method: String,
+    pub parameters: EvidenceParametersV1,
+    pub review_state: String,
+}
+
+#[derive(Clone, Debug)]
+pub(crate) struct RevisionEvidenceRows {
+    pub evidence: Vec<RevisionEvidenceRow>,
+    pub pages: Vec<PageMetadata>,
+}
+
+pub(crate) struct ProjectCreateCommand {
+    pub project_id: ProjectId,
+    pub name: String,
+    pub actor: ActorId,
+    pub data_class: DataClass,
+    pub created_at_ms: i64,
+    pub audit_event_id: AuditEventId,
+}
+
+impl Store {
+    pub(crate) fn project_create_with_audit(
+        &mut self,
+        command: ProjectCreateCommand,
+    ) -> Result<ProjectReceipt> {
+        if !project_name_is_valid(&command.name) {
+            return Err(HeleosError::InvalidId);
+        }
+        let project_id_text = command.project_id.as_uuid().to_string();
+        let audit_event_id = command.audit_event_id;
+        let created_at_ms = command.created_at_ms;
+        self.with_immediate_transaction(|transaction| {
+            transaction
+                .execute(
+                    "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+                     VALUES (?1, ?2, ?3, ?4, ?5)",
+                    params![
+                        project_id_text,
+                        command.name,
+                        created_at_ms,
+                        command.actor.as_str(),
+                        data_class_text(command.data_class),
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            let after = json!({
+                "data_class": data_class_text(command.data_class),
+                "name": command.name,
+                "project_id": project_id_text,
+            });
+            append_audit(
+                transaction,
+                AuditEventInput {
+                    id: audit_event_id,
+                    sequence: next_audit_sequence(transaction)?,
+                    project_id: Some(command.project_id),
+                    actor: command.actor,
+                    action: AuditAction::ProjectCreated,
+                    subject_type: AuditSubjectType::Project,
+                    subject_id: project_id_text,
+                    before: serde_json::Value::Null,
+                    after,
+                    reason: "project created".to_owned(),
+                    occurred_at_ms: created_at_ms,
+                    previous_hash: audit_head(transaction)?,
+                },
+            )?;
+            Ok(ProjectReceipt {
+                project_id: command.project_id,
+                created_at_ms,
+                audit_event_id,
+            })
+        })
+    }
+
+    pub(crate) fn idempotency_lookup(
+        &self,
+        project_id: ProjectId,
+        key: &IdempotencyKey,
+    ) -> Result<IdempotencyLookup> {
+        let project = project_id.as_uuid().to_string();
+        let exists = self
+            .connection
+            .query_row(
+                "SELECT EXISTS(SELECT 1 FROM projects WHERE id = ?1)",
+                [&project],
+                |row| row.get::<_, i64>(0),
+            )
+            .map_err(|_| HeleosError::Database)?;
+        if exists != 1 {
+            return Ok(IdempotencyLookup::MissingProject);
+        }
+        let mut statement = self
+            .connection
+            .prepare(
+                "SELECT id, state, attempt, lease_expires_at_ms, created_at_ms, deadline_at_ms,
+                        input_json, budget_json, checkpoint_json
+                 FROM job_runs
+                 WHERE project_id = ?1 AND kind = 'pdf_ingest' AND idempotency_key = ?2",
+            )
+            .map_err(|_| HeleosError::Database)?;
+        let mut rows = statement
+            .query(params![project, key.as_str()])
+            .map_err(|_| HeleosError::Database)?;
+        let Some(row) = rows.next().map_err(|_| HeleosError::Database)? else {
+            return Ok(IdempotencyLookup::Vacant);
+        };
+        let job_id = parse_uuid_text::<JobId>(row, 0, 36)?;
+        let state = parse_job_state(&bounded_text(row, 1, 16)?)?;
+        let attempt = bounded_u32(row, 2, 0, 16)?;
+        let lease_expires_at_ms = optional_nonnegative_i64(row, 3)?;
+        let created_at_ms = nonnegative_i64(row, 4)?;
+        let deadline_at_ms = nonnegative_i64(row, 5)?;
+        let input: IngestInputV1 = parse_bounded_json(row, 6, ONE_MIB)?;
+        let budget: IngestBudgetV1 = parse_bounded_json(row, 7, ONE_MIB)?;
+        let checkpoint: IngestCheckpointV1 = parse_bounded_json(row, 8, EIGHT_MIB)?;
+        if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
+            return Err(HeleosError::Integrity);
+        }
+        let record = JobRecord {
+            job_id,
+            state,
+            attempt,
+            lease_expires_at_ms,
+            created_at_ms,
+            deadline_at_ms,
+            input,
+            budget,
+            checkpoint,
+        };
+        record.validate_for_scope(project_id, key)?;
+        Ok(IdempotencyLookup::Existing(record))
+    }
+
+    pub(crate) fn quota_snapshot(
+        &self,
+        project_id: ProjectId,
+        digest: Option<Sha256Digest>,
+    ) -> Result<QuotaSnapshot> {
+        let project = project_id.as_uuid().to_string();
+        let committed_store_overall_bytes = checked_sum(
+            &self.connection,
+            "SELECT byte_length FROM content_objects ORDER BY sha256",
+            [],
+        )?;
+        let committed_store_evidence_bytes = checked_sum(
+            &self.connection,
+            "SELECT byte_length FROM content_objects
+             WHERE media_type = 'application/vnd.heleos.evidence-manifest+json;version=1'
+             ORDER BY sha256",
+            [],
+        )?;
+        let committed_store_quarantine_bytes = checked_sum(
+            &self.connection,
+            "SELECT byte_length FROM content_objects
+             WHERE admission_state = 'quarantined'
+             ORDER BY sha256",
+            [],
+        )?;
+        let committed_project_overall_bytes = checked_sum(
+            &self.connection,
+            "SELECT content.byte_length
+             FROM content_objects AS content
+             WHERE content.sha256 IN (
+                 SELECT revision.content_sha256
+                 FROM project_documents AS link
+                 JOIN document_revisions AS revision ON revision.document_id = link.document_id
+                 WHERE link.project_id = ?1
+                 UNION
+                 SELECT evidence.content_sha256 FROM evidence_objects AS evidence
+                 WHERE evidence.project_id = ?1
+                 UNION
+                 SELECT event.content_sha256 FROM ingest_events AS event
+                 WHERE event.project_id = ?1 AND event.content_sha256 IS NOT NULL
+                   AND event.outcome LIKE 'quarantined_%'
+             )
+             ORDER BY content.sha256",
+            [&project],
+        )?;
+        let committed_project_evidence_bytes = checked_sum(
+            &self.connection,
+            "SELECT content.byte_length
+             FROM content_objects AS content
+             WHERE content.sha256 IN (
+                 SELECT evidence.content_sha256 FROM evidence_objects AS evidence
+                 WHERE evidence.project_id = ?1
+             )
+             ORDER BY content.sha256",
+            [&project],
+        )?;
+        let committed_project_quarantine_bytes = checked_sum(
+            &self.connection,
+            "SELECT content.byte_length
+             FROM content_objects AS content
+             WHERE content.sha256 IN (
+                 SELECT event.content_sha256 FROM ingest_events AS event
+                 WHERE event.project_id = ?1 AND event.content_sha256 IS NOT NULL
+                   AND event.outcome LIKE 'quarantined_%'
+             )
+             ORDER BY content.sha256",
+            [&project],
+        )?;
+
+        let reservations = self.nonterminal_reservations(project_id)?;
+        let store_overall_bytes = self
+            .add_store_reservations(committed_store_overall_bytes, &reservations.store_overall)?;
+        let store_evidence_bytes = self
+            .add_store_reservations(committed_store_evidence_bytes, &reservations.store_evidence)?;
+        let store_quarantine_bytes = self.add_store_reservations(
+            committed_store_quarantine_bytes,
+            &reservations.store_quarantine,
+        )?;
+        let project_overall_bytes = self.add_project_reservations(
+            project_id,
+            committed_project_overall_bytes,
+            &reservations.project_overall,
+            ProjectReservationCategory::Overall,
+        )?;
+        let project_evidence_bytes = self.add_project_reservations(
+            project_id,
+            committed_project_evidence_bytes,
+            &reservations.project_evidence,
+            ProjectReservationCategory::Evidence,
+        )?;
+        let project_quarantine_bytes = self.add_project_reservations(
+            project_id,
+            committed_project_quarantine_bytes,
+            &reservations.project_quarantine,
+            ProjectReservationCategory::Quarantine,
+        )?;
+
+        let admission = match digest {
+            Some(digest) => self.admission_record(digest)?,
+            None => None,
+        };
+        let store_overall_accounted = digest.is_some_and(|digest| {
+            admission.is_some() || reservations.store_overall.contains_key(&digest)
+        });
+        let store_evidence_accounted = digest.is_some_and(|digest| {
+            admission.as_ref().is_some_and(|value| {
+                value.media_type == EVIDENCE_MANIFEST_MEDIA_TYPE
+                    && value.admission_state == "accepted"
+            }) || reservations.store_evidence.contains_key(&digest)
+        });
+        let store_quarantine_accounted = digest.is_some_and(|digest| {
+            admission
+                .as_ref()
+                .is_some_and(|value| value.admission_state == "quarantined")
+                || reservations.store_quarantine.contains_key(&digest)
+        });
+        let project_overall_accounted = match digest {
+            Some(digest) => {
+                self.project_has_digest(project_id, digest, ProjectReservationCategory::Overall)?
+                    || reservations.project_overall.contains_key(&digest)
+            }
+            None => false,
+        };
+        let project_evidence_accounted = match digest {
+            Some(digest) => {
+                self.project_has_digest(project_id, digest, ProjectReservationCategory::Evidence)?
+                    || reservations.project_evidence.contains_key(&digest)
+            }
+            None => false,
+        };
+        let project_quarantine_accounted = match digest {
+            Some(digest) => {
+                self.project_has_digest(project_id, digest, ProjectReservationCategory::Quarantine)?
+                    || reservations.project_quarantine.contains_key(&digest)
+            }
+            None => false,
+        };
+        Ok(QuotaSnapshot {
+            store_overall_bytes,
+            project_overall_bytes,
+            store_evidence_bytes,
+            project_evidence_bytes,
+            store_quarantine_bytes,
+            project_quarantine_bytes,
+            admission,
+            store_overall_accounted,
+            project_overall_accounted,
+            store_evidence_accounted,
+            project_evidence_accounted,
+            store_quarantine_accounted,
+            project_quarantine_accounted,
+        })
+    }
+
+    fn nonterminal_job_count(&self) -> Result<usize> {
+        let declared_count = self
+            .connection
+            .query_row(
+                "SELECT COUNT(*) FROM job_runs
+                 WHERE state IN ('queued', 'running', 'interrupted')",
+                [],
+                |row| row.get::<_, i64>(0),
+            )
+            .map_err(|_| HeleosError::Database)?;
+        bounded_nonterminal_job_count(declared_count)
+    }
+
+    fn visit_nonterminal_jobs<F>(&self, declared_count: usize, mut visit: F) -> Result<()>
+    where
+        F: FnMut(&JobRecord) -> Result<()>,
+    {
+        let fetch_limit = declared_count
+            .checked_add(1)
+            .and_then(|value| i64::try_from(value).ok())
+            .ok_or(HeleosError::Integrity)?;
+        let mut statement = self
+            .connection
+            .prepare(
+                "SELECT job.id, job.project_id, job.idempotency_key, job.state, job.attempt,
+                        job.lease_owner, job.lease_expires_at_ms, job.created_at_ms,
+                        job.deadline_at_ms, job.input_json, job.budget_json,
+                        job.checkpoint_json
+                 FROM job_runs AS job
+                 WHERE job.state IN ('queued', 'running', 'interrupted')
+                 ORDER BY job.id
+                 LIMIT ?1",
+            )
+            .map_err(|_| HeleosError::Database)?;
+        let mut audit_statement = self
+            .connection
+            .prepare(LATEST_JOB_AUDIT_SQL)
+            .map_err(|_| HeleosError::Database)?;
+        let mut rows = statement
+            .query([fetch_limit])
+            .map_err(|_| HeleosError::Database)?;
+        let mut observed_count = 0_usize;
+        while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+            observed_count = observed_count
+                .checked_add(1)
+                .ok_or(HeleosError::Integrity)?;
+            if observed_count > declared_count {
+                return Err(HeleosError::Integrity);
+            }
+            let job_id = parse_uuid_text::<JobId>(row, 0, 36)?;
+            let row_project_id = parse_uuid_text::<ProjectId>(row, 1, 36)?;
+            let idempotency_key = IdempotencyKey::try_from(bounded_text(row, 2, 128)?)
+                .map_err(|_| HeleosError::Integrity)?;
+            let state_text = bounded_text(row, 3, 16)?;
+            let state = parse_job_state(&state_text)?;
+            let attempt = bounded_u32(row, 4, 0, MAX_JOB_ATTEMPTS)?;
+            let lease_owner = optional_canonical_uuid_text(row, 5)?;
+            let lease_expires_at_ms = optional_nonnegative_i64(row, 6)?;
+            let created_at_ms = nonnegative_i64(row, 7)?;
+            let deadline_at_ms = nonnegative_i64(row, 8)?;
+            let input_json = bounded_text(row, 9, ONE_MIB)?;
+            let budget_json = bounded_text(row, 10, ONE_MIB)?;
+            let checkpoint_json = bounded_text(row, 11, EIGHT_MIB)?;
+            let input: IngestInputV1 =
+                serde_json::from_str(&input_json).map_err(|_| HeleosError::Integrity)?;
+            let budget: IngestBudgetV1 =
+                serde_json::from_str(&budget_json).map_err(|_| HeleosError::Integrity)?;
+            let checkpoint: IngestCheckpointV1 =
+                serde_json::from_str(&checkpoint_json).map_err(|_| HeleosError::Integrity)?;
+            if json_text(&input, ONE_MIB)? != input_json
+                || json_text(&budget, ONE_MIB)? != budget_json
+                || json_text(&checkpoint, EIGHT_MIB)? != checkpoint_json
+            {
+                return Err(HeleosError::Integrity);
+            }
+            let record = JobRecord {
+                job_id,
+                state,
+                attempt,
+                lease_expires_at_ms,
+                created_at_ms,
+                deadline_at_ms,
+                input,
+                budget,
+                checkpoint,
+            };
+            record.validate_for_scope(row_project_id, &idempotency_key)?;
+            if (record.state == JobState::Running) != lease_owner.is_some() {
+                return Err(HeleosError::Integrity);
+            }
+            let expected_audit_after = json!({
+                "attempt": record.attempt,
+                "budget_sha256": sha256_text(&budget_json),
+                "checkpoint_sha256": sha256_text(&checkpoint_json),
+                "deadline_at_ms": record.deadline_at_ms,
+                "input_sha256": sha256_text(&input_json),
+                "lease_expires_at_ms": record.lease_expires_at_ms,
+                "lease_owner": lease_owner,
+                "state": state_text,
+                "terminal_result_ids": JsonValue::Null,
+            });
+            require_latest_job_audit(
+                &mut audit_statement,
+                record.job_id,
+                row_project_id,
+                record.state,
+                &expected_audit_after,
+            )?;
+            visit(&record)?;
+        }
+        if observed_count != declared_count {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(())
+    }
+
+    fn nonterminal_reservations(&self, project_id: ProjectId) -> Result<NonterminalReservations> {
+        let declared_count = self.nonterminal_job_count()?;
+        let mut reservations = NonterminalReservations::default();
+        self.visit_nonterminal_jobs(declared_count, |record| {
+            let original = match &record.checkpoint.phase {
+                IngestCheckpointPhaseV1::VaultPublished { original }
+                | IngestCheckpointPhaseV1::ProcessingComplete { original, .. } => Some(original),
+                IngestCheckpointPhaseV1::PreflightRejected { .. } => None,
+                IngestCheckpointPhaseV1::Terminal { .. } => return Err(HeleosError::Integrity),
+            };
+            if let Some(original) = original
+                && let Some(admission) = self.admission_record(original.digest)?
+            {
+                let reserved_original = ReservedObject {
+                    byte_length: original.byte_length,
+                    media: ReservedMedia::Pdf,
+                };
+                validate_reserved_admission(&admission, reserved_original)?;
+                let accepted_manifest = if admission.admission_state == "accepted" {
+                    Some(self.accepted_manifest_reservation(original.digest)?)
+                } else {
+                    None
+                };
+                reservations.reserve_admitted_original(
+                    project_id,
+                    record,
+                    reserved_original,
+                    &admission,
+                    accepted_manifest,
+                )?;
+            } else {
+                reservations.reserve_unadmitted_record(project_id, record)?;
+            }
+            Ok(())
+        })?;
+        Ok(reservations)
+    }
+
+    pub(crate) fn vault_inventory_rows(&self) -> Result<VaultInventory> {
+        let live_job_count = self.nonterminal_job_count()?;
+        let declared_count = self
+            .connection
+            .query_row(VAULT_INVENTORY_COUNT_SQL, [], |row| row.get::<_, i64>(0))
+            .map_err(|_| HeleosError::Database)?;
+        let declared_count = bounded_vault_inventory_count(declared_count)?;
+        let fetch_limit = declared_count
+            .checked_add(1)
+            .and_then(|value| i64::try_from(value).ok())
+            .ok_or(HeleosError::Integrity)?;
+        let mut inventory = BTreeMap::new();
+
+        {
+            let mut statement = self
+                .connection
+                .prepare(
+                    "SELECT sha256, byte_length, media_type, admission_state, vault_key,
+                            quarantine_reason
+                     FROM content_objects
+                     ORDER BY sha256
+                     LIMIT ?1",
+                )
+                .map_err(|_| HeleosError::Database)?;
+            let mut rows = statement
+                .query([fetch_limit])
+                .map_err(|_| HeleosError::Database)?;
+            let mut observed_content_count = 0_usize;
+            while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+                observed_content_count = observed_content_count
+                    .checked_add(1)
+                    .ok_or(HeleosError::Integrity)?;
+                if observed_content_count > declared_count {
+                    return Err(HeleosError::Integrity);
+                }
+                let admission_state = match bounded_text(row, 3, 16)?.as_str() {
+                    "accepted" => FoundationAdmissionState::Accepted,
+                    "quarantined" => FoundationAdmissionState::Quarantined,
+                    _ => return Err(HeleosError::Integrity),
+                };
+                let content = FoundationContentObject {
+                    sha256: parse_digest_text(row, 0)?,
+                    byte_length: nonnegative_u64(row, 1)?,
+                    media_type: bounded_text(row, 2, 256)?,
+                    admission_state,
+                    vault_key: bounded_text(row, 4, 256)?,
+                    quarantine: optional_bounded_json(row, 5, ONE_MIB)?,
+                };
+                content.validate()?;
+                let media = match content.media_type.as_str() {
+                    PDF_MEDIA_TYPE => ReservedMedia::Pdf,
+                    EVIDENCE_MANIFEST_MEDIA_TYPE => ReservedMedia::EvidenceManifest,
+                    _ => return Err(HeleosError::Integrity),
+                };
+                insert_vault_inventory_entry(
+                    &mut inventory,
+                    VaultInventoryEntry {
+                        digest: content.sha256,
+                        expected_byte_length: content.byte_length,
+                        vault_key: content.vault_key,
+                    },
+                    media,
+                    declared_count,
+                )?;
+            }
+        }
+
+        self.visit_nonterminal_jobs(live_job_count, |record| {
+            match &record.checkpoint.phase {
+                IngestCheckpointPhaseV1::PreflightRejected { .. } => {}
+                IngestCheckpointPhaseV1::VaultPublished { original } => {
+                    insert_vault_inventory_object(
+                        &mut inventory,
+                        original,
+                        ReservedMedia::Pdf,
+                        declared_count,
+                    )?;
+                }
+                IngestCheckpointPhaseV1::ProcessingComplete {
+                    original,
+                    candidate,
+                } => {
+                    insert_vault_inventory_object(
+                        &mut inventory,
+                        original,
+                        ReservedMedia::Pdf,
+                        declared_count,
+                    )?;
+                    if let crate::ingest::job::ProcessingCandidateV1::Accepted {
+                        manifest_object,
+                        ..
+                    } = candidate
+                    {
+                        insert_vault_inventory_object(
+                            &mut inventory,
+                            manifest_object,
+                            ReservedMedia::EvidenceManifest,
+                            declared_count,
+                        )?;
+                    }
+                }
+                IngestCheckpointPhaseV1::Terminal { .. } => {
+                    return Err(HeleosError::Integrity);
+                }
+            }
+            Ok(())
+        })?;
+        if inventory.len() != declared_count {
+            return Err(HeleosError::Integrity);
+        }
+        VaultInventory::try_from_entries(inventory.into_values().map(|(entry, _)| entry))
+    }
+
+    fn accepted_manifest_reservation(
+        &self,
+        original: Sha256Digest,
+    ) -> Result<(Sha256Digest, ReservedObject)> {
+        let mut statement = self
+            .connection
+            .prepare(
+                "SELECT DISTINCT evidence.content_sha256, content.byte_length,
+                        content.media_type, content.admission_state, content.vault_key
+                 FROM document_revisions AS revision
+                 JOIN evidence_objects AS evidence
+                   ON evidence.document_revision_id = revision.id
+                 JOIN content_objects AS content ON content.sha256 = evidence.content_sha256
+                 WHERE revision.content_sha256 = ?1
+                   AND evidence.parent_content_sha256 = ?1
+                   AND evidence.extraction_method = 'heleos.pdf-probe/v1'
+                   AND evidence.review_state = 'accepted'
+                 ORDER BY evidence.content_sha256
+                 LIMIT 2",
+            )
+            .map_err(|_| HeleosError::Database)?;
+        let mut rows = statement
+            .query([original.to_string()])
+            .map_err(|_| HeleosError::Database)?;
+        let row = rows
+            .next()
+            .map_err(|_| HeleosError::Database)?
+            .ok_or(HeleosError::Integrity)?;
+        let digest = parse_digest_text(row, 0)?;
+        let reserved = ReservedObject {
+            byte_length: nonnegative_u64(row, 1)?,
+            media: ReservedMedia::EvidenceManifest,
+        };
+        if bounded_text(row, 2, 256)? != EVIDENCE_MANIFEST_MEDIA_TYPE
+            || bounded_text(row, 3, 16)? != "accepted"
+            || bounded_text(row, 4, 256)? != crate::Vault::object_key(digest)
+            || rows.next().map_err(|_| HeleosError::Database)?.is_some()
+        {
+            return Err(HeleosError::Integrity);
+        }
+        Ok((digest, reserved))
+    }
+
+    fn add_store_reservations(
+        &self,
+        mut committed: u64,
+        reservations: &BTreeMap<Sha256Digest, ReservedObject>,
+    ) -> Result<u64> {
+        for (digest, reserved) in reservations {
+            if let Some(admission) = self.admission_record(*digest)? {
+                validate_reserved_admission(&admission, *reserved)?;
+            } else {
+                committed = committed
+                    .checked_add(reserved.byte_length)
+                    .ok_or(HeleosError::Integrity)?;
+            }
+        }
+        Ok(committed)
+    }
+
+    fn add_project_reservations(
+        &self,
+        project_id: ProjectId,
+        mut committed: u64,
+        reservations: &BTreeMap<Sha256Digest, ReservedObject>,
+        category: ProjectReservationCategory,
+    ) -> Result<u64> {
+        for (digest, reserved) in reservations {
+            if self.project_has_digest(project_id, *digest, category)? {
+                let admission = self
+                    .admission_record(*digest)?
+                    .ok_or(HeleosError::Integrity)?;
+                validate_reserved_admission(&admission, *reserved)?;
+            } else {
+                committed = committed
+                    .checked_add(reserved.byte_length)
+                    .ok_or(HeleosError::Integrity)?;
+            }
+        }
+        Ok(committed)
+    }
+
+    fn project_has_digest(
+        &self,
+        project_id: ProjectId,
+        digest: Sha256Digest,
+        category: ProjectReservationCategory,
+    ) -> Result<bool> {
+        let sql = match category {
+            ProjectReservationCategory::Overall => {
+                "SELECT EXISTS(
+                    SELECT 1
+                    FROM project_documents AS link
+                    JOIN document_revisions AS revision ON revision.document_id = link.document_id
+                    WHERE link.project_id = ?1 AND revision.content_sha256 = ?2
+                    UNION ALL
+                    SELECT 1 FROM evidence_objects
+                    WHERE project_id = ?1 AND content_sha256 = ?2
+                    UNION ALL
+                    SELECT 1 FROM ingest_events
+                    WHERE project_id = ?1 AND content_sha256 = ?2
+                )"
+            }
+            ProjectReservationCategory::Evidence => {
+                "SELECT EXISTS(
+                    SELECT 1 FROM evidence_objects
+                    WHERE project_id = ?1 AND content_sha256 = ?2
+                )"
+            }
+            ProjectReservationCategory::Quarantine => {
+                "SELECT EXISTS(
+                    SELECT 1 FROM ingest_events
+                    WHERE project_id = ?1 AND content_sha256 = ?2
+                      AND outcome LIKE 'quarantined_%'
+                )"
+            }
+        };
+        let exists = self
+            .connection
+            .query_row(
+                sql,
+                params![project_id.as_uuid().to_string(), digest.to_string()],
+                |row| row.get::<_, i64>(0),
+            )
+            .map_err(|_| HeleosError::Database)?;
+        match exists {
+            0 => Ok(false),
+            1 => Ok(true),
+            _ => Err(HeleosError::Integrity),
+        }
+    }
+
+    fn admission_record(&self, digest: Sha256Digest) -> Result<Option<AdmissionRecord>> {
+        let mut statement = self
+            .connection
+            .prepare(
+                "SELECT sha256, byte_length, media_type, admission_state, vault_key,
+                        quarantine_reason
+                 FROM content_objects WHERE sha256 = ?1",
+            )
+            .map_err(|_| HeleosError::Database)?;
+        let mut rows = statement
+            .query([digest.to_string()])
+            .map_err(|_| HeleosError::Database)?;
+        let Some(row) = rows.next().map_err(|_| HeleosError::Database)? else {
+            return Ok(None);
+        };
+        let parsed_digest = parse_digest_text(row, 0)?;
+        let byte_length = nonnegative_u64(row, 1)?;
+        let media_type = bounded_text(row, 2, 80)?;
+        let admission_state = bounded_text(row, 3, 16)?;
+        let vault_key = bounded_text(row, 4, 256)?;
+        let quarantine = match row.get_ref(5).map_err(|_| HeleosError::Database)? {
+            ValueRef::Null => None,
+            ValueRef::Text(bytes) if bytes.len() <= ONE_MIB => {
+                let value = serde_json::from_slice::<IntakeQuarantineV1>(bytes)
+                    .map_err(|_| HeleosError::Integrity)?;
+                if crate::ingest::evidence::bounded_canonical_json(&value, ONE_MIB)?.as_slice()
+                    != bytes
+                {
+                    return Err(HeleosError::Integrity);
+                }
+                Some(value)
+            }
+            _ => return Err(HeleosError::Integrity),
+        };
+        if rows.next().map_err(|_| HeleosError::Database)?.is_some() || parsed_digest != digest {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(Some(AdmissionRecord {
+            digest,
+            byte_length,
+            media_type,
+            admission_state,
+            vault_key,
+            quarantine,
+        }))
+    }
+
+    pub(crate) fn job_create_with_checkpoint_and_audit(
+        &mut self,
+        command: JobCreateCommand,
+    ) -> Result<()> {
+        command.input.validate()?;
+        command.budget.validate_for_input(&command.input)?;
+        command.checkpoint.validate_for_input(&command.input)?;
+        if command.input.project_id != command.project_id
+            || command.deadline_at_ms != checked_time_add(command.created_at_ms, JOB_DEADLINE_MS)?
+            || !matches!(
+                command.checkpoint.phase,
+                IngestCheckpointPhaseV1::PreflightRejected { .. }
+                    | IngestCheckpointPhaseV1::VaultPublished { .. }
+            )
+        {
+            return Err(HeleosError::Integrity);
+        }
+        let input_json = json_text(&command.input, ONE_MIB)?;
+        let budget_json = json_text(&command.budget, ONE_MIB)?;
+        let checkpoint_json = json_text(&command.checkpoint, EIGHT_MIB)?;
+        self.with_immediate_transaction(|transaction| {
+            transaction
+                .execute(
+                    "INSERT INTO job_runs
+                        (id, project_id, kind, idempotency_key, state, attempt,
+                         lease_owner, lease_expires_at_ms, deadline_at_ms, budget_json,
+                         input_json, checkpoint_json, terminal_reason, created_at_ms,
+                         updated_at_ms)
+                     VALUES (?1, ?2, 'pdf_ingest', ?3, 'queued', 0, NULL, NULL, ?4,
+                             ?5, ?6, ?7, NULL, ?8, ?8)",
+                    params![
+                        command.job_id.as_uuid().to_string(),
+                        command.project_id.as_uuid().to_string(),
+                        command.input.idempotency_key.as_str(),
+                        command.deadline_at_ms,
+                        budget_json,
+                        input_json,
+                        checkpoint_json,
+                        command.created_at_ms,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            append_action(
+                transaction,
+                AppendAction {
+                    id: command.audit_event_id,
+                    project_id: command.project_id,
+                    actor: command.actor,
+                    action: AuditAction::JobCreated,
+                    subject_type: AuditSubjectType::Job,
+                    subject_id: command.job_id.as_uuid().to_string(),
+                    before: JsonValue::Null,
+                    after: job_audit_snapshot(transaction, command.job_id)?,
+                    reason: "intake job created",
+                    occurred_at_ms: command.created_at_ms,
+                },
+            )?;
+            Ok(())
+        })
+    }
+
+    pub(crate) fn job_start_or_resume_with_audit(
+        &mut self,
+        command: JobStartCommand,
+    ) -> Result<u32> {
+        if command.lease_expires_at_ms != checked_time_add(command.now_ms, LEASE_DURATION_MS)? {
+            return Err(HeleosError::Integrity);
+        }
+        self.with_immediate_transaction(|transaction| {
+            let before = job_audit_snapshot(transaction, command.job_id)?;
+            let changed = transaction
+                .execute(
+                    "UPDATE job_runs
+                     SET state = 'running', attempt = 1, lease_owner = ?2,
+                         lease_expires_at_ms = ?3, updated_at_ms = ?4
+                     WHERE id = ?1 AND project_id = ?5 AND state = 'queued' AND attempt = 0
+                       AND deadline_at_ms > ?4",
+                    params![
+                        command.job_id.as_uuid().to_string(),
+                        command.lease_owner.hyphenated().to_string(),
+                        command.lease_expires_at_ms,
+                        command.now_ms,
+                        command.project_id.as_uuid().to_string(),
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            if changed != 1 {
+                return Err(HeleosError::InvalidStateTransition);
+            }
+            append_action(
+                transaction,
+                AppendAction {
+                    id: command.audit_event_id,
+                    project_id: command.project_id,
+                    actor: command.actor,
+                    action: AuditAction::JobStarted,
+                    subject_type: AuditSubjectType::Job,
+                    subject_id: command.job_id.as_uuid().to_string(),
+                    before,
+                    after: job_audit_snapshot(transaction, command.job_id)?,
+                    reason: "intake job started",
+                    occurred_at_ms: command.now_ms,
+                },
+            )?;
+            Ok(1)
+        })
+    }
+
+    pub(crate) fn job_recover_expired_attempt_with_audit(
+        &mut self,
+        command: JobRecoverCommand,
+    ) -> Result<JobRecoveryOutcome> {
+        self.with_immediate_transaction(|transaction| {
+            require_current_job_audit_anchor(transaction, command.job_id)?;
+            let record = job_record_by_id(transaction, command.job_id)?;
+            if record.input.expected_probe_provenance != command.expected_probe_provenance {
+                return Err(HeleosError::Integrity);
+            }
+            let project_id = record.input.project_id;
+            match record.state {
+                JobState::Succeeded => return Ok(JobRecoveryOutcome::Terminal(record)),
+                JobState::Failed | JobState::Cancelled | JobState::Interrupted => {
+                    return Err(HeleosError::InvalidStateTransition);
+                }
+                JobState::Queued => {
+                    let expires_at = match checked_time_add(command.now_ms, LEASE_DURATION_MS) {
+                        Ok(value) => value,
+                        Err(HeleosError::Integrity) if command.now_ms >= record.deadline_at_ms => {
+                            command.now_ms
+                        }
+                        Err(error) => return Err(error),
+                    };
+                    let before = job_audit_snapshot(transaction, command.job_id)?;
+                    let changed = transaction
+                        .execute(
+                            "UPDATE job_runs
+                             SET state = 'running', attempt = 1, lease_owner = ?2,
+                                 lease_expires_at_ms = ?3, updated_at_ms = ?4
+                             WHERE id = ?1 AND state = 'queued' AND attempt = 0",
+                            params![
+                                command.job_id.as_uuid().to_string(),
+                                command.lease_owner.hyphenated().to_string(),
+                                expires_at,
+                                command.now_ms,
+                            ],
+                        )
+                        .map_err(|_| HeleosError::Database)?;
+                    if changed != 1 {
+                        return Err(HeleosError::InvalidStateTransition);
+                    }
+                    append_action(
+                        transaction,
+                        AppendAction {
+                            id: command.audit_event_ids[0],
+                            project_id,
+                            actor: command.actor.clone(),
+                            action: AuditAction::JobStarted,
+                            subject_type: AuditSubjectType::Job,
+                            subject_id: command.job_id.as_uuid().to_string(),
+                            before,
+                            after: job_audit_snapshot(transaction, command.job_id)?,
+                            reason: "queued intake job started during resume",
+                            occurred_at_ms: command.now_ms,
+                        },
+                    )?;
+                    if command.now_ms >= record.deadline_at_ms {
+                        fail_job_in_transaction(
+                            transaction,
+                            FailJob {
+                                job_id: command.job_id,
+                                project_id,
+                                attempt: 1,
+                                actor: &command.actor,
+                                now_ms: command.now_ms,
+                                terminal_reason: "deadline_expired",
+                                audit_reason: "intake job deadline expired",
+                                audit_event_id: command.audit_event_ids[1],
+                            },
+                        )?;
+                        return Ok(JobRecoveryOutcome::DeadlineExpired);
+                    }
+                    return Ok(JobRecoveryOutcome::Started {
+                        record: job_record_by_id(transaction, command.job_id)?,
+                        interrupted_event_id: None,
+                    });
+                }
+                JobState::Running => {}
+            }
+
+            if command.now_ms >= record.deadline_at_ms {
+                fail_job_in_transaction(
+                    transaction,
+                    FailJob {
+                        job_id: command.job_id,
+                        project_id,
+                        attempt: record.attempt,
+                        actor: &command.actor,
+                        now_ms: command.now_ms,
+                        terminal_reason: "deadline_expired",
+                        audit_reason: "intake job deadline expired",
+                        audit_event_id: command.audit_event_ids[0],
+                    },
+                )?;
+                return Ok(JobRecoveryOutcome::DeadlineExpired);
+            }
+            if record.lease_expires_at_ms.ok_or(HeleosError::Integrity)? > command.now_ms {
+                return Err(HeleosError::LeaseUnavailable);
+            }
+            if record.attempt == MAX_JOB_ATTEMPTS {
+                fail_job_in_transaction(
+                    transaction,
+                    FailJob {
+                        job_id: command.job_id,
+                        project_id,
+                        attempt: record.attempt,
+                        actor: &command.actor,
+                        now_ms: command.now_ms,
+                        terminal_reason: "attempt_limit",
+                        audit_reason: "intake job attempt limit reached",
+                        audit_event_id: command.audit_event_ids[0],
+                    },
+                )?;
+                return Ok(JobRecoveryOutcome::AttemptLimit);
+            }
+
+            let before = job_audit_snapshot(transaction, command.job_id)?;
+            let changed = transaction
+                .execute(
+                    "UPDATE job_runs
+                     SET state = 'interrupted', lease_owner = NULL, lease_expires_at_ms = NULL,
+                         updated_at_ms = ?2
+                     WHERE id = ?1 AND state = 'running' AND attempt = ?3",
+                    params![
+                        command.job_id.as_uuid().to_string(),
+                        command.now_ms,
+                        i64::from(record.attempt),
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            if changed != 1 {
+                return Err(HeleosError::InvalidStateTransition);
+            }
+            let checkpoint_json = json_text(&record.checkpoint, EIGHT_MIB)?;
+            let details_json = json_text(
+                &IngestEventDetailV1::Interrupted {
+                    schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
+                    attempt: record.attempt,
+                    checkpoint_sha256: sha256_text(&checkpoint_json),
+                },
+                ONE_MIB,
+            )?;
+            transaction
+                .execute(
+                    "INSERT INTO ingest_events
+                        (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
+                         source_path, idempotency_key, actor, terminal_at_ms, details_json)
+                     VALUES (?1, ?2, ?3, NULL, 'interrupted', ?4, ?5, '<redacted>',
+                             ?6, ?7, ?8, ?9)",
+                    params![
+                        command.interrupted_event_id.as_uuid().to_string(),
+                        project_id.as_uuid().to_string(),
+                        command.job_id.as_uuid().to_string(),
+                        i64::from(record.attempt),
+                        record.input.source_display,
+                        record.input.idempotency_key.as_str(),
+                        command.actor.as_str(),
+                        command.now_ms,
+                        details_json,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            append_action(
+                transaction,
+                AppendAction {
+                    id: command.audit_event_ids[0],
+                    project_id,
+                    actor: command.actor.clone(),
+                    action: AuditAction::JobInterrupted,
+                    subject_type: AuditSubjectType::Job,
+                    subject_id: command.job_id.as_uuid().to_string(),
+                    before,
+                    after: job_audit_snapshot(transaction, command.job_id)?,
+                    reason: "expired intake attempt interrupted",
+                    occurred_at_ms: command.now_ms,
+                },
+            )?;
+            let interrupted = job_audit_snapshot(transaction, command.job_id)?;
+            let next_attempt = record
+                .attempt
+                .checked_add(1)
+                .ok_or(HeleosError::Integrity)?;
+            let expires_at = checked_time_add(command.now_ms, LEASE_DURATION_MS)?;
+            let changed = transaction
+                .execute(
+                    "UPDATE job_runs
+                     SET state = 'running', attempt = ?2, lease_owner = ?3,
+                         lease_expires_at_ms = ?4, updated_at_ms = ?5
+                     WHERE id = ?1 AND state = 'interrupted' AND attempt = ?6",
+                    params![
+                        command.job_id.as_uuid().to_string(),
+                        i64::from(next_attempt),
+                        command.lease_owner.hyphenated().to_string(),
+                        expires_at,
+                        command.now_ms,
+                        i64::from(record.attempt),
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            if changed != 1 {
+                return Err(HeleosError::InvalidStateTransition);
+            }
+            append_action(
+                transaction,
+                AppendAction {
+                    id: command.audit_event_ids[1],
+                    project_id,
+                    actor: command.actor,
+                    action: AuditAction::JobResumed,
+                    subject_type: AuditSubjectType::Job,
+                    subject_id: command.job_id.as_uuid().to_string(),
+                    before: interrupted,
+                    after: job_audit_snapshot(transaction, command.job_id)?,
+                    reason: "interrupted intake job resumed",
+                    occurred_at_ms: command.now_ms,
+                },
+            )?;
+            Ok(JobRecoveryOutcome::Started {
+                record: job_record_by_id(transaction, command.job_id)?,
+                interrupted_event_id: Some(command.interrupted_event_id),
+            })
+        })
+    }
+
+    pub(crate) fn job_fail_with_audit(&mut self, command: JobFailCommand) -> Result<()> {
+        self.with_immediate_transaction(|transaction| {
+            require_current_job_audit_anchor(transaction, command.job_id)?;
+            fail_job_in_transaction(
+                transaction,
+                FailJob {
+                    job_id: command.job_id,
+                    project_id: command.project_id,
+                    attempt: command.attempt,
+                    actor: &command.actor,
+                    now_ms: command.now_ms,
+                    terminal_reason: "internal_failure",
+                    audit_reason: "trusted intake processing failure",
+                    audit_event_id: command.audit_event_id,
+                },
+            )
+        })
+    }
+
+    pub(crate) fn job_checkpoint_with_audit(
+        &mut self,
+        command: JobCheckpointCommand,
+    ) -> Result<()> {
+        if !matches!(
+            &command.checkpoint.phase,
+            IngestCheckpointPhaseV1::ProcessingComplete { .. }
+                | IngestCheckpointPhaseV1::PreflightRejected {
+                    content_sha256: Some(_),
+                    quarantine: IntakeQuarantineV1 {
+                        reason: crate::IntakeQuarantineReasonV1::ProjectAssociationQuota,
+                        ..
+                    },
+                    ..
+                }
+        ) {
+            return Err(HeleosError::Integrity);
+        }
+        command.checkpoint.validate()?;
+        let checkpoint_json = json_text(&command.checkpoint, EIGHT_MIB)?;
+        self.with_immediate_transaction(|transaction| {
+            let before = job_audit_snapshot(transaction, command.job_id)?;
+            let input = transaction
+                .query_row(
+                    "SELECT input_json FROM job_runs WHERE id = ?1 AND project_id = ?2",
+                    params![
+                        command.job_id.as_uuid().to_string(),
+                        command.project_id.as_uuid().to_string(),
+                    ],
+                    |row| {
+                        parse_bounded_json::<IngestInputV1>(row, 0, ONE_MIB)
+                            .map_err(|_| rusqlite::Error::InvalidQuery)
+                    },
+                )
+                .map_err(|_| HeleosError::Integrity)?;
+            command.checkpoint.validate_for_input(&input)?;
+            let changed = transaction
+                .execute(
+                    "UPDATE job_runs SET checkpoint_json = ?3, updated_at_ms = ?4
+                     WHERE id = ?1 AND project_id = ?2 AND state = 'running'
+                       AND attempt = ?5",
+                    params![
+                        command.job_id.as_uuid().to_string(),
+                        command.project_id.as_uuid().to_string(),
+                        checkpoint_json,
+                        command.now_ms,
+                        i64::from(command.attempt),
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            if changed != 1 {
+                return Err(HeleosError::InvalidStateTransition);
+            }
+            append_action(
+                transaction,
+                AppendAction {
+                    id: command.audit_event_id,
+                    project_id: command.project_id,
+                    actor: command.actor,
+                    action: AuditAction::JobCheckpointed,
+                    subject_type: AuditSubjectType::Job,
+                    subject_id: command.job_id.as_uuid().to_string(),
+                    before,
+                    after: job_audit_snapshot(transaction, command.job_id)?,
+                    reason: "intake processing checkpoint committed",
+                    occurred_at_ms: command.now_ms,
+                },
+            )?;
+            Ok(())
+        })
+    }
+
+    pub(crate) fn accepted_intake_commit(
+        &mut self,
+        command: AcceptedIntakeCommand,
+    ) -> Result<IngestReceipt> {
+        command.original.validate()?;
+        command.manifest_object.validate()?;
+        command.manifest.validate()?;
+        if command.original.digest != command.manifest.original.sha256
+            || command.original.byte_length != command.manifest.original.byte_length
+            || command.manifest_object.digest
+                != Sha256Digest::hash_reader(command.manifest.canonical_bytes()?.as_slice())?
+            || command.manifest_object.byte_length
+                != u64::try_from(command.manifest.canonical_bytes()?.len())
+                    .map_err(|_| HeleosError::Integrity)?
+            || command.pages != command.manifest.pages
+            || command.source_display.is_empty()
+            || command.source_display.len() > 4096
+        {
+            return Err(HeleosError::Integrity);
+        }
+        let parameters = EvidenceParametersV1::new(
+            command.manifest.probe_provenance.clone(),
+            command.manifest.requested_limits,
+        )?;
+        let parameters_json = json_text(&parameters, ONE_MIB)?;
+        let manifest = command.manifest.clone();
+        let original = command.original.clone();
+        let manifest_object = command.manifest_object.clone();
+        self.with_immediate_transaction(|transaction| {
+            require_accepted_job_authority(transaction, &command)?;
+            let before_job = job_audit_snapshot(transaction, command.job_id)?;
+            insert_or_verify_content_object(
+                transaction,
+                &original,
+                PDF_MEDIA_TYPE,
+                "accepted",
+                None,
+                command.now_ms,
+                &command.actor,
+            )?;
+            insert_or_verify_content_object(
+                transaction,
+                &manifest_object,
+                EVIDENCE_MANIFEST_MEDIA_TYPE,
+                "accepted",
+                None,
+                command.now_ms,
+                &command.actor,
+            )?;
+
+            let document_id = manifest.document_id;
+            let revision_id = manifest.revision_id;
+            transaction
+                .execute(
+                    "INSERT OR IGNORE INTO documents (id, created_at_ms, created_by)
+                     VALUES (?1, ?2, ?3)",
+                    params![
+                        document_id.as_digest().to_string(),
+                        command.now_ms,
+                        command.actor.as_str(),
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            let revision_inserted = transaction
+                .execute(
+                    "INSERT OR IGNORE INTO document_revisions
+                        (id, document_id, content_sha256, created_at_ms, created_by)
+                     VALUES (?1, ?2, ?3, ?4, ?5)",
+                    params![
+                        revision_id.as_digest().to_string(),
+                        document_id.as_digest().to_string(),
+                        original.digest.to_string(),
+                        command.now_ms,
+                        command.actor.as_str(),
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?
+                == 1;
+            require_revision_authority(transaction, document_id, revision_id, original.digest)?;
+            transaction
+                .execute(
+                    "INSERT OR IGNORE INTO project_documents
+                        (project_id, document_id, linked_at_ms, linked_by)
+                     VALUES (?1, ?2, ?3, ?4)",
+                    params![
+                        command.project_id.as_uuid().to_string(),
+                        document_id.as_digest().to_string(),
+                        command.now_ms,
+                        command.actor.as_str(),
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+
+            for page in &command.pages {
+                let transform_json = json_text(&page.transform, ONE_MIB)?;
+                transaction
+                    .execute(
+                        "INSERT OR IGNORE INTO sheets
+                            (id, revision_id, zero_based_page_index, width_micropoints,
+                             height_micropoints, rotation_degrees, unit,
+                             parent_content_sha256, transform_json)
+                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'pt', ?7, ?8)",
+                        params![
+                            page.page_id.as_digest().to_string(),
+                            revision_id.as_digest().to_string(),
+                            i64::from(page.index),
+                            i64::try_from(page.width_micropoints)
+                                .map_err(|_| HeleosError::Integrity)?,
+                            i64::try_from(page.height_micropoints)
+                                .map_err(|_| HeleosError::Integrity)?,
+                            i64::from(page.rotation_degrees),
+                            original.digest.to_string(),
+                            transform_json,
+                        ],
+                    )
+                    .map_err(|_| HeleosError::Database)?;
+            }
+            require_sheet_authority(transaction, revision_id, original.digest, &command.pages)?;
+
+            let evidence_inserted = transaction
+                .execute(
+                    "INSERT OR IGNORE INTO evidence_objects
+                        (id, project_id, job_id, document_revision_id, content_sha256,
+                         parent_content_sha256, extraction_method, parameters_json,
+                         review_state, created_at_ms)
+                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'heleos.pdf-probe/v1', ?7,
+                             'accepted', ?8)",
+                    params![
+                        command.evidence_id.as_uuid().to_string(),
+                        command.project_id.as_uuid().to_string(),
+                        command.job_id.as_uuid().to_string(),
+                        revision_id.as_digest().to_string(),
+                        manifest_object.digest.to_string(),
+                        original.digest.to_string(),
+                        parameters_json,
+                        command.now_ms,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?
+                == 1;
+            let lineages = evidence_lineages(transaction, revision_id)?;
+            let current_evidence_id = lineages
+                .iter()
+                .find(|lineage| lineage.project_id == command.project_id)
+                .ok_or(HeleosError::Integrity)?
+                .evidence_id;
+            require_evidence_authority(
+                transaction,
+                command.project_id,
+                revision_id,
+                manifest_object.digest,
+                original.digest,
+                &parameters_json,
+            )?;
+
+            let outcome = if revision_inserted {
+                IngestOutcome::AcceptedNew
+            } else {
+                IngestOutcome::AcceptedDuplicate
+            };
+            let mut preexisting = command.preexisting_vault_digests.clone();
+            preexisting.sort_unstable();
+            preexisting.dedup();
+            let receipt = IngestReceipt {
+                ingest_event_id: command.ingest_event_id,
+                authoritative_job_id: command.job_id,
+                attempt: command.attempt,
+                outcome,
+                content_sha256: Some(original.digest),
+                byte_length: original.byte_length,
+                quarantine: None,
+                document_id: Some(document_id),
+                revision_id: Some(revision_id),
+                sheet_ids: command.pages.iter().map(|page| page.page_id).collect(),
+                evidence_manifest: Some(EvidenceManifestReceipt {
+                    manifest: manifest.clone(),
+                    manifest_content_sha256: manifest_object.digest,
+                    manifest_byte_length: manifest_object.byte_length,
+                    manifest_media_type: EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
+                    manifest_vault_key: manifest_object.vault_key.clone(),
+                    original_vault_key: original.vault_key.clone(),
+                    lineages,
+                }),
+                preexisting_vault_digests: preexisting,
+            };
+            receipt.validate()?;
+            let checkpoint = IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::Terminal {
+                    receipt: receipt.clone(),
+                },
+            };
+            let checkpoint_json = json_text(&checkpoint, EIGHT_MIB)?;
+            let details_json = json_text(
+                &IngestEventDetailV1::Accepted {
+                    schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
+                    attempt: command.attempt,
+                    document_id,
+                    revision_id,
+                    evidence_id: current_evidence_id,
+                },
+                ONE_MIB,
+            )?;
+            let source_metadata =
+                json_text(&json!({"schema": "heleos.source-record/v1"}), ONE_MIB)?;
+            transaction
+                .execute(
+                    "INSERT INTO source_records
+                        (id, project_id, job_id, source_name, source_path, content_sha256,
+                         metadata_json, created_at_ms)
+                     VALUES (?1, ?2, ?3, ?4, '<redacted>', ?5, ?6, ?7)",
+                    params![
+                        command.source_record_id,
+                        command.project_id.as_uuid().to_string(),
+                        command.job_id.as_uuid().to_string(),
+                        command.source_display,
+                        original.digest.to_string(),
+                        source_metadata,
+                        command.now_ms,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            transaction
+                .execute(
+                    "INSERT INTO ingest_events
+                        (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
+                         source_path, idempotency_key, actor, terminal_at_ms, details_json)
+                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, '<redacted>', ?8, ?9, ?10, ?11)",
+                    params![
+                        command.ingest_event_id.as_uuid().to_string(),
+                        command.project_id.as_uuid().to_string(),
+                        command.job_id.as_uuid().to_string(),
+                        original.digest.to_string(),
+                        ingest_outcome_text(outcome),
+                        i64::from(command.attempt),
+                        command.source_display,
+                        command.idempotency_key.as_str(),
+                        command.actor.as_str(),
+                        command.now_ms,
+                        details_json,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            let changed = transaction
+                .execute(
+                    "UPDATE job_runs
+                     SET state = 'succeeded', lease_owner = NULL, lease_expires_at_ms = NULL,
+                         checkpoint_json = ?4, terminal_reason = 'completed', updated_at_ms = ?5
+                     WHERE id = ?1 AND project_id = ?2 AND state = 'running' AND attempt = ?3",
+                    params![
+                        command.job_id.as_uuid().to_string(),
+                        command.project_id.as_uuid().to_string(),
+                        i64::from(command.attempt),
+                        checkpoint_json,
+                        command.now_ms,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            if changed != 1 {
+                return Err(HeleosError::InvalidStateTransition);
+            }
+            append_action(
+                transaction,
+                AppendAction {
+                    id: command.audit_event_ids[0],
+                    project_id: command.project_id,
+                    actor: command.actor.clone(),
+                    action: AuditAction::JobSucceeded,
+                    subject_type: AuditSubjectType::Job,
+                    subject_id: command.job_id.as_uuid().to_string(),
+                    before: before_job,
+                    after: job_audit_snapshot(transaction, command.job_id)?,
+                    reason: "intake job completed",
+                    occurred_at_ms: command.now_ms,
+                },
+            )?;
+            append_action(
+                transaction,
+                AppendAction {
+                    id: command.audit_event_ids[1],
+                    project_id: command.project_id,
+                    actor: command.actor.clone(),
+                    action: AuditAction::IngestAccepted,
+                    subject_type: AuditSubjectType::IngestAttempt,
+                    subject_id: command.ingest_event_id.as_uuid().to_string(),
+                    before: JsonValue::Null,
+                    after: json!({
+                        "attempt": command.attempt,
+                        "authoritative_job_id": command.job_id,
+                        "content_sha256": original.digest,
+                        "document_id": document_id,
+                        "evidence_id": current_evidence_id,
+                        "ingest_event_id": command.ingest_event_id,
+                        "manifest_content_sha256": manifest_object.digest,
+                        "revision_id": revision_id,
+                    }),
+                    reason: "PDF intake accepted",
+                    occurred_at_ms: command.now_ms,
+                },
+            )?;
+            if evidence_inserted {
+                append_action(
+                    transaction,
+                    AppendAction {
+                        id: command.audit_event_ids[2],
+                        project_id: command.project_id,
+                        actor: command.actor.clone(),
+                        action: AuditAction::EvidenceCreated,
+                        subject_type: AuditSubjectType::Evidence,
+                        subject_id: current_evidence_id.as_uuid().to_string(),
+                        before: JsonValue::Null,
+                        after: json!({
+                            "evidence_id": current_evidence_id,
+                            "manifest_content_sha256": manifest_object.digest,
+                            "revision_id": revision_id,
+                        }),
+                        reason: "canonical PDF evidence created",
+                        occurred_at_ms: command.now_ms,
+                    },
+                )?;
+            }
+            Ok(receipt)
+        })
+    }
+
+    pub(crate) fn quarantined_intake_commit(
+        &mut self,
+        command: QuarantinedIntakeCommand,
+    ) -> Result<IngestReceipt> {
+        command.quarantine.validate()?;
+        if command.source_display.is_empty()
+            || command.source_display.len() > 4096
+            || command.source_display.chars().any(char::is_control)
+            || command.source_record_id.is_empty()
+            || command.source_record_id.len() > 256
+            || command.outcome
+                != crate::ingest::job::quarantine_ingest_outcome(&command.quarantine.reason)
+        {
+            return Err(HeleosError::Integrity);
+        }
+        if let Some(retained) = &command.retained {
+            retained.validate()?;
+            if command.content_sha256 != Some(retained.digest)
+                || command.byte_length != retained.byte_length
+                || !matches!(
+                    command.quarantine.reason,
+                    crate::IntakeQuarantineReasonV1::Pdf(_)
+                        | crate::IntakeQuarantineReasonV1::EvidenceManifestQuota
+                )
+            {
+                return Err(HeleosError::Integrity);
+            }
+        } else if matches!(
+            command.quarantine.reason,
+            crate::IntakeQuarantineReasonV1::Pdf(_)
+                | crate::IntakeQuarantineReasonV1::EvidenceManifestQuota
+        ) {
+            return Err(HeleosError::Integrity);
+        }
+        let quarantine_json = json_text(&command.quarantine, ONE_MIB)?;
+        self.with_immediate_transaction(|transaction| {
+            require_quarantined_job_authority(transaction, &command)?;
+            let before_job = job_audit_snapshot(transaction, command.job_id)?;
+            if let Some(retained) = &command.retained {
+                insert_or_verify_content_object(
+                    transaction,
+                    retained,
+                    PDF_MEDIA_TYPE,
+                    "quarantined",
+                    Some(&quarantine_json),
+                    command.now_ms,
+                    &command.actor,
+                )?;
+            }
+            let mut preexisting = command.preexisting_vault_digests.clone();
+            preexisting.sort_unstable();
+            preexisting.dedup();
+            let receipt = IngestReceipt {
+                ingest_event_id: command.ingest_event_id,
+                authoritative_job_id: command.job_id,
+                attempt: command.attempt,
+                outcome: command.outcome,
+                content_sha256: command.content_sha256,
+                byte_length: command.byte_length,
+                quarantine: Some(command.quarantine.clone()),
+                document_id: None,
+                revision_id: None,
+                sheet_ids: Vec::new(),
+                evidence_manifest: None,
+                preexisting_vault_digests: preexisting,
+            };
+            receipt.validate()?;
+            let terminal_checkpoint = IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::Terminal {
+                    receipt: receipt.clone(),
+                },
+            };
+            let checkpoint_json = json_text(&terminal_checkpoint, EIGHT_MIB)?;
+            let source_metadata =
+                json_text(&json!({"schema": "heleos.source-record/v1"}), ONE_MIB)?;
+            let retained_digest = command
+                .retained
+                .as_ref()
+                .map(|object| object.digest.to_string());
+            transaction
+                .execute(
+                    "INSERT INTO source_records
+                        (id, project_id, job_id, source_name, source_path, content_sha256,
+                         metadata_json, created_at_ms)
+                     VALUES (?1, ?2, ?3, ?4, '<redacted>', ?5, ?6, ?7)",
+                    params![
+                        command.source_record_id,
+                        command.project_id.as_uuid().to_string(),
+                        command.job_id.as_uuid().to_string(),
+                        command.source_display,
+                        retained_digest,
+                        source_metadata,
+                        command.now_ms,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            let details_json = json_text(
+                &IngestEventDetailV1::Quarantined {
+                    schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
+                    attempt: command.attempt,
+                    content_sha256: command.content_sha256,
+                    byte_length: command.byte_length,
+                    quarantine: command.quarantine.clone(),
+                },
+                ONE_MIB,
+            )?;
+            transaction
+                .execute(
+                    "INSERT INTO ingest_events
+                        (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
+                         source_path, idempotency_key, actor, terminal_at_ms, details_json)
+                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, '<redacted>', ?8, ?9, ?10, ?11)",
+                    params![
+                        command.ingest_event_id.as_uuid().to_string(),
+                        command.project_id.as_uuid().to_string(),
+                        command.job_id.as_uuid().to_string(),
+                        retained_digest,
+                        ingest_outcome_text(command.outcome),
+                        i64::from(command.attempt),
+                        command.source_display,
+                        command.idempotency_key.as_str(),
+                        command.actor.as_str(),
+                        command.now_ms,
+                        details_json,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            let changed = transaction
+                .execute(
+                    "UPDATE job_runs
+                     SET state = 'succeeded', lease_owner = NULL, lease_expires_at_ms = NULL,
+                         checkpoint_json = ?4, terminal_reason = 'completed', updated_at_ms = ?5
+                     WHERE id = ?1 AND project_id = ?2 AND state = 'running' AND attempt = ?3",
+                    params![
+                        command.job_id.as_uuid().to_string(),
+                        command.project_id.as_uuid().to_string(),
+                        i64::from(command.attempt),
+                        checkpoint_json,
+                        command.now_ms,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            if changed != 1 {
+                return Err(HeleosError::InvalidStateTransition);
+            }
+            append_action(
+                transaction,
+                AppendAction {
+                    id: command.audit_event_ids[0],
+                    project_id: command.project_id,
+                    actor: command.actor.clone(),
+                    action: AuditAction::JobSucceeded,
+                    subject_type: AuditSubjectType::Job,
+                    subject_id: command.job_id.as_uuid().to_string(),
+                    before: before_job,
+                    after: job_audit_snapshot(transaction, command.job_id)?,
+                    reason: "intake job completed",
+                    occurred_at_ms: command.now_ms,
+                },
+            )?;
+            append_action(
+                transaction,
+                AppendAction {
+                    id: command.audit_event_ids[1],
+                    project_id: command.project_id,
+                    actor: command.actor,
+                    action: AuditAction::IngestQuarantined,
+                    subject_type: AuditSubjectType::IngestAttempt,
+                    subject_id: command.ingest_event_id.as_uuid().to_string(),
+                    before: JsonValue::Null,
+                    after: json!({
+                        "attempt": command.attempt,
+                        "authoritative_job_id": command.job_id,
+                        "content_sha256": command.content_sha256,
+                        "ingest_event_id": command.ingest_event_id,
+                        "quarantine_sha256": sha256_text(&quarantine_json),
+                    }),
+                    reason: "PDF intake quarantined",
+                    occurred_at_ms: command.now_ms,
+                },
+            )?;
+            Ok(receipt)
+        })
+    }
+
+    pub(crate) fn replay_attempt_commit(
+        &mut self,
+        command: ReplayAttemptCommand,
+    ) -> Result<IngestReceipt> {
+        let records_ingest_attempt = match (
+            command.source_display.as_ref(),
+            command.ingest_event_id,
+            command.source_record_id.as_ref(),
+        ) {
+            (Some(source), Some(_), Some(record))
+                if !source.is_empty()
+                    && source.len() <= 4096
+                    && !source.chars().any(char::is_control)
+                    && !record.is_empty()
+                    && record.len() <= 256 =>
+            {
+                true
+            }
+            (None, None, None) if command.preexisting_vault_digests.is_empty() => false,
+            _ => return Err(HeleosError::Integrity),
+        };
+        self.with_immediate_transaction(|transaction| {
+            let record = job_record_by_id(transaction, command.authoritative_job_id)?;
+            record.validate_for_scope(command.project_id, &command.idempotency_key)?;
+            if record.state != JobState::Succeeded {
+                return Err(HeleosError::InvalidStateTransition);
+            }
+            let IngestCheckpointPhaseV1::Terminal {
+                receipt: authoritative,
+            } = &record.checkpoint.phase
+            else {
+                return Err(HeleosError::Integrity);
+            };
+            let authoritative_content = authoritative_event_content(
+                transaction,
+                command.project_id,
+                command.authoritative_job_id,
+                authoritative,
+            )?;
+            if !records_ingest_attempt {
+                append_action(
+                    transaction,
+                    AppendAction {
+                        id: command.audit_event_id,
+                        project_id: command.project_id,
+                        actor: command.actor,
+                        action: AuditAction::IngestReplayed,
+                        subject_type: AuditSubjectType::IngestAttempt,
+                        subject_id: authoritative.ingest_event_id.as_uuid().to_string(),
+                        before: JsonValue::Null,
+                        after: json!({
+                            "authoritative_attempt": authoritative.attempt,
+                            "authoritative_event_id": authoritative.ingest_event_id,
+                            "authoritative_job_id": authoritative.authoritative_job_id,
+                            "checkpoint_sha256": sha256_text(&json_text(&record.checkpoint, EIGHT_MIB)?),
+                        }),
+                        reason: "terminal intake result observed during resume",
+                        occurred_at_ms: command.now_ms,
+                    },
+                )?;
+                return Ok(authoritative.clone());
+            }
+
+            let ingest_event_id = command.ingest_event_id.ok_or(HeleosError::Integrity)?;
+            let source_record_id = command.source_record_id.ok_or(HeleosError::Integrity)?;
+            let source_display = command.source_display.ok_or(HeleosError::Integrity)?;
+            let mut receipt = authoritative.clone();
+            receipt.ingest_event_id = ingest_event_id;
+            receipt.outcome = IngestOutcome::IdempotentReplay;
+            receipt.preexisting_vault_digests = command.preexisting_vault_digests;
+            receipt.validate()?;
+            let source_metadata =
+                json_text(&json!({"schema": "heleos.source-record/v1"}), ONE_MIB)?;
+            transaction
+                .execute(
+                    "INSERT INTO source_records
+                        (id, project_id, job_id, source_name, source_path, content_sha256,
+                         metadata_json, created_at_ms)
+                     VALUES (?1, ?2, ?3, ?4, '<redacted>', ?5, ?6, ?7)",
+                    params![
+                        source_record_id,
+                        command.project_id.as_uuid().to_string(),
+                        command.authoritative_job_id.as_uuid().to_string(),
+                        source_display,
+                        authoritative_content.map(|digest| digest.to_string()),
+                        source_metadata,
+                        command.now_ms,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            let details_json = json_text(
+                &IngestEventDetailV1::Replay {
+                    schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
+                    authoritative_job_id: command.authoritative_job_id,
+                    authoritative_attempt: authoritative.attempt,
+                    authoritative_event_id: authoritative.ingest_event_id,
+                },
+                ONE_MIB,
+            )?;
+            transaction
+                .execute(
+                    "INSERT INTO ingest_events
+                        (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
+                         source_path, idempotency_key, actor, terminal_at_ms, details_json)
+                     VALUES (?1, ?2, ?3, ?4, 'idempotent_replay', NULL, ?5, '<redacted>',
+                             ?6, ?7, ?8, ?9)",
+                    params![
+                        ingest_event_id.as_uuid().to_string(),
+                        command.project_id.as_uuid().to_string(),
+                        command.authoritative_job_id.as_uuid().to_string(),
+                        authoritative_content.map(|digest| digest.to_string()),
+                        source_display,
+                        command.idempotency_key.as_str(),
+                        command.actor.as_str(),
+                        command.now_ms,
+                        details_json,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            append_action(
+                transaction,
+                AppendAction {
+                    id: command.audit_event_id,
+                    project_id: command.project_id,
+                    actor: command.actor,
+                    action: AuditAction::IngestReplayed,
+                    subject_type: AuditSubjectType::IngestAttempt,
+                    subject_id: ingest_event_id.as_uuid().to_string(),
+                    before: JsonValue::Null,
+                    after: json!({
+                        "authoritative_attempt": authoritative.attempt,
+                        "authoritative_event_id": authoritative.ingest_event_id,
+                        "authoritative_job_id": command.authoritative_job_id,
+                        "ingest_event_id": ingest_event_id,
+                    }),
+                    reason: "completed intake replayed",
+                    occurred_at_ms: command.now_ms,
+                },
+            )?;
+            Ok(receipt)
+        })
+    }
+
+    pub(crate) fn conflict_attempt_commit(
+        &mut self,
+        command: ConflictAttemptCommand,
+    ) -> Result<()> {
+        command.submitted_input.validate()?;
+        command
+            .submitted_budget
+            .validate_for_input(&command.submitted_input)?;
+        if command.source_display.is_empty()
+            || command.source_display.len() > 4096
+            || command.source_display.chars().any(char::is_control)
+            || command.source_record_id.is_empty()
+            || command.source_record_id.len() > 256
+        {
+            return Err(HeleosError::Integrity);
+        }
+        self.with_immediate_transaction(|transaction| {
+            let authoritative = job_record_by_id(transaction, command.authoritative_job_id)?;
+            if authoritative.input.project_id != command.project_id
+                || authoritative.input.idempotency_key != command.submitted_input.idempotency_key
+            {
+                return Err(HeleosError::Integrity);
+            }
+            let mismatching_fields = authoritative.input.frozen_mismatching_fields(
+                &command.submitted_input,
+                authoritative.budget == command.submitted_budget,
+            );
+            if mismatching_fields.is_empty() {
+                return Err(HeleosError::Integrity);
+            }
+            let details_json = json_text(
+                &IngestEventDetailV1::Conflict {
+                    schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
+                    authoritative_job_id: command.authoritative_job_id,
+                    mismatching_fields: mismatching_fields.clone(),
+                },
+                ONE_MIB,
+            )?;
+            let source_metadata =
+                json_text(&json!({"schema": "heleos.source-record/v1"}), ONE_MIB)?;
+            transaction
+                .execute(
+                    "INSERT INTO source_records
+                        (id, project_id, job_id, source_name, source_path, content_sha256,
+                         metadata_json, created_at_ms)
+                     VALUES (?1, ?2, ?3, ?4, '<redacted>', NULL, ?5, ?6)",
+                    params![
+                        command.source_record_id,
+                        command.project_id.as_uuid().to_string(),
+                        command.authoritative_job_id.as_uuid().to_string(),
+                        command.source_display,
+                        source_metadata,
+                        command.now_ms,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            transaction
+                .execute(
+                    "INSERT INTO ingest_events
+                        (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
+                         source_path, idempotency_key, actor, terminal_at_ms, details_json)
+                     VALUES (?1, ?2, ?3, NULL, 'denied_conflict', NULL, ?4, '<redacted>',
+                             ?5, ?6, ?7, ?8)",
+                    params![
+                        command.ingest_event_id.as_uuid().to_string(),
+                        command.project_id.as_uuid().to_string(),
+                        command.authoritative_job_id.as_uuid().to_string(),
+                        command.source_display,
+                        command.submitted_input.idempotency_key.as_str(),
+                        command.actor.as_str(),
+                        command.now_ms,
+                        details_json,
+                    ],
+                )
+                .map_err(|_| HeleosError::Database)?;
+            append_action(
+                transaction,
+                AppendAction {
+                    id: command.audit_event_id,
+                    project_id: command.project_id,
+                    actor: command.actor,
+                    action: AuditAction::IngestConflictDenied,
+                    subject_type: AuditSubjectType::IngestAttempt,
+                    subject_id: command.ingest_event_id.as_uuid().to_string(),
+                    before: JsonValue::Null,
+                    after: json!({
+                        "authoritative_job_id": command.authoritative_job_id,
+                        "ingest_event_id": command.ingest_event_id,
+                        "mismatching_fields": mismatching_fields,
+                    }),
+                    reason: "idempotency conflict denied",
+                    occurred_at_ms: command.now_ms,
+                },
+            )?;
+            Ok(())
+        })
+    }
+
+    pub(crate) fn revision_evidence_rows(
+        &self,
+        revision_id: RevisionId,
+    ) -> Result<RevisionEvidenceRows> {
+        let count = self
+            .connection
+            .query_row(
+                "SELECT COUNT(*) FROM evidence_objects
+                 WHERE document_revision_id = ?1",
+                [revision_id.as_digest().to_string()],
+                |row| row.get::<_, i64>(0),
+            )
+            .map_err(|_| HeleosError::Database)?;
+        let count = usize::try_from(count).map_err(|_| HeleosError::Integrity)?;
+        if count == 0 {
+            return Err(HeleosError::NotFound);
+        }
+        if count > 100_000 {
+            return Err(HeleosError::ResourceLimit);
+        }
+        let page_count = self
+            .connection
+            .query_row(
+                "SELECT COUNT(*) FROM sheets WHERE revision_id = ?1",
+                [revision_id.as_digest().to_string()],
+                |row| row.get::<_, i64>(0),
+            )
+            .map_err(|_| HeleosError::Database)?;
+        let page_count = usize::try_from(page_count).map_err(|_| HeleosError::Integrity)?;
+        if page_count == 0 || page_count > 10_000 {
+            return Err(if page_count == 0 {
+                HeleosError::Integrity
+            } else {
+                HeleosError::ResourceLimit
+            });
+        }
+        let evidence = self.revision_evidence_rows_after_count(revision_id, count)?;
+        let pages = self.revision_page_rows_after_count(revision_id, page_count)?;
+        Ok(RevisionEvidenceRows { evidence, pages })
+    }
+
+    fn revision_page_rows_after_count(
+        &self,
+        revision_id: RevisionId,
+        count: usize,
+    ) -> Result<Vec<PageMetadata>> {
+        if count == 0 || count > 10_000 {
+            return Err(HeleosError::Integrity);
+        }
+        let fetch_limit = count.checked_add(1).ok_or(HeleosError::Integrity)?;
+        let mut statement = self
+            .connection
+            .prepare(
+                "SELECT id, zero_based_page_index, width_micropoints,
+                        height_micropoints, unit, rotation_degrees,
+                        transform_json, parent_content_sha256
+                 FROM sheets
+                 WHERE revision_id = ?1
+                 ORDER BY zero_based_page_index, id
+                 LIMIT ?2",
+            )
+            .map_err(|_| HeleosError::Database)?;
+        let mut rows = statement
+            .query(params![
+                revision_id.as_digest().to_string(),
+                i64::try_from(fetch_limit).map_err(|_| HeleosError::Integrity)?,
+            ])
+            .map_err(|_| HeleosError::Database)?;
+        let mut pages = Vec::with_capacity(count);
+        while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+            if pages.len() == count {
+                return Err(HeleosError::Integrity);
+            }
+            if bounded_text(row, 4, 2)? != "pt"
+                || parse_digest_text(row, 7)? != *revision_id.as_digest()
+            {
+                return Err(HeleosError::Integrity);
+            }
+            pages.push(PageMetadata {
+                index: bounded_u32(row, 1, 0, 9_999)?,
+                page_id: parse_digest_id::<SheetId>(row, 0)?,
+                width_micropoints: nonnegative_u64(row, 2)?,
+                height_micropoints: nonnegative_u64(row, 3)?,
+                unit: crate::PageUnit::Point,
+                rotation_degrees: u16::try_from(bounded_u32(row, 5, 0, 270)?)
+                    .map_err(|_| HeleosError::Integrity)?,
+                transform: parse_bounded_json(row, 6, ONE_MIB)?,
+            });
+        }
+        if pages.len() != count {
+            return Err(HeleosError::Integrity);
+        }
+        crate::pdf::geometry::validate_page_metadata(
+            &pages,
+            *revision_id.as_digest(),
+            crate::PdfLimits::default(),
+        )?;
+        Ok(pages)
+    }
+
+    fn revision_evidence_rows_after_count(
+        &self,
+        revision_id: RevisionId,
+        expected_count: usize,
+    ) -> Result<Vec<RevisionEvidenceRow>> {
+        if expected_count == 0 || expected_count > 100_000 {
+            return Err(HeleosError::Integrity);
+        }
+        let fetch_limit = expected_count
+            .checked_add(1)
+            .ok_or(HeleosError::Integrity)?;
+        let mut statement = self
+            .connection
+            .prepare(
+                "SELECT evidence.id, evidence.project_id, evidence.job_id,
+                        revision.document_id, revision.id,
+                        parent.sha256, parent.byte_length, parent.vault_key,
+                        parent.media_type, parent.admission_state,
+                        manifest.sha256, manifest.byte_length, manifest.vault_key,
+                        manifest.media_type, manifest.admission_state,
+                        evidence.extraction_method, evidence.parameters_json,
+                        evidence.review_state
+                 FROM evidence_objects AS evidence
+                 JOIN document_revisions AS revision
+                   ON revision.id = evidence.document_revision_id
+                 JOIN content_objects AS parent
+                   ON parent.sha256 = evidence.parent_content_sha256
+                 JOIN content_objects AS manifest
+                   ON manifest.sha256 = evidence.content_sha256
+                 WHERE evidence.document_revision_id = ?1
+                 ORDER BY evidence.project_id, evidence.id, evidence.job_id
+                 LIMIT ?2",
+            )
+            .map_err(|_| HeleosError::Database)?;
+        let mut rows = statement
+            .query(params![
+                revision_id.as_digest().to_string(),
+                i64::try_from(fetch_limit).map_err(|_| HeleosError::Integrity)?,
+            ])
+            .map_err(|_| HeleosError::Database)?;
+        let mut result = Vec::with_capacity(expected_count);
+        while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+            if result.len() == expected_count {
+                return Err(HeleosError::Integrity);
+            }
+            let evidence_id = parse_uuid_text::<EvidenceId>(row, 0, 36)?;
+            let project_id = parse_uuid_text::<ProjectId>(row, 1, 36)?;
+            let originating_job_id = parse_uuid_text::<JobId>(row, 2, 36)?;
+            let document_id = parse_digest_id::<DocumentId>(row, 3)?;
+            let stored_revision_id = parse_digest_id::<RevisionId>(row, 4)?;
+            let original_digest = parse_digest_text(row, 5)?;
+            let original = StoredObjectV1 {
+                digest: original_digest,
+                byte_length: nonnegative_u64(row, 6)?,
+                vault_key: bounded_text(row, 7, 256)?,
+            };
+            if bounded_text(row, 8, 256)? != PDF_MEDIA_TYPE
+                || bounded_text(row, 9, 16)? != "accepted"
+            {
+                return Err(HeleosError::Integrity);
+            }
+            let manifest_digest = parse_digest_text(row, 10)?;
+            let manifest = StoredObjectV1 {
+                digest: manifest_digest,
+                byte_length: nonnegative_u64(row, 11)?,
+                vault_key: bounded_text(row, 12, 256)?,
+            };
+            if original.byte_length > crate::PdfLimits::default().max_input_bytes
+                || manifest.byte_length
+                    > u64::try_from(crate::MAX_EVIDENCE_MANIFEST_BYTES)
+                        .map_err(|_| HeleosError::Integrity)?
+            {
+                return Err(HeleosError::ResourceLimit);
+            }
+            if bounded_text(row, 13, 256)? != EVIDENCE_MANIFEST_MEDIA_TYPE
+                || bounded_text(row, 14, 16)? != "accepted"
+            {
+                return Err(HeleosError::Integrity);
+            }
+            original.validate()?;
+            manifest.validate()?;
+            let extraction_method = bounded_text(row, 15, 256)?;
+            let parameters: EvidenceParametersV1 = parse_bounded_json(row, 16, ONE_MIB)?;
+            let review_state = bounded_text(row, 17, 16)?;
+            result.push(RevisionEvidenceRow {
+                lineage: EvidenceManifestLineage {
+                    project_id,
+                    evidence_id,
+                    originating_job_id,
+                },
+                document_id,
+                revision_id: stored_revision_id,
+                original,
+                manifest,
+                extraction_method,
+                parameters,
+                review_state,
+            });
+        }
+        if result.len() != expected_count {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(result)
+    }
+
+    pub(crate) fn foundation_inspection_rows(
+        &self,
+        project_id: ProjectId,
+    ) -> Result<FoundationInspection> {
+        const CONTENT_PREDICATE: &str = "content.sha256 IN (
+                SELECT revision.content_sha256
+                FROM project_documents AS project_document
+                JOIN document_revisions AS revision
+                  ON revision.document_id = project_document.document_id
+                WHERE project_document.project_id = ?1
+                UNION
+                SELECT evidence.content_sha256
+                FROM evidence_objects AS evidence
+                WHERE evidence.project_id = ?1
+                UNION
+                SELECT event.content_sha256
+                FROM ingest_events AS event
+                JOIN content_objects AS retained
+                  ON retained.sha256 = event.content_sha256
+                WHERE event.project_id = ?1
+                  AND retained.admission_state = 'quarantined'
+            )";
+
+        let project = project_id.as_uuid().to_string();
+        match bounded_relation_count(
+            &self.connection,
+            "SELECT COUNT(*) FROM projects WHERE id = ?1",
+            &project,
+        )? {
+            0 => return Err(HeleosError::NotFound),
+            1 => {}
+            _ => return Err(HeleosError::Integrity),
+        }
+
+        let content_count = bounded_relation_count(
+            &self.connection,
+            &format!("SELECT COUNT(*) FROM content_objects AS content WHERE {CONTENT_PREDICATE}"),
+            &project,
+        )?;
+        let document_count = bounded_relation_count(
+            &self.connection,
+            "SELECT COUNT(*)
+             FROM documents AS document
+             JOIN project_documents AS project_document
+               ON project_document.document_id = document.id
+             WHERE project_document.project_id = ?1",
+            &project,
+        )?;
+        let revision_count = bounded_relation_count(
+            &self.connection,
+            "SELECT COUNT(*)
+             FROM document_revisions AS revision
+             JOIN project_documents AS project_document
+               ON project_document.document_id = revision.document_id
+             WHERE project_document.project_id = ?1",
+            &project,
+        )?;
+        let project_document_count = bounded_relation_count(
+            &self.connection,
+            "SELECT COUNT(*) FROM project_documents WHERE project_id = ?1",
+            &project,
+        )?;
+        let sheet_count = bounded_relation_count(
+            &self.connection,
+            "SELECT COUNT(*)
+             FROM sheets AS sheet
+             JOIN document_revisions AS revision ON revision.id = sheet.revision_id
+             JOIN project_documents AS project_document
+               ON project_document.document_id = revision.document_id
+             WHERE project_document.project_id = ?1",
+            &project,
+        )?;
+        let evidence_count = bounded_relation_count(
+            &self.connection,
+            "SELECT COUNT(*) FROM evidence_objects WHERE project_id = ?1",
+            &project,
+        )?;
+        let ingest_event_count = bounded_relation_count(
+            &self.connection,
+            "SELECT COUNT(*) FROM ingest_events WHERE project_id = ?1",
+            &project,
+        )?;
+        let job_count = bounded_relation_count(
+            &self.connection,
+            "SELECT COUNT(*) FROM job_runs WHERE project_id = ?1",
+            &project,
+        )?;
+        let audit_event_count = bounded_relation_count(
+            &self.connection,
+            "SELECT COUNT(*) FROM audit_events WHERE project_id = ?1",
+            &project,
+        )?;
+        let counts = FoundationCounts {
+            content_objects: content_count,
+            documents: document_count,
+            revisions: revision_count,
+            project_documents: project_document_count,
+            sheets: sheet_count,
+            evidence_objects: evidence_count,
+            ingest_events: ingest_event_count,
+            jobs: job_count,
+            audit_events: audit_event_count,
+        };
+        counts.validate()?;
+        if document_count != project_document_count {
+            return Err(HeleosError::Integrity);
+        }
+        let mut remaining = MAX_FOUNDATION_INSPECTION_ROWS;
+
+        let content_objects = fetch_foundation_content(
+            &self.connection,
+            &project,
+            CONTENT_PREDICATE,
+            count_as_usize(content_count)?,
+            &mut remaining,
+        )?;
+        let document_ids = fetch_project_documents(
+            &self.connection,
+            &project,
+            count_as_usize(document_count)?,
+            &mut remaining,
+        )?;
+        let revision_ids = fetch_project_revisions(
+            &self.connection,
+            &project,
+            count_as_usize(revision_count)?,
+            &mut remaining,
+        )?;
+        let sheets = fetch_project_sheets(
+            &self.connection,
+            &project,
+            count_as_usize(sheet_count)?,
+            &mut remaining,
+        )?;
+        let evidence = fetch_project_evidence(
+            &self.connection,
+            &project,
+            count_as_usize(evidence_count)?,
+            &mut remaining,
+        )?;
+        let intake_events = fetch_project_intake_events(
+            &self.connection,
+            &project,
+            count_as_usize(ingest_event_count)?,
+            &mut remaining,
+        )?;
+        let jobs = fetch_project_jobs(
+            &self.connection,
+            &project,
+            count_as_usize(job_count)?,
+            &mut remaining,
+        )?;
+        verify_project_audit_count(
+            &self.connection,
+            &project,
+            count_as_usize(audit_event_count)?,
+            &mut remaining,
+        )?;
+
+        FoundationInspection::new(
+            project_id,
+            FoundationInspectionParts {
+                counts,
+                content_objects,
+                document_ids,
+                revision_ids,
+                sheets,
+                evidence,
+                intake_events,
+                jobs,
+            },
+        )
+    }
+
+    pub(crate) fn audit_chain_rows(
+        &self,
+        mut consume: impl FnMut(RawAuditRow) -> Result<()>,
+    ) -> Result<()> {
+        let mut statement = self
+            .connection
+            .prepare(
+                "SELECT id, sequence, project_id, actor, action, subject_type, subject_id,
+                        before_json, after_json, reason, occurred_at_ms, previous_hash, event_hash
+                 FROM audit_events
+                 ORDER BY sequence, id",
+            )
+            .map_err(|_| HeleosError::Database)?;
+        let mut rows = statement.query([]).map_err(|_| HeleosError::Database)?;
+        while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+            consume(read_audit_row(row)?)?;
+        }
+        Ok(())
+    }
+}
+
+fn bounded_relation_count(connection: &Connection, sql: &str, project_id: &str) -> Result<u64> {
+    let mut statement = connection.prepare(sql).map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query([project_id])
+        .map_err(|_| HeleosError::Database)?;
+    let row = rows
+        .next()
+        .map_err(|_| HeleosError::Database)?
+        .ok_or(HeleosError::Integrity)?;
+    let count = match row.get_ref(0).map_err(|_| HeleosError::Database)? {
+        ValueRef::Integer(value)
+            if value >= 0
+                && u64::try_from(value).is_ok_and(|value| value <= JCS_SAFE_INTEGER_MAX) =>
+        {
+            u64::try_from(value).map_err(|_| HeleosError::Integrity)?
+        }
+        _ => return Err(HeleosError::Integrity),
+    };
+    if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(count)
+}
+
+fn count_as_usize(count: u64) -> Result<usize> {
+    usize::try_from(count).map_err(|_| HeleosError::Integrity)
+}
+
+fn inspection_fetch_limit(remaining: usize, expected: usize) -> Result<i64> {
+    if expected > remaining {
+        return Err(HeleosError::Integrity);
+    }
+    i64::try_from(remaining.checked_add(1).ok_or(HeleosError::Integrity)?)
+        .map_err(|_| HeleosError::Integrity)
+}
+
+fn finish_inspection_relation(
+    observed: usize,
+    expected: usize,
+    remaining: &mut usize,
+) -> Result<()> {
+    if observed != expected {
+        return Err(HeleosError::Integrity);
+    }
+    *remaining = remaining
+        .checked_sub(observed)
+        .ok_or(HeleosError::Integrity)?;
+    Ok(())
+}
+
+fn fetch_foundation_content(
+    connection: &Connection,
+    project_id: &str,
+    predicate: &str,
+    expected: usize,
+    remaining: &mut usize,
+) -> Result<Vec<FoundationContentObject>> {
+    let sql = format!(
+        "SELECT content.sha256, content.byte_length, content.media_type,
+                content.admission_state, content.vault_key, content.quarantine_reason
+         FROM content_objects AS content
+         WHERE {predicate}
+         ORDER BY content.sha256
+         LIMIT ?2"
+    );
+    let mut statement = connection
+        .prepare(&sql)
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query(params![
+            project_id,
+            inspection_fetch_limit(*remaining, expected)?,
+        ])
+        .map_err(|_| HeleosError::Database)?;
+    let mut values = Vec::with_capacity(expected);
+    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+        if values.len() == expected {
+            return Err(HeleosError::Integrity);
+        }
+        let admission_state = match bounded_text(row, 3, 16)?.as_str() {
+            "accepted" => FoundationAdmissionState::Accepted,
+            "quarantined" => FoundationAdmissionState::Quarantined,
+            _ => return Err(HeleosError::Integrity),
+        };
+        values.push(FoundationContentObject {
+            sha256: parse_digest_text(row, 0)?,
+            byte_length: nonnegative_u64(row, 1)?,
+            media_type: bounded_text(row, 2, 256)?,
+            admission_state,
+            vault_key: bounded_text(row, 4, 256)?,
+            quarantine: optional_bounded_json(row, 5, ONE_MIB)?,
+        });
+    }
+    finish_inspection_relation(values.len(), expected, remaining)?;
+    Ok(values)
+}
+
+fn fetch_project_documents(
+    connection: &Connection,
+    project_id: &str,
+    expected: usize,
+    remaining: &mut usize,
+) -> Result<Vec<DocumentId>> {
+    let mut statement = connection
+        .prepare(
+            "SELECT document.id
+             FROM documents AS document
+             JOIN project_documents AS project_document
+               ON project_document.document_id = document.id
+             WHERE project_document.project_id = ?1
+             ORDER BY document.id
+             LIMIT ?2",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query(params![
+            project_id,
+            inspection_fetch_limit(*remaining, expected)?,
+        ])
+        .map_err(|_| HeleosError::Database)?;
+    let mut values = Vec::with_capacity(expected);
+    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+        if values.len() == expected {
+            return Err(HeleosError::Integrity);
+        }
+        values.push(parse_digest_id(row, 0)?);
+    }
+    finish_inspection_relation(values.len(), expected, remaining)?;
+    Ok(values)
+}
+
+fn fetch_project_revisions(
+    connection: &Connection,
+    project_id: &str,
+    expected: usize,
+    remaining: &mut usize,
+) -> Result<Vec<RevisionId>> {
+    let mut statement = connection
+        .prepare(
+            "SELECT revision.id, revision.document_id, revision.content_sha256
+             FROM document_revisions AS revision
+             JOIN project_documents AS project_document
+               ON project_document.document_id = revision.document_id
+             WHERE project_document.project_id = ?1
+             ORDER BY revision.id
+             LIMIT ?2",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query(params![
+            project_id,
+            inspection_fetch_limit(*remaining, expected)?,
+        ])
+        .map_err(|_| HeleosError::Database)?;
+    let mut values = Vec::with_capacity(expected);
+    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+        if values.len() == expected {
+            return Err(HeleosError::Integrity);
+        }
+        let revision = parse_digest_id::<RevisionId>(row, 0)?;
+        let document = parse_digest_id::<DocumentId>(row, 1)?;
+        let content = parse_digest_text(row, 2)?;
+        let (expected_document, expected_revision) = crate::canonical_document_ids(content);
+        if document != expected_document || revision != expected_revision {
+            return Err(HeleosError::Integrity);
+        }
+        values.push(revision);
+    }
+    finish_inspection_relation(values.len(), expected, remaining)?;
+    Ok(values)
+}
+
+fn fetch_project_sheets(
+    connection: &Connection,
+    project_id: &str,
+    expected: usize,
+    remaining: &mut usize,
+) -> Result<Vec<FoundationSheet>> {
+    let mut statement = connection
+        .prepare(
+            "SELECT sheet.id, sheet.revision_id, sheet.zero_based_page_index,
+                    sheet.width_micropoints, sheet.height_micropoints, sheet.unit,
+                    sheet.rotation_degrees, sheet.transform_json,
+                    sheet.parent_content_sha256
+             FROM sheets AS sheet
+             JOIN document_revisions AS revision ON revision.id = sheet.revision_id
+             JOIN project_documents AS project_document
+               ON project_document.document_id = revision.document_id
+             WHERE project_document.project_id = ?1
+             ORDER BY sheet.revision_id, sheet.zero_based_page_index, sheet.id
+             LIMIT ?2",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query(params![
+            project_id,
+            inspection_fetch_limit(*remaining, expected)?,
+        ])
+        .map_err(|_| HeleosError::Database)?;
+    let mut values = Vec::with_capacity(expected);
+    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+        if values.len() == expected {
+            return Err(HeleosError::Integrity);
+        }
+        if bounded_text(row, 5, 2)? != "pt" {
+            return Err(HeleosError::Integrity);
+        }
+        values.push(FoundationSheet {
+            sheet_id: parse_digest_id(row, 0)?,
+            revision_id: parse_digest_id(row, 1)?,
+            index: bounded_u32(row, 2, 0, 9_999)?,
+            width_micropoints: nonnegative_u64(row, 3)?,
+            height_micropoints: nonnegative_u64(row, 4)?,
+            unit: crate::PageUnit::Point,
+            rotation_degrees: u16::try_from(bounded_u32(row, 6, 0, 270)?)
+                .map_err(|_| HeleosError::Integrity)?,
+            transform: parse_bounded_json(row, 7, ONE_MIB)?,
+            parent_content_sha256: parse_digest_text(row, 8)?,
+        });
+    }
+    finish_inspection_relation(values.len(), expected, remaining)?;
+    Ok(values)
+}
+
+fn fetch_project_evidence(
+    connection: &Connection,
+    project_id: &str,
+    expected: usize,
+    remaining: &mut usize,
+) -> Result<Vec<FoundationEvidenceLineage>> {
+    let mut statement = connection
+        .prepare(
+            "SELECT evidence.id, evidence.job_id, revision.document_id, revision.id,
+                    original.sha256, original.byte_length, original.vault_key,
+                    original.media_type, original.admission_state,
+                    manifest.sha256, manifest.byte_length, manifest.vault_key,
+                    manifest.media_type, manifest.admission_state,
+                    evidence.extraction_method, evidence.parameters_json,
+                    evidence.review_state
+             FROM evidence_objects AS evidence
+             JOIN document_revisions AS revision
+               ON revision.id = evidence.document_revision_id
+             JOIN content_objects AS original
+               ON original.sha256 = evidence.parent_content_sha256
+             JOIN content_objects AS manifest
+               ON manifest.sha256 = evidence.content_sha256
+             WHERE evidence.project_id = ?1
+             ORDER BY revision.id, manifest.sha256, evidence.id
+             LIMIT ?2",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query(params![
+            project_id,
+            inspection_fetch_limit(*remaining, expected)?,
+        ])
+        .map_err(|_| HeleosError::Database)?;
+    let mut values = Vec::with_capacity(expected);
+    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+        if values.len() == expected {
+            return Err(HeleosError::Integrity);
+        }
+        if bounded_text(row, 8, 16)? != "accepted" || bounded_text(row, 13, 16)? != "accepted" {
+            return Err(HeleosError::Integrity);
+        }
+        let parameters: EvidenceParametersV1 = parse_bounded_json(row, 15, ONE_MIB)?;
+        values.push(FoundationEvidenceLineage {
+            evidence_id: parse_uuid_text(row, 0, 36)?,
+            originating_job_id: parse_uuid_text(row, 1, 36)?,
+            document_id: parse_digest_id(row, 2)?,
+            revision_id: parse_digest_id(row, 3)?,
+            original: FoundationEvidenceContent {
+                sha256: parse_digest_text(row, 4)?,
+                byte_length: nonnegative_u64(row, 5)?,
+                vault_key: bounded_text(row, 6, 256)?,
+                media_type: bounded_text(row, 7, 256)?,
+            },
+            manifest: FoundationEvidenceContent {
+                sha256: parse_digest_text(row, 9)?,
+                byte_length: nonnegative_u64(row, 10)?,
+                vault_key: bounded_text(row, 11, 256)?,
+                media_type: bounded_text(row, 12, 256)?,
+            },
+            extraction_method: bounded_text(row, 14, 256)?,
+            requested_limits: parameters.requested_limits,
+            probe_provenance: parameters.probe_provenance,
+            review_state: bounded_text(row, 16, 16)?,
+        });
+    }
+    finish_inspection_relation(values.len(), expected, remaining)?;
+    Ok(values)
+}
+
+fn fetch_project_intake_events(
+    connection: &Connection,
+    project_id: &str,
+    expected: usize,
+    remaining: &mut usize,
+) -> Result<Vec<FoundationIntakeEvent>> {
+    let mut statement = connection
+        .prepare(
+            "SELECT id, job_id, content_sha256, outcome, attempt, terminal_at_ms
+             FROM ingest_events
+             WHERE project_id = ?1
+             ORDER BY terminal_at_ms, id
+             LIMIT ?2",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query(params![
+            project_id,
+            inspection_fetch_limit(*remaining, expected)?,
+        ])
+        .map_err(|_| HeleosError::Database)?;
+    let mut values = Vec::with_capacity(expected);
+    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+        if values.len() == expected {
+            return Err(HeleosError::Integrity);
+        }
+        values.push(FoundationIntakeEvent {
+            ingest_event_id: parse_uuid_text(row, 0, 36)?,
+            job_id: optional_uuid_text(row, 1, 36)?,
+            content_sha256: optional_digest_text(row, 2)?,
+            outcome: parse_ingest_outcome(&bounded_text(row, 3, 32)?)?,
+            attempt: optional_bounded_u32(row, 4, 1, MAX_JOB_ATTEMPTS)?,
+            terminal_at_ms: nonnegative_i64(row, 5)?,
+        });
+    }
+    finish_inspection_relation(values.len(), expected, remaining)?;
+    Ok(values)
+}
+
+fn fetch_project_jobs(
+    connection: &Connection,
+    project_id: &str,
+    expected: usize,
+    remaining: &mut usize,
+) -> Result<Vec<FoundationJob>> {
+    let mut statement = connection
+        .prepare(
+            "SELECT id, kind, state, attempt, created_at_ms, updated_at_ms, terminal_reason
+             FROM job_runs
+             WHERE project_id = ?1
+             ORDER BY created_at_ms, id
+             LIMIT ?2",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query(params![
+            project_id,
+            inspection_fetch_limit(*remaining, expected)?,
+        ])
+        .map_err(|_| HeleosError::Database)?;
+    let mut values = Vec::with_capacity(expected);
+    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+        if values.len() == expected {
+            return Err(HeleosError::Integrity);
+        }
+        values.push(FoundationJob {
+            job_id: parse_uuid_text(row, 0, 36)?,
+            kind: bounded_text(row, 1, 16)?,
+            state: parse_job_state(&bounded_text(row, 2, 16)?)?,
+            attempt: bounded_u32(row, 3, 0, MAX_JOB_ATTEMPTS)?,
+            created_at_ms: nonnegative_i64(row, 4)?,
+            updated_at_ms: nonnegative_i64(row, 5)?,
+            terminal_reason: optional_terminal_reason(row, 6)?,
+        });
+    }
+    finish_inspection_relation(values.len(), expected, remaining)?;
+    Ok(values)
+}
+
+fn verify_project_audit_count(
+    connection: &Connection,
+    project_id: &str,
+    expected: usize,
+    remaining: &mut usize,
+) -> Result<()> {
+    let mut statement = connection
+        .prepare(
+            "SELECT id, sequence
+             FROM audit_events
+             WHERE project_id = ?1
+             ORDER BY sequence, id
+             LIMIT ?2",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query(params![
+            project_id,
+            inspection_fetch_limit(*remaining, expected)?,
+        ])
+        .map_err(|_| HeleosError::Database)?;
+    let mut observed = 0_usize;
+    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+        if observed == expected {
+            return Err(HeleosError::Integrity);
+        }
+        let _: AuditEventId = parse_uuid_text(row, 0, 36)?;
+        if nonnegative_u64(row, 1)? == 0 {
+            return Err(HeleosError::Integrity);
+        }
+        observed = observed.checked_add(1).ok_or(HeleosError::Integrity)?;
+    }
+    finish_inspection_relation(observed, expected, remaining)
+}
+
+fn require_accepted_job_authority(
+    transaction: &Transaction<'_>,
+    command: &AcceptedIntakeCommand,
+) -> Result<()> {
+    let (state, attempt, input, checkpoint) = {
+        let mut statement = transaction
+            .prepare(
+                "SELECT state, attempt, input_json, checkpoint_json
+                 FROM job_runs WHERE id = ?1 AND project_id = ?2",
+            )
+            .map_err(|_| HeleosError::Database)?;
+        let mut rows = statement
+            .query(params![
+                command.job_id.as_uuid().to_string(),
+                command.project_id.as_uuid().to_string(),
+            ])
+            .map_err(|_| HeleosError::Database)?;
+        let row = rows
+            .next()
+            .map_err(|_| HeleosError::Database)?
+            .ok_or(HeleosError::Integrity)?;
+        let state = parse_job_state(&bounded_text(row, 0, 16)?)?;
+        let attempt = bounded_u32(row, 1, 0, MAX_JOB_ATTEMPTS)?;
+        let input = parse_bounded_json::<IngestInputV1>(row, 2, ONE_MIB)?;
+        let checkpoint = parse_bounded_json::<IngestCheckpointV1>(row, 3, EIGHT_MIB)?;
+        if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
+            return Err(HeleosError::Integrity);
+        }
+        (state, attempt, input, checkpoint)
+    };
+    input.validate()?;
+    checkpoint.validate_for_input(&input)?;
+    if state != JobState::Running
+        || attempt != command.attempt
+        || input.project_id != command.project_id
+        || input.idempotency_key != command.idempotency_key
+        || input.source_display != command.source_display
+    {
+        return Err(HeleosError::Integrity);
+    }
+    match &checkpoint.phase {
+        IngestCheckpointPhaseV1::ProcessingComplete {
+            original,
+            candidate:
+                crate::ingest::job::ProcessingCandidateV1::Accepted {
+                    manifest,
+                    manifest_object,
+                },
+        } if original == &command.original
+            && manifest == &command.manifest
+            && manifest_object == &command.manifest_object =>
+        {
+            Ok(())
+        }
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn require_quarantined_job_authority(
+    transaction: &Transaction<'_>,
+    command: &QuarantinedIntakeCommand,
+) -> Result<()> {
+    let record = job_record_by_id(transaction, command.job_id)?;
+    record.validate_for_scope(command.project_id, &command.idempotency_key)?;
+    if record.state != JobState::Running
+        || record.attempt != command.attempt
+        || record.input.source_display != command.source_display
+    {
+        return Err(HeleosError::Integrity);
+    }
+    match &record.checkpoint.phase {
+        IngestCheckpointPhaseV1::PreflightRejected {
+            content_sha256,
+            byte_length,
+            quarantine,
+        } if command.retained.is_none()
+            && *content_sha256 == command.content_sha256
+            && *byte_length == command.byte_length
+            && quarantine == &command.quarantine =>
+        {
+            Ok(())
+        }
+        IngestCheckpointPhaseV1::ProcessingComplete {
+            original,
+            candidate:
+                crate::ingest::job::ProcessingCandidateV1::Quarantined {
+                    outcome,
+                    quarantine,
+                },
+        } if command.retained.as_ref() == Some(original)
+            && *outcome == command.outcome
+            && quarantine == &command.quarantine =>
+        {
+            Ok(())
+        }
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn read_audit_row(row: &Row<'_>) -> Result<RawAuditRow> {
+    Ok(RawAuditRow {
+        id: bounded_value(row, 0, 36)?,
+        sequence: bounded_value(row, 1, 0)?,
+        project_id: bounded_value(row, 2, 36)?,
+        actor: bounded_value(row, 3, 128)?,
+        action: bounded_value(row, 4, 32)?,
+        subject_type: bounded_value(row, 5, 32)?,
+        subject_id: bounded_value(row, 6, 256)?,
+        before_json: bounded_value(row, 7, 1024 * 1024)?,
+        after_json: bounded_value(row, 8, 1024 * 1024)?,
+        reason: bounded_value(row, 9, 1024)?,
+        occurred_at_ms: bounded_value(row, 10, 0)?,
+        previous_hash: bounded_value(row, 11, 64)?,
+        event_hash: bounded_value(row, 12, 64)?,
+    })
+}
+
+fn bounded_value(row: &Row<'_>, index: usize, max_text_bytes: usize) -> Result<RawAuditValue> {
+    let value = row.get_ref(index).map_err(|_| HeleosError::Database)?;
+    Ok(match value {
+        ValueRef::Null => RawAuditValue::Null,
+        ValueRef::Integer(value) => RawAuditValue::Integer(value),
+        ValueRef::Text(bytes) if bytes.len() <= max_text_bytes => {
+            match std::str::from_utf8(bytes) {
+                Ok(value) => RawAuditValue::Text(value.to_owned()),
+                Err(_) => RawAuditValue::Invalid,
+            }
+        }
+        ValueRef::Real(_) | ValueRef::Text(_) | ValueRef::Blob(_) => RawAuditValue::Invalid,
+    })
+}
+
+fn bounded_text(row: &Row<'_>, index: usize, maximum: usize) -> Result<String> {
+    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
+        ValueRef::Text(bytes) if bytes.len() <= maximum => std::str::from_utf8(bytes)
+            .map(str::to_owned)
+            .map_err(|_| HeleosError::Integrity),
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn parse_uuid_text<T>(row: &Row<'_>, index: usize, maximum: usize) -> Result<T>
+where
+    T: FromStr<Err = HeleosError>,
+{
+    let value = bounded_text(row, index, maximum)?;
+    let uuid = uuid::Uuid::parse_str(&value).map_err(|_| HeleosError::Integrity)?;
+    if uuid.hyphenated().to_string() != value {
+        return Err(HeleosError::Integrity);
+    }
+    T::from_str(&value).map_err(|_| HeleosError::Integrity)
+}
+
+fn optional_uuid_text<T>(row: &Row<'_>, index: usize, maximum: usize) -> Result<Option<T>>
+where
+    T: FromStr<Err = HeleosError>,
+{
+    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
+        ValueRef::Null => Ok(None),
+        ValueRef::Text(bytes) if bytes.len() <= maximum => {
+            let value = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
+            let uuid = uuid::Uuid::parse_str(value).map_err(|_| HeleosError::Integrity)?;
+            if uuid.hyphenated().to_string() != value {
+                return Err(HeleosError::Integrity);
+            }
+            T::from_str(value)
+                .map(Some)
+                .map_err(|_| HeleosError::Integrity)
+        }
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn optional_canonical_uuid_text(row: &Row<'_>, index: usize) -> Result<Option<String>> {
+    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
+        ValueRef::Null => Ok(None),
+        ValueRef::Text(bytes) if bytes.len() <= 36 => {
+            let value = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
+            let parsed = uuid::Uuid::parse_str(value).map_err(|_| HeleosError::Integrity)?;
+            if parsed.hyphenated().to_string() != value {
+                return Err(HeleosError::Integrity);
+            }
+            Ok(Some(value.to_owned()))
+        }
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn parse_digest_text(row: &Row<'_>, index: usize) -> Result<Sha256Digest> {
+    Sha256Digest::from_str(&bounded_text(row, index, 64)?).map_err(|_| HeleosError::Integrity)
+}
+
+fn optional_digest_text(row: &Row<'_>, index: usize) -> Result<Option<Sha256Digest>> {
+    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
+        ValueRef::Null => Ok(None),
+        ValueRef::Text(bytes) if bytes.len() <= 64 => {
+            let value = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
+            Sha256Digest::from_str(value)
+                .map(Some)
+                .map_err(|_| HeleosError::Integrity)
+        }
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn parse_digest_id<T: From<Sha256Digest>>(row: &Row<'_>, index: usize) -> Result<T> {
+    parse_digest_text(row, index).map(T::from)
+}
+
+fn nonnegative_i64(row: &Row<'_>, index: usize) -> Result<i64> {
+    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
+        ValueRef::Integer(value)
+            if value >= 0
+                && u64::try_from(value).is_ok_and(|value| value <= JCS_SAFE_INTEGER_MAX) =>
+        {
+            Ok(value)
+        }
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn optional_nonnegative_i64(row: &Row<'_>, index: usize) -> Result<Option<i64>> {
+    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
+        ValueRef::Null => Ok(None),
+        ValueRef::Integer(value)
+            if value >= 0
+                && u64::try_from(value).is_ok_and(|value| value <= JCS_SAFE_INTEGER_MAX) =>
+        {
+            Ok(Some(value))
+        }
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn nonnegative_u64(row: &Row<'_>, index: usize) -> Result<u64> {
+    u64::try_from(nonnegative_i64(row, index)?).map_err(|_| HeleosError::Integrity)
+}
+
+fn bounded_u32(row: &Row<'_>, index: usize, minimum: u32, maximum: u32) -> Result<u32> {
+    let value = u32::try_from(nonnegative_i64(row, index)?).map_err(|_| HeleosError::Integrity)?;
+    if !(minimum..=maximum).contains(&value) {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(value)
+}
+
+fn optional_bounded_u32(
+    row: &Row<'_>,
+    index: usize,
+    minimum: u32,
+    maximum: u32,
+) -> Result<Option<u32>> {
+    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
+        ValueRef::Null => Ok(None),
+        ValueRef::Integer(value) => {
+            let value = u32::try_from(value).map_err(|_| HeleosError::Integrity)?;
+            if !(minimum..=maximum).contains(&value) {
+                return Err(HeleosError::Integrity);
+            }
+            Ok(Some(value))
+        }
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn parse_job_state(value: &str) -> Result<JobState> {
+    match value {
+        "queued" => Ok(JobState::Queued),
+        "running" => Ok(JobState::Running),
+        "interrupted" => Ok(JobState::Interrupted),
+        "succeeded" => Ok(JobState::Succeeded),
+        "failed" => Ok(JobState::Failed),
+        "cancelled" => Ok(JobState::Cancelled),
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn parse_ingest_outcome(value: &str) -> Result<IngestOutcome> {
+    match value {
+        "accepted_new" => Ok(IngestOutcome::AcceptedNew),
+        "accepted_duplicate" => Ok(IngestOutcome::AcceptedDuplicate),
+        "idempotent_replay" => Ok(IngestOutcome::IdempotentReplay),
+        "quarantined_corrupt" => Ok(IngestOutcome::QuarantinedCorrupt),
+        "quarantined_encrypted" => Ok(IngestOutcome::QuarantinedEncrypted),
+        "quarantined_unsupported" => Ok(IngestOutcome::QuarantinedUnsupported),
+        "quarantined_suspicious" => Ok(IngestOutcome::QuarantinedSuspicious),
+        "quarantined_limit" => Ok(IngestOutcome::QuarantinedLimit),
+        "interrupted" => Ok(IngestOutcome::Interrupted),
+        "denied_conflict" => Ok(IngestOutcome::DeniedConflict),
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn optional_terminal_reason(
+    row: &Row<'_>,
+    index: usize,
+) -> Result<Option<FoundationJobTerminalReason>> {
+    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
+        ValueRef::Null => Ok(None),
+        ValueRef::Text(bytes) if bytes.len() <= 32 => {
+            let value = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
+            match value {
+                "completed" => Ok(Some(FoundationJobTerminalReason::Completed)),
+                "deadline_expired" => Ok(Some(FoundationJobTerminalReason::DeadlineExpired)),
+                "attempt_limit" => Ok(Some(FoundationJobTerminalReason::AttemptLimit)),
+                "internal_failure" => Ok(Some(FoundationJobTerminalReason::InternalFailure)),
+                "cancelled" => Ok(Some(FoundationJobTerminalReason::Cancelled)),
+                _ => Err(HeleosError::Integrity),
+            }
+        }
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn parse_bounded_json<T>(row: &Row<'_>, index: usize, maximum: usize) -> Result<T>
+where
+    T: DeserializeOwned + Serialize,
+{
+    let bytes = match row.get_ref(index).map_err(|_| HeleosError::Database)? {
+        ValueRef::Text(bytes) if !bytes.is_empty() && bytes.len() <= maximum => bytes,
+        _ => return Err(HeleosError::Integrity),
+    };
+    let value = serde_json::from_slice::<T>(bytes).map_err(|_| HeleosError::Integrity)?;
+    if crate::ingest::evidence::bounded_canonical_json(&value, maximum)?.as_slice() != bytes {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(value)
+}
+
+fn optional_bounded_json<T>(row: &Row<'_>, index: usize, maximum: usize) -> Result<Option<T>>
+where
+    T: DeserializeOwned + Serialize,
+{
+    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
+        ValueRef::Null => Ok(None),
+        ValueRef::Text(bytes) if !bytes.is_empty() && bytes.len() <= maximum => {
+            let value = serde_json::from_slice::<T>(bytes).map_err(|_| HeleosError::Integrity)?;
+            if crate::ingest::evidence::bounded_canonical_json(&value, maximum)?.as_slice() != bytes
+            {
+                return Err(HeleosError::Integrity);
+            }
+            Ok(Some(value))
+        }
+        _ => Err(HeleosError::Integrity),
+    }
+}
+
+fn checked_sum<P: Params>(connection: &Connection, sql: &str, parameters: P) -> Result<u64> {
+    let mut statement = connection.prepare(sql).map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query(parameters)
+        .map_err(|_| HeleosError::Database)?;
+    let mut total = 0_u64;
+    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+        let value = match row.get_ref(0).map_err(|_| HeleosError::Database)? {
+            ValueRef::Integer(value) if value >= 0 => {
+                u64::try_from(value).map_err(|_| HeleosError::Integrity)?
+            }
+            _ => return Err(HeleosError::Integrity),
+        };
+        total = total.checked_add(value).ok_or(HeleosError::Integrity)?;
+    }
+    Ok(total)
+}
+
+fn checked_time_add(value: i64, delta: i64) -> Result<i64> {
+    if value < 0 || delta < 0 {
+        return Err(HeleosError::Integrity);
+    }
+    let result = value.checked_add(delta).ok_or(HeleosError::Integrity)?;
+    if u64::try_from(result).map_err(|_| HeleosError::Integrity)? > JCS_SAFE_INTEGER_MAX {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(result)
+}
+
+fn sha256_text(value: &str) -> Sha256Digest {
+    let mut hasher = Sha256::new();
+    hasher.update(value.as_bytes());
+    Sha256Digest::from_bytes(hasher.finalize().into())
+}
+
+fn job_record_by_id(transaction: &Transaction<'_>, job_id: JobId) -> Result<JobRecord> {
+    let mut statement = transaction
+        .prepare(
+            "SELECT state, attempt, lease_expires_at_ms, created_at_ms, deadline_at_ms,
+                    input_json, budget_json, checkpoint_json
+             FROM job_runs WHERE id = ?1",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query([job_id.as_uuid().to_string()])
+        .map_err(|_| HeleosError::Database)?;
+    let row = rows
+        .next()
+        .map_err(|_| HeleosError::Database)?
+        .ok_or(HeleosError::NotFound)?;
+    let record = JobRecord {
+        job_id,
+        state: parse_job_state(&bounded_text(row, 0, 16)?)?,
+        attempt: bounded_u32(row, 1, 0, MAX_JOB_ATTEMPTS)?,
+        lease_expires_at_ms: optional_nonnegative_i64(row, 2)?,
+        created_at_ms: nonnegative_i64(row, 3)?,
+        deadline_at_ms: nonnegative_i64(row, 4)?,
+        input: parse_bounded_json(row, 5, ONE_MIB)?,
+        budget: parse_bounded_json(row, 6, ONE_MIB)?,
+        checkpoint: parse_bounded_json(row, 7, EIGHT_MIB)?,
+    };
+    if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
+        return Err(HeleosError::Integrity);
+    }
+    record.validate_for_scope(record.input.project_id, &record.input.idempotency_key)?;
+    Ok(record)
+}
+
+fn authoritative_event_content(
+    transaction: &Transaction<'_>,
+    project_id: ProjectId,
+    job_id: JobId,
+    receipt: &IngestReceipt,
+) -> Result<Option<Sha256Digest>> {
+    let mut statement = transaction
+        .prepare(
+            "SELECT content_sha256, outcome
+             FROM ingest_events
+             WHERE id = ?1 AND project_id = ?2 AND job_id = ?3 AND attempt = ?4",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query(params![
+            receipt.ingest_event_id.as_uuid().to_string(),
+            project_id.as_uuid().to_string(),
+            job_id.as_uuid().to_string(),
+            i64::from(receipt.attempt),
+        ])
+        .map_err(|_| HeleosError::Database)?;
+    let row = rows
+        .next()
+        .map_err(|_| HeleosError::Database)?
+        .ok_or(HeleosError::Integrity)?;
+    let content = match row.get_ref(0).map_err(|_| HeleosError::Database)? {
+        ValueRef::Null => None,
+        ValueRef::Text(bytes) if bytes.len() == 64 => {
+            let text = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
+            Some(Sha256Digest::from_str(text).map_err(|_| HeleosError::Integrity)?)
+        }
+        _ => return Err(HeleosError::Integrity),
+    };
+    if bounded_text(row, 1, 32)? != ingest_outcome_text(receipt.outcome)
+        || rows.next().map_err(|_| HeleosError::Database)?.is_some()
+    {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(content)
+}
+
+fn job_audit_snapshot(transaction: &Transaction<'_>, job_id: JobId) -> Result<JsonValue> {
+    let mut statement = transaction
+        .prepare(
+            "SELECT project_id, idempotency_key, state, attempt, created_at_ms, deadline_at_ms,
+                    lease_owner, lease_expires_at_ms, input_json, budget_json, checkpoint_json
+             FROM job_runs WHERE id = ?1",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query([job_id.as_uuid().to_string()])
+        .map_err(|_| HeleosError::Database)?;
+    let row = rows
+        .next()
+        .map_err(|_| HeleosError::Database)?
+        .ok_or(HeleosError::Integrity)?;
+    let project_id = parse_uuid_text::<ProjectId>(row, 0, 36)?;
+    let idempotency_key =
+        IdempotencyKey::try_from(bounded_text(row, 1, 128)?).map_err(|_| HeleosError::Integrity)?;
+    let state_text = bounded_text(row, 2, 16)?;
+    let state = parse_job_state(&state_text)?;
+    let attempt = bounded_u32(row, 3, 0, 16)?;
+    let created_at_ms = nonnegative_i64(row, 4)?;
+    let deadline_at_ms = nonnegative_i64(row, 5)?;
+    let lease_owner = match row.get_ref(6).map_err(|_| HeleosError::Database)? {
+        ValueRef::Null => None,
+        ValueRef::Text(bytes) if bytes.len() <= 36 => {
+            let text = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
+            let parsed = uuid::Uuid::parse_str(text).map_err(|_| HeleosError::Integrity)?;
+            if parsed.hyphenated().to_string() != text {
+                return Err(HeleosError::Integrity);
+            }
+            Some(text.to_owned())
+        }
+        _ => return Err(HeleosError::Integrity),
+    };
+    let lease_expires_at_ms = optional_nonnegative_i64(row, 7)?;
+    let input_json = bounded_text(row, 8, ONE_MIB)?;
+    let budget_json = bounded_text(row, 9, ONE_MIB)?;
+    let checkpoint_json = bounded_text(row, 10, EIGHT_MIB)?;
+    let input: IngestInputV1 =
+        serde_json::from_str(&input_json).map_err(|_| HeleosError::Integrity)?;
+    let budget: IngestBudgetV1 =
+        serde_json::from_str(&budget_json).map_err(|_| HeleosError::Integrity)?;
+    let checkpoint: IngestCheckpointV1 =
+        serde_json::from_str(&checkpoint_json).map_err(|_| HeleosError::Integrity)?;
+    JobRecord {
+        job_id,
+        state,
+        attempt,
+        lease_expires_at_ms,
+        created_at_ms,
+        deadline_at_ms,
+        input,
+        budget,
+        checkpoint: checkpoint.clone(),
+    }
+    .validate_for_scope(project_id, &idempotency_key)?;
+    let terminal_result_ids = match &checkpoint.phase {
+        IngestCheckpointPhaseV1::Terminal { receipt } => terminal_result_ids(receipt, project_id)?,
+        _ => JsonValue::Null,
+    };
+    if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(json!({
+        "attempt": attempt,
+        "budget_sha256": sha256_text(&budget_json),
+        "checkpoint_sha256": sha256_text(&checkpoint_json),
+        "deadline_at_ms": deadline_at_ms,
+        "input_sha256": sha256_text(&input_json),
+        "lease_expires_at_ms": lease_expires_at_ms,
+        "lease_owner": lease_owner,
+        "state": state_text,
+        "terminal_result_ids": terminal_result_ids,
+    }))
+}
+
+fn require_current_job_audit_anchor(transaction: &Transaction<'_>, job_id: JobId) -> Result<()> {
+    let mut statement = transaction
+        .prepare(
+            "SELECT after_json
+             FROM audit_events
+             WHERE subject_type = 'job' AND subject_id = ?1
+               AND action IN (
+                   'job_created', 'job_started', 'job_checkpointed', 'job_interrupted',
+                   'job_resumed', 'job_succeeded', 'job_failed'
+               )
+             ORDER BY sequence DESC, id DESC
+             LIMIT 1",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query([job_id.as_uuid().to_string()])
+        .map_err(|_| HeleosError::Database)?;
+    let row = rows
+        .next()
+        .map_err(|_| HeleosError::Database)?
+        .ok_or(HeleosError::Integrity)?;
+    let anchored: JsonValue = parse_bounded_json(row, 0, ONE_MIB)?;
+    if rows.next().map_err(|_| HeleosError::Database)?.is_some()
+        || anchored != job_audit_snapshot(transaction, job_id)?
+    {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+struct FailJob<'a> {
+    job_id: JobId,
+    project_id: ProjectId,
+    attempt: u32,
+    actor: &'a ActorId,
+    now_ms: i64,
+    terminal_reason: &'a str,
+    audit_reason: &'static str,
+    audit_event_id: AuditEventId,
+}
+
+fn fail_job_in_transaction(transaction: &Transaction<'_>, command: FailJob<'_>) -> Result<()> {
+    if !matches!(
+        command.terminal_reason,
+        "deadline_expired" | "attempt_limit" | "internal_failure"
+    ) || !(1..=MAX_JOB_ATTEMPTS).contains(&command.attempt)
+    {
+        return Err(HeleosError::Integrity);
+    }
+    let before = job_audit_snapshot(transaction, command.job_id)?;
+    let changed = transaction
+        .execute(
+            "UPDATE job_runs
+             SET state = 'failed', lease_owner = NULL, lease_expires_at_ms = NULL,
+                 terminal_reason = ?4, updated_at_ms = ?5
+             WHERE id = ?1 AND project_id = ?2 AND state = 'running' AND attempt = ?3",
+            params![
+                command.job_id.as_uuid().to_string(),
+                command.project_id.as_uuid().to_string(),
+                i64::from(command.attempt),
+                command.terminal_reason,
+                command.now_ms,
+            ],
+        )
+        .map_err(|_| HeleosError::Database)?;
+    if changed != 1 {
+        return Err(HeleosError::InvalidStateTransition);
+    }
+    append_action(
+        transaction,
+        AppendAction {
+            id: command.audit_event_id,
+            project_id: command.project_id,
+            actor: command.actor.clone(),
+            action: AuditAction::JobFailed,
+            subject_type: AuditSubjectType::Job,
+            subject_id: command.job_id.as_uuid().to_string(),
+            before,
+            after: job_audit_snapshot(transaction, command.job_id)?,
+            reason: command.audit_reason,
+            occurred_at_ms: command.now_ms,
+        },
+    )?;
+    Ok(())
+}
+
+fn terminal_result_ids(receipt: &IngestReceipt, project_id: ProjectId) -> Result<JsonValue> {
+    let evidence_id = match &receipt.evidence_manifest {
+        Some(manifest) => {
+            let mut matches = manifest
+                .lineages
+                .iter()
+                .filter(|lineage| lineage.project_id == project_id);
+            let evidence_id = matches.next().ok_or(HeleosError::Integrity)?.evidence_id;
+            if matches.next().is_some() {
+                return Err(HeleosError::Integrity);
+            }
+            Some(evidence_id)
+        }
+        None => None,
+    };
+    Ok(json!({
+        "document_id": receipt.document_id,
+        "evidence_id": evidence_id,
+        "ingest_event_id": receipt.ingest_event_id,
+        "revision_id": receipt.revision_id,
+    }))
+}
+
+fn insert_or_verify_content_object(
+    transaction: &Transaction<'_>,
+    object: &StoredObjectV1,
+    media_type: &str,
+    admission_state: &str,
+    quarantine_json: Option<&str>,
+    now_ms: i64,
+    actor: &ActorId,
+) -> Result<()> {
+    transaction
+        .execute(
+            "INSERT OR IGNORE INTO content_objects
+                (sha256, byte_length, media_type, admission_state, vault_key, created_at_ms,
+                 created_by, quarantine_reason)
+             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
+            params![
+                object.digest.to_string(),
+                i64::try_from(object.byte_length).map_err(|_| HeleosError::Integrity)?,
+                media_type,
+                admission_state,
+                object.vault_key,
+                now_ms,
+                actor.as_str(),
+                quarantine_json,
+            ],
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut statement = transaction
+        .prepare(
+            "SELECT byte_length, media_type, admission_state, vault_key, quarantine_reason
+             FROM content_objects WHERE sha256 = ?1",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query([object.digest.to_string()])
+        .map_err(|_| HeleosError::Database)?;
+    let row = rows
+        .next()
+        .map_err(|_| HeleosError::Database)?
+        .ok_or(HeleosError::Integrity)?;
+    let stored_quarantine = match row.get_ref(4).map_err(|_| HeleosError::Database)? {
+        ValueRef::Null => None,
+        ValueRef::Text(bytes) if bytes.len() <= ONE_MIB => {
+            Some(std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?)
+        }
+        _ => return Err(HeleosError::Integrity),
+    };
+    if nonnegative_u64(row, 0)? != object.byte_length
+        || bounded_text(row, 1, 80)? != media_type
+        || bounded_text(row, 2, 16)? != admission_state
+        || bounded_text(row, 3, 256)? != object.vault_key
+        || stored_quarantine != quarantine_json
+        || rows.next().map_err(|_| HeleosError::Database)?.is_some()
+    {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+fn require_revision_authority(
+    transaction: &Transaction<'_>,
+    document_id: DocumentId,
+    revision_id: RevisionId,
+    content: Sha256Digest,
+) -> Result<()> {
+    let exact = transaction
+        .query_row(
+            "SELECT EXISTS(
+                SELECT 1 FROM document_revisions
+                WHERE id = ?1 AND document_id = ?2 AND content_sha256 = ?3
+             )",
+            params![
+                revision_id.as_digest().to_string(),
+                document_id.as_digest().to_string(),
+                content.to_string(),
+            ],
+            |row| row.get::<_, i64>(0),
+        )
+        .map_err(|_| HeleosError::Database)?;
+    if exact != 1 {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+fn require_sheet_authority(
+    transaction: &Transaction<'_>,
+    revision_id: RevisionId,
+    parent: Sha256Digest,
+    expected: &[PageMetadata],
+) -> Result<()> {
+    let mut statement = transaction
+        .prepare(
+            "SELECT id, zero_based_page_index, width_micropoints, height_micropoints,
+                    rotation_degrees, unit, parent_content_sha256, transform_json
+             FROM sheets WHERE revision_id = ?1
+             ORDER BY zero_based_page_index, id",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query([revision_id.as_digest().to_string()])
+        .map_err(|_| HeleosError::Database)?;
+    for page in expected {
+        let row = rows
+            .next()
+            .map_err(|_| HeleosError::Database)?
+            .ok_or(HeleosError::Integrity)?;
+        let transform: crate::PageTransform = parse_bounded_json(row, 7, ONE_MIB)?;
+        if parse_digest_id::<SheetId>(row, 0)? != page.page_id
+            || bounded_u32(row, 1, 0, 9_999)? != page.index
+            || nonnegative_u64(row, 2)? != page.width_micropoints
+            || nonnegative_u64(row, 3)? != page.height_micropoints
+            || bounded_u32(row, 4, 0, 270)? != u32::from(page.rotation_degrees)
+            || bounded_text(row, 5, 2)? != "pt"
+            || parse_digest_text(row, 6)? != parent
+            || transform != page.transform
+        {
+            return Err(HeleosError::Integrity);
+        }
+    }
+    if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+fn evidence_lineages(
+    transaction: &Transaction<'_>,
+    revision_id: RevisionId,
+) -> Result<Vec<EvidenceManifestLineage>> {
+    let mut statement = transaction
+        .prepare(
+            "SELECT project_id, id, job_id
+             FROM evidence_objects
+             WHERE document_revision_id = ?1 AND review_state = 'accepted'
+             ORDER BY project_id, id, job_id",
+        )
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement
+        .query([revision_id.as_digest().to_string()])
+        .map_err(|_| HeleosError::Database)?;
+    let mut result = Vec::new();
+    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
+        if result.len() == 100_000 {
+            return Err(HeleosError::ResourceLimit);
+        }
+        result.push(EvidenceManifestLineage {
+            project_id: parse_uuid_text::<ProjectId>(row, 0, 36)?,
+            evidence_id: parse_uuid_text::<EvidenceId>(row, 1, 36)?,
+            originating_job_id: parse_uuid_text::<JobId>(row, 2, 36)?,
+        });
+    }
+    if result.is_empty() {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(result)
+}
+
+fn require_evidence_authority(
+    transaction: &Transaction<'_>,
+    project_id: ProjectId,
+    revision_id: RevisionId,
+    manifest: Sha256Digest,
+    parent: Sha256Digest,
+    parameters_json: &str,
+) -> Result<()> {
+    let exact = transaction
+        .query_row(
+            "SELECT EXISTS(
+                SELECT 1 FROM evidence_objects
+                WHERE project_id = ?1 AND document_revision_id = ?2
+                  AND content_sha256 = ?3 AND parent_content_sha256 = ?4
+                  AND extraction_method = 'heleos.pdf-probe/v1'
+                  AND parameters_json = ?5 AND review_state = 'accepted'
+             )",
+            params![
+                project_id.as_uuid().to_string(),
+                revision_id.as_digest().to_string(),
+                manifest.to_string(),
+                parent.to_string(),
+                parameters_json,
+            ],
+            |row| row.get::<_, i64>(0),
+        )
+        .map_err(|_| HeleosError::Database)?;
+    if exact != 1 {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(())
+}
+
+const fn ingest_outcome_text(outcome: IngestOutcome) -> &'static str {
+    match outcome {
+        IngestOutcome::AcceptedNew => "accepted_new",
+        IngestOutcome::AcceptedDuplicate => "accepted_duplicate",
+        IngestOutcome::IdempotentReplay => "idempotent_replay",
+        IngestOutcome::QuarantinedCorrupt => "quarantined_corrupt",
+        IngestOutcome::QuarantinedEncrypted => "quarantined_encrypted",
+        IngestOutcome::QuarantinedUnsupported => "quarantined_unsupported",
+        IngestOutcome::QuarantinedSuspicious => "quarantined_suspicious",
+        IngestOutcome::QuarantinedLimit => "quarantined_limit",
+        IngestOutcome::Interrupted => "interrupted",
+        IngestOutcome::DeniedConflict => "denied_conflict",
+    }
+}
+
+struct AppendAction<'a> {
+    id: AuditEventId,
+    project_id: ProjectId,
+    actor: ActorId,
+    action: AuditAction,
+    subject_type: AuditSubjectType,
+    subject_id: String,
+    before: JsonValue,
+    after: JsonValue,
+    reason: &'a str,
+    occurred_at_ms: i64,
+}
+
+fn append_action(transaction: &Transaction<'_>, input: AppendAction<'_>) -> Result<AuditEvent> {
+    append_audit(
+        transaction,
+        AuditEventInput {
+            id: input.id,
+            sequence: next_audit_sequence(transaction)?,
+            project_id: Some(input.project_id),
+            actor: input.actor,
+            action: input.action,
+            subject_type: input.subject_type,
+            subject_id: input.subject_id,
+            before: input.before,
+            after: input.after,
+            reason: input.reason.to_owned(),
+            occurred_at_ms: input.occurred_at_ms,
+            previous_hash: audit_head(transaction)?,
+        },
+    )
+}
+
+fn data_class_text(value: DataClass) -> &'static str {
+    match value {
+        DataClass::Public => "PUBLIC",
+        DataClass::Internal => "INTERNAL",
+        DataClass::ProjectConfidential => "PROJECT_CONFIDENTIAL",
+        DataClass::Secret => "SECRET",
+    }
+}
+
+fn next_audit_sequence(transaction: &Transaction<'_>) -> Result<u64> {
+    let previous = transaction
+        .query_row("SELECT MAX(sequence) FROM audit_events", [], |row| {
+            row.get::<_, Option<i64>>(0)
+        })
+        .map_err(|_| HeleosError::Database)?;
+    let next = match previous {
+        Some(value) if value > 0 => u64::try_from(value)
+            .ok()
+            .and_then(|value| value.checked_add(1))
+            .ok_or(HeleosError::Integrity)?,
+        None => 1,
+        _ => return Err(HeleosError::Integrity),
+    };
+    if next > JCS_SAFE_INTEGER_MAX {
+        return Err(HeleosError::Integrity);
+    }
+    Ok(next)
+}
+
+fn audit_head(transaction: &Transaction<'_>) -> Result<Sha256Digest> {
+    let mut statement = transaction
+        .prepare("SELECT event_hash FROM audit_events ORDER BY sequence DESC, id DESC LIMIT 1")
+        .map_err(|_| HeleosError::Database)?;
+    let mut rows = statement.query([]).map_err(|_| HeleosError::Database)?;
+    let Some(row) = rows.next().map_err(|_| HeleosError::Database)? else {
+        return Ok(Sha256Digest::from_bytes([0; 32]));
+    };
+    let bytes = match row.get_ref(0).map_err(|_| HeleosError::Database)? {
+        ValueRef::Text(bytes) if bytes.len() == 64 => bytes,
+        _ => return Err(HeleosError::Integrity),
+    };
+    let value = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
+    Sha256Digest::from_str(value).map_err(|_| HeleosError::Integrity)
+}
+
+pub(crate) fn append_audit(
+    transaction: &Transaction<'_>,
+    input: AuditEventInput,
+) -> Result<AuditEvent> {
+    let event = AuditEvent::build(input)?;
+    let before_json = String::from_utf8(crate::canonical_json(&event.before)?)
+        .map_err(|_| HeleosError::Integrity)?;
+    let after_json = String::from_utf8(crate::canonical_json(&event.after)?)
+        .map_err(|_| HeleosError::Integrity)?;
+    transaction
+        .execute(
+            "INSERT INTO audit_events
+                (id, sequence, project_id, actor, action, subject_type, subject_id, before_json,
+                 after_json, reason, occurred_at_ms, previous_hash, event_hash)
+             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
+            params![
+                event.id.as_uuid().to_string(),
+                i64::try_from(event.sequence).map_err(|_| HeleosError::Integrity)?,
+                event.project_id.map(|id| id.as_uuid().to_string()),
+                event.actor.as_str(),
+                event.action.as_str(),
+                event.subject_type.as_str(),
+                event.subject_id,
+                before_json,
+                after_json,
+                event.reason,
+                event.occurred_at_ms,
+                event.previous_hash.to_string(),
+                event.event_hash.to_string(),
+            ],
+        )
+        .map_err(|_| HeleosError::Database)?;
+    Ok(event)
+}
+
+#[cfg(test)]
+mod tests {
+    use uuid::Uuid;
+
+    use super::*;
+    use crate::{
+        INTAKE_QUARANTINE_SCHEMA_V1, IntakeQuarantineReasonV1, PageTransform, PageUnit, PdfLimits,
+        PdfProbeProvenance, PdfQuarantineReason, VaultInventory, VaultInventoryEntry, page_id,
+    };
+
+    fn uuid(value: u128) -> Uuid {
+        Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)
+    }
+
+    fn provenance() -> PdfProbeProvenance {
+        PdfProbeProvenance {
+            parser_name: "fixture-parser".to_owned(),
+            parser_version: "1.0.0".to_owned(),
+            guest_wasm_sha256: Sha256Digest::from_bytes([2; 32]),
+            guest_source_tree_sha256: Sha256Digest::from_bytes([3; 32]),
+            guest_dependency_graph_sha256: Sha256Digest::from_bytes([4; 32]),
+            protocol_version: "heleos.pdf-probe/v1".to_owned(),
+        }
+    }
+
+    fn accepted_receipt(
+        authoritative_job_id: JobId,
+        mut lineages: Vec<EvidenceManifestLineage>,
+    ) -> IngestReceipt {
+        let content = Sha256Digest::from_bytes([1; 32]);
+        let manifest = EvidenceManifestV1::new(
+            content,
+            7,
+            vec![PageMetadata {
+                index: 0,
+                page_id: page_id(content, 0),
+                width_micropoints: 612_000_000,
+                height_micropoints: 792_000_000,
+                unit: PageUnit::Point,
+                rotation_degrees: 0,
+                transform: PageTransform {
+                    m11: 1,
+                    m12: 0,
+                    m21: 0,
+                    m22: -1,
+                    tx_micropoints: 0,
+                    ty_micropoints: 792_000_000,
+                },
+            }],
+            provenance(),
+        )
+        .expect("valid manifest");
+        let manifest_bytes = manifest.canonical_bytes().expect("manifest bytes");
+        let manifest_digest =
+            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest");
+        let mut preexisting_vault_digests = vec![content, manifest_digest];
+        preexisting_vault_digests.sort_unstable();
+        lineages.sort_by_key(|lineage| {
+            (
+                *lineage.project_id.as_uuid().as_bytes(),
+                *lineage.evidence_id.as_uuid().as_bytes(),
+                *lineage.originating_job_id.as_uuid().as_bytes(),
+            )
+        });
+        let (document_id, revision_id) = crate::canonical_document_ids(content);
+        IngestReceipt {
+            ingest_event_id: IngestEventId::from_uuid(uuid(20)),
+            authoritative_job_id,
+            attempt: 1,
+            outcome: IngestOutcome::AcceptedDuplicate,
+            content_sha256: Some(content),
+            byte_length: 7,
+            quarantine: None,
+            document_id: Some(document_id),
+            revision_id: Some(revision_id),
+            sheet_ids: vec![page_id(content, 0)],
+            evidence_manifest: Some(EvidenceManifestReceipt {
+                manifest,
+                manifest_content_sha256: manifest_digest,
+                manifest_byte_length: u64::try_from(manifest_bytes.len())
+                    .expect("manifest length fits"),
+                manifest_media_type: EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
+                manifest_vault_key: crate::Vault::object_key(manifest_digest),
+                original_vault_key: crate::Vault::object_key(content),
+                lineages,
+            }),
+            preexisting_vault_digests,
+        }
+    }
+
+    fn valid_input(project_id: ProjectId, key: &str) -> IngestInputV1 {
+        IngestInputV1 {
+            schema: crate::ingest::job::INGEST_INPUT_SCHEMA_V1.to_owned(),
+            project_id,
+            kind: "pdf_ingest".to_owned(),
+            idempotency_key: IdempotencyKey::try_from(key).expect("idempotency key"),
+            content_sha256: Some(Sha256Digest::from_bytes([1; 32])),
+            byte_length: 7,
+            source_display: "utf8:file.pdf".to_owned(),
+            requested_limits: PdfLimits::default().into(),
+            expected_probe_provenance: provenance(),
+            deadline_profile_ms: 300_000,
+        }
+    }
+
+    fn insert_project(store: &Store, project_id: ProjectId) {
+        store
+            .connection
+            .execute(
+                "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+                 VALUES (?1, 'project', 0, 'actor', 'INTERNAL')",
+                [project_id.as_uuid().to_string()],
+            )
+            .expect("insert project");
+    }
+
+    fn insert_job(
+        store: &Store,
+        project_id: ProjectId,
+        job_id: JobId,
+        key: &str,
+        input: &IngestInputV1,
+        budget: &IngestBudgetV1,
+        checkpoint: &IngestCheckpointV1,
+    ) {
+        insert_job_with_times(
+            (store, project_id, job_id, key, input, budget, checkpoint),
+            0,
+            300_000,
+        );
+    }
+
+    type JobFixture<'a> = (
+        &'a Store,
+        ProjectId,
+        JobId,
+        &'a str,
+        &'a IngestInputV1,
+        &'a IngestBudgetV1,
+        &'a IngestCheckpointV1,
+    );
+
+    fn insert_job_with_times(fixture: JobFixture<'_>, created_at_ms: i64, deadline_at_ms: i64) {
+        let (store, project_id, job_id, key, input, budget, checkpoint) = fixture;
+        store
+            .connection
+            .execute(
+                "INSERT INTO job_runs
+                    (id, project_id, kind, idempotency_key, state, attempt, lease_owner,
+                     lease_expires_at_ms, deadline_at_ms, budget_json, input_json,
+                     checkpoint_json, terminal_reason, created_at_ms, updated_at_ms)
+                 VALUES (?1, ?2, 'pdf_ingest', ?3, 'succeeded', 1, NULL, NULL, ?4,
+                         ?5, ?6, ?7, 'completed', ?8, ?9)",
+                params![
+                    job_id.as_uuid().to_string(),
+                    project_id.as_uuid().to_string(),
+                    key,
+                    deadline_at_ms,
+                    json_text(budget, ONE_MIB).expect("budget JSON"),
+                    json_text(input, ONE_MIB).expect("input JSON"),
+                    json_text(checkpoint, EIGHT_MIB).expect("checkpoint JSON"),
+                    created_at_ms,
+                    created_at_ms + 1,
+                ],
+            )
+            .expect("insert job");
+    }
+
+    fn accepted_page(content: Sha256Digest) -> PageMetadata {
+        PageMetadata {
+            index: 0,
+            page_id: page_id(content, 0),
+            width_micropoints: 612_000_000,
+            height_micropoints: 792_000_000,
+            unit: PageUnit::Point,
+            rotation_degrees: 0,
+            transform: PageTransform {
+                m11: 1,
+                m12: 0,
+                m21: 0,
+                m22: -1,
+                tx_micropoints: 0,
+                ty_micropoints: 792_000_000,
+            },
+        }
+    }
+
+    fn accepted_manifest(content: Sha256Digest) -> EvidenceManifestV1 {
+        EvidenceManifestV1::new(content, 7, vec![accepted_page(content)], provenance())
+            .expect("valid accepted manifest")
+    }
+
+    fn stored_object(byte: u8, byte_length: u64) -> StoredObjectV1 {
+        let digest = Sha256Digest::from_bytes([byte; 32]);
+        StoredObjectV1 {
+            digest,
+            byte_length,
+            vault_key: crate::Vault::object_key(digest),
+        }
+    }
+
+    fn inventory_entry(object: &StoredObjectV1) -> VaultInventoryEntry {
+        VaultInventoryEntry {
+            digest: object.digest,
+            expected_byte_length: object.byte_length,
+            vault_key: object.vault_key.clone(),
+        }
+    }
+
+    fn input_for_object(
+        project_id: ProjectId,
+        key: &str,
+        object: &StoredObjectV1,
+    ) -> IngestInputV1 {
+        let mut input = valid_input(project_id, key);
+        input.content_sha256 = Some(object.digest);
+        input.byte_length = object.byte_length;
+        input
+    }
+
+    fn queue_inventory_job(
+        store: &mut Store,
+        project_id: ProjectId,
+        job_id: JobId,
+        input: IngestInputV1,
+        checkpoint: IngestCheckpointV1,
+        created_at_ms: i64,
+        audit_event_id: AuditEventId,
+    ) {
+        store
+            .job_create_with_checkpoint_and_audit(JobCreateCommand {
+                job_id,
+                project_id,
+                budget: IngestBudgetV1::new(input.byte_length),
+                input,
+                checkpoint,
+                actor: ActorId::from_str("fixture-actor").expect("actor"),
+                created_at_ms,
+                deadline_at_ms: created_at_ms + JOB_DEADLINE_MS,
+                audit_event_id,
+            })
+            .expect("queue inventory fixture job");
+    }
+
+    fn start_inventory_job(
+        store: &mut Store,
+        project_id: ProjectId,
+        job_id: JobId,
+        now_ms: i64,
+        lease_owner: Uuid,
+        audit_event_id: AuditEventId,
+    ) {
+        store
+            .job_start_or_resume_with_audit(JobStartCommand {
+                job_id,
+                project_id,
+                actor: ActorId::from_str("fixture-actor").expect("actor"),
+                lease_owner,
+                now_ms,
+                lease_expires_at_ms: now_ms + LEASE_DURATION_MS,
+                audit_event_id,
+            })
+            .expect("start inventory fixture job");
+    }
+
+    fn accepted_commit_fixture() -> (Store, AcceptedIntakeCommand) {
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let project_id = ProjectId::from_uuid(uuid(100));
+        let job_id = JobId::from_uuid(uuid(101));
+        insert_project(&store, project_id);
+        let input = valid_input(project_id, "accepted-command");
+        let original = StoredObjectV1 {
+            digest: input.content_sha256.expect("content digest"),
+            byte_length: input.byte_length,
+            vault_key: crate::Vault::object_key(input.content_sha256.expect("content digest")),
+        };
+        let manifest = accepted_manifest(original.digest);
+        let manifest_bytes = manifest.canonical_bytes().expect("manifest bytes");
+        let manifest_digest =
+            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest");
+        let manifest_object = StoredObjectV1 {
+            digest: manifest_digest,
+            byte_length: u64::try_from(manifest_bytes.len()).expect("manifest length"),
+            vault_key: crate::Vault::object_key(manifest_digest),
+        };
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::ProcessingComplete {
+                original: original.clone(),
+                candidate: crate::ingest::job::ProcessingCandidateV1::Accepted {
+                    manifest: manifest.clone(),
+                    manifest_object: manifest_object.clone(),
+                },
+            },
+        };
+        store
+            .connection
+            .execute(
+                "INSERT INTO job_runs
+                    (id, project_id, kind, idempotency_key, state, attempt, lease_owner,
+                     lease_expires_at_ms, deadline_at_ms, budget_json, input_json,
+                     checkpoint_json, terminal_reason, created_at_ms, updated_at_ms)
+                 VALUES (?1, ?2, 'pdf_ingest', ?3, 'running', 1, ?4, 30000, 300000,
+                         ?5, ?6, ?7, NULL, 0, 0)",
+                params![
+                    job_id.as_uuid().to_string(),
+                    project_id.as_uuid().to_string(),
+                    input.idempotency_key.as_str(),
+                    uuid(102).hyphenated().to_string(),
+                    json_text(&IngestBudgetV1::new(input.byte_length), ONE_MIB)
+                        .expect("budget JSON"),
+                    json_text(&input, ONE_MIB).expect("input JSON"),
+                    json_text(&checkpoint, EIGHT_MIB).expect("checkpoint JSON"),
+                ],
+            )
+            .expect("insert running accepted job");
+        let command = AcceptedIntakeCommand {
+            job_id,
+            project_id,
+            attempt: 1,
+            actor: ActorId::from_str("fixture-actor").expect("actor"),
+            idempotency_key: input.idempotency_key,
+            source_display: input.source_display,
+            original,
+            manifest_object,
+            pages: manifest.pages.clone(),
+            manifest,
+            ingest_event_id: IngestEventId::from_uuid(uuid(103)),
+            source_record_id: uuid(104).hyphenated().to_string(),
+            evidence_id: EvidenceId::from_uuid(uuid(105)),
+            now_ms: 1,
+            preexisting_vault_digests: Vec::new(),
+            audit_event_ids: [
+                AuditEventId::from_uuid(uuid(106)),
+                AuditEventId::from_uuid(uuid(107)),
+                AuditEventId::from_uuid(uuid(108)),
+            ],
+        };
+        (store, command)
+    }
+
+    fn authoritative_row_counts(store: &Store) -> [i64; 7] {
+        [
+            "content_objects",
+            "documents",
+            "sheets",
+            "evidence_objects",
+            "source_records",
+            "ingest_events",
+            "audit_events",
+        ]
+        .map(|table| {
+            store
+                .connection
+                .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
+                    row.get(0)
+                })
+                .expect("count authoritative rows")
+        })
+    }
+
+    fn assert_accepted_commit_rejected_without_authority(
+        mut store: Store,
+        command: AcceptedIntakeCommand,
+    ) {
+        let before = authoritative_row_counts(&store);
+        assert!(matches!(
+            store.accepted_intake_commit(command),
+            Err(HeleosError::Integrity)
+        ));
+        assert_eq!(authoritative_row_counts(&store), before);
+        assert_eq!(
+            store
+                .connection
+                .query_row("SELECT state FROM job_runs", [], |row| row
+                    .get::<_, String>(0),)
+                .expect("read unchanged job state"),
+            "running"
+        );
+    }
+
+    fn replace_command_object_authority(command: &mut AcceptedIntakeCommand) {
+        let original_digest = Sha256Digest::from_bytes([8; 32]);
+        let manifest = accepted_manifest(original_digest);
+        let manifest_bytes = manifest
+            .canonical_bytes()
+            .expect("alternate manifest bytes");
+        let manifest_digest =
+            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("alternate digest");
+        command.original = StoredObjectV1 {
+            digest: original_digest,
+            byte_length: 7,
+            vault_key: crate::Vault::object_key(original_digest),
+        };
+        command.manifest_object = StoredObjectV1 {
+            digest: manifest_digest,
+            byte_length: u64::try_from(manifest_bytes.len()).expect("alternate length"),
+            vault_key: crate::Vault::object_key(manifest_digest),
+        };
+        command.pages = manifest.pages.clone();
+        command.manifest = manifest;
+    }
+
+    fn prepopulate_revision_lineages(
+        store: &mut Store,
+        command: &AcceptedIntakeCommand,
+        count: u32,
+    ) {
+        store
+            .connection
+            .pragma_update(None, "foreign_keys", "OFF")
+            .expect("disable fixture foreign keys");
+        let transaction = store
+            .connection
+            .transaction()
+            .expect("begin lineage fixture");
+        for (object, media_type) in [
+            (&command.original, PDF_MEDIA_TYPE),
+            (&command.manifest_object, EVIDENCE_MANIFEST_MEDIA_TYPE),
+        ] {
+            transaction
+                .execute(
+                    "INSERT INTO content_objects
+                        (sha256, byte_length, media_type, admission_state, vault_key,
+                         created_at_ms, created_by, quarantine_reason)
+                     VALUES (?1, ?2, ?3, 'accepted', ?4, 0, 'fixture-actor', NULL)",
+                    params![
+                        object.digest.to_string(),
+                        i64::try_from(object.byte_length).expect("object length"),
+                        media_type,
+                        object.vault_key,
+                    ],
+                )
+                .expect("insert accepted content fixture");
+        }
+        transaction
+            .execute(
+                "INSERT INTO documents (id, created_at_ms, created_by)
+                 VALUES (?1, 0, 'fixture-actor')",
+                [command.manifest.document_id.as_digest().to_string()],
+            )
+            .expect("insert fixture document");
+        transaction
+            .execute(
+                "INSERT INTO document_revisions
+                    (id, document_id, content_sha256, created_at_ms, created_by)
+                 VALUES (?1, ?2, ?3, 0, 'fixture-actor')",
+                params![
+                    command.manifest.revision_id.as_digest().to_string(),
+                    command.manifest.document_id.as_digest().to_string(),
+                    command.original.digest.to_string(),
+                ],
+            )
+            .expect("insert fixture revision");
+        let parameters = EvidenceParametersV1::new(
+            command.manifest.probe_provenance.clone(),
+            command.manifest.requested_limits,
+        )
+        .expect("fixture parameters");
+        let parameters_json = json_text(&parameters, ONE_MIB).expect("parameters JSON");
+        let mut statement = transaction
+            .prepare(
+                "INSERT INTO evidence_objects
+                    (id, project_id, job_id, document_revision_id, content_sha256,
+                     parent_content_sha256, extraction_method, parameters_json,
+                     review_state, created_at_ms)
+                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'heleos.pdf-probe/v1', ?7,
+                         'accepted', 0)",
+            )
+            .expect("prepare lineage insert");
+        for index in 0..count {
+            let offset = 20_000_u128 + u128::from(index) * 3;
+            statement
+                .execute(params![
+                    uuid(offset).hyphenated().to_string(),
+                    uuid(offset + 1).hyphenated().to_string(),
+                    uuid(offset + 2).hyphenated().to_string(),
+                    command.manifest.revision_id.as_digest().to_string(),
+                    command.manifest_object.digest.to_string(),
+                    command.original.digest.to_string(),
+                    parameters_json,
+                ])
+                .expect("insert accepted lineage fixture");
+        }
+        drop(statement);
+        transaction.commit().expect("commit lineage fixture");
+        store
+            .connection
+            .pragma_update(None, "foreign_keys", "ON")
+            .expect("restore fixture foreign keys");
+    }
+
+    fn minimal_revision_evidence_store(
+        row_count: usize,
+        valid_identifiers: bool,
+    ) -> (Store, RevisionId) {
+        assert!(row_count > 0);
+        let store = Store::open_in_memory().expect("open minimal evidence store");
+        store
+            .connection
+            .execute_batch(
+                "CREATE TABLE content_objects (
+                    sha256 TEXT, byte_length INTEGER, vault_key TEXT,
+                    media_type TEXT, admission_state TEXT
+                 );
+                 CREATE TABLE document_revisions (id TEXT, document_id TEXT);
+                 CREATE TABLE sheets (
+                    id TEXT, revision_id TEXT, zero_based_page_index INTEGER,
+                    width_micropoints INTEGER, height_micropoints INTEGER,
+                    rotation_degrees INTEGER, unit TEXT,
+                    parent_content_sha256 TEXT, transform_json TEXT
+                 );
+                 CREATE TABLE evidence_objects (
+                    id TEXT, project_id TEXT, job_id TEXT, document_revision_id TEXT,
+                    content_sha256 TEXT, parent_content_sha256 TEXT,
+                    extraction_method TEXT, parameters_json TEXT, review_state TEXT
+                 );",
+            )
+            .expect("create minimal evidence schema");
+        let original = Sha256Digest::from_bytes([4; 32]);
+        let manifest = Sha256Digest::from_bytes([2; 32]);
+        let document = DocumentId::from(Sha256Digest::from_bytes([3; 32]));
+        let revision = RevisionId::from(Sha256Digest::from_bytes([4; 32]));
+        for digest in [original, manifest] {
+            store
+                .connection
+                .execute(
+                    "INSERT INTO content_objects VALUES (?1, 7, ?2, ?3, 'accepted')",
+                    params![
+                        digest.to_string(),
+                        crate::Vault::object_key(digest),
+                        if digest == original {
+                            PDF_MEDIA_TYPE
+                        } else {
+                            EVIDENCE_MANIFEST_MEDIA_TYPE
+                        },
+                    ],
+                )
+                .expect("insert minimal content");
+        }
+        store
+            .connection
+            .execute(
+                "INSERT INTO document_revisions VALUES (?1, ?2)",
+                params![
+                    revision.as_digest().to_string(),
+                    document.as_digest().to_string(),
+                ],
+            )
+            .expect("insert minimal revision");
+        let page = accepted_page(original);
+        store
+            .connection
+            .execute(
+                "INSERT INTO sheets VALUES (?1, ?2, 0, ?3, ?4, 0, 'pt', ?5, ?6)",
+                params![
+                    page.page_id.as_digest().to_string(),
+                    revision.as_digest().to_string(),
+                    i64::try_from(page.width_micropoints).expect("page width"),
+                    i64::try_from(page.height_micropoints).expect("page height"),
+                    original.to_string(),
+                    json_text(&page.transform, ONE_MIB).expect("page transform JSON"),
+                ],
+            )
+            .expect("insert minimal sheet");
+        let parameters = EvidenceParametersV1::new(provenance(), PdfLimits::default().into())
+            .expect("minimal parameters");
+        let identifier = |value: u128| {
+            if valid_identifiers {
+                uuid(value).hyphenated().to_string()
+            } else {
+                "invalid".to_owned()
+            }
+        };
+        store
+            .connection
+            .execute(
+                "INSERT INTO evidence_objects VALUES
+                    (?1, ?2, ?3, ?4, ?5, ?6, 'heleos.pdf-probe/v1', ?7, 'accepted')",
+                params![
+                    identifier(300),
+                    identifier(301),
+                    identifier(302),
+                    revision.as_digest().to_string(),
+                    manifest.to_string(),
+                    original.to_string(),
+                    json_text(&parameters, ONE_MIB).expect("minimal parameters JSON"),
+                ],
+            )
+            .expect("insert first minimal lineage");
+        let mut populated = 1;
+        while populated < row_count {
+            let add = populated.min(row_count - populated);
+            store
+                .connection
+                .execute(
+                    "INSERT INTO evidence_objects
+                     SELECT id, project_id, job_id, document_revision_id, content_sha256,
+                            parent_content_sha256, extraction_method, parameters_json,
+                            review_state
+                     FROM evidence_objects LIMIT ?1",
+                    [i64::try_from(add).expect("copy count")],
+                )
+                .expect("double minimal lineages");
+            populated += add;
+        }
+        (store, revision)
+    }
+
+    fn minimal_foundation_inspection_store(audit_count: usize) -> (Store, ProjectId) {
+        assert!(audit_count > 0);
+        let store = Store::open_in_memory().expect("open minimal inspection store");
+        store
+            .connection
+            .execute_batch(
+                "CREATE TABLE projects (id TEXT);
+                 CREATE TABLE content_objects (
+                    sha256 TEXT, byte_length INTEGER, media_type TEXT,
+                    admission_state TEXT, vault_key TEXT, quarantine_reason TEXT
+                 );
+                 CREATE TABLE documents (id TEXT);
+                 CREATE TABLE document_revisions (
+                    id TEXT, document_id TEXT, content_sha256 TEXT
+                 );
+                 CREATE TABLE project_documents (project_id TEXT, document_id TEXT);
+                 CREATE TABLE sheets (
+                    id TEXT, revision_id TEXT, zero_based_page_index INTEGER,
+                    width_micropoints INTEGER, height_micropoints INTEGER, unit TEXT,
+                    rotation_degrees INTEGER, transform_json TEXT,
+                    parent_content_sha256 TEXT
+                 );
+                 CREATE TABLE evidence_objects (
+                    id TEXT, project_id TEXT, job_id TEXT, document_revision_id TEXT,
+                    content_sha256 TEXT, parent_content_sha256 TEXT,
+                    extraction_method TEXT, parameters_json TEXT, review_state TEXT
+                 );
+                 CREATE TABLE ingest_events (
+                    id TEXT, project_id TEXT, job_id TEXT, content_sha256 TEXT,
+                    outcome TEXT, attempt INTEGER, terminal_at_ms INTEGER
+                 );
+                 CREATE TABLE job_runs (
+                    id TEXT, project_id TEXT, kind TEXT, state TEXT, attempt INTEGER,
+                    created_at_ms INTEGER, updated_at_ms INTEGER, terminal_reason TEXT
+                 );
+                 CREATE TABLE audit_events (id TEXT, sequence INTEGER, project_id TEXT);",
+            )
+            .expect("create minimal inspection schema");
+        let project_id = ProjectId::from_uuid(uuid(900));
+        store
+            .connection
+            .execute(
+                "INSERT INTO projects VALUES (?1)",
+                [project_id.as_uuid().to_string()],
+            )
+            .expect("insert inspection project");
+        store
+            .connection
+            .execute(
+                "WITH RECURSIVE generated(value) AS (
+                    SELECT 1
+                    UNION ALL
+                    SELECT value + 1 FROM generated WHERE value < ?1
+                 )
+                 INSERT INTO audit_events
+                 SELECT printf('%08x-0000-4000-8000-%012x', value, value),
+                        value, ?2
+                 FROM generated",
+                params![
+                    i64::try_from(audit_count).expect("audit count"),
+                    project_id.as_uuid().to_string(),
+                ],
+            )
+            .expect("insert bounded audit rows");
+        (store, project_id)
+    }
+
+    fn inspection_store_with_quarantine(reason: IntakeQuarantineReasonV1) -> (Store, ProjectId) {
+        let (store, project_id) = minimal_foundation_inspection_store(1);
+        let digest = Sha256Digest::from_bytes([11; 32]);
+        let job_id = JobId::from_uuid(uuid(910));
+        let probe_provenance = match reason {
+            IntakeQuarantineReasonV1::Pdf(_) | IntakeQuarantineReasonV1::EvidenceManifestQuota => {
+                Some(provenance())
+            }
+            IntakeQuarantineReasonV1::InputBytes { .. }
+            | IntakeQuarantineReasonV1::OriginalRetentionQuota
+            | IntakeQuarantineReasonV1::ProjectAssociationQuota => None,
+        };
+        let quarantine = IntakeQuarantineV1 {
+            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+            reason: reason.clone(),
+            probe_provenance,
+        };
+        let outcome = if matches!(reason, IntakeQuarantineReasonV1::Pdf(_)) {
+            "quarantined_corrupt"
+        } else {
+            "quarantined_limit"
+        };
+        store
+            .connection
+            .execute(
+                "INSERT INTO content_objects VALUES (?1, 7, ?2, 'quarantined', ?3, ?4)",
+                params![
+                    digest.to_string(),
+                    PDF_MEDIA_TYPE,
+                    crate::Vault::object_key(digest),
+                    json_text(&quarantine, ONE_MIB).expect("quarantine JSON"),
+                ],
+            )
+            .expect("insert retained quarantine");
+        store
+            .connection
+            .execute(
+                "INSERT INTO job_runs VALUES (?1, ?2, 'pdf_ingest', 'succeeded', 1, 0, 0, 'completed')",
+                params![
+                    job_id.as_uuid().to_string(),
+                    project_id.as_uuid().to_string(),
+                ],
+            )
+            .expect("insert terminal quarantine job");
+        store
+            .connection
+            .execute(
+                "INSERT INTO ingest_events VALUES (?1, ?2, ?3, ?4, ?5, 1, 0)",
+                params![
+                    uuid(911).hyphenated().to_string(),
+                    project_id.as_uuid().to_string(),
+                    job_id.as_uuid().to_string(),
+                    digest.to_string(),
+                    outcome,
+                ],
+            )
+            .expect("insert retained quarantine event");
+        (store, project_id)
+    }
+
+    #[test]
+    fn accepted_commit_requires_the_exact_durable_processing_authority() {
+        // Break caught: terminal authority drifting from the frozen input/checkpoint job anchor.
+        let (store, command) = accepted_commit_fixture();
+        let vault_checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::VaultPublished {
+                original: command.original.clone(),
+            },
+        };
+        store
+            .connection
+            .execute(
+                "UPDATE job_runs SET checkpoint_json = ?1, updated_at_ms = 1",
+                [json_text(&vault_checkpoint, EIGHT_MIB).expect("vault checkpoint JSON")],
+            )
+            .expect("replace processing checkpoint with vault checkpoint");
+        assert_accepted_commit_rejected_without_authority(store, command);
+
+        let (store, mut command) = accepted_commit_fixture();
+        command.idempotency_key = IdempotencyKey::try_from("different-key").expect("key");
+        assert_accepted_commit_rejected_without_authority(store, command);
+
+        let (store, mut command) = accepted_commit_fixture();
+        command.source_display = "utf8:different.pdf".to_owned();
+        assert_accepted_commit_rejected_without_authority(store, command);
+
+        let (store, mut command) = accepted_commit_fixture();
+        replace_command_object_authority(&mut command);
+        assert_accepted_commit_rejected_without_authority(store, command);
+    }
+
+    #[test]
+    fn revision_evidence_count_is_bounded_before_rows_and_detects_count_drift() {
+        // Break caught: streaming hostile rows before the 100,000 count cap or trusting drift.
+        let (store, revision) = minimal_revision_evidence_store(100_000, true);
+        assert_eq!(
+            store
+                .revision_evidence_rows(revision)
+                .expect("100,000 lineages are permitted")
+                .evidence
+                .len(),
+            100_000
+        );
+
+        let (store, revision) = minimal_revision_evidence_store(100_001, false);
+        assert!(matches!(
+            store.revision_evidence_rows(revision),
+            Err(HeleosError::ResourceLimit)
+        ));
+
+        let (store, revision) = minimal_revision_evidence_store(2, true);
+        let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
+        let function_calls = std::sync::Arc::clone(&calls);
+        store
+            .connection
+            .create_scalar_function(
+                "task6_drift_visible",
+                1,
+                rusqlite::functions::FunctionFlags::SQLITE_UTF8
+                    | rusqlite::functions::FunctionFlags::SQLITE_INNOCUOUS,
+                move |context| {
+                    use std::sync::atomic::Ordering;
+
+                    let rowid = context.get::<i64>(0)?;
+                    let call = function_calls.fetch_add(1, Ordering::SeqCst);
+                    Ok(call < 2 || rowid == 1)
+                },
+            )
+            .expect("register deterministic drift seam");
+        store
+            .connection
+            .execute_batch(
+                "ALTER TABLE evidence_objects RENAME TO evidence_base;
+                 CREATE VIEW evidence_objects AS
+                 SELECT id, project_id, job_id, document_revision_id, content_sha256,
+                        parent_content_sha256, extraction_method, parameters_json, review_state
+                 FROM evidence_base
+                 WHERE task6_drift_visible(rowid);",
+            )
+            .expect("install count-drift view");
+        assert!(matches!(
+            store.revision_evidence_rows(revision),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn revision_evidence_page_count_accepts_n_and_rejects_n_plus_one_before_rows() {
+        // Break caught: fetching page metadata before enforcing the exact 10,000-page cap.
+        let (mut store, revision) = minimal_revision_evidence_store(1, true);
+        store
+            .connection
+            .execute("DELETE FROM sheets", [])
+            .expect("clear minimal sheet");
+        let transaction = store.connection.transaction().expect("begin page fixture");
+        {
+            let mut insert = transaction
+                .prepare("INSERT INTO sheets VALUES (?1, ?2, ?3, 1, 1, 0, 'pt', ?2, ?4)")
+                .expect("prepare page insert");
+            let transform = PageTransform {
+                m11: 1,
+                m12: 0,
+                m21: 0,
+                m22: -1,
+                tx_micropoints: 0,
+                ty_micropoints: 1,
+            };
+            let transform_json = json_text(&transform, ONE_MIB).expect("page transform JSON");
+            for index in 0_u32..10_000 {
+                insert
+                    .execute(params![
+                        page_id(*revision.as_digest(), index)
+                            .as_digest()
+                            .to_string(),
+                        revision.as_digest().to_string(),
+                        i64::from(index),
+                        transform_json,
+                    ])
+                    .expect("insert page");
+            }
+        }
+        transaction.commit().expect("commit page fixture");
+        assert_eq!(
+            store
+                .revision_evidence_rows(revision)
+                .expect("10,000 pages are permitted")
+                .pages
+                .len(),
+            10_000
+        );
+        store
+            .connection
+            .execute("INSERT INTO sheets SELECT * FROM sheets LIMIT 1", [])
+            .expect("insert page cap sentinel");
+        assert!(matches!(
+            store.revision_evidence_rows(revision),
+            Err(HeleosError::ResourceLimit)
+        ));
+    }
+
+    #[test]
+    fn foundation_inspection_accepts_only_sticky_database_quarantines() {
+        // Break caught: a preflight-only quarantine reason appearing as retained content.
+        for reason in [
+            IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic),
+            IntakeQuarantineReasonV1::EvidenceManifestQuota,
+        ] {
+            let (store, project_id) = inspection_store_with_quarantine(reason);
+            store
+                .foundation_inspection_rows(project_id)
+                .expect("sticky quarantine is inspectable");
+        }
+
+        for reason in [
+            IntakeQuarantineReasonV1::InputBytes {
+                limit_bytes: PdfLimits::default().max_input_bytes,
+                observed_bytes: PdfLimits::default().max_input_bytes + 1,
+            },
+            IntakeQuarantineReasonV1::OriginalRetentionQuota,
+            IntakeQuarantineReasonV1::ProjectAssociationQuota,
+        ] {
+            let (store, project_id) = inspection_store_with_quarantine(reason);
+            assert!(matches!(
+                store.foundation_inspection_rows(project_id),
+                Err(HeleosError::Integrity)
+            ));
+        }
+    }
+
+    #[test]
+    fn foundation_inspection_counts_are_bounded_before_rows_and_detect_drift() {
+        // Break caught: inspection allocating from row declarations or trusting count/fetch drift.
+        let (store, project_id) =
+            minimal_foundation_inspection_store(MAX_FOUNDATION_INSPECTION_ROWS);
+        let inspection = store
+            .foundation_inspection_rows(project_id)
+            .expect("exact 100,000 total rows are permitted");
+        assert_eq!(
+            inspection.counts.audit_events,
+            u64::try_from(MAX_FOUNDATION_INSPECTION_ROWS).expect("inspection cap")
+        );
+
+        let (store, project_id) =
+            minimal_foundation_inspection_store(MAX_FOUNDATION_INSPECTION_ROWS + 1);
+        assert!(matches!(
+            store.foundation_inspection_rows(project_id),
+            Err(HeleosError::ResourceLimit)
+        ));
+
+        let (store, project_id) = minimal_foundation_inspection_store(2);
+        let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
+        let function_calls = std::sync::Arc::clone(&calls);
+        store
+            .connection
+            .create_scalar_function(
+                "task6_inspection_drift_visible",
+                1,
+                rusqlite::functions::FunctionFlags::SQLITE_UTF8
+                    | rusqlite::functions::FunctionFlags::SQLITE_INNOCUOUS,
+                move |context| {
+                    use std::sync::atomic::Ordering;
+
+                    let sequence = context.get::<i64>(0)?;
+                    let call = function_calls.fetch_add(1, Ordering::SeqCst);
+                    Ok(call < 2 || sequence == 1)
+                },
+            )
+            .expect("register inspection drift seam");
+        store
+            .connection
+            .execute_batch(
+                "ALTER TABLE audit_events RENAME TO audit_base;
+                 CREATE VIEW audit_events AS
+                 SELECT id, sequence, project_id
+                 FROM audit_base
+                 WHERE task6_inspection_drift_visible(sequence);",
+            )
+            .expect("install inspection count-drift view");
+        assert!(matches!(
+            store.foundation_inspection_rows(project_id),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn accepted_commit_audits_a_bounded_summary_not_the_full_lineage_receipt() {
+        // Break caught: a valid large lineage receipt exceeding the independent audit JSON cap.
+        let (mut store, mut command) = accepted_commit_fixture();
+        prepopulate_revision_lineages(&mut store, &command, 7_000);
+        command.preexisting_vault_digests =
+            vec![command.original.digest, command.manifest_object.digest];
+        command.preexisting_vault_digests.sort_unstable();
+        let receipt = store
+            .accepted_intake_commit(command)
+            .expect("large valid receipt commits");
+        assert!(
+            serde_json::to_vec(&receipt)
+                .expect("serialize large receipt")
+                .len()
+                > ONE_MIB
+        );
+        let after_json = store
+            .connection
+            .query_row(
+                "SELECT after_json FROM audit_events WHERE action = 'ingest_accepted'",
+                [],
+                |row| row.get::<_, String>(0),
+            )
+            .expect("read accepted audit summary");
+        assert!(after_json.len() <= ONE_MIB);
+        let after: JsonValue = serde_json::from_str(&after_json).expect("parse audit summary");
+        let evidence = receipt
+            .evidence_manifest
+            .as_ref()
+            .expect("accepted evidence receipt");
+        let evidence_id = evidence
+            .lineages
+            .iter()
+            .find(|lineage| lineage.project_id == ProjectId::from_uuid(uuid(100)))
+            .expect("current project lineage")
+            .evidence_id;
+        assert_eq!(
+            after,
+            json!({
+                "attempt": receipt.attempt,
+                "authoritative_job_id": receipt.authoritative_job_id,
+                "content_sha256": receipt.content_sha256,
+                "document_id": receipt.document_id,
+                "evidence_id": evidence_id,
+                "ingest_event_id": receipt.ingest_event_id,
+                "manifest_content_sha256": evidence.manifest_content_sha256,
+                "revision_id": receipt.revision_id,
+            })
+        );
+    }
+
+    #[test]
+    fn borrowed_audit_text_is_copied_only_within_the_field_cap() {
+        // Break caught: row.get::<Value> allocating hostile audit text before its cap is checked.
+        let connection = rusqlite::Connection::open_in_memory().expect("open test connection");
+        let at_cap = "x".repeat(1024 * 1024);
+        let over_cap = "x".repeat(1024 * 1024 + 1);
+        let mut statement = connection
+            .prepare("SELECT ?1 UNION ALL SELECT ?2")
+            .expect("prepare bounded-value query");
+        let mut rows = statement
+            .query(params![at_cap, over_cap])
+            .expect("query bounded values");
+        let first = rows.next().expect("first row query").expect("first row");
+        assert!(matches!(
+            bounded_value(first, 0, 1024 * 1024).expect("read at-cap value"),
+            RawAuditValue::Text(value) if value.len() == 1024 * 1024
+        ));
+        let second = rows.next().expect("second row query").expect("second row");
+        assert!(matches!(
+            bounded_value(second, 0, 1024 * 1024).expect("read over-cap value"),
+            RawAuditValue::Invalid
+        ));
+    }
+
+    #[test]
+    fn persisted_job_reconstruction_binds_budget_checkpoint_and_terminal_job_authority() {
+        // Break caught: individually valid JCS objects drifting from their containing job.
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let project_id = ProjectId::from_uuid(uuid(1));
+        insert_project(&store, project_id);
+
+        let job_id = JobId::from_uuid(uuid(2));
+        let input = valid_input(project_id, "budget-drift");
+        let budget = IngestBudgetV1::new(input.byte_length + 1);
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::Terminal {
+                receipt: accepted_receipt(
+                    job_id,
+                    vec![EvidenceManifestLineage {
+                        project_id,
+                        evidence_id: EvidenceId::from_uuid(uuid(3)),
+                        originating_job_id: job_id,
+                    }],
+                ),
+            },
+        };
+        insert_job(
+            &store,
+            project_id,
+            job_id,
+            "budget-drift",
+            &input,
+            &budget,
+            &checkpoint,
+        );
+        assert!(matches!(
+            store.idempotency_lookup(project_id, &input.idempotency_key),
+            Err(HeleosError::Integrity)
+        ));
+
+        let job_id = JobId::from_uuid(uuid(4));
+        let input = valid_input(project_id, "job-drift");
+        let budget = IngestBudgetV1::new(input.byte_length);
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::Terminal {
+                receipt: accepted_receipt(
+                    JobId::from_uuid(uuid(99)),
+                    vec![EvidenceManifestLineage {
+                        project_id,
+                        evidence_id: EvidenceId::from_uuid(uuid(5)),
+                        originating_job_id: JobId::from_uuid(uuid(99)),
+                    }],
+                ),
+            },
+        };
+        insert_job(
+            &store,
+            project_id,
+            job_id,
+            "job-drift",
+            &input,
+            &budget,
+            &checkpoint,
+        );
+        assert!(matches!(
+            store.idempotency_lookup(project_id, &input.idempotency_key),
+            Err(HeleosError::Integrity)
+        ));
+
+        let job_id = JobId::from_uuid(uuid(6));
+        let input = valid_input(project_id, "deadline-drift");
+        let budget = IngestBudgetV1::new(input.byte_length);
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::Terminal {
+                receipt: accepted_receipt(
+                    job_id,
+                    vec![EvidenceManifestLineage {
+                        project_id,
+                        evidence_id: EvidenceId::from_uuid(uuid(7)),
+                        originating_job_id: job_id,
+                    }],
+                ),
+            },
+        };
+        insert_job_with_times(
+            (
+                &store,
+                project_id,
+                job_id,
+                "deadline-drift",
+                &input,
+                &budget,
+                &checkpoint,
+            ),
+            10,
+            300_011,
+        );
+        assert!(matches!(
+            store.idempotency_lookup(project_id, &input.idempotency_key),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn repository_derives_fixed_deadline_and_lease_boundaries() {
+        // Break caught: caller-selected recovery clocks escaping the frozen lifecycle profile.
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let project_id = ProjectId::from_uuid(uuid(30));
+        insert_project(&store, project_id);
+        let actor = ActorId::from_str("fixture-actor").expect("actor");
+        let job_id = JobId::from_uuid(uuid(31));
+        let input = valid_input(project_id, "deadline-command-drift");
+        let original = StoredObjectV1 {
+            digest: input.content_sha256.expect("content digest"),
+            byte_length: input.byte_length,
+            vault_key: crate::Vault::object_key(input.content_sha256.expect("content digest")),
+        };
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::VaultPublished { original },
+        };
+        assert!(matches!(
+            store.job_create_with_checkpoint_and_audit(JobCreateCommand {
+                job_id,
+                project_id,
+                input: input.clone(),
+                budget: IngestBudgetV1::new(input.byte_length),
+                checkpoint: checkpoint.clone(),
+                actor: actor.clone(),
+                created_at_ms: 10,
+                deadline_at_ms: 300_011,
+                audit_event_id: AuditEventId::from_uuid(uuid(32)),
+            }),
+            Err(HeleosError::Integrity)
+        ));
+        let near_jcs_max = i64::try_from(JCS_SAFE_INTEGER_MAX).expect("JCS maximum fits i64");
+        assert!(matches!(
+            store.job_create_with_checkpoint_and_audit(JobCreateCommand {
+                job_id,
+                project_id,
+                input: input.clone(),
+                budget: IngestBudgetV1::new(input.byte_length),
+                checkpoint: checkpoint.clone(),
+                actor: actor.clone(),
+                created_at_ms: near_jcs_max - JOB_DEADLINE_MS + 1,
+                deadline_at_ms: near_jcs_max,
+                audit_event_id: AuditEventId::from_uuid(uuid(36)),
+            }),
+            Err(HeleosError::Integrity)
+        ));
+
+        store
+            .job_create_with_checkpoint_and_audit(JobCreateCommand {
+                job_id,
+                project_id,
+                input,
+                budget: IngestBudgetV1::new(7),
+                checkpoint,
+                actor: actor.clone(),
+                created_at_ms: 10,
+                deadline_at_ms: 300_010,
+                audit_event_id: AuditEventId::from_uuid(uuid(33)),
+            })
+            .expect("create job with fixed deadline");
+        assert!(matches!(
+            store.job_start_or_resume_with_audit(JobStartCommand {
+                job_id,
+                project_id,
+                actor,
+                lease_owner: uuid(34),
+                now_ms: 20,
+                lease_expires_at_ms: 30_021,
+                audit_event_id: AuditEventId::from_uuid(uuid(35)),
+            }),
+            Err(HeleosError::Integrity)
+        ));
+        assert!(matches!(
+            store.job_start_or_resume_with_audit(JobStartCommand {
+                job_id,
+                project_id,
+                actor: ActorId::from_str("fixture-actor").expect("actor"),
+                lease_owner: uuid(37),
+                now_ms: near_jcs_max - LEASE_DURATION_MS + 1,
+                lease_expires_at_ms: near_jcs_max,
+                audit_event_id: AuditEventId::from_uuid(uuid(38)),
+            }),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn quota_snapshot_reserves_nonterminal_vault_published_objects() {
+        // Break caught: a crashed published object leaving quota headroom available to a rival job.
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let project_id = ProjectId::from_uuid(uuid(40));
+        insert_project(&store, project_id);
+        let input = valid_input(project_id, "reserved-original");
+        let digest = input.content_sha256.expect("complete digest");
+        store
+            .job_create_with_checkpoint_and_audit(JobCreateCommand {
+                job_id: JobId::from_uuid(uuid(41)),
+                project_id,
+                input: input.clone(),
+                budget: IngestBudgetV1::new(input.byte_length),
+                checkpoint: IngestCheckpointV1 {
+                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                    phase: IngestCheckpointPhaseV1::VaultPublished {
+                        original: StoredObjectV1 {
+                            digest,
+                            byte_length: input.byte_length,
+                            vault_key: crate::Vault::object_key(digest),
+                        },
+                    },
+                },
+                actor: ActorId::from_str("fixture-actor").expect("actor"),
+                created_at_ms: 10,
+                deadline_at_ms: 300_010,
+                audit_event_id: AuditEventId::from_uuid(uuid(42)),
+            })
+            .expect("create reserved job");
+
+        let snapshot = store
+            .quota_snapshot(project_id, None)
+            .expect("snapshot durable reservations");
+        assert_eq!(snapshot.project_overall_bytes, input.byte_length);
+        assert_eq!(snapshot.project_quarantine_bytes, input.byte_length);
+        assert_eq!(snapshot.store_overall_bytes, input.byte_length);
+        assert_eq!(snapshot.store_quarantine_bytes, input.byte_length);
+        assert_eq!(snapshot.project_evidence_bytes, 0);
+        assert_eq!(snapshot.store_evidence_bytes, 0);
+    }
+
+    #[test]
+    fn quota_snapshot_classifies_processing_reservations_without_double_counting() {
+        // Break caught: processing checkpoints dropping original/manifest category reservations.
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let project_id = ProjectId::from_uuid(uuid(50));
+        insert_project(&store, project_id);
+        let actor = ActorId::from_str("fixture-actor").expect("actor");
+        let input = valid_input(project_id, "accepted-reservation");
+        let original = StoredObjectV1 {
+            digest: input.content_sha256.expect("complete digest"),
+            byte_length: input.byte_length,
+            vault_key: crate::Vault::object_key(input.content_sha256.expect("complete digest")),
+        };
+        let manifest = accepted_manifest(original.digest);
+        let manifest_bytes = manifest.canonical_bytes().expect("manifest bytes");
+        let manifest_object = StoredObjectV1 {
+            digest: Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest"),
+            byte_length: u64::try_from(manifest_bytes.len()).expect("manifest length"),
+            vault_key: crate::Vault::object_key(
+                Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest"),
+            ),
+        };
+        let job_id = JobId::from_uuid(uuid(51));
+        store
+            .job_create_with_checkpoint_and_audit(JobCreateCommand {
+                job_id,
+                project_id,
+                input: input.clone(),
+                budget: IngestBudgetV1::new(input.byte_length),
+                checkpoint: IngestCheckpointV1 {
+                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                    phase: IngestCheckpointPhaseV1::VaultPublished {
+                        original: original.clone(),
+                    },
+                },
+                actor: actor.clone(),
+                created_at_ms: 10,
+                deadline_at_ms: 300_010,
+                audit_event_id: AuditEventId::from_uuid(uuid(52)),
+            })
+            .expect("create accepted reservation");
+        store
+            .job_start_or_resume_with_audit(JobStartCommand {
+                job_id,
+                project_id,
+                actor: actor.clone(),
+                lease_owner: uuid(53),
+                now_ms: 11,
+                lease_expires_at_ms: 30_011,
+                audit_event_id: AuditEventId::from_uuid(uuid(54)),
+            })
+            .expect("start accepted reservation");
+        store
+            .job_checkpoint_with_audit(JobCheckpointCommand {
+                job_id,
+                project_id,
+                actor,
+                attempt: 1,
+                checkpoint: IngestCheckpointV1 {
+                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                    phase: IngestCheckpointPhaseV1::ProcessingComplete {
+                        original: original.clone(),
+                        candidate: crate::ingest::job::ProcessingCandidateV1::Accepted {
+                            manifest: manifest.clone(),
+                            manifest_object: manifest_object.clone(),
+                        },
+                    },
+                },
+                now_ms: 12,
+                audit_event_id: AuditEventId::from_uuid(uuid(55)),
+            })
+            .expect("checkpoint accepted reservation");
+
+        let snapshot = store
+            .quota_snapshot(project_id, Some(original.digest))
+            .expect("snapshot accepted reservations");
+        let combined = original
+            .byte_length
+            .checked_add(manifest_object.byte_length)
+            .expect("combined length");
+        assert_eq!(snapshot.store_overall_bytes, combined);
+        assert_eq!(snapshot.project_overall_bytes, combined);
+        assert_eq!(snapshot.store_evidence_bytes, manifest_object.byte_length);
+        assert_eq!(snapshot.project_evidence_bytes, manifest_object.byte_length);
+        assert_eq!(snapshot.store_quarantine_bytes, 0);
+        assert_eq!(snapshot.project_quarantine_bytes, 0);
+        assert!(snapshot.project_overall_accounted);
+        assert!(!snapshot.project_quarantine_accounted);
+
+        store
+            .accepted_intake_commit(AcceptedIntakeCommand {
+                job_id,
+                project_id,
+                attempt: 1,
+                actor: ActorId::from_str("fixture-actor").expect("actor"),
+                idempotency_key: input.idempotency_key.clone(),
+                source_display: input.source_display.clone(),
+                original: original.clone(),
+                manifest_object: manifest_object.clone(),
+                manifest,
+                pages: vec![accepted_page(original.digest)],
+                ingest_event_id: IngestEventId::from_uuid(uuid(56)),
+                source_record_id: uuid(57).hyphenated().to_string(),
+                evidence_id: EvidenceId::from_uuid(uuid(58)),
+                now_ms: 20,
+                preexisting_vault_digests: Vec::new(),
+                audit_event_ids: [
+                    AuditEventId::from_uuid(uuid(59)),
+                    AuditEventId::from_uuid(uuid(60)),
+                    AuditEventId::from_uuid(uuid(61)),
+                ],
+            })
+            .expect("commit canonical accepted authority");
+        let duplicate_input = valid_input(project_id, "accepted-reservation-duplicate");
+        store
+            .job_create_with_checkpoint_and_audit(JobCreateCommand {
+                job_id: JobId::from_uuid(uuid(62)),
+                project_id,
+                input: duplicate_input.clone(),
+                budget: IngestBudgetV1::new(duplicate_input.byte_length),
+                checkpoint: IngestCheckpointV1 {
+                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                    phase: IngestCheckpointPhaseV1::VaultPublished {
+                        original: original.clone(),
+                    },
+                },
+                actor: ActorId::from_str("fixture-actor").expect("actor"),
+                created_at_ms: 21,
+                deadline_at_ms: 300_021,
+                audit_event_id: AuditEventId::from_uuid(uuid(63)),
+            })
+            .expect("create canonical duplicate reservation");
+        let deduplicated = store
+            .quota_snapshot(project_id, Some(original.digest))
+            .expect("deduplicate committed and reserved original");
+        assert_eq!(deduplicated.store_overall_bytes, combined);
+    }
+
+    #[test]
+    fn quota_reservation_dedup_conflicts_caps_and_audit_anchors_fail_closed() {
+        // Break caught: duplicate jobs multiplying usage or hostile reservations escaping bounds.
+        assert_eq!(
+            bounded_nonterminal_job_count(
+                i64::try_from(MAX_FOUNDATION_INSPECTION_ROWS).expect("cap fits")
+            )
+            .expect("exact cap"),
+            MAX_FOUNDATION_INSPECTION_ROWS
+        );
+        assert!(matches!(
+            bounded_nonterminal_job_count(
+                i64::try_from(MAX_FOUNDATION_INSPECTION_ROWS + 1).expect("N+1 fits")
+            ),
+            Err(HeleosError::ResourceLimit)
+        ));
+        let digest = Sha256Digest::from_bytes([7; 32]);
+        let mut reservations = BTreeMap::new();
+        insert_reservation(
+            &mut reservations,
+            digest,
+            ReservedObject {
+                byte_length: 7,
+                media: ReservedMedia::Pdf,
+            },
+        )
+        .expect("first reservation");
+        insert_reservation(
+            &mut reservations,
+            digest,
+            ReservedObject {
+                byte_length: 7,
+                media: ReservedMedia::Pdf,
+            },
+        )
+        .expect("equal reservation deduplicates");
+        assert_eq!(reservations.len(), 1);
+        assert!(matches!(
+            insert_reservation(
+                &mut reservations,
+                digest,
+                ReservedObject {
+                    byte_length: 8,
+                    media: ReservedMedia::Pdf,
+                },
+            ),
+            Err(HeleosError::Integrity)
+        ));
+        assert!(matches!(
+            insert_reservation(
+                &mut reservations,
+                digest,
+                ReservedObject {
+                    byte_length: 7,
+                    media: ReservedMedia::EvidenceManifest,
+                },
+            ),
+            Err(HeleosError::Integrity)
+        ));
+
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let project_id = ProjectId::from_uuid(uuid(60));
+        insert_project(&store, project_id);
+        let input = valid_input(project_id, "missing-anchor");
+        let budget = IngestBudgetV1::new(input.byte_length);
+        let original = StoredObjectV1 {
+            digest: input.content_sha256.expect("digest"),
+            byte_length: input.byte_length,
+            vault_key: crate::Vault::object_key(input.content_sha256.expect("digest")),
+        };
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::VaultPublished { original },
+        };
+        store
+            .connection
+            .execute(
+                "INSERT INTO job_runs
+                    (id, project_id, kind, idempotency_key, state, attempt, lease_owner,
+                     lease_expires_at_ms, deadline_at_ms, budget_json, input_json,
+                     checkpoint_json, terminal_reason, created_at_ms, updated_at_ms)
+                 VALUES (?1, ?2, 'pdf_ingest', ?3, 'queued', 0, NULL, NULL, 300000,
+                         ?4, ?5, ?6, NULL, 0, 0)",
+                params![
+                    JobId::from_uuid(uuid(61)).as_uuid().to_string(),
+                    project_id.as_uuid().to_string(),
+                    input.idempotency_key.as_str(),
+                    json_text(&budget, ONE_MIB).expect("budget JSON"),
+                    json_text(&input, ONE_MIB).expect("input JSON"),
+                    json_text(&checkpoint, EIGHT_MIB).expect("checkpoint JSON"),
+                ],
+            )
+            .expect("insert unaudited nonterminal job");
+        assert!(matches!(
+            store.quota_snapshot(project_id, None),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn vault_inventory_unions_content_and_every_nonterminal_checkpoint_phase() {
+        // Break caught: backup/reconciliation omitting a resumable original or accepted manifest.
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let project_id = ProjectId::from_uuid(uuid(70));
+        insert_project(&store, project_id);
+
+        let committed = stored_object(70, 7);
+        store
+            .connection
+            .execute(
+                "INSERT INTO content_objects
+                    (sha256, byte_length, media_type, admission_state, vault_key,
+                     created_at_ms, created_by, quarantine_reason)
+                 VALUES (?1, ?2, ?3, 'accepted', ?4, 0, 'fixture-actor', NULL)",
+                params![
+                    committed.digest.to_string(),
+                    i64::try_from(committed.byte_length).expect("length"),
+                    PDF_MEDIA_TYPE,
+                    committed.vault_key,
+                ],
+            )
+            .expect("insert committed inventory object");
+
+        let preflight = stored_object(71, 7);
+        let preflight_input = input_for_object(project_id, "inventory-preflight", &preflight);
+        queue_inventory_job(
+            &mut store,
+            project_id,
+            JobId::from_uuid(uuid(71)),
+            preflight_input,
+            IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::PreflightRejected {
+                    content_sha256: Some(preflight.digest),
+                    byte_length: preflight.byte_length,
+                    quarantine: IntakeQuarantineV1 {
+                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                        reason: IntakeQuarantineReasonV1::OriginalRetentionQuota,
+                        probe_provenance: None,
+                    },
+                },
+            },
+            10,
+            AuditEventId::from_uuid(uuid(72)),
+        );
+
+        let published = stored_object(72, 7);
+        queue_inventory_job(
+            &mut store,
+            project_id,
+            JobId::from_uuid(uuid(73)),
+            input_for_object(project_id, "inventory-published", &published),
+            IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::VaultPublished {
+                    original: published.clone(),
+                },
+            },
+            20,
+            AuditEventId::from_uuid(uuid(74)),
+        );
+
+        let accepted = stored_object(73, 7);
+        let accepted_manifest = accepted_manifest(accepted.digest);
+        let accepted_manifest_bytes = accepted_manifest.canonical_bytes().expect("manifest bytes");
+        let accepted_manifest_digest =
+            Sha256Digest::hash_reader(accepted_manifest_bytes.as_slice()).expect("manifest digest");
+        let accepted_manifest_object = StoredObjectV1 {
+            digest: accepted_manifest_digest,
+            byte_length: u64::try_from(accepted_manifest_bytes.len()).expect("manifest length"),
+            vault_key: crate::Vault::object_key(accepted_manifest_digest),
+        };
+        let accepted_job_id = JobId::from_uuid(uuid(75));
+        queue_inventory_job(
+            &mut store,
+            project_id,
+            accepted_job_id,
+            input_for_object(project_id, "inventory-accepted", &accepted),
+            IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::VaultPublished {
+                    original: accepted.clone(),
+                },
+            },
+            30,
+            AuditEventId::from_uuid(uuid(76)),
+        );
+        start_inventory_job(
+            &mut store,
+            project_id,
+            accepted_job_id,
+            31,
+            uuid(77),
+            AuditEventId::from_uuid(uuid(78)),
+        );
+        store
+            .job_checkpoint_with_audit(JobCheckpointCommand {
+                job_id: accepted_job_id,
+                project_id,
+                actor: ActorId::from_str("fixture-actor").expect("actor"),
+                attempt: 1,
+                checkpoint: IngestCheckpointV1 {
+                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                    phase: IngestCheckpointPhaseV1::ProcessingComplete {
+                        original: accepted.clone(),
+                        candidate: crate::ingest::job::ProcessingCandidateV1::Accepted {
+                            manifest: accepted_manifest,
+                            manifest_object: accepted_manifest_object.clone(),
+                        },
+                    },
+                },
+                now_ms: 32,
+                audit_event_id: AuditEventId::from_uuid(uuid(79)),
+            })
+            .expect("checkpoint accepted inventory job");
+
+        let quarantined = stored_object(74, 7);
+        let quarantined_job_id = JobId::from_uuid(uuid(80));
+        queue_inventory_job(
+            &mut store,
+            project_id,
+            quarantined_job_id,
+            input_for_object(project_id, "inventory-quarantined", &quarantined),
+            IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::VaultPublished {
+                    original: quarantined.clone(),
+                },
+            },
+            40,
+            AuditEventId::from_uuid(uuid(81)),
+        );
+        start_inventory_job(
+            &mut store,
+            project_id,
+            quarantined_job_id,
+            41,
+            uuid(82),
+            AuditEventId::from_uuid(uuid(83)),
+        );
+        store
+            .job_checkpoint_with_audit(JobCheckpointCommand {
+                job_id: quarantined_job_id,
+                project_id,
+                actor: ActorId::from_str("fixture-actor").expect("actor"),
+                attempt: 1,
+                checkpoint: IngestCheckpointV1 {
+                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                    phase: IngestCheckpointPhaseV1::ProcessingComplete {
+                        original: quarantined.clone(),
+                        candidate: crate::ingest::job::ProcessingCandidateV1::Quarantined {
+                            outcome: IngestOutcome::QuarantinedCorrupt,
+                            quarantine: IntakeQuarantineV1 {
+                                schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                                reason: IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::Corrupt),
+                                probe_provenance: Some(provenance()),
+                            },
+                        },
+                    },
+                },
+                now_ms: 42,
+                audit_event_id: AuditEventId::from_uuid(uuid(84)),
+            })
+            .expect("checkpoint quarantined inventory job");
+        store
+            .with_immediate_transaction(|transaction| {
+                let before = job_audit_snapshot(transaction, quarantined_job_id)?;
+                let changed = transaction
+                    .execute(
+                        "UPDATE job_runs
+                         SET state = 'interrupted', lease_owner = NULL,
+                             lease_expires_at_ms = NULL, updated_at_ms = 43
+                         WHERE id = ?1 AND state = 'running' AND attempt = 1",
+                        [quarantined_job_id.as_uuid().to_string()],
+                    )
+                    .map_err(|_| HeleosError::Database)?;
+                if changed != 1 {
+                    return Err(HeleosError::Integrity);
+                }
+                append_action(
+                    transaction,
+                    AppendAction {
+                        id: AuditEventId::from_uuid(uuid(85)),
+                        project_id,
+                        actor: ActorId::from_str("fixture-actor").expect("actor"),
+                        action: AuditAction::JobInterrupted,
+                        subject_type: AuditSubjectType::Job,
+                        subject_id: quarantined_job_id.as_uuid().to_string(),
+                        before,
+                        after: job_audit_snapshot(transaction, quarantined_job_id)?,
+                        reason: "inventory fixture interruption",
+                        occurred_at_ms: 43,
+                    },
+                )?;
+                Ok(())
+            })
+            .expect("persist interrupted inventory fixture");
+
+        let expected = VaultInventory::try_from_entries([
+            inventory_entry(&committed),
+            inventory_entry(&published),
+            inventory_entry(&accepted),
+            inventory_entry(&accepted_manifest_object),
+            inventory_entry(&quarantined),
+        ])
+        .expect("build expected inventory");
+        assert_eq!(
+            store.vault_inventory_rows().expect("read vault inventory"),
+            expected
+        );
+    }
+
+    #[test]
+    fn vault_inventory_excludes_failed_and_cancelled_job_objects() {
+        // Break caught: a terminal failed/cancelled checkpoint being mistaken for resumable data.
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let project_id = ProjectId::from_uuid(uuid(90));
+        insert_project(&store, project_id);
+
+        let failed = stored_object(90, 7);
+        let failed_job_id = JobId::from_uuid(uuid(91));
+        queue_inventory_job(
+            &mut store,
+            project_id,
+            failed_job_id,
+            input_for_object(project_id, "inventory-failed", &failed),
+            IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::VaultPublished { original: failed },
+            },
+            10,
+            AuditEventId::from_uuid(uuid(92)),
+        );
+        start_inventory_job(
+            &mut store,
+            project_id,
+            failed_job_id,
+            11,
+            uuid(93),
+            AuditEventId::from_uuid(uuid(94)),
+        );
+        store
+            .job_fail_with_audit(JobFailCommand {
+                job_id: failed_job_id,
+                project_id,
+                attempt: 1,
+                actor: ActorId::from_str("fixture-actor").expect("actor"),
+                now_ms: 12,
+                audit_event_id: AuditEventId::from_uuid(uuid(95)),
+            })
+            .expect("fail inventory fixture job");
+
+        let cancelled = stored_object(91, 7);
+        let cancelled_job_id = JobId::from_uuid(uuid(96));
+        queue_inventory_job(
+            &mut store,
+            project_id,
+            cancelled_job_id,
+            input_for_object(project_id, "inventory-cancelled", &cancelled),
+            IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::VaultPublished {
+                    original: cancelled,
+                },
+            },
+            20,
+            AuditEventId::from_uuid(uuid(97)),
+        );
+        store
+            .connection
+            .execute(
+                "UPDATE job_runs
+                 SET state = 'cancelled', terminal_reason = 'cancelled', updated_at_ms = 21
+                 WHERE id = ?1",
+                [cancelled_job_id.as_uuid().to_string()],
+            )
+            .expect("cancel inventory fixture job");
+
+        assert_eq!(
+            store.vault_inventory_rows().expect("read empty inventory"),
+            VaultInventory::try_from_entries([]).expect("empty inventory")
+        );
+    }
+
+    #[test]
+    fn vault_inventory_rejects_noncanonical_job_json_stale_audit_and_object_conflicts() {
+        // Break caught: trusting mutable checkpoint bytes or silently choosing one object length.
+        fn queued_store() -> (Store, ProjectId, StoredObjectV1) {
+            let mut store = Store::open_in_memory().expect("open store");
+            store.migrate().expect("migrate store");
+            let project_id = ProjectId::from_uuid(uuid(110));
+            insert_project(&store, project_id);
+            let object = stored_object(110, 7);
+            queue_inventory_job(
+                &mut store,
+                project_id,
+                JobId::from_uuid(uuid(111)),
+                input_for_object(project_id, "inventory-authority", &object),
+                IngestCheckpointV1 {
+                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                    phase: IngestCheckpointPhaseV1::VaultPublished {
+                        original: object.clone(),
+                    },
+                },
+                10,
+                AuditEventId::from_uuid(uuid(112)),
+            );
+            (store, project_id, object)
+        }
+
+        for column in ["input_json", "budget_json", "checkpoint_json"] {
+            let (store, _, _) = queued_store();
+            store
+                .connection
+                .execute_batch(
+                    "DROP TRIGGER job_runs_frozen_identity;
+                     DROP TRIGGER job_runs_legal_transition;
+                     PRAGMA ignore_check_constraints = ON;",
+                )
+                .expect("open hostile job fixture");
+            let current = store
+                .connection
+                .query_row(&format!("SELECT {column} FROM job_runs"), [], |row| {
+                    row.get::<_, String>(0)
+                })
+                .expect("read canonical job JSON");
+            store
+                .connection
+                .execute(
+                    &format!("UPDATE job_runs SET {column} = ?1"),
+                    [format!(" {current}")],
+                )
+                .expect("write noncanonical job JSON");
+            assert!(matches!(
+                store.vault_inventory_rows(),
+                Err(HeleosError::Integrity)
+            ));
+        }
+
+        let (store, _, _) = queued_store();
+        store
+            .connection
+            .execute_batch("DROP TRIGGER audit_events_no_update;")
+            .expect("open hostile audit fixture");
+        store
+            .connection
+            .execute(
+                "UPDATE audit_events SET after_json = '{}' WHERE subject_type = 'job'",
+                [],
+            )
+            .expect("replace latest audit anchor");
+        assert!(matches!(
+            store.vault_inventory_rows(),
+            Err(HeleosError::Integrity)
+        ));
+
+        let (mut store, project_id, object) = queued_store();
+        assert_eq!(
+            store.vault_inventory_rows().expect("deduplicate exact row"),
+            VaultInventory::try_from_entries([inventory_entry(&object)])
+                .expect("expected deduplicated inventory")
+        );
+        let conflicting = StoredObjectV1 {
+            byte_length: object.byte_length + 1,
+            ..object.clone()
+        };
+        queue_inventory_job(
+            &mut store,
+            project_id,
+            JobId::from_uuid(uuid(113)),
+            input_for_object(project_id, "inventory-conflict", &conflicting),
+            IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::VaultPublished {
+                    original: conflicting,
+                },
+            },
+            20,
+            AuditEventId::from_uuid(uuid(114)),
+        );
+        assert!(matches!(
+            store.vault_inventory_rows(),
+            Err(HeleosError::Integrity)
+        ));
+
+        let (store, _, object) = queued_store();
+        store
+            .connection
+            .execute(
+                "INSERT INTO content_objects
+                    (sha256, byte_length, media_type, admission_state, vault_key,
+                     created_at_ms, created_by, quarantine_reason)
+                 VALUES (?1, ?2, 'application/pdf', 'accepted', 'wrong/key',
+                         0, 'fixture-actor', NULL)",
+                params![
+                    object.digest.to_string(),
+                    i64::try_from(object.byte_length).expect("object length"),
+                ],
+            )
+            .expect("insert wrong-key content authority");
+        assert!(matches!(
+            store.vault_inventory_rows(),
+            Err(HeleosError::Integrity)
+        ));
+
+        let (store, _, object) = queued_store();
+        store
+            .connection
+            .execute(
+                "INSERT INTO content_objects
+                    (sha256, byte_length, media_type, admission_state, vault_key,
+                     created_at_ms, created_by, quarantine_reason)
+                 VALUES (?1, ?2,
+                         'application/vnd.heleos.evidence-manifest+json;version=1',
+                         'accepted', ?3, 0, 'fixture-actor', NULL)",
+                params![
+                    object.digest.to_string(),
+                    i64::try_from(object.byte_length).expect("object length"),
+                    object.vault_key,
+                ],
+            )
+            .expect("insert conflicting-media content authority");
+        assert!(matches!(
+            store.vault_inventory_rows(),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn vault_inventory_caps_count_before_rows_detects_drift_and_bounds_scalars() {
+        // Break caught: allocating hostile rows before the 100,000 cap or trusting count drift.
+        fn content_store(count: usize) -> Store {
+            let mut store = Store::open_in_memory().expect("open store");
+            store.migrate().expect("migrate store");
+            store
+                .connection
+                .execute(
+                    "WITH RECURSIVE sequence(value) AS (
+                         VALUES(1) UNION ALL SELECT value + 1 FROM sequence WHERE value < ?1
+                     ), digests(digest) AS (
+                         SELECT printf('%064x', value) FROM sequence
+                     )
+                     INSERT INTO content_objects
+                         (sha256, byte_length, media_type, admission_state, vault_key,
+                          created_at_ms, created_by, quarantine_reason)
+                     SELECT digest, 1, 'application/pdf', 'accepted',
+                            'objects/sha256/' || substr(digest, 1, 2) || '/' ||
+                            substr(digest, 3, 2) || '/' || digest,
+                            0, 'fixture-actor', NULL
+                     FROM digests",
+                    [i64::try_from(count).expect("count fits")],
+                )
+                .expect("insert bounded inventory rows");
+            store
+        }
+
+        let store = content_store(MAX_FOUNDATION_INSPECTION_ROWS);
+        store
+            .vault_inventory_rows()
+            .expect("exact 100,000 inventory objects are permitted");
+        let store = content_store(MAX_FOUNDATION_INSPECTION_ROWS + 1);
+        assert!(matches!(
+            store.vault_inventory_rows(),
+            Err(HeleosError::ResourceLimit)
+        ));
+
+        let store = content_store(2);
+        let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
+        let function_calls = std::sync::Arc::clone(&calls);
+        store
+            .connection
+            .create_scalar_function(
+                "task6_inventory_drift_visible",
+                1,
+                rusqlite::functions::FunctionFlags::SQLITE_UTF8
+                    | rusqlite::functions::FunctionFlags::SQLITE_INNOCUOUS,
+                move |context| {
+                    use std::sync::atomic::Ordering;
+
+                    let digest = context.get::<String>(0)?;
+                    let call = function_calls.fetch_add(1, Ordering::SeqCst);
+                    Ok(call < 2 || digest.ends_with('1'))
+                },
+            )
+            .expect("register inventory drift seam");
+        store
+            .connection
+            .execute_batch(
+                "ALTER TABLE content_objects RENAME TO content_base;
+                 CREATE VIEW content_objects AS
+                 SELECT sha256, byte_length, media_type, admission_state, vault_key,
+                        created_at_ms, created_by, quarantine_reason
+                 FROM content_base WHERE task6_inventory_drift_visible(sha256);",
+            )
+            .expect("install inventory drift view");
+        assert!(matches!(
+            store.vault_inventory_rows(),
+            Err(HeleosError::Integrity)
+        ));
+
+        for (label, digest, key) in [
+            (
+                "digest",
+                "1".repeat(65),
+                "objects/sha256/11/11/".to_owned() + &"1".repeat(64),
+            ),
+            ("key", "1".repeat(64), "k".repeat(257)),
+        ] {
+            let mut store = Store::open_in_memory().expect("open scalar store");
+            store.migrate().expect("migrate scalar store");
+            store
+                .connection
+                .execute_batch("ALTER TABLE content_objects RENAME TO content_base;")
+                .expect("rename scalar content table");
+            store
+                .connection
+                .execute_batch(&format!(
+                    "CREATE VIEW content_objects AS
+                     SELECT '{digest}' AS sha256, 1 AS byte_length,
+                            'application/pdf' AS media_type, 'accepted' AS admission_state,
+                            '{key}' AS vault_key, 0 AS created_at_ms,
+                            'fixture-actor' AS created_by, NULL AS quarantine_reason"
+                ))
+                .unwrap_or_else(|error| panic!("create {label} scalar view: {error}"));
+            assert!(matches!(
+                store.vault_inventory_rows(),
+                Err(HeleosError::Integrity)
+            ));
+        }
+    }
+
+    #[test]
+    fn latest_job_audit_lookup_uses_the_subject_sequence_index() {
+        // Break caught: a bounded live-job query still window-sorting all terminal history.
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let details = store
+            .connection
+            .prepare(&format!("EXPLAIN QUERY PLAN {LATEST_JOB_AUDIT_SQL}"))
+            .expect("prepare latest-audit query plan")
+            .query_map([uuid(120).hyphenated().to_string()], |row| {
+                row.get::<_, String>(3)
+            })
+            .expect("query latest-audit plan")
+            .collect::<rusqlite::Result<Vec<_>>>()
+            .expect("collect latest-audit plan");
+        assert!(
+            details.iter().any(|detail| {
+                detail.contains("audit_events_subject_sequence")
+                    && detail.contains("subject_type")
+                    && detail.contains("subject_id")
+            }),
+            "latest audit plan must use the bounded subject index: {details:?}"
+        );
+
+        let project_id = ProjectId::from_uuid(uuid(121));
+        insert_project(&store, project_id);
+        store
+            .with_immediate_transaction(|transaction| {
+                for ordinal in 0_u128..4_096 {
+                    append_action(
+                        transaction,
+                        AppendAction {
+                            id: AuditEventId::from_uuid(uuid(10_000 + ordinal)),
+                            project_id,
+                            actor: ActorId::from_str("fixture-actor")?,
+                            action: AuditAction::JobFailed,
+                            subject_type: AuditSubjectType::Job,
+                            subject_id: JobId::from_uuid(uuid(122)).as_uuid().to_string(),
+                            before: JsonValue::Null,
+                            after: JsonValue::Null,
+                            reason: "terminal history fixture",
+                            occurred_at_ms: i64::try_from(ordinal)
+                                .map_err(|_| HeleosError::Integrity)?,
+                        },
+                    )?;
+                }
+                Ok(())
+            })
+            .expect("append bounded terminal audit history");
+        let live = stored_object(123, 7);
+        queue_inventory_job(
+            &mut store,
+            project_id,
+            JobId::from_uuid(uuid(124)),
+            input_for_object(project_id, "inventory-after-history", &live),
+            IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::VaultPublished {
+                    original: live.clone(),
+                },
+            },
+            5_000,
+            AuditEventId::from_uuid(uuid(20_000)),
+        );
+        assert_eq!(
+            store
+                .vault_inventory_rows()
+                .expect("read inventory without scanning terminal history"),
+            VaultInventory::try_from_entries([inventory_entry(&live)])
+                .expect("expected live inventory")
+        );
+    }
+
+    #[test]
+    fn terminal_audit_snapshot_selects_exactly_one_lineage_for_the_job_project() {
+        // Break caught: a same-project duplicate legitimately reuses the first job's lineage.
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let project_id = ProjectId::from_uuid(uuid(10));
+        insert_project(&store, project_id);
+        let current_job = JobId::from_uuid(uuid(11));
+        let first_job = JobId::from_uuid(uuid(12));
+        let evidence_id = EvidenceId::from_uuid(uuid(13));
+        let input = valid_input(project_id, "reused-lineage");
+        let budget = IngestBudgetV1::new(input.byte_length);
+        let checkpoint = IngestCheckpointV1 {
+            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+            phase: IngestCheckpointPhaseV1::Terminal {
+                receipt: accepted_receipt(
+                    current_job,
+                    vec![EvidenceManifestLineage {
+                        project_id,
+                        evidence_id,
+                        originating_job_id: first_job,
+                    }],
+                ),
+            },
+        };
+        insert_job(
+            &store,
+            project_id,
+            current_job,
+            "reused-lineage",
+            &input,
+            &budget,
+            &checkpoint,
+        );
+        let snapshot = store
+            .with_immediate_transaction(|transaction| job_audit_snapshot(transaction, current_job))
+            .expect("build terminal snapshot");
+        assert_eq!(
+            snapshot["terminal_result_ids"]["evidence_id"],
+            json!(evidence_id)
+        );
+
+        for (suffix, lineages) in [
+            (
+                "missing",
+                vec![EvidenceManifestLineage {
+                    project_id: ProjectId::from_uuid(uuid(14)),
+                    evidence_id: EvidenceId::from_uuid(uuid(15)),
+                    originating_job_id: first_job,
+                }],
+            ),
+            (
+                "ambiguous",
+                vec![
+                    EvidenceManifestLineage {
+                        project_id,
+                        evidence_id: EvidenceId::from_uuid(uuid(16)),
+                        originating_job_id: first_job,
+                    },
+                    EvidenceManifestLineage {
+                        project_id,
+                        evidence_id: EvidenceId::from_uuid(uuid(17)),
+                        originating_job_id: JobId::from_uuid(uuid(18)),
+                    },
+                ],
+            ),
+        ] {
+            let job_id = JobId::from_uuid(uuid(if suffix == "missing" { 19 } else { 21 }));
+            let key = format!("lineage-{suffix}");
+            let input = valid_input(project_id, &key);
+            let checkpoint = IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::Terminal {
+                    receipt: accepted_receipt(job_id, lineages),
+                },
+            };
+            insert_job(
+                &store,
+                project_id,
+                job_id,
+                &key,
+                &input,
+                &IngestBudgetV1::new(input.byte_length),
+                &checkpoint,
+            );
+            assert!(matches!(
+                store.with_immediate_transaction(|transaction| {
+                    job_audit_snapshot(transaction, job_id)
+                }),
+                Err(HeleosError::Integrity)
+            ));
+        }
+    }
+
+    #[test]
+    fn checked_sum_reports_integrity_for_authoritative_total_overflow() {
+        // Break caught: SQLite's SUM overflow escaping as a generic database failure.
+        let connection = Connection::open_in_memory().expect("open sum fixture");
+        connection
+            .execute_batch(
+                "CREATE TABLE quota_values (value INTEGER NOT NULL);
+                 INSERT INTO quota_values VALUES (9223372036854775807);
+                 INSERT INTO quota_values VALUES (9223372036854775807);
+                 INSERT INTO quota_values VALUES (9223372036854775807);",
+            )
+            .expect("insert overflowing values");
+        assert!(matches!(
+            checked_sum(
+                &connection,
+                "SELECT value FROM quota_values ORDER BY rowid",
+                [],
+            ),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn audit_head_validates_borrowed_text_type_and_exact_digest_length() {
+        // Break caught: SQLite allocating an unbounded owned event-hash string before validation.
+        fn read_head(value: impl rusqlite::ToSql) -> Result<Sha256Digest> {
+            let mut connection = Connection::open_in_memory().expect("open audit-head fixture");
+            connection
+                .execute_batch(
+                    "CREATE TABLE audit_events (
+                        id TEXT NOT NULL,
+                        sequence INTEGER NOT NULL,
+                        event_hash
+                    );",
+                )
+                .expect("create audit-head table");
+            connection
+                .execute(
+                    "INSERT INTO audit_events (id, sequence, event_hash) VALUES ('event', 1, ?1)",
+                    [&value],
+                )
+                .expect("insert audit head");
+            let transaction = connection.transaction().expect("begin audit-head read");
+            audit_head(&transaction)
+        }
+
+        let exact = "1".repeat(64);
+        assert_eq!(
+            read_head(exact.clone()).expect("read exact text digest"),
+            Sha256Digest::from_str(&exact).expect("parse fixture digest")
+        );
+        assert!(matches!(
+            read_head("1".repeat(65)),
+            Err(HeleosError::Integrity)
+        ));
+        assert!(matches!(
+            read_head(vec![b'1'; 64]),
+            Err(HeleosError::Integrity)
+        ));
+    }
+}
diff --git a/crates/heleos-core/src/store/mod.rs b/crates/heleos-core/src/store/mod.rs
index 832995c..66e460b 100644
--- a/crates/heleos-core/src/store/mod.rs
+++ b/crates/heleos-core/src/store/mod.rs
@@ -1,10 +1,11 @@
+pub(crate) mod ingest_repository;
 mod migration;
 mod permissions;
 mod schema;
 
 use std::ffi::OsString;
 use std::fs::{self, File, OpenOptions};
 use std::io::{self, Read, Seek, SeekFrom, Write};
 use std::path::{Path, PathBuf};
 use std::time::Duration;
 
@@ -246,41 +247,67 @@ impl Store {
             database_identity_handle: None,
             source_sidecar_handles: Vec::new(),
             writer_lock: None,
             reader_lock: None,
             database_path: None,
             _snapshot_directory: None,
             read_only: false,
         })
     }
 
-    pub fn with_immediate_transaction<T>(
+    /// Runs one crate-owned immediate transaction.
+    ///
+    /// Normal consumers cannot use this primitive to bypass audited repositories.
+    ///
+    /// ```compile_fail
+    /// #![forbid(unsafe_code)]
+    /// use heleos_core::Store;
+    ///
+    /// fn raw_write(store: &mut Store) {
+    ///     let _ = store.with_immediate_transaction(|transaction| {
+    ///         transaction.execute("DELETE FROM projects", []).unwrap();
+    ///         Ok(())
+    ///     });
+    /// }
+    /// ```
+    pub(crate) fn with_immediate_transaction<T>(
         &mut self,
         operation: impl FnOnce(&Transaction<'_>) -> Result<T>,
     ) -> Result<T> {
         if self.read_only {
             return Err(HeleosError::PolicyDenied);
         }
         self.recheck_writer_lock_identity()?;
         self.recheck_database_identity()?;
         let transaction = self
             .connection
             .transaction_with_behavior(TransactionBehavior::Immediate)
             .map_err(database_error)?;
         let value = operation(&transaction)?;
-        transaction.commit().map_err(database_error)?;
-        self.recheck_writer_lock_identity()?;
-        self.harden_sqlite_sidecars()?;
-        self.recheck_database_identity()?;
+        transaction
+            .commit()
+            .map_err(|_| HeleosError::CommitOutcomeUnknown)?;
+        self.recheck_writer_lock_identity()
+            .and_then(|()| self.harden_sqlite_sidecars())
+            .and_then(|()| self.recheck_database_identity())
+            .map_err(|_| HeleosError::CommitOutcomeUnknown)?;
         Ok(value)
     }
 
+    pub(crate) fn require_writer_capability(&self) -> Result<()> {
+        if self.read_only {
+            return Err(HeleosError::PolicyDenied);
+        }
+        self.recheck_writer_lock_identity()?;
+        self.recheck_database_identity()
+    }
+
     pub fn verify_integrity(&self) -> Result<IntegrityReport> {
         self.recheck_database_identity()?;
         let (integrity_check_violations, integrity_check_truncated) =
             collect_single_column_check(&self.connection, INTEGRITY_CHECK_SQL)?;
         let (quick_check_violations, quick_check_truncated) =
             collect_single_column_check(&self.connection, QUICK_CHECK_SQL)?;
 
         let mut statement = self
             .connection
             .prepare("PRAGMA foreign_key_check")
@@ -727,20 +754,45 @@ fn register_jcs_validator(connection: &Connection) -> Result<()> {
         .create_scalar_function("heleos_is_jcs", 1, flags, |context| {
             let text = context.get::<String>(0)?;
             let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
                 return Ok(false);
             };
             let Ok(canonical) = serde_jcs::to_string(&value) else {
                 return Ok(false);
             };
             Ok(canonical == text)
         })
+        .map_err(database_error)?;
+    connection
+        .create_scalar_function("heleos_is_uuid", 1, flags, |context| {
+            let text = context.get::<String>(0)?;
+            let valid = uuid::Uuid::parse_str(&text)
+                .is_ok_and(|value| value.hyphenated().to_string() == text);
+            Ok(valid)
+        })
+        .map_err(database_error)?;
+    connection
+        .create_scalar_function("heleos_valid_text", 3, flags, |context| {
+            let text = context.get::<String>(0)?;
+            let max_bytes = context.get::<i64>(1)?;
+            let allow_ordinary_whitespace = context.get::<i64>(2)? != 0;
+            let valid_control = |character: char| {
+                allow_ordinary_whitespace && matches!(character, '\n' | '\r' | '\t')
+            };
+            let valid = max_bytes >= 0
+                && !text.is_empty()
+                && u64::try_from(text.len()).is_ok_and(|length| length <= max_bytes as u64)
+                && !text
+                    .chars()
+                    .any(|character| character.is_control() && !valid_control(character));
+            Ok(valid)
+        })
         .map_err(database_error)
 }
 
 fn collect_single_column_check(
     connection: &Connection,
     sql: &'static str,
 ) -> Result<(Vec<String>, bool)> {
     let mut statement = connection.prepare(sql).map_err(database_error)?;
     let rows = statement
         .query_map([], |row| row.get::<_, String>(0))
@@ -1057,20 +1109,114 @@ fn lock_is_busy(error: &io::Error) -> bool {
 }
 
 fn database_error(_: rusqlite::Error) -> HeleosError {
     HeleosError::Database
 }
 
 #[cfg(test)]
 mod tests {
     use super::*;
 
+    #[test]
+    fn crate_owned_connections_enforce_foundation_settings_and_raw_boundary() {
+        let root = tempfile::Builder::new()
+            .prefix("heleos-connection-settings-")
+            .tempdir()
+            .expect("create settings fixture directory");
+        apply_private_permissions(root.path()).expect("harden settings fixture directory");
+        let canonical_root = fs::canonicalize(root.path()).expect("canonical settings fixture");
+        let database = canonical_root.join("foundation.sqlite3");
+        let mut store = Store::open_writer(&database).expect("open settings writer");
+
+        let settings = (
+            store
+                .connection
+                .pragma_query_value(None, "foreign_keys", |row| row.get::<_, i64>(0))
+                .expect("query foreign keys"),
+            store
+                .connection
+                .pragma_query_value(None, "journal_mode", |row| row.get::<_, String>(0))
+                .expect("query journal mode"),
+            store
+                .connection
+                .pragma_query_value(None, "synchronous", |row| row.get::<_, i64>(0))
+                .expect("query synchronous"),
+            store
+                .connection
+                .pragma_query_value(None, "busy_timeout", |row| row.get::<_, i64>(0))
+                .expect("query busy timeout"),
+            store
+                .connection
+                .pragma_query_value(None, "query_only", |row| row.get::<_, i64>(0))
+                .expect("query writer query-only"),
+            store
+                .connection
+                .pragma_query_value(None, "temp_store", |row| row.get::<_, i64>(0))
+                .expect("query temp store"),
+            store
+                .connection
+                .db_config(DbConfig::SQLITE_DBCONFIG_TRUSTED_SCHEMA)
+                .expect("query trusted schema"),
+            store
+                .connection
+                .db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE)
+                .expect("query defensive mode"),
+            store
+                .connection
+                .db_config(DbConfig::SQLITE_DBCONFIG_DQS_DDL)
+                .expect("query DQS DDL"),
+            store
+                .connection
+                .db_config(DbConfig::SQLITE_DBCONFIG_DQS_DML)
+                .expect("query DQS DML"),
+            store
+                .connection
+                .db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_CREATE)
+                .expect("query attach-create"),
+            store
+                .connection
+                .db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_WRITE)
+                .expect("query attach-write"),
+        );
+        assert_eq!(
+            settings,
+            (
+                1,
+                "wal".to_owned(),
+                2,
+                5_000,
+                0,
+                2,
+                false,
+                true,
+                false,
+                false,
+                false,
+                false
+            )
+        );
+
+        store.read_only = true;
+        assert!(matches!(
+            store.with_immediate_transaction(|_| Ok(())),
+            Err(HeleosError::PolicyDenied)
+        ));
+
+        let memory = Store::open_in_memory().expect("open in-memory settings store");
+        assert!(
+            memory
+                .connection
+                .db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE)
+                .expect("query in-memory defensive mode")
+        );
+    }
+
     #[test]
     fn simultaneous_first_use_process_helper() {
         let Some(database) = std::env::var_os("HELEOS_TEST_FIRST_CREATE_DATABASE") else {
             return;
         };
         let barrier = PathBuf::from(
             std::env::var_os("HELEOS_TEST_FIRST_CREATE_BARRIER")
                 .expect("first-create barrier directory is configured"),
         );
         let mut outcome_name = PathBuf::from(
diff --git a/crates/heleos-core/tests/ingest.rs b/crates/heleos-core/tests/ingest.rs
new file mode 100644
index 0000000..4e13f12
--- /dev/null
+++ b/crates/heleos-core/tests/ingest.rs
@@ -0,0 +1,1549 @@
+#![forbid(unsafe_code)]
+
+use std::{
+    fs,
+    str::FromStr,
+    sync::atomic::{AtomicI64, Ordering},
+};
+#[cfg(unix)]
+use std::{
+    io::{Seek, SeekFrom, Write},
+    sync::{Mutex, atomic::AtomicBool},
+};
+
+use heleos_core::{
+    ActorId, AuditChainFinding, AuditChainReport, AuditEvent, AuditEventId, Clock, DataClass,
+    FaultInjector, FaultPoint, FoundationInspection, FoundationReader, HeleosError, IdGenerator,
+    IdempotencyKey, IngestEngine, IngestOutcome, IngestRequest, IntakeQuarantineReasonV1,
+    IntakeSource, PageMetadata, PageTransform, PageUnit, PdfInspection, PdfLimits, PdfProbe,
+    PdfProbeOutcome, PdfProbeProvenance, PdfQuarantine, PdfQuarantineReason, ProjectCreateRequest,
+    ProjectId, ProjectReceipt, ProjectService, Result, RevisionId, Sha256Digest, Store, Vault,
+    VaultConfig, VaultOpenMode, VerifiedObject, canonical_document_ids, canonical_json, page_id,
+};
+use tempfile::TempDir;
+use uuid::Uuid;
+
+const NOW_MS: i64 = 1_700_000_000_000;
+
+struct FixedClock;
+
+impl Clock for FixedClock {
+    fn now_unix_ms(&self) -> i64 {
+        NOW_MS
+    }
+}
+
+struct ManualClock(AtomicI64);
+
+impl ManualClock {
+    fn new(now_ms: i64) -> Self {
+        Self(AtomicI64::new(now_ms))
+    }
+
+    fn set(&self, now_ms: i64) {
+        self.0.store(now_ms, Ordering::SeqCst);
+    }
+}
+
+impl Clock for ManualClock {
+    fn now_unix_ms(&self) -> i64 {
+        self.0.load(Ordering::SeqCst)
+    }
+}
+
+struct FailAt(FaultPoint);
+
+impl FaultInjector for FailAt {
+    fn inject(&self, point: FaultPoint) -> Result<()> {
+        if point == self.0 {
+            Err(HeleosError::FaultInjected)
+        } else {
+            Ok(())
+        }
+    }
+}
+
+#[cfg(unix)]
+struct MutatingClock {
+    file: Mutex<fs::File>,
+    replacement: Vec<u8>,
+    fired: AtomicBool,
+}
+
+#[cfg(unix)]
+impl MutatingClock {
+    fn new(file: fs::File, replacement: &[u8]) -> Self {
+        Self {
+            file: Mutex::new(file),
+            replacement: replacement.to_vec(),
+            fired: AtomicBool::new(false),
+        }
+    }
+}
+
+#[cfg(unix)]
+impl Clock for MutatingClock {
+    fn now_unix_ms(&self) -> i64 {
+        if !self.fired.swap(true, Ordering::SeqCst) {
+            let mut file = self.file.lock().expect("lock source mutator");
+            file.seek(SeekFrom::Start(0))
+                .expect("rewind source mutator");
+            file.write_all(&self.replacement)
+                .expect("replace retained source bytes");
+            file.sync_all().expect("sync replaced source bytes");
+        }
+        NOW_MS
+    }
+}
+
+struct SequenceIds {
+    values: std::sync::Mutex<std::vec::IntoIter<Uuid>>,
+}
+
+#[derive(Clone)]
+struct AcceptedProbe {
+    provenance: PdfProbeProvenance,
+}
+
+#[derive(Clone)]
+struct QuarantinedProbe {
+    provenance: PdfProbeProvenance,
+    reason: PdfQuarantineReason,
+}
+
+#[derive(Clone)]
+struct FailingProbe {
+    provenance: PdfProbeProvenance,
+}
+
+impl PdfProbe for FailingProbe {
+    fn provenance(&self) -> Result<PdfProbeProvenance> {
+        Ok(self.provenance.clone())
+    }
+
+    fn probe(&self, _: VerifiedObject, _: RevisionId, _: PdfLimits) -> Result<PdfProbeOutcome> {
+        Err(HeleosError::Integrity)
+    }
+}
+
+impl PdfProbe for QuarantinedProbe {
+    fn provenance(&self) -> Result<PdfProbeProvenance> {
+        Ok(self.provenance.clone())
+    }
+
+    fn probe(&self, input: VerifiedObject, _: RevisionId, _: PdfLimits) -> Result<PdfProbeOutcome> {
+        Ok(PdfProbeOutcome::Quarantined(PdfQuarantine {
+            content_sha256: input.digest(),
+            byte_length: input.byte_length(),
+            provenance: self.provenance.clone(),
+            reason: self.reason,
+        }))
+    }
+}
+
+impl PdfProbe for AcceptedProbe {
+    fn provenance(&self) -> Result<PdfProbeProvenance> {
+        Ok(self.provenance.clone())
+    }
+
+    fn probe(
+        &self,
+        input: VerifiedObject,
+        revision: RevisionId,
+        _: PdfLimits,
+    ) -> Result<PdfProbeOutcome> {
+        let digest = input.digest();
+        Ok(PdfProbeOutcome::Accepted(PdfInspection {
+            revision_id: revision,
+            content_sha256: digest,
+            byte_length: input.byte_length(),
+            provenance: self.provenance.clone(),
+            pages: vec![PageMetadata {
+                index: 0,
+                page_id: page_id(digest, 0),
+                width_micropoints: 612_000_000,
+                height_micropoints: 792_000_000,
+                unit: PageUnit::Point,
+                rotation_degrees: 0,
+                transform: PageTransform {
+                    m11: 1,
+                    m12: 0,
+                    m21: 0,
+                    m22: -1,
+                    tx_micropoints: 0,
+                    ty_micropoints: 792_000_000,
+                },
+            }],
+        }))
+    }
+}
+
+fn accepted_probe() -> AcceptedProbe {
+    AcceptedProbe {
+        provenance: PdfProbeProvenance {
+            parser_name: "fixture-parser".to_owned(),
+            parser_version: "1.0.0".to_owned(),
+            guest_wasm_sha256: Sha256Digest::from_bytes([1; 32]),
+            guest_source_tree_sha256: Sha256Digest::from_bytes([2; 32]),
+            guest_dependency_graph_sha256: Sha256Digest::from_bytes([3; 32]),
+            protocol_version: "heleos.pdf-probe/v1".to_owned(),
+        },
+    }
+}
+
+impl SequenceIds {
+    fn new(values: impl IntoIterator<Item = Uuid>) -> Self {
+        Self {
+            values: std::sync::Mutex::new(values.into_iter().collect::<Vec<_>>().into_iter()),
+        }
+    }
+}
+
+impl IdGenerator for SequenceIds {
+    fn next_uuid(&self) -> Uuid {
+        self.values
+            .lock()
+            .expect("test ID source lock")
+            .next()
+            .expect("test ID source exhausted")
+    }
+}
+
+fn opened_vault(root: &TempDir) -> Vault {
+    heleos_core::apply_private_permissions(root.path()).expect("harden vault parent");
+    let parent = fs::canonicalize(root.path()).expect("canonicalize vault parent");
+    Vault::open(VaultConfig {
+        root: parent.join("vault"),
+        open_mode: VaultOpenMode::CreateNew,
+    })
+    .expect("open test vault")
+}
+
+fn reopened_vault(root: &TempDir) -> Vault {
+    let parent = fs::canonicalize(root.path()).expect("canonicalize vault parent");
+    Vault::open(VaultConfig {
+        root: parent.join("vault"),
+        open_mode: VaultOpenMode::CreateOrOpen,
+    })
+    .expect("reopen test vault")
+}
+
+fn accepted_reader_fixture() -> (
+    Store,
+    Vault,
+    TempDir,
+    RevisionId,
+    Sha256Digest,
+    Sha256Digest,
+) {
+    let mut store = Store::open_in_memory().expect("open store");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let project_id = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+    );
+    let ids = SequenceIds::new(
+        (2_u128..=40).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+    );
+    ProjectService::new(&mut store, &FixedClock, &ids)
+        .expect("construct project service")
+        .create(ProjectCreateRequest {
+            project_id: Some(project_id),
+            name: "Demo".to_owned(),
+            actor: ActorId::from_str("tester").expect("actor"),
+            data_class: DataClass::Internal,
+        })
+        .expect("create project");
+    let source_root = TempDir::new().expect("create source directory");
+    let source_path = source_root.path().join("fixture.pdf");
+    let source_bytes = b"%PDF-1.7\nfixture\n%%EOF\n";
+    fs::write(&source_path, source_bytes).expect("write source fixture");
+    let original = Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
+    let (_, revision) = canonical_document_ids(original);
+    let receipt = IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
+        .expect("construct intake engine")
+        .ingest(IngestRequest {
+            project_id,
+            source: IntakeSource::open(&source_path).expect("open retained intake source"),
+            idempotency_key: IdempotencyKey::try_from("fixture-key").expect("key"),
+            actor: ActorId::from_str("tester").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        })
+        .expect("ingest fixture");
+    let manifest = receipt
+        .evidence_manifest
+        .expect("accepted evidence receipt")
+        .manifest_content_sha256;
+    (store, vault, vault_root, revision, original, manifest)
+}
+
+#[test]
+fn audited_project_creation_matches_the_frozen_hash_vector() {
+    // Break caught: project creation that omits its same-transaction, domain-separated audit row.
+    let mut store = Store::open_in_memory().expect("open store");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let project_id = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+    );
+    let audit_id = AuditEventId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000002").expect("audit UUID"),
+    );
+    let ids = SequenceIds::new([*audit_id.as_uuid()]);
+    let request = ProjectCreateRequest {
+        project_id: Some(project_id),
+        name: "Demo".to_owned(),
+        actor: ActorId::from_str("tester").expect("actor"),
+        data_class: DataClass::Internal,
+    };
+
+    let receipt = ProjectService::new(&mut store, &FixedClock, &ids)
+        .expect("construct project service")
+        .create(request)
+        .expect("create audited project");
+
+    assert_eq!(receipt.project_id, project_id);
+    assert_eq!(receipt.created_at_ms, NOW_MS);
+    assert_eq!(receipt.audit_event_id, audit_id);
+    assert_eq!(
+        canonical_json(&receipt).expect("canonical receipt"),
+        br#"{"audit_event_id":"00000000-0000-4000-8000-000000000002","created_at_ms":1700000000000,"project_id":"00000000-0000-4000-8000-000000000001"}"#
+    );
+
+    let report = FoundationReader::new(&store, &vault)
+        .verify_audit_chain()
+        .expect("verify audit chain");
+    assert!(report.valid);
+    assert_eq!(report.checked_event_count, 1);
+    assert_eq!(report.first_sequence, Some(1));
+    assert_eq!(report.last_sequence, Some(1));
+    assert_eq!(
+        report.head_hash,
+        Sha256Digest::from_str("266987a9f2db93655d060542189a830bb76fbc5e9e88ea93add8fc5958614578")
+            .expect("frozen hash")
+    );
+    assert!(report.findings.is_empty());
+    assert!(!report.findings_truncated);
+}
+
+#[test]
+fn empty_foundation_inspection_has_the_frozen_bounded_image() {
+    // Break caught: inspection depending on insertion order or leaking unscoped foundation rows.
+    let mut store = Store::open_in_memory().expect("open store");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let project_id = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+    );
+    let audit_id = AuditEventId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000002").expect("audit UUID"),
+    );
+    let ids = SequenceIds::new([*audit_id.as_uuid()]);
+    ProjectService::new(&mut store, &FixedClock, &ids)
+        .expect("construct project service")
+        .create(ProjectCreateRequest {
+            project_id: Some(project_id),
+            name: "Demo".to_owned(),
+            actor: ActorId::from_str("tester").expect("actor"),
+            data_class: DataClass::Internal,
+        })
+        .expect("create project");
+
+    let inspection = FoundationReader::new(&store, &vault)
+        .inspect_foundation(project_id)
+        .expect("inspect empty project");
+    let bytes = canonical_json(&inspection).expect("canonical inspection");
+    assert_eq!(
+        bytes,
+        br#"{"content_objects":[],"counts":{"audit_events":1,"content_objects":0,"documents":0,"evidence_objects":0,"ingest_events":0,"jobs":0,"project_documents":0,"revisions":0,"sheets":0},"document_ids":[],"evidence":[],"intake_events":[],"jobs":[],"project_id":"00000000-0000-4000-8000-000000000001","revision_ids":[],"schema":"heleos.foundation-inspection/v1","sheets":[]}"#
+    );
+    let decoded = serde_json::from_slice::<FoundationInspection>(&bytes)
+        .expect("strict inspection round trip");
+    assert_eq!(decoded, inspection);
+    let missing_project = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000003").expect("missing project UUID"),
+    );
+    assert!(matches!(
+        FoundationReader::new(&store, &vault).inspect_foundation(missing_project),
+        Err(HeleosError::NotFound)
+    ));
+    assert!(matches!(
+        FoundationReader::new(&store, &vault)
+            .evidence_manifest_for_revision(RevisionId::from(Sha256Digest::from_bytes([9; 32]))),
+        Err(HeleosError::NotFound)
+    ));
+}
+
+#[test]
+fn idempotency_keys_reject_controls_and_utf8_byte_overflow() {
+    // Break caught: an unbounded/control-bearing key reaching SQL or canonical job input.
+    assert!(IdempotencyKey::try_from("stable-key").is_ok());
+    assert!(IdempotencyKey::try_from("").is_err());
+    assert!(IdempotencyKey::try_from("bad\nkey").is_err());
+    assert!(IdempotencyKey::try_from("x".repeat(128)).is_ok());
+    assert!(IdempotencyKey::try_from("x".repeat(129)).is_err());
+    assert!(IdempotencyKey::try_from("é".repeat(64)).is_ok());
+    assert!(IdempotencyKey::try_from(format!("{}x", "é".repeat(64))).is_err());
+}
+
+#[test]
+fn public_audit_and_project_dtos_reject_invalid_cross_field_states() {
+    // Break caught: unchecked serde constructing impossible authoritative/public DTO states.
+    let invalid_project_receipt = serde_json::json!({
+        "project_id": "00000000-0000-4000-8000-000000000001",
+        "created_at_ms": -1,
+        "audit_event_id": "00000000-0000-4000-8000-000000000002"
+    });
+    assert!(serde_json::from_value::<ProjectReceipt>(invalid_project_receipt).is_err());
+
+    let invalid_event = serde_json::json!({
+        "id": "00000000-0000-4000-8000-000000000002",
+        "sequence": 0,
+        "project_id": null,
+        "actor": "tester",
+        "action": "project_created",
+        "subject_type": "project",
+        "subject_id": "subject",
+        "before": null,
+        "after": null,
+        "reason": "reason",
+        "occurred_at_ms": 0,
+        "previous_hash": "0000000000000000000000000000000000000000000000000000000000000000",
+        "event_hash": "0000000000000000000000000000000000000000000000000000000000000000"
+    });
+    assert!(serde_json::from_value::<AuditEvent>(invalid_event).is_err());
+
+    let invalid_report = serde_json::json!({
+        "schema": "wrong",
+        "valid": true,
+        "checked_event_count": 0,
+        "first_sequence": null,
+        "last_sequence": null,
+        "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
+        "findings": [],
+        "findings_truncated": false
+    });
+    assert!(serde_json::from_value::<AuditChainReport>(invalid_report).is_err());
+
+    let finding_with_unknown_field = serde_json::json!({
+        "kind": "non_canonical_before",
+        "detail": {
+            "event_id": "00000000-0000-4000-8000-000000000002",
+            "sequence": 1,
+            "unknown": true
+        }
+    });
+    assert!(serde_json::from_value::<AuditChainFinding>(finding_with_unknown_field).is_err());
+    let finding_with_unknown_outer_field = serde_json::json!({
+        "kind": "non_canonical_before",
+        "detail": {
+            "event_id": "00000000-0000-4000-8000-000000000002",
+            "sequence": 1
+        },
+        "unknown": true
+    });
+    assert!(serde_json::from_value::<AuditChainFinding>(finding_with_unknown_outer_field).is_err());
+}
+
+#[test]
+fn missing_project_is_rejected_before_any_retained_source_work() {
+    // Break caught: source fingerprint I/O or mutation masking the frozen NotFound precedence.
+    let mut store = Store::open_in_memory().expect("open store");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let source_root = TempDir::new().expect("create source directory");
+    let source_path = source_root.path().join("mutated.pdf");
+    let oversized = fs::File::create(&source_path).expect("create hostile sparse source");
+    oversized
+        .set_len(PdfLimits::default().max_input_bytes + 1)
+        .expect("size hostile sparse source");
+    drop(oversized);
+    let missing_project = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+    );
+    let ids = SequenceIds::new(std::iter::empty::<Uuid>());
+    let mut nondefault_limits = PdfLimits::default();
+    nondefault_limits.max_pages -= 1;
+    let nondefault = IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
+        .expect("construct intake engine")
+        .ingest(IngestRequest {
+            project_id: missing_project,
+            source: IntakeSource::open(&source_path).expect("open retained intake source"),
+            idempotency_key: IdempotencyKey::try_from("missing-nondefault").expect("key"),
+            actor: ActorId::from_str("tester").expect("actor"),
+            pdf_limits: nondefault_limits,
+        });
+    assert!(matches!(nondefault, Err(HeleosError::NotFound)));
+
+    let result = IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
+        .expect("construct intake engine")
+        .ingest(IngestRequest {
+            project_id: missing_project,
+            source: IntakeSource::open(&source_path).expect("open retained intake source"),
+            idempotency_key: IdempotencyKey::try_from("missing-project").expect("key"),
+            actor: ActorId::from_str("tester").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        });
+
+    assert!(matches!(result, Err(HeleosError::NotFound)));
+    assert!(
+        FoundationReader::new(&store, &vault)
+            .verify_audit_chain()
+            .expect("verify unchanged audit chain")
+            .checked_event_count
+            == 0
+    );
+}
+
+#[test]
+fn completed_same_key_replays_and_conflicts_are_durable_without_a_second_job() {
+    // Break caught: returning replay/conflict errors before their immutable event and audit commit.
+    let mut store = Store::open_in_memory().expect("open store");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let project_id = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+    );
+    let ids = SequenceIds::new(
+        (2_u128..=80).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+    );
+    ProjectService::new(&mut store, &FixedClock, &ids)
+        .expect("construct project service")
+        .create(ProjectCreateRequest {
+            project_id: Some(project_id),
+            name: "Replay project".to_owned(),
+            actor: ActorId::from_str("creator").expect("actor"),
+            data_class: DataClass::Internal,
+        })
+        .expect("create project");
+    let source_root = TempDir::new().expect("create source directory");
+    let first_path = source_root.path().join("first.pdf");
+    let replay_path = source_root.path().join("renamed.pdf");
+    let conflict_path = source_root.path().join("different.pdf");
+    let bytes = b"%PDF-1.7\nreplay fixture\n%%EOF\n";
+    fs::write(&first_path, bytes).expect("write first fixture");
+    fs::write(&replay_path, bytes).expect("write replay fixture");
+    fs::write(&conflict_path, b"%PDF-1.7\nconflict fixture\n%%EOF\n")
+        .expect("write conflict fixture");
+    let probe = accepted_probe();
+    let key = IdempotencyKey::try_from("same-key").expect("key");
+    let first = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
+        .expect("construct intake engine")
+        .ingest(IngestRequest {
+            project_id,
+            source: IntakeSource::open(&first_path).expect("open first source"),
+            idempotency_key: key.clone(),
+            actor: ActorId::from_str("first-actor").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        })
+        .expect("first accepted ingest");
+
+    let replay = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
+        .expect("construct replay engine")
+        .ingest(IngestRequest {
+            project_id,
+            source: IntakeSource::open(&replay_path).expect("open replay source"),
+            idempotency_key: key.clone(),
+            actor: ActorId::from_str("second-actor").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        })
+        .expect("completed same-key replay");
+    assert_eq!(replay.outcome, IngestOutcome::IdempotentReplay);
+    assert_eq!(replay.authoritative_job_id, first.authoritative_job_id);
+    assert_eq!(replay.attempt, first.attempt);
+    assert_ne!(replay.ingest_event_id, first.ingest_event_id);
+    assert_eq!(replay.content_sha256, first.content_sha256);
+    assert_eq!(replay.evidence_manifest, first.evidence_manifest);
+    assert_eq!(replay.preexisting_vault_digests.len(), 2);
+
+    let conflict = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
+        .expect("construct conflict engine")
+        .ingest(IngestRequest {
+            project_id,
+            source: IntakeSource::open(&conflict_path).expect("open conflict source"),
+            idempotency_key: key,
+            actor: ActorId::from_str("third-actor").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        });
+    assert!(matches!(conflict, Err(HeleosError::IdempotencyConflict)));
+
+    let inspection = FoundationReader::new(&store, &vault)
+        .inspect_foundation(project_id)
+        .expect("inspect replay/conflict authority");
+    assert_eq!(inspection.counts.jobs, 1);
+    assert_eq!(inspection.counts.ingest_events, 3);
+    assert_eq!(inspection.counts.audit_events, 9);
+    assert_eq!(
+        inspection.intake_events[0].outcome,
+        IngestOutcome::AcceptedNew
+    );
+    assert_eq!(
+        inspection.intake_events[1].outcome,
+        IngestOutcome::IdempotentReplay
+    );
+    assert_eq!(
+        inspection.intake_events[2].outcome,
+        IngestOutcome::DeniedConflict
+    );
+}
+
+#[test]
+fn input_cap_and_pdf_quarantine_are_finite_succeeded_jobs_and_pdf_admission_is_sticky() {
+    // Break caught: handled limits escaping as hard errors or retained quarantine being re-probed.
+    let mut store = Store::open_in_memory().expect("open store");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let project_id = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+    );
+    let ids = SequenceIds::new(
+        (2_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+    );
+    ProjectService::new(&mut store, &FixedClock, &ids)
+        .expect("construct project service")
+        .create(ProjectCreateRequest {
+            project_id: Some(project_id),
+            name: "Quarantine project".to_owned(),
+            actor: ActorId::from_str("creator").expect("actor"),
+            data_class: DataClass::Internal,
+        })
+        .expect("create project");
+    let source_root = TempDir::new().expect("create source directory");
+    let oversized_path = source_root.path().join("oversized.pdf");
+    let oversized = fs::File::create(&oversized_path).expect("create sparse source");
+    oversized
+        .set_len(PdfLimits::default().max_input_bytes + 1)
+        .expect("size sparse source");
+    drop(oversized);
+    let probe = QuarantinedProbe {
+        provenance: accepted_probe().provenance,
+        reason: PdfQuarantineReason::BadMagic,
+    };
+    let capped = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
+        .expect("construct intake engine")
+        .ingest(IngestRequest {
+            project_id,
+            source: IntakeSource::open(&oversized_path).expect("open sparse source"),
+            idempotency_key: IdempotencyKey::try_from("input-cap").expect("key"),
+            actor: ActorId::from_str("tester").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        })
+        .expect("input cap is a handled result");
+    assert_eq!(capped.outcome, IngestOutcome::QuarantinedLimit);
+    assert_eq!(capped.content_sha256, None);
+    assert_eq!(capped.byte_length, PdfLimits::default().max_input_bytes + 1);
+    assert!(matches!(
+        capped.quarantine.as_ref().expect("input quarantine").reason,
+        IntakeQuarantineReasonV1::InputBytes { .. }
+    ));
+
+    let pdf_path = source_root.path().join("bad.pdf");
+    fs::write(&pdf_path, b"not a PDF").expect("write quarantined source");
+    let first = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
+        .expect("construct quarantine engine")
+        .ingest(IngestRequest {
+            project_id,
+            source: IntakeSource::open(&pdf_path).expect("open quarantined source"),
+            idempotency_key: IdempotencyKey::try_from("bad-first").expect("key"),
+            actor: ActorId::from_str("tester").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        })
+        .expect("PDF quarantine is a handled result");
+    assert_eq!(first.outcome, IngestOutcome::QuarantinedCorrupt);
+    assert!(matches!(
+        first.quarantine.as_ref().expect("PDF quarantine").reason,
+        IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic)
+    ));
+    assert_eq!(first.preexisting_vault_digests, Vec::new());
+
+    let sticky = IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
+        .expect("construct sticky engine")
+        .ingest(IngestRequest {
+            project_id,
+            source: IntakeSource::open(&pdf_path).expect("open sticky source"),
+            idempotency_key: IdempotencyKey::try_from("bad-second").expect("key"),
+            actor: ActorId::from_str("tester").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        })
+        .expect("sticky quarantine result");
+    assert_eq!(sticky.outcome, IngestOutcome::QuarantinedCorrupt);
+    assert_eq!(sticky.quarantine, first.quarantine);
+    assert_eq!(
+        sticky.preexisting_vault_digests,
+        vec![first.content_sha256.expect("retained digest")]
+    );
+
+    let inspection = FoundationReader::new(&store, &vault)
+        .inspect_foundation(project_id)
+        .expect("inspect quarantine jobs");
+    assert_eq!(inspection.counts.jobs, 3);
+    assert_eq!(inspection.counts.ingest_events, 3);
+    assert_eq!(inspection.counts.content_objects, 1);
+    assert_eq!(inspection.counts.documents, 0);
+    assert!(
+        inspection
+            .jobs
+            .iter()
+            .all(|job| job.state == heleos_core::JobState::Succeeded)
+    );
+}
+
+#[test]
+fn sticky_admission_requires_canonical_reason_and_exact_probe_identity_before_a_job() {
+    // Break caught: trusting noncanonical stored authority or discovering provenance drift after
+    // a new job already owns a live lease.
+    for accepted_first in [false, true] {
+        let database_root = TempDir::new().expect("create database parent");
+        heleos_core::apply_private_permissions(database_root.path())
+            .expect("harden database parent");
+        let database_path = fs::canonicalize(database_root.path())
+            .expect("canonicalize database parent")
+            .join("foundation.sqlite3");
+        let mut store = Store::open_writer(&database_path).expect("open writer");
+        store.migrate().expect("migrate store");
+        let vault_root = TempDir::new().expect("create vault parent");
+        let vault = opened_vault(&vault_root);
+        let project_id = ProjectId::from_uuid(
+            Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+        );
+        let ids = SequenceIds::new(
+            (2_u128..=120).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+        );
+        ProjectService::new(&mut store, &FixedClock, &ids)
+            .expect("construct project service")
+            .create(ProjectCreateRequest {
+                project_id: Some(project_id),
+                name: "Sticky integrity".to_owned(),
+                actor: ActorId::from_str("creator").expect("actor"),
+                data_class: DataClass::Internal,
+            })
+            .expect("create project");
+        let source_root = TempDir::new().expect("create source directory");
+        let source_path = source_root.path().join("sticky.pdf");
+        fs::write(&source_path, b"%PDF-1.7\nsticky authority\n%%EOF\n").expect("write source");
+        let initial_probe = accepted_probe();
+        if accepted_first {
+            IngestEngine::new(&mut store, &vault, &initial_probe, &FixedClock, &ids)
+                .expect("construct accepted engine")
+                .ingest(IngestRequest {
+                    project_id,
+                    source: IntakeSource::open(&source_path).expect("open source"),
+                    idempotency_key: IdempotencyKey::try_from("first-accepted").expect("key"),
+                    actor: ActorId::from_str("tester").expect("actor"),
+                    pdf_limits: PdfLimits::default(),
+                })
+                .expect("commit accepted authority");
+        } else {
+            let quarantine_probe = QuarantinedProbe {
+                provenance: initial_probe.provenance.clone(),
+                reason: PdfQuarantineReason::BadMagic,
+            };
+            IngestEngine::new(&mut store, &vault, &quarantine_probe, &FixedClock, &ids)
+                .expect("construct quarantine engine")
+                .ingest(IngestRequest {
+                    project_id,
+                    source: IntakeSource::open(&source_path).expect("open source"),
+                    idempotency_key: IdempotencyKey::try_from("first-quarantine").expect("key"),
+                    actor: ActorId::from_str("tester").expect("actor"),
+                    pdf_limits: PdfLimits::default(),
+                })
+                .expect("commit quarantine authority");
+        }
+
+        let mut changed_probe = accepted_probe();
+        changed_probe.provenance.parser_version = "1.0.1".to_owned();
+        let drift = IngestEngine::new(&mut store, &vault, &changed_probe, &FixedClock, &ids)
+            .expect("construct drift engine")
+            .ingest(IngestRequest {
+                project_id,
+                source: IntakeSource::open(&source_path).expect("open drift source"),
+                idempotency_key: IdempotencyKey::try_from("drift").expect("key"),
+                actor: ActorId::from_str("tester").expect("actor"),
+                pdf_limits: PdfLimits::default(),
+            });
+        assert!(matches!(drift, Err(HeleosError::Integrity)));
+        assert_eq!(
+            FoundationReader::new(&store, &vault)
+                .inspect_foundation(project_id)
+                .expect("inspect after provenance rejection")
+                .counts
+                .jobs,
+            1
+        );
+
+        if !accepted_first {
+            drop(store);
+            let connection = rusqlite::Connection::open(&database_path)
+                .expect("open hostile quarantine mutator");
+            connection
+                .execute_batch(
+                    "PRAGMA ignore_check_constraints = ON;
+                     DROP TRIGGER content_objects_no_update;",
+                )
+                .expect("authorize hostile quarantine mutation");
+            let canonical: String = connection
+                .query_row("SELECT quarantine_reason FROM content_objects", [], |row| {
+                    row.get(0)
+                })
+                .expect("read canonical quarantine");
+            connection
+                .execute(
+                    "UPDATE content_objects SET quarantine_reason = ?1",
+                    [format!(" {canonical}")],
+                )
+                .expect("store noncanonical quarantine bytes");
+            drop(connection);
+            let mut reopened = Store::open_writer(&database_path).expect("reopen writer");
+            let noncanonical =
+                IngestEngine::new(&mut reopened, &vault, &initial_probe, &FixedClock, &ids)
+                    .expect("construct noncanonical authority engine")
+                    .ingest(IngestRequest {
+                        project_id,
+                        source: IntakeSource::open(&source_path).expect("open source"),
+                        idempotency_key: IdempotencyKey::try_from("noncanonical").expect("key"),
+                        actor: ActorId::from_str("tester").expect("actor"),
+                        pdf_limits: PdfLimits::default(),
+                    });
+            assert!(matches!(noncanonical, Err(HeleosError::Integrity)));
+        }
+    }
+}
+
+#[test]
+#[cfg(unix)]
+fn no_publish_replay_rehashes_the_same_retained_source_before_authority() {
+    // Break caught: replay/conflict and sticky branches relying on a stale first-pass digest.
+    let mut store = Store::open_in_memory().expect("open store");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let project_id = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+    );
+    let ids = SequenceIds::new(
+        (2_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+    );
+    ProjectService::new(&mut store, &FixedClock, &ids)
+        .expect("construct project service")
+        .create(ProjectCreateRequest {
+            project_id: Some(project_id),
+            name: "Mutation project".to_owned(),
+            actor: ActorId::from_str("creator").expect("actor"),
+            data_class: DataClass::Internal,
+        })
+        .expect("create project");
+    let source_root = TempDir::new().expect("create source directory");
+    let source_path = source_root.path().join("same.pdf");
+    let original = b"%PDF-1.7\nsource alpha\n%%EOF\n";
+    let replacement = b"%PDF-1.7\nsource omega\n%%EOF\n";
+    assert_eq!(original.len(), replacement.len());
+    fs::write(&source_path, original).expect("write source");
+    let probe = accepted_probe();
+    let key = IdempotencyKey::try_from("mutating-replay").expect("key");
+    IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
+        .expect("construct first engine")
+        .ingest(IngestRequest {
+            project_id,
+            source: IntakeSource::open(&source_path).expect("open first source"),
+            idempotency_key: key.clone(),
+            actor: ActorId::from_str("tester").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        })
+        .expect("commit first authority");
+
+    let writer = fs::OpenOptions::new()
+        .read(true)
+        .write(true)
+        .open(&source_path)
+        .expect("open retained source mutator");
+    let source = IntakeSource::open(&source_path).expect("open retained replay source");
+    let clock = MutatingClock::new(writer, replacement);
+    let replay = IngestEngine::new(&mut store, &vault, &probe, &clock, &ids)
+        .expect("construct replay engine")
+        .ingest(IngestRequest {
+            project_id,
+            source,
+            idempotency_key: key,
+            actor: ActorId::from_str("tester").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        });
+    assert!(matches!(replay, Err(HeleosError::Integrity)));
+    let inspection = FoundationReader::new(&store, &vault)
+        .inspect_foundation(project_id)
+        .expect("inspect unchanged authority");
+    assert_eq!(inspection.counts.jobs, 1);
+    assert_eq!(inspection.counts.ingest_events, 1);
+}
+
+#[test]
+fn every_in_process_fault_boundary_resumes_without_duplicate_authority() {
+    // Break caught: process-loss boundaries either becoming Failed or duplicating authority.
+    for point in [
+        FaultPoint::AfterVaultPublish,
+        FaultPoint::AfterJobStart,
+        FaultPoint::AfterCheckpointCommit,
+        FaultPoint::BeforeAuthoritativeCommit,
+        FaultPoint::AfterAuthoritativeCommit,
+    ] {
+        let database_root = TempDir::new().expect("create database parent");
+        heleos_core::apply_private_permissions(database_root.path())
+            .expect("harden database parent");
+        let database_path = fs::canonicalize(database_root.path())
+            .expect("canonicalize database parent")
+            .join("foundation.sqlite3");
+        let mut store = Store::open_writer(&database_path).expect("open store");
+        store.migrate().expect("migrate store");
+        let vault_root = TempDir::new().expect("create vault parent");
+        let vault = opened_vault(&vault_root);
+        let project_id = ProjectId::from_uuid(
+            Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+        );
+        let ids = SequenceIds::new(
+            (2_u128..=200).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+        );
+        let clock = ManualClock::new(NOW_MS);
+        ProjectService::new(&mut store, &clock, &ids)
+            .expect("construct project service")
+            .create(ProjectCreateRequest {
+                project_id: Some(project_id),
+                name: "Fault matrix".to_owned(),
+                actor: ActorId::from_str("creator").expect("actor"),
+                data_class: DataClass::Internal,
+            })
+            .expect("create project");
+        let source_root = TempDir::new().expect("create source directory");
+        let source_path = source_root.path().join("fault.pdf");
+        fs::write(&source_path, b"%PDF-1.7\nfault fixture\n%%EOF\n").expect("write source");
+        let probe = accepted_probe();
+        let fault = FailAt(point);
+        let interrupted =
+            IngestEngine::with_fault_injector(&mut store, &vault, &probe, &clock, &ids, &fault)
+                .expect("construct fault engine")
+                .ingest(IngestRequest {
+                    project_id,
+                    source: IntakeSource::open(&source_path).expect("open retained source"),
+                    idempotency_key: IdempotencyKey::try_from("fault-key").expect("key"),
+                    actor: ActorId::from_str("tester").expect("actor"),
+                    pdf_limits: PdfLimits::default(),
+                });
+        assert!(matches!(interrupted, Err(HeleosError::FaultInjected)));
+        drop(store);
+        drop(vault);
+        let mut store = Store::open_writer(&database_path).expect("reopen store after fault");
+        let vault = reopened_vault(&vault_root);
+        let before = FoundationReader::new(&store, &vault)
+            .inspect_foundation(project_id)
+            .expect("inspect fault checkpoint");
+        assert_eq!(before.jobs.len(), 1);
+        let job = before.jobs[0].clone();
+        if matches!(
+            point,
+            FaultPoint::AfterJobStart
+                | FaultPoint::AfterCheckpointCommit
+                | FaultPoint::BeforeAuthoritativeCommit
+        ) {
+            clock.set(NOW_MS + 30_000);
+        }
+        let resumed = IngestEngine::new(&mut store, &vault, &probe, &clock, &ids)
+            .expect("construct resume engine")
+            .resume(job.job_id, ActorId::from_str("resumer").expect("actor"))
+            .expect("resume durable checkpoint");
+        match point {
+            FaultPoint::AfterVaultPublish => {
+                assert_eq!(resumed.resumed_attempt, Some(1));
+                assert_eq!(resumed.interrupted_event_id, None);
+            }
+            FaultPoint::AfterJobStart
+            | FaultPoint::AfterCheckpointCommit
+            | FaultPoint::BeforeAuthoritativeCommit => {
+                assert_eq!(resumed.resumed_attempt, Some(2));
+                assert!(resumed.interrupted_event_id.is_some());
+            }
+            FaultPoint::AfterAuthoritativeCommit => {
+                assert_eq!(resumed.resumed_attempt, None);
+                assert_eq!(resumed.interrupted_event_id, None);
+            }
+        }
+        assert!(matches!(
+            resumed.receipt.outcome,
+            IngestOutcome::AcceptedNew | IngestOutcome::AcceptedDuplicate
+        ));
+        let after = FoundationReader::new(&store, &vault)
+            .inspect_foundation(project_id)
+            .expect("inspect resumed authority");
+        assert_eq!(after.counts.jobs, 1);
+        assert_eq!(after.counts.documents, 1);
+        assert_eq!(after.counts.revisions, 1);
+        assert_eq!(after.counts.evidence_objects, 1);
+        assert_eq!(after.counts.content_objects, 2);
+    }
+}
+
+#[test]
+fn sticky_fault_checkpoints_resume_without_reprobing_admitted_content() {
+    // Break caught: a sticky job checkpointed as ambiguous VaultPublished and reprobed on resume.
+    for accepted in [true, false] {
+        for point in [FaultPoint::AfterVaultPublish, FaultPoint::AfterJobStart] {
+            let mut store = Store::open_in_memory().expect("open store");
+            store.migrate().expect("migrate store");
+            let vault_root = TempDir::new().expect("create vault parent");
+            let vault = opened_vault(&vault_root);
+            let project_id = ProjectId::from_uuid(
+                Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+            );
+            let ids = SequenceIds::new(
+                (2_u128..=400)
+                    .map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+            );
+            let clock = ManualClock::new(NOW_MS);
+            ProjectService::new(&mut store, &clock, &ids)
+                .expect("construct project service")
+                .create(ProjectCreateRequest {
+                    project_id: Some(project_id),
+                    name: "Sticky recovery".to_owned(),
+                    actor: ActorId::from_str("creator").expect("actor"),
+                    data_class: DataClass::Internal,
+                })
+                .expect("create project");
+            let source_root = TempDir::new().expect("create source directory");
+            let source_path = source_root.path().join("sticky.pdf");
+            fs::write(&source_path, b"%PDF-1.7\nsticky recovery\n%%EOF\n").expect("write source");
+            let provenance = accepted_probe().provenance;
+            let first = if accepted {
+                IngestEngine::new(
+                    &mut store,
+                    &vault,
+                    &AcceptedProbe {
+                        provenance: provenance.clone(),
+                    },
+                    &clock,
+                    &ids,
+                )
+                .expect("construct accepted engine")
+                .ingest(IngestRequest {
+                    project_id,
+                    source: IntakeSource::open(&source_path).expect("open source"),
+                    idempotency_key: IdempotencyKey::try_from("first-authority").expect("key"),
+                    actor: ActorId::from_str("tester").expect("actor"),
+                    pdf_limits: PdfLimits::default(),
+                })
+            } else {
+                IngestEngine::new(
+                    &mut store,
+                    &vault,
+                    &QuarantinedProbe {
+                        provenance: provenance.clone(),
+                        reason: PdfQuarantineReason::Corrupt,
+                    },
+                    &clock,
+                    &ids,
+                )
+                .expect("construct quarantine engine")
+                .ingest(IngestRequest {
+                    project_id,
+                    source: IntakeSource::open(&source_path).expect("open source"),
+                    idempotency_key: IdempotencyKey::try_from("first-authority").expect("key"),
+                    actor: ActorId::from_str("tester").expect("actor"),
+                    pdf_limits: PdfLimits::default(),
+                })
+            }
+            .expect("establish sticky authority");
+            let no_probe = FailingProbe {
+                provenance: provenance.clone(),
+            };
+            let interrupted = IngestEngine::with_fault_injector(
+                &mut store,
+                &vault,
+                &no_probe,
+                &clock,
+                &ids,
+                &FailAt(point),
+            )
+            .expect("construct sticky fault engine")
+            .ingest(IngestRequest {
+                project_id,
+                source: IntakeSource::open(&source_path).expect("open sticky source"),
+                idempotency_key: IdempotencyKey::try_from("second-key").expect("key"),
+                actor: ActorId::from_str("tester").expect("actor"),
+                pdf_limits: PdfLimits::default(),
+            });
+            assert!(matches!(interrupted, Err(HeleosError::FaultInjected)));
+            let inspection = FoundationReader::new(&store, &vault)
+                .inspect_foundation(project_id)
+                .expect("inspect sticky checkpoint");
+            let job_id = inspection
+                .jobs
+                .iter()
+                .find(|job| job.state != heleos_core::JobState::Succeeded)
+                .expect("nonterminal sticky job")
+                .job_id;
+            if point == FaultPoint::AfterJobStart {
+                clock.set(NOW_MS + 30_000);
+            }
+            let resumed = IngestEngine::new(&mut store, &vault, &no_probe, &clock, &ids)
+                .expect("construct sticky resume engine")
+                .resume(job_id, ActorId::from_str("resumer").expect("actor"))
+                .expect("resume typed sticky checkpoint");
+            if accepted {
+                assert_eq!(resumed.receipt.outcome, IngestOutcome::AcceptedDuplicate);
+                let evidence = first.evidence_manifest.expect("accepted evidence");
+                let mut expected_preexisting = vec![
+                    first.content_sha256.expect("original digest"),
+                    evidence.manifest_content_sha256,
+                ];
+                expected_preexisting.sort_unstable();
+                assert_eq!(
+                    resumed.receipt.preexisting_vault_digests,
+                    expected_preexisting
+                );
+            } else {
+                assert_eq!(resumed.receipt.outcome, IngestOutcome::QuarantinedCorrupt);
+                assert_eq!(
+                    resumed.receipt.preexisting_vault_digests,
+                    vec![first.content_sha256.expect("quarantined digest")]
+                );
+            }
+        }
+    }
+}
+
+#[test]
+fn trusted_post_start_failure_is_audited_failed_while_injected_loss_is_recoverable() {
+    // Break caught: trusted probe/object failures leaking an indefinitely live Running lease.
+    let mut store = Store::open_in_memory().expect("open store");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let project_id = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+    );
+    let ids = SequenceIds::new(
+        (2_u128..=80).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+    );
+    ProjectService::new(&mut store, &FixedClock, &ids)
+        .expect("construct project service")
+        .create(ProjectCreateRequest {
+            project_id: Some(project_id),
+            name: "Failure project".to_owned(),
+            actor: ActorId::from_str("creator").expect("actor"),
+            data_class: DataClass::Internal,
+        })
+        .expect("create project");
+    let source_root = TempDir::new().expect("create source directory");
+    let source_path = source_root.path().join("failure.pdf");
+    fs::write(&source_path, b"%PDF-1.7\ntrusted failure\n%%EOF\n").expect("write source");
+    let probe = FailingProbe {
+        provenance: accepted_probe().provenance,
+    };
+    let failure = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
+        .expect("construct failure engine")
+        .ingest(IngestRequest {
+            project_id,
+            source: IntakeSource::open(&source_path).expect("open retained source"),
+            idempotency_key: IdempotencyKey::try_from("trusted-failure").expect("key"),
+            actor: ActorId::from_str("tester").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        });
+    assert!(matches!(failure, Err(HeleosError::Integrity)));
+    let inspection = FoundationReader::new(&store, &vault)
+        .inspect_foundation(project_id)
+        .expect("inspect failed job");
+    assert_eq!(inspection.counts.jobs, 1);
+    assert_eq!(inspection.counts.ingest_events, 0);
+    assert_eq!(inspection.counts.audit_events, 4);
+    assert_eq!(inspection.jobs[0].state, heleos_core::JobState::Failed);
+    assert_eq!(
+        inspection.jobs[0].terminal_reason,
+        Some(heleos_core::FoundationJobTerminalReason::InternalFailure)
+    );
+}
+
+#[test]
+fn accepted_intake_commits_two_objects_and_reader_visible_evidence() {
+    // Break caught: accepted intake lacking its finite job, manifest lineage, and stable reader.
+    let mut store = Store::open_in_memory().expect("open store");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let project_id = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+    );
+    let ids = SequenceIds::new(
+        (2_u128..=40).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+    );
+    ProjectService::new(&mut store, &FixedClock, &ids)
+        .expect("construct project service")
+        .create(ProjectCreateRequest {
+            project_id: Some(project_id),
+            name: "Demo".to_owned(),
+            actor: ActorId::from_str("tester").expect("actor"),
+            data_class: DataClass::Internal,
+        })
+        .expect("create project");
+
+    let source_root = TempDir::new().expect("create source directory");
+    let source_path = source_root.path().join("fixture.pdf");
+    let source_bytes = b"%PDF-1.7\nfixture\n%%EOF\n";
+    fs::write(&source_path, source_bytes).expect("write source fixture");
+    let source_digest = Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
+    let (expected_document, expected_revision) = canonical_document_ids(source_digest);
+    let probe = accepted_probe();
+    let receipt = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
+        .expect("construct intake engine")
+        .ingest(IngestRequest {
+            project_id,
+            source: IntakeSource::open(&source_path).expect("open retained intake source"),
+            idempotency_key: IdempotencyKey::try_from("request-one").expect("key"),
+            actor: ActorId::from_str("tester").expect("actor"),
+            pdf_limits: PdfLimits::default(),
+        })
+        .expect("ingest accepted fixture");
+
+    assert_eq!(receipt.outcome, IngestOutcome::AcceptedNew);
+    assert_eq!(receipt.content_sha256, Some(source_digest));
+    assert_eq!(receipt.document_id, Some(expected_document));
+    assert_eq!(receipt.revision_id, Some(expected_revision));
+    assert_eq!(receipt.sheet_ids, vec![page_id(source_digest, 0)]);
+    assert!(receipt.quarantine.is_none());
+    assert!(receipt.evidence_manifest.is_some());
+    assert_eq!(fs::read(&source_path).expect("reread source"), source_bytes);
+
+    let reader = FoundationReader::new(&store, &vault);
+    let evidence = reader
+        .evidence_manifest_for_revision(expected_revision)
+        .expect("read revision evidence");
+    assert_eq!(evidence.manifest.original.sha256, source_digest);
+    assert_eq!(evidence.lineages.len(), 1);
+    let inspection = reader
+        .inspect_foundation(project_id)
+        .expect("inspect project foundation");
+    assert_eq!(
+        canonical_json(&inspection).expect("canonical populated inspection"),
+        br#"{"content_objects":[{"admission_state":"accepted","byte_length":1354,"media_type":"application/vnd.heleos.evidence-manifest+json;version=1","quarantine":null,"sha256":"310106e8067cf1f28f777684bb188d6a60b8d30828dc3bc856ce356c00f12be8","vault_key":"objects/sha256/31/01/310106e8067cf1f28f777684bb188d6a60b8d30828dc3bc856ce356c00f12be8"},{"admission_state":"accepted","byte_length":23,"media_type":"application/pdf","quarantine":null,"sha256":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","vault_key":"objects/sha256/c6/3e/c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248"}],"counts":{"audit_events":7,"content_objects":2,"documents":1,"evidence_objects":1,"ingest_events":1,"jobs":1,"project_documents":1,"revisions":1,"sheets":1},"document_ids":["c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248"],"evidence":[{"document_id":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","evidence_id":"00000000-0000-4000-8000-00000000000a","extraction_method":"heleos.pdf-probe/v1","manifest":{"byte_length":1354,"media_type":"application/vnd.heleos.evidence-manifest+json;version=1","sha256":"310106e8067cf1f28f777684bb188d6a60b8d30828dc3bc856ce356c00f12be8","vault_key":"objects/sha256/31/01/310106e8067cf1f28f777684bb188d6a60b8d30828dc3bc856ce356c00f12be8"},"original":{"byte_length":23,"media_type":"application/pdf","sha256":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","vault_key":"objects/sha256/c6/3e/c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248"},"originating_job_id":"00000000-0000-4000-8000-000000000003","probe_provenance":{"guest_dependency_graph_sha256":"0303030303030303030303030303030303030303030303030303030303030303","guest_source_tree_sha256":"0202020202020202020202020202020202020202020202020202020202020202","guest_wasm_sha256":"0101010101010101010101010101010101010101010101010101010101010101","parser_name":"fixture-parser","parser_version":"1.0.0","protocol_version":"heleos.pdf-probe/v1"},"requested_limits":{"max_fuel":5000000000,"max_guest_memory_bytes":805306368,"max_indirect_objects":250000,"max_input_bytes":268435456,"max_instances":1,"max_metadata_bytes":16777216,"max_nested_references":64,"max_page_axis_points":14400,"max_pages":10000,"max_protocol_output_bytes":4194304,"max_tables":4,"timeout_seconds":120},"review_state":"accepted","revision_id":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248"}],"intake_events":[{"attempt":1,"content_sha256":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","ingest_event_id":"00000000-0000-4000-8000-000000000008","job_id":"00000000-0000-4000-8000-000000000003","outcome":"accepted_new","terminal_at_ms":1700000000000}],"jobs":[{"attempt":1,"created_at_ms":1700000000000,"job_id":"00000000-0000-4000-8000-000000000003","kind":"pdf_ingest","state":"succeeded","terminal_reason":"completed","updated_at_ms":1700000000000}],"project_id":"00000000-0000-4000-8000-000000000001","revision_ids":["c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248"],"schema":"heleos.foundation-inspection/v1","sheets":[{"height_micropoints":792000000,"index":0,"parent_content_sha256":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","revision_id":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","rotation_degrees":0,"sheet_id":"781c897d999f036e5ceaffbeb1c5b90053472024df18667778451cfe5e21a0c7","transform":{"m11":1,"m12":0,"m21":0,"m22":-1,"tx_micropoints":0,"ty_micropoints":792000000},"unit":"pt","width_micropoints":612000000}]}"#,
+    );
+    assert_eq!(inspection.counts.content_objects, 2);
+    assert_eq!(inspection.counts.documents, 1);
+    assert_eq!(inspection.counts.revisions, 1);
+    assert_eq!(inspection.counts.sheets, 1);
+    assert_eq!(inspection.counts.evidence_objects, 1);
+    assert_eq!(inspection.counts.ingest_events, 1);
+    assert_eq!(inspection.counts.jobs, 1);
+}
+
+#[test]
+fn evidence_reader_reconstructs_cross_project_lineage_in_canonical_order() {
+    // Break caught: a revision reader selecting one project row or trusting insertion order.
+    let mut store = Store::open_in_memory().expect("open store");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let project_a = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project A UUID"),
+    );
+    let project_b = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000002").expect("project B UUID"),
+    );
+    let ids = SequenceIds::new(
+        (10_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+    );
+    for (project_id, name) in [(project_b, "Project B"), (project_a, "Project A")] {
+        ProjectService::new(&mut store, &FixedClock, &ids)
+            .expect("construct project service")
+            .create(ProjectCreateRequest {
+                project_id: Some(project_id),
+                name: name.to_owned(),
+                actor: ActorId::from_str("tester").expect("actor"),
+                data_class: DataClass::Internal,
+            })
+            .expect("create project");
+    }
+
+    let source_root = TempDir::new().expect("create source directory");
+    let source_path = source_root.path().join("shared.pdf");
+    let source_bytes = b"%PDF-1.7\nshared fixture\n%%EOF\n";
+    fs::write(&source_path, source_bytes).expect("write source fixture");
+    let source_digest = Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
+    let (_, revision_id) = canonical_document_ids(source_digest);
+    let probe = accepted_probe();
+    for (project_id, key) in [(project_b, "project-b"), (project_a, "project-a")] {
+        IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
+            .expect("construct intake engine")
+            .ingest(IngestRequest {
+                project_id,
+                source: IntakeSource::open(&source_path).expect("open retained intake source"),
+                idempotency_key: IdempotencyKey::try_from(key).expect("key"),
+                actor: ActorId::from_str("tester").expect("actor"),
+                pdf_limits: PdfLimits::default(),
+            })
+            .expect("ingest shared fixture");
+    }
+
+    let evidence = FoundationReader::new(&store, &vault)
+        .evidence_manifest_for_revision(revision_id)
+        .expect("reconstruct cross-project evidence");
+    assert_eq!(evidence.lineages.len(), 2);
+    assert_eq!(evidence.lineages[0].project_id, project_a);
+    assert_eq!(evidence.lineages[1].project_id, project_b);
+    assert!(evidence.lineages[0].evidence_id != evidence.lineages[1].evidence_id);
+    assert_eq!(
+        serde_json::from_slice::<heleos_core::EvidenceManifestReceipt>(
+            &canonical_json(&evidence).expect("canonical evidence receipt")
+        )
+        .expect("strict evidence receipt round trip"),
+        evidence
+    );
+}
+
+#[test]
+fn evidence_reader_rehashes_both_committed_vault_objects() {
+    // Break caught: trusting database digests without reopening immutable object bytes.
+    let (store, vault, vault_root, revision, original, _) = accepted_reader_fixture();
+    fs::write(
+        vault_root
+            .path()
+            .join("vault")
+            .join(Vault::object_key(original)),
+        b"corrupt original",
+    )
+    .expect("corrupt original object");
+    assert!(matches!(
+        FoundationReader::new(&store, &vault).evidence_manifest_for_revision(revision),
+        Err(HeleosError::Integrity)
+    ));
+
+    let (store, vault, vault_root, revision, _, manifest) = accepted_reader_fixture();
+    fs::write(
+        vault_root
+            .path()
+            .join("vault")
+            .join(Vault::object_key(manifest)),
+        b"corrupt manifest",
+    )
+    .expect("corrupt manifest object");
+    assert!(matches!(
+        FoundationReader::new(&store, &vault).evidence_manifest_for_revision(revision),
+        Err(HeleosError::Integrity)
+    ));
+}
+
+#[test]
+fn evidence_reader_rejects_one_disagreeing_cross_project_row() {
+    // Break caught: selecting the first lineage instead of requiring every row to agree.
+    let database_root = TempDir::new().expect("create database parent");
+    heleos_core::apply_private_permissions(database_root.path()).expect("harden database parent");
+    let database_path = fs::canonicalize(database_root.path())
+        .expect("canonicalize database parent")
+        .join("foundation.sqlite3");
+    let mut store = Store::open_writer(&database_path).expect("open writer");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let project_a = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project A UUID"),
+    );
+    let project_b = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000002").expect("project B UUID"),
+    );
+    let ids = SequenceIds::new(
+        (10_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+    );
+    for (project_id, name) in [(project_a, "Project A"), (project_b, "Project B")] {
+        ProjectService::new(&mut store, &FixedClock, &ids)
+            .expect("construct project service")
+            .create(ProjectCreateRequest {
+                project_id: Some(project_id),
+                name: name.to_owned(),
+                actor: ActorId::from_str("tester").expect("actor"),
+                data_class: DataClass::Internal,
+            })
+            .expect("create project");
+    }
+    let source_root = TempDir::new().expect("create source directory");
+    let source_path = source_root.path().join("shared.pdf");
+    let source_bytes = b"%PDF-1.7\nshared hostile fixture\n%%EOF\n";
+    fs::write(&source_path, source_bytes).expect("write source fixture");
+    let source_digest = Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
+    let (_, revision_id) = canonical_document_ids(source_digest);
+    let probe = accepted_probe();
+    for (project_id, key) in [(project_a, "project-a"), (project_b, "project-b")] {
+        IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
+            .expect("construct intake engine")
+            .ingest(IngestRequest {
+                project_id,
+                source: IntakeSource::open(&source_path).expect("open retained intake source"),
+                idempotency_key: IdempotencyKey::try_from(key).expect("key"),
+                actor: ActorId::from_str("tester").expect("actor"),
+                pdf_limits: PdfLimits::default(),
+            })
+            .expect("ingest shared fixture");
+    }
+    drop(store);
+
+    let read_only = Store::open_read_only(&database_path).expect("open clean read-only snapshot");
+    let reader = FoundationReader::new(&read_only, &vault);
+    assert_eq!(
+        reader
+            .evidence_manifest_for_revision(revision_id)
+            .expect("read evidence from read-only snapshot")
+            .lineages
+            .len(),
+        2
+    );
+    assert_eq!(
+        reader
+            .inspect_foundation(project_a)
+            .expect("inspect read-only snapshot")
+            .counts
+            .evidence_objects,
+        1
+    );
+    drop(read_only);
+
+    let connection = rusqlite::Connection::open(&database_path).expect("open hostile verifier");
+    connection
+        .execute_batch(
+            "PRAGMA ignore_check_constraints = ON;
+             DROP TRIGGER evidence_objects_no_update;",
+        )
+        .expect("authorize hostile verifier mutation");
+    connection
+        .execute(
+            "UPDATE evidence_objects
+             SET extraction_method = 'hostile.other/v1'
+             WHERE project_id = ?1",
+            [project_b.as_uuid().to_string()],
+        )
+        .expect("mutate one lineage");
+    drop(connection);
+
+    let store = Store::open_read_only(&database_path).expect("open read-only snapshot");
+    assert!(matches!(
+        FoundationReader::new(&store, &vault).evidence_manifest_for_revision(revision_id),
+        Err(HeleosError::Integrity)
+    ));
+}
+
+#[test]
+fn populated_inspection_is_canonically_sorted_and_rejects_permutations() {
+    // Break caught: query/insertion order leaking into the public foundation image.
+    let mut store = Store::open_in_memory().expect("open store");
+    store.migrate().expect("migrate store");
+    let vault_root = TempDir::new().expect("create vault parent");
+    let vault = opened_vault(&vault_root);
+    let project_id = ProjectId::from_uuid(
+        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
+    );
+    let ids = SequenceIds::new(
+        (2_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
+    );
+    ProjectService::new(&mut store, &FixedClock, &ids)
+        .expect("construct project service")
+        .create(ProjectCreateRequest {
+            project_id: Some(project_id),
+            name: "Demo".to_owned(),
+            actor: ActorId::from_str("tester").expect("actor"),
+            data_class: DataClass::Internal,
+        })
+        .expect("create project");
+
+    let source_root = TempDir::new().expect("create source directory");
+    let mut sources = [
+        ("first.pdf", b"%PDF-1.7\nfirst\n%%EOF\n".as_slice()),
+        ("second.pdf", b"%PDF-1.7\nsecond\n%%EOF\n".as_slice()),
+    ]
+    .into_iter()
+    .map(|(name, bytes)| {
+        (
+            Sha256Digest::hash_reader(bytes).expect("hash source"),
+            name,
+            bytes,
+        )
+    })
+    .collect::<Vec<_>>();
+    sources.sort_by(|left, right| right.0.cmp(&left.0));
+    let probe = accepted_probe();
+    for (ordinal, (_, name, bytes)) in sources.iter().enumerate() {
+        let path = source_root.path().join(name);
+        fs::write(&path, bytes).expect("write source fixture");
+        IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
+            .expect("construct intake engine")
+            .ingest(IngestRequest {
+                project_id,
+                source: IntakeSource::open(&path).expect("open retained intake source"),
+                idempotency_key: IdempotencyKey::try_from(format!("request-{ordinal}"))
+                    .expect("key"),
+                actor: ActorId::from_str("tester").expect("actor"),
+                pdf_limits: PdfLimits::default(),
+            })
+            .expect("ingest source fixture");
+    }
+
+    let inspection = FoundationReader::new(&store, &vault)
+        .inspect_foundation(project_id)
+        .expect("inspect populated foundation");
+    assert_eq!(inspection.content_objects.len(), 4);
+    assert_eq!(inspection.document_ids.len(), 2);
+    assert_eq!(inspection.revision_ids.len(), 2);
+    assert_eq!(inspection.sheets.len(), 2);
+    assert_eq!(inspection.evidence.len(), 2);
+    assert_eq!(inspection.intake_events.len(), 2);
+    assert_eq!(inspection.jobs.len(), 2);
+    assert!(
+        inspection
+            .content_objects
+            .windows(2)
+            .all(|pair| pair[0].sha256 < pair[1].sha256)
+    );
+    assert!(
+        inspection
+            .revision_ids
+            .windows(2)
+            .all(|pair| pair[0].as_digest() < pair[1].as_digest())
+    );
+
+    let canonical = canonical_json(&inspection).expect("canonical populated inspection");
+    let mut permuted = serde_json::from_slice::<serde_json::Value>(&canonical)
+        .expect("decode public inspection image");
+    permuted["content_objects"]
+        .as_array_mut()
+        .expect("content array")
+        .swap(0, 1);
+    assert!(serde_json::from_value::<FoundationInspection>(permuted).is_err());
+
+    let mut permuted = serde_json::from_slice::<serde_json::Value>(&canonical)
+        .expect("decode public inspection image");
+    permuted["evidence"]
+        .as_array_mut()
+        .expect("evidence array")
+        .swap(0, 1);
+    assert!(serde_json::from_value::<FoundationInspection>(permuted).is_err());
+
+    let mut over_cap = serde_json::from_slice::<serde_json::Value>(&canonical)
+        .expect("decode public inspection image");
+    over_cap["counts"]["audit_events"] = serde_json::json!(100_001);
+    assert!(serde_json::from_value::<FoundationInspection>(over_cap).is_err());
+
+    let mut unknown = serde_json::from_slice::<serde_json::Value>(&canonical)
+        .expect("decode public inspection image");
+    unknown["unknown"] = serde_json::json!(true);
+    assert!(serde_json::from_value::<FoundationInspection>(unknown).is_err());
+
+    let mut missing = serde_json::from_slice::<serde_json::Value>(&canonical)
+        .expect("decode public inspection image");
+    missing
+        .as_object_mut()
+        .expect("inspection object")
+        .remove("schema");
+    assert!(serde_json::from_value::<FoundationInspection>(missing).is_err());
+
+    let mut invalid_sheet = serde_json::from_slice::<serde_json::Value>(&canonical)
+        .expect("decode public inspection image");
+    invalid_sheet["sheets"][0]["width_micropoints"] = serde_json::json!(0);
+    assert!(serde_json::from_value::<FoundationInspection>(invalid_sheet).is_err());
+}
diff --git a/crates/heleos-core/tests/migrations.rs b/crates/heleos-core/tests/migrations.rs
index 1d936d3..f9c4f68 100644
--- a/crates/heleos-core/tests/migrations.rs
+++ b/crates/heleos-core/tests/migrations.rs
@@ -6,22 +6,22 @@ use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
 use std::path::{Path, PathBuf};
 use std::process::{Command, Stdio};
 
 use fs2::FileExt;
 use heleos_core::{
     HeleosError, INTEGRITY_VIOLATION_LIMIT, Store, apply_private_permissions,
     verify_private_permissions,
 };
 #[cfg(unix)]
 use rusqlite::OpenFlags;
-use rusqlite::config::DbConfig;
-use rusqlite::{Connection, params};
+use rusqlite::functions::FunctionFlags;
+use rusqlite::{Connection, Transaction, TransactionBehavior, params};
 use uuid::Uuid;
 
 const TABLES: [&str; 14] = [
     "audit_events",
     "content_objects",
     "corrections",
     "document_revisions",
     "documents",
     "evidence_objects",
     "ingest_events",
@@ -98,215 +98,334 @@ impl UnixMetadataSnapshot {
         }
     }
 }
 
 fn migrated_store(database: &TestDatabase) -> Store {
     let mut store = Store::open_writer(&database.path).expect("open writer");
     store.migrate().expect("migrate database");
     store
 }
 
+fn raw_verifier_connection(database: &TestDatabase) -> Connection {
+    raw_verifier_connection_path(&database.path)
+}
+
+fn raw_verifier_connection_path(path: &Path) -> Connection {
+    let connection = Connection::open(path).expect("open verifier-only connection");
+    let flags = FunctionFlags::SQLITE_UTF8
+        | FunctionFlags::SQLITE_DETERMINISTIC
+        | FunctionFlags::SQLITE_INNOCUOUS;
+    connection
+        .create_scalar_function("heleos_is_jcs", 1, flags, |context| {
+            let text = context.get::<String>(0)?;
+            let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
+                return Ok(false);
+            };
+            let Ok(canonical) = serde_jcs::to_string(&value) else {
+                return Ok(false);
+            };
+            Ok(canonical == text)
+        })
+        .expect("register verifier JCS validator");
+    connection
+        .create_scalar_function("heleos_is_uuid", 1, flags, |context| {
+            let text = context.get::<String>(0)?;
+            Ok(Uuid::parse_str(&text).is_ok_and(|value| value.hyphenated().to_string() == text))
+        })
+        .expect("register verifier UUID validator");
+    connection
+        .create_scalar_function("heleos_valid_text", 3, flags, |context| {
+            let text = context.get::<String>(0)?;
+            let max_bytes = context.get::<i64>(1)?;
+            let allow_ordinary_whitespace = context.get::<i64>(2)? != 0;
+            let valid_control = |character: char| {
+                allow_ordinary_whitespace && matches!(character, '\n' | '\r' | '\t')
+            };
+            let valid = u64::try_from(max_bytes).is_ok_and(|maximum| {
+                !text.is_empty()
+                    && u64::try_from(text.len()).is_ok_and(|length| length <= maximum)
+                    && !text
+                        .chars()
+                        .any(|character| character.is_control() && !valid_control(character))
+            });
+            Ok(valid)
+        })
+        .expect("register verifier text validator");
+    connection
+        .pragma_update(None, "foreign_keys", "ON")
+        .expect("enable verifier foreign keys");
+    connection
+        .pragma_update(None, "trusted_schema", "OFF")
+        .expect("disable verifier trusted schema");
+    connection
+}
+
+fn with_raw_verifier_transaction<T>(
+    database: &TestDatabase,
+    operation: impl FnOnce(&Transaction<'_>) -> Result<T, HeleosError>,
+) -> Result<T, HeleosError> {
+    let mut connection = raw_verifier_connection(database);
+    let transaction = connection
+        .transaction_with_behavior(TransactionBehavior::Immediate)
+        .expect("begin verifier-only transaction");
+    let value = operation(&transaction)?;
+    transaction
+        .commit()
+        .expect("commit verifier-only transaction");
+    Ok(value)
+}
+
+fn insert_job_test_project(transaction: &Transaction<'_>, project_id: &str) {
+    transaction
+        .execute(
+            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+             VALUES (?1, ?1, 0, 'actor', 'INTERNAL')",
+            [project_id],
+        )
+        .expect("insert job-test project");
+}
+
+fn insert_queued_job(transaction: &Transaction<'_>, job_id: &str, project_id: &str, key: &str) {
+    transaction
+        .execute(
+            "INSERT INTO job_runs
+                (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
+                 budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
+             VALUES (?1, ?2, 'pdf_ingest', ?3, 'queued', 0, 300000,
+                     '{}', '{}', '{}', 0, 0)",
+            params![job_id, project_id, key],
+        )
+        .expect("insert queued job fixture");
+}
+
+fn start_job(transaction: &Transaction<'_>, job_id: &str, updated_at_ms: i64) {
+    transaction
+        .execute(
+            "UPDATE job_runs
+             SET state = 'running', attempt = 1,
+                 lease_owner = '00000000-0000-4000-8000-000000000001',
+                 lease_expires_at_ms = 30000, updated_at_ms = ?2
+             WHERE id = ?1",
+            params![job_id, updated_at_ms],
+        )
+        .expect("start job fixture");
+}
+
+fn assert_sql_error_contains(result: rusqlite::Result<usize>, expected: &str) {
+    let error = result.expect_err("hostile SQL unexpectedly succeeded");
+    assert!(
+        error.to_string().contains(expected),
+        "unexpected SQLite error {error:?}; expected {expected:?}"
+    );
+}
+
+fn canonical_json_string_with_exact_bytes(byte_length: usize) -> String {
+    assert!(byte_length >= 2);
+    format!("\"{}\"", "x".repeat(byte_length - 2))
+}
+
 fn spawn_crash_left_writer(database: &TestDatabase) {
     let status = Command::new(env::current_exe().expect("locate test executable"))
         .arg("--exact")
         .arg("crash_left_wal_process_helper")
         .arg("--nocapture")
         .env("HELEOS_TEST_CRASH_DATABASE", &database.path)
         .status()
         .expect("spawn crash-left WAL fixture writer");
     assert!(status.success(), "crash-left WAL fixture failed");
 }
 
-fn insert_complete_fixture(store: &mut Store, payload: &str) {
+fn insert_complete_fixture(database: &TestDatabase, payload: &str) {
     let json = serde_json::to_string(&payload).expect("encode payload as JSON string");
-    store
-        .with_immediate_transaction(|tx| {
-            tx.execute(
-                "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+    with_raw_verifier_transaction(database, |tx| {
+        tx.execute(
+            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                  VALUES (?1, ?2, ?3, ?4, ?5)",
-                params!["project-1", payload, 1_i64, payload, "PROJECT_CONFIDENTIAL"],
-            )
-            .expect("insert project");
-            tx.execute(
-                "INSERT INTO content_objects
+            params!["project-1", payload, 1_i64, payload, "PROJECT_CONFIDENTIAL"],
+        )
+        .expect("insert project");
+        tx.execute(
+            "INSERT INTO content_objects
                     (sha256, byte_length, admission_state, vault_key, created_at_ms, created_by,
                      quarantine_reason)
                  VALUES (?1, ?2, ?3, ?4, ?5, ?6, NULL)",
-                params!["a".repeat(64), 4_i64, "accepted", payload, 2_i64, payload],
-            )
-            .expect("insert content object");
-            tx.execute(
-                "INSERT INTO documents (id, created_at_ms, created_by)
+            params!["a".repeat(64), 4_i64, "accepted", payload, 2_i64, payload],
+        )
+        .expect("insert content object");
+        tx.execute(
+            "INSERT INTO documents (id, created_at_ms, created_by)
                  VALUES (?1, ?2, ?3)",
-                params!["document-1", 3_i64, payload],
-            )
-            .expect("insert document");
-            tx.execute(
-                "INSERT INTO document_revisions
+            params!["document-1", 3_i64, payload],
+        )
+        .expect("insert document");
+        tx.execute(
+            "INSERT INTO document_revisions
                     (id, document_id, content_sha256, created_at_ms, created_by)
                  VALUES (?1, ?2, ?3, ?4, ?5)",
-                params!["revision-1", "document-1", "a".repeat(64), 4_i64, payload],
-            )
-            .expect("insert revision");
-            tx.execute(
-                "INSERT INTO project_documents
+            params!["revision-1", "document-1", "a".repeat(64), 4_i64, payload],
+        )
+        .expect("insert revision");
+        tx.execute(
+            "INSERT INTO project_documents
                     (project_id, document_id, linked_at_ms, linked_by)
                  VALUES (?1, ?2, ?3, ?4)",
-                params!["project-1", "document-1", 5_i64, payload],
-            )
-            .expect("link project document");
-            tx.execute(
-                "INSERT INTO job_runs
+            params!["project-1", "document-1", 5_i64, payload],
+        )
+        .expect("link project document");
+        tx.execute(
+            "INSERT INTO job_runs
                     (id, project_id, kind, idempotency_key, state, attempt, lease_owner,
                      lease_expires_at_ms, deadline_at_ms, budget_json, input_json,
                      checkpoint_json, terminal_reason, created_at_ms, updated_at_ms)
-                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, NULL, NULL, NULL, ?7, ?8, ?9, NULL, ?10, ?11)",
-                params![
-                    "job-1",
-                    "project-1",
-                    "pdf_ingest",
-                    payload,
-                    "succeeded",
-                    1_i64,
-                    &json,
-                    &json,
-                    &json,
-                    6_i64,
-                    7_i64
-                ],
-            )
-            .expect("insert job");
-            tx.execute(
-                "INSERT INTO ingest_events
-                    (id, project_id, job_id, content_sha256, outcome, source_name, source_path,
-                     idempotency_key, actor, terminal_at_ms, details_json)
-                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
-                params![
-                    "ingest-1",
-                    "project-1",
-                    "job-1",
-                    "a".repeat(64),
-                    "accepted_new",
-                    payload,
-                    payload,
-                    payload,
-                    payload,
-                    8_i64,
-                    &json
-                ],
-            )
-            .expect("insert ingest event");
-            tx.execute(
-                "INSERT INTO sheets
+                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, NULL, NULL, ?7, ?8, ?9, ?10,
+                         'completed', ?11, ?12)",
+            params![
+                "job-1",
+                "project-1",
+                "pdf_ingest",
+                payload,
+                "succeeded",
+                1_i64,
+                100_i64,
+                &json,
+                &json,
+                &json,
+                6_i64,
+                7_i64
+            ],
+        )
+        .expect("insert job");
+        tx.execute(
+            "INSERT INTO ingest_events
+                    (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
+                     source_path, idempotency_key, actor, terminal_at_ms, details_json)
+                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, '<redacted>', ?8, ?9, ?10, ?11)",
+            params![
+                "ingest-1",
+                "project-1",
+                "job-1",
+                "a".repeat(64),
+                "accepted_new",
+                1_i64,
+                payload,
+                payload,
+                payload,
+                8_i64,
+                &json
+            ],
+        )
+        .expect("insert ingest event");
+        tx.execute(
+            "INSERT INTO sheets
                     (id, revision_id, zero_based_page_index, width_micropoints,
                      height_micropoints, rotation_degrees, unit, parent_content_sha256,
                      transform_json)
                  VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
-                params![
-                    "sheet-1",
-                    "revision-1",
-                    0_i64,
-                    612_000_i64,
-                    792_000_i64,
-                    0_i64,
-                    "pt",
-                    "a".repeat(64),
-                    &json
-                ],
-            )
-            .expect("insert sheet");
-            tx.execute(
-                "INSERT INTO scales
+            params![
+                "sheet-1",
+                "revision-1",
+                0_i64,
+                612_000_i64,
+                792_000_i64,
+                0_i64,
+                "pt",
+                "a".repeat(64),
+                &json
+            ],
+        )
+        .expect("insert sheet");
+        tx.execute(
+            "INSERT INTO scales
                     (id, sheet_id, numerator, denominator, source, created_at_ms, created_by)
                  VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
-                params!["scale-1", "sheet-1", 1_i64, 48_i64, payload, 9_i64, payload],
-            )
-            .expect("insert scale");
-            tx.execute(
-                "INSERT INTO source_records
+            params!["scale-1", "sheet-1", 1_i64, 48_i64, payload, 9_i64, payload],
+        )
+        .expect("insert scale");
+        tx.execute(
+            "INSERT INTO source_records
                     (id, project_id, job_id, source_name, source_path, content_sha256,
                      metadata_json, created_at_ms)
                  VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
-                params![
-                    "source-1",
-                    "project-1",
-                    "job-1",
-                    payload,
-                    payload,
-                    "a".repeat(64),
-                    &json,
-                    10_i64
-                ],
-            )
-            .expect("insert source record");
-            tx.execute(
-                "INSERT INTO evidence_objects
+            params![
+                "source-1",
+                "project-1",
+                "job-1",
+                payload,
+                "<redacted>",
+                "a".repeat(64),
+                &json,
+                10_i64
+            ],
+        )
+        .expect("insert source record");
+        tx.execute(
+            "INSERT INTO evidence_objects
                     (id, project_id, job_id, document_revision_id, content_sha256,
                      parent_content_sha256, extraction_method, parameters_json, review_state,
                      created_at_ms)
                  VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
-                params![
-                    "evidence-1",
-                    "project-1",
-                    "job-1",
-                    "revision-1",
-                    "a".repeat(64),
-                    "a".repeat(64),
-                    payload,
-                    &json,
-                    "unreviewed",
-                    11_i64
-                ],
-            )
-            .expect("insert evidence object");
-            tx.execute(
-                "INSERT INTO corrections
+            params![
+                "evidence-1",
+                "project-1",
+                "job-1",
+                "revision-1",
+                "a".repeat(64),
+                "a".repeat(64),
+                payload,
+                &json,
+                "unreviewed",
+                11_i64
+            ],
+        )
+        .expect("insert evidence object");
+        tx.execute(
+            "INSERT INTO corrections
                     (id, project_id, evidence_id, actor, reason, before_json, after_json,
                      created_at_ms)
                  VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
-                params![
-                    "correction-1",
-                    "project-1",
-                    "evidence-1",
-                    payload,
-                    payload,
-                    &json,
-                    &json,
-                    12_i64
-                ],
-            )
-            .expect("insert correction");
-            tx.execute(
-                "INSERT INTO audit_events
+            params![
+                "correction-1",
+                "project-1",
+                "evidence-1",
+                payload,
+                payload,
+                &json,
+                &json,
+                12_i64
+            ],
+        )
+        .expect("insert correction");
+        tx.execute(
+            "INSERT INTO audit_events
                     (id, sequence, project_id, actor, action, subject_type, subject_id,
                      before_json, after_json, reason, occurred_at_ms, previous_hash, event_hash)
                  VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
-                params![
-                    "audit-1",
-                    1_i64,
-                    "project-1",
-                    payload,
-                    payload,
-                    "project",
-                    "project-1",
-                    &json,
-                    &json,
-                    payload,
-                    13_i64,
-                    "0".repeat(64),
-                    "b".repeat(64)
-                ],
-            )
-            .expect("insert audit event");
-            Ok(())
-        })
-        .expect("commit fixture");
+            params![
+                "audit-1",
+                1_i64,
+                "project-1",
+                payload,
+                "project_created",
+                "project",
+                "project-1",
+                &json,
+                &json,
+                payload,
+                13_i64,
+                "0".repeat(64),
+                "b".repeat(64)
+            ],
+        )
+        .expect("insert audit event");
+        Ok(())
+    })
+    .expect("commit fixture");
 }
 
 #[test]
 fn empty_database_migrates_to_version_one_and_reopen_is_idempotent() {
     let database = TestDatabase::new("fresh-migration");
     let mut store = Store::open_writer(&database.path).expect("open empty database");
     assert_eq!(store.schema_version().expect("read empty version"), 0);
 
     let first = store.migrate().expect("apply foundation migration");
     assert_eq!(first.from_version, 0);
@@ -315,1121 +434,2548 @@ fn empty_database_migrates_to_version_one_and_reopen_is_idempotent() {
     drop(store);
 
     let mut reopened = Store::open_writer(&database.path).expect("reopen database");
     let second = reopened.migrate().expect("repeat migration");
     assert_eq!(second.from_version, 1);
     assert_eq!(second.to_version, 1);
     assert!(second.applied_versions.is_empty());
 }
 
 #[test]
-fn writer_connection_enforces_all_foundation_sqlite_settings() {
-    let database = TestDatabase::new("connection-settings");
-    let mut store = migrated_store(&database);
-
-    let settings = store
-        .with_immediate_transaction(|tx| {
-            let foreign_keys = tx
-                .pragma_query_value(None, "foreign_keys", |row| row.get::<_, i64>(0))
-                .expect("query foreign_keys");
-            let journal_mode = tx
-                .pragma_query_value(None, "journal_mode", |row| row.get::<_, String>(0))
-                .expect("query journal mode");
-            let synchronous = tx
-                .pragma_query_value(None, "synchronous", |row| row.get::<_, i64>(0))
-                .expect("query synchronous");
-            let busy_timeout = tx
-                .pragma_query_value(None, "busy_timeout", |row| row.get::<_, i64>(0))
-                .expect("query busy timeout");
-            let query_only = tx
-                .pragma_query_value(None, "query_only", |row| row.get::<_, i64>(0))
-                .expect("query writer query_only");
-            let temp_store = tx
-                .pragma_query_value(None, "temp_store", |row| row.get::<_, i64>(0))
-                .expect("query temp store");
-            let trusted_schema = tx
-                .db_config(DbConfig::SQLITE_DBCONFIG_TRUSTED_SCHEMA)
-                .expect("query trusted schema");
-            let defensive = tx
-                .db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE)
-                .expect("query defensive mode");
-            let dqs_ddl = tx
-                .db_config(DbConfig::SQLITE_DBCONFIG_DQS_DDL)
-                .expect("query DQS DDL mode");
-            let dqs_dml = tx
-                .db_config(DbConfig::SQLITE_DBCONFIG_DQS_DML)
-                .expect("query DQS DML mode");
-            let attach_create = tx
-                .db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_CREATE)
-                .expect("query attach create mode");
-            let attach_write = tx
-                .db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_WRITE)
-                .expect("query attach write mode");
-            Ok((
-                foreign_keys,
-                journal_mode,
-                synchronous,
-                busy_timeout,
-                query_only,
-                temp_store,
-                trusted_schema,
-                defensive,
-                dqs_ddl,
-                dqs_dml,
-                attach_create,
-                attach_write,
-            ))
-        })
-        .expect("inspect settings");
-
-    assert_eq!(
-        settings,
-        (
-            1,
-            "wal".to_owned(),
-            2,
-            5_000,
-            0,
-            2,
-            false,
-            true,
-            false,
-            false,
-            false,
-            false,
+fn foundation_migration_creates_exact_required_tables() {
+    let database = TestDatabase::new("table-set");
+    drop(migrated_store(&database));
+    let connection = raw_verifier_connection(&database);
+    let mut statement = connection
+        .prepare(
+            "SELECT name FROM sqlite_schema
+             WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
+             ORDER BY name",
         )
+        .expect("prepare table query");
+    let tables = statement
+        .query_map([], |row| row.get::<_, String>(0))
+        .expect("query table names")
+        .collect::<rusqlite::Result<Vec<_>>>()
+        .expect("collect table names");
+
+    assert_eq!(tables, TABLES);
+}
+
+#[test]
+fn task_six_schema_exposes_media_authority_and_attempt_identity() {
+    // Break caught: accepted manifests/PDFs sharing an untyped content row or duplicate job attempts.
+    let database = TestDatabase::new("task-six-columns");
+    let store = migrated_store(&database);
+    drop(store);
+    let connection = Connection::open_with_flags(
+        &database.path,
+        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY
+            | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX
+            | rusqlite::OpenFlags::SQLITE_OPEN_NOFOLLOW,
+    )
+    .expect("open verifier-only schema connection");
+
+    let columns = |table: &str| {
+        let mut statement = connection
+            .prepare(&format!("PRAGMA table_info({table})"))
+            .expect("prepare table column query");
+        statement
+            .query_map([], |row| row.get::<_, String>(1))
+            .expect("query table columns")
+            .collect::<std::result::Result<Vec<_>, _>>()
+            .expect("collect table columns")
+    };
+    assert!(
+        columns("content_objects")
+            .iter()
+            .any(|column| column == "media_type")
+    );
+    assert!(
+        columns("ingest_events")
+            .iter()
+            .any(|column| column == "attempt")
     );
 }
 
 #[test]
-fn foundation_migration_creates_exact_required_tables() {
-    let database = TestDatabase::new("table-set");
-    let mut store = migrated_store(&database);
-    let tables = store
-        .with_immediate_transaction(|tx| {
-            let mut statement = tx
-                .prepare(
-                    "SELECT name FROM sqlite_schema
-                     WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
-                     ORDER BY name",
-                )
-                .expect("prepare table query");
-            let names = statement
-                .query_map([], |row| row.get::<_, String>(0))
-                .expect("query table names")
-                .collect::<rusqlite::Result<Vec<_>>>()
-                .expect("collect table names");
-            Ok(names)
+fn task_six_latest_job_audit_index_has_the_exact_subject_sequence_shape() {
+    // Break caught: every live-job lookup window-sorting the complete historical audit chain.
+    let database = TestDatabase::new("task-six-job-audit-index");
+    drop(migrated_store(&database));
+    let connection = raw_verifier_connection(&database);
+    let sql = connection
+        .query_row(
+            "SELECT sql FROM sqlite_schema
+             WHERE type = 'index' AND name = 'audit_events_subject_sequence'",
+            [],
+            |row| row.get::<_, String>(0),
+        )
+        .expect("read exact latest-job-audit index");
+    assert_eq!(
+        sql,
+        "CREATE INDEX audit_events_subject_sequence ON audit_events(subject_type, subject_id, sequence DESC)"
+    );
+    let mut statement = connection
+        .prepare("PRAGMA index_xinfo(audit_events_subject_sequence)")
+        .expect("prepare index shape query");
+    let columns = statement
+        .query_map([], |row| {
+            Ok((
+                row.get::<_, i64>(0)?,
+                row.get::<_, Option<String>>(2)?,
+                row.get::<_, i64>(3)?,
+                row.get::<_, i64>(5)?,
+            ))
         })
-        .expect("inspect schema");
-
-    assert_eq!(tables, TABLES);
+        .expect("query index columns")
+        .collect::<rusqlite::Result<Vec<_>>>()
+        .expect("collect index columns");
+    assert_eq!(
+        columns,
+        vec![
+            (0, Some("subject_type".to_owned()), 0, 1),
+            (1, Some("subject_id".to_owned()), 0, 1),
+            (2, Some("sequence".to_owned()), 1, 1),
+            (3, None, 0, 0),
+        ]
+    );
 }
 
 #[test]
 fn bound_sql_payloads_remain_inert_data_in_every_sensitive_text_class() {
     let database = TestDatabase::new("bound-values");
-    let mut store = migrated_store(&database);
-    let payload = "Robert'); DROP TABLE projects; --\n../vault/$HOME/\u{001b}[31m";
-    insert_complete_fixture(&mut store, payload);
-
-    let recovered = store
-        .with_immediate_transaction(|tx| {
-            let project_name = tx
-                .query_row(
-                    "SELECT name FROM projects WHERE id = ?1",
-                    ["project-1"],
-                    |row| row.get::<_, String>(0),
-                )
-                .expect("query project name");
-            let actor = tx
-                .query_row(
-                    "SELECT actor FROM ingest_events WHERE id = ?1",
-                    ["ingest-1"],
-                    |row| row.get::<_, String>(0),
-                )
-                .expect("query actor");
-            let idempotency_key = tx
-                .query_row(
-                    "SELECT idempotency_key FROM job_runs WHERE id = ?1",
-                    ["job-1"],
-                    |row| row.get::<_, String>(0),
-                )
-                .expect("query idempotency key");
-            let path = tx
-                .query_row(
-                    "SELECT source_path FROM source_records WHERE id = ?1",
-                    ["source-1"],
-                    |row| row.get::<_, String>(0),
-                )
-                .expect("query path");
-            let json = tx
-                .query_row(
-                    "SELECT metadata_json FROM source_records WHERE id = ?1",
-                    ["source-1"],
-                    |row| row.get::<_, String>(0),
-                )
-                .expect("query JSON");
-            Ok((project_name, actor, idempotency_key, path, json))
-        })
-        .expect("read payloads");
+    drop(migrated_store(&database));
+    let payload = "Robert'); DROP TABLE projects; -- ../vault/$HOME/[31m";
+    insert_complete_fixture(&database, payload);
+
+    let recovered = with_raw_verifier_transaction(&database, |tx| {
+        let project_name = tx
+            .query_row(
+                "SELECT name FROM projects WHERE id = ?1",
+                ["project-1"],
+                |row| row.get::<_, String>(0),
+            )
+            .expect("query project name");
+        let actor = tx
+            .query_row(
+                "SELECT actor FROM ingest_events WHERE id = ?1",
+                ["ingest-1"],
+                |row| row.get::<_, String>(0),
+            )
+            .expect("query actor");
+        let idempotency_key = tx
+            .query_row(
+                "SELECT idempotency_key FROM job_runs WHERE id = ?1",
+                ["job-1"],
+                |row| row.get::<_, String>(0),
+            )
+            .expect("query idempotency key");
+        let path = tx
+            .query_row(
+                "SELECT source_path FROM source_records WHERE id = ?1",
+                ["source-1"],
+                |row| row.get::<_, String>(0),
+            )
+            .expect("query path");
+        let json = tx
+            .query_row(
+                "SELECT metadata_json FROM source_records WHERE id = ?1",
+                ["source-1"],
+                |row| row.get::<_, String>(0),
+            )
+            .expect("query JSON");
+        Ok((project_name, actor, idempotency_key, path, json))
+    })
+    .expect("read payloads");
 
     assert_eq!(recovered.0, payload);
     assert_eq!(recovered.1, payload);
     assert_eq!(recovered.2, payload);
-    assert_eq!(recovered.3, payload);
+    assert_eq!(recovered.3, "<redacted>");
     assert_eq!(
         serde_json::from_str::<String>(&recovered.4).expect("decode stored JSON"),
         payload
     );
 }
 
 #[test]
 fn foreign_keys_json_and_exact_persisted_enums_fail_closed() {
     let database = TestDatabase::new("constraints");
-    let mut store = migrated_store(&database);
+    drop(migrated_store(&database));
 
-    store
-        .with_immediate_transaction(|tx| {
+    with_raw_verifier_transaction(&database, |tx| {
+        tx.execute(
+            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+                 VALUES (?1, ?2, ?3, ?4, ?5)",
+            params!["project-valid", "name", 1_i64, "actor", "INTERNAL"],
+        )
+        .expect("insert valid project prerequisite");
+        assert!(
             tx.execute(
                 "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
-                 VALUES (?1, ?2, ?3, ?4, ?5)",
-                params!["project-valid", "name", 1_i64, "actor", "INTERNAL"],
-            )
-            .expect("insert valid project prerequisite");
-            assert!(
-                tx.execute(
-                    "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                      VALUES (?1, ?2, ?3, ?4, ?5)",
-                    params!["project-invalid", "name", 1_i64, "actor", "PRIVATE"],
-                )
-                .is_err()
-            );
-            assert!(
-                tx.execute(
-                    "INSERT INTO project_documents
+                params!["project-invalid", "name", 1_i64, "actor", "PRIVATE"],
+            )
+            .is_err()
+        );
+        assert!(
+            tx.execute(
+                "INSERT INTO project_documents
                         (project_id, document_id, linked_at_ms, linked_by)
                      VALUES (?1, ?2, ?3, ?4)",
-                    params!["missing-project", "missing-document", 1_i64, "actor"],
-                )
-                .is_err()
-            );
-            assert!(
-                tx.execute(
-                    "INSERT INTO content_objects
+                params!["missing-project", "missing-document", 1_i64, "actor"],
+            )
+            .is_err()
+        );
+        assert!(
+            tx.execute(
+                "INSERT INTO content_objects
                         (sha256, byte_length, admission_state, vault_key, created_at_ms, created_by)
                      VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
-                    params!["c".repeat(64), 1_i64, "pending", "key", 1_i64, "actor"],
-                )
-                .is_err()
-            );
-            assert!(
-                tx.execute(
-                    "INSERT INTO job_runs
-                        (id, project_id, kind, idempotency_key, state, attempt, budget_json,
-                         input_json, checkpoint_json, created_at_ms, updated_at_ms)
-                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
-                    params![
-                        "job-invalid",
-                        "project-valid",
-                        "pdf_ingest",
-                        "key",
-                        "paused",
-                        0_i64,
-                        "{}",
-                        "{}",
-                        "{}",
-                        1_i64,
-                        1_i64
-                    ],
-                )
-                .is_err()
-            );
+                params!["c".repeat(64), 1_i64, "pending", "key", 1_i64, "actor"],
+            )
+            .is_err()
+        );
+        assert!(
             tx.execute(
                 "INSERT INTO job_runs
-                    (id, project_id, kind, idempotency_key, state, attempt, budget_json,
-                     input_json, checkpoint_json, created_at_ms, updated_at_ms)
-                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
+                        (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
+                         budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
+                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
                 params![
-                    "job-valid",
+                    "job-invalid",
                     "project-valid",
                     "pdf_ingest",
-                    "valid-key",
-                    "queued",
+                    "key",
+                    "paused",
                     0_i64,
+                    100_i64,
                     "{}",
                     "{}",
                     "{}",
                     1_i64,
                     1_i64
                 ],
             )
-            .expect("insert valid job prerequisite");
-            assert!(
-                tx.execute(
-                    "INSERT INTO ingest_events
+            .is_err()
+        );
+        tx.execute(
+            "INSERT INTO job_runs
+                    (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
+                     budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
+                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
+            params![
+                "job-valid",
+                "project-valid",
+                "pdf_ingest",
+                "valid-key",
+                "queued",
+                0_i64,
+                100_i64,
+                "{}",
+                "{}",
+                "{}",
+                1_i64,
+                1_i64
+            ],
+        )
+        .expect("insert valid job prerequisite");
+        assert!(
+            tx.execute(
+                "INSERT INTO ingest_events
                         (id, project_id, job_id, outcome, source_name, source_path,
                          idempotency_key, actor, terminal_at_ms, details_json)
                      VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
-                    params![
-                        "ingest-invalid",
-                        "project-valid",
-                        "job-valid",
-                        "accepted",
-                        "name",
-                        "path",
-                        "valid-key",
-                        "actor",
-                        1_i64,
-                        "{}"
-                    ],
-                )
-                .is_err()
-            );
-            assert!(
-                tx.execute(
-                    "INSERT INTO source_records
+                params![
+                    "ingest-invalid",
+                    "project-valid",
+                    "job-valid",
+                    "accepted",
+                    "name",
+                    "path",
+                    "valid-key",
+                    "actor",
+                    1_i64,
+                    "{}"
+                ],
+            )
+            .is_err()
+        );
+        assert!(
+            tx.execute(
+                "INSERT INTO source_records
                         (id, project_id, source_name, source_path, metadata_json, created_at_ms)
                      VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
-                    params![
-                        "source-invalid",
-                        "project-valid",
-                        "name",
-                        "path",
-                        "not-json",
-                        1_i64
-                    ],
-                )
-                .is_err()
-            );
-            Ok(())
-        })
-        .expect("constraint checks complete");
+                params![
+                    "source-invalid",
+                    "project-valid",
+                    "name",
+                    "path",
+                    "not-json",
+                    1_i64
+                ],
+            )
+            .is_err()
+        );
+        Ok(())
+    })
+    .expect("constraint checks complete");
 }
 
 #[test]
-fn declared_uniqueness_foreign_keys_and_delete_actions_are_independently_enforced() {
-    let database = TestDatabase::new("constraint-matrix");
-    let mut store = migrated_store(&database);
-    insert_complete_fixture(&mut store, "collision");
+fn running_job_same_state_update_requires_a_new_checkpoint_and_nondecreasing_timestamp() {
+    // Break caught: an updated-at-only heartbeat bypassed the finite checkpoint transition.
+    let database = TestDatabase::new("running-checkpoint-transition");
+    drop(migrated_store(&database));
 
-    store
-        .with_immediate_transaction(|tx| {
-            let uniqueness_collisions = [
-                (
-                    "content object sha256",
+    with_raw_verifier_transaction(&database, |tx| {
+        tx.execute(
+            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+             VALUES (?1, ?2, ?3, ?4, ?5)",
+            params!["project", "name", 1_i64, "actor", "INTERNAL"],
+        )
+        .expect("insert project prerequisite");
+        tx.execute(
+            "INSERT INTO job_runs
+                (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
+                 budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
+             VALUES (?1, ?2, 'pdf_ingest', ?3, 'queued', 0, ?4, '{}', '{}', '{}', ?5, ?5)",
+            params!["job", "project", "key", 100_i64, 1_i64],
+        )
+        .expect("insert queued job");
+        tx.execute(
+            "UPDATE job_runs
+             SET state = 'running', attempt = 1, lease_owner = ?1,
+                 lease_expires_at_ms = 50, updated_at_ms = 2
+             WHERE id = 'job'",
+            ["00000000-0000-4000-8000-000000000001"],
+        )
+        .expect("start job");
+
+        assert!(
+            tx.execute("UPDATE job_runs SET updated_at_ms = 3 WHERE id = 'job'", [],)
+                .is_err(),
+            "updated-at-only running heartbeat bypassed checkpoint transition"
+        );
+        assert!(
+            tx.execute(
+                "UPDATE job_runs SET checkpoint_json = checkpoint_json,
+                    updated_at_ms = updated_at_ms WHERE id = 'job'",
+                [],
+            )
+            .is_err(),
+            "running no-op update bypassed checkpoint transition"
+        );
+        assert!(
+            tx.execute(
+                "UPDATE job_runs SET checkpoint_json = '{\"phase\":\"backward\"}',
+                    updated_at_ms = 1 WHERE id = 'job'",
+                [],
+            )
+            .is_err(),
+            "running checkpoint update moved time backward"
+        );
+        tx.execute(
+            "UPDATE job_runs
+             SET checkpoint_json = '{\"phase\":\"processing_complete\"}', updated_at_ms = 2
+             WHERE id = 'job'",
+            [],
+        )
+        .expect("commit exact same-millisecond running checkpoint update");
+        Ok(())
+    })
+    .expect("verify running checkpoint transition");
+}
+
+#[test]
+fn task_six_job_checks_and_transition_triggers_fail_closed_at_raw_sql_boundary() {
+    let database = TestDatabase::new("task-six-job-matrix");
+    drop(migrated_store(&database));
+
+    with_raw_verifier_transaction(&database, |tx| {
+        insert_job_test_project(tx, "project");
+        insert_job_test_project(tx, "project-other");
+
+        let insert_state = |id: &str,
+                            key: &str,
+                            state: &str,
+                            attempt: i64,
+                            lease_owner: Option<&str>,
+                            lease_expires_at_ms: Option<i64>,
+                            deadline_at_ms: Option<i64>,
+                            terminal_reason: Option<&str>,
+                            created_at_ms: i64,
+                            updated_at_ms: i64| {
+            tx.execute(
+                "INSERT INTO job_runs
+                    (id, project_id, kind, idempotency_key, state, attempt, lease_owner,
+                     lease_expires_at_ms, deadline_at_ms, budget_json, input_json,
+                     checkpoint_json, terminal_reason, created_at_ms, updated_at_ms)
+                 VALUES (?1, 'project', 'pdf_ingest', ?2, ?3, ?4, ?5, ?6, ?7,
+                         '{}', '{}', '{}', ?8, ?9, ?10)",
+                params![
+                    id,
+                    key,
+                    state,
+                    attempt,
+                    lease_owner,
+                    lease_expires_at_ms,
+                    deadline_at_ms,
+                    terminal_reason,
+                    created_at_ms,
+                    updated_at_ms
+                ],
+            )
+        };
+
+        assert!(
+            insert_state(
+                "max-time",
+                "max-time",
+                "queued",
+                0,
+                None,
+                None,
+                Some(9_007_199_254_740_991),
+                None,
+                9_007_199_254_740_991,
+                9_007_199_254_740_991,
+            )
+            .is_ok(),
+            "JCS-safe timestamp boundary was rejected"
+        );
+        for (label, result) in [
+            (
+                "kind literal",
+                tx.execute(
+                    "INSERT INTO job_runs
+                        (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
+                         budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
+                     VALUES ('bad-kind', 'project', 'other', 'bad-kind', 'queued', 0, 1,
+                             '{}', '{}', '{}', 0, 0)",
+                    [],
+                ),
+            ),
+            (
+                "attempt upper bound",
+                insert_state(
+                    "attempt-17",
+                    "attempt-17",
+                    "running",
+                    17,
+                    Some("00000000-0000-4000-8000-000000000001"),
+                    Some(1),
+                    Some(2),
+                    None,
+                    0,
+                    0,
+                ),
+            ),
+            (
+                "running lease required",
+                insert_state(
+                    "running-no-lease",
+                    "running-no-lease",
+                    "running",
+                    1,
+                    None,
+                    None,
+                    Some(2),
+                    None,
+                    0,
+                    0,
+                ),
+            ),
+            (
+                "lease UUID shape",
+                insert_state(
+                    "running-bad-owner",
+                    "running-bad-owner",
+                    "running",
+                    1,
+                    Some("not-a-uuid"),
+                    Some(1),
+                    Some(2),
+                    None,
+                    0,
+                    0,
+                ),
+            ),
+            (
+                "queued lease forbidden",
+                insert_state(
+                    "queued-with-lease",
+                    "queued-with-lease",
+                    "queued",
+                    0,
+                    Some("00000000-0000-4000-8000-000000000001"),
+                    Some(1),
+                    Some(2),
+                    None,
+                    0,
+                    0,
+                ),
+            ),
+            (
+                "interrupted attempt maximum",
+                insert_state(
+                    "interrupted-16",
+                    "interrupted-16",
+                    "interrupted",
+                    16,
+                    None,
+                    None,
+                    Some(2),
+                    None,
+                    0,
+                    0,
+                ),
+            ),
+            (
+                "terminal reason required",
+                insert_state(
+                    "succeeded-no-reason",
+                    "succeeded-no-reason",
+                    "succeeded",
+                    1,
+                    None,
+                    None,
+                    Some(2),
+                    None,
+                    0,
+                    0,
+                ),
+            ),
+            (
+                "terminal lease forbidden",
+                insert_state(
+                    "succeeded-with-lease",
+                    "succeeded-with-lease",
+                    "succeeded",
+                    1,
+                    Some("00000000-0000-4000-8000-000000000001"),
+                    Some(1),
+                    Some(2),
+                    Some("completed"),
+                    0,
+                    0,
+                ),
+            ),
+            (
+                "terminal reason literal",
+                insert_state(
+                    "failed-bad-reason",
+                    "failed-bad-reason",
+                    "failed",
+                    1,
+                    None,
+                    None,
+                    Some(2),
+                    Some("other"),
+                    0,
+                    0,
+                ),
+            ),
+            (
+                "deadline required",
+                insert_state(
+                    "no-deadline",
+                    "no-deadline",
+                    "queued",
+                    0,
+                    None,
+                    None,
+                    None,
+                    None,
+                    0,
+                    0,
+                ),
+            ),
+            (
+                "deadline precedes creation",
+                insert_state(
+                    "early-deadline",
+                    "early-deadline",
+                    "queued",
+                    0,
+                    None,
+                    None,
+                    Some(1),
+                    None,
+                    2,
+                    2,
+                ),
+            ),
+            (
+                "updated precedes creation",
+                insert_state(
+                    "early-update",
+                    "early-update",
+                    "queued",
+                    0,
+                    None,
+                    None,
+                    Some(3),
+                    None,
+                    2,
+                    1,
+                ),
+            ),
+            (
+                "timestamp JCS upper bound",
+                insert_state(
+                    "time-over",
+                    "time-over",
+                    "queued",
+                    0,
+                    None,
+                    None,
+                    Some(9_007_199_254_740_992),
+                    None,
+                    0,
+                    0,
+                ),
+            ),
+            (
+                "idempotency key byte cap",
+                insert_state(
+                    "key-over",
+                    &"k".repeat(129),
+                    "queued",
+                    0,
+                    None,
+                    None,
+                    Some(2),
+                    None,
+                    0,
+                    0,
+                ),
+            ),
+            (
+                "idempotency key controls",
+                insert_state(
+                    "key-control",
+                    "key\ncontrol",
+                    "queued",
+                    0,
+                    None,
+                    None,
+                    Some(2),
+                    None,
+                    0,
+                    0,
+                ),
+            ),
+        ] {
+            assert!(result.is_err(), "{label} check unexpectedly passed");
+        }
+
+        for (id, reason) in [
+            ("failed-deadline", "deadline_expired"),
+            ("failed-attempt", "attempt_limit"),
+            ("failed-internal", "internal_failure"),
+        ] {
+            insert_state(
+                id,
+                id,
+                "failed",
+                16,
+                None,
+                None,
+                Some(2),
+                Some(reason),
+                0,
+                0,
+            )
+            .expect("insert exact failed terminal reason");
+        }
+        insert_state(
+            "interrupted-max",
+            "interrupted-max",
+            "interrupted",
+            15,
+            None,
+            None,
+            Some(2),
+            None,
+            0,
+            0,
+        )
+        .expect("insert max resumable interrupted attempt");
+        insert_state(
+            "succeeded-max",
+            "succeeded-max",
+            "succeeded",
+            16,
+            None,
+            None,
+            Some(2),
+            Some("completed"),
+            0,
+            0,
+        )
+        .expect("insert max succeeded attempt");
+        for attempt in [0_i64, 16_i64] {
+            let id = format!("cancelled-{attempt}");
+            insert_state(
+                &id,
+                &id,
+                "cancelled",
+                attempt,
+                None,
+                None,
+                Some(2),
+                Some("cancelled"),
+                0,
+                0,
+            )
+            .expect("insert cancelled attempt boundary");
+        }
+
+        insert_queued_job(tx, "frozen", "project", "frozen");
+        for sql in [
+            "UPDATE job_runs SET state = 'running', attempt = 1,
+                lease_owner = '00000000-0000-4000-8000-000000000001',
+                lease_expires_at_ms = 1, updated_at_ms = 1,
+                id = 'frozen-other' WHERE id = 'frozen'",
+            "UPDATE job_runs SET state = 'running', attempt = 1,
+                lease_owner = '00000000-0000-4000-8000-000000000001',
+                lease_expires_at_ms = 1, updated_at_ms = 1,
+                project_id = 'project-other' WHERE id = 'frozen'",
+            "UPDATE job_runs SET state = 'running', attempt = 1,
+                lease_owner = '00000000-0000-4000-8000-000000000001',
+                lease_expires_at_ms = 1, updated_at_ms = 1,
+                kind = 'other' WHERE id = 'frozen'",
+            "UPDATE job_runs SET state = 'running', attempt = 1,
+                lease_owner = '00000000-0000-4000-8000-000000000001',
+                lease_expires_at_ms = 1, updated_at_ms = 1,
+                idempotency_key = 'other' WHERE id = 'frozen'",
+            "UPDATE job_runs SET state = 'running', attempt = 1,
+                lease_owner = '00000000-0000-4000-8000-000000000001',
+                lease_expires_at_ms = 1, updated_at_ms = 1,
+                deadline_at_ms = 300001 WHERE id = 'frozen'",
+            "UPDATE job_runs SET state = 'running', attempt = 1,
+                lease_owner = '00000000-0000-4000-8000-000000000001',
+                lease_expires_at_ms = 1, updated_at_ms = 1,
+                budget_json = '{\"changed\":true}' WHERE id = 'frozen'",
+            "UPDATE job_runs SET state = 'running', attempt = 1,
+                lease_owner = '00000000-0000-4000-8000-000000000001',
+                lease_expires_at_ms = 1, updated_at_ms = 1,
+                input_json = '{\"changed\":true}' WHERE id = 'frozen'",
+            "UPDATE job_runs SET state = 'running', attempt = 1,
+                lease_owner = '00000000-0000-4000-8000-000000000001',
+                lease_expires_at_ms = 1, updated_at_ms = 1,
+                created_at_ms = 1 WHERE id = 'frozen'",
+        ] {
+            assert_sql_error_contains(tx.execute(sql, []), "frozen identity cannot change");
+        }
+        assert_sql_error_contains(
+            tx.execute("DELETE FROM job_runs WHERE id = 'frozen'", []),
+            "job_runs rows cannot be deleted",
+        );
+
+        insert_queued_job(tx, "queued-cancel", "project", "queued-cancel");
+        tx.execute(
+            "UPDATE job_runs SET state = 'cancelled', terminal_reason = 'cancelled',
+                    updated_at_ms = 1 WHERE id = 'queued-cancel'",
+            [],
+        )
+        .expect("queued to cancelled is legal");
+
+        insert_queued_job(tx, "running-checkpoint", "project", "running-checkpoint");
+        start_job(tx, "running-checkpoint", 1);
+        tx.execute(
+            "UPDATE job_runs SET checkpoint_json = '{\"step\":1}', updated_at_ms = 2
+             WHERE id = 'running-checkpoint'",
+            [],
+        )
+        .expect("running checkpoint update is legal");
+
+        insert_queued_job(tx, "recover", "project", "recover");
+        start_job(tx, "recover", 1);
+        tx.execute(
+            "UPDATE job_runs SET state = 'interrupted', lease_owner = NULL,
+                    lease_expires_at_ms = NULL, updated_at_ms = 2 WHERE id = 'recover'",
+            [],
+        )
+        .expect("running to interrupted is legal");
+        tx.execute(
+            "UPDATE job_runs SET state = 'running', attempt = 2,
+                    lease_owner = '00000000-0000-4000-8000-000000000002',
+                    lease_expires_at_ms = 60000, updated_at_ms = 3 WHERE id = 'recover'",
+            [],
+        )
+        .expect("interrupted to next running attempt is legal");
+
+        insert_queued_job(tx, "interrupt-cancel", "project", "interrupt-cancel");
+        start_job(tx, "interrupt-cancel", 1);
+        tx.execute(
+            "UPDATE job_runs SET state = 'interrupted', lease_owner = NULL,
+                    lease_expires_at_ms = NULL, updated_at_ms = 2
+             WHERE id = 'interrupt-cancel'",
+            [],
+        )
+        .expect("prepare interrupted cancellation");
+        tx.execute(
+            "UPDATE job_runs SET state = 'cancelled', terminal_reason = 'cancelled',
+                    updated_at_ms = 3 WHERE id = 'interrupt-cancel'",
+            [],
+        )
+        .expect("interrupted to cancelled is legal");
+
+        for (suffix, state, reason) in [
+            ("success", "succeeded", "completed"),
+            ("failure", "failed", "internal_failure"),
+            ("cancel", "cancelled", "cancelled"),
+        ] {
+            let id = format!("running-{suffix}");
+            insert_queued_job(tx, &id, "project", &id);
+            start_job(tx, &id, 1);
+            tx.execute(
+                "UPDATE job_runs SET state = ?2, lease_owner = NULL,
+                        lease_expires_at_ms = NULL, terminal_reason = ?3, updated_at_ms = 2
+                 WHERE id = ?1",
+                params![id, state, reason],
+            )
+            .expect("running terminal transition is legal");
+        }
+
+        insert_queued_job(
+            tx,
+            "illegal-queued-terminal",
+            "project",
+            "illegal-queued-terminal",
+        );
+        assert_sql_error_contains(
+            tx.execute(
+                "UPDATE job_runs SET state = 'succeeded', attempt = 1,
+                        terminal_reason = 'completed', updated_at_ms = 1
+                 WHERE id = 'illegal-queued-terminal'",
+                [],
+            ),
+            "illegal job_runs state transition",
+        );
+        insert_queued_job(
+            tx,
+            "illegal-attempt-jump",
+            "project",
+            "illegal-attempt-jump",
+        );
+        assert_sql_error_contains(
+            tx.execute(
+                "UPDATE job_runs SET state = 'running', attempt = 2,
+                        lease_owner = '00000000-0000-4000-8000-000000000001',
+                        lease_expires_at_ms = 1, updated_at_ms = 1
+                 WHERE id = 'illegal-attempt-jump'",
+                [],
+            ),
+            "illegal job_runs state transition",
+        );
+        insert_queued_job(
+            tx,
+            "illegal-interrupt-jump",
+            "project",
+            "illegal-interrupt-jump",
+        );
+        start_job(tx, "illegal-interrupt-jump", 1);
+        assert_sql_error_contains(
+            tx.execute(
+                "UPDATE job_runs SET state = 'interrupted', attempt = 2, lease_owner = NULL,
+                        lease_expires_at_ms = NULL, updated_at_ms = 2
+                 WHERE id = 'illegal-interrupt-jump'",
+                [],
+            ),
+            "illegal job_runs state transition",
+        );
+        insert_queued_job(tx, "illegal-resume-jump", "project", "illegal-resume-jump");
+        start_job(tx, "illegal-resume-jump", 1);
+        tx.execute(
+            "UPDATE job_runs SET state = 'interrupted', lease_owner = NULL,
+                    lease_expires_at_ms = NULL, updated_at_ms = 2
+             WHERE id = 'illegal-resume-jump'",
+            [],
+        )
+        .expect("prepare illegal resume jump");
+        assert_sql_error_contains(
+            tx.execute(
+                "UPDATE job_runs SET state = 'running', attempt = 3,
+                        lease_owner = '00000000-0000-4000-8000-000000000003',
+                        lease_expires_at_ms = 60000, updated_at_ms = 3
+                 WHERE id = 'illegal-resume-jump'",
+                [],
+            ),
+            "illegal job_runs state transition",
+        );
+        assert_sql_error_contains(
+            tx.execute(
+                "UPDATE job_runs SET updated_at_ms = 4 WHERE id = 'queued-cancel'",
+                [],
+            ),
+            "illegal job_runs state transition",
+        );
+        Ok(())
+    })
+    .expect("verify Task 6 job check/transition matrix");
+}
+
+#[test]
+fn task_six_authority_literals_attempt_identity_redaction_and_text_caps_fail_closed() {
+    let database = TestDatabase::new("task-six-authority-checks");
+    drop(migrated_store(&database));
+
+    with_raw_verifier_transaction(&database, |tx| {
+        insert_job_test_project(tx, "project");
+        insert_queued_job(tx, "job", "project", "job-key");
+
+        let insert_event = |id: &str,
+                            job_id: Option<&str>,
+                            outcome: &str,
+                            attempt: Option<i64>,
+                            source_name: &str,
+                            source_path: &str,
+                            key: &str,
+                            actor: &str,
+                            terminal_at_ms: i64| {
+            tx.execute(
+                "INSERT INTO ingest_events
+                    (id, project_id, job_id, outcome, attempt, source_name, source_path,
+                     idempotency_key, actor, terminal_at_ms, details_json)
+                 VALUES (?1, 'project', ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, '{}')",
+                params![
+                    id,
+                    job_id,
+                    outcome,
+                    attempt,
+                    source_name,
+                    source_path,
+                    key,
+                    actor,
+                    terminal_at_ms
+                ],
+            )
+        };
+
+        insert_event(
+            "authoritative",
+            Some("job"),
+            "interrupted",
+            Some(1),
+            "utf8:source.pdf",
+            "<redacted>",
+            "key",
+            "actor",
+            1,
+        )
+        .expect("insert authoritative attempt event");
+        assert!(
+            insert_event(
+                "duplicate-attempt",
+                Some("job"),
+                "accepted_new",
+                Some(1),
+                "utf8:source.pdf",
+                "<redacted>",
+                "key",
+                "actor",
+                1,
+            )
+            .is_err(),
+            "duplicate (job, attempt) authority was accepted"
+        );
+        for id in ["replay-one", "replay-two"] {
+            insert_event(
+                id,
+                Some("job"),
+                "idempotent_replay",
+                None,
+                "utf8:source.pdf",
+                "<redacted>",
+                "key",
+                "actor",
+                1,
+            )
+            .expect("nullable replay attempt remains non-authoritative");
+        }
+        insert_event(
+            "conflict",
+            Some("job"),
+            "denied_conflict",
+            None,
+            "utf8:source.pdf",
+            "<redacted>",
+            "key",
+            "actor",
+            1,
+        )
+        .expect("insert conflict observation with null attempt");
+
+        for (label, result) in [
+            (
+                "replay positive attempt",
+                insert_event(
+                    "replay-positive",
+                    Some("job"),
+                    "idempotent_replay",
+                    Some(2),
+                    "name",
+                    "<redacted>",
+                    "key",
+                    "actor",
+                    1,
+                ),
+            ),
+            (
+                "authoritative null attempt",
+                insert_event(
+                    "authority-null",
+                    Some("job"),
+                    "interrupted",
+                    None,
+                    "name",
+                    "<redacted>",
+                    "key",
+                    "actor",
+                    1,
+                ),
+            ),
+            (
+                "authoritative missing job",
+                insert_event(
+                    "authority-no-job",
+                    None,
+                    "interrupted",
+                    Some(2),
+                    "name",
+                    "<redacted>",
+                    "key",
+                    "actor",
+                    1,
+                ),
+            ),
+            (
+                "attempt upper bound",
+                insert_event(
+                    "attempt-over",
+                    Some("job"),
+                    "interrupted",
+                    Some(17),
+                    "name",
+                    "<redacted>",
+                    "key",
+                    "actor",
+                    1,
+                ),
+            ),
+            (
+                "outcome literal",
+                insert_event(
+                    "outcome-other",
+                    Some("job"),
+                    "accepted",
+                    Some(2),
+                    "name",
+                    "<redacted>",
+                    "key",
+                    "actor",
+                    1,
+                ),
+            ),
+            (
+                "raw source path",
+                insert_event(
+                    "raw-path",
+                    Some("job"),
+                    "idempotent_replay",
+                    None,
+                    "name",
+                    "/secret/path.pdf",
+                    "key",
+                    "actor",
+                    1,
+                ),
+            ),
+            (
+                "source-name byte cap",
+                insert_event(
+                    "source-over",
+                    Some("job"),
+                    "idempotent_replay",
+                    None,
+                    &"s".repeat(4097),
+                    "<redacted>",
+                    "key",
+                    "actor",
+                    1,
+                ),
+            ),
+            (
+                "idempotency byte cap",
+                insert_event(
+                    "event-key-over",
+                    Some("job"),
+                    "idempotent_replay",
+                    None,
+                    "name",
+                    "<redacted>",
+                    &"k".repeat(129),
+                    "actor",
+                    1,
+                ),
+            ),
+            (
+                "actor byte cap",
+                insert_event(
+                    "actor-over",
+                    Some("job"),
+                    "idempotent_replay",
+                    None,
+                    "name",
+                    "<redacted>",
+                    "key",
+                    &"a".repeat(129),
+                    1,
+                ),
+            ),
+            (
+                "actor control",
+                insert_event(
+                    "actor-control",
+                    Some("job"),
+                    "idempotent_replay",
+                    None,
+                    "name",
+                    "<redacted>",
+                    "key",
+                    "actor\n",
+                    1,
+                ),
+            ),
+            (
+                "timestamp upper bound",
+                insert_event(
+                    "event-time-over",
+                    Some("job"),
+                    "idempotent_replay",
+                    None,
+                    "name",
+                    "<redacted>",
+                    "key",
+                    "actor",
+                    9_007_199_254_740_992,
+                ),
+            ),
+        ] {
+            assert!(result.is_err(), "{label} unexpectedly passed");
+        }
+        insert_event(
+            "event-caps",
+            Some("job"),
+            "idempotent_replay",
+            None,
+            &"s".repeat(4096),
+            "<redacted>",
+            &"k".repeat(128),
+            &"a".repeat(128),
+            9_007_199_254_740_991,
+        )
+        .expect("insert event at every scalar cap");
+
+        tx.execute(
+            "INSERT INTO source_records
+                (id, project_id, source_name, source_path, metadata_json, created_at_ms)
+             VALUES ('redacted-source', 'project', 'name', '<redacted>', '{}', 0)",
+            [],
+        )
+        .expect("insert redacted source record");
+        assert!(
+            tx.execute(
+                "INSERT INTO source_records
+                    (id, project_id, source_name, source_path, metadata_json, created_at_ms)
+                 VALUES ('raw-source', 'project', 'name', '/raw/path', '{}', 0)",
+                [],
+            )
+            .is_err(),
+            "source record persisted a raw path"
+        );
+        tx.execute(
+            "INSERT INTO source_records
+                (id, project_id, source_name, source_path, metadata_json, created_at_ms)
+             VALUES ('source-caps', 'project', ?1, '<redacted>', '{}', ?2)",
+            params!["s".repeat(4096), 9_007_199_254_740_991_i64],
+        )
+        .expect("insert source record at safe-name/timestamp caps");
+        for (id, name, timestamp) in [
+            ("source-name-over", "s".repeat(4097), 0_i64),
+            ("source-name-control", "source\n".to_owned(), 0_i64),
+            (
+                "source-time-over",
+                "source".to_owned(),
+                9_007_199_254_740_992_i64,
+            ),
+        ] {
+            assert!(
+                tx.execute(
+                    "INSERT INTO source_records
+                        (id, project_id, source_name, source_path, metadata_json, created_at_ms)
+                     VALUES (?1, 'project', ?2, '<redacted>', '{}', ?3)",
+                    params![id, name, timestamp],
+                )
+                .is_err(),
+                "source-record scalar cap unexpectedly passed for {id}"
+            );
+        }
+
+        tx.execute(
+            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+             VALUES ('text-caps', ?1, 9007199254740991, ?2, 'PUBLIC')",
+            params!["é".repeat(128), "a".repeat(128)],
+        )
+        .expect("insert project at UTF-8/timestamp caps");
+        for (id, name, actor, timestamp) in [
+            (
+                "name-over",
+                format!("{}x", "é".repeat(128)),
+                "actor".to_owned(),
+                0_i64,
+            ),
+            (
+                "name-control",
+                "name\n".to_owned(),
+                "actor".to_owned(),
+                0_i64,
+            ),
+            ("creator-over", "name".to_owned(), "a".repeat(129), 0_i64),
+            (
+                "project-time-over",
+                "name".to_owned(),
+                "actor".to_owned(),
+                9_007_199_254_740_992_i64,
+            ),
+        ] {
+            assert!(
+                tx.execute(
+                    "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+                     VALUES (?1, ?2, ?3, ?4, 'PUBLIC')",
+                    params![id, name, timestamp, actor],
+                )
+                .is_err(),
+                "project scalar check unexpectedly accepted {id}"
+            );
+        }
+
+        for (digest, media_type) in [
+            ("1".repeat(64), "application/pdf"),
+            (
+                "2".repeat(64),
+                "application/vnd.heleos.evidence-manifest+json;version=1",
+            ),
+        ] {
+            tx.execute(
+                "INSERT INTO content_objects
+                    (sha256, byte_length, media_type, admission_state, vault_key,
+                     created_at_ms, created_by, quarantine_reason)
+                 VALUES (?1, 1, ?2, 'accepted', ?1, 0, 'actor', NULL)",
+                params![digest, media_type],
+            )
+            .expect("insert exact content media literal");
+        }
+        for (digest, media_type, state, reason) in [
+            ("3".repeat(64), "text/plain", "accepted", None),
+            ("4".repeat(64), "application/pdf", "accepted", Some("{}")),
+            ("5".repeat(64), "application/pdf", "quarantined", None),
+            (
+                "6".repeat(64),
+                "application/pdf",
+                "quarantined",
+                Some("{ }"),
+            ),
+        ] {
+            assert!(
+                tx.execute(
                     "INSERT INTO content_objects
+                        (sha256, byte_length, media_type, admission_state, vault_key,
+                         created_at_ms, created_by, quarantine_reason)
+                     VALUES (?1, 1, ?2, ?3, ?1, 0, 'actor', ?4)",
+                    params![digest, media_type, state, reason],
+                )
+                .is_err(),
+                "content authority check unexpectedly passed for {digest}"
+            );
+        }
+        Ok(())
+    })
+    .expect("verify Task 6 authority and scalar check matrix");
+}
+
+#[test]
+fn task_six_json_and_audit_scalar_caps_accept_n_and_reject_n_plus_one() {
+    let database = TestDatabase::new("task-six-byte-caps");
+    drop(migrated_store(&database));
+
+    let one_mib = canonical_json_string_with_exact_bytes(1024 * 1024);
+    let one_mib_plus_one = canonical_json_string_with_exact_bytes(1024 * 1024 + 1);
+    let eight_mib = canonical_json_string_with_exact_bytes(8 * 1024 * 1024);
+    let eight_mib_plus_one = canonical_json_string_with_exact_bytes(8 * 1024 * 1024 + 1);
+
+    with_raw_verifier_transaction(&database, |tx| {
+        insert_job_test_project(tx, "project");
+        insert_job_test_project(tx, "project-other");
+        tx.execute(
+            "INSERT INTO job_runs
+                (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
+                 budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
+             VALUES ('cap-job', 'project', 'pdf_ingest', 'cap-job', 'queued', 0, 1,
+                     ?1, ?1, ?2, 0, 0)",
+            params![&one_mib, &eight_mib],
+        )
+        .expect("insert job JSON at independent byte caps");
+        for (id, budget, input, checkpoint) in [
+            ("budget-over", one_mib_plus_one.as_str(), "{}", "{}"),
+            ("input-over", "{}", one_mib_plus_one.as_str(), "{}"),
+            ("checkpoint-over", "{}", "{}", eight_mib_plus_one.as_str()),
+        ] {
+            assert!(
+                tx.execute(
+                    "INSERT INTO job_runs
+                        (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
+                         budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
+                     VALUES (?1, 'project', 'pdf_ingest', ?1, 'queued', 0, 1,
+                             ?2, ?3, ?4, 0, 0)",
+                    params![id, budget, input, checkpoint],
+                )
+                .is_err(),
+                "job JSON N+1 cap unexpectedly passed for {id}"
+            );
+        }
+
+        tx.execute(
+            "INSERT INTO content_objects
+                (sha256, byte_length, media_type, admission_state, vault_key,
+                 created_at_ms, created_by, quarantine_reason)
+             VALUES (?1, 1, 'application/pdf', 'quarantined', ?1, 0, 'actor', ?2)",
+            params!["1".repeat(64), &one_mib],
+        )
+        .expect("insert quarantine JSON at byte cap");
+        assert!(
+            tx.execute(
+                "INSERT INTO content_objects
+                    (sha256, byte_length, media_type, admission_state, vault_key,
+                     created_at_ms, created_by, quarantine_reason)
+                 VALUES (?1, 1, 'application/pdf', 'quarantined', ?1, 0, 'actor', ?2)",
+                params!["2".repeat(64), &one_mib_plus_one],
+            )
+            .is_err(),
+            "quarantine JSON N+1 cap unexpectedly passed"
+        );
+
+        tx.execute(
+            "INSERT INTO ingest_events
+                (id, project_id, job_id, outcome, attempt, source_name, source_path,
+                 idempotency_key, actor, terminal_at_ms, details_json)
+             VALUES ('details-cap', 'project', 'cap-job', 'idempotent_replay', NULL,
+                     'name', '<redacted>', 'key', 'actor', 0, ?1)",
+            [&one_mib],
+        )
+        .expect("insert event details at byte cap");
+        assert!(
+            tx.execute(
+                "INSERT INTO ingest_events
+                    (id, project_id, job_id, outcome, attempt, source_name, source_path,
+                     idempotency_key, actor, terminal_at_ms, details_json)
+                 VALUES ('details-over', 'project', 'cap-job', 'idempotent_replay', NULL,
+                         'name', '<redacted>', 'key', 'actor', 0, ?1)",
+                [&one_mib_plus_one],
+            )
+            .is_err(),
+            "event details N+1 cap unexpectedly passed"
+        );
+        tx.execute(
+            "INSERT INTO source_records
+                (id, project_id, job_id, source_name, source_path, metadata_json, created_at_ms)
+             VALUES ('metadata-cap', 'project', 'cap-job', 'name', '<redacted>', ?1, 0)",
+            [&one_mib],
+        )
+        .expect("insert source metadata at byte cap");
+        assert!(
+            tx.execute(
+                "INSERT INTO source_records
+                    (id, project_id, job_id, source_name, source_path, metadata_json, created_at_ms)
+                 VALUES ('metadata-over', 'project', 'cap-job', 'name', '<redacted>', ?1, 0)",
+                [&one_mib_plus_one],
+            )
+            .is_err(),
+            "source metadata N+1 cap unexpectedly passed"
+        );
+
+        let original = "3".repeat(64);
+        let manifest = "4".repeat(64);
+        for (digest, media_type) in [
+            (&original, "application/pdf"),
+            (
+                &manifest,
+                "application/vnd.heleos.evidence-manifest+json;version=1",
+            ),
+        ] {
+            tx.execute(
+                "INSERT INTO content_objects
+                    (sha256, byte_length, media_type, admission_state, vault_key,
+                     created_at_ms, created_by)
+                 VALUES (?1, 1, ?2, 'accepted', ?1, 0, 'actor')",
+                params![digest, media_type],
+            )
+            .expect("insert accepted lineage content");
+        }
+        tx.execute(
+            "INSERT INTO documents (id, created_at_ms, created_by)
+             VALUES ('document', 0, 'actor')",
+            [],
+        )
+        .expect("insert cap-test document");
+        tx.execute(
+            "INSERT INTO document_revisions
+                (id, document_id, content_sha256, created_at_ms, created_by)
+             VALUES ('revision', 'document', ?1, 0, 'actor')",
+            [&original],
+        )
+        .expect("insert cap-test revision");
+        tx.execute(
+            "INSERT INTO sheets
+                (id, revision_id, zero_based_page_index, width_micropoints,
+                 height_micropoints, rotation_degrees, unit, parent_content_sha256,
+                 transform_json)
+             VALUES ('sheet-cap', 'revision', 0, 1, 1, 0, 'pt', ?1, ?2)",
+            params![&original, &one_mib],
+        )
+        .expect("insert sheet transform at byte cap");
+        assert!(
+            tx.execute(
+                "INSERT INTO sheets
+                    (id, revision_id, zero_based_page_index, width_micropoints,
+                     height_micropoints, rotation_degrees, unit, parent_content_sha256,
+                     transform_json)
+                 VALUES ('sheet-over', 'revision', 1, 1, 1, 0, 'pt', ?1, ?2)",
+                params![&original, &one_mib_plus_one],
+            )
+            .is_err(),
+            "sheet transform N+1 cap unexpectedly passed"
+        );
+        tx.execute(
+            "INSERT INTO evidence_objects
+                (id, project_id, job_id, document_revision_id, content_sha256,
+                 parent_content_sha256, extraction_method, parameters_json, review_state,
+                 created_at_ms)
+             VALUES ('evidence-cap', 'project', 'cap-job', 'revision', ?1, ?2,
+                     'heleos.pdf-probe/v1', ?3, 'accepted', 0)",
+            params![&manifest, &original, &one_mib],
+        )
+        .expect("insert evidence parameters at byte cap");
+        assert!(
+            tx.execute(
+                "INSERT INTO evidence_objects
+                    (id, project_id, job_id, document_revision_id, content_sha256,
+                     parent_content_sha256, extraction_method, parameters_json, review_state,
+                     created_at_ms)
+                 VALUES ('evidence-over', 'project-other', 'cap-job', 'revision', ?1, ?2,
+                         'heleos.pdf-probe/v1', ?3, 'accepted', 0)",
+                params![&manifest, &original, &one_mib_plus_one],
+            )
+            .is_err(),
+            "evidence parameters N+1 cap unexpectedly passed"
+        );
+        tx.execute(
+            "INSERT INTO corrections
+                (id, project_id, evidence_id, actor, reason, before_json, after_json,
+                 created_at_ms)
+             VALUES ('correction-cap', 'project', 'evidence-cap', 'actor', 'reason',
+                     ?1, ?1, 0)",
+            [&one_mib],
+        )
+        .expect("insert correction JSON at byte caps");
+        for (id, before, after) in [
+            ("correction-before-over", one_mib_plus_one.as_str(), "{}"),
+            ("correction-after-over", "{}", one_mib_plus_one.as_str()),
+        ] {
+            assert!(
+                tx.execute(
+                    "INSERT INTO corrections
+                        (id, project_id, evidence_id, actor, reason, before_json, after_json,
+                         created_at_ms)
+                     VALUES (?1, 'project', 'evidence-cap', 'actor', 'reason', ?2, ?3, 0)",
+                    params![id, before, after],
+                )
+                .is_err(),
+                "correction JSON N+1 cap unexpectedly passed for {id}"
+            );
+        }
+
+        let insert_audit = |id: &str,
+                            sequence: i64,
+                            action: &str,
+                            subject_type: &str,
+                            subject_id: &str,
+                            before: &str,
+                            after: &str,
+                            reason: &str,
+                            occurred_at_ms: i64,
+                            hash_byte: char| {
+            let digest = hash_byte.to_string().repeat(64);
+            tx.execute(
+                "INSERT INTO audit_events
+                    (id, sequence, project_id, actor, action, subject_type, subject_id,
+                     before_json, after_json, reason, occurred_at_ms, previous_hash, event_hash)
+                 VALUES (?1, ?2, 'project', 'actor', ?3, ?4, ?5, ?6, ?7, ?8, ?9,
+                         ?10, ?11)",
+                params![
+                    id,
+                    sequence,
+                    action,
+                    subject_type,
+                    subject_id,
+                    before,
+                    after,
+                    reason,
+                    occurred_at_ms,
+                    "0".repeat(64),
+                    digest
+                ],
+            )
+        };
+        insert_audit(
+            "audit-cap",
+            1,
+            "project_created",
+            "project",
+            &"s".repeat(256),
+            &one_mib,
+            &one_mib,
+            &"r".repeat(1024),
+            9_007_199_254_740_991,
+            'a',
+        )
+        .expect("insert audit row at scalar/document caps");
+        for (label, result) in [
+            (
+                "before N+1",
+                insert_audit(
+                    "audit-before-over",
+                    2,
+                    "project_created",
+                    "project",
+                    "subject",
+                    &one_mib_plus_one,
+                    "{}",
+                    "reason",
+                    0,
+                    'b',
+                ),
+            ),
+            (
+                "after N+1",
+                insert_audit(
+                    "audit-after-over",
+                    3,
+                    "project_created",
+                    "project",
+                    "subject",
+                    "{}",
+                    &one_mib_plus_one,
+                    "reason",
+                    0,
+                    'c',
+                ),
+            ),
+            (
+                "subject N+1",
+                insert_audit(
+                    "audit-subject-over",
+                    4,
+                    "project_created",
+                    "project",
+                    &"s".repeat(257),
+                    "{}",
+                    "{}",
+                    "reason",
+                    0,
+                    'd',
+                ),
+            ),
+            (
+                "subject control",
+                insert_audit(
+                    "audit-subject-control",
+                    5,
+                    "project_created",
+                    "project",
+                    "subject\n",
+                    "{}",
+                    "{}",
+                    "reason",
+                    0,
+                    'e',
+                ),
+            ),
+            (
+                "reason N+1",
+                insert_audit(
+                    "audit-reason-over",
+                    6,
+                    "project_created",
+                    "project",
+                    "subject",
+                    "{}",
+                    "{}",
+                    &"r".repeat(1025),
+                    0,
+                    'f',
+                ),
+            ),
+            (
+                "reason hostile control",
+                insert_audit(
+                    "audit-reason-control",
+                    7,
+                    "project_created",
+                    "project",
+                    "subject",
+                    "{}",
+                    "{}",
+                    "reason\u{001b}",
+                    0,
+                    '1',
+                ),
+            ),
+            (
+                "action literal",
+                insert_audit(
+                    "audit-action",
+                    8,
+                    "other",
+                    "project",
+                    "subject",
+                    "{}",
+                    "{}",
+                    "reason",
+                    0,
+                    '2',
+                ),
+            ),
+            (
+                "subject-type literal",
+                insert_audit(
+                    "audit-subject-type",
+                    9,
+                    "project_created",
+                    "other",
+                    "subject",
+                    "{}",
+                    "{}",
+                    "reason",
+                    0,
+                    '3',
+                ),
+            ),
+            (
+                "sequence zero",
+                insert_audit(
+                    "audit-sequence-zero",
+                    0,
+                    "project_created",
+                    "project",
+                    "subject",
+                    "{}",
+                    "{}",
+                    "reason",
+                    0,
+                    '4',
+                ),
+            ),
+            (
+                "sequence N+1",
+                insert_audit(
+                    "audit-sequence-over",
+                    9_007_199_254_740_992,
+                    "project_created",
+                    "project",
+                    "subject",
+                    "{}",
+                    "{}",
+                    "reason",
+                    0,
+                    '6',
+                ),
+            ),
+            (
+                "timestamp N+1",
+                insert_audit(
+                    "audit-time-over",
+                    10,
+                    "project_created",
+                    "project",
+                    "subject",
+                    "{}",
+                    "{}",
+                    "reason",
+                    9_007_199_254_740_992,
+                    '5',
+                ),
+            ),
+        ] {
+            assert!(result.is_err(), "audit {label} check unexpectedly passed");
+        }
+        tx.execute(
+            "INSERT INTO audit_events
+                (id, sequence, project_id, actor, action, subject_type, subject_id,
+                 before_json, after_json, reason, occurred_at_ms, previous_hash, event_hash)
+             VALUES ('audit-actor-cap', 9007199254740991, 'project', ?1,
+                     'project_created', 'project', 'subject', '{}', '{}', 'reason\nline', 0,
+                     ?2, ?3)",
+            params!["a".repeat(128), "0".repeat(64), "6".repeat(64)],
+        )
+        .expect("insert audit actor/sequence and ordinary-whitespace caps");
+        for (id, actor, hash) in [
+            ("audit-actor-over", "a".repeat(129), "7".repeat(64)),
+            ("audit-actor-control", "actor\n".to_owned(), "8".repeat(64)),
+        ] {
+            assert!(
+                tx.execute(
+                    "INSERT INTO audit_events
+                        (id, sequence, project_id, actor, action, subject_type, subject_id,
+                         before_json, after_json, reason, occurred_at_ms, previous_hash,
+                         event_hash)
+                     VALUES (?1, 11, 'project', ?2, 'project_created', 'project', 'subject',
+                             '{}', '{}', 'reason', 0, ?3, ?4)",
+                    params![id, actor, "0".repeat(64), hash],
+                )
+                .is_err(),
+                "audit actor cap unexpectedly passed for {id}"
+            );
+        }
+        Ok(())
+    })
+    .expect("verify Task 6 JSON/audit N/N+1 caps");
+}
+
+#[test]
+fn declared_uniqueness_foreign_keys_and_delete_actions_are_independently_enforced() {
+    let database = TestDatabase::new("constraint-matrix");
+    drop(migrated_store(&database));
+    insert_complete_fixture(&database, "collision");
+
+    with_raw_verifier_transaction(&database, |tx| {
+        let uniqueness_collisions = [
+            (
+                "content object sha256",
+                "INSERT INTO content_objects
                         (sha256, byte_length, admission_state, vault_key, created_at_ms,
                          created_by, quarantine_reason)
                      VALUES (
                         'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                         99, 'accepted', 'different-vault-key', 1, 'actor', NULL)",
-                ),
-                (
-                    "document revision content",
-                    "INSERT INTO document_revisions
+            ),
+            (
+                "document revision content",
+                "INSERT INTO document_revisions
                         (id, document_id, content_sha256, created_at_ms, created_by)
                      VALUES ('revision-2', 'document-1',
                         'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                         1, 'actor')",
-                ),
-                (
-                    "project document pair",
-                    "INSERT INTO project_documents
+            ),
+            (
+                "project document pair",
+                "INSERT INTO project_documents
                         (project_id, document_id, linked_at_ms, linked_by)
                      VALUES ('project-1', 'document-1', 1, 'actor')",
-                ),
-                (
-                    "sheet revision page",
-                    "INSERT INTO sheets
+            ),
+            (
+                "sheet revision page",
+                "INSERT INTO sheets
                         (id, revision_id, zero_based_page_index, width_micropoints,
                          height_micropoints, rotation_degrees, unit, parent_content_sha256,
                          transform_json)
                      VALUES ('sheet-2', 'revision-1', 0, 1, 1, 0, 'pt',
                         'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                         '{}')",
-                ),
-                (
-                    "job idempotency tuple",
-                    "INSERT INTO job_runs
-                        (id, project_id, kind, idempotency_key, state, attempt, budget_json,
-                         input_json, checkpoint_json, created_at_ms, updated_at_ms)
+            ),
+            (
+                "job idempotency tuple",
+                "INSERT INTO job_runs
+                        (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
+                         budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
                      VALUES ('job-2', 'project-1', 'pdf_ingest', 'collision', 'queued', 0,
-                        '{}', '{}', '{}', 1, 1)",
-                ),
-                (
-                    "evidence identity tuple",
-                    "INSERT INTO evidence_objects
+                        100, '{}', '{}', '{}', 1, 1)",
+            ),
+            (
+                "evidence identity tuple",
+                "INSERT INTO evidence_objects
                         (id, project_id, job_id, document_revision_id, content_sha256,
                          parent_content_sha256, extraction_method, parameters_json, review_state,
                          created_at_ms)
                      VALUES ('evidence-2', 'project-1', 'job-1', 'revision-1',
                         'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                         'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                         'fixture', '{}', 'unreviewed', 1)",
-                ),
-                (
-                    "audit sequence",
-                    "INSERT INTO audit_events
+            ),
+            (
+                "audit sequence",
+                "INSERT INTO audit_events
                         (id, sequence, project_id, actor, action, subject_type, subject_id,
                          before_json, after_json, reason, occurred_at_ms, previous_hash,
                          event_hash)
-                     VALUES ('audit-2', 1, 'project-1', 'actor', 'action', 'project',
+                     VALUES ('audit-2', 1, 'project-1', 'actor', 'project_created', 'project',
                         'project-1', '{}', '{}', 'reason', 1,
                         '0000000000000000000000000000000000000000000000000000000000000000',
                         'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc')",
-                ),
-                (
-                    "audit event hash",
-                    "INSERT INTO audit_events
+            ),
+            (
+                "audit event hash",
+                "INSERT INTO audit_events
                         (id, sequence, project_id, actor, action, subject_type, subject_id,
                          before_json, after_json, reason, occurred_at_ms, previous_hash,
                          event_hash)
-                     VALUES ('audit-3', 2, 'project-1', 'actor', 'action', 'project',
+                     VALUES ('audit-3', 2, 'project-1', 'actor', 'project_created', 'project',
                         'project-1', '{}', '{}', 'reason', 1,
                         '0000000000000000000000000000000000000000000000000000000000000000',
                         'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')",
-                ),
-            ];
-            for (label, sql) in uniqueness_collisions {
-                assert!(tx.execute(sql, []).is_err(), "accepted {label} collision");
-            }
+            ),
+        ];
+        for (label, sql) in uniqueness_collisions {
+            assert!(tx.execute(sql, []).is_err(), "accepted {label} collision");
+        }
 
-            let mut actual_foreign_keys = Vec::new();
-            for child in TABLES {
-                let mut statement = tx
-                    .prepare(
-                        "SELECT \"from\", \"table\", \"to\", on_delete
+        let mut actual_foreign_keys = Vec::new();
+        for child in TABLES {
+            let mut statement = tx
+                .prepare(
+                    "SELECT \"from\", \"table\", \"to\", on_delete
                          FROM pragma_foreign_key_list(?1)",
-                    )
-                    .expect("prepare foreign-key introspection");
-                let rows = statement
-                    .query_map([child], |row| {
-                        Ok((
-                            child.to_owned(),
-                            row.get::<_, String>(0)?,
-                            row.get::<_, String>(1)?,
-                            row.get::<_, String>(2)?,
-                            row.get::<_, String>(3)?,
-                        ))
-                    })
-                    .expect("query foreign-key declarations");
-                actual_foreign_keys.extend(
-                    rows.collect::<rusqlite::Result<Vec<_>>>()
-                        .expect("collect foreign-key declarations"),
-                );
-            }
-            actual_foreign_keys.sort();
-            let mut expected_foreign_keys = vec![
-                ("audit_events", "project_id", "projects", "id", "RESTRICT"),
-                (
-                    "corrections",
-                    "evidence_id",
-                    "evidence_objects",
-                    "id",
-                    "RESTRICT",
-                ),
-                ("corrections", "project_id", "projects", "id", "RESTRICT"),
-                (
-                    "document_revisions",
-                    "content_sha256",
-                    "content_objects",
-                    "sha256",
-                    "RESTRICT",
-                ),
-                (
-                    "document_revisions",
-                    "document_id",
-                    "documents",
-                    "id",
-                    "RESTRICT",
-                ),
-                (
-                    "evidence_objects",
-                    "content_sha256",
-                    "content_objects",
-                    "sha256",
-                    "RESTRICT",
-                ),
-                (
-                    "evidence_objects",
-                    "document_revision_id",
-                    "document_revisions",
-                    "id",
-                    "RESTRICT",
-                ),
-                ("evidence_objects", "job_id", "job_runs", "id", "RESTRICT"),
-                (
-                    "evidence_objects",
-                    "parent_content_sha256",
-                    "content_objects",
-                    "sha256",
-                    "RESTRICT",
-                ),
-                (
-                    "evidence_objects",
-                    "project_id",
-                    "projects",
-                    "id",
-                    "RESTRICT",
-                ),
-                (
-                    "ingest_events",
-                    "content_sha256",
-                    "content_objects",
-                    "sha256",
-                    "RESTRICT",
-                ),
-                ("ingest_events", "job_id", "job_runs", "id", "RESTRICT"),
-                ("ingest_events", "project_id", "projects", "id", "RESTRICT"),
-                ("job_runs", "project_id", "projects", "id", "RESTRICT"),
-                (
-                    "project_documents",
-                    "document_id",
-                    "documents",
-                    "id",
-                    "RESTRICT",
-                ),
-                (
-                    "project_documents",
-                    "project_id",
-                    "projects",
-                    "id",
-                    "CASCADE",
-                ),
-                ("scales", "sheet_id", "sheets", "id", "RESTRICT"),
-                (
-                    "sheets",
-                    "parent_content_sha256",
-                    "content_objects",
-                    "sha256",
-                    "RESTRICT",
-                ),
-                (
-                    "sheets",
-                    "revision_id",
-                    "document_revisions",
-                    "id",
-                    "RESTRICT",
-                ),
-                (
-                    "source_records",
-                    "content_sha256",
-                    "content_objects",
-                    "sha256",
-                    "RESTRICT",
-                ),
-                ("source_records", "job_id", "job_runs", "id", "RESTRICT"),
-                ("source_records", "project_id", "projects", "id", "RESTRICT"),
-            ];
-            expected_foreign_keys.sort();
-            assert_eq!(
-                actual_foreign_keys,
-                expected_foreign_keys
-                    .into_iter()
-                    .map(|(child, from, parent, to, on_delete)| (
+                )
+                .expect("prepare foreign-key introspection");
+            let rows = statement
+                .query_map([child], |row| {
+                    Ok((
                         child.to_owned(),
-                        from.to_owned(),
-                        parent.to_owned(),
-                        to.to_owned(),
-                        on_delete.to_owned(),
+                        row.get::<_, String>(0)?,
+                        row.get::<_, String>(1)?,
+                        row.get::<_, String>(2)?,
+                        row.get::<_, String>(3)?,
                     ))
-                    .collect::<Vec<_>>()
+                })
+                .expect("query foreign-key declarations");
+            actual_foreign_keys.extend(
+                rows.collect::<rusqlite::Result<Vec<_>>>()
+                    .expect("collect foreign-key declarations"),
             );
+        }
+        actual_foreign_keys.sort();
+        let mut expected_foreign_keys = vec![
+            ("audit_events", "project_id", "projects", "id", "RESTRICT"),
+            (
+                "corrections",
+                "evidence_id",
+                "evidence_objects",
+                "id",
+                "RESTRICT",
+            ),
+            ("corrections", "project_id", "projects", "id", "RESTRICT"),
+            (
+                "document_revisions",
+                "content_sha256",
+                "content_objects",
+                "sha256",
+                "RESTRICT",
+            ),
+            (
+                "document_revisions",
+                "document_id",
+                "documents",
+                "id",
+                "RESTRICT",
+            ),
+            (
+                "evidence_objects",
+                "content_sha256",
+                "content_objects",
+                "sha256",
+                "RESTRICT",
+            ),
+            (
+                "evidence_objects",
+                "document_revision_id",
+                "document_revisions",
+                "id",
+                "RESTRICT",
+            ),
+            ("evidence_objects", "job_id", "job_runs", "id", "RESTRICT"),
+            (
+                "evidence_objects",
+                "parent_content_sha256",
+                "content_objects",
+                "sha256",
+                "RESTRICT",
+            ),
+            (
+                "evidence_objects",
+                "project_id",
+                "projects",
+                "id",
+                "RESTRICT",
+            ),
+            (
+                "ingest_events",
+                "content_sha256",
+                "content_objects",
+                "sha256",
+                "RESTRICT",
+            ),
+            ("ingest_events", "job_id", "job_runs", "id", "RESTRICT"),
+            ("ingest_events", "project_id", "projects", "id", "RESTRICT"),
+            ("job_runs", "project_id", "projects", "id", "RESTRICT"),
+            (
+                "project_documents",
+                "document_id",
+                "documents",
+                "id",
+                "RESTRICT",
+            ),
+            (
+                "project_documents",
+                "project_id",
+                "projects",
+                "id",
+                "CASCADE",
+            ),
+            ("scales", "sheet_id", "sheets", "id", "RESTRICT"),
+            (
+                "sheets",
+                "parent_content_sha256",
+                "content_objects",
+                "sha256",
+                "RESTRICT",
+            ),
+            (
+                "sheets",
+                "revision_id",
+                "document_revisions",
+                "id",
+                "RESTRICT",
+            ),
+            (
+                "source_records",
+                "content_sha256",
+                "content_objects",
+                "sha256",
+                "RESTRICT",
+            ),
+            ("source_records", "job_id", "job_runs", "id", "RESTRICT"),
+            ("source_records", "project_id", "projects", "id", "RESTRICT"),
+        ];
+        expected_foreign_keys.sort();
+        assert_eq!(
+            actual_foreign_keys,
+            expected_foreign_keys
+                .into_iter()
+                .map(|(child, from, parent, to, on_delete)| (
+                    child.to_owned(),
+                    from.to_owned(),
+                    parent.to_owned(),
+                    to.to_owned(),
+                    on_delete.to_owned(),
+                ))
+                .collect::<Vec<_>>()
+        );
 
-            tx.execute(
-                "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+        tx.execute(
+            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                  VALUES ('cascade-project', 'name', 1, 'actor', 'INTERNAL')",
-                [],
-            )
-            .expect("insert cascade project");
-            tx.execute(
-                "INSERT INTO documents (id, created_at_ms, created_by)
+            [],
+        )
+        .expect("insert cascade project");
+        tx.execute(
+            "INSERT INTO documents (id, created_at_ms, created_by)
                  VALUES ('cascade-document', 1, 'actor')",
-                [],
-            )
-            .expect("insert cascade document");
-            tx.execute(
-                "INSERT INTO project_documents
+            [],
+        )
+        .expect("insert cascade document");
+        tx.execute(
+            "INSERT INTO project_documents
                     (project_id, document_id, linked_at_ms, linked_by)
                  VALUES ('cascade-project', 'cascade-document', 1, 'actor')",
+            [],
+        )
+        .expect("insert cascade link");
+        assert!(
+            tx.execute("DELETE FROM documents WHERE id = 'cascade-document'", [],)
+                .is_err(),
+            "document-side RESTRICT was not enforced"
+        );
+        tx.execute("DELETE FROM projects WHERE id = 'cascade-project'", [])
+            .expect("project-side CASCADE delete");
+        assert_eq!(
+            tx.query_row(
+                "SELECT count(*) FROM project_documents
+                     WHERE document_id = 'cascade-document'",
                 [],
+                |row| row.get::<_, i64>(0),
             )
-            .expect("insert cascade link");
-            assert!(
-                tx.execute("DELETE FROM documents WHERE id = 'cascade-document'", [],)
-                    .is_err(),
-                "document-side RESTRICT was not enforced"
-            );
-            tx.execute("DELETE FROM projects WHERE id = 'cascade-project'", [])
-                .expect("project-side CASCADE delete");
-            assert_eq!(
-                tx.query_row(
-                    "SELECT count(*) FROM project_documents
-                     WHERE document_id = 'cascade-document'",
-                    [],
-                    |row| row.get::<_, i64>(0),
-                )
-                .expect("count cascaded links"),
-                0
-            );
-            assert!(
-                tx.execute("DELETE FROM projects WHERE id = 'project-1'", [])
-                    .is_err(),
-                "dependent project RESTRICT actions were not enforced"
-            );
-            Ok(())
-        })
-        .expect("verify uniqueness, foreign-key, and delete matrix");
+            .expect("count cascaded links"),
+            0
+        );
+        assert!(
+            tx.execute("DELETE FROM projects WHERE id = 'project-1'", [])
+                .is_err(),
+            "dependent project RESTRICT actions were not enforced"
+        );
+        Ok(())
+    })
+    .expect("verify uniqueness, foreign-key, and delete matrix");
 }
 
 #[test]
 fn every_json_column_rejects_valid_but_non_jcs_text_at_raw_transaction_boundary() {
     let database = TestDatabase::new("jcs-boundary");
-    let mut store = migrated_store(&database);
+    drop(migrated_store(&database));
 
-    store
-        .with_immediate_transaction(|tx| {
-            tx.execute(
-                "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+    with_raw_verifier_transaction(&database, |tx| {
+        tx.execute(
+            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                  VALUES (?1, ?2, ?3, ?4, ?5)",
-                params!["jcs-project", "name", 1_i64, "actor", "INTERNAL"],
-            )
-            .expect("insert project prerequisite");
-            tx.execute(
-                "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+            params!["jcs-project", "name", 1_i64, "actor", "INTERNAL"],
+        )
+        .expect("insert project prerequisite");
+        tx.execute(
+            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                  VALUES (?1, ?2, ?3, ?4, ?5)",
-                params!["jcs-project-2", "name", 1_i64, "actor", "INTERNAL"],
-            )
-            .expect("insert independent project prerequisite");
-            tx.execute(
-                "INSERT INTO content_objects
+            params!["jcs-project-2", "name", 1_i64, "actor", "INTERNAL"],
+        )
+        .expect("insert independent project prerequisite");
+        tx.execute(
+            "INSERT INTO content_objects
                     (sha256, byte_length, admission_state, vault_key, created_at_ms, created_by)
                  VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
-                params!["1".repeat(64), 1_i64, "accepted", "key", 1_i64, "actor"],
-            )
-            .expect("insert content prerequisite");
+            params!["1".repeat(64), 1_i64, "accepted", "key", 1_i64, "actor"],
+        )
+        .expect("insert content prerequisite");
+        tx.execute(
+            "INSERT INTO documents (id, created_at_ms, created_by) VALUES (?1, ?2, ?3)",
+            params!["jcs-document", 1_i64, "actor"],
+        )
+        .expect("insert document prerequisite");
+        tx.execute(
+            "INSERT INTO document_revisions
+                    (id, document_id, content_sha256, created_at_ms, created_by)
+                 VALUES (?1, ?2, ?3, ?4, ?5)",
+            params![
+                "jcs-revision",
+                "jcs-document",
+                "1".repeat(64),
+                1_i64,
+                "actor"
+            ],
+        )
+        .expect("insert revision prerequisite");
+        tx.execute(
+            "INSERT INTO job_runs
+                    (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
+                     budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
+                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
+            params![
+                "jcs-job",
+                "jcs-project",
+                "pdf_ingest",
+                "canonical",
+                "queued",
+                0_i64,
+                100_i64,
+                "{}",
+                "{}",
+                "{}",
+                1_i64,
+                1_i64
+            ],
+        )
+        .expect("insert job prerequisite");
+        tx.execute(
+            "INSERT INTO evidence_objects
+                    (id, project_id, job_id, document_revision_id, content_sha256,
+                     parent_content_sha256, extraction_method, parameters_json, review_state,
+                     created_at_ms)
+                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
+            params![
+                "jcs-evidence",
+                "jcs-project",
+                "jcs-job",
+                "jcs-revision",
+                "1".repeat(64),
+                "1".repeat(64),
+                "fixture",
+                "{}",
+                "unreviewed",
+                1_i64
+            ],
+        )
+        .expect("insert evidence prerequisite");
+
+        for (id, key, budget, input, checkpoint) in [
+            ("bad-budget", "bad-budget", "{ }", "{}", "{}"),
+            ("bad-input", "bad-input", "{}", "{\"b\":2,\"a\":1}", "{}"),
+            (
+                "bad-checkpoint",
+                "bad-checkpoint",
+                "{}",
+                "{}",
+                "{\"n\":1.0}",
+            ),
+        ] {
+            assert!(
+                tx.execute(
+                    "INSERT INTO job_runs
+                            (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
+                             budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
+                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
+                    params![
+                        id,
+                        "jcs-project",
+                        "pdf_ingest",
+                        key,
+                        "queued",
+                        0_i64,
+                        100_i64,
+                        budget,
+                        input,
+                        checkpoint,
+                        1_i64,
+                        1_i64
+                    ],
+                )
+                .is_err(),
+                "non-JCS job JSON was accepted for {id}"
+            );
+        }
+
+        assert!(
             tx.execute(
-                "INSERT INTO documents (id, created_at_ms, created_by) VALUES (?1, ?2, ?3)",
-                params!["jcs-document", 1_i64, "actor"],
+                "INSERT INTO ingest_events
+                        (id, project_id, job_id, outcome, attempt, source_name, source_path,
+                         idempotency_key, actor, terminal_at_ms, details_json)
+                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, '<redacted>', ?7, ?8, ?9, ?10)",
+                params![
+                    "bad-details",
+                    "jcs-project",
+                    "jcs-job",
+                    "interrupted",
+                    1_i64,
+                    "name",
+                    "key",
+                    "actor",
+                    1_i64,
+                    "{\"text\":\"\\u0061\"}"
+                ],
             )
-            .expect("insert document prerequisite");
+            .is_err()
+        );
+        assert!(
             tx.execute(
-                "INSERT INTO document_revisions
-                    (id, document_id, content_sha256, created_at_ms, created_by)
-                 VALUES (?1, ?2, ?3, ?4, ?5)",
+                "INSERT INTO sheets
+                        (id, revision_id, zero_based_page_index, width_micropoints,
+                         height_micropoints, rotation_degrees, unit, parent_content_sha256,
+                         transform_json)
+                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                 params![
+                    "bad-transform",
                     "jcs-revision",
-                    "jcs-document",
-                    "1".repeat(64),
+                    0_i64,
                     1_i64,
-                    "actor"
+                    1_i64,
+                    0_i64,
+                    "pt",
+                    "1".repeat(64),
+                    "{\"\u{e000}\":1,\"\u{10000}\":2}"
                 ],
             )
-            .expect("insert revision prerequisite");
+            .is_err()
+        );
+        assert!(
             tx.execute(
-                "INSERT INTO job_runs
-                    (id, project_id, kind, idempotency_key, state, attempt, budget_json,
-                     input_json, checkpoint_json, created_at_ms, updated_at_ms)
-                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
+                "INSERT INTO source_records
+                        (id, project_id, job_id, source_name, source_path, content_sha256,
+                         metadata_json, created_at_ms)
+                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                 params![
-                    "jcs-job",
+                    "bad-metadata",
                     "jcs-project",
-                    "pdf_ingest",
-                    "canonical",
-                    "queued",
-                    0_i64,
-                    "{}",
-                    "{}",
-                    "{}",
-                    1_i64,
+                    "jcs-job",
+                    "name",
+                    "<redacted>",
+                    "1".repeat(64),
+                    "{\"x\" :1}",
                     1_i64
                 ],
             )
-            .expect("insert job prerequisite");
+            .is_err()
+        );
+        assert!(
             tx.execute(
                 "INSERT INTO evidence_objects
-                    (id, project_id, job_id, document_revision_id, content_sha256,
-                     parent_content_sha256, extraction_method, parameters_json, review_state,
-                     created_at_ms)
-                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
+                        (id, project_id, job_id, document_revision_id, content_sha256,
+                         parent_content_sha256, extraction_method, parameters_json, review_state,
+                         created_at_ms)
+                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                 params![
-                    "jcs-evidence",
-                    "jcs-project",
+                    "bad-parameters",
+                    "jcs-project-2",
                     "jcs-job",
                     "jcs-revision",
                     "1".repeat(64),
                     "1".repeat(64),
                     "fixture",
-                    "{}",
+                    "{\"n\":1e0}",
                     "unreviewed",
                     1_i64
                 ],
             )
-            .expect("insert evidence prerequisite");
-
-            for (id, key, budget, input, checkpoint) in [
-                ("bad-budget", "bad-budget", "{ }", "{}", "{}"),
-                ("bad-input", "bad-input", "{}", "{\"b\":2,\"a\":1}", "{}"),
-                (
-                    "bad-checkpoint",
-                    "bad-checkpoint",
-                    "{}",
-                    "{}",
-                    "{\"n\":1.0}",
-                ),
-            ] {
-                assert!(
-                    tx.execute(
-                        "INSERT INTO job_runs
-                            (id, project_id, kind, idempotency_key, state, attempt, budget_json,
-                             input_json, checkpoint_json, created_at_ms, updated_at_ms)
-                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
-                        params![
-                            id,
-                            "jcs-project",
-                            "pdf_ingest",
-                            key,
-                            "queued",
-                            0_i64,
-                            budget,
-                            input,
-                            checkpoint,
-                            1_i64,
-                            1_i64
-                        ],
-                    )
-                    .is_err(),
-                    "non-JCS job JSON was accepted for {id}"
-                );
-            }
-
+            .is_err()
+        );
+        for (id, before, after) in [
+            ("bad-correction-before", "[] ", "[]"),
+            ("bad-correction-after", "[]", "{\"x\":\"\\/\"}"),
+        ] {
             assert!(
                 tx.execute(
-                    "INSERT INTO ingest_events
-                        (id, project_id, job_id, outcome, source_name, source_path,
-                         idempotency_key, actor, terminal_at_ms, details_json)
-                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
+                    "INSERT INTO corrections
+                            (id, project_id, evidence_id, actor, reason, before_json, after_json,
+                             created_at_ms)
+                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                     params![
-                        "bad-details",
+                        id,
                         "jcs-project",
-                        "jcs-job",
-                        "interrupted",
-                        "name",
-                        "path",
-                        "key",
+                        "jcs-evidence",
                         "actor",
-                        1_i64,
-                        "{\"text\":\"\\u0061\"}"
-                    ],
-                )
-                .is_err()
-            );
-            assert!(
-                tx.execute(
-                    "INSERT INTO sheets
-                        (id, revision_id, zero_based_page_index, width_micropoints,
-                         height_micropoints, rotation_degrees, unit, parent_content_sha256,
-                         transform_json)
-                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
-                    params![
-                        "bad-transform",
-                        "jcs-revision",
-                        0_i64,
-                        1_i64,
-                        1_i64,
-                        0_i64,
-                        "pt",
-                        "1".repeat(64),
-                        "{\"\u{e000}\":1,\"\u{10000}\":2}"
-                    ],
-                )
-                .is_err()
-            );
-            assert!(
-                tx.execute(
-                    "INSERT INTO source_records
-                        (id, project_id, job_id, source_name, source_path, content_sha256,
-                         metadata_json, created_at_ms)
-                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
-                    params![
-                        "bad-metadata",
-                        "jcs-project",
-                        "jcs-job",
-                        "name",
-                        "path",
-                        "1".repeat(64),
-                        "{\"x\" :1}",
+                        "reason",
+                        before,
+                        after,
                         1_i64
                     ],
                 )
-                .is_err()
+                .is_err(),
+                "non-JCS correction JSON was accepted for {id}"
             );
+        }
+        for (id, sequence, before, after, hash) in [
+            (
+                "bad-audit-before",
+                1_i64,
+                "{\"x\":-0}",
+                "{}",
+                "2".repeat(64),
+            ),
+            (
+                "bad-audit-after",
+                2_i64,
+                "{}",
+                "{\"x\":1.00}",
+                "3".repeat(64),
+            ),
+        ] {
             assert!(
                 tx.execute(
-                    "INSERT INTO evidence_objects
-                        (id, project_id, job_id, document_revision_id, content_sha256,
-                         parent_content_sha256, extraction_method, parameters_json, review_state,
-                         created_at_ms)
-                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
-                    params![
-                        "bad-parameters",
-                        "jcs-project-2",
-                        "jcs-job",
-                        "jcs-revision",
-                        "1".repeat(64),
-                        "1".repeat(64),
-                        "fixture",
-                        "{\"n\":1e0}",
-                        "unreviewed",
-                        1_i64
-                    ],
-                )
-                .is_err()
-            );
-            for (id, before, after) in [
-                ("bad-correction-before", "[] ", "[]"),
-                ("bad-correction-after", "[]", "{\"x\":\"\\/\"}"),
-            ] {
-                assert!(
-                    tx.execute(
-                        "INSERT INTO corrections
-                            (id, project_id, evidence_id, actor, reason, before_json, after_json,
-                             created_at_ms)
-                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
-                        params![
-                            id,
-                            "jcs-project",
-                            "jcs-evidence",
-                            "actor",
-                            "reason",
-                            before,
-                            after,
-                            1_i64
-                        ],
-                    )
-                    .is_err(),
-                    "non-JCS correction JSON was accepted for {id}"
-                );
-            }
-            for (id, sequence, before, after, hash) in [
-                (
-                    "bad-audit-before",
-                    1_i64,
-                    "{\"x\":-0}",
-                    "{}",
-                    "2".repeat(64),
-                ),
-                (
-                    "bad-audit-after",
-                    2_i64,
-                    "{}",
-                    "{\"x\":1.00}",
-                    "3".repeat(64),
-                ),
-            ] {
-                assert!(
-                    tx.execute(
-                        "INSERT INTO audit_events
+                    "INSERT INTO audit_events
                             (id, sequence, project_id, actor, action, subject_type, subject_id,
                              before_json, after_json, reason, occurred_at_ms, previous_hash,
                              event_hash)
                          VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
-                        params![
-                            id,
-                            sequence,
-                            "jcs-project",
-                            "actor",
-                            "action",
-                            "project",
-                            "jcs-project",
-                            before,
-                            after,
-                            "reason",
-                            1_i64,
-                            "0".repeat(64),
-                            hash
-                        ],
-                    )
-                    .is_err(),
-                    "non-JCS audit JSON was accepted for {id}"
-                );
-            }
-            Ok(())
-        })
-        .expect("check every JCS insertion boundary");
+                    params![
+                        id,
+                        sequence,
+                        "jcs-project",
+                        "actor",
+                        "project_created",
+                        "project",
+                        "jcs-project",
+                        before,
+                        after,
+                        "reason",
+                        1_i64,
+                        "0".repeat(64),
+                        hash
+                    ],
+                )
+                .is_err(),
+                "non-JCS audit JSON was accepted for {id}"
+            );
+        }
+        Ok(())
+    })
+    .expect("check every JCS insertion boundary");
 }
 
 #[test]
 fn immutable_foundation_rows_reject_updates_and_deletes() {
     let database = TestDatabase::new("immutable");
-    let mut store = migrated_store(&database);
-    insert_complete_fixture(&mut store, "fixture");
+    drop(migrated_store(&database));
+    insert_complete_fixture(&database, "fixture");
 
-    store
-        .with_immediate_transaction(|tx| {
+    with_raw_verifier_transaction(&database, |tx| {
             let mutations = [
                 "UPDATE content_objects SET byte_length = 99 WHERE sha256 = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'",
                 "DELETE FROM content_objects WHERE sha256 = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'",
                 "UPDATE document_revisions SET created_by = 'changed' WHERE id = 'revision-1'",
                 "DELETE FROM document_revisions WHERE id = 'revision-1'",
                 "UPDATE sheets SET rotation_degrees = 90 WHERE id = 'sheet-1'",
                 "DELETE FROM sheets WHERE id = 'sheet-1'",
                 "UPDATE evidence_objects SET review_state = 'accepted' WHERE id = 'evidence-1'",
                 "DELETE FROM evidence_objects WHERE id = 'evidence-1'",
                 "UPDATE ingest_events SET actor = 'changed' WHERE id = 'ingest-1'",
                 "DELETE FROM ingest_events WHERE id = 'ingest-1'",
                 "UPDATE audit_events SET actor = 'changed' WHERE id = 'audit-1'",
                 "DELETE FROM audit_events WHERE id = 'audit-1'",
             ];
             for sql in mutations {
                 assert!(tx.execute(sql, []).is_err(), "mutation unexpectedly passed: {sql}");
             }
             Ok(())
-        })
-        .expect("immutable checks complete");
+    })
+    .expect("immutable checks complete");
 }
 
 #[test]
 fn quarantined_content_cannot_create_a_revision_or_sheet() {
     let database = TestDatabase::new("quarantine-boundary");
-    let mut store = migrated_store(&database);
+    drop(migrated_store(&database));
 
-    store
-        .with_immediate_transaction(|tx| {
-            tx.execute(
-                "INSERT INTO content_objects
+    with_raw_verifier_transaction(&database, |tx| {
+        tx.execute(
+            "INSERT INTO content_objects
                     (sha256, byte_length, admission_state, vault_key, created_at_ms, created_by,
                      quarantine_reason)
                  VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
+            params![
+                "f".repeat(64),
+                7_i64,
+                "quarantined",
+                "quarantine-key",
+                1_i64,
+                "actor",
+                "{}"
+            ],
+        )
+        .expect("insert quarantined content");
+        tx.execute(
+            "INSERT INTO documents (id, created_at_ms, created_by) VALUES (?1, ?2, ?3)",
+            params!["quarantined-document", 1_i64, "actor"],
+        )
+        .expect("insert document shell");
+        assert!(
+            tx.execute(
+                "INSERT INTO document_revisions
+                        (id, document_id, content_sha256, created_at_ms, created_by)
+                     VALUES (?1, ?2, ?3, ?4, ?5)",
                 params![
+                    "quarantined-revision",
+                    "quarantined-document",
                     "f".repeat(64),
-                    7_i64,
-                    "quarantined",
-                    "quarantine-key",
                     1_i64,
-                    "actor",
-                    "corrupt"
+                    "actor"
                 ],
             )
-            .expect("insert quarantined content");
-            tx.execute(
-                "INSERT INTO documents (id, created_at_ms, created_by) VALUES (?1, ?2, ?3)",
-                params!["quarantined-document", 1_i64, "actor"],
+            .is_err()
+        );
+        let revision_count = tx
+            .query_row(
+                "SELECT count(*) FROM document_revisions WHERE id = ?1",
+                ["quarantined-revision"],
+                |row| row.get::<_, i64>(0),
             )
-            .expect("insert document shell");
-            assert!(
-                tx.execute(
-                    "INSERT INTO document_revisions
-                        (id, document_id, content_sha256, created_at_ms, created_by)
-                     VALUES (?1, ?2, ?3, ?4, ?5)",
-                    params![
-                        "quarantined-revision",
-                        "quarantined-document",
-                        "f".repeat(64),
-                        1_i64,
-                        "actor"
-                    ],
-                )
-                .is_err()
-            );
-            let revision_count = tx
-                .query_row(
-                    "SELECT count(*) FROM document_revisions WHERE id = ?1",
-                    ["quarantined-revision"],
-                    |row| row.get::<_, i64>(0),
-                )
-                .expect("count rejected revision");
-            let sheet_count = tx
-                .query_row("SELECT count(*) FROM sheets", [], |row| {
-                    row.get::<_, i64>(0)
-                })
-                .expect("count rejected sheets");
-            assert_eq!((revision_count, sheet_count), (0, 0));
-            Ok(())
-        })
-        .expect("verify quarantine boundary");
+            .expect("count rejected revision");
+        let sheet_count = tx
+            .query_row("SELECT count(*) FROM sheets", [], |row| {
+                row.get::<_, i64>(0)
+            })
+            .expect("count rejected sheets");
+        assert_eq!((revision_count, sheet_count), (0, 0));
+        Ok(())
+    })
+    .expect("verify quarantine boundary");
 }
 
 #[test]
 fn raw_transactions_enforce_sheet_scale_and_accepted_evidence_lineage() {
     let database = TestDatabase::new("raw-lineage-boundary");
-    let mut store = migrated_store(&database);
-    insert_complete_fixture(&mut store, "lineage");
+    drop(migrated_store(&database));
+    insert_complete_fixture(&database, "lineage");
 
-    store
-        .with_immediate_transaction(|tx| {
-            for (digest, state, reason) in [
-                ("d".repeat(64), "accepted", None),
-                ("f".repeat(64), "quarantined", Some("suspicious")),
-            ] {
-                tx.execute(
-                    "INSERT INTO content_objects
+    with_raw_verifier_transaction(&database, |tx| {
+        for (digest, state, reason) in [
+            ("d".repeat(64), "accepted", None),
+            ("f".repeat(64), "quarantined", Some("{}")),
+        ] {
+            tx.execute(
+                "INSERT INTO content_objects
                         (sha256, byte_length, admission_state, vault_key, created_at_ms,
                          created_by, quarantine_reason)
                      VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
-                    params![
-                        digest,
-                        1_i64,
-                        state,
-                        format!("vault-{state}"),
-                        1_i64,
-                        "actor",
-                        reason
-                    ],
-                )
-                .expect("insert lineage fixture content");
-            }
+                params![
+                    digest,
+                    1_i64,
+                    state,
+                    format!("vault-{state}"),
+                    1_i64,
+                    "actor",
+                    reason
+                ],
+            )
+            .expect("insert lineage fixture content");
+        }
 
-            for (sheet_id, parent_hash) in [
-                ("sheet-quarantined-parent", "f".repeat(64)),
-                ("sheet-mismatched-accepted-parent", "d".repeat(64)),
-            ] {
-                assert!(
-                    tx.execute(
-                        "INSERT INTO sheets
+        for (sheet_id, parent_hash) in [
+            ("sheet-quarantined-parent", "f".repeat(64)),
+            ("sheet-mismatched-accepted-parent", "d".repeat(64)),
+        ] {
+            assert!(
+                tx.execute(
+                    "INSERT INTO sheets
                             (id, revision_id, zero_based_page_index, width_micropoints,
                              height_micropoints, rotation_degrees, unit, parent_content_sha256,
                              transform_json)
                          VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
-                        params![
-                            sheet_id,
-                            "revision-1",
-                            1_i64,
-                            1_i64,
-                            1_i64,
-                            0_i64,
-                            "pt",
-                            parent_hash,
-                            "{}"
-                        ],
-                    )
-                    .is_err(),
-                    "raw SQL attached invalid parent content to a sheet"
-                );
-                assert!(
-                    tx.execute(
-                        "INSERT INTO scales
+                    params![
+                        sheet_id,
+                        "revision-1",
+                        1_i64,
+                        1_i64,
+                        1_i64,
+                        0_i64,
+                        "pt",
+                        parent_hash,
+                        "{}"
+                    ],
+                )
+                .is_err(),
+                "raw SQL attached invalid parent content to a sheet"
+            );
+            assert!(
+                tx.execute(
+                    "INSERT INTO scales
                             (id, sheet_id, numerator, denominator, source, created_at_ms,
                              created_by)
                          VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
-                        params![
-                            format!("scale-{sheet_id}"),
-                            sheet_id,
-                            1_i64,
-                            1_i64,
-                            "fixture",
-                            1_i64,
-                            "actor"
-                        ],
-                    )
-                    .is_err(),
-                    "raw SQL attached a scale to an invalid sheet"
-                );
-            }
+                    params![
+                        format!("scale-{sheet_id}"),
+                        sheet_id,
+                        1_i64,
+                        1_i64,
+                        "fixture",
+                        1_i64,
+                        "actor"
+                    ],
+                )
+                .is_err(),
+                "raw SQL attached a scale to an invalid sheet"
+            );
+        }
 
-            for (id, derivative, parent) in [
-                (
-                    "accepted-quarantined-derivative",
-                    "f".repeat(64),
-                    "a".repeat(64),
-                ),
-                (
-                    "accepted-quarantined-parent",
-                    "d".repeat(64),
-                    "f".repeat(64),
-                ),
-                ("accepted-mismatched-parent", "d".repeat(64), "d".repeat(64)),
-            ] {
-                assert!(
-                    tx.execute(
-                        "INSERT INTO evidence_objects
+        for (id, derivative, parent) in [
+            (
+                "accepted-quarantined-derivative",
+                "f".repeat(64),
+                "a".repeat(64),
+            ),
+            (
+                "accepted-quarantined-parent",
+                "d".repeat(64),
+                "f".repeat(64),
+            ),
+            ("accepted-mismatched-parent", "d".repeat(64), "d".repeat(64)),
+        ] {
+            assert!(
+                tx.execute(
+                    "INSERT INTO evidence_objects
                             (id, project_id, job_id, document_revision_id, content_sha256,
                              parent_content_sha256, extraction_method, parameters_json,
                              review_state, created_at_ms)
                          VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
-                        params![
-                            id,
-                            "project-1",
-                            "job-1",
-                            "revision-1",
-                            derivative,
-                            parent,
-                            "fixture",
-                            "{}",
-                            "accepted",
-                            1_i64
-                        ],
-                    )
-                    .is_err(),
-                    "raw SQL admitted invalid accepted evidence lineage: {id}"
-                );
-            }
+                    params![
+                        id,
+                        "project-1",
+                        "job-1",
+                        "revision-1",
+                        derivative,
+                        parent,
+                        "fixture",
+                        "{}",
+                        "accepted",
+                        1_i64
+                    ],
+                )
+                .is_err(),
+                "raw SQL admitted invalid accepted evidence lineage: {id}"
+            );
+        }
 
-            tx.execute(
-                "INSERT INTO evidence_objects
+        tx.execute(
+            "INSERT INTO evidence_objects
                     (id, project_id, job_id, document_revision_id, content_sha256,
                      parent_content_sha256, extraction_method, parameters_json, review_state,
                      created_at_ms)
                  VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
-                params![
-                    "unreviewed-quarantined-evidence",
-                    "project-1",
-                    "job-1",
-                    "revision-1",
-                    "f".repeat(64),
-                    "f".repeat(64),
-                    "fixture",
-                    "{}",
-                    "unreviewed",
-                    1_i64
-                ],
-            )
-            .expect("non-accepted review state preserves quarantined evidence");
-            Ok(())
-        })
-        .expect("complete raw lineage boundary checks");
+            params![
+                "unreviewed-quarantined-evidence",
+                "project-1",
+                "job-1",
+                "revision-1",
+                "f".repeat(64),
+                "f".repeat(64),
+                "fixture",
+                "{}",
+                "unreviewed",
+                1_i64
+            ],
+        )
+        .expect("non-accepted review state preserves quarantined evidence");
+        Ok(())
+    })
+    .expect("complete raw lineage boundary checks");
 }
 
 #[test]
 fn a_schema_newer_than_the_binary_fails_closed() {
     let database = TestDatabase::new("future-version");
-    let mut store = migrated_store(&database);
-    store
-        .with_immediate_transaction(|tx| {
-            tx.execute(
-                "INSERT INTO schema_migrations (version, sha256) VALUES (?1, ?2)",
-                params![2_i64, "d".repeat(64)],
-            )
-            .expect("insert future version");
-            Ok(())
-        })
-        .expect("commit future marker");
+    drop(migrated_store(&database));
+    with_raw_verifier_transaction(&database, |tx| {
+        tx.execute(
+            "INSERT INTO schema_migrations (version, sha256) VALUES (?1, ?2)",
+            params![2_i64, "d".repeat(64)],
+        )
+        .expect("insert future version");
+        Ok(())
+    })
+    .expect("commit future marker");
 
+    let mut store = Store::open_writer(&database.path).expect("reopen future-version database");
     assert!(matches!(store.migrate(), Err(HeleosError::Migration)));
 }
 
 #[test]
 fn a_migration_hash_mismatch_fails_closed() {
     let database = TestDatabase::new("migration-hash");
-    let mut store = migrated_store(&database);
-    store
-        .with_immediate_transaction(|tx| {
-            tx.execute(
-                "UPDATE schema_migrations SET sha256 = ?1 WHERE version = ?2",
-                params!["e".repeat(64), 1_i64],
-            )
-            .expect("tamper migration hash");
-            Ok(())
-        })
-        .expect("commit hash tamper");
+    drop(migrated_store(&database));
+    with_raw_verifier_transaction(&database, |tx| {
+        tx.execute(
+            "UPDATE schema_migrations SET sha256 = ?1 WHERE version = ?2",
+            params!["e".repeat(64), 1_i64],
+        )
+        .expect("tamper migration hash");
+        Ok(())
+    })
+    .expect("commit hash tamper");
 
+    let mut store = Store::open_writer(&database.path).expect("reopen tampered database");
     assert!(matches!(store.migrate(), Err(HeleosError::Migration)));
 }
 
 #[test]
 fn writer_lock_process_helper() {
     let Some(database) = env::var_os("HELEOS_TEST_LOCK_DATABASE") else {
         return;
     };
     let _store = Store::open_writer(database).expect("child obtains writer lock");
     println!("HELEOS_WRITER_LOCKED");
@@ -1462,31 +3008,32 @@ fn paused_writer_lock_process_helper() {
     let _ = std::io::stdin().read(&mut release);
 }
 
 #[test]
 fn crash_left_wal_process_helper() {
     let Some(database) = env::var_os("HELEOS_TEST_CRASH_DATABASE") else {
         return;
     };
     let mut store = Store::open_writer(database).expect("crash fixture opens writer");
     store.migrate().expect("crash fixture migrates");
-    store
-        .with_immediate_transaction(|tx| {
-            tx.execute(
-                "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
-                 VALUES (?1, ?2, ?3, ?4, ?5)",
-                params!["crash-project", "name", 1_i64, "actor", "INTERNAL"],
-            )
-            .expect("write crash-left WAL row");
-            Ok(())
-        })
-        .expect("commit crash-left WAL row");
+    drop(store);
+    let crash_database = PathBuf::from(
+        env::var_os("HELEOS_TEST_CRASH_DATABASE").expect("crash database remains configured"),
+    );
+    let connection = raw_verifier_connection_path(&crash_database);
+    connection
+        .execute(
+            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
+             VALUES (?1, ?2, ?3, ?4, ?5)",
+            params!["crash-project", "name", 1_i64, "actor", "INTERNAL"],
+        )
+        .expect("write crash-left WAL row");
     std::process::exit(0);
 }
 
 #[test]
 fn independent_processes_cannot_both_hold_the_writer_lock() {
     let database = TestDatabase::new("process-lock");
     let mut child = Command::new(env::current_exe().expect("locate test executable"))
         .arg("--exact")
         .arg("writer_lock_process_helper")
         .arg("--nocapture")
@@ -1626,52 +3173,36 @@ fn writer_fails_closed_before_mutation_after_lock_path_replacement() {
         .path()
         .to_owned();
     let displaced_lock = database.root.join("displaced.writer.lock");
 
     fs::rename(&lock_path, &displaced_lock).expect("displace held writer lock path");
     File::create(&lock_path).expect("create replacement writer lock path");
     fs::set_permissions(&lock_path, fs::Permissions::from_mode(0o600))
         .expect("harden replacement lock path");
 
     assert!(matches!(writer.migrate(), Err(HeleosError::PolicyDenied)));
-    assert!(matches!(
-        writer.with_immediate_transaction(|tx| {
-            tx.execute(
-                "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
-                 VALUES (?1, ?2, ?3, ?4, ?5)",
-                params!["must-not-write", "name", 1_i64, "actor", "INTERNAL"],
-            )
-            .expect("operation must never run");
-            Ok(())
-        }),
-        Err(HeleosError::PolicyDenied)
-    ));
 }
 
 #[test]
 fn read_only_verification_has_no_mutation_capability() {
     let database = TestDatabase::new("read-only");
     let store = migrated_store(&database);
     drop(store);
 
-    let mut read_only = Store::open_read_only(&database.path).expect("open read-only store");
+    let read_only = Store::open_read_only(&database.path).expect("open read-only store");
     assert_eq!(read_only.schema_version().expect("read schema version"), 1);
     assert!(
         read_only
             .verify_integrity()
             .expect("verify read-only")
             .is_clean()
     );
-    assert!(matches!(
-        read_only.with_immediate_transaction(|_| Ok(())),
-        Err(HeleosError::PolicyDenied)
-    ));
 }
 
 #[cfg(unix)]
 #[test]
 fn read_only_open_does_not_change_parent_database_wal_or_shm_metadata() {
     let database = TestDatabase::new("read-only-metadata");
     spawn_crash_left_writer(&database);
 
     let wal = database_sidecar(&database.path, "-wal");
     let shm = database_sidecar(&database.path, "-shm");
@@ -1917,21 +3448,21 @@ fn integrity_quick_and_foreign_key_checks_report_clean() {
     assert!(report.foreign_key_violations.is_empty());
     assert!(!report.foreign_key_check_truncated);
     assert!(report.is_clean());
 }
 
 #[test]
 fn integrity_reporting_does_not_silently_stop_at_sqlites_default_100_rows() {
     let database = TestDatabase::new("integrity-dirty");
     drop(migrated_store(&database));
 
-    let connection = Connection::open(&database.path).expect("open raw corruption fixture");
+    let connection = raw_verifier_connection(&database);
     connection
         .pragma_update(None, "foreign_keys", "OFF")
         .expect("permit deliberate foreign-key violations");
     let page_size: u64 = connection
         .pragma_query_value(None, "page_size", |row| row.get::<_, i64>(0))
         .expect("read SQLite page size")
         .try_into()
         .expect("positive SQLite page size");
     connection
         .execute("CREATE TABLE fixture_rows (value INTEGER) STRICT", [])
@@ -2135,19 +3666,11 @@ fn symlink_and_non_regular_database_or_lock_paths_are_rejected() {
         Store::open_writer(&database.path),
         Err(HeleosError::PolicyDenied)
     ));
 }
 
 #[test]
 fn in_memory_store_has_the_same_schema_and_defensive_policy() {
     let mut store = Store::open_in_memory().expect("open in-memory store");
     store.migrate().expect("migrate in-memory store");
     assert_eq!(store.schema_version().expect("schema version"), 1);
-    let defensive = store
-        .with_immediate_transaction(|tx| {
-            Ok(tx
-                .db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE)
-                .expect("query defensive mode"))
-        })
-        .expect("inspect in-memory settings");
-    assert!(defensive);
 }
