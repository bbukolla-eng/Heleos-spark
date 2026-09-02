"""Immutable ML registry contracts and canonical document compilers."""

from .canonical import (
    canonicalize_receipt,
    compile_benchmark_spec,
    compile_dataset_manifest,
    compile_model_manifest,
)
from .contracts import (
    ArtifactDescriptor,
    CompiledBenchmarkSpec,
    CompiledDatasetManifest,
    CompiledModelManifest,
    DatasetDisposition,
    DatasetPurpose,
    ModelExecutionRequest,
    ModelObservation,
    ObservationBatch,
    PromotionState,
    ReceiptStatus,
    RightsVerdict,
)

__all__ = [
    "ArtifactDescriptor",
    "CompiledBenchmarkSpec",
    "CompiledDatasetManifest",
    "CompiledModelManifest",
    "DatasetDisposition",
    "DatasetPurpose",
    "ModelExecutionRequest",
    "ModelObservation",
    "ObservationBatch",
    "PromotionState",
    "ReceiptStatus",
    "RightsVerdict",
    "canonicalize_receipt",
    "compile_benchmark_spec",
    "compile_dataset_manifest",
    "compile_model_manifest",
]
