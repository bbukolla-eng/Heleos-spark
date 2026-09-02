-- One physical CAS object may be referenced by immutable metadata belonging to
-- more than one frozen baseline. Rebuild only the metadata table so its
-- deduplication key includes baseline lineage; the store key remains the
-- physical content identity shared by those rows.
CREATE TEMP TABLE agent_artifacts_016_snapshot AS
SELECT rowid AS preserved_rowid, * FROM agent_artifacts;

DROP TABLE agent_artifacts;

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
    UNIQUE(project_id, revision_set_id, sha256, media_type, schema_version, store_key)
);

INSERT INTO agent_artifacts(
    rowid, id, project_id, revision_set_id, sha256, byte_size, media_type,
    schema_version, store_key, created_at
)
SELECT preserved_rowid, id, project_id, revision_set_id, sha256, byte_size,
       media_type, schema_version, store_key, created_at
FROM agent_artifacts_016_snapshot;

DROP TABLE agent_artifacts_016_snapshot;

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

CREATE TRIGGER agent_artifacts_are_immutable BEFORE UPDATE ON agent_artifacts
BEGIN SELECT RAISE(ABORT, 'agent artifacts are immutable'); END;

CREATE TRIGGER agent_artifacts_cannot_be_deleted BEFORE DELETE ON agent_artifacts
BEGIN SELECT RAISE(ABORT, 'agent artifacts cannot be deleted'); END;
