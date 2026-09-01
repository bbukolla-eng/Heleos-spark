-- Immutable, content-addressed HELIOS P1B definition packs. These tables are
-- intentionally independent from P0 project authority and P1A execution state.
CREATE TABLE engine_domain_packs (
    id TEXT PRIMARY KEY CHECK(id <> ''),
    protocol TEXT NOT NULL CHECK(protocol = 'helios.p1b.domain-pack/v1'),
    pack_code TEXT NOT NULL CHECK(pack_code <> '' AND pack_code = trim(pack_code)),
    version TEXT NOT NULL CHECK(version <> '' AND version = trim(version)),
    title TEXT NOT NULL CHECK(title <> '' AND title = trim(title)),
    jurisdiction TEXT NOT NULL CHECK(jurisdiction <> '' AND jurisdiction = trim(jurisdiction)),
    sha256 TEXT NOT NULL UNIQUE CHECK(
        length(sha256) = 64
        AND sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    canonical_json TEXT NOT NULL CHECK(json_valid(canonical_json)),
    created_at TEXT NOT NULL,
    UNIQUE(pack_code, version)
);

CREATE TABLE engine_pack_provenance (
    pack_id TEXT NOT NULL REFERENCES engine_domain_packs(id)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    kind TEXT NOT NULL CHECK(
        kind IN ('PUBLIC_URL', 'PROJECT_DOCUMENT', 'CONTENT_DIGEST')
    ),
    reference TEXT NOT NULL CHECK(reference <> '' AND reference = trim(reference)),
    PRIMARY KEY(pack_id, kind, reference)
) WITHOUT ROWID;

CREATE TABLE engine_catalog_items (
    pack_id TEXT NOT NULL REFERENCES engine_domain_packs(id)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    code TEXT NOT NULL CHECK(code <> '' AND code = trim(code)),
    type TEXT NOT NULL CHECK(
        type IN (
            'SYSTEM', 'COMPONENT', 'MATERIAL', 'CONNECTION',
            'ACCESSORY', 'EQUIPMENT', 'ASSEMBLY'
        )
    ),
    title TEXT NOT NULL CHECK(title <> '' AND title = trim(title)),
    uom TEXT NOT NULL CHECK(uom IN ('EA', 'LF', 'SF', 'CF', 'LB', 'FT', 'IN')),
    PRIMARY KEY(pack_id, code)
) WITHOUT ROWID;

CREATE TABLE engine_catalog_relations (
    pack_id TEXT NOT NULL,
    from_code TEXT NOT NULL,
    relation TEXT NOT NULL CHECK(relation <> '' AND relation = trim(relation)),
    to_code TEXT NOT NULL,
    PRIMARY KEY(pack_id, from_code, relation, to_code),
    FOREIGN KEY(pack_id, from_code)
        REFERENCES engine_catalog_items(pack_id, code)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(pack_id, to_code)
        REFERENCES engine_catalog_items(pack_id, code)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
) WITHOUT ROWID;

CREATE TABLE engine_rule_definitions (
    pack_id TEXT NOT NULL,
    code TEXT NOT NULL CHECK(code <> '' AND code = trim(code)),
    type TEXT NOT NULL CHECK(type IN ('CLASSIFY', 'REQUIRE', 'MEASURE', 'ASSEMBLE')),
    subject_code TEXT NOT NULL,
    required_observations_json TEXT NOT NULL CHECK(json_valid(required_observations_json)),
    required_evidence_json TEXT NOT NULL CHECK(json_valid(required_evidence_json)),
    output_claim_type TEXT NOT NULL CHECK(
        output_claim_type <> '' AND output_claim_type = trim(output_claim_type)
    ),
    output_uom TEXT CHECK(
        output_uom IS NULL OR output_uom IN ('EA', 'LF', 'SF', 'CF', 'LB', 'FT', 'IN')
    ),
    PRIMARY KEY(pack_id, code),
    FOREIGN KEY(pack_id, subject_code)
        REFERENCES engine_catalog_items(pack_id, code)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
) WITHOUT ROWID;

CREATE INDEX engine_catalog_relations_by_to_item
ON engine_catalog_relations(pack_id, to_code, relation, from_code);

-- Child projections are staged before the parent. Inserting the parent is the
-- one append-only seal operation, and it succeeds only for an exact projection
-- of the canonical document. The identity check also blocks INSERT OR REPLACE.
CREATE TRIGGER engine_domain_packs_validate_seal
BEFORE INSERT ON engine_domain_packs
BEGIN
    SELECT RAISE(ABORT, 'engine domain pack canonical payload is invalid')
    WHERE helios_domain_pack_is_canonical(NEW.canonical_json, NEW.sha256) <> 1;

    SELECT RAISE(ABORT, 'engine domain pack identity cannot be replaced')
    WHERE EXISTS (
        SELECT 1 FROM engine_domain_packs
        WHERE id = NEW.id
           OR (pack_code = NEW.pack_code AND version = NEW.version)
           OR sha256 = NEW.sha256
    );

    SELECT RAISE(ABORT, 'engine domain pack canonical header is incoherent')
    WHERE json_type(NEW.canonical_json, '$') IS NOT 'object'
       OR (SELECT COUNT(*) FROM json_each(NEW.canonical_json)) <> 9
       OR EXISTS (
           SELECT 1 FROM json_each(NEW.canonical_json)
           WHERE key NOT IN (
               'protocol', 'pack_code', 'version', 'title', 'jurisdiction',
               'provenance', 'catalog', 'relations', 'rules'
           )
       )
       OR json_extract(NEW.canonical_json, '$.protocol') IS NOT NEW.protocol
       OR json_extract(NEW.canonical_json, '$.pack_code') IS NOT NEW.pack_code
       OR json_extract(NEW.canonical_json, '$.version') IS NOT NEW.version
       OR json_extract(NEW.canonical_json, '$.title') IS NOT NEW.title
       OR json_extract(NEW.canonical_json, '$.jurisdiction') IS NOT NEW.jurisdiction;

    SELECT RAISE(ABORT, 'engine domain pack canonical projection counts are incoherent')
    WHERE json_type(NEW.canonical_json, '$.provenance') IS NOT 'array'
       OR json_type(NEW.canonical_json, '$.catalog') IS NOT 'array'
       OR json_type(NEW.canonical_json, '$.relations') IS NOT 'array'
       OR json_type(NEW.canonical_json, '$.rules') IS NOT 'array'
       OR (SELECT COUNT(*) FROM engine_pack_provenance WHERE pack_id = NEW.id)
            <> json_array_length(NEW.canonical_json, '$.provenance')
       OR (SELECT COUNT(*) FROM engine_catalog_items WHERE pack_id = NEW.id)
            <> json_array_length(NEW.canonical_json, '$.catalog')
       OR (SELECT COUNT(*) FROM engine_catalog_relations WHERE pack_id = NEW.id)
            <> json_array_length(NEW.canonical_json, '$.relations')
       OR (SELECT COUNT(*) FROM engine_rule_definitions WHERE pack_id = NEW.id)
            <> json_array_length(NEW.canonical_json, '$.rules');

    SELECT RAISE(ABORT, 'engine domain pack canonical provenance is incoherent')
    WHERE EXISTS (
        SELECT 1 FROM engine_pack_provenance AS projection
        WHERE projection.pack_id = NEW.id
          AND NOT EXISTS (
              SELECT 1 FROM json_each(NEW.canonical_json, '$.provenance') AS canonical
              WHERE json_type(canonical.value, '$') = 'object'
                AND (SELECT COUNT(*) FROM json_each(canonical.value)) = 2
                AND json_extract(canonical.value, '$.kind') IS projection.kind
                AND json_extract(canonical.value, '$.reference') IS projection.reference
          )
    ) OR EXISTS (
        SELECT 1 FROM json_each(NEW.canonical_json, '$.provenance') AS canonical
        WHERE NOT EXISTS (
            SELECT 1 FROM engine_pack_provenance AS projection
            WHERE projection.pack_id = NEW.id
              AND json_type(canonical.value, '$') = 'object'
              AND (SELECT COUNT(*) FROM json_each(canonical.value)) = 2
              AND projection.kind IS json_extract(canonical.value, '$.kind')
              AND projection.reference IS json_extract(canonical.value, '$.reference')
        )
    );

    SELECT RAISE(ABORT, 'engine domain pack canonical catalog is incoherent')
    WHERE EXISTS (
        SELECT 1 FROM engine_catalog_items AS projection
        WHERE projection.pack_id = NEW.id
          AND NOT EXISTS (
              SELECT 1 FROM json_each(NEW.canonical_json, '$.catalog') AS canonical
              WHERE json_type(canonical.value, '$') = 'object'
                AND (SELECT COUNT(*) FROM json_each(canonical.value)) = 4
                AND json_extract(canonical.value, '$.code') IS projection.code
                AND json_extract(canonical.value, '$.type') IS projection.type
                AND json_extract(canonical.value, '$.title') IS projection.title
                AND json_extract(canonical.value, '$.uom') IS projection.uom
          )
    ) OR EXISTS (
        SELECT 1 FROM json_each(NEW.canonical_json, '$.catalog') AS canonical
        WHERE NOT EXISTS (
            SELECT 1 FROM engine_catalog_items AS projection
            WHERE projection.pack_id = NEW.id
              AND json_type(canonical.value, '$') = 'object'
              AND (SELECT COUNT(*) FROM json_each(canonical.value)) = 4
              AND projection.code IS json_extract(canonical.value, '$.code')
              AND projection.type IS json_extract(canonical.value, '$.type')
              AND projection.title IS json_extract(canonical.value, '$.title')
              AND projection.uom IS json_extract(canonical.value, '$.uom')
        )
    );

    SELECT RAISE(ABORT, 'engine domain pack canonical relations are incoherent')
    WHERE EXISTS (
        SELECT 1 FROM engine_catalog_relations AS projection
        WHERE projection.pack_id = NEW.id
          AND NOT EXISTS (
              SELECT 1 FROM json_each(NEW.canonical_json, '$.relations') AS canonical
              WHERE json_type(canonical.value, '$') = 'object'
                AND (SELECT COUNT(*) FROM json_each(canonical.value)) = 3
                AND json_extract(canonical.value, '$.from_code') IS projection.from_code
                AND json_extract(canonical.value, '$.relation') IS projection.relation
                AND json_extract(canonical.value, '$.to_code') IS projection.to_code
          )
    ) OR EXISTS (
        SELECT 1 FROM json_each(NEW.canonical_json, '$.relations') AS canonical
        WHERE NOT EXISTS (
            SELECT 1 FROM engine_catalog_relations AS projection
            WHERE projection.pack_id = NEW.id
              AND json_type(canonical.value, '$') = 'object'
              AND (SELECT COUNT(*) FROM json_each(canonical.value)) = 3
              AND projection.from_code IS json_extract(canonical.value, '$.from_code')
              AND projection.relation IS json_extract(canonical.value, '$.relation')
              AND projection.to_code IS json_extract(canonical.value, '$.to_code')
        )
    );

    SELECT RAISE(ABORT, 'engine domain pack canonical rules are incoherent')
    WHERE EXISTS (
        SELECT 1 FROM engine_rule_definitions AS projection
        WHERE projection.pack_id = NEW.id
          AND NOT EXISTS (
              SELECT 1 FROM json_each(NEW.canonical_json, '$.rules') AS canonical
              WHERE json_type(canonical.value, '$') = 'object'
                AND (SELECT COUNT(*) FROM json_each(canonical.value)) =
                    CASE WHEN json_type(canonical.value, '$.output_uom') IS NULL
                         THEN 6 ELSE 7 END
                AND json_type(canonical.value, '$.required_observations') = 'array'
                AND json_type(canonical.value, '$.required_evidence') = 'array'
                AND json_extract(canonical.value, '$.code') IS projection.code
                AND json_extract(canonical.value, '$.type') IS projection.type
                AND json_extract(canonical.value, '$.subject_code') IS projection.subject_code
                AND json(json_extract(canonical.value, '$.required_observations'))
                    IS json(projection.required_observations_json)
                AND json(json_extract(canonical.value, '$.required_evidence'))
                    IS json(projection.required_evidence_json)
                AND json_extract(canonical.value, '$.output_claim_type')
                    IS projection.output_claim_type
                AND json_extract(canonical.value, '$.output_uom') IS projection.output_uom
          )
    ) OR EXISTS (
        SELECT 1 FROM json_each(NEW.canonical_json, '$.rules') AS canonical
        WHERE NOT EXISTS (
            SELECT 1 FROM engine_rule_definitions AS projection
            WHERE projection.pack_id = NEW.id
              AND json_type(canonical.value, '$') = 'object'
              AND (SELECT COUNT(*) FROM json_each(canonical.value)) =
                  CASE WHEN json_type(canonical.value, '$.output_uom') IS NULL
                       THEN 6 ELSE 7 END
              AND json_type(canonical.value, '$.required_observations') = 'array'
              AND json_type(canonical.value, '$.required_evidence') = 'array'
              AND projection.code IS json_extract(canonical.value, '$.code')
              AND projection.type IS json_extract(canonical.value, '$.type')
              AND projection.subject_code IS json_extract(canonical.value, '$.subject_code')
              AND json(projection.required_observations_json)
                  IS json(json_extract(canonical.value, '$.required_observations'))
              AND json(projection.required_evidence_json)
                  IS json(json_extract(canonical.value, '$.required_evidence'))
              AND projection.output_claim_type
                  IS json_extract(canonical.value, '$.output_claim_type')
              AND projection.output_uom IS json_extract(canonical.value, '$.output_uom')
        )
    );
END;

CREATE TRIGGER engine_domain_packs_are_immutable BEFORE UPDATE ON engine_domain_packs
BEGIN SELECT RAISE(ABORT, 'engine domain packs are immutable'); END;
CREATE TRIGGER engine_domain_packs_cannot_be_deleted BEFORE DELETE ON engine_domain_packs
BEGIN SELECT RAISE(ABORT, 'engine domain packs cannot be deleted'); END;

CREATE TRIGGER engine_pack_provenance_rejects_sealed_insert
BEFORE INSERT ON engine_pack_provenance
WHEN EXISTS (SELECT 1 FROM engine_domain_packs WHERE id = NEW.pack_id)
BEGIN SELECT RAISE(ABORT, 'sealed engine pack projections reject inserts'); END;
CREATE TRIGGER engine_pack_provenance_are_immutable BEFORE UPDATE ON engine_pack_provenance
BEGIN SELECT RAISE(ABORT, 'engine pack provenance is immutable'); END;
CREATE TRIGGER engine_pack_provenance_cannot_be_deleted BEFORE DELETE ON engine_pack_provenance
BEGIN SELECT RAISE(ABORT, 'engine pack provenance cannot be deleted'); END;

CREATE TRIGGER engine_catalog_items_rejects_sealed_insert
BEFORE INSERT ON engine_catalog_items
WHEN EXISTS (SELECT 1 FROM engine_domain_packs WHERE id = NEW.pack_id)
BEGIN SELECT RAISE(ABORT, 'sealed engine pack projections reject inserts'); END;
CREATE TRIGGER engine_catalog_items_are_immutable BEFORE UPDATE ON engine_catalog_items
BEGIN SELECT RAISE(ABORT, 'engine catalog items are immutable'); END;
CREATE TRIGGER engine_catalog_items_cannot_be_deleted BEFORE DELETE ON engine_catalog_items
BEGIN SELECT RAISE(ABORT, 'engine catalog items cannot be deleted'); END;

CREATE TRIGGER engine_catalog_relations_rejects_sealed_insert
BEFORE INSERT ON engine_catalog_relations
WHEN EXISTS (SELECT 1 FROM engine_domain_packs WHERE id = NEW.pack_id)
BEGIN SELECT RAISE(ABORT, 'sealed engine pack projections reject inserts'); END;
CREATE TRIGGER engine_catalog_relations_are_immutable BEFORE UPDATE ON engine_catalog_relations
BEGIN SELECT RAISE(ABORT, 'engine catalog relations are immutable'); END;
CREATE TRIGGER engine_catalog_relations_cannot_be_deleted BEFORE DELETE ON engine_catalog_relations
BEGIN SELECT RAISE(ABORT, 'engine catalog relations cannot be deleted'); END;

CREATE TRIGGER engine_rule_definitions_rejects_sealed_insert
BEFORE INSERT ON engine_rule_definitions
WHEN EXISTS (SELECT 1 FROM engine_domain_packs WHERE id = NEW.pack_id)
BEGIN SELECT RAISE(ABORT, 'sealed engine pack projections reject inserts'); END;
CREATE TRIGGER engine_rule_definitions_are_immutable BEFORE UPDATE ON engine_rule_definitions
BEGIN SELECT RAISE(ABORT, 'engine rule definitions are immutable'); END;
CREATE TRIGGER engine_rule_definitions_cannot_be_deleted BEFORE DELETE ON engine_rule_definitions
BEGIN SELECT RAISE(ABORT, 'engine rule definitions cannot be deleted'); END;
