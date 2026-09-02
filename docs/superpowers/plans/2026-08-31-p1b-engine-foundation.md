# P1B HELIOS Engine Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build HELIOS's first real Division 23 engine foundation: deterministic domain-pack compilation, immutable SQLite persistence, and a finite installed CLI proof.

**Architecture:** External builders produce candidate JSON domain packs. HELIOS strictly compiles them into canonical, non-executable definitions and atomically stores immutable catalog items, relations, rules, and provenance. Project-specific evidence, quantities, pricing, approvals, and bids remain in P0.

**Tech Stack:** Python 3.12 standard library, SQLite, `argparse`, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-31-p1b-engine-foundation-design.md`

## Global Constraints

- HELIOS is not a research system; do not add NotebookLM, ATHENA, research-loop, web-search, or provider-runtime code.
- Do not ingest drawings or specifications in this phase.
- Do not write P0/P1A authority tables from the engine package.
- Domain packs are strict, canonical, immutable, declarative, and non-executable.
- Do not add third-party runtime dependencies.
- Use one focused RED/GREEN cycle per task and one final full-suite run only.

---

### Task 1: Strict domain-pack compiler

**Files:**
- Create: `src/helios_takeoff_core/engine/__init__.py`
- Create: `src/helios_takeoff_core/engine/contracts.py`
- Create: `src/helios_takeoff_core/engine/compiler.py`
- Create: `tests/test_engine_domain_pack_compiler.py`

**Interfaces:**
- Produces `DOMAIN_PACK_PROTOCOL = "helios.p1b.domain-pack/v1"`.
- Produces frozen `CompiledDomainPack` with canonical document, canonical bytes, SHA-256 digest, and indexed catalog/rule access.
- Produces `compile_domain_pack(document: object) -> CompiledDomainPack`.

- [ ] Write focused tests for a valid pack, order-independent digest, exact fields, duplicate/unresolved codes, unsupported UOM/type, and prohibited executable/bid-authority fields.
- [ ] Run `python3 -m unittest tests.test_engine_domain_pack_compiler -v` once and record RED.
- [ ] Implement strict parsing, normalization, cross-reference checks, canonical JSON, and digesting.
- [ ] Re-run only the focused test module and record GREEN.
- [ ] Commit compiler and tests.

### Task 2: Immutable domain-pack registry

**Files:**
- Create: `src/helios_takeoff_core/migrations/017_engine_domain_packs.sql`
- Create: `src/helios_takeoff_core/engine/repository.py`
- Create: `tests/test_engine_domain_pack_repository.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_p1a_cli.py`

**Interfaces:**
- Consumes `CompiledDomainPack`.
- Produces `EngineRepository.import_domain_pack(pack) -> str`.
- Produces `get_domain_pack`, `get_catalog_item`, `list_catalog_relations`, and `get_rule_definition`.

- [ ] Write focused tests for schema 17, atomic normalized import, immutable tables, idempotent replay, same-version conflict, exact query results, zero P0/P1A mutations, and the existing P0/P1A init commands reporting schema 17.
- [ ] Run `python3 -m unittest tests.test_engine_domain_pack_repository tests.test_database.DatabaseBootstrapTests.test_initialize_applies_versioned_schema_and_enables_foreign_keys -v` once and record RED.
- [ ] Add migration 017 and repository implementation using one re-entrant transaction.
- [ ] Re-run only the focused command and record GREEN.
- [ ] Commit persistence and tests.

### Task 3: Deterministic rule and assembly engine

**Files:**
- Create: `src/helios_takeoff_core/engine/evaluator.py`
- Create: `tests/test_engine_evaluator.py`

**Interfaces:**
- Produces `evaluate_subject(pack, *, subject_code, observations, evidence_kinds) -> dict[str, object]`.
- Produces `resolve_assembly(pack, *, item_code) -> dict[str, object]`.

- [ ] Write focused tests for ready versus blocked rules, exact lineage, deterministic output, unknown subjects, nested assembly resolution, duplicate traversal, and cycle rejection.
- [ ] Run `PYTHONPATH=src python3 -m unittest tests.test_engine_evaluator -v` once and record RED.
- [ ] Implement the pure rule evaluator and structural assembly resolver without database or P0 writes.
- [ ] Re-run only the focused command and record GREEN.
- [ ] Commit the engine kernel.

### Task 4: Installed finite operator surface

**Files:**
- Create: `src/helios_takeoff_core/engine/acceptance.py`
- Create: `src/helios_takeoff_core/engine_cli.py`
- Create: `examples/p1b_engine_acceptance.py`
- Create: `tests/test_engine_cli.py`
- Create: `tests/test_engine_acceptance.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_package_metadata.py`
- Modify: `README.md`

**Interfaces:**
- Produces installed `helios-engine` JSON CLI for compile/import/show/evaluate/resolve operations.
- Produces `build_engine_foundation_acceptance(work_root: Path) -> dict[str, object]` with marker `P1B_ENGINE_FOUNDATION_SUCCEEDED`.

- [ ] Write focused CLI and acceptance tests, including real evaluation/assembly results, P0/P1A before/after count equality, and finite execution counters.
- [ ] Run `PYTHONPATH=src python3 -m unittest tests.test_engine_cli tests.test_engine_acceptance tests.test_package_metadata -v` once and record RED.
- [ ] Implement CLI, installed acceptance, compatibility wrapper, metadata, and concise README usage.
- [ ] Re-run only the focused command and record GREEN.
- [ ] Commit the usable operator surface.

### Task 5: One final branch gate

**Files:**
- Create: `docs/verification/p1b-engine-foundation-acceptance.md`

- [ ] Build a fresh wheel without network dependency resolution and install it into a fresh virtual environment.
- [ ] Run installed `helios-engine --help` and installed finite acceptance.
- [ ] Run `compileall`, the full unit suite exactly once, and `git diff --check`.
- [ ] Record exact commands, results, limitations, and final `git rev-parse HEAD` in the verification document.
- [ ] Commit verification evidence and request independent whole-branch review.
