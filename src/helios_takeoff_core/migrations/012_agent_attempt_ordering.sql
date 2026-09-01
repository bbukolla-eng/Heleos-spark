-- Reserve migration 012 for final Task 3 attempt ordering and lifecycle gates.
-- Task 4 execution artifact outcomes begin at migration 013.

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

CREATE TRIGGER agent_work_item_leased_event_requires_current_budgeted_attempt
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'LEASED'
AND NOT EXISTS (
    SELECT 1
    FROM agent_execution_attempts AS attempt
    JOIN agent_work_items AS item ON item.id = attempt.work_item_id
    WHERE attempt.id = NEW.execution_attempt_id
      AND item.id = NEW.work_item_id
      AND attempt.attempt_number <= item.max_attempts
      AND attempt.attempt_number = (
          SELECT COUNT(*)
          FROM agent_execution_attempts AS existing
          WHERE existing.work_item_id = item.id
      )
      AND attempt.attempt_number = (
          SELECT COUNT(*) + 1
          FROM agent_work_item_events AS retry
          WHERE retry.work_item_id = item.id
            AND retry.event_type = 'RETRY_SCHEDULED'
      )
)
BEGIN
    SELECT RAISE(ABORT, 'LEASED requires the current sequential attempt within budget');
END;

CREATE TRIGGER agent_work_item_retry_requires_remaining_attempt_budget
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'RETRY_SCHEDULED'
AND NOT EXISTS (
    SELECT 1
    FROM agent_execution_attempts AS attempt
    JOIN agent_work_items AS item ON item.id = attempt.work_item_id
    WHERE attempt.id = NEW.execution_attempt_id
      AND item.id = NEW.work_item_id
      AND attempt.attempt_number < item.max_attempts
)
BEGIN
    SELECT RAISE(ABORT, 'RETRY_SCHEDULED requires remaining attempt budget');
END;

DROP TRIGGER agent_work_item_blocked_event_requires_preflight_state;

CREATE TRIGGER agent_work_item_blocked_event_requires_preflight_state
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'BLOCKED'
AND NOT (
    (
        NEW.execution_attempt_id IS NULL
        AND COALESCE((
            SELECT event_type FROM agent_work_item_events
            WHERE work_item_id = NEW.work_item_id
            ORDER BY created_at DESC, rowid DESC LIMIT 1
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
                ORDER BY created_at DESC, rowid DESC LIMIT 1
            )
              AND event_type = 'STARTED'
              AND execution_attempt_id = NEW.execution_attempt_id
        )
    )
)
BEGIN
    SELECT RAISE(ABORT, 'BLOCKED requires preflight state or the immediately preceding owned started attempt');
END;

CREATE TRIGGER agent_work_item_event_reason_rejects_embedded_nul
BEFORE INSERT ON agent_work_item_events
WHEN instr(CAST(NEW.reason_code AS BLOB), X'00') > 0
BEGIN
    SELECT RAISE(ABORT, 'agent work item event reason cannot contain NUL');
END;

CREATE TRIGGER agent_worker_availability_reason_rejects_embedded_nul
BEFORE INSERT ON agent_worker_availability_events
WHEN instr(CAST(NEW.reason_code AS BLOB), X'00') > 0
BEGIN
    SELECT RAISE(ABORT, 'worker availability reason cannot contain NUL');
END;
