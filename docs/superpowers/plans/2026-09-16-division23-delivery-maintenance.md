# CSI Division 23 structural maintenance plan

**Supersedes the old guide-format ledger/view implementation proposal.** The
[master CSI plan](2026-09-16-division23-section-delivery.md), its section cards and
canonical schema-version-2 register/contracts are the active structure. The
former proposal must not be executed against the superseded schema or selected
instead of mechanical product work.

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans for an explicitly selected bounded change. This document is a maintenance boundary, not a recurring assignment.

**Goal:** Keep the verified CSI identities, parent/child tree, task frames and
linked views consistent without creating another queue or review loop.

**Architecture:** Read-only structural verification checks the canonical register,
contracts and linked cards. It cannot mark a section accepted, schedule work,
change a goal or substitute title coverage for mechanical taxonomy.

**Tech Stack:** Python standard library, JSON and Markdown, using the project's
pinned tooling and the exact verification command recorded in the migration receipt.

**Spec:** [Section task contracts](../specs/2026-09-16-division23-section-task-contracts.md)
and [delivery structure](../specs/2026-09-16-division23-delivery-structure.md).

## Global Constraints

- Root `CURRENT_STATUS.md` is the sole live execution queue.
- Mac-first mechanical capability work remains the implementation priority.
- Approved rules, deterministic calculations and accepted evidence remain authoritative.
- Classifications are pinned to the verified April 2016 baseline; current-2026 reconciliation is explicit.
- No new scheduler, hook, recurring job, provider authorization or release is introduced.
- One writer per named path; only Codex accepts/integrates and commits.

## Active structural check boundary

The migration assigns `scripts/verify-csi-division23.py` and
`tests/test_csi_delivery_structure.py` to its separate bounded implementation
candidate. Its actual acceptance/installation state is recorded in the migration
receipt and root status; this document does not claim a pending candidate has
already passed.

The finite check surface is:

1. Schema version, unique exact CSI IDs and source/page/edition provenance.
2. Verified parent graph, exact inverse child lists and linked card existence.
3. Four task frames per node and exact ordered criterion bindings to nonempty
   expected/check templates.
4. Full baseline row/task counts and removal of old live agency-format cards/IDs.
5. Negative fixtures for malformed relationships, missing cards and criterion drift.

A passing structural check verifies metadata consistency only. It does not freeze
missing numeric oracles, admit a model, accept a mechanical section or confer
commercial catalogue redistribution rights.

## When maintenance is warranted

- [ ] For a changed catalogue edition, retain the authorized primary source,
  independently verify its exact classification tree and record every changed
  number/title/relationship before versioning affected cards and scope.
- [ ] For a changed task scope, update the selected contract/card/criterion bindings
  together; retain the old accepted receipt and state the extension reason.
- [ ] For a recorded acceptance, bind exact receipt/evidence scope to the register,
  regenerate the readable view and update root status in the same checkpoint.
- [ ] Run affected structural checks for those changed bytes, then resume the
  next product action. Do not repeat successful checks on unchanged inputs.

Optional future tools require a concrete routing defect and their own bounded
contract. The prior unscheduled ledger/view proposal is not an outstanding
mechanical requirement and creates no all-section gate.
