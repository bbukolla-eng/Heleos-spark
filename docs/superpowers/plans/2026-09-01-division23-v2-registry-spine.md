# Division 23 V2 Registry Spine Implementation Plan

> **Execution:** Use `superpowers:subagent-driven-development`. Freeze and route the Build Fabric task before any production edit. One focused RED/GREEN cycle and one affected-integration gate; never rerun an unchanged failure.

**Goal:** Deliver the first usable HELIOS engine database: compile a broad, cited Division 23 semantic pack, store its immutable canonical bytes in an isolated SQLite database, query its graph projections, reopen it, and rebuild every projection deterministically.

**Supersession:** This plan replaces Tasks 1–3 of `2026-09-01-division23-v2-kernels.md`. Those tasks must not run in parallel. Formula execution, observation bundles, unit arithmetic, result sets, and calculation kernels become B2 and later work. The v1 engine, migration 017, P0, and P1A remain unchanged.

**Boundary:** This task contains no drawing/PDF/OCR ingestion, NotebookLM or ATHENA runtime, model inference, quantity calculation, price approval, estimate approval, or bid-release authority. Its bundled source is explicitly a non-authoritative `TEST_FIXTURE`; real public sources arrive later through governed research/source packets.

## Owned files

- Create `src/helios_takeoff_core/engine/v2/__init__.py`
- Create `src/helios_takeoff_core/engine/v2/contracts.py`
- Create `src/helios_takeoff_core/engine/v2/compiler.py`
- Create `src/helios_takeoff_core/engine/v2/store.py`
- Create `src/helios_takeoff_core/engine/v2/migrations/001_engine_registry.sql`
- Create `src/helios_takeoff_core/engine/v2/schemas/domain-pack-v2.schema.json`
- Create `tests/test_engine_v2_registry_spine.py`
- Create `tests/fixtures/engine_v2/full_division23_registry_pack.json`
- Modify `pyproject.toml`
- Modify `tests/test_package_metadata.py`

No other file is owned. In particular, do not modify P0/P1A/v1 migrations, repositories, services, APIs, CLI entry points, drawing paths, research paths, or Build Fabric implementation.

## Frozen pack envelope

Protocol: `helios.engine.domain-pack/v2`.

Required top-level fields:

```text
protocol, pack_code, version, title, jurisdiction,
valid_from, valid_through, supersedes_pack_sha256,
sources, definitions, relations,
units, observations, lookups, assembly_edges, rules
```

`sources`, `definitions`, and `relations` are implemented in B1. The five executable arrays are required and canonical but must be empty; non-empty executable content fails closed until B2. This reserves one future-compatible envelope without claiming formula execution exists.

Pack supersession exists only through `supersedes_pack_sha256`. It must resolve to an already imported pack with the same `pack_code`, a different digest/version, and an earlier `valid_from`. There is no within-pack `SUPERSEDES` relation.

Definition kinds:

```text
SYSTEM, EQUIPMENT, DUCT, PIPE, FITTING, VALVE, ACCESSORY,
MATERIAL, CONNECTION, INSULATION, SUPPORT, LABOR_ASSEMBLY,
PRICING_INPUT, RULE_CANDIDATE
```

Domain families:

```text
SHARED, AIRSIDE, PIPING, EQUIPMENT, INSULATION,
SUPPORTS, LABOR, PRICING, CODE_SPEC
```

Relation kinds:

```text
BELONGS_TO_SYSTEM, USES_MATERIAL, USES_CONNECTION, COMPOSED_OF,
REQUIRES_ACCESSORY, REQUIRES_INSULATION, REQUIRES_SUPPORT,
HAS_LABOR_ASSEMBLY, HAS_PRICING_INPUT, GOVERNED_BY_RULE
```

Every source has stable identity, authority class, stable reference, publisher, title, retrieval date, jurisdiction, and optional content digest. Every definition and relation has at least one `source_ref` resolving to a declared source and containing locator, applicability, and limitations. The compiler rejects floats, unknown fields, duplicate identities, unresolved sources/endpoints, empty citations, illegal relation domain/range, invalid validity intervals, non-empty executable arrays, and non-canonical values.

Canonical identity is order-independent across source, definition, relation, and citation arrays. The compiler deep-freezes compiled records and hashes exact UTF-8 canonical JSON bytes.

## Frozen public API

`contracts.py` defines the closed enums plus immutable `DefinitionRecord`, `RelationRecord`, `CitationRecord`, `CompiledDomainPack`, and `StoreIntegrityReport`.

```python
def compile_domain_pack(document: object) -> CompiledDomainPack: ...

class EngineStore:
    def __init__(self, path: str | Path) -> None: ...
    @property
    def path(self) -> Path: ...
    def initialize(self) -> None: ...
    def import_pack(self, pack: CompiledDomainPack) -> str: ...
    def load_pack(self, *, pack_sha256: str) -> CompiledDomainPack: ...
    def get_source(self, *, pack_sha256: str, source_id: str) -> dict[str, object]: ...
    def get_definition(self, *, pack_sha256: str, definition_code: str) -> DefinitionRecord: ...
    def list_definitions(self, *, pack_sha256: str, kind: DefinitionKind | None = None, family: DomainFamily | None = None) -> tuple[DefinitionRecord, ...]: ...
    def get_relation(self, *, pack_sha256: str, relation_code: str) -> RelationRecord: ...
    def neighbors(self, *, pack_sha256: str, definition_code: str, relation: RelationKind | None = None, direction: str = "OUT") -> tuple[RelationRecord, ...]: ...
    def list_citations(self, *, pack_sha256: str, assertion_kind: str, assertion_code: str) -> tuple[CitationRecord, ...]: ...
    def list_events(self, *, pack_sha256: str) -> tuple[dict[str, object], ...]: ...
    def rebuild_indexes(self, *, pack_sha256: str | None = None) -> None: ...
    def verify_integrity(self) -> StoreIntegrityReport: ...
```

## Isolated database

Migration `001_engine_registry.sql` creates exactly seven user tables:

1. `engine_store_migrations`
2. `engine_packs`
3. `engine_source_index`
4. `engine_definition_index`
5. `engine_relation_index`
6. `engine_citation_index`
7. `engine_registry_events`

`engine_packs.canonical_json` is the sole semantic authority. The four index tables are disposable projections. Registry events are append-only import/rebuild provenance. Pack rows and events are immutable. Foreign keys and integrity triggers fail closed. The store registers a deterministic canonical-pack SQLite function, records packaged migration hashes, and rejects P0/P1A/v1 databases plus unknown user tables.

Imports use `BEGIN IMMEDIATE`. Exact digest replay makes no semantic write and creates no extra import event. Changed bytes under one `(pack_code, version)` conflict. Reopen/load recompiles and byte-verifies the authoritative blob. Rebuild replaces projections from that blob and appends one chained rebuild event. Integrity verification checks canonical bytes, hashes, projection equality, citations, endpoints, migration digest, and event-chain identity.

## Acceptance fixture and behavior

The fixture covers every definition kind and all ten relation kinds across airside, piping, equipment, insulation, supports, labor, pricing, and code/spec families. It includes multiple `TEST_FIXTURE` sources so citation resolution and source identity are exercised without claiming production authority.

One focused module proves:

- unchanged v1 golden pack digest and migration 017 digest;
- strict compile and order-independent identity;
- rejection of malformed, uncited, unresolved, executable, and domain-invalid packs;
- exactly seven v2 tables and an isolated migration ledger;
- atomic idempotent import, conflict behavior, pack-level supersession checks, reopen/load, queries, and graph neighbors;
- citation resolution for every definition and relation;
- deterministic projection rebuild and append-only events;
- fail-closed tamper detection;
- packaged migration/schema resources and continued exclusion of `tools/` and `build_control/` from the wheel.

Focused command:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_engine_v2_registry_spine \
  tests.test_package_metadata -v
```

The affected gate adds existing v1 engine/compiler/database/package tests once. Completion requires a real external or configured local builder, distinct independent review, Codex-created canonical commit, and Build Fabric integration receipt.
