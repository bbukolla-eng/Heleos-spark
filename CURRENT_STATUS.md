# Current Status

**Isolated automation side task, 2026-09-23 UTC:** This copy is in
`/Users/bekim/.codex/worktrees/github-automation/Heleos-spark`, branch
`codex/github-automation`, initial base `f9d7ceda027875b0dbef24710bfca96c6c95a104`; published setup
commit `ce9d99e9ec8193edd79b03a7da35c0ef76746536` is the parent of this follow-up.
Codex owns only this automation candidate; Cursor's parent checkout and the
product queue below are unchanged. GITHUB-AUTOMATION-2026-09-23 is in progress:
custom app installed only on Heleos-spark, key secret name verified, CodeQL setup
passed, Dependabot enabled and native review/security/merge rules strengthened.
Automation remains disabled. Local policy tests and schema checks pass; independent
review, publication and GitHub pilot remain outstanding. Scoped setup commit/push/PR publication is authorized by the latest owner request;
no unrelated product merge is authorized. [Task record](docs/operations/github-automation-2026-09-23/README.md).
PR [#26](https://github.com/bbukolla-eng/Heleos-spark/pull/26) is open against
`2026-09-21-t5b5`. Initial GitHub checks/dependency review and Amazon Q passed;
CodeRabbit review is running. The CodeQL protection follow-up passed 23 local
policy tests. Next side-task action: review the latest candidate and its CI, then
perform a disabled-mode GitHub pilot after the checkpoint foundation reaches main.
Do not use this side checkpoint to reroute or reopen the product queue.

Updated 2026-09-22. Checkout `/Users/bekim/Heleos-spark`, branch `2026-09-21-t5b5`; this queue update is based on `5715a45dd4b26f8107271e5f0b2c04cd3901517d`. Queue edits are local until explicitly committed.
**Resume here is the single live queue.** The goal defines the product outcome;
the CSI register defines scope. Current delivery priority is complete Division 23
capability on **Mac first**; native Windows delivery follows the connected workflow.

## Resume here

**Checkout controller:** Cursor, recorded 2026-09-21 by owner direction for this checkout and branch. Cursor holds this live queue, reviews candidate bytes here, updates checkpoints, and commits locally only when the owner explicitly asks. The workflow stays: freeze the contract, test, record evidence, update the checkpoint, and run `python3 scripts/verify-build-checkpoint.py --staged`.

The owner keeps merging to `main`, pull-request approval, push, branch protection, auto-merge, and the `claude-egress` secret. Those stay GitHub Settings actions in [github automation](docs/policies/github-automation.md). NotebookLM stays on the Codex connection. Until Cursor has that connection, section research reuses saved records under `docs/research/notebooklm/`. This handoff leaves `.github/`, hook paths, and `docs/decisions/` unchanged. Older records that name Codex as commit owner stay historical. The evidence PDF acceptance in this section stays as written, and this record does not commit that candidate.

**Scope authority:** [Full CSI Division 23 hierarchy and section plans](docs/superpowers/plans/2026-09-16-division23-section-delivery.md).
The verified **April 2016 CSI/CSC catalogue** contains **432 nodes including the
Division 23 root, 431 entries below it, and 14 direct branches**. Every node has
its own card, four planned delivery frames and finite criterion bindings. Read
only the selected card and required contract subset. The old agency-guide
organizing format and its 56 active cards have been removed. It is not the live
scope or a second roadmap.

**Primary product task: CSI-23-21-23-DEFINE.** Prepare Hydronic Pumps for its first source-backed implementation slice. This advances the approved equipment-counting work; it is not a selection inferred from the owner's earlier hydronic illustration.

**Next executable action:** Prepare CSI-23-21-23-DEFINE: reuse the recorded NotebookLM inventory and equipment findings, verify pump-family source passages, and record scope, input gaps and the first bounded implementation contract. If required sources are unavailable, record the affected gap and advance to CSI-23-07-19-DEFINE.

**Division-wide delivery commitment:** Every section/subsection receives the same depth of verified research, taxonomy, applicable rules, connected implementation, correction/export behavior, meaningful tests and independent review as ducts and air devices. Reuse accepted components; do not reduce a section to its title or one specimen. The effort varies with scope, not with its place in the queue.

**Staged work — all 14 branches remain visible.** The table is the live scheduling order, not a mechanical acceptance claim. “Prepare” permits source inventory/verification and contract definition; none of these new rows is implementation-ready yet. Cursor remains checkout controller and assigns named workers/reviewers before dispatch. Codex makes this bounded queue repair under the owner's current request; no worker is being launched by this edit.

| Order | CSI branch | First section task / card | Stage | Next source-definition deliverable |
| --- | --- | --- | --- | --- |
| 1 | 23 20 00 — HVAC Piping and Pumps | [CSI-23-21-23-DEFINE — Hydronic Pumps](docs/superpowers/plans/division23-sections/csi-23-21-23.md) | Next: prepare | Equipment-count rules and connected counts already exist; verify applicability to pump families and package boundaries. |
| 2 | 23 07 00 — HVAC Insulation | [CSI-23-07-19-DEFINE — HVAC Piping Insulation](docs/superpowers/plans/division23-sections/csi-23-07-19.md) | Preparation slot 2 | Inventory retained insulation findings; verify material, size, thickness, service and host relationships before freezing cases. |
| 3 | 23 09 00 — Instrumentation and Control for HVAC | [CSI-23-09-13.23-DEFINE — Sensors and Transmitters](docs/superpowers/plans/division23-sections/csi-23-09-13-23.md) | Preparation slot 3 | Inventory sensor/transmitter evidence; resolve device, point and packaged-component boundaries before freezing cases. |
| 4 | 23 05 00 — Common Work Results for HVAC | [CSI-23-05-29-DEFINE — Hangers and Supports for HVAC Piping and Equipment](docs/superpowers/plans/division23-sections/csi-23-05-29.md) | Queued | Verify host links, assemblies, exclusions and supported spacing/count evidence; do not infer spacing from a title. |
| 5 | 23 06 00 — Schedules for HVAC | [CSI-23-06-20.13-DEFINE — Hydronic Pump Schedule](docs/superpowers/plans/division23-sections/csi-23-06-20-13.md) | Queued | Bind schedule fields to the pump section and existing reconciliation; distinguish a schedule row from a physical instance. |
| 6 | 23 10 00 — Facility Fuel Systems | [CSI-23-11-23-DEFINE — Facility Natural-Gas Piping](docs/superpowers/plans/division23-sections/csi-23-11-23.md) | Queued | Verify gas-piping source scope, materials, joints, fittings and testing obligations; freeze supported quantities and exceptions. |
| 7 | 23 40 00 — HVAC Air Cleaning Devices | [CSI-23-41-13-DEFINE — Panel Air Filters](docs/superpowers/plans/division23-sections/csi-23-41-13.md) | Queued | Verify filter instances, banks, equipment-package ownership and replacement/service distinctions. |
| 8 | 23 50 00 — Central Heating Equipment | [CSI-23-52-00-DEFINE — Heating Boilers](docs/superpowers/plans/division23-sections/csi-23-52-00.md) | Queued | Inventory boiler children and package evidence; choose the first supported child behavior and retain all other children as open. |
| 9 | 23 60 00 — Central Cooling Equipment | [CSI-23-64-16-DEFINE — Centrifugal Water Chillers](docs/superpowers/plans/division23-sections/csi-23-64-16.md) | Queued | Verify chiller children, equipment packages and separately supplied components before count/reconciliation cases. |
| 10 | 23 70 00 — Central HVAC Equipment | [CSI-23-73-13-DEFINE — Modular Indoor Central-Station Air-Handling Units](docs/superpowers/plans/division23-sections/csi-23-73-13.md) | Queued | Verify AHU assembly and module boundaries, schedules and separately supplied components. |
| 11 | 23 80 00 — Decentralized HVAC Equipment | [CSI-23-81-26-DEFINE — Split-System Air-Conditioners](docs/superpowers/plans/division23-sections/csi-23-81-26.md) | Queued | Verify indoor/outdoor assembly relationships and child variants; define double-counting and missing-evidence cases. |
| 12 | 23 01 00 — Operation and Maintenance of HVAC Systems | [CSI-23-01-30.51-DEFINE — HVAC Air-Distribution System Cleaning](docs/superpowers/plans/division23-sections/csi-23-01-30-51.md) | Queued | Verify cleaning extents, affected assets and evidence/reporting obligations; do not invent material counts. |
| 13 | 23 08 00 — Commissioning of HVAC | [CSI-23-08-00-DEFINE — Commissioning of HVAC](docs/superpowers/plans/division23-sections/csi-23-08-00.md) | Queued | Verify commissioning scope, asset/system links, required tests and deliverables as evidence-backed obligations. |
| 14 | 23 30 00 — HVAC Air Distribution | [CSI-23-31-13-DEFINE — Metal Ducts](docs/superpowers/plans/division23-sections/csi-23-31-13.md) | Blocked scope; preserved | Retain bodies for 23 31 13.13, .16 and .19 to finish the recorded definition; preserve accepted duct/air-device software. |

**Queue advancement:** Keep one implementation task active and at most two independent definition preparations. Complete each admitted slice through its connected Mac checks; do not collect definitions for all branches before coding. At a completion or an input block, select the first ready row above, record the reason for any skip, and replace a completed row's seed with the next unfinished section/child in that branch. Move a served branch behind unserved ready branches after a bounded connected slice; do not finish an entire branch before starting others. Missing sources block dependent implementation, not unrelated source preparation. No unchanged recognition retry or duct refinement displaces this lineup without a recorded defect or changed input.

**CSI23-EXECUTION-LINEUP-2026-09-22 — queue repair complete locally:** all 14 branch/seed/card bindings and CSI structural checks pass. Independent review accepts this planning-only change; [checks](docs/operations/csi23-lineup-2026-09-22-checks.json) and [review](docs/operations/csi23-lineup-2026-09-22-review.json) retain the evidence. No new mechanical section or implementation is accepted. The next executable action is the pump definition above; these edits remain uncommitted.

**Each handoff must say:** task ID; stage and named owner; exact next action; frozen output and expected cases; real dependencies; checks/reviewer; evidence; completed outcome or missing prerequisite; next selected task. [The master plan's staged-execution contract](docs/superpowers/plans/2026-09-16-division23-section-delivery.md#staged-execution-contract) defines the preparation finish line and admission to implementation. The existing checkpoint checks bind the primary task/next action; they do not automatically schedule workers or enforce branch rotation.

**KIMI-RETIRE-2026-09-22 complete locally.** Kimi is removed from active assignments and its plugin is uninstalled. The runner CLI/library reject dispatch before a worker workspace is created; the old stdin adapter refuses every invocation without reading prompts or credentials. Historical protocol records and evidence remain readable. [Task record](docs/operations/kimi-removal-2026-09-22.json) and [independent review](docs/operations/kimi-removal-2026-09-22-review.json) bind the accepted candidate to 66 passing checks (47 runner, 17 protocol, 2 adapter); the initial failures and fixes are retained. [Collaboration record](docs/operations/codex-cursor-collaboration-2026-09-22.md) preserves Cursor's controller role and corrects stale documentation about existing Cursor runner support.

The owner explicitly requested the scoped commit and PR. That publication checkpoint was subsequently pushed as PR #24. This local queue repair advances the product task as described above. No merge, release, GitHub Settings change or native Windows acceptance is included. The routing helper still reports `main_checkout_unavailable`; branch/base and owned-file identities were verified directly. Unrelated `.cursor/` files are preserved outside this commit.

**EVIDENCE-PDF-1** local software is independently reviewed and accepted for the
frozen contract and specimen rows. It is committed on branch `2026-09-21-t5b5`
as `fa45862dbd65d634b45f772a7c874fe1ee1b6e43`, based on
`49b7f57a208e162054b0d856dd299852a1bfce50`. This acceptance does not close a CSI
section, qualify recognition, accept Windows, or record a bid.

Evidence paths: contract
`docs/operations/evidence-pdf-2026-09-21-contract.md`, specimen
`tests/fixtures/evidence-pdf/specimen-rows.json`, writer
`scripts/evidence_pdf.py`, review
`docs/operations/evidence-pdf-2026-09-21-review.md`, task record
`docs/operations/evidence-pdf-2026-09-21.json`. The task record says `complete`.

The reviewer re-ran these commands and each exited 0:
`python3 -m unittest discover -s tests/drawing-workspace -p 'test_evidence_pdf.py' -v`
(4 tests), the same form for `test_takeoff_workbook.py` (13),
`test_takeoff_workflow.py` (12), `test_workspace_package.py` (18),
`test_equipment_count_workflow.py` (8), and `test_air_device_workflow.py` (5).
Mac PDFKit inspect (`swift /tmp/heleos-evidence-review/inspect.swift /tmp/heleos-evidence-review/duct-current.pdf /tmp/heleos-evidence-review/pages`)
and `qlmanage -t -s 800 -o /tmp/heleos-evidence-review /tmp/heleos-evidence-review/duct-current.pdf`
also exited 0. `pdftotext` and `pdfinfo` are not installed and were not run.
`python3 scripts/verify-build-checkpoint.py --staged` exited 0 on the index
as it stood at the end of that review, before this status edit.

Notes that are not blocking: a PDF `ValueError` during draft export is labeled
`workbook_projection`, and each section is a single page. The package test
expects 84 files because the PDF writer is in the payload. The previously
accepted 83-file equipment package record stays that milestone. Cursor is the
checkout controller recorded in Resume here.

That PDF checkpoint outcome is `complete` in `fa45862dbd65d634b45f772a7c874fe1ee1b6e43`.
The independent review accepted the export candidate with no blocking findings.
The review text still describes the status it saw, including branch `main` and
an uncommitted candidate. `pdftotext` and `pdfinfo` were not run.

**CSI-23-31-13-DEFINE** is preserved with blocked source-dependent criteria; it is no longer the sole live assignment. The packet under
`docs/engineering/division23/csi-23-31-13/define/` binds approved D01-D10 and
E01-E09 to Metal Ducts. D01-D06 stay blocked. Child product types 23 31 13.13,
23 31 13.16, and 23 31 13.19 have catalogue titles only. This packet does not
accept the section.

**Completed — EQUIPMENT-COUNT-1-SOFTWARE:** approved E01–E12 / EC01–EC20 now drive
source-bound physical counts, separate package/procurement/installation channels,
known-plus-unknown results, corrections, reopening/history and CSV/JSON/Excel.
The [frozen contract](.heleos/equipment-count-2026-09-16/contract.md),
[API](.heleos/equipment-count-2026-09-16/api.md) and
[completion record](docs/operations/equipment-count-2026-09-16.json) bind EQ01–EQ07,
exact candidate identities, independent reviews and check results. Accepted local
software; integrated in `db695750d095c6845f0689e3cc4da79d09333935`, based on
`05c55645fdf01a3238c13097c2f100dfa653799a` in the main checkout above.

Verification: all 20 approved cases, adapter and connected workflow tests passed;
51 JavaScript checks passed; the Mac browser correction/reopen case changed three
pumps plus one AHU to two pumps plus one AHU while preserving history. The actual
83-file portable package built and loaded its pinned equipment rules. The broad
Python run had 1,250 passes and 10 failures from two outdated fixture modules;
those fixtures were repaired and all 26 affected tests then passed independently.
The failed run is retained; no all-green 1,260-test rerun is claimed. The pre-existing
untracked geometry test's one-line correction remains local, with its patch and
before/after hashes recorded. Unrelated existing changes remain preserved.

The equipment parent remains open for qualified automatic physical recognition
and representative-project proof. This accepted software admits explicitly
reviewed physical regions; draft tags do not establish physical quantities. It
closes no CSI section. All 432 CSI cards and their finite task/acceptance frames
remain the Division 23 lineup, with agency guides used only as supporting sources.
Use actual dependencies to advance independent section definitions and behavior;
do not reopen the completed equipment, duct or air-device software without a
recorded relevant change or defect.

**Edition reconciliation (separate blocked input):** CSI-CATALOGUE-2026-RECONCILE needs an authorized complete newer-edition catalogue. It is not an executable alternative to section work. The staged table above supplies independent preparation work under the verified 2016 baseline.

**NotebookLM is required throughout section delivery.** Every assignment/terminal task record carries the [research disposition and evidence](docs/operations/notebooklm-research.md#required-task-evidence-owner-reaffirmed-2026-09-22): newly queried and passage-verified, applicable verified findings reused, or unavailable with its scoped gap and follow-up. Review checks the source-to-behavior/test links. Pure administrative edits identify that they introduce no new technical claims. There is no silent skip, no claim of a fresh query from reused evidence, and no automatic NotebookLM enforcement hook yet.

**NotebookLM entrypoint:** [CSI Division 23 notebook](https://notebooklm.google.com/notebook/53cebee4-b959-406d-930c-fc697a3d9e61),
with the primary catalogue and 14 indexed branches. The [current routing record](docs/research/notebooklm/csi-division23-routing.json)
records source/label IDs and the five renamed reference libraries. Guide
publishers are evidence providers; their inventories do not define section
identity or product coverage. All previous reference source IDs remain intact.

**Parked:** AIR-DEVICE-COUNT-1-RECOGNITION needs a materially different bounded
experiment and frozen metrics after eight terminal runs; AC01 found 0 of 3
physical symbols. Its accepted software child stays closed. Claude's fresh
contained request failed with expired OAuth even though the CLI reported logged
in; the migration validator and equipment assignment used the authorized Codex fallback. Do not repeat
that provider request without a changed usable login state.

A missing prerequisite blocks only its dependent behavior. Duct/air-device
acceptance never gates unrelated sections. The owner's hydronic illustration
selected no priority or permanent example. Cancelled 4specs adoption remains
cancelled. Do not replay this completed structure migration or turn its checker
into a recurring status-only task.

## Coverage and completion

The [CSI register](docs/plans/division-23-section-register.json),
[task frames](docs/plans/division-23-task-contracts.json) and
[completion matrix](docs/plans/division-23-completion-matrix.md) now share the same
CSI hierarchy. **432 cards and 1,728 task frames are planned coverage, not 1,728
ready assignments. Zero mechanical sections are accepted.** DEFINE freezes
applicable source bodies, full taxonomy, rules, independent expected results,
numerical tolerances, interfaces and exact implementation checks before dependent
work. The 2016 classification baseline is complete for that edition; it is not
claimed to be the complete 2026 catalogue.

A task is complete when its frozen mandatory criteria pass for the recorded
bytes, independent review accepts that deliverable, and integration/evidence and
the next action are recorded. Expected UNKNOWN passes only its named case.
Optional improvements get separate tasks. No recurring owner approval is needed
within existing authority.

A section is complete when SC01–SC08 cover its entire required own taxonomy and
children: source evidence, approved rules, supported quantities or service
obligations, reconciliation, corrections/reopening, Excel/evidence PDF and a
frozen representative Mac dossier. Parent nodes reuse accepted child evidence;
they do not demand repeated child review. Shared software and project-specific
absence cannot close a whole section. Follow [acceptance](docs/operations/acceptance.md)
and the [section work breakdown](docs/plans/division-23-section-work-breakdown.md).

## Accepted capabilities and remaining product work

| Capability | Preserved local result | Remaining scope |
| --- | --- | --- |
| Foundation | Rust core, vault/intake/SQLite/recovery and local integration | Foundation release/CI/App decisions remain separate gates |
| Coordinates and scale | Revision-bound geometry, scale review and invalidation | Broader original drawings/representative proof |
| Source and schedule review | Source-linked extraction, correspondence, decisions/history, section and requirement review | Full section taxonomies and broader supported types |
| Duct | Approved D01–D10 connected calculation/correction and evidence | Recognition/representative scope; no repeated refinements without a recorded defect |
| Air-device software | Approved A01–A12 calculation, producer transport, review/correction/reopen/history and source exports | Recognition child parked; software acceptance is preserved |
| Equipment software | E01–E12/EC01–EC20 connected counts, separate quantity channels, correction/reopen/history and CSV/JSON/Excel independently verified on Mac | Automatic physical recognition, representative qualification and full CSI section bindings remain open |
| Estimator output | Source-linked Excel/formulas with supported result projection, local package checks, and an independently accepted evidence PDF for the frozen contract and specimen rows, committed in `fa45862dbd65d634b45f772a7c874fe1ee1b6e43` | Desktop spreadsheet and representative estimator proof. The PDF is not a bid, a CSI section closure, recognition qualification, or Windows acceptance. |
| Remaining Division 23 | Every CSI baseline node has a linked task and acceptance card | Every unfinished required taxonomy and connected behavior stays open |
| Platform/product | Mac implementation and local verification; portable shared code | Later WINDOWS-DELIVERY-1, representative product and final owner release acceptance |

The [terminal checkpoint](docs/operations/delivery-checkpoint-2026-09-16.json)
joins the workbook's original pre-integration acceptance with its integrated
commit and separates accepted air-device software from failed recognition. It
preserves original receipts and adds no new product acceptance. Complete section
and Mac/Windows product acceptance remain outstanding.

## Latest checkpoint and continuity

**BUILD-CHECKPOINT-GUARD-1 complete:** the repository-local pre-commit hook is
installed, and the CI definition now checks every new checkpoint commit. The
validator binds staged or committed status, task outcome, exact changed-file and
evidence hashes, review and next action. It permits honest in-progress checkpoints;
it does not rewrite status, commit, push, schedule tasks or reopen accepted work.
The [current receipt](docs/operations/build-checkpoint.json),
[independent review](.heleos/build-checkpoint-2026-09-16/review.md) and
[operating workflow](docs/operations/build-checkpoints.md) preserve the contract,
verification and usage. Base `db695750d095c6845f0689e3cc4da79d09333935`; integrated
by the completion commit containing this checkpoint. All 190 top-level CI unit
tests passed locally, including 61 guard boundary tests. Actual Git tests reject
missing checkpoints and accept correctly recorded progress. No product quantity
rule changed. All implementation/test workers are terminal.

**CI-RETAINED-LINKS-1 complete:** the existing repository CI checker exposed
56 relocated-document link errors and four roadmap prose errors. A bounded
repair preserves archived/approved bytes, pins their original link locations and
continues checking every target. All 23 affected tests passed independently and
the scoped review accepted the repair. Earlier failed results remain recorded;
the enlarged full test suite was not rerun or claimed as rerun.

**CI-DOCUMENT-CLOSURE-1 complete:** the clean staged snapshot exposed references to
pre-existing untracked documentation. An independent audit accepted exactly 52
hash-pinned research/history documents for local retention. Their old next actions
remain historical; they add no mechanical authority and do not replace this queue.
The [closure review](.heleos/build-checkpoint-2026-09-16/document-closure-review.md)
records the scope and provenance limits. The resulting clean staged snapshot
passed the repository CI checker: 1,025 tracked files, zero failures. Its
[exact-tree log](.heleos/build-checkpoint-2026-09-16/staged-repository-checks-final.log)
preserves the tested identity; the earlier failed snapshot remains recorded.

**Remaining limits:** local hooks can be bypassed; the checker establishes record
consistency, not mechanical truth. CI publication and a successful GitHub run are
not claimed; no push or remote settings change was made. New clones must run the
installer. The initial NotebookLM lookup was too narrow: the connected tool is
named Gemini Notebook MCP. A delayed N07 query and raw-source verification now
confirm the limited Python process/hash findings; this is recorded honestly as
post-implementation research, with the initial mistake preserved. Primary
Git/Actions references were verified before implementation. Existing Claude
authentication failure remains unchanged.
This bounded guard setup returned execution to the product queue. The live
action is the selected CSI section DEFINE in Resume here. It is not a
recurring status-only assignment.

**EQUIPMENT-COUNT-1-SOFTWARE**, base
`05c55645fdf01a3238c13097c2f100dfa653799a`: accepted connected software, with the
[completion record](docs/operations/equipment-count-2026-09-16.json) preserving
review dispositions, source-research reuse, browser/export/package evidence,
fixture repairs, verification limits and terminal worker/process states. Codex
reviewed and integrated the candidate; no worker committed or submitted private
data externally. That checkpoint named EVIDENCE-PDF-1 as its next task. Resume
here now records that export as accepted local software committed in `fa45862dbd65d634b45f772a7c874fe1ee1b6e43`, and the
live action is the selected CSI section DEFINE. The completed CSI migration
below remains scope authority, not a task to replay.

**CSI23-HIERARCHY-MIGRATION-1**, base
`1adf57af329c3d228db83703b75638fcba39ef18`, main checkout above:
replaced the 56-guide active format with the verified CSI hierarchy, cards,
contracts and matrix; updated goal/instructions/research routing; created the
CSI NotebookLM index and renamed five supporting libraries. Public-source
additions, note/label changes and readbacks are recorded in
`.heleos/csi-hierarchy-migration-2026-09-16/` and the
[CSI research record](docs/research/notebooklm/csi-division23-findings-2026-09-16.json).
No accepted application code or approved calculation rule was changed. Historical
agency-source receipts remain evidence, not active planning instructions.

The read-only `scripts/verify-csi-division23.py` checks schema, CSI identities,
parent/child consistency, task/criterion bindings and card paths. Its 28 focused
positive/negative tests and live structure validation pass independently. Source
verification compared all 432 ordered identities/pages and titles with the
publisher PDF; NotebookLM readbacks match every branch row. Final migration
review/integration evidence is recorded in
`docs/operations/csi-hierarchy-migration-2026-09-16.json`.

Current-edition reconciliation, full mechanical definitions/implementations,
representative acceptance and later Windows delivery remain outstanding. The
catalogue reference is admitted for this internal planning/research; commercial
redistribution permission is not established. Independent CSI section DEFINE
work is the live action in Resume here. Catalogue reconciliation stays blocked
on an authorized newer-edition catalogue.

The earlier CSI migration amended the repository goal. This checkpoint installs
only the local completion hook and CI validation; no app goal or recurring
automation was created. `active-build-status.py` validates
committed checkout/worktree authority and does not schedule the build. Read
status again for changed bytes, handoff or recovery; unchanged rereads are not
progress. Earlier exact checkpoints remain in Git history and the
[historical status archive](docs/operations/status-archive/CURRENT_STATUS-through-2026-09-16-c1143a7.md).

<!-- build-checkpoint:v1 -->
```json
{
  "schema_version": 1,
  "receipt": "docs/operations/build-checkpoint.json",
  "task_id": "GITHUB-AUTOMATION-2026-09-23",
  "outcome": "in_progress",
  "next_task_id": "CSI-23-21-23-DEFINE",
  "next_action": "Prepare CSI-23-21-23-DEFINE: reuse the recorded NotebookLM inventory and equipment findings, verify pump-family source passages, and record scope, input gaps and the first bounded implementation contract. If required sources are unavailable, record the affected gap and advance to CSI-23-07-19-DEFINE."
}
```
<!-- /build-checkpoint -->

<!-- active-build-authority:v1 -->
```json
{"schema_version":1,"active_builds":[]}
```
<!-- /active-build-authority -->

The authority block tracks registered development worktrees, not child processes.
Record each terminal worker result and advance the live action in the same
completion checkpoint.
