# Division 23 Section Work Breakdown Implementation Plan

> **For agentic workers:** Use `executing-plans` for the assigned task, with the root coordinator assigning exact write paths and independently accepting each result. This register is the section queue; it does not authorize a worker to change estimating rules, commit, or restart completed work.

**Goal:** Deliver every actual Division 23 section/subsection and its complete source-backed taxonomy through evidence, approved deterministic quantities, reconciliation, corrections, exports and fixed acceptance endpoints on Mac and Windows.

**Architecture:** [The machine-readable register](division-23-section-register.json) separates authority-qualified section identities, reusable stage contracts and section-specific task instances. Source paragraphs generate typed taxons and obligations, each with its own completion endpoint; shared infrastructure satisfies only the behavior it actually covers.

**Tech stack:** The existing local evidence/source-identity stores, deterministic calculation and review workflow, JSON work records, Excel/evidence-PDF outputs, and native Mac/Windows packages. This plan changes no application code or quantity authority.

**Delivery order: Mac first, as directed by the owner.** Build and accept the section/taxon capabilities on Mac. Keep shared code portable. `WINDOWS-DELIVERY-1` is a separate deferred delivery milestone after the connected workflow is ready; it is not a dependency of any section/taxon engineering task.

## Fixed acceptance, scope and current finish line

Acceptance is a **recorded pass against a frozen contract**, as defined in [the acceptance policy](../operations/acceptance.md). Freeze the deliverable, included/excluded scope, exact sources and fixtures, independent expected results, numerical tolerances where relevant, commands or bounded manual checks, evidence and reviewer. **Every mandatory check listed for that specific task must pass**, including zero unexplained omissions from its declared coverage set. Skipped or unavailable required checks remain open. An expected `UNKNOWN` or explicitly recorded source conflict can satisfy its named check; unfinished work in other tasks does not block this task. Independent coordinating Codex acceptance closes an authorized engineering task; it does not require a new owner confirmation for every implementation.

A missing fixture, expected result or numerical recognition threshold is `acceptance_definition_pending`, with named definition work. It is never an indefinite request for more testing. A new feature, source edition or higher target gets a new task or explicit contract version; unchanged accepted work stays closed. Existing D01–D10, A01–A12 and E01–E12 approvals remain settled.

The immediate task is **`D23-VA-232113-TAX`**. Its first executable child is **`D23-VA-232113-TAX-AUDIT-1`**: finish a source-bound audit of the retained VA hydronic guide's **37 numbered paragraphs**, including every subordinate clause and identified entity, variant, attribute, relationship, reference, exclusion, option and service/deliverable obligation. Deliver `docs/engineering/division23/va-232113/source-audit.json`; apply the frozen HA01–HA08 checks below. The 222 prepared seeds are starting work items to normalize and split, not a final taxon count.

That bounded audit has a finite endpoint. Passing it does **not** close the whole section TAX task or establish the complete global hydronic taxonomy. Parent TAX must freeze the complete declared section scope and evidence set, reconcile other required source/child coverage, and resolve required coverage gaps before it closes. Whole-section engineering acceptance additionally requires every mandatory taxon and stage, frozen representative expectations, and current Mac workflow evidence. Record that platform scope; Windows delivery is separate.

## What is verified and what remains open

The register has **56 separate agency guide records and 448 named section-stage tasks**, all open: **52 UFGS entries** from the live **August 2026** table of contents and **four individually verified VA guides**. These are known implementation inputs, not 56 authoritative CSI leaves. The complete CSI Division 23 denominator and hierarchy remain unresolved; no completion percentage is reported.

| Source | Exact boundary and retained identity |
| --- | --- |
| [UFGS August 2026 TOC](https://www.wbdg.org/FFC/DOD/UFGS/UFGS_TOC.pdf) | Division 23 on PDF pages 9–10, 52 entries from 23 01 30.41 through 23 84 19.00. PDF SHA-256 `a1782e639275c8936ff58395da73f7e01ab23f8d859a6fa5689c9ae86f3d7e80`; retrieval metadata and exact parsed rows are in the register. |
| [VA 23 21 13 Hydronic Piping](https://www.wbdg.org/FFC/VA/VAASC/VA%2023%2021%2013.pdf) | Guide dated 03-01-23; retained indexed text SHA-256 `acfe2927e09697d9c3d43f07725bccc8802e4787ad77f5ca17abb6cbab46d31f`, 52,297 characters. All 37 headings and their source spans are retained. |
| Other VA guides | 23 05 93.01 dated 11-01-21; 23 09 23 and 23 36 00 dated 03-01-23. Their exact headers and source-specific findings support separate records; their complete taxonomies remain open. |
| [CSI overview](https://www.csiresources.org/standards/overview) and [authorized publisher overview](https://theconstructionstandard.com/masterformat-division-23-heating-ventilating-and-air-conditioning-hvac) | Confirm edition-aware authoritative access, not a complete public subsection list. Two existing NotebookLM CSI records were also checked by the coordinator: identical 9,264-character orientation text, zero Division 23 section entries. |

The UFGS boiler identities are the August 2026 **23 52 00.01–.04** entries; do not revive a cached February inventory. The 23 52 00.04 title wraps across pages 9–10 and is retained as printed. The public VA inventory routes returned redirect/application shells; one browser attempt showed no list. That is an unavailable inventory, not an empty inventory.

Only supported Division 23 parentage is recorded. Decimal extensions and agency suffixes remain part of the exact identifiers; they are not silently converted into CSI children. A substitution or related-work reference is a typed relationship, not proof of hierarchy. The owner's hypothetical `23.02.01` is not a verified section number.

## Named catalogue and supporting work

- [ ] **`D23-CSI-CATALOGUE-1`** — obtain an authorized complete edition-specific CSI Division 23 source, pin its identity and permitted use, import its real numbers/titles/parentage, reconcile every guide mapping, and instantiate eight stage tasks for every newly verified actual section/subsection. Endpoint: no unaccounted-for authoritative entry or silently forced mapping. Missing authorized catalogue blocks the all-sections claim; known-guide work continues.

- [ ] **`D23-VA-INVENTORY-1`** — supplement the four individual VA records when a readable official inventory becomes available. Endpoint: exact dated source rows and explicit differences are recorded. It cannot replace the CSI task; no further retry is needed to execute this plan.

- [ ] **`EQUIPMENT-COUNT-1`** remains independently authorized shared-engine work under approved **E01–E12 / EC01–EC20**. Reuse accepted source/reconciliation/calculation/export behavior, and map only the relevant equipment taxons to it. A counter does not close pumps, boilers, chillers, terminal units or their complete sections.

Duct and air-device acceptance are not gates for these tasks. Only actual required inputs block a dependent action: an unknown pipe length blocks its calculated insulation amount, while its insulation requirement, controls responsibility and service scope can still be prepared. Claude's recorded expired OAuth affects that dispatch only; this plan used the authorized Codex fallback.

## Eight concrete tasks for every section

Each section's task stem below owns **`-TAX`, `-EVID`, `-RULE`, `-CALC`, `-RECON`, `-EDIT`, `-OUT`, `-ACCEPT`**. All 448 full IDs, actual artifact paths, source boundaries and dependency IDs are directly inspectable in the JSON. A dependency means the relevant taxon's output; it does not force all taxons or sections into a single serial lane. Each task uses the following contract once, bound to its exact section title and specific subject.

| Stage | Concrete delivered artifact and pass condition | Actual prerequisite |
| --- | --- | --- |
| **TAX** | `taxonomy.json`: every declared source clause and required type/variant has a reviewed source-bound record; 0 unexplained omissions, duplicate/orphan identities or unresolved required coverage gaps. A single-source audit is a supporting child. | Exact applicable source identity/body and frozen complete declared scope; source audit can begin with the retained VA body. |
| **EVID** | `evidence-producer-contract.json`: drawing, schedule, specification and manual-review records preserve exact source/revision/page/region/role; original-source and ambiguous/missing cases meet frozen expectations. | Relevant TAX nodes and authorized original fixtures; scale only for measured routes. |
| **RULE** | `rule-packet.md` plus exact-hash examples/owner receipt: every taxon has an approved count/measure basis or non-quantity obligation output; no invented default or reopened unchanged approval. | Relevant TAX scope/options and genuinely unresolved owner rule decisions. EVID completion is not a preparation gate. |
| **CALC** | `quantity-integration.json`: connected app results match independent expected physical/package/installation/obligation outputs; deterministic replay and known-plus-UNKNOWN behavior pass. | Relevant EVID plus approved RULE; measured geometry/scale or package evidence only where actually needed. |
| **RECON** | `responsibility-crosslinks.json`: requirements, physical identity, package membership, supply/install/interfaces and service scope match named cases without duplicate purchases or phantom references. | Relevant TAX clauses; exact physical evidence only for links asserting a specific instance. Final quantities are not a blanket prerequisite. |
| **EDIT** | `revision-reopen-evidence.json`: corrections and changed source/rule bytes invalidate affected results only; exact expected histories, recalculation and reopened state pass. | Affected evidence/requirements; CALC or RECON only for changed quantities or relationships. |
| **OUT** | `export-verification.json` with Excel/evidence-PDF specimens: every expected row/total matches core and required links open the correct source; unknown/stale states remain visible. | Current evidence/requirements; CALC only for exported quantities. Partial known and unresolved scope can export. |
| **ACCEPT** | `acceptance.json`: all mandatory taxons/children/stages close; frozen estimator/representative and current Mac workflow criteria pass against exact bytes; independent reviewer records Mac engineering acceptance. | All mandatory taxon/stage results, frozen representative oracles and the current Mac workflow. No Windows prerequisite. |

Other than the fixed audit below, exact source/fixture sets and numerical recognition or representative thresholds have not been frozen for these section tasks. Their gate state is **`acceptance_definition_pending`**. Prepare and freeze those definitions before the relevant run; do not invent an accuracy target or select tolerance after seeing a failure.

## The 56 section work packages

Every row remains **open**. The full body must establish actual types/variants and required child scope; the title-derived work prompts in JSON are preparation subjects, not verified mechanical requirements. All task paths are planned outputs rather than claims that implementation artifacts exist.

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

For example, the hydronic package contains `D23-VA-232113-TAX` through `D23-VA-232113-ACCEPT`; outputs live under `docs/engineering/division23/va-232113/`. UFGS 23 21 13.00 20 has a separate stem and source scope. These are not silently merged.

## Taxon and section endpoints

Every reviewed taxon receives a stable source-qualified ID, an exact source span, kind/type/subtype, applicable service/material/size/joint and other attributes, supported relationships, exclusions/options, output basis and responsibility. Keep physical items, assembly parts, temporary work, consumables, tools/spares, services, references and deliverables distinct. Do not create a speculative service × material × size × joint cross-product.

Its task IDs follow `<taxon-id>-TAX` through `<taxon-id>-ACCEPT`. The last is the explicit completion endpoint: all relevant frozen stage checks pass, representative expected results cover the required variants/exceptions, and the current Mac workflow evidence is accepted. Supporting engineering tasks may close earlier under their own finite contracts. Nonphysical taxons use an obligation/document/responsibility projection and applicable tests; they are not omitted because they lack a material quantity.

An **UNKNOWN obligation** must carry its source clause, missing input/document, affected taxon/results, next action and resolution history. Examples are an unchosen guide option, missing host, unshown field-work extent, unavailable referenced section or unknown supply responsibility. UNKNOWN is not zero. A mentioned section is not a supplied document. A repeated plan/schedule/spec appearance is not another physical object. Package purchase, internal physical part and installation work retain separate relationships and outputs.

A section reaches Mac engineering acceptance when its complete declared taxonomy and every mandatory child/taxon/stage pass the fixed contract, including connected edits/reopen/outputs, frozen representative expectations, and current Mac workflow checks. No required mechanical scope remains unresolved. A project's sourced not-applicable decision affects only that project revision; it cannot close product support. Full product completion additionally requires the authoritative CSI register, the separately scheduled Windows delivery milestone, and the owner's final product/release acceptance. That later milestone does not hold the Mac section endpoint open.

## Worked checklist: VA 23 21 13, dated 03-01-23

This checklist follows **all 37 headings in Parts 1–3** of the retained guide. The source body is `.heleos/div23-scope-review-2026-09-16/source-1.txt`; the verified heading manifest is `.heleos/div23-section-work-plan-2026-09-16/hydronic-heading-verification.json`. Exact character/line spans and **222 named taxon seeds** are in the register. Indexed text locations are not original PDF page numbers.

Each checkbox below is a concrete child of `D23-VA-232113-TAX-AUDIT-1`, feeding the parent section's eight tasks. Its artifact is the matching paragraph of `source-audit.json`. It depends on that exact retained source; reading external references or obtaining new estimating decisions blocks only the dependent behavior. Split combined seeds where independent source variants need separate evidence/acceptance. The checklist prepares work and claims no closed taxonomy or implemented section.

### Part 1: General, references and deliverables

- [ ] **1.1 — Service and scope dimensions** · `D23-VA-232113-TAX-P1-1`

  **Source:** DESCRIPTION; retained text lines 39–54. **Taxon work:** chilled-water service; condenser-water service; heating-hot-water service; drain-piping scope; domestic-makeup extension; glycol-water service.

  **Relationships and limits:** Bind each service to pipe/connection instances; preserve common-work references as references.

  **Endpoint:** All six described service/scope nodes link to source and pipe/equipment endpoints; no service implies a quantity or a default material. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **1.2 — Related-section references** · `D23-VA-232113-TAX-P1-2`

  **Source:** RELATED WORK; retained text lines 55–100. **Taxon work:** general requirements; shop drawings product data and samples; sustainable construction; general commissioning; walk-in coolers and freezers; seismic restraints; boiler-plant common work; HVAC common work; noise and vibration control; HVAC and boiler insulation; HVAC commissioning; DDC controls; hydronic pumps; steam and condensate piping; HVAC water treatment; convection heating and cooling units; earthwork excavation/backfill.

  **Relationships and limits:** Preserve every stated section number/title and //option//; distinguish supplied documents from mentioned documents.

  **Endpoint:** Every A–Q reference has a typed external-reference edge and available/unavailable status; referenced work is never counted or marked supported merely by citation. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **1.3 — Standards and edition bindings** · `D23-VA-232113-TAX-P1-3`

  **Source:** APPLICABLE PUBLICATIONS; retained text lines 101–314. **Taxon work:** ASME publications and BPVC; ASTM publications; AWS welding qualification; EJMA expansion joint standard; MSS valve/fitting standards; NFPA 70; TEMA standards.

  **Relationships and limits:** Retain each individual publication designator and printed/optional edition; do not silently update to a newer edition.

  **Endpoint:** Each listed publication is represented and linked only to supported clauses; project-selected editions and source precedence are explicit or UNKNOWN. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **1.4 — Submittals and deliverables** · `D23-VA-232113-TAX-P1-4`

  **Source:** SUBMITTALS; retained text lines 315–470. **Taxon work:** product-data package; U-1 pressure-vessel report; welder qualification certificate; coordination drawings; as-built piping drawings; O&M manuals and wiring diagrams; system readiness checklist; training plans/instructor qualifications.

  **Relationships and limits:** Inventory C.1–C.17 product-data subjects separately; connect vessel reports, drawing formats/copies and optional commissioning deliverables without assuming project adoption.

  **Endpoint:** All A–J obligations and C.1–C.17 subjects have exact clause edges, target objects, responsible party/UNKNOWN and deliverable status; copy counts and optional content remain source parameters. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **1.5 — Quality assurance constraints** · `D23-VA-232113-TAX-P1-5`

  **Source:** QUALITY ASSURANCE; retained text lines 471–510. **Taxon work:** welding certification/age requirement; single-manufacturer requirement; casting traceability/date marking; bio-based material condition; sustainability reference.

  **Relationships and limits:** Link constraints to applicable joint/material/product families; preserve source conditions and referenced common work.

  **Endpoint:** All A–E constraints have applicability predicates and test cases for applicable, inapplicable and unresolved products; QA records never become material quantities. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **1.6 — As-built cross-reference** · `D23-VA-232113-TAX-P1-6`

  **Source:** AS-BUILT DOCUMENTATION; retained text lines 511–516. **Taxon work:** HVAC-common-work as-built obligation.

  **Relationships and limits:** Link this reference to 1.4 drawing obligations and the external section rather than duplicating an as-built purchase.

  **Endpoint:** A single obligation graph preserves both source clauses, the external reference status and unresolved document requirements. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **1.7 — Pressed-fitting spare tools and source tension** · `D23-VA-232113-TAX-P1-7`

  **Source:** SPARE PARTS; retained text lines 517–524. **Taxon work:** pressed/sealed fitting tool set by used pipe size.

  **Relationships and limits:** Retain the tool-per-used-size condition and flag tension with the steel-fitting spec-writer note in 2.3.

  **Endpoint:** A review issue links 1.7 and 2.3 with context; tool quantities stay UNKNOWN until applicable joining methods and project decision are known. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

### Part 2: Products, attributes and assemblies

- [ ] **2.1 — Supports, sleeves and plates** · `D23-VA-232113-TAX-P2-1`

  **Source:** PIPE AND EQUIPMENT SUPPORTS, PIPE SLEEVES, AND WALL AND CEILING PLATES; retained text lines 525–530. **Taxon work:** pipe support; equipment support; pipe sleeve; wall plate; ceiling plate.

  **Relationships and limits:** All are delegated to 23 05 11; preserve host/type/size and supply/install ownership, with no invented support spacing.

  **Endpoint:** Every named host accessory has a reference edge, exact host or unresolved host, and duplicate-prevention link to common-work records. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.2 — Pipe and tubing families** · `D23-VA-232113-TAX-P2-2`

  **Source:** PIPE AND TUBING; retained text lines 531–568. **Taxon work:** steel pipe; copper tube Type K; copper tube Type L; copper tube Type M; PVC drain pipe; CPVC chemical-feed pipe; vent-piping service; condenser-treatment chemical-feed service; pipe-insulation shield/support reference.

  **Relationships and limits:** Enumerate A–E service/material/schedule/hard-or-soft/size/location conditions; note exterior-PVC restriction and optional under-slab runouts.

  **Endpoint:** A clause matrix maps permitted source combinations and exclusions without a Cartesian cross-product; missing service/size/route/elevation/scale stays UNKNOWN and cannot yield a final length. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.3 — Steel fittings, flanges and branches** · `D23-VA-232113-TAX-P2-3`

  **Source:** FITTINGS FOR STEEL PIPE; retained text lines 569–634. **Taxon work:** butt-weld fitting; socket-weld fitting; threaded forged-steel fitting; malleable-iron screwed fitting; cast-iron screwed fitting alternative; union; water hose adapter; long-radius elbow condition; weld-neck flange; slip-on flange; convoluted flange option; gasket; flange bolting; weldolet/branchlet/threadolet; drain/vent/gauge half-coupling.

  **Relationships and limits:** Preserve small/large size thresholds, walls, pressure/service conditions, reducer/nipple exclusions and contextual compression/press-fitting note.

  **Endpoint:** Each A–C variant and exclusion has a source span and selected/alternative/UNKNOWN state; fittings, joints and package internals cannot automatically become separate purchases. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.4 — Copper joining and fitting variants** · `D23-VA-232113-TAX-P2-4`

  **Source:** FITTINGS FOR COPPER TUBING; retained text lines 635–668. **Taxon work:** soldered copper joint; mechanically formed tee; bronze flange/flanged fitting; cast-copper fitting; wrought-copper solder fitting.

  **Relationships and limits:** Retain joint materials, connection method and source standard designators as attributes rather than universal defaults.

  **Endpoint:** Each A–C variant has evidence/rule cases for pipe compatibility and source options; formed tees and purchased fittings are distinguished by approved rules. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.5 — Plastic fitting variants** · `D23-VA-232113-TAX-P2-5`

  **Source:** FITTINGS FOR PLASTIC PIPING; retained text lines 669–678. **Taxon work:** socket/solvent-weld fitting; PVC drainage-pattern fitting; CPVC chemical-feed fitting.

  **Relationships and limits:** Bind schedules, solvent joint type and service to the corresponding pipe family.

  **Endpoint:** All A–C source branches map to pipe-service relationships; unknown schedule/material/service prevents a supported component total. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.6 — Dielectric interface variants** · `D23-VA-232113-TAX-P2-6`

  **Source:** DIELECTRIC FITTINGS; retained text lines 679–698. **Taxon work:** threaded dielectric union; flanged dielectric union; dielectric gasket; bolt sleeve; brass ball-valve alternative; dielectric nipple option.

  **Relationships and limits:** Preserve dissimilar-metal interface, size and temperature constraints; alternatives do not coexist unless actual scope says so.

  **Endpoint:** Copper/ferrous interface fixtures prove selected alternative and package boundaries; one interface cannot create duplicate union/valve/nipple purchases. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.7 — Thread and sealant requirements** · `D23-VA-232113-TAX-P2-7`

  **Source:** SCREWED JOINTS; retained text lines 699–706. **Taxon work:** pipe-thread standard; thread lubricant/sealant.

  **Relationships and limits:** Joint/consumable requirements have no invented per-joint allowance or unit conversion.

  **Endpoint:** Threaded connections link to specified standard/service suitability and supported evidence; missing consumable quantity remains an obligation/UNKNOWN rather than an allowance. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.8 — Valve types and operating assemblies** · `D23-VA-232113-TAX-P2-8`

  **Source:** VALVES; retained text lines 707–944. **Taxon work:** ball shut-off valve; butterfly shut-off valve; gate shut-off valve; globe valve; angle valve; swing check valve; double-disc silent/non-slam check variant; guided-disc silent/non-slam check variant; manual balancing ball/globe alternatives; automatic constant-flow balancing valve designs; combination balancing assembly; manual radiator/convector valve; lever actuator; worm-gear actuator; chain operator; balancing readout kit.

  **Relationships and limits:** Inventory A–I, body/trim/seat, size/pressure/temperature/joint, actuator/height conditions, insulation extensions and isolation-versus-balancing distinction.

  **Endpoint:** Every stated valve/actuator alternative is classified; fixture matrix includes separate isolation and balancing, combination assemblies, optional readout kit and buried-use restriction without duplicate purchases. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.9 — Flow devices, identification and meters** · `D23-VA-232113-TAX-P2-9`

  **Source:** WATER FLOW MEASURING DEVICES; retained text lines 945–1044. **Taxon work:** Venturi flow device; wafer circuit sensor; self-averaging annular sensor; insertion-turbine control sensor reference; flow-device identification tag; portable flow-indicating meter; permanent flow-indicating meter; meter manifold/valves/hoses/case.

  **Relationships and limits:** Record design-flow/range/accuracy attributes, BAS coordination, optional displayed units and one-portable-meter-per-range source condition.

  **Endpoint:** B–H variants and A performance predicate have source-linked records; hardware, BAS points, portable kits and included accessories remain distinct; no meters are inferred from unsupported flow data. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.10 — Strainer alternatives and pump reference** · `D23-VA-232113-TAX-P2-10`

  **Source:** STRAINERS; retained text lines 1045–1066. **Taxon work:** basket strainer option; Y-strainer option; strainer screen; suction-diffuser pump-section reference.

  **Relationships and limits:** Preserve screen material/perforation/free-area attributes and writer-note context; resolve the source wording before admitting numeric rules.

  **Endpoint:** Both bracketed alternatives and suction-diffuser reference appear in coverage; unresolved screen wording/selection stays an issue, with no duplicate pump accessory count. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.11 — Flexible connector variants** · `D23-VA-232113-TAX-P2-11`

  **Source:** FLEXIBLE CONNECTORS FOR WATER SERVICE; retained text lines 1067–1122. **Taxon work:** single-arch flanged spool connector; multiple-arch flanged spool connector; bronze braided-hose variant; stainless braided-hose variants; retaining rings/control units.

  **Relationships and limits:** Bind connector materials, size ranges, pressure/temperature and included components; retain any source range gaps for review.

  **Endpoint:** Flanged and braided variants have cases at stated size boundaries and an unspecified-size case; no interpolation into an unlisted range or separate purchase of integrated parts. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.12 — Expansion assemblies, guides and supports** · `D23-VA-232113-TAX-P2-12`

  **Source:** EXPANSION JOINTS; retained text lines 1123–1244. **Taxon work:** internally pressurized bellows; externally pressurized bellows; expansion compensator; slip/telescoping joint contractor option; expansion-joint nameplate; pipe guide; heat-exchanger saddle/frame/hanger support; bellows sleeves/rings/tie-rods/covers.

  **Relationships and limits:** Preserve EJMA/ASME references, movement/end connection/pressure/temperature, guide constraints and the source's heat-exchanger support clause under this heading.

  **Endpoint:** A–I all map; contractor options and internally supplied parts are distinguished from separate supports; guide/anchor quantities require actual layout/manufacturer/project basis. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.13 — Hydronic equipment, tank variants and packages** · `D23-VA-232113-TAX-P2-13`

  **Source:** HYDRONIC SYSTEM COMPONENTS; retained text lines 1245–1500. **Taxon work:** shell-and-tube water/water heat exchanger; plate-and-frame heat exchanger; optional pre-piped/pre-wired heat-transfer package; air purger; tangential air separator; diaphragm pre-pressurized expansion tank; closed expansion tank horizontal; closed expansion tank vertical; pressure-reducing valve; pressure-relief valve; automatic air vent; buffer tank; replacement gasket sets/wrenches; tank gauge glass/guard/air-control/drain/charging accessories; heat-exchanger guards/cradles.

  **Relationships and limits:** Map A–K and source-defined internal parts/certificates, performance schedule, optional packages and optional connections; preserve reference to steam/condensate requirements.

  **Endpoint:** All equipment and accessory variants have source/assembly edges and fixtures; physical instances, package purchases, installation and spare supplies remain separate under applicable approved equipment rules. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.14 — Water filters and chemical-feeder reference** · `D23-VA-232113-TAX-P2-14`

  **Source:** WATER FILTERS AND POT CHEMICAL FEEDERS; retained text lines 1501–1506. **Taxon work:** water filter reference; pot chemical feeder reference.

  **Relationships and limits:** Resolve 23 25 00 closed-loop chemical-treatment scope and link 3.1.J/3.7 installation responsibility.

  **Endpoint:** Referenced components remain visible and UNKNOWN until supplied scope is checked; crosslinks prohibit duplicate feeder/filter purchases across sections. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.15 — Pressure instrumentation** · `D23-VA-232113-TAX-P2-15`

  **Source:** GAUGES, PRESSURE AND COMPOUND; retained text lines 1507–1534. **Taxon work:** pressure gauge; vacuum gauge; compound gauge; gauge union cock; pressure snubber; gauge set-hand feature.

  **Relationships and limits:** Record service/range/accuracy/connection/material and source condenser-suction range without inventing gauge locations.

  **Endpoint:** All A–C variants and associated parts link to locations or explicit UNKNOWN; integrated features are attributes, not extra equipment instances. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.16 — Pressure and temperature test provisions** · `D23-VA-232113-TAX-P2-16`

  **Source:** PRESSURE/TEMPERATURE TEST PROVISIONS; retained text lines 1535–1560. **Taxon work:** permanent test plug; pressure-adapter probe; portable compound gauge; pocket thermometer.

  **Relationships and limits:** Preserve installed-where-shown/alternative-to-test-connection logic and source test-items-to-COR obligation.

  **Endpoint:** The permanent device and three portable test-item obligations are separate; project-adopted quantities are explicit or UNKNOWN, with no duplicate gauge substitution. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.17 — Thermometers and wells** · `D23-VA-232113-TAX-P2-17`

  **Source:** THERMOMETERS; retained text lines 1561–1592. **Taxon work:** liquid-column thermometer variants; straight/fixed/adjustable-angle stem; separable socket/well.

  **Relationships and limits:** Retain medium/service, case, scale ranges, stem/extension and insulation-clearance attributes.

  **Endpoint:** A–E are represented; thermometer and well roles have evidence/assembly cases, with source ranges treated as guide parameters pending applicability. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.18 — Firestop material reference** · `D23-VA-232113-TAX-P2-18`

  **Source:** FIRESTOPPING MATERIAL; retained text lines 1593–1596. **Taxon work:** HVAC-common-work firestopping reference.

  **Relationships and limits:** Resolve common-work material and connect to 3.1.L insulated/uninsulated host responsibility.

  **Endpoint:** Each penetration obligation has host/reference/supply-install status; missing firestop system or penetration extent is explicit UNKNOWN. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **2.19 — Optional electric heat-tracing system** · `D23-VA-232113-TAX-P2-19`

  **Source:** //ELECTRICAL HEAT TRACING SYSTEMS; retained text lines 1597–1712. **Taxon work:** self-regulating heat-trace cable; end seal; power connection fitting; mounting bracket/clamp; attachment tape; pipe thermostat/sensor; heat-trace identification sign; electrical power coordination.

  **Relationships and limits:** Keep whole-heading optionality, weather/freeze/service selections, floor/elevation/frost-line extent and manufacturer conditions; do not adopt wattage/spacing as quantity allowances.

  **Endpoint:** A–E and all options map; route/extent, valve wrapping, circuit ends, accessories and power responsibility have fixtures; unset choices and electrical connection data remain UNKNOWN. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

### Part 3: Execution and service obligations

- [ ] **3.1 — Installation conditions and undisplayed work** · `D23-VA-232113-TAX-P3-1`

  **Source:** INSTALLATION; retained text lines 1713–1836. **Taxon work:** field-coordinated pipe runs/offsets/fittings obligation; material protection obligation; piping/equipment support and clearance; drain slope/eccentric reducer condition; valve-access/adjacent-union requirement; connection flexibility/swing-joint condition; branch orientation condition; high-point air-vent requirement; low-point drain-valve requirement; automatic-vent drain connection; install-furnished-by-others components; thermometer-well pipe-size condition; penetration firestop responsibility; copper/steel dielectric connection.

  **Relationships and limits:** Link each A–M instruction to hosts and other paragraph taxons; retain unshown-field-work obligation without fabricating route geometry or default allowances.

  **Endpoint:** Every A–M clause has a disposition and scope link; field-measured/unshown work is unresolved until supported, and furnished-by-others items retain installation without duplicate supply. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **3.2 — Joint execution obligations** · `D23-VA-232113-TAX-P3-2`

  **Source:** PIPE JOINTS; retained text lines 1837–1862. **Taxon work:** welding execution/qualification; threaded-joint execution; cast-iron flange mating condition; solvent-weld installation instruction.

  **Relationships and limits:** Relate joint work to the product/joint taxons in 2.3–2.7 and stated standards, including finishes/protection.

  **Endpoint:** All A–D obligations link to applicable joints and source instructions; missing joint evidence remains UNKNOWN rather than one joint per arbitrary pipe segment. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **3.3 — Expansion joint installation and design services** · `D23-VA-232113-TAX-P3-3`

  **Source:** EXPANSION JOINTS (BELLOWS AND SLIP TYPE); retained text lines 1863–1896. **Taxon work:** anchor/guide design and engineering verification; cold-setting requirement; restraint removal/manufacturer verification; inspection/replacement access requirement.

  **Relationships and limits:** Link anchors/guides to 2.12 assemblies; preserve manufacturer/layout basis and required verification services.

  **Endpoint:** All A–D obligations link to exact assemblies or unresolved hosts; anchors/guides/services are not duplicated or assigned default spacing. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **3.4 — Optional aboveground seismic scope** · `D23-VA-232113-TAX-P3-4`

  **Source:** //SEISMIC BRACING ABOVEGROUND PIPING; retained text lines 1897–1902. **Taxon work:** aboveground-piping seismic-bracing reference.

  **Relationships and limits:** Retain //option// and 13 05 41 reference; coordinate with supports and external structural scope.

  **Endpoint:** An explicit applicable/not-applicable/unresolved project decision and reference availability are retained; option absence cannot complete product seismic support. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **3.5 — Leak test alternatives and exclusions** · `D23-VA-232113-TAX-P3-5`

  **Source:** LEAK TESTING ABOVEGROUND PIPING; retained text lines 1903–1926. **Taxon work:** leak inspection/correction; operating pressure/temperature test; hydrostatic test; factory-tested equipment exemption/isolation.

  **Relationships and limits:** Preserve COR-approved alternative/combination, design basis and factory-tested exclusion; avoid unsourced test pressure or test counts.

  **Endpoint:** Each system/test-boundary fixture records chosen procedure, supported design parameters and equipment exclusions or UNKNOWN; guide test factors are not universal calculation rules. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **3.6 — Flushing, cleaning and temporary works** · `D23-VA-232113-TAX-P3-6`

  **Source:** FLUSHING AND CLEANING PIPING SYSTEMS; retained text lines 1927–2004. **Taxon work:** initial flushing; temporary bypass piping/hoses; temporary strainers; temporary/booster pump requirement; component isolation/protection; chemical cleaning/circulation; strainer cleaning/blowdown; final flushing; water/drainage handling.

  **Relationships and limits:** Retain stage order, conditional methods, source duration/velocity parameters and treatment reference; temporary works are separate from permanent quantities.

  **Endpoint:** A–D stage/host/evidence records preserve all temporary and service scope with known/UNKNOWN extent; durations are requirements, never inferred labor/pricing quantities. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **3.7 — Water-treatment installation and training** · `D23-VA-232113-TAX-P3-7`

  **Source:** WATER TREATMENT; retained text lines 2005–2022. **Taxon work:** water-treatment equipment installation; treatment-system piping; system filling/chemical charge; operating-personnel instruction.

  **Relationships and limits:** Crosslink 23 25 00 products and 2.14/3.1.J; separate chemical supply, pipe amount, installation and service obligations.

  **Endpoint:** All A–D work has ownership and source linkage; known piping/equipment does not imply chemical amount and training is reconciled with 3.11 without duplication. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **3.8 — Optional heat-trace installation** · `D23-VA-232113-TAX-P3-8`

  **Source:** //ELECTRIC HEAT TRACING; retained text lines 2023–2028. **Taxon work:** manufacturer-directed tracing installation; electrical connection coordination.

  **Relationships and limits:** Link to 2.19 optional selection and actual electrical responsibility; no default cable amount.

  **Endpoint:** Both A–B obligations follow the project-selected option, source manufacturer requirements and responsibility or UNKNOWN. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **3.9 — Startup, tests and defect response** · `D23-VA-232113-TAX-P3-9`

  **Source:** STARTUP AND TESTING; retained text lines 2029–2058. **Taxon work:** manufacturer/standard operating tests; simultaneous system test linkage; defect correction/retest; optional CxA observation/notice; pressure-gauge setting.

  **Relationships and limits:** Capture system/component relationships, notice/coordination option and explicit gauge adjustment.

  **Endpoint:** All A–D obligations link to system/component and completion evidence or UNKNOWN; notice parameters/failed tests survive revision and reopen. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **3.10 — Optional commissioning participation** · `D23-VA-232113-TAX-P3-10`

  **Source:** //COMMISSIONING; retained text lines 2059–2068. **Taxon work:** commissioning documentation reference; component-to-system test participation.

  **Relationships and limits:** Preserve whole-heading optionality and 23 08 00 dependency; the source reference alone is not commissioning acceptance.

  **Endpoint:** Each component maps to its larger system/test record or UNKNOWN and project-selected commissioning option; no duplicated commissioning service purchase. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

- [ ] **3.11 — Demonstration and training** · `D23-VA-232113-TAX-P3-11`

  **Source:** DEMONSTRATION AND TRAINING; retained text lines 2069–2082. **Taxon work:** manufacturer representative instruction; training duration option; instructor/plan submission option.

  **Relationships and limits:** Preserve unresolved //4// //   // hour//s// selection; link to 1.4 and 3.7 without automatic four-hour allowance.

  **Endpoint:** All A–B training targets, supplier, selected duration and documentation are explicit or UNKNOWN; example tests prove unselected duration stays unknown. Every subordinate clause receives a disposition; split combined seeds where independent variants need their own evidence or acceptance.

### Fixed acceptance for the first executable audit

Contract **`D23-VA-232113-TAX-AUDIT-1`, version 1** consumes the exact VA source hash above. Its deliverable includes source identity; 37 paragraph records with exact headings/spans and subordinate clause dispositions; unique typed taxons with attributes, relationships and output basis; references with availability; options/exclusions/conflicts; and named missing-scope work.

| Case | Mandatory check | Frozen expected result |
| --- | --- | --- |
| **HA01** | Compare the paragraph inventory to the retained heading manifest. | numbered paragraphs = 37; missing = 0; extra unexplained = 0; changed headings = 0 |
| **HA02** | Independently read each paragraph and subordinate clause against its recorded disposition. | unexplained clause omissions = 0; unexplained taxon omissions = 0 |
| **HA03** | Validate each defined taxon ID and source span and each internal relationship target. | duplicate ids = 0; orphan internal edges = 0; unresolvable source spans = 0; defined taxons without source = 0 |
| **HA04** | Review related sections/standards, guide notes/options and material/service/size/joint predicates. | Every identified item is recorded with its source and applicable/alternative/reference/UNKNOWN disposition; no inferred CSI parent or universal project mandate. |
| **HA05** | Inspect the 1.7 versus 2.3 fitting/tool issue and the 3.11 training-duration option. | Both fitting/tool clauses remain linked as an unresolved contextual issue; unselected training duration remains UNKNOWN, not a default four hours. |
| **HA06** | Inspect field-coordinated/unshown work in 3.1 and assembly/responsibility links in 2.13, 2.14 and 3.1.J. | Written obligations remain present; absent route or project scope produces UNKNOWN, and the audit creates no unsupported physical/procurement quantities. |
| **HA07** | Validate JSON syntax and the required output-field/schema contract. | syntax errors = 0; schema errors = 0 |
| **HA08** | Independent Codex reviewer checks the source-comparison record and exact artifact hash. | Every named HA01–HA07 check passes its recorded expected outcome; outcome/evidence/limits/next task recorded. Unrelated unfinished work does not block this audit. |

Run `python3 -m json.tool docs/engineering/division23/va-232113/source-audit.json` and require exit 0. The independent coordinator then performs the bounded source comparison: reconcile each exact heading, inspect every subordinate clause disposition and every defined taxon/source/relationship, and record HA01–HA08 results with artifact/source hashes. Required JSON schema/field checks must report zero errors. Identity/count comparisons are exact, with allowed omissions/duplicate/orphan/unresolvable identities **0**. This source audit has no invented recognition threshold or quantity tolerance.

Preserve the contextual **1.7 versus 2.3** pressed-fitting tool/steel-fitting note issue. Do not turn it into a universal press-fitting ban or automatic tool purchase. Preserve the unselected **3.11** training duration as UNKNOWN; four hours is a guide option, not a settled estimate. Likewise, a statement requiring field-coordinated offsets does not supply drawable route geometry or authorize an allowance.

After HA01–HA08 pass and independent review accepts the exact artifact, close the audit and move to parent TAX's complete declared scope/evidence reconciliation. Do not rerun the accepted audit on unchanged bytes. A new source/variant or demonstrated defect gets a named successor/defect task and explicit changed contract scope.

## Execution and handoff

Before each implementation assignment, the coordinator records exact checkout/base, source and rule hashes, sole-writer code/test paths, one deliverable, the fixed checks and output location. Shared paths remain singly owned. Workers report terminal results; the coordinator independently accepts/integrates, updates root `CURRENT_STATUS.md` and advances to the next unfinished task. This planning worker owns only the two plan files and makes no commits.

Prepared artifacts: this readable plan and the JSON register. Current evidence establishes source identities and an actionable queue; it does not establish new quantity rules, complete CSI coverage, closed hydronic taxonomy or product acceptance. The next concrete deliverable is the fixed VA hydronic source audit, while approved equipment engine work remains independent.
