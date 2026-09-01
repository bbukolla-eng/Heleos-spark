"""Immutable public contracts for HELIOS P1B domain packs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


DOMAIN_PACK_PROTOCOL = "helios.p1b.domain-pack/v1"


@dataclass(frozen=True)
class CompiledDomainPack:
    """A strict, normalized, content-addressed domain-pack definition."""

    canonical_document: Mapping[str, object]
    canonical_bytes: bytes
    sha256: str
    catalog_by_code: Mapping[str, Mapping[str, object]]
    rules_by_code: Mapping[str, Mapping[str, object]]

    def catalog_item(self, code: str) -> Mapping[str, object] | None:
        """Return a catalog definition by its stable code, if present."""
        return self.catalog_by_code.get(code)

    def rule(self, code: str) -> Mapping[str, object] | None:
        """Return a rule definition by its stable code, if present."""
        return self.rules_by_code.get(code)
