# EQUIPMENT-COUNT-1 connected calculation contract v1

Frozen before implementation, 2026-09-16. Coordinator and only commit owner: Codex.
Checkout: /Users/bekim/Heleos-spark; branch main; base 05c55645fdf01a3238c13097c2f100dfa653799a.

Deliverable: source-bound physical equipment calculation and review in the Mac drawing workspace, with separately visible assembly, component, procurement and installation quantities, immutable review history, correction/recalculation, reopen, CSV/JSON and Excel export. This is shared mechanical software support; it closes no CSI section. Automatic physical equipment recognition, representative-project acceptance and evidence PDF rendering have separate outstanding tasks. Existing tag detection remains draft context, never automatic physical truth. Explicitly reviewed physical-region admissions exercise the source-bound calculation path while recognition is unqualified.

Approved inputs:
- E01–E12: docs/superpowers/specs/2026-09-16-equipment-counting-rules.md, SHA256 dfeea1adabd2478dca7622e96051d6ae3a2ea8eeac42179542b32c4ba03ae134.
- EC01–EC20: tests/fixtures/equipment-takeoff/2026-09-16-rule-examples.json, SHA256 0c233e93253b0c545c4a59ed2b4c6205e021f20c1300dfb63dbff5cf2e1ec150.
- Owner approval: tests/fixtures/equipment-takeoff/2026-09-16-owner-decision.json. No new class decision is pending.
- Verified research reuse: docs/research/notebooklm/equipment-counting-findings-2026-09-16.json. Three retained source text hashes independently rechecked this session. Existing claims remain limited to their supporting source and project applicability. No fresh external query/submission is needed for unchanged findings. Agency documents support examples; CSI identities are not inferred from agency numbering.

Mandatory checks (exact integer comparisons, null means UNKNOWN; no numerical tolerance):
- EQ01: executable cases cover all approved EC01–EC20 expected outcomes, including non-counted included components, separate procurement/installation, duplicates, conflicts, multiplicity, lifecycle and empty coverage. No test oracle is generated from calculation output.
- EQ02: review binding requires current source/revision/page/geometry and actor/reason; unknown tags or tag-only detections cannot establish physical instances. Malformed or unsupported quantity input is rejected or remains UNKNOWN as specified, never silently counted.
- EQ03: saved calculations replay, reopened state agrees, repeated same intent never adds instances; stale-generation writes fail without mutation. Historical observations and quantities survive correction.
- EQ04: literal connected fixture begins with three reviewed pumps and one unrelated AHU; excluding one false pump leaves two pumps and the AHU, prior three remains in history. Source change invalidates affected rows, unrelated results stay current.
- EQ05: web UI displays distinct quantity channels and unknowns, never takes a final total from a user field. Review/correction, source navigation and history are reachable. DOM tests cover save pins, errors and stale view handling.
- EQ06: connected workflow/export CSV/JSON/Excel agree with accepted calculation rows; exact source identities are retained; package closure includes new runtime/UI/rule dependencies. Existing draft tag reader, air and duct support are preserved.
- EQ07: independent review against EQ01–EQ06; concrete defects resolved. New features receive new task IDs and do not silently change this contract.

Commands: python3 -m unittest discover -s tests/drawing-workspace -p 'test_equipment_calculation.py'; equivalent discovery for test_project_equipment_takeoff.py, test_equipment_count_workflow.py, test_takeoff_workbook.py, test_takeoff_workflow.py, test_workspace_package.py and existing equipment-tag regressions. node --test tests/drawing-workspace/equipment_counts.test.js. Run relevant existing UI tests for shared workflow changes. Exact terminal results and changed-file identities go in completion.json.

Writers: backend worker owns scripts/equipment_count_rules.py, scripts/equipment_calculation.py, scripts/project_equipment_takeoff.py, their two new test modules and tests/fixtures/equipment-takeoff/connected-equipment-cases.json. Codex owns integration, workbook, package, connected tests, status and this ledger. UI may be assigned only apps/drawing-workspace/equipment_counts.js and tests/drawing-workspace/equipment_counts.test.js after the API contract is frozen. No overlapping writes, external provider submissions, commits by workers, or changes to approved rules.

Claude Code: last actual guarded request ended 401 expired OAuth; retained at .heleos/csi-hierarchy-migration-2026-09-16/claude-terminal.json. No changed authentication evidence; use authorized Codex fallback, preserve failure evidence, do not repeat unchanged failed dispatch.

Acceptance reviewer: separate read-only Codex agent; coordinator independently executes declared checks and accepts/integrates candidate. On completion update CURRENT_STATUS.md and move to the first remaining section capability/producer/outputs task with explicit dependencies; do not reopen unchanged duct/air milestones.
