"""Append-only project applicability decisions over candidate knowledge rules.

The records preserve a project's review history. They do not approve engineering
rules, execute rule statements, validate compiled runtime behavior, or calculate
quantities.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3


VERSION = "mechanical-rule-decision-store-1"
SCHEMA_VERSION = 1
SCHEMA_TABLE = "mechanical_rule_decision_schema"
DECISION_TABLE = "mechanical_rule_decisions"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_TEXT = 10_000
MAX_ITEMS = 2_000
MAX_DEPTH = 20
STATES = ("current", "superseded", "retired")
DISPOSITIONS = ("applies", "excludes", "defer", "withdraw")
FIELDS = frozenset({
    "kind", "project_id", "document_run_id", "requirement_id",
    "source_rule_id", "compiled_rule_id", "object_id", "binding_sha256",
    "disposition", "actor", "reason", "citations", "source_pins",
    "supersedes",
})
FORBIDDEN = frozenset({
    "approval", "approved", "active", "quantity", "promotion",
    "promotion_evidence", "permission", "permissions", "status",
})
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class DecisionError(ValueError):
    """Stable failure returned by the decision store."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _now():
    return datetime.now(timezone.utc).isoformat()


def _validate_json(value, depth=0, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_ITEMS or depth > MAX_DEPTH:
        raise DecisionError("invalid_payload", "Decision payload exceeds the structured-data limit.")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise DecisionError("invalid_payload", "Decision payload contains a non-finite number.")
        return
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise DecisionError("invalid_payload", "Decision payload contains an oversized string.")
        return
    if isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise DecisionError("invalid_payload", "Decision payload contains too many items.")
        for item in value:
            _validate_json(item, depth + 1, count)
        return
    if isinstance(value, dict):
        if len(value) > MAX_ITEMS:
            raise DecisionError("invalid_payload", "Decision payload contains too many fields.")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise DecisionError("invalid_payload", "Decision payload has an invalid field name.")
            _validate_json(item, depth + 1, count)
        return
    raise DecisionError("invalid_payload", "Decision payload must contain only JSON values.")


def _canonical(value):
    _validate_json(value)
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise DecisionError("invalid_payload", "Decision payload is not valid JSON: %s" % error)
    if len(data) > MAX_JSON_BYTES:
        raise DecisionError("invalid_payload", "Decision payload exceeds the saved-record limit.")
    return data, json.loads(data.decode("utf-8"))


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise DecisionError("invalid_payload", "%s must be a nonempty bounded string." % field)
    return value


def _id_text(value, field):
    value = _text(value, field)
    if value != value.strip():
        raise DecisionError("invalid_payload", "%s must use an exact identifier." % field)
    return value


def _decision_id(payload_bytes):
    digest = hashlib.sha256(b"heleos-project-applicability-decision-v1\0" + payload_bytes).hexdigest()
    return "decision_" + digest


class DecisionStore:
    """Store immutable decisions in the KnowledgeStore SQLite database."""

    def __init__(self, knowledge_store):
        if (knowledge_store is None or
                not isinstance(getattr(knowledge_store, "database", None), Path)):
            raise DecisionError("invalid_store", "DecisionStore requires a KnowledgeStore instance.")
        self.knowledge = knowledge_store
        self.database = knowledge_store.database
        connection = self._connect()
        try:
            self._transaction(connection, lambda: self._initialize(connection))
        finally:
            connection.close()

    def _connect(self):
        connection = sqlite3.connect(str(self.database), timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA journal_mode = WAL")
            return connection
        except Exception:
            connection.close()
            raise

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
    def _read_transaction(connection, operation):
        try:
            connection.execute("BEGIN")
            result = operation()
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _initialize(connection):
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        has_schema = SCHEMA_TABLE in tables
        has_decisions = DECISION_TABLE in tables
        if has_schema:
            versions = [row[0] for row in connection.execute(
                "SELECT version FROM " + SCHEMA_TABLE + " ORDER BY version")]
            if versions != [SCHEMA_VERSION] or not has_decisions:
                raise DecisionError("schema_version", "Unsupported decision-store schema version.")
            return
        if has_decisions:
            raise DecisionError("schema_version", "Decision table has no version authority.")
        connection.execute("""
            CREATE TABLE mechanical_rule_decision_schema (
                version INTEGER PRIMARY KEY
            )
        """)
        connection.execute(
            "INSERT INTO mechanical_rule_decision_schema(version) VALUES (?)",
            (SCHEMA_VERSION,))
        connection.execute("""
            CREATE TABLE mechanical_rule_decisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                project_id TEXT NOT NULL,
                source_rule_id TEXT NOT NULL REFERENCES rules(id),
                object_id TEXT NOT NULL,
                supersedes TEXT REFERENCES mechanical_rule_decisions(id),
                recorded_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE INDEX mechanical_rule_decisions_project
            ON mechanical_rule_decisions(project_id, sequence)
        """)
        connection.execute("""
            CREATE INDEX mechanical_rule_decisions_target
            ON mechanical_rule_decisions(
                project_id, source_rule_id, object_id, sequence)
        """)

    @staticmethod
    def _normalize(payload):
        if not isinstance(payload, dict):
            raise DecisionError("invalid_payload", "Decision payload must be a dictionary.")
        forbidden = sorted(set(payload) & FORBIDDEN)
        if forbidden:
            raise DecisionError(
                "authority_forbidden",
                "Applicability decisions cannot contain approval, promotion, or quantity fields.")
        if set(payload) != FIELDS:
            missing = sorted(FIELDS - set(payload))
            unknown = sorted(set(payload) - FIELDS)
            detail = "missing %s" % ", ".join(missing) if missing else "unknown %s" % ", ".join(unknown)
            raise DecisionError("invalid_payload", "Decision payload fields are invalid: %s." % detail)
        unused, normalized = _canonical(payload)
        if normalized["kind"] != "project_applicability":
            raise DecisionError("invalid_payload", "Decision kind must be project_applicability.")
        for field in ("project_id", "document_run_id", "requirement_id",
                      "source_rule_id", "compiled_rule_id", "object_id"):
            _id_text(normalized[field], field)
        if not isinstance(normalized["binding_sha256"], str) or not HEX64.fullmatch(
                normalized["binding_sha256"]):
            raise DecisionError("invalid_payload", "binding_sha256 must be a lowercase SHA-256.")
        if normalized["disposition"] not in DISPOSITIONS:
            raise DecisionError("invalid_payload", "Decision disposition is not supported.")
        _text(normalized["actor"], "actor")
        _text(normalized["reason"], "reason")
        supersedes = normalized["supersedes"]
        if supersedes is not None:
            _id_text(supersedes, "supersedes")
        if normalized["disposition"] == "withdraw" and supersedes is None:
            raise DecisionError("supersedes_required", "A withdrawal must supersede a current decision.")

        citations = normalized["citations"]
        if not isinstance(citations, list):
            raise DecisionError("invalid_payload", "citations must be a list.")
        if normalized["disposition"] in ("applies", "excludes") and not citations:
            raise DecisionError("citation_required", "Applies and excludes decisions require evidence.")
        for citation in citations:
            if not isinstance(citation, dict) or set(citation) != {"source_id", "locator"}:
                raise DecisionError("invalid_payload", "Each citation requires only source_id and locator.")
            _id_text(citation["source_id"], "citation source_id")
            _text(citation["locator"], "citation locator")

        pins = normalized["source_pins"]
        if not isinstance(pins, list):
            raise DecisionError("invalid_payload", "source_pins must be a list.")
        pin_ids = []
        for pin in pins:
            expected = {"source_id", "snapshot_sha256", "lifecycle_sequence", "state"}
            if not isinstance(pin, dict) or set(pin) != expected:
                raise DecisionError("invalid_payload", "Each source pin has invalid fields.")
            pin_ids.append(_id_text(pin["source_id"], "pin source_id"))
            if not isinstance(pin["snapshot_sha256"], str) or not HEX64.fullmatch(
                    pin["snapshot_sha256"]):
                raise DecisionError("invalid_payload", "Pin snapshot_sha256 must be a lowercase SHA-256.")
            sequence = pin["lifecycle_sequence"]
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                raise DecisionError("invalid_payload", "Pin lifecycle_sequence must be positive.")
            if pin["state"] not in STATES:
                raise DecisionError("invalid_payload", "Pin source state is invalid.")
        if len(pin_ids) != len(set(pin_ids)):
            raise DecisionError("invalid_payload", "Source pins must not contain duplicates.")
        if pin_ids != sorted(pin_ids):
            raise DecisionError("invalid_payload", "Source pins must use canonical source order.")
        payload_bytes, normalized = _canonical(normalized)
        return payload_bytes, normalized

    @staticmethod
    def _raise_knowledge(error):
        code = getattr(error, "code", "knowledge_error")
        message = getattr(error, "message", str(error))
        raise DecisionError(code, message) from None

    def _source(self, connection, source_id):
        try:
            row, metadata, snapshot = self.knowledge._source_row(connection, source_id)
            lifecycle = self.knowledge._latest_source_decision(connection, source_id)
            return row, metadata, snapshot, lifecycle
        except DecisionError:
            raise
        except Exception as error:
            self._raise_knowledge(error)

    def _rule(self, connection, rule_id):
        try:
            return self.knowledge._rule_result(connection, rule_id)
        except DecisionError:
            raise
        except Exception as error:
            self._raise_knowledge(error)

    @staticmethod
    def _rule_project(rule):
        payload = rule["payload"]
        return payload.get("project_id") or payload.get("applicability", {}).get("project_id")

    def source_pins(self, source_ids):
        if not isinstance(source_ids, list):
            raise DecisionError("invalid_source_ids", "source_ids must be a list.")
        for source_id in source_ids:
            _id_text(source_id, "source_id")
        if len(source_ids) != len(set(source_ids)):
            raise DecisionError("invalid_source_ids", "source_ids must not contain duplicates.")
        connection = self._connect()
        try:
            def read():
                result = []
                for source_id in sorted(source_ids):
                    row, unused_metadata, unused_snapshot, lifecycle = self._source(
                        connection, source_id)
                    result.append({
                        "source_id": source_id,
                        "snapshot_sha256": row["snapshot_sha256"],
                        "lifecycle_sequence": lifecycle["sequence"],
                        "state": lifecycle["state"],
                    })
                return result
            return self._read_transaction(connection, read)
        finally:
            connection.close()

    @staticmethod
    def _target(payload):
        return payload["project_id"], payload["source_rule_id"], payload["object_id"]

    def _dependencies(self, connection, payload, admission):
        rule = self._rule(connection, payload["source_rule_id"])
        if self._rule_project(rule) != payload["project_id"]:
            code = "project_mismatch" if admission else "integrity_error"
            raise DecisionError(code, "Source rule belongs to another project.")
        if rule["payload"].get("source_requirement_id") != payload["requirement_id"]:
            code = "requirement_mismatch" if admission else "integrity_error"
            raise DecisionError(code, "Source rule belongs to another requirement.")

        pins = {pin["source_id"]: pin for pin in payload["source_pins"]}
        required = {citation["source_id"] for citation in rule["payload"]["citations"]}
        required.update(citation["source_id"] for citation in payload["citations"])
        missing = sorted(required - set(pins))
        if missing:
            code = "source_pin_missing" if admission else "integrity_error"
            raise DecisionError(code, "Source pins do not cover every citation.")

        issues = set()
        for source_id, pin in pins.items():
            row, metadata, unused_snapshot, lifecycle = self._source(connection, source_id)
            source_project = metadata.get("project_id", "unspecified")
            if source_project not in ("unspecified", payload["project_id"]):
                code = "project_mismatch" if admission else "integrity_error"
                raise DecisionError(code, "Pinned source belongs to another project.")
            changed = (pin["snapshot_sha256"] != row["snapshot_sha256"] or
                       pin["lifecycle_sequence"] != lifecycle["sequence"] or
                       pin["state"] != lifecycle["state"])
            if changed:
                if admission:
                    raise DecisionError("source_pin_mismatch", "Source pin no longer matches lifecycle state.")
                issues.add("source_pin_changed:" + source_id)
            if lifecycle["state"] != "current":
                issues.add("source_not_current:" + source_id)
                if admission and payload["disposition"] in ("applies", "excludes"):
                    raise DecisionError("source_not_current", "Current source evidence is required.")
        return sorted(issues)

    def _raw_row(self, connection, decision_id):
        row = connection.execute("""
            SELECT sequence, id, payload_json, project_id, source_rule_id,
                   object_id, supersedes, recorded_at
            FROM mechanical_rule_decisions WHERE id = ?
        """, (decision_id,)).fetchone()
        if row is None:
            raise DecisionError("decision_not_found", "Applicability decision was not found.")
        try:
            parsed = json.loads(row["payload_json"])
            payload_bytes, payload = self._normalize(parsed)
        except (json.JSONDecodeError, UnicodeError, DecisionError) as error:
            raise DecisionError("integrity_error", "Stored decision payload is invalid: %s" % error) from None
        if payload_bytes.decode("utf-8") != row["payload_json"]:
            raise DecisionError("integrity_error", "Stored decision payload is not canonical.")
        if _decision_id(payload_bytes) != row["id"]:
            raise DecisionError("integrity_error", "Stored decision identity does not match its payload.")
        if (row["project_id"] != payload["project_id"] or
                row["source_rule_id"] != payload["source_rule_id"] or
                row["object_id"] != payload["object_id"] or
                row["supersedes"] != payload["supersedes"]):
            raise DecisionError("integrity_error", "Stored decision index does not match its payload.")
        if (isinstance(row["sequence"], bool) or row["sequence"] < 1 or
                not isinstance(row["recorded_at"], str) or not row["recorded_at"]):
            raise DecisionError("integrity_error", "Stored decision metadata is invalid.")
        return row, payload

    def _verify_chain(self, connection, row, payload):
        seen = set()
        current_row, current_payload = row, payload
        while True:
            if current_row["id"] in seen:
                raise DecisionError("integrity_error", "Decision parent chain contains a cycle.")
            seen.add(current_row["id"])
            target = self._target(current_payload)
            previous = connection.execute("""
                SELECT id FROM mechanical_rule_decisions
                WHERE project_id = ? AND source_rule_id = ? AND object_id = ?
                  AND sequence < ? ORDER BY sequence DESC LIMIT 1
            """, target + (current_row["sequence"],)).fetchone()
            parent_id = current_payload["supersedes"]
            if parent_id is None:
                if previous is not None:
                    raise DecisionError("integrity_error", "Decision chain omitted its prior head.")
                return
            if previous is None or previous["id"] != parent_id:
                raise DecisionError("integrity_error", "Decision parent is not the prior target head.")
            parent_row, parent_payload = self._raw_row(connection, parent_id)
            if self._target(parent_payload) != target or parent_row["sequence"] >= current_row["sequence"]:
                raise DecisionError("integrity_error", "Decision parent belongs to another target.")
            current_row, current_payload = parent_row, parent_payload

    def _result(self, connection, decision_id):
        row, payload = self._raw_row(connection, decision_id)
        self._verify_chain(connection, row, payload)
        issues = self._dependencies(connection, payload, admission=False)
        return {"id": row["id"], "payload": payload, "sequence": row["sequence"],
                "recorded_at": row["recorded_at"], "issues": issues}

    def record(self, payload):
        payload_bytes, normalized = self._normalize(payload)
        decision_id = _decision_id(payload_bytes)
        connection = self._connect()
        try:
            def write():
                duplicate = connection.execute(
                    "SELECT id FROM mechanical_rule_decisions WHERE id = ?",
                    (decision_id,)).fetchone()
                if duplicate is not None:
                    return self._result(connection, decision_id)

                self._dependencies(connection, normalized, admission=True)
                target = self._target(normalized)
                latest = connection.execute("""
                    SELECT id FROM mechanical_rule_decisions
                    WHERE project_id = ? AND source_rule_id = ? AND object_id = ?
                    ORDER BY sequence DESC LIMIT 1
                """, target).fetchone()
                parent_id = normalized["supersedes"]
                if parent_id is not None:
                    parent_row, parent_payload = self._raw_row(connection, parent_id)
                    self._verify_chain(connection, parent_row, parent_payload)
                    if self._target(parent_payload) != target:
                        raise DecisionError("parent_mismatch", "Superseded decision belongs to another target.")
                    if latest is None or latest["id"] != parent_id:
                        raise DecisionError("stale_head", "Superseded decision is not the current target head.")
                elif latest is not None:
                    raise DecisionError("stale_head", "A current target decision must be superseded explicitly.")

                recorded_at = _now()
                connection.execute("""
                    INSERT INTO mechanical_rule_decisions(
                        id, payload_json, project_id, source_rule_id, object_id,
                        supersedes, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (decision_id, payload_bytes.decode("utf-8"), normalized["project_id"],
                      normalized["source_rule_id"], normalized["object_id"],
                      parent_id, recorded_at))
                return self._result(connection, decision_id)
            return self._transaction(connection, write)
        finally:
            connection.close()

    def get(self, decision_id):
        _id_text(decision_id, "decision_id")
        connection = self._connect()
        try:
            return self._read_transaction(
                connection, lambda: self._result(connection, decision_id))
        finally:
            connection.close()

    def list(self, project_id):
        _id_text(project_id, "project_id")
        connection = self._connect()
        try:
            def read():
                ids = [row["id"] for row in connection.execute("""
                    SELECT id FROM mechanical_rule_decisions
                    WHERE project_id = ? ORDER BY sequence
                """, (project_id,))]
                return [self._result(connection, decision_id) for decision_id in ids]
            return self._read_transaction(connection, read)
        finally:
            connection.close()
