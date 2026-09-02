-- Preserve legacy histories while rejecting illegal lifecycle facts on new writes.
CREATE TRIGGER agent_work_item_events_require_canonical_reason_code
BEFORE INSERT ON agent_work_item_events
WHEN NEW.reason_code IS NULL
  OR NEW.reason_code = ''
  OR substr(NEW.reason_code, 1, 1) NOT GLOB '[A-Z]'
  OR NEW.reason_code GLOB '*[^A-Z0-9_]*'
BEGIN
    SELECT RAISE(ABORT, 'agent work item event reason must be an uppercase token');
END;

CREATE TRIGGER agent_worker_availability_events_require_canonical_reason_code
BEFORE INSERT ON agent_worker_availability_events
WHEN NEW.reason_code IS NULL
  OR NEW.reason_code = ''
  OR substr(NEW.reason_code, 1, 1) NOT GLOB '[A-Z]'
  OR NEW.reason_code GLOB '*[^A-Z0-9_]*'
BEGIN
    SELECT RAISE(ABORT, 'worker availability reason must be an uppercase token');
END;

CREATE TRIGGER agent_work_item_first_event_must_be_queued
BEFORE INSERT ON agent_work_item_events
WHEN NOT EXISTS (
    SELECT 1 FROM agent_work_item_events WHERE work_item_id = NEW.work_item_id
)
AND (NEW.event_type <> 'QUEUED' OR NEW.execution_attempt_id IS NOT NULL)
BEGIN
    SELECT RAISE(ABORT, 'first agent work item event must be QUEUED without an attempt');
END;

CREATE TRIGGER agent_work_item_queued_event_must_be_first
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'QUEUED'
AND (
    NEW.execution_attempt_id IS NOT NULL
    OR EXISTS (
        SELECT 1 FROM agent_work_item_events WHERE work_item_id = NEW.work_item_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'QUEUED must be the first event and cannot name an attempt');
END;

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
        ORDER BY created_at DESC, rowid DESC LIMIT 1
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
            ORDER BY created_at DESC, rowid DESC LIMIT 1
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
            ORDER BY created_at DESC, rowid DESC LIMIT 1
        )
          AND event_type = 'STARTED'
          AND execution_attempt_id = NEW.execution_attempt_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'agent work item outcome requires the immediately preceding started attempt');
END;

CREATE TRIGGER agent_work_item_blocked_event_requires_preflight_state
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'BLOCKED'
AND (
    NEW.execution_attempt_id IS NOT NULL
    OR COALESCE((
        SELECT event_type FROM agent_work_item_events
        WHERE work_item_id = NEW.work_item_id
        ORDER BY created_at DESC, rowid DESC LIMIT 1
    ), '') NOT IN ('QUEUED', 'RETRY_SCHEDULED')
)
BEGIN
    SELECT RAISE(ABORT, 'BLOCKED requires queued or retry-scheduled preflight state without an attempt');
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
            ORDER BY created_at DESC, rowid DESC LIMIT 1
        )
          AND event_type = 'STARTED'
          AND execution_attempt_id = NEW.execution_attempt_id
    )
)
BEGIN
    SELECT RAISE(ABORT, 'CANCELLED requires the immediately preceding started attempt');
END;

CREATE TRIGGER agent_work_item_success_requires_input_artifact
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'SUCCEEDED'
AND NOT EXISTS (
    SELECT 1 FROM agent_execution_attempt_artifacts
    WHERE execution_attempt_id = NEW.execution_attempt_id
      AND purpose = 'INPUT'
)
BEGIN
    SELECT RAISE(ABORT, 'successful attempt requires an immutable input artifact link');
END;
