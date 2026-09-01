# ATHENA Source Packet Intake — R1A Implementation Plan

**Goal:** Build the first usable, repository-local receiving boundary through which Codex or Claude can issue one engine research question, import cited packets from one or more ATHENA/NotebookLM notebooks, deduplicate public source identities, and prepare an immutable task-scoped builder bundle.

**Position:** This bounded increment controls before the larger `2026-09-01-athena-research-roundtrip.md` plan. It implements packet intake and builder consumption now. Independent original-source verification, task-scoped source acceptance, `ResearchOutcome`, real authenticated browser receipts, and direct-primary admission follow in R1B; they do not delay R1A.

**Boundary:** ATHENA and NotebookLM remain external build-time research assistants. Their packets are advisory inputs, never engine rules, quantities, prices, approvals, or bids. This task contains no drawing ingestion, browser automation, provider API, login logic, source body, new SQLite database, P0/P1A write, or installed runtime dependency.

## Real Build Fabric Interfaces

R1A uses the interfaces that exist in the repository:

- `BuildPaths` exposes `repo_root` and `state_root`.
- `append_event(path, event, lock_root)` receives the external lock root.
- `ContentAddressedStore` collections are single path components and `StoredObject` exposes `sha256`, `path`, and `replayed`.
- Core CLI commands register through `register_command`.
- A frozen BuildTask's `required_source_packet_ids` are task-scoped dependency node IDs. Each must resolve inside that task's committed graph to an `INTEGRATED` `SourcePacket` node whose `manifest_sha256` is the immutable packet digest. Distinct task-scoped node IDs may point to the same reusable packet digest.

## Owned Files

Create:

- `build_control/schemas/notebook-manifest-v1.schema.json`
- `build_control/schemas/athena-worker-profile-v1.schema.json`
- `build_control/schemas/research-request-v1.schema.json`
- `build_control/schemas/source-record-v1.schema.json`
- `build_control/schemas/source-packet-v1.schema.json`
- `build_control/schemas/builder-source-bundle-v1.schema.json`
- `build_control/worker_profiles/athena.v1.json`
- `build_control/worker_profiles/athena.instructions.v1.md`
- `build_control/source_registry/notebooks.v1.json`
- `build_control/source_registry/README.md`
- `tools/helios_build/source_registry.py`
- `tools/helios_build/research.py`
- `tests/test_build_research_packets.py`
- `tests/test_build_research_task_input.py`
- `tests/test_build_distribution_boundary.py`

Modify:

- `tools/helios_build/cli.py`

Generated immutable state:

```text
build_control/tasks/research_requests/sha256/<digest>.json
build_control/source_packets/sha256/<digest>.json
build_control/source_registry/sources/sha256/<digest>.json
build_control/source_registry/imports.jsonl
build_control/builder_source_bundles/sha256/<digest>.json
```

## Contracts

### Notebook manifest

The immutable initial manifest contains exactly eleven logical notebooks:

1. `N01` — Takeoff methodology and drawing interpretation
2. `N02` — Duct, fittings, and SMACNA
3. `N03` — Hydronic, refrigerant, and condensate piping
4. `N04` — Equipment, schedules, and manufacturer literature
5. `N05` — Insulation, supports, seismic, and vibration
6. `N06` — NYC/NYS codes, public work, labor, and tax
7. `N07` — Pricing, procurement, and vendor intelligence
8. `N08` — OCR, computer vision, PDF/CAD, and AI models
9. `N09` — Estimating, bidding, proposals, and risk
10. `N10` — Project management, submittals, TAB, and closeout
11. `N11` — HELIOS software architecture, database, and agent engineering

No live NotebookLM URL, provider notebook ID, credential, cookie, or account identity is stored in Git. A reviewed successor manifest may add notebooks without changing importer code.

### ResearchRequest

Codex or Claude supplies an exact question, target engine component, target logical notebook, jurisdiction, `US_CUSTOMARY` units, desired authority class, exclusions, deadline, notebook-manifest digest, and ATHENA-profile digest. Export validates and content-addresses the request; it does not call a provider.

### SourceRecord

One canonical public or authorized source identity contains URL or authorized-document identity, publisher, title, publication/effective/retrieval dates, jurisdiction, authority/data class, license notes, and optional content digest. It never contains a source body. The same source appearing in multiple notebooks resolves to one record and preserves all packet/notebook memberships.

### SourcePacket

One request-bound response from exactly one logical notebook contains immutable packet identity, exact request/manifest/profile hashes, sources, resolved or unresolved citations with precise locators, findings, applicability, conflicts, limitations, confidence, unanswered questions, and sanitized synthesis hashes. It contains no prompt transcript, browser state, provider credential, code, domain pack, or engine authority.

### BuilderSourceBundle

A deterministic derivative for one frozen BuildTask contains only its declared and integrated packet dependencies, resolved source records, findings, conflicts, limitations, and gaps. It cannot contain code, executable rules, domain packs, quantities, or commercial decisions.

## Operator Surface

```bash
python -m tools.helios_build research request export request.json
python -m tools.helios_build research packet import packet-n02.json packet-n03.json
python -m tools.helios_build research prepare-task <task-manifest-sha256>
```

Packet import validates the complete batch before publishing its final append-only import event. Exact replay is idempotent. Changed bytes under an existing identity conflict; corrections use explicit successor IDs.

## Acceptance Behaviors

1. A valid batch imports packets from multiple logical notebooks and emits immutable packet/source identities.
2. Every packet binds to the exact exported request hash, notebook manifest hash, ATHENA profile hash, jurisdiction, unit system, and notebook ID.
3. One invalid packet makes the whole batch non-authoritative: no final batch event or partially indexed import appears.
4. One source referenced from several packets/notebooks resolves to one canonical SourceRecord plus multiple memberships; non-identical identity reuse fails closed.
5. Exact replay is idempotent; changed packet content conflicts; successor lineage is explicit and non-cyclic.
6. Resolved citations require precise locators. Unresolved citations, conflicts, limitations, and unanswered questions remain visible in the builder bundle.
7. `prepare-task` includes only the frozen task's declared, integrated `SourcePacket` dependency nodes and fails on missing, unintegrated, wrong-type, superseded, or digest-mismatched packet lineage.
8. ATHENA profile permissions allow source discovery, notebook maintenance, gap recognition, conflict comparison, cited synthesis, and builder-usefulness feedback only; they forbid code/database/rule/quantity/price/approval/bid authority.
9. Clean distribution inspection proves Build Fabric, research code, ATHENA, NotebookLM/provider code, and generated research artifacts are absent from the runtime wheel.

## Verification Budget

- One focused RED/GREEN cycle for the substantive implementation.
- One affected-integration gate during independent review.
- No full-suite loop in R1A.
- A remaining Critical/Major defect receives at most one substantive correction; otherwise split or escalate.

## Done

R1A is done when installed repository tooling—not a fixture wrapper—can export a strict request, batch-import multiple cited packets, preserve cross-notebook source identity, and prepare a frozen task's exact builder bundle while the shipped HELIOS runtime remains fully independent of ATHENA, NotebookLM, Grok Bot, browser sessions, and the research artifacts.
