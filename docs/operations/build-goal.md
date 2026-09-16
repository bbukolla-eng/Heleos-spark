# Heleos Spark build goal

Stable outcome text for the owner to use in the app. Immediate assignments belong
only in [CURRENT_STATUS.md](../../CURRENT_STATUS.md), not in the goal.

```text
/goal Build Heleos Spark into a working local-first application that performs complete CSI Division 23 mechanical takeoffs from project drawings, schedules, specifications and revisions, covering every section and subsection of the verified hierarchy.

Build and verify the connected product on Mac first; retain portable shared code and complete Windows delivery verification afterward. Heleos identifies mechanical items and service obligations, measures supported geometry, calculates quantities, reconciles requirements, supports corrections and revisions, and produces source-linked estimator Excel and evidence PDF outputs. The user reviews exceptions and corrects results without first performing the takeoff themselves.

Cover the complete source-backed taxonomy of each section: required types, variants, attributes, relationships, exclusions, service obligations and child sections. Include equipment, air devices, ductwork, piping, fittings, accessories, controls, insulation, common work, fuel systems, heating/cooling systems, demolition and specification-driven services. Project-specific absence does not complete product coverage.

Resume from CURRENT_STATUS.md and the selected task's ledger. Reuse completed foundations, approved D01–D10, A01–A12 and E01–E12 with their approved examples, imperial outputs and accepted evidence. Work by actual required inputs; unfinished recognition or another category does not block unrelated tasks. Follow the NotebookLM source-verification and bounded Claude implementation workflows in AGENTS.md; Codex independently reviews, verifies, integrates and owns commits.

Complete each task against its frozen deliverable, inputs, independent expected results, tolerances, checks and review authority. Record its result and advance the queue. New requirements create explicit extension tasks or contract versions; accepted work is not repeated without a demonstrated defect or changed relevant input. Apply docs/operations/acceptance.md and the section completion register.

Finish when the complete verified Division 23 register is satisfied and representative projects demonstrate usable, traceable takeoffs on Mac and Windows, with the required engineering and final product acceptance recorded. Unfinished sections, recognition, output or platform work remains outstanding; shared helpers alone do not complete a section.
```

## Verified coordination boundary

During DELIVERY-STRUCTURE-PLAN-1 on 2026-09-16, `get_goal` returned **null**.
Earlier paused-goal snapshots are historical. This file has not created, changed,
resumed or completed an app goal. The prior immediate duct assignment must not be
copied into a new goal; its approved rules and accepted work are already recorded.

No causal build-hook execution trace has been established. The existing
`scripts/active-build-status.py` validates committed checkout/worktree authority;
it neither schedules tasks nor chooses the next mechanical category. The previous
configuration audit found a notify command for the computer-use client's
turn-ended action, not a verified build scheduler. No hook or automation was
changed for this planning task.

The [delivery structure](../superpowers/specs/2026-09-16-division23-delivery-structure.md)
and [maintenance plan](../superpowers/plans/2026-09-16-division23-delivery-maintenance.md)
address demonstrated record and instruction conflicts. Their proposed validator
is not installed or running. CURRENT_STATUS.md remains the single live queue.
