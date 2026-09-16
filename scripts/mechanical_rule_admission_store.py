"""Immutable rule packages and append-only review/approval lifecycle events.

Admission applies only to requirement-applicability behavior. It grants no
quantity, application, model, account, or release authority.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3


VERSION = "mechanical-rule-admission-store-1"
SCHEMA_VERSION = 1
SCHEMA_TABLE = "mechanical_rule_admission_schema"
PACKAGE_TABLE = "mechanical_rule_packages"
EVENT_TABLE = "mechanical_rule_admission_events"
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_TEXT = 10_000
MAX_ITEMS = 100_000
MAX_DEPTH = 24
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
PACKAGE_FIELDS = frozenset({
    "project_id", "rule_id", "author", "reason", "scope", "source_pins",
    "implementation", "validation", "rollback",
})
SCOPE_FIELDS = frozenset({
    "purpose", "compiled_rule_id", "compiled_sha256", "selector", "effect",
    "conditions", "status",
})
IMPLEMENTATION_FIELDS = frozenset({
    "mechanical_rule_scope", "mechanical_rule_applicability",
    "mechanical_rule_validation",
})
REPORT_FIELDS = frozenset({
    "schema", "rule_id", "compiled_rule_id", "compiled_sha256",
    "implementation", "cases", "results", "passed", "coverage",
    "quantity_authority", "sha256",
})
EVENT_FIELDS = frozenset({
    "project_id", "rule_id", "version_id", "action", "actor", "reason",
    "supersedes",
})
FORBIDDEN = frozenset({
    "approval", "approved", "active", "quantity", "promotion",
    "promotion_evidence", "permission", "permissions",
})
PIN_STATES = ("current", "superseded", "retired")
SCOPE_STATES = ("supported", "needs_context", "unsupported")
EVENT_ACTIONS = ("review_pass", "review_reject", "approve", "withdraw")
RESULT_STATES = (
    "matched", "not_applicable", "needs_context", "source_blocked",
    "unsupported",
)


class RuleAdmissionError(ValueError):
    """Stable package or lifecycle validation failure."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json_value(value, depth=0, count=None):
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_ITEMS or depth > MAX_DEPTH:
        raise RuleAdmissionError("size_limit", "Payload exceeds the structured-data limit.")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise RuleAdmissionError("invalid_payload", "Payload contains a non-finite number.")
        return
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise RuleAdmissionError("size_limit", "Payload contains an oversized string.")
        return
    if isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise RuleAdmissionError("size_limit", "Payload contains too many items.")
        for item in value:
            _json_value(item, depth + 1, count)
        return
    if isinstance(value, dict):
        if len(value) > MAX_ITEMS:
            raise RuleAdmissionError("size_limit", "Payload contains too many fields.")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise RuleAdmissionError("invalid_payload", "Payload has an invalid field name.")
            _json_value(item, depth + 1, count)
        return
    raise RuleAdmissionError("invalid_payload", "Payload must contain only JSON values.")


def _canonical(value):
    _json_value(value)
    try:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RuleAdmissionError("invalid_payload", "Payload is not valid JSON: %s" % error)
    if len(data) > MAX_JSON_BYTES:
        raise RuleAdmissionError("size_limit", "Payload exceeds the saved-record limit.")
    return data, json.loads(data.decode("utf-8"))


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise RuleAdmissionError("invalid_payload", "%s must be a nonempty bounded string." % field)
    return value


def _identity(value, field):
    value = _text(value, field)
    if value != value.strip() or len(value) > 512:
        raise RuleAdmissionError("invalid_payload", "%s must be an exact bounded identifier." % field)
    return value


def _hash(value, field):
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise RuleAdmissionError("invalid_payload", "%s must be a lowercase SHA-256." % field)
    return value


def _content_id(prefix, domain, data):
    digest = hashlib.sha256(domain.encode("ascii") + b"\0" + data).hexdigest()
    return prefix + digest


def _actor_identity(value):
    return " ".join(value.split()).casefold()


class RuleAdmissionStore:
    """Persist immutable candidate packages and their explicit lifecycle events."""

    def __init__(self, knowledge_store):
        if (knowledge_store is None or
                not isinstance(getattr(knowledge_store, "database", None), Path)):
            raise RuleAdmissionError("invalid_store", "RuleAdmissionStore requires a KnowledgeStore.")
        self.knowledge = knowledge_store
        self.database = knowledge_store.database
        connection = self._connect()
        try:
            self._write_transaction(connection, lambda: self._initialize(connection))
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
    def _write_transaction(connection, operation):
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
        present = [name in tables for name in (SCHEMA_TABLE, PACKAGE_TABLE, EVENT_TABLE)]
        if present[0]:
            versions = [row[0] for row in connection.execute(
                "SELECT version FROM " + SCHEMA_TABLE + " ORDER BY version")]
            if versions != [SCHEMA_VERSION] or not all(present):
                raise RuleAdmissionError("schema_version", "Unsupported admission-store schema version.")
            return
        if any(present[1:]):
            raise RuleAdmissionError("schema_version", "Admission tables have no version authority.")
        connection.execute("""
            CREATE TABLE mechanical_rule_admission_schema (
                version INTEGER PRIMARY KEY
            )
        """)
        connection.execute(
            "INSERT INTO mechanical_rule_admission_schema(version) VALUES (?)",
            (SCHEMA_VERSION,))
        connection.execute("""
            CREATE TABLE mechanical_rule_packages (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                project_id TEXT NOT NULL,
                rule_id TEXT NOT NULL REFERENCES rules(id),
                recorded_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE INDEX mechanical_rule_packages_project
            ON mechanical_rule_packages(project_id, sequence)
        """)
        connection.execute("""
            CREATE TABLE mechanical_rule_admission_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                project_id TEXT NOT NULL,
                rule_id TEXT NOT NULL REFERENCES rules(id),
                version_id TEXT NOT NULL REFERENCES mechanical_rule_packages(id),
                action TEXT NOT NULL,
                supersedes TEXT REFERENCES mechanical_rule_admission_events(id),
                recorded_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE INDEX mechanical_rule_admission_events_project
            ON mechanical_rule_admission_events(project_id, sequence)
        """)
        connection.execute("""
            CREATE INDEX mechanical_rule_admission_events_rule
            ON mechanical_rule_admission_events(project_id, rule_id, sequence)
        """)

    @staticmethod
    def _normalize_implementation(value, field):
        if not isinstance(value, dict) or set(value) != IMPLEMENTATION_FIELDS:
            raise RuleAdmissionError("invalid_implementation", "%s has invalid fields." % field)
        return {key: _hash(value[key], field + "." + key)
                for key in sorted(IMPLEMENTATION_FIELDS)}

    @staticmethod
    def _normalize_pins(value):
        if not isinstance(value, list):
            raise RuleAdmissionError("invalid_pins", "source_pins must be a list.")
        pins, seen = [], set()
        for index, pin in enumerate(value):
            field = "source_pins[%d]" % index
            expected = {"source_id", "snapshot_sha256", "lifecycle_sequence", "state"}
            if not isinstance(pin, dict) or set(pin) != expected:
                raise RuleAdmissionError("invalid_pins", "%s has invalid fields." % field)
            source_id = _identity(pin["source_id"], field + ".source_id")
            if source_id in seen:
                raise RuleAdmissionError("invalid_pins", "source_pins contains a duplicate source.")
            seen.add(source_id)
            sequence = pin["lifecycle_sequence"]
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
                raise RuleAdmissionError("invalid_pins", "%s has an invalid lifecycle sequence." % field)
            if pin["state"] not in PIN_STATES:
                raise RuleAdmissionError("invalid_pins", "%s has an invalid lifecycle state." % field)
            pins.append({
                "source_id": source_id,
                "snapshot_sha256": _hash(pin["snapshot_sha256"], field + ".snapshot_sha256"),
                "lifecycle_sequence": sequence,
                "state": pin["state"],
            })
        if [pin["source_id"] for pin in pins] != sorted(seen):
            raise RuleAdmissionError("invalid_pins", "source_pins must use canonical source order.")
        return pins

    @classmethod
    def _normalize_report(cls, value, rule_id, scope, implementation):
        if not isinstance(value, dict) or set(value) != REPORT_FIELDS:
            raise RuleAdmissionError("invalid_validation", "Validation report fields are invalid.")
        report = _canonical(value)[1]
        supplied_hash = _hash(report["sha256"], "validation.sha256")
        without_hash = {key: item for key, item in report.items() if key != "sha256"}
        if hashlib.sha256(_canonical(without_hash)[0]).hexdigest() != supplied_hash:
            raise RuleAdmissionError("validation_hash", "Validation report identity does not match its bytes.")
        if (report["schema"] != "mechanical-rule-validation/v1" or
                report["rule_id"] != rule_id or
                report["compiled_rule_id"] != scope["compiled_rule_id"] or
                report["compiled_sha256"] != scope["compiled_sha256"] or
                report["implementation"] != implementation):
            raise RuleAdmissionError("validation_identity", "Validation report identities do not match the package.")
        _hash(report["compiled_sha256"], "validation.compiled_sha256")
        cls._normalize_implementation(report["implementation"], "validation.implementation")
        if report["quantity_authority"] != "none" or not isinstance(report["passed"], bool):
            raise RuleAdmissionError("invalid_validation", "Validation report authority or verdict is invalid.")
        coverage = report["coverage"]
        if not isinstance(coverage, dict) or set(coverage) != {"positive", "negative", "unknown"}:
            raise RuleAdmissionError("validation_coverage", "Validation coverage fields are invalid.")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in coverage.values()):
            raise RuleAdmissionError("validation_coverage", "Validation coverage counts are invalid.")
        cases, results = report["cases"], report["results"]
        if (not isinstance(cases, list) or not isinstance(results, list) or
                not 3 <= len(cases) <= 100 or len(results) != len(cases)):
            raise RuleAdmissionError("validation_coverage", "Validation must contain 3 to 100 complete cases.")
        case_ids, kinds = [], {"positive": 0, "negative": 0, "unknown": 0}
        expected_for_kind = {
            "positive": "matched", "negative": "not_applicable",
            "unknown": "needs_context",
        }
        by_id = {}
        for case in cases:
            if (not isinstance(case, dict) or
                    set(case) != {"id", "kind", "object", "expected_status"}):
                raise RuleAdmissionError("invalid_validation", "Validation case fields are invalid.")
            case_id = _identity(case["id"], "validation case id")
            kind = case["kind"]
            if kind not in kinds or case["expected_status"] != expected_for_kind.get(kind):
                raise RuleAdmissionError("validation_coverage", "Validation case expectation is invalid.")
            if not isinstance(case["object"], dict):
                raise RuleAdmissionError("invalid_validation", "Validation case object must be a dictionary.")
            if case_id in by_id:
                raise RuleAdmissionError("validation_coverage", "Validation case IDs must be distinct.")
            case_ids.append(case_id)
            kinds[kind] += 1
            by_id[case_id] = case
        if case_ids != sorted(case_ids) or kinds != coverage or any(not count for count in kinds.values()):
            raise RuleAdmissionError("validation_coverage", "Validation coverage is incomplete or noncanonical.")
        result_ids, verdicts = [], []
        for result in results:
            expected = {"case_id", "actual_status", "expected_status", "passed"}
            if not isinstance(result, dict) or set(result) != expected:
                raise RuleAdmissionError("invalid_validation", "Validation result fields are invalid.")
            case_id = _identity(result["case_id"], "validation result case_id")
            case = by_id.get(case_id)
            actual = result["actual_status"]
            if (case is None or actual not in RESULT_STATES or
                    result["expected_status"] != case["expected_status"] or
                    not isinstance(result["passed"], bool) or
                    result["passed"] != (actual == case["expected_status"])):
                raise RuleAdmissionError("invalid_validation", "Validation result is inconsistent.")
            result_ids.append(case_id)
            verdicts.append(result["passed"])
        expected_passed = scope["status"] == "supported" and all(verdicts)
        if result_ids != case_ids or report["passed"] != expected_passed:
            raise RuleAdmissionError("invalid_validation", "Validation verdict does not match its results.")
        return report

    @classmethod
    def _normalize_package(cls, payload):
        if not isinstance(payload, dict):
            raise RuleAdmissionError("invalid_payload", "Rule package must be a dictionary.")
        if set(payload) & FORBIDDEN:
            raise RuleAdmissionError("authority_forbidden", "Rule packages cannot carry authority or quantity fields.")
        if set(payload) != PACKAGE_FIELDS:
            raise RuleAdmissionError("invalid_payload", "Rule package fields are invalid.")
        package = _canonical(payload)[1]
        _identity(package["project_id"], "project_id")
        _identity(package["rule_id"], "rule_id")
        _text(package["author"], "author")
        _text(package["reason"], "reason")
        scope = package["scope"]
        if not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS:
            raise RuleAdmissionError("invalid_scope", "Rule scope fields are invalid.")
        if scope["purpose"] != "requirement_applicability":
            raise RuleAdmissionError("invalid_scope", "Only requirement_applicability may be packaged.")
        _identity(scope["compiled_rule_id"], "scope.compiled_rule_id")
        _hash(scope["compiled_sha256"], "scope.compiled_sha256")
        if (not isinstance(scope["selector"], dict) or
                not isinstance(scope["effect"], dict) or
                not isinstance(scope["conditions"], list) or
                scope["status"] not in SCOPE_STATES):
            raise RuleAdmissionError("invalid_scope", "Compiled rule scope is invalid.")
        package["source_pins"] = cls._normalize_pins(package["source_pins"])
        package["implementation"] = cls._normalize_implementation(
            package["implementation"], "implementation")
        package["validation"] = cls._normalize_report(
            package["validation"], package["rule_id"], scope,
            package["implementation"])
        if package["rollback"] != {"action": "disable_rule"}:
            raise RuleAdmissionError("invalid_rollback", "Rollback action must be disable_rule.")
        return _canonical(package)

    @staticmethod
    def _normalize_event(payload):
        if not isinstance(payload, dict):
            raise RuleAdmissionError("invalid_event", "Admission event must be a dictionary.")
        if set(payload) & FORBIDDEN:
            raise RuleAdmissionError("authority_forbidden", "Event payload contains forbidden authority fields.")
        if set(payload) != EVENT_FIELDS:
            raise RuleAdmissionError("invalid_event", "Admission event fields are invalid.")
        event = _canonical(payload)[1]
        for field in ("project_id", "rule_id", "version_id"):
            _identity(event[field], field)
        if event["action"] not in EVENT_ACTIONS:
            raise RuleAdmissionError("invalid_event", "Admission event action is invalid.")
        _text(event["actor"], "actor")
        _text(event["reason"], "reason")
        if event["supersedes"] is not None:
            _identity(event["supersedes"], "supersedes")
        return _canonical(event)

    @staticmethod
    def _knowledge_error(error):
        raise RuleAdmissionError(
            getattr(error, "code", "knowledge_error"),
            getattr(error, "message", str(error))) from None

    def _source(self, connection, source_id):
        try:
            row, metadata, snapshot = self.knowledge._source_row(connection, source_id)
            lifecycle = self.knowledge._latest_source_decision(connection, source_id)
            return row, metadata, snapshot, lifecycle
        except RuleAdmissionError:
            raise
        except Exception as error:
            self._knowledge_error(error)

    def _rule(self, connection, rule_id):
        try:
            return self.knowledge._rule_result(connection, rule_id)
        except RuleAdmissionError:
            raise
        except Exception as error:
            self._knowledge_error(error)

    @staticmethod
    def _rule_project(rule):
        payload = rule["payload"]
        return payload.get("project_id") or payload.get("applicability", {}).get("project_id")

    def _package_issues(self, connection, payload, admission):
        rule = self._rule(connection, payload["rule_id"])
        if self._rule_project(rule) != payload["project_id"]:
            code = "project_mismatch" if admission else "integrity_error"
            raise RuleAdmissionError(code, "Candidate rule belongs to another project.")
        pins = {pin["source_id"]: pin for pin in payload["source_pins"]}
        required = {citation["source_id"] for citation in rule["payload"]["citations"]}
        if not required <= set(pins):
            code = "source_pin_missing" if admission else "integrity_error"
            raise RuleAdmissionError(code, "Package pins do not cover candidate citations.")
        vocabulary_found = False
        issues = set()
        for source_id, pin in pins.items():
            row, metadata, unused_snapshot, lifecycle = self._source(connection, source_id)
            source_project = metadata.get("project_id", "unspecified")
            if source_project not in ("unspecified", payload["project_id"]):
                code = "project_mismatch" if admission else "integrity_error"
                raise RuleAdmissionError(code, "Pinned source belongs to another project.")
            if (metadata.get("source_class") == "application_vocabulary" and
                    metadata.get("snapshot_kind") == "mechanical_vocabulary"):
                vocabulary_found = True
            changed = (pin["snapshot_sha256"] != row["snapshot_sha256"] or
                       pin["lifecycle_sequence"] != lifecycle["sequence"] or
                       pin["state"] != lifecycle["state"])
            if changed:
                if admission:
                    raise RuleAdmissionError("source_pin_mismatch", "Source pin no longer matches.")
                issues.add("source_pin_changed:" + source_id)
            if lifecycle["state"] != "current":
                issues.add("source_not_current:" + source_id)
        if not vocabulary_found:
            code = "vocabulary_pin_missing" if admission else "integrity_error"
            raise RuleAdmissionError(code, "Package has no pinned mechanical vocabulary.")
        if not payload["validation"]["passed"]:
            issues.add("validation_failed")
        if payload["scope"]["status"] != "supported":
            issues.add("scope_not_supported:" + payload["scope"]["status"])
        return sorted(issues)

    def _package_row(self, connection, version_id):
        row = connection.execute("""
            SELECT sequence, id, payload_json, project_id, rule_id, recorded_at
            FROM mechanical_rule_packages WHERE id = ?
        """, (version_id,)).fetchone()
        if row is None:
            raise RuleAdmissionError("version_not_found", "Rule package was not found.")
        try:
            parsed = json.loads(row["payload_json"])
            package_bytes, payload = self._normalize_package(parsed)
        except (json.JSONDecodeError, UnicodeError, RuleAdmissionError) as error:
            raise RuleAdmissionError("integrity_error", "Stored rule package is invalid: %s" % error) from None
        expected_id = _content_id(
            "rule_version_", "heleos-rule-admission-package-v1", package_bytes)
        if (package_bytes.decode("utf-8") != row["payload_json"] or
                expected_id != row["id"] or row["project_id"] != payload["project_id"] or
                row["rule_id"] != payload["rule_id"]):
            raise RuleAdmissionError("integrity_error", "Stored package identity does not match its payload.")
        if row["sequence"] < 1 or not isinstance(row["recorded_at"], str) or not row["recorded_at"]:
            raise RuleAdmissionError("integrity_error", "Stored package metadata is invalid.")
        return row, payload

    def _package_result(self, connection, version_id):
        row, payload = self._package_row(connection, version_id)
        issues = self._package_issues(connection, payload, admission=False)
        return {"id": row["id"], "payload": payload, "sequence": row["sequence"],
                "recorded_at": row["recorded_at"], "issues": issues}

    def prepare(self, payload):
        package_bytes, normalized = self._normalize_package(payload)
        version_id = _content_id(
            "rule_version_", "heleos-rule-admission-package-v1", package_bytes)
        connection = self._connect()
        try:
            def write():
                duplicate = connection.execute(
                    "SELECT id FROM mechanical_rule_packages WHERE id = ?",
                    (version_id,)).fetchone()
                if duplicate is not None:
                    return self._package_result(connection, version_id)
                self._package_issues(connection, normalized, admission=True)
                recorded_at = _now()
                connection.execute("""
                    INSERT INTO mechanical_rule_packages(
                        id, payload_json, project_id, rule_id, recorded_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (version_id, package_bytes.decode("utf-8"),
                      normalized["project_id"], normalized["rule_id"], recorded_at))
                return self._package_result(connection, version_id)
            return self._write_transaction(connection, write)
        finally:
            connection.close()

    def _event_row(self, connection, event_id):
        row = connection.execute("""
            SELECT sequence, id, payload_json, project_id, rule_id, version_id,
                   action, supersedes, recorded_at
            FROM mechanical_rule_admission_events WHERE id = ?
        """, (event_id,)).fetchone()
        if row is None:
            raise RuleAdmissionError("event_not_found", "Rule admission event was not found.")
        try:
            parsed = json.loads(row["payload_json"])
            event_bytes, payload = self._normalize_event(parsed)
        except (json.JSONDecodeError, UnicodeError, RuleAdmissionError) as error:
            raise RuleAdmissionError("integrity_error", "Stored admission event is invalid: %s" % error) from None
        expected_id = _content_id(
            "rule_event_", "heleos-rule-admission-event-v1", event_bytes)
        if (event_bytes.decode("utf-8") != row["payload_json"] or expected_id != row["id"] or
                any(row[field] != payload[field] for field in
                    ("project_id", "rule_id", "version_id", "action", "supersedes"))):
            raise RuleAdmissionError("integrity_error", "Stored event identity does not match its payload.")
        if row["sequence"] < 1 or not isinstance(row["recorded_at"], str) or not row["recorded_at"]:
            raise RuleAdmissionError("integrity_error", "Stored event metadata is invalid.")
        return row, payload

    def _verify_event_chain(self, connection, row, payload):
        seen = set()
        current_row, current_payload = row, payload
        while True:
            if current_row["id"] in seen:
                raise RuleAdmissionError("integrity_error", "Admission event chain contains a cycle.")
            seen.add(current_row["id"])
            previous = connection.execute("""
                SELECT id FROM mechanical_rule_admission_events
                WHERE project_id = ? AND rule_id = ? AND sequence < ?
                ORDER BY sequence DESC LIMIT 1
            """, (current_payload["project_id"], current_payload["rule_id"],
                  current_row["sequence"])).fetchone()
            parent_id = current_payload["supersedes"]
            if parent_id is None:
                if previous is not None:
                    raise RuleAdmissionError("integrity_error", "Admission event omitted its prior head.")
                return
            if previous is None or previous["id"] != parent_id:
                raise RuleAdmissionError("integrity_error", "Admission parent is not the prior rule head.")
            parent_row, parent_payload = self._event_row(connection, parent_id)
            if ((parent_payload["project_id"], parent_payload["rule_id"]) !=
                    (payload["project_id"], payload["rule_id"])):
                raise RuleAdmissionError("integrity_error", "Admission parent belongs to another rule.")
            current_row, current_payload = parent_row, parent_payload

    def _active_before(self, connection, project_id, rule_id, before_sequence=None):
        query = """
            SELECT id FROM mechanical_rule_admission_events
            WHERE project_id = ? AND rule_id = ?
        """
        values = [project_id, rule_id]
        if before_sequence is not None:
            query += " AND sequence < ?"
            values.append(before_sequence)
        query += " ORDER BY sequence"
        active = None
        for item in connection.execute(query, values):
            unused_row, event = self._event_row(connection, item["id"])
            if event["action"] == "approve":
                active = event["version_id"]
            elif event["action"] == "withdraw":
                if active != event["version_id"]:
                    raise RuleAdmissionError("integrity_error", "Withdrawal did not target the active version.")
                active = None
        return active

    def _latest_review(self, connection, project_id, rule_id, version_id, before_sequence=None):
        query = """
            SELECT id FROM mechanical_rule_admission_events
            WHERE project_id = ? AND rule_id = ? AND version_id = ?
              AND action IN ('review_pass','review_reject')
        """
        values = [project_id, rule_id, version_id]
        if before_sequence is not None:
            query += " AND sequence < ?"
            values.append(before_sequence)
        query += " ORDER BY sequence DESC LIMIT 1"
        row = connection.execute(query, values).fetchone()
        return None if row is None else self._event_row(connection, row["id"])[1]

    def _event_dependencies(self, connection, payload, admission, before_sequence=None):
        package = self._package_result(connection, payload["version_id"])
        if (package["payload"]["project_id"] != payload["project_id"] or
                package["payload"]["rule_id"] != payload["rule_id"]):
            code = "package_mismatch" if admission else "integrity_error"
            raise RuleAdmissionError(code, "Event package belongs to another project or rule.")
        action = payload["action"]
        if action.startswith("review_"):
            if _actor_identity(payload["actor"]) == _actor_identity(package["payload"]["author"]):
                code = "self_review" if admission else "integrity_error"
                raise RuleAdmissionError(code, "Package author cannot review the same version.")
            if self._active_before(
                    connection, payload["project_id"], payload["rule_id"],
                    before_sequence) == payload["version_id"]:
                code = "active_review_forbidden" if admission else "integrity_error"
                raise RuleAdmissionError(code, "Withdraw an active version before reviewing it again.")
        return package

    def _event_result(self, connection, event_id):
        row, payload = self._event_row(connection, event_id)
        self._verify_event_chain(connection, row, payload)
        package = self._event_dependencies(
            connection, payload, admission=False, before_sequence=row["sequence"])
        return {"id": row["id"], "payload": payload, "sequence": row["sequence"],
                "recorded_at": row["recorded_at"], "issues": package["issues"]}

    def record(self, payload):
        event_bytes, normalized = self._normalize_event(payload)
        event_id = _content_id(
            "rule_event_", "heleos-rule-admission-event-v1", event_bytes)
        connection = self._connect()
        try:
            def write():
                duplicate = connection.execute(
                    "SELECT id FROM mechanical_rule_admission_events WHERE id = ?",
                    (event_id,)).fetchone()
                if duplicate is not None:
                    return self._event_result(connection, event_id)
                package = self._event_dependencies(connection, normalized, admission=True)
                latest = connection.execute("""
                    SELECT id FROM mechanical_rule_admission_events
                    WHERE project_id = ? AND rule_id = ?
                    ORDER BY sequence DESC LIMIT 1
                """, (normalized["project_id"], normalized["rule_id"])).fetchone()
                parent_id = normalized["supersedes"]
                if parent_id is not None:
                    parent_row, parent = self._event_row(connection, parent_id)
                    self._verify_event_chain(connection, parent_row, parent)
                    if ((parent["project_id"], parent["rule_id"]) !=
                            (normalized["project_id"], normalized["rule_id"])):
                        raise RuleAdmissionError("parent_mismatch", "Event parent belongs to another rule.")
                    if latest is None or latest["id"] != parent_id:
                        raise RuleAdmissionError("stale_head", "Event parent is not the current rule head.")
                elif latest is not None:
                    raise RuleAdmissionError("stale_head", "Current rule event head must be superseded.")

                active = self._active_before(
                    connection, normalized["project_id"], normalized["rule_id"])
                if normalized["action"].startswith("review_") and active == normalized["version_id"]:
                    raise RuleAdmissionError(
                        "active_review_forbidden", "Withdraw an active version before reviewing it again.")
                if normalized["action"] == "review_pass" and package["issues"]:
                    raise RuleAdmissionError(
                        "package_blocked", "Package has unresolved admission issues.")
                if normalized["action"] == "approve":
                    review = self._latest_review(
                        connection, normalized["project_id"], normalized["rule_id"],
                        normalized["version_id"])
                    if review is None or review["action"] != "review_pass":
                        raise RuleAdmissionError("review_not_passed", "Latest version review must pass.")
                    if package["issues"]:
                        raise RuleAdmissionError("package_blocked", "Package has unresolved admission issues.")
                    if active == normalized["version_id"]:
                        raise RuleAdmissionError("already_active", "Rule version is already active.")
                if normalized["action"] == "withdraw":
                    if active is None:
                        raise RuleAdmissionError("no_active_version", "Rule has no active version to withdraw.")
                    if active != normalized["version_id"]:
                        raise RuleAdmissionError(
                            "active_version_mismatch", "Withdrawal must target the active version.")

                recorded_at = _now()
                connection.execute("""
                    INSERT INTO mechanical_rule_admission_events(
                        id, payload_json, project_id, rule_id, version_id,
                        action, supersedes, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (event_id, event_bytes.decode("utf-8"), normalized["project_id"],
                      normalized["rule_id"], normalized["version_id"],
                      normalized["action"], parent_id, recorded_at))
                return self._event_result(connection, event_id)
            return self._write_transaction(connection, write)
        finally:
            connection.close()

    def get(self, version_id):
        _identity(version_id, "version_id")
        connection = self._connect()
        try:
            return self._read_transaction(
                connection, lambda: self._package_result(connection, version_id))
        finally:
            connection.close()

    def versions(self, project_id):
        _identity(project_id, "project_id")
        connection = self._connect()
        try:
            def read():
                ids = [row["id"] for row in connection.execute("""
                    SELECT id FROM mechanical_rule_packages
                    WHERE project_id = ? ORDER BY sequence
                """, (project_id,))]
                return [self._package_result(connection, version_id) for version_id in ids]
            return self._read_transaction(connection, read)
        finally:
            connection.close()

    def events(self, project_id):
        _identity(project_id, "project_id")
        connection = self._connect()
        try:
            def read():
                ids = [row["id"] for row in connection.execute("""
                    SELECT id FROM mechanical_rule_admission_events
                    WHERE project_id = ? ORDER BY sequence
                """, (project_id,))]
                return [self._event_result(connection, event_id) for event_id in ids]
            return self._read_transaction(connection, read)
        finally:
            connection.close()
