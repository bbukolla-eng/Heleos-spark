"""Pure deterministic evaluation for compiled HELIOS domain packs."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from ..errors import NotFoundError, ValidationError
from .contracts import CompiledDomainPack


STRUCTURAL_RELATIONS = frozenset(
    {
        "COMPOSED_OF",
        "REQUIRES",
        "REQUIRES_ACCESSORY",
        "USES_MATERIAL",
        "USES_CONNECTION",
    }
)

_STABLE_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def evaluate_subject(
    pack: CompiledDomainPack,
    *,
    subject_code: str,
    observations: object,
    evidence_kinds: object,
) -> dict[str, object]:
    """Partition a subject's rules into deterministic candidates and blocks."""
    if pack.catalog_item(subject_code) is None:
        raise NotFoundError("catalog item was not found")

    normalized_observations = _validated_observations(observations)
    normalized_evidence = _validated_evidence_kinds(evidence_kinds)
    available_evidence = set(normalized_evidence)
    candidate_claims: list[dict[str, object]] = []
    blocked_rules: list[dict[str, object]] = []

    matching_rules = sorted(
        (
            rule
            for rule in pack.rules_by_code.values()
            if rule["subject_code"] == subject_code
        ),
        key=lambda rule: str(rule["code"]),
    )
    for rule in matching_rules:
        required_observations = tuple(str(name) for name in rule["required_observations"])
        required_evidence = tuple(str(kind) for kind in rule["required_evidence"])
        missing_observations = sorted(
            name for name in required_observations if name not in normalized_observations
        )
        missing_evidence = sorted(
            kind for kind in required_evidence if kind not in available_evidence
        )
        if missing_observations or missing_evidence:
            blocked: dict[str, object] = {
                "rule_code": rule["code"],
                "rule_type": rule["type"],
                "subject_code": subject_code,
                "output_claim_type": rule["output_claim_type"],
                "missing_observations": missing_observations,
                "missing_evidence_kinds": missing_evidence,
            }
            if "output_uom" in rule:
                blocked["output_uom"] = rule["output_uom"]
            blocked_rules.append(blocked)
            continue

        candidate: dict[str, object] = {
            "rule_code": rule["code"],
            "rule_type": rule["type"],
            "subject_code": subject_code,
            "output_claim_type": rule["output_claim_type"],
        }
        if "output_uom" in rule:
            candidate["output_uom"] = rule["output_uom"]
        candidate["observations"] = {
            name: normalized_observations[name] for name in sorted(required_observations)
        }
        candidate["evidence_kinds"] = sorted(required_evidence)
        candidate_claims.append(candidate)

    return {
        **_pack_lineage(pack),
        "subject_code": subject_code,
        "candidate_claims": candidate_claims,
        "blocked_rules": blocked_rules,
    }


def resolve_assembly(pack: CompiledDomainPack, *, item_code: str) -> dict[str, object]:
    """Resolve the rooted graph of outgoing structural catalog relations."""
    if pack.catalog_item(item_code) is None:
        raise NotFoundError("catalog item was not found")

    outgoing: dict[str, list[tuple[str, str, str]]] = {}
    for relation in pack.canonical_document["relations"]:  # type: ignore[union-attr]
        relation_name = str(relation["relation"])
        if relation_name not in STRUCTURAL_RELATIONS:
            continue
        edge = (
            str(relation["from_code"]),
            relation_name,
            str(relation["to_code"]),
        )
        outgoing.setdefault(edge[0], []).append(edge)
    for edges in outgoing.values():
        edges.sort()

    state: dict[str, int] = {}
    reachable_codes: set[str] = set()
    reachable_edges: set[tuple[str, str, str]] = set()

    def visit(code: str) -> None:
        if state.get(code) == 1:
            raise ValidationError("structural assembly relation cycle detected")
        if state.get(code) == 2:
            return
        state[code] = 1
        reachable_codes.add(code)
        for edge in outgoing.get(code, []):
            reachable_edges.add(edge)
            visit(edge[2])
        state[code] = 2

    visit(item_code)

    return {
        **_pack_lineage(pack),
        "item_code": item_code,
        "items": [dict(pack.catalog_by_code[code]) for code in sorted(reachable_codes)],
        "relations": [
            {"from_code": from_code, "relation": relation, "to_code": to_code}
            for from_code, relation, to_code in sorted(reachable_edges)
        ],
    }


def _validated_observations(value: object) -> dict[str, str | int | float | bool]:
    if not isinstance(value, Mapping):
        raise ValidationError("observations must be an object")
    result: dict[str, str | int | float | bool] = {}
    for name, observation in value.items():
        _require_stable_name(name, "observation name")
        if type(observation) not in {str, int, float, bool}:
            raise ValidationError("observation values must be finite JSON scalars")
        if isinstance(observation, float) and not math.isfinite(observation):
            raise ValidationError("observation values must be finite JSON scalars")
        result[name] = observation
    return result


def _validated_evidence_kinds(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValidationError("evidence_kinds must be an array")
    result: list[str] = []
    for kind in value:
        _require_stable_name(kind, "evidence kind")
        result.append(kind)
    return sorted(set(result))


def _require_stable_name(value: object, name: str) -> None:
    if not isinstance(value, str) or _STABLE_NAME_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{name} must be an uppercase stable name")


def _pack_lineage(pack: CompiledDomainPack) -> dict[str, object]:
    return {
        "pack_code": pack.canonical_document["pack_code"],
        "version": pack.canonical_document["version"],
        "sha256": pack.sha256,
    }
