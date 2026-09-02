"""Focused contracts for the strict P1B domain-pack compiler."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest

from helios_takeoff_core.errors import ValidationError


def valid_pack() -> dict[str, object]:
    return {
        "protocol": "helios.p1b.domain-pack/v1",
        "pack_code": "DIV23-HVAC",
        "version": "1.0.0",
        "title": "Division 23 HVAC definitions",
        "jurisdiction": "US",
        "provenance": [
            {
                "kind": "PUBLIC_URL",
                "reference": "https://example.test/div23-hvac",
            }
        ],
        "catalog": [
            {
                "code": "MAT-DUCT",
                "type": "MATERIAL",
                "title": "Sheet metal ductwork",
                "uom": "SF",
            },
            {
                "code": "SYS-AHU",
                "type": "SYSTEM",
                "title": "Air handling unit system",
                "uom": "EA",
            },
            {
                "code": "CMP-AHU",
                "type": "COMPONENT",
                "title": "Air handling unit",
                "uom": "EA",
            },
        ],
        "relations": [
            {
                "from_code": "CMP-AHU",
                "relation": "PART_OF",
                "to_code": "SYS-AHU",
            },
            {
                "from_code": "CMP-AHU",
                "relation": "USES_MATERIAL",
                "to_code": "MAT-DUCT",
            },
        ],
        "rules": [
            {
                "code": "MEASURE-AHU-COUNT",
                "type": "MEASURE",
                "subject_code": "CMP-AHU",
                "required_observations": ["AHU_TAG"],
                "required_evidence": ["MECHANICAL_PLAN"],
                "output_claim_type": "EQUIPMENT_COUNT",
                "output_uom": "EA",
            }
        ],
    }


class DomainPackCompilerTests(unittest.TestCase):
    def test_compiles_a_valid_pack_into_canonical_indexes(self) -> None:
        """Removing normalization or the indexes makes this consumer contract fail."""
        from helios_takeoff_core.engine.compiler import compile_domain_pack

        compiled = compile_domain_pack(valid_pack())

        self.assertEqual(compiled.canonical_document["pack_code"], "DIV23-HVAC")
        self.assertEqual([item["code"] for item in compiled.canonical_document["catalog"]], [
            "CMP-AHU",
            "MAT-DUCT",
            "SYS-AHU",
        ])
        self.assertEqual(compiled.catalog_by_code["CMP-AHU"]["title"], "Air handling unit")
        self.assertEqual(compiled.rules_by_code["MEASURE-AHU-COUNT"]["output_uom"], "EA")
        self.assertEqual(len(compiled.sha256), 64)
        self.assertEqual(compiled.canonical_bytes, compiled.canonical_bytes.strip())

    def test_digest_is_independent_of_definition_order(self) -> None:
        """Removing stable sorting makes equivalent definition packs hash differently."""
        from helios_takeoff_core.engine.compiler import compile_domain_pack

        reordered = valid_pack()
        reordered["catalog"] = list(reversed(reordered["catalog"]))
        reordered["relations"] = list(reversed(reordered["relations"]))
        reordered["rules"] = list(reversed(reordered["rules"]))

        self.assertEqual(compile_domain_pack(valid_pack()).sha256, compile_domain_pack(reordered).sha256)

    def test_rejects_unknown_fields(self) -> None:
        """Accepting an arbitrary schema field makes the strict pack contract fail."""
        from helios_takeoff_core.engine.compiler import compile_domain_pack

        document = valid_pack()
        document["notes"] = "not part of v1"

        with self.assertRaisesRegex(ValidationError, "fields"):
            compile_domain_pack(document)

    def test_rejects_duplicate_and_unresolved_codes(self) -> None:
        """Dropping unique and cross-reference checks accepts ambiguous definitions."""
        from helios_takeoff_core.engine.compiler import compile_domain_pack

        duplicate = valid_pack()
        duplicate["catalog"].append(deepcopy(duplicate["catalog"][0]))
        with self.assertRaisesRegex(ValidationError, "duplicate catalog code"):
            compile_domain_pack(duplicate)

        unresolved = valid_pack()
        unresolved["relations"][0]["to_code"] = "SYS-MISSING"
        with self.assertRaisesRegex(ValidationError, "unknown catalog code"):
            compile_domain_pack(unresolved)

    def test_rejects_unsupported_type_and_uom(self) -> None:
        """Widening the closed type or UOM allowlists accepts unsupported definitions."""
        from helios_takeoff_core.engine.compiler import compile_domain_pack

        unsupported_type = valid_pack()
        unsupported_type["catalog"][0]["type"] = "LABOR"
        with self.assertRaisesRegex(ValidationError, "catalog type"):
            compile_domain_pack(unsupported_type)

        unsupported_uom = valid_pack()
        unsupported_uom["rules"][0]["output_uom"] = "GAL"
        with self.assertRaisesRegex(ValidationError, "UOM"):
            compile_domain_pack(unsupported_uom)

    def test_rejects_executable_and_bid_authority_fields(self) -> None:
        """Allowing commands or bid authority would breach the P1B boundary."""
        from helios_takeoff_core.engine.compiler import compile_domain_pack

        executable = valid_pack()
        executable["rules"][0]["command"] = "echo unsafe"
        with self.assertRaisesRegex(ValidationError, "fields"):
            compile_domain_pack(executable)

        bid_authority = valid_pack()
        bid_authority["rules"][0]["price"] = "100.00"
        with self.assertRaisesRegex(ValidationError, "fields"):
            compile_domain_pack(bid_authority)

    def test_compiled_pack_is_deeply_immutable(self) -> None:
        """Leaving nested definitions mutable desynchronizes them from the digest."""
        from helios_takeoff_core.engine.compiler import compile_domain_pack

        compiled = compile_domain_pack(valid_pack())
        original_bytes = compiled.canonical_bytes
        original_digest = compiled.sha256

        with self.assertRaises(TypeError):
            compiled.canonical_document["title"] = "tampered"  # type: ignore[index]
        with self.assertRaises(AttributeError):
            compiled.canonical_document["catalog"].append({})  # type: ignore[union-attr]
        with self.assertRaises(TypeError):
            compiled.rules_by_code["MEASURE-AHU-COUNT"]["output_claim_type"] = "TAMPERED"

        self.assertEqual(compiled.canonical_bytes, original_bytes)
        self.assertEqual(compiled.sha256, original_digest)
        self.assertEqual(hashlib.sha256(compiled.canonical_bytes).hexdigest(), compiled.sha256)

    def test_requires_project_document_provenance_to_be_an_opaque_stable_code(self) -> None:
        """Accepting paths would let pack provenance name local files."""
        from helios_takeoff_core.engine.compiler import compile_domain_pack

        accepted = valid_pack()
        accepted["provenance"] = [{"kind": "PROJECT_DOCUMENT", "reference": "DOC-2026-08-31"}]
        self.assertEqual(
            compile_domain_pack(accepted).canonical_document["provenance"][0]["reference"],
            "DOC-2026-08-31",
        )

        for path in ("/tmp/spec.pdf", "relative/spec.pdf", "file:///tmp/spec.pdf"):
            with self.subTest(path=path):
                rejected = valid_pack()
                rejected["provenance"] = [{"kind": "PROJECT_DOCUMENT", "reference": path}]
                with self.assertRaisesRegex(ValidationError, "PROJECT_DOCUMENT"):
                    compile_domain_pack(rejected)


if __name__ == "__main__":
    unittest.main()
