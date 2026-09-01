"""Strict compiler for non-executable HELIOS P1B domain-pack definitions."""

from __future__ import annotations

import hashlib
import json
import re
from types import MappingProxyType
from typing import Mapping, cast
from urllib.parse import urlparse

from ..errors import ValidationError
from .contracts import CompiledDomainPack, DOMAIN_PACK_PROTOCOL


CATALOG_TYPES = frozenset(
    {"SYSTEM", "COMPONENT", "MATERIAL", "CONNECTION", "ACCESSORY", "EQUIPMENT", "ASSEMBLY"}
)
RULE_TYPES = frozenset({"CLASSIFY", "REQUIRE", "MEASURE", "ASSEMBLE"})
UOMS = frozenset({"EA", "LF", "SF", "CF", "LB", "FT", "IN"})
PROVENANCE_KINDS = frozenset({"PUBLIC_URL", "PROJECT_DOCUMENT", "CONTENT_DIGEST"})

_TOP_LEVEL_FIELDS = {
    "protocol",
    "pack_code",
    "version",
    "title",
    "jurisdiction",
    "provenance",
    "catalog",
    "relations",
    "rules",
}
_CATALOG_FIELDS = {"code", "type", "title", "uom"}
_RELATION_FIELDS = {"from_code", "relation", "to_code"}
_RULE_REQUIRED_FIELDS = {
    "code",
    "type",
    "subject_code",
    "required_observations",
    "required_evidence",
    "output_claim_type",
}
_RULE_FIELDS = _RULE_REQUIRED_FIELDS | {"output_uom"}
_PROVENANCE_FIELDS = {"kind", "reference"}
_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")
_RELATION_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CLAIM_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def compile_domain_pack(document: object) -> CompiledDomainPack:
    """Validate, normalize, and content-address one exact v1 domain pack."""
    if not isinstance(document, dict):
        raise ValidationError("domain pack must be an object")
    _require_exact_fields(document, _TOP_LEVEL_FIELDS, "domain pack")
    if document["protocol"] != DOMAIN_PACK_PROTOCOL:
        raise ValidationError("domain pack protocol is unsupported")

    pack_code = _code(document["pack_code"], "pack_code")
    version = _text(document["version"], "version")
    title = _text(document["title"], "title")
    jurisdiction = _text(document["jurisdiction"], "jurisdiction")
    provenance = _parse_provenance(document["provenance"])
    catalog = _parse_catalog(document["catalog"])
    catalog_codes = {item["code"] for item in catalog}
    relations = _parse_relations(document["relations"], catalog_codes)
    rules = _parse_rules(document["rules"], catalog_codes)

    canonical_document: dict[str, object] = {
        "protocol": DOMAIN_PACK_PROTOCOL,
        "pack_code": pack_code,
        "version": version,
        "title": title,
        "jurisdiction": jurisdiction,
        "provenance": provenance,
        "catalog": catalog,
        "relations": relations,
        "rules": rules,
    }
    canonical_bytes = json.dumps(
        canonical_document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    frozen_document = cast(Mapping[str, object], _freeze(canonical_document))
    frozen_catalog = cast(tuple[Mapping[str, object], ...], frozen_document["catalog"])
    frozen_rules = cast(tuple[Mapping[str, object], ...], frozen_document["rules"])
    catalog_by_code = MappingProxyType({str(item["code"]): item for item in frozen_catalog})
    rules_by_code = MappingProxyType({str(rule["code"]): rule for rule in frozen_rules})
    return CompiledDomainPack(
        canonical_document=frozen_document,
        canonical_bytes=canonical_bytes,
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        catalog_by_code=catalog_by_code,
        rules_by_code=rules_by_code,
    )


def _parse_provenance(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValidationError("domain pack provenance must contain at least one record")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"provenance entry {index} must be an object")
        _require_exact_fields(entry, _PROVENANCE_FIELDS, f"provenance entry {index}")
        kind = _text(entry["kind"], "provenance kind")
        if kind not in PROVENANCE_KINDS:
            raise ValidationError("provenance kind is unsupported")
        reference = _text(entry["reference"], "provenance reference")
        if kind == "PUBLIC_URL":
            parsed = urlparse(reference)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValidationError("PUBLIC_URL provenance reference must be an absolute HTTP URL")
        elif kind == "PROJECT_DOCUMENT" and _CODE_PATTERN.fullmatch(reference) is None:
            raise ValidationError("PROJECT_DOCUMENT provenance reference must be an uppercase stable code")
        elif kind == "CONTENT_DIGEST" and _SHA256_PATTERN.fullmatch(reference) is None:
            raise ValidationError("CONTENT_DIGEST provenance reference must be a lowercase SHA-256 digest")
        key = (kind, reference)
        if key in seen:
            raise ValidationError("domain pack contains duplicate provenance")
        seen.add(key)
        result.append({"kind": kind, "reference": reference})
    return sorted(result, key=lambda entry: (entry["kind"], entry["reference"]))


def _parse_catalog(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValidationError("domain pack catalog must contain at least one item")
    result: list[dict[str, str]] = []
    codes: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"catalog entry {index} must be an object")
        _require_exact_fields(entry, _CATALOG_FIELDS, f"catalog entry {index}")
        code = _code(entry["code"], "catalog code")
        if code in codes:
            raise ValidationError("domain pack contains a duplicate catalog code")
        codes.add(code)
        item_type = _text(entry["type"], "catalog type")
        if item_type not in CATALOG_TYPES:
            raise ValidationError("catalog type is unsupported")
        uom = _uom(entry["uom"], "catalog UOM")
        result.append(
            {"code": code, "type": item_type, "title": _text(entry["title"], "catalog title"), "uom": uom}
        )
    return sorted(result, key=lambda entry: entry["code"])


def _parse_relations(value: object, catalog_codes: set[str]) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValidationError("domain pack relations must be an array")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"relation entry {index} must be an object")
        _require_exact_fields(entry, _RELATION_FIELDS, f"relation entry {index}")
        from_code = _code(entry["from_code"], "relation from_code")
        to_code = _code(entry["to_code"], "relation to_code")
        if from_code not in catalog_codes or to_code not in catalog_codes:
            raise ValidationError("relation references an unknown catalog code")
        relation = _text(entry["relation"], "relation")
        if _RELATION_PATTERN.fullmatch(relation) is None:
            raise ValidationError("relation must be an uppercase verb phrase")
        key = (from_code, relation, to_code)
        if key in seen:
            raise ValidationError("domain pack contains a duplicate relation")
        seen.add(key)
        result.append({"from_code": from_code, "relation": relation, "to_code": to_code})
    return sorted(result, key=lambda entry: (entry["from_code"], entry["relation"], entry["to_code"]))


def _parse_rules(value: object, catalog_codes: set[str]) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValidationError("domain pack rules must be an array")
    result: list[dict[str, object]] = []
    codes: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValidationError(f"rule entry {index} must be an object")
        if not _RULE_REQUIRED_FIELDS.issubset(entry) or set(entry) - _RULE_FIELDS:
            raise ValidationError(f"rule entry {index} fields are invalid")
        code = _code(entry["code"], "rule code")
        if code in codes:
            raise ValidationError("domain pack contains a duplicate rule code")
        codes.add(code)
        rule_type = _text(entry["type"], "rule type")
        if rule_type not in RULE_TYPES:
            raise ValidationError("rule type is unsupported")
        subject_code = _code(entry["subject_code"], "rule subject_code")
        if subject_code not in catalog_codes:
            raise ValidationError("rule references an unknown catalog code")
        required_observations = _code_list(entry["required_observations"], "required_observations")
        required_evidence = _code_list(entry["required_evidence"], "required_evidence")
        if not required_observations and not required_evidence:
            raise ValidationError("rule must require at least one observation or evidence item")
        output_claim_type = _text(entry["output_claim_type"], "output_claim_type")
        if _CLAIM_TYPE_PATTERN.fullmatch(output_claim_type) is None:
            raise ValidationError("output_claim_type must be uppercase text")
        normalized: dict[str, object] = {
            "code": code,
            "type": rule_type,
            "subject_code": subject_code,
            "required_observations": required_observations,
            "required_evidence": required_evidence,
            "output_claim_type": output_claim_type,
        }
        if "output_uom" in entry:
            normalized["output_uom"] = _uom(entry["output_uom"], "rule output UOM")
        result.append(normalized)
    return sorted(result, key=lambda entry: str(entry["code"]))


def _require_exact_fields(value: dict[object, object], fields: set[str], context: str) -> None:
    if set(value) != fields:
        raise ValidationError(f"{context} fields are invalid")


def _text(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValidationError(f"{name} must be non-blank text without controls")
    return value


def _code(value: object, name: str) -> str:
    result = _text(value, name)
    if _CODE_PATTERN.fullmatch(result) is None:
        raise ValidationError(f"{name} must be an uppercase stable code")
    return result


def _uom(value: object, name: str) -> str:
    result = _text(value, name)
    if result not in UOMS:
        raise ValidationError(f"{name} is unsupported")
    return result


def _code_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValidationError(f"{name} must be an array")
    result = [_named_code(item, name) for item in value]
    if len(set(result)) != len(result):
        raise ValidationError(f"{name} must not contain duplicates")
    return sorted(result)


def _named_code(value: object, name: str) -> str:
    result = _text(value, name)
    if _CLAIM_TYPE_PATTERN.fullmatch(result) is None:
        raise ValidationError(f"{name} must contain uppercase stable names")
    return result


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value
