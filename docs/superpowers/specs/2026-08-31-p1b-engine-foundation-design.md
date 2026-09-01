# P1B HELIOS Engine Foundation Design

## Purpose

P1B begins the actual HELIOS engine. It adds a deterministic Division 23 domain-pack compiler and an immutable SQLite registry for the definitions that future takeoff, estimating, pricing, and bidding engines execute.

ATHENA, NotebookLM, Codex, and Claude are build-time collaborators outside the HELIOS runtime. They may propose source-backed domain packs, but HELIOS does not research, manage notebooks, schedule research, or learn from research conversations.

## Boundary

This slice owns reusable engine definitions:

- systems, components, materials, connections, accessories, equipment classes, and assemblies;
- typed relations between those catalog items;
- declarative measurement, classification, requirement, and assembly rules;
- immutable pack identity, canonical content digest, and production-rule provenance.

P0 continues to own project documents, evidence, extracted claims, quantities, takeoff snapshots, quotes, estimates, approvals, and bid releases. P1A continues to own worker execution. P1B domain packs cannot write either authority surface.

This slice does not ingest drawings, execute OCR or vision, calculate a project quantity, price work, approve a rule pack, or call an external AI/provider.

## Pack contract

The protocol is `helios.p1b.domain-pack/v1`. A pack contains:

- `pack_code`, `version`, `title`, and `jurisdiction`;
- one or more provenance records with a stable public URL, project-document reference, or content digest;
- catalog items with unique codes and a type from `SYSTEM`, `COMPONENT`, `MATERIAL`, `CONNECTION`, `ACCESSORY`, `EQUIPMENT`, or `ASSEMBLY`;
- typed relations whose endpoints exist in the same pack;
- declarative rules with a unique code, subject catalog item, rule type, required observations/evidence, output claim type, and optional output UOM.

The compiler performs exact-field validation, rejects duplicate codes and unresolved references, normalizes order, and computes SHA-256 over canonical compact JSON. It rejects executable code, callbacks, file paths, ambient commands, quantities, prices, approval/release instructions, and arbitrary unknown fields.

## Persistence

Migration `017_engine_domain_packs.sql` adds only immutable definition tables:

- `engine_domain_packs`
- `engine_pack_provenance`
- `engine_catalog_items`
- `engine_catalog_relations`
- `engine_rule_definitions`

Every row is append-only. `(pack_code, version)` and the canonical digest are unique. Importing identical canonical content is idempotent; importing different content under an existing `(pack_code, version)` fails. The import is one transaction.

Catalog codes are definitions, not P0 instance identifiers. Existing P0 `subject_key` values are unchanged. Later extraction engines may cite the pack digest, catalog code, and rule code in claim payloads while continuing to enter P0 through the existing extraction-run and extraction-claim authority boundary.

## Operator surface

The installed `helios-engine` CLI provides finite JSON operations:

- `compile --input PACK.json [--output COMPILED.json]`
- `import --database DB --input PACK.json`
- `pack show --database DB --pack-code CODE --version VERSION`
- `item show --database DB --pack-code CODE --version VERSION --item-code CODE`
- `evaluate --database DB --pack-code CODE --version VERSION --input CONTEXT.json`
- `assembly resolve --database DB --pack-code CODE --version VERSION --item-code CODE`

There is no daemon, scheduler, provider login, research endpoint, or automatic activation.

## Engine execution

The first runtime kernel consumes a compiled pack plus supplied observations and evidence kinds. It never invents missing input. For every rule attached to a subject catalog item it returns either a deterministic candidate claim intent or an explicit blocked result listing the missing observations/evidence. The result carries the pack digest, rule code, subject code, output claim type, and output UOM so a later extraction adapter can submit it through P0's existing claim boundary.

Assembly resolution follows only the standard structural relations `COMPOSED_OF`, `REQUIRES`, `REQUIRES_ACCESSORY`, `USES_MATERIAL`, and `USES_CONNECTION`. It returns a deterministic transitive component graph, rejects cycles, and does not assign project quantities or prices.

## Acceptance

One installed acceptance run compiles and imports a small non-authoritative fixture pack, proves deterministic digesting across input order, queries a component and its typed system/material relations, evaluates one ready and one blocked rule, resolves a nested assembly, proves an identical replay is idempotent, proves conflicting same-version content is rejected, and verifies every enumerated P0/P1A table count is unchanged.

Only one focused RED/GREEN cycle is run per implementation task and one full suite at the final branch gate.
