"""Pure deterministic evaluation and relation-bound assembly resolution."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import fields, is_dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from fractions import Fraction
import hashlib
import json
from types import MappingProxyType
from typing import cast

from ...errors import ValidationError
from .contracts import (
    AssemblyNode,
    BundleMode,
    CalculationBasis,
    CandidateAuthority,
    CompiledDomainPack,
    EngineResultLine,
    EngineResultSet,
    EvaluationStatus,
    FormulaTrace,
    OBSERVATION_BUNDLE_PROTOCOL,
    Observation,
    ObservationBundle,
    RESULT_SET_PROTOCOL,
    ResultKind,
    ScalarType,
    SourceRef,
    TypedValue,
)
from .formulas import FormulaBlocked, canonical_decimal, evaluate_formula, exact_decimal_multiply


_ALLOWED_ASSEMBLY_RELATIONS = frozenset(
    {
        "COMPOSED_OF",
        "USES_MATERIAL",
        "USES_CONNECTION",
        "REQUIRES_ACCESSORY",
        "REQUIRES_INSULATION",
        "REQUIRES_SUPPORT",
        "HAS_LABOR_ASSEMBLY",
        "HAS_PRICING_INPUT",
    }
)


class _AssemblyEvaluationBlocked(Exception):
    def __init__(
        self,
        reasons: tuple[str, ...],
        *,
        missing_observations: tuple[str, ...],
    ) -> None:
        super().__init__(reasons[0] if reasons else "ASSEMBLY_BLOCKED")
        self.reasons = reasons or ("ASSEMBLY_BLOCKED",)
        self.missing_observations = missing_observations


class _AssemblyState:
    def __init__(self, observations: Mapping[str, TypedValue]) -> None:
        self.observations = observations
        self.observation_refs: list[str] = []
        self.trace_steps: list[dict[str, object]] = []
        self.source_refs: set[SourceRef] = set()
        self.skipped_relations: set[str] = set()

    def record_observations(self, names: Iterable[str]) -> None:
        for name in names:
            if name not in self.observation_refs:
                self.observation_refs.append(name)


def evaluate_bundle(
    pack: CompiledDomainPack,
    bundle: ObservationBundle,
) -> EngineResultSet:
    """Evaluate every rule governed by the bundle subject into immutable candidates."""
    _validate_runtime_inputs(pack, bundle)
    observations = _typed_observations(bundle)
    observation_records = {observation.name: observation for observation in bundle.observations}
    lines: list[EngineResultLine] = []
    skipped_rules: set[str] = set()
    skipped_relations: set[str] = set()

    for rule_code in sorted(pack.rules_by_code):
        rule = pack.rules_by_code[rule_code]
        governed_relation_code = _required_text(rule, "relation_code", "rule")
        governed_relation = pack.relations_by_code.get(governed_relation_code)
        if governed_relation is None:
            raise ValidationError("rule references an unresolved governed relation")
        if governed_relation.from_code != bundle.subject_code:
            continue

        result_kind = _enum_value(ResultKind, rule.get("result_kind"), "rule result_kind")
        calculation_basis = _enum_value(
            CalculationBasis,
            rule.get("calculation_basis"),
            "rule calculation_basis",
        )
        output_scalar_type = _enum_value(
            ScalarType,
            rule.get("output_scalar_type"),
            "rule output_scalar_type",
        )
        output_uom = _optional_text(rule.get("output_uom"), "rule output_uom")
        output_dimension = _required_text(rule, "output_dimension", "rule")
        output_claim_type = _required_text(rule, "output_claim_type", "rule")
        source_refs = set(_source_refs(pack, rule.get("source_refs"), "rule source_refs"))
        source_refs.update(_relation_source_refs(pack, governed_relation))
        observation_refs: list[str] = []
        trace_steps: list[dict[str, object]] = []

        condition = rule.get("condition")
        if condition is not None:
            condition_evaluation = evaluate_formula(
                condition,
                expected_scalar_type=ScalarType.BOOLEAN,
                expected_uom=None,
                observations=observations,
                pack=pack,
            )
            _append_unique(observation_refs, condition_evaluation.observation_refs)
            source_refs.update(_trace_source_refs(pack, condition_evaluation.trace.root))
            trace_steps.append(
                {
                    "stage": "RULE_CONDITION",
                    "trace": condition_evaluation.trace.root,
                }
            )
            if condition_evaluation.status is EvaluationStatus.BLOCKED:
                lines.append(
                    _result_line(
                        pack=pack,
                        bundle=bundle,
                        rule_code=rule_code,
                        governed_relation_code=governed_relation_code,
                        result_kind=result_kind,
                        calculation_basis=calculation_basis,
                        output_claim_type=output_claim_type,
                        status=EvaluationStatus.BLOCKED,
                        value=None,
                        source_refs=source_refs,
                        observation_refs=observation_refs,
                        observation_records=observation_records,
                        blocked_reasons=condition_evaluation.blocked_reasons,
                        trace_steps=trace_steps,
                        missing_observations=condition_evaluation.trace.missing_observations,
                        assembly=None,
                    )
                )
                continue
            if condition_evaluation.value != TypedValue(ScalarType.BOOLEAN, True, None):
                skipped_rules.add(rule_code)
                continue

        formula_evaluation = evaluate_formula(
            rule["formula"],
            expected_scalar_type=output_scalar_type,
            expected_uom=output_uom,
            observations=observations,
            pack=pack,
        )
        _append_unique(observation_refs, formula_evaluation.observation_refs)
        source_refs.update(_trace_source_refs(pack, formula_evaluation.trace.root))
        trace_steps.append(
            {
                "stage": "RULE_FORMULA",
                "trace": formula_evaluation.trace.root,
            }
        )
        if formula_evaluation.status is EvaluationStatus.BLOCKED:
            lines.append(
                _result_line(
                    pack=pack,
                    bundle=bundle,
                    rule_code=rule_code,
                    governed_relation_code=governed_relation_code,
                    result_kind=result_kind,
                    calculation_basis=calculation_basis,
                    output_claim_type=output_claim_type,
                    status=EvaluationStatus.BLOCKED,
                    value=None,
                    source_refs=source_refs,
                    observation_refs=observation_refs,
                    observation_records=observation_records,
                    blocked_reasons=formula_evaluation.blocked_reasons,
                    trace_steps=trace_steps,
                    missing_observations=formula_evaluation.trace.missing_observations,
                    assembly=None,
                )
            )
            continue

        value = formula_evaluation.value
        if value is None:
            raise ValidationError("ready formula evaluation must contain a value")
        result_validation_reasons = _quantity_reasons(
            pack,
            value,
            require_nonnegative=result_kind in {ResultKind.QUANTITY, ResultKind.ASSEMBLY},
            dimension=output_dimension,
        )
        if result_validation_reasons:
            trace_steps.append(
                {
                    "stage": "RESULT_QUANTITY_VALIDATION",
                    "blocked_reasons": list(result_validation_reasons),
                }
            )
            lines.append(
                _result_line(
                    pack=pack,
                    bundle=bundle,
                    rule_code=rule_code,
                    governed_relation_code=governed_relation_code,
                    result_kind=result_kind,
                    calculation_basis=calculation_basis,
                    output_claim_type=output_claim_type,
                    status=EvaluationStatus.BLOCKED,
                    value=None,
                    source_refs=source_refs,
                    observation_refs=observation_refs,
                    observation_records=observation_records,
                    blocked_reasons=result_validation_reasons,
                    trace_steps=trace_steps,
                    missing_observations=(),
                    assembly=None,
                )
            )
            continue
        assembly: AssemblyNode | None = None
        blocked_reasons: tuple[str, ...] = ()
        missing_observations: tuple[str, ...] = ()
        status = EvaluationStatus.READY
        if result_kind is ResultKind.ASSEMBLY:
            state = _AssemblyState(observations)
            try:
                assembly = _resolve_assembly(
                    pack,
                    item_code=bundle.subject_code,
                    quantity=value,
                    path=(),
                    item_path=(),
                    state=state,
                    root_source_refs=_definition_source_refs(pack, bundle.subject_code),
                )
            except _AssemblyEvaluationBlocked as error:
                status = EvaluationStatus.BLOCKED
                value = None
                assembly = None
                blocked_reasons = error.reasons
                missing_observations = error.missing_observations
            _append_unique(observation_refs, state.observation_refs)
            trace_steps.extend(state.trace_steps)
            source_refs.update(state.source_refs)
            skipped_relations.update(state.skipped_relations)

        lines.append(
            _result_line(
                pack=pack,
                bundle=bundle,
                rule_code=rule_code,
                governed_relation_code=governed_relation_code,
                result_kind=result_kind,
                calculation_basis=calculation_basis,
                output_claim_type=output_claim_type,
                status=status,
                value=value,
                source_refs=source_refs,
                observation_refs=observation_refs,
                observation_records=observation_records,
                blocked_reasons=blocked_reasons,
                trace_steps=trace_steps,
                missing_observations=missing_observations,
                assembly=assembly,
            )
        )

    ordered_lines = tuple(sorted(lines, key=lambda line: (line.kind.value, line.rule_code, line.line_code)))
    ordered_skipped_rules = tuple(sorted(skipped_rules))
    ordered_skipped_relations = tuple(sorted(skipped_relations))
    result_document: dict[str, object] = {
        "protocol": RESULT_SET_PROTOCOL,
        "mode": bundle.mode.value,
        "bundle_sha256": bundle.sha256,
        "pack_sha256": pack.sha256,
        "subject_code": bundle.subject_code,
        "project_id": bundle.project_id,
        "extraction_run_id": bundle.extraction_run_id,
        "line_sha256s": [line.sha256 for line in ordered_lines],
        "skipped_rules": list(ordered_skipped_rules),
        "skipped_relations": list(ordered_skipped_relations),
    }
    return EngineResultSet(
        protocol=RESULT_SET_PROTOCOL,
        mode=bundle.mode,
        bundle_sha256=bundle.sha256,
        pack_sha256=pack.sha256,
        subject_code=bundle.subject_code,
        project_id=bundle.project_id,
        extraction_run_id=bundle.extraction_run_id,
        lines=ordered_lines,
        skipped_rules=ordered_skipped_rules,
        skipped_relations=ordered_skipped_relations,
        sha256=_sha256(result_document),
    )


def resolve_assembly(
    pack: CompiledDomainPack,
    bundle: ObservationBundle,
    *,
    item_code: str,
    quantity: TypedValue,
) -> AssemblyNode:
    """Resolve one quantity-bearing assembly while retaining every relation path."""
    _validate_runtime_inputs(pack, bundle)
    if item_code not in pack.definitions_by_code:
        raise ValidationError("assembly item references an unresolved definition")
    state = _AssemblyState(_typed_observations(bundle))
    try:
        return _resolve_assembly(
            pack,
            item_code=item_code,
            quantity=quantity,
            path=(),
            item_path=(),
            state=state,
            root_source_refs=_definition_source_refs(pack, item_code),
        )
    except _AssemblyEvaluationBlocked as error:
        raise FormulaBlocked(
            error.reasons[0],
            missing_observations=error.missing_observations,
        ) from error


def _resolve_assembly(
    pack: CompiledDomainPack,
    *,
    item_code: str,
    quantity: TypedValue,
    path: tuple[str, ...],
    item_path: tuple[str, ...],
    state: _AssemblyState,
    root_source_refs: tuple[SourceRef, ...],
) -> AssemblyNode:
    if item_code in item_path:
        raise ValidationError("structural assembly cycle detected")
    root_reasons = _quantity_reasons(
        pack,
        quantity,
        require_nonnegative=True,
        dimension=_quantity_dimension(pack, quantity),
    )
    if root_reasons:
        raise _AssemblyEvaluationBlocked(root_reasons, missing_observations=())
    state.source_refs.update(root_source_refs)
    current_item_path = (*item_path, item_code)
    outgoing: list[tuple[str, Mapping[str, object], object]] = []
    for edge in pack.assembly_edges_by_code.values():
        relation_code = _required_text(edge, "relation_code", "assembly edge")
        relation = pack.relations_by_code.get(relation_code)
        if relation is None:
            raise ValidationError("assembly edge references an unresolved relation")
        if relation.kind.value not in _ALLOWED_ASSEMBLY_RELATIONS:
            raise ValidationError("assembly edge references a non-assembly relation")
        if relation.from_code == item_code:
            outgoing.append((relation_code, edge, relation))
    outgoing.sort(key=lambda item: (item[0], _required_text(item[1], "code", "assembly edge")))

    children: list[AssemblyNode] = []
    for relation_code, edge, relation_object in outgoing:
        relation = cast(object, relation_object)
        child_path = (*path, relation_code)
        edge_source_refs = _source_refs(
            pack, edge.get("source_refs"), "assembly edge source_refs"
        )
        relation_source_refs = _relation_source_refs(pack, relation)
        child_code = cast(str, getattr(relation, "to_code"))
        child_definition_refs = _definition_source_refs(pack, child_code)
        state.source_refs.update(edge_source_refs)
        state.source_refs.update(relation_source_refs)
        state.source_refs.update(child_definition_refs)
        condition = edge.get("condition")
        if condition is not None:
            condition_evaluation = evaluate_formula(
                condition,
                expected_scalar_type=ScalarType.BOOLEAN,
                expected_uom=None,
                observations=state.observations,
                pack=pack,
            )
            state.record_observations(condition_evaluation.observation_refs)
            state.source_refs.update(
                _trace_source_refs(pack, condition_evaluation.trace.root)
            )
            state.trace_steps.append(
                {
                    "stage": "ASSEMBLY_CONDITION",
                    "relation_code": relation_code,
                    "path": list(child_path),
                    "trace": condition_evaluation.trace.root,
                }
            )
            if condition_evaluation.status is EvaluationStatus.BLOCKED:
                raise _AssemblyEvaluationBlocked(
                    condition_evaluation.blocked_reasons,
                    missing_observations=condition_evaluation.trace.missing_observations,
                )
            if condition_evaluation.value != TypedValue(ScalarType.BOOLEAN, True, None):
                state.skipped_relations.add(relation_code)
                continue

        edge_uom = _required_text(edge, "uom", "assembly edge")
        quantity_evaluation = evaluate_formula(
            edge["quantity"],
            expected_scalar_type=ScalarType.DECIMAL,
            expected_uom=edge_uom,
            observations=state.observations,
            pack=pack,
        )
        state.record_observations(quantity_evaluation.observation_refs)
        state.source_refs.update(
            _trace_source_refs(pack, quantity_evaluation.trace.root)
        )
        state.trace_steps.append(
            {
                "stage": "ASSEMBLY_QUANTITY",
                "relation_code": relation_code,
                "path": list(child_path),
                "trace": quantity_evaluation.trace.root,
            }
        )
        if quantity_evaluation.status is EvaluationStatus.BLOCKED:
            raise _AssemblyEvaluationBlocked(
                quantity_evaluation.blocked_reasons,
                missing_observations=quantity_evaluation.trace.missing_observations,
            )
        multiplicity = quantity_evaluation.value
        if multiplicity is None:
            raise ValidationError("ready assembly quantity must contain a value")
        if _numeric_fraction(multiplicity) < 0:
            state.trace_steps.append(
                {
                    "stage": "ASSEMBLY_QUANTITY_VALIDATION",
                    "relation_code": relation_code,
                    "path": list(child_path),
                    "blocked_reasons": ["NEGATIVE_MULTIPLICITY"],
                }
            )
            raise _AssemblyEvaluationBlocked(
                ("NEGATIVE_MULTIPLICITY",),
                missing_observations=(),
            )
        child_value = exact_decimal_multiply(_numeric_text(quantity), _numeric_text(multiplicity))
        if child_value == "0":
            continue
        child_quantity = TypedValue(ScalarType.DECIMAL, child_value, edge_uom)
        child_reasons = _quantity_reasons(
            pack,
            child_quantity,
            require_nonnegative=True,
            dimension=_quantity_dimension(pack, child_quantity),
        )
        if child_reasons:
            state.trace_steps.append(
                {
                    "stage": "ASSEMBLY_QUANTITY_VALIDATION",
                    "relation_code": relation_code,
                    "path": list(child_path),
                    "blocked_reasons": list(child_reasons),
                }
            )
            raise _AssemblyEvaluationBlocked(child_reasons, missing_observations=())
        child = _resolve_assembly(
            pack,
            item_code=child_code,
            quantity=child_quantity,
            path=child_path,
            item_path=current_item_path,
            state=state,
            root_source_refs=child_definition_refs,
        )
        children.append(child)

    return AssemblyNode(
        path=path,
        item_code=item_code,
        quantity=quantity,
        source_refs=tuple(sorted(root_source_refs, key=_source_ref_key)),
        children=tuple(children),
    )


def _result_line(
    *,
    pack: CompiledDomainPack,
    bundle: ObservationBundle,
    rule_code: str,
    governed_relation_code: str,
    result_kind: ResultKind,
    calculation_basis: CalculationBasis,
    output_claim_type: str,
    status: EvaluationStatus,
    value: TypedValue | None,
    source_refs: Iterable[SourceRef],
    observation_refs: Iterable[str],
    observation_records: Mapping[str, Observation],
    blocked_reasons: Iterable[str],
    trace_steps: Iterable[Mapping[str, object]],
    missing_observations: Iterable[str],
    assembly: AssemblyNode | None,
) -> EngineResultLine:
    ordered_observation_refs = tuple(dict.fromkeys(observation_refs))
    ordered_source_refs = tuple(sorted(set(source_refs), key=_source_ref_key))
    ordered_blocked_reasons = tuple(dict.fromkeys(blocked_reasons))
    trace = _combined_trace(
        rule_code=rule_code,
        status=status,
        steps=tuple(trace_steps),
        missing_observations=tuple(sorted(set(missing_observations))),
    )
    evidence_item_ids = _line_evidence(bundle, ordered_observation_refs, observation_records)
    primary_evidence_item_id = (
        bundle.primary_evidence_item_id
        if bundle.primary_evidence_item_id in evidence_item_ids
        else None
    )
    line_code = rule_code
    relation_path = (governed_relation_code,)
    line_document: dict[str, object] = {
        "line_code": line_code,
        "kind": result_kind.value,
        "status": status.value,
        "authority": CandidateAuthority.NON_AUTHORITATIVE_CANDIDATE.value,
        "subject_code": bundle.subject_code,
        "project_id": bundle.project_id,
        "extraction_run_id": bundle.extraction_run_id,
        "subject_kind": bundle.subject_kind,
        "subject_key": bundle.subject_key,
        "output_claim_type": output_claim_type,
        "calculation_basis": calculation_basis.value,
        "value": _json_ready(value),
        "bundle_sha256": bundle.sha256,
        "pack_sha256": pack.sha256,
        "rule_code": rule_code,
        "source_refs": _json_ready(ordered_source_refs),
        "evidence_item_ids": list(evidence_item_ids),
        "primary_evidence_item_id": primary_evidence_item_id,
        "observation_refs": list(ordered_observation_refs),
        "confidence": _minimum_confidence(ordered_observation_refs, observation_records),
        "blocked_reasons": list(ordered_blocked_reasons),
        "formula_trace": _json_ready(trace),
        "assembly": _json_ready(assembly),
        "relation_path": list(relation_path),
    }
    return EngineResultLine(
        line_code=line_code,
        kind=result_kind,
        status=status,
        authority=CandidateAuthority.NON_AUTHORITATIVE_CANDIDATE,
        subject_code=bundle.subject_code,
        project_id=bundle.project_id,
        extraction_run_id=bundle.extraction_run_id,
        subject_kind=bundle.subject_kind,
        subject_key=bundle.subject_key,
        output_claim_type=output_claim_type,
        calculation_basis=calculation_basis,
        value=value,
        bundle_sha256=bundle.sha256,
        pack_sha256=pack.sha256,
        rule_code=rule_code,
        source_refs=ordered_source_refs,
        evidence_item_ids=evidence_item_ids,
        primary_evidence_item_id=primary_evidence_item_id,
        observation_refs=ordered_observation_refs,
        confidence=_minimum_confidence(ordered_observation_refs, observation_records),
        blocked_reasons=ordered_blocked_reasons,
        formula_trace=trace,
        assembly=assembly,
        relation_path=relation_path,
        sha256=_sha256(line_document),
    )


def _combined_trace(
    *,
    rule_code: str,
    status: EvaluationStatus,
    steps: tuple[Mapping[str, object], ...],
    missing_observations: tuple[str, ...],
) -> FormulaTrace:
    root_document: dict[str, object] = {
        "op": "RULE_EVALUATION",
        "rule_code": rule_code,
        "steps": list(steps),
    }
    trace_document: dict[str, object] = {
        "status": status.value,
        "root": root_document,
        "missing_observations": list(missing_observations),
    }
    frozen_root = cast(Mapping[str, object], _freeze(root_document))
    return FormulaTrace(
        status=status,
        root=frozen_root,
        missing_observations=missing_observations,
        sha256=_sha256(trace_document),
    )


def _validate_runtime_inputs(pack: CompiledDomainPack, bundle: ObservationBundle) -> None:
    if not isinstance(pack, CompiledDomainPack) or not isinstance(pack.canonical_bytes, bytes):
        raise ValidationError("pack must be a compiled v2 domain pack")
    if not isinstance(bundle, ObservationBundle):
        raise ValidationError("bundle must be a compiled observation bundle")
    if bundle.protocol != OBSERVATION_BUNDLE_PROTOCOL:
        raise ValidationError("observation bundle protocol is unsupported")
    try:
        canonical_document = json.loads(
            pack.canonical_bytes.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
        from .compiler import compile_domain_pack, compile_observation_bundle

        rebound_pack = compile_domain_pack(canonical_document)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValidationError("compiled pack canonical bytes are invalid") from error
    if (
        rebound_pack.canonical_bytes != pack.canonical_bytes
        or rebound_pack.sha256 != pack.sha256
        or _json_ready(pack.canonical_document) != canonical_document
        or any(
            getattr(pack, field_name) != getattr(rebound_pack, field_name)
            for field_name in (
                "sources_by_id",
                "definitions_by_code",
                "relations_by_code",
                "units_by_uom",
                "observations_by_name",
                "lookups_by_code",
                "assembly_edges_by_code",
                "rules_by_code",
            )
        )
    ):
        raise ValidationError("compiled pack projections do not match canonical bytes")
    if bundle.mode is BundleMode.PROJECT_BOUND:
        raise ValidationError("PROJECT_BOUND observation bundles are not implemented in B2")
    if bundle.mode is not BundleMode.BENCHMARK:
        raise ValidationError("observation bundle mode is unsupported")
    if bundle.pack_sha256 != pack.sha256:
        raise ValidationError("observation bundle pack_sha256 does not match the compiled pack")
    if bundle.subject_code not in pack.definitions_by_code:
        raise ValidationError("observation bundle subject_code is unresolved")
    bundle_document: dict[str, object] = {
        "protocol": bundle.protocol,
        "mode": bundle.mode.value,
        "pack_sha256": bundle.pack_sha256,
        "subject_code": bundle.subject_code,
        "context": _json_ready(bundle.context),
        "observations": _json_ready(bundle.observations),
        "benchmark_artifact_sha256": bundle.benchmark_artifact_sha256,
    }
    rebound_bundle = compile_observation_bundle(bundle_document, pack=rebound_pack)
    if rebound_bundle != bundle:
        raise ValidationError("observation bundle projections do not match its canonical digest")


def _typed_observations(bundle: ObservationBundle) -> Mapping[str, TypedValue]:
    result: dict[str, TypedValue] = {}
    for observation in bundle.observations:
        if observation.name in result:
            raise ValidationError("observation bundle contains a duplicate observation")
        result[observation.name] = TypedValue(
            observation.scalar_type,
            observation.value,
            observation.uom,
        )
    return MappingProxyType(result)


def _definition_source_refs(pack: CompiledDomainPack, item_code: str) -> tuple[SourceRef, ...]:
    definition = pack.definitions_by_code.get(item_code)
    if definition is None:
        raise ValidationError("assembly item references an unresolved definition")
    entries = (
        {
            "source_ref": citation.source_ref,
            "locator": citation.locator,
            "applicability": citation.applicability,
            "limitations": citation.limitations,
        }
        for citation in definition.citations
    )
    return _source_refs(pack, tuple(entries), "definition citations")


def _relation_source_refs(pack: CompiledDomainPack, relation: object) -> tuple[SourceRef, ...]:
    citations = getattr(relation, "citations", None)
    if not isinstance(citations, tuple):
        raise ValidationError("governed relation citations are invalid")
    entries = tuple(
        {
            "source_ref": citation.source_ref,
            "locator": citation.locator,
            "applicability": citation.applicability,
            "limitations": citation.limitations,
        }
        for citation in citations
    )
    return _source_refs(pack, entries, "governed relation citations")


def _trace_source_refs(
    pack: CompiledDomainPack, trace: Mapping[str, object] | None,
) -> tuple[SourceRef, ...]:
    lookup_codes: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            if value.get("op") == "LOOKUP" and isinstance(value.get("lookup_code"), str):
                lookup_codes.add(cast(str, value["lookup_code"]))
            for child in value.values():
                visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                visit(child)

    visit(trace)
    result: set[SourceRef] = set()
    for code in sorted(lookup_codes):
        lookup = pack.lookups_by_code.get(code)
        if lookup is None:
            raise ValidationError("formula trace references an unresolved lookup")
        result.update(_source_refs(pack, lookup.get("source_refs"), "lookup source refs"))
    return tuple(sorted(result, key=_source_ref_key))


def _source_refs(
    pack: CompiledDomainPack,
    value: object,
    context: str,
) -> tuple[SourceRef, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValidationError(f"{context} must be an array")
    result: list[SourceRef] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            raise ValidationError(f"{context} entries must be objects")
        if "source_id" in entry:
            result.append(
                SourceRef(
                    source_id=_mapping_text(entry, "source_id", context),
                    source_identity=_mapping_text(entry, "source_identity", context),
                    locator=_mapping_text(entry, "locator", context),
                    jurisdiction=_mapping_text(entry, "jurisdiction", context),
                    applicability=_mapping_text(entry, "applicability", context),
                    limitations=_mapping_text(entry, "limitations", context),
                )
            )
            continue
        source_id = _mapping_text(entry, "source_ref", context)
        source = pack.sources_by_id.get(source_id)
        if source is None:
            raise ValidationError(f"{context} references an unresolved source")
        content_sha256 = source.get("content_sha256")
        source_identity = (
            f"sha256:{content_sha256}"
            if isinstance(content_sha256, str)
            else f"reference:{_mapping_text(source, 'stable_reference', context)}"
        )
        result.append(
            SourceRef(
                source_id=source_id,
                source_identity=source_identity,
                locator=_mapping_text(entry, "locator", context),
                jurisdiction=_mapping_text(source, "jurisdiction", context),
                applicability=_mapping_text(entry, "applicability", context),
                limitations=_mapping_text(entry, "limitations", context),
            )
        )
    if not result:
        raise ValidationError(f"{context} must contain at least one source")
    return tuple(sorted(set(result), key=_source_ref_key))


def _line_evidence(
    bundle: ObservationBundle,
    observation_refs: tuple[str, ...],
    observation_records: Mapping[str, Observation],
) -> tuple[str, ...]:
    if bundle.mode is BundleMode.BENCHMARK:
        return ()
    evidence: set[str] = set()
    for name in observation_refs:
        observation = observation_records.get(name)
        if observation is not None:
            evidence.update(observation.evidence_item_ids)
    return tuple(sorted(evidence))


def _minimum_confidence(
    observation_refs: tuple[str, ...],
    observation_records: Mapping[str, Observation],
) -> str | None:
    available = [
        observation_records[name].confidence
        for name in observation_refs
        if name in observation_records
    ]
    if not available:
        return None
    try:
        return min(available, key=Decimal)
    except InvalidOperation as error:
        raise ValidationError("observation confidence must be an exact decimal") from error


def _quantity_reasons(
    pack: CompiledDomainPack,
    value: TypedValue,
    *,
    require_nonnegative: bool,
    dimension: str,
) -> tuple[str, ...]:
    numeric = _numeric_fraction(value)
    reasons: list[str] = []
    if require_nonnegative and numeric < 0:
        reasons.append("NEGATIVE_QUANTITY")
    if dimension == "COUNT" and numeric.denominator != 1:
        reasons.append("NON_INTEGRAL_COUNT")
    return tuple(reasons)


def _quantity_dimension(pack: CompiledDomainPack, value: TypedValue) -> str:
    if value.uom is None:
        return "DIMENSIONLESS"
    unit = pack.units_by_uom.get(value.uom)
    if unit is None:
        raise ValidationError("quantity references an unresolved unit")
    dimension = unit.get("dimension")
    if not isinstance(dimension, str):
        raise ValidationError("quantity unit dimension is invalid")
    return dimension


def _numeric_fraction(value: TypedValue) -> Fraction:
    return Fraction(Decimal(_numeric_text(value)))


def _numeric_text(value: TypedValue) -> str:
    if value.scalar_type is ScalarType.INTEGER and isinstance(value.value, int) and not isinstance(value.value, bool):
        return str(value.value)
    if value.scalar_type is ScalarType.DECIMAL and isinstance(value.value, str):
        return canonical_decimal(value.value, "quantity value")
    raise ValidationError("assembly quantity must be DECIMAL or INTEGER")


def _source_ref_key(value: SourceRef) -> tuple[str, str, str, str, str, str]:
    return (
        value.source_id,
        value.source_identity,
        value.locator,
        value.jurisdiction,
        value.applicability,
        value.limitations,
    )


def _append_unique(target: list[str], values: Iterable[str]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def _required_text(value: Mapping[str, object], key: str, context: str) -> str:
    return _mapping_text(value, key, context)


def _mapping_text(value: Mapping[str, object], key: str, context: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValidationError(f"{context} {key} must be non-blank text")
    return item


def _optional_text(value: object, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{context} must be null or non-blank text")
    return value


def _enum_value(enum_type: type[Enum], value: object, context: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        raise ValidationError(f"{context} is unsupported") from error


def _sha256(value: object) -> str:
    canonical_bytes = json.dumps(
        _json_ready(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def _json_ready(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        raise ValidationError("engine results must not contain a float")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _json_ready(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    raise ValidationError("engine result contains a non-canonical value")


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value
