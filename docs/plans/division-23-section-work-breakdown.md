# Division 23 Section Work Breakdown Implementation Plan

**Execution entrypoint:** [Master section delivery plan](../superpowers/plans/2026-09-16-division23-section-delivery.md), with **56 individual section cards and 224 named delivery tasks**. Use each card's DEFINE/RESULT/CONNECT/QUALIFY criteria and scoped output dependencies. This document and the original 448 stage addresses describe coverage; the optional maintenance-tool plan does not replace mechanical section work.


> **For agentic workers:** Use `executing-plans` for the assigned task, with the root coordinator assigning exact write paths and independently accepting each result. This register records section coverage; CURRENT_STATUS.md Resume here is the live queue; it does not authorize a worker to change estimating rules, commit, or restart completed work.

**Goal:** Deliver every actual Division 23 section/subsection and its complete source-backed taxonomy through evidence, approved deterministic quantities, reconciliation, corrections, exports and fixed acceptance endpoints on Mac and Windows.

**Architecture:** [The machine-readable register](division-23-section-register.json) separates authority-qualified section identities, reusable stage contracts and section-specific planned coverage slots. Source paragraphs generate typed taxons and obligations, each with its own completion endpoint; shared infrastructure satisfies only the behavior it actually covers.

**Tech stack:** The existing local evidence/source-identity stores, deterministic calculation and review workflow, JSON work records, Excel/evidence-PDF outputs, and native Mac/Windows packages. This plan changes no application code or quantity authority.

**Delivery order: Mac first, as directed by the owner.** Build and accept the section/taxon capabilities on Mac. Keep shared code portable. `WINDOWS-DELIVERY-1` is a separate deferred delivery milestone after the connected workflow is ready; it is not a dependency of any section/taxon engineering task.

## Fixed acceptance, scope and current finish line

Acceptance is a **recorded pass against a frozen contract**, as defined in [the acceptance policy](../operations/acceptance.md). Freeze the deliverable, included/excluded scope, exact sources and fixtures, independent expected results, numerical tolerances where relevant, commands or bounded manual checks, evidence and reviewer. **Every mandatory check listed for that specific task must pass**, including zero unexplained omissions from its declared coverage set. Skipped or unavailable required checks remain open. An expected `UNKNOWN` or explicitly recorded source conflict can satisfy its named check; unfinished work in other tasks does not block this task. Independent coordinating Codex acceptance closes an authorized engineering task; it does not require a new owner confirmation for every implementation.

A missing fixture, expected result or numerical recognition threshold is `acceptance_definition_pending`, with named definition work. It is never an indefinite request for more testing. A new feature, source edition or higher target gets a new task or explicit contract version; unchanged accepted work stays closed. Existing D01–D10, A01–A12 and E01–E12 approvals remain settled.

The next implementation task is **`EQUIPMENT-COUNT-1`**, continuing the already approved calculation order and E01–E12/EC01–EC20. Connect physical-instance, package/procurement and installation quantities on Mac, with unknowns, corrections, reopening/history and source-linked exports. This shared implementation supports relevant section CALC tasks without completing an entire section.

The owner's hydronic mention explained the requested planning structure. It selected no section priority and requested no permanent worked example. The extra hydronic audit assignment is removed; sequencing follows existing approvals and actual dependencies.

## What is verified and what remains open

The register has **56 separate agency guide records and 448 named coverage slots**, all planned: **52 UFGS entries** from the live **August 2026** table of contents and **four individually verified VA guides**. These are known implementation inputs, not 56 authoritative CSI leaves. The complete CSI Division 23 denominator and hierarchy remain unresolved; no completion percentage is reported.

| Source | Exact boundary and retained identity |
| --- | --- |
| [UFGS August 2026 TOC](https://www.wbdg.org/FFC/DOD/UFGS/UFGS_TOC.pdf) | Division 23 on PDF pages 9–10, 52 entries from 23 01 30.41 through 23 84 19.00. PDF SHA-256 `a1782e639275c8936ff58395da73f7e01ab23f8d859a6fa5689c9ae86f3d7e80`; retrieval metadata and exact parsed rows are in the register. |
| [VA 23 21 13 Hydronic Piping](https://www.wbdg.org/FFC/VA/VAASC/VA%2023%2021%2013.pdf) | Guide dated 03-01-23; retained indexed text SHA-256 `acfe2927e09697d9c3d43f07725bccc8802e4787ad77f5ca17abb6cbab46d31f`, 52,297 characters. All 37 headings and their source spans are retained. |
| Other VA guides | 23 05 93.01 dated 11-01-21; 23 09 23 and 23 36 00 dated 03-01-23. Their exact headers and source-specific findings support separate records; their complete taxonomies remain open. |
| [CSI overview](https://www.csiresources.org/standards/overview) and [authorized publisher overview](https://theconstructionstandard.com/masterformat-division-23-heating-ventilating-and-air-conditioning-hvac) | Confirm edition-aware authoritative access, not a complete public subsection list. Two existing NotebookLM CSI records were also checked by the coordinator: identical 9,264-character orientation text, zero Division 23 section entries. |

The UFGS boiler identities are the August 2026 **23 52 00.01–.04** entries; do not revive a cached February inventory. The 23 52 00.04 title wraps across pages 9–10 and is retained as printed. The public VA inventory routes returned redirect/application shells; one browser attempt showed no list. That is an unavailable inventory, not an empty inventory.

Only supported Division 23 parentage is recorded. Decimal extensions and agency suffixes remain part of the exact identifiers; they are not silently converted into CSI children. A substitution or related-work reference is a typed relationship, not proof of hierarchy. The owner's hypothetical `23.02.01` is not a verified section number.

## Named catalogue and supporting work

- [ ] **`D23-CSI-CATALOGUE-1`** — obtain an authorized complete edition-specific CSI Division 23 source, pin its identity and permitted use, import its real numbers/titles/parentage, reconcile every guide mapping, and instantiate eight coverage dimensions for every newly verified actual section/subsection. Endpoint: no unaccounted-for authoritative entry or silently forced mapping. Missing authorized catalogue blocks the all-sections claim; known-guide work continues.

- [ ] **`D23-VA-INVENTORY-1`** — supplement the four individual VA records when a readable official inventory becomes available. Endpoint: exact dated source rows and explicit differences are recorded. It cannot replace the CSI task; no further retry is needed to execute this plan.

- [ ] **`EQUIPMENT-COUNT-1`** remains independently authorized shared-engine work under approved **E01–E12 / EC01–EC20**. Reuse accepted source/reconciliation/calculation/export behavior, and map only the relevant equipment taxons to it. A counter does not close pumps, boilers, chillers, terminal units or their complete sections.

Duct and air-device acceptance are not gates for these tasks. Only actual required inputs block a dependent action: an unknown pipe length blocks its calculated insulation amount, while its insulation requirement, controls responsibility and service scope can still be prepared. Claude's recorded expired OAuth affects that dispatch only; this plan used the authorized Codex fallback.

## Eight coverage dimensions for every section

Each section retains stable **`-TAX`, `-EVID`, `-RULE`, `-CALC`, `-RECON`, `-EDIT`, `-OUT`, `-ACCEPT`** coverage addresses. The 448 JSON objects are planned slots, not 448 ready tasks or required review rounds. The eight dimensions map to SC01–SC08 in the [delivery contract](../superpowers/specs/2026-09-16-division23-delivery-structure.md). One independently testable implementation may satisfy several scoped cells.

The dependency IDs express coverage relationships. Before an actual assignment, bind only required inputs to producer task, output, scope and identity. Partial accepted output can unblock consumers without completing the producer's whole section. No automatic scheduler may treat the template arrows as execution prerequisites. Freeze the selected task's contract as normal preparation; no all-register approval ceremony is required.

| Stage | Concrete delivered artifact and pass condition | Actual prerequisite |
| --- | --- | --- |
| **TAX** | `taxonomy.json`: every declared source clause and required type/variant has a reviewed source-bound record; 0 unexplained omissions, duplicate/orphan identities or unresolved required coverage gaps. A single-source audit is a supporting child. | Exact applicable source identity/body and frozen complete declared scope; source review uses the assigned section’s verified body. |
| **EVID** | `evidence-producer-contract.json`: drawing, schedule, specification and manual-review records preserve exact source/revision/page/region/role; original-source and ambiguous/missing cases meet frozen expectations. | Relevant TAX nodes and authorized original fixtures; scale only for measured routes. |
| **RULE** | `rule-packet.md` plus exact-hash examples/owner receipt: every taxon has an approved count/measure basis or non-quantity obligation output; no invented default or reopened unchanged approval. | Relevant TAX scope/options and genuinely unresolved owner rule decisions. EVID completion is not a preparation gate. |
| **CALC** | `quantity-integration.json`: connected app results match independent expected physical/package/installation/obligation outputs; deterministic replay and known-plus-UNKNOWN behavior pass. | Relevant EVID plus approved RULE; measured geometry/scale or package evidence only where actually needed. |
| **RECON** | `responsibility-crosslinks.json`: requirements, physical identity, package membership, supply/install/interfaces and service scope match named cases without duplicate purchases or phantom references. | Relevant TAX clauses; exact physical evidence only for links asserting a specific instance. Final quantities are not a blanket prerequisite. |
| **EDIT** | `revision-reopen-evidence.json`: corrections and changed source/rule bytes invalidate affected results only; exact expected histories, recalculation and reopened state pass. | Affected evidence/requirements; CALC or RECON only for changed quantities or relationships. |
| **OUT** | `export-verification.json` with Excel/evidence-PDF specimens: every expected row/total matches core and required links open the correct source; unknown/stale states remain visible. | Current evidence/requirements; CALC only for exported quantities. Partial known and unresolved scope can export. |
| **ACCEPT** | `acceptance.json`: all mandatory taxons/children/stages close; frozen estimator/representative and current Mac workflow criteria pass against exact bytes; independent reviewer records Mac engineering acceptance. | All mandatory taxon/stage results, frozen representative oracles and the current Mac workflow. No Windows prerequisite. |

Exact source/fixture sets and numerical recognition or representative thresholds have not been frozen for these section coverage slots. Their gate state is **`acceptance_definition_pending`**. Prepare and freeze those definitions before the relevant run; do not invent an accuracy target or select tolerance after seeing a failure.

## The 56 section work packages

Every section remains **open**, with planned coverage slots. The [completion matrix](division-23-completion-matrix.md) exposes every known row and its scoped endpoint. The full body must establish actual types/variants and required child scope; the title-derived work prompts in JSON are preparation subjects, not verified mechanical requirements. All task paths are planned outputs rather than claims that implementation artifacts exist.

| Authority / section | Verified title | Edition | Task stem |
| --- | --- | --- | --- |
| UFGS 23 01 30.41 | HVAC SYSTEM CLEANING | 05/22 | `D23-UFGS-230130.41` |
| UFGS 23 03 00 | BASIC MECHANICAL MATERIALS AND METHODS | 11/25 | `D23-UFGS-230300` |
| UFGS 23 05 15 | COMMON PIPING FOR HVAC | 05/22, CHG 2: 08/24 | `D23-UFGS-230515` |
| UFGS 23 05 48.19 | SEISMIC BRACING FOR MECHANICAL SYSTEMS | 02/25 | `D23-UFGS-230548.19` |
| UFGS 23 05 93 | TESTING, ADJUSTING, AND BALANCING FOR HVAC | 05/25 | `D23-UFGS-230593` |
| UFGS 23 07 00 | THERMAL INSULATION FOR MECHANICAL SYSTEMS | 08/24 | `D23-UFGS-230700` |
| UFGS 23 08 00 | COMMISSIONING OF MECHANICAL[ AND PLUMBING] SYSTEMS | 05/23, CHG 1: 08/24 | `D23-UFGS-230800` |
| UFGS 23 08 01.00 20 | TESTING INDUSTRIAL VENTILATION SYSTEMS | 04/06 | `D23-UFGS-230801.00-20` |
| UFGS 23 09 00 | INSTRUMENTATION AND CONTROL FOR HVAC | 08/24, CHG 1: 08/25 | `D23-UFGS-230900` |
| UFGS 23 09 13 | INSTRUMENTATION AND CONTROL DEVICES FOR HVAC | 11/15, CHG 2: 05/21 | `D23-UFGS-230913` |
| UFGS 23 09 23.01 | LONWORKS DIRECT DIGITAL CONTROL FOR HVAC AND OTHER BUILDING CONTROL SYSTEMS | 08/24 | `D23-UFGS-230923.01` |
| UFGS 23 09 23.02 | BACNET DIRECT DIGITAL CONTROL FOR HVAC AND OTHER BUILDING CONTROL SYSTEMS | 08/24 | `D23-UFGS-230923.02` |
| UFGS 23 09 53.00 20 | SPACE TEMPERATURE CONTROL SYSTEMS | 02/10, CHG 3: 08/24 | `D23-UFGS-230953.00-20` |
| UFGS 23 09 93 | SEQUENCES OF OPERATION FOR HVAC CONTROL | 11/15 | `D23-UFGS-230993` |
| UFGS 23 11 20 | FACILITY GAS PIPING | 05/20 | `D23-UFGS-231120` |
| UFGS 23 21 13.00 20 | LOW TEMPERATURE WATER (LTW) HEATING SYSTEM | 04/06, CHG 2: 11/19 | `D23-UFGS-232113.00-20` |
| UFGS 23 21 13.23 20 | [HIGH][MEDIUM] TEMPERATURE WATER SYSTEM WITHIN BUILDINGS | 07/07, CHG 1: 11/19 | `D23-UFGS-232113.23-20` |
| UFGS 23 21 23 | HYDRONIC PUMPS | 05/25 | `D23-UFGS-232123` |
| UFGS 23 22 26.00 20 | STEAM SYSTEM AND TERMINAL UNITS | 02/10, CHG 1: 05/15 | `D23-UFGS-232226.00-20` |
| UFGS 23 23 00 | REFRIGERANT PIPING | 08/21 | `D23-UFGS-232300` |
| UFGS 23 25 00 | CHEMICAL TREATMENT OF WATER FOR MECHANICAL SYSTEMS | 05/21 | `D23-UFGS-232500` |
| UFGS 23 30 00 | HVAC AIR DISTRIBUTION | 02/25 | `D23-UFGS-233000` |
| UFGS 23 35 16 | MECHANICAL ENGINE[ AND WELDING FUME] EXHAUST SYSTEMS | 02/25 | `D23-UFGS-233516` |
| UFGS 23 35 19.00 20 | INDUSTRIAL VENTILATION AND EXHAUST | 02/10, CHG 3: 11/24 | `D23-UFGS-233519.00-20` |
| UFGS 23 44 00.00 10 | CHEMICAL, BIOLOGICAL, AND RADIOLOGICAL (CBR) AIR FILTRATION SYSTEM | 02/16 | `D23-UFGS-234400.00-10` |
| UFGS 23 50 52 | CENTRAL HIGH TEMPERATURE WATER (HTW) GENERATING PLANTS | 08/26 | `D23-UFGS-235052` |
| UFGS 23 52 00.01 | LOW PRESSURE (<260 PSIG) WATER HEATING BOILERS (UNDER 6,000,000 BTU/HR INPUT) | 08/26 | `D23-UFGS-235200.01` |
| UFGS 23 52 00.02 | LOW PRESSURE (<260 PSIG) WATER HEATING BOILERS (OVER 6,000,000 BTU/HR INPUT) | 08/26 | `D23-UFGS-235200.02` |
| UFGS 23 52 00.03 | STEAM BOILERS AND EQUIPMENT (400,000 - 6,000,000 BTU/HR INPUT) | 08/26 | `D23-UFGS-235200.03` |
| UFGS 23 52 00.04 | STEAM BOILERS AND EQUIPMENT (OVER 6,000,000 BTU/HR) INPUT | 08/26 | `D23-UFGS-235200.04` |
| UFGS 23 52 30 | HEAT RECOVERY BOILERS | 08/26 | `D23-UFGS-235230` |
| UFGS 23 52 33.01 | STEAM HEATING PLANT WATERTUBE COAL/OIL OR COAL | 08/26 | `D23-UFGS-235233.01` |
| UFGS 23 52 33.02 | CENTRAL STEAM GENERATING SYSTEM - COMBINATION GAS AND OIL-FIRED | 08/26 | `D23-UFGS-235233.02` |
| UFGS 23 54 19 | BUILDING HEATING SYSTEMS, WARM AIR | 08/21 | `D23-UFGS-235419` |
| UFGS 23 57 10.00 10 | FORCED HOT WATER HEATING SYSTEMS USING WATER AND STEAM HEAT EXCHANGERS | 11/19 | `D23-UFGS-235710.00-10` |
| UFGS 23 63 00.00 | COLD STORAGE REFRIGERATION SYSTEMS | 08/22 | `D23-UFGS-236300.00` |
| UFGS 23 64 10 | WATER CHILLERS, VAPOR COMPRESSION TYPE | 05/25 | `D23-UFGS-236410` |
| UFGS 23 64 26 | CHILLED, CHILLED-HOT, AND CONDENSER WATER PIPING SYSTEMS | 11/25 | `D23-UFGS-236426` |
| UFGS 23 65 00 | COOLING TOWERS AND REMOTE EVAPORATIVELY-COOLED CONDENSERS | 05/25 | `D23-UFGS-236500` |
| UFGS 23 71 19 | THERMAL ENERGY STORAGE SYSTEM: ICE-ON-COIL | 05/18 | `D23-UFGS-237119` |
| UFGS 23 72 00 | ENERGY RECOVERY SYSTEMS | 05/24 | `D23-UFGS-237200` |
| UFGS 23 74 33 | DEDICATED OUTDOOR AIR SYSTEMS (DOAS) | 05/24 | `D23-UFGS-237433` |
| UFGS 23 75 15 | CUSTOM-PACKAGED, AIRCRAFT PRE-CONDITIONED AIR UNITS | 02/20, CHG 1: 05/24 | `D23-UFGS-237515` |
| UFGS 23 76 00 | EVAPORATIVE COOLING SYSTEMS | 08/21 | `D23-UFGS-237600` |
| UFGS 23 80 20.00 10 | GAS-FIRED HEATING EQUIPMENT | 05/20 | `D23-UFGS-238020.00-10` |
| UFGS 23 81 00 | DECENTRALIZED UNITARY HVAC EQUIPMENT | 05/24 | `D23-UFGS-238100` |
| UFGS 23 81 23 | COMPUTER ROOM AIR CONDITIONING UNITS | 11/20 | `D23-UFGS-238123` |
| UFGS 23 81 29 | VARIABLE REFRIGERANT FLOW HVAC SYSTEMS | 02/20 | `D23-UFGS-238129` |
| UFGS 23 81 47 | WATER-LOOP AND GROUND-LOOP HEAT PUMP SYSTEMS | 02/25 | `D23-UFGS-238147` |
| UFGS 23 82 00.00 20 | TERMINAL HEATING UNITS | 02/16, CHG 1: 08/18 | `D23-UFGS-238200.00-20` |
| UFGS 23 83 00.00 20 | ELECTRIC SPACE HEATING EQUIPMENT | 04/06 | `D23-UFGS-238300.00-20` |
| UFGS 23 84 19.00 | DESICCANT COOLING SYSTEMS | 02/18 | `D23-UFGS-238419.00` |
| VA 23 05 93.01 | DVA/USACE PROJECTS TESTING, ADJUSTING, AND BALANCING FOR HVAC | 11-01-21 | `D23-VA-230593.01` |
| VA 23 09 23 | DIRECT-DIGITAL CONTROL SYSTEM FOR HVAC | 03-01-23 | `D23-VA-230923` |
| VA 23 21 13 | HYDRONIC PIPING | 03-01-23 | `D23-VA-232113` |
| VA 23 36 00 | AIR TERMINAL UNITS | 03-01-23 | `D23-VA-233600` |

Every section uses its own source-qualified task stem and output directory. Similar numbers in different guide systems do not establish equivalent scope or authorize merging records.

## Taxon and section endpoints

Every reviewed taxon receives a stable source-qualified ID, an exact source span, kind/type/subtype, applicable service/material/size/joint and other attributes, supported relationships, exclusions/options, output basis and responsibility. Keep physical items, assembly parts, temporary work, consumables, tools/spares, services, references and deliverables distinct. Do not create a speculative service × material × size × joint cross-product.

Its coverage addresses follow `<taxon-id>-TAX` through `<taxon-id>-ACCEPT`; these do not require eight implementation tasks. The last is the explicit completion endpoint: all relevant frozen stage checks pass, representative expected results cover the required variants/exceptions, and the current Mac workflow evidence is accepted. Supporting engineering tasks may close earlier under their own finite contracts. Nonphysical taxons use an obligation/document/responsibility projection and applicable tests; they are not omitted because they lack a material quantity.

An **UNKNOWN obligation** must carry its source clause, missing input/document, affected taxon/results, next action and resolution history. Examples are an unchosen guide option, missing host, unshown field-work extent, unavailable referenced section or unknown supply responsibility. UNKNOWN is not zero. A mentioned section is not a supplied document. A repeated plan/schedule/spec appearance is not another physical object. Package purchase, internal physical part and installation work retain separate relationships and outputs.

A section reaches Mac engineering acceptance when its complete declared taxonomy and every mandatory child/taxon/stage pass the fixed contract, including connected edits/reopen/outputs, frozen representative expectations, and current Mac workflow checks. No required mechanical scope remains unresolved. A project's sourced not-applicable decision affects only that project revision; it cannot close product support. Full product completion additionally requires the authoritative CSI register, the separately scheduled Windows delivery milestone, and the owner's final product/release acceptance. That later milestone does not hold the Mac section endpoint open.

## Execution and handoff

Before each implementation assignment, the coordinator records exact checkout/base, source and rule hashes, sole-writer code/test paths, one deliverable, the fixed checks and output location. Shared paths remain singly owned. Workers report terminal results; the coordinator independently accepts/integrates, updates root `CURRENT_STATUS.md` and advances to the next unfinished task. Implementation workers own only their assigned paths and make no commits; Codex owns integration.

Prepared artifacts: this readable plan and the JSON register. Source identities and task definitions do not establish new quantity rules, complete CSI coverage, completed taxonomies or product acceptance. Resume the already approved physical-equipment implementation on Mac.
