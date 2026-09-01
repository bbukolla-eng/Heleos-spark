"""Focused behavioral proof for the Division 23 v2 calculation kernel."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from helios_takeoff_core.errors import ValidationError


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "engine_v2"
PACK_PATH = FIXTURES / "full_division23_calculation_pack.json"


def load_json(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def compiled_pack():
    from helios_takeoff_core.engine.v2 import compile_domain_pack

    return compile_domain_pack(load_json(PACK_PATH.name))


def compiled_bundle(name: str, pack=None):
    from helios_takeoff_core.engine.v2 import compile_observation_bundle

    pack = compiled_pack() if pack is None else pack
    document = load_json(name)
    document["pack_sha256"] = pack.sha256
    return compile_observation_bundle(document, pack=pack)


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


class Division23CalculationAssemblyTests(unittest.TestCase):
    def test_frozen_contracts_and_protocols_are_exact(self) -> None:
        """Removing the frozen public types or authority marker breaks downstream consumers."""
        from helios_takeoff_core.engine.v2 import (
            CandidateAuthority,
            OBSERVATION_BUNDLE_PROTOCOL,
            RESULT_SET_PROTOCOL,
            ScalarType,
            TypedValue,
        )

        self.assertEqual(OBSERVATION_BUNDLE_PROTOCOL, "helios.engine.observation-bundle/v1")
        self.assertEqual(RESULT_SET_PROTOCOL, "helios.engine.result-set/v1")
        self.assertEqual(CandidateAuthority.NON_AUTHORITATIVE_CANDIDATE.value, "NON_AUTHORITATIVE_CANDIDATE")
        value = TypedValue(ScalarType.DECIMAL, "1.25", "LF")
        with self.assertRaises(FrozenInstanceError):
            value.value = "2"  # type: ignore[misc]

    def test_pack_identity_is_order_independent_and_b1_identity_is_unchanged(self) -> None:
        """Losing canonical sorting or changing empty-pack normalization corrupts pack identity."""
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        original = load_json(PACK_PATH.name)
        reordered = deepcopy(original)
        for field in (
            "sources", "definitions", "relations", "units", "observations",
            "lookups", "assembly_edges", "rules",
        ):
            reordered[field] = list(reversed(reordered[field]))  # type: ignore[arg-type]
        first = compile_domain_pack(original)
        second = compile_domain_pack(reordered)
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(hashlib.sha256(first.canonical_bytes).hexdigest(), first.sha256)

        b1 = compile_domain_pack(load_json("full_division23_registry_pack.json"))
        self.assertEqual(b1.sha256, "220f6762835284d55d3379b75a5d5f3abe01a13c66aebe87a47d38b0d658cd32")
        self.assertEqual(tuple(b1.units_by_uom), ())
        self.assertEqual(tuple(b1.rules_by_code), ())

    def test_formula_uses_exact_units_and_blocks_inexact_or_missing_inputs(self) -> None:
        """Binary floats, implicit rounding, or guessed inputs make deterministic results unsafe."""
        from helios_takeoff_core.engine.v2 import (
            EvaluationStatus,
            ScalarType,
            TypedValue,
            evaluate_formula,
        )

        pack = compiled_pack()
        ready = evaluate_formula(
            {"op": "OBSERVATION", "name": "LENGTH"},
            expected_scalar_type=ScalarType.DECIMAL,
            expected_uom="LF",
            observations={"LENGTH": TypedValue(ScalarType.DECIMAL, "10", "FT")},
            pack=pack,
        )
        self.assertEqual(ready.status, EvaluationStatus.READY)
        self.assertEqual(ready.value, TypedValue(ScalarType.DECIMAL, "10", "LF"))
        self.assertEqual(ready.observation_refs, ("LENGTH",))

        inexact = evaluate_formula(
            {"op": "OBSERVATION", "name": "LENGTH"},
            expected_scalar_type=ScalarType.DECIMAL,
            expected_uom="LF",
            observations={"LENGTH": TypedValue(ScalarType.DECIMAL, "1", "THIRD-LF")},
            pack=pack,
        )
        self.assertEqual(inexact.status, EvaluationStatus.BLOCKED)
        self.assertEqual(inexact.blocked_reasons, ("INEXACT_UNIT_CONVERSION",))

        missing = evaluate_formula(
            {"op": "OBSERVATION", "name": "LENGTH"},
            expected_scalar_type=ScalarType.DECIMAL,
            expected_uom="LF",
            observations={},
            pack=pack,
        )
        self.assertEqual(missing.blocked_reasons, ("MISSING_OBSERVATION:LENGTH",))

    def test_airside_equipment_bundle_spans_every_airside_family_without_authority(self) -> None:
        """Dropping cited assembly children or authority lineage yields an incomplete candidate."""
        from helios_takeoff_core.engine.v2 import (
            CandidateAuthority,
            EvaluationStatus,
            ResultKind,
            ScalarType,
            TypedValue,
            evaluate_bundle,
        )

        pack = compiled_pack()
        bundle = compiled_bundle("airside_equipment_bundle.json", pack)
        result = evaluate_bundle(pack, bundle)
        ready = {line.output_claim_type: line for line in result.lines if line.status is EvaluationStatus.READY}
        self.assertEqual(ready["EQUIPMENT_COUNT"].value, TypedValue(ScalarType.INTEGER, 1, "EA"))
        self.assertEqual(ready["DUCT_SURFACE_AREA"].value, TypedValue(ScalarType.DECIMAL, "60", "SF"))
        self.assertEqual(ready["DUCT_WEIGHT"].value, TypedValue(ScalarType.DECIMAL, "90", "LB"))
        assembly_line = ready["AIRSIDE_EQUIPMENT_ASSEMBLY"]
        self.assertEqual(assembly_line.kind, ResultKind.ASSEMBLY)
        nodes = tuple(walk(assembly_line.assembly))
        self.assertEqual(
            {node.item_code for node in nodes},
            {
                "EQ-AHU", "DUCT-RECT", "ACC-ACCESS-DOOR", "MAT-GALV", "CONN-SLIP",
                "INS-DUCT-WRAP", "SUP-TRAPEZE", "LAB-DUCT", "PRICE-DUCT",
            },
        )
        self.assertTrue(all(line.authority is CandidateAuthority.NON_AUTHORITATIVE_CANDIDATE for line in result.lines))
        self.assertTrue(all(line.pack_sha256 == pack.sha256 and line.bundle_sha256 == bundle.sha256 for line in result.lines))
        self.assertTrue(all(line.source_refs for line in result.lines))
        self.assertIn("RULE-AHU-SKIP", result.skipped_rules)

        false_insulation = load_json("airside_equipment_bundle.json")
        false_insulation["pack_sha256"] = pack.sha256
        next(item for item in false_insulation["observations"] if item["name"] == "INSULATED")["value"] = False  # type: ignore[index,union-attr]
        from helios_takeoff_core.engine.v2 import compile_observation_bundle

        omitted = evaluate_bundle(pack, compile_observation_bundle(false_insulation, pack=pack))
        omitted_assembly = next(line.assembly for line in omitted.lines if line.kind is ResultKind.ASSEMBLY)
        self.assertNotIn("INS-DUCT-WRAP", {node.item_code for node in walk(omitted_assembly)})
        self.assertIn("REL-DUCT-INSULATION", omitted.skipped_relations)

    def test_piping_bundle_preserves_relation_paths_and_diamond_contributions(self) -> None:
        """Global node deduplication or wrong multiplicity undercounts fittings and connections."""
        from helios_takeoff_core.engine.v2 import EvaluationStatus, ResultKind, ScalarType, TypedValue, evaluate_bundle

        pack = compiled_pack()
        bundle = compiled_bundle("piping_bundle.json", pack)
        result = evaluate_bundle(pack, bundle)
        ready = {line.output_claim_type: line for line in result.lines if line.status is EvaluationStatus.READY}
        self.assertEqual(ready["PIPE_LENGTH"].value, TypedValue(ScalarType.DECIMAL, "40", "LF"))
        assembly = next(line.assembly for line in result.lines if line.kind is ResultKind.ASSEMBLY)
        nodes = tuple(walk(assembly))
        fittings = [node for node in nodes if node.item_code == "FIT-ELBOW"]
        valve_connections = [
            node for node in nodes
            if node.item_code == "CONN-SOLDER" and "REL-VALVE-CONNECTION" in node.path
        ]
        material_paths = [node.path for node in nodes if node.item_code == "MAT-COPPER"]
        self.assertEqual(fittings[0].quantity, TypedValue(ScalarType.DECIMAL, "3", "EA"))
        self.assertEqual(valve_connections[0].quantity.value, "2")
        self.assertEqual(len(material_paths), 3)
        self.assertEqual(material_paths, sorted(material_paths))

    def test_missing_input_blocks_complete_candidate_and_project_mode_is_rejected(self) -> None:
        """Partial assemblies and unsupported project identity must never look complete."""
        from helios_takeoff_core.engine.v2 import BundleMode, EvaluationStatus, compile_observation_bundle, evaluate_bundle

        pack = compiled_pack()
        document = load_json("piping_bundle.json")
        document["pack_sha256"] = pack.sha256
        document["observations"] = [item for item in document["observations"] if item["name"] != "LENGTH"]  # type: ignore[index]
        result = evaluate_bundle(pack, compile_observation_bundle(document, pack=pack))
        blocked = [line for line in result.lines if line.status is EvaluationStatus.BLOCKED]
        self.assertTrue(blocked)
        self.assertTrue(all("MISSING_OBSERVATION:LENGTH" in line.blocked_reasons for line in blocked))
        self.assertTrue(all(line.assembly is None for line in blocked))

        project = load_json("piping_bundle.json")
        project["mode"] = BundleMode.PROJECT_BOUND.value
        project["pack_sha256"] = pack.sha256
        with self.assertRaisesRegex(ValidationError, "PROJECT_BOUND"):
            compile_observation_bundle(project, pack=pack)

    def test_compiler_rejects_float_bad_bindings_and_assembly_cycles(self) -> None:
        """Ambient numeric values or unbound graph edges bypass the closed executable model."""
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        floating = load_json(PACK_PATH.name)
        floating["rules"][0]["formula"]["value"] = 0.1  # type: ignore[index]
        with self.assertRaisesRegex(ValidationError, "float"):
            compile_domain_pack(floating)

        unresolved = load_json(PACK_PATH.name)
        unresolved["assembly_edges"][0]["relation_code"] = "REL-MISSING"  # type: ignore[index]
        with self.assertRaisesRegex(ValidationError, "relation"):
            compile_domain_pack(unresolved)

        wrong_type = load_json(PACK_PATH.name)
        wrong_type["assembly_edges"][0]["relation_code"] = "REL-AHU-COUNT-RULE"  # type: ignore[index]
        with self.assertRaisesRegex(ValidationError, "assembly relation"):
            compile_domain_pack(wrong_type)

        cycle = load_json(PACK_PATH.name)
        citation = deepcopy(cycle["relations"][0]["citations"])  # type: ignore[index]
        cycle["relations"].append({  # type: ignore[union-attr]
            "code": "REL-DUCT-AHU-CYCLE", "kind": "COMPOSED_OF",
            "from_code": "DUCT-RECT", "to_code": "EQ-AHU", "citations": citation,
        })
        cycle["assembly_edges"].append({  # type: ignore[union-attr]
            "code": "EDGE-DUCT-AHU-CYCLE", "relation_code": "REL-DUCT-AHU-CYCLE",
            "quantity": {"op": "LITERAL", "value_type": "DECIMAL", "value": "1"},
            "uom": "EA", "condition": None,
            "source_refs": deepcopy(cycle["assembly_edges"][0]["source_refs"]),  # type: ignore[index]
        })
        with self.assertRaisesRegex(ValidationError, "cycle"):
            compile_domain_pack(cycle)

    def test_executable_pack_reopens_with_all_indexes_and_results_are_immutable(self) -> None:
        """Ignoring executable indexes on reopen or exposing mutable results breaks replay."""
        from helios_takeoff_core.engine.v2 import EngineStore, evaluate_bundle

        pack = compiled_pack()
        bundle = compiled_bundle("airside_equipment_bundle.json", pack)
        with tempfile.TemporaryDirectory() as root:
            store = EngineStore(Path(root) / "engine.sqlite3")
            store.initialize()
            store.import_pack(pack)
            reopened = store.load_pack(pack_sha256=pack.sha256)
            self.assertEqual(reopened.canonical_bytes, pack.canonical_bytes)
            self.assertEqual(tuple(reopened.units_by_uom), tuple(pack.units_by_uom))
            self.assertEqual(tuple(reopened.observations_by_name), tuple(pack.observations_by_name))
            self.assertEqual(tuple(reopened.lookups_by_code), tuple(pack.lookups_by_code))
            self.assertEqual(tuple(reopened.assembly_edges_by_code), tuple(pack.assembly_edges_by_code))
            self.assertEqual(tuple(reopened.rules_by_code), tuple(pack.rules_by_code))
            self.assertTrue(store.verify_integrity().valid)
        result = evaluate_bundle(pack, bundle)
        with self.assertRaises(FrozenInstanceError):
            result.sha256 = "0" * 64  # type: ignore[misc]

    def test_evaluator_rebinds_pack_and_bundle_projections_to_claimed_digests(self) -> None:
        """Frozen wrappers cannot make forged projections or observations share a legitimate digest."""
        from helios_takeoff_core.engine.v2 import evaluate_bundle

        pack = compiled_pack()
        bundle = compiled_bundle("airside_equipment_bundle.json", pack)
        with self.assertRaisesRegex(ValidationError, "projections"):
            evaluate_bundle(pack=replace(pack, rules_by_code={}), bundle=bundle)

        forged_observation = replace(bundle.observations[0], value=2)
        forged_bundle = replace(
            bundle,
            observations=(forged_observation, *bundle.observations[1:]),
        )
        with self.assertRaisesRegex(ValidationError, "canonical digest"):
            evaluate_bundle(pack, forged_bundle)

    def test_compiler_and_runtime_share_executable_shape_and_formula_typing(self) -> None:
        """Compilation rejects runtime-impossible shapes and derived unit relabeling."""
        from helios_takeoff_core.engine.v2 import compile_domain_pack

        missing_edge_uom = load_json(PACK_PATH.name)
        missing_edge_uom["assembly_edges"][0]["uom"] = None  # type: ignore[index]
        with self.assertRaisesRegex(ValidationError, "output unit"):
            compile_domain_pack(missing_edge_uom)

        nonnumeric_assembly = load_json(PACK_PATH.name)
        assembly_rule = next(  # type: ignore[union-attr]
            rule for rule in nonnumeric_assembly["rules"] if rule["code"] == "RULE-AHU-ASSEMBLY"
        )
        assembly_rule.update({
            "output_scalar_type": "BOOLEAN",
            "output_dimension": "DIMENSIONLESS",
            "output_uom": None,
            "formula": {"op": "LITERAL", "value_type": "BOOLEAN", "value": True},
        })
        with self.assertRaisesRegex(ValidationError, "numeric unit-bearing"):
            compile_domain_pack(nonnumeric_assembly)

        derived_relabel = load_json(PACK_PATH.name)
        length_rule = next(  # type: ignore[union-attr]
            rule for rule in derived_relabel["rules"] if rule["code"] == "RULE-PIPE-LENGTH"
        )
        length_rule["formula"] = {
            "op": "DIVIDE",
            "left": {"op": "OBSERVATION", "name": "LENGTH"},
            "right": {"op": "OBSERVATION", "name": "LENGTH"},
        }
        with self.assertRaisesRegex(ValidationError, "dimension"):
            compile_domain_pack(derived_relabel)

        wrong_accepted_dimension = load_json(PACK_PATH.name)
        length_observation = next(  # type: ignore[union-attr]
            item for item in wrong_accepted_dimension["observations"] if item["name"] == "LENGTH"
        )
        length_observation["accepted_uoms"].append("EA")
        with self.assertRaisesRegex(ValidationError, "accepted_uoms"):
            compile_domain_pack(wrong_accepted_dimension)

        dimensional_text_observation = load_json(PACK_PATH.name)
        gauge_observation = next(  # type: ignore[union-attr]
            item for item in dimensional_text_observation["observations"] if item["name"] == "GAUGE"
        )
        gauge_observation["dimension"] = "LENGTH"
        with self.assertRaisesRegex(ValidationError, "unitless and dimensionless"):
            compile_domain_pack(dimensional_text_observation)

        dimensional_boolean_lookup = load_json(PACK_PATH.name)
        lookup = dimensional_boolean_lookup["lookups"][0]  # type: ignore[index]
        lookup.update({
            "value_type": "BOOLEAN",
            "dimension": "LENGTH",
            "uom": None,
            "entries": [{"keys": ["24"], "value": True}],
        })
        with self.assertRaisesRegex(ValidationError, "lookup output"):
            compile_domain_pack(dimensional_boolean_lookup)

        dimensional_text_rule = load_json(PACK_PATH.name)
        skip_rule = next(  # type: ignore[union-attr]
            item for item in dimensional_text_rule["rules"] if item["code"] == "RULE-AHU-SKIP"
        )
        skip_rule.update({
            "result_kind": "RECONCILIATION",
            "output_scalar_type": "TEXT",
            "output_dimension": "LENGTH",
            "output_uom": None,
            "formula": {"op": "LITERAL", "value_type": "TEXT", "value": "candidate"},
        })
        with self.assertRaisesRegex(ValidationError, "dimensionless"):
            compile_domain_pack(dimensional_text_rule)

    def test_units_are_one_closed_family_and_bounds_use_canonical_units(self) -> None:
        """Separate bases cannot masquerade as convertible; bounds are canonical-unit values."""
        from helios_takeoff_core.engine.v2 import compile_domain_pack, compile_observation_bundle

        unrelated_base = load_json(PACK_PATH.name)
        unrelated_base["units"].append({  # type: ignore[union-attr]
            "uom": "IN", "dimension": "LENGTH", "base_uom": "IN",
            "to_base_numerator": "1", "to_base_denominator": "1",
        })
        with self.assertRaisesRegex(ValidationError, "coherent base"):
            compile_domain_pack(unrelated_base)

        convertible = load_json(PACK_PATH.name)
        convertible["units"].append({  # type: ignore[union-attr]
            "uom": "IN", "dimension": "LENGTH", "base_uom": "LF",
            "to_base_numerator": "1", "to_base_denominator": "12",
        })
        length_definition = next(  # type: ignore[union-attr]
            item for item in convertible["observations"] if item["name"] == "LENGTH"
        )
        length_definition["accepted_uoms"].append("IN")
        length_definition["bounds"]["maximum"] = "10"
        pack = compile_domain_pack(convertible)

        at_bound = load_json("piping_bundle.json")
        at_bound["pack_sha256"] = pack.sha256
        length_value = next(  # type: ignore[union-attr]
            item for item in at_bound["observations"] if item["name"] == "LENGTH"
        )
        length_value.update({"value": "120", "uom": "IN"})
        compiled = compile_observation_bundle(at_bound, pack=pack)
        self.assertEqual(next(item for item in compiled.observations if item.name == "LENGTH").value, "120")

        over_bound = deepcopy(at_bound)
        next(  # type: ignore[union-attr]
            item for item in over_bound["observations"] if item["name"] == "LENGTH"
        )["value"] = "121"
        with self.assertRaisesRegex(ValidationError, "above its maximum"):
            compile_observation_bundle(over_bound, pack=pack)

    def test_assembly_provenance_survives_success_false_zero_and_blocked_paths(self) -> None:
        """Every consulted graph assertion remains cited even when it emits no child."""
        from helios_takeoff_core.engine.v2 import (
            EvaluationStatus,
            ResultKind,
            compile_domain_pack,
            compile_observation_bundle,
            evaluate_bundle,
        )

        document = load_json(PACK_PATH.name)
        source_refs = deepcopy(document["lookups"][0]["source_refs"])  # type: ignore[index]
        source_refs[0]["locator"] = "lookup-insulation-condition"
        document["lookups"].append({  # type: ignore[union-attr]
            "code": "INCLUDE-INSULATION",
            "key_types": ["BOOLEAN"],
            "value_type": "BOOLEAN",
            "dimension": "DIMENSIONLESS",
            "uom": None,
            "entries": [
                {"keys": [False], "value": False},
                {"keys": [True], "value": True},
            ],
            "source_refs": source_refs,
        })
        insulation_edge = next(  # type: ignore[union-attr]
            edge for edge in document["assembly_edges"] if edge["code"] == "EDGE-DUCT-INSULATION"
        )
        insulation_edge["condition"] = {
            "op": "LOOKUP",
            "lookup_code": "INCLUDE-INSULATION",
            "keys": [{"op": "OBSERVATION", "name": "INSULATED"}],
        }
        pack = compile_domain_pack(document)

        def assembly_line(bundle_document: dict[str, object]):
            bundle_document["pack_sha256"] = pack.sha256
            result = evaluate_bundle(
                pack, compile_observation_bundle(bundle_document, pack=pack)
            )
            return next(line for line in result.lines if line.kind is ResultKind.ASSEMBLY)

        successful = assembly_line(load_json("airside_equipment_bundle.json"))
        successful_locators = {ref.locator for ref in successful.source_refs}
        self.assertTrue({
            "lookup-insulation-condition", "edge-duct-insulation",
            "rel-duct-insulation", "duct-insulation",
        }.issubset(successful_locators))
        duct_node = next(node for node in walk(successful.assembly) if node.item_code == "DUCT-RECT")
        self.assertEqual({ref.locator for ref in duct_node.source_refs}, {"duct"})
        insulation_node = next(
            node for node in walk(successful.assembly) if node.item_code == "INS-DUCT-WRAP"
        )
        self.assertEqual({ref.locator for ref in insulation_node.source_refs}, {"duct-insulation"})

        false_document = load_json("airside_equipment_bundle.json")
        next(  # type: ignore[union-attr]
            item for item in false_document["observations"] if item["name"] == "INSULATED"
        )["value"] = False
        false_line = assembly_line(false_document)
        self.assertEqual(false_line.status, EvaluationStatus.READY)
        self.assertTrue({
            "lookup-insulation-condition", "edge-duct-insulation",
            "rel-duct-insulation", "duct-insulation",
        }.issubset({ref.locator for ref in false_line.source_refs}))

        zero_document = deepcopy(document)
        zero_refs = deepcopy(source_refs)
        zero_refs[0]["locator"] = "lookup-zero-quantity"
        zero_document["lookups"].append({  # type: ignore[union-attr]
            "code": "INSULATION-AREA",
            "key_types": ["BOOLEAN"],
            "value_type": "DECIMAL",
            "dimension": "AREA",
            "uom": "SF",
            "entries": [
                {"keys": [False], "value": "0"},
                {"keys": [True], "value": "0"},
            ],
            "source_refs": zero_refs,
        })
        zero_edge = next(  # type: ignore[union-attr]
            edge for edge in zero_document["assembly_edges"] if edge["code"] == "EDGE-DUCT-INSULATION"
        )
        zero_edge["quantity"] = {
            "op": "LOOKUP",
            "lookup_code": "INSULATION-AREA",
            "keys": [{"op": "OBSERVATION", "name": "INSULATED"}],
        }
        zero_pack = compile_domain_pack(zero_document)
        zero_bundle_document = load_json("airside_equipment_bundle.json")
        zero_bundle_document["pack_sha256"] = zero_pack.sha256
        zero_result = evaluate_bundle(
            zero_pack,
            compile_observation_bundle(zero_bundle_document, pack=zero_pack),
        )
        zero_line = next(line for line in zero_result.lines if line.kind is ResultKind.ASSEMBLY)
        self.assertIn("lookup-zero-quantity", {ref.locator for ref in zero_line.source_refs})
        self.assertNotIn("INS-DUCT-WRAP", {node.item_code for node in walk(zero_line.assembly)})

        blocked_document = load_json("airside_equipment_bundle.json")
        blocked_document["pack_sha256"] = zero_pack.sha256
        blocked_document["observations"] = [  # type: ignore[index]
            item for item in blocked_document["observations"] if item["name"] != "INSULATED"
        ]
        blocked_result = evaluate_bundle(
            zero_pack,
            compile_observation_bundle(blocked_document, pack=zero_pack),
        )
        blocked_line = next(line for line in blocked_result.lines if line.kind is ResultKind.ASSEMBLY)
        self.assertEqual(blocked_line.status, EvaluationStatus.BLOCKED)
        self.assertIn("lookup-insulation-condition", {ref.locator for ref in blocked_line.source_refs})
        condition_step = next(
            step
            for step in blocked_line.formula_trace.root["steps"]
            if step["stage"] == "ASSEMBLY_CONDITION"
            and step["relation_code"] == "REL-DUCT-INSULATION"
        )
        self.assertEqual(
            condition_step["trace"]["used_lookups"][0]["lookup_code"],
            "INCLUDE-INSULATION",
        )

    def test_region_schema_matches_the_compiler_canonical_mapping(self) -> None:
        """Structural preflight and semantic compilation accept the same nested region shape."""
        from jsonschema import Draft202012Validator
        from helios_takeoff_core.engine.v2 import compile_observation_bundle

        pack = compiled_pack()
        document = load_json("airside_equipment_bundle.json")
        document["pack_sha256"] = pack.sha256
        region = {
            "page": 1,
            "bbox": ["10", "20", "30", "40"],
            "rotated": False,
            "metadata": {"sheet": "M-101", "tags": ["equipment", "benchmark"]},
        }
        document["observations"][0]["region"] = region  # type: ignore[index]
        schema_path = (
            ROOT / "src" / "helios_takeoff_core" / "engine" / "v2"
            / "schemas" / "observation-bundle-v1.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(document)
        bundle = compile_observation_bundle(document, pack=pack)
        compiled_region = bundle.observations[0].region
        self.assertEqual(compiled_region["page"], 1)
        self.assertEqual(compiled_region["bbox"], ("10", "20", "30", "40"))
        self.assertEqual(compiled_region["metadata"]["sheet"], "M-101")  # type: ignore[index]

    def test_invalid_quantities_and_integer_division_are_deterministically_blocked(self) -> None:
        """Negative or fractional counts block complete candidates without partial assemblies."""
        from helios_takeoff_core.engine.v2 import (
            EvaluationStatus,
            ResultKind,
            compile_domain_pack,
            compile_observation_bundle,
            evaluate_bundle,
        )

        negative_document = load_json(PACK_PATH.name)
        negative_rule = next(  # type: ignore[union-attr]
            rule for rule in negative_document["rules"] if rule["code"] == "RULE-PIPE-LENGTH"
        )
        negative_rule["formula"] = {"op": "LITERAL", "value_type": "DECIMAL", "value": "-1"}
        negative_pack = compile_domain_pack(negative_document)
        piping = load_json("piping_bundle.json")
        piping["pack_sha256"] = negative_pack.sha256
        negative_result = evaluate_bundle(
            negative_pack,
            compile_observation_bundle(piping, pack=negative_pack),
        )
        negative_line = next(
            line for line in negative_result.lines if line.output_claim_type == "PIPE_LENGTH"
        )
        self.assertEqual(negative_line.status, EvaluationStatus.BLOCKED)
        self.assertEqual(negative_line.blocked_reasons, ("NEGATIVE_QUANTITY",))

        division_document = load_json(PACK_PATH.name)
        count_rule = next(  # type: ignore[union-attr]
            rule for rule in division_document["rules"] if rule["code"] == "RULE-AHU-COUNT"
        )
        count_rule["output_scalar_type"] = "DECIMAL"
        count_rule["formula"] = {
            "op": "DIVIDE",
            "left": {"op": "OBSERVATION", "name": "COUNT"},
            "right": {"op": "LITERAL", "value_type": "INTEGER", "value": 2},
        }
        division_pack = compile_domain_pack(division_document)
        airside = load_json("airside_equipment_bundle.json")
        airside["pack_sha256"] = division_pack.sha256
        division_result = evaluate_bundle(
            division_pack,
            compile_observation_bundle(airside, pack=division_pack),
        )
        count_line = next(
            line for line in division_result.lines if line.output_claim_type == "EQUIPMENT_COUNT"
        )
        self.assertEqual(count_line.status, EvaluationStatus.BLOCKED)
        self.assertEqual(count_line.blocked_reasons, ("NON_INTEGRAL_COUNT",))

        negative_edge_document = load_json(PACK_PATH.name)
        negative_fitting_edge = next(  # type: ignore[union-attr]
            edge for edge in negative_edge_document["assembly_edges"] if edge["code"] == "EDGE-PIPE-FITTING"
        )
        negative_fitting_edge["quantity"]["value"] = "-0.075"
        negative_edge_pack = compile_domain_pack(negative_edge_document)
        piping["pack_sha256"] = negative_edge_pack.sha256
        negative_edge_result = evaluate_bundle(
            negative_edge_pack,
            compile_observation_bundle(piping, pack=negative_edge_pack),
        )
        negative_assembly = next(
            line for line in negative_edge_result.lines if line.kind is ResultKind.ASSEMBLY
        )
        self.assertEqual(negative_assembly.status, EvaluationStatus.BLOCKED)
        self.assertEqual(negative_assembly.blocked_reasons, ("NEGATIVE_MULTIPLICITY",))
        self.assertIsNone(negative_assembly.assembly)

        fractional_document = load_json(PACK_PATH.name)
        fitting_edge = next(  # type: ignore[union-attr]
            edge for edge in fractional_document["assembly_edges"] if edge["code"] == "EDGE-PIPE-FITTING"
        )
        fitting_edge["quantity"]["value"] = "0.0125"
        fractional_pack = compile_domain_pack(fractional_document)
        piping["pack_sha256"] = fractional_pack.sha256
        fractional_result = evaluate_bundle(
            fractional_pack,
            compile_observation_bundle(piping, pack=fractional_pack),
        )
        assembly_line = next(line for line in fractional_result.lines if line.kind is ResultKind.ASSEMBLY)
        self.assertEqual(assembly_line.status, EvaluationStatus.BLOCKED)
        self.assertEqual(assembly_line.blocked_reasons, ("NON_INTEGRAL_COUNT",))
        self.assertIsNone(assembly_line.assembly)


if __name__ == "__main__":
    unittest.main()
