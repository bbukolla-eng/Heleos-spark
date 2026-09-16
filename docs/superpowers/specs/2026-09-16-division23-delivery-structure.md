# Division 23 delivery structure and completion contract

Owner request: locate review/continuation bottlenecks, make all mechanical sections
visible, and give tasks and sections finite completion endpoints. Audit base:
`c1143a7c1bc75339cdd8dd71cfe0580dd0d611ca`, main checkout. This document defines
coordination behavior; it adopts no new estimating rule or mechanical taxonomy.

## Constraints

- Mac is the current implementation and engineering acceptance target.
- Approved D01–D10, A01–A12, E01–E12 and imperial outputs remain authoritative.
- Accepted work is reused; a demonstrated defect or changed relevant input is required to reopen affected work.
- A user illustration does not select a priority or create an example-specific deliverable.
- The cancelled 4specs adoption remains cancelled.
- Codex is the only commit owner; workers write only their assigned paths.
- NotebookLM supports source verification before knowledge-dependent work; coordination-only checks need no new mechanical research.
- No push, deployment, credential change, billing change or new automation is authorized by this plan.
- New continuity tooling uses Python 3.9-compatible standard-library code and unittest.

## What is causing the bottleneck

These findings were verified against the audit base, not inferred from the number
of test logs. Existing review fixes and targeted regression checks have legitimate
evidence; this audit does not justify weakening those checks.

| Finding | Evidence at the audit base | Consequence | Required correction |
| --- | --- | --- | --- |
| The register describes stage slots as executable tasks | `docs/plans/division-23-section-register.json`, `stage_contract_semantics` and every section's `tasks`; all 448 acceptance definitions are pending | Work looks scheduled without inputs, an oracle or a review finish line | Call them planned coverage slots; create a ready implementation task only for a bounded deliverable with a complete contract |
| Partial-output dependency language is not represented in data | Same register's dependency prose versus whole-stage `depends_on` IDs | A consumer can wait for an entire section when it needs one accepted output | Bind dependencies to producer ID, output ID and scope; until bound, require coordinator resolution rather than automatic readiness |
| Accepted software and unsuccessful recognition share a label | `.heleos/air-device-count-2026-09-16/connection-verification-final.json`, `state` and `limits`; `local-recognition-outcome.json`, AC01 physical 0 versus expected 3 | The software appears unfinished because model qualification is unfinished | Retain the parent; distinguish accepted software from the parked recognition child. No model rerun to perform this bookkeeping |
| A pre-integration workbook receipt remains easy to mistake for live state | `.heleos/workbook-export-2026-09-16/acceptance.json` says integration pending; `resume-state.json` records commit `2757d31ff93f60ed567ddd8bc9257c29a0e52fa8` | Completed workbook work can be selected again | Point continuation to a terminal checkpoint joining the original acceptance and integration receipts; preserve original records |
| Provider instructions conflict with task acceptance | `.agents/skills/claude-code-headless/SKILL.md` rejects any unresolved item; `CLAUDE.md` requires a legacy Workflow tool | An expected UNKNOWN or unavailable legacy tool can become a new gate | Reject unresolved mandatory contract defects; use the actual bounded runner for headless assignments |
| Goal/status/history duplicate routing instructions | Root status mixed a live queue with long history; `get_goal` now returns null while prose describes an older paused goal | Old “next” instructions can outlive the work | Goal states outcome only; one live queue; immutable history linked separately. Report current tool state accurately |
| Catalogue coverage is incomplete | 56 agency guides, no verified intermediate CSI hierarchy, 51 title/inventory-only bodies, 5 with some body evidence | “Full Division 23” has no verified complete denominator | Keep all known rows visible and the catalogue gap explicit. It blocks a full-coverage claim, not independent known-scope implementation |

## One responsibility per record

| Record | Responsibility | Must not do |
| --- | --- | --- |
| `docs/operations/build-goal.md` | Stable product outcome and Mac-first delivery order | Name a permanently fixed next duct/device task |
| `docs/plans/division-23-section-register.json` | Source-qualified section identities and required coverage slots | Pretend an agency guide is a verified CSI leaf; schedule template slots automatically |
| `docs/plans/build-work-ledger.json` (planned tool deliverable) | Actual task states, frozen contracts, output dependencies and queue order | Infer acceptance from prose or silently change a contract |
| `CURRENT_STATUS.md` | Short resume view, primary task, ready alternatives, current limits and pointers | Duplicate the complete history or create another competing queue |
| Section completion matrix | Readable projection of every known section and its missing coverage | Independently maintain a second set of statuses |
| Task receipts under `.heleos/` | Exact commands, results, hashes, review and integration evidence | Treat a historical “pending” record as the latest terminal disposition |

Until the planned ledger tools are implemented, **CURRENT_STATUS.md remains the
single live queue**. The new plan does not activate a scheduler, hook, recurring
job or background build. The existing active-build-status command checks Git
checkout authority only; it does not select product tasks.

## What complete means for one task

An implementation task has one independently testable deliverable. Its contract
lists its scope, exclusions, input identities, outputs, permitted write paths,
rule decisions, criterion IDs, exact check procedures, expected results/tolerances,
reviewer and evidence locations before the affected implementation/evaluation.

The task is **accepted** when all its mandatory criteria pass for the recorded
bytes, independent review accepts that scope, and its integration and evidence
are recorded. A source query, accepted rule packet, calculation kernel, connected
workflow and recognition qualification are different deliverables. Closing one
must neither reopen nor falsely close the others.

| State | Meaning and exit |
| --- | --- |
| planned | Coverage is identified; this is not an executable assignment |
| definition_pending | A selected deliverable has named missing contract fields and a finite definition action |
| ready | Frozen contract exists and the exact required outputs are available |
| in_progress | One assigned writer is producing the deliverable |
| review | Candidate and declared check results exist; blocking findings cite mandatory criteria |
| accepted | Required checks, independent review and integration evidence are recorded; preserve this terminal milestone |
| blocked_input | A specific missing input, responsible next action and dependent scope are recorded; other ready work continues |
| deferred | Intentionally outside the current delivery lane; never an implicit dependency |

`not_required_by_contract` is a justified coverage-cell disposition, not a claim
that a required feature is finished. A nonphysical service may use an obligation
output instead of a material count. A project's not-applicable decision does not
complete product coverage.

## Review protocol with an endpoint

1. Freeze the candidate manifest and criterion list. Combine scope and correctness
   review against that same candidate; separate reviewers may inspect it concurrently.
2. Every blocking finding cites a criterion ID, actual versus expected behavior,
   evidence/reproducer and affected paths. Boundary/security violations may cite
   the applicable shared policy. Preferences and additional features get follow-up IDs.
3. Correct the finding; rerun its check and the affected regression surface.
   Broaden verification only for a recorded new risk or changed dependency.
4. The reviewer closes the cited findings; Codex records the accepted result and
   advances. Another general review round is not a default requirement.
5. If the same issue survives two correction rounds, diagnose the disputed oracle,
   input or implementation assumption and record a bounded next action. This is
   a trigger to change approach, never permission to waive a failing requirement.
6. An expected UNKNOWN passes its named case. It cannot replace a supported quantity
   that the same contract requires. New acceptance requirements need an explicit
   version/reason; never move the finish line silently after a failed result.

Engineering acceptance within existing authority belongs to the coordinating
Codex. The owner decides genuinely missing estimating policies and final release,
not every regression fixture or routine implementation review.

## What complete means for each section

The eight dimensions below are **coverage obligations**, not eight compulsory
worker assignments or review rounds per item. One accepted change can satisfy
several cells when its exact scope and evidence are linked.

| ID | Required section result | Pass evidence |
| --- | --- | --- |
| SC01 / TAX | Versioned section/subsection scope and complete required types, variants, attributes, relationships, exclusions, services and child references | Source inventory reconciles with zero unexplained omissions; required taxonomy records and relationships resolve |
| SC02 / EVID | Drawing, schedule and specification evidence for required supported items, plus explicit ambiguity/omission behavior | Frozen original-source cases preserve source/revision/page/region and meet declared extraction/recognition checks |
| SC03 / RULE | Every required quantity or obligation has its applicable approved basis | Rule/version bindings and independently established cases cover inclusion, exclusion, lifecycle and unknown treatment |
| SC04 / CALC | Deterministic quantities or appropriate nonnumeric obligations | All named cases match exact counts or predeclared measurement tolerances; package and purchase/installation boundaries remain correct |
| SC05 / RECON | Plans, schedules, specs, assemblies and responsibilities agree or yield the expected exception | Named duplicate, mismatch, omission and package cases pass without invented items or double procurement |
| SC06 / EDIT | Corrections, revisions, recalculation and reopening work in the application | Named lifecycle cases preserve history and invalidate affected results only |
| SC07 / OUT | Required estimator Excel and evidence PDF outputs agree with accepted results | Expected rows/totals, unknown/stale presentation and original-source links pass; documents open successfully |
| SC08 / ACCEPT | Complete declared mechanical scope works on Mac for the frozen representative set | All mandatory cells/taxons/children have accepted evidence; independent section dossier passes and records exact platform scope |

Apply SC01–SC08 to **every row** in the linked completion matrix. Parent completion
aggregates required child evidence without replaying their reviews. A shared
counter, parser or export is supporting evidence for identified cells; it cannot
by itself close the section. Full product completion additionally requires the
complete verified catalogue, final representative acceptance and later platform
delivery. Those later gates do not reopen accepted Mac engineering tasks.

Section scope is frozen to identified sources/editions and explicit required
types; it is not an unlimited promise to support every hypothetical variant.
Changed editions or added supported types create a versioned extension task.

## Work lineup and maintenance rhythm

- Preserve `EQUIPMENT-COUNT-1` as the existing approved primary implementation.
  Its first action is binding the connected implementation brief to E01–E12 and
  EC01–EC20, current interfaces and named lifecycle cases. This is normal task
  preparation, not another owner approval or replacement equipment-rules project.
- Keep recognition qualification as its own parked child until a materially
  different bounded experiment is defined. Do not repeat the eight terminal runs.
- Keep all known sections visible. Prepare the next selected section's contract
  using its full sources; do not freeze 448 contracts before implementation can proceed.
- The small maintenance implementation in the linked plan may run alongside
  product work on non-overlapping paths. It does not become a new all-section gate.
- On each task completion: record terminal outcome, link evidence to covered
  cells, update the queue and status in the same completion commit, then advance.
- Report delivered capability, failed named criteria and next deliverable. Use
  counts with their denominator; do not report a full Division 23 percentage while
  the authoritative hierarchy remains unverified.

## Scope of this delivery

This delivery supplies the audit, corrected coordination documents, task/section
completion definitions and the executable maintenance plan. It does not implement
the planned ledger validator, a scheduler, equipment quantities, new recognition
models or the remaining mechanical sections. Those remain separately visible work.
