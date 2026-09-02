"""Focused contracts for the deterministic P1B engine kernel."""

from __future__ import annotations

from copy import deepcopy
import json
import math
import unittest

from helios_takeoff_core.engine.compiler import compile_domain_pack
from helios_takeoff_core.errors import NotFoundError, ValidationError


def engine_pack() -> dict[str, object]:
    return {
        "protocol": "helios.p1b.domain-pack/v1",
        "pack_code": "DIV23-ENGINE",
        "version": "1.0.0",
        "title": "Division 23 deterministic engine fixture",
        "jurisdiction": "US",
        "provenance": [
            {
                "kind": "PUBLIC_URL",
                "reference": "https://example.test/div23-engine",
            }
        ],
        "catalog": [
            {"code": "ASM-ROOT", "type": "ASSEMBLY", "title": "Root", "uom": "EA"},
            {"code": "ASM-LEFT", "type": "ASSEMBLY", "title": "Left", "uom": "EA"},
            {"code": "ASM-RIGHT", "type": "ASSEMBLY", "title": "Right", "uom": "EA"},
            {"code": "CMP-SHARED", "type": "COMPONENT", "title": "Shared", "uom": "EA"},
            {"code": "MAT-SHEET", "type": "MATERIAL", "title": "Sheet", "uom": "SF"},
            {"code": "SYS-OUTSIDE", "type": "SYSTEM", "title": "Outside", "uom": "EA"},
        ],
        "relations": [
            {"from_code": "ASM-ROOT", "relation": "COMPOSED_OF", "to_code": "ASM-RIGHT"},
            {"from_code": "ASM-LEFT", "relation": "REQUIRES", "to_code": "CMP-SHARED"},
            {"from_code": "CMP-SHARED", "relation": "USES_MATERIAL", "to_code": "MAT-SHEET"},
            {"from_code": "ASM-ROOT", "relation": "COMPOSED_OF", "to_code": "ASM-LEFT"},
            {"from_code": "ASM-RIGHT", "relation": "REQUIRES", "to_code": "CMP-SHARED"},
            {"from_code": "SYS-OUTSIDE", "relation": "COMPOSED_OF", "to_code": "ASM-ROOT"},
            {"from_code": "ASM-ROOT", "relation": "PART_OF", "to_code": "SYS-OUTSIDE"},
        ],
        "rules": [
            {
                "code": "Z-BLOCKED-RULE",
                "type": "CLASSIFY",
                "subject_code": "CMP-SHARED",
                "required_observations": ["WIDTH", "HEIGHT"],
                "required_evidence": ["SCHEDULE", "DETAIL"],
                "output_claim_type": "DUCT_CLASS",
            },
            {
                "code": "A-READY-RULE",
                "type": "MEASURE",
                "subject_code": "CMP-SHARED",
                "required_observations": ["ACTIVE", "COUNT", "TAG"],
                "required_evidence": ["MECHANICAL_PLAN"],
                "output_claim_type": "EQUIPMENT_COUNT",
                "output_uom": "EA",
            },
            {
                "code": "OTHER-SUBJECT-RULE",
                "type": "REQUIRE",
                "subject_code": "MAT-SHEET",
                "required_observations": ["GAUGE"],
                "required_evidence": [],
                "output_claim_type": "MATERIAL_GAUGE",
            },
        ],
    }


class EngineEvaluatorTests(unittest.TestCase):
    def test_evaluates_only_matching_rules_as_ready_or_explicitly_blocked(self) -> None:
        """Dropping readiness checks or subject filtering emits incorrect candidate claims."""
        from helios_takeoff_core.engine.evaluator import evaluate_subject

        compiled = compile_domain_pack(engine_pack())

        result = evaluate_subject(
            compiled,
            subject_code="CMP-SHARED",
            observations={"TAG": "AHU-1", "COUNT": 2, "ACTIVE": True, "EXTRA": 9.5},
            evidence_kinds=["SCHEDULE", "MECHANICAL_PLAN"],
        )

        self.assertEqual(
            result,
            {
                "pack_code": "DIV23-ENGINE",
                "version": "1.0.0",
                "sha256": compiled.sha256,
                "subject_code": "CMP-SHARED",
                "candidate_claims": [
                    {
                        "rule_code": "A-READY-RULE",
                        "rule_type": "MEASURE",
                        "subject_code": "CMP-SHARED",
                        "output_claim_type": "EQUIPMENT_COUNT",
                        "output_uom": "EA",
                        "observations": {"ACTIVE": True, "COUNT": 2, "TAG": "AHU-1"},
                        "evidence_kinds": ["MECHANICAL_PLAN"],
                    }
                ],
                "blocked_rules": [
                    {
                        "rule_code": "Z-BLOCKED-RULE",
                        "rule_type": "CLASSIFY",
                        "subject_code": "CMP-SHARED",
                        "output_claim_type": "DUCT_CLASS",
                        "missing_observations": ["HEIGHT", "WIDTH"],
                        "missing_evidence_kinds": ["DETAIL"],
                    }
                ],
            },
        )

    def test_evaluation_is_deterministic_json_and_does_not_mutate_inputs(self) -> None:
        """Depending on caller order or mutating context breaks replay-safe evaluation."""
        from helios_takeoff_core.engine.evaluator import evaluate_subject

        compiled = compile_domain_pack(engine_pack())
        observations = {"TAG": "AHU-1", "COUNT": 2, "ACTIVE": False, "EXTRA": 1.25}
        evidence_kinds = ["SCHEDULE", "MECHANICAL_PLAN", "SCHEDULE"]
        observations_before = deepcopy(observations)
        evidence_before = list(evidence_kinds)

        first = evaluate_subject(
            compiled,
            subject_code="CMP-SHARED",
            observations=observations,
            evidence_kinds=evidence_kinds,
        )
        second = evaluate_subject(
            compiled,
            subject_code="CMP-SHARED",
            observations=dict(reversed(list(observations.items()))),
            evidence_kinds=list(reversed(evidence_kinds)),
        )

        self.assertEqual(json.dumps(first, allow_nan=False), json.dumps(second, allow_nan=False))
        self.assertEqual(observations, observations_before)
        self.assertEqual(evidence_kinds, evidence_before)
        self.assertIs(first["candidate_claims"][0]["observations"]["ACTIVE"], False)

    def test_blocked_rule_preserves_optional_output_uom(self) -> None:
        """A blocked measurement still declares the output it would produce."""
        from helios_takeoff_core.engine.evaluator import evaluate_subject

        compiled = compile_domain_pack(engine_pack())
        result = evaluate_subject(
            compiled,
            subject_code="CMP-SHARED",
            observations={},
            evidence_kinds=[],
        )

        blocked_by_code = {rule["rule_code"]: rule for rule in result["blocked_rules"]}
        self.assertEqual(blocked_by_code["A-READY-RULE"]["output_claim_type"], "EQUIPMENT_COUNT")
        self.assertEqual(blocked_by_code["A-READY-RULE"]["output_uom"], "EA")
        self.assertEqual(blocked_by_code["Z-BLOCKED-RULE"]["output_claim_type"], "DUCT_CLASS")
        self.assertNotIn("output_uom", blocked_by_code["Z-BLOCKED-RULE"])

    def test_rejects_unknown_subjects_and_malformed_runtime_context(self) -> None:
        """Accepting unknown subjects or non-JSON context makes evaluation ambiguous."""
        from helios_takeoff_core.engine.evaluator import evaluate_subject

        compiled = compile_domain_pack(engine_pack())

        with self.assertRaises(NotFoundError):
            evaluate_subject(
                compiled,
                subject_code="CMP-MISSING",
                observations={},
                evidence_kinds=[],
            )

        invalid_contexts = [
            ({"lowercase": 1}, []),
            ({"BAD-NAME": 1}, []),
            ({"VALUE": None}, []),
            ({"VALUE": [1]}, []),
            ({"VALUE": {"nested": True}}, []),
            ({"VALUE": math.nan}, []),
            ({"VALUE": math.inf}, []),
            ({"VALUE": -math.inf}, []),
            ({"VALUE": 1}, ["lowercase"]),
            ({"VALUE": 1}, ["BAD-KIND"]),
        ]
        for observations, evidence_kinds in invalid_contexts:
            with self.subTest(observations=observations, evidence_kinds=evidence_kinds):
                with self.assertRaises(ValidationError):
                    evaluate_subject(
                        compiled,
                        subject_code="CMP-SHARED",
                        observations=observations,
                        evidence_kinds=evidence_kinds,
                    )

        for invalid_observations in ([], None, "VALUE"):
            with self.subTest(invalid_observations=invalid_observations):
                with self.assertRaises(ValidationError):
                    evaluate_subject(
                        compiled,
                        subject_code="CMP-SHARED",
                        observations=invalid_observations,
                        evidence_kinds=[],
                    )

        for invalid_evidence in ({"PLAN"}, None, "PLAN"):
            with self.subTest(invalid_evidence=invalid_evidence):
                with self.assertRaises(ValidationError):
                    evaluate_subject(
                        compiled,
                        subject_code="CMP-SHARED",
                        observations={},
                        evidence_kinds=invalid_evidence,
                    )

    def test_resolves_nested_outgoing_structural_graph_with_diamond_reuse(self) -> None:
        """Following incoming/non-structural edges or duplicating a diamond corrupts assemblies."""
        from helios_takeoff_core.engine.evaluator import resolve_assembly

        compiled = compile_domain_pack(engine_pack())

        result = resolve_assembly(compiled, item_code="ASM-ROOT")

        self.assertEqual(
            result,
            {
                "pack_code": "DIV23-ENGINE",
                "version": "1.0.0",
                "sha256": compiled.sha256,
                "item_code": "ASM-ROOT",
                "items": [
                    {"code": "ASM-LEFT", "type": "ASSEMBLY", "title": "Left", "uom": "EA"},
                    {"code": "ASM-RIGHT", "type": "ASSEMBLY", "title": "Right", "uom": "EA"},
                    {"code": "ASM-ROOT", "type": "ASSEMBLY", "title": "Root", "uom": "EA"},
                    {"code": "CMP-SHARED", "type": "COMPONENT", "title": "Shared", "uom": "EA"},
                    {"code": "MAT-SHEET", "type": "MATERIAL", "title": "Sheet", "uom": "SF"},
                ],
                "relations": [
                    {"from_code": "ASM-LEFT", "relation": "REQUIRES", "to_code": "CMP-SHARED"},
                    {"from_code": "ASM-RIGHT", "relation": "REQUIRES", "to_code": "CMP-SHARED"},
                    {"from_code": "ASM-ROOT", "relation": "COMPOSED_OF", "to_code": "ASM-LEFT"},
                    {"from_code": "ASM-ROOT", "relation": "COMPOSED_OF", "to_code": "ASM-RIGHT"},
                    {"from_code": "CMP-SHARED", "relation": "USES_MATERIAL", "to_code": "MAT-SHEET"},
                ],
            },
        )
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)

    def test_resolve_assembly_rejects_unknown_items_and_reachable_cycles(self) -> None:
        """Ignoring missing roots or back edges can return an invalid partial assembly."""
        from helios_takeoff_core.engine.evaluator import resolve_assembly

        compiled = compile_domain_pack(engine_pack())
        with self.assertRaises(NotFoundError):
            resolve_assembly(compiled, item_code="ASM-MISSING")

        cyclic = engine_pack()
        cyclic["relations"].append(
            {"from_code": "MAT-SHEET", "relation": "REQUIRES", "to_code": "ASM-ROOT"}
        )
        with self.assertRaisesRegex(ValidationError, "cycle"):
            resolve_assembly(compile_domain_pack(cyclic), item_code="ASM-ROOT")


if __name__ == "__main__":
    unittest.main()
