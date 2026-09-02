# Division 23 V2 Calculation and Assembly Kernel — B2 Implementation Plan

**Goal:** Turn the integrated Division 23 v2 semantic registry into a deterministic, source-cited calculation and assembly engine for structured benchmark inputs across equipment, duct, pipe, fittings, valves, accessories, materials, connections, insulation, supports, labor, and pricing.

**Boundary:** B2 is runtime engine code, not a CLI product, agent, drawing reader, research adapter, pricing authority, approval path, or bid release system. It consumes an explicit immutable pack SHA and a strict structured observation bundle. Every output is a `NON_AUTHORITATIVE_CANDIDATE`. It writes no P0/P1A facts and introduces no new SQLite tables.

## Architecture

Keep three layers distinct:

1. **Ontology:** existing definitions, relations, citations, and immutable pack identity.
2. **Executable knowledge:** units, observation definitions, lookups, rules, and relation-bound assembly edges inside the same canonical pack bytes.
3. **Runtime facts/results:** immutable structured observation bundles and deterministic result sets held outside the registry database.

Executable rules must resolve to a `RULE_CANDIDATE` definition and an existing `GOVERNED_BY_RULE` relation. Assembly edges reference existing relation codes rather than duplicating endpoints or relation kinds. The allowed assembly relation kinds are `COMPOSED_OF`, `USES_MATERIAL`, `USES_CONNECTION`, `REQUIRES_ACCESSORY`, `REQUIRES_INSULATION`, `REQUIRES_SUPPORT`, `HAS_LABOR_ASSEMBLY`, and `HAS_PRICING_INPUT`.

## Owned Runtime Files

Modify:

- `src/helios_takeoff_core/engine/v2/__init__.py`
- `src/helios_takeoff_core/engine/v2/contracts.py`
- `src/helios_takeoff_core/engine/v2/compiler.py`
- `src/helios_takeoff_core/engine/v2/store.py`
- `src/helios_takeoff_core/engine/v2/schemas/domain-pack-v2.schema.json`
- `tests/test_engine_v2_registry_spine.py`
- `tests/test_package_metadata.py`

Create:

- `src/helios_takeoff_core/engine/v2/formulas.py`
- `src/helios_takeoff_core/engine/v2/evaluator.py`
- `src/helios_takeoff_core/engine/v2/schemas/observation-bundle-v1.schema.json`
- `tests/test_engine_v2_calculation_assembly.py`
- `tests/fixtures/engine_v2/full_division23_calculation_pack.json`
- `tests/fixtures/engine_v2/airside_equipment_bundle.json`
- `tests/fixtures/engine_v2/piping_bundle.json`

Do not modify migration `001_engine_registry.sql`; canonical pack bytes already persist all five executable arrays. Extend store integrity comparison only so reopen/load verifies the new compiled indexes.

## Frozen Interfaces

Add constants `OBSERVATION_BUNDLE_PROTOCOL` and `RESULT_SET_PROTOCOL`; exact scalar, bundle-mode, evaluation-status, result-kind, calculation-basis, and candidate-authority enums; and frozen typed value, observation, bundle, formula evaluation, assembly node, result line, and result set dataclasses.

Expose:

```python
def compile_observation_bundle(document: object, *, pack: CompiledDomainPack) -> ObservationBundle: ...
def evaluate_formula(expression, *, expected_scalar_type, expected_uom, observations, pack) -> FormulaEvaluation: ...
def evaluate_bundle(pack: CompiledDomainPack, bundle: ObservationBundle) -> EngineResultSet: ...
def resolve_assembly(pack: CompiledDomainPack, bundle: ObservationBundle, *, item_code: str, quantity: TypedValue) -> AssemblyNode: ...
```

B2 accepts `BENCHMARK` bundles only. `PROJECT_BOUND` is reserved and rejected as not implemented because B2 owns no project identity, evidence, or approval state.

## Deterministic Formula Model

The closed expression AST supports only literals, named observations, typed lookups, arithmetic, comparison, conditional expressions, minimum, and maximum. Bound it to depth 32, 256 nodes per expression, and 4,096 nodes per pack. Forbid floats, exponent notation, NaN, callbacks, imports, files, environment values, commands, URLs, and provider fields.

Numeric values are exact decimal strings. Unit conversions use exact rational factors and dimension vectors for count, length, mass, time, and currency. Never round an inexact conversion. Addition, subtraction, minimum, maximum, and comparisons require compatible dimensions. Multiplication adds dimension exponents; division subtracts them and blocks division by zero. Count outputs must be integral; quantities and multiplicities must be nonnegative.

Missing observations, missing lookup keys, invalid conversions, and divide-by-zero yield deterministic `BLOCKED` candidates, never defaults or guesses. A false rule condition emits no line and records the skipped rule. A false assembly-edge condition omits that child and records the skipped relation.

## Assembly Semantics

Traverse outgoing edges sorted by relation code and preserve relation-code paths. Do not globally deduplicate nodes: a diamond represents two real contributions. Reject assembly cycles at compile time and retain a path-local runtime cycle guard. Child quantity equals exact parent quantity multiplied by exact edge multiplicity and converted to the declared output unit; zero children are omitted. Any reachable blocked edge blocks that assembly line, so no partial candidate is presented as complete.

Every result line carries the explicit pack SHA, bundle SHA, rule code, relation path, exact cited-source union, trace digest, result digest, authority, status, and calculation basis. Confidence is the minimum confidence of observations actually read. Pricing and labor remain separate typed child candidates and are never bid eligible in B2.

## Proof Fixtures

The benchmark pack covers:

- Airside/equipment: AHU, rectangular duct, access door, galvanized material, slip connection, duct wrap, trapeze support, duct labor, and duct pricing.
- Piping: heating-water supply pipe, elbow, balancing valve, copper, solder connection, pipe insulation, pipe hanger, pipe labor, and pipe pricing.

All fixture rates are cited `TEST_FIXTURE` data labeled `benchmark only; not bid eligible`. Tests prove exact results and hashes, false conditional omission, blocked missing input, inexact conversion blocking, no floats, graph reference/type errors, cycle rejection, diamond multiplication, immutability, order-independent pack identity, executable-pack reopen, and unchanged B1 empty-pack/v1 identities.

## Verification Budget

- One focused RED/GREEN pass: `PYTHONPATH=src python3 -m unittest tests.test_engine_v2_calculation_assembly -v`
- One affected integration pass after independent acceptance: `PYTHONPATH=src python3 -m unittest tests.test_engine_v2_calculation_assembly tests.test_engine_v2_registry_spine tests.test_engine_domain_pack_compiler tests.test_engine_domain_pack_repository tests.test_engine_evaluator tests.test_package_metadata -v`
- At most one narrow correction task. No repeated full-suite loop.

## Done

B2 is done when a caller loads one explicit immutable v2 pack and evaluates the two structured benchmark bundles into deterministic, fully traced, source-cited, non-authoritative candidates spanning the full Division 23 registry families without drawing ingestion, provider access, project writes, or commercial authority.
