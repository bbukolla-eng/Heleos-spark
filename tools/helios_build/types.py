"""Shared, dependency-free Build Fabric value types."""

from __future__ import annotations

from enum import StrEnum
from typing import TypeAlias


JsonValue: TypeAlias = (
    None | bool | int | str | list["JsonValue"] | dict[str, "JsonValue"]
)


class HandoffTransport(StrEnum):
    LOCAL_ADAPTER = "LOCAL_ADAPTER"
    EXTERNAL_SESSION = "EXTERNAL_SESSION"


class ExternalProvider(StrEnum):
    CODEX = "CODEX"
    CLAUDE = "CLAUDE"
    KIMI = "KIMI"
    GROK = "GROK"
    CURSOR = "CURSOR"
    COPILOT = "COPILOT"


class NodeType(StrEnum):
    RESEARCH_REQUEST = "ResearchRequest"
    SOURCE_PACKET = "SourcePacket"
    INTERFACE_FREEZE = "InterfaceFreeze"
    BUILD_TASK = "BuildTask"
    ATTEMPT = "Attempt"
    CHECKPOINT = "Checkpoint"
    ARTIFACT = "Artifact"
    PATCH = "Patch"
    REVIEW = "Review"
    INTEGRATION = "Integration"
    CAPABILITY = "Capability"


class EdgeType(StrEnum):
    DEPENDS_ON = "DEPENDS_ON"
    PRODUCES = "PRODUCES"
    CONSUMES = "CONSUMES"
    REVIEWS = "REVIEWS"
    TOUCHES_INTERFACE = "TOUCHES_INTERFACE"
    BLOCKS = "BLOCKS"


class NodeState(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    DISPATCHED = "DISPATCHED"
    RETURNED = "RETURNED"
    REVIEWED = "REVIEWED"
    ACCEPTED = "ACCEPTED"
    INTEGRATED = "INTEGRATED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class AttemptOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
