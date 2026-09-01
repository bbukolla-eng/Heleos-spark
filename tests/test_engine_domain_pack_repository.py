"""Focused persistence contracts for immutable P1B domain packs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database
from helios_takeoff_core.engine.compiler import compile_domain_pack
from helios_takeoff_core.engine.contracts import CompiledDomainPack
from helios_takeoff_core.errors import ConflictError, NotFoundError, ValidationError


def valid_pack() -> dict[str, object]:
    return {
        "protocol": "helios.p1b.domain-pack/v1",
        "pack_code": "DIV23-HVAC",
        "version": "1.0.0",
        "title": "Division 23 HVAC definitions",
        "jurisdiction": "US",
        "provenance": [
            {"kind": "PUBLIC_URL", "reference": "https://example.test/div23-hvac"},
            {"kind": "CONTENT_DIGEST", "reference": "a" * 64},
        ],
        "catalog": [
            {"code": "MAT-DUCT", "type": "MATERIAL", "title": "Sheet metal", "uom": "SF"},
            {"code": "SYS-AHU", "type": "SYSTEM", "title": "AHU system", "uom": "EA"},
            {"code": "CMP-AHU", "type": "COMPONENT", "title": "Air handler", "uom": "EA"},
        ],
        "relations": [
            {"from_code": "CMP-AHU", "relation": "USES_MATERIAL", "to_code": "MAT-DUCT"},
            {"from_code": "CMP-AHU", "relation": "PART_OF", "to_code": "SYS-AHU"},
        ],
        "rules": [
            {
                "code": "MEASURE-AHU-COUNT",
                "type": "MEASURE",
                "subject_code": "CMP-AHU",
                "required_observations": ["AHU_TAG"],
                "required_evidence": ["MECHANICAL_PLAN"],
                "output_claim_type": "EQUIPMENT_COUNT",
                "output_uom": "EA",
            },
            {
                "code": "CLASSIFY-AHU",
                "type": "CLASSIFY",
                "subject_code": "CMP-AHU",
                "required_observations": ["AHU_TAG"],
                "required_evidence": [],
                "output_claim_type": "EQUIPMENT_CLASS",
            },
        ],
    }


def _stage_pack_projections(
    connection: sqlite3.Connection, *, pack_id: str, document: dict[str, Any]
) -> None:
    for item in document["catalog"]:
        connection.execute(
            """
            INSERT INTO engine_catalog_items(pack_id, code, type, title, uom)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pack_id, item["code"], item["type"], item["title"], item["uom"]),
        )
    for provenance in document["provenance"]:
        connection.execute(
            """
            INSERT INTO engine_pack_provenance(pack_id, kind, reference)
            VALUES (?, ?, ?)
            """,
            (pack_id, provenance["kind"], provenance["reference"]),
        )
    for relation in document["relations"]:
        connection.execute(
            """
            INSERT INTO engine_catalog_relations(pack_id, from_code, relation, to_code)
            VALUES (?, ?, ?, ?)
            """,
            (pack_id, relation["from_code"], relation["relation"], relation["to_code"]),
        )
    for rule in document["rules"]:
        connection.execute(
            """
            INSERT INTO engine_rule_definitions(
                pack_id, code, type, subject_code,
                required_observations_json, required_evidence_json,
                output_claim_type, output_uom
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pack_id,
                rule["code"],
                rule["type"],
                rule["subject_code"],
                json.dumps(rule["required_observations"], separators=(",", ":")),
                json.dumps(rule["required_evidence"], separators=(",", ":")),
                rule["output_claim_type"],
                rule.get("output_uom"),
            ),
        )


def _insert_pack_parent(
    connection: sqlite3.Connection,
    *,
    pack_id: str,
    compiled: CompiledDomainPack,
    title: str | None = None,
) -> None:
    document = json.loads(compiled.canonical_bytes)
    connection.execute(
        """
        INSERT INTO engine_domain_packs(
            id, protocol, pack_code, version, title, jurisdiction,
            sha256, canonical_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'now')
        """,
        (
            pack_id,
            document["protocol"],
            document["pack_code"],
            document["version"],
            document["title"] if title is None else title,
            document["jurisdiction"],
            compiled.sha256,
            compiled.canonical_bytes.decode("utf-8"),
        ),
    )


def _insert_raw_pack_parent(
    connection: sqlite3.Connection,
    *,
    pack_id: str,
    document: dict[str, Any],
    canonical_json: str,
    sha256: str,
) -> None:
    connection.execute(
        """
        INSERT INTO engine_domain_packs(
            id, protocol, pack_code, version, title, jurisdiction,
            sha256, canonical_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'now')
        """,
        (
            pack_id,
            document["protocol"],
            document["pack_code"],
            document["version"],
            document["title"],
            document["jurisdiction"],
            sha256,
            canonical_json,
        ),
    )


class EngineDomainPackRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        from helios_takeoff_core.engine.repository import EngineRepository

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "helios.sqlite3")
        self.database.initialize()
        self.repository = EngineRepository(self.database)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _non_engine_counts(self) -> dict[str, int]:
        with self.database.connection() as connection:
            tables = [
                row["name"]
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table'
                      AND name NOT LIKE 'sqlite_%'
                      AND name NOT LIKE 'engine_%'
                      AND name <> 'schema_migrations'
                    ORDER BY name
                    """
                )
            ]
            return {table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}

    def _assert_no_engine_rows(self) -> None:
        with self.database.connection() as connection:
            for table in (
                "engine_domain_packs",
                "engine_pack_provenance",
                "engine_catalog_items",
                "engine_catalog_relations",
                "engine_rule_definitions",
            ):
                with self.subTest(table=table):
                    self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_schema_17_adds_only_the_five_definition_tables_with_closed_checks_and_composite_fks(self) -> None:
        """A loose or authority-expanding migration makes this schema contract fail."""
        expected = {
            "engine_domain_packs",
            "engine_pack_provenance",
            "engine_catalog_items",
            "engine_catalog_relations",
            "engine_rule_definitions",
        }
        with self.database.connection() as connection:
            version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'engine_%'"
                )
            }
            relation_fks = connection.execute("PRAGMA foreign_key_list(engine_catalog_relations)").fetchall()
            rule_fks = connection.execute("PRAGMA foreign_key_list(engine_rule_definitions)").fetchall()
            pack_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(engine_domain_packs)")
            }

            for table in (
                "engine_pack_provenance",
                "engine_catalog_items",
                "engine_catalog_relations",
                "engine_rule_definitions",
            ):
                with self.subTest(table=table), self.assertRaises(sqlite3.OperationalError):
                    connection.execute(f"SELECT rowid FROM {table}").fetchone()

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO engine_domain_packs(
                        id, protocol, pack_code, version, title, jurisdiction,
                        sha256, canonical_json, created_at
                    ) VALUES ('bad', 'wrong', 'PACK', '1', 'Bad', 'US', ?, '{}', 'now')
                    """,
                    ("0" * 64,),
                )

        self.assertEqual(version, 17)
        self.assertEqual(tables, expected)
        self.assertNotIn("finalized_at", pack_columns)
        self.assertEqual(
            {(row["from"], row["table"], row["to"]) for row in relation_fks},
            {
                ("pack_id", "engine_catalog_items", "pack_id"),
                ("from_code", "engine_catalog_items", "code"),
                ("to_code", "engine_catalog_items", "code"),
            },
        )
        self.assertEqual(
            {(row["from"], row["table"], row["to"]) for row in rule_fks},
            {
                ("pack_id", "engine_catalog_items", "pack_id"),
                ("subject_code", "engine_catalog_items", "code"),
            },
        )

    def test_import_is_atomic_normalized_and_does_not_mutate_p0_or_p1a(self) -> None:
        """Writing outside the five registry tables or committing a partial pack fails this contract."""
        before = self._non_engine_counts()
        compiled = compile_domain_pack(valid_pack())

        pack_id = self.repository.import_domain_pack(compiled)

        self.assertTrue(pack_id)
        self.assertEqual(self._non_engine_counts(), before)
        with self.database.connection() as connection:
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "engine_domain_packs",
                    "engine_pack_provenance",
                    "engine_catalog_items",
                    "engine_catalog_relations",
                    "engine_rule_definitions",
                )
            }
            stored = connection.execute(
                "SELECT sha256, canonical_json FROM engine_domain_packs WHERE id = ?",
                (pack_id,),
            ).fetchone()
        self.assertEqual(counts, {
            "engine_domain_packs": 1,
            "engine_pack_provenance": 2,
            "engine_catalog_items": 3,
            "engine_catalog_relations": 2,
            "engine_rule_definitions": 2,
        })
        self.assertEqual(stored["sha256"], compiled.sha256)
        self.assertEqual(stored["canonical_json"].encode("utf-8"), compiled.canonical_bytes)

        broken_document = json.loads(compiled.canonical_bytes)
        broken_document["pack_code"] = "DIV23-BROKEN"
        broken_document["relations"].append(deepcopy(broken_document["relations"][0]))
        broken_bytes = json.dumps(
            broken_document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        broken = CompiledDomainPack(
            canonical_document=broken_document,
            canonical_bytes=broken_bytes,
            sha256=hashlib.sha256(broken_bytes).hexdigest(),
            catalog_by_code={},
            rules_by_code={},
        )
        with self.assertRaises(ValidationError):
            self.repository.import_domain_pack(broken)
        with self.database.connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM engine_domain_packs").fetchone()[0], 1
            )

    def test_queries_return_exact_canonical_definitions_in_deterministic_order(self) -> None:
        """Returning storage metadata or unordered definitions breaks the public query API."""
        compiled = compile_domain_pack(valid_pack())
        self.repository.import_domain_pack(compiled)
        expected = json.loads(compiled.canonical_bytes)

        self.assertEqual(
            self.repository.get_domain_pack(pack_code="DIV23-HVAC", version="1.0.0"), expected
        )
        self.assertEqual(
            self.repository.get_catalog_item(
                pack_code="DIV23-HVAC", version="1.0.0", item_code="CMP-AHU"
            ),
            next(item for item in expected["catalog"] if item["code"] == "CMP-AHU"),
        )
        self.assertEqual(
            self.repository.list_catalog_relations(
                pack_code="DIV23-HVAC", version="1.0.0", item_code="CMP-AHU"
            ),
            expected["relations"],
        )
        self.assertEqual(
            self.repository.list_catalog_relations(pack_code="DIV23-HVAC", version="1.0.0"),
            expected["relations"],
        )
        self.assertEqual(
            self.repository.get_rule_definition(
                pack_code="DIV23-HVAC", version="1.0.0", rule_code="MEASURE-AHU-COUNT"
            ),
            next(rule for rule in expected["rules"] if rule["code"] == "MEASURE-AHU-COUNT"),
        )
        self.assertNotIn(
            "output_uom",
            self.repository.get_rule_definition(
                pack_code="DIV23-HVAC", version="1.0.0", rule_code="CLASSIFY-AHU"
            ),
        )

    def test_identical_replay_returns_id_and_same_version_change_conflicts(self) -> None:
        """A replay insert or silent same-version replacement violates immutable identity."""
        compiled = compile_domain_pack(valid_pack())
        first_id = self.repository.import_domain_pack(compiled)

        self.assertEqual(self.repository.import_domain_pack(compiled), first_id)

        changed = valid_pack()
        changed["title"] = "Changed immutable title"
        with self.assertRaises(ConflictError):
            self.repository.import_domain_pack(compile_domain_pack(changed))
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM engine_domain_packs").fetchone()[0], 1)

    def test_rejects_digest_that_does_not_match_canonical_bytes_before_insert(self) -> None:
        """Trusting a caller-supplied digest breaks content-addressed pack identity."""
        compiled = compile_domain_pack(valid_pack())
        incoherent = replace(compiled, sha256="0" * 64)

        with self.assertRaisesRegex(ValidationError, "digest"):
            self.repository.import_domain_pack(incoherent)

        self._assert_no_engine_rows()

    def test_rejects_canonical_document_that_disagrees_with_bytes_before_insert(self) -> None:
        """Using document B for indexes while storing bytes A creates a split-brain pack."""
        compiled = compile_domain_pack(valid_pack())
        changed = valid_pack()
        changed["title"] = "Different document"
        incoherent = replace(
            compiled,
            canonical_document=compile_domain_pack(changed).canonical_document,
        )

        with self.assertRaisesRegex(ValidationError, "document"):
            self.repository.import_domain_pack(incoherent)

        self._assert_no_engine_rows()

    def test_rejects_valid_json_bytes_that_are_not_exact_canonical_encoding(self) -> None:
        """Accepting alternate JSON encodings defeats byte-stable canonical identity."""
        compiled = compile_domain_pack(valid_pack())
        noncanonical_bytes = json.dumps(
            json.loads(compiled.canonical_bytes),
            sort_keys=False,
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8")
        incoherent = replace(
            compiled,
            canonical_bytes=noncanonical_bytes,
            sha256=hashlib.sha256(noncanonical_bytes).hexdigest(),
        )

        with self.assertRaisesRegex(ValidationError, "canonical"):
            self.repository.import_domain_pack(incoherent)

        self._assert_no_engine_rows()

    def test_rejects_canonical_bytes_that_bypass_the_strict_compiler(self) -> None:
        """A coherent hand-built value cannot bypass forbidden-field validation."""
        document = valid_pack()
        document["price"] = 100
        canonical_bytes = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        forged = CompiledDomainPack(
            canonical_document=document,
            canonical_bytes=canonical_bytes,
            sha256=hashlib.sha256(canonical_bytes).hexdigest(),
            catalog_by_code={},
            rules_by_code={},
        )

        with self.assertRaisesRegex(ValidationError, "fields"):
            self.repository.import_domain_pack(forged)

        self._assert_no_engine_rows()

    def test_sealed_pack_rejects_every_normalized_projection_insert_and_replace(self) -> None:
        """A committed parent seals every normalized projection against direct SQL."""
        pack_id = self.repository.import_domain_pack(compile_domain_pack(valid_pack()))
        insertions = (
            (
                "engine_pack_provenance",
                "INSERT INTO engine_pack_provenance(pack_id, kind, reference) VALUES (?, 'PUBLIC_URL', 'https://example.test/extra')",
                (pack_id,),
            ),
            (
                "engine_catalog_items",
                "INSERT INTO engine_catalog_items(pack_id, code, type, title, uom) VALUES (?, 'CMP-EXTRA', 'COMPONENT', 'Extra', 'EA')",
                (pack_id,),
            ),
            (
                "engine_catalog_relations",
                "INSERT INTO engine_catalog_relations(pack_id, from_code, relation, to_code) VALUES (?, 'CMP-AHU', 'REQUIRES', 'SYS-AHU')",
                (pack_id,),
            ),
            (
                "engine_rule_definitions",
                """
                INSERT INTO engine_rule_definitions(
                    pack_id, code, type, subject_code,
                    required_observations_json, required_evidence_json,
                    output_claim_type, output_uom
                ) VALUES (?, 'RULE-EXTRA', 'CLASSIFY', 'CMP-AHU', '["AHU_TAG"]', '[]', 'EQUIPMENT_CLASS', NULL)
                """,
                (pack_id,),
            ),
        )
        for table, statement, parameters in insertions:
            with self.subTest(table=table), self.database.connection() as connection:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "sealed"):
                    connection.execute(statement, parameters)

        with self.database.connection() as connection:
            original = connection.execute(
                """
                SELECT required_observations_json, required_evidence_json,
                       output_claim_type, output_uom
                FROM engine_rule_definitions
                WHERE pack_id = ? AND code = 'MEASURE-AHU-COUNT'
                """,
                (pack_id,),
            ).fetchone()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "sealed"):
                connection.execute(
                    """
                    INSERT OR REPLACE INTO engine_rule_definitions(
                        pack_id, code, type, subject_code,
                        required_observations_json, required_evidence_json,
                        output_claim_type, output_uom
                    ) VALUES (?, 'MEASURE-AHU-COUNT', 'MEASURE', 'CMP-AHU',
                              '["AHU_TAG"]', '["MECHANICAL_PLAN"]',
                              'TAMPERED_CLAIM', 'EA')
                    """,
                    (pack_id,),
                )
        with self.database.connection() as connection:
            preserved = connection.execute(
                """
                SELECT required_observations_json, required_evidence_json,
                       output_claim_type, output_uom
                FROM engine_rule_definitions
                WHERE pack_id = ? AND code = 'MEASURE-AHU-COUNT'
                """,
                (pack_id,),
            ).fetchone()
        self.assertEqual(dict(preserved), dict(original))

        with self.database.connection() as connection:
            parent_before = connection.execute(
                "SELECT * FROM engine_domain_packs WHERE id = ?", (pack_id,)
            ).fetchone()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "cannot be replaced"):
                connection.execute(
                    """
                    INSERT OR REPLACE INTO engine_domain_packs(
                        id, protocol, pack_code, version, title, jurisdiction,
                        sha256, canonical_json, created_at
                    ) VALUES (?, ?, ?, ?, 'Tampered title', ?, ?, ?, 'later')
                    """,
                    (
                        pack_id,
                        parent_before["protocol"],
                        parent_before["pack_code"],
                        parent_before["version"],
                        parent_before["jurisdiction"],
                        parent_before["sha256"],
                        parent_before["canonical_json"],
                    ),
                )
        with self.database.connection() as connection:
            parent_after = connection.execute(
                "SELECT * FROM engine_domain_packs WHERE id = ?", (pack_id,)
            ).fetchone()
        self.assertEqual(dict(parent_after), dict(parent_before))

    def test_orphan_projection_can_be_staged_but_cannot_commit(self) -> None:
        """Deferred child FKs permit parent-last assembly but reject unfinished commits."""
        connection = sqlite3.connect(self.database.path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN")
            try:
                connection.execute(
                    """
                    INSERT INTO engine_catalog_items(pack_id, code, type, title, uom)
                    VALUES ('orphan-pack', 'CMP-ORPHAN', 'COMPONENT', 'Orphan', 'EA')
                    """
                )
            except sqlite3.IntegrityError:
                self.fail("child-to-parent foreign keys must be deferred until commit")
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM engine_catalog_items WHERE pack_id = 'orphan-pack'"
                ).fetchone()[0],
                1,
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY"):
                connection.commit()
            connection.rollback()
        finally:
            connection.close()
        self._assert_no_engine_rows()

    def test_parent_insert_rejects_incoherent_headers_and_projections(self) -> None:
        """The append-only parent seal requires exact canonical headers and projection sets."""
        compiled = compile_domain_pack(valid_pack())
        canonical = json.loads(compiled.canonical_bytes)
        cases: list[tuple[str, dict[str, Any], str | None]] = []

        wrong_provenance = deepcopy(canonical)
        wrong_provenance["provenance"][0]["reference"] = "b" * 64
        cases.append(("provenance membership", wrong_provenance, None))

        extra_catalog = deepcopy(canonical)
        extra_catalog["catalog"].append(
            {"code": "CMP-EXTRA", "type": "COMPONENT", "title": "Extra", "uom": "EA"}
        )
        cases.append(("catalog count", extra_catalog, None))

        wrong_relation = deepcopy(canonical)
        wrong_relation["relations"][0]["relation"] = "REQUIRES"
        cases.append(("relation membership", wrong_relation, None))

        wrong_rule_arrays = deepcopy(canonical)
        wrong_rule_arrays["rules"][0]["required_observations"] = ["OTHER_INPUT"]
        wrong_rule_arrays["rules"][0]["required_evidence"] = ["OTHER_EVIDENCE"]
        cases.append(("rule arrays", wrong_rule_arrays, None))

        wrong_null_uom = deepcopy(canonical)
        next(
            rule for rule in wrong_null_uom["rules"] if rule["code"] == "MEASURE-AHU-COUNT"
        )["output_uom"] = None
        cases.append(("rule null UOM", wrong_null_uom, None))

        cases.append(("header", deepcopy(canonical), "Different normalized title"))

        for index, (name, staged_document, parent_title) in enumerate(cases):
            connection = self.database._new_connection()
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("BEGIN")
                pack_id = f"invalid-seal-{index}"
                _stage_pack_projections(
                    connection, pack_id=pack_id, document=staged_document
                )
                with self.subTest(case=name), self.assertRaisesRegex(
                    sqlite3.IntegrityError, "canonical"
                ):
                    _insert_pack_parent(
                        connection,
                        pack_id=pack_id,
                        compiled=compiled,
                        title=parent_title,
                    )
                connection.rollback()
            finally:
                connection.close()

        with self.assertRaises(NotFoundError):
            self.repository.get_domain_pack(pack_code="DIV23-HVAC", version="1.0.0")
        self._assert_no_engine_rows()

    def test_parent_seal_rejects_compiler_invalid_or_noncanonical_payloads(self) -> None:
        """Projection equality cannot publish bytes the strict compiler did not produce."""
        compiled = compile_domain_pack(valid_pack())
        canonical = json.loads(compiled.canonical_bytes)

        whitespace_json = json.dumps(canonical, indent=2, ensure_ascii=False)
        reordered_json = json.dumps(
            {key: canonical[key] for key in reversed(canonical)},
            separators=(",", ":"),
            ensure_ascii=False,
        )

        explicit_null_uom = deepcopy(canonical)
        next(
            rule
            for rule in explicit_null_uom["rules"]
            if rule["code"] == "CLASSIFY-AHU"
        )["output_uom"] = None
        explicit_null_json = json.dumps(
            explicit_null_uom, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

        empty_definitions = deepcopy(canonical)
        empty_definitions["provenance"] = []
        empty_definitions["catalog"] = []
        empty_definitions["relations"] = []
        empty_definitions["rules"] = []
        empty_json = json.dumps(
            empty_definitions, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

        duplicate_observation = deepcopy(canonical)
        duplicate_observation["rules"][0]["required_observations"] = [
            "AHU_TAG",
            "AHU_TAG",
        ]
        duplicate_observation_json = json.dumps(
            duplicate_observation, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

        cases = (
            ("wrong digest", canonical, compiled.canonical_bytes.decode("utf-8"), "0" * 64),
            (
                "whitespace encoding",
                canonical,
                whitespace_json,
                hashlib.sha256(whitespace_json.encode("utf-8")).hexdigest(),
            ),
            (
                "reordered encoding",
                canonical,
                reordered_json,
                hashlib.sha256(reordered_json.encode("utf-8")).hexdigest(),
            ),
            (
                "explicit null output UOM",
                explicit_null_uom,
                explicit_null_json,
                hashlib.sha256(explicit_null_json.encode("utf-8")).hexdigest(),
            ),
            (
                "empty provenance and catalog",
                empty_definitions,
                empty_json,
                hashlib.sha256(empty_json.encode("utf-8")).hexdigest(),
            ),
            (
                "duplicate observation",
                duplicate_observation,
                duplicate_observation_json,
                hashlib.sha256(duplicate_observation_json.encode("utf-8")).hexdigest(),
            ),
        )

        for index, (name, document, canonical_json, sha256) in enumerate(cases):
            connection = self.database._new_connection()
            try:
                connection.execute("BEGIN")
                pack_id = f"invalid-compiler-seal-{index}"
                _stage_pack_projections(connection, pack_id=pack_id, document=document)
                with self.subTest(case=name), self.assertRaisesRegex(
                    sqlite3.IntegrityError, "canonical payload"
                ):
                    _insert_raw_pack_parent(
                        connection,
                        pack_id=pack_id,
                        document=document,
                        canonical_json=canonical_json,
                        sha256=sha256,
                    )
                connection.rollback()
            finally:
                connection.close()

        self._assert_no_engine_rows()

    def test_raw_sqlite_connection_without_validator_cannot_seal_pack(self) -> None:
        """Bypassing Database registration fails closed instead of publishing a pack."""
        compiled = compile_domain_pack(valid_pack())
        document = json.loads(compiled.canonical_bytes)
        connection = sqlite3.connect(self.database.path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN")
            _stage_pack_projections(connection, pack_id="raw-pack", document=document)
            with self.assertRaisesRegex(sqlite3.OperationalError, "no such function"):
                _insert_pack_parent(
                    connection,
                    pack_id="raw-pack",
                    compiled=compiled,
                )
            connection.rollback()
        finally:
            connection.close()

        self._assert_no_engine_rows()

    def test_all_five_tables_reject_updates_and_deletes(self) -> None:
        """Removing any append-only trigger permits registry history to be rewritten."""
        self.repository.import_domain_pack(compile_domain_pack(valid_pack()))

        table_keys = {
            "engine_domain_packs": "id",
            "engine_pack_provenance": "reference",
            "engine_catalog_items": "code",
            "engine_catalog_relations": "relation",
            "engine_rule_definitions": "code",
        }
        for table, key in table_keys.items():
            with self.subTest(table=table), self.database.connection() as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f"UPDATE {table} SET {key} = {key}")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(f"DELETE FROM {table}")

    def test_missing_pack_item_and_rule_raise_not_found(self) -> None:
        """Returning ambiguous nulls makes finite CLI errors inconsistent."""
        self.repository.import_domain_pack(compile_domain_pack(valid_pack()))

        with self.assertRaises(NotFoundError):
            self.repository.get_domain_pack(pack_code="MISSING", version="1")
        with self.assertRaises(NotFoundError):
            self.repository.get_catalog_item(
                pack_code="DIV23-HVAC", version="1.0.0", item_code="MISSING"
            )
        with self.assertRaises(NotFoundError):
            self.repository.get_rule_definition(
                pack_code="DIV23-HVAC", version="1.0.0", rule_code="MISSING"
            )


if __name__ == "__main__":
    unittest.main()
