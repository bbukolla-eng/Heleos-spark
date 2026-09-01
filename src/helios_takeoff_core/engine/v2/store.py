"""Isolated immutable-blob SQLite store for Division 23 v2 registry packs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
import hashlib
from importlib import resources
import json
from pathlib import Path
import sqlite3
from typing import Iterator, Mapping, cast

from ...errors import ConflictError, NotFoundError, ValidationError
from .compiler import compile_domain_pack
from .contracts import (
    CitationRecord,
    CompiledDomainPack,
    DefinitionKind,
    DefinitionRecord,
    DomainFamily,
    RelationKind,
    RelationRecord,
    StoreIntegrityReport,
)


_MIGRATION_NAME = "001_engine_registry.sql"
_EXPECTED_TABLES = frozenset(
    {
        "engine_store_migrations", "engine_packs", "engine_source_index",
        "engine_definition_index", "engine_relation_index", "engine_citation_index",
        "engine_registry_events",
    }
)
_ASSERTION_KINDS = frozenset({"DEFINITION", "RELATION"})


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _json_text(value: object) -> str:
    return _json_bytes(value).decode("utf-8")


def _materialize(value: object) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: _materialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_materialize(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise TypeError("value is not canonical JSON")


class EngineStore:
    """Own a dedicated seven-table registry whose canonical blobs are authoritative."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def initialize(self) -> None:
        """Create only the isolated schema, or reject every foreign/partial database."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            tables = self._table_names(connection)
            if tables:
                self._validate_schema_or_raise(connection)
                return
            try:
                connection.execute("BEGIN IMMEDIATE")
                if self._table_names(connection):
                    self._validate_schema_or_raise(connection)
                    connection.commit()
                    return
                migration_bytes = self._migration_bytes()
                for statement in self._sql_statements(migration_bytes.decode("utf-8")):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO engine_store_migrations(version, name, sha256, applied_at) VALUES (1, ?, ?, ?)",
                    (_MIGRATION_NAME, hashlib.sha256(migration_bytes).hexdigest(), _now()),
                )
                self._validate_schema_or_raise(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def import_pack(self, pack: CompiledDomainPack) -> str:
        """Atomically import canonical bytes and disposable graph projections."""
        compiled = self._validated_compiled_pack(pack)
        document = cast(dict[str, object], json.loads(compiled.canonical_bytes))
        source_count, definition_count, relation_count, citation_count = self._projection_counts(compiled)
        with self._transaction() as connection:
            self._validate_schema_or_raise(connection)
            persisted_packs = tuple(
                self._load_compiled(connection, str(row["pack_sha256"]))
                for row in connection.execute("SELECT pack_sha256 FROM engine_packs ORDER BY pack_sha256").fetchall()
            )
            existing = next(
                (
                    item for item in persisted_packs
                    if item.canonical_document["pack_code"] == document["pack_code"]
                    and item.canonical_document["version"] == document["version"]
                ),
                None,
            )
            if existing is not None:
                if existing.sha256 == compiled.sha256 and existing.canonical_bytes == compiled.canonical_bytes:
                    return compiled.sha256
                raise ConflictError("pack code and version already identify different immutable content")
            digest_owner = next((item for item in persisted_packs if item.sha256 == compiled.sha256), None)
            if digest_owner is not None:
                raise ConflictError("pack digest already identifies a different immutable identity")
            self._validate_supersession(connection, compiled, document)
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
                    source_count, definition_count, relation_count, citation_count, _now(),
                ),
            )
            self._insert_projections(connection, compiled, projection_generation=1)
            self._append_event(
                connection,
                pack_sha256=compiled.sha256,
                event_type="PACK_IMPORTED",
                projection_generation=1,
            )
        return compiled.sha256

    def load_pack(self, *, pack_sha256: str) -> CompiledDomainPack:
        with self._read_transaction() as connection:
            self._validate_schema_or_raise(connection)
            compiled = self._load_compiled(connection, pack_sha256)
            self._projection_generation(connection, compiled, require_projection_rows=True)
            return compiled

    def get_source(self, *, pack_sha256: str, source_id: str) -> dict[str, object]:
        with self._read_transaction() as connection:
            compiled = self._load_compiled(connection, pack_sha256)
            self._validate_projection_snapshot(connection, compiled)
            row = connection.execute(
                "SELECT source_json FROM engine_source_index WHERE pack_sha256 = ? AND source_id = ?",
                (pack_sha256, source_id),
            ).fetchone()
            if row is None:
                raise NotFoundError("source was not found")
            actual = self._parse_json_text(row["source_json"], "source projection")
            expected = compiled.sources_by_id.get(source_id)
            if expected is None or actual != _materialize(expected):
                raise ValidationError("source projection does not match canonical pack")
            return cast(dict[str, object], actual)

    def get_definition(self, *, pack_sha256: str, definition_code: str) -> DefinitionRecord:
        with self._read_transaction() as connection:
            compiled = self._load_compiled(connection, pack_sha256)
            self._validate_projection_snapshot(connection, compiled)
            row = connection.execute(
                "SELECT definition_json FROM engine_definition_index WHERE pack_sha256 = ? AND definition_code = ?",
                (pack_sha256, definition_code),
            ).fetchone()
            if row is None:
                raise NotFoundError("definition was not found")
            self._validate_definition_projection(compiled, definition_code, row["definition_json"])
            return compiled.definitions_by_code[definition_code]

    def list_definitions(
        self,
        *,
        pack_sha256: str,
        kind: DefinitionKind | None = None,
        family: DomainFamily | None = None,
    ) -> tuple[DefinitionRecord, ...]:
        if kind is not None and not isinstance(kind, DefinitionKind):
            raise ValidationError("kind must be a DefinitionKind")
        if family is not None and not isinstance(family, DomainFamily):
            raise ValidationError("family must be a DomainFamily")
        clauses = ["pack_sha256 = ?"]
        parameters: list[object] = [pack_sha256]
        if kind is not None:
            clauses.append("kind = ?")
            parameters.append(kind.value)
        if family is not None:
            clauses.append("family = ?")
            parameters.append(family.value)
        with self._read_transaction() as connection:
            compiled = self._load_compiled(connection, pack_sha256)
            self._validate_projection_snapshot(connection, compiled)
            rows = connection.execute(
                "SELECT definition_code, definition_json FROM engine_definition_index WHERE "
                + " AND ".join(clauses)
                + " ORDER BY definition_code",
                parameters,
            ).fetchall()
            result: list[DefinitionRecord] = []
            for row in rows:
                code = str(row["definition_code"])
                self._validate_definition_projection(compiled, code, row["definition_json"])
                result.append(compiled.definitions_by_code[code])
            return tuple(result)

    def get_relation(self, *, pack_sha256: str, relation_code: str) -> RelationRecord:
        with self._read_transaction() as connection:
            compiled = self._load_compiled(connection, pack_sha256)
            self._validate_projection_snapshot(connection, compiled)
            row = connection.execute(
                "SELECT relation_json FROM engine_relation_index WHERE pack_sha256 = ? AND relation_code = ?",
                (pack_sha256, relation_code),
            ).fetchone()
            if row is None:
                raise NotFoundError("relation was not found")
            self._validate_relation_projection(compiled, relation_code, row["relation_json"])
            return compiled.relations_by_code[relation_code]

    def neighbors(
        self,
        *,
        pack_sha256: str,
        definition_code: str,
        relation: RelationKind | None = None,
        direction: str = "OUT",
    ) -> tuple[RelationRecord, ...]:
        if relation is not None and not isinstance(relation, RelationKind):
            raise ValidationError("relation must be a RelationKind")
        if direction not in {"OUT", "IN", "BOTH"}:
            raise ValidationError("direction must be OUT, IN, or BOTH")
        with self._read_transaction() as connection:
            compiled = self._load_compiled(connection, pack_sha256)
            self._validate_projection_snapshot(connection, compiled)
            if definition_code not in compiled.definitions_by_code:
                raise NotFoundError("definition was not found")
            endpoint_clause = {
                "OUT": "from_code = ?",
                "IN": "to_code = ?",
                "BOTH": "(from_code = ? OR to_code = ?)",
            }[direction]
            parameters: list[object] = [pack_sha256, definition_code]
            if direction == "BOTH":
                parameters.append(definition_code)
            relation_clause = ""
            if relation is not None:
                relation_clause = " AND kind = ?"
                parameters.append(relation.value)
            rows = connection.execute(
                "SELECT relation_code, relation_json FROM engine_relation_index "
                f"WHERE pack_sha256 = ? AND {endpoint_clause}{relation_clause} ORDER BY relation_code",
                parameters,
            ).fetchall()
            result: list[RelationRecord] = []
            for row in rows:
                code = str(row["relation_code"])
                self._validate_relation_projection(compiled, code, row["relation_json"])
                result.append(compiled.relations_by_code[code])
            return tuple(result)

    def list_citations(
        self, *, pack_sha256: str, assertion_kind: str, assertion_code: str
    ) -> tuple[CitationRecord, ...]:
        if assertion_kind not in _ASSERTION_KINDS:
            raise ValidationError("assertion_kind must be DEFINITION or RELATION")
        with self._read_transaction() as connection:
            compiled = self._load_compiled(connection, pack_sha256)
            self._validate_projection_snapshot(connection, compiled)
            expected_record: DefinitionRecord | RelationRecord | None
            if assertion_kind == "DEFINITION":
                expected_record = compiled.definitions_by_code.get(assertion_code)
            else:
                expected_record = compiled.relations_by_code.get(assertion_code)
            if expected_record is None:
                raise NotFoundError("assertion was not found")
            rows = connection.execute(
                """
                SELECT citation_ordinal, source_id, citation_json
                FROM engine_citation_index
                WHERE pack_sha256 = ? AND assertion_kind = ? AND assertion_code = ?
                ORDER BY citation_ordinal
                """,
                (pack_sha256, assertion_kind, assertion_code),
            ).fetchall()
            actual = tuple(
                CitationRecord(**cast(dict[str, str], self._parse_json_text(row["citation_json"], "citation projection")))
                for row in rows
            )
            if actual != expected_record.citations or any(
                row["source_id"] != citation.source_ref or row["citation_ordinal"] != index
                for index, (row, citation) in enumerate(zip(rows, actual, strict=True))
            ):
                raise ValidationError("citation projection does not match canonical pack")
            return actual

    def list_events(self, *, pack_sha256: str) -> tuple[dict[str, object], ...]:
        with self._read_transaction() as connection:
            compiled = self._load_compiled(connection, pack_sha256)
            self._projection_generation(connection, compiled, require_projection_rows=True)
            rows = connection.execute(
                """
                SELECT event_sequence, event_type, projection_generation, previous_event_sha256,
                       event_sha256, event_json, created_at
                FROM engine_registry_events
                WHERE pack_sha256 = ? ORDER BY event_sequence
                """,
                (pack_sha256,),
            ).fetchall()
            return tuple(
                {
                    "event_sequence": row["event_sequence"],
                    "event_type": row["event_type"],
                    "projection_generation": row["projection_generation"],
                    "previous_event_sha256": row["previous_event_sha256"],
                    "event_sha256": row["event_sha256"],
                    "event": self._parse_json_text(row["event_json"], "registry event"),
                    "created_at": row["created_at"],
                }
                for row in rows
            )

    def rebuild_indexes(self, *, pack_sha256: str | None = None) -> None:
        with self._transaction() as connection:
            self._validate_schema_or_raise(connection)
            if pack_sha256 is None:
                rows = connection.execute("SELECT pack_sha256 FROM engine_packs ORDER BY pack_sha256").fetchall()
                digests = [str(row["pack_sha256"]) for row in rows]
            else:
                self._require_digest(pack_sha256)
                if connection.execute("SELECT 1 FROM engine_packs WHERE pack_sha256 = ?", (pack_sha256,)).fetchone() is None:
                    raise NotFoundError("domain pack was not found")
                digests = [pack_sha256]
            compiled_packs: list[tuple[CompiledDomainPack, int]] = []
            for digest in digests:
                compiled = self._load_compiled(connection, digest)
                generation = self._projection_generation(
                    connection, compiled, require_projection_rows=False
                )
                compiled_packs.append((compiled, generation))
            for compiled, current_generation in compiled_packs:
                digest = compiled.sha256
                next_generation = current_generation + 1
                connection.execute("DELETE FROM engine_citation_index WHERE pack_sha256 = ?", (digest,))
                connection.execute("DELETE FROM engine_relation_index WHERE pack_sha256 = ?", (digest,))
                connection.execute("DELETE FROM engine_definition_index WHERE pack_sha256 = ?", (digest,))
                connection.execute("DELETE FROM engine_source_index WHERE pack_sha256 = ?", (digest,))
                self._insert_projections(
                    connection, compiled, projection_generation=next_generation
                )
                connection.execute(
                    "UPDATE engine_packs SET projection_generation = ? WHERE pack_sha256 = ?",
                    (next_generation, digest),
                )
                self._append_event(
                    connection,
                    pack_sha256=digest,
                    event_type="PROJECTIONS_REBUILT",
                    projection_generation=next_generation,
                )

    def verify_integrity(self) -> StoreIntegrityReport:
        errors: list[str] = []
        pack_count = 0
        event_count = 0
        try:
            with self._read_transaction() as connection:
                self._validate_schema_or_raise(connection)
                pack_rows = connection.execute("SELECT pack_sha256 FROM engine_packs ORDER BY pack_sha256").fetchall()
                pack_count = len(pack_rows)
                event_count = connection.execute("SELECT COUNT(*) FROM engine_registry_events").fetchone()[0]
                for row in pack_rows:
                    digest = str(row["pack_sha256"])
                    try:
                        compiled = self._load_compiled(connection, digest)
                        generation = self._projection_generation(
                            connection, compiled, require_projection_rows=True
                        )
                        if self._actual_projection_snapshot(connection, digest) != self._expected_projection_snapshot(
                            compiled, generation
                        ):
                            errors.append(f"pack {digest} projection mismatch")
                    except (ValidationError, NotFoundError, sqlite3.DatabaseError, TypeError, ValueError) as error:
                        errors.append(f"pack {digest} integrity failure: {error}")
        except (ValidationError, sqlite3.DatabaseError) as error:
            errors.append(str(error))
        return StoreIntegrityReport(not errors, tuple(errors), pack_count, event_count)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.create_function(
                "helios_v2_pack_is_canonical", 11, self._sqlite_pack_is_canonical, deterministic=True
            )
            connection.create_function(
                "helios_v2_event_is_canonical", 8, self._sqlite_event_is_canonical, deterministic=True
            )
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _read_transaction(self) -> Iterator[sqlite3.Connection]:
        """Hold one stable SQLite snapshot across validation and the dependent read."""
        with self._connect() as connection:
            try:
                connection.execute("BEGIN")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _sqlite_pack_is_canonical(
        canonical_json: object,
        digest: object,
        protocol: object,
        pack_code: object,
        version: object,
        valid_from: object,
        supersedes_pack_sha256: object,
        source_count: object,
        definition_count: object,
        relation_count: object,
        citation_count: object,
    ) -> int:
        try:
            if not isinstance(canonical_json, bytes) or not isinstance(digest, str):
                return 0
            document = json.loads(canonical_json.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            compiled = compile_domain_pack(document)
            expected_header = (
                document["protocol"], document["pack_code"], document["version"],
                document["valid_from"], document["supersedes_pack_sha256"],
                *EngineStore._projection_counts(compiled),
            )
            actual_header = (
                protocol, pack_code, version, valid_from, supersedes_pack_sha256,
                source_count, definition_count, relation_count, citation_count,
            )
            return int(
                compiled.canonical_bytes == canonical_json
                and compiled.sha256 == digest
                and actual_header == expected_header
            )
        except Exception:
            return 0

    @staticmethod
    def _sqlite_event_is_canonical(
        event_json: object,
        event_sha256: object,
        pack_sha256: object,
        event_sequence: object,
        event_type: object,
        projection_generation: object,
        previous_event_sha256: object,
        created_at: object,
    ) -> int:
        try:
            if (
                not isinstance(event_json, str)
                or not isinstance(event_sha256, str)
                or not isinstance(pack_sha256, str)
                or not isinstance(event_sequence, int)
                or not isinstance(event_type, str)
                or not isinstance(projection_generation, int)
                or (previous_event_sha256 is not None and not isinstance(previous_event_sha256, str))
                or not isinstance(created_at, str)
            ):
                return 0
            event = {
                "protocol": "helios.engine.registry-event/v1",
                "pack_sha256": pack_sha256,
                "event_sequence": event_sequence,
                "event_type": event_type,
                "projection_generation": projection_generation,
                "previous_event_sha256": previous_event_sha256,
                "created_at": created_at,
            }
            canonical = _json_text(event)
            return int(
                event_json == canonical
                and event_sha256 == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            )
        except Exception:
            return 0

    @staticmethod
    def _migration_bytes() -> bytes:
        return (resources.files("helios_takeoff_core.engine.v2") / "migrations" / _MIGRATION_NAME).read_bytes()

    @classmethod
    def _expected_schema_objects(cls) -> dict[tuple[str, str], str]:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        try:
            connection.create_function("helios_v2_pack_is_canonical", 11, lambda *_args: 0, deterministic=True)
            connection.create_function("helios_v2_event_is_canonical", 8, lambda *_args: 0, deterministic=True)
            for statement in cls._sql_statements(cls._migration_bytes().decode("utf-8")):
                connection.execute(statement)
            return cls._schema_objects(connection)
        finally:
            connection.close()

    @staticmethod
    def _schema_objects(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
        rows = connection.execute(
            """
            SELECT type, name, sql FROM sqlite_master
            WHERE type IN ('table', 'index', 'trigger')
              AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
            """
        ).fetchall()
        return {(str(row[0]), str(row[1])): str(row[2]) for row in rows}

    @staticmethod
    def _table_names(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

    def _validate_schema_or_raise(self, connection: sqlite3.Connection) -> None:
        tables = self._table_names(connection)
        if tables != _EXPECTED_TABLES:
            raise ValidationError("engine store must be an isolated exact seven-table database")
        if self._schema_objects(connection) != self._expected_schema_objects():
            raise ValidationError("engine store schema objects do not match the packaged migration")
        rows = connection.execute("SELECT version, name, sha256 FROM engine_store_migrations").fetchall()
        expected_digest = hashlib.sha256(self._migration_bytes()).hexdigest()
        if len(rows) != 1 or tuple(rows[0]) != (1, _MIGRATION_NAME, expected_digest):
            raise ValidationError("engine store migration identity is invalid")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValidationError("engine store contains a foreign-key violation")

    @staticmethod
    def _sql_statements(script: str) -> Iterator[str]:
        pending = ""
        for line in script.splitlines(keepends=True):
            pending += line
            if sqlite3.complete_statement(pending):
                statement = pending.strip()
                if statement:
                    yield statement
                pending = ""
        if pending.strip():
            raise sqlite3.OperationalError("migration ends with an incomplete SQL statement")

    @classmethod
    def _validated_compiled_pack(cls, pack: CompiledDomainPack) -> CompiledDomainPack:
        if not isinstance(pack, CompiledDomainPack) or not isinstance(pack.canonical_bytes, bytes):
            raise ValidationError("pack must be a compiled v2 domain pack")
        if hashlib.sha256(pack.canonical_bytes).hexdigest() != pack.sha256:
            raise ValidationError("pack digest does not match canonical bytes")
        try:
            document = json.loads(pack.canonical_bytes.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            recompiled = compile_domain_pack(document)
            declared = _materialize(pack.canonical_document)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise ValidationError("pack canonical content is invalid") from error
        if recompiled.canonical_bytes != pack.canonical_bytes or recompiled.sha256 != pack.sha256 or declared != document:
            raise ValidationError("pack projections do not share one canonical identity")
        if (
            _materialize(pack.sources_by_id) != _materialize(recompiled.sources_by_id)
            or pack.definitions_by_code != recompiled.definitions_by_code
            or pack.relations_by_code != recompiled.relations_by_code
            or _materialize(pack.units_by_uom) != _materialize(recompiled.units_by_uom)
            or _materialize(pack.observations_by_name) != _materialize(recompiled.observations_by_name)
            or _materialize(pack.lookups_by_code) != _materialize(recompiled.lookups_by_code)
            or _materialize(pack.assembly_edges_by_code) != _materialize(recompiled.assembly_edges_by_code)
            or _materialize(pack.rules_by_code) != _materialize(recompiled.rules_by_code)
        ):
            raise ValidationError("pack compiled indexes do not match canonical bytes")
        return recompiled

    @classmethod
    def _validate_supersession(
        cls, connection: sqlite3.Connection, compiled: CompiledDomainPack, document: dict[str, object]
    ) -> None:
        supersedes = document["supersedes_pack_sha256"]
        if supersedes is None:
            return
        try:
            prior = cls._load_compiled(connection, cast(str, supersedes))
        except NotFoundError:
            raise ValidationError("superseded pack has not been imported")
        prior_document = prior.canonical_document
        if prior_document["pack_code"] != document["pack_code"]:
            raise ValidationError("superseded pack must have the same pack_code")
        if prior_document["version"] == document["version"] or prior.sha256 == compiled.sha256:
            raise ValidationError("superseding pack must have a different version and digest")
        if date.fromisoformat(str(prior_document["valid_from"])) >= date.fromisoformat(str(document["valid_from"])):
            raise ValidationError("superseded pack valid_from must be strictly earlier")

    @staticmethod
    def _projection_counts(compiled: CompiledDomainPack) -> tuple[int, int, int, int]:
        citation_count = sum(
            len(record.citations) for record in compiled.definitions_by_code.values()
        ) + sum(len(record.citations) for record in compiled.relations_by_code.values())
        return (
            len(compiled.sources_by_id),
            len(compiled.definitions_by_code),
            len(compiled.relations_by_code),
            citation_count,
        )

    @classmethod
    def _insert_projections(
        cls,
        connection: sqlite3.Connection,
        compiled: CompiledDomainPack,
        *,
        projection_generation: int,
    ) -> None:
        document = cast(dict[str, object], json.loads(compiled.canonical_bytes))
        digest = compiled.sha256
        for source in cast(list[dict[str, object]], document["sources"]):
            connection.execute(
                """
                INSERT INTO engine_source_index(
                    pack_sha256, source_id, projection_generation, source_json
                ) VALUES (?, ?, ?, ?)
                """,
                (digest, source["source_id"], projection_generation, _json_text(source)),
            )
        for definition in cast(list[dict[str, object]], document["definitions"]):
            connection.execute(
                """
                INSERT INTO engine_definition_index(
                    pack_sha256, definition_code, kind, family,
                    projection_generation, definition_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    digest, definition["code"], definition["kind"], definition["family"],
                    projection_generation, _json_text(definition),
                ),
            )
        for relation in cast(list[dict[str, object]], document["relations"]):
            connection.execute(
                """
                INSERT INTO engine_relation_index(
                    pack_sha256, relation_code, kind, from_code, to_code,
                    projection_generation, relation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest, relation["code"], relation["kind"], relation["from_code"],
                    relation["to_code"], projection_generation, _json_text(relation),
                ),
            )
        for assertion_kind, assertions in (
            ("DEFINITION", cast(list[dict[str, object]], document["definitions"])),
            ("RELATION", cast(list[dict[str, object]], document["relations"])),
        ):
            for assertion in assertions:
                for ordinal, citation in enumerate(cast(list[dict[str, str]], assertion["citations"])):
                    connection.execute(
                        """
                        INSERT INTO engine_citation_index(
                            pack_sha256, assertion_kind, assertion_code,
                            citation_ordinal, source_id, projection_generation, citation_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            digest, assertion_kind, assertion["code"], ordinal,
                            citation["source_ref"], projection_generation, _json_text(citation),
                        ),
                    )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        *,
        pack_sha256: str,
        event_type: str,
        projection_generation: int,
    ) -> None:
        prior = connection.execute(
            """
            SELECT event_sequence, event_sha256, projection_generation FROM engine_registry_events
            WHERE pack_sha256 = ? ORDER BY event_sequence DESC LIMIT 1
            """,
            (pack_sha256,),
        ).fetchone()
        sequence = 1 if prior is None else int(prior["event_sequence"]) + 1
        previous = None if prior is None else str(prior["event_sha256"])
        expected_type = "PACK_IMPORTED" if prior is None else "PROJECTIONS_REBUILT"
        if event_type != expected_type:
            raise ValidationError("registry event type does not match lifecycle position")
        expected_generation = 1 if prior is None else int(prior["projection_generation"]) + 1
        if projection_generation != expected_generation:
            raise ValidationError("registry event projection generation does not match lifecycle position")
        created_at = _now()
        event = {
            "protocol": "helios.engine.registry-event/v1",
            "pack_sha256": pack_sha256,
            "event_sequence": sequence,
            "event_type": event_type,
            "projection_generation": projection_generation,
            "previous_event_sha256": previous,
            "created_at": created_at,
        }
        event_json = _json_text(event)
        event_sha256 = hashlib.sha256(event_json.encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO engine_registry_events(
                pack_sha256, event_sequence, event_type, projection_generation,
                previous_event_sha256, event_sha256, event_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pack_sha256, sequence, event_type, projection_generation,
                previous, event_sha256, event_json, created_at,
            ),
        )

    @staticmethod
    def _require_digest(digest: str) -> None:
        if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValidationError("pack_sha256 must be a lowercase SHA-256 digest")

    @classmethod
    def _load_compiled(
        cls,
        connection: sqlite3.Connection,
        digest: str,
        *,
        _lineage: frozenset[str] | None = None,
    ) -> CompiledDomainPack:
        cls._require_digest(digest)
        lineage = frozenset() if _lineage is None else _lineage
        if digest in lineage:
            raise ValidationError("persisted supersession lineage contains a cycle")
        row = connection.execute(
            """
            SELECT pack_sha256, protocol, pack_code, version, valid_from,
                   supersedes_pack_sha256, canonical_json, projection_generation,
                   source_count, definition_count, relation_count, citation_count
            FROM engine_packs WHERE pack_sha256 = ?
            """,
            (digest,),
        ).fetchone()
        if row is None:
            raise NotFoundError("domain pack was not found")
        blob = row["canonical_json"]
        if not isinstance(blob, bytes):
            raise ValidationError("canonical pack authority must be stored as bytes")
        try:
            document = json.loads(blob.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            compiled = compile_domain_pack(document)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValidationError("canonical pack blob is invalid") from error
        if compiled.canonical_bytes != blob or compiled.sha256 != digest:
            raise ValidationError("canonical pack blob identity is invalid")
        expected_header = (
            digest,
            document["protocol"],
            document["pack_code"],
            document["version"],
            document["valid_from"],
            document["supersedes_pack_sha256"],
            *cls._projection_counts(compiled),
        )
        actual_header = (
            row["pack_sha256"], row["protocol"], row["pack_code"], row["version"],
            row["valid_from"], row["supersedes_pack_sha256"], row["source_count"],
            row["definition_count"], row["relation_count"], row["citation_count"],
        )
        if actual_header != expected_header:
            raise ValidationError("canonical pack header does not match canonical blob")
        if not isinstance(row["projection_generation"], int) or row["projection_generation"] < 1:
            raise ValidationError("canonical pack projection generation is invalid")
        supersedes = document["supersedes_pack_sha256"]
        if supersedes is not None:
            try:
                prior = cls._load_compiled(
                    connection,
                    cast(str, supersedes),
                    _lineage=lineage | {digest},
                )
            except NotFoundError as error:
                raise ValidationError("persisted supersession lineage references a missing pack") from error
            prior_document = prior.canonical_document
            if (
                prior.sha256 == compiled.sha256
                or prior_document["pack_code"] != document["pack_code"]
                or prior_document["version"] == document["version"]
                or date.fromisoformat(str(prior_document["valid_from"]))
                >= date.fromisoformat(str(document["valid_from"]))
            ):
                raise ValidationError("persisted supersession lineage is invalid")
        return compiled

    @staticmethod
    def _parse_json_text(value: object, context: str) -> object:
        if not isinstance(value, str):
            raise ValidationError(f"{context} must be canonical JSON text")
        try:
            parsed = json.loads(value, parse_constant=lambda constant: (_ for _ in ()).throw(ValueError(constant)))
        except (json.JSONDecodeError, ValueError) as error:
            raise ValidationError(f"{context} is invalid") from error
        if _json_text(parsed) != value:
            raise ValidationError(f"{context} is not canonical")
        return parsed

    @classmethod
    def _validate_definition_projection(
        cls, compiled: CompiledDomainPack, code: str, serialized: object
    ) -> None:
        record = compiled.definitions_by_code.get(code)
        if record is None:
            raise ValidationError("definition projection contains an unknown code")
        actual = cls._parse_json_text(serialized, "definition projection")
        expected = next(
            item for item in cast(tuple[Mapping[str, object], ...], compiled.canonical_document["definitions"])
            if item["code"] == code
        )
        if actual != _materialize(expected):
            raise ValidationError("definition projection does not match canonical pack")

    @classmethod
    def _validate_relation_projection(
        cls, compiled: CompiledDomainPack, code: str, serialized: object
    ) -> None:
        record = compiled.relations_by_code.get(code)
        if record is None:
            raise ValidationError("relation projection contains an unknown code")
        actual = cls._parse_json_text(serialized, "relation projection")
        expected = next(
            item for item in cast(tuple[Mapping[str, object], ...], compiled.canonical_document["relations"])
            if item["code"] == code
        )
        if actual != _materialize(expected):
            raise ValidationError("relation projection does not match canonical pack")

    @staticmethod
    def _expected_projection_snapshot(
        compiled: CompiledDomainPack, projection_generation: int
    ) -> dict[str, tuple[tuple[object, ...], ...]]:
        document = cast(dict[str, object], json.loads(compiled.canonical_bytes))
        sources = tuple(
            (source["source_id"], projection_generation, _json_text(source))
            for source in cast(list[dict[str, object]], document["sources"])
        )
        definitions = tuple(
            (
                definition["code"], definition["kind"], definition["family"],
                projection_generation, _json_text(definition),
            )
            for definition in cast(list[dict[str, object]], document["definitions"])
        )
        relations = tuple(
            (
                relation["code"], relation["kind"], relation["from_code"],
                relation["to_code"], projection_generation, _json_text(relation),
            )
            for relation in cast(list[dict[str, object]], document["relations"])
        )
        citations: list[tuple[object, ...]] = []
        for assertion_kind, assertions in (
            ("DEFINITION", cast(list[dict[str, object]], document["definitions"])),
            ("RELATION", cast(list[dict[str, object]], document["relations"])),
        ):
            for assertion in assertions:
                for ordinal, citation in enumerate(cast(list[dict[str, str]], assertion["citations"])):
                    citations.append(
                        (
                            assertion_kind, assertion["code"], ordinal, citation["source_ref"],
                            projection_generation, _json_text(citation),
                        )
                    )
        return {
            "sources": sources,
            "definitions": definitions,
            "relations": relations,
            "citations": tuple(sorted(citations)),
        }

    @staticmethod
    def _actual_projection_snapshot(
        connection: sqlite3.Connection, digest: str
    ) -> dict[str, tuple[tuple[object, ...], ...]]:
        queries = {
            "sources": "SELECT source_id, projection_generation, source_json FROM engine_source_index WHERE pack_sha256 = ? ORDER BY source_id",
            "definitions": "SELECT definition_code, kind, family, projection_generation, definition_json FROM engine_definition_index WHERE pack_sha256 = ? ORDER BY definition_code",
            "relations": "SELECT relation_code, kind, from_code, to_code, projection_generation, relation_json FROM engine_relation_index WHERE pack_sha256 = ? ORDER BY relation_code",
            "citations": "SELECT assertion_kind, assertion_code, citation_ordinal, source_id, projection_generation, citation_json FROM engine_citation_index WHERE pack_sha256 = ? ORDER BY assertion_kind, assertion_code, citation_ordinal",
        }
        return {
            name: tuple(tuple(row) for row in connection.execute(query, (digest,)).fetchall())
            for name, query in queries.items()
        }

    @classmethod
    def _validate_projection_snapshot(
        cls, connection: sqlite3.Connection, compiled: CompiledDomainPack
    ) -> None:
        generation = cls._projection_generation(
            connection, compiled, require_projection_rows=True
        )
        if cls._actual_projection_snapshot(connection, compiled.sha256) != cls._expected_projection_snapshot(
            compiled, generation
        ):
            raise ValidationError("projection snapshot does not match canonical pack")

    @classmethod
    def _projection_generation(
        cls,
        connection: sqlite3.Connection,
        compiled: CompiledDomainPack,
        *,
        require_projection_rows: bool,
    ) -> int:
        row = connection.execute(
            """
            SELECT projection_generation, source_count, definition_count,
                   relation_count, citation_count
            FROM engine_packs WHERE pack_sha256 = ?
            """,
            (compiled.sha256,),
        ).fetchone()
        if row is None:
            raise NotFoundError("domain pack was not found")
        generation = row["projection_generation"]
        if not isinstance(generation, int) or generation < 1:
            raise ValidationError("projection generation state is invalid")
        event_errors = cls._event_errors(connection, compiled.sha256)
        if event_errors:
            raise ValidationError(event_errors[0])
        latest = connection.execute(
            """
            SELECT projection_generation FROM engine_registry_events
            WHERE pack_sha256 = ? ORDER BY event_sequence DESC LIMIT 1
            """,
            (compiled.sha256,),
        ).fetchone()
        if latest is None or latest["projection_generation"] != generation:
            raise ValidationError("pack and registry event projection generations disagree")
        if require_projection_rows:
            expectations = (
                ("engine_source_index", int(row["source_count"])),
                ("engine_definition_index", int(row["definition_count"])),
                ("engine_relation_index", int(row["relation_count"])),
                ("engine_citation_index", int(row["citation_count"])),
            )
            for table, expected_count in expectations:
                total, current = connection.execute(
                    f"""
                    SELECT COUNT(*),
                           SUM(CASE WHEN projection_generation = ? THEN 1 ELSE 0 END)
                    FROM {table} WHERE pack_sha256 = ?
                    """,
                    (generation, compiled.sha256),
                ).fetchone()
                current_count = 0 if current is None else int(current)
                if int(total) != expected_count or current_count != expected_count:
                    raise ValidationError(
                        "projection snapshot generation rows do not match current pack state"
                    )
        return generation

    @staticmethod
    def _event_errors(connection: sqlite3.Connection, digest: str) -> list[str]:
        rows = connection.execute(
            """
            SELECT event_sequence, event_type, projection_generation, previous_event_sha256,
                   event_sha256, event_json, created_at
            FROM engine_registry_events WHERE pack_sha256 = ? ORDER BY event_sequence
            """,
            (digest,),
        ).fetchall()
        errors: list[str] = []
        previous: str | None = None
        for expected_sequence, row in enumerate(rows, start=1):
            expected_type = "PACK_IMPORTED" if expected_sequence == 1 else "PROJECTIONS_REBUILT"
            expected_event = {
                "protocol": "helios.engine.registry-event/v1",
                "pack_sha256": digest,
                "event_sequence": expected_sequence,
                "event_type": expected_type,
                "projection_generation": expected_sequence,
                "previous_event_sha256": previous,
                "created_at": row["created_at"],
            }
            expected_json = _json_text(expected_event)
            expected_hash = hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
            if (
                row["event_sequence"] != expected_sequence
                or row["event_type"] != expected_type
                or row["projection_generation"] != expected_sequence
                or row["previous_event_sha256"] != previous
                or row["event_json"] != expected_json
                or row["event_sha256"] != expected_hash
            ):
                errors.append(f"pack {digest} registry event chain mismatch")
                break
            previous = str(row["event_sha256"])
        if not rows:
            errors.append(f"pack {digest} has no registry event")
        return errors
