# EQUIPMENT-COUNT-1 independent integration review

Reviewer: `/root/equipment_integration_review`, a separate Codex agent.
Base: `05c55645fdf01a3238c13097c2f100dfa653799a` in `/Users/bekim/Heleos-spark`, branch `main`.
Recorded: 2026-09-16T20:00:24.744820+00:00

## Verdict

No unresolved concrete defect in the reviewed root integration candidate against EQ03–EQ06. The bounded connected workflow suite passes all eight tests. This is an integration review, not acceptance of the equipment calculation kernel, the separately assigned new UI panel, automatic recognition, representative projects, evidence PDF output, a full CSI section, or Windows delivery.

The reviewed changes connect physical equipment review without promoting legacy tag counts into physical authority; preserve unknown versus zero; carry distinct physical, component, procurement and installation quantities into the workbook; preserve source identities and history; and add the new runtime, UI and exact approved rule dependencies to the explicit package closure. No code changes or commits were made by this reviewer. This report is the sole authorized write.

## Findings and disposition

- **EQ06, resolved:** the workbook required equipment declaration `state`, while the early producer result omitted it. The producer now returns `matched` or `unresolved`; a pure calculation-to-workbook fixture succeeds, and the connected schedule-conflict export test passes.
- **EQ04, resolved fixture mismatch:** the early connected fixture declared coverage using only graphic evidence. The current fixture includes explicit coverage evidence for its selected physical drawing sources. The independent connected run now verifies the literal three-pump plus one-AHU correction/reopen/export case and affected-source-only invalidation.
- **EQ05 / approved E09, resolved:** count-only projects previously inherited an unconditional Measurements requirement. The integration now permits reviewing an empty Measurements stage for equipment/air-device-only scope. The test verifies that adding piping restores the measurement requirement, rather than globally bypassing it.
- **EQ05, resolved:** legacy equipment panel instructions still described physical counting. The static panel now says draft equipment tag review and directs physical quantities to the separate reviewed-assembly workflow.
- **EQ03, clarified and verified:** the first test submitted the exact original intent with an old generation, which is an idempotent no-op. The coordinator clarified that the stale-generation criterion concerns writes, while identical replay must not add instances. The corrected fixed case changes an observation while retaining the obsolete generation and verifies rejection without saved-state mutation. Current-request replay still does not add generations; correction and reopen retain prior results.
- **Withdrawn concurrency observation:** an early comment suggested an incorrect expected number of unavailable workbook rows. Direct execution confirmed exactly four rows (duct, air devices, equipment, other Division 23), matching the current test.

## Independent checks observed

- `python3 -m unittest discover -s tests/drawing-workspace -p 'test_equipment_count_workflow.py'` — latest execution **exit 0**, **8 tests passed**, 0.211 seconds. Earlier execution had one failure from the exact-intent replay test described above; it was not discarded or reported as passing.
- `git diff --check -- scripts/takeoff_workflow.py scripts/takeoff_workbook.py scripts/workspace_package.py scripts/drawing-workspace.py apps/drawing-workspace/workflow.js apps/drawing-workspace/index.html apps/drawing-workspace/equipment.js` — **exit 0**, no findings, run again with the passing connected suite.
- `python3 -m py_compile scripts/takeoff_workbook.py scripts/workspace_package.py scripts/drawing-workspace.py scripts/takeoff_workflow.py` — **exit 0**, earlier static candidate.
- `node --check apps/drawing-workspace/workflow.js` and `node --check apps/drawing-workspace/equipment.js` — **exit 0**, earlier static candidate.
- Direct pure calculation/workbook probe: one reviewed pump and one matching schedule declaration projected successfully; unavailable workbook summary had exactly four rows.

No broad suite was run while the backend module was absent. The backend and new UI panel were being implemented concurrently; these were consulted for interface compatibility, but this review does not substitute for their separate reviews. The coordinator retains responsibility for the final regression batch and exact accepted candidate identity. This report records the latest observed root integration file identities below; later relevant changes require scoped reconsideration.

## Reviewed root candidate identities at report time

| Path | SHA-256 |
| --- | --- |
| `scripts/takeoff_workflow.py` | `05e854c1dd2a23ff6578f15e3c033a15a8ae587fb953533380af17d7b3507375` |
| `scripts/takeoff_workbook.py` | `288be02e6f195de16d3ff6da262836466cccafab69c77fe749c9025697b73c77` |
| `scripts/workspace_package.py` | `f6c19580eb6f7b3f89b6f5a7a41b0263930faab901a703a6339f6ba901c27dad` |
| `scripts/drawing-workspace.py` | `ad60ed9d77cddc66107c7df19f46a358ad89b7c2ce5eb847143febbd1a57f50b` |
| `apps/drawing-workspace/workflow.js` | `8eb2e33a72b127b57506f07743fb911d4423e8ff12b65edb0f9c324678c19d4e` |
| `apps/drawing-workspace/index.html` | `0f4fbde4820cea2ada1db0ecbafd803d489d72ec5fbc26517c4413c979f86660` |
| `apps/drawing-workspace/equipment.js` | `cd4ebfb39334db6ac8b8d36bd9972fb4a9d819b6e6ce77a2b400cf39eddb6202` |
| `tests/drawing-workspace/test_equipment_count_workflow.py` | `81d04d321cd97b09252f5c380bc5f1045fc78a3be0286b340a7de6fe65f98a68` |


## Final EQ05 UI review addendum

Recorded: 2026-09-16T20:06:17.650443+00:00. Reviewer: `/root/equipment_integration_review`.

Scope: full `equipment_counts.js` and its targeted DOM cases, plus the shared wrapper deltas for region-pick completion, selection and navigation. The exact supplied UI SHA-256 `5334f74dbca88a299b2e95ccb8dab0dc25dda09d89a76441aded3f15144dc2b5` was verified before recording this verdict.

**Verdict: no unresolved concrete EQ05 defect in the reviewed UI candidate.**

Observed behavior:
- The edit draft clones the saved request and pins both generation and the current source contexts. Changed pins prevent stale saves while retaining the draft. Saves supply actor/reason through the shared workflow and submit the source records, never a final total.
- Final and known quantities are rendered from the server result. Physical assemblies, components, procurement, installation, removal and reinstallation remain separate; group, schedule, system and package values have distinct displays. An incomplete scope is not displayed as zero.
- Source-region admission checks revision/page and a finite normalized nonempty box against its captured context. New instances remain unresolved until reviewed. Evidence, attributes, package/duplicate/multiplicity relationships and schedule declarations are available in the editor.
- Source navigation carries exact source identity and geometry. Saved calculation history is preserved while corrections modify only the draft.
- Explicit stale-source replacement retires affected sources/evidence and dependent observations/parent children, relations and schedules from the draft. It retains unrelated instances and saved history, preserves existing unresolved requirements, adds named unresolved obligations for removed dependencies, and resets coverage rather than silently treating removals as completion.
- The shared workflow listener now awaits `regionPicked`; navigation and page selection call `cancelRegion`, and selection rerenders the Equipment stage. No unhandled asynchronous wrapper mismatch remains.

Independent checks on this UI candidate:
- `node --test tests/drawing-workspace/equipment_counts.test.js` — **exit 0**, **20 tests passed**, 0 failed, duration 43.672709 ms.
- `git diff --check -- apps/drawing-workspace/equipment_counts.js tests/drawing-workspace/equipment_counts.test.js apps/drawing-workspace/workflow.js` — **exit 0**, no findings.

No additional browser session or broad suite was started. The coordinator is performing the actual Mac browser workflow separately. The UI review does not add automatic recognition, evidence PDF, representative-project, CSI-section or Windows acceptance claims. Earlier report findings and verification history remain intact.

| UI review path | SHA-256 |
| --- | --- |
| `apps/drawing-workspace/equipment_counts.js` | `0202bca7b4ab5a7fe22ebc6c0e838cd0826e3aabbec24ad6fe90f2af15de27b0` |
| `tests/drawing-workspace/equipment_counts.test.js` | `3ebeae1cacb57ba12c6532a0a6f14d30dab6f36e4fef417e8470d07bcb080ce1` |
| `apps/drawing-workspace/workflow.js` | `8eb2e33a72b127b57506f07743fb911d4423e8ff12b65edb0f9c324678c19d4e` |


## EQ05 coverage-selector correction — final scoped delta

Recorded: 2026-09-16T20:07:25.488672+00:00. Reviewer: `/root/equipment_integration_review`.

The coordinator's subsequent live Mac browser check found a concrete UI defect not caught in the earlier DOM review: editing an occurrence changed draft coverage to unknown while the mounted selector continued to display Complete. Selecting its already-visible Complete value did not fire a change event, so the saved request unexpectedly remained unknown. This finding supersedes the earlier no-unresolved-defect statement for that prior UI identity; earlier test observations are retained as history.

The bounded fix adds a reference to the currently mounted coverage selector. `invalidateCoverage()` now sets both draft coverage and the selector value to `unknown`, without rendering or replacing the focused input. `render()` clears the reference before building the new view and the coverage field assigns the new live reference. Inspection found no stale-control mutation or additional behavior change in this delta.

The new literal regression begins at Complete, excludes the third pump, asserts that the existing selector visibly becomes Unknown and both input nodes remain mounted, explicitly reselects Complete, then verifies the saved request contains Complete coverage and the exclusion.

**Scoped verdict: the coverage-selector defect is closed by code inspection and the focused regression; no unresolved defect in this delta.** Fresh live-browser confirmation remains the coordinator's separate check.

Independent command: `node --test --test-name-pattern='coverage' tests/drawing-workspace/equipment_counts.test.js` — **exit 0**, **3 matching tests passed**, 0 failures, duration 43.857417 ms. The file now contains 21 DOM cases; this reviewer reran only the three coverage-related cases because the remaining UI implementation was unchanged. No broad review, backend suite or extra browser was repeated.

Latest reviewed UI identities supersede the prior addendum hashes:
- `apps/drawing-workspace/equipment_counts.js`: `0202bca7b4ab5a7fe22ebc6c0e838cd0826e3aabbec24ad6fe90f2af15de27b0`
- `tests/drawing-workspace/equipment_counts.test.js`: `3ebeae1cacb57ba12c6532a0a6f14d30dab6f36e4fef417e8470d07bcb080ce1`
