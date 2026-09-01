-- Rebuild the two affinity-sensitive tables through ordinary transactional DDL.
-- The migration runner disables FK enforcement only while holding its write
-- transaction, validates the complete graph, and commits this script with its
-- migration-ledger row. Explicit rowids preserve the identity and insertion
-- order of every existing immutable ledger row.
CREATE TEMP TABLE agent_work_items_013_snapshot AS
SELECT rowid AS preserved_rowid, * FROM agent_work_items;

CREATE TEMP TABLE agent_execution_attempts_013_snapshot AS
SELECT rowid AS preserved_rowid, * FROM agent_execution_attempts;

DROP VIEW agent_work_item_states;
DROP TABLE agent_execution_attempts;
DROP TABLE agent_work_items;

CREATE TABLE agent_work_items (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES agent_jobs(id) ON DELETE RESTRICT,
    workflow_run_id TEXT NOT NULL REFERENCES agent_workflow_runs(id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    revision_set_id TEXT NOT NULL REFERENCES revision_sets(id) ON DELETE RESTRICT,
    capability TEXT NOT NULL,
    worker_id TEXT NOT NULL REFERENCES agent_workers(id) ON DELETE RESTRICT,
    adapter_revision_id TEXT NOT NULL REFERENCES agent_adapter_revisions(id) ON DELETE RESTRICT,
    input_artifact_id TEXT REFERENCES agent_artifacts(id) ON DELETE RESTRICT,
    max_attempts BLOB NOT NULL CHECK(max_attempts > 0),
    created_at TEXT NOT NULL
);

INSERT INTO agent_work_items(
    rowid, id, job_id, workflow_run_id, project_id, revision_set_id, capability,
    worker_id, adapter_revision_id, input_artifact_id, max_attempts, created_at
)
SELECT preserved_rowid, id, job_id, workflow_run_id, project_id, revision_set_id,
       capability, worker_id, adapter_revision_id, input_artifact_id,
       max_attempts, created_at
FROM agent_work_items_013_snapshot;

CREATE TABLE agent_execution_attempts (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES agent_work_items(id) ON DELETE RESTRICT,
    attempt_number BLOB NOT NULL CHECK(attempt_number > 0),
    worker_id TEXT NOT NULL REFERENCES agent_workers(id) ON DELETE RESTRICT,
    adapter_revision_id TEXT NOT NULL REFERENCES agent_adapter_revisions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(work_item_id, attempt_number)
);

INSERT INTO agent_execution_attempts(
    rowid, id, work_item_id, attempt_number, worker_id, adapter_revision_id, created_at
)
SELECT preserved_rowid, id, work_item_id, attempt_number, worker_id,
       adapter_revision_id, created_at
FROM agent_execution_attempts_013_snapshot;

DROP TABLE agent_execution_attempts_013_snapshot;
DROP TABLE agent_work_items_013_snapshot;

CREATE INDEX agent_work_items_by_run
ON agent_work_items(workflow_run_id, created_at, id);

CREATE INDEX agent_execution_attempts_by_item
ON agent_execution_attempts(work_item_id, attempt_number);

CREATE VIEW agent_work_item_states AS
SELECT item.id AS work_item_id,
       item.job_id,
       item.workflow_run_id,
       item.project_id,
       item.revision_set_id,
       (
           SELECT event.event_type
           FROM agent_work_item_events AS event
           WHERE event.work_item_id = item.id
           ORDER BY event.rowid DESC
           LIMIT 1
       ) AS state
FROM agent_work_items AS item;

CREATE TRIGGER agent_work_items_require_execution_lineage
BEFORE INSERT ON agent_work_items
WHEN NOT EXISTS (
    SELECT 1
    FROM agent_jobs AS job
    JOIN agent_workflow_runs AS run ON run.job_id = job.id
    WHERE job.id = NEW.job_id
      AND run.id = NEW.workflow_run_id
      AND job.project_id = NEW.project_id
      AND run.project_id = NEW.project_id
      AND job.revision_set_id = NEW.revision_set_id
      AND run.revision_set_id = NEW.revision_set_id
)
OR NOT EXISTS (
    SELECT 1 FROM agent_worker_capabilities
    WHERE worker_id = NEW.worker_id AND capability = NEW.capability
)
OR (
    NEW.input_artifact_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1 FROM agent_artifacts
        WHERE id = NEW.input_artifact_id
          AND project_id = NEW.project_id
          AND revision_set_id = NEW.revision_set_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'agent work item lineage and assignment must match its job and run');
END;

CREATE TRIGGER agent_work_items_require_registered_adapter_assignment
BEFORE INSERT ON agent_work_items
WHEN NOT EXISTS (
    SELECT 1 FROM agent_worker_adapter_assignments
    WHERE worker_id = NEW.worker_id
      AND adapter_revision_id = NEW.adapter_revision_id
)
BEGIN
    SELECT RAISE(ABORT, 'agent work items require a registered worker adapter assignment');
END;

CREATE TRIGGER agent_work_items_are_immutable BEFORE UPDATE ON agent_work_items
BEGIN SELECT RAISE(ABORT, 'agent work items are immutable'); END;

CREATE TRIGGER agent_work_items_cannot_be_deleted BEFORE DELETE ON agent_work_items
BEGIN SELECT RAISE(ABORT, 'agent work items cannot be deleted'); END;

CREATE TRIGGER agent_execution_attempts_require_item_assignment
BEFORE INSERT ON agent_execution_attempts
WHEN NOT EXISTS (
    SELECT 1 FROM agent_work_items
    WHERE id = NEW.work_item_id
      AND worker_id = NEW.worker_id
      AND adapter_revision_id = NEW.adapter_revision_id
      AND NEW.attempt_number <= max_attempts
)
BEGIN
    SELECT RAISE(ABORT, 'agent execution attempt exceeds or mismatches its work item assignment');
END;

CREATE TRIGGER agent_execution_attempts_require_sequential_ordinal
BEFORE INSERT ON agent_execution_attempts
WHEN NOT EXISTS (
    SELECT 1
    FROM agent_work_items AS item
    WHERE item.id = NEW.work_item_id
      AND NEW.attempt_number <= item.max_attempts
      AND NEW.attempt_number = (
          SELECT COUNT(*) + 1
          FROM agent_execution_attempts AS existing
          WHERE existing.work_item_id = NEW.work_item_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'agent execution attempts require the next sequential ordinal within budget');
END;

CREATE TRIGGER agent_execution_attempts_are_immutable BEFORE UPDATE ON agent_execution_attempts
BEGIN SELECT RAISE(ABORT, 'agent execution attempts are immutable'); END;

CREATE TRIGGER agent_execution_attempts_cannot_be_deleted BEFORE DELETE ON agent_execution_attempts
BEGIN SELECT RAISE(ABORT, 'agent execution attempts cannot be deleted'); END;

-- Lifecycle state is append order, not caller-controlled wall-clock text. This
-- keeps every legacy row readable while making immutable rowid the one ordering
-- rule for both legacy and canonical post-migration facts.
DROP TRIGGER agent_work_item_leased_event_requires_next_attempt;
DROP TRIGGER agent_work_item_started_event_requires_lease;
DROP TRIGGER agent_work_item_outcome_requires_started_attempt;
DROP TRIGGER agent_work_item_cancelled_event_requires_started_attempt;
DROP TRIGGER agent_work_item_blocked_event_requires_preflight_state;

CREATE TRIGGER agent_work_item_leased_event_requires_next_attempt
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'LEASED'
AND (
    NEW.execution_attempt_id IS NULL
    OR NOT EXISTS (
        SELECT 1 FROM agent_execution_attempts
        WHERE id = NEW.execution_attempt_id AND work_item_id = NEW.work_item_id
    )
    OR COALESCE((
        SELECT event_type FROM agent_work_item_events
        WHERE work_item_id = NEW.work_item_id
        ORDER BY rowid DESC LIMIT 1
    ), '') NOT IN ('QUEUED', 'RETRY_SCHEDULED')
    OR EXISTS (
        SELECT 1 FROM agent_work_item_events
        WHERE work_item_id = NEW.work_item_id
          AND execution_attempt_id = NEW.execution_attempt_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'LEASED requires one new owned attempt after QUEUED or RETRY_SCHEDULED');
END;

CREATE TRIGGER agent_work_item_started_event_requires_lease
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'STARTED'
AND (
    NEW.execution_attempt_id IS NULL
    OR NOT EXISTS (
        SELECT 1 FROM agent_execution_attempts
        WHERE id = NEW.execution_attempt_id AND work_item_id = NEW.work_item_id
    )
    OR NOT EXISTS (
        SELECT 1 FROM agent_work_item_events
        WHERE rowid = (
            SELECT rowid FROM agent_work_item_events
            WHERE work_item_id = NEW.work_item_id
            ORDER BY rowid DESC LIMIT 1
        )
          AND event_type = 'LEASED'
          AND execution_attempt_id = NEW.execution_attempt_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'STARTED requires the immediately preceding lease for its owned attempt');
END;

CREATE TRIGGER agent_work_item_outcome_requires_started_attempt
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type IN ('SUCCEEDED', 'RETRY_SCHEDULED', 'FAILED', 'ESCALATED')
AND (
    NEW.execution_attempt_id IS NULL
    OR NOT EXISTS (
        SELECT 1 FROM agent_execution_attempts
        WHERE id = NEW.execution_attempt_id AND work_item_id = NEW.work_item_id
    )
    OR NOT EXISTS (
        SELECT 1 FROM agent_work_item_events
        WHERE rowid = (
            SELECT rowid FROM agent_work_item_events
            WHERE work_item_id = NEW.work_item_id
            ORDER BY rowid DESC LIMIT 1
        )
          AND event_type = 'STARTED'
          AND execution_attempt_id = NEW.execution_attempt_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'agent work item outcome requires the immediately preceding started attempt');
END;

CREATE TRIGGER agent_work_item_cancelled_event_requires_started_attempt
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'CANCELLED'
AND (
    NEW.execution_attempt_id IS NULL
    OR NOT EXISTS (
        SELECT 1 FROM agent_execution_attempts
        WHERE id = NEW.execution_attempt_id AND work_item_id = NEW.work_item_id
    )
    OR NOT EXISTS (
        SELECT 1 FROM agent_work_item_events
        WHERE rowid = (
            SELECT rowid FROM agent_work_item_events
            WHERE work_item_id = NEW.work_item_id
            ORDER BY rowid DESC LIMIT 1
        )
          AND event_type = 'STARTED'
          AND execution_attempt_id = NEW.execution_attempt_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'CANCELLED requires the immediately preceding started attempt');
END;

CREATE TRIGGER agent_work_item_blocked_event_requires_preflight_state
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'BLOCKED'
AND NOT (
    (
        NEW.execution_attempt_id IS NULL
        AND COALESCE((
            SELECT event_type FROM agent_work_item_events
            WHERE work_item_id = NEW.work_item_id
            ORDER BY rowid DESC LIMIT 1
        ), '') IN ('QUEUED', 'RETRY_SCHEDULED')
    )
    OR
    (
        NEW.execution_attempt_id IS NOT NULL
        AND EXISTS (
            SELECT 1 FROM agent_execution_attempts
            WHERE id = NEW.execution_attempt_id
              AND work_item_id = NEW.work_item_id
        )
        AND EXISTS (
            SELECT 1 FROM agent_work_item_events
            WHERE rowid = (
                SELECT rowid FROM agent_work_item_events
                WHERE work_item_id = NEW.work_item_id
                ORDER BY rowid DESC LIMIT 1
            )
              AND event_type = 'STARTED'
              AND execution_attempt_id = NEW.execution_attempt_id
        )
    )
)
BEGIN
    SELECT RAISE(ABORT, 'BLOCKED requires preflight state or the immediately preceding owned started attempt');
END;

CREATE TRIGGER agent_work_items_require_integer_attempt_budget
BEFORE INSERT ON agent_work_items
WHEN typeof(NEW.max_attempts) <> 'integer'
  OR NEW.max_attempts <= 0
BEGIN
    SELECT RAISE(ABORT, 'agent work item attempt budget must be a positive SQLite integer');
END;

CREATE TRIGGER agent_execution_attempts_require_integer_ordinal_and_budget
BEFORE INSERT ON agent_execution_attempts
WHEN typeof(NEW.attempt_number) <> 'integer'
  OR NEW.attempt_number <= 0
  OR NOT EXISTS (
      SELECT 1
      FROM agent_work_items AS item
      WHERE item.id = NEW.work_item_id
        AND typeof(item.max_attempts) = 'integer'
        AND item.max_attempts > 0
        AND NEW.attempt_number <= item.max_attempts
  )
BEGIN
    SELECT RAISE(ABORT, 'agent attempt ordinal and immutable budget must be positive SQLite integers');
END;

CREATE TRIGGER agent_work_item_event_reason_requires_text_storage
BEFORE INSERT ON agent_work_item_events
WHEN typeof(NEW.reason_code) <> 'text'
BEGIN
    SELECT RAISE(ABORT, 'agent work item event reason must use SQLite text storage');
END;

CREATE TRIGGER agent_worker_availability_reason_requires_text_storage
BEFORE INSERT ON agent_worker_availability_events
WHEN typeof(NEW.reason_code) <> 'text'
BEGIN
    SELECT RAISE(ABORT, 'worker availability reason must use SQLite text storage');
END;

CREATE TRIGGER agent_work_item_events_require_canonical_timestamp
BEFORE INSERT ON agent_work_item_events
WHEN typeof(NEW.created_at) <> 'text'
  OR length(NEW.created_at) <> 32
  OR NEW.created_at NOT GLOB
     '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]+00:00'
  OR julianday(NEW.created_at) IS NULL
  OR date(NEW.created_at) <> substr(NEW.created_at, 1, 10)
  OR strftime('%H:%M:%S', NEW.created_at) <> substr(NEW.created_at, 12, 8)
BEGIN
    SELECT RAISE(ABORT, 'agent work item events require a canonical UTC timestamp');
END;

CREATE TRIGGER agent_work_item_events_reject_backdating
BEFORE INSERT ON agent_work_item_events
WHEN EXISTS (
    SELECT 1
    FROM agent_work_item_events AS persisted
    WHERE persisted.work_item_id = NEW.work_item_id
      AND julianday(persisted.created_at) IS NOT NULL
      AND (
          julianday(NEW.created_at) < julianday(persisted.created_at)
          OR (
              length(persisted.created_at) = 32
              AND persisted.created_at GLOB
                  '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]+00:00'
              AND NEW.created_at < persisted.created_at
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'agent work item events cannot backdate persisted history');
END;

CREATE TRIGGER agent_worker_availability_events_require_canonical_timestamp
BEFORE INSERT ON agent_worker_availability_events
WHEN typeof(NEW.created_at) <> 'text'
  OR length(NEW.created_at) <> 32
  OR NEW.created_at NOT GLOB
     '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]+00:00'
  OR julianday(NEW.created_at) IS NULL
  OR date(NEW.created_at) <> substr(NEW.created_at, 1, 10)
  OR strftime('%H:%M:%S', NEW.created_at) <> substr(NEW.created_at, 12, 8)
BEGIN
    SELECT RAISE(ABORT, 'worker availability events require a canonical UTC timestamp');
END;

CREATE TRIGGER agent_worker_availability_events_reject_backdating
BEFORE INSERT ON agent_worker_availability_events
WHEN EXISTS (
    SELECT 1
    FROM agent_worker_availability_events AS persisted
    WHERE persisted.worker_id = NEW.worker_id
      AND persisted.adapter_revision_id = NEW.adapter_revision_id
      AND julianday(persisted.created_at) IS NOT NULL
      AND (
          julianday(NEW.created_at) < julianday(persisted.created_at)
          OR (
              length(persisted.created_at) = 32
              AND persisted.created_at GLOB
                  '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]+00:00'
              AND NEW.created_at < persisted.created_at
          )
      )
)
BEGIN
    SELECT RAISE(ABORT, 'worker availability events cannot backdate exact-adapter history');
END;

CREATE TRIGGER agent_work_item_events_require_increasing_append_position
AFTER INSERT ON agent_work_item_events
WHEN EXISTS (
    SELECT 1
    FROM agent_work_item_events AS persisted
    WHERE persisted.work_item_id = NEW.work_item_id
      AND persisted.rowid <> NEW.rowid
      AND persisted.rowid >= NEW.rowid
)
BEGIN
    SELECT RAISE(ABORT, 'agent work item event rowid must advance append position');
END;

CREATE TRIGGER agent_worker_availability_events_require_increasing_append_position
AFTER INSERT ON agent_worker_availability_events
WHEN EXISTS (
    SELECT 1
    FROM agent_worker_availability_events AS persisted
    WHERE persisted.worker_id = NEW.worker_id
      AND persisted.adapter_revision_id = NEW.adapter_revision_id
      AND persisted.rowid <> NEW.rowid
      AND persisted.rowid >= NEW.rowid
)
BEGIN
    SELECT RAISE(ABORT, 'worker availability rowid must advance exact-adapter append position');
END;
