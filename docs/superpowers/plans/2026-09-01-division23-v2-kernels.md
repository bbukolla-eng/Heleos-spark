# Division 23 V2 Kernels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the isolated `helios.engine.domain-pack/v2` registry and deterministic structured-input airside, piping, and equipment kernels while preserving every v1 byte, digest, evaluation result, and P0/P1A authority boundary.

**Architecture:** V2 is a new package under `helios_takeoff_core.engine.v2`; it does not branch inside or reinterpret the existing v1 compiler, repository, evaluator, CLI, or migration 017. A strict compiler turns source-cited JSON packs into immutable canonical blobs, an isolated `EngineStore` persists only those blobs plus rebuildable indexes in its own database, and a pure evaluator turns a versioned `ObservationBundle` into an immutable `EngineResultSet`. Three installed JSON packs provide the first airside, piping, and equipment/scope proofs. The optional P0 adapter derives extraction-claim requests from ready project-bound result lines but contains no service, quantity, approval, or release logic.

**Tech Stack:** Python 3.12+, standard-library `dataclasses`, `enum.StrEnum`, `decimal.Decimal`, `fractions.Fraction`, `hashlib`, `json`, `sqlite3`, `typing.Protocol`, SQLite JSON1, `unittest`, setuptools package data. No new runtime dependency and no network dependency.

**Spec:** `docs/superpowers/specs/2026-09-01-enriched-build-fabric-design.md`, especially sections 3.4, 6, 9, 13 Deliverable B, 14, 15, and 17.

## Global Constraints

- The v2 protocol is exactly `helios.engine.domain-pack/v2`; the bundle protocol is exactly `helios.engine.observation-bundle/v1`; the result protocol is exactly `helios.engine.result-set/v1`; the capability-matrix protocol is exactly `helios.engine.capability-matrix/v1`.
- `src/helios_takeoff_core/engine/compiler.py`, `contracts.py`, `evaluator.py`, `repository.py`, `acceptance.py`, `src/helios_takeoff_core/engine_cli.py`, and `src/helios_takeoff_core/migrations/017_engine_domain_packs.sql` remain unchanged.
- The current v1 demo pack digest remains `cbb6545bef2bf7762888de66e8e57cc5b9b16af590605ea839249c30133edc3e`; migration 017 remains SHA-256 `30fe60e8bf6a3a506fa0a16cb557cb4f06b3a2b4305963773018aa5a5acfc89d`.
- Build Fabric Tasks 1-9 are a hard prerequisite for the first v2 production edit. `python -m tools.helios_build report` must report `BUILD_FABRIC_CORE_CODE_COMPLETE: true`; before that point only the read-only v1 compatibility lock in Task 1 Step 0 may run.
- Before any Task 1 test or production file is created or edited, Codex freezes and content-addresses the substantive `DELIVERABLE_B_DIV23_V2` TaskManifest and graph described in Task 1. Retroactive manifest, graph, handoff, review, integration, or installed-code receipts do not count.
- The real Task 1 patch must use one honest Build Fabric transport: exact-available `LOCAL_ADAPTER`, or an identified real `EXTERNAL_SESSION`. A missing/unavailable local adapter reroutes through the CLI with `LOCAL_ADAPTER_UNAVAILABLE`; a terminal local attempt requires a successor task and `LOCAL_ADAPTER_BLOCKED_SUCCESSOR`. If the Build Fabric CLI itself, a real external session, or a distinct reviewer is unavailable, the task is `BLOCKED`; direct editing or manual receipt fabrication is not a fallback.
- V2 uses `EngineStore` and `engine/v2/migrations/` only. It is never initialized through `helios_takeoff_core.db.Database` and rejects a path containing `schema_migrations`, a P0/P1A authority table, a v1 engine table, or any unknown user table.
- One immutable canonical pack blob is authoritative. Source, catalog, rule, lookup, and relation indexes are disposable projections that `EngineStore.rebuild_indexes()` can recreate exactly.
- Every evaluation and store lookup uses an explicit pack SHA-256. There is no latest-pack lookup, implicit activation, approval, or promotion.
- Pack compilation and evaluation are pure and make no database, filesystem, browser, provider, model, or network call.
- All numeric input and formula literals are plain decimal strings or integers. Python/JSON floats, exponent notation, NaN, infinity, implicit rounding, and binary floating-point arithmetic are rejected.
- Calculations keep exact `Decimal` values. Unit factors are exact positive rational numerator/denominator pairs. A conversion that cannot produce a finite exact Decimal blocks with `INEXACT_UNIT_CONVERSION` instead of rounding.
- Definition arrays and reference sets are canonicalized by stable identity. Formula argument order and ordered assembly paths retain semantic order.
- Catalog items, formula lookups, rules, and assembly edges have non-empty per-definition citations. Every `source_id` and source identity resolves inside the same pack.
- BENCHMARK bundles reference a content-addressed structured artifact and contain no P0 identity or evidence ID. PROJECT_BOUND bundles reference a pre-existing project, extraction run, instance subject, primary evidence item, and canonical evidence set.
- Missing input produces a blocked result line. The evaluator never supplies a default that is not in the pack.
- This plan ingests no drawing, PDF, CAD, OCR, model output stream, private bid document, or live project packet. Fixtures are hand-authored structured JSON.
- Airside, piping, and equipment pack work depends on accepted source records, not specifically on ATHENA. A source record is eligible through either `ATHENA_SOURCE_PACKET` or an independently governed `DIRECT_PRIMARY` original-source verification and named build-task acceptance. A missing accepted source record blocks only the affected lane; Tasks 1-5 and 10 continue and may merge without claiming that lane accepted.
- SourcePacket bodies resolve only at `build_control/source_packets/sha256/{packet_sha256}.json` or through the frozen research-registry lookup. A flat `build_control/source_packets/{packet_sha256}.json` path is invalid.
- The packaged capability matrix starts truthfully at `EXPERIMENTAL` only for the bounded structured proofs and `NOT_STARTED` elsewhere. Full Division 23 coverage is rejected unless every required matrix entry is `PRODUCTION_APPROVED`.
- Focused commands target five minutes, the affected integration gate targets ten minutes, and the final milestone gate targets twenty minutes. An unchanged failure is not rerun; a substantive correction gets at most one verification rerun.
- No model, pack, evaluator, adapter, or acceptance helper may create quantity assertions, reviews, takeoff snapshots, quotes, estimates, approvals, releases, or P1A rows.

## Frozen Public Contract

The implementation must use these exact top-level pack fields:

```json
{
  "protocol": "helios.engine.domain-pack/v2",
  "pack_code": "DIV23-AIRSIDE",
  "version": "2.0.0",
  "title": "Division 23 airside structured rules",
  "jurisdiction": "US",
  "sources": [],
  "units": [],
  "catalog": [],
  "observations": [],
  "lookups": [],
  "assembly_edges": [],
  "rules": []
}
```

The strict definition shapes are:

| Definition | Exact fields | Closed behavior |
|---|---|---|
| Source | `source_id`, `admission_channel`, `source_packet_id`, `direct_admission_id`, `original_verification_id`, `accepted_for_build_event_sha256`, `accepted_for_build_task_id`, `authority_class`, `stable_reference`, `content_sha256`, `publisher`, `title`, `retrieval_date`, `jurisdiction` | Channel is `ATHENA_SOURCE_PACKET` or `DIRECT_PRIMARY`. Exactly one ingress ID is non-null; both channels require original-verification and accepted-for-build artifacts. `DIRECT_PRIMARY` also requires `PRIMARY_PUBLIC`. Digests are lowercase SHA-256 or `null` only where declared nullable. |
| SourceRef | `source_id`, `source_identity`, `locator`, `jurisdiction`, `applicability`, `limitations` | Identity is `sha256:<digest>` when a digest exists, otherwise `reference:<stable_reference>`. |
| Unit | `uom`, `dimension`, `base_uom`, `to_base_numerator`, `to_base_denominator` | Factor is positive and both integer strings are canonical. |
| CatalogItem | `code`, `lane`, `type`, `title`, `dimension`, `default_uom`, `source_refs` | Lane is `AIRSIDE`, `PIPING`, or `EQUIPMENT`; citations are non-empty. |
| ObservationDefinition | `name`, `scalar_type`, `dimension`, `canonical_uom`, `accepted_uoms`, `enum_values`, `bounds`, `evidence_cardinality` | Scalar type is `DECIMAL`, `INTEGER`, `BOOLEAN`, `TEXT`, or closed `ENUM`. |
| Lookup | `code`, `key_types`, `value_type`, `dimension`, `uom`, `entries`, `source_refs` | Entry shape is `keys`, `value`; key tuples are unique. |
| AssemblyEdge | `code`, `from_code`, `relation`, `to_code`, `quantity`, `uom`, `condition`, `source_refs` | Quantity is a formula AST; condition is a Boolean AST or `null`. |
| Rule | `code`, `lane`, `subject_code`, `result_kind`, `calculation_basis`, `output_claim_type`, `output_scalar_type`, `output_dimension`, `output_uom`, `condition`, `formula`, `source_refs` | Kind is `QUANTITY`, `RECONCILIATION`, or `ASSEMBLY`; basis is `MEASURED`, `RULE_DERIVED`, or `ALLOWANCE`. |

Allowed formula nodes are exact tagged objects:

```text
LITERAL(value_type, value)
OBSERVATION(name)
LOOKUP(lookup_code, keys)
ADD(left, right)
SUBTRACT(left, right)
MULTIPLY(left, right)
DIVIDE(left, right)
COMPARE(operator, left, right)
IF(condition, then, else)
MIN(args)
MAX(args)
```

`COMPARE.operator` is one of `EQ`, `NE`, `LT`, `LE`, `GT`, or `GE`. Each AST is limited to depth 32 and 256 nodes; one pack is limited to 4096 total AST nodes. No other operation or field is valid.

The task dependency graph is:

```text
Task 1 contracts/schema/v1 gate
  -> Task 2 formulas/compiler
       -> Task 3 isolated store
       -> Task 4 bundle/result evaluator
          -> Task 5 assembly multiplicity
             -> Tasks 6, 7, 8 lane packs (parallel after accepted source records exist)
                -> Task 9 packaged capability matrix
          -> Task 10 P0 adapter boundary (independent of lane source records)
Tasks 3, 9, and 10 -> Task 11 installed integration gate
```

Task 5 freezes assembly multiplicity after Task 4 and before the three lane packs. Tasks 1-5 and 10 are not delayed by source availability. The ATHENA `ResearchRequest → SourcePacket` chain remains mandatory for the separate research-roundtrip receipt, but a lane pack may use a separately accepted DIRECT_PRIMARY record without fabricating that chain.

---

### Task 1: Freeze V1 Compatibility and Add V2 Contracts

**Files:**

- Create: `src/helios_takeoff_core/engine/v2/__init__.py`
- Create: `src/helios_takeoff_core/engine/v2/contracts.py`
- Create: `src/helios_takeoff_core/engine/v2/schemas/domain-pack-v2.schema.json`
- Create: `src/helios_takeoff_core/engine/v2/schemas/observation-bundle-v1.schema.json`
- Create: `tests/test_engine_v1_compatibility.py`
- Create: `tests/test_engine_v2_contracts.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_package_metadata.py`
- Create before builder work: `build_control/tasks/deliverable-b-div23-v2-contracts.json`
- Create through Build Fabric: the content-addressed TaskManifest under `build_control/tasks/sha256/{digest[0:2]}/{digest}.json`, graph nodes/events under `build_control/graph/events.jsonl`, and sanitized receipts under `build_control/graph/receipts/`

**Interfaces:**

- Consumes: existing `helios_takeoff_core.errors.ValidationError` and v1 public imports.
- Produces: frozen enums/dataclasses, protocol constants, canonical JSON scalar aliases, and package-data availability used by every later task.

- [ ] **Step 0: Freeze the Build Fabric self-use checkpoint before any v2 edit**

The only permitted pre-freeze check is this read-only v1 lock:

```bash
python -c "from hashlib import sha256; from pathlib import Path; from helios_takeoff_core.engine.acceptance import DEMO_DOMAIN_PACK; from helios_takeoff_core.engine.compiler import compile_domain_pack; assert compile_domain_pack(DEMO_DOMAIN_PACK).sha256 == 'cbb6545bef2bf7762888de66e8e57cc5b9b16af590605ea839249c30133edc3e'; assert sha256(Path('src/helios_takeoff_core/migrations/017_engine_domain_packs.sql').read_bytes()).hexdigest() == '30fe60e8bf6a3a506fa0a16cb557cb4f06b3a2b4305963773018aa5a5acfc89d'"
python -m tools.helios_build report
```

Require `BUILD_FABRIC_CORE_CODE_COMPLETE: true`. Then author `build_control/tasks/deliverable-b-div23-v2-contracts.json` with `task_id: division23-v2-contracts`, `capability_id: DELIVERABLE_B_DIV23_V2`, the 40-hex output of `git rev-parse HEAD` as `base_commit_sha` (never the symbolic name `HEAD`), builder `codex-builder-v1`, distinct reviewer `codex-reviewer-v1`, integrator `codex-control-v1`, exact ownership of the Task 1 files above, the three frozen protocol IDs, the two schema IDs, no required source packets, the RED/GREEN commands below, budgets of 300/600/1200 seconds, and one correction round. Freeze graph ID `division23-v2-contracts` with `BuildTask`, `Review`, and `Integration` nodes, with Review depending on BuildTask and Integration depending on Review. Validate/content-address the task and record both emitted hashes before dispatch:

```bash
python -m tools.helios_build task validate build_control/tasks/deliverable-b-div23-v2-contracts.json
python -m tools.helios_build graph status
```

Route the patch only after that freeze. Use `doctor`, `dispatch`, and `collect` for an exactly available `LOCAL_ADAPTER`; otherwise use `external-session begin` with the actual provider/session ID and `LOCAL_ADAPTER_UNAVAILABLE`, followed by `external-session import-handoff`. A terminal local attempt first creates a successor manifest and uses `LOCAL_ADAPTER_BLOCKED_SUCCESSOR`. Review through a different profile/session using `review` or `external-session import-review`. Unavailable `tools.helios_build` means `BLOCKED`, not direct work.

- [ ] **Step 1: Write the failing v1 isolation and v2 contract tests**

```python
class EngineV1CompatibilityTests(unittest.TestCase):
    def test_v1_golden_identity_and_migration_remain_unchanged(self) -> None:
        compiled = compile_domain_pack(deepcopy(DEMO_DOMAIN_PACK))
        self.assertEqual(
            compiled.sha256,
            "cbb6545bef2bf7762888de66e8e57cc5b9b16af590605ea839249c30133edc3e",
        )
        migration = Path(
            "src/helios_takeoff_core/migrations/017_engine_domain_packs.sql"
        ).read_bytes()
        self.assertEqual(
            hashlib.sha256(migration).hexdigest(),
            "30fe60e8bf6a3a506fa0a16cb557cb4f06b3a2b4305963773018aa5a5acfc89d",
        )
        self.assertEqual(DOMAIN_PACK_PROTOCOL, "helios.p1b.domain-pack/v1")

class EngineV2ContractTests(unittest.TestCase):
    def test_protocols_and_dataclasses_are_frozen(self) -> None:
        self.assertEqual(DOMAIN_PACK_PROTOCOL_V2, "helios.engine.domain-pack/v2")
        self.assertEqual(OBSERVATION_BUNDLE_PROTOCOL, "helios.engine.observation-bundle/v1")
        self.assertEqual(RESULT_SET_PROTOCOL, "helios.engine.result-set/v1")
        value = TypedValue(ScalarType.DECIMAL, "1.25", "LF")
        with self.assertRaises(FrozenInstanceError):
            value.value = "2"  # type: ignore[misc]
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m unittest tests.test_engine_v1_compatibility tests.test_engine_v2_contracts -v`

Expected: the v1 assertions pass and the v2 test fails because `helios_takeoff_core.engine.v2` does not exist.

- [ ] **Step 3: Implement the frozen public types**

`contracts.py` must define these signatures exactly:

```python
DOMAIN_PACK_PROTOCOL_V2 = "helios.engine.domain-pack/v2"
OBSERVATION_BUNDLE_PROTOCOL = "helios.engine.observation-bundle/v1"
RESULT_SET_PROTOCOL = "helios.engine.result-set/v1"

class ScalarType(StrEnum):
    DECIMAL = "DECIMAL"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"
    TEXT = "TEXT"
    ENUM = "ENUM"

class BundleMode(StrEnum):
    BENCHMARK = "BENCHMARK"
    PROJECT_BOUND = "PROJECT_BOUND"

class WorkState(StrEnum):
    NEW = "NEW"
    DEMOLITION = "DEMOLITION"
    EXISTING = "EXISTING"
    RELOCATE = "RELOCATE"
    REUSE = "REUSE"

class CommercialDimension(StrEnum):
    BASE = "BASE"
    ALTERNATE = "ALTERNATE"
    ALLOWANCE = "ALLOWANCE"
    UNIT_PRICE = "UNIT_PRICE"

class ObservationOrigin(StrEnum):
    HUMAN = "HUMAN"
    STRUCTURED_IMPORT = "STRUCTURED_IMPORT"
    MODEL = "MODEL"

class EvaluationStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"

class ResultKind(StrEnum):
    QUANTITY = "QUANTITY"
    RECONCILIATION = "RECONCILIATION"
    ASSEMBLY = "ASSEMBLY"

class CalculationBasis(StrEnum):
    MEASURED = "MEASURED"
    RULE_DERIVED = "RULE_DERIVED"
    ALLOWANCE = "ALLOWANCE"

@dataclass(frozen=True)
class SourceRef:
    source_id: str
    source_identity: str
    locator: str
    jurisdiction: str
    applicability: str
    limitations: str

@dataclass(frozen=True)
class CompiledDomainPack:
    canonical_document: Mapping[str, object]
    canonical_bytes: bytes
    sha256: str
    sources_by_id: Mapping[str, Mapping[str, object]]
    catalog_by_code: Mapping[str, Mapping[str, object]]
    observations_by_name: Mapping[str, Mapping[str, object]]
    lookups_by_code: Mapping[str, Mapping[str, object]]
    rules_by_code: Mapping[str, Mapping[str, object]]
    assembly_edges_by_code: Mapping[str, Mapping[str, object]]

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
    root: Mapping[str, object] | None
    missing_observations: tuple[str, ...]
    sha256: str

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
    sha256: str
```

`__init__.py` exports only these v2 types and later v2 entry functions; it does not re-export or shadow the v1 `DOMAIN_PACK_PROTOCOL` name.

- [ ] **Step 4: Add strict packaged schemas and package-data declarations**

Both schemas use draft 2020-12, `additionalProperties: false` at every object, closed enums, lowercase 64-character digest patterns, and `oneOf` branches for BENCHMARK and PROJECT_BOUND. Add these package-data paths without changing the v1 console entry point or schema version:

```toml
[tool.setuptools.package-data]
helios_takeoff_core = [
  "migrations/*.sql",
  "engine/v2/migrations/*.sql",
  "engine/v2/schemas/*.json",
  "engine/v2/packs/*.json",
]
```

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run: `python -m unittest tests.test_engine_v1_compatibility tests.test_engine_v2_contracts tests.test_package_metadata -v`

Expected: PASS; the v1 hashes are unchanged and both schema resources resolve through `importlib.resources.files("helios_takeoff_core.engine.v2")`.

- [ ] **Step 6: Commit the contract boundary**

Only `codex-control-v1`, after the distinct accepted review, stages and commits the exact Git-derived accepted patch:

```bash
git add pyproject.toml tests/test_package_metadata.py tests/test_engine_v1_compatibility.py tests/test_engine_v2_contracts.py src/helios_takeoff_core/engine/v2
git commit -m "feat: freeze Division 23 v2 contracts"
```

Invoke `python -m tools.helios_build integrate build_control/tasks/deliverable-b-div23-v2-contracts.json --commit-sha` with the resulting literal 40-hex commit hash as its final argument. Build the wheel from that canonical commit and record the clean-wheel v2 import/schema-resource check as the installed-code acceptance receipt. The TaskManifest, graph, handoff, review, integration, and installed-code receipts must resolve to the same task/base/patch/commit chain before `PRODUCTION_SELF_USE.DELIVERABLE_B_DIV23_V2` becomes true or Task 2 begins.

---

### Task 2: Implement Exact Units, Formula AST, and Pack Compilation

**Files:**

- Create: `src/helios_takeoff_core/engine/v2/formulas.py`
- Create: `src/helios_takeoff_core/engine/v2/compiler.py`
- Create: `tests/test_engine_v2_formulas.py`
- Create: `tests/test_engine_v2_compiler.py`
- Create: `tests/fixtures/engine_v2/minimal_pack.json`

**Interfaces:**

- Consumes: Task 1 enums and `CompiledDomainPack`.
- Produces:

```python
def compile_domain_pack(document: object) -> CompiledDomainPack: ...

def evaluate_formula(
    expression: Mapping[str, object],
    *,
    observations: Mapping[str, TypedValue],
    lookups: Mapping[str, Mapping[str, object]],
    units: Mapping[str, Mapping[str, object]],
) -> tuple[TypedValue, Mapping[str, object], tuple[str, ...]]: ...
```

The tuple contains the exact value, canonical trace root, and ordered names of observations actually used.

- [ ] **Step 1: Write failing compiler tests for canonical identity and citations**

```python
def test_v2_compiler_normalizes_order_and_resolves_every_source_ref() -> None:
    first = compile_domain_pack(load_fixture("minimal_pack.json"))
    reordered = reverse_definition_arrays(load_fixture("minimal_pack.json"))
    self.assertEqual(first.canonical_bytes, reordered.canonical_bytes)
    self.assertEqual(first.sha256, reordered.sha256)
    self.assertEqual(tuple(first.catalog_by_code), ("CMP-SEGMENT",))

def test_v2_compiler_rejects_unresolved_or_pack_level_only_provenance() -> None:
    document = load_fixture("minimal_pack.json")
    document["rules"][0]["source_refs"] = []
    with self.assertRaisesRegex(ValidationError, "source_refs"):
        compile_domain_pack(document)

def test_v2_compiler_rejects_executable_ambient_and_unknown_ast_fields() -> None:
    for forbidden in ("command", "callback", "path", "provider", "environment", "url"):
        document = load_fixture("minimal_pack.json")
        document["rules"][0]["formula"][forbidden] = "forbidden"
        with self.subTest(forbidden=forbidden):
            with self.assertRaisesRegex(ValidationError, "fields"):
                compile_domain_pack(document)

def test_source_admission_modes_are_explicit_and_fail_closed() -> None:
    direct = load_fixture("minimal_pack.json")
    source = direct["sources"][0]
    source.update({
        "admission_channel": "DIRECT_PRIMARY",
        "source_packet_id": None,
        "direct_admission_id": "DIRECT-AIRSIDE-PRIMARY-001",
        "original_verification_id": "VERIFY-AIRSIDE-PRIMARY-001",
        "accepted_for_build_event_sha256": "e" * 64,
        "accepted_for_build_task_id": "BUILD-AIRSIDE-V2-001",
        "authority_class": "PRIMARY_PUBLIC",
    })
    self.assertEqual(
        compile_domain_pack(direct).canonical_document["sources"][0]["admission_channel"],
        "DIRECT_PRIMARY",
    )
    invalid = deepcopy(direct)
    invalid["sources"][0]["source_packet_id"] = "PACKET-MUST-BE-EXCLUSIVE"
    with self.assertRaisesRegex(ValidationError, "ingress"):
        compile_domain_pack(invalid)
    neither = deepcopy(direct)
    neither["sources"][0]["direct_admission_id"] = None
    with self.assertRaisesRegex(ValidationError, "ingress"):
        compile_domain_pack(neither)

    packet = load_fixture("minimal_pack.json")
    packet_source = packet["sources"][0]
    self.assertEqual(packet_source["admission_channel"], "ATHENA_SOURCE_PACKET")
    self.assertIsNotNone(packet_source["source_packet_id"])
    self.assertIsNone(packet_source["direct_admission_id"])
    packet_source["original_verification_id"] = None
    with self.assertRaisesRegex(ValidationError, "original verification"):
        compile_domain_pack(packet)
    missing_acceptance = deepcopy(direct)
    missing_acceptance["sources"][0]["accepted_for_build_event_sha256"] = None
    with self.assertRaisesRegex(ValidationError, "accepted-for-build"):
        compile_domain_pack(missing_acceptance)
```

`minimal_pack.json` uses an exact non-authoritative test Source with `admission_channel: "ATHENA_SOURCE_PACKET"`, `source_packet_id: "PACKET-MINIMAL-001"`, `direct_admission_id: null`, `original_verification_id: "VERIFY-MINIMAL-001"`, `accepted_for_build_event_sha256` equal to 64 lowercase `d` characters, and `accepted_for_build_task_id: "BUILD-MINIMAL-V2-001"`.

- [ ] **Step 2: Write failing formula tests for Decimal, dimensions, and bounded ASTs**

```python
def test_formula_uses_decimal_and_exact_rational_unit_conversion() -> None:
    value, trace, used = evaluate_formula(
        {
            "op": "MULTIPLY",
            "left": {"op": "OBSERVATION", "name": "LENGTH"},
            "right": {"op": "LITERAL", "value_type": "DECIMAL", "value": "2"},
        },
        observations={"LENGTH": TypedValue(ScalarType.DECIMAL, "12.5", "LF")},
        lookups={},
        units=unit_fixture(),
    )
    self.assertEqual(value, TypedValue(ScalarType.DECIMAL, "25", "LF"))
    self.assertEqual(used, ("LENGTH",))
    self.assertEqual(trace["op"], "MULTIPLY")

def test_formula_rejects_binary_float_and_blocks_nonterminating_conversion() -> None:
    with self.assertRaisesRegex(ValidationError, "decimal string"):
        compile_literal({"op": "LITERAL", "value_type": "DECIMAL", "value": 0.1})
    with self.assertRaisesRegex(FormulaBlocked, "INEXACT_UNIT_CONVERSION"):
        convert_decimal(Decimal("1"), numerator=1, denominator=3)
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run: `python -m unittest tests.test_engine_v2_compiler tests.test_engine_v2_formulas -v`

Expected: FAIL because the compiler and formula modules do not exist.

- [ ] **Step 4: Implement canonical Decimal and exact conversion helpers**

`formulas.py` defines and uses these exact helpers:

```python
DECIMAL_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
MAX_FORMULA_DEPTH = 32
MAX_FORMULA_NODES = 256
MAX_PACK_FORMULA_NODES = 4096

class FormulaBlocked(ValidationError):
    def __init__(self, reason: str, *, missing_observations: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.reason = reason
        self.missing_observations = missing_observations

def canonical_decimal(value: object, field_name: str) -> str:
    if not isinstance(value, str) or DECIMAL_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must be a plain decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValidationError(f"{field_name} must be finite")
    if parsed.is_zero():
        return "0"
    rendered = format(parsed, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered

def convert_decimal(value: Decimal, *, numerator: int, denominator: int) -> Decimal:
    if numerator <= 0 or denominator <= 0:
        raise ValidationError("unit conversion factor must be positive")
    exact = Fraction(value) * Fraction(numerator, denominator)
    reduced_denominator = exact.denominator
    while reduced_denominator % 2 == 0:
        reduced_denominator //= 2
    while reduced_denominator % 5 == 0:
        reduced_denominator //= 5
    if reduced_denominator != 1:
        raise FormulaBlocked("INEXACT_UNIT_CONVERSION")
    return Decimal(exact.numerator) / Decimal(exact.denominator)
```

Exact arithmetic dynamically selects sufficient local Decimal precision and traps `Inexact` and `Rounded`. Division reduces an exact fraction first and blocks if the result cannot terminate. The closed dimension table permits dimensionless multiplication, `LENGTH × LENGTH = AREA`, `AREA × MASS_PER_AREA = MASS`, and like-dimension division to `DIMENSIONLESS`; every other dimensional combination is rejected at compile time.

- [ ] **Step 5: Implement strict pack compilation**

`compile_domain_pack()` must:

1. Require the exact top-level and nested fields frozen above.
2. Reject every Python float before normalization.
3. Validate codes, scalar types, units, observation bounds, lookup key/value types, and duplicate identities.
4. Resolve each `SourceRef` and require its `source_identity` to match its `Source`. `admission_channel` is closed to `ATHENA_SOURCE_PACKET` and `DIRECT_PRIMARY`. ATHENA_SOURCE_PACKET requires non-null `source_packet_id` and null `direct_admission_id`; direct admission requires the inverse plus `PRIMARY_PUBLIC`. Both require non-blank `original_verification_id` and `accepted_for_build_task_id`, plus a lowercase 64-character `accepted_for_build_event_sha256`. Reject both-ingress and neither-ingress records.
5. Require citations on catalog items, lookups, assembly edges, and rules.
6. Resolve catalog, observation, lookup, rule, and assembly references.
7. Type-check and dimension-check every formula and condition.
8. Reject structural assembly cycles before producing a compiled pack.
9. Sort definition arrays by stable identity and source refs by all six fields, while retaining `MIN`/`MAX`/lookup key argument order.
10. Deep-freeze nested objects with `MappingProxyType` and tuples.
11. Serialize with `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` and hash the exact UTF-8 bytes.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `python -m unittest tests.test_engine_v2_compiler tests.test_engine_v2_formulas -v`

Expected: PASS, including stable bytes after source/definition reordering and explicit rejection of float, unknown fields, unresolved citations, invalid lookup keys, cycles, excessive AST depth, and incompatible dimensions.

- [ ] **Step 7: Commit the compiler kernel**

```bash
git add src/helios_takeoff_core/engine/v2/compiler.py src/helios_takeoff_core/engine/v2/formulas.py tests/test_engine_v2_compiler.py tests/test_engine_v2_formulas.py tests/fixtures/engine_v2/minimal_pack.json
git commit -m "feat: compile bounded source-backed formulas"
```

---

### Task 3: Add the Dedicated Immutable EngineStore

**Files:**

- Create: `src/helios_takeoff_core/engine/v2/store.py`
- Create: `src/helios_takeoff_core/engine/v2/migrations/001_engine_registry.sql`
- Create: `tests/test_engine_v2_store.py`

**Interfaces:**

- Consumes: `compile_domain_pack()` and `CompiledDomainPack`.
- Produces:

```python
class EngineStore:
    def __init__(self, path: str | Path) -> None: ...
    def initialize(self) -> None: ...
    def import_pack(self, pack: CompiledDomainPack) -> str: ...
    def load_pack(self, *, pack_sha256: str) -> CompiledDomainPack: ...
    def get_source(self, *, pack_sha256: str, source_id: str) -> dict[str, object]: ...
    def get_catalog_item(self, *, pack_sha256: str, item_code: str) -> dict[str, object]: ...
    def get_rule(self, *, pack_sha256: str, rule_code: str) -> dict[str, object]: ...
    def list_relations(
        self,
        *,
        pack_sha256: str,
        from_code: str | None = None,
        to_code: str | None = None,
    ) -> tuple[dict[str, object], ...]: ...
    def rebuild_indexes(self, *, pack_sha256: str | None = None) -> None: ...
```

No method selects by latest version. `import_pack()` returns the pack digest, not a generated ID.

- [ ] **Step 1: Write failing isolation, immutability, replay, and rebuild tests**

```python
def test_store_rejects_p0_p1a_and_v1_database_paths() -> None:
    p0_path = self.root / "p0.sqlite3"
    Database(p0_path).initialize()
    with self.assertRaisesRegex(ValidationError, "dedicated engine registry"):
        EngineStore(p0_path).initialize()

def test_import_is_idempotent_and_same_version_change_conflicts() -> None:
    store = EngineStore(self.root / "engine-v2.sqlite3")
    store.initialize()
    compiled = compile_domain_pack(load_fixture("minimal_pack.json"))
    self.assertEqual(store.import_pack(compiled), compiled.sha256)
    self.assertEqual(store.import_pack(compiled), compiled.sha256)
    changed = changed_title_same_identity(load_fixture("minimal_pack.json"))
    with self.assertRaises(ConflictError):
        store.import_pack(compile_domain_pack(changed))

def test_indexes_are_disposable_and_rebuild_from_blob() -> None:
    store = initialized_store(self.root / "engine-v2.sqlite3")
    compiled = compile_domain_pack(load_fixture("minimal_pack.json"))
    store.import_pack(compiled)
    delete_all_projection_rows(store.path)
    store.rebuild_indexes(pack_sha256=compiled.sha256)
    self.assertEqual(
        store.get_catalog_item(pack_sha256=compiled.sha256, item_code="CMP-SEGMENT")["code"],
        "CMP-SEGMENT",
    )

def test_raw_sqlite_connection_cannot_insert_or_replace_authoritative_blob() -> None:
    store = initialized_store(self.root / "engine-v2.sqlite3")
    compiled = compile_domain_pack(load_fixture("minimal_pack.json"))
    with sqlite3.connect(store.path) as raw:
        with self.assertRaisesRegex(sqlite3.OperationalError, "no such function"):
            insert_raw_pack(raw, compiled)
    store.import_pack(compiled)
    with sqlite3.connect(store.path) as raw:
        with self.assertRaisesRegex(sqlite3.IntegrityError, "cannot be replaced"):
            replace_raw_pack(raw, compiled)
```

- [ ] **Step 2: Run the focused store test and verify RED**

Run: `python -m unittest tests.test_engine_v2_store -v`

Expected: FAIL because `EngineStore` and its migration do not exist.

- [ ] **Step 3: Add the isolated schema**

`001_engine_registry.sql` creates exactly these tables:

```sql
CREATE TABLE engine_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE engine_packs (
    sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    protocol TEXT NOT NULL CHECK(protocol = 'helios.engine.domain-pack/v2'),
    pack_code TEXT NOT NULL CHECK(pack_code <> '' AND pack_code = trim(pack_code)),
    version TEXT NOT NULL CHECK(version <> '' AND version = trim(version)),
    canonical_json TEXT NOT NULL CHECK(json_valid(canonical_json)),
    created_at TEXT NOT NULL,
    UNIQUE(pack_code, version)
);

CREATE TABLE engine_source_index (
    pack_sha256 TEXT NOT NULL REFERENCES engine_packs(sha256) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    PRIMARY KEY(pack_sha256, source_id)
) WITHOUT ROWID;

CREATE TABLE engine_catalog_index (
    pack_sha256 TEXT NOT NULL REFERENCES engine_packs(sha256) ON DELETE CASCADE,
    item_code TEXT NOT NULL,
    lane TEXT NOT NULL CHECK(lane IN ('AIRSIDE', 'PIPING', 'EQUIPMENT')),
    item_type TEXT NOT NULL,
    PRIMARY KEY(pack_sha256, item_code)
) WITHOUT ROWID;

CREATE TABLE engine_rule_index (
    pack_sha256 TEXT NOT NULL REFERENCES engine_packs(sha256) ON DELETE CASCADE,
    rule_code TEXT NOT NULL,
    subject_code TEXT NOT NULL,
    result_kind TEXT NOT NULL CHECK(result_kind IN ('QUANTITY', 'RECONCILIATION', 'ASSEMBLY')),
    PRIMARY KEY(pack_sha256, rule_code)
) WITHOUT ROWID;

CREATE TABLE engine_lookup_index (
    pack_sha256 TEXT NOT NULL REFERENCES engine_packs(sha256) ON DELETE CASCADE,
    lookup_code TEXT NOT NULL,
    PRIMARY KEY(pack_sha256, lookup_code)
) WITHOUT ROWID;

CREATE TABLE engine_relation_index (
    pack_sha256 TEXT NOT NULL REFERENCES engine_packs(sha256) ON DELETE CASCADE,
    edge_code TEXT NOT NULL,
    from_code TEXT NOT NULL,
    relation TEXT NOT NULL,
    to_code TEXT NOT NULL,
    PRIMARY KEY(pack_sha256, edge_code)
) WITHOUT ROWID;
```

Add a `BEFORE INSERT` trigger that requires `helios_engine_v2_pack_is_canonical(NEW.canonical_json, NEW.sha256) = 1`, verifies the canonical header matches `protocol`, `pack_code`, and `version`, and rejects an existing digest or `(pack_code, version)` with `engine pack identity cannot be replaced`. Add `BEFORE UPDATE` and `BEFORE DELETE` abort triggers to `engine_packs`. Projection tables intentionally remain replaceable because they are rebuildable. The store recompiles and byte-compares `canonical_json` before every load and rebuild.

- [ ] **Step 4: Implement migration and database rejection behavior**

Before creating `engine_schema_migrations`, inspect `sqlite_master`. Reject `schema_migrations`, every current P0/P1A/v1 table name, and every table outside this migration's seven-table allowlist. Record each v2 migration filename and SHA-256; a later mismatch between an applied migration and its packaged bytes is a `ValidationError`. Connections enable foreign keys, use `sqlite3.Row`, register deterministic `helios_engine_v2_pack_is_canonical` by parsing/recompiling/byte-comparing the blob, and use `BEGIN IMMEDIATE` for imports/rebuilds. A raw connection lacks the validator and therefore fails closed at the insert trigger.

`import_pack()` recompiles the blob before opening its write transaction. It inserts one parent row and then calls the same private `_replace_indexes(connection, compiled)` routine used by `rebuild_indexes()`. Identical content refreshes only disposable indexes and returns the existing digest. Different content with the same `(pack_code, version)` raises `ConflictError`.

- [ ] **Step 5: Run the focused store test and verify GREEN**

Run: `python -m unittest tests.test_engine_v2_store -v`

Expected: PASS; P0/P1A/v1 paths fail closed, migration checksums are enforced, replay is idempotent, changed same-version content conflicts, raw insert/replace fails closed, pack rows reject mutation, and indexes rebuild from canonical blobs.

- [ ] **Step 6: Commit the isolated registry**

```bash
git add src/helios_takeoff_core/engine/v2/store.py src/helios_takeoff_core/engine/v2/migrations/001_engine_registry.sql tests/test_engine_v2_store.py
git commit -m "feat: add isolated immutable engine store"
```

---

### Task 4: Compile Observation Bundles and Emit EngineResultSets

**Files:**

- Create: `src/helios_takeoff_core/engine/v2/evaluator.py`
- Create: `tests/test_engine_v2_evaluator.py`
- Create: `tests/fixtures/engine_v2/benchmark/minimal_bundle.json`
- Create: `tests/fixtures/engine_v2/project_bound/minimal_bundle.json`
- Modify: `src/helios_takeoff_core/engine/v2/compiler.py`
- Modify: `src/helios_takeoff_core/engine/v2/__init__.py`

**Interfaces:**

- Consumes: Task 1 bundle/result dataclasses and Task 2 formula evaluator.
- Produces:

```python
def compile_observation_bundle(document: object) -> ObservationBundle: ...

def evaluate_bundle(
    pack: CompiledDomainPack,
    bundle: ObservationBundle,
) -> EngineResultSet: ...
```

- [ ] **Step 1: Write failing mode-boundary and deterministic-result tests**

```python
def test_benchmark_forbids_project_identity_and_evidence() -> None:
    document = load_bundle("benchmark/minimal_bundle.json")
    document["project_id"] = "project-not-allowed"
    with self.assertRaisesRegex(ValidationError, "BENCHMARK"):
        compile_observation_bundle(document)

def test_project_bound_requires_distinct_instance_identity_and_evidence_subsets() -> None:
    document = load_bundle("project_bound/minimal_bundle.json")
    document["subject_key"] = document["subject_code"]
    with self.assertRaisesRegex(ValidationError, "project-instance"):
        compile_observation_bundle(document)

def test_result_digest_and_order_ignore_input_map_order() -> None:
    pack = compile_domain_pack(load_fixture("minimal_pack.json"))
    first = evaluate_bundle(pack, compile_observation_bundle(load_bundle("benchmark/minimal_bundle.json")))
    second = evaluate_bundle(pack, compile_observation_bundle(reordered_bundle()))
    self.assertEqual(first, second)
    self.assertEqual(first.lines[0].status, EvaluationStatus.READY)
    self.assertEqual(first.lines[0].confidence, "0.91")
```

- [ ] **Step 2: Run the focused evaluator test and verify RED**

Run: `python -m unittest tests.test_engine_v2_evaluator -v`

Expected: FAIL because bundle compilation and v2 evaluation are not implemented.

- [ ] **Step 3: Implement exact bundle variants and hashing**

Both bundle variants contain the exact common fields below; mode-forbidden fields are absent rather than null:

```json
{
  "protocol": "helios.engine.observation-bundle/v1",
  "mode": "BENCHMARK",
  "pack_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "subject_code": "CMP-SEGMENT",
  "context": {
    "system": "SUPPLY-AIR",
    "floor": null,
    "area": null,
    "phase": "BASE",
    "zone": null,
    "work_state": "NEW",
    "commercial_dimension": "BASE",
    "revision_ids": [],
    "addendum_ids": []
  },
  "observations": [],
  "benchmark_artifact_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
}
```

PROJECT_BOUND replaces `benchmark_artifact_sha256` with `project_id`, `extraction_run_id`, `subject_kind`, `subject_key`, `evidence_item_ids`, and `primary_evidence_item_id`. Evidence IDs and revision/addendum IDs are sorted, non-empty where required, and duplicate-free. Each project observation's evidence subset must be contained in the bundle set. The primary ID must be in the set. BENCHMARK observations have empty evidence subsets. The input document does not contain its own digest; the compiler hashes the entire normalized strict document and stores that computed digest in `ObservationBundle.sha256`.

Observation confidence uses a canonical decimal string in `[0,1]`. `origin` is `HUMAN`, `STRUCTURED_IMPORT`, or `MODEL`. A MODEL observation requires non-null model ID, model revision, input hash, and region. HUMAN and STRUCTURED_IMPORT observations use null model fields but still require a content-addressed input hash. The test independently recomputes the normalized document digest and verifies the returned bundle digest.

- [ ] **Step 4: Implement ordered ready and blocked result lines**

For each subject rule, evaluate its condition and formula. Missing observations, missing lookup keys, invalid exact conversions, and divide-by-zero become a BLOCKED line with a canonical trace and reason; schema/type/cross-reference errors remain `ValidationError`.

For each line:

- `observation_refs` is the ordered unique set actually visited; unselected `IF` branches do not contribute.
- `confidence` is the minimum confidence among those observations, or `null` when none were used.
- PROJECT_BOUND `evidence_item_ids` is the sorted union of the used observations' evidence subsets; BENCHMARK uses an empty tuple.
- `primary_evidence_item_id` is present only when it is in the line's exact evidence set.
- `FormulaTrace.sha256` hashes the canonical trace excluding its digest.
- `EngineResultLine.sha256` hashes the canonical line excluding its digest.
- Result lines sort by `(kind.value, rule_code, line_code)`.
- `EngineResultSet.sha256` hashes protocol, mode, bundle digest, pack digest, subject, project/run identity, and the ordered line digests.

- [ ] **Step 5: Run the focused evaluator test and verify GREEN**

Run: `python -m unittest tests.test_engine_v2_evaluator -v`

Expected: PASS for BENCHMARK/PROJECT_BOUND exclusivity, pack-digest mismatch, typed validation, blocked missing input, short-circuit lineage, confidence minimum, evidence subset derivation, canonical traces, line ordering, and result-set hashing.

- [ ] **Step 6: Commit the observation/result kernel**

```bash
git add src/helios_takeoff_core/engine/v2/compiler.py src/helios_takeoff_core/engine/v2/evaluator.py src/helios_takeoff_core/engine/v2/__init__.py tests/test_engine_v2_evaluator.py tests/fixtures/engine_v2/benchmark/minimal_bundle.json tests/fixtures/engine_v2/project_bound/minimal_bundle.json
git commit -m "feat: evaluate typed observation bundles"
```

---

### Task 5: Preserve Assembly Multiplicity as a Quantity-Bearing Tree

**Files:**

- Modify: `src/helios_takeoff_core/engine/v2/evaluator.py`
- Create: `tests/test_engine_v2_assemblies.py`
- Create: `tests/fixtures/engine_v2/assembly_pack.json`

**Interfaces:**

- Consumes: compiled assembly edges and observation bundles.
- Produces:

```python
def resolve_assembly(
    pack: CompiledDomainPack,
    bundle: ObservationBundle,
    *,
    item_code: str,
    quantity: Decimal = Decimal("1"),
) -> AssemblyNode: ...
```

- [ ] **Step 1: Write the failing diamond-multiplicity and blocked-edge tests**

```python
def test_diamond_reuse_keeps_two_paths_and_multiplies_quantities() -> None:
    root = resolve_assembly(pack(), bundle(), item_code="ASM-ROOT", quantity=Decimal("2"))
    shared = [node for node in walk(root) if node.item_code == "CMP-SHARED"]
    self.assertEqual([node.path for node in shared], [
        ("EDGE-LEFT", "EDGE-LEFT-SHARED"),
        ("EDGE-RIGHT", "EDGE-RIGHT-SHARED"),
    ])
    self.assertEqual([node.quantity.value for node in shared], ["6", "10"])

def test_missing_multiplicity_input_returns_blocked_assembly_line() -> None:
    result = evaluate_bundle(pack(), bundle_without("ACCESSORY_COUNT"))
    line = next(line for line in result.lines if line.kind is ResultKind.ASSEMBLY)
    self.assertEqual(line.status, EvaluationStatus.BLOCKED)
    self.assertEqual(line.blocked_reasons, ("MISSING_OBSERVATION:ACCESSORY_COUNT",))
```

- [ ] **Step 2: Run the focused assembly test and verify RED**

Run: `python -m unittest tests.test_engine_v2_assemblies -v`

Expected: FAIL because v2 assembly resolution is not implemented.

- [ ] **Step 3: Implement path-specific resolution**

Resolve children in edge-code order. Evaluate each edge condition first; false omits the edge, and a missing condition input blocks the containing assembly rule. Evaluate multiplicity, require it to be non-negative, multiply it by the parent quantity, and omit zero. An `EA` multiplicity must be integral. Never deduplicate by item code: a diamond creates one `AssemblyNode` per edge path. Each node preserves the edge's source refs and cumulative quantity. Although the compiler already rejects cycles, retain a path-local runtime guard that raises `ValidationError("structural assembly cycle detected")` if a forged compiled object reaches the evaluator.

- [ ] **Step 4: Run the focused assembly test and verify GREEN**

Run: `python -m unittest tests.test_engine_v2_assemblies -v`

Expected: PASS for nested multiplication, diamond reuse, conditional omission, missing-input block, zero omission, non-integral EA rejection, deterministic child order, and cycle defense.

- [ ] **Step 5: Commit multiplicity behavior**

```bash
git add src/helios_takeoff_core/engine/v2/evaluator.py tests/test_engine_v2_assemblies.py tests/fixtures/engine_v2/assembly_pack.json
git commit -m "feat: resolve quantity-bearing assemblies"
```

---

### Task 6: Add the Source-Backed Airside Structured Kernel

**Files:**

- Create: `src/helios_takeoff_core/engine/v2/packs/airside-v2.json`
- Create: `tests/test_engine_v2_airside.py`
- Create: `tests/fixtures/engine_v2/benchmark/airside_segment.json`
- Create: `tests/fixtures/engine_v2/benchmark/airside_fitting.json`
- Create: `tests/fixtures/engine_v2/benchmark/airside_accessory.json`

**Interfaces:**

- Consumes: Tasks 1-5 plus the accepted source-record IDs declared by the lane's immutable build task. For `ATHENA_SOURCE_PACKET`, resolve `Path("build_control/source_packets/sha256") / f"{packet_sha256}.json"` or the equivalent frozen research-registry lookup. For `DIRECT_PRIMARY`, resolve `direct_admission_id` through the frozen source registry; no packet file is required. Both channels must resolve `original_verification_id`, `accepted_for_build_event_sha256`, and `accepted_for_build_task_id`.
- Produces: one installed airside pack loaded by explicit digest through `EngineStore`; no Python airside calculation module is added.

- [ ] **Step 1: Confirm the source gate without blocking core work**

Run: `python -m tools.helios_build graph status`

Expected before lane work: the airside task is READY only when each required source record resolves its original-verification artifact and exact accepted-for-build event for the named lane task. An ATHENA_SOURCE_PACKET record must resolve its sole packet ingress through the canonical `sha256/` path or registry. A DIRECT_PRIMARY record must resolve its sole direct-admission ingress and have `PRIMARY_PUBLIC` authority. If the lane is BLOCKED, record the source dependency and continue Tasks 7, 8, or 10 when their dependencies permit; do not create an uncited substitute pack.

- [ ] **Step 2: Write failing airside behavior tests**

```python
def test_rectangular_segment_emits_lf_sf_and_lb_with_lineage() -> None:
    result = evaluate_installed_pack("airside-v2.json", "airside_segment.json")
    ready = {line.output_claim_type: line for line in result.lines if line.status is READY}
    self.assertEqual(ready["DUCT_LENGTH"].value, TypedValue(DECIMAL, "10", "LF"))
    self.assertEqual(ready["DUCT_SURFACE_AREA"].value, TypedValue(DECIMAL, "60", "SF"))
    pounds_per_sf = lookup_decimal("airside-v2.json", "DUCT-GAUGE-LB-PER-SF", ["24"])
    self.assertEqual(
        Decimal(ready["DUCT_WEIGHT"].value.value),
        Decimal("60") * pounds_per_sf,
    )
    for line in ready.values():
        self.assertTrue(line.source_refs)
        self.assertEqual(line.evidence_item_ids, ())

def test_fitting_and_accessory_emit_counts_and_missing_width_blocks_segment() -> None:
    self.assertEqual(ready_value("airside_fitting.json", "FITTING_COUNT"), "2")
    self.assertEqual(ready_value("airside_accessory.json", "ACCESSORY_COUNT"), "1")
    blocked = evaluate_without("airside_segment.json", "WIDTH")
    self.assertIn("MISSING_OBSERVATION:WIDTH", blocked.blocked_reasons)
```

- [ ] **Step 3: Run the focused airside test and verify RED**

Run: `python -m unittest tests.test_engine_v2_airside -v`

Expected: FAIL because the installed airside pack does not exist.

- [ ] **Step 4: Author the cited airside pack**

The pack defines these subjects and formulas:

- `AIRSIDE-RECTANGULAR-SEGMENT`: `DUCT_LENGTH = LENGTH`; `DUCT_SURFACE_AREA = 2 × (WIDTH + HEIGHT) × LENGTH`; `DUCT_WEIGHT = DUCT_SURFACE_AREA × LOOKUP(DUCT-GAUGE-LB-PER-SF, MATERIAL, PRESSURE_CLASS, GAUGE)`; `DUCT_INSULATION_AREA = DUCT_SURFACE_AREA` when `INSULATION_TYPE == "EXTERNAL_WRAP"`.
- `AIRSIDE-FITTING`: `FITTING_COUNT = COUNT` in `EA`.
- `AIRSIDE-ACCESSORY`: `ACCESSORY_COUNT = COUNT` in `EA`.
- Segment observation definitions include pressure class, material, gauge, liner state, and external-wrap state so each emitted line records the exact observation subset it used.

Copy source URL/document identity, digest when present, publisher, title, retrieval date, locator, jurisdiction, applicability, and limitations from accepted source records. Preserve `admission_channel`, nullable `source_packet_id`, nullable `direct_admission_id`, `original_verification_id`, `accepted_for_build_event_sha256`, and `accepted_for_build_task_id` exactly. One ingress ID is populated and the other is null. Cite the catalog definitions, gauge lookup, each rule, and each assembly edge separately. The benchmark is structured JSON with width `2 FT`, height `1 FT`, and length `10 LF`; it contains no drawing or P0 evidence.

- [ ] **Step 5: Run the focused airside test and verify GREEN**

Run: `python -m unittest tests.test_engine_v2_airside -v`

Expected: PASS for LF/SF/LB/count outputs, pressure/material/insulation lineage, source resolution, order-independent hashes, and a missing-width blocked line.

- [ ] **Step 6: Commit the airside capability**

```bash
git add src/helios_takeoff_core/engine/v2/packs/airside-v2.json tests/test_engine_v2_airside.py tests/fixtures/engine_v2/benchmark/airside_segment.json tests/fixtures/engine_v2/benchmark/airside_fitting.json tests/fixtures/engine_v2/benchmark/airside_accessory.json
git commit -m "feat: add source-backed airside kernel"
```

---

### Task 7: Add the Source-Backed Piping Structured Kernel

**Files:**

- Create: `src/helios_takeoff_core/engine/v2/packs/piping-v2.json`
- Create: `tests/test_engine_v2_piping.py`
- Create: `tests/fixtures/engine_v2/benchmark/piping_segment.json`
- Create: `tests/fixtures/engine_v2/benchmark/piping_fitting.json`
- Create: `tests/fixtures/engine_v2/benchmark/piping_valve.json`

**Interfaces:**

- Consumes: Tasks 1-5 and accepted piping source records under the same ATHENA_SOURCE_PACKET-or-DIRECT_PRIMARY contract as Task 6.
- Produces: one installed piping pack evaluated through the shared compiler/evaluator only.

- [ ] **Step 1: Confirm the piping source gate**

Run: `python -m tools.helios_build graph status`

Expected: the lane begins only when its material/joining/insulation and valve-connection source records are original-verified and accepted for this task. A blocked piping source does not block Tasks 6, 8, or 10.

- [ ] **Step 2: Write failing piping behavior tests**

```python
def test_pipe_segment_and_insulation_preserve_service_material_and_joining() -> None:
    result = evaluate_installed_pack("piping-v2.json", "piping_segment.json")
    ready = {line.output_claim_type: line for line in result.lines if line.status is READY}
    self.assertEqual(ready["PIPE_LENGTH"].value, TypedValue(DECIMAL, "40", "LF"))
    self.assertEqual(ready["PIPE_INSULATION_LENGTH"].value, TypedValue(DECIMAL, "40", "LF"))
    self.assertEqual(
        set(ready["PIPE_LENGTH"].observation_refs),
        {"LENGTH", "MATERIAL", "JOINING_METHOD", "SERVICE"},
    )

def test_fitting_valve_and_connection_assembly_keep_multiplicity() -> None:
    self.assertEqual(ready_value("piping_fitting.json", "PIPE_FITTING_COUNT"), "3")
    result = evaluate_installed_pack("piping-v2.json", "piping_valve.json")
    assembly = next(line.assembly for line in result.lines if line.kind is ASSEMBLY)
    connection_nodes = [node for node in walk(assembly) if node.item_code == "VALVE-CONNECTION"]
    self.assertEqual([node.quantity.value for node in connection_nodes], ["2"])
```

- [ ] **Step 3: Run the focused piping test and verify RED**

Run: `python -m unittest tests.test_engine_v2_piping -v`

Expected: FAIL because the installed piping pack does not exist.

- [ ] **Step 4: Author the cited piping pack**

Define `PIPING-SEGMENT`, `PIPING-FITTING`, and `PIPING-VALVE`. Segment rules emit `PIPE_LENGTH` and conditionally `PIPE_INSULATION_LENGTH`; fitting and valve rules emit `EA`. A Boolean `PIPE-APPLICABILITY` lookup keyed by material, joining method, and service is the segment-rule condition, so those observations are actually evaluated and therefore appear in lineage. The valve assembly uses a cited edge whose multiplicity expression emits two connection components for the exact selected valve fixture. Observations include material, nominal size, joining method, service, insulation type, and riser state. Every rule, lookup, and edge cites the accepted source locator that supports its applicability; unsupported combinations remain absent rather than generalized.

- [ ] **Step 5: Run the focused piping test and verify GREEN**

Run: `python -m unittest tests.test_engine_v2_piping -v`

Expected: PASS for LF/count results, insulation condition, material/joining/service lineage, valve connection multiplicity, missing-service block, and per-definition citations.

- [ ] **Step 6: Commit the piping capability**

```bash
git add src/helios_takeoff_core/engine/v2/packs/piping-v2.json tests/test_engine_v2_piping.py tests/fixtures/engine_v2/benchmark/piping_segment.json tests/fixtures/engine_v2/benchmark/piping_fitting.json tests/fixtures/engine_v2/benchmark/piping_valve.json
git commit -m "feat: add source-backed piping kernel"
```

---

### Task 8: Add Equipment Reconciliation and Accessory Assemblies

**Files:**

- Create: `src/helios_takeoff_core/engine/v2/packs/equipment-v2.json`
- Create: `tests/test_engine_v2_equipment.py`
- Create: `tests/fixtures/engine_v2/benchmark/equipment_matched.json`
- Create: `tests/fixtures/engine_v2/benchmark/equipment_missing.json`
- Create: `tests/fixtures/engine_v2/benchmark/equipment_duplicate.json`
- Create: `tests/fixtures/engine_v2/benchmark/equipment_conflict.json`

**Interfaces:**

- Consumes: Tasks 1-5 and accepted equipment/accessory source records under the same ATHENA_SOURCE_PACKET-or-DIRECT_PRIMARY contract as Task 6.
- Produces: installed equipment/scope pack with RECONCILIATION and ASSEMBLY lines.

- [ ] **Step 1: Confirm the equipment source gate**

Run: `python -m tools.helios_build graph status`

Expected: accessory candidates require an accepted manufacturer or authorized source record. Reconciliation may cite the owner-approved methodology source record, but no uncited manufacturer accessory is admitted. Either admission mode is valid only with original verification and named task acceptance.

- [ ] **Step 2: Write failing reconciliation and accessory tests**

```python
def test_reconciliation_emits_all_four_closed_states() -> None:
    cases = {
        "equipment_matched.json": "MATCHED",
        "equipment_missing.json": "MISSING",
        "equipment_duplicate.json": "DUPLICATE",
        "equipment_conflict.json": "CONFLICT",
    }
    for fixture, expected in cases.items():
        with self.subTest(fixture=fixture):
            line = result_line(fixture, "EQUIPMENT_RECONCILIATION")
            self.assertEqual(line.value, TypedValue(ENUM, expected, None))

def test_accessory_candidate_is_quantity_bearing_and_source_cited() -> None:
    line = result_line("equipment_matched.json", "EQUIPMENT_ACCESSORIES")
    self.assertEqual(line.kind, ASSEMBLY)
    self.assertEqual(line.status, READY)
    self.assertTrue(line.assembly.children)
    self.assertTrue(all(node.source_refs for node in line.assembly.children))
```

- [ ] **Step 3: Run the focused equipment test and verify RED**

Run: `python -m unittest tests.test_engine_v2_equipment -v`

Expected: FAIL because the installed equipment pack does not exist.

- [ ] **Step 4: Author the cited equipment pack**

The reconciliation formula is the exact nested conditional below:

```text
IF SCHEDULE_MATCH_COUNT == 0
THEN "MISSING"
ELSE IF SCHEDULE_MATCH_COUNT > 1
THEN "DUPLICATE"
ELSE IF PLAN_MODEL != SCHEDULE_MODEL
THEN "CONFLICT"
ELSE "MATCHED"
```

Define plan tag, schedule-match count, plan model, schedule model, equipment class, and count observations. The result is a RECONCILIATION line with closed ENUM output. Define cited accessory assembly edges for the selected equipment fixture; their quantities are formula expressions, not hard-coded evaluator logic. Missing plan tag or schedule count blocks. Duplicate and conflict states remain candidate outputs and are never silently resolved.

- [ ] **Step 5: Run the focused equipment test and verify GREEN**

Run: `python -m unittest tests.test_engine_v2_equipment -v`

Expected: PASS for matched/missing/duplicate/conflict, deterministic observation lineage, blocked missing input, cited accessory multiplicity, and absence of P0 identity in all benchmark results.

- [ ] **Step 6: Commit the equipment capability**

```bash
git add src/helios_takeoff_core/engine/v2/packs/equipment-v2.json tests/test_engine_v2_equipment.py tests/fixtures/engine_v2/benchmark/equipment_matched.json tests/fixtures/engine_v2/benchmark/equipment_missing.json tests/fixtures/engine_v2/benchmark/equipment_duplicate.json tests/fixtures/engine_v2/benchmark/equipment_conflict.json
git commit -m "feat: add equipment reconciliation kernel"
```

---

### Task 9: Publish the Versioned Division 23 Capability Matrix

**Files:**

- Create: `src/helios_takeoff_core/engine/v2/capabilities.py`
- Create: `src/helios_takeoff_core/engine/v2/schemas/capability-matrix-v1.schema.json`
- Create: `src/helios_takeoff_core/engine/v2/capability_matrices/division23-v1.json`
- Create: `tests/test_engine_v2_capability_matrix.py`
- Modify: `src/helios_takeoff_core/engine/v2/__init__.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_package_metadata.py`

**Interfaces:**

- Consumes: the actual merged state of Tasks 6-8; matrix entries describe installed capability and never activate a rule pack.
- Produces:

```python
CAPABILITY_MATRIX_PROTOCOL = "helios.engine.capability-matrix/v1"

class CapabilityState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    EXPERIMENTAL = "EXPERIMENTAL"
    VERIFIED = "VERIFIED"
    PRODUCTION_APPROVED = "PRODUCTION_APPROVED"

@dataclass(frozen=True)
class CapabilityEntry:
    code: str
    title: str
    state: CapabilityState
    required_for_full_claim: bool
    implementation_refs: tuple[str, ...]
    limitations: tuple[str, ...]

@dataclass(frozen=True)
class CompiledCapabilityMatrix:
    canonical_document: Mapping[str, object]
    canonical_bytes: bytes
    sha256: str
    entries: tuple[CapabilityEntry, ...]

def compile_capability_matrix(document: object) -> CompiledCapabilityMatrix: ...
def load_packaged_capability_matrix() -> CompiledCapabilityMatrix: ...
def require_full_division23_coverage(matrix: CompiledCapabilityMatrix) -> None: ...
```

- [ ] **Step 1: Write the failing schema, truthful-state, and full-claim tests**

```python
def test_packaged_matrix_reports_truthful_initial_states() -> None:
    matrix = load_packaged_capability_matrix()
    states = {entry.code: entry.state for entry in matrix.entries}
    self.assertEqual(states["AIR_DISTRIBUTION"], CapabilityState.EXPERIMENTAL)
    self.assertEqual(states["HYDRONIC_PIPING"], CapabilityState.EXPERIMENTAL)
    self.assertEqual(states["EQUIPMENT_CENTRAL_PLANT"], CapabilityState.EXPERIMENTAL)
    self.assertEqual(states["INSULATION"], CapabilityState.EXPERIMENTAL)
    self.assertEqual(states["CONTROLS_BAS_INTERFACES"], CapabilityState.NOT_STARTED)
    self.assertNotIn(CapabilityState.PRODUCTION_APPROVED, states.values())

def test_full_division23_claim_requires_every_required_entry_to_be_production_approved() -> None:
    matrix = load_packaged_capability_matrix()
    with self.assertRaisesRegex(PreconditionError, "full Division 23 coverage is not approved"):
        require_full_division23_coverage(matrix)
    approved = compile_capability_matrix(all_required_entries_production_approved(matrix))
    self.assertIsNone(require_full_division23_coverage(approved))

def test_matrix_is_strict_versioned_and_order_independent() -> None:
    document = load_matrix_document()
    first = compile_capability_matrix(document)
    second = compile_capability_matrix(reverse_entries(document))
    self.assertEqual(first.canonical_bytes, second.canonical_bytes)
    invalid = deepcopy(document)
    invalid["full_division23"] = True
    with self.assertRaisesRegex(ValidationError, "fields"):
        compile_capability_matrix(invalid)
```

- [ ] **Step 2: Run the focused capability-matrix test and verify RED**

Run: `python -m unittest tests.test_engine_v2_capability_matrix -v`

Expected: FAIL because the capability schema, matrix, and compiler do not exist.

- [ ] **Step 3: Implement the strict matrix compiler and coverage gate**

The matrix document's exact fields are `protocol`, `matrix_code`, `version`, `title`, and `entries`. Each entry's exact fields are `code`, `title`, `state`, `required_for_full_claim`, `implementation_refs`, and `limitations`. Require at least one entry, unique uppercase stable codes, sorted duplicate-free refs/limitations, closed states, and `additionalProperties: false`. Canonicalize entries by code and hash compact sorted UTF-8 JSON. `require_full_division23_coverage()` sorts every required entry whose state is not `PRODUCTION_APPROVED` and raises:

```python
blocked = tuple(
    sorted(
        entry.code
        for entry in matrix.entries
        if entry.required_for_full_claim
        and entry.state is not CapabilityState.PRODUCTION_APPROVED
    )
)
if blocked:
    raise PreconditionError(
        "full Division 23 coverage is not approved: " + ",".join(blocked)
    )
```

- [ ] **Step 4: Add the truthful packaged matrix**

Set these exact initial states; every entry has `required_for_full_claim: true`:

| Entry | Initial state | Implementation reference or limitation |
|---|---|---|
| `AIR_DISTRIBUTION` | `EXPERIMENTAL` | `pack:DIV23-AIRSIDE@2.0.0`; structured BENCHMARK proof only |
| `HYDRONIC_PIPING` | `EXPERIMENTAL` | `pack:DIV23-PIPING@2.0.0`; selected hydronic structured proof only |
| `REFRIGERANT_PIPING` | `NOT_STARTED` | No installed verified behavior |
| `CONDENSATE_PIPING` | `NOT_STARTED` | No installed verified behavior |
| `STEAM_CONDENSATE_PIPING` | `NOT_STARTED` | No installed verified behavior |
| `HVAC_FUEL_SYSTEMS` | `NOT_STARTED` | No installed verified behavior |
| `EQUIPMENT_CENTRAL_PLANT` | `EXPERIMENTAL` | `pack:DIV23-EQUIPMENT@2.0.0`; reconciliation/accessory BENCHMARK proof only |
| `CONTROLS_BAS_INTERFACES` | `NOT_STARTED` | No installed verified behavior |
| `INSULATION` | `EXPERIMENTAL` | Airside and piping structured rules only |
| `SUPPORTS_SEISMIC_VIBRATION` | `NOT_STARTED` | No installed verified behavior |
| `TESTING_TAB_COMMISSIONING` | `NOT_STARTED` | No installed verified behavior |
| `DEMOLITION_RELOCATION_PHASING` | `NOT_STARTED` | No installed verified behavior |
| `ALTERNATES_ALLOWANCES` | `NOT_STARTED` | No installed verified behavior |

Add `"engine/v2/capability_matrices/*.json"` to setuptools package data and assert that both the matrix schema and JSON resource ship in `tests/test_package_metadata.py`.

- [ ] **Step 5: Run the focused test and verify GREEN**

Run: `python -m unittest tests.test_engine_v2_capability_matrix tests.test_package_metadata -v`

Expected: PASS; the packaged matrix is canonical and truthful, all required non-approved entries block a full-coverage claim, and only an independently compiled all-approved matrix passes the claim gate.

- [ ] **Step 6: Commit the capability truth surface**

```bash
git add src/helios_takeoff_core/engine/v2/capabilities.py src/helios_takeoff_core/engine/v2/schemas/capability-matrix-v1.schema.json src/helios_takeoff_core/engine/v2/capability_matrices/division23-v1.json src/helios_takeoff_core/engine/v2/__init__.py pyproject.toml tests/test_engine_v2_capability_matrix.py tests/test_package_metadata.py
git commit -m "feat: publish Division 23 capability matrix"
```

---

### Task 10: Implement the Narrow P0 Extraction-Claim Adapter Boundary

**Files:**

- Create: `src/helios_takeoff_core/engine/v2/p0_adapter.py`
- Create: `tests/test_engine_v2_p0_adapter.py`
- Modify: `src/helios_takeoff_core/engine/v2/__init__.py`

**Interfaces:**

- Consumes: ready PROJECT_BOUND `EngineResultSet` instances from Task 4 and the existing P0 route `/v1/evidence-items/{primary_evidence_item_id}/claims`.
- Produces the boundary only; loopback service, SDK, CLI, authentication, and product transport remain in their separate implementation plan.

```python
class P0Client(Protocol):
    def post_json(
        self,
        path: str,
        *,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> Mapping[str, object]: ...

def submit_engine_claims(
    p0_client: P0Client,
    extraction_run_id: str,
    engine_result_set: EngineResultSet,
) -> tuple[str, ...]: ...
```

- [ ] **Step 1: Write failing adapter authority and idempotency tests**

```python
def test_adapter_posts_only_derived_ready_project_bound_claims() -> None:
    client = RecordingP0Client()
    claim_ids = submit_engine_claims(client, RUN_ID, project_result_set())
    self.assertEqual(claim_ids, ("claim-1",))
    request = client.requests[0]
    self.assertEqual(request.path, f"/v1/evidence-items/{PRIMARY_EVIDENCE_ID}/claims")
    self.assertEqual(
        request.headers,
        {"Idempotency-Key": f"engine-result:{project_result_set().lines[0].sha256}"},
    )
    self.assertEqual(set(request.payload), {
        "extraction_run_id", "evidence_item_ids", "subject_kind", "subject_key",
        "claim_type", "payload", "confidence",
    })

def test_adapter_rejects_benchmark_blocked_and_internal_identity_mismatch() -> None:
    with self.assertRaisesRegex(ValidationError, "PROJECT_BOUND"):
        submit_engine_claims(RecordingP0Client(), RUN_ID, benchmark_result_set())
    blocked_client = RecordingP0Client()
    with self.assertRaisesRegex(ValidationError, "ready"):
        submit_engine_claims(blocked_client, RUN_ID, blocked_result_set())
    self.assertEqual(blocked_client.requests, [])
    with self.assertRaisesRegex(ValidationError, "extraction run"):
        submit_engine_claims(RecordingP0Client(), "different-run", project_result_set())
    with self.assertRaisesRegex(ValidationError, "result line project"):
        submit_engine_claims(RecordingP0Client(), RUN_ID, line_project_mismatch_result_set())
    with self.assertRaisesRegex(ValidationError, "primary evidence"):
        submit_engine_claims(RecordingP0Client(), RUN_ID, primary_evidence_mismatch_result_set())
```

- [ ] **Step 2: Run the focused adapter test and verify RED**

Run: `python -m unittest tests.test_engine_v2_p0_adapter -v`

Expected: FAIL because the adapter does not exist.

- [ ] **Step 3: Implement payload derivation and fail-closed guards**

Validate only facts already sealed inside `EngineResultSet`: the function argument matches `engine_result_set.extraction_run_id`; every ready line has the same project/run identity as its result set; evidence is non-empty, sorted, and duplicate-free; primary evidence belongs to that exact line set; and subject, confidence, trace, and digests are present. For each ready line, derive the route, subject fields, claim type, confidence, and evidence solely from the immutable line. Pass evidence to P0 as `[primary_evidence_item_id, *sorted(other_ids)]` so the existing repository records the declared primary first. Blocked lines are never posted; a set with no ready lines raises `ValidationError("engine result set has no ready lines")`. The nested claim payload is exactly:

```python
claim_payload = {
    "protocol": "helios.engine.claim-payload/v1",
    "result_line_sha256": line.sha256,
    "pack_sha256": line.pack_sha256,
    "rule_code": line.rule_code,
    "source_refs": [asdict(reference) for reference in line.source_refs],
    "bundle_sha256": line.bundle_sha256,
    "evidence_sha256": canonical_evidence_sha256(line.evidence_item_ids),
    "formula_trace_sha256": line.formula_trace.sha256,
    "calculation_basis": line.calculation_basis.value,
    "typed_value": asdict(line.value),
}
```

Use `Idempotency-Key: engine-result:{engine_result_line.sha256}`. Reject BENCHMARK sets, run mismatch, no-ready-line sets, empty ready-line evidence, primary evidence outside the line set, duplicate evidence, result/line project or run mismatch, or a client response whose exact field set is not `{"id"}`. Do not import `Database`, `TakeoffRepository`, `TakeoffService`, or any quantity/approval module. `P0Client` exposes only `post_json`; the existing P0 claim route remains authoritative for whether the run exists and whether each evidence item belongs to that run's project. The separate service/SDK plan supplies POST transport and its generated-P0-fixture test proves cross-project rejection and the allowed table deltas without adding a second adapter preflight API.

- [ ] **Step 4: Run the focused adapter test and verify GREEN**

Run: `python -m unittest tests.test_engine_v2_p0_adapter -v`

Expected: PASS for exact request derivation, internal identity/evidence consistency, deterministic ordering, replay key stability, and adapter-owned rejection cases. Actual run/project/evidence validation remains covered through the existing P0 claim route in the local service/SDK plan.

- [ ] **Step 5: Commit the adapter boundary**

```bash
git add src/helios_takeoff_core/engine/v2/p0_adapter.py src/helios_takeoff_core/engine/v2/__init__.py tests/test_engine_v2_p0_adapter.py
git commit -m "feat: add engine extraction-claim boundary"
```

---

### Task 11: Prove Installed V2 Kernels and V1 Compatibility

**Files:**

- Create: `src/helios_takeoff_core/engine/v2/acceptance.py`
- Create: `tests/test_engine_v2_acceptance.py`
- Modify: `tests/test_package_metadata.py`

**Interfaces:**

- Consumes: the three accepted installed packs, packaged capability matrix, `EngineStore`, pure evaluator, and v1 compatibility gate.
- Produces:

```python
def build_division23_v2_acceptance(work_root: Path) -> dict[str, object]: ...
```

The acceptance helper is a finite Python API for the later shared service/SDK/CLI plan; this task does not add or change a console command.

- [ ] **Step 1: Write the failing installed acceptance test**

```python
def test_installed_acceptance_runs_all_lanes_offline_and_keeps_stores_separate() -> None:
    with TemporaryDirectory() as temporary_directory:
        result = build_division23_v2_acceptance(Path(temporary_directory))
    self.assertEqual(result["acceptance"], "DIVISION23_V2_KERNELS_SUCCEEDED")
    self.assertEqual(set(result["lanes"]), {"AIRSIDE", "PIPING", "EQUIPMENT"})
    self.assertTrue(all(lane["ready_lines"] >= 1 for lane in result["lanes"].values()))
    self.assertTrue(all(lane["blocked_lines"] >= 1 for lane in result["lanes"].values()))
    self.assertEqual(result["database"]["authority_tables"], [])
    self.assertEqual(result["v1"]["demo_sha256"], V1_DEMO_SHA256)
    self.assertFalse(result["coverage"]["full_division23_claim_allowed"])
    self.assertIn("CONTROLS_BAS_INTERFACES", result["coverage"]["blocking_entries"])
```

- [ ] **Step 2: Run the focused acceptance test and verify RED**

Run: `python -m unittest tests.test_engine_v2_acceptance -v`

Expected: FAIL because the acceptance helper does not exist.

- [ ] **Step 3: Implement the finite offline proof**

Create a new non-existing `engine-v2.sqlite3` under `work_root`, initialize it with `EngineStore`, load all three pack resources through `importlib.resources`, compile/import/reload by digest, and evaluate one ready and one missing-input BENCHMARK fixture for each lane. Load and compile the packaged capability matrix, call `require_full_division23_coverage()`, require the expected `PreconditionError`, and report `full_division23_claim_allowed: false` plus the sorted blocking entries. Report exact pack, bundle, line, trace, matrix, and result-set digests. Rebuild all indexes and prove the loaded canonical bytes and evaluation digests are unchanged. Inspect the registry database and require its user-table set to equal the seven v2 tables from Task 3. Recompile the unchanged v1 demo and report its golden digest. Refuse to overwrite an existing work root database.

- [ ] **Step 4: Run the affected engine gate and verify GREEN**

Run:

```bash
python -m unittest \
  tests.test_engine_v1_compatibility \
  tests.test_engine_v2_contracts \
  tests.test_engine_v2_compiler \
  tests.test_engine_v2_formulas \
  tests.test_engine_v2_store \
  tests.test_engine_v2_evaluator \
  tests.test_engine_v2_assemblies \
  tests.test_engine_v2_airside \
  tests.test_engine_v2_piping \
  tests.test_engine_v2_equipment \
  tests.test_engine_v2_capability_matrix \
  tests.test_engine_v2_p0_adapter \
  tests.test_engine_v2_acceptance \
  tests.test_engine_domain_pack_compiler \
  tests.test_engine_domain_pack_repository \
  tests.test_engine_evaluator \
  tests.test_engine_cli \
  tests.test_engine_acceptance \
  tests.test_package_metadata -v
```

Expected: PASS within the ten-minute affected-integration budget. Existing v1 expected dictionaries, CLI output, migration version, digests, and database behavior remain unchanged.

- [ ] **Step 5: Build and inspect the clean wheel**

Run:

```bash
python -m build --wheel
python -c "import glob,zipfile; p=glob.glob('dist/helios_takeoff_core-*.whl')[-1]; z=zipfile.ZipFile(p); names=set(z.namelist()); required={'helios_takeoff_core/engine/v2/migrations/001_engine_registry.sql','helios_takeoff_core/engine/v2/schemas/domain-pack-v2.schema.json','helios_takeoff_core/engine/v2/schemas/observation-bundle-v1.schema.json','helios_takeoff_core/engine/v2/schemas/capability-matrix-v1.schema.json','helios_takeoff_core/engine/v2/capability_matrices/division23-v1.json','helios_takeoff_core/engine/v2/packs/airside-v2.json','helios_takeoff_core/engine/v2/packs/piping-v2.json','helios_takeoff_core/engine/v2/packs/equipment-v2.json'}; assert required <= names; assert not any(n.startswith('build_control/') or n.startswith('tools/helios_build/') for n in names)"
```

Expected: the wheel contains the v2 runtime migration, three schemas, capability matrix, and three packs, while excluding build-control, provider, browser, source-packet body, and research tooling.

- [ ] **Step 6: Run the final milestone gate once**

Run: `python -m unittest discover -s tests -v`

Expected: PASS within the twenty-minute milestone budget. Run it once initially and at most once more only after a substantive correction.

- [ ] **Step 7: Commit the installed capability proof**

```bash
git add src/helios_takeoff_core/engine/v2/acceptance.py tests/test_engine_v2_acceptance.py tests/test_package_metadata.py
git commit -m "feat: prove installed Division 23 v2 kernels"
```

## Completion Receipts and Handoff

This plan's code is complete only when all of these statements are evidenced by the Task 11 receipt:

- Build Fabric Tasks 1-9 were complete before the first v2 edit; Task 1 has a pre-edit frozen `DELIVERABLE_B_DIV23_V2` task/graph, real `LOCAL_ADAPTER` or `EXTERNAL_SESSION` handoff, distinct accepted review, Codex integration, and installed-code receipt on one task/base/patch/commit chain, and `PRODUCTION_SELF_USE.DELIVERABLE_B_DIV23_V2` is true. Missing local CLI/adapter capability was never bypassed: it either used the explicit external-session route or left the task blocked.
- V1 golden digest, evaluation, assembly, CLI, migration 017, and mixed P0/P1A database behavior are unchanged.
- EngineStore has a separate migration ledger/database, rejects authority databases, stores one canonical blob, and rebuilds projections.
- Pure compile/evaluate operations touch no database and work offline.
- Each lane produces at least one ready and one blocked structured-input result with resolved per-definition sources.
- Each lane source preserves its `admission_channel`, mutually exclusive packet/direct ingress ID, original-verification artifact, accepted-for-build event digest, and acceptance task. ATHENA_SOURCE_PACKET resolves through a canonical SHA-256 packet path/registry lookup; DIRECT_PRIMARY resolves its governed direct-admission record; only the separate research receipt claims the ATHENA round trip.
- Airside emits LF, SF, LB, and count; piping emits LF/count plus quantity-bearing connections; equipment emits the four reconciliation states plus cited accessory candidates.
- The packaged capability matrix reports only the bounded proofs as `EXPERIMENTAL`, reports unimplemented roadmap areas as `NOT_STARTED`, and rejects a full-Division-23 claim because required entries are not all `PRODUCTION_APPROVED`.
- BENCHMARK results cannot enter the P0 adapter.
- The adapter exposes only `post_json`, validates sealed result consistency, creates only extraction-claim requests derived from ready PROJECT_BOUND lines, relies on the existing P0 claim route for actual run/project/evidence validation, and cannot reach quantity, review, approval, release, or P1A routes.
- No live drawing ingestion, OCR, provider, NotebookLM, model, service, SDK, CLI change, or product UI is included.

After this plan merges, the local service/SDK/CLI plan consumes `compile_domain_pack`, `EngineStore.load_pack`, `compile_observation_bundle`, `evaluate_bundle`, and `submit_engine_claims` without duplicating formulas or weakening the P0 boundary.
