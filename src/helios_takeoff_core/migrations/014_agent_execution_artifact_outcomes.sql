-- Task 4 makes terminal execution claims depend on the immutable artifacts
-- produced by the exact attempt. Existing histories remain readable; every
-- new result link and outcome must satisfy this forward contract.
CREATE TRIGGER agent_execution_attempt_artifacts_require_one_purpose_link
BEFORE INSERT ON agent_execution_attempt_artifacts
WHEN EXISTS (
    SELECT 1
    FROM agent_execution_attempt_artifacts AS existing
    WHERE existing.execution_attempt_id = NEW.execution_attempt_id
      AND existing.purpose = NEW.purpose
)
BEGIN
    SELECT RAISE(ABORT, 'execution attempts permit exactly one artifact link per purpose');
END;

CREATE TRIGGER agent_work_item_success_requires_complete_result_artifacts
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'SUCCEEDED'
AND EXISTS (
    SELECT 1
    FROM (
        SELECT 'INPUT' AS purpose
        UNION ALL SELECT 'STDOUT'
        UNION ALL SELECT 'STDERR'
        UNION ALL SELECT 'REPORT'
    ) AS required
    WHERE NOT EXISTS (
        SELECT 1
        FROM agent_execution_attempt_artifacts AS link
        WHERE link.execution_attempt_id = NEW.execution_attempt_id
          AND link.purpose = required.purpose
    )
)
BEGIN
    SELECT RAISE(ABORT, 'successful attempt requires INPUT, STDOUT, STDERR, and REPORT artifacts');
END;

CREATE TRIGGER agent_work_item_unsuccessful_outcome_requires_complete_artifacts
BEFORE INSERT ON agent_work_item_events
WHEN (
    NEW.event_type IN ('RETRY_SCHEDULED', 'FAILED', 'ESCALATED')
    OR (NEW.event_type = 'BLOCKED' AND NEW.execution_attempt_id IS NOT NULL)
)
AND EXISTS (
    SELECT 1
    FROM (
        SELECT 'INPUT' AS purpose
        UNION ALL SELECT 'STDOUT'
        UNION ALL SELECT 'STDERR'
        UNION ALL SELECT 'ERROR'
    ) AS required
    WHERE NOT EXISTS (
        SELECT 1
        FROM agent_execution_attempt_artifacts AS link
        WHERE link.execution_attempt_id = NEW.execution_attempt_id
          AND link.purpose = required.purpose
    )
)
BEGIN
    SELECT RAISE(ABORT, 'unsuccessful attempt requires INPUT, STDOUT, STDERR, and ERROR artifacts');
END;
