"""Frozen public contracts for the isolated HELIOS ML registry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Mapping


MODEL_MANIFEST_PROTOCOL = "helios.ml.model-manifest/v1"
DATASET_MANIFEST_PROTOCOL = "helios.ml.dataset-manifest/v1"
BENCHMARK_SPEC_PROTOCOL = "helios.ml.benchmark-spec/v1"
ARTIFACT_RECEIPT_PROTOCOL = "helios.ml.artifact-receipt/v1"
SCAN_RECEIPT_PROTOCOL = "helios.ml.scan-receipt/v1"
BENCHMARK_RECEIPT_PROTOCOL = "helios.ml.benchmark-receipt/v1"
DATASET_INTAKE_RECEIPT_PROTOCOL = "helios.ml.dataset-intake-receipt/v1"
CORE_ACCEPTANCE_PROTOCOL = "helios.ml.core-acceptance/v1"
TARGET_ACCEPTANCE_PROTOCOL = "helios.ml.target-acceptance/v1"

ML_CORE_CODE_ACCEPTED = "ML_CORE_CODE_ACCEPTED"
DATASET_INTAKE_ACCEPTED = "DATASET_INTAKE_ACCEPTED"
DATASET_INTAKE_RESTRICTED = "DATASET_INTAKE_RESTRICTED"
TARGET_MODEL_BENCHMARK_ACCEPTED = "TARGET_MODEL_BENCHMARK_ACCEPTED"
TARGET_MODEL_BENCHMARK_BLOCKED = "TARGET_MODEL_BENCHMARK_BLOCKED"


class PromotionState(StrEnum):
    """Closed lifecycle states for immutable model manifests."""

    QUARANTINED = "QUARANTINED"
    CANDIDATE = "CANDIDATE"
    QUALIFIED = "QUALIFIED"
    PROMOTED = "PROMOTED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class RightsVerdict(StrEnum):
    """Closed finding values for each independently reviewed right."""

    PERMITTED = "PERMITTED"
    PROHIBITED = "PROHIBITED"
    UNKNOWN = "UNKNOWN"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ReceiptStatus(StrEnum):
    """Closed statuses for immutable operational receipts."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"


class DatasetDisposition(StrEnum):
    """Closed intake decisions for immutable datasets."""

    ACCEPTED = "ACCEPTED"
    RESTRICTED = "RESTRICTED"
    REJECTED = "REJECTED"


class DatasetPurpose(StrEnum):
    """Declared, non-interchangeable purposes for a dataset."""

    EVALUATION = "EVALUATION"
    TRAINING = "TRAINING"
    RESEARCH_ONLY = "RESEARCH_ONLY"


@dataclass(frozen=True)
class CompiledModelManifest:
    canonical_document: Mapping[str, object]
    canonical_bytes: bytes
    sha256: str


@dataclass(frozen=True)
class CompiledDatasetManifest:
    canonical_document: Mapping[str, object]
    canonical_bytes: bytes
    sha256: str


@dataclass(frozen=True)
class CompiledBenchmarkSpec:
    canonical_document: Mapping[str, object]
    canonical_bytes: bytes
    sha256: str


@dataclass(frozen=True)
class ArtifactDescriptor:
    logical_name: str
    source_url: str
    sha256: str
    byte_size: int
    media_type: str
    serialization: str


@dataclass(frozen=True)
class ModelExecutionRequest:
    model_manifest_sha256: str
    input_sha256: str
    input_path: Path
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ModelObservation:
    name: str
    value: object
    confidence: str
    model_repository: str
    model_revision: str
    input_sha256: str
    region: Mapping[str, object] | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ObservationBatch:
    observations: tuple[ModelObservation, ...]
    runtime_receipt: Mapping[str, object]
