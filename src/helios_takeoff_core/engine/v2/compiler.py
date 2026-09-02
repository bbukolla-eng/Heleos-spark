"""Strict canonical compiler for non-executable Division 23 v2 registry packs."""

from __future__ import annotations

from datetime import date
from fractions import Fraction
import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping, cast

from ...errors import ValidationError
from .contracts import (
    CalculationBasis,
    BundleMode,
    CommercialDimension,
    CitationRecord,
    CompiledDomainPack,
    DefinitionKind,
    DefinitionRecord,
    DOMAIN_PACK_PROTOCOL_V2,
    DomainFamily,
    Observation,
    ObservationBundle,
    ObservationOrigin,
    OBSERVATION_BUNDLE_PROTOCOL,
    ProjectContext,
    RelationKind,
    RelationRecord,
    ResultKind,
    ScalarType,
    WorkState,
)


_TOP_LEVEL_FIELDS = {
    "protocol", "pack_code", "version", "title", "jurisdiction",
    "valid_from", "valid_through", "supersedes_pack_sha256",
    "sources", "definitions", "relations",
    "units", "observations", "lookups", "assembly_edges", "rules",
}
_SOURCE_REQUIRED_FIELDS = {
    "source_id", "authority_class", "stable_reference", "publisher", "title",
    "retrieved_on", "jurisdiction",
}
_SOURCE_FIELDS = _SOURCE_REQUIRED_FIELDS | {"content_sha256"}
_DEFINITION_FIELDS = {"code", "kind", "family", "title", "description", "attributes", "citations"}
_RELATION_FIELDS = {"code", "kind", "from_code", "to_code", "citations"}
_CITATION_FIELDS = {"source_ref", "locator", "applicability", "limitations"}
_EXECUTABLE_FIELDS = ("units", "observations", "lookups", "assembly_edges", "rules")
_UNIT_FIELDS = {"uom", "dimension", "base_uom", "to_base_numerator", "to_base_denominator"}
_OBSERVATION_FIELDS = {
    "name", "scalar_type", "dimension", "canonical_uom", "accepted_uoms",
    "enum_values", "bounds", "evidence_cardinality",
}
_LOOKUP_FIELDS = {"code", "key_types", "value_type", "dimension", "uom", "entries", "source_refs"}
_LOOKUP_ENTRY_FIELDS = {"keys", "value"}
_EDGE_FIELDS = {"code", "relation_code", "quantity", "uom", "condition", "source_refs"}
_RULE_FIELDS = {
    "code", "relation_code", "result_kind", "calculation_basis", "output_claim_type",
    "output_scalar_type", "output_dimension", "output_uom", "condition", "formula", "source_refs",
}
_SOURCE_REF_FIELDS = {
    "source_id", "source_identity", "locator", "jurisdiction", "applicability", "limitations",
}
_DIMENSIONS = {
    "DIMENSIONLESS", "COUNT", "LENGTH", "AREA", "MASS", "TIME", "CURRENCY",
    "MASS_PER_AREA", "TIME_PER_LENGTH", "CURRENCY_PER_LENGTH", "COUNT_PER_LENGTH",
}
_ASSEMBLY_RELATIONS = {
    RelationKind.COMPOSED_OF, RelationKind.USES_MATERIAL, RelationKind.USES_CONNECTION,
    RelationKind.REQUIRES_ACCESSORY, RelationKind.REQUIRES_INSULATION,
    RelationKind.REQUIRES_SUPPORT, RelationKind.HAS_LABOR_ASSEMBLY,
    RelationKind.HAS_PRICING_INPUT,
}
_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")
_TOKEN_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ATTRIBUTE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_BENCHMARK_BUNDLE_FIELDS = {
    "protocol", "mode", "pack_sha256", "subject_code", "context", "observations",
    "benchmark_artifact_sha256",
}
_CONTEXT_FIELDS = {
    "system", "floor", "area", "phase", "zone", "work_state",
    "commercial_dimension", "revision_ids", "addendum_ids",
}
_OBSERVATION_VALUE_FIELDS = {
    "name", "origin", "scalar_type", "value", "uom", "confidence",
    "evidence_item_ids", "model_id", "model_revision", "input_sha256", "region", "revision_ids",
}

_PHYSICAL_KINDS = frozenset(
    {
        DefinitionKind.SYSTEM, DefinitionKind.EQUIPMENT, DefinitionKind.DUCT,
        DefinitionKind.PIPE, DefinitionKind.FITTING, DefinitionKind.VALVE,
        DefinitionKind.ACCESSORY,
    }
)
_RELATION_RANGES: dict[RelationKind, tuple[frozenset[DefinitionKind], frozenset[DefinitionKind]]] = {
    RelationKind.BELONGS_TO_SYSTEM: (
        _PHYSICAL_KINDS - {DefinitionKind.SYSTEM}, frozenset({DefinitionKind.SYSTEM})
    ),
    RelationKind.USES_MATERIAL: (
        _PHYSICAL_KINDS | {DefinitionKind.INSULATION, DefinitionKind.SUPPORT},
        frozenset({DefinitionKind.MATERIAL}),
    ),
    RelationKind.USES_CONNECTION: (
        _PHYSICAL_KINDS, frozenset({DefinitionKind.CONNECTION})
    ),
    RelationKind.COMPOSED_OF: (
        frozenset({DefinitionKind.SYSTEM, DefinitionKind.EQUIPMENT, DefinitionKind.DUCT, DefinitionKind.PIPE}),
        _PHYSICAL_KINDS - {DefinitionKind.SYSTEM},
    ),
    RelationKind.REQUIRES_ACCESSORY: (
        _PHYSICAL_KINDS, frozenset({DefinitionKind.ACCESSORY})
    ),
    RelationKind.REQUIRES_INSULATION: (
        _PHYSICAL_KINDS, frozenset({DefinitionKind.INSULATION})
    ),
    RelationKind.REQUIRES_SUPPORT: (
        _PHYSICAL_KINDS, frozenset({DefinitionKind.SUPPORT})
    ),
    RelationKind.HAS_LABOR_ASSEMBLY: (
        _PHYSICAL_KINDS | {DefinitionKind.INSULATION, DefinitionKind.SUPPORT},
        frozenset({DefinitionKind.LABOR_ASSEMBLY}),
    ),
    RelationKind.HAS_PRICING_INPUT: (
        _PHYSICAL_KINDS | {DefinitionKind.INSULATION, DefinitionKind.SUPPORT, DefinitionKind.LABOR_ASSEMBLY},
        frozenset({DefinitionKind.PRICING_INPUT}),
    ),
    RelationKind.GOVERNED_BY_RULE: (
        frozenset(DefinitionKind) - {DefinitionKind.RULE_CANDIDATE},
        frozenset({DefinitionKind.RULE_CANDIDATE}),
    ),
}


def compile_domain_pack(document: object) -> CompiledDomainPack:
    """Validate, normalize, deep-freeze, and hash one exact v2 semantic pack."""
    if not isinstance(document, dict):
        raise ValidationError("domain pack must be an object")
    _require_exact_fields(document, _TOP_LEVEL_FIELDS, "domain pack")
    _reject_floats(document)
    if document["protocol"] != DOMAIN_PACK_PROTOCOL_V2:
        raise ValidationError("domain pack protocol is unsupported")

    pack_code = _code(document["pack_code"], "pack_code")
    version = _text(document["version"], "version")
    title = _text(document["title"], "title")
    jurisdiction = _text(document["jurisdiction"], "jurisdiction")
    valid_from = _date_text(document["valid_from"], "valid_from")
    valid_through = _optional_date_text(document["valid_through"], "valid_through")
    if valid_through is not None and valid_through < valid_from:
        raise ValidationError("domain pack validity interval is invalid")
    supersedes = _optional_sha256(document["supersedes_pack_sha256"], "supersedes_pack_sha256")

    for field in _EXECUTABLE_FIELDS:
        if not isinstance(document[field], list):
            raise ValidationError(f"{field} must be an array")

    sources = _parse_sources(document["sources"])
    source_ids = {source["source_id"] for source in sources}
    definitions, definition_records = _parse_definitions(document["definitions"], source_ids)
    definition_kinds = {record.code: record.kind for record in definition_records}
    relations, relation_records = _parse_relations(document["relations"], source_ids, definition_kinds)
    relation_by_code = {record.code: record for record in relation_records}
    source_by_id = {str(source["source_id"]): source for source in sources}
    units = _parse_units(document["units"])
    unit_uoms = {str(unit["uom"]) for unit in units}
    observations = _parse_observations(document["observations"], units)
    observation_names = {str(item["name"]) for item in observations}
    lookups = _parse_lookups(document["lookups"], units, source_by_id)
    lookup_codes = {str(item["code"]) for item in lookups}
    assembly_edges = _parse_assembly_edges(
        document["assembly_edges"], unit_uoms, source_by_id, relation_by_code,
        observation_names, lookup_codes,
    )
    rules = _parse_rules(
        document["rules"], unit_uoms, source_by_id, relation_by_code,
        definition_kinds, observation_names, lookup_codes,
    )
    _validate_pack_formula_budget(assembly_edges, rules)
    _validate_executable_types(units, observations, lookups, assembly_edges, rules)
    _reject_assembly_cycles(assembly_edges, relation_by_code)

    canonical_document: dict[str, object] = {
        "protocol": DOMAIN_PACK_PROTOCOL_V2,
        "pack_code": pack_code,
        "version": version,
        "title": title,
        "jurisdiction": jurisdiction,
        "valid_from": valid_from,
        "valid_through": valid_through,
        "supersedes_pack_sha256": supersedes,
        "sources": sources,
        "definitions": definitions,
        "relations": relations,
        "units": units,
        "observations": observations,
        "lookups": lookups,
        "assembly_edges": assembly_edges,
        "rules": rules,
    }
    canonical_bytes = _canonical_bytes(canonical_document)
    frozen_document = cast(Mapping[str, object], _freeze(canonical_document))
    frozen_sources = cast(tuple[Mapping[str, object], ...], frozen_document["sources"])

    frozen_definition_records = tuple(_freeze_definition(record) for record in definition_records)
    frozen_relation_records = tuple(_freeze_relation(record) for record in relation_records)
    frozen_units = cast(tuple[Mapping[str, object], ...], frozen_document["units"])
    frozen_observations = cast(tuple[Mapping[str, object], ...], frozen_document["observations"])
    frozen_lookups = cast(tuple[Mapping[str, object], ...], frozen_document["lookups"])
    frozen_edges = cast(tuple[Mapping[str, object], ...], frozen_document["assembly_edges"])
    frozen_rules = cast(tuple[Mapping[str, object], ...], frozen_document["rules"])
    return CompiledDomainPack(
        canonical_document=frozen_document,
        canonical_bytes=canonical_bytes,
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        sources_by_id=MappingProxyType({str(source["source_id"]): source for source in frozen_sources}),
        definitions_by_code=MappingProxyType({record.code: record for record in frozen_definition_records}),
        relations_by_code=MappingProxyType({record.code: record for record in frozen_relation_records}),
        units_by_uom=MappingProxyType({str(item["uom"]): item for item in frozen_units}),
        observations_by_name=MappingProxyType({str(item["name"]): item for item in frozen_observations}),
        lookups_by_code=MappingProxyType({str(item["code"]): item for item in frozen_lookups}),
        assembly_edges_by_code=MappingProxyType({str(item["code"]): item for item in frozen_edges}),
        rules_by_code=MappingProxyType({str(item["code"]): item for item in frozen_rules}),
    )


def compile_observation_bundle(
    document: object, *, pack: CompiledDomainPack,
) -> ObservationBundle:
    """Validate, canonicalize, and hash one B2 benchmark observation bundle."""
    if not isinstance(document, dict):
        raise ValidationError("observation bundle must be an object")
    _reject_floats(document)
    if document.get("protocol") != OBSERVATION_BUNDLE_PROTOCOL:
        raise ValidationError("observation bundle protocol is unsupported")
    try:
        mode = BundleMode(document.get("mode"))
    except (TypeError, ValueError) as error:
        raise ValidationError("observation bundle mode is unsupported") from error
    if mode is BundleMode.PROJECT_BOUND:
        raise ValidationError("PROJECT_BOUND observation bundles are not implemented in B2")
    _require_exact_fields(document, _BENCHMARK_BUNDLE_FIELDS, "benchmark observation bundle")
    if not isinstance(pack, CompiledDomainPack):
        raise ValidationError("pack must be a compiled v2 domain pack")
    pack_sha256 = _required_sha256(document["pack_sha256"], "bundle pack_sha256")
    if pack_sha256 != pack.sha256:
        raise ValidationError("observation bundle pack_sha256 does not match pack")
    subject_code = _code(document["subject_code"], "bundle subject_code")
    if subject_code not in pack.definitions_by_code:
        raise ValidationError("observation bundle subject is unresolved")
    context_document, context = _parse_context(document["context"])
    observations_document, observations = _parse_bundle_observations(document["observations"], pack)
    benchmark_sha = _required_sha256(
        document["benchmark_artifact_sha256"], "benchmark artifact sha256",
    )
    canonical_document: dict[str, object] = {
        "protocol": OBSERVATION_BUNDLE_PROTOCOL,
        "mode": BundleMode.BENCHMARK.value,
        "pack_sha256": pack_sha256,
        "subject_code": subject_code,
        "context": context_document,
        "observations": observations_document,
        "benchmark_artifact_sha256": benchmark_sha,
    }
    canonical_bytes = _canonical_bytes(canonical_document)
    return ObservationBundle(
        protocol=OBSERVATION_BUNDLE_PROTOCOL,
        mode=BundleMode.BENCHMARK,
        pack_sha256=pack_sha256,
        subject_code=subject_code,
        context=context,
        observations=observations,
        benchmark_artifact_sha256=benchmark_sha,
        project_id=None,
        extraction_run_id=None,
        subject_kind=None,
        subject_key=None,
        evidence_item_ids=(),
        primary_evidence_item_id=None,
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
    )


def _parse_context(value: object) -> tuple[dict[str, object], ProjectContext]:
    if not isinstance(value, dict):
        raise ValidationError("observation context must be an object")
    _require_exact_fields(value, _CONTEXT_FIELDS, "observation context")
    optional_fields = {
        field: _optional_text(value[field], f"context {field}")
        for field in ("system", "floor", "area", "phase", "zone")
    }
    try:
        work_state = WorkState(_text(value["work_state"], "context work_state"))
        commercial_dimension = CommercialDimension(
            _text(value["commercial_dimension"], "context commercial_dimension")
        )
    except ValueError as error:
        raise ValidationError("context work state or commercial dimension is unsupported") from error
    revision_ids = _string_array(value["revision_ids"], "context revision_ids")
    addendum_ids = _string_array(value["addendum_ids"], "context addendum_ids")
    normalized: dict[str, object] = {
        **optional_fields,
        "work_state": work_state.value,
        "commercial_dimension": commercial_dimension.value,
        "revision_ids": list(revision_ids),
        "addendum_ids": list(addendum_ids),
    }
    return normalized, ProjectContext(
        system=optional_fields["system"], floor=optional_fields["floor"],
        area=optional_fields["area"], phase=optional_fields["phase"],
        zone=optional_fields["zone"], work_state=work_state,
        commercial_dimension=commercial_dimension,
        revision_ids=revision_ids, addendum_ids=addendum_ids,
    )


def _parse_bundle_observations(
    value: object, pack: CompiledDomainPack,
) -> tuple[list[dict[str, object]], tuple[Observation, ...]]:
    if not isinstance(value, list):
        raise ValidationError("bundle observations must be an array")
    documents: list[dict[str, object]] = []
    records: list[Observation] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"observation {index} must be an object")
        _require_exact_fields(entry, _OBSERVATION_VALUE_FIELDS, f"observation {index}")
        name = _token(entry["name"], "observation name")
        if name in seen:
            raise ValidationError("observation bundle contains duplicate observations")
        seen.add(name)
        definition = pack.observations_by_name.get(name)
        if definition is None:
            raise ValidationError("observation bundle references an unresolved observation")
        scalar_type = _scalar_type(entry["scalar_type"], "observation scalar_type")
        if scalar_type.value != definition["scalar_type"]:
            raise ValidationError("observation scalar type does not match definition")
        normalized_value = _scalar_value(entry["value"], scalar_type, "observation value")
        uom = _optional_uom(entry["uom"], set(pack.units_by_uom), "observation uom")
        accepted = cast(tuple[object, ...], definition["accepted_uoms"])
        if (uom is None) != (definition["canonical_uom"] is None) or (uom is not None and uom not in accepted):
            raise ValidationError("observation unit is not accepted by its definition")
        if scalar_type is ScalarType.ENUM and normalized_value not in definition["enum_values"]:
            raise ValidationError("observation enum value is not allowed")
        _validate_observation_bounds(
            normalized_value,
            scalar_type,
            definition["bounds"],
            value_uom=uom,
            canonical_uom=cast(str | None, definition["canonical_uom"]),
            units=pack.units_by_uom,
        )
        try:
            origin = ObservationOrigin(_text(entry["origin"], "observation origin"))
        except ValueError as error:
            raise ValidationError("observation origin is unsupported") from error
        confidence = _decimal_text(entry["confidence"], "observation confidence")
        confidence_numerator, confidence_denominator = _decimal_fraction(confidence)
        if confidence_numerator < 0 or confidence_numerator > confidence_denominator:
            raise ValidationError("observation confidence must be between zero and one")
        evidence_ids = _string_array(entry["evidence_item_ids"], "observation evidence ids")
        if evidence_ids:
            raise ValidationError("benchmark observations cannot claim project evidence")
        model_id = _optional_text(entry["model_id"], "observation model_id")
        model_revision = _optional_text(entry["model_revision"], "observation model_revision")
        if origin is ObservationOrigin.MODEL:
            if model_id is None or model_revision is None:
                raise ValidationError("MODEL observation requires model identity")
        elif model_id is not None or model_revision is not None:
            raise ValidationError("non-MODEL observation cannot declare model identity")
        input_sha = _required_sha256(entry["input_sha256"], "observation input_sha256")
        region = _parse_region(entry["region"])
        revision_ids = _string_array(entry["revision_ids"], "observation revision_ids")
        normalized: dict[str, object] = {
            "name": name, "origin": origin.value, "scalar_type": scalar_type.value,
            "value": normalized_value, "uom": uom, "confidence": confidence,
            "evidence_item_ids": list(evidence_ids), "model_id": model_id,
            "model_revision": model_revision, "input_sha256": input_sha,
            "region": None if region is None else _materialize_frozen(region),
            "revision_ids": list(revision_ids),
        }
        documents.append(normalized)
        records.append(Observation(
            name=name, origin=origin, scalar_type=scalar_type, value=cast(str | int | bool, normalized_value),
            uom=uom, confidence=confidence, evidence_item_ids=evidence_ids,
            model_id=model_id, model_revision=model_revision, input_sha256=input_sha,
            region=region, revision_ids=revision_ids,
        ))
    paired = sorted(zip(documents, records, strict=True), key=lambda item: item[1].name)
    return [item[0] for item in paired], tuple(item[1] for item in paired)


def _validate_observation_bounds(
    value: object,
    scalar_type: ScalarType,
    bounds: object,
    *,
    value_uom: str | None,
    canonical_uom: str | None,
    units: Mapping[str, Mapping[str, object]],
) -> None:
    if bounds is None:
        return
    if scalar_type not in {ScalarType.DECIMAL, ScalarType.INTEGER} or not isinstance(bounds, Mapping):
        raise ValidationError("observation definition bounds are invalid")
    value_fraction = Fraction(*_decimal_fraction(str(value)))
    if value_uom is not None or canonical_uom is not None:
        if value_uom is None or canonical_uom is None:
            raise ValidationError("numeric observation unit shape is incompatible")
        source_unit = units.get(value_uom)
        canonical_unit = units.get(canonical_uom)
        if source_unit is None or canonical_unit is None:
            raise ValidationError("observation bounds reference an unresolved unit")
        if (
            source_unit.get("dimension") != canonical_unit.get("dimension")
            or source_unit.get("base_uom") != canonical_unit.get("base_uom")
        ):
            raise ValidationError("observation accepted unit is not convertible to its canonical unit")
        source_factor = Fraction(
            int(cast(str, source_unit["to_base_numerator"])),
            int(cast(str, source_unit["to_base_denominator"])),
        )
        canonical_factor = Fraction(
            int(cast(str, canonical_unit["to_base_numerator"])),
            int(cast(str, canonical_unit["to_base_denominator"])),
        )
        value_fraction = value_fraction * source_factor / canonical_factor
    minimum = bounds.get("minimum")
    maximum = bounds.get("maximum")
    if isinstance(minimum, str):
        if value_fraction < Fraction(*_decimal_fraction(minimum)):
            raise ValidationError("observation value is below its minimum")
    if isinstance(maximum, str):
        if value_fraction > Fraction(*_decimal_fraction(maximum)):
            raise ValidationError("observation value is above its maximum")


def _parse_region(value: object) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidationError("observation region must be an object or null")
    return cast(Mapping[str, object], _freeze(_canonical_value(value, "observation region")))


def _materialize_frozen(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _materialize_frozen(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_materialize_frozen(item) for item in value]
    return value


def _parse_units(value: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    if not isinstance(value, list):
        raise ValidationError("units must be an array")
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"unit {index} must be an object")
        _require_exact_fields(entry, _UNIT_FIELDS, f"unit {index}")
        uom = _code(entry["uom"], "unit uom")
        if uom in seen:
            raise ValidationError("domain pack contains duplicate unit identity")
        seen.add(uom)
        dimension = _token(entry["dimension"], "unit dimension")
        if dimension not in _DIMENSIONS:
            raise ValidationError("unit dimension is unsupported")
        numerator = _positive_integer_text(entry["to_base_numerator"], "unit numerator")
        denominator = _positive_integer_text(entry["to_base_denominator"], "unit denominator")
        result.append({
            "uom": uom, "dimension": dimension,
            "base_uom": _code(entry["base_uom"], "unit base_uom"),
            "to_base_numerator": numerator, "to_base_denominator": denominator,
        })
    result.sort(key=lambda item: str(item["uom"]))
    by_uom = {str(item["uom"]): item for item in result}
    base_by_dimension: dict[str, str] = {}
    for item in result:
        base = by_uom.get(str(item["base_uom"]))
        if base is None or base["dimension"] != item["dimension"]:
            raise ValidationError("unit references an incompatible base unit")
        if base["base_uom"] != base["uom"] or base["to_base_numerator"] != "1" or base["to_base_denominator"] != "1":
            raise ValidationError("unit base must be self-referential with factor one")
        dimension = str(item["dimension"])
        base_uom = str(base["uom"])
        established_base = base_by_dimension.setdefault(dimension, base_uom)
        if established_base != base_uom:
            raise ValidationError("each unit dimension must use one coherent base unit")
    return result


def _parse_observations(
    value: object, units: list[dict[str, object]],
) -> list[dict[str, object]]:
    unit_uoms = {str(unit["uom"]) for unit in units}
    unit_dimensions = {
        str(unit["uom"]): str(unit["dimension"])
        for unit in units
    }
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    if not isinstance(value, list):
        raise ValidationError("observations must be an array")
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"observation definition {index} must be an object")
        _require_exact_fields(entry, _OBSERVATION_FIELDS, f"observation definition {index}")
        name = _token(entry["name"], "observation name")
        if name in seen:
            raise ValidationError("domain pack contains duplicate observation identity")
        seen.add(name)
        scalar_type = _scalar_type(entry["scalar_type"], "observation scalar_type")
        dimension = _token(entry["dimension"], "observation dimension")
        if dimension not in _DIMENSIONS:
            raise ValidationError("observation dimension is unsupported")
        canonical_uom = _optional_uom(entry["canonical_uom"], unit_uoms, "observation canonical_uom")
        accepted_raw = entry["accepted_uoms"]
        if not isinstance(accepted_raw, list):
            raise ValidationError("observation accepted_uoms must be an array")
        accepted = sorted({_code(item, "observation accepted uom") for item in accepted_raw})
        if len(accepted) != len(accepted_raw) or any(item not in unit_uoms for item in accepted):
            raise ValidationError("observation accepted_uoms are invalid")
        if canonical_uom is not None and canonical_uom not in accepted:
            raise ValidationError("observation canonical_uom must be accepted")
        if scalar_type in {ScalarType.DECIMAL, ScalarType.INTEGER} and canonical_uom is None:
            raise ValidationError("numeric observation requires a canonical unit")
        if scalar_type in {ScalarType.DECIMAL, ScalarType.INTEGER}:
            if any(unit_dimensions.get(uom) != dimension for uom in accepted):
                raise ValidationError("observation accepted_uoms must match its dimension")
        elif canonical_uom is not None or accepted or dimension != "DIMENSIONLESS":
            raise ValidationError("non-numeric observation must be unitless and dimensionless")
        enums_raw = entry["enum_values"]
        if not isinstance(enums_raw, list):
            raise ValidationError("observation enum_values must be an array")
        enums = sorted({_text(item, "observation enum value") for item in enums_raw})
        if len(enums) != len(enums_raw) or (scalar_type is ScalarType.ENUM) != bool(enums):
            raise ValidationError("observation enum_values do not match scalar_type")
        bounds = _parse_bounds(entry["bounds"], scalar_type)
        result.append({
            "name": name, "scalar_type": scalar_type.value, "dimension": dimension,
            "canonical_uom": canonical_uom, "accepted_uoms": accepted,
            "enum_values": enums, "bounds": bounds,
            "evidence_cardinality": _token(entry["evidence_cardinality"], "evidence cardinality"),
        })
    return sorted(result, key=lambda item: str(item["name"]))


def _parse_bounds(value: object, scalar_type: ScalarType) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidationError("observation bounds must be an object or null")
    _require_exact_fields(value, {"minimum", "maximum"}, "observation bounds")
    if scalar_type not in {ScalarType.DECIMAL, ScalarType.INTEGER}:
        raise ValidationError("only numeric observations can declare bounds")
    minimum = _optional_decimal(value["minimum"], "observation minimum")
    maximum = _optional_decimal(value["maximum"], "observation maximum")
    if minimum is not None and maximum is not None:
        minimum_numerator, minimum_denominator = _decimal_fraction(minimum)
        maximum_numerator, maximum_denominator = _decimal_fraction(maximum)
        if minimum_numerator * maximum_denominator > maximum_numerator * minimum_denominator:
            raise ValidationError("observation bounds are inverted")
    return {"minimum": minimum, "maximum": maximum}


def _parse_lookups(
    value: object,
    units: list[dict[str, object]],
    source_by_id: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    unit_uoms = {str(unit["uom"]) for unit in units}
    if not isinstance(value, list):
        raise ValidationError("lookups must be an array")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"lookup {index} must be an object")
        _require_exact_fields(entry, _LOOKUP_FIELDS, f"lookup {index}")
        code = _code(entry["code"], "lookup code")
        if code in seen:
            raise ValidationError("domain pack contains duplicate lookup identity")
        seen.add(code)
        key_types_raw = entry["key_types"]
        if not isinstance(key_types_raw, list) or not key_types_raw:
            raise ValidationError("lookup key_types must be a non-empty array")
        key_types = [_scalar_type(item, "lookup key type") for item in key_types_raw]
        value_type = _scalar_type(entry["value_type"], "lookup value_type")
        dimension = _token(entry["dimension"], "lookup dimension")
        if dimension not in _DIMENSIONS:
            raise ValidationError("lookup dimension is unsupported")
        uom = _optional_uom(entry["uom"], unit_uoms, "lookup uom")
        if value_type in {ScalarType.DECIMAL, ScalarType.INTEGER} and uom is None:
            raise ValidationError("numeric lookup requires a unit")
        if value_type not in {ScalarType.DECIMAL, ScalarType.INTEGER} and (
            uom is not None or dimension != "DIMENSIONLESS"
        ):
            raise ValidationError("non-numeric lookup output must be unitless and dimensionless")
        entries_raw = entry["entries"]
        if not isinstance(entries_raw, list) or not entries_raw:
            raise ValidationError("lookup entries must be a non-empty array")
        entries: list[dict[str, object]] = []
        keys_seen: set[tuple[object, ...]] = set()
        for entry_index, lookup_entry in enumerate(entries_raw):
            if not isinstance(lookup_entry, dict):
                raise ValidationError(f"lookup entry {entry_index} must be an object")
            _require_exact_fields(lookup_entry, _LOOKUP_ENTRY_FIELDS, f"lookup entry {entry_index}")
            raw_keys = lookup_entry["keys"]
            if not isinstance(raw_keys, list) or len(raw_keys) != len(key_types):
                raise ValidationError("lookup entry keys do not match key_types")
            keys = tuple(_scalar_value(item, scalar, "lookup key") for item, scalar in zip(raw_keys, key_types, strict=True))
            if keys in keys_seen:
                raise ValidationError("lookup contains duplicate key tuple")
            keys_seen.add(keys)
            entries.append({"keys": list(keys), "value": _scalar_value(lookup_entry["value"], value_type, "lookup value")})
        entries.sort(key=lambda item: _canonical_bytes(item["keys"]))
        result.append({
            "code": code, "key_types": [item.value for item in key_types],
            "value_type": value_type.value, "dimension": dimension, "uom": uom,
            "entries": entries, "source_refs": _parse_source_refs(entry["source_refs"], source_by_id),
        })
    return sorted(result, key=lambda item: str(item["code"]))


def _parse_assembly_edges(
    value: object,
    unit_uoms: set[str],
    source_by_id: Mapping[str, Mapping[str, object]],
    relations: Mapping[str, RelationRecord],
    observation_names: set[str],
    lookup_codes: set[str],
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValidationError("assembly_edges must be an array")
    result: list[dict[str, object]] = []
    seen_codes: set[str] = set()
    seen_relations: set[str] = set()
    counter = [0]
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"assembly edge {index} must be an object")
        _require_exact_fields(entry, _EDGE_FIELDS, f"assembly edge {index}")
        code = _code(entry["code"], "assembly edge code")
        relation_code = _code(entry["relation_code"], "assembly relation code")
        relation = relations.get(relation_code)
        if relation is None:
            raise ValidationError("assembly edge references an unresolved relation")
        if relation.kind not in _ASSEMBLY_RELATIONS:
            raise ValidationError("assembly relation kind is not allowed")
        if code in seen_codes or relation_code in seen_relations:
            raise ValidationError("assembly edge identity or relation is duplicated")
        seen_codes.add(code)
        seen_relations.add(relation_code)
        uom = _optional_uom(entry["uom"], unit_uoms, "assembly edge uom")
        if uom is None:
            raise ValidationError("assembly edge requires an output unit")
        condition = None if entry["condition"] is None else _parse_formula(
            entry["condition"], observation_names, lookup_codes, counter=counter,
        )
        result.append({
            "code": code, "relation_code": relation_code,
            "quantity": _parse_formula(entry["quantity"], observation_names, lookup_codes, counter=counter),
            "uom": uom,
            "condition": condition,
            "source_refs": _parse_source_refs(entry["source_refs"], source_by_id),
        })
    return sorted(result, key=lambda item: str(item["code"]))


def _parse_rules(
    value: object,
    unit_uoms: set[str],
    source_by_id: Mapping[str, Mapping[str, object]],
    relations: Mapping[str, RelationRecord],
    definition_kinds: Mapping[str, DefinitionKind],
    observation_names: set[str],
    lookup_codes: set[str],
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValidationError("rules must be an array")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    governed_relations: set[str] = set()
    counter = [0]
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"rule {index} must be an object")
        _require_exact_fields(entry, _RULE_FIELDS, f"rule {index}")
        code = _code(entry["code"], "rule code")
        relation_code = _code(entry["relation_code"], "rule relation_code")
        relation = relations.get(relation_code)
        if relation is None:
            raise ValidationError("rule references an unresolved relation")
        if (
            relation.kind is not RelationKind.GOVERNED_BY_RULE
            or relation.to_code != code
            or definition_kinds.get(code) is not DefinitionKind.RULE_CANDIDATE
        ):
            raise ValidationError("rule must bind exactly one governed relation")
        if sum(
            candidate.kind is RelationKind.GOVERNED_BY_RULE and candidate.to_code == code
            for candidate in relations.values()
        ) != 1:
            raise ValidationError("rule must resolve to exactly one governed relation")
        if code in seen or relation_code in governed_relations:
            raise ValidationError("rule identity or governed relation is duplicated")
        seen.add(code)
        governed_relations.add(relation_code)
        try:
            result_kind = ResultKind(_text(entry["result_kind"], "rule result_kind"))
            basis = CalculationBasis(_text(entry["calculation_basis"], "rule calculation_basis"))
        except ValueError as error:
            raise ValidationError("rule result kind or calculation basis is unsupported") from error
        scalar_type = _scalar_type(entry["output_scalar_type"], "rule output_scalar_type")
        dimension = _token(entry["output_dimension"], "rule output_dimension")
        if dimension not in _DIMENSIONS:
            raise ValidationError("rule output dimension is unsupported")
        output_uom = _optional_uom(entry["output_uom"], unit_uoms, "rule output_uom")
        if scalar_type in {ScalarType.DECIMAL, ScalarType.INTEGER} and output_uom is None:
            raise ValidationError("numeric rule output requires a unit")
        if scalar_type not in {ScalarType.DECIMAL, ScalarType.INTEGER} and output_uom is not None:
            raise ValidationError("non-numeric rule output cannot declare a unit")
        if scalar_type not in {ScalarType.DECIMAL, ScalarType.INTEGER} and dimension != "DIMENSIONLESS":
            raise ValidationError("non-numeric rule output must be dimensionless")
        if result_kind in {ResultKind.QUANTITY, ResultKind.ASSEMBLY} and (
            scalar_type not in {ScalarType.DECIMAL, ScalarType.INTEGER}
            or output_uom is None
        ):
            raise ValidationError("quantity and assembly rules require numeric unit-bearing outputs")
        condition = None if entry["condition"] is None else _parse_formula(
            entry["condition"], observation_names, lookup_codes, counter=counter,
        )
        result.append({
            "code": code, "relation_code": relation_code, "result_kind": result_kind.value,
            "calculation_basis": basis.value,
            "output_claim_type": _token(entry["output_claim_type"], "rule output_claim_type"),
            "output_scalar_type": scalar_type.value, "output_dimension": dimension,
            "output_uom": output_uom, "condition": condition,
            "formula": _parse_formula(entry["formula"], observation_names, lookup_codes, counter=counter),
            "source_refs": _parse_source_refs(entry["source_refs"], source_by_id),
        })
    return sorted(result, key=lambda item: str(item["code"]))


def _parse_source_refs(
    value: object, sources: Mapping[str, Mapping[str, object]],
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValidationError("executable assertion must contain source_refs")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"source ref {index} must be an object")
        _require_exact_fields(entry, _SOURCE_REF_FIELDS, f"source ref {index}")
        source_id = _code(entry["source_id"], "source ref source_id")
        source = sources.get(source_id)
        if source is None:
            raise ValidationError("source ref references an unresolved source")
        content_sha = source["content_sha256"]
        expected_identity = (
            f"sha256:{content_sha}" if content_sha is not None
            else f"reference:{source['stable_reference']}"
        )
        identity = (
            source_id, _text(entry["source_identity"], "source identity"),
            _text(entry["locator"], "source locator"),
            _text(entry["jurisdiction"], "source jurisdiction"),
            _text(entry["applicability"], "source applicability"),
            _text(entry["limitations"], "source limitations"),
        )
        if identity[1] != expected_identity:
            raise ValidationError("source ref identity does not match source")
        if identity in seen:
            raise ValidationError("executable assertion contains duplicate source ref")
        seen.add(identity)
        result.append({
            "source_id": identity[0], "source_identity": identity[1], "locator": identity[2],
            "jurisdiction": identity[3], "applicability": identity[4], "limitations": identity[5],
        })
    return sorted(result, key=lambda item: tuple(item[key] for key in (
        "source_id", "source_identity", "locator", "jurisdiction", "applicability", "limitations",
    )))


def _parse_formula(
    value: object,
    observation_names: set[str],
    lookup_codes: set[str],
    *,
    counter: list[int],
    depth: int = 1,
    tree_counter: list[int] | None = None,
) -> dict[str, object]:
    if tree_counter is None:
        tree_counter = [0]
    if depth > 32:
        raise ValidationError("formula exceeds maximum depth")
    counter[0] += 1
    tree_counter[0] += 1
    if counter[0] > 4096:
        raise ValidationError("pack exceeds maximum formula nodes")
    if tree_counter[0] > 256:
        raise ValidationError("formula exceeds maximum nodes")
    if not isinstance(value, dict) or "op" not in value:
        raise ValidationError("formula node must be a tagged object")
    op = _token(value["op"], "formula op")
    if op == "LITERAL":
        _require_exact_fields(value, {"op", "value_type", "value"}, "LITERAL formula")
        scalar = _scalar_type(value["value_type"], "literal value_type")
        return {"op": op, "value_type": scalar.value, "value": _scalar_value(value["value"], scalar, "literal value")}
    if op == "OBSERVATION":
        _require_exact_fields(value, {"op", "name"}, "OBSERVATION formula")
        name = _token(value["name"], "formula observation name")
        if name not in observation_names:
            raise ValidationError("formula references an unresolved observation")
        return {"op": op, "name": name}
    if op == "LOOKUP":
        _require_exact_fields(value, {"op", "lookup_code", "keys"}, "LOOKUP formula")
        code = _code(value["lookup_code"], "formula lookup_code")
        if code not in lookup_codes:
            raise ValidationError("formula references an unresolved lookup")
        keys = value["keys"]
        if not isinstance(keys, list) or not keys:
            raise ValidationError("LOOKUP keys must be a non-empty array")
        return {"op": op, "lookup_code": code, "keys": [
            _parse_formula(item, observation_names, lookup_codes, counter=counter, depth=depth + 1, tree_counter=tree_counter)
            for item in keys
        ]}
    if op in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"}:
        _require_exact_fields(value, {"op", "left", "right"}, f"{op} formula")
        return {
            "op": op,
            "left": _parse_formula(value["left"], observation_names, lookup_codes, counter=counter, depth=depth + 1, tree_counter=tree_counter),
            "right": _parse_formula(value["right"], observation_names, lookup_codes, counter=counter, depth=depth + 1, tree_counter=tree_counter),
        }
    if op == "COMPARE":
        _require_exact_fields(value, {"op", "operator", "left", "right"}, "COMPARE formula")
        operator = _token(value["operator"], "compare operator")
        if operator not in {"EQ", "NE", "LT", "LE", "GT", "GE"}:
            raise ValidationError("compare operator is unsupported")
        return {
            "op": op, "operator": operator,
            "left": _parse_formula(value["left"], observation_names, lookup_codes, counter=counter, depth=depth + 1, tree_counter=tree_counter),
            "right": _parse_formula(value["right"], observation_names, lookup_codes, counter=counter, depth=depth + 1, tree_counter=tree_counter),
        }
    if op == "IF":
        _require_exact_fields(value, {"op", "condition", "then", "else"}, "IF formula")
        return {"op": op, **{
            key: _parse_formula(value[key], observation_names, lookup_codes, counter=counter, depth=depth + 1, tree_counter=tree_counter)
            for key in ("condition", "then", "else")
        }}
    if op in {"MIN", "MAX"}:
        _require_exact_fields(value, {"op", "args"}, f"{op} formula")
        args = value["args"]
        if not isinstance(args, list) or not args:
            raise ValidationError(f"{op} args must be a non-empty array")
        return {"op": op, "args": [
            _parse_formula(item, observation_names, lookup_codes, counter=counter, depth=depth + 1, tree_counter=tree_counter)
            for item in args
        ]}
    raise ValidationError("formula operation is unsupported")


def _validate_executable_types(
    units: list[dict[str, object]],
    observations: list[dict[str, object]],
    lookups: list[dict[str, object]],
    edges: list[dict[str, object]],
    rules: list[dict[str, object]],
) -> None:
    unit_dimensions = {str(item["uom"]): str(item["dimension"]) for item in units}
    observation_types = {
        str(item["name"]): (ScalarType(str(item["scalar_type"])), str(item["dimension"]))
        for item in observations
    }
    lookup_types = {
        str(item["code"]): (
            tuple(ScalarType(str(key)) for key in cast(list[object], item["key_types"])),
            ScalarType(str(item["value_type"])),
            str(item["dimension"]),
        )
        for item in lookups
    }
    for item in observations:
        uom = item["canonical_uom"]
        if uom is not None and unit_dimensions.get(str(uom)) != item["dimension"]:
            raise ValidationError("observation unit dimension is incompatible")
    for item in lookups:
        uom = item["uom"]
        if uom is not None and unit_dimensions.get(str(uom)) != item["dimension"]:
            raise ValidationError("lookup unit dimension is incompatible")
    for edge in edges:
        scalar, dimension = _infer_formula_type(
            cast(Mapping[str, object], edge["quantity"]), observation_types, lookup_types,
        )
        if scalar not in {ScalarType.DECIMAL, ScalarType.INTEGER}:
            raise ValidationError("assembly edge quantity must be numeric")
        edge_uom = cast(str, edge["uom"])
        expected_dimension = unit_dimensions[edge_uom]
        edge_formula = cast(Mapping[str, object], edge["quantity"])
        if dimension != expected_dimension and not (
            dimension == "DIMENSIONLESS" and edge_formula.get("op") == "LITERAL"
        ):
            raise ValidationError("assembly edge quantity dimension does not match its output unit")
        condition = edge["condition"]
        if condition is not None and _infer_formula_type(
            cast(Mapping[str, object], condition), observation_types, lookup_types,
        ) != (ScalarType.BOOLEAN, "DIMENSIONLESS"):
            raise ValidationError("assembly edge condition must be Boolean")
    for rule in rules:
        condition = rule["condition"]
        if condition is not None and _infer_formula_type(
            cast(Mapping[str, object], condition), observation_types, lookup_types,
        ) != (ScalarType.BOOLEAN, "DIMENSIONLESS"):
            raise ValidationError("rule condition must be Boolean")
        actual = _infer_formula_type(
            cast(Mapping[str, object], rule["formula"]), observation_types, lookup_types,
        )
        expected = (ScalarType(str(rule["output_scalar_type"])), str(rule["output_dimension"]))
        numeric = {ScalarType.DECIMAL, ScalarType.INTEGER}
        scalar_compatible = actual[0] is expected[0] or (
            actual[0] in numeric and expected[0] in numeric
        )
        rule_formula = cast(Mapping[str, object], rule["formula"])
        dimension_compatible = actual[1] == expected[1] or (
            actual[1] == "DIMENSIONLESS"
            and rule_formula.get("op") == "LITERAL"
            and actual[0] in numeric
        )
        if not scalar_compatible or not dimension_compatible:
            raise ValidationError("rule formula type or dimension does not match its output")
        uom = rule["output_uom"]
        if uom is not None and unit_dimensions.get(str(uom)) != expected[1]:
            raise ValidationError("rule output unit dimension is incompatible")


def _validate_pack_formula_budget(
    edges: list[dict[str, object]], rules: list[dict[str, object]],
) -> None:
    roots: list[object] = []
    for edge in edges:
        roots.append(edge["quantity"])
        if edge["condition"] is not None:
            roots.append(edge["condition"])
    for rule in rules:
        roots.append(rule["formula"])
        if rule["condition"] is not None:
            roots.append(rule["condition"])

    def count(node: object) -> int:
        if isinstance(node, Mapping):
            return (1 if "op" in node else 0) + sum(
                count(value) for key, value in node.items() if key != "op"
            )
        if isinstance(node, (list, tuple)):
            return sum(count(value) for value in node)
        return 0

    if sum(count(root) for root in roots) > 4096:
        raise ValidationError("pack exceeds maximum formula nodes")


def _infer_formula_type(
    node: Mapping[str, object],
    observations: Mapping[str, tuple[ScalarType, str]],
    lookups: Mapping[str, tuple[tuple[ScalarType, ...], ScalarType, str]],
) -> tuple[ScalarType, str]:
    op = str(node["op"])
    if op == "LITERAL":
        return ScalarType(str(node["value_type"])), "DIMENSIONLESS"
    if op == "OBSERVATION":
        return observations[str(node["name"])]
    if op == "LOOKUP":
        key_types, value_type, dimension = lookups[str(node["lookup_code"])]
        keys = cast(tuple[Mapping[str, object], ...], node["keys"])
        if len(keys) != len(key_types):
            raise ValidationError("lookup formula key count is invalid")
        for expression, expected in zip(keys, key_types, strict=True):
            scalar, dimension_name = _infer_formula_type(expression, observations, lookups)
            if scalar is not expected or dimension_name != "DIMENSIONLESS":
                raise ValidationError("lookup formula key type is invalid")
        return value_type, dimension
    if op in {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "COMPARE"}:
        left = _infer_formula_type(cast(Mapping[str, object], node["left"]), observations, lookups)
        right = _infer_formula_type(cast(Mapping[str, object], node["right"]), observations, lookups)
        if op == "COMPARE":
            numeric = {ScalarType.DECIMAL, ScalarType.INTEGER}
            if left[1] != right[1] or not (
                left[0] is right[0] or (left[0] in numeric and right[0] in numeric)
            ):
                raise ValidationError("comparison operands are incompatible")
            if node["operator"] not in {"EQ", "NE"} and left[0] is ScalarType.BOOLEAN:
                raise ValidationError("ordered comparison does not accept Boolean operands")
            return ScalarType.BOOLEAN, "DIMENSIONLESS"
        if left[0] not in {ScalarType.DECIMAL, ScalarType.INTEGER} or right[0] not in {ScalarType.DECIMAL, ScalarType.INTEGER}:
            raise ValidationError("arithmetic operands must be numeric")
        scalar = (
            ScalarType.DECIMAL
            if op == "DIVIDE" or ScalarType.DECIMAL in {left[0], right[0]}
            else ScalarType.INTEGER
        )
        if op in {"ADD", "SUBTRACT"}:
            if left[1] != right[1]:
                raise ValidationError("additive dimensions are incompatible")
            return scalar, left[1]
        if op == "MULTIPLY":
            return scalar, _multiply_dimension(left[1], right[1])
        return scalar, _divide_dimension(left[1], right[1])
    if op == "IF":
        condition = _infer_formula_type(cast(Mapping[str, object], node["condition"]), observations, lookups)
        then_type = _infer_formula_type(cast(Mapping[str, object], node["then"]), observations, lookups)
        else_type = _infer_formula_type(cast(Mapping[str, object], node["else"]), observations, lookups)
        if condition != (ScalarType.BOOLEAN, "DIMENSIONLESS") or then_type != else_type:
            raise ValidationError("IF branches or condition are incompatible")
        return then_type
    if op in {"MIN", "MAX"}:
        values = [
            _infer_formula_type(item, observations, lookups)
            for item in cast(tuple[Mapping[str, object], ...], node["args"])
        ]
        numeric = {ScalarType.DECIMAL, ScalarType.INTEGER}
        if (
            not values
            or any(item[0] not in numeric or item[1] != values[0][1] for item in values)
        ):
            raise ValidationError(f"{op} operands are incompatible")
        return (
            ScalarType.DECIMAL
            if any(item[0] is ScalarType.DECIMAL for item in values)
            else ScalarType.INTEGER,
            values[0][1],
        )
    raise ValidationError("formula operation is unsupported")


def _multiply_dimension(left: str, right: str) -> str:
    if left == "DIMENSIONLESS":
        return right
    if right == "DIMENSIONLESS":
        return left
    pair = frozenset((left, right))
    products = {
        frozenset(("LENGTH",)): "AREA",
        frozenset(("AREA", "MASS_PER_AREA")): "MASS",
        frozenset(("LENGTH", "TIME_PER_LENGTH")): "TIME",
        frozenset(("LENGTH", "CURRENCY_PER_LENGTH")): "CURRENCY",
        frozenset(("LENGTH", "COUNT_PER_LENGTH")): "COUNT",
    }
    result = products.get(pair)
    if result is None:
        raise ValidationError("multiplicative dimensions are incompatible")
    return result


def _divide_dimension(left: str, right: str) -> str:
    if right == "DIMENSIONLESS":
        return left
    if left == right:
        return "DIMENSIONLESS"
    quotients = {
        ("AREA", "LENGTH"): "LENGTH",
        ("MASS", "AREA"): "MASS_PER_AREA",
        ("TIME", "LENGTH"): "TIME_PER_LENGTH",
        ("CURRENCY", "LENGTH"): "CURRENCY_PER_LENGTH",
        ("COUNT", "LENGTH"): "COUNT_PER_LENGTH",
    }
    result = quotients.get((left, right))
    if result is None:
        raise ValidationError("divisive dimensions are incompatible")
    return result


def _reject_assembly_cycles(
    edges: list[dict[str, object]], relations: Mapping[str, RelationRecord],
) -> None:
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        relation = relations[str(edge["relation_code"])]
        adjacency.setdefault(relation.from_code, []).append(relation.to_code)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(code: str) -> None:
        if code in visiting:
            raise ValidationError("assembly graph contains a cycle")
        if code in visited:
            return
        visiting.add(code)
        for child in sorted(adjacency.get(code, ())):
            visit(child)
        visiting.remove(code)
        visited.add(code)

    for code in sorted(adjacency):
        visit(code)


def _scalar_type(value: object, context: str) -> ScalarType:
    try:
        return ScalarType(_text(value, context))
    except ValueError as error:
        raise ValidationError(f"{context} is unsupported") from error


def _scalar_value(value: object, scalar_type: ScalarType, context: str) -> object:
    if scalar_type is ScalarType.DECIMAL:
        return _decimal_text(value, context)
    if scalar_type is ScalarType.INTEGER:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError(f"{context} must be an integer")
        return value
    if scalar_type is ScalarType.BOOLEAN:
        if not isinstance(value, bool):
            raise ValidationError(f"{context} must be a boolean")
        return value
    return _text(value, context)


def _optional_uom(value: object, unit_uoms: set[str], context: str) -> str | None:
    if value is None:
        return None
    result = _code(value, context)
    if result not in unit_uoms:
        raise ValidationError(f"{context} references an unresolved unit")
    return result


def _positive_integer_text(value: object, context: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise ValidationError(f"{context} must be a positive canonical integer string")
    return value


def _decimal_text(value: object, context: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) is None:
        raise ValidationError(f"{context} must be a plain decimal string")
    negative = value.startswith("-")
    body = value[1:] if negative else value
    if "." in body:
        body = body.rstrip("0").rstrip(".")
    if body == "0":
        return "0"
    return ("-" if negative else "") + body


def _optional_decimal(value: object, context: str) -> str | None:
    return None if value is None else _decimal_text(value, context)


def _decimal_fraction(value: str) -> tuple[int, int]:
    negative = value.startswith("-")
    body = value[1:] if negative else value
    whole, _, fraction = body.partition(".")
    denominator = 10 ** len(fraction)
    numerator = int(whole) * denominator + (int(fraction) if fraction else 0)
    return (-numerator if negative else numerator, denominator)


def _parse_sources(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or not value:
        raise ValidationError("domain pack sources must contain at least one source")
    result: list[dict[str, object]] = []
    source_ids: set[str] = set()
    references: set[str] = set()
    content_digests: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"source {index} must be an object")
        if not _SOURCE_REQUIRED_FIELDS.issubset(entry) or set(entry) - _SOURCE_FIELDS:
            raise ValidationError(f"source {index} fields are invalid")
        source_id = _code(entry["source_id"], "source_id")
        stable_reference = _text(entry["stable_reference"], "stable_reference")
        content_sha256 = _optional_sha256(entry.get("content_sha256"), "content_sha256")
        if source_id in source_ids or stable_reference in references:
            raise ValidationError("domain pack contains duplicate source identity")
        if content_sha256 is not None and content_sha256 in content_digests:
            raise ValidationError("domain pack contains duplicate source content identity")
        source_ids.add(source_id)
        references.add(stable_reference)
        if content_sha256 is not None:
            content_digests.add(content_sha256)
        normalized: dict[str, object] = {
            "source_id": source_id,
            "authority_class": _token(entry["authority_class"], "authority_class"),
            "stable_reference": stable_reference,
            "publisher": _text(entry["publisher"], "publisher"),
            "title": _text(entry["title"], "source title"),
            "retrieved_on": _date_text(entry["retrieved_on"], "retrieved_on"),
            "jurisdiction": _text(entry["jurisdiction"], "source jurisdiction"),
            "content_sha256": content_sha256,
        }
        result.append(normalized)
    return sorted(result, key=lambda source: str(source["source_id"]))


def _parse_definitions(
    value: object, source_ids: set[object]
) -> tuple[list[dict[str, object]], tuple[DefinitionRecord, ...]]:
    if not isinstance(value, list) or not value:
        raise ValidationError("domain pack definitions must contain at least one definition")
    documents: list[dict[str, object]] = []
    records: list[DefinitionRecord] = []
    codes: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"definition {index} must be an object")
        _require_exact_fields(entry, _DEFINITION_FIELDS, f"definition {index}")
        code = _code(entry["code"], "definition code")
        if code in codes:
            raise ValidationError("domain pack contains duplicate definition identity")
        codes.add(code)
        try:
            kind = DefinitionKind(_text(entry["kind"], "definition kind"))
            family = DomainFamily(_text(entry["family"], "definition family"))
        except ValueError as error:
            raise ValidationError("definition kind or family is unsupported") from error
        attributes = _attributes(entry["attributes"])
        citation_documents, citations = _parse_citations(entry["citations"], source_ids)
        title = _text(entry["title"], "definition title")
        description = _text(entry["description"], "definition description")
        documents.append(
            {
                "code": code, "kind": kind.value, "family": family.value,
                "title": title, "description": description,
                "attributes": attributes, "citations": citation_documents,
            }
        )
        records.append(DefinitionRecord(code, kind, family, title, description, attributes, citations))
    paired = sorted(zip(documents, records, strict=True), key=lambda pair: pair[1].code)
    return [pair[0] for pair in paired], tuple(pair[1] for pair in paired)


def _parse_relations(
    value: object,
    source_ids: set[object],
    definition_kinds: Mapping[str, DefinitionKind],
) -> tuple[list[dict[str, object]], tuple[RelationRecord, ...]]:
    if not isinstance(value, list):
        raise ValidationError("domain pack relations must be an array")
    documents: list[dict[str, object]] = []
    records: list[RelationRecord] = []
    codes: set[str] = set()
    identities: set[tuple[RelationKind, str, str]] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"relation {index} must be an object")
        _require_exact_fields(entry, _RELATION_FIELDS, f"relation {index}")
        code = _code(entry["code"], "relation code")
        if code in codes:
            raise ValidationError("domain pack contains duplicate relation code")
        codes.add(code)
        try:
            kind = RelationKind(_text(entry["kind"], "relation kind"))
        except ValueError as error:
            raise ValidationError("relation kind is unsupported") from error
        from_code = _code(entry["from_code"], "relation from_code")
        to_code = _code(entry["to_code"], "relation to_code")
        if from_code not in definition_kinds or to_code not in definition_kinds:
            raise ValidationError("relation references an unresolved endpoint")
        if from_code == to_code:
            raise ValidationError("relation cannot reference the same endpoint twice")
        identity = (kind, from_code, to_code)
        if identity in identities:
            raise ValidationError("domain pack contains duplicate relation identity")
        identities.add(identity)
        allowed_from, allowed_to = _RELATION_RANGES[kind]
        if definition_kinds[from_code] not in allowed_from or definition_kinds[to_code] not in allowed_to:
            raise ValidationError("relation domain or range is invalid")
        citation_documents, citations = _parse_citations(entry["citations"], source_ids)
        documents.append(
            {
                "code": code, "kind": kind.value, "from_code": from_code,
                "to_code": to_code, "citations": citation_documents,
            }
        )
        records.append(RelationRecord(code, kind, from_code, to_code, citations))
    paired = sorted(zip(documents, records, strict=True), key=lambda pair: pair[1].code)
    return [pair[0] for pair in paired], tuple(pair[1] for pair in paired)


def _parse_citations(
    value: object, source_ids: set[object]
) -> tuple[list[dict[str, str]], tuple[CitationRecord, ...]]:
    if not isinstance(value, list) or not value:
        raise ValidationError("each assertion must contain at least one citation")
    documents: list[dict[str, str]] = []
    identities: set[tuple[str, str, str, str]] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"citation {index} must be an object")
        _require_exact_fields(entry, _CITATION_FIELDS, f"citation {index}")
        source_ref = _code(entry["source_ref"], "citation source_ref")
        if source_ref not in source_ids:
            raise ValidationError("citation references an unresolved source")
        identity = (
            source_ref,
            _text(entry["locator"], "citation locator"),
            _text(entry["applicability"], "citation applicability"),
            _text(entry["limitations"], "citation limitations"),
        )
        if identity in identities:
            raise ValidationError("assertion contains a duplicate citation")
        identities.add(identity)
        documents.append(
            {
                "source_ref": identity[0], "locator": identity[1],
                "applicability": identity[2], "limitations": identity[3],
            }
        )
    documents.sort(key=lambda item: (item["source_ref"], item["locator"], item["applicability"], item["limitations"]))
    records = tuple(CitationRecord(**document) for document in documents)
    return documents, records


def _attributes(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValidationError("definition attributes must be an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or _ATTRIBUTE_KEY_PATTERN.fullmatch(key) is None:
            raise ValidationError("definition attribute keys must be lower_snake_case")
        result[key] = _canonical_value(item, f"attribute {key}")
    return result


def _canonical_value(value: object, context: str) -> object:
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        raise ValidationError(f"{context} must not contain a float")
    if isinstance(value, str):
        return _text(value, context)
    if isinstance(value, list):
        return [_canonical_value(item, context) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValidationError(f"{context} object keys must be strings")
        return {key: _canonical_value(value[key], context) for key in sorted(value)}
    raise ValidationError(f"{context} contains a non-canonical value")


def _freeze_definition(record: DefinitionRecord) -> DefinitionRecord:
    return DefinitionRecord(
        record.code, record.kind, record.family, record.title, record.description,
        cast(Mapping[str, object], _freeze(dict(record.attributes))), record.citations,
    )


def _freeze_relation(record: RelationRecord) -> RelationRecord:
    return RelationRecord(record.code, record.kind, record.from_code, record.to_code, record.citations)


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _reject_floats(value: object) -> None:
    if isinstance(value, float):
        raise ValidationError("domain pack must not contain a float")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError("domain pack object keys must be strings")
            _reject_floats(item)
    elif isinstance(value, list):
        for item in value:
            _reject_floats(item)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _require_exact_fields(value: dict[object, object], expected: set[str], context: str) -> None:
    if set(value) != expected:
        raise ValidationError(f"{context} fields are invalid")


def _text(value: object, context: str) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or "\x00" in value or any(ord(character) < 32 for character in value)
    ):
        raise ValidationError(f"{context} must be non-blank canonical text")
    return value


def _code(value: object, context: str) -> str:
    result = _text(value, context)
    if _CODE_PATTERN.fullmatch(result) is None:
        raise ValidationError(f"{context} must be an uppercase stable code")
    return result


def _token(value: object, context: str) -> str:
    result = _text(value, context)
    if _TOKEN_PATTERN.fullmatch(result) is None:
        raise ValidationError(f"{context} must be an uppercase token")
    return result


def _date_text(value: object, context: str) -> str:
    result = _text(value, context)
    try:
        parsed = date.fromisoformat(result)
    except ValueError as error:
        raise ValidationError(f"{context} must be an ISO date") from error
    if parsed.isoformat() != result:
        raise ValidationError(f"{context} must be a canonical ISO date")
    return result


def _optional_date_text(value: object, context: str) -> str | None:
    return None if value is None else _date_text(value, context)


def _optional_sha256(value: object, context: str) -> str | None:
    if value is None:
        return None
    result = _text(value, context)
    if _SHA256_PATTERN.fullmatch(result) is None:
        raise ValidationError(f"{context} must be a lowercase SHA-256 digest")
    return result


def _required_sha256(value: object, context: str) -> str:
    result = _optional_sha256(value, context)
    if result is None:
        raise ValidationError(f"{context} must be a lowercase SHA-256 digest")
    return result


def _optional_text(value: object, context: str) -> str | None:
    return None if value is None else _text(value, context)


def _string_array(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValidationError(f"{context} must be an array")
    result = tuple(sorted(_text(item, context) for item in value))
    if len(set(result)) != len(result):
        raise ValidationError(f"{context} must not contain duplicates")
    return result
