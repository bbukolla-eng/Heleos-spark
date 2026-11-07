# Codex and Cursor collaboration — 2026-09-22

This records the current collaboration arrangement and verification limits. It is not another build queue. [AGENTS.md](../../AGENTS.md) controls authority; [CURRENT_STATUS.md](../../CURRENT_STATUS.md) remains the single live queue. A later explicit owner decision changes these roles.

## Responsibilities under the current instructions

| Participant | Responsibility |
| --- | --- |
| Cursor | Checkout controller: select the ready task, assign non-overlapping paths, implement or coordinate implementation, review candidates, integrate accepted work and maintain CURRENT_STATUS.md/checkpoints. Local commits require an explicit owner request. |
| Codex | Use the connected NotebookLM tools, verify supporting passages, retain source-backed research packets, perform bounded independent reviews/checks, and implement only specifically assigned paths that do not conflict with Cursor's work. |
| Claude Code | Bounded implementation or review through the existing guarded workflow when authentication, containment and the exact-base research packet are ready. No self-approval or integration. |
| Owner | Decide genuinely new estimating rules and final product acceptance. Commit and remote actions follow the current explicit authority in AGENTS.md. |

The author of a change cannot supply its independent acceptance review. If Cursor authors a candidate, Codex or another uninvolved reviewer reviews it; if Codex or Claude authors it, Cursor can review it. The controller records the acceptance result.

## One delivery cycle

1. Verify live checkout, branch, commit and dirty paths. Resume the named unfinished task; reuse unchanged accepted evidence.
2. For a knowledge-dependent change, Codex reuses verified findings or queries the relevant NotebookLM sources, checks consequential claims against source passages, and records source identities and external submissions. Approved rules and deterministic calculations stay authoritative.
3. Freeze the bounded deliverable, source inputs, independently expected cases, numerical tolerances, check commands and independent reviewer under the selected section's existing task record.
4. Assign one writer per named path. Every worker receives the exact base, instruction/research identities, permitted tools/data, finish line and stop conditions. Parallel work is useful only when its dependencies and files are independent.
5. Implement the connected behavior and focused meaningful tests. A worker returns a candidate with complete changed paths, actual check results, unresolved findings and terminal process state.
6. Independently inspect candidate bytes and run the declared checks. Verify the required connected Mac workflow, including correction, reopening, revision and source-linked outputs when those belong to the task. A model response or worker-reported green check is not acceptance.
7. Close the task when every frozen mandatory criterion passes and independent review resolves its in-scope defects. Keep recognition qualification, whole-section acceptance and eventual Windows verification distinct from completed local software.
8. The controller records evidence and the next executable action in the task record and CURRENT_STATUS.md. Run the checkpoint guard for an explicitly requested commit. New requirements receive a new task/version; completed work is not restarted for reassurance.

The full CSI Division 23 hierarchy remains the scope. One blocked source, model or estimating decision holds only its dependent output. Shared duct or air-device work cannot replace the other section tasks. Use the existing section cards/contracts rather than introducing a second list here.

## Observed capabilities and limits

Verified locally on 2026-09-22:

- Checkout `/Users/bekim/Heleos-spark`; branch `2026-09-21-t5b5`; HEAD `ce714a0907e89f72a871f7d2dcf9472368523ab2`. No tracked changes were present before this note. Two existing untracked Cursor learning-state files were preserved.
- `cursor-agent --version` returned `2026.09.02-c22c1a3`; `cursor-agent status` exited 0 and reported authenticated. Account details and credentials were not recorded. This confirms CLI availability/authentication, not a newly launched implementation task.
- The connected tool inventory exposes NotebookLM notebook/query/source tools to Codex. No new research query was needed for this process clarification.
- Correction after direct code inspection: the [guarded worker runner](guarded-worker-runner.md) admits configured `codex`, `claude_code`, `grok` and `cursor` commands. The earlier claim that no Cursor adapter existed came from stale guide wording. Existing Cursor support is not proof that a new live dispatch succeeded. Kimi was removed from the active build and its plugin uninstalled by owner direction on 2026-09-22.
- No Codex subagents were live when checked. Old chat assignments were not resumed against the new branch.
- `scripts/active-build-status.py --human` exited 1 with `main_checkout_unavailable`. The live branch/HEAD were verified directly. The controller should reconcile the missing-main routing requirement before relying on this helper; its failure is not a reason to replay accepted product work.
- CURRENT_STATUS.md's header still names `49b7f57a208e162054b0d856dd299852a1bfce50`, while live HEAD is `ce714a0907e89f72a871f7d2dcf9472368523ab2`. This is a recorded stale header, not proof that later commits are absent. Cursor owns the status correction.

The current recorded product task is `CSI-23-31-13-DEFINE`. Its next input is verified source bodies for children `23 31 13.13`, `23 31 13.16` and `23 31 13.19`; the existing definition packet has explicit gaps. A useful division of work is Codex source research and independent checking, with Cursor maintaining the section packet and connected implementation queue. This is an assignment proposal, not a claim that either new worker has been launched.

No application code, controller assignment, hook, GitHub setting, commit or remote state was changed by this process review.
