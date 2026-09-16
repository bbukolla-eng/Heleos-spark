# Heleos Spark build goal

Replacement text for the saved goal, prepared 2026-09-16. This file does not
change or resume the app's saved goal. Keep immediate task selection in
`CURRENT_STATUS.md` so the goal remains valid as capabilities are completed.

```text
/goal Build Heleos Spark into a working local-first Mac and Windows application that performs complete CSI Division 23 mechanical takeoffs from project drawings, schedules, specifications and revisions, including every section and subsection of the verified Division 23 hierarchy.

Heleos must identify mechanical items, measure supported geometry, calculate quantities, reconcile requirements and produce source-linked takeoff results across equipment, air devices, ductwork, piping, fittings, accessories, controls, insulation and demolition. Include common work, supports and restraints, identification, fuel systems, pumps, air cleaning, central heating and cooling, central and decentralized HVAC equipment, and specification-driven testing, balancing, commissioning, operation and maintenance obligations. Track requirements that are services or deliverables appropriately rather than inventing physical quantities. The user reviews exceptions and corrects results without first performing the takeoff themselves.

Follow the original approved roadmap: mechanical knowledge and rule foundations, drawing coordinates and verified scale, schedule reconciliation, takeoff calculations, correction and recalculation, then estimator-ready Excel and evidence PDF outputs. Reuse completed foundations. Integrate suitable local AI models where needed; model proposals do not override source evidence, approved rules or deterministic calculations.

Resume from the Resume here section of CURRENT_STATUS.md, the selected task ledger and docs/superpowers/specs/2026-09-13-calculation-sequence-design.md. Preserve completed code and verification evidence. Select the next unfinished authorized action in dependency order, deliver a connected capability, run the necessary checks and update the status and ledger before moving forward. Do not restart accepted work or repeat unchanged checks unless a recorded concrete defect, relevant changed input or unresolved acceptance condition justifies it. If one action is blocked, advance ready independent work and retain the blocker explicitly. Do not let status maintenance become a substitute for product work.

The approved calculation order is duct lengths by size, then air-device counts, then equipment counts, using imperial outputs. Duct measurements use drawn centerlines, vertical lengths supported by dimensions or elevations, separate fitting counts, and waste or allowances kept separate from measured totals. D01–D10 and A01–A12 with their approved examples are settled. This is the initial delivery sequence, not a limit on Division 23 coverage. Prepare source-backed rules and checked examples for remaining quantity classes, obtaining only genuinely missing class-specific decisions before their dependent calculations.

Use the NotebookLM and bounded Claude Code workflows in AGENTS.md and the project research instructions. Reuse verified findings, check consequential source passages, log external submissions and independently verify accepted implementation. Codex remains the integration and commit owner. Provider unavailability does not block safe independent work.

Prepare licensed dataset candidates, label mappings, duplicate/source checks and representative evaluation examples alongside implementation where they support the active capability. Establish expected quantities and acceptance thresholds before evaluating results. Track product coverage separately from project applicability: a section absent from one project does not count as an implemented product capability.

Finish when the connected application produces usable, traceable takeoffs on representative projects on both Mac and Windows and the full Division 23 coverage and acceptance register is satisfied. Unfinished sections and subsections, mechanical categories, recognition quality, unresolved acceptance work and native platform verification remain outstanding until completed. Local tests or partial takeoff slices do not establish full-product completion.
```

## Continuation diagnosis

The saved goal inspected on 2026-09-16 was paused and still directed the agent to
finalize duct rules and implement the duct calculation. Those instructions are
obsolete. Root status also contained several historical “next” actions under live
work. The replacement above delegates immediate action to the current status
queue instead of preserving a permanently stale feature assignment in the goal.

No local build-continuation hook was identified in the inspected project and user
configuration. The configured `notify` command invokes the computer-use client's
`turn-ended` action; its internal behavior was not inspected. The read-only
`scripts/active-build-status.py` validates checkout authority and does not choose,
execute or schedule tasks. No hook was disabled or modified, and there is no
verified hook execution trace establishing the cause of repeated work.

The available goal tools can read/create a goal or mark it complete/blocked; they
cannot edit its wording or resume it. Updating the saved goal remains an app-side
action. Do not falsely close or reset the unfinished goal to work around this.
Evidence for this repository-only correction is in
`.heleos/continuation-cursor-2026-09-16/`.
