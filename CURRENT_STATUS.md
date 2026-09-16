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

**Duct and air-device acceptance do not gate the other Division 23 categories.**
The owner explicitly rejected that bottleneck on 2026-09-16. The initial class
order is delivery priority; work advances from its actual inputs and class rules.
Do not return to duct/air-device refinement to fill a gap in another task.

**Primary ready task: EQUIPMENT-COUNT-1 — implement connected physical-equipment
counts under the approved E01–E12 and EC01–EC20.** The owner approved the
[equipment packet](docs/superpowers/specs/2026-09-16-equipment-counting-rules.md)
and its 20 examples on 2026-09-16; the
[exact decision and hashes](tests/fixtures/equipment-takeoff/2026-09-16-owner-decision.json)
are recorded. Do not ask again or repeat rule preparation.

First executable action: bind the approved inputs and implement the deterministic
equipment count projection plus focused checks, reusing source identities and
keeping instance, package and installation outputs separate. Then connect it to
existing equipment observations, reconciliation, corrections/reopen/history and
exports. The fixed finish line is that connected workflow with visible known and
unresolved quantities; a standalone kernel is an intermediate checkpoint only.
The task contract and research receipts are in
`.heleos/equipment-rules-2026-09-16/resume-state.json`. No equipment quantity engine
or representative/native acceptance is claimed yet.

**Ready independent preparation: PIPING-RULES-1 — prepare the hydronic piping
rule/example packet.** Reuse N10's recorded public source inventory; verify
applicable pipe/service/material/size, fittings/valves, vertical/dimension and
work-status requirements. Keep steam/condensate, refrigerant and fuel variants
explicit as separate subsequent packets. Coordinates, scale and source identity
are reusable prerequisites; duct/air-device recognition is not. Do not transfer
duct rules to piping without the appropriate class basis.

**Ready independent implementation: EVIDENCE-PDF-1 — connect consolidated,
source-linked PDF evidence to the existing export flow.** Use current supported
quantities and preserve stale/unknown states. This output task must not indefinitely
displace the remaining mechanical categories.

**Other ready independent lanes:** section/subsection coverage mapping;
insulation/liner/jacket requirements; controls and supply/installation
responsibility; common-work/support requirements; TAB, commissioning and O&M
obligations. Their [actual dependencies and work lineup](docs/research/div23-scope-and-proposed-lineup-2026-09-16.md)
are recorded individually. Specification requirement extraction can precede final
host quantities; missing quantities block only their dependent calculations.
Full hierarchy verification and project applicability remain distinct from
product completion. Do not invent CSI section numbers or treat the nine-category
vocabulary as full coverage.

Use bounded assignments, one writer per path and available execution capacity.
Optional representative-project feedback does not block authorized preparation.
All remaining categories stay outstanding until connected and accepted.

**Parked conditions:** air-device original-drawing recognition needs a materially
different bounded experiment after eight terminal runs; Claude needs a refreshed
login after the recorded expired-token failure; native Windows and representative
acceptance need their own evidence. Each blocks only its dependent action.
Record changed inputs and acceptance criteria before retrying. No provider process
is running. A synthetic-reader demonstration is not recognition acceptance.

**Reuse accepted work:** duct calculation/topology, schedule reconciliation,
air-device software and approved rules, and source-linked Excel. Do not rerun
accepted implementations, unchanged tests or status maintenance to fill a turn.
The [replacement goal](docs/operations/build-goal.md) now states the same
independence; it has not been installed in the app. The historical “first/next”
wording in the calculation plan is explicitly superseded. Execute ready work,
record its evidence and advance this section in the same completion update.

## Roadmap coverage snapshot

| Roadmap section | Local implementation state | Remaining acceptance/work | Target product outcome |
| --- | --- | --- | --- |
| 0.1 Trust foundation | Rust core, immutable vault, intake, SQLite, recovery/backup and supply-chain implementation integrated; local macOS evidence retained. | Native Windows/NTFS, exact release candidate/CI, GitHub App decisions and release dossier remain open. They do not block authorized independent product implementation. | Reuse the core for local takeoff; preserve release gates. |
| Engineering coordination | Guarded workers and provider controls implemented; bounded Claude workflow in use. | Native Windows containment/platform evidence remains open. | Support the current product task; no new orchestration redesign. |
| 0.2 Knowledge, taxonomy, source/evaluation registry | Versioned sources, mechanical vocabulary, requirement/rule review and model baseline connected locally. Notebook inventory/research retained; relevant notebooks expanded. | Complete mechanical coverage, dataset rights/labels and representative truth/thresholds remain open. | Reuse verified findings for each active class; research only concrete implementation needs. |
| 0.3 Drawing coordinates and scale | Revision-bound geometry, scale review and invalidation connected locally. | Native parity and representative drawings remain to verify. | Reuse for source identity; each counts have no scale dependency. |
| 0.4 Schedule reconciliation | Source-linked schedule extraction and plan correspondence, decisions/history and exceptions connected locally. | Broader schedule fields/types and representative acceptance remain open. | Feed air-device attributes/declarations and then equipment counts. |
| 0.5 Full CSI Division 23 mechanical takeoff | Partial implementation: duct capability preserved; air-device calculation, source producer, review/correction UI and exports verified locally; equipment draft tag review exists. | Complete section/subsection coverage remains open across common work, O&M, schedules, insulation, commissioning, controls, fuel systems, piping/pumps, air distribution/cleaning, central heating/cooling and central/decentralized HVAC equipment. Cross-cutting demolition, relocation and revision scope remains open. **Air-device recognition remains unqualified:** the installed model missed all three physical symbols in AC01. | Equipment rules/examples approved; connected equipment count implementation is ready. Advance piping and other categories against their actual dependencies; no duct/air-device acceptance gate. |
| 0.6 Correction, recalculation and estimator outputs | Duct and air-device correction/recalculation, append-only history, CSV/JSON and source-linked Excel with live formulas connected locally. Workbook projection, formula recalculation, stale/unknown states and portable packaging verified. | Consolidated evidence PDF, native Excel/Windows and representative estimator acceptance remain unfinished. | **Next independent implementation: consolidated evidence PDF for supported results**, reusing current source identities and unknown/review state. |
| 0.7 Mac/Windows product | Local Mac drawing/takeoff preview and relocatable package; Windows launch/package implementation prepared. | Native Windows execution, desktop shell/parity and install/update/recovery acceptance remain open. | Advance shared local workflow and independent platform verification alongside supported takeoff classes. |
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
