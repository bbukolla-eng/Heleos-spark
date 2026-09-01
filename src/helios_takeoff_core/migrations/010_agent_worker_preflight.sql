-- Harden the P1A execution spine with explicit worker/adapter and policy lineage.
-- Columns remain nullable for rows written before this forward migration; the
-- insert triggers below require every new row to satisfy the hardened contract.
ALTER TABLE agent_workers ADD COLUMN worker_code TEXT;

CREATE UNIQUE INDEX agent_workers_by_worker_code
ON agent_workers(worker_code);

CREATE TRIGGER agent_workers_require_worker_code
BEFORE INSERT ON agent_workers
WHEN NEW.worker_code IS NULL
  OR NEW.worker_code = ''
  OR NEW.worker_code GLOB '*[^a-z0-9-]*'
  OR NEW.worker_code GLOB '-*'
  OR NEW.worker_code GLOB '*-'
  OR NEW.worker_code LIKE '%--%'
BEGIN
    SELECT RAISE(ABORT, 'agent workers require a lowercase hyphenated worker code');
END;

CREATE TABLE agent_worker_adapter_assignments (
    worker_id TEXT NOT NULL REFERENCES agent_workers(id) ON DELETE RESTRICT,
    adapter_revision_id TEXT NOT NULL REFERENCES agent_adapter_revisions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(worker_id, adapter_revision_id)
);

CREATE TRIGGER agent_worker_adapter_assignments_are_immutable
BEFORE UPDATE ON agent_worker_adapter_assignments
BEGIN SELECT RAISE(ABORT, 'agent worker adapter assignments are immutable'); END;

CREATE TRIGGER agent_worker_adapter_assignments_cannot_be_deleted
BEFORE DELETE ON agent_worker_adapter_assignments
BEGIN SELECT RAISE(ABORT, 'agent worker adapter assignments cannot be deleted'); END;

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

ALTER TABLE agent_worker_availability_events ADD COLUMN adapter_revision_id TEXT
    REFERENCES agent_adapter_revisions(id) ON DELETE RESTRICT;
ALTER TABLE agent_worker_availability_events ADD COLUMN reason_code TEXT;

CREATE TRIGGER agent_worker_availability_events_require_assignment
BEFORE INSERT ON agent_worker_availability_events
WHEN NEW.adapter_revision_id IS NULL
  OR NEW.reason_code IS NULL
  OR trim(NEW.reason_code) = ''
  OR NOT EXISTS (
      SELECT 1 FROM agent_worker_adapter_assignments
      WHERE worker_id = NEW.worker_id
        AND adapter_revision_id = NEW.adapter_revision_id
  )
BEGIN
    SELECT RAISE(ABORT, 'worker availability requires a registered adapter assignment and reason');
END;

CREATE INDEX agent_worker_availability_events_by_assignment
ON agent_worker_availability_events(worker_id, adapter_revision_id, created_at, id);

ALTER TABLE agent_jobs ADD COLUMN requested_by_actor_id TEXT
    REFERENCES actors(id) ON DELETE RESTRICT;
ALTER TABLE agent_jobs ADD COLUMN policy_sha256 TEXT;

CREATE TRIGGER agent_jobs_require_actor_and_policy
BEFORE INSERT ON agent_jobs
WHEN NEW.requested_by_actor_id IS NULL
  OR NOT EXISTS (SELECT 1 FROM actors WHERE id = NEW.requested_by_actor_id)
  OR NEW.policy_sha256 IS NULL
  OR length(NEW.policy_sha256) <> 64
  OR NEW.policy_sha256 GLOB '*[^0-9a-f]*'
BEGIN
    SELECT RAISE(ABORT, 'agent jobs require a real actor and lowercase SHA-256 policy digest');
END;

ALTER TABLE agent_workflow_runs ADD COLUMN workflow_definition_sha256 TEXT;

CREATE TRIGGER agent_workflow_runs_require_definition_digest
BEFORE INSERT ON agent_workflow_runs
WHEN NEW.workflow_definition_sha256 IS NULL
  OR length(NEW.workflow_definition_sha256) <> 64
  OR NEW.workflow_definition_sha256 GLOB '*[^0-9a-f]*'
BEGIN
    SELECT RAISE(ABORT, 'agent workflow runs require a lowercase SHA-256 definition digest');
END;

ALTER TABLE agent_work_item_events ADD COLUMN reason_code TEXT;
ALTER TABLE agent_work_item_events ADD COLUMN detail_json TEXT;

CREATE TRIGGER agent_work_item_events_require_reason_and_object_detail
BEFORE INSERT ON agent_work_item_events
WHEN NEW.reason_code IS NULL
  OR trim(NEW.reason_code) = ''
  OR CASE
      WHEN NEW.detail_json IS NULL THEN 0
      WHEN json_valid(NEW.detail_json) = 0 THEN 1
      WHEN json_type(NEW.detail_json) <> 'object' THEN 1
      ELSE 0
     END
BEGIN
    SELECT RAISE(ABORT, 'agent work item events require a reason and JSON object detail');
END;

CREATE TABLE agent_execution_attempt_artifacts (
    execution_attempt_id TEXT NOT NULL REFERENCES agent_execution_attempts(id) ON DELETE RESTRICT,
    artifact_id TEXT NOT NULL REFERENCES agent_artifacts(id) ON DELETE RESTRICT,
    purpose TEXT NOT NULL CHECK(purpose IN ('INPUT', 'STDOUT', 'STDERR', 'REPORT', 'ERROR')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(execution_attempt_id, artifact_id, purpose)
);

CREATE TRIGGER agent_execution_attempt_artifacts_require_job_lineage
BEFORE INSERT ON agent_execution_attempt_artifacts
WHEN NOT EXISTS (
    SELECT 1
    FROM agent_execution_attempts AS attempt
    JOIN agent_work_items AS item ON item.id = attempt.work_item_id
    JOIN agent_artifacts AS artifact ON artifact.id = NEW.artifact_id
    WHERE attempt.id = NEW.execution_attempt_id
      AND artifact.project_id = item.project_id
      AND artifact.revision_set_id = item.revision_set_id
)
BEGIN
    SELECT RAISE(ABORT, 'attempt artifacts must match the attempt job baseline');
END;

CREATE TRIGGER agent_execution_attempt_artifacts_are_immutable
BEFORE UPDATE ON agent_execution_attempt_artifacts
BEGIN SELECT RAISE(ABORT, 'agent execution attempt artifacts are immutable'); END;

CREATE TRIGGER agent_execution_attempt_artifacts_cannot_be_deleted
BEFORE DELETE ON agent_execution_attempt_artifacts
BEGIN SELECT RAISE(ABORT, 'agent execution attempt artifacts cannot be deleted'); END;
