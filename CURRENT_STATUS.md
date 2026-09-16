# Current Status

Updated 2026-09-16. Work in `/Users/bekim/Heleos-spark`, branch `main`.
The **Resume here** section is the single current work queue. Roadmap targets and
completed evidence below are context, not assignments. Historical “Next” statements in the
[archived status](docs/operations/status-archive/CURRENT_STATUS-through-2026-09-16-7397696.md)
are evidence, not assignments. Exact archived bytes and original locations are
recorded in the [archive manifest](docs/operations/status-archive/2026-09-16-archive-manifest.json).

The full local-first **CSI Division 23, including all sections and subsections,**
Mac/Windows goal is unchanged. Ducts and air devices are partial delivery slices;
they do not define mechanical takeoff completion. Product coverage must be tracked
against the complete verified section hierarchy and the applicable project
specifications. A section absent from one pilot is not automatically a completed
product capability. Settled decisions:
imperial output; initial priority duct lengths → air-device counts → equipment
counts; approved D01–D10, A01–A12/AC01–AC17 and E01–E12/EC01–EC20. Do not reopen those choices. Accepted duct work is
preserved. Further duct refinements, geometry/evaluation expansions or repeated
test cycles are deferred unless a recorded concrete defect blocks the active
usable deliverable; repair only that dependency.

## Resume here

**Work and completion are organized by actual Division 23 section/subsection and
its entire taxonomy.** The owner clarified this explicitly on 2026-09-16. Use the
[section task register](docs/plans/division-23-section-register.json) and
[work breakdown with completion endpoints](docs/plans/division-23-section-work-breakdown.md).
[Acceptance is a frozen pass/fail contract](docs/operations/acceptance.md): named
inputs, independently expected results, numerical tolerances, checks and review.
Once every mandatory criterion passes, close the task; new requirements get new
tasks or explicit contract versions. Undefined criteria get named definition work.
The checklist applies to that task only. Correctly recording an expected unknown
or source conflict can pass its check; unrelated unfinished tasks do not block it.

**Current build focus: Division 23 capabilities on Mac.** Accept each mechanical
task against its defined Mac checks and advance. Keep shared code portable;
`WINDOWS-DELIVERY-1` is a separate later delivery check after the connected workflow
is ready, not a section/taxon acceptance dependency. Its deferred state is recorded
under roadmap 0.7; it is not a reason to revisit completed work.

A generic category, parser, counter or export does not complete a section. Each
required type, variant, attribute, relationship, exclusion and service obligation
must have evidence and an explicit implementation/acceptance disposition.
Project not-applicable decisions do not close product scope.

**Primary next task: `D23-VA-232113-TAX` — complete the hydronic piping taxonomy
artifact for the section's full scope.** Start from the verified VA 23 21 13 source
and all 37 numbered paragraphs, including general requirements, products and
execution. Build the typed entity/variant/attribute/relationship inventory, connect
referenced work to its own section tasks, preserve optional clauses and unresolved
conflicts, and provide coverage fixtures for every identified taxon. The next executable
child is `D23-VA-232113-TAX-AUDIT-1`: deliver the source audit against its frozen
HA01–HA08 criteria. Passing that audit closes the child only; full TAX coverage
remains a separate endpoint. This is a
section taxonomy task, not another duct or air-device refinement. The worked
checklist and exact endpoint are in the section work breakdown. Source receipts:
`.heleos/div23-section-work-plan-2026-09-16/`.

Each registered section has separate **TAX → evidence/rules → calculation and
reconciliation → corrections/outputs → acceptance** task endpoints. Those arrows
represent actual input dependencies within that work; they do not require every
section to run serially. All section and subsection coverage remains open until
its complete required task set and child scope are accepted. Shared implementations
must link to section tasks that reuse them and leave the remaining work visible.

| Work available alongside the primary section task | Concrete next endpoint |
| --- | --- |
| `D23-CSI-CATALOGUE-1` | Obtain and verify the complete authoritative edition-specific CSI Division 23 section/subsection hierarchy; reconcile government/project aliases and omissions. Public guide inventories are a working seed, not a complete CSI list. |
| `EQUIPMENT-COUNT-1`, shared by the relevant section CALC tasks | Implement and connect physical-equipment counts under already approved E01–E12/EC01–EC20, preserving instance/package/installation separation, unresolved quantities, correction/reopen/history and exports. Do not repeat approval or packet preparation. |
| Registered controls, insulation, common-work, fuel, piping variants, plant/equipment and service sections | Execute their named TAX/EVID/RULE tasks from their own verified sources and inputs. Missing host quantities block only dependent calculations. Use the register's section-specific subjects and endpoints, not a single generic “other categories” task. |
| Section OUT tasks / `EVIDENCE-PDF-1` shared support | Connect consolidated source-linked evidence PDF for supported results, then verify each section's required outputs as its data becomes available. |

**Just delivered: `DIV23-SCOPE-REVIEW-1`.** The Documents stage now groups
explicit supplied mechanical section headings and written requirements, preserves
decimal suffixes, shows unknown/unclassified/heading-only scope and stale reviews,
links original source locations, and reopens/exports JSON and CSV. It is a shared
source-review capability, **not completion of any section's full taxonomy or
takeoff**. Reuse this implementation; do not repeatedly polish it while the section
work register remains unfinished.

Duct and air-device acceptance do not gate any unrelated section. The initial
class order is priority, not an all-section dependency chain. Reuse accepted
coordinates/scale, reconciliation, calculation, correction and Excel capabilities.
The [equipment approval](tests/fixtures/equipment-takeoff/2026-09-16-owner-decision.json)
remains authoritative; the next rule decision for another class blocks only that
class's dependent calculation.

**Parked conditions:** air-device recognition needs a materially different bounded
experiment after eight terminal runs; Claude needs a refreshed login after the
recorded expired-token failure. Record changed inputs before retrying. Provider unavailability
does not block authorized Codex implementation. No provider process is running.

Before handoff, record the completed child task, evidence, exact bytes and next
unfinished child task; update this section in the same completion commit. Do not
return to historical “next” statements. The [replacement goal](docs/operations/build-goal.md)
now includes section/taxonomy closure; its text is prepared in the repository and
has not changed or resumed the saved app goal. No build-continuation hook was
identified or modified.

## Roadmap coverage snapshot

| Roadmap section | Local implementation state | Remaining acceptance/work | Target product outcome |
| --- | --- | --- | --- |
| 0.1 Trust foundation | Rust core, immutable vault, intake, SQLite, recovery/backup and supply-chain implementation integrated; local macOS evidence retained. | Task 10 release-candidate/CI, GitHub App decisions and release dossier remain open; platform delivery is tracked under 0.7. These are separate from current mechanical implementation. | Reuse the core for local takeoff; preserve release gates. |
| Engineering coordination | Guarded workers and provider controls implemented; bounded Claude workflow in use. | Remaining platform qualification is tracked under 0.7. | Support the current product task; no new orchestration redesign. |
| 0.2 Knowledge, taxonomy, source/evaluation registry | Versioned sources, requirement review and source-linked section projection connected locally. Section/subsection task register now defines taxonomy and acceptance endpoints. | Full authoritative hierarchy reconciliation, full taxonomy per section, dataset rights/labels and representative truth remain open. | Complete the hydronic section taxonomy; advance other registered section tasks independently. |
| 0.3 Drawing coordinates and scale | Revision-bound geometry, scale review and invalidation connected locally. | Native parity and representative drawings remain to verify. | Reuse for source identity; each counts have no scale dependency. |
| 0.4 Schedule reconciliation | Source-linked schedule extraction and plan correspondence, decisions/history and exceptions connected locally. | Broader schedule fields/types and representative acceptance remain open. | Feed air-device attributes/declarations and then equipment counts. |
| 0.5 Full CSI Division 23 mechanical takeoff | Partial implementation: duct capability preserved; air-device calculation, source producer, review/correction UI and exports verified locally; equipment draft tag review exists. | Complete section/subsection coverage remains open across common work, O&M, schedules, insulation, commissioning, controls, fuel systems, piping/pumps, air distribution/cleaning, central heating/cooling and central/decentralized HVAC equipment. Cross-cutting demolition, relocation and revision scope remains open. **Air-device recognition remains unqualified:** the installed model missed all three physical symbols in AC01. | Execute full section/subsection task sets and acceptance endpoints. Shared equipment rules/count infrastructure and section review support them; no section is closed by a generic helper. |
| 0.6 Correction, recalculation and estimator outputs | Duct and air-device correction/recalculation, append-only history, CSV/JSON and source-linked Excel with live formulas connected locally. Workbook projection, formula recalculation, stale/unknown states and portable packaging verified. | Consolidated evidence PDF, desktop spreadsheet and representative estimator verification remain unfinished. | Consolidated evidence PDF is shared support for registered section OUT tasks, reusing current source identities and unknown/review state. |
| 0.7 Mac/Windows product | Current implementation and validation target: Mac. Local drawing/takeoff preview and relocatable package; Windows launch/package implementation prepared. | `WINDOWS-DELIVERY-1` is deferred until the connected workflow is ready: native execution, containment/NTFS, desktop parity and install/update/recovery evidence. No Windows acceptance claimed. | Complete current mechanical capabilities on Mac, then verify Windows delivery as a separate milestone; it does not gate section/taxon engineering acceptance. |
| 0.8 iPhone and bounded automation | Supporting automation contracts exist; no completed companion claim. | Companion/synchronization/approval scope and native device acceptance remain future work. | Preserve original roadmap scope; do not make companion work a new Mac/Windows delivery prerequisite. |
| 1.0 Representative production pilot | No production acceptance claimed. | Freeze representative projects/truth/thresholds, verify full requested Division 23 scope and native platforms, then record human release acceptance. | Usable, traceable takeoffs demonstrated on representative Mac and Windows projects. |

## Acceptance boundaries

The [full Division 23 audit](docs/research/div23-scope-and-proposed-lineup-2026-09-16.md)
records the owner's complete section/subsection scope. Its later package order is
proposed; it does not approve new quantity rules. Existing class order and
independent output work are authorized. E01–E12 and EC01–EC20 now supply the
approved physical-equipment basis; that class decision is no longer outstanding.

AIR-DEVICE-COUNT-1 remains incomplete as a recognition capability: the connected
software passed local checks, but the final original-drawing run found zero
physical assemblies where AC01 contains three. All eight runs are terminal and
retained. A synthetic-reader UI demonstration is software evidence, not model
accuracy. Preserve this gap while ready independent capabilities advance.

WORKBOOK-EXPORT-1 is locally implemented and independently accepted; native
Excel/Windows and representative estimator acceptance remain open. Accepted
source-bound calculations, corrections, history and exports are reusable.
Consolidated evidence PDF and all remaining Division 23 categories remain work.

## Completed checkpoints and retained evidence

These records preserve outcomes and limits. Only **Resume here** selects work.

- DIV23-SECTION-WORK-PLAN-1: at main base
  `ab438ee4e846d0e987f4ef85922c769d4acbe1e5`, prepared a section/subsection work
  register and fixed [acceptance policy](docs/operations/acceptance.md). The seed
  contains 52 UFGS August 2026 and four independently identified VA guide records,
  with 448 section-stage tasks and a hydronic worked breakdown across 37 numbered
  paragraphs. The 222 hydronic taxon seeds require normalization and review;
  they are not a completed taxonomy. Full authoritative CSI hierarchy acquisition
  and reconciliation is explicit task `D23-CSI-CATALOGUE-1`; the two recorded
  NotebookLM CSI sources were checked and contain duplicate orientation pages,
  not the full hierarchy. One public N10 hydronic query reused its verified body;
  source spans/identities and external retrievals are recorded. No section is
  accepted and no completion percentage is asserted. Engineering acceptance now
  requires fixed criteria and independent evidence; new requirements create new
  tasks/contract versions. Independent review and structural checks passed: all
  52 retained UFGS rows, 37 exact hydronic heading/spans, unique IDs, resolved
  dependencies and an acyclic task graph. The owner reaffirmed **Mac first**;
  section/taxon engineering acceptance now targets Mac, with no Windows task
  dependency. Final policy review passed. Planning workers are terminal.
  Ledger `.heleos/div23-section-work-plan-2026-09-16/`, including
  `final-plan-checks.json` and `handoff.json`.
  Next: the fixed hydronic source-audit child, then the remaining section TAX work;
  approved equipment implementation remains independently ready.


- DIV23-SCOPE-REVIEW-1: at main base
  `ab438ee4e846d0e987f4ef85922c769d4acbe1e5`, connected supplied section/requirement
  review, exact-source heading validation, existing applicability decisions,
  reopening and JSON/CSV exports. Preserves decimal/agency suffixes; distinguishes
  wrapped cross-references, non-23 boundaries, unclassified pages, heading-only
  scope and current/stale/pending review. One logged N10 query and four source
  reads yielded eight verified findings; no new quantity rules were adopted.
  Independent review findings were corrected and checked. Local focused checks
  pass on Python 3.9/3.12/3.14; browser section/source navigation, original five-page
  Foundation/Poppler integration and isolated relocated-package reopen/export
  pass. Exact commands, counts, runtime candidate identities and file hashes are
  retained in `.heleos/div23-scope-review-2026-09-16/`.
  [Source adoption record](docs/research/notebooklm/div23-scope-review-implementation-2026-09-16.json).
  Full taxonomies, representative acceptance and native Windows remain open;
  **no Division 23 section is marked complete by this feature.** All verification
  processes are terminal. Next: `D23-VA-232113-TAX` in the section work register.


- EQUIPMENT-RULES-1 / DIV23-UNBLOCK-1: at main base
  `7edc0964139d4431e88ade3f6367b4bbe038d495`, completed the E01–E12
  packet and EC01–EC20 semantic examples; the owner approved both. One logged N10 query, three source
  reads and seven verified passages support the packet. Source hashes/offsets,
  literal arithmetic, rule/case references and exact approval bindings checked. Rejected generated
  universal-guide and AHU filter-rack claims. Updated execution wording in the
  calculation plan, scope lineup, goal template and `AGENTS.md` so duct/air-device
  acceptance is not an all-category gate. No product calculation behavior changed.
  [Research](docs/research/notebooklm/equipment-counting-findings-2026-09-16.json);
  ledger `.heleos/equipment-rules-2026-09-16/`; all research calls terminal.
  Original-page fixtures, equipment implementation, model/native acceptance and
  remaining categories stay open. No Claude dispatch while its recorded auth
  failure awaits refresh; no private upload. Next ready action: EQUIPMENT-COUNT-1;
  piping preparation and supported PDF work remain independent.

- CONTINUATION-CURSOR-1: at main base
  `17b4d6e3e4fa229faff24226d1732b532c88df9a`, replaced competing immediate
  directions with one primary task, ready independent work and explicit parked
  conditions. Added anti-replay guidance to `AGENTS.md` and a copyable replacement
  goal. No application, calculation, model, hook or saved-goal state was changed.
  Ledger: `.heleos/continuation-cursor-2026-09-16/`; local documentation/authority
  verification is recorded there. No external submission or worker invocation.
  Remaining limit: app-side goal wording is still obsolete and paused; no local
  continuation hook cause was established. Next action: **EQUIPMENT-RULES-1** above.

- DIV23-SCOPE-LINEUP-1: owner clarified full section/subsection coverage at main
  base `2757d31ff93f60ed567ddd8bc9257c29a0e52fa8`. Updated scope wording and
  recorded the audit/proposed queue; no application changes or new quantity
  rules. NotebookLM inventory reused; three public VA/UFGS supporting bodies
  queried/read and consequential passages verified. CSI/publisher references
  checked for edition context. Full current hierarchy verification remains open.
  [Findings and limits](docs/research/notebooklm/div23-scope-findings-2026-09-16.json);
  ledger `.heleos/div23-scope-lineup-2026-09-16/`. Source/logging processes are
  terminal. Documentation links, JSON and diff checks passed. Optional representative
  project-priority feedback is pending; it does not block authorized work.
- WORKBOOK-EXPORT-1 is independently accepted locally in
  `/Users/bekim/Heleos-spark`, `main`, implementation base
  `bfc7cc6a07a11bd42ab13e875dc35a97f7960eb2`. The connected workbook
  [contract](docs/superpowers/specs/2026-09-16-takeoff-workbook-contract.md) and
  [verified research](docs/research/notebooklm/workbook-export-findings-2026-09-16.json)
  bind the implementation. NotebookLM ZIP passages and primary SpreadsheetML
  documentation were checked; previous source-link and rounding findings reused.
  The bounded Claude invocation `workbook-writer-claude-001` terminated before
  inference with an expired OAuth token (provider exit1, runner exit2). The
  failed invocation is retained at `heleos-worker-zkn0bT`; no candidate was
  produced or accepted. Codex implemented independently under existing authority;
  Claude login refresh is pending and does not block independent product work.
  Checks: 47 focused Python checks passed on 3.9/3.12/3.14; independent reviewer
  found no blockers and reproduced stale-source invalidation and exact exported
  source locators. The independent spreadsheet engine verified 20 formulas,
  12 mutation/restoration cases, stale/mixed cases and nine inspected views of
  all five sheets. Clean staged archive
  `24900f5ab54f4a6b71e48e48f3c7f38cafbe1448` passed the 47 checks, a relocated
  75-file package and workbook/workflow imports. The existing ZIP inventory test
  was updated for the two intentionally added files; original failure logs remain.
  JavaScript syntax and diff checks passed. Final implementation record:
  [workbook export](docs/research/notebooklm/workbook-export-implementation-2026-09-16.json).
  Completed-task ledger: `.heleos/workbook-export-2026-09-16/`; all processes
  terminal. Recognition, remaining mechanical classes and native/release gates
  remain outstanding as described in the current queue.
- Accepted attribute/policy prerequisite: `f2a12387bc1d73406949867f233acb3322193f26`;
  [implementation record](docs/research/notebooklm/air-device-attributes-implementation-2026-09-16.json).
  Accepted duct evidence remains in `.heleos/duct-topology-2026-09-15/`; all18
  accepted input hashes were checked unchanged before air-device work.
- Kernel assignment `air-device-count-claude-001` at exact base
  `73976966d94b6036eb833bf39b9711f777f11075` terminated at its 1800-second
  bound (runner exit2/timeout). Both allowed files are retained in provider run
  `heleos-worker-luOdpA`; no report or test claim is accepted. Original bytes and
  failure evidence are preserved; the repaired count kernel is independently accepted locally
  (90 tests on Python 3.9/3.12/3.14 and closed review). Exact accepted files are
  recorded in `kernel-acceptance.json`.
  Do not restart this invocation.
- Codex reviewed and integrated the count kernel, project state, source producer,
  reader, UI, workflow and package connection at base
  `b724a9a0f4778b1988be84d79e915ee841781ef7`. Checks: 90 kernel tests and
  73 reader/producer/project/workflow tests on Python 3.9/3.12/3.14; 23 Node UI
  tests; 12 existing workflow checks; package checks and relocated imports.
  The clean index archive verified 193 Python checks (175 unchanged checks plus
  18 package checks after repairing a host-only test fixture dependency), 23 Node
  checks and the 73-file relocatable runtime with six imports and the owner rule
  binding. This removes uncommitted runtime dependencies from future assignments.
  Independent review closed the recorded findings. The browser demonstrated
  correction, reopening, coverage review and history using a synthetic reader.
  It is software evidence, not model accuracy. All workers and test/model
  processes are terminal. Exact final checks, file identities, local model
  outcome and checkpoint inventory are in the task ledger.
- This local application checkpoint includes the explicit runtime package and
  air-device test dependency closure, preserving accepted prior runtime bytes.
  It does not grant native Windows, production, clean-release or representative
  recognition acceptance. Other research/evaluation work outside that inventory
  remains uncommitted and preserved. No remote push is authorized.
- Prior air-device ledger: `.heleos/air-device-count-2026-09-16/resume-state.json` and
  `claude-submission.json`; exact run, failure, test and acceptance evidence stays
  there. Preserve all prior runs and remaining uncommitted paths.
- NotebookLM uses the committed verified air-device findings; no repeated query
  or private upload is needed. Claude candidates require independent checks and
  integration. Codex remains the sole commit owner; no push is authorized.

The stored goal still contains an obsolete immediate duct-next-step paragraph.
Current owner direction and this queue supersede it. Available goal controls can
read/create or mark complete/blocked, but cannot edit wording or resume a paused
goal. The last goal snapshot reported paused; owner-directed work can still continue
under the current queue. No reset, scope reduction or false completion is authorized.

<!-- active-build-authority:v1 -->
```json
{"schema_version":1,"active_builds":[]}
```
<!-- /active-build-authority -->

The authority list tracks registered development worktrees, not provider child
processes. No provider invocation is currently running; terminal results are in
their task ledgers above.
