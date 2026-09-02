# Repository-owned workflows

Scripts in this directory are run by the Claude Code `Workflow` tool by name. They are executable supply chain under spec section 11: versioned, reviewed like code, pinned by hash in `REGISTRY.json`, and changed only through a reviewed pull request. The operating model (`docs/roadmap/dynamic-workflow-operating-model.md`) says how they are contracted, verified, recorded, and stopped.

## Running one

Inside a Claude Code session on this repository, with a token ceiling in the launching message (`+300k` for example):

```text
Workflow tool call: { "name": "spec-coverage-audit", "args": { ...JSON value, not a string... } }
```

The tool returns a run id and a transcript directory whose `journal.jsonl` holds every agent's return value. The session records the run under `docs/runs/<run_id>/` (the record tooling of Phase 0 formats it; until then, the run README format used for revision 2 is the template). A run whose status is not `COMPLETE` satisfies no gate.

## Scripts

| Script | Version | Purpose | Reads | Writes | Ceiling / agents | Status |
|---|---|---|---|---|---|---|
| `spec-coverage-audit.js` | 1.0.0 | Map every checkable requirement of chosen spec sections to where the target documents cover it; findings for gaps | repository files | nothing | 150k / 30 | Run in revision 2 (as an inline script; this file is its generalization) |
| `verify-findings.js` | 1.0.0 | Independent skeptics refute or confirm each finding from primary sources; survivors reported with corrections | repository files and the findings JSON | nothing | 200k / 40 | Run in revision 2 (three inline runs) |
| `judge-panel.js` | 1.0.0 | Independent designs from distinct angles, scored by independent judges, synthesized | repository files | nothing | 300k / 12 | Run in revision 2 (one inline run) |

Proposed and not yet written: `harness-probe`, `roadmap-review`, `provenance-audit`, `foundation-task`, `foundation-milestone`, `pr-review`, `acceptance-verify`, `research-triage` (operating model, section 5).

## Rules every script follows

- `export const meta = {...}` is a pure literal; no `Date.now()`, `Math.random()`, argless `new Date()`, `require`, `import`, `fetch`, or `process`.
- Every `agent()` call has a `label` and a `schema`; every prompt states that the repository is read-only for the agent unless the script is a diamond that returns patches.
- Every bound on coverage (chunking, top-N, dropped agents) is logged and turns the status into `PARTIAL`.
- Any string that would merge, approve, force-push, or bypass hooks is forbidden in scripts and prompts: `merge_pull_request`, `pull_request_review_write`, `--force`, `--no-verify`, `HELEOS_BRANCH_GUARD`, `HELEOS_GUARD_ALLOW_SELF_EDIT`.
- Scripts refuse to run without a token ceiling when their policy sets `require_ceiling`; otherwise they log the absence and treat the policy ceiling as advisory.

## Registry

`REGISTRY.json` lists each script with its version, SHA-256, policy (data classes, external providers, network, whether it may edit, ceiling, agent cap, human gate), and the run records that admitted it. The Phase 0 lint (`tools/wf/lint.py`, proposed) fails when a script's hash changes without a version bump.
