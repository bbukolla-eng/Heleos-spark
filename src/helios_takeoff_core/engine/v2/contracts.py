"""Frozen public contracts for the deterministic Division 23 v2 engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


DOMAIN_PACK_PROTOCOL_V2 = "helios.engine.domain-pack/v2"
OBSERVATION_BUNDLE_PROTOCOL = "helios.engine.observation-bundle/v1"
RESULT_SET_PROTOCOL = "helios.engine.result-set/v1"


class DefinitionKind(str, Enum):
    SYSTEM = "SYSTEM"
    EQUIPMENT = "EQUIPMENT"
    DUCT = "DUCT"
    PIPE = "PIPE"
    FITTING = "FITTING"
    VALVE = "VALVE"
    ACCESSORY = "ACCESSORY"
    MATERIAL = "MATERIAL"
    CONNECTION = "CONNECTION"
    INSULATION = "INSULATION"
    SUPPORT = "SUPPORT"
    LABOR_ASSEMBLY = "LABOR_ASSEMBLY"
    PRICING_INPUT = "PRICING_INPUT"
    RULE_CANDIDATE = "RULE_CANDIDATE"


class DomainFamily(str, Enum):
    SHARED = "SHARED"
    AIRSIDE = "AIRSIDE"
    PIPING = "PIPING"
    EQUIPMENT = "EQUIPMENT"
    INSULATION = "INSULATION"
    SUPPORTS = "SUPPORTS"
    LABOR = "LABOR"
    PRICING = "PRICING"
    CODE_SPEC = "CODE_SPEC"


class RelationKind(str, Enum):
    BELONGS_TO_SYSTEM = "BELONGS_TO_SYSTEM"
    USES_MATERIAL = "USES_MATERIAL"
    USES_CONNECTION = "USES_CONNECTION"
    COMPOSED_OF = "COMPOSED_OF"
    REQUIRES_ACCESSORY = "REQUIRES_ACCESSORY"
    REQUIRES_INSULATION = "REQUIRES_INSULATION"
    REQUIRES_SUPPORT = "REQUIRES_SUPPORT"
    HAS_LABOR_ASSEMBLY = "HAS_LABOR_ASSEMBLY"
    HAS_PRICING_INPUT = "HAS_PRICING_INPUT"
    GOVERNED_BY_RULE = "GOVERNED_BY_RULE"


class ScalarType(str, Enum):
    DECIMAL = "DECIMAL"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"
    TEXT = "TEXT"
    ENUM = "ENUM"


class BundleMode(str, Enum):
    BENCHMARK = "BENCHMARK"
    PROJECT_BOUND = "PROJECT_BOUND"


class WorkState(str, Enum):
    NEW = "NEW"
    DEMOLITION = "DEMOLITION"
    EXISTING = "EXISTING"
    RELOCATE = "RELOCATE"
    REUSE = "REUSE"


class CommercialDimension(str, Enum):
    BASE = "BASE"
    ALTERNATE = "ALTERNATE"
    ALLOWANCE = "ALLOWANCE"
    UNIT_PRICE = "UNIT_PRICE"


class ObservationOrigin(str, Enum):
    HUMAN = "HUMAN"
    STRUCTURED_IMPORT = "STRUCTURED_IMPORT"
    MODEL = "MODEL"


class EvaluationStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class ResultKind(str, Enum):
    QUANTITY = "QUANTITY"
    RECONCILIATION = "RECONCILIATION"
    ASSEMBLY = "ASSEMBLY"


class CalculationBasis(str, Enum):
    MEASURED = "MEASURED"
    RULE_DERIVED = "RULE_DERIVED"
    ALLOWANCE = "ALLOWANCE"


class CandidateAuthority(str, Enum):
    NON_AUTHORITATIVE_CANDIDATE = "NON_AUTHORITATIVE_CANDIDATE"


@dataclass(frozen=True)
class CitationRecord:
    source_ref: str
    locator: str
    applicability: str
    limitations: str


@dataclass(frozen=True)
class SourceRef:
    source_id: str
    source_identity: str
    locator: str
    jurisdiction: str
    applicability: str
    limitations: str


@dataclass(frozen=True)
class DefinitionRecord:
    code: str
    kind: DefinitionKind
    family: DomainFamily
    title: str
    description: str
    attributes: Mapping[str, object]
    citations: tuple[CitationRecord, ...]


@dataclass(frozen=True)
class RelationRecord:
    code: str
    kind: RelationKind
    from_code: str
    to_code: str
    citations: tuple[CitationRecord, ...]


@dataclass(frozen=True)
class CompiledDomainPack:
    canonical_document: Mapping[str, object]
    canonical_bytes: bytes
    sha256: str
    sources_by_id: Mapping[str, Mapping[str, object]]
    definitions_by_code: Mapping[str, DefinitionRecord]
    relations_by_code: Mapping[str, RelationRecord]
    units_by_uom: Mapping[str, Mapping[str, object]]
    observations_by_name: Mapping[str, Mapping[str, object]]
    lookups_by_code: Mapping[str, Mapping[str, object]]
    assembly_edges_by_code: Mapping[str, Mapping[str, object]]
    rules_by_code: Mapping[str, Mapping[str, object]]


@dataclass(frozen=True)
class ProjectContext:
    system: str | None
    floor: str | None
    area: str | None
    phase: str | None
    zone: str | None
    work_state: WorkState
    commercial_dimension: CommercialDimension
    revision_ids: tuple[str, ...]
    addendum_ids: tuple[str, ...]


@dataclass(frozen=True)
class Observation:
    name: str
    origin: ObservationOrigin
    scalar_type: ScalarType
    value: str | int | bool
    uom: str | None
    confidence: str
    evidence_item_ids: tuple[str, ...]
    model_id: str | None
    model_revision: str | None
    input_sha256: str
    region: Mapping[str, object] | None
    revision_ids: tuple[str, ...]


@dataclass(frozen=True)
class ObservationBundle:
    protocol: str
    mode: BundleMode
    pack_sha256: str
    subject_code: str
    context: ProjectContext
    observations: tuple[Observation, ...]
    benchmark_artifact_sha256: str | None
    project_id: str | None
    extraction_run_id: str | None
    subject_kind: str | None
    subject_key: str | None
    evidence_item_ids: tuple[str, ...]
    primary_evidence_item_id: str | None
    sha256: str


@dataclass(frozen=True)
class TypedValue:
    scalar_type: ScalarType
    value: str | int | bool
    uom: str | None


@dataclass(frozen=True)
class FormulaTrace:
    status: EvaluationStatus
    root: Mapping[str, object]
    missing_observations: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class FormulaEvaluation:
    status: EvaluationStatus
    value: TypedValue | None
    trace: FormulaTrace
    observation_refs: tuple[str, ...]
    blocked_reasons: tuple[str, ...]


@dataclass(frozen=True)
class AssemblyNode:
    path: tuple[str, ...]
    item_code: str
    quantity: TypedValue
    source_refs: tuple[SourceRef, ...]
    children: tuple["AssemblyNode", ...]


@dataclass(frozen=True)
class EngineResultLine:
    line_code: str
    kind: ResultKind
    status: EvaluationStatus
    authority: CandidateAuthority
    subject_code: str
    project_id: str | None
    extraction_run_id: str | None
    subject_kind: str | None
    subject_key: str | None
    output_claim_type: str
    calculation_basis: CalculationBasis
    value: TypedValue | None
    bundle_sha256: str
    pack_sha256: str
    rule_code: str
    source_refs: tuple[SourceRef, ...]
    evidence_item_ids: tuple[str, ...]
    primary_evidence_item_id: str | None
    observation_refs: tuple[str, ...]
    confidence: str | None
    blocked_reasons: tuple[str, ...]
    formula_trace: FormulaTrace
    assembly: AssemblyNode | None
    relation_path: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class EngineResultSet:
    protocol: str
    mode: BundleMode
    bundle_sha256: str
    pack_sha256: str
    subject_code: str
    project_id: str | None
    extraction_run_id: str | None
    lines: tuple[EngineResultLine, ...]
    skipped_rules: tuple[str, ...]
    skipped_relations: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class StoreIntegrityReport:
    valid: bool
    errors: tuple[str, ...]
    pack_count: int
    event_count: int
