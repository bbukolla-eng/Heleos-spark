-- Bind each attempt-artifact purpose to the designated immutable artifact
-- identity and media/schema contract. SQLite can prove ledger lineage and
-- metadata identity; process execution and stored-byte validation remain the
-- runner/service boundary's responsibility.
CREATE TRIGGER agent_execution_attempt_artifacts_require_purpose_contract
BEFORE INSERT ON agent_execution_attempt_artifacts
WHEN NOT EXISTS (
    SELECT 1
    FROM agent_execution_attempts AS attempt
    JOIN agent_work_items AS item ON item.id = attempt.work_item_id
    JOIN agent_artifacts AS artifact ON artifact.id = NEW.artifact_id
    WHERE attempt.id = NEW.execution_attempt_id
      AND artifact.project_id = item.project_id
      AND artifact.revision_set_id = item.revision_set_id
      AND CASE NEW.purpose
          WHEN 'INPUT' THEN
              artifact.id = item.input_artifact_id
              AND artifact.media_type = 'application/vnd.helios.revision-set-manifest+json'
              AND artifact.schema_version = 'helios.p0.revision-set-manifest/v1'
          WHEN 'STDOUT' THEN
              artifact.id <> item.input_artifact_id
              AND artifact.media_type = 'application/vnd.helios.worker-stdout'
              AND artifact.schema_version = 'helios.p1a.worker-stdout/v1'
          WHEN 'STDERR' THEN
              artifact.id <> item.input_artifact_id
              AND artifact.media_type = 'application/vnd.helios.worker-stderr'
              AND artifact.schema_version = 'helios.p1a.worker-stderr/v1'
          WHEN 'REPORT' THEN
              artifact.id <> item.input_artifact_id
              AND artifact.media_type = 'application/vnd.helios.baseline-audit-report+json'
              AND artifact.schema_version = 'helios.p1a.baseline-audit-report/v1'
          WHEN 'ERROR' THEN
              artifact.id <> item.input_artifact_id
              AND artifact.media_type = 'application/vnd.helios.execution-error+json'
              AND artifact.schema_version = 'helios.p1a.execution-error/v1'
          ELSE 0
      END
)
BEGIN
    SELECT RAISE(ABORT, 'attempt artifact purpose requires its designated identity, lineage, media type, and schema');
END;

CREATE TRIGGER agent_work_item_success_requires_report_digest_binding
BEFORE INSERT ON agent_work_item_events
WHEN NEW.event_type = 'SUCCEEDED'
AND NOT EXISTS (
    SELECT 1
    FROM agent_execution_attempt_artifacts AS link
    JOIN agent_artifacts AS artifact ON artifact.id = link.artifact_id
    WHERE link.execution_attempt_id = NEW.execution_attempt_id
      AND link.purpose = 'REPORT'
      AND json_valid(NEW.detail_json) = 1
      AND json_type(NEW.detail_json) = 'object'
      AND json_type(NEW.detail_json, '$.report_sha256') = 'text'
      AND json_extract(NEW.detail_json, '$.report_sha256') = artifact.sha256
)
BEGIN
    SELECT RAISE(ABORT, 'successful event must bind the exact report artifact digest');
END;
