"""Immutable sources and candidate rules for the mechanical knowledge lane.

This store is intentionally separate from the Foundation operational database.
It records source and rule bytes; lifecycle decisions live in append-only tables.
Nothing in this module approves a rule or computes a takeoff quantity.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_TEXT = 10_000
MAX_ITEMS = 2_000
MAX_DEPTH = 20
SOURCE_STATES = ("current", "superseded", "retired")
REQUIRED_METADATA = (
    "source_class", "title", "rights_basis", "jurisdiction", "edition",
    "retrieved_at", "applicability", "locator", "snapshot_kind",
)
OPTIONAL_METADATA = ("project_id", "revision_id", "source_artifact_sha256")
UNKNOWN_ALLOWED_METADATA = ("jurisdiction", "edition", "applicability")
REQUIRED_RULE = (
    "taxonomy_version", "statement", "categories", "citations",
    "applicability", "qualifiers",
)
OPTIONAL_RULE = (
    "term_ids", "source_requirement_id", "project_id", "conditions",
    "predecessor_id", "promotion_evidence",
)
PROMOTION_FIELDS = ("status", "permission", "permissions", "approved", "active")


class KnowledgeError(ValueError):
    """A stable, caller-readable knowledge-store failure."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _text(value, field, code, allow_unspecified=False):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise KnowledgeError(code, "%s must be a nonempty bounded string." % field)
    value = value.strip()
    if value == "unspecified" and not allow_unspecified:
        raise KnowledgeError(code, "%s must be supplied explicitly." % field)
    return value


def _validate_json(value, code, field, depth=0, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_ITEMS or depth > MAX_DEPTH:
        raise KnowledgeError(code, "%s exceeds the structured-data limit." % field)
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise KnowledgeError(code, "%s contains a non-finite number." % field)
        return
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise KnowledgeError(code, "%s contains an oversized string." % field)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_ITEMS:
            raise KnowledgeError(code, "%s contains too many items." % field)
        for item in value:
            _validate_json(item, code, field, depth + 1, count)
        return
    if isinstance(value, dict):
        if len(value) > MAX_ITEMS:
            raise KnowledgeError(code, "%s contains too many fields." % field)
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise KnowledgeError(code, "%s contains an invalid field name." % field)
            _validate_json(item, code, field, depth + 1, count)
        return
    raise KnowledgeError(code, "%s must contain only JSON values." % field)


def _canonical(value, code, field):
    _validate_json(value, code, field)
    try:
        packed = json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise KnowledgeError(code, "%s is not valid JSON: %s" % (field, error))
    if len(packed) > MAX_JSON_BYTES:
        raise KnowledgeError(code, "%s exceeds the saved-record limit." % field)
    return packed, json.loads(packed.decode("utf-8"))


def _identifier(kind, payload):
    return kind + "_" + _digest(("heleos-mechanical-%s-v1\0" % kind).encode("ascii") + payload)


class KnowledgeStore:
    """Per-call SQLite access to immutable knowledge records."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise KnowledgeError("invalid_root", "Knowledge root must be a directory.")
        self.database = self.root / "knowledge.sqlite3"
        connection = self._connect()
        try:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_schema (
                    version INTEGER PRIMARY KEY
                );
                INSERT OR IGNORE INTO knowledge_schema(version) VALUES (1);

                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL,
                    snapshot BLOB NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL REFERENCES sources(id),
                    event TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('current','superseded','retired')),
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    replacement_id TEXT REFERENCES sources(id),
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS source_history_lookup
                    ON source_history(source_id, sequence);

                CREATE TABLE IF NOT EXISTS rules (
                    id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rule_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id TEXT NOT NULL REFERENCES rules(id),
                    event TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status = 'candidate'),
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS rule_history_lookup
                    ON rule_history(rule_id, sequence);
            """)
            versions = connection.execute(
                "SELECT version FROM knowledge_schema ORDER BY version").fetchall()
            if [row[0] for row in versions] != [SCHEMA_VERSION]:
                raise KnowledgeError("schema_error", "Unsupported knowledge schema version.")
            connection.commit()
        finally:
            connection.close()

    def _connect(self):
        connection = sqlite3.connect(str(self.database), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @staticmethod
    def _transaction(connection, operation):
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = operation()
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _normalize_metadata(metadata):
        if not isinstance(metadata, dict):
            raise KnowledgeError("invalid_metadata", "Source metadata must be a dictionary.")
        unknown = sorted(set(metadata) - set(REQUIRED_METADATA) - set(OPTIONAL_METADATA))
        if unknown:
            raise KnowledgeError("invalid_metadata", "Unsupported source metadata: %s." % ", ".join(unknown))
        normalized = {}
        for field in REQUIRED_METADATA:
            if field not in metadata:
                raise KnowledgeError("invalid_metadata", "Missing source metadata: %s." % field)
            normalized[field] = _text(
                metadata[field], field, "invalid_metadata",
                allow_unspecified=field in UNKNOWN_ALLOWED_METADATA)
        for field in OPTIONAL_METADATA:
            normalized[field] = _text(metadata.get(field, "unspecified"), field,
                                      "invalid_metadata", allow_unspecified=True)
        return normalized

    @staticmethod
    def _source_row(connection, source_id):
        row = connection.execute(
            "SELECT id, metadata_json, snapshot_sha256, snapshot FROM sources WHERE id = ?",
            (source_id,)).fetchone()
        if row is None:
            raise KnowledgeError("source_not_found", "Knowledge source was not found.")
        try:
            metadata = json.loads(row["metadata_json"])
            metadata_bytes, normalized = _canonical(metadata, "integrity_error", "stored source metadata")
        except (json.JSONDecodeError, UnicodeError) as error:
            raise KnowledgeError("integrity_error", "Stored source metadata is invalid: %s" % error)
        if metadata_bytes.decode("utf-8") != row["metadata_json"]:
            raise KnowledgeError("integrity_error", "Stored source metadata is not canonical.")
        snapshot = bytes(row["snapshot"])
        if _digest(snapshot) != row["snapshot_sha256"]:
            raise KnowledgeError("integrity_error", "Stored source snapshot hash does not match its bytes.")
        expected = _identifier("source", metadata_bytes + b"\0" + snapshot)
        if expected != row["id"]:
            raise KnowledgeError("integrity_error", "Stored source identity does not match its content.")
        return row, normalized, snapshot

    @staticmethod
    def _latest_source_decision(connection, source_id):
        row = connection.execute("""
            SELECT sequence, event, state, actor, reason, replacement_id, recorded_at
            FROM source_history WHERE source_id = ? ORDER BY sequence DESC LIMIT 1
        """, (source_id,)).fetchone()
        if row is None:
            raise KnowledgeError("integrity_error", "Source has no lifecycle record.")
        return row

    @classmethod
    def _source_result(cls, connection, source_id):
        row, metadata, unused = cls._source_row(connection, source_id)
        decision = cls._latest_source_decision(connection, source_id)
        return {"id": row["id"], "metadata": metadata,
                "snapshot_sha256": row["snapshot_sha256"], "state": decision["state"]}

    def add_source(self, metadata, snapshot):
        normalized = self._normalize_metadata(metadata)
        metadata_bytes, normalized = _canonical(normalized, "invalid_metadata", "source metadata")
        if not isinstance(snapshot, bytes) or not snapshot or len(snapshot) > MAX_SNAPSHOT_BYTES:
            raise KnowledgeError("invalid_snapshot", "Snapshot must be nonempty bytes within the size limit.")
        snapshot_sha256 = _digest(snapshot)
        source_id = _identifier("source", metadata_bytes + b"\0" + snapshot)
        connection = self._connect()
        try:
            def write():
                existing = connection.execute(
                    "SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
                if existing is None:
                    timestamp = _now()
                    connection.execute("""
                        INSERT INTO sources(id, metadata_json, snapshot_sha256, snapshot, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (source_id, metadata_bytes.decode("utf-8"), snapshot_sha256,
                          sqlite3.Binary(snapshot), timestamp))
                    connection.execute("""
                        INSERT INTO source_history(source_id, event, state, actor, reason,
                                                   replacement_id, recorded_at)
                        VALUES (?, 'registered', 'current', 'knowledge_store',
                                'Immutable source registered.', NULL, ?)
                    """, (source_id, timestamp))
                return self._source_result(connection, source_id)
            return self._transaction(connection, write)
        finally:
            connection.close()

    def get_source(self, source_id):
        connection = self._connect()
        try:
            return self._source_result(connection, source_id)
        finally:
            connection.close()

    def source_bytes(self, source_id):
        connection = self._connect()
        try:
            unused_row, unused_metadata, snapshot = self._source_row(connection, source_id)
            return snapshot
        finally:
            connection.close()

    def set_source_state(self, source_id, state, actor, reason, replacement_id=None):
        if state not in SOURCE_STATES:
            raise KnowledgeError("invalid_state", "Source state must be current, superseded, or retired.")
        actor = _text(actor, "actor", "invalid_decision")
        reason = _text(reason, "reason", "invalid_decision")
        if state == "superseded" and not replacement_id:
            raise KnowledgeError("invalid_replacement", "A superseded source requires a replacement.")
        if state != "superseded" and replacement_id is not None:
            raise KnowledgeError("invalid_replacement", "Only a superseded source may name a replacement.")
        connection = self._connect()
        try:
            def write():
                unused, source_metadata, unused_bytes = self._source_row(connection, source_id)
                if state == "superseded":
                    if replacement_id == source_id:
                        raise KnowledgeError("replacement_cycle", "A source cannot replace itself.")
                    unused, replacement_metadata, unused_bytes = self._source_row(
                        connection, replacement_id)
                    if source_metadata["project_id"] != replacement_metadata["project_id"]:
                        raise KnowledgeError("project_mismatch", "Replacement source belongs to another project.")
                    visited = {source_id}
                    cursor = replacement_id
                    while cursor is not None:
                        if cursor in visited:
                            raise KnowledgeError("replacement_cycle", "Source replacement would create a cycle.")
                        visited.add(cursor)
                        self._source_row(connection, cursor)
                        decision = self._latest_source_decision(connection, cursor)
                        cursor = decision["replacement_id"] if decision["state"] == "superseded" else None
                connection.execute("""
                    INSERT INTO source_history(source_id, event, state, actor, reason,
                                               replacement_id, recorded_at)
                    VALUES (?, 'state_changed', ?, ?, ?, ?, ?)
                """, (source_id, state, actor, reason, replacement_id, _now()))
                return self._source_result(connection, source_id)
            return self._transaction(connection, write)
        finally:
            connection.close()

    @staticmethod
    def _normalize_rule(payload):
        if not isinstance(payload, dict):
            raise KnowledgeError("invalid_payload", "Rule payload must be a dictionary.")
        if any(field in payload for field in PROMOTION_FIELDS):
            raise KnowledgeError(
                "promotion_forbidden",
                "Candidate rules cannot supply approval, active status, or permission.")
        unknown = sorted(set(payload) - set(REQUIRED_RULE) - set(OPTIONAL_RULE))
        if unknown:
            raise KnowledgeError("invalid_payload", "Unsupported rule fields: %s." % ", ".join(unknown))
        for field in REQUIRED_RULE:
            if field not in payload:
                raise KnowledgeError("invalid_payload", "Missing rule field: %s." % field)
        packed, normalized = _canonical(payload, "invalid_payload", "rule payload")
        normalized["taxonomy_version"] = _text(
            normalized["taxonomy_version"], "taxonomy_version", "invalid_payload")
        # Validate statement content without normalizing it: exact source wording,
        # including leading and trailing whitespace, is part of the rule identity.
        _text(normalized["statement"], "statement", "invalid_payload")
        for field in ("categories", "qualifiers"):
            values = normalized[field]
            if not isinstance(values, list):
                raise KnowledgeError("invalid_payload", "%s must be a list." % field)
            if field == "categories" and not values:
                raise KnowledgeError("invalid_payload", "categories must not be empty.")
            normalized[field] = [_text(value, field, "invalid_payload") for value in values]
        if not isinstance(normalized["applicability"], dict):
            raise KnowledgeError("invalid_payload", "applicability must be a dictionary.")
        citations = normalized["citations"]
        if not isinstance(citations, list) or not citations:
            raise KnowledgeError("invalid_payload", "citations must be a nonempty list.")
        for citation in citations:
            if not isinstance(citation, dict) or set(citation) != {"source_id", "locator"}:
                raise KnowledgeError("invalid_payload", "Each citation requires only source_id and locator.")
            citation["source_id"] = _text(citation["source_id"], "citation source_id", "invalid_payload")
            citation["locator"] = _text(citation["locator"], "citation locator", "invalid_payload")
        if "term_ids" in normalized:
            if not isinstance(normalized["term_ids"], list):
                raise KnowledgeError("invalid_payload", "term_ids must be a list.")
            normalized["term_ids"] = [
                _text(value, "term_id", "invalid_payload") for value in normalized["term_ids"]]
        for field in ("source_requirement_id", "project_id", "predecessor_id"):
            if field in normalized:
                normalized[field] = _text(normalized[field], field, "invalid_payload")
        applicability_project = normalized["applicability"].get("project_id")
        if applicability_project is not None:
            applicability_project = _text(
                applicability_project, "applicability.project_id", "invalid_payload")
            normalized["applicability"]["project_id"] = applicability_project
        payload_project = normalized.get("project_id")
        if (payload_project is not None and applicability_project is not None and
                payload_project != applicability_project):
            raise KnowledgeError(
                "project_mismatch",
                "Rule project_id must match applicability.project_id.")
        if "promotion_evidence" in normalized and not isinstance(normalized["promotion_evidence"], dict):
            raise KnowledgeError("invalid_payload", "promotion_evidence must be a dictionary.")
        unused, normalized = _canonical(normalized, "invalid_payload", "rule payload")
        return normalized

    @staticmethod
    def _rule_project(payload):
        return payload.get("project_id") or payload["applicability"].get("project_id")

    @classmethod
    def _rule_result(cls, connection, rule_id):
        row = connection.execute(
            "SELECT id, payload_json FROM rules WHERE id = ?", (rule_id,)).fetchone()
        if row is None:
            raise KnowledgeError("rule_not_found", "Candidate rule was not found.")
        try:
            payload = json.loads(row["payload_json"])
            payload_bytes, payload = _canonical(payload, "integrity_error", "stored rule payload")
        except (json.JSONDecodeError, UnicodeError) as error:
            raise KnowledgeError("integrity_error", "Stored rule payload is invalid: %s" % error)
        if payload_bytes.decode("utf-8") != row["payload_json"]:
            raise KnowledgeError("integrity_error", "Stored rule payload is not canonical.")
        if _identifier("rule", payload_bytes) != row["id"]:
            raise KnowledgeError("integrity_error", "Stored rule identity does not match its payload.")
        issues = set()
        for citation in payload["citations"]:
            cls._source_row(connection, citation["source_id"])
            decision = cls._latest_source_decision(connection, citation["source_id"])
            if decision["state"] != "current":
                issues.add("cited_source_not_current:" + citation["source_id"])
        return {"id": row["id"], "payload": payload, "status": "candidate",
                "issues": sorted(issues)}

    def add_rule(self, payload):
        normalized = self._normalize_rule(payload)
        payload_bytes, normalized = _canonical(normalized, "invalid_payload", "rule payload")
        rule_id = _identifier("rule", payload_bytes)
        connection = self._connect()
        try:
            def write():
                rule_project = self._rule_project(normalized)
                for citation in normalized["citations"]:
                    unused, source_metadata, unused_bytes = self._source_row(
                        connection, citation["source_id"])
                    source_project = source_metadata["project_id"]
                    if (source_project != "unspecified" and
                            source_project != rule_project):
                        raise KnowledgeError(
                            "project_mismatch",
                            "Project-specific citation does not belong to the rule project.")
                predecessor_id = normalized.get("predecessor_id")
                if predecessor_id is not None:
                    predecessor = self._rule_result(connection, predecessor_id)
                    if self._rule_project(predecessor["payload"]) != rule_project:
                        raise KnowledgeError(
                            "project_mismatch",
                            "Rule predecessor belongs to another project.")
                existing = connection.execute(
                    "SELECT id FROM rules WHERE id = ?", (rule_id,)).fetchone()
                if existing is None:
                    timestamp = _now()
                    connection.execute(
                        "INSERT INTO rules(id, payload_json, created_at) VALUES (?, ?, ?)",
                        (rule_id, payload_bytes.decode("utf-8"), timestamp))
                    connection.execute("""
                        INSERT INTO rule_history(rule_id, event, status, actor, reason, recorded_at)
                        VALUES (?, 'registered', 'candidate', 'knowledge_store',
                                'Immutable candidate rule registered.', ?)
                    """, (rule_id, timestamp))
                return self._rule_result(connection, rule_id)
            return self._transaction(connection, write)
        finally:
            connection.close()

    def get_rule(self, rule_id):
        connection = self._connect()
        try:
            return self._rule_result(connection, rule_id)
        finally:
            connection.close()

    def list_rules(self, project_id=None):
        if project_id is not None:
            project_id = _text(project_id, "project_id", "invalid_project")
        connection = self._connect()
        try:
            result = []
            for row in connection.execute("SELECT id FROM rules ORDER BY id"):
                rule = self._rule_result(connection, row["id"])
                if project_id is None or self._rule_project(rule["payload"]) == project_id:
                    result.append(rule)
            return result
        finally:
            connection.close()

    def history(self, kind, record_id):
        connection = self._connect()
        try:
            if kind == "source":
                self._source_row(connection, record_id)
                rows = connection.execute("""
                    SELECT sequence, event, state, actor, reason, replacement_id, recorded_at
                    FROM source_history WHERE source_id = ? ORDER BY sequence
                """, (record_id,)).fetchall()
            elif kind == "rule":
                self._rule_result(connection, record_id)
                rows = connection.execute("""
                    SELECT sequence, event, status, actor, reason, recorded_at
                    FROM rule_history WHERE rule_id = ? ORDER BY sequence
                """, (record_id,)).fetchall()
            else:
                raise KnowledgeError("invalid_kind", "History kind must be source or rule.")
            return [dict(row) for row in rows]
        finally:
            connection.close()
