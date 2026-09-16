# Acceptance for Heleos build tasks and mechanical sections

Acceptance is a recorded **pass against a fixed contract**, not a request to keep
improving a feature indefinitely. This policy implements the owner's 2026-09-16
direction to give every section/subsection and task an explicit endpoint.

## Freeze the finish line before implementation

Every executable task records these fields in its task brief or ledger:

| Field | Required content |
| --- | --- |
| Identity | Task ID, section/source namespace, contract version and content hash |
| Deliverable | Named artifact and observable behavior; explicit included/excluded work and platform scope (Mac for current implementation) |
| Inputs | Frozen source editions, fixtures and prerequisite identities/hashes |
| Expected results | Independently established outputs for each named case, including exception cases |
| Pass criteria | Required comparisons and numerical tolerances; zero unexplained omissions from the defined coverage set |
| Verification | Exact commands or bounded manual procedure and the evidence each produces |
| Reviewer | Independent review responsibility; Codex coordinates acceptance and remains the commit owner |
| Completion record | Actual exit/results, reviewed implementation identities, remaining scope and next task |

An acceptance criterion may not say only “accurate,” “good enough,” “fully tested,”
“estimator ready” or “representative acceptance.” It must say how the result is
checked. A task lacking an expected value, tolerance, fixture or required decision
has state `acceptance_definition_pending` for that gate and names the exact
definition work needed. Research needed to define that contract may proceed as a
separate bounded task. An undefined acceptance gate must not trigger repeat runs.

## Engineering task acceptance

Codex may accept an authorized engineering task when **all** of these are true:

1. The defined artifact and connected behavior exist at recorded exact bytes.
2. Every mandatory check listed in this task's fixed contract passes on its
   frozen inputs. A skipped, unrun or unavailable required check remains open.
3. Independent review has resolved defects within that contract. Additional
   feature suggestions have separate task IDs and do not hold this task open.
4. The result and verification limits are recorded; integration and status point
   to the next unfinished task. “Candidate,” “checks passed,” “accepted” and
   “integrated” remain separate recorded states until each actually occurs.

A **failure** means that a named mandatory check produces a result outside its
recorded expected outcome or tolerance. “Unresolved” means that failure still
exists or its correction has not passed verification. The requirement applies
only to this task's contract; unfinished work in other tasks does not block it.
It is a finite checklist, with no requirement to prove that future defects are
impossible.

A source conflict recorded correctly as `UNKNOWN` passes a check
that explicitly expects that result. Unfinished work outside the task's declared
dependencies does not prevent closing it. A required calculation that expects a supported quantity cannot pass by
substituting `UNKNOWN`. New suggestions and non-mandatory improvements get
separate tasks; changing a mandatory criterion requires an explicit contract
revision and reason, never a silent waiver of a failed check.

This does not require a fresh owner confirmation for every implementation task.
The owner decides genuinely unresolved estimating rules and final product/release
acceptance. Existing D01–D10, A01–A12 and E01–E12 approvals remain settled.

## Concrete checks by kind of work

| Kind | Objective acceptance basis |
| --- | --- |
| Source/coverage audit | Every entry in the frozen source inventory is mapped to a taxon, requirement, reference, explicit exclusion or unresolved finding. Counts reconcile; zero unexplained omissions. Source IDs, editions and locators resolve. |
| Section taxonomy | The declared section scope and required type/variant set are frozen. Every required taxon has a stable ID, source, required attributes, units/basis, relationships and exception treatment. Schema validation passes; no orphan or duplicate identities; every required relationship resolves. Unsupported/missing required taxons keep the taxonomy task open. |
| Deterministic counts | Exact expected counts and assembly/installation breakdowns on approved cases, including duplicates, lifecycle separation and unknown quantities. No quantity invented to make a test pass. |
| Deterministic measurements | Expected geometry/calculation outputs under approved rules and imperial units. Exact arithmetic where specified; any tolerance/rounding is recorded numerically in the case before execution. No tolerance is selected after viewing a failure. |
| AI recognition | Frozen original-source examples and adjudicated truth; explicit precision/recall, class and geometry thresholds and permitted review burden before evaluation. Every mandatory metric passes. Synthetic software fixtures do not substitute for recognition evidence. Missing thresholds are a named definition task, not an open-ended testing instruction. |
| Reconciliation and correction | Expected matches/exceptions, package boundaries, edits, recalculation and affected-only invalidation match named lifecycle fixtures; unrelated results remain unchanged and history survives reopening. |
| Outputs | Every expected row/total matches the accepted calculation; required evidence links resolve to the correct source/page/region; unknown/stale values remain visible. Each required Excel/PDF artifact opens and agrees with the test oracle. |
| Platform | The named workflow passes its prescribed test on the actual required Mac or Windows environment, with exact package/build identity. A portable implementation or Mac test does not stand in for native Windows execution. |

Passing a test written to mirror implementation output is not independent expected
truth. Case expectations come from approved examples, independently calculated
values or adjudicated source evidence.

## Whole-section and product acceptance

A section is engineering-accepted for the recorded platform scope when its complete declared taxonomy and every required
child section/taxon are covered; all mandatory TAX, EVID, RULE, CALC, RECON, EDIT,
OUT and ACCEPT tasks have accepted evidence; the frozen representative expectations
and current Mac workflow criteria pass; and no required mechanical scope is left unresolved.
Source conflicts and missing quantities remain honest exceptions while working,
but they do not silently satisfy a required section completion criterion.

The current build target is Mac. Native Windows verification is the separate
`WINDOWS-DELIVERY-1` product task, scheduled after the connected workflow is ready.
It is not a dependency of the section or taxon engineering ACCEPT tasks. Preserve
portable code and record Windows support as unverified until its own prescribed
checks pass; a Mac acceptance does not claim Windows support.

Local implementation acceptance is a real completed milestone. Remaining
representative gates belong to named tasks. They do not reopen that milestone
or prevent independent section work. Project-specific not-applicable decisions
do not establish product coverage. Final product acceptance additionally requires
the complete authoritative section register to reconcile, Mac workflow acceptance,
the separately scheduled Windows delivery milestone, and the owner's final
acceptance of the delivered workflow.

## Keep acceptance finite

- Once the frozen contract passes and the result is independently accepted,
  close the task and advance. Do not rerun unchanged evidence for reassurance.
- A new idea, source edition, supported variant or higher accuracy target gets a
  new task or an explicit new contract version with its changed scope and reason.
- A demonstrated defect against an accepted contract gets a linked defect task
  identifying affected evidence. Preserve the historical result; do not erase
  unrelated accepted work or reset the whole section's implementation.
- Report completed tasks, open tasks and failed named criteria. Do not invent a
  completion percentage while the full catalogue or denominator is unverified.
