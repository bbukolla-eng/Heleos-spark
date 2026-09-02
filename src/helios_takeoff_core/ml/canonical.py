"""Fail-closed canonical compilers for immutable ML registry documents."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from types import MappingProxyType
from typing import Callable, Mapping, TypeVar, cast
from urllib.parse import urlparse

from ..errors import ValidationError
from .contracts import (
    ARTIFACT_RECEIPT_PROTOCOL,
    BENCHMARK_RECEIPT_PROTOCOL,
    BENCHMARK_SPEC_PROTOCOL,
    CORE_ACCEPTANCE_PROTOCOL,
    DATASET_INTAKE_RECEIPT_PROTOCOL,
    DATASET_MANIFEST_PROTOCOL,
    MODEL_MANIFEST_PROTOCOL,
    SCAN_RECEIPT_PROTOCOL,
    TARGET_ACCEPTANCE_PROTOCOL,
    CompiledBenchmarkSpec,
    CompiledDatasetManifest,
    CompiledModelManifest,
    DatasetPurpose,
    PromotionState,
    ReceiptStatus,
    RightsVerdict,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HF_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_RIGHTS = frozenset({"ACCESS", "COPYING", "REDISTRIBUTION", "COMMERCIAL_USE", "TRAINING"})
_FORBIDDEN_SCHEMA_TOKENS = (
    "executable", "path", "callback", "import", "environment", "providercommand", "credential",
)
_MODEL_FIELDS = frozenset({
    "protocol", "model_code", "manifest_version", "provider", "repository", "revision", "source_url",
    "task", "intended_use", "model_card_sha256", "artifacts", "rights", "runtime",
    "memory_requirements", "hardware_requirements", "input_schema", "output_schema",
    "permitted_data_classes", "benchmark_bindings", "promotion",
})
_DATASET_FIELDS = frozenset({
    "protocol", "dataset_code", "manifest_version", "provider", "dataset", "revision", "source_url",
    "purpose", "intended_use", "artifacts", "rights", "provenance", "permitted_data_classes",
})
_BENCHMARK_FIELDS = frozenset({
    "protocol", "benchmark_code", "version", "task", "dataset_manifest_sha256", "model_manifest_sha256",
    "input_artifact_sha256", "metrics", "thresholds", "confidence_threshold", "warmup_count",
    "measurement_repetitions", "failure_set_review_required",
})
_ARTIFACT_FIELDS = frozenset({"logical_name", "source_url", "sha256", "byte_size", "media_type", "serialization"})
_RIGHT_FIELDS = frozenset({"right", "verdict", "source_url", "reviewed_by", "reviewed_at", "basis"})
_RUNTIME_FIELDS = frozenset({
    "dependency_lock_sha256", "adapter_name", "adapter_revision", "python_version", "framework_versions",
    "device_class", "precision", "trust_remote_code",
})
_MEMORY_FIELDS = frozenset({"minimum_bytes", "recommended_bytes"})
_HARDWARE_FIELDS = frozenset({"accelerator", "minimum_unified_memory_bytes"})
_PROMOTION_FIELDS = frozenset({
    "state", "supersedes_manifest_sha256", "benchmark_receipt_sha256s", "rollback_manifest_sha256",
    "failure_set_review_sha256",
})
_PROVENANCE_FIELDS = frozenset({"original_collection_method", "coverage", "private_or_proprietary_risk"})
_METRIC_FIELDS = frozenset({"name", "direction"})
_RECEIPT_ENVELOPE_FIELDS = frozenset({"protocol", "status", "evidence_refs"})
_RECEIPT_PROTOCOLS = frozenset({
    ARTIFACT_RECEIPT_PROTOCOL, SCAN_RECEIPT_PROTOCOL, BENCHMARK_RECEIPT_PROTOCOL,
    DATASET_INTAKE_RECEIPT_PROTOCOL, CORE_ACCEPTANCE_PROTOCOL, TARGET_ACCEPTANCE_PROTOCOL,
})
_T = TypeVar("_T")


def compile_model_manifest(document: Mapping[str, object]) -> CompiledModelManifest:
    """Validate and content-address one immutable Hugging Face model manifest."""
    normalized = _normalize_model_manifest(document)
    canonical_bytes = _canonical_bytes(normalized)
    return CompiledModelManifest(_freeze_mapping(normalized), canonical_bytes, _sha256(canonical_bytes))


def compile_dataset_manifest(document: Mapping[str, object]) -> CompiledDatasetManifest:
    """Validate and content-address one immutable dataset manifest."""
    normalized = _normalize_dataset_manifest(document)
    canonical_bytes = _canonical_bytes(normalized)
    return CompiledDatasetManifest(_freeze_mapping(normalized), canonical_bytes, _sha256(canonical_bytes))


def compile_benchmark_spec(document: Mapping[str, object]) -> CompiledBenchmarkSpec:
    """Validate and content-address a frozen benchmark definition."""
    normalized = _normalize_benchmark_spec(document)
    canonical_bytes = _canonical_bytes(normalized)
    return CompiledBenchmarkSpec(_freeze_mapping(normalized), canonical_bytes, _sha256(canonical_bytes))


def canonicalize_receipt(document: Mapping[str, object]) -> CompiledBenchmarkSpec:
    """Canonicalize the closed common receipt envelope.

    Protocol-specific receipt fields deliberately remain blocked until their producing
    subsystems define an exact parser; this boundary cannot carry credentials or host
    claims by accident.
    """
    source = _document(document, _RECEIPT_ENVELOPE_FIELDS, "receipt")
    protocol = _text(source["protocol"], "receipt protocol")
    if protocol not in _RECEIPT_PROTOCOLS:
        raise ValidationError("receipt protocol is unsupported")
    try:
        status = ReceiptStatus(_text(source["status"], "receipt status")).value
    except ValueError as error:
        raise ValidationError("receipt status is unsupported") from error
    normalized = {
        "protocol": protocol,
        "status": status,
        "evidence_refs": _sha_set(source["evidence_refs"], "receipt evidence_refs"),
    }
    canonical_bytes = _canonical_bytes(normalized)
    return CompiledBenchmarkSpec(_freeze_mapping(normalized), canonical_bytes, _sha256(canonical_bytes))


def _normalize_model_manifest(document: Mapping[str, object]) -> dict[str, object]:
    source = _document(document, _MODEL_FIELDS, "model manifest")
    if source["protocol"] != MODEL_MANIFEST_PROTOCOL:
        raise ValidationError("model manifest protocol is unsupported")
    if _text(source["provider"], "provider") != "HUGGING_FACE":
        raise ValidationError("model provider must be HUGGING_FACE")
    revision = _hf_revision(source["revision"], "revision")
    runtime = _runtime(source["runtime"])
    if runtime["trust_remote_code"] is not False:
        raise ValidationError("trust_remote_code must be false")
    promotion = _promotion(source["promotion"])
    return {
        "protocol": MODEL_MANIFEST_PROTOCOL,
        "model_code": _text(source["model_code"], "model_code"),
        "manifest_version": _text(source["manifest_version"], "manifest_version"),
        "provider": "HUGGING_FACE",
        "repository": _text(source["repository"], "repository"),
        "revision": revision,
        "source_url": _url(source["source_url"], "source_url"),
        "task": _text(source["task"], "task"),
        "intended_use": _text(source["intended_use"], "intended_use"),
        "model_card_sha256": _sha(source["model_card_sha256"], "model_card_sha256"),
        "artifacts": _artifacts(source["artifacts"]),
        "rights": _rights(source["rights"]),
        "runtime": runtime,
        "memory_requirements": _memory(source["memory_requirements"]),
        "hardware_requirements": _hardware(source["hardware_requirements"]),
        "input_schema": _schema(source["input_schema"], "input_schema"),
        "output_schema": _schema(source["output_schema"], "output_schema"),
        "permitted_data_classes": _text_set(source["permitted_data_classes"], "permitted_data_classes"),
        "benchmark_bindings": _sha_set(source["benchmark_bindings"], "benchmark_bindings"),
        "promotion": promotion,
    }


def _normalize_dataset_manifest(document: Mapping[str, object]) -> dict[str, object]:
    source = _document(document, _DATASET_FIELDS, "dataset manifest")
    if source["protocol"] != DATASET_MANIFEST_PROTOCOL:
        raise ValidationError("dataset manifest protocol is unsupported")
    provider = _text(source["provider"], "dataset provider")
    if provider not in {"HUGGING_FACE", "KAGGLE"}:
        raise ValidationError("dataset provider is unsupported")
    revision = (
        _hf_revision(source["revision"], "dataset revision")
        if provider == "HUGGING_FACE" else _text(source["revision"], "dataset revision")
    )
    try:
        purpose = DatasetPurpose(_text(source["purpose"], "dataset purpose")).value
    except ValueError as error:
        raise ValidationError("dataset purpose is unsupported") from error
    return {
        "protocol": DATASET_MANIFEST_PROTOCOL,
        "dataset_code": _text(source["dataset_code"], "dataset_code"),
        "manifest_version": _text(source["manifest_version"], "manifest_version"),
        "provider": provider,
        "dataset": _text(source["dataset"], "dataset"),
        "revision": revision,
        "source_url": _url(source["source_url"], "source_url"),
        "purpose": purpose,
        "intended_use": _text(source["intended_use"], "intended_use"),
        "artifacts": _artifacts(source["artifacts"]),
        "rights": _rights(source["rights"]),
        "provenance": _provenance(source["provenance"]),
        "permitted_data_classes": _text_set(source["permitted_data_classes"], "permitted_data_classes"),
    }


def _normalize_benchmark_spec(document: Mapping[str, object]) -> dict[str, object]:
    source = _document(document, _BENCHMARK_FIELDS, "benchmark spec")
    if source["protocol"] != BENCHMARK_SPEC_PROTOCOL:
        raise ValidationError("benchmark spec protocol is unsupported")
    metrics = _metrics(source["metrics"])
    metric_names = {str(metric["name"]) for metric in metrics}
    thresholds = _thresholds(source["thresholds"], metric_names)
    return {
        "protocol": BENCHMARK_SPEC_PROTOCOL,
        "benchmark_code": _text(source["benchmark_code"], "benchmark_code"),
        "version": _text(source["version"], "version"),
        "task": _text(source["task"], "task"),
        "dataset_manifest_sha256": _sha(source["dataset_manifest_sha256"], "dataset_manifest_sha256"),
        "model_manifest_sha256": _sha(source["model_manifest_sha256"], "model_manifest_sha256"),
        "input_artifact_sha256": _sha(source["input_artifact_sha256"], "input_artifact_sha256"),
        "metrics": metrics,
        "thresholds": thresholds,
        "confidence_threshold": _decimal(source["confidence_threshold"], "confidence_threshold", between_zero_and_one=True),
        "warmup_count": _nonnegative_int(source["warmup_count"], "warmup_count"),
        "measurement_repetitions": _positive_int(source["measurement_repetitions"], "measurement_repetitions"),
        "failure_set_review_required": _bool(source["failure_set_review_required"], "failure_set_review_required"),
    }


def _document(value: Mapping[str, object], expected: frozenset[str], label: str) -> Mapping[str, object]:
    source = _mapping(value, label)
    _reject_floats(source)
    fields = set(source)
    missing = expected - fields
    unknown = fields - expected
    if missing:
        raise ValidationError(f"{label} is missing required fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ValidationError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")
    return source


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValidationError(f"{label} field names must be strings")
    return cast(Mapping[str, object], value)


def _artifacts(value: object) -> list[dict[str, object]]:
    return _semantic_set(value, "artifacts", lambda item: _artifact(item), lambda item: str(item["logical_name"]))


def _artifact(value: object) -> dict[str, object]:
    item = _exact_mapping(value, _ARTIFACT_FIELDS, "artifact descriptor")
    return {
        "logical_name": _text(item["logical_name"], "artifact logical_name"),
        "source_url": _url(item["source_url"], "artifact source_url"),
        "sha256": _sha(item["sha256"], "artifact sha256"),
        "byte_size": _positive_int(item["byte_size"], "artifact byte_size"),
        "media_type": _text(item["media_type"], "artifact media_type"),
        "serialization": _text(item["serialization"], "artifact serialization"),
    }


def _rights(value: object) -> list[dict[str, object]]:
    rights = _semantic_set(value, "rights", _right, lambda item: str(item["right"]))
    found = {str(item["right"]) for item in rights}
    if found != _RIGHTS:
        raise ValidationError("rights must contain exactly the five rights")
    return rights


def _right(value: object) -> dict[str, object]:
    item = _exact_mapping(value, _RIGHT_FIELDS, "rights finding")
    right = _text(item["right"], "right")
    if right not in _RIGHTS:
        raise ValidationError("right is unsupported")
    try:
        verdict = RightsVerdict(_text(item["verdict"], "rights verdict")).value
    except ValueError as error:
        raise ValidationError("rights verdict is unsupported") from error
    return {
        "right": right,
        "verdict": verdict,
        "source_url": _url(item["source_url"], "rights source_url"),
        "reviewed_by": _text(item["reviewed_by"], "reviewed_by"),
        "reviewed_at": _timestamp(item["reviewed_at"], "reviewed_at"),
        "basis": _text(item["basis"], "rights basis"),
    }


def _runtime(value: object) -> dict[str, object]:
    item = _exact_mapping(value, _RUNTIME_FIELDS, "runtime")
    framework_versions = _mapping(item["framework_versions"], "runtime framework_versions")
    if not framework_versions:
        raise ValidationError("runtime framework_versions must not be empty")
    normalized_frameworks: dict[str, str] = {}
    for key, version in framework_versions.items():
        framework_name = _text(key, "framework name")
        if framework_name in normalized_frameworks:
            raise ValidationError("runtime contains duplicate framework names")
        normalized_frameworks[framework_name] = _text(version, "framework version")
    return {
        "dependency_lock_sha256": _sha(item["dependency_lock_sha256"], "runtime dependency_lock_sha256"),
        "adapter_name": _text(item["adapter_name"], "runtime adapter_name"),
        "adapter_revision": _text(item["adapter_revision"], "runtime adapter_revision"),
        "python_version": _text(item["python_version"], "runtime python_version"),
        "framework_versions": dict(sorted(normalized_frameworks.items())),
        "device_class": _text(item["device_class"], "runtime device_class"),
        "precision": _text(item["precision"], "runtime precision"),
        "trust_remote_code": _bool(item["trust_remote_code"], "trust_remote_code"),
    }


def _memory(value: object) -> dict[str, object]:
    item = _exact_mapping(value, _MEMORY_FIELDS, "memory_requirements")
    minimum = _positive_int(item["minimum_bytes"], "minimum_bytes")
    recommended = _positive_int(item["recommended_bytes"], "recommended_bytes")
    if recommended < minimum:
        raise ValidationError("recommended_bytes must not be less than minimum_bytes")
    return {"minimum_bytes": minimum, "recommended_bytes": recommended}


def _hardware(value: object) -> dict[str, object]:
    item = _exact_mapping(value, _HARDWARE_FIELDS, "hardware_requirements")
    return {
        "accelerator": _text(item["accelerator"], "hardware accelerator"),
        "minimum_unified_memory_bytes": _positive_int(
            item["minimum_unified_memory_bytes"], "minimum_unified_memory_bytes",
        ),
    }


def _schema(value: object, label: str) -> dict[str, object]:
    schema = _normalize_json(_mapping(value, label), label)
    _reject_forbidden_schema_fields(schema, label)
    return schema


def _reject_forbidden_schema_fields(value: object, label: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if any(token in normalized_key for token in _FORBIDDEN_SCHEMA_TOKENS):
                raise ValidationError(f"{label} contains forbidden schema field: {key}")
            _reject_forbidden_schema_fields(child, label)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_schema_fields(child, label)


def _promotion(value: object) -> dict[str, object]:
    item = _exact_mapping(value, _PROMOTION_FIELDS, "promotion")
    try:
        state = PromotionState(_text(item["state"], "promotion state"))
    except ValueError as error:
        raise ValidationError("promotion state is unsupported") from error
    supersedes = _optional_sha(item["supersedes_manifest_sha256"], "supersedes_manifest_sha256")
    benchmarks = _sha_set(item["benchmark_receipt_sha256s"], "benchmark_receipt_sha256s")
    rollback = _optional_sha(item["rollback_manifest_sha256"], "rollback_manifest_sha256")
    review = _optional_sha(item["failure_set_review_sha256"], "failure_set_review_sha256")
    if state is PromotionState.QUALIFIED and (not benchmarks or review is None):
        raise ValidationError("QUALIFIED model requires passed benchmark receipts and failure-set review")
    if state is PromotionState.PROMOTED:
        if not benchmarks or review is None or rollback is None:
            raise ValidationError("PROMOTED model requires passed benchmark receipts, failure-set review, and rollback manifest")
        if rollback == supersedes:
            raise ValidationError("PROMOTED model rollback manifest must be distinct from superseded manifest")
    if state in {PromotionState.RETIRED, PromotionState.REJECTED} and supersedes is None and review is None:
        raise ValidationError(f"{state.value} model requires a successor or reason receipt")
    return {
        "state": state.value,
        "supersedes_manifest_sha256": supersedes,
        "benchmark_receipt_sha256s": benchmarks,
        "rollback_manifest_sha256": rollback,
        "failure_set_review_sha256": review,
    }


def _provenance(value: object) -> dict[str, object]:
    item = _exact_mapping(value, _PROVENANCE_FIELDS, "dataset provenance")
    return {
        "original_collection_method": _text(item["original_collection_method"], "original_collection_method"),
        "coverage": _text(item["coverage"], "coverage"),
        "private_or_proprietary_risk": _text(item["private_or_proprietary_risk"], "private_or_proprietary_risk"),
    }


def _metrics(value: object) -> list[dict[str, object]]:
    def parse(metric: object) -> dict[str, object]:
        item = _exact_mapping(metric, _METRIC_FIELDS, "metric declaration")
        direction = _text(item["direction"], "metric direction")
        if direction not in {"MINIMUM", "MAXIMUM"}:
            raise ValidationError("metric direction is unsupported")
        return {"name": _text(item["name"], "metric name"), "direction": direction}

    return _semantic_set(value, "metric declarations", parse, lambda item: str(item["name"]))


def _thresholds(value: object, metric_names: set[str]) -> dict[str, object]:
    values = _mapping(value, "thresholds")
    if set(values) != metric_names:
        raise ValidationError("thresholds must declare exactly one threshold for each metric")
    return {
        _text(name, "threshold metric name"): _decimal(raw, f"threshold {name}")
        for name, raw in sorted(values.items())
    }


def _semantic_set(
    value: object,
    label: str,
    parser: Callable[[object], _T],
    key: Callable[[_T], str],
) -> list[_T]:
    if not isinstance(value, list):
        raise ValidationError(f"{label} must be an array")
    parsed = [parser(item) for item in value]
    identifiers = [key(item) for item in parsed]
    if len(set(identifiers)) != len(identifiers):
        raise ValidationError(f"{label} contains duplicates")
    return sorted(parsed, key=key)


def _text_set(value: object, label: str) -> list[str]:
    return _semantic_set(value, label, lambda item: _text(item, label), lambda item: item)


def _sha_set(value: object, label: str) -> list[str]:
    return _semantic_set(value, label, lambda item: _sha(item, label), lambda item: item)


def _exact_mapping(value: object, expected: frozenset[str], label: str) -> Mapping[str, object]:
    item = _mapping(value, label)
    actual = set(item)
    if actual != expected:
        missing = expected - actual
        unknown = actual - expected
        parts: list[str] = []
        if missing:
            parts.append(f"missing fields: {', '.join(sorted(missing))}")
        if unknown:
            parts.append(f"unknown fields: {', '.join(sorted(unknown))}")
        raise ValidationError(f"{label} has invalid fields ({'; '.join(parts)})")
    return item


def _normalize_json(value: object, label: str) -> dict[str, object] | list[object] | str | int | bool | None:
    if isinstance(value, float):
        raise ValidationError("binary floats are forbidden; use a decimal string")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValidationError(f"{label} field names must be strings")
        return {key: _normalize_json(child, label) for key, child in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize_json(child, label) for child in value]
    raise ValidationError(f"{label} contains a non-JSON value")


def _reject_floats(value: object) -> None:
    if isinstance(value, float):
        raise ValidationError("binary floats are forbidden; use a decimal string")
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_floats(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_floats(child)


def _canonical_bytes(document: Mapping[str, object]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], _freeze(value))


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a nonblank string")
    return value.strip()


def _sha(value: object, label: str) -> str:
    digest = _text(value, label)
    if not _SHA256.fullmatch(digest):
        raise ValidationError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _optional_sha(value: object, label: str) -> str | None:
    return None if value is None else _sha(value, label)


def _hf_revision(value: object, label: str) -> str:
    revision = _text(value, label)
    if not _HF_REVISION.fullmatch(revision):
        raise ValidationError(f"{label} must be an immutable lowercase hexadecimal commit")
    return revision


def _url(value: object, label: str) -> str:
    url = _text(value, label)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValidationError(f"{label} must be a credential-free HTTPS URL")
    return url


def _timestamp(value: object, label: str) -> str:
    timestamp = _text(value, label)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValidationError(f"{label} must include a timezone")
    return timestamp


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValidationError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValidationError(f"{label} must be a nonnegative integer")
    return value


def _bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValidationError(f"{label} must be a boolean")
    return value


def _decimal(value: object, label: str, *, between_zero_and_one: bool = False) -> str:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise ValidationError(f"{label} must be a non-exponent decimal string")
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise ValidationError(f"{label} must be a decimal string") from error
    if between_zero_and_one and not Decimal("0") <= decimal <= Decimal("1"):
        raise ValidationError(f"{label} must be between zero and one")
    return value
