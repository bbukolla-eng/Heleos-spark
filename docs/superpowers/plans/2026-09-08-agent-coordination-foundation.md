# Agent Coordination Foundation Implementation Plan

**Status:** Active implementation

**Base commit:** `af526a3c9c7ad93f360b6629e9b592a81787b341`

**Workspace:** `/Users/bekim/Heleos-spark/.worktrees/agent-control-foundation`

## Outcome

Build a deterministic, provider-neutral protocol for bounded AI-worker assignments and handoffs. This is engineering tooling for the research and implementation lanes; it does not dispatch providers, execute returned commands, write production truth, bypass Foundation 0.1 release gates, or merge into the current release candidate.

The first executable finish line is a new `heleos-worker-protocol` Rust package that validates strict JSON task and handoff documents and emits their canonical SHA-256 identities. Codex, Claude Code, Kimi, coding-capable Grok, and Cursor may receive write-capable implementation tasks. NotebookLM and GrokBots/Athena remain research-only providers.

## Frozen decisions

- Schemas are `heleos.worker-task/v1`, `heleos.worker-handoff/v1`, and `heleos.worker-validation/v1`.
- JSON records use `serde(deny_unknown_fields)` and canonical JCS before hashing.
- A task binds its provider, mode, exact 40-character lowercase Git base commit, bounded objective, sorted unique project-relative allowed and forbidden paths, input data class, egress policy, instruction-file SHA-256 map, acceptance commands, and nonzero action/time limits.
- `SECRET` is never admitted. `INTERNAL` and `PROJECT_CONFIDENTIAL` require `local_only` egress. `PUBLIC` may use `approved_external` only when an explicit provider is named by the packet.
- `notebook_lm` and `grok_bots` accept only `research` mode. The other named providers accept `implementation` or `research`.
- A handoff binds the task digest, provider, terminal state, changed paths, deterministic check records, optional candidate commit, and bounded unresolved items. Validation is performed against the original task; changed paths must be within its allowlist and the provider/task identity must match.
- The package never invokes a shell, Git, a provider, a network, or a production database. Its CLI only reads the two named local JSON files and writes one validation envelope to standard output or one bounded diagnostic to standard error.
- No new third-party package is admitted: use only already-pinned workspace dependencies.

## Current implementation tasks

1. Add `crates/heleos-worker-protocol` to the workspace and implement the strict library types, validation, canonical serialization, and hashing test-first.
2. Add a thin `heleos-worker-protocol` CLI with `task <file>` and `handoff --task <file> <file>` commands, tested black-box before implementation.
3. Add public synthetic task/handoff fixtures and an operations guide showing write-capable and research-only routes without credentials or private project data.
4. Replace the milestone-only roadmap with a decision-complete current/future map that names entry, exit, evidence, and blocked/parallel work for every milestone.
5. Run formatting, package tests, strict Clippy, locked/offline workspace metadata, provenance, and changed-file checks; then commit the isolated branch. Do not merge it into the Foundation 0.1 release candidate until that candidate is accepted.

## Future implementation sequence

1. Persist validated task and handoff identities in the governed operational store through an explicit migration.
2. Add deterministic leases, idempotent claims, deadlines, cancellations, and action/cost accounting.
3. Add provider adapters one at a time behind the same protocol, beginning with local write-capable CLIs and keeping NotebookLM/GrokBots research-only.
4. Add quarantined patch and research-artifact ingestion, exact-base application, and test-gated promotion.
5. Add UI status and owner approval surfaces only after the controller behavior is proven headlessly.

## Acceptance

- Tests visibly fail for missing task validation and missing CLI behavior before production code is added.
- Valid task and handoff fixtures produce stable canonical bytes and digests across two physical roots.
- Unknown fields, invalid schema/provider/mode/base/path/data/egress/limit combinations, task-provider mismatch, out-of-scope writes, and noncanonical duplicate paths fail closed.
- The CLI performs no command execution and contains no provider SDK or network dependency.
- `cargo +1.96.1 test --locked --offline -p heleos-worker-protocol` and strict package Clippy pass with zero ignored tests.
- Existing Foundation source, manifests other than the workspace membership/lock record, SBOM, release scripts, workflow gate, and GitHub App registry remain unchanged.
