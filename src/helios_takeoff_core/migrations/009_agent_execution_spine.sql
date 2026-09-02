-- P1A is an append-only execution ledger layered beside the authoritative P0 records.
CREATE TABLE agent_workers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE agent_worker_capabilities (
    worker_id TEXT NOT NULL REFERENCES agent_workers(id) ON DELETE RESTRICT,
    capability TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(worker_id, capability)
);

CREATE TABLE agent_adapter_revisions (
    id TEXT PRIMARY KEY,
    adapter_name TEXT NOT NULL,
    adapter_kind TEXT NOT NULL CHECK(adapter_kind IN ('BUILTIN', 'SUBPROCESS')),
    revision TEXT NOT NULL,
    configuration_sha256 TEXT NOT NULL
        CHECK(length(configuration_sha256) = 64 AND configuration_sha256 NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL,
    UNIQUE(adapter_name, revision)
);

CREATE TABLE agent_worker_availability_events (
    id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL REFERENCES agent_workers(id) ON DELETE RESTRICT,
    availability TEXT NOT NULL CHECK(availability IN ('AVAILABLE', 'UNAVAILABLE')),
    created_at TEXT NOT NULL
);

CREATE INDEX agent_worker_availability_events_by_worker
ON agent_worker_availability_events(worker_id, created_at, id);

CREATE TABLE agent_artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    revision_set_id TEXT NOT NULL REFERENCES revision_sets(id) ON DELETE RESTRICT,
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
    media_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    store_key TEXT NOT NULL CHECK(
        store_key <> ''
        AND substr(store_key, 1, 1) <> '/'
        AND store_key NOT LIKE '../%'
        AND store_key NOT LIKE '%/../%'
        AND store_key NOT LIKE '%\\%'
    ),
    created_at TEXT NOT NULL,
    UNIQUE(sha256, media_type, schema_version, store_key)
);

CREATE INDEX agent_artifacts_by_baseline
ON agent_artifacts(project_id, revision_set_id, created_at, id);

CREATE TRIGGER agent_artifacts_require_matching_frozen_baseline
BEFORE INSERT ON agent_artifacts
WHEN NOT EXISTS (
    SELECT 1 FROM revision_sets
    WHERE id = NEW.revision_set_id
      AND project_id = NEW.project_id
      AND status = 'FROZEN'
)
BEGIN
    SELECT RAISE(ABORT, 'agent artifacts require a matching frozen revision set');
END;

CREATE TABLE agent_jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    revision_set_id TEXT NOT NULL REFERENCES revision_sets(id) ON DELETE RESTRICT,
    job_type TEXT NOT NULL CHECK(job_type IN ('BASELINE_AUDIT')),
    input_artifact_id TEXT REFERENCES agent_artifacts(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE INDEX agent_jobs_by_baseline
ON agent_jobs(project_id, revision_set_id, created_at, id);

CREATE TRIGGER agent_jobs_require_matching_frozen_baseline
BEFORE INSERT ON agent_jobs
WHEN NOT EXISTS (
    SELECT 1 FROM revision_sets
    WHERE id = NEW.revision_set_id
      AND project_id = NEW.project_id
      AND status = 'FROZEN'
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
    SELECT RAISE(ABORT, 'agent jobs require a matching frozen revision set and input artifact');
END;

CREATE TABLE agent_workflow_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES agent_jobs(id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    revision_set_id TEXT NOT NULL REFERENCES revision_sets(id) ON DELETE RESTRICT,
    workflow_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX agent_workflow_runs_by_job
ON agent_workflow_runs(job_id, created_at, id);

CREATE TRIGGER agent_workflow_runs_require_job_lineage
BEFORE INSERT ON agent_workflow_runs
WHEN NOT EXISTS (
    SELECT 1 FROM agent_jobs
    WHERE id = NEW.job_id
      AND project_id = NEW.project_id
      AND revision_set_id = NEW.revision_set_id
)
BEGIN
    SELECT RAISE(ABORT, 'agent workflow run lineage must match its job');
END;

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
    max_attempts INTEGER NOT NULL CHECK(max_attempts > 0),
    created_at TEXT NOT NULL
);

CREATE INDEX agent_work_items_by_run
ON agent_work_items(workflow_run_id, created_at, id);

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

CREATE TABLE agent_work_item_dependencies (
    work_item_id TEXT NOT NULL REFERENCES agent_work_items(id) ON DELETE RESTRICT,
    depends_on_work_item_id TEXT NOT NULL REFERENCES agent_work_items(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(work_item_id, depends_on_work_item_id),
    CHECK(work_item_id <> depends_on_work_item_id)
);

CREATE INDEX agent_work_item_dependencies_by_predecessor
ON agent_work_item_dependencies(depends_on_work_item_id, work_item_id);

CREATE TRIGGER agent_work_item_dependencies_require_same_run
BEFORE INSERT ON agent_work_item_dependencies
WHEN NOT EXISTS (
    SELECT 1
    FROM agent_work_items AS item
    JOIN agent_work_items AS predecessor
      ON predecessor.id = NEW.depends_on_work_item_id
    WHERE item.id = NEW.work_item_id
      AND predecessor.job_id = item.job_id
      AND predecessor.workflow_run_id = item.workflow_run_id
      AND predecessor.project_id = item.project_id
      AND predecessor.revision_set_id = item.revision_set_id
)
BEGIN
    SELECT RAISE(ABORT, 'agent work item dependencies must stay within one run');
END;

CREATE TABLE agent_execution_attempts (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES agent_work_items(id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
    worker_id TEXT NOT NULL REFERENCES agent_workers(id) ON DELETE RESTRICT,
    adapter_revision_id TEXT NOT NULL REFERENCES agent_adapter_revisions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(work_item_id, attempt_number)
);

CREATE INDEX agent_execution_attempts_by_item
ON agent_execution_attempts(work_item_id, attempt_number);

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

CREATE TABLE agent_work_item_events (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES agent_work_items(id) ON DELETE RESTRICT,
    execution_attempt_id TEXT REFERENCES agent_execution_attempts(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'QUEUED', 'LEASED', 'STARTED', 'SUCCEEDED', 'RETRY_SCHEDULED',
        'FAILED', 'BLOCKED', 'ESCALATED', 'CANCELLED'
    )),
    created_at TEXT NOT NULL
);

CREATE INDEX agent_work_item_events_by_item
ON agent_work_item_events(work_item_id, created_at, id);

CREATE TRIGGER agent_work_item_events_require_matching_attempt
BEFORE INSERT ON agent_work_item_events
WHEN NEW.execution_attempt_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1 FROM agent_execution_attempts
     WHERE id = NEW.execution_attempt_id AND work_item_id = NEW.work_item_id
 )
BEGIN
    SELECT RAISE(ABORT, 'agent work item event attempt must belong to its work item');
END;

CREATE TRIGGER agent_terminal_work_items_reject_later_events
BEFORE INSERT ON agent_work_item_events
WHEN EXISTS (
    SELECT 1 FROM agent_work_item_events
    WHERE work_item_id = NEW.work_item_id
      AND event_type IN ('SUCCEEDED', 'FAILED', 'BLOCKED', 'ESCALATED', 'CANCELLED')
)
BEGIN
    SELECT RAISE(ABORT, 'terminal agent work items cannot receive later events');
END;

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
           ORDER BY event.created_at DESC, event.rowid DESC
           LIMIT 1
       ) AS state
FROM agent_work_items AS item;

-- Every P1A fact is immutable. State transitions are new event rows, never updates.
CREATE TRIGGER agent_workers_are_immutable BEFORE UPDATE ON agent_workers
BEGIN SELECT RAISE(ABORT, 'agent workers are immutable'); END;
CREATE TRIGGER agent_workers_cannot_be_deleted BEFORE DELETE ON agent_workers
BEGIN SELECT RAISE(ABORT, 'agent workers cannot be deleted'); END;

CREATE TRIGGER agent_worker_capabilities_are_immutable BEFORE UPDATE ON agent_worker_capabilities
BEGIN SELECT RAISE(ABORT, 'agent worker capabilities are immutable'); END;
CREATE TRIGGER agent_worker_capabilities_cannot_be_deleted BEFORE DELETE ON agent_worker_capabilities
BEGIN SELECT RAISE(ABORT, 'agent worker capabilities cannot be deleted'); END;

CREATE TRIGGER agent_adapter_revisions_are_immutable BEFORE UPDATE ON agent_adapter_revisions
BEGIN SELECT RAISE(ABORT, 'agent adapter revisions are immutable'); END;
CREATE TRIGGER agent_adapter_revisions_cannot_be_deleted BEFORE DELETE ON agent_adapter_revisions
BEGIN SELECT RAISE(ABORT, 'agent adapter revisions cannot be deleted'); END;

CREATE TRIGGER agent_worker_availability_events_are_immutable BEFORE UPDATE ON agent_worker_availability_events
BEGIN SELECT RAISE(ABORT, 'agent worker availability events are immutable'); END;
CREATE TRIGGER agent_worker_availability_events_cannot_be_deleted BEFORE DELETE ON agent_worker_availability_events
BEGIN SELECT RAISE(ABORT, 'agent worker availability events cannot be deleted'); END;

CREATE TRIGGER agent_artifacts_are_immutable BEFORE UPDATE ON agent_artifacts
BEGIN SELECT RAISE(ABORT, 'agent artifacts are immutable'); END;
CREATE TRIGGER agent_artifacts_cannot_be_deleted BEFORE DELETE ON agent_artifacts
BEGIN SELECT RAISE(ABORT, 'agent artifacts cannot be deleted'); END;

CREATE TRIGGER agent_jobs_are_immutable BEFORE UPDATE ON agent_jobs
BEGIN SELECT RAISE(ABORT, 'agent jobs are immutable'); END;
CREATE TRIGGER agent_jobs_cannot_be_deleted BEFORE DELETE ON agent_jobs
BEGIN SELECT RAISE(ABORT, 'agent jobs cannot be deleted'); END;

CREATE TRIGGER agent_workflow_runs_are_immutable BEFORE UPDATE ON agent_workflow_runs
BEGIN SELECT RAISE(ABORT, 'agent workflow runs are immutable'); END;
CREATE TRIGGER agent_workflow_runs_cannot_be_deleted BEFORE DELETE ON agent_workflow_runs
BEGIN SELECT RAISE(ABORT, 'agent workflow runs cannot be deleted'); END;

CREATE TRIGGER agent_work_items_are_immutable BEFORE UPDATE ON agent_work_items
BEGIN SELECT RAISE(ABORT, 'agent work items are immutable'); END;
CREATE TRIGGER agent_work_items_cannot_be_deleted BEFORE DELETE ON agent_work_items
BEGIN SELECT RAISE(ABORT, 'agent work items cannot be deleted'); END;

CREATE TRIGGER agent_work_item_dependencies_are_immutable BEFORE UPDATE ON agent_work_item_dependencies
BEGIN SELECT RAISE(ABORT, 'agent work item dependencies are immutable'); END;
CREATE TRIGGER agent_work_item_dependencies_cannot_be_deleted BEFORE DELETE ON agent_work_item_dependencies
BEGIN SELECT RAISE(ABORT, 'agent work item dependencies cannot be deleted'); END;

CREATE TRIGGER agent_execution_attempts_are_immutable BEFORE UPDATE ON agent_execution_attempts
BEGIN SELECT RAISE(ABORT, 'agent execution attempts are immutable'); END;
CREATE TRIGGER agent_execution_attempts_cannot_be_deleted BEFORE DELETE ON agent_execution_attempts
BEGIN SELECT RAISE(ABORT, 'agent execution attempts cannot be deleted'); END;

CREATE TRIGGER agent_work_item_events_are_immutable BEFORE UPDATE ON agent_work_item_events
BEGIN SELECT RAISE(ABORT, 'agent work item events are immutable'); END;
CREATE TRIGGER agent_work_item_events_cannot_be_deleted BEFORE DELETE ON agent_work_item_events
BEGIN SELECT RAISE(ABORT, 'agent work item events cannot be deleted'); END;
