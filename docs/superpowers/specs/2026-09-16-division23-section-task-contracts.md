# CSI Division 23 section task and acceptance contracts

This specification makes the verified CSI hierarchy the master scope. Every
Division, section and subsection has a coverage address, four planned delivery
frames and finite criterion bindings. Source guides do not supply a competing
hierarchy. The baseline is **MasterFormat 2016 Numbers & Titles, April 2016**;
current-2026 reconciliation is explicitly separate.

## Global Constraints

- Mac is the current implementation and section engineering acceptance target; Windows delivery follows the connected workflow.
- Approved D01–D10, A01–A12, E01–E12 and imperial outputs retain their approved scope.
- Preserve completed software and unchanged accepted evidence. Section acceptance requires full required mechanical scope and descendants.
- Classification is verified for **MasterFormat 2016 Numbers & Titles, April 2016**, not represented as complete current-2026 coverage.
- The cancelled 4specs adoption remains cancelled. No unverified agency crosswalk, numbering or parentage is introduced.
- One writer per named path; Codex independently verifies, integrates and remains the only commit owner.
- Use the recorded CSI NotebookLM routing and verify mechanical source passages; source titles and model answers do not establish rules.
- No automatic scheduling, background build, hook change, push or release follows from this plan.

## Structure and scope

The [canonical register](../../plans/division-23-section-register.json) contains
432 classification nodes including the Division root and 431 nonroot entries.
Every node has an existing Markdown card, exact number/title/page/edition,
verified parent and children, and DEFINE / RESULT / CONNECT / QUALIFY task IDs.
The [task index](../../plans/division-23-task-contracts.json) has 1,728 planned
frames and 10,800 mandatory criterion bindings. These are explicit work endpoints,
not assertions that 1,728 implementation assignments are ready.

A broad parent includes its own required mechanical scope and required descendants.
Accepted child evidence satisfies the corresponding parent criteria by exact
scope/identity; parents do not repeat child implementation or review. A large
section can split independently testable families into child implementation tasks
without creating four assignments for every individual fitting or attribute.

Each section's mechanical scope must enumerate all required types, variants,
attributes, relationships, exclusions, services and child references. A title
is not a mechanical taxonomy; a classification catalogue provides no calculation
rule, project requirement, material quantity or current-edition change list.

## Finite acceptance

**A task is accepted when every mandatory criterion in its frozen contract passes
on identified inputs and candidate bytes, independent review accepts that scope,
and integration/evidence are recorded.** Missing acceptance inputs get named
resolution tasks. New requirements get explicit contract versions or extension
tasks; they do not silently move an accepted finish line.

“No unresolved mandatory failures” means none of the fixed comparisons for that
one task remain outside their expected result. It does not require every other
section, later platform gate or possible future enhancement to be complete.
An expected UNKNOWN passes its designated exception case. It cannot replace the
known quantity/obligation required by another case. Unknown is never zero.

A DEFINE receipt may accurately account for referenced-source or rule gaps and
release separately admitted scoped outputs. Missing primary mechanical evidence
fails the affected source criterion; unresolved behavior is never advertised as
ready. Full section QUALIFY cannot close while a required taxonomy item, child,
rule or behavior remains unsupported. A project-specific exclusion does not
close product coverage.

## Task inputs, outputs and readiness

| Stage | Independent deliverable | Dependency scope |
| --- | --- | --- |
| DEFINE | Verified mechanical taxonomy, rule bindings, independent expected cases and exact implementation packets | The selected primary mechanical bodies/passages and applicable approved decisions; missing reference inputs are explicitly scoped |
| RESULT | Source-linked deterministic quantities/obligations for admitted behavior | Only the required accepted source/rule/case producer outputs; no blanket recognition-complete requirement |
| CONNECT | Mac review/reconciliation, correction, revision/reopen and Excel/evidence-PDF workflow | The exact input/result interfaces used by each behavior; evidence-only work can proceed before unrelated quantity support |
| QUALIFY | Full required section/child coverage passes SC01–SC08 on the fixed representative Mac dossier | All mandatory scoped evidence, independent representative truth and fixed thresholds |

Before implementation, freeze one bounded `implementation-packet.json` containing
`task_id`, `contract_version`, `section_id`, `scope`, `exclusions`, `source_ids`,
`rule_ids`, `fixture_hashes`, `base_commit`, `write_paths`, `writer`, `interfaces`,
`dependencies`, `criteria_ids`, `check_commands`, `reviewer` and `receipt_path`.
Each dependency names `producer_task`, `output_id`, `scope` and accepted identity.
No unresolved path/interface/expected-value field may be promoted to ready.
The actual task ledger records this packet and task state; the register is not a
replacement execution queue.

Each node's card names the output directory. DEFINE files have these required
contents:

| File | Required contents |
| --- | --- |
| `scope.json` | `section_id`, `catalogue_edition`, exact children, declared mechanical scope, primary source identities and each applicable clause's taxonomy/exclusion/gap disposition |
| `taxonomy.json` | Stable item IDs; type/variant/attribute/relationship/exclusion/service records; required children; verified source-passage references for each adopted record |
| `rule-bindings.json` | Item/behavior, applicable approved rule/version, evidence and scope; explicit conflict/missing-decision records |
| `expected-cases.json` | Stable case ID, independent source of truth, input hashes, expected identities/values/states, imperial units, numerical tolerance where needed, rule binding and criterion IDs |
| `implementation-packet.json` | The exact fields listed above for each admitted bounded assignment; read/write and external-submission boundaries |
| `input-gaps.json` | Gap ID, affected scope/cases, missing input, responsible next action, finite resolution criterion, expected evidence and dependent readiness |

For each required gap, create `<section-id>-INPUT-<gap-id>` in the selected ledger.
The finish line is the exact missing source/decision/fixture/interface supplied
and independently checked, or a justified versioned scope decision; merely naming
a gap never closes its dependent mechanical behavior.

Exact counts, identities, link targets, exception states and history compare
exactly. Measurement cases declare numeric tolerances before execution. Recognition
and allowed review burden use independently justified original-source metrics
frozen before evaluation. Field performance limits are not automatically software
accuracy targets.

## Criterion binding

Bind every suffix below as `<section-id>-<suffix>`; for example
`CSI-23-01-30.51-D01`. The JSON task's `criteria_ids` must exactly match the shared
stage template order. These IDs are acceptance criteria, not extensions to the
separately approved mechanical rule IDs D01–D10, A01–A12 or E01–E12.

## DEFINE mandatory criteria

Verified mechanical scope and independently expected cases, with admitted implementation packets and explicitly bounded missing inputs.

| Criterion suffix | Expected result | Check procedure |
| --- | --- | --- |
| `D01` | The exact CSI number, title, parent, children, edition and catalogue page match the verified register; the mechanical source inventory has retained body identities and passages for the declared deliverable. | Compare the register row with the pinned catalogue and compare every adopted mechanical claim with its retained source passage; report absent primary bodies as failed input checks. |
| `D02` | The declared scope enumerates required types, variants, attributes, relationships, exclusions, services and children, with no unexplained omission; missing referenced inputs have explicit affected scope and resolution tasks. | Reconcile every applicable source clause and required child with the versioned taxonomy and coverage map, including obligations without material quantities. |
| `D03` | Each admitted quantity or obligation is bound to an applicable approved rule/version; conflicts, unsupported claims and missing decisions are separately recorded. | Check rule bindings against retained evidence and the existing approved D01-D10, A01-A12 and E01-E12 scopes; verify that source classification never supplies an estimating rule. |
| `D04` | Each admitted behavior has a nonempty independently established case set with exact expected results, units and any numerical tolerance frozen before implementation/evaluation. | Inspect expected-case provenance, hashes and version; require exact identities/counts/states, case-specific measurement tolerances and separately justified recognition metrics. |
| `D05` | The implementation packet names one deliverable, actual output dependencies, exact code/test write paths, current interfaces, check commands, independent reviewer and receipt location. | Resolve each dependency to producer output and scope and inspect named paths/interfaces in the current checkout; do not dispatch a frame containing unresolved implementation fields. |
| `D06` | The definition receipt separates ready outputs from blocked inputs and links a finite resolution task for each required gap; independent review and terminal outcome are recorded. | Check each gap for missing input, affected behavior, responsible action and finish line; verify that blocked cases are not advertised as ready or mechanically accepted. |

## RESULT mandatory criteria

Source-bound deterministic quantities or obligations for admitted scope, independently checked against fixed expectations.

| Criterion suffix | Expected result | Check procedure |
| --- | --- | --- |
| `R01` | Every supported result preserves its exact source/revision/page/region or clause identity, taxonomy identity and rule version. | Compare the frozen source identity cases and reject missing or mismatched result-to-source bindings. |
| `R02` | Deterministic quantities and nonnumeric obligations match all independently expected admitted cases under their predeclared tolerances. | Run the frozen calculation/obligation cases independently and compare exact counts/states or numeric measurements with the recorded tolerance; no generated result may establish its own oracle. |
| `R03` | Inclusion, exclusion, lifecycle, package ownership and separate procurement/installation boundaries match their approved cases without double counting. | Run named positive, exclusion, duplicate, new/existing/demolition and package cases applicable to the declared scope. |
| `R04` | Missing evidence, conflicting sources and unresolved required inputs produce the expected explicit exception; UNKNOWN is never silently converted to zero. | Run supported-result and exception pairs; verify that UNKNOWN cannot satisfy a case requiring a known quantity or obligation. |
| `R05` | Only materially affected results become stale after changed relevant source/rule inputs, and deterministic recalculation preserves unaffected results and history. | Run the frozen changed-input and unchanged-input cases against retained prior result identities. |
| `R06` | Declared checks pass on the candidate bytes and an independent reviewer accepts the implemented scope; result outputs and integration are recorded. | Inspect command exits, actual-versus-expected evidence, candidate manifest and reviewer disposition; close only the scoped engineering task. |

## CONNECT mandatory criteria

The Mac review, reconciliation, correction, revision, reopen, Excel and evidence-PDF workflow passes its fixed cases.

| Criterion suffix | Expected result | Check procedure |
| --- | --- | --- |
| `C01` | The Mac application consumes the declared source/result outputs and displays the correct section identity, scope and evidence. | Run the frozen application entry/open cases using the admitted source and result interfaces. |
| `C02` | Drawing, schedule, specification and responsibility discrepancies produce the exact expected reconciled result or review exception. | Run the named agreement, missing, conflicting, duplicate and assembly cases from the contract. |
| `C03` | A correction changes the intended result and affected calculations, preserving its source link and correction history. | Execute the correction case through the application and compare stored/result/evidence identities against the frozen expected transition. |
| `C04` | Save, close and reopen reproduce accepted results, exceptions and history without losing user corrections. | Execute the frozen persistence/reopen case in the Mac application and compare the before/after expected state. |
| `C05` | A revision invalidates only affected results and provides the required reconciliation/recalculation path. | Execute the frozen source-revision case and compare affected and unaffected identity sets. |
| `C06` | The Excel output contains the expected section/classification, rows, quantities/obligations, units and unknown/stale presentation. | Generate and reopen the workbook; compare its parsed values and source references with the fixed expected export projection. |
| `C07` | The evidence PDF exposes the required original-source references and agrees with the accepted result/revision. | Generate and open the PDF; inspect the frozen expected evidence locations, legibility and current-versus-stale presentation. |
| `C08` | All declared connected workflow checks pass on the candidate bytes and independent review/integration evidence is recorded. | Review the Mac workflow, workbook and PDF evidence together against C01-C07 and the named regression surface; record the terminal outcome. |

## QUALIFY mandatory criteria

All required scope and children satisfy SC01-SC08 on the frozen representative Mac dossier.

| Criterion suffix | Expected result | Check procedure |
| --- | --- | --- |
| `Q01` | Every required taxonomy item and descendant is covered by accepted SC01-SC08 evidence for the declared section version; no required gap is hidden by project non-applicability. | Join the complete scope/child map to accepted receipts and list any missing required coverage; parents reuse exact accepted child evidence rather than rerunning it. |
| `Q02` | Representative original-source projects, independently expected outcomes, tolerances and review-burden thresholds were fixed before qualification. | Verify the representative dossier manifest, oracle provenance and freeze identity precede the evaluation and remain unchanged. |
| `Q03` | The connected Mac workflow meets every mandatory representative criterion, including supported quantities/obligations, exceptions, corrections, reopening and outputs. | Run only the still-required representative checks; compare all actual outcomes with the frozen dossier and reuse unchanged accepted evidence by identity. |
| `Q04` | Independent review closes all mandatory criterion failures in this scope, and the receipt records exact candidate/source/rule/fixture/platform identities and remaining unrelated limits. | Inspect the criterion-result map and review dispositions; expected UNKNOWN cases may pass, but required supported results or required scope cannot remain unresolved. |
| `Q05` | The section/parent completion address, register receipt links and live next action are updated after accepted integration without reopening unchanged work. | Verify the accepted section version, all required child receipts and status handoff reference the same integrated candidate; later Windows/product gates stay separate. |

## Receipt, review and continuation

The receipt records task/contract/version/hash, source/rule/fixture identities,
candidate manifest, every mandatory criterion's actual/expected result and
command exit, numeric tolerances, evidence paths, independent reviewer,
integration identity, platform scope and next action. An unrun or skipped mandatory
check is not a pass. Blocking findings cite the failed criterion and concrete
reproducer. Preferences and additional scope get follow-up tasks.

Once required corrections and affected regressions pass, record acceptance and
advance. Do not require another general review round by default. The existing
approved equipment task remains independent shared product work; catalogue order
is navigation. Duct/device recognition and later Windows delivery do not block
unrelated ready CSI sections.

Read the [master index](../plans/2026-09-16-division23-section-delivery.md), the
selected card and its contract subset. Root `CURRENT_STATUS.md` is the only live
queue. NotebookLM research follows the [CSI routing](../../research/notebooklm/csi-division23-routing.json)
and [verified findings](../../research/notebooklm/csi-division23-findings-2026-09-16.json).
