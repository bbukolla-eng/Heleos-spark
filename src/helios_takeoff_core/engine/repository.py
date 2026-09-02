"""Transactional persistence for immutable HELIOS P1B domain-pack definitions."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Mapping

from ..db import Database
from ..errors import ConflictError, NotFoundError, ValidationError
from .compiler import compile_domain_pack
from .contracts import CompiledDomainPack


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class EngineRepository:
    """Import and query append-only reusable engine definitions."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def import_domain_pack(self, pack: CompiledDomainPack) -> str:
        """Atomically persist one compiled pack, or return its exact replay identity."""
        document, canonical_json, canonical_sha256 = _validated_canonical_document(pack)
        pack_code = str(document["pack_code"])
        version = str(document["version"])

        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT id, sha256, canonical_json
                FROM engine_domain_packs
                WHERE pack_code = ? AND version = ?
                """,
                (pack_code, version),
            ).fetchone()
            if existing is not None:
                if (
                    existing["sha256"] == canonical_sha256
                    and existing["canonical_json"] == canonical_json
                ):
                    return str(existing["id"])
                raise ConflictError(
                    "domain pack code and version already identify different immutable content"
                )

            digest_owner = connection.execute(
                "SELECT id FROM engine_domain_packs WHERE sha256 = ?",
                (canonical_sha256,),
            ).fetchone()
            if digest_owner is not None:
                raise ConflictError("domain pack digest already identifies different immutable content")

            pack_id = _new_id()
            for item in document["catalog"]:  # type: ignore[union-attr]
                connection.execute(
                    """
                    INSERT INTO engine_catalog_items(pack_id, code, type, title, uom)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (pack_id, item["code"], item["type"], item["title"], item["uom"]),
                )

            for provenance in document["provenance"]:  # type: ignore[union-attr]
                connection.execute(
                    """
                    INSERT INTO engine_pack_provenance(pack_id, kind, reference)
                    VALUES (?, ?, ?)
                    """,
                    (pack_id, provenance["kind"], provenance["reference"]),
                )

            for relation in document["relations"]:  # type: ignore[union-attr]
                connection.execute(
                    """
                    INSERT INTO engine_catalog_relations(pack_id, from_code, relation, to_code)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        pack_id,
                        relation["from_code"],
                        relation["relation"],
                        relation["to_code"],
                    ),
                )

            for rule in document["rules"]:  # type: ignore[union-attr]
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
                        _compact_json(rule["required_observations"]),
                        _compact_json(rule["required_evidence"]),
                        rule["output_claim_type"],
                        rule.get("output_uom"),
                    ),
                )

            connection.execute(
                """
                INSERT INTO engine_domain_packs(
                    id, protocol, pack_code, version, title, jurisdiction,
                    sha256, canonical_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pack_id,
                    document["protocol"],
                    pack_code,
                    version,
                    document["title"],
                    document["jurisdiction"],
                    canonical_sha256,
                    canonical_json,
                    _now(),
                ),
            )

        return pack_id

    def get_domain_pack(self, *, pack_code: str, version: str) -> dict[str, Any]:
        """Return the exact canonical document for one immutable pack identity."""
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT canonical_json FROM engine_domain_packs
                WHERE pack_code = ? AND version = ?
                """,
                (pack_code, version),
            ).fetchone()
        if row is None:
            raise NotFoundError("domain pack was not found")
        return json.loads(row["canonical_json"])

    def get_catalog_item(
        self, *, pack_code: str, version: str, item_code: str
    ) -> dict[str, Any]:
        """Return one catalog definition in canonical field form."""
        with self.database.connection() as connection:
            pack_id = self._pack_id(connection, pack_code=pack_code, version=version)
            row = connection.execute(
                """
                SELECT code, type, title, uom
                FROM engine_catalog_items
                WHERE pack_id = ? AND code = ?
                """,
                (pack_id, item_code),
            ).fetchone()
        if row is None:
            raise NotFoundError("catalog item was not found")
        return {
            "code": row["code"],
            "type": row["type"],
            "title": row["title"],
            "uom": row["uom"],
        }

    def list_catalog_relations(
        self,
        *,
        pack_code: str,
        version: str,
        item_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """List canonical typed relations, optionally touching one catalog item."""
        with self.database.connection() as connection:
            pack_id = self._pack_id(connection, pack_code=pack_code, version=version)
            if item_code is not None:
                item = connection.execute(
                    """
                    SELECT 1 FROM engine_catalog_items
                    WHERE pack_id = ? AND code = ?
                    """,
                    (pack_id, item_code),
                ).fetchone()
                if item is None:
                    raise NotFoundError("catalog item was not found")
                rows = connection.execute(
                    """
                    SELECT from_code, relation, to_code
                    FROM engine_catalog_relations
                    WHERE pack_id = ? AND (from_code = ? OR to_code = ?)
                    ORDER BY from_code, relation, to_code
                    """,
                    (pack_id, item_code, item_code),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT from_code, relation, to_code
                    FROM engine_catalog_relations
                    WHERE pack_id = ?
                    ORDER BY from_code, relation, to_code
                    """,
                    (pack_id,),
                ).fetchall()
        return [
            {
                "from_code": row["from_code"],
                "relation": row["relation"],
                "to_code": row["to_code"],
            }
            for row in rows
        ]

    def get_rule_definition(
        self, *, pack_code: str, version: str, rule_code: str
    ) -> dict[str, Any]:
        """Return one declarative rule in canonical field form."""
        with self.database.connection() as connection:
            pack_id = self._pack_id(connection, pack_code=pack_code, version=version)
            row = connection.execute(
                """
                SELECT code, type, subject_code,
                       required_observations_json, required_evidence_json,
                       output_claim_type, output_uom
                FROM engine_rule_definitions
                WHERE pack_id = ? AND code = ?
                """,
                (pack_id, rule_code),
            ).fetchone()
        if row is None:
            raise NotFoundError("rule definition was not found")
        result: dict[str, Any] = {
            "code": row["code"],
            "type": row["type"],
            "subject_code": row["subject_code"],
            "required_observations": json.loads(row["required_observations_json"]),
            "required_evidence": json.loads(row["required_evidence_json"]),
            "output_claim_type": row["output_claim_type"],
        }
        if row["output_uom"] is not None:
            result["output_uom"] = row["output_uom"]
        return result

    @staticmethod
    def _pack_id(
        connection: Any, *, pack_code: str, version: str
    ) -> str:
        row = connection.execute(
            "SELECT id FROM engine_domain_packs WHERE pack_code = ? AND version = ?",
            (pack_code, version),
        ).fetchone()
        if row is None:
            raise NotFoundError("domain pack was not found")
        return str(row["id"])


def _compact_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validated_canonical_document(
    pack: CompiledDomainPack,
) -> tuple[dict[str, Any], str, str]:
    """Recompile and prove all supplied projections share one strict byte identity."""
    if not isinstance(pack.canonical_bytes, bytes):
        raise ValidationError("domain pack canonical bytes must be bytes")
    actual_digest = hashlib.sha256(pack.canonical_bytes).hexdigest()
    if not isinstance(pack.sha256, str) or pack.sha256 != actual_digest:
        raise ValidationError("domain pack digest does not match canonical bytes")

    try:
        canonical_json = pack.canonical_bytes.decode("utf-8")
        parsed = json.loads(canonical_json, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValidationError("domain pack canonical bytes must contain valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValidationError("domain pack canonical document must be an object")

    recompiled = compile_domain_pack(parsed)
    if recompiled.canonical_bytes != pack.canonical_bytes:
        raise ValidationError("domain pack canonical bytes do not match strict compiler output")
    if recompiled.sha256 != actual_digest:
        raise ValidationError("domain pack digest does not match strict compiler output")

    try:
        declared_document = _materialize_json(pack.canonical_document)
    except (TypeError, ValueError) as error:
        raise ValidationError("domain pack canonical document is not valid JSON") from error
    recompiled_document = _materialize_json(recompiled.canonical_document)
    if declared_document != recompiled_document:
        raise ValidationError("domain pack canonical document does not match canonical bytes")
    if not isinstance(recompiled_document, dict):  # pragma: no cover - compiler invariant
        raise ValidationError("strict compiler did not return an object")
    return recompiled_document, recompiled.canonical_bytes.decode("utf-8"), recompiled.sha256


def _materialize_json(value: object) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: _materialize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_materialize_json(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError("value is not JSON-compatible")


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"unsupported JSON constant: {value}")
