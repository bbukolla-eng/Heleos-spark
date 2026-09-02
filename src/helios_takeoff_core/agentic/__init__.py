"""Content-addressed artifacts and strict contracts for P1A workers."""

from .artifacts import ArtifactRecord, ArtifactStore
from .contracts import validate_worker_result

__all__ = ["ArtifactRecord", "ArtifactStore", "validate_worker_result"]
