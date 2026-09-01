CREATE TABLE engine_store_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    applied_at TEXT NOT NULL
);

CREATE TABLE engine_packs (
    pack_sha256 TEXT PRIMARY KEY CHECK(length(pack_sha256) = 64 AND pack_sha256 NOT GLOB '*[^0-9a-f]*'),
    protocol TEXT NOT NULL CHECK(protocol = 'helios.engine.domain-pack/v2'),
    pack_code TEXT NOT NULL,
    version TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    supersedes_pack_sha256 TEXT REFERENCES engine_packs(pack_sha256) ON DELETE RESTRICT,
    canonical_json BLOB NOT NULL,
    projection_generation INTEGER NOT NULL CHECK(projection_generation > 0),
    source_count INTEGER NOT NULL CHECK(source_count > 0),
    definition_count INTEGER NOT NULL CHECK(definition_count > 0),
    relation_count INTEGER NOT NULL CHECK(relation_count >= 0),
    citation_count INTEGER NOT NULL CHECK(citation_count > 0),
    imported_at TEXT NOT NULL,
    UNIQUE(pack_code, version)
);

CREATE TABLE engine_source_index (
    pack_sha256 TEXT NOT NULL REFERENCES engine_packs(pack_sha256) ON DELETE RESTRICT,
    source_id TEXT NOT NULL,
    projection_generation INTEGER NOT NULL CHECK(projection_generation > 0),
    source_json TEXT NOT NULL CHECK(json_valid(source_json)),
    PRIMARY KEY(pack_sha256, source_id)
) WITHOUT ROWID;

CREATE TABLE engine_definition_index (
    pack_sha256 TEXT NOT NULL REFERENCES engine_packs(pack_sha256) ON DELETE RESTRICT,
    definition_code TEXT NOT NULL,
    kind TEXT NOT NULL,
    family TEXT NOT NULL,
    projection_generation INTEGER NOT NULL CHECK(projection_generation > 0),
    definition_json TEXT NOT NULL CHECK(json_valid(definition_json)),
    PRIMARY KEY(pack_sha256, definition_code)
) WITHOUT ROWID;

CREATE TABLE engine_relation_index (
    pack_sha256 TEXT NOT NULL REFERENCES engine_packs(pack_sha256) ON DELETE RESTRICT,
    relation_code TEXT NOT NULL,
    kind TEXT NOT NULL,
    from_code TEXT NOT NULL,
    to_code TEXT NOT NULL,
    projection_generation INTEGER NOT NULL CHECK(projection_generation > 0),
    relation_json TEXT NOT NULL CHECK(json_valid(relation_json)),
    PRIMARY KEY(pack_sha256, relation_code),
    FOREIGN KEY(pack_sha256, from_code)
        REFERENCES engine_definition_index(pack_sha256, definition_code) ON DELETE RESTRICT,
    FOREIGN KEY(pack_sha256, to_code)
        REFERENCES engine_definition_index(pack_sha256, definition_code) ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE engine_citation_index (
    pack_sha256 TEXT NOT NULL REFERENCES engine_packs(pack_sha256) ON DELETE RESTRICT,
    assertion_kind TEXT NOT NULL CHECK(assertion_kind IN ('DEFINITION', 'RELATION')),
    assertion_code TEXT NOT NULL,
    citation_ordinal INTEGER NOT NULL CHECK(citation_ordinal >= 0),
    source_id TEXT NOT NULL,
    projection_generation INTEGER NOT NULL CHECK(projection_generation > 0),
    citation_json TEXT NOT NULL CHECK(json_valid(citation_json)),
    PRIMARY KEY(pack_sha256, assertion_kind, assertion_code, citation_ordinal),
    FOREIGN KEY(pack_sha256, source_id)
        REFERENCES engine_source_index(pack_sha256, source_id) ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE engine_registry_events (
    pack_sha256 TEXT NOT NULL REFERENCES engine_packs(pack_sha256) ON DELETE RESTRICT,
    event_sequence INTEGER NOT NULL CHECK(event_sequence > 0),
    event_type TEXT NOT NULL CHECK(event_type IN ('PACK_IMPORTED', 'PROJECTIONS_REBUILT')),
    projection_generation INTEGER NOT NULL CHECK(projection_generation > 0),
    previous_event_sha256 TEXT,
    event_sha256 TEXT NOT NULL UNIQUE CHECK(length(event_sha256) = 64 AND event_sha256 NOT GLOB '*[^0-9a-f]*'),
    event_json TEXT NOT NULL CHECK(json_valid(event_json)),
    created_at TEXT NOT NULL,
    PRIMARY KEY(pack_sha256, event_sequence)
) WITHOUT ROWID;

CREATE INDEX engine_definitions_by_kind_family
ON engine_definition_index(pack_sha256, kind, family, definition_code);
CREATE INDEX engine_relations_by_outbound
ON engine_relation_index(pack_sha256, from_code, kind, relation_code);
CREATE INDEX engine_relations_by_inbound
ON engine_relation_index(pack_sha256, to_code, kind, relation_code);
CREATE INDEX engine_citations_by_source
ON engine_citation_index(pack_sha256, source_id, assertion_kind, assertion_code);

CREATE TRIGGER engine_packs_validate_canonical_insert
BEFORE INSERT ON engine_packs
BEGIN
    SELECT RAISE(ABORT, 'engine pack canonical payload is invalid')
    WHERE helios_v2_pack_is_canonical(
        NEW.canonical_json, NEW.pack_sha256, NEW.protocol, NEW.pack_code,
        NEW.version, NEW.valid_from, NEW.supersedes_pack_sha256,
        NEW.source_count, NEW.definition_count, NEW.relation_count, NEW.citation_count
    ) <> 1;
    SELECT RAISE(ABORT, 'engine pack initial projection generation is invalid')
    WHERE NEW.projection_generation <> 1;
END;
CREATE TRIGGER engine_packs_validate_supersession_insert
BEFORE INSERT ON engine_packs
WHEN NEW.supersedes_pack_sha256 IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'engine pack supersession lineage is invalid')
    WHERE NEW.pack_sha256 = NEW.supersedes_pack_sha256
       OR NOT EXISTS (
            SELECT 1 FROM engine_packs WHERE pack_sha256 = NEW.supersedes_pack_sha256
       )
       OR NEW.pack_code <> (
            SELECT pack_code FROM engine_packs WHERE pack_sha256 = NEW.supersedes_pack_sha256
       )
       OR NEW.version = (
            SELECT version FROM engine_packs WHERE pack_sha256 = NEW.supersedes_pack_sha256
       )
       OR NEW.valid_from <= (
            SELECT valid_from FROM engine_packs WHERE pack_sha256 = NEW.supersedes_pack_sha256
       );
END;
CREATE TRIGGER engine_packs_are_immutable
BEFORE UPDATE ON engine_packs
BEGIN
    SELECT RAISE(ABORT, 'engine pack canonical authority is immutable')
    WHERE NEW.pack_sha256 IS NOT OLD.pack_sha256
       OR NEW.protocol IS NOT OLD.protocol
       OR NEW.pack_code IS NOT OLD.pack_code
       OR NEW.version IS NOT OLD.version
       OR NEW.valid_from IS NOT OLD.valid_from
       OR NEW.supersedes_pack_sha256 IS NOT OLD.supersedes_pack_sha256
       OR NEW.canonical_json IS NOT OLD.canonical_json
       OR NEW.source_count IS NOT OLD.source_count
       OR NEW.definition_count IS NOT OLD.definition_count
       OR NEW.relation_count IS NOT OLD.relation_count
       OR NEW.citation_count IS NOT OLD.citation_count
       OR NEW.imported_at IS NOT OLD.imported_at;
    SELECT RAISE(ABORT, 'engine pack projection generation advance is invalid')
    WHERE NEW.projection_generation <> OLD.projection_generation + 1
       OR NOT EXISTS (
            SELECT 1 FROM engine_registry_events
            WHERE pack_sha256 = OLD.pack_sha256
              AND projection_generation = OLD.projection_generation
              AND event_sequence = (
                  SELECT MAX(event_sequence) FROM engine_registry_events
                  WHERE pack_sha256 = OLD.pack_sha256
              )
       );
    SELECT RAISE(ABORT, 'engine pack projection generation is not fully rewritten')
    WHERE (SELECT COUNT(*) FROM engine_source_index WHERE pack_sha256 = OLD.pack_sha256) <> NEW.source_count
       OR (SELECT COUNT(*) FROM engine_source_index WHERE pack_sha256 = OLD.pack_sha256 AND projection_generation = NEW.projection_generation) <> NEW.source_count
       OR (SELECT COUNT(*) FROM engine_definition_index WHERE pack_sha256 = OLD.pack_sha256) <> NEW.definition_count
       OR (SELECT COUNT(*) FROM engine_definition_index WHERE pack_sha256 = OLD.pack_sha256 AND projection_generation = NEW.projection_generation) <> NEW.definition_count
       OR (SELECT COUNT(*) FROM engine_relation_index WHERE pack_sha256 = OLD.pack_sha256) <> NEW.relation_count
       OR (SELECT COUNT(*) FROM engine_relation_index WHERE pack_sha256 = OLD.pack_sha256 AND projection_generation = NEW.projection_generation) <> NEW.relation_count
       OR (SELECT COUNT(*) FROM engine_citation_index WHERE pack_sha256 = OLD.pack_sha256) <> NEW.citation_count
       OR (SELECT COUNT(*) FROM engine_citation_index WHERE pack_sha256 = OLD.pack_sha256 AND projection_generation = NEW.projection_generation) <> NEW.citation_count;
END;
CREATE TRIGGER engine_packs_cannot_be_deleted
BEFORE DELETE ON engine_packs
BEGIN SELECT RAISE(ABORT, 'engine packs cannot be deleted'); END;

CREATE TRIGGER engine_relation_index_validates_insert
BEFORE INSERT ON engine_relation_index
BEGIN
    SELECT RAISE(ABORT, 'relation endpoint is missing')
    WHERE NOT EXISTS (
        SELECT 1 FROM engine_definition_index
        WHERE pack_sha256 = NEW.pack_sha256 AND definition_code = NEW.from_code
    ) OR NOT EXISTS (
        SELECT 1 FROM engine_definition_index
        WHERE pack_sha256 = NEW.pack_sha256 AND definition_code = NEW.to_code
    );
END;
CREATE TRIGGER engine_relation_index_validates_update
BEFORE UPDATE ON engine_relation_index
BEGIN
    SELECT RAISE(ABORT, 'relation endpoint is missing')
    WHERE NOT EXISTS (
        SELECT 1 FROM engine_definition_index
        WHERE pack_sha256 = NEW.pack_sha256 AND definition_code = NEW.from_code
    ) OR NOT EXISTS (
        SELECT 1 FROM engine_definition_index
        WHERE pack_sha256 = NEW.pack_sha256 AND definition_code = NEW.to_code
    );
END;

CREATE TRIGGER engine_citation_index_validates_insert
BEFORE INSERT ON engine_citation_index
BEGIN
    SELECT RAISE(ABORT, 'citation source is missing')
    WHERE NOT EXISTS (
        SELECT 1 FROM engine_source_index
        WHERE pack_sha256 = NEW.pack_sha256 AND source_id = NEW.source_id
    );
    SELECT RAISE(ABORT, 'citation assertion is missing')
    WHERE (NEW.assertion_kind = 'DEFINITION' AND NOT EXISTS (
        SELECT 1 FROM engine_definition_index
        WHERE pack_sha256 = NEW.pack_sha256 AND definition_code = NEW.assertion_code
    )) OR (NEW.assertion_kind = 'RELATION' AND NOT EXISTS (
        SELECT 1 FROM engine_relation_index
        WHERE pack_sha256 = NEW.pack_sha256 AND relation_code = NEW.assertion_code
    ));
END;
CREATE TRIGGER engine_citation_index_validates_update
BEFORE UPDATE ON engine_citation_index
BEGIN
    SELECT RAISE(ABORT, 'citation source is missing')
    WHERE NOT EXISTS (
        SELECT 1 FROM engine_source_index
        WHERE pack_sha256 = NEW.pack_sha256 AND source_id = NEW.source_id
    );
    SELECT RAISE(ABORT, 'citation assertion is missing')
    WHERE (NEW.assertion_kind = 'DEFINITION' AND NOT EXISTS (
        SELECT 1 FROM engine_definition_index
        WHERE pack_sha256 = NEW.pack_sha256 AND definition_code = NEW.assertion_code
    )) OR (NEW.assertion_kind = 'RELATION' AND NOT EXISTS (
        SELECT 1 FROM engine_relation_index
        WHERE pack_sha256 = NEW.pack_sha256 AND relation_code = NEW.assertion_code
    ));
END;

CREATE TRIGGER engine_registry_events_are_immutable
BEFORE UPDATE ON engine_registry_events
BEGIN SELECT RAISE(ABORT, 'engine registry events are immutable'); END;
CREATE TRIGGER engine_registry_events_cannot_be_deleted
BEFORE DELETE ON engine_registry_events
BEGIN SELECT RAISE(ABORT, 'engine registry events cannot be deleted'); END;
CREATE TRIGGER engine_registry_events_validate_insert
BEFORE INSERT ON engine_registry_events
BEGIN
    SELECT RAISE(ABORT, 'engine registry event lifecycle is invalid')
    WHERE (
        NEW.event_sequence = 1 AND (
            NEW.event_type <> 'PACK_IMPORTED'
            OR NEW.projection_generation <> 1
            OR NEW.previous_event_sha256 IS NOT NULL
            OR EXISTS (
                SELECT 1 FROM engine_registry_events WHERE pack_sha256 = NEW.pack_sha256
            )
        )
    ) OR (
        NEW.event_sequence > 1 AND (
            NEW.event_type <> 'PROJECTIONS_REBUILT'
            OR NOT EXISTS (
                SELECT 1 FROM engine_registry_events
                WHERE pack_sha256 = NEW.pack_sha256
                  AND event_sequence = NEW.event_sequence - 1
                  AND event_sha256 = NEW.previous_event_sha256
                  AND projection_generation = NEW.projection_generation - 1
            )
            OR EXISTS (
                SELECT 1 FROM engine_registry_events
                WHERE pack_sha256 = NEW.pack_sha256
                  AND event_sequence >= NEW.event_sequence
            )
        )
    );
    SELECT RAISE(ABORT, 'engine registry event projection generation is unattested')
    WHERE NOT EXISTS (
            SELECT 1 FROM engine_packs
            WHERE pack_sha256 = NEW.pack_sha256
              AND projection_generation = NEW.projection_generation
              AND (SELECT COUNT(*) FROM engine_source_index WHERE pack_sha256 = NEW.pack_sha256) = source_count
              AND (SELECT COUNT(*) FROM engine_source_index WHERE pack_sha256 = NEW.pack_sha256 AND projection_generation = NEW.projection_generation) = source_count
              AND (SELECT COUNT(*) FROM engine_definition_index WHERE pack_sha256 = NEW.pack_sha256) = definition_count
              AND (SELECT COUNT(*) FROM engine_definition_index WHERE pack_sha256 = NEW.pack_sha256 AND projection_generation = NEW.projection_generation) = definition_count
              AND (SELECT COUNT(*) FROM engine_relation_index WHERE pack_sha256 = NEW.pack_sha256) = relation_count
              AND (SELECT COUNT(*) FROM engine_relation_index WHERE pack_sha256 = NEW.pack_sha256 AND projection_generation = NEW.projection_generation) = relation_count
              AND (SELECT COUNT(*) FROM engine_citation_index WHERE pack_sha256 = NEW.pack_sha256) = citation_count
              AND (SELECT COUNT(*) FROM engine_citation_index WHERE pack_sha256 = NEW.pack_sha256 AND projection_generation = NEW.projection_generation) = citation_count
       );
    SELECT RAISE(ABORT, 'engine registry event canonical payload is invalid')
    WHERE helios_v2_event_is_canonical(
        NEW.event_json, NEW.event_sha256, NEW.pack_sha256, NEW.event_sequence,
        NEW.event_type, NEW.projection_generation, NEW.previous_event_sha256, NEW.created_at
    ) <> 1;
END;
