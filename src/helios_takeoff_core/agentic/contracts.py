"""Strict JSON contracts accepted from P1A worker processes."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ValidationError


WORKER_RESULT_PROTOCOL = "helios.p1a.worker-result/v1"
BASELINE_AUDIT_REPORT_KIND = "BASELINE_AUDIT_REPORT"
BASELINE_AUDIT_REPORT_SCHEMA = "helios.p1a.baseline-audit-report/v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WORKER_CODE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
WORKERS_CONFIG_PROTOCOL = "helios.p1a.workers-config/v1"
BASELINE_AUDIT_PAYLOAD_FIELDS = {
    "project_id",
    "revision_set_id",
    "input_sha256",
    "document_count",
    "document_counts_by_type",
    "required_document_types_absent",
    "superseded_document_references_not_included",
    "missing_issue_date_document_ids",
    "limitation",
}


@dataclass(frozen=True)
class WorkerConfig:
    """One normalized, host-controlled local subprocess adapter."""

    worker_code: str
    adapter_name: str
    adapter_kind: str
    adapter_revision: str
    capability: str
    result_protocol: str
    argv: tuple[str, ...]
    preflight_argv: tuple[str, ...]
    timeout_seconds: int
    environment_variables: tuple[str, ...]
    configuration_sha256: str


@dataclass(frozen=True)
class WorkersConfig:
    """Strict normalized worker configuration without credential values."""

    workers: tuple[WorkerConfig, ...]

    def find_worker(self, worker_code: str) -> WorkerConfig | None:
        return next((worker for worker in self.workers if worker.worker_code == worker_code), None)

    def worker(self, worker_code: str) -> WorkerConfig:
        worker = self.find_worker(worker_code)
        if worker is None:
            raise ValidationError(f"workers configuration has no entry for {worker_code}")
        return worker


def load_workers_config(path: Path) -> WorkersConfig:
    """Load strict JSON and reject duplicate object keys before normalization."""
    if not isinstance(path, Path):
        raise ValidationError("workers configuration path must be a Path")
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(text, object_pairs_hook=_unique_object)
    except (OSError, UnicodeDecodeError) as error:
        raise ValidationError("workers configuration cannot be read as UTF-8") from error
    except json.JSONDecodeError as error:
        raise ValidationError("workers configuration must contain valid JSON") from error
    return parse_workers_config(payload)


def parse_workers_config(payload: object) -> WorkersConfig:
    """Validate the exact v1 schema and derive stable per-adapter hashes."""
    if not isinstance(payload, dict):
        raise ValidationError("workers configuration must be an object")
    _require_exact_fields(payload, {"protocol", "workers"}, "workers configuration")
    if payload["protocol"] != WORKERS_CONFIG_PROTOCOL:
        raise ValidationError("workers configuration protocol is unsupported")
    entries = payload["workers"]
    if not isinstance(entries, list):
        raise ValidationError("workers configuration workers must be an array")
    workers = tuple(_parse_worker_config(entry, index=index) for index, entry in enumerate(entries))
    codes = [worker.worker_code for worker in workers]
    if len(set(codes)) != len(codes):
        raise ValidationError("workers configuration contains a duplicate worker_code")
    return WorkersConfig(workers=tuple(sorted(workers, key=lambda worker: worker.worker_code)))


def _parse_worker_config(value: object, *, index: int) -> WorkerConfig:
    if not isinstance(value, dict):
        raise ValidationError(f"workers configuration entry {index} must be an object")
    fields = {
        "worker_code",
        "adapter_name",
        "adapter_kind",
        "adapter_revision",
        "capability",
        "result_protocol",
        "argv",
        "preflight_argv",
        "timeout_seconds",
        "environment_variables",
    }
    _require_exact_fields(value, fields, f"workers configuration entry {index}")
    worker_code = _config_text(value["worker_code"], "worker_code")
    if WORKER_CODE_PATTERN.fullmatch(worker_code) is None:
        raise ValidationError("workers configuration worker_code must be lowercase hyphenated text")
    adapter_name = _config_text(value["adapter_name"], "adapter_name")
    adapter_kind = _config_text(value["adapter_kind"], "adapter_kind")
    if adapter_kind != "SUBPROCESS":
        raise ValidationError("configured worker adapter_kind must be SUBPROCESS")
    adapter_revision = _config_text(value["adapter_revision"], "adapter_revision")
    capability = _config_text(value["capability"], "capability")
    if capability != "BASELINE_AUDIT":
        raise ValidationError("configured worker capability must be BASELINE_AUDIT")
    result_protocol = _config_text(value["result_protocol"], "result_protocol")
    if result_protocol != WORKER_RESULT_PROTOCOL:
        raise ValidationError("configured worker result_protocol is unsupported")
    argv = _argv(value["argv"], "argv")
    preflight_argv = _argv(value["preflight_argv"], "preflight_argv")
    timeout_seconds = value["timeout_seconds"]
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 300
    ):
        raise ValidationError("configured worker timeout_seconds must be an integer from 1 through 300")
    environment_variables_value = value["environment_variables"]
    if not isinstance(environment_variables_value, list):
        raise ValidationError("configured worker environment_variables must be an array")
    environment_variables = tuple(
        _config_text(name, "environment variable name") for name in environment_variables_value
    )
    if any(ENVIRONMENT_NAME_PATTERN.fullmatch(name) is None for name in environment_variables):
        raise ValidationError("configured environment variable names are invalid")
    if len(set(environment_variables)) != len(environment_variables):
        raise ValidationError("configured environment variable names must not repeat")
    normalized = {
        "worker_code": worker_code,
        "adapter_name": adapter_name,
        "adapter_kind": adapter_kind,
        "adapter_revision": adapter_revision,
        "capability": capability,
        "result_protocol": result_protocol,
        "argv": list(argv),
        "preflight_argv": list(preflight_argv),
        "timeout_seconds": timeout_seconds,
        "environment_variables": sorted(environment_variables),
    }
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return WorkerConfig(
        worker_code=worker_code,
        adapter_name=adapter_name,
        adapter_kind=adapter_kind,
        adapter_revision=adapter_revision,
        capability=capability,
        result_protocol=result_protocol,
        argv=argv,
        preflight_argv=preflight_argv,
        timeout_seconds=timeout_seconds,
        environment_variables=tuple(sorted(environment_variables)),
        configuration_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"workers configuration contains duplicate object key: {key}")
        result[key] = value
    return result


def _config_text(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValidationError(f"configured worker {name} must be non-blank text without controls")
    return value


def _argv(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > 64:
        raise ValidationError(f"configured worker {name} must contain 1 through 64 arguments")
    arguments = tuple(_config_text(argument, f"{name} argument") for argument in value)
    if any(len(argument.encode("utf-8")) > 4096 for argument in arguments):
        raise ValidationError(f"configured worker {name} argument is too long")
    return arguments


def validate_worker_result(payload: object, *, expected_input_sha256: str) -> dict[str, Any]:
    """Validate and return the exact P1A baseline-audit worker-result object."""
    _require_sha256(expected_input_sha256, "expected input SHA-256")
    if not isinstance(payload, dict):
        raise ValidationError("worker result must be an object")
    _require_exact_fields(payload, {"protocol", "status", "input_sha256", "report"}, "worker result")
    if payload["protocol"] != WORKER_RESULT_PROTOCOL:
        raise ValidationError("worker result protocol is unsupported")
    if payload["status"] != "SUCCEEDED":
        raise ValidationError("worker result status must be SUCCEEDED")
    input_sha256 = payload["input_sha256"]
    _require_sha256(input_sha256, "worker result input_sha256")
    if input_sha256 != expected_input_sha256:
        raise ValidationError("worker result input_sha256 does not match the input artifact")

    report = payload["report"]
    if not isinstance(report, dict):
        raise ValidationError("worker result report must be an object")
    _require_exact_fields(report, {"kind", "schema_version", "payload"}, "worker result report")
    if report["kind"] != BASELINE_AUDIT_REPORT_KIND:
        raise ValidationError("worker result report kind is unsupported")
    if report["schema_version"] != BASELINE_AUDIT_REPORT_SCHEMA:
        raise ValidationError("worker result report schema_version is unsupported")
    if not isinstance(report["payload"], dict):
        raise ValidationError("worker result report payload must be an object")
    return payload


def validate_baseline_audit_payload(
    payload: object,
    *,
    expected_project_id: str,
    expected_revision_set_id: str,
    expected_input_sha256: str,
) -> dict[str, Any]:
    """Validate report payload shape, counts, and immutable input lineage."""
    if not isinstance(payload, dict) or set(payload) != BASELINE_AUDIT_PAYLOAD_FIELDS:
        raise ValidationError("baseline audit report payload fields are invalid")
    if payload["project_id"] != expected_project_id or payload["revision_set_id"] != expected_revision_set_id:
        raise ValidationError("baseline audit report lineage does not match the work item")
    if payload["input_sha256"] != expected_input_sha256:
        raise ValidationError("baseline audit report input digest does not match the work item")
    document_count = payload["document_count"]
    if not isinstance(document_count, int) or isinstance(document_count, bool) or document_count < 0:
        raise ValidationError("baseline audit report document_count is invalid")
    counts = payload["document_counts_by_type"]
    if (
        not isinstance(counts, dict)
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            for name, count in counts.items()
        )
        or sum(counts.values()) != document_count
    ):
        raise ValidationError("baseline audit report document counts are invalid")
    for field in ("required_document_types_absent", "missing_issue_date_document_ids"):
        value = payload[field]
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise ValidationError(f"baseline audit report {field} is invalid")
    references = payload["superseded_document_references_not_included"]
    if not isinstance(references, list) or any(
        not isinstance(reference, dict)
        or set(reference) != {"document_revision_id", "supersedes_document_revision_id"}
        or any(not isinstance(value, str) or not value for value in reference.values())
        for reference in references
    ):
        raise ValidationError("baseline audit report supersession references are invalid")
    if not isinstance(payload["limitation"], str) or not payload["limitation"].strip():
        raise ValidationError("baseline audit report limitation is invalid")
    return payload


def _require_exact_fields(payload: dict[str, Any], expected_fields: set[str], name: str) -> None:
    if set(payload) != expected_fields:
        raise ValidationError(f"{name} fields are invalid")


def _require_sha256(value: object, name: str) -> None:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ValidationError(f"{name} must be a 64-character lowercase hexadecimal SHA-256 digest")
