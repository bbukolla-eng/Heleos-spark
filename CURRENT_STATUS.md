# Current Status

Updated 2026-09-16. Work in `/Users/bekim/Heleos-spark`, branch `main`.
This is the single current work queue; historical “Next” statements in the
[archived status](docs/operations/status-archive/CURRENT_STATUS-through-2026-09-16-7397696.md)
are evidence, not assignments. Exact archived bytes and original locations are
recorded in the [archive manifest](docs/operations/status-archive/2026-09-16-archive-manifest.json).

The full local-first Division 23 Mac/Windows goal is unchanged. Settled decisions:
imperial output; duct lengths → air-device counts → equipment counts; approved
D01–D10 and A01–A12/AC01–AC17. Do not reopen those choices. Accepted duct work is
preserved. Further duct refinements, geometry/evaluation expansions or repeated
test cycles are deferred unless a recorded concrete defect blocks the active
usable deliverable; repair only that dependency.

| Roadmap section | Local implementation state | Remaining acceptance/work | Next usable product outcome |
| --- | --- | --- | --- |
| 0.1 Trust foundation | Rust core, immutable vault, intake, SQLite, recovery/backup and supply-chain implementation integrated; local macOS evidence retained. | Native Windows/NTFS, exact release candidate/CI, GitHub App decisions and release dossier remain open. They do not block authorized independent product implementation. | Reuse the core for local takeoff; preserve release gates. |
| Engineering coordination | Guarded workers and provider controls implemented; bounded Claude workflow in use. | Native Windows containment/platform evidence remains open. | Support the current product task; no new orchestration redesign. |
| 0.2 Knowledge, taxonomy, source/evaluation registry | Versioned sources, mechanical vocabulary, requirement/rule review and model baseline connected locally. Notebook inventory/research retained; relevant notebooks expanded. | Complete mechanical coverage, dataset rights/labels and representative truth/thresholds remain open. | Reuse verified findings for each active class; research only concrete implementation needs. |
| 0.3 Drawing coordinates and scale | Revision-bound geometry, scale review and invalidation connected locally. | Native parity and representative drawings remain to verify. | Reuse for source identity; each counts have no scale dependency. |
| 0.4 Schedule reconciliation | Source-linked schedule extraction and plan correspondence, decisions/history and exceptions connected locally. | Broader schedule fields/types and representative acceptance remain open. | Feed air-device attributes/declarations and then equipment counts. |
| 0.5 Mechanical takeoff | Duct calculation/correction/evidence capability accepted locally. Air-device attributes/policy accepted; count kernel, reader and producer in active implementation. Equipment draft tag review exists. | Finish connected air-device counts, then physical equipment counts. Piping, fittings, accessories, controls, insulation and demolition coverage remain outstanding; deferred duct edge cases remain recorded. | **Connected air-device takeoff workflow**, then equipment. |
| 0.6 Correction, recalculation and estimator outputs | Duct correction/recalculation and draft source-linked CSV/JSON/PDF paths exist. | Air-device correction/history/export connection now; estimator-ready Excel with formulas and consolidated evidence PDF remain unfinished. | Deliver outputs for supported classes without waiting for every later mechanical category. |
| 0.7 Mac/Windows product | Local Mac drawing/takeoff preview and relocatable package; Windows launch/package implementation prepared. | Native Windows execution, desktop shell/parity and install/update/recovery acceptance remain open. | Advance shared local workflow and independent platform verification alongside supported takeoff classes. |
| 0.8 iPhone and bounded automation | Supporting automation contracts exist; no completed companion claim. | Companion/synchronization/approval scope and native device acceptance remain future work. | Preserve original roadmap scope; do not make companion work a new Mac/Windows delivery prerequisite. |
| 1.0 Representative production pilot | No production acceptance claimed. | Freeze representative projects/truth/thresholds, verify full requested Division 23 scope and native platforms, then record human release acceptance. | Usable, traceable takeoffs demonstrated on representative Mac and Windows projects. |

## Active deliverable and fixed finish line

AIR-DEVICE-COUNT-1 is finished locally only when the application can read selected
original drawings with the configured local reader, retain evidence, calculate
source-bound each counts under A01–A12, present known subtotals and exceptions,
review/correct/recalculate without duplicate replay, reopen saved history and
export counts with original-source references. The user reviews exceptions rather
than entering the takeoff first. All 17 approved cases, necessary source/lifecycle
checks and independent review support this finish line. Helpers/tests alone do
not finish the capability. Unsupported combinations remain visible exceptions and
outstanding work; neither they nor unrelated later categories justify indefinite
delay of the usable connection. Once necessary checks pass, checkpoint and advance.

Then advance to physical equipment counts and estimator-ready Excel/evidence PDF
for supported results. Native Mac/Windows work may proceed independently. No new
duct refinement is a prerequisite without a documented blocking defect.

## Live work and evidence

- Accepted attribute/policy prerequisite: `f2a12387bc1d73406949867f233acb3322193f26`;
  [implementation record](docs/research/notebooklm/air-device-attributes-implementation-2026-09-16.json).
  Accepted duct evidence remains in `.heleos/duct-topology-2026-09-15/`; all18
  accepted input hashes were checked unchanged before air-device work.
- Kernel assignment `air-device-count-claude-001` at exact base
  `73976966d94b6036eb833bf39b9711f777f11075` is running under the guarded runner.
  Live PID65395 was verified after the interruption; resume session1406.
  Preserve this invocation—do not restart from a quiet output or stale chat.
- Codex owns `local_air_device_vision.py`, application connection and commits;
  `airdevice_map` owns the named source-producer/test candidate. Initial adapter
  checks pass; producer has two concrete source-binding/cancellation repairs in
  progress. These are air-device dependencies, not duct refinement work.
- Active ledger: `.heleos/air-device-count-2026-09-16/resume-state.json` and
  `claude-submission.json`; exact run, failure, test and acceptance evidence stays
  there. Earlier application edits remain uncommitted and must be preserved.
- NotebookLM uses the committed verified air-device findings; no repeated query
  or private upload is needed. Claude candidates require independent checks and
  integration. Codex remains the sole commit owner; no push is authorized.

The stored goal still contains an obsolete immediate duct-next-step paragraph.
Current owner direction and this queue supersede it. Available goal controls can
read/create or mark complete/blocked, but cannot edit wording or resume a paused
goal. The last goal snapshot reported paused; this owner-directed turn continues
implementation. No reset, scope reduction or false completion is authorized.

<!-- active-build-authority:v1 -->
```json
{"schema_version":1,"active_builds":[]}
```
<!-- /active-build-authority -->

The authority list tracks registered development worktrees, not provider child
processes. The live provider invocation is recorded in the active ledger above.
