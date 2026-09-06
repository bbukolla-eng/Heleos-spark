# Orchestration preflight: compaction-safe multi-engine engineering control plane

**Date:** 2026-08-30

**Scope:** Read-only architecture audit plus this ignored report

**Worktree:** `/Users/bekim/Heleos-spark/.worktrees/foundation-0.1`

**Recommendation:** Hybrid governed control plane

**Activation status:** Design only; do not activate against the Foundation worktree during Task 6

## Executive decision

Use a **hybrid governed control plane**: one deterministic local controller owns the authoritative task state, the integration worktree, its index, and the Foundation branch; provider adapters are replaceable workers or reviewers behind schema, data-class, path, budget, and isolation gates. Harness-native hooks are convenience triggers, never the source of truth. Durable local checkpoints and an exclusive controller lock provide compaction and crash recovery.

Do not deploy full distributed automation. The repository's tracked design already requires a deterministic controller, names Codex as sole coordinator and merge owner, and treats every model/provider result as a candidate rather than authority. The tracked engineering-research plan currently prohibits private repository material, diffs, and code-derived prompts from entering NotebookLM, Grok Bot, Claude Code, Kimi, Grok, Cursor, or another external provider under that plan. Non-controller engines must therefore remain disabled for private Heleos work until the owner approves a reviewed policy amendment.

Task 6 has an especially strict boundary: exactly eighteen authorized paths are held unstaged and uncommitted. This report is ignored by `.superpowers/sdd/.gitignore`; no control-plane file, hook, plugin, package, Git configuration, or provider was installed or activated.

## Evidence and authority snapshot

### Authoritative repository state

- HEAD: `f67b9b7d67e1f726defd72b0c3ca9a23c42f785d`.
- Authoritative plan: `docs/superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md`.
- Plan Git blob: `bd855d63fec241df80cebc877345a4a273c53429`.
- Plan SHA-256: `d3f3ef97bac1bef279b1085c3d6bf9ab8abb39c7eaa81caefab15314b764b235`.
- The ignored SDD ledger names Task 6 as in progress. It is useful recovery evidence but is not a tracked authority.
- The plan, committed code, reviewed diffs, and owner decisions outrank ignored ledger/report state. Provider memory, session transcripts, model summaries, and raw outputs are untrusted inputs.
- The Git index was empty at audit time.
- `git status --porcelain=v1` reported thirteen entries representing all eighteen Task 6 paths: nine tracked modifications, one untracked directory containing six files, and three other untracked files.
- No active repository Git hook exists. The common Git directory contains only sample hooks, and `core.hooksPath` is unset.

### Protected Task 6 path set

No rollout phase may alter, format, stage, commit, restore, or regenerate any of these paths until the current Task 6 controller completes its existing review/commit flow:

1. `crates/heleos-core/migrations/0001_foundation.sql`
2. `crates/heleos-core/src/lib.rs`
3. `crates/heleos-core/src/error.rs`
4. `crates/heleos-core/src/pdf/mod.rs`
5. `crates/heleos-core/src/pdf/geometry.rs`
6. `crates/heleos-core/src/pdf/wasi_host.rs`
7. `crates/heleos-core/src/store/mod.rs`
8. `crates/heleos-core/src/vault/mod.rs`
9. `crates/heleos-core/src/store/ingest_repository.rs`
10. `crates/heleos-core/src/ingest/mod.rs`
11. `crates/heleos-core/src/ingest/job.rs`
12. `crates/heleos-core/src/ingest/audit.rs`
13. `crates/heleos-core/src/ingest/evidence.rs`
14. `crates/heleos-core/src/ingest/inspection.rs`
15. `crates/heleos-core/src/ingest/fault.rs`
16. `crates/heleos-core/tests/migrations.rs`
17. `crates/heleos-core/tests/ingest.rs`
18. `crates/heleos-core/tests/restart.rs`

The current Task 6 report freezes a SHA-256 and Git-blob pair for each path. A future pre-edit or rollout gate must verify all eighteen values against that report or, after Task 6 is committed, against the reviewed Task 6 commit. Directory-level status counts are not sufficient.

### Binding architecture and security constraints

- Codex is the sole coordinator and merge owner.
- A deterministic controller, not an LLM, owns permissions, budgets, leases, timeouts, idempotency, state transitions, and stop conditions.
- Workers receive a frozen base, exact objective, allowed paths/tools, acceptance commands, forbidden actions, and time/action/output/cost limits.
- No worker pushes, merges, self-approves, changes release state, edits credentials, or promotes production truth.
- Default local runtime/test operation has no network egress.
- `SECRET` never leaves the owner-controlled secret store and never enters repository state, prompts, logs, command arguments, provider output, or checkpoints.
- `INTERNAL` and `PROJECT_CONFIDENTIAL` external egress is denied in Foundation 0.1.
- Only governed `PUBLIC` packets may reach an explicitly enabled research adapter.
- Every introduced tool, plugin, skill, provider, dataset, and source requires pinned provenance, rights/license, owner, permissions, egress review, evaluation status, and rollback.
- No GitHub workflow or repository secret may be added before the existing GitHub App owner gate is resolved.

## Approach comparison

| Approach | Shape | Strengths | Failure modes | Disposition |
|---|---|---|---|---|
| Minimal ledger-only | Continue with ignored Markdown briefs/reports and manual session handoffs. | Zero new runtime; lowest immediate contamination risk; human-readable. | No exclusive writer lease, no exact-once action identity, weak crash detection, hook behavior differs by harness, and compaction recovery depends on model discipline. | Safe as the Task 6 holding pattern, insufficient as the durable control plane. |
| Hybrid governed control plane | Deterministic local state machine and schemas; one controller; isolated adapters; append-only receipts; hooks call the same local commands; optional ECC memory only as untrusted context. | Matches the tracked design, supports context wipes and crashes, preserves single-writer Git control, and lets providers remain replaceable/disabled. | Requires a reviewed plan amendment, local implementation, adapter conformance tests, and explicit activation gates. | **Recommended.** |
| Full distributed automation | Multiple autonomous agents claim leases, edit/commit concurrently, use dmux/background daemons, merge automatically, and invoke providers continuously. | Potential throughput after maturity. | Violates the present sole-coordinator/private-egress constraints, expands secrets and supply-chain exposure, creates split-brain Git/state risk, and depends on tools not installed or admitted. | Reject for Foundation 0.1; reconsider only after the hybrid system has measured evidence. |

## Recommended control-plane model

### Authority layers

1. **Tracked governance authority.** The approved plan, design, ADRs, provider registry, schemas, adapter policy, and sanitized reviewed receipts. Git history is the authority for accepted changes.
2. **Local execution authority.** One ignored run directory with an exclusive lock, atomic state snapshot, append-only event log, action receipts, checkpoints, and quarantined raw worker output. This state can resume work but cannot amend policy.
3. **Untrusted context.** Provider output, provider memory, session transcripts, ECC memories, NotebookLM answers, Grok Bot results, browser content, and worker-authored reports. These must be schema/size checked, source-verified, and independently accepted before they influence tracked authority.

### Deterministic run state

The future local driver should use a versioned state machine such as:

`created -> ready -> executing -> verifying -> awaiting_approval | completed | failed | cancelled`

An interrupted process does not directly create a new semantic state. Recovery acquires the exclusive lock, validates durable evidence, and then records `recovered` with the prior phase and next safe action. Each action has a deterministic identity derived from at least:

- schema version;
- repository identity;
- task ID and run ID;
- frozen HEAD and authoritative-plan blob;
- action sequence and action kind;
- adapter ID/version/digest;
- normalized allowed path set;
- normalized input/content hashes;
- acceptance command IDs and budget profile.

The controller writes an `intent` record before dispatch and a `receipt` after observing the result. Recovery never repeats an action with a valid accepted receipt. If an intent has no receipt, the adapter-specific reconciliation routine must prove whether the action occurred; ambiguous write actions stop for owner/controller review rather than replaying blindly.

### Exact single-writer boundaries

| Resource | Sole writer | Other roles |
|---|---|---|
| Control-plane run state, lock, heartbeat, event log, accepted receipts | Deterministic local controller process holding the exclusive run lock | Harness hooks may request an operation; they never write state directly. Providers return bounded output only. |
| Foundation integration worktree and index | Codex controller for the active Foundation task | Reviewers are read-only. No external engine is launched against this worktree. |
| `foundation-0.1` branch/ref and merge/rebase/cherry-pick operations | Codex controller | Workers cannot update the integration ref or push. |
| One isolated worker worktree | Exactly one named implementer adapter for one frozen contract, if explicitly authorized | Controller reads the result; reviewers use immutable snapshots. No second writer shares that worktree. |
| One ephemeral worker branch/ref | Its one named implementer only if the contract explicitly permits a worker commit; otherwise no worker Git-ref write | Controller remains sole integrator and may accept a patch into the integration branch after review. |
| Review snapshot | No writer | Review engines inspect a frozen tree/diff package and return findings; they cannot fix what they review. |
| Provider registry, policy, schemas, ADRs | Controller through a separately planned/reviewed tracked commit | Models may propose text but cannot directly promote it. |
| ECC project memory | The controller via a bound local runtime identity, when admitted | Memories are create-only, unreviewed context. They cannot grant authority or mutate the task state. |
| NotebookLM/Grok Bot/browser research | Owner-gated research adapter on approved `PUBLIC` source sets | No filesystem/Git write, no private code, no production authority. |

At most one process may hold the run lock. A PID file alone is not a lock. Use a retained OS advisory lock on a fixed owner-private file; the heartbeat is observability, not takeover authority. A new controller may recover only after it can acquire that same lock.

## Artifact placement

### Tracked after a reviewed plan amendment

Reuse the engineering-research plan's already declared paths rather than creating parallel provider policy:

- `governance/agents/providers.toml`
- `governance/agents/worker-contract.schema.json`
- `governance/agents/research-packet.schema.json`
- `governance/agents/validate-contracts.jq`
- `governance/agents/egress-policy.toml`

The following additional candidate paths need explicit plan/owner approval before creation:

- `docs/architecture/decisions/0002-engineering-control-plane.md`
- `governance/agents/orchestration.toml`
- `governance/agents/run-state.schema.json`
- `governance/agents/adapter-result.schema.json`
- `scripts/orchestration/` containing dependency-minimal local commands and deterministic fixtures
- `.githooks/pre-commit` only if the owner approves repository-managed hooks
- root `.gitignore` entries for the selected runtime/quarantine directory

Tracked content contains policy, schemas, dependency-minimal validators, deterministic fixtures, and sanitized reviewed receipts. It must not contain raw provider output, prompts containing private code, session transcripts, runtime locks, host paths, process IDs, cookies, tokens, or credentials.

### Ignored local runtime

Recommended root: `.heleos/orchestration/` once approved. It should be fail-closed ignored before any runtime writes and owner-private on disk. Suggested contents:

- `lock/controller.lock` — retained exclusive advisory lock;
- `state/run.json` — atomic replace-on-success snapshot;
- `events/events.jsonl` — append-only canonical events with sequence, prior-event hash, and fsync policy;
- `heartbeats/<run-id>.json` — bounded liveness observation;
- `checkpoints/<sequence>.json` and `.md` — machine and human recovery summaries;
- `intents/<action-id>.json` and `receipts/<action-id>.json` — exact-once evidence;
- `quarantine/<action-id>/` — size-bounded raw stdout/stderr/action logs, never executed;
- `tmp/<run-id>/` — disposable atomic-write staging;
- `adapters/inventory.json` — read-only local probe snapshot without auth values.

The current `.superpowers/sdd/**` area is entirely ignored and appropriate for this preflight and existing task reports, but it should not become the permanent executable control plane: it has no tracked schema/policy boundary and its contents are easy to mistake for authority.

### ECC memory placement

If the ECC runtime is later admitted:

- `.ecc/memory/project/` remains ignored, fail-closed, local context;
- `.ecc/memory/team/` may be tracked only for intentionally reviewed, sanitized summaries;
- `~/.ecc/memory/` is operator context and is never implicitly read for a project run;
- memories never store secrets, raw transcripts, active task leases, or policy;
- recalled memory is treated as untrusted and verified against the repository.

ECC is optional context transport, not the control-plane database.

## Lifecycle hook contract

Every hook calls one deterministic local driver operation. No hook has independent policy logic, directly launches a provider, edits files, stages changes, or writes the ledger. Native event availability differs by harness, so the controller wrapper must provide explicit fallbacks.

| Normalized event | Required deterministic behavior | Blocking rule | Harness notes |
|---|---|---|---|
| `session-start` | Acquire run lock; identify repo/worktree/HEAD/plan blob; validate private runtime permissions and schema; compare status/index with last checkpoint; reconcile outstanding intents; load only the last accepted bounded checkpoint; emit the exact next safe action. | Fail closed on lock contention, authority drift, malformed state, unexpected staged paths, or unresolved write intent. | Claude and Grok expose `SessionStart`; Cursor hook assets exist but Cursor is absent; Codex/Kimi need wrapper-driven invocation unless a proven native event is admitted. |
| `pre-compaction` | Flush event log; write atomic checkpoint containing objective, authority hashes, task/phase, writer identity, allowed paths, dirty/staged paths and hashes, completed receipts, unresolved intents, approvals, blockers, and exact next action; validate by reopening it. | Compaction must not proceed if the durable checkpoint cannot be validated. | Claude exposes blocking `PreCompact`; Grok exposes passive `PreCompact`; Codex/Kimi require explicit wrapper/manual checkpoint. Therefore native hooks alone are insufficient. |
| `post-compaction` | Reopen the checkpoint by ID/hash; compare repository authority and lock ownership; reconstruct the minimal prompt/context; require the controller to acknowledge the exact next action before any edit/dispatch. | Fail closed on missing/mismatched checkpoint or repository drift. | Claude and Grok expose `PostCompact`; other adapters need an explicit resume-check immediately after compaction/continuation. |
| `pre-edit` | Canonicalize the target without following an unsafe final link; require the active writer lease; check the exact allowed-path set, task/base/plan hashes, protected path rules, clean index expectations, and action budget; record edit intent. | Deny edits outside the contract, edits by reviewers, concurrent writers, or edits while a prior write is ambiguous. | Map from Claude/Grok `PreToolUse` for edit/write tools and Cursor `before*`; Kimi's configured `PreToolUse` hook is currently broken by a stale path; Codex needs the local editing wrapper/gate. |
| `post-edit` | Enumerate actual changed/untracked paths from Git; reject path expansion; run only cheap deterministic checks (`git diff --check`, size/cap checks, targeted format check when declared); hash changed paths; write receipt and checkpoint. | Do not auto-revert. Freeze the run and report unexpected changes. | Map from `PostToolUse`/`afterFileEdit`; never trust the tool's claimed path list without Git reconciliation. |
| `pre-commit` | Require controller/authorized worker ref ownership; verify exact declared staged path set, no ignored/raw runtime content, frozen base and plan, accepted test receipts, no unresolved intent, no secret canaries, `git diff --cached --check`, and human-readable cached diff/name review. | Fail closed. No `--no-verify` path. No hook may stage files. | Repository currently has no active hook. A future tracked `.githooks/pre-commit` plus local `core.hooksPath` activation needs owner approval and tests. The controller still runs the gate explicitly because hooks can be bypassed or absent. |
| `stop` | Flush events; reconcile background work; create a terminal/paused/blocked checkpoint; verify no provider/action is still running; record index/status; release the run lock only after persistence succeeds. | A hook may delay a normal stop, but forced interrupt/API failure cannot be assumed to fire it. Heartbeat recovery must cover missed stops. | Claude/Grok support stop gates with limits and fail-open cases. Codex currently has only turn-end notification evidence, not a proven blocking lifecycle gate. |

Post-edit formatting must never run over the whole repository or modify a file outside the active path contract. Existing generic ECC stop hooks include JS/TS formatting behavior and therefore must not be activated wholesale in this Rust worktree without a reviewed, task-specific profile.

## Heartbeat and recovery loops

### Heartbeat loop

- Controller retains the exclusive lock for the entire run ownership interval.
- Write a heartbeat every 15 seconds and immediately before and after each external process/tool boundary.
- Heartbeat fields are bounded and non-secret: schema, run ID, controller instance nonce, task, phase, monotonic action sequence, UTC observation time, process identity evidence, HEAD, plan blob, last event hash, and current intent ID.
- Write to a same-directory temporary file, fsync as specified, atomically replace, reopen, and validate.
- Ninety seconds without an update is **stale for alerting only**. It does not permit takeover while the OS lock remains held.
- Heartbeat write failure freezes new dispatch/edit actions and moves to a safe checkpoint attempt; it does not silently continue.

### Controller recovery loop

1. Acquire the same exclusive run lock non-blocking. If another process holds it, report the owner evidence and stop.
2. Validate runtime directory ownership/permissions, file types, size/count bounds, schemas, event sequence/hash continuity, and snapshot/event agreement.
3. Recompute repository identity, HEAD, plan blob, current branch/worktree, index, dirty/untracked paths, and protected-path hashes.
4. If authority or path state differs from the checkpoint, enter `awaiting_approval`; never reset, checkout, clean, stage, or replay automatically.
5. Reconcile the last intent:
   - read-only action: safe to re-run only when its contract declares idempotent;
   - provider call: query only a local durable job ID/status if the adapter supports it; otherwise mark ambiguous and do not resubmit;
   - edit: inspect Git/path hashes to decide whether the edit receipt can be reconstructed;
   - commit: inspect exact object/ref state before any retry;
   - external side effect: never retry without owner approval.
6. Emit a recovery event and fresh checkpoint, then continue from the exact declared next action.

### Worker recovery loop

- Every worker action has a maximum wall time, output bytes, tool/action count, and optional cost bound.
- Poll durable local worker state at a bounded cadence; do not parse live prose as status authority.
- On timeout, interrupt the process, capture bounded output, and mark the action failed/ambiguous according to adapter evidence.
- Never let a worker heartbeat renew the controller lease.
- A worker crash leaves its worktree frozen for inspection. Only the controller decides reuse, patch extraction, or disposal.
- No recovery routine deletes a worktree, branch, quarantine result, or unknown file automatically.

## Local harness, plugin, and hook inventory

This inventory is based only on local paths, local `--version`/`--help` output, and redacted configuration shapes. Authentication was not probed and no provider API was invoked.

| Surface | Observed local capability | Admission/use decision |
|---|---|---|
| Codex | `codex-cli 0.147.0` at `/Users/bekim/.local/node/bin/codex`; `exec`, resume, JSON/JSON-schema output, sandbox selection, cwd, and output-file options are present. Current user config enables multi-agent plus Grok/Kimi plugins and uses a turn-end notification. | Existing controller surface. Do not treat notification as lifecycle governance. Adapter must force task-specific sandbox/approval settings rather than inherit permissive user defaults. |
| Claude Code | `2.1.226`; print/stream-JSON, resume/continue, permission mode, settings isolation, agents, and strict MCP config are available. User settings have no direct hooks. ECC `2.2.0` is cached/installed only for another project and is not enabled for this repo. | Candidate adapter, disabled for private Heleos by current egress policy. If admitted later, run with isolated settings and no inherited plugins/MCP by default. |
| Grok CLI | `0.2.111 (94172f2aa4e5)`; headless JSON/streaming JSON, resume/fork, schema output, sandbox, memory toggle, worktree, hooks, and sessions exist. Grok supports `SessionStart`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop`, `StopFailure`, `SubagentStart/Stop`, `PreCompact`, and `PostCompact`. User config currently says `permission_mode = "always-approve"`, remembers approvals, and enables ten plugins. | High-risk inherited configuration; disabled for private Heleos. A future adapter must use isolated config/home, explicit sandbox/permission mode, no cross-session memory, and a plugin allowlist. Grok hook failures generally fail open, so hooks cannot enforce the control plane. |
| Grok Codex plugin | Enabled Codex plugin `grok-plugin-codex` version `0.2.0+codex.20260711001630`; bounded MCP/job source and `grok` skill are present. | Installed capability only; no call made. Disabled for private repo and not authoritative. Must be governed/pinned before any allowed public-synthetic use. |
| Kimi CLI | `0.34.0`; prompt mode, JSON/stream-JSON, session resume, agent profiles, and extra directory options exist. | Candidate adapter, disabled for private Heleos by current egress policy. |
| Kimi Codex plugin | Enabled Codex plugin `kimi` version `1.9.9` provides ask/challenge/review/status/rescue/pursue/swarm skills. | Installed but not ready: Kimi config's only `PreToolUse` approval hook points to removed plugin path `1.9.8`; the corresponding `1.9.9` hook exists. Do not repair without approval; treat Kimi write delegation as disabled. |
| Cursor | No `cursor` executable, `~/.cursor` directory, or Cursor application observed. ECC includes reusable Cursor hook assets only. | Unavailable. Installation/admission is an owner gate already named by the research plan. |
| NotebookLM | No CLI/MCP/application observed. The tracked research plan requires an owner-authenticated official browser session and private-sharing/rights checks. | Unavailable/unverified locally; research-only, `PUBLIC` approved sources, never control-plane state or code authority. |
| Grok Bot | No local CLI/config/application matching Grok Bot observed. The tracked plan requires official-platform discovery and an owner login/privacy gate before use. | Unavailable/unverified; public-data adversarial research only. |
| dmux/tmux | Neither `dmux` nor `tmux` is on `PATH`. A local `dmux-workflows` skill and ECC dmux session-adapter source exist. | Guidance/source assets only; runtime unavailable. Do not install during Task 6. Even if admitted later, dmux manages panes, not authority, leases, or merge safety. |
| ECC | ECC plugin `2.2.0` is cached for Claude and includes 285 skills, reusable hooks, session adapters, Git-hook assets, and Memory Vault design/runtime source. Codex-local `unified-memory` and `dmux-workflows` skill files exist but differ from the ECC 2.2.0 copies. No `ecc`, `ecc-memory-mcp`, or `~/.ecc` exists. | Skill guidance is available; runtime memory/CLI capability is absent. Version drift between skill copies must be resolved before admission. Do not label the cached plugin or account-level GitHub App as a callable project control plane. |
| Repository hooks | No active Git hooks; only sample files in the common Git directory. No repo `.grok/hooks` or `.cursor` hooks. | Hook activation gap. Implement only after tracked contracts/tests and owner approval. |

### Relevant installed skill catalog

- Local orchestration/recovery guidance: `unified-memory`, `dmux-workflows`, `agent-introspection-debugging`, `eval-harness`, `verification-loop`.
- Cross-engine guidance: `grok-codex-collaboration`, `kimi-codex-collaboration`.
- Codex Grok plugin: `grok`.
- Codex Kimi plugin: `kimi-ask`, `kimi-challenge`, `kimi-review`, plus status/rescue/pursue/swarm/result/replay/cancel/setup skills.
- Governance/security/testing: Superpowers `6.3.0`, Codex Security `0.1.22`, and local TDD/verification/security skills.
- Claude has enabled Codex, GitHub, Grok Build, Hookify, and Superpowers plugins, among others; this is not a safe adapter allowlist for Heleos.
- Grok has enabled council, skill-creator, PR-review, Obsidian, Grok Build, Codex, UI/UX, Superpowers, theme designer, and GitHub plugins; this is not a safe adapter allowlist for Heleos.

Catalog presence is not admission. Before an adapter uses any item, pin its exact version/digest/origin, license/rights, permissions, egress, evaluation, owner, and rollback in governed files.

## Provider adapter contract

Every provider adapter should implement the same narrow local interface:

- `probe`: local executable/config capability only; no authentication request and no provider call;
- `prepare`: create a fresh isolated workspace/config/home and validate the frozen `WorkerContract`;
- `dispatch`: use a fixed executable path and argument array or stdin, never a shell string;
- `poll`: return structured local job status without treating model prose as state;
- `collect`: enforce output/time/action limits, hash raw output, and validate the adapter-result schema;
- `cancel`: terminate only the exact owned process/job;
- `reconcile`: determine local completed/running/absent/ambiguous state after controller recovery;
- `dispose`: owner/controller-approved cleanup only after retained evidence is accepted.

Common mandatory controls:

- isolated worktree or public-synthetic disposable workspace;
- fresh isolated provider config/home; no host home, keychain, Git helper, browser cookies, ambient MCP, inherited plugins, cross-session memory, or unrelated directories;
- allowlisted environment variables and endpoints;
- no credential in args, prompts, output, state, logs, or checkpoints;
- owner-mediated authentication only inside an admitted disposable runner;
- schema-constrained final result, bounded raw stdout/stderr, and canonical hashes;
- no direct edit of the integration worktree, index, Foundation ref, tracked governance, secrets, or release state;
- read-only reviewer adapters receive immutable snapshot/diff packages and cannot repair findings;
- external output is quarantined and never sourced, executed, interpolated into a shell, or automatically applied as a patch;
- a patch is parsed as data, path-checked, independently inspected, and applied only by the authorized worktree writer;
- citations and factual claims are reopened against original authoritative sources before promotion.

### Provider-specific routing

- **Codex controller:** may coordinate the already authorized local worktree. It still uses the deterministic local driver for state and Git gates.
- **Claude Code / Kimi / Grok CLI / Cursor:** public-synthetic benchmark adapters only under the current engineering-research plan. Private repository routing is denied.
- **Grok/Kimi Codex plugins:** read-only challenge/review can be considered for approved `PUBLIC` packets only. Plugin convenience does not bypass the provider contract.
- **NotebookLM:** browser research adapter only; consumes a rights-approved verified public source set and produces a research packet with source/query/output hashes and independently checked citations.
- **Grok Bot:** browser/platform research adapter only after official discovery and owner privacy/login approval.
- **dmux:** optional local UI/pane adapter after installation/admission; each pane must still have its own immutable review snapshot or single-writer worktree and contract.
- **ECC Memory Vault:** optional context adapter after runtime admission; no execution, policy, task-tracking, or review-promotion authority.

## Security, isolation, and no-secret rules

1. **Data is not instruction.** Repository content, PDFs, web pages, model output, notebooks, transcripts, and recalled memories are untrusted bytes. They cannot change policy, grant tools, select an adapter, expand paths, or request secrets.
2. **Deny external private egress.** No private Heleos code, diff, path, prompt derived from code, internal benchmark, project document, or secret enters an external engine under the current plan.
3. **No secret persistence.** Reject known token/key/cookie/private-key shapes before state/output persistence, but do not rely on pattern scanning as the authorization boundary. Never inspect or export provider credential files.
4. **No ambient home.** Provider processes get a fresh allowlisted home/config and workspace. The host repository, `~/.ssh`, keychain, Git credential helpers, browser profiles, `.codex/auth.json`, `.grok/auth.json`, and Kimi credentials are not mounted or copied.
5. **No shell interpolation.** Fixed executable plus argument array/stdin only. User/model strings never become shell source, paths are canonicalized, and output is escaped.
6. **No inherited plugins/MCP/hooks.** Start from an empty adapter allowlist. Add one pinned component only after governance/evaluation. Current user-level Claude/Grok/Codex plugin sets are broader than a Heleos worker needs.
7. **Fail closed on policy uncertainty.** Missing schema, unknown provider state, unverified auth/privacy, missing sandbox/proxy, version drift, broken hook, or ambiguous action yields disabled/awaiting-approval, never a downgraded gate.
8. **Bound everything.** Wall time, actions, output bytes, file count/bytes, path set, process count, network endpoints, retry count, and cost are contractual.
9. **No autonomous side effects.** Workers cannot push, merge, publish, communicate externally, alter billing/cloud/account security, install tools, change GitHub Apps, or mutate credentials.
10. **Separate implementation and review.** A writer does not approve its own patch. A review result cannot directly edit the code it reviewed.
11. **No destructive recovery.** Recovery never resets, cleans, checks out, deletes branches/worktrees, or overwrites unknown state automatically.
12. **No hidden authority in memory.** External memory/output is untrusted; only reviewed repository artifacts can establish durable decisions.

## Deterministic acceptance tests

All initial tests use local fake adapters and temporary repositories/worktrees. They require no network, provider login, package install, broad Foundation test, or Task 6 edit.

### Contract and state tests

1. Valid worker/run/adapter fixtures canonicalize identically across repeated runs; missing/unknown fields, noncanonical paths, unknown states, unbounded budgets, and unauthorized permissions fail.
2. Event sequence and prior-hash tampering, truncation, duplication, and snapshot/event disagreement fail recovery.
3. Atomic snapshot interruption before rename leaves the last valid snapshot readable; interruption after rename yields the new valid snapshot.
4. Secret canary fixtures for token, private key, cookie, credential path, and forbidden private path are rejected from prompts, state, receipts, and sanitized reports.
5. Oversized stdout/stderr, too many files, path traversal, symlink final components, device files, and invalid UTF-8 are bounded or rejected without execution.

### Single-writer and Git tests

6. Two controllers race for one run lock: exactly one acquires it; the loser performs no state, edit, stage, ref, or provider action.
7. A stale heartbeat with a held lock cannot be stolen. An acquirable lock plus stale state enters recovery rather than starting a new run.
8. Pre-edit accepts every exact allowed path and denies sibling/prefix-confusion paths, symlink escapes, case/normalization aliases, `.git`, ignored runtime, and protected Task 6 paths.
9. Post-edit detects a worker that claims one file but changes two; it freezes without reverting either.
10. Pre-commit rejects missing/extra staged paths, ignored/raw outputs, unresolved intents, changed plan/base, absent receipts, whitespace errors, and `--no-verify` attempts.
11. Reviewer mode cannot write a file, index, ref, run state, or accepted receipt.

### Crash and exact-once tests

12. Terminate after durable intent but before fake dispatch: recovery safely dispatches once only when the adapter contract says absent/idempotent.
13. Terminate after fake dispatch but before receipt: local durable job reconciliation records the existing result and does not dispatch twice.
14. Terminate after edit but before post-edit receipt: recovery reconstructs the exact diff/hash receipt or stops on ambiguity.
15. Terminate after Git commit succeeds but before controller receipt: recovery identifies the exact commit/ref and never creates a duplicate commit.
16. Unknown external-side-effect outcome is never retried automatically.

### Hook parity tests

17. Feed the same normalized fixture to session-start, pre/post-compaction, pre/post-edit, pre-commit, and stop adapters for Codex wrapper, Claude, Grok, Kimi wrapper, and Cursor-compatibility modes. Each must request the same driver transition and produce no direct state write.
18. Hook timeout/crash/malformed output cannot bypass explicit controller gates. A native fail-open hook is detected by the wrapper's explicit gate.
19. Stop omission on forced process termination is recovered by lock/heartbeat/state logic.

### Required simulated context-wipe test

20. Create a temporary Git repository and a fake two-step task. Start a controller run with a frozen plan/base and one allowed file. Complete step one, then invoke `pre-compaction` and validate its checkpoint.
21. Destroy all in-memory controller/model context and session-local prompt state. Do not preserve a transcript or hidden summary. Terminate the original controller process so its lock is released.
22. Start a new controller instance using only the tracked fixture plus ignored durable control-plane files. It must acquire the lock, verify the event chain/repository state, load the bounded checkpoint, state the exact next action, and complete step two without redoing step one.
23. Compare the final file tree, Git/index state, accepted receipts, semantic event sequence, and final state with an uninterrupted control run. They must match exactly except for explicitly normalized controller-instance/recovery observations.
24. Repeat with deliberate HEAD drift, plan drift, unauthorized dirty file, staged file, corrupt checkpoint, missing receipt, and a held lock. Every variant must fail closed at the named boundary.

### Task 6 non-contamination test

25. Before any rollout action in this worktree, compare HEAD/plan blob, empty index, exact eighteen-path set, and all eighteen frozen hashes to the current approved Task 6 boundary. The dry-run must make zero tracked/untracked change outside its temporary directory.
26. After Task 6 is reviewed and committed, repeat against the Task 6 commit, require a clean worktree/index, and create the control-plane work on a new separately reviewed task/commit only.

## Phased rollout

### Phase 0 — current hold

- Keep this report ignored and unstaged.
- Do not install, configure, activate, or test hooks/plugins/runtimes.
- Do not launch a provider, create a worktree, alter Git configuration, or run formatting/tests.
- Let the existing Task 6 controller review and commit exactly its eighteen authorized paths.

### Phase 1 — tracked policy only

- After Task 6 is complete and clean, amend the authoritative plan and add a dedicated control-plane task with exact files and stop rules.
- Implement the already planned provider/worker/research schemas first; add the control-plane ADR/state/adapter schemas only if approved.
- Record every adapter/plugin/skill/runtime in governance before use.
- No provider calls and no hooks yet.

### Phase 2 — local fake-adapter skeleton

- Build the dependency-minimal controller, lock, state/event/checkpoint formats, path gates, and local fake adapter.
- Run only the deterministic acceptance suite in temporary repositories.
- Pass the simulated context wipe and all crash windows.
- Keep all harness hooks in shadow/report-only mode.

### Phase 3 — explicit local controller gates

- Put session-start, pre/post-edit, pre-commit, and checkpoint operations in the controller wrapper.
- Activate repository-managed Git hooks only after owner approval; keep explicit controller invocation authoritative.
- Use Codex as the sole controller. No non-controller provider receives private Heleos data.

### Phase 4 — public-synthetic provider evaluation

- Complete the engineering-research plan's provider inventory, sandbox/egress proof, boundary compliance, and deterministic benchmark packets.
- Fix or remove the stale Kimi approval-hook configuration through a separately approved user-config action.
- Admit only exact versions/digests and isolated configurations that pass.
- NotebookLM and Grok Bot remain owner-login/privacy-gated and public-research-only.

### Phase 5 — optional read-only multi-engine review

- Only if policy permits the relevant data class, add one provider at a time for read-only immutable snapshot/diff review.
- Require independent local validation before any finding enters the task ledger.
- Measure false positives, boundary compliance, cost, latency, recovery, and prompt-injection resistance.

### Phase 6 — optional isolated writers and dmux

- Consider one-writer-per-worktree provider implementation only after read-only review is reliable and the owner approves private-code routing.
- dmux may be added as a UI after `tmux`/dmux provenance, version, permissions, egress, and rollback are admitted.
- Automatic merges, distributed lease claims, and multi-writer worktrees remain prohibited.

## Install and availability gaps

- `ecc` and `ecc-memory-mcp` runtime binaries are absent; no project/user Memory Vault exists.
- ECC Codex-local skill files drift from the cached ECC 2.2.0 copies; provenance/version must be reconciled.
- `dmux` and `tmux` are absent.
- Cursor CLI/application/config is absent.
- NotebookLM has no observed local CLI/MCP/application; official browser authentication/privacy is unverified.
- Grok Bot has no observed local adapter; the official platform identity/capability is not yet established by the research task.
- Kimi's configured `PreToolUse` approval hook points to a missing 1.9.8 plugin file while 1.9.9 is installed.
- Claude ECC hooks are not active for this repository.
- Grok has no repo/global custom hook directory, although its runtime supports hooks and scans compatible sources.
- The repository has no active Git hooks or `core.hooksPath`.
- `governance/agents/**`, `docs/research/engineering/**`, and `docs/research/notebooklm/**` from the separate engineering-research plan are not implemented in this worktree.
- Provider authentication states were deliberately not probed because that could contact provider services; readiness remains unverified.
- No admitted disposable VM/OS sandbox plus allowlisted egress proxy for native provider CLIs was observed.

These are gaps, not authorization to install or repair anything.

## Questions requiring owner approval

1. Approve the hybrid governed control plane and reject full distributed automation for Foundation 0.1?
2. Approve a post-Task-6 plan amendment and the candidate tracked paths listed above?
3. Approve `.heleos/orchestration/` as the ignored owner-private runtime/quarantine root, including retention/size limits?
4. Keep external engines restricted to public-synthetic/research packets, or begin a separate security/policy process for any private Heleos code review? The current tracked policy does not permit the latter.
5. Should isolated implementers return patches only, or may one named worker commit to its unique ephemeral branch while Codex remains sole integrator?
6. Approve repository-managed `.githooks/pre-commit` plus local `core.hooksPath` activation after tests, or keep gates wrapper-only?
7. Approve installing/admitting ECC runtime, or retain schema/checkpoint files without ECC?
8. Approve repairing Kimi's stale user-level hook only after its version/provenance review?
9. Approve installing/admitting `tmux` and dmux in a later phase, or defer pane orchestration entirely?
10. Define raw untrusted provider-output retention: recommended default is bounded, ignored, owner-private quarantine with explicit expiry and no automatic deletion until the accepted receipt is durable.
11. Confirm whether Codex's current broad user configuration must be replaced by a dedicated isolated Heleos adapter profile before Phase 3.

## Final preflight verdict

The hybrid architecture is viable, but only the manual/ledger holding pattern is authorized today. The immediate blockers are policy—not code: Task 6's protected eighteen-path commit is unfinished; the tracked research-provider governance files are not implemented; private egress to non-controller engines is denied; provider sandbox/egress mediation is unproved; several runtimes/adapters are absent; and Kimi's sole configured approval hook is stale. The safe next action is to finish and review Task 6 without contamination, then seek owner approval for a plan-only control-plane task.
