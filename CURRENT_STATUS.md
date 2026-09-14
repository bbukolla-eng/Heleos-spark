# Current repository status

Completion reporting (owner direction 2026-09-13): AGENTS.md now requires the
coordinating agent to update this file for every completed scoped task before
reporting completion or handing off. Updates include the outcome, checkout/base
commit, verification and evidence, remaining limits, and one next action;
authorized completion commits include the status update. This documentation task
uses main base 913d4308bdd12ff75e126a1f9832f8f7fefd900b. Whitespace checks pass;
detailed checks and file identities are in .heleos/completion-status-policy-2026-09-13. Product
implementation and acceptance are unchanged. Next action remains the in-progress
duct fixture preparation recorded below.

Continuity checkpoint 913d430 (2026-09-13) records CURRENT_STATUS.md only.
Product implementation and the in-progress duct PDF
fixtures remain uncommitted in this checkout and must be preserved. The fixture
task's latest record is .heleos/duct-fixture-build-2026-09-13/progress.json;
its current state is preparing, with no completed handoff. This checkpoint does
not mark that work complete or approve the remaining duct rules. Older HEADs and
next actions below describe their named historical checkpoints.

Active product goal (2026-09-13, DUCT-CALCULATION-1): the owner activated the full
local-first Mac/Windows Division 23 goal in session
01a09d78-e467-7a61-99ba-be72b8f49910. The goal remains active and incomplete.
The [remaining duct rule/example packet](docs/superpowers/specs/2026-09-13-duct-measurement-rules.md)
and nine literal synthetic examples are prepared; arithmetic checks pass, while
the owner's remaining-rule and example-adjudication answer is pending. These are
not source-bound PDF fixtures or representative-project acceptance results.
The [dataset preparation manifest](docs/research/engineering/roboflow-preparation-manifest-2026-09-13.json)
links all nine existing candidates by hash, preserves 67 raw labels for review,
records the observed HVAC overlap and current baseline limits, and admits none.
Both read-only preparation agents are terminal. Exact preparation checks and
process state are in .heleos/duct-calculation-build-2026-09-13.

Dataset preparation implementation (2026-09-13, DATASET-AUDIT-1):
scripts/mechanical_dataset_audit.py now audits explicit local COCO exports without
changing inputs or admitting data. It retains raw label/count information, finds
identical image bytes across declared splits, reports source-group conflicts and
unknown ancestry, and reports the current baseline size limits without trimming
annotations. Root-verified tests pass 21 cases on Python 3.14.6 and 3.9.6. An
independent synthetic CLI fixture retains all 252 annotations, detects cross-split
overlap despite different group claims, reports the 251-object image, and produces
identical repeat/dual-runtime reports. Invalid Unicode fails with structured input
errors. Inputs and existing product code remain unchanged. See the
[usage guide](docs/operations/mechanical-dataset-audit.md) and
.heleos/dataset-audit-build-2026-09-13 for exact checks and hashes. No downloaded
archive, semantic/rights acceptance, model execution or native Windows proof is
claimed. The implementation worker and verification commands are terminal.

Confirmed calculation sequence (2026-09-13): the owner selected
duct lengths by size, then air-device counts, then equipment counts, with imperial
outputs. The owner approved drawn centerlines, vertical lengths supported by
dimensions/elevations, separate fitting counts, and waste/allowances kept separate
from measured totals. This supersedes the earlier unresolved class/unit choice;
the historical blocked-goal audit remains preserved. The
[calculation sequence](docs/superpowers/specs/2026-09-13-calculation-sequence-design.md)
records the steps, remaining detailed rules, proposed checked examples and roles
for all nine supplied dataset candidates. This is planning only: calculation code,
training, data/model admission and formal acceptance are not newly completed.
Next action: record the pending answer to the concrete remaining rules/examples,
then implement the first source-bound duct observation and calculation slice.
Automatic duct paths are not yet produced by the existing positioned-text or
four-field box-observation adapters; add an explicit versioned observation path
while preserving their current contracts. Dataset source/codebook and overlap
audits remain parallel preparation; no export archive or model has been acquired.

Latest build checkpoint (2026-09-11, RECONCILIATION-1): persistent bidirectional
schedule/plan correspondence is implemented and connected to the Equipment screen.
Unique exact normalized schedule/plan pairs match automatically. Missing sides,
repeated references, conflicting schedule fields, unfamiliar tag identities and
explicit logical-sheet revision conflicts remain visible. Original fields, source
boxes, page geometry, reader/code identities and generation history are retained.
References and declared schedule quantities do not establish physical counts.

Exception decisions select a schedule and corresponding plan references, with an
explicit disposition/reason for every omitted endpoint. Decisions are source-bound,
append-only and withdrawable. Changed dependent evidence reopens the affected
correspondence; unrelated groups remain current. Newly included unread pages remain
coverage gaps. Resolved duplicate warnings retain their source/history but stop
blocking the matching workflow. Clean matches also offer optional correction.
Draft exports include complete graphs/events plus both edge and unmatched-group CSVs.

A concrete older equipment-adapter bug is also fixed: reuse now binds the exact
saved reading result, not only its input fingerprint. A retry with changed actual
schedule values creates a new result. Old equipment records become stale; current
quantities show UNKNOWN while original counts remain separately preserved in the
saved history and equipment-history.csv.

Verification: all 354 affected non-TCP cases pass on Python 3.14.6 and 3.9.6;
Python 3.9 grammar, JavaScript syntax, focused local DOM and whitespace checks pass.
Real Foundation/Poppler integration passes four grouped checks on an original
three-page PDF: exact fields/references; persistent bidirectional graph and explicit
duplicate/conflict decisions; affected-only assignment changes; complete reopen and
JSON/CSV export without rereading. Receipt:
.heleos/schedule-reconciliation-acceptance-2026-09-11/reconciliation-acceptance.json.
This is original synthetic integration evidence, not measured job/AI accuracy.
Exact commands, 67 monitored paths, worker reports and final handoff are in
.heleos/schedule-reconciliation-build-2026-09-11. All workers and command sessions
are terminal. Changes remain locally integrated and uncommitted on main
HEAD15d3a0269904ffa0f7da905debc59a6a61c47fef.

Next product dependency: implement duct lengths by size as the first airside class
from the original 0.5 roadmap, connecting recognition evidence to deterministic
quantities and current reconciliation identities. The 2026-09-13 planning checkpoint
above records the owner choices; the detailed rule/fixture packet must be finalized
before implementation. Preserve the full product goal. Later work
remains correction/recalculation, formula Excel/evidence PDF, mechanical-class
expansion and native Mac/Windows product parity. Formal 0.2-0.4 acceptance remains
open, including real model/data admission and predecessor gates. Live local model
execution remains unproven after the recorded connection denial; browser and the
prior 15 denied TCP-bind cases remain unverified. No prohibited retry, private
egress, model download, engineering-rule promotion, extra reviewer round, Git
mutation or release action occurred. This section owns the next action.

Prior build checkpoint (2026-09-11, SHEET-SCALE-1): shared sheet coordinates and
scale lifecycle are implemented and connected to the drawing workspace. Full
Foundation crop/rotation/transform/source identities reach the measurement code.
Decimal distances retain original PDF points and exact scale-decision bindings.
The saved document reader extracts explicit metric/imperial scale labels and NTS
as unverified candidates. Assigning a view and verifying its scale are explicit;
overlapping active detail scales, unavailable geometry and unverified/retired
facts block dependent measurements. Withdrawal makes affected current item
quantities unknown. Explicit recalculation creates a replacement and relinks
affected items while preserving original measurements, scale decisions and history.
Other page/view measurements remain current. UI and CSV distinguish current,
blocked and superseded results; sheet-scales.json retains complete original evidence.

Native integration exposed and corrected a pre-existing rotated-text defect:
Poppler bbox headers describe the unrotated crop, but word positions already use
displayed coordinates. Both document reading and the legacy equipment reader now
use the shared Foundation-bound normalization. Document-reading-3 freezes full
coordinates and rechecks them around extraction. Old unbound Poppler readings are
preserved and shown as stale; they are not silently reused as current evidence.

Final evidence: 333 affected non-TCP cases pass on Python 3.14.6 and 3.9.6; grammar,
JavaScript syntax, local DOM behavior and whitespace checks pass. A real native
Foundation/Poppler run passes four grouped checks on an original two-page cropped
and rotated PDF: matrices/corners/rendering, saved label reading and independently
expected distances, affected-only withdrawal/recalculation, full reopen and exports
without rereading. Receipt:
.heleos/sheet-scale-acceptance-2026-09-11-attempt-2/sheet-scale-acceptance.json.
The first failed attempt is retained with its exact rotated-text diagnostic. Task
reports, commands, final identities and handoff are in
.heleos/sheet-scale-build-2026-09-11. All coding workers and command sessions are
terminal. Local edits remain uncommitted on main HEAD15d3a0269904ffa0f7da905debc59a6a61c47fef.

Next concrete coding action: complete the original 0.4 bidirectional schedule/plan
reconciliation using the current sheet contract and existing schedule extraction.
Persist source-linked correspondence and missing/duplicate/revision conflicts,
then feed their resolved identities into the first owner-selected takeoff class.
The first-class choice remains pending. Full mechanical calculations, tracing,
formula Excel/evidence PDF, native Mac/Windows product parity and complete takeoff
remain unfinished. This code checkpoint does not accept all of 0.2/0.3 or promote
engineering rules or AI models. Live local model inference is still unproven after
the session's connection denial; no retry or alternate route was attempted.
Browser behavior and the prior 15 denied TCP-bind cases remain unverified. No
private-data egress, model/download, extra reviewer, Git mutation or release action
occurred. This section owns the next action; older checkpoints below are historical.

Prior build checkpoint (2026-09-11, MODEL-INFERENCE-1): the local model connection
is implemented in the drawing workspace. Frozen rendered PNGs retain original
revision/page and renderer identity. The all-mechanical adapter returns bounded
image observations with the complete response. Explicit jobs save page results,
resume without repeating completed images, and generate baseline records directly.
The interface supports prepare, identity, start, cancel and resume; opening/viewing
saved work never starts inference. Exports retain inputs and all successful and
failed responses. Unknown memory stays unknown and cannot pass resource thresholds.

Final assembled evidence: 271 affected non-TCP cases pass on Python 3.14.6 and
3.9.6; Python 3.9 grammar, JavaScript syntax, local DOM behavior and whitespace
checks pass. Real Foundation/Poppler integration passes with an original synthetic
injected adapter: frozen PNG lineage, generated baseline, full reopen and complete
response export. Receipt:
.heleos/model-inference-acceptance-2026-09-11/model-inference-acceptance.json.
This proves the implemented connection, not AI accuracy. This session's loopback
runtime connection was denied with PermissionError errno1; no model ran and no
alternate connection was attempted. Native Windows, browser and the previously
denied 15 TCP-bind cases remain unverified. No download, private egress, real rule
approval, Git mutation, extra review round or release action occurred.

MODEL-INFERENCE-1 workers and command sessions are terminal. Exact code identities,
commands, failed/corrected attempts and handoff are in
.heleos/model-inference-build-2026-09-11. Next concrete coding action: implement
revision-bound sheet coordinates and verified scale lifecycle, including rotated
pages, detail views, explicit scale decisions and invalidation of dependent
measurements. Then continue schedule reconciliation, the owner-selected first
class, correction/recalculation, formula Excel/evidence PDF, full mechanical
expansion and native Mac/Windows parity. The first-class question remains pending;
it does not block scale implementation. Full goal remains active; 0.2/0.3 and
complete takeoff acceptance are not claimed. Older next actions below are historical.

Prior build checkpoint (2026-09-11, MODEL-BASELINE-1): the mechanical model/data
baseline is implemented and connected to the local drawing workspace. Immutable
datasets bind actual source snapshot hashes, originating job groups, independent
expected objects and train/validation/test splits. Model declarations retain source
rights statements, artifact digests, runtime and preprocessing. Frozen plans bind
those records and declared metrics before importing predictions. The server scores
one-to-one matches, misses and extras; duplicate detections and negative pages
count, absent categories retain unknown ratios, and original supplied labels stay
unchanged. All nine mechanical categories remain visible in coverage. Source
lifecycle and scorer changes preserve history and block reuse of stale plans.

The advanced workspace panel registers local source evidence, datasets, model
declarations, plans and saved outputs. Reopen and draft exports preserve complete
records in model-baseline.json and model-evaluations.csv. These are local research
records; they neither complete takeoff stages nor select/promote a production
model. Artifact declarations and supplied timings are explicitly distinct from
observed model execution and hardware performance. Source rights remain recorded
provenance statements, not a permission adjudication. The document-page list
helper collision that could prevent requirement rendering was also corrected.

Fresh evidence: 227 affected non-TCP application cases pass on Python 3.14.6 and
Python 3.9.6. JavaScript syntax, local DOM-stub behavior, Python 3.9 grammar and
whitespace checks pass. Seven real Foundation/Poppler checks pass against the
original synthetic PDF, with independent literal tag rectangles, actual saved
reader predictions, exact replay, full reopen without another extraction and
JSON/CSV export. Receipt:
.heleos/model-baseline-acceptance-2026-09-11-attempt-2/model-baseline-acceptance.json.
The first acceptance-script initialization failure and a fixed label-preservation
failure remain recorded in .heleos/model-baseline-build-2026-09-11/progress.md.
All workers and command sessions are terminal.

Read-only local inspection also verified qwen3.5:9b's installed manifest and all
four artifact digests, including the 6,594,462,816-byte model, plus the Ollama
executable and bundle metadata. This confirms local files, not vision capability,
model accuracy or runtime readiness. Exact evidence is in
.heleos/model-baseline-build-2026-09-11/local-model-artifact-verification.json.

Next concrete coding action: bind actual inference from the existing local vision
adapter to frozen model/dataset records and original rendered page inputs, using
the already installed candidate after its local capability and use basis are
established. Then continue verified sheet/scale, schedule reconciliation, the
owner-selected first airside class, corrections/recalculation and formula Excel /
evidence PDF outputs, full mechanical expansion and native Mac/Windows parity.
The first-class choice remains pending; independent baseline integration continues.
The full product goal remains active; this does not accept all of 0.2 or establish
complete takeoff. Actual AI inference, private-job accuracy, browser behavior,
15 previously denied TCP-bind cases and native Windows parity remain unverified.
No network/model call, download, extra reviewer round, Git mutation, private egress,
real engineering approval or release action occurred in this task.

Prior build checkpoint (2026-09-11, KNOWLEDGE-4): versioned requirement-rule
validation and admission are implemented in the local drawing workspace.
Independently specified cases are executed against the current compiler/matcher;
inputs, expected/actual results, source lifecycle pins, exact code identities and
disable-rule rollback behavior stay in an immutable version record. Passing checks
do not activate it. Separate review and explicit approval/withdrawal events retain
actor, reason and parent history. New draft versions and reviews preserve an
existing active version until explicit replacement or withdrawal. Changed sources
or implementation disable its effective approval; source reactivation cannot
resurrect it. Previous-reading versions remain inspectable and exportable. Original
project clauses and item decisions stay separate and unchanged. Admission covers
requirement applicability only; quantity rules still require their own calculation
implementation and validation.

Fresh evidence: 174 affected non-TCP application cases pass on Python 3.14.6 and
3.9.6. JavaScript syntax, Python 3.9 grammar and whitespace checks pass. Ten real
Foundation/Poppler checks pass on an original synthetic three-page PDF: candidate
reading, fixed tag cases, distinct synthetic author/reviewer/owner events, saved
approval after full workspace reopen, withdrawal without automatic rollback, and
rule-admission.json/rule-versions.csv export. Receipt:
.heleos/knowledge-rule-acceptance-2026-09-11/rule-admission-acceptance.json.
The task ledger, terminal reports and exact identities are in
.heleos/knowledge-rule-build-2026-09-11. All workers and command sessions are terminal.
Actor names are local audit labels, not authenticated identity proof; no real
project engineering rule was reviewed or approved in these checks.

The full takeoff product goal remains active. Next coding task: mechanical
model/dataset manifests and a reproducible evaluation baseline binding source
rights, data splits, pinned assets/runtime and declared result metrics. Reuse the
existing source registry and document-reader components; coding-worker benchmarks
do not establish mechanical model performance. Then continue verified sheet/scale,
schedule reconciliation, the selected airside class, correction/recalculation and
formula Excel/evidence PDF outputs, followed by full mechanical expansion and
native parity. The first airside-class choice remains pending and does not block
this knowledge work. This checkpoint does not accept all of 0.2 or complete takeoff.
Browser access and 15 TCP-bind cases remain unverified after their earlier denials;
neither was retried. Local model execution, private-job accuracy and native Windows
parity remain open. No Git metadata write, publication, private-source transfer,
dependency/model installation, extra reviewer round or release action occurred.
Preserve the entire draft workspace including mechanical-knowledge; the Foundation
release gates remain deferred.

Owner correction (2026-09-10): Heleos performs the takeoffs. Rereading the approved
design, original roadmaps and owner manual confirms that development automation
is supporting work. Codex's recent recognition-first proposal skipped the explicit
Division 23 knowledge prerequisite and is now marked superseded in the existing
[engine implementation ledger](docs/superpowers/plans/2026-09-10-equipment-workflow.md).
The original product order remains knowledge/vocabulary/rules and model baseline,
verified sheet/scale, schedule reconciliation, a selected airside takeoff class,
then correction/recalculation and live-formula Excel/evidence PDF. The first 0.2
knowledge component has since been implemented as recorded above. Continue its
unfinished dependencies without restarting completed work or dispatching the
superseded recognition proposal. Full mechanical scope and Mac/Windows parity remain.
The source-reading correction itself changed no implementation or prior check result.

Latest product work (2026-09-10): the actual document-reading prerequisite is now implemented and connected in visible main, based on 15d3a0269904ffa0f7da905debc59a6a61c47fef. The local reader preserves positioned PDF text, extracts explicit equipment schedule fields and written mechanical requirements, matches equipment tags to plan references, and feeds saved equipment review records and exports. Equipment count review consumes these document outputs instead of rescanning text, and refuses stale or missing document readings. Source locations, original wording, unknown quantities and disagreements remain visible. These are uncommitted local product edits; the existing seven-stage interface is supporting infrastructure, not completion of the automated takeoff engine. Automatic symbol recognition, system tracing, full quantities and estimating remain unfinished. Mac and Windows remain the target; native Windows execution is not claimed.

Updated: 2026-09-10. This snapshot records local integration through Task 9, a passing local Task 10 supply-chain implementation, the tested native-suite receipt candidate, the completed cross-platform agent-controller build through exact clean commit `f6f1bc82c2ba6f6a2fe552d9bd3adb5dc4e0c950`, visible-main public research and benchmark integration through `0dd2f77c8ecf722c4f1ac57e94acef7b9653a292`, the completed Foundation release-status implementation checkpoint `885f8d8c713773a29fbc27ee1f13d829d5d022a3`, the completed GitHub App owner-decision applier checkpoint `738aca452332e4495066f9cba2e46b6aaef6f8bf`, the completed unresolved-draft generator checkpoint `5fac3b54457d832614b837365b9c3adc7d26fcdb`, the completed deterministic native-evidence return exporter through implementation correction `e6fbfb539f5bc6e725825936e7aa5bc16e0a7ade`, and the completed Foundation native-return binding verifier through implementation checkpoint `a2a0c44404cad97eb186043d6ff7f8ff92b59378`. Native-Windows launchers, GitHub App inventory, guarded-provider evidence, terminal Cursor quota evidence, and terminal Athena quarantine/adjudication remain recorded; no owner App decision, new provider benchmark run, Cursor live write, native Windows result, completed Foundation 0.2 milestone, or production-release acceptance is claimed.

<!-- active-build-authority:v1 -->
```json
{"schema_version":1,"active_builds":[]}
```
<!-- /active-build-authority -->

## Document-reading implementation checkpoint - 2026-09-10

The owner corrected the previous interpretation of workflow-first: the actual
reading and mechanical interpretation code must precede trials, not merely stage
screens and manual records. That correction now governs the next build order.
New scripts/document_layout.py, document_schedule.py, document_requirements.py
and document_pipeline.py read accepted PDFs with the existing local Poppler
reader. Explicit schedule cells retain raw fields and locations; written
requirements retain negation, conditions, sections and source lines. Plan links
include header-declared equipment prefixes and preserve leading-zero identities.
The persisted document reading feeds the equipment review engine and supplies
schedule, requirements and equipment-register CSVs in the project ZIP. A reviewed
physical count that disagrees with an explicit schedule quantity raises an issue.

Verification is now against that implemented code: 27 document/parser cases and
22 equipment/workflow cases passed on both Python 3.14.6 and Python 3.9.6.
Both affected client scripts pass Node syntax checks. A real Foundation/Poppler
run read an original synthetic three-page PDF, populated a schedule row and two
written requirements, matched plan tags, built equipment review records and
exported the results. Its terminal PASS receipt is
.heleos/document-reading-acceptance-2026-09-10/document-reading-acceptance.json.
This proves the local code path, not real-job accuracy, live model behavior,
browser interaction, native Windows parity or release acceptance.

Reader limits are explicit: embedded text and bounded horizontal table/prose
layouts; scans, merged/staggered tables and cross-page interpretation remain
unresolved. It does not infer geometry, accessories, physical quantities,
contractual precedence or engineering rules. New native-acceptance processes
and both coding workers are terminal. No installation, external data transfer,
push, release action or production PDF intake change occurred.

The earlier recognition-next instruction is superseded by the owner correction
at the top of this status. Recognition remains required product work, after its
knowledge, coordinate/scale and schedule prerequisites are addressed for the
supported scope. Preserve this document-reading layer and the full mechanical
ambition. The fuel diagnostic remains deferred; stage screens and repeated trials
must not replace the missing engine code.

## Earlier project takeoff workflow checkpoint - owner direction 2026-09-10

The owner explicitly directed building the workflow before further individual drawing trials. The PDF diagnostic was stopped after compiling a bounded phase probe; the probe was not executed and no guest, intake limit or original drawing changed. Its retained receipt identifies a fuel-budget exhaustion, not a classified corrupt-PDF defect. That investigation remains deferred.

New code in scripts/takeoff_workflow.py and apps/drawing-workspace/workflow.js supplies seven stages, saved page roles, equipment extraction from the document register, calibration within an explicitly chosen uniform-scale view, recorded length geometry, linked quantity calculations, records across all eight mechanical scope categories, scope review, explicit allowance review, unresolved questions and a ZIP containing actual saved records. Stage navigation never implies completion. All quantities remain draft; automatic symbol recognition, system tracing, full schedule fields, specification interpretation, pricing and labor are still to build.

Fresh checks: the 12 new workflow cases and 10 equipment cases pass on Python 3.14.6 and 3.9.6, including the connected project-to-export path, aspect-ratio-aware measurement, calibration boundaries, source mismatches, persistence, stale edits, immutable history, review invalidation, unknown counts, allowance corrections, and actual HTTP handlers over local IPC. All three client scripts pass syntax checks; HTML nesting and 78 static element IDs pass structural checks. These do not establish live browser behavior or real drawing accuracy. The previously confirmed session restrictions were not retried as a substitute for building the workflow.

The document-content and equipment-schedule reader named as the next item at this checkpoint is now implemented as described above. The [operator guide](docs/operations/drawing-workspace.md#project-takeoff-workflow) describes the connected steps; the existing [implementation ledger](docs/superpowers/plans/2026-09-10-equipment-workflow.md) and ignored handoff receipts own changed code identities and remaining limits.

## Earlier equipment draft implementation checkpoint - 2026-09-10

The owner's subsequent direction was to build the takeoff product, followed by an owner-supplied Division 23 workflow document. The current code applies its equipment-first register, duplicate handling, source records and unknown-versus-zero requirements. Full measurements, mechanical recognition, schedule field extraction, controls, accessories, specifications, revisions and estimating remain unfinished. The document is a functional reference, not a trained model or admitted engineering rule source.

The ten focused equipment checks pass on Python 3.14.6 and 3.9.6. Real Foundation intake, Poppler extraction/rendering, reconciliation, persisted review and CSV export passed together on synthetic inputs. HTTP handlers were checked over local IPC; these results do not establish live browser behavior, AI recognition, native Windows parity or release acceptance. Equipment drafts currently have separate persistence and must be preserved alongside the Foundation workspace when backing up.

Implementation is saved locally but is not committed, independently accepted or published. All processes started for this equipment checkpoint are terminal. The earlier drawing-only server was left untouched; its availability has not been rechecked and it does not serve these new bytes. Preserve the uncommitted files, the prior research report, and both ignored equipment workspaces. The [equipment ledger](docs/superpowers/plans/2026-09-10-equipment-workflow.md) records commands, evidence and limitations; the [operator guide](docs/operations/drawing-workspace.md#equipment-register) describes the new controls.

At this historical equipment checkpoint, committed-main build discovery reported uncommitted_authority because the updated status was not committed. The earlier session could not write Git metadata. Its direct verification identified main at 15d3a0269904ffa0f7da905debc59a6a61c47fef. Preserve the saved equipment ledger and changed-file receipt; verify the live HEAD and current status when resuming instead of treating this historical failure as current or restarting completed work.

The earlier intake-first next action is superseded by the owner's workflow-first instruction and current implementation checkpoint above. Existing Foundation release requirements remain deferred and unchanged.

## Completed drawing-only workspace - earlier owner direction 2026-09-10

The owner deferred GitHub App decisions, reaffirmed Mac and Windows, and directed advancing the product build. The first drawing-workspace slice is implementation finished, controller checked, committed, and locally integrated. It uses the existing native CLI with Python 3.9+ and local browser assets. No core, guest, dependency lock, frozen source closure, workflow, or production acceptance authority changed.

Final checks: 15/15 service tests pass under Python 3.14.6 and 3.9.6; all ten independent real-CLI acceptance checks pass with local Poppler rendering; JavaScript syntax, Python 3.9 parsing, and whitespace checks pass. Actual browser verification imported a real 21-page set, displayed two distinct pages, used fit and 125% zoom, and preserved page selection across refresh. No browser console errors were observed. Two other real sets were quarantined and exposed specific intake compatibility findings; they were not promoted or repaired. Scale and quantities remain unverified/unimplemented.

The [operator guide](docs/operations/drawing-workspace.md) contains Mac/Windows launch commands and the checked Mac renderer font setup. The [execution plan](docs/superpowers/plans/2026-09-10-drawing-workspace.md) records exact file identities, CLI artifact identity, commands, failures and limits. Data is retained under visible-main `.heleos/drawing-workspace`; originals remain in their owner-supplied location. Implementation and test workers are terminal. The product server is intentionally running from the preserved clean drawing-workspace worktree at the integrated implementation commit, using the saved data in visible main. Its per-run local URL is handed to the owner separately.

There is no active implementation worktree after this completed drawing-only slice. Preserve `.worktrees/drawing-workspace-2026-09-10`, branch `build/drawing-workspace-2026-09-10`, checkpoint `81ea09d3380b0002f322458abdf2bf8646c4e76c`; do not recreate completed work. The equipment work above is saved in visible main and supersedes the earlier viewer-only next-action scope. Intake compatibility findings still require synthetic reproduction before proposing corrections to any frozen guest source. The prior Foundation native-return binding verifier and return packager are also terminal checkpoints. The ignored Finder-visible `ACTIVE_BUILD` remains a legacy pointer, not recovery authority. GitHub App decisions and native Windows evidence remain deferred release requirements, not the next local product-development task.

## Local integration checkpoint - 2026-09-09

The histories were first reconciled in `/Users/bekim/Heleos-spark/.worktrees/agent-control-integration` on non-release branch `integration/agent-control-foundation-2026-09-09`. Merge commit `6d5bf4302ca0edf8cafe88085321fd97b5fe9e6d` has parents `origin/main` checkpoint `63ae39c3bc33dc8c8b4f05e5ff3dacbcb51d7184` and active-build checkpoint `f6f1bc82c2ba6f6a2fe552d9bd3adb5dc4e0c950`; documentation checkpoint `147970271f47bd5b2f3de42ec4ffb2d3613a4665` records that merge. History-preserving merge `14aa98ad84946694c1a1524b457566626df1f955` then incorporated visible-main checkpoint `2abff26eefa551d2db2bf776bf9152973bf6c242`. Native-receipt merge `2d6f11427ea311d56242e7591d1e610863b3eadb` subsequently joined visible-main checkpoint `9cca377a38a567100089510e617e5cec88526669` with receipt documentation child `a1357b357d071dced9bc8e70ec8bd66501ff2eb6`, preserving exact tested ancestor `58ab1e36c1f0cfccaf69c3a3c78b41495168fbbe`. These checkpoints supersede earlier integration/divergence descriptions retained below and preserve the active-build authority above; completed work must not be restarted after compaction. [docs/roadmap.md](docs/roadmap.md) is canonical; root [ROADMAP.md](ROADMAP.md) is historical.

Recorded local integration checks passed: the Python repository gate checked 252 files with 0 failures, all 101 Python tests passed, and Rust formatting, check, Clippy, and all product, worker, and storage suites passed. The single provenance entry-point remains intentionally fail-closed because inherited remote workflows lack admitted Foundation authority pending owner dispositions and supporting evidence for Azure Pipelines, AWS Connector for GitHub, Amazon Q Developer, and ECC Tools. The local suite results do not override that gate.

No push, release, or workflow change occurred at this checkpoint, and no native Windows/NTFS acceptance is claimed. Resolve the four GitHub App dispositions before any workflow change.

Foundation receipt checkpoint: the Task 10 native-suite receipt implementation and governed staleness repairs are committed through exact candidate `58ab1e36c1f0cfccaf69c3a3c78b41495168fbbe`, and that candidate passed the complete native macOS ARM64 offline supply-chain gate. The deterministic complete-history Windows transfer package for that exact candidate is built and byte-verified. Future: execute the admitted seven-suite matrix from that package on native Windows/NTFS, then satisfy the remaining workflow, publication, CI, and acceptance gates. No Foundation acceptance is claimed.

Anti-compaction routing: use the committed active-build authority above for agent-controller work. For Foundation native-suite evidence, preserve `/Users/bekim/Heleos-spark/.worktrees/foundation-native-suite-receipt`, branch `build/foundation-native-suite-receipt`, based on `af526a3c9c7ad93f360b6629e9b592a81787b341`, with exact macOS-tested candidate `58ab1e36c1f0cfccaf69c3a3c78b41495168fbbe`. Reconcile live branch, HEAD, dirty paths, and this checkpoint before resuming. Do not restart completed work, mistake a later documentation child for the tested candidate, or infer native execution from synthetic transcript tests.

## Prior integrated local implementation

Tasks 7-9 are complete, committed, converged, and locally integrated. Task 10's local supply-chain implementation is committed as `6157458c6566d8ad26a2ec2c6ba1c7d8a697e2b6`, parent `c1596c4cc536155ad3a052cfec2c9b8951814aba`, tree `c4539ca7e4ecf62e0e767684c314dd56e4527a0c`, subject `chore: implement local Foundation supply-chain gate`, on branch `build/foundation-0.1-release-gate`. The corrected full native macOS ARM64 gate ended with `SUPPLY_CHAIN_LOCAL_PASS`: the approved PDF guest, provenance, complete 405-package/1,035-edge graph, cargo-deny, cargo-audit, semantic secret scan, Clippy, builds, focused and whole-workspace tests, network-denied verifier suite, and two-root SBOM regeneration all passed. No workflow, release-candidate SHA, push, CI attestation, native Windows/NTFS acceptance, or Foundation completion is claimed.

## Deferred Foundation release finish line

The complete local histories and executable tree are landed in visible local `main`; do not repeat that landing after compaction. Two independent release actions remain: copy `/Users/bekim/Heleos-spark/WINDOWS_NATIVE_HANDOFF_58ab1e3` to a native Windows x64 local fixed NTFS volume and run its exact importer command, and obtain explicit owner dispositions with installation evidence for the four GitHub Apps before any workflow write. Neither gate prevents safe local implementation. The isolated App verifier and public inventory below are completed checkpoints, not owner decisions or Foundation integration. Preserve the completed one-time lock normalization, all three byte-frozen PDF source-closure manifests, the admitted SBOM, and Tasks 7-9 evidence. The owner ordered no further review rounds or reviewer dispatches; deterministic checks, exact-byte controls, and causal failure evidence remain required.

## Foundation release-status guardrail checkpoint

Implementation commit `885f8d8c713773a29fbc27ee1f13d829d5d022a3` on `build/foundation-release-status-2026-09-09` adds the executable read-only `scripts/foundation-release-status.py`, strict `governance/foundation-release-evidence.schema.json`, 25 black-box continuity cases, the operator guide, and top-level workflow links. The command resolves the unique visible-main checkout from any registered worktree, validates committed GitHub App state, independently verifies the ignored Foundation Windows transfer package, binds a future release ledger to exact candidate/workflow/platform identities, and refuses premature or mismatched acceptance dossiers. It never writes Git or repository files, fetches evidence links, publishes a workflow, pushes, runs CI, or grants release authority.

Controller verification passed all 25 focused cases under Python 3.14.6 and system Python 3.9.6, the complete 48-test continuity suite under both runtimes, all 89 research tests, JSON parsing, and staged whitespace checks. The current real-repository inspection is deterministically `BLOCKED`; the native transfer verifies as `PASS` with `native_evidence=false`, and the first unfinished action is the four owner GitHub App dispositions. `--require-ready` exits `2` for that expected blocked state instead of misreporting a code failure or release pass.

This implementation is complete and must not be recreated after compaction. No unfinished build is declared in the authority block because its code/test checkpoint is terminal and has been fast-forwarded into local `main` through documentation checkpoint `4c47089ef7ecd68d921e6434766a3be35473a826`; the preserved worktree is inspection history, not a second source of truth. Resume by running `python3 scripts/foundation-release-status.py --human` from visible local `main`, then act only on the first reported gate or continue an independently authorized local engineering slice.

## GitHub App owner-decision applier checkpoint

Implementation commit `738aca452332e4495066f9cba2e46b6aaef6f8bf` on `build/github-app-decisions-2026-09-09` adds the executable `scripts/apply-github-app-decisions.py`, strict `governance/github-app-decisions.schema.json`, 35 black-box continuity cases, the operator guide, and top-level workflow links. The command defaults to a no-write dry run, binds an owner packet to the exact visible-main HEAD and committed registry SHA-256, rejects stale or dirty state, and uses an atomic one-file replacement only after explicit `--apply`. It cannot change GitHub account installations, follow evidence references, mutate workflows, commit, push, or grant release acceptance.

Controller verification passed all 35 focused cases under Python 3.14.6 and system Python 3.9.6, the complete 83-test continuity suite under both runtimes, all 89 research tests, JSON parsing, Python AST parsing, stable implementation/test hashes, and staged whitespace checks. The atomic-write suite verifies a replaced inode, unchanged unrelated files and Git state, exact rerun idempotence, unique visible-main routing, secret-safe failures, strict packet semantics, and fail-closed Git override handling.

This implementation is complete and must not be recreated after compaction. The four live registry entries remain `owner_decision_required`; no decision packet was invented or applied, and no GitHub account, network, workflow, or remote Git action occurred. The authority block is empty because the code/test checkpoint is terminal. After this branch is fast-forwarded into visible local `main`, resume at the current release-status first action: obtain the owner's exact four dispositions and real decision and installation evidence, dry-run the bound packet, and apply only that evidenced local registry update.

## GitHub App unresolved-draft generator checkpoint

Implementation commit `5fac3b54457d832614b837365b9c3adc7d26fcdb` on `build/github-app-decision-draft-2026-09-09` adds the executable `scripts/prepare-github-app-decisions.py`, 17 black-box continuity cases, a visible tracked `OWNER_ACTION_REQUIRED` entry point, and exact operator documentation. The command prepares one ignored, private-mode JSON draft bound to the unique visible-main HEAD and committed GitHub App registry hash. It derives only the canonical App names and leaves the owner date plus seven decision/evidence fields per App as exactly 29 JSON `null` values.

Controller verification passed all 17 focused cases and the complete 100-test continuity suite under Python 3.14.6 and system Python 3.9.6, all 89 research tests, Python AST parsing, JSON parsing, implementation/test hash checks, and staged whitespace checks. The race and filesystem cases verify same-directory fsync, atomic no-replace publication, private mode, exact inode cleanup, unique visible-main routing, source/destination drift rejection, and preservation of unrelated tracked and untracked state. The implementation SHA-256 is `a8a0696d15d7cafec1cb759674b0d32b9359fff96990eaa886c33e17c650171f`; its test SHA-256 is `3ebe64fe099c8293360544e8da99843db3aeeaedecda7f9590903a0e6c56f0fd`.

This implementation is complete and must not be recreated after compaction. It made no owner decision, evidence determination, GitHub account, network, workflow, registry, or remote Git change and grants no release authority. The active-build authority is empty because the tested code checkpoint is terminal. After local-main integration, generate a fresh draft from the final main commit, have Bekim Bukolla supply all 29 actual values and supporting evidence, then use the existing applier dry run before any separately authorized local registry update.

## Foundation native-evidence return exporter checkpoint

The deterministic POSIX return packager is complete through implementation commit `e897b81` and bounded-input correction `e6fbfb539f5bc6e725825936e7aa5bc16e0a7ade`; documentation is committed as `2d67dc1`. Causal and expanded acceptance tests are recorded in `a83c974`, `30ecd20`, `9ba4520`, and `c407c22`. The new `scripts/export-foundation-native-evidence.py` validates exact returned summary, receipt, manifest, transcript, and local candidate identities; preserves original bytes in a deterministic 18-member USTAR archive; publishes without clobbering; and reports only false authority flags. It makes no network, Cargo, workflow, GitHub account, remote Git, or release-state change.

Final verification passed 41/41 focused exporter cases under Python 3.14.6 and system Python 3.9.6, 141/141 complete continuity cases under each interpreter, 89/89 research cases under Python 3.14.6, and 7/7 Rust native-receipt compatibility cases. The additional system-Python research run intentionally stopped at the existing `validate-sources.py` Python 3.11+ runtime guard with `python_3_11_required`; the exporter and the full continuity suite pass on system Python 3.9.6. Final SHA-256 identities are exporter `2d89e947e11811984e791c5410b1fbca34b495b97ad2586192789540bbf0be36`, acceptance tests `35beb8394bc54a36a39f242a8b5c43281f6abd16fb3fe10494b310a66efddb4f`, and operator guide `e9e4ce60ec3619aa3a6b7624d35720ba83cb2e1a7c8ef97776f2a42f376f8577`; all are tracked mode `100644`.

This implementation is complete and must not be recreated after compaction. No archive was generated because genuine returned native Windows inputs do not yet exist. The next concrete action for this path is to run the already prepared candidate on native Windows x64/NTFS, return its original evidence files, and invoke this exporter on those bytes. Synthetic fixtures, local macOS checks, and the archive structure do not establish native execution, owner approval, CI authority, Foundation acceptance, or release approval.

## Foundation native-return binding checkpoint

Planning checkpoint `8bbc3c1e531b2aaa93e68734b374292eecd19690` freezes the standalone verifier contract and implementation sequence. The causal test-only checkpoint `20e337b181a197a718f5f70cb962b171a59d208` failed for the expected reason: its real synthetic Git repository, ignored Foundation handoff, bundle, checksums, committed importer, and seven-suite returned summary were created successfully, but the absent `scripts/verify-foundation-native-return-binding.py` made the subprocess exit `2`. Implementation checkpoint `a2a0c44404cad97eb186043d6ff7f8ff92b59378` adds that read-only POSIX verifier and expands the test contract to 50 cases.

The verifier resolves the unique registered visible-main checkout, uses bounded and sanitized read-only Git subprocesses, pins and rechecks protected source descriptors, reuses the established Foundation transfer semantics, strictly validates the returned importer summary and embedded native-suite receipt, and compares the candidate plus actual outbound manifest and bundle digests. It reports the nested transcript-manifest digest separately and never treats it as the outbound manifest. PASS binds those local identities only; every native authentication, independent execution, CI, owner-approval, and release-approval field remains false. The command does not inspect transcript bytes, execute Cargo or the native gate, write files, fetch, push, change a workflow or ledger, or grant Foundation acceptance.

Controller GREEN passed 50/50 focused cases under Python 3.14.6 in 76.632 seconds and Python 3.9.6 in 76.807 seconds. The complete continuity suite passed 191/191 under Python 3.14.6 in 507.293 seconds and Python 3.9.6 in 494.461 seconds. Python 3.9 AST grammar and whitespace checks passed. Final implementation SHA-256 is `6ae998691df98bd3f3f821a04aa74efbdc8a43e7cf1427bfd91430a954146590`; test SHA-256 is `b8dba3d309028b2505a46d94d015166d73660ea4065003c5985a428b73a0b354`; both are tracked mode `100644`.

This implementation is complete and must not be recreated after compaction. It produced no native evidence because no genuine returned Windows summary exists yet. Run the frozen candidate on native Windows x64/NTFS, preserve the original standalone final summary, then run this binding verifier against that summary and the original outbound handoff before invoking the existing return exporter. GitHub App owner dispositions, workflow publication, owner-authorized remote Git handoff, exact-SHA CI, and final owner acceptance remain independent open gates.

## Public research and benchmark implementation checkpoint

Visible local `main` now contains the Research Task 1 contracts, strict jq/TOML validation, provider/egress policy, and four public-source lane manifests through `ad73e5fa07934d70c5e73c273e5936aa964051dc`. The four lane manifests contain 54 unique research-only source candidates and validate together without an aggregate or production authority. Their source bytes, citations, rights, and admission remain unverified; Task 6 independent source review is open under the owner's no-reviewer-agent direction.

Branch `build/agent-benchmarks-2026-09-09` in `.worktrees/agent-benchmarks-2026-09-09` advances that exact visible-main base through checkpoint `787c723bee4543e0198f92c69e76e704ba897a1e`. It adds the stdout-only deterministic aggregate-candidate builder, five frozen PUBLIC synthetic CLI cases, five truthful `sandbox_unavailable` CLI packets, the routing report, the official GrokBot discovery/contract/evaluation artifact set, and 29 new deterministic tests. The five CLI packets bind exact case-set digest `524f1f0562ff1844c4082f91721e0fe7bcf644edaff51e9aa91d4b61a8fed8f7`; no case or provider was launched and every capability score is zero. The Grok browser packet is disabled with no app interaction; the terminal ATHENA task was not repeated.

Controller fan-in passed all 89 research tests, all new focused tests under both Python 3.14 and system Python 3.9, Python compilation, benchmark and packet JSON parsing, SourceEntryV1 validation, direct jq packet validation, and whitespace checks. The source-aggregate builder writes only to stdout and cannot modify `governance/sources.toml`; no workflow, account, credential, provider, remote Git, source admission, production data, or release state changed.

After this branch is locally integrated, resume at the first genuinely unfinished safe action: keep Foundation native Windows/NTFS and GitHub App owner dispositions as independent release gates; preserve Task 6/8/9 independent-review checkboxes as open rather than rerunning completed implementation; and continue only work that does not start Foundation 0.2 or fabricate provider/native evidence. Remote push remains separately unauthorized.

## Completed isolated agent-controller build

Branch `build/agent-control-foundation` in `.worktrees/agent-control-foundation` contains executable checkpoint `02c61d17c8746de31acf7f397ac8c83a9614a184`, tree `931e4863383cccb38c240eae287fde4cd56bd01d`, followed only by documentation continuity. Resolve the live exact branch HEAD through the visible-main authority block and `scripts/active-build-status.py`; do not encode this file's own commit as a self-reference. The branch contains the deterministic worker protocol, guarded Claude/Kimi/Grok runner, macOS containment commit `28daf4c0f213540a05f1928f4936ee641250686e`, Kimi adapter repair `df0233b6812d1f46de3a714ec1a647f7a245a81f`, Windows containment implementation `5f3619158ffb56e0df61a68bebd267de468b2f61`, and native-gate commit `e190567eae05ec876049508ff35eeef28ec92320`. Grok admission is committed as `56758dd`, parallel temporary-inventory test isolation as `9a62481`, and accepted live Grok fixture/evidence as `fb18a39`. The Windows code creates verified NTFS write roots, a restricted token, explicit inherited handles, and a kill-on-close Job Object; the runner accepts only `windows_restricted_token_job` on Windows and has no uncontained fallback.

Controller checks passed: 12 Windows-platform portable tests and, after the Kimi capability addition, 46 worker-runner tests with zero failures or ignores; strict host and x86_64 MSVC Clippy/checks; full-workspace formatting; locked/offline metadata; staged whitespace; and normal provenance after rebuilding its unchanged exact-byte snapshot binary for the intentional Cargo manifest/lock change. The PowerShell gate parses and rejects this Mac before any command with `non_windows`, `native_evidence=false`, and all 13 required native test names. These results are compile/host evidence, not native Windows/NTFS acceptance.

The assembled full-workspace test exposed one real verifier regression: its release-scope sentinel assumed exactly seven total local packages and rejected the three new worker-tool packages. The test-first repair now classifies the exact seven Foundation packages and exact three tooling packages, fails closed on identity/version/substitution drift, and leaves the frozen secret-scan baseline byte-identical. All 27 verifier tests and the complete locked/offline host workspace suite now pass with the required prebuilt CLI and PDF guest, including Foundation acceptance, all seven hostile-intake cases, worker protocol/runner suites, and 12 Windows portable cases. The exact Rust workspace also closes under the MSVC target in the documented SDK-boundary probe; this remains compile evidence only.

Live contained launches are causally recorded in `crates/heleos-worker-runner/evidence/live-seatbelt-provider-runs.md`. After the earlier account-limit stop, Claude Code 2.1.261 completed one PUBLIC-only exact-scope write under `macos_seatbelt`; the runner inventoried exactly `tests/fixtures/runner/live/claude-seatbelt.txt`, and controller hash acceptance passed. Kimi exposed a real adapter defect, fixed in `df0233b`; its next launch reached Kimi 0.34 but its combined credential/runtime data root attempted a real-home storage write and was denied. Commit `ce49970` now probes the exact configured executable without launching it or reading a prompt, classifies the installed 0.34.0 bytes as a combined authentication/runtime root, and returns typed exit 78 before any unsupported worker-state-root use. The adapter passes 25/25 cases on Python 3.14 and 3.9, and the runner proves the provider and synthetic credential bytes remain untouched under Seatbelt. All processes are terminal; the successful Claude retained checkout contains exactly its accepted candidate.

Grok Build `0.2.111` is authenticated and has completed a PUBLIC-only macOS Seatbelt write. The first candidate failed the declared hash check and was rejected without integration; the second task produced exactly `tests/fixtures/runner/live/grok-seatbelt.txt`, 84 bytes with SHA-256 `fe8e15e6d8c4ded8a4e6bd29238a0a23130d1f3df200565b8a6b198738d983f4`, and passed controller acceptance. Both runs are terminal. [Live Grok evidence](crates/heleos-worker-runner/evidence/live-grok-run.md) records the task identities, retained runs, and exact acceptance results. The Grok adapter passes all 23 tests under Python 3.14.6 and 3.9.6. After `9a62481` isolated three shared-temp inventory assertions without weakening them, the full `./scripts/verify-foundation` gate exited `0`, including provenance `pass` over 172 files, the locked/offline workspace suite, reproducible PDF guest, and clean/offline acceptance rerun. These repairs and evidence are committed on this isolated branch.

Codex adapter and runner admission is committed as `ba33106`: all 14 Codex-adapter tests pass under Python 3.14.6 and 3.9.6, and the assembled runner passes 42 tests. The official Codex CLI was upgraded from `0.147.0` to exact `0.153.4` after the older version rejected `gpt-6-astra`. Three separately identified PUBLIC-only tasks are terminal: the first macOS Seatbelt task failed closed before the model because Codex runtime state required a denied write; the second explicitly selected runner containment `none` and authenticated on the old CLI but received the model/version HTTP 400; the third used `0.153.4` and succeeded once. It produced exactly `tests/fixtures/runner/live/codex-astra-runner.txt`, 81 bytes with SHA-256 `195846e4eab5d1882207d9e18512172c37254e6cfea9a2d3d97d63a2114340a0`; both controller acceptance checks passed and the completed handoff validates. [Live Codex evidence](crates/heleos-worker-runner/evidence/live-codex-run.md) records these outcomes. The successful run used runner containment `none` with Codex's own `workspace-write` mode: it provides no outer host/process/read/network/authority containment proof. The post-Codex `./scripts/verify-foundation` gate exited `0`, including provenance `pass` over 176 files, the complete locked/offline workspace suite, the reproducible two-root PDF guest, and the clean/offline acceptance rerun.

The next isolated finish line is the exact native Windows/NTFS execution of the frozen candidate through `scripts/run-windows-native-candidate.ps1`. It always invokes the existing importer with `-Full`, retains its output, and only emits native PASS after nested exact-commit/NTFS evidence agrees. Kimi remains write-disabled under Seatbelt because the reviewed installed build exposes no supported read-only-auth/writable-runtime separation; credentials must not be copied and the strict allowed roots must not be widened merely to force success. Cursor is authenticated and admitted, but no live write is accepted: API-model quota is exhausted until the reported September 14, 2026 reset, and composer-2.5 rejects required workspace exclusion. Do not retry before reset or an explicit owner spend-limit action; preserve tasks 001-005. Do not repeat the completed Claude, Grok, or Codex tasks, merge this branch into the Foundation 0.1 candidate before that candidate is accepted, or push it without a separate owner-authorized remote handoff. The engineering branch is integrated into visible local `main` through `14aa98ad84946694c1a1524b457566626df1f955`, but not into the Foundation 0.1 candidate; no remote push or deployment occurred, and Foundation 0.1 remains unaccepted.

Cursor implementation is committed through `02e5d2858c8d08d9d3e68741ee18037c515fcc77`, after admission `042c9c0`, runtime repair `7d31665`, config repair `f14d9b6`, and model selection `9659280`. The current adapter passes 23 tests on both Python runtimes, and the runner passes 44 host tests. All five PUBLIC-only Cursor attempts are terminal with clean retained checkouts; none yielded accepted output. [Live Cursor evidence](crates/heleos-worker-runner/evidence/live-cursor-run.md) records installation, authentication, all outcomes/hashes, controls, and the quota stop. The last attempt reached GPT-5.6 Terra 272K High under macOS Seatbelt before the usage-limit response. No further Cursor call is queued.

Native-Windows handoff automation began at `7e7d9e1bd62b540e8864f5e7d3474a3a75b74d8f` and now includes launcher commit `47861fa`. `scripts/package-windows-candidate.py` packages only an exact clean branch/commit into a deterministic full-history bundle, reconstructs it in a retained bare repository, runs strict object verification, and publishes its manifest last without overwriting prior evidence. `scripts/import-windows-native-candidate.ps1` requires independently supplied manifest and commit hashes, creates only a new local NTFS checkout, verifies the exact ref/HEAD and clean state, and runs preflight by default or the full native gate only with `-Full`. `scripts/run-windows-native-candidate.ps1` supplies the owner-facing one-command full run, requires a new retained run directory, and atomically writes machine-readable output without overwriting prior evidence. Controller checks passed 13 packager cases on both Python 3.14 and 3.9, 27 portable importer checks, 39 portable launcher checks, duplicate-PATH tool-resolution coverage, and both Mac fail-closed gate modes. These checks do not claim native Windows evidence.

The active-build checkpoint `f6f1bc82c2ba6f6a2fe552d9bd3adb5dc4e0c950` is packaged for native Windows as `WINDOWS_NATIVE_HANDOFF_f6f1bc8`. Its manifest SHA-256 is `ed867ce7119fc6d5f3b80a21e375d045a8a221529d4f22b4cebc0fd1fc0c8c2b`; its complete-history bundle is 4,817,728 bytes with SHA-256 `52a8923c746b1d9911130760c78bfc579a5fe42dea1d55e2e70bb773f63d0c77`. Explicit-branch reconstruction, strict object verification, exact clean HEAD, all five transfer-file hashes, and copied `git bundle verify` pass. This is a transfer candidate, not native Windows/NTFS evidence.

Active-build visibility was committed as `e1ad311` and anchored from visible local `main` by documentation commit `9747046ef7665bb7ad7f4a8e3da90db2721597f6`. `scripts/active-build-status.py` reads the current committed authority and reports no active builds when the list is empty; when entries exist, it validates their registered path, branch, checkpoint ancestry, live HEAD, dirtiness, and main divergence without mutating Git. `/Users/bekim/Heleos-spark/ACTIVE_BUILD` is now only a legacy ignored pointer to the completed worktree, not a duplicate source tree or current routing instruction.

## Preparatory source-curation checkpoint

Commit `02c61d17c8746de31acf7f397ac8c83a9614a184` adds a standard-library, offline `SourceEntryV1` metadata validator, 15 synthetic black-box tests, and an operator contract. It accepts only explicitly named `research_only` PUBLIC candidate manifests, hashes exact input bytes, rejects production/promotion fields and malformed rights/citation/applicability metadata, and always reports `production_authority=false`, `source_bytes_verified=false`, `citations_verified=false`, and `rights_verified=false`. It does not read source caches, write registries, fetch URLs, create a taxonomy, or begin Foundation 0.2.

Documentation-only descendants `f215be8c22e83f5783302fe9392070b0dbd95429` and `f6f1bc82c2ba6f6a2fe552d9bd3adb5dc4e0c950` preserve that implementation checkpoint without self-referencing their own moving HEAD; the latter is the recorded active-build checkpoint.

The 15 focused tests, 22 continuity tests, Python compilation, whitespace, and clean-checkout provenance over 193 tracked files passed. The complete `./scripts/verify-foundation` gate on the same code checkpoint exited `0`, including reproducible two-root PDF guest builds, the complete locked/offline workspace, and clean/network-denied acceptance, hostile-input, hostile-storage, and recovery reruns.

The owner-requested ATHENA task in the Grok Bot Mac application is terminal. Its two exact received artifacts, Markdown and JSON, remain ignored and immutable under `/Users/bekim/Heleos-spark/ATHENA_RESEARCH_QUARANTINE/2026-09-09-div23/`; controller primary-source validation found five current seed rows, one held division-date row, and five retired/superseded rows. The packet's May 2026 UFGS bundle reference was stale against the official August 2026 release. A separate six-source controller candidate passed the new metadata grammar with exact manifest SHA-256 `f97780a86717b9c9124f5ae4f8df987243cf7d099a73eab9835ac2abc91a95ba`, while all authority/verification flags remained false. CSI use is restrictive and UFGS redistribution remains unresolved, so none of these research bytes is admitted to Git, the source registry, rules, or production truth.

## Native-suite receipt checkpoint (2026-09-09)

Next platform action: run the admitted Windows entry point on native Windows/NTFS at exact candidate `58ab1e36c1f0cfccaf69c3a3c78b41495168fbbe` and retain its manifest, list/run transcripts, and canonical receipt. The receipt validator, synthetic tests, and complete macOS gate are finished local implementation work; native Windows execution remains pending. Explicit owner disposition for the four GitHub Apps gates workflow writes independently.

- Workspace: `/Users/bekim/Heleos-spark/.worktrees/foundation-native-suite-receipt`; branch: `build/foundation-native-suite-receipt`; base: `af526a3c9c7ad93f360b6629e9b592a81787b341`; exact tested implementation candidate: `58ab1e36c1f0cfccaf69c3a3c78b41495168fbbe`. Its three-commit implementation chain is `4fc8695` (native receipt), `5ae28c3` (first governed SBOM refresh), and `58ab1e3` (exact history-fixture adjudication plus final SBOM). This checkpoint supersedes the older next-action routing while retaining the completed evidence below.
- The prior Windows script required only seven zero-exit Cargo commands and emitted no machine-verifiable native-suite receipt. The new path captures list/run transcripts and exit codes for the exact ordered seven-suite matrix, rejects dirty candidates and HEAD drift, and validates the exact manifest identity, command arguments, and transcript basenames before receipt issuance.
- The `core-store` list and run must each contain exactly one `store::tests::windows_checked_close_releases_handles_in_three_real_rename_phases`. Each suite requires an equal, nonempty listed/passed test-name multiset; every harness list/result summary must agree with its records, with zero failures, ignores, benchmarks, or measured tests. SHA-256 binds the manifest and ordered transcript bytes into a JCS canonical JSON receipt for the candidate SHA, `windows-x86_64`, `NTFS`, and all seven suites. The new append-only verifier admission pins verifier/test source and both scripts; each entry point checks its own admitted bytes and exact clean-HEAD source identity.
- Controller-observed RED progression: initial dispatch rejected the unexpected `verify-native-suite` argument; the first implementation accepted a missing exact Store sentinel despite matching counts; `cargo +1.96.1 test --frozen -p heleos-verification --test native_suite_receipt native_suite_receipt_rejects_changes_to_exact_ordered_suite_matrix -- --exact --nocapture` exited `101` with 171 of 172 tampering cases accepted. The later full receipt regression run exited `101` with `3` passed and `4` failed, exposing two name-multiset cases, three failed/ignored-record cases, twelve forged/missing-summary cases, and missing receipt schema/digests. This is recorded execution history, not a claim of retained immutable log artifacts. The fixes are covered by the seven black-box receipt tests; synthetic Windows-labelled fixtures are not native execution evidence.
- Controller-observed local GREEN: after the exact 23rd history finding was classified as the immutable Windows forbidden-path fixture from `5f36191`, `cargo +1.96.1 test --locked --offline -p heleos-verification --test native_suite_receipt --bin verify-provenance` exited `0` with `7/7` receipt tests and `23/23` verifier tests. Strict package/all-target Clippy, formatting, and whitespace/diff checks passed. At exact clean candidate `58ab1e36c1f0cfccaf69c3a3c78b41495168fbbe`, `./scripts/verify-supply-chain` exited `0` with `SUPPLY_CHAIN_LOCAL_PASS`: it verified the 405-package/1,035-edge SBOM twice at SHA-256 `82e0ab177ab2ee13f3d40d14cdea6f0e5837d8f0e549e5383276d68ee66d3516`, rebuilt and verified the PDF guest twice, admitted exactly 23 redacted history findings and one current marker finding, checked all 1,242 pinned RustSec advisories over 405 dependencies, ran strict workspace Clippy, passed the full serialized workspace and network-denied verifier suites, and rechecked clean HEAD/source identity. This is complete native macOS local-candidate evidence, not native Windows or release acceptance.
- No native Windows/NTFS run, workflow write or GitHub App decision, remote push, or Foundation acceptance occurred in this slice. The native run remains next for the exact tested candidate; workflow/app, exact-SHA remote CI, and final acceptance remain open.

## Foundation native transfer checkpoint (2026-09-09)

- Commit `cbf936afd59e563e7e20f8a2ed446ea4047f6b13` on visible local `main` adds the fail-closed `foundation-supply-chain` packaging profile, trusted Foundation importer, portable tests, and execution plan. The original worker-containment package/import contract remains unchanged.
- TDD evidence is retained in the ignored SDD ledger: missing profile and missing importer both failed before implementation. Controller GREEN is 19/19 real isolated-Git packaging tests on Python 3.14 and 3.9, 62/62 portable Foundation importer checks, 27/27 unchanged worker importer checks, the complete 101/101 Python repository suite, Python compilation, PowerShell parsing, and whitespace checks.
- Exact release ref `release/foundation-0.1-native-58ab1e36c1f0cfccaf69c3a3c78b41495168fbbe` points to the clean tested candidate. Packaging reconstructed the complete history, passed `git fsck --strict`, and advertised exactly that one ref.
- Finder-visible ignored handoff `/Users/bekim/Heleos-spark/WINDOWS_NATIVE_HANDOFF_58ab1e3` contains the three-file candidate plus the separately trusted importer and operating instructions. Manifest SHA-256 is `2b84da624fbc0e8549d3bb009ed898dd454a58d7e6e68226a2ab14e9295778ff`; bundle size is 4,210,962 bytes with SHA-256 `401f2c67970177c76825f42b485601f06ee300becbb3c59d8ec6ef93ab9d4765`; importer SHA-256 is `4f0c71cc2e4b7676279e50bf2729258c832f97bffcd98f89948aa9cc1731f1bf`. The visible copies pass all four independent checksum checks and `git bundle verify` reports complete history.
- Packaging is not native evidence. The next action remains a real native Windows x64/NTFS run whose retained final importer summary and canonical receipt attest the exact candidate.

## Later human and platform gates

On `build/agent-control-foundation`, `faf8f53` adds validation for pending and owner-resolved retain/restrict/suspend/remove App dispositions and disposition-compatible egress; resolved states require substantive inventory/decision/installation evidence fields and a valid decision date, with reasoned inventory-unavailability exceptions only for suspend/remove. `337941b` records the public GET inventory observed on 2026-09-09; `cf265eb` preserves that App checkpoint, with documentation continuity through `f6f1bc8`. Public application metadata and historical activity do not prove current installation grants, repository scope, running version/digest, or actual egress; installation APIs returned 403/401. Preserve these completed checkpoints after compaction; neither changes an installation or grants release authority.

Azure Pipelines, AWS Connector for GitHub, Amazon Q Developer, and ECC Tools all remain `owner_decision_required` in `governance/github-apps.toml`, with egress `prohibited pending owner decision`. Next, obtain the owner's retain/restrict/suspend/remove choices and supporting decision and installation evidence; validate the resulting registry before any workflow write. Workflow writing and release remain blocked pending those choices and evidence; authorized independent local implementation continues. Push and remote CI need a separate owner-authorized Git handoff. macOS and native Windows/NTFS CI must attest the exact frozen candidate SHA before the documentation-only acceptance dossier or any Foundation 0.1 completion claim. No fetch, push, workflow publication, deployment, or account-level mutation occurred.

## Completed work now visible here

The owner requested that completed implementation be placed in the visible repository. The Task 10 implementation checkpoint is `6157458c6566d8ad26a2ec2c6ba1c7d8a697e2b6`, exactly 15 paths and 4,183 insertions, with this continuity update as its direct documentation-only child; both are the local fast-forward landing. Earlier local merge `dee9179ff99f78864248f62bceb80bd2c4e595cf` integrated the Task 7-9 chain, and continuity commit `c1596c4cc536155ad3a052cfec2c9b8951814aba` is the sole parent of the Task 10 implementation checkpoint.

The visible checkout therefore includes the Rust workspace, typed domain contracts, SQLite migrations/Store, immutable vault, PDF protocol/guest/sandbox, crash-safe intake, encrypted backup/verification/restore CLI, platform publication helper, independent storage and hostile-input verifiers, portable Foundation entry points, governance registries, and architecture/operations documents. The merge added 31 implementation paths and about 36,600 changed lines. This landing does not mean Foundation 0.1 release acceptance is complete.

Fresh post-merge checks passed here: locked/offline metadata, formatting, normal provenance, frozen verifier build, strict all-workspace/all-target/all-feature Clippy, the CLI build, and the frozen Task 9 black-box suites (`1/1` Foundation plus `7/7` hostile intake, zero failed or ignored). The first CLI attempt intentionally stopped while pinned tool installation was concurrently changing Cargo's seed cache; once installers were terminal, the exact isolated rerun passed. Both the causal stop and the passing rerun are preserved. These are local macOS results, not native Windows or final release acceptance.

## Work locations

Root agent instructions are available in [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), [KIMI.md](KIMI.md), [GROK.md](GROK.md), [CURSOR.md](CURSOR.md), and [GROKBOTS.md](GROKBOTS.md), with the workflow index in [SKILLS.md](SKILLS.md). These documents define scoped writing, handoff, and continuity; their creation does not install, authenticate, or invoke those providers.

| Location under this project | Purpose |
| --- | --- |
| `/Users/bekim/Heleos-spark` | Visible local `main` with the complete reconciled executable tree and native-suite receipt merge `2d6f114...` |
| `/Users/bekim/Heleos-spark/ACTIVE_BUILD` | Legacy Finder-visible ignored pointer to completed `build/agent-control-foundation` checkpoint `f6f1bc8`; inspection only, not current recovery authority or a source copy |
| `/Users/bekim/Heleos-spark/WINDOWS_NATIVE_HANDOFF_f6f1bc8` | Finder-visible exact-checkpoint-SHA transfer artifact; native Windows/local fixed NTFS execution remains pending |
| `/Users/bekim/Heleos-spark/WINDOWS_NATIVE_HANDOFF_58ab1e3` | Finder-visible exact Foundation candidate, trusted importer, hashes, and command; native Windows x64/NTFS execution remains pending |
| `/Users/bekim/Heleos-spark/ATHENA_RESEARCH_QUARANTINE/2026-09-09-div23/` | Finder-visible ignored Athena receipts, controller adjudication, and separate research-only six-source UFGS candidate; no admitted source or production authority |
| `.worktrees/agent-control-foundation/` | Active `build/agent-control-foundation` implementation with guarded providers, macOS containment, the Windows restricted-token/Job Object candidate, deterministic Windows package/import tools, and a machine-readable continuity guard; not merged into Foundation 0.1 |
| `.worktrees/foundation-native-suite-receipt/` | Completed local receipt implementation on `build/foundation-native-suite-receipt`, based on `af526a3...`; exact macOS-tested candidate `58ab1e3...`, native Windows/NTFS execution next |
| `.worktrees/foundation-0.1-build/` | Closed, clean Task 7-9 implementation chain and immutable execution evidence |
| `.worktrees/foundation-0.1-release/` | Passing local `build/foundation-0.1-release-gate` Task 10 supply-chain/SBOM implementation at `6157458...` plus this continuity child; workflow and native Windows gates remain open |
| `.worktrees/claude-task4/` | Independently accepted CLI candidate; exact six files integrated into the build worktree |
| `.worktrees/actual-build-plan/docs/superpowers/plans/2026-09-06-foundation-completion.md` | Current completion plan |
| `.worktrees/WORKSPACES.md` | Workspace and recovery-copy map |

The detailed checkpoint is `.worktrees/foundation-0.1-build/.superpowers/sdd/2026-09-06-foundation-completion/COMPACTION_RECOVERY.md`. In Finder, use Command-Shift-G to open a `.worktrees` path; its leading dot hides the folder.

## Where staleness occurred and how it is prevented

1. The main checkout previously lagged while development happened in a hidden worktree. Merge `dee9179...` fixes that visibility/history gap.
2. Progress records still called Claude session `6654` and Windows probe `4554` running after both terminated. Those records now distinguish completed implementation, pending review, and failed verification.
3. Older temporary-worktree registrations still have broken Git links. They are not active build authority and have not been deleted or pruned.
4. The compaction checkpoint now records the exact completed chain, convergence evidence, visible-main merge, post-merge gates, Task 10 worktree, `f6f1bc8` active-build checkpoint, terminal Athena task, and refreshed Windows handoff. Completed work, including Tasks 7-9, must not be recreated after compaction.
5. `scripts/verify-repo-state.py --expect-branch ... --expect-head ... --require-clean` now fails closed when a resumed controller is in the wrong checkout, on stale bytes, or carrying uncommitted work; its JSON retains the actual state for diagnosis.
6. `scripts/active-build-status.py --human` resolves visible local `main` as the sole authority, validates every explicitly registered active build, and reports the current live commit. It accepts an explicit empty list when no work remains, while rejecting a missing worktree, wrong branch, unrelated history, or uncommitted authority bytes. The completed agent-controller branch was removed from the authority list after its bytes were integrated, preventing compaction from routing work to an 81-commit-behind checkout.

## Initial Task 10 implementation checkpoint (superseded by the receipt checkpoint above)

- Tasks 7-9 are complete, committed, converged, and locally integrated. The exact post-baseline path counts are `18/2/1/8/8`; amended convergence passed with no merge inside that chain. No reviewer agents remain active or required by the owner's current direction.
- Task 10 local implementation commit `6157458c6566d8ad26a2ec2c6ba1c7d8a697e2b6` has sole parent `c1596c4...`, tree `c4539ca7e4ecf62e0e767684c314dd56e4527a0c`, exact 15-path patch SHA-256 `9232cb6785b0ca26257b0429abc825bd2c43c0112205c39eb08d7deaacaa9637`, and no workflow or app-registry change. It is an implementation visibility checkpoint, not `release_candidate_sha`.
- Exact `cargo-deny 0.20.2`, `cargo-audit 0.22.2`, `cargo-cyclonedx 0.5.9`, and Gitleaks `8.30.1` are installed. A clean RustSec snapshot is frozen at commit `bf25f6575a93a35f30796c65c0ed91bee7fa19fd`; offline `cargo audit --no-fetch` passed for all 405 lock packages against 1,242 pinned advisories, with zero vulnerabilities and zero warnings.
- The one-and-only offline Task 10 lock normalization is complete. Only `serde_json` and `toml 1.1.4+spec-1.1.0` were added to the existing `heleos-verification` dependency list. At initial checkpoint `6157458`, lock SHA-256 was `6bcb2d5a43927c4c9e7fc1e34c6da982a0841061ed18c4b6ec0be4002daefe92`, previously `821daf0341e3500c7c210a1d5802b4bcf4ce0447d383e686ccc1d32ae9f4c08d`. Locked/offline metadata passed. No package identity, source, checksum, resolved feature, or production dependency changed; do not repeat unlocked normalization or regenerate the lock.
- The corrected `deny.toml` policy at SHA-256 `23b676ca0f994980eb24485d81d42258d718955c98014bbbe0609417472db70b` passed the integrated offline/frozen advisories, bans, licenses, and sources gate. The policy includes the full development/build graph and freezes forty exact-version duplicate skips with upstream rationales, no wildcard/subtree skips, and an unskipped version in every family. Heleos engineering owns convergence before Foundation 0.2. Only the real `heleos-cli`, `heleos-core`, and `heleos-platform-fs` manifests gained exactly `publish = false`; the verifier already had it. `crates/heleos-pdf-guest/Cargo.toml`, `crates/heleos-pdf-protocol/Cargo.toml`, and `crates/heleos-test-fixtures/Cargo.toml` remain byte-identical to the accepted PDF source closure, as required by default Task 9 provenance.
- Both supply-chain scripts use a bounded disposable audit workspace constructed from exact verified source/lock bytes. Only its copies of those three PDF manifests receive `publish = false`, solely for cargo-deny to classify the seven local packages under the license-only private exception. Require byte-identical lockfiles and exact dependency-graph equality with the real workspace, allowing only the declared copied-manifest metadata and relocation differences in metadata evidence. This copy has no product-source, build, guest, SBOM, or other artifact authority. All seven local packages remain in the full graph and SBOM; empty private-registry/source exemptions and external license/source/checksum/advisory enforcement remain mandatory.
- The initial canonical Task 10 file scope included `Cargo.lock`, the three real CLI/core/platform metadata-only manifest edits, `tests/verification/Cargo.toml`, `tests/verification/src/bin/verify-provenance.rs`, and `governance/secret-scan-baseline.toml`. The native-suite checkpoint above records the later admitted receipt paths. The three PDF source-closure manifests remain outside modification ownership. The plan preserves both already-governed Windows-only `cap-primitives` direct edges in `heleos-platform-fs` and `heleos-cli`, no requested features, the unchanged five-symbol inspection allowlist, and zero core edge/use.
- The pinned SBOM generator's raw seven member files cover only 390 of 405 packages and contain physical workspace paths. The deterministic Rust aggregate at `artifacts/sbom/heleos-foundation-0.1.cdx.json` restores the complete 405-package, 1,035-edge graph and rejects physical paths. At initial checkpoint `6157458`, two isolated builds produced artifact SHA-256 `12ae1413dc2e3de542d9c0587e56db19f66d9e2562504f7ecafce42ab50fb2a7`; the tested receipt candidate above supersedes that source-input-bound artifact with SHA-256 `82e0ab177ab2ee13f3d40d14cdea6f0e5837d8f0e549e5383276d68ee66d3516`. The disposable audit workspace supplied no artifact authority.
- At initial checkpoint `6157458`, full-history Gitleaks reported 22 redacted adjudication candidates. Exact candidate `58ab1e3` added the separately classified immutable Windows forbidden-path fixture, producing exactly 23 history findings and one current marker under the later baseline recorded above. Scanner exit zero permits report collection only; semantic verification authorizes the result. No generic ignore, future-receipt allowance, raw Match/Secret logging, auto-learning, or target/cache scan authority is admitted. Heleos engineering owns re-adjudication before Foundation 0.2 or sooner on scanner/rule/context/scope drift.
- The first full local run stopped at `backup::tests::restore_seek_failure_preserves_io` because parallel sibling tests changed the same default temp namespace between the test's before/after inventory reads. The exact case passed alone, and all 55 backup tests passed serially. Both supply-chain entry points now set `RUST_TEST_THREADS=1`; no production or frozen Task 7 test source changed. The complete corrected rerun passed, including 294 core unit tests, the 143-second two-root PDF probe suite, the 274-second hostile-intake suite twice (ordinary and network-denied), and every remaining workspace test with zero failures or ignores.
- The visible local `main` remains ahead of `origin/main`. No fetch, push, force-push, workflow publication, deployment, or account-level mutation occurred.

Resume at the next local finish line above. The [canonical Task 10 amendment](docs/superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md#task-10-gate-supply-chain-windows-ci-and-foundation-release-evidence) binds the exact implementation scope and policy. Do not restart completed Tasks 7-9, dispatch review agents, repeat unlocked dependency normalization, reset/clean, or bypass a failing guard.
