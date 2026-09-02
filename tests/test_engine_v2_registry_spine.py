"""Focused contract for the isolated Division 23 v2 semantic registry."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from importlib import resources
import json
import re
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from helios_takeoff_core.errors import ConflictError, NotFoundError, ValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "engine_v2" / "full_division23_registry_pack.json"
V1_MIGRATION = ROOT / "src" / "helios_takeoff_core" / "migrations" / "017_engine_domain_packs.sql"
V2_SCHEMA = ROOT / "src" / "helios_takeoff_core" / "engine" / "v2" / "schemas" / "domain-pack-v2.schema.json"


def fixture_pack() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class Division23V2CompilerTests(unittest.TestCase):
    def test_v1_identity_and_migration_remain_exact(self) -> None:
        from tests.test_engine_domain_pack_compiler import valid_pack
        from helios_takeoff_core.engine.compiler import compile_domain_pack as compile_v1

        self.assertEqual(
            compile_v1(valid_pack()).sha256,
            "6d448537430a3dd6708f77e335998c14448a661ba594190c82735ccac93b591e",
        )
        self.assertEqual(
            hashlib.sha256(V1_MIGRATION.read_bytes()).hexdigest(),
            "30fe60e8bf6a3a506fa0a16cb557cb4f06b3a2b4305963773018aa5a5acfc89d",
        )

    def test_compiles_full_spectrum_pack_with_cited_immutable_records(self) -> None:
        from helios_takeoff_core.engine.v2 import (
            DefinitionKind,
            DomainFamily,
            RelationKind,
            compile_domain_pack,
        )

        compiled = compile_domain_pack(fixture_pack())

        self.assertEqual({record.kind for record in compiled.definitions_by_code.values()}, set(DefinitionKind))
        self.assertEqual({record.family for record in compiled.definitions_by_code.values()}, set(DomainFamily))
        self.assertEqual({record.kind for record in compiled.relations_by_code.values()}, set(RelationKind))
        self.assertEqual(len(compiled.sources_by_id), 3)
        self.assertTrue(all(record.citations for record in compiled.definitions_by_code.values()))
        self.assertTrue(all(record.citations for record in compiled.relations_by_code.values()))
        self.assertTrue(all(source["authority_class"] == "TEST_FIXTURE" for source in compiled.sources_by_id.values()))
        with self.assertRaises(TypeError):
            compiled.canonical_document["title"] = "changed"  # type: ignore[index]
        with self.assertRaises(TypeError):
            compiled.definitions_by_code["DUCT-RECT"].attributes["uom"] = "EA"  # type: ignore[index]

    def test_identity_is_order_independent(self) -> None:
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        reordered = fixture_pack()
        reordered["sources"] = list(reversed(reordered["sources"]))  # type: ignore[arg-type,index]
        reordered["definitions"] = list(reversed(reordered["definitions"]))  # type: ignore[arg-type,index]
        reordered["relations"] = list(reversed(reordered["relations"]))  # type: ignore[arg-type,index]
        for definition in reordered["definitions"]:  # type: ignore[union-attr]
            definition["citations"] = list(reversed(definition["citations"]))

        original = compile_domain_pack(fixture_pack())
        shuffled = compile_domain_pack(reordered)
        self.assertEqual(shuffled.canonical_bytes, original.canonical_bytes)
        self.assertEqual(shuffled.sha256, original.sha256)

    def test_source_content_identity_is_digest_first_and_missing_normalizes_to_null(self) -> None:
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        explicit_null = fixture_pack()
        omitted = fixture_pack()
        del omitted["sources"][1]["content_sha256"]  # type: ignore[index]
        self.assertEqual(
            compile_domain_pack(omitted).canonical_bytes,
            compile_domain_pack(explicit_null).canonical_bytes,
        )

        duplicate_content = fixture_pack()
        duplicate_content["sources"][1]["content_sha256"] = "a" * 64  # type: ignore[index]
        with self.assertRaisesRegex(ValidationError, "content identity"):
            compile_domain_pack(duplicate_content)

    def test_packaged_schema_matches_compiler_lexical_contract_and_declares_semantic_authority(self) -> None:
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        schema = json.loads(V2_SCHEMA.read_text(encoding="utf-8"))
        self.assertIn("compile_domain_pack", schema["$comment"])
        canonical_text = schema["$defs"]["canonicalText"]
        pattern = re.compile(canonical_text["pattern"])
        for value in ("Valid text", "US-NY", "1.0.0"):
            with self.subTest(value=value):
                self.assertIsNotNone(pattern.fullmatch(value))
        for value in ("", " leading", "trailing ", "embedded\ncontrol"):
            with self.subTest(value=value):
                self.assertIsNone(pattern.fullmatch(value))

        canonical_ref = {"$ref": "#/$defs/canonicalText"}
        for property_name in ("version", "title", "jurisdiction"):
            self.assertEqual(schema["properties"][property_name], canonical_ref)
        for property_name in ("stable_reference", "publisher", "title", "jurisdiction"):
            self.assertEqual(schema["$defs"]["source"]["properties"][property_name], canonical_ref)
        for property_name in ("title", "description"):
            self.assertEqual(schema["$defs"]["definition"]["properties"][property_name], canonical_ref)
        for property_name in ("locator", "applicability", "limitations"):
            self.assertEqual(schema["$defs"]["citation"]["properties"][property_name], canonical_ref)
        self.assertEqual(schema["$defs"]["canonicalValue"]["oneOf"][3], canonical_ref)

        for field, invalid in (("title", " padded"), ("jurisdiction", "US-NY\n")):
            document = fixture_pack()
            document[field] = invalid
            with self.subTest(field=field), self.assertRaisesRegex(ValidationError, "canonical text"):
                compile_domain_pack(document)

    def test_relation_domain_and_range_are_table_driven_across_all_kinds(self) -> None:
        from helios_takeoff_core.engine.v2 import RelationKind, compile_domain_pack

        compiled = compile_domain_pack(fixture_pack())
        hydronic_codes = {
            "REL-PIPE-BELONGS", "REL-PIPE-COMPOSED-FITTING", "REL-PIPE-COMPOSED-VALVE",
            "REL-PIPE-MATERIAL", "REL-PIPE-CONNECTION", "REL-FITTING-MATERIAL",
            "REL-FITTING-CONNECTION", "REL-VALVE-MATERIAL", "REL-VALVE-CONNECTION",
        }
        self.assertTrue(hydronic_codes.issubset(compiled.relations_by_code))

        invalid_endpoint: dict[RelationKind, tuple[str, str]] = {
            RelationKind.BELONGS_TO_SYSTEM: ("to_code", "MAT-GALV"),
            RelationKind.USES_MATERIAL: ("to_code", "CONN-SLIP"),
            RelationKind.USES_CONNECTION: ("to_code", "MAT-GALV"),
            RelationKind.COMPOSED_OF: ("from_code", "VALVE-BALANCE"),
            RelationKind.REQUIRES_ACCESSORY: ("to_code", "MAT-GALV"),
            RelationKind.REQUIRES_INSULATION: ("to_code", "MAT-GALV"),
            RelationKind.REQUIRES_SUPPORT: ("to_code", "MAT-GALV"),
            RelationKind.HAS_LABOR_ASSEMBLY: ("to_code", "MAT-GALV"),
            RelationKind.HAS_PRICING_INPUT: ("to_code", "MAT-GALV"),
            RelationKind.GOVERNED_BY_RULE: ("to_code", "MAT-GALV"),
        }
        self.assertEqual(set(invalid_endpoint), set(RelationKind))
        for kind, (field, value) in invalid_endpoint.items():
            document = fixture_pack()
            relation = next(item for item in document["relations"] if item["kind"] == kind.value)  # type: ignore[union-attr]
            relation[field] = value
            with self.subTest(kind=kind.value), self.assertRaisesRegex(ValidationError, "domain or range"):
                compile_domain_pack(document)

    def test_rejects_malformed_unresolved_uncited_executable_and_domain_invalid_content(self) -> None:
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        cases: list[tuple[str, dict[str, object], str]] = []
        unknown = fixture_pack()
        unknown["unexpected"] = True
        cases.append(("unknown", unknown, "fields"))
        floating = fixture_pack()
        floating["definitions"][0]["attributes"]["factor"] = 1.5  # type: ignore[index]
        cases.append(("float", floating, "float"))
        uncited = fixture_pack()
        uncited["definitions"][0]["citations"] = []  # type: ignore[index]
        cases.append(("uncited", uncited, "citation"))
        unresolved_source = fixture_pack()
        unresolved_source["relations"][0]["citations"][0]["source_ref"] = "MISSING"  # type: ignore[index]
        cases.append(("source", unresolved_source, "source"))
        unresolved_endpoint = fixture_pack()
        unresolved_endpoint["relations"][0]["to_code"] = "SYS-MISSING"  # type: ignore[index]
        cases.append(("endpoint", unresolved_endpoint, "endpoint"))
        malformed_executable = fixture_pack()
        malformed_executable["rules"] = [{"code": "DO-NOT-RUN"}]
        cases.append(("executable", malformed_executable, "rule"))
        domain_invalid = fixture_pack()
        domain_invalid["relations"][0]["to_code"] = "MAT-GALV"  # type: ignore[index]
        cases.append(("domain", domain_invalid, "domain"))
        invalid_interval = fixture_pack()
        invalid_interval["valid_through"] = "2025-12-31"
        cases.append(("interval", invalid_interval, "validity"))

        for label, document, message in cases:
            with self.subTest(label=label), self.assertRaisesRegex(ValidationError, message):
                compile_domain_pack(document)


class EngineStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "engine-v2.sqlite3"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _store(self):
        from helios_takeoff_core.engine.v2 import EngineStore

        store = EngineStore(self.path)
        store.initialize()
        return store

    def test_initialize_creates_exactly_seven_isolated_tables_and_packaged_resources(self) -> None:
        store = self._store()
        with sqlite3.connect(store.path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        self.assertEqual(
            tables,
            {
                "engine_store_migrations",
                "engine_packs",
                "engine_source_index",
                "engine_definition_index",
                "engine_relation_index",
                "engine_citation_index",
                "engine_registry_events",
            },
        )
        package = resources.files("helios_takeoff_core.engine.v2")
        self.assertTrue((package / "migrations" / "001_engine_registry.sql").is_file())
        self.assertTrue((package / "schemas" / "domain-pack-v2.schema.json").is_file())
        self.assertTrue((package / "schemas" / "observation-bundle-v1.schema.json").is_file())

        for forbidden in ("projects", "agent_jobs", "engine_domain_packs", "unknown_table"):
            path = Path(self.temporary_directory.name) / f"{forbidden}.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute(f"CREATE TABLE {forbidden}(id TEXT PRIMARY KEY)")
            from helios_takeoff_core.engine.v2 import EngineStore

            with self.subTest(forbidden=forbidden), self.assertRaisesRegex(ValidationError, "isolated"):
                EngineStore(path).initialize()

    def test_import_reopen_queries_citations_neighbors_and_idempotency(self) -> None:
        from helios_takeoff_core.engine.v2 import DefinitionKind, DomainFamily, RelationKind, compile_domain_pack

        store = self._store()
        compiled = compile_domain_pack(fixture_pack())
        digest = store.import_pack(compiled)
        self.assertEqual(digest, compiled.sha256)
        self.assertEqual(store.import_pack(compiled), digest)
        self.assertEqual(len(store.list_events(pack_sha256=digest)), 1)

        reopened = type(store)(store.path)
        reopened.initialize()
        self.assertEqual(reopened.load_pack(pack_sha256=digest).canonical_bytes, compiled.canonical_bytes)
        self.assertEqual(reopened.get_source(pack_sha256=digest, source_id="FIXTURE-AIRSIDE")["authority_class"], "TEST_FIXTURE")
        self.assertEqual(reopened.get_definition(pack_sha256=digest, definition_code="DUCT-RECT").kind, DefinitionKind.DUCT)
        self.assertEqual(len(reopened.list_definitions(pack_sha256=digest)), 21)
        self.assertEqual(len(reopened.list_definitions(pack_sha256=digest, kind=DefinitionKind.DUCT)), 1)
        self.assertEqual(len(reopened.list_definitions(pack_sha256=digest, family=DomainFamily.PIPING)), 5)
        self.assertEqual(reopened.get_relation(pack_sha256=digest, relation_code="REL-MATERIAL").kind, RelationKind.USES_MATERIAL)
        self.assertEqual(len(reopened.neighbors(pack_sha256=digest, definition_code="DUCT-RECT", direction="OUT")), 8)
        self.assertEqual(len(reopened.neighbors(pack_sha256=digest, definition_code="MAT-GALV", direction="IN")), 1)
        self.assertEqual(
            len(reopened.neighbors(pack_sha256=digest, definition_code="DUCT-RECT", relation=RelationKind.USES_MATERIAL)),
            1,
        )
        self.assertEqual(len(reopened.neighbors(pack_sha256=digest, definition_code="PIPE-HWS")), 10)
        self.assertEqual(
            {edge.to_code for edge in reopened.neighbors(pack_sha256=digest, definition_code="FIT-ELBOW")},
            {"MAT-COPPER", "CONN-SOLDER"},
        )
        self.assertEqual(
            {edge.to_code for edge in reopened.neighbors(pack_sha256=digest, definition_code="VALVE-BALANCE")},
            {"MAT-COPPER", "CONN-SOLDER"},
        )
        definition_citations = reopened.list_citations(pack_sha256=digest, assertion_kind="DEFINITION", assertion_code="DUCT-RECT")
        relation_citations = reopened.list_citations(pack_sha256=digest, assertion_kind="RELATION", assertion_code="REL-MATERIAL")
        self.assertEqual(definition_citations[0].source_ref, "FIXTURE-AIRSIDE")
        self.assertEqual(relation_citations[0].locator, "rel-material")
        with self.assertRaises(NotFoundError):
            reopened.get_definition(pack_sha256=digest, definition_code="MISSING")
        with self.assertRaisesRegex(ValidationError, "direction"):
            reopened.neighbors(pack_sha256=digest, definition_code="DUCT-RECT", direction="SIDEWAYS")

    def test_conflicts_and_pack_level_supersession_fail_closed(self) -> None:
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        store = self._store()
        first = compile_domain_pack(fixture_pack())
        store.import_pack(first)

        changed_same_version = fixture_pack()
        changed_same_version["title"] = "Conflicting immutable content"
        with self.assertRaises(ConflictError):
            store.import_pack(compile_domain_pack(changed_same_version))

        successor_document = fixture_pack()
        successor_document["version"] = "2.0.0"
        successor_document["valid_from"] = "2026-02-01"
        successor_document["supersedes_pack_sha256"] = first.sha256
        successor = compile_domain_pack(successor_document)
        self.assertEqual(store.import_pack(successor), successor.sha256)

        unresolved = fixture_pack()
        unresolved["version"] = "3.0.0"
        unresolved["valid_from"] = "2026-03-01"
        unresolved["supersedes_pack_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValidationError, "superseded"):
            store.import_pack(compile_domain_pack(unresolved))

        wrong_code = fixture_pack()
        wrong_code["pack_code"] = "OTHER-PACK"
        wrong_code["version"] = "2.0.0"
        wrong_code["valid_from"] = "2026-03-01"
        wrong_code["supersedes_pack_sha256"] = first.sha256
        with self.assertRaisesRegex(ValidationError, "pack_code"):
            store.import_pack(compile_domain_pack(wrong_code))

        self.assertNotIn("SUPERSEDES", {relation.kind.value for relation in successor.relations_by_code.values()})

    def test_projection_rebuild_is_exact_and_appends_one_chained_event(self) -> None:
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        store = self._store()
        compiled = compile_domain_pack(fixture_pack())
        digest = store.import_pack(compiled)
        before = store.list_events(pack_sha256=digest)
        with sqlite3.connect(store.path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("DELETE FROM engine_citation_index WHERE pack_sha256 = ?", (digest,))
            connection.execute("DELETE FROM engine_relation_index WHERE pack_sha256 = ?", (digest,))
            connection.execute("DELETE FROM engine_definition_index WHERE pack_sha256 = ?", (digest,))
            connection.execute("DELETE FROM engine_source_index WHERE pack_sha256 = ?", (digest,))
        self.assertFalse(store.verify_integrity().valid)

        store.rebuild_indexes(pack_sha256=digest)

        self.assertTrue(store.verify_integrity().valid)
        self.assertEqual(len(store.list_definitions(pack_sha256=digest)), 21)
        after = store.list_events(pack_sha256=digest)
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(after[-1]["previous_event_sha256"], before[-1]["event_sha256"])
        self.assertEqual(after[-1]["event_type"], "PROJECTIONS_REBUILT")
        self.assertEqual(after[-1]["projection_generation"], 2)
        with sqlite3.connect(store.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT projection_generation FROM engine_packs WHERE pack_sha256 = ?", (digest,)
                ).fetchone()[0],
                2,
            )
            for table in (
                "engine_source_index", "engine_definition_index",
                "engine_relation_index", "engine_citation_index",
            ):
                self.assertEqual(
                    connection.execute(
                        f"SELECT DISTINCT projection_generation FROM {table} WHERE pack_sha256 = ?",
                        (digest,),
                    ).fetchall(),
                    [(2,)],
                )

    def test_migration_blob_projection_citation_endpoint_and_event_tampering_fail_closed(self) -> None:
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        store = self._store()
        compiled = compile_domain_pack(fixture_pack())
        digest = store.import_pack(compiled)

        with sqlite3.connect(store.path) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE engine_packs SET canonical_json = '{}' WHERE pack_sha256 = ?", (digest,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE engine_registry_events SET event_type = 'TAMPERED' WHERE pack_sha256 = ?", (digest,))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE engine_relation_index SET to_code = 'MISSING' WHERE pack_sha256 = ?", (digest,))
            connection.execute(
                "UPDATE engine_definition_index SET definition_json = '{}' WHERE pack_sha256 = ? AND definition_code = 'DUCT-RECT'",
                (digest,),
            )
        report = store.verify_integrity()
        self.assertFalse(report.valid)
        self.assertTrue(any("projection" in error for error in report.errors))

        migration_store = self._store()
        with sqlite3.connect(migration_store.path) as connection:
            connection.execute("UPDATE engine_store_migrations SET sha256 = ?", ("0" * 64,))
        with self.assertRaisesRegex(ValidationError, "migration"):
            migration_store.initialize()

    def test_pack_header_trigger_and_independent_reads_reject_incoherent_columns(self) -> None:
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        store = self._store()
        compiled = compile_domain_pack(fixture_pack())
        digest = store.import_pack(compiled)

        other_document = fixture_pack()
        other_document["pack_code"] = "DIRECT-SQL-PACK"
        other_document["version"] = "2.0.0"
        other = compile_domain_pack(other_document)
        connection = sqlite3.connect(store.path)
        try:
            connection.create_function(
                "helios_v2_pack_is_canonical", 11, store._sqlite_pack_is_canonical, deterministic=True
            )
            source_count, definition_count, relation_count, citation_count = store._projection_counts(other)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "canonical payload"):
                connection.execute(
                    """
                    INSERT INTO engine_packs(
                        pack_sha256, protocol, pack_code, version, valid_from,
                        supersedes_pack_sha256, canonical_json, projection_generation,
                        source_count, definition_count, relation_count, citation_count, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        other.sha256, other_document["protocol"], "INCOHERENT-HEADER",
                        other_document["version"], other_document["valid_from"], None,
                        other.canonical_bytes, 1, source_count, definition_count,
                        relation_count, citation_count, "2026-09-01T00:00:00+00:00",
                    ),
                )
        finally:
            connection.close()

        with sqlite3.connect(store.path) as connection:
            trigger_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='engine_packs_are_immutable'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER engine_packs_are_immutable")
            connection.execute(
                "UPDATE engine_packs SET pack_code = 'CORRUPTED-HEADER' WHERE pack_sha256 = ?", (digest,)
            )
            connection.execute(trigger_sql)

        with self.assertRaisesRegex(ValidationError, "header"):
            store.load_pack(pack_sha256=digest)
        report = store.verify_integrity()
        self.assertFalse(report.valid)
        self.assertTrue(any("header" in error for error in report.errors))

    def test_filtered_projection_queries_fail_closed_and_replay_after_rebuild(self) -> None:
        from helios_takeoff_core.engine.v2 import DefinitionKind, DomainFamily, RelationKind, compile_domain_pack

        store = self._store()
        digest = store.import_pack(compile_domain_pack(fixture_pack()))

        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "UPDATE engine_definition_index SET kind = 'PIPE' WHERE pack_sha256 = ? AND definition_code = 'DUCT-RECT'",
                (digest,),
            )
        with self.assertRaisesRegex(ValidationError, "projection snapshot"):
            store.list_definitions(pack_sha256=digest, kind=DefinitionKind.DUCT)
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "UPDATE engine_definition_index SET kind = 'DUCT', family = 'PIPING' WHERE pack_sha256 = ? AND definition_code = 'DUCT-RECT'",
                (digest,),
            )
        with self.assertRaisesRegex(ValidationError, "projection snapshot"):
            store.list_definitions(pack_sha256=digest, family=DomainFamily.AIRSIDE)
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "UPDATE engine_definition_index SET family = 'AIRSIDE' WHERE pack_sha256 = ? AND definition_code = 'DUCT-RECT'",
                (digest,),
            )
            connection.execute(
                "UPDATE engine_relation_index SET from_code = 'PIPE-HWS' WHERE pack_sha256 = ? AND relation_code = 'REL-MATERIAL'",
                (digest,),
            )
        with self.assertRaisesRegex(ValidationError, "projection snapshot"):
            store.neighbors(
                pack_sha256=digest, definition_code="DUCT-RECT", relation=RelationKind.USES_MATERIAL
            )
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "UPDATE engine_relation_index SET from_code = 'DUCT-RECT' WHERE pack_sha256 = ? AND relation_code = 'REL-MATERIAL'",
                (digest,),
            )
            connection.execute(
                "DELETE FROM engine_relation_index WHERE pack_sha256 = ? AND relation_code = 'REL-VALVE-CONNECTION'",
                (digest,),
            )
        with self.assertRaisesRegex(ValidationError, "projection snapshot"):
            store.neighbors(pack_sha256=digest, definition_code="DUCT-RECT")

        store.rebuild_indexes(pack_sha256=digest)
        self.assertEqual(
            len(store.neighbors(pack_sha256=digest, definition_code="DUCT-RECT", relation=RelationKind.USES_MATERIAL)),
            1,
        )

    def test_filtered_projection_read_uses_one_stable_snapshot_during_concurrent_write(self) -> None:
        from helios_takeoff_core.engine.v2 import DefinitionKind, EngineStore, compile_domain_pack

        store = self._store()
        digest = store.import_pack(compile_domain_pack(fixture_pack()))
        with sqlite3.connect(store.path) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")

        snapshot_validated = threading.Event()
        continue_read = threading.Event()

        class PausingStore(EngineStore):
            @classmethod
            def _validate_projection_snapshot(cls, connection, compiled) -> None:  # type: ignore[no-untyped-def]
                EngineStore._validate_projection_snapshot(connection, compiled)
                snapshot_validated.set()
                if not continue_read.wait(timeout=5):
                    raise RuntimeError("concurrent-writer test did not release the stable read")

        outcomes: list[object] = []

        def read_filtered() -> None:
            try:
                outcomes.append(
                    PausingStore(store.path).list_definitions(
                        pack_sha256=digest, kind=DefinitionKind.DUCT
                    )
                )
            except BaseException as error:
                outcomes.append(error)

        reader = threading.Thread(target=read_filtered, daemon=True)
        reader.start()
        self.assertTrue(snapshot_validated.wait(timeout=5))
        with sqlite3.connect(store.path, timeout=2) as connection:
            connection.execute(
                "UPDATE engine_definition_index SET kind = 'PIPE' WHERE pack_sha256 = ? AND definition_code = 'DUCT-RECT'",
                (digest,),
            )
        continue_read.set()
        reader.join(timeout=5)

        self.assertFalse(reader.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], tuple)
        self.assertEqual(len(outcomes[0]), 1)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValidationError, "projection snapshot"):
            store.list_definitions(pack_sha256=digest, kind=DefinitionKind.DUCT)

    def test_sql_and_independent_load_enforce_supersession_lineage(self) -> None:
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        store = self._store()
        first = compile_domain_pack(fixture_pack())
        store.import_pack(first)

        connection = sqlite3.connect(store.path)
        connection.create_function(
            "helios_v2_pack_is_canonical", 11, store._sqlite_pack_is_canonical, deterministic=True
        )

        def insert_pack(document: dict[str, object]) -> None:
            compiled = compile_domain_pack(document)
            counts = store._projection_counts(compiled)
            connection.execute(
                """
                INSERT INTO engine_packs(
                    pack_sha256, protocol, pack_code, version, valid_from,
                    supersedes_pack_sha256, canonical_json, projection_generation,
                    source_count, definition_count, relation_count, citation_count, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    compiled.sha256, document["protocol"], document["pack_code"],
                    document["version"], document["valid_from"],
                    document["supersedes_pack_sha256"], compiled.canonical_bytes, 1,
                    *counts, "2026-09-01T00:00:00+00:00",
                ),
            )

        invalid_documents: list[dict[str, object]] = []
        missing = fixture_pack()
        missing["version"] = "2.0.0"
        missing["valid_from"] = "2026-02-01"
        missing["supersedes_pack_sha256"] = "f" * 64
        invalid_documents.append(missing)
        wrong_code = fixture_pack()
        wrong_code["pack_code"] = "WRONG-LINEAGE"
        wrong_code["version"] = "2.1.0"
        wrong_code["valid_from"] = "2026-02-01"
        wrong_code["supersedes_pack_sha256"] = first.sha256
        invalid_documents.append(wrong_code)
        same_version = fixture_pack()
        same_version["title"] = "Different content with reused version"
        same_version["valid_from"] = "2026-02-01"
        same_version["supersedes_pack_sha256"] = first.sha256
        invalid_documents.append(same_version)
        nonlater = fixture_pack()
        nonlater["version"] = "2.2.0"
        nonlater["supersedes_pack_sha256"] = first.sha256
        invalid_documents.append(nonlater)

        try:
            for document in invalid_documents:
                with self.subTest(version=document["version"]), self.assertRaisesRegex(
                    sqlite3.IntegrityError, "supersession lineage"
                ):
                    insert_pack(document)

            bypassed = fixture_pack()
            bypassed["pack_code"] = "BYPASSED-LINEAGE"
            bypassed["version"] = "9.0.0"
            bypassed["valid_from"] = "2026-09-01"
            bypassed["supersedes_pack_sha256"] = first.sha256
            bypassed_compiled = compile_domain_pack(bypassed)
            trigger_sql = connection.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type='trigger' AND name='engine_packs_validate_supersession_insert'
                """
            ).fetchone()[0]
            connection.execute("DROP TRIGGER engine_packs_validate_supersession_insert")
            insert_pack(bypassed)
            connection.execute(trigger_sql)
            connection.commit()
        finally:
            connection.close()

        with self.assertRaisesRegex(ValidationError, "supersession lineage"):
            store.load_pack(pack_sha256=bypassed_compiled.sha256)
        report = store.verify_integrity()
        self.assertFalse(report.valid)
        self.assertTrue(any("supersession lineage" in error for error in report.errors))

    def test_registry_event_semantics_and_hashed_public_metadata_reject_fabrication(self) -> None:
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        store = self._store()
        digest = store.import_pack(compile_domain_pack(fixture_pack()))
        prior = store.list_events(pack_sha256=digest)[0]["event_sha256"]

        def forged(
            event_type: str,
            *,
            projection_generation: int,
            json_created_at: str,
            column_created_at: str,
        ) -> tuple[object, ...]:
            event = {
                "protocol": "helios.engine.registry-event/v1",
                "pack_sha256": digest,
                "event_sequence": 2,
                "event_type": event_type,
                "projection_generation": projection_generation,
                "previous_event_sha256": prior,
                "created_at": json_created_at,
            }
            event_json = json.dumps(event, sort_keys=True, separators=(",", ":"))
            return (
                digest, 2, event_type, projection_generation, prior,
                hashlib.sha256(event_json.encode("utf-8")).hexdigest(), event_json, column_created_at,
            )

        connection = sqlite3.connect(store.path)
        try:
            connection.create_function(
                "helios_v2_event_is_canonical", 8, store._sqlite_event_is_canonical, deterministic=True
            )
            insert = """
                INSERT INTO engine_registry_events(
                    pack_sha256, event_sequence, event_type, projection_generation,
                    previous_event_sha256, event_sha256, event_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            timestamp = "2026-09-01T00:00:00+00:00"
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    insert,
                    forged(
                        "PACK_IMPORTED", projection_generation=2,
                        json_created_at=timestamp, column_created_at=timestamp,
                    ),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    insert,
                    forged(
                        "PROJECTIONS_REBUILT",
                        projection_generation=2,
                        json_created_at=timestamp,
                        column_created_at="2026-09-01T00:00:01+00:00",
                    ),
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "unattested"):
                connection.execute(
                    insert,
                    forged(
                        "PROJECTIONS_REBUILT", projection_generation=2,
                        json_created_at=timestamp, column_created_at=timestamp,
                    ),
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "fully rewritten"):
                connection.execute(
                    "UPDATE engine_packs SET projection_generation = 2 WHERE pack_sha256 = ?",
                    (digest,),
                )
        finally:
            connection.close()

        self.assertEqual(len(store.list_events(pack_sha256=digest)), 1)
        self.assertTrue(store.verify_integrity().valid)

    def test_public_store_operations_close_every_sqlite_connection(self) -> None:
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        store = self._store()
        digest = store.import_pack(compile_domain_pack(fixture_pack()))
        real_connect = sqlite3.connect
        opened: list[sqlite3.Connection] = []

        def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = real_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        with patch("helios_takeoff_core.engine.v2.store.sqlite3.connect", side_effect=tracking_connect):
            store.load_pack(pack_sha256=digest)
            store.list_definitions(pack_sha256=digest)

        self.assertTrue(opened)
        for connection in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
