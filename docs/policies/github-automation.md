# GitHub automation

Owner: Bekim Bukolla. The September 22 side conversation explicitly requests
CodeRabbit review, automatic fixes, automatic merging and security review setup.
The owner confirmed the custom app's repository and exact permissions at install.
This setup does not authorize merging the existing product PR 24 or changing the
parent checkout. Its build queue remains with Cursor.

## Verified live configuration

[Setup record](../operations/github-automation-2026-09-23/README.md) preserves the
snapshots, public research and outstanding acceptance work.

- `Heleos Automation bbukolla` (app 5040385, installation 163923904) is installed
  on `bbukolla-eng/Heleos-spark` only. Contents and pull requests: read/write;
  Actions, Checks and Metadata: read. No administrative or workflow write grant.
- `heleos-automation` environment accepts branch `main` only. The owner generated
  and stored `HELEOS_AUTOMATION_PRIVATE_KEY`; Codex verified the secret name only.
  App ID and bot identity are environment variables. The repository activation
  switch `HELEOS_AUTOMATION_ENABLED` remains `false` and the activation time is
  unset. Installation and a saved credential are not workflow activation.
- Ruleset 22285340 requires a current approving review, stale dismissal,
  last-push approval, resolved threads and strict required checks. No bypass
  actors remain. Required check publishers are pinned; the `ai-reviewers`
  publisher is GitHub Actions, not the previously mismatched app.
- Only merge commits are allowed. Force pushes and branch deletion remain
  prohibited. Repository native auto-merge is enabled, but does not supply
  independent acceptance by itself.
- Dependabot alerts and security fixes are enabled. Extended CodeQL setup passed
  for Actions, JavaScript/TypeScript and Python on current main. Native CodeQL
  merge protection blocks errors and high/critical security findings. Rust
  coverage remains to be observed when the Rust code reaches main.

## Candidate workflow contract

The following candidate is under review; activation is a separate recorded gate:

| Component | Behavior |
| --- | --- |
| `.coderabbit.yaml` | Incremental reviews, requests changes for unresolved findings, no automatic review pause. Correctness, security, tests and source evidence are explicit review instructions. |
| `dependency-review.yml` | Read-only PR dependency review blocking newly introduced high/critical vulnerabilities. Its check must be made required after publication and publisher verification. |
| `delivery.yml` | Scheduled/manual trusted-main controller with a repository-scoped app token. No PR code/artifact checkout or execution. Disabled until acceptance. |
| `publish_task.py` | Active checkout controllers invoke it after independent acceptance: validate exact files, create the task branch, commit, push, open/reuse a PR and enroll it for delivery. Does not watch arbitrary dirty work. |
| `delivery_controller.py` | Exact-head independent approval, current target, complete inventories, required checks, security scan and protection drift checks. Refuses forks, preactivation PRs and held work. Stops on failed or pending main CI. |
| `auto-merge.yml` | Retires the old label-triggered squash path. No active merge path remains in this legacy file. |

The controller uses the merge API with the verified head SHA and merge method
`merge`. It never leaves a queued native auto-merge eligible across a new head
whose sensitive-change consent has not been checked. Native branch requirements
still apply. Only one merge can occur per run; resulting main CI must pass before
another delivery. Main security results are a separate acceptance check.

CodeRabbit or Codex bot approval must be an actual `APPROVED` review for the
current head and must come from neither the PR author nor a commit writer. A
comment, acknowledgement or passing test alone is not acceptance. The existing
`ai-reviewers` acknowledgement gate remains supplementary, not an approval.

Sensitive files include automation, hooks, worker boundaries, instructions
and access/security policy. Routine status, checkpoint receipts, section research
and product calculation scripts are not sensitive merely because they change. They require an owner comment exactly
`Heleos owner consent FULL_40_CHARACTER_HEAD_SHA`, in addition to independent
review and CI. This consent changes with the candidate SHA.

An `autofix` label permits at most two deduplicated CodeRabbit requests for
separate stacked candidates on ordinary public-repository PRs. Sensitive paths
and CodeRabbit-authored fixes do not re-enter that loop. A fix is never its own
independent review. Receipt/checkpoint failures are not waived. Child-to-parent
integration and provider plan availability must be verified before calling this
an end-to-end automatic fixing capability. Never issue provider commands that
force approval or mark unresolved findings resolved merely to pass a gate.

## Credentials, egress and acceptance

No secret belongs in Git, prompts, reports or chat. Workflow defaults are read
only and actions use full SHA pins. The publishing identity is not a reviewer.
Private documents are outside this public-repository automation scope.

The existing approved decision is
[the signed egress policy](../decisions/2026-09-03-egress-policy.md).
The earlier statement that it was missing was stale. Existing Claude workflows
still use `claude-egress`; its inspected environment has no protection rules,
so the historical claim that required reviewers already protected it is false.
This setup does not modify its secret. Tightening that separate credential path
requires explicit scope and remains an open security finding.

Public NotebookLM research was reused and supporting source passages were read
again. The setup record binds findings to code and tests. Provider comments log
purpose, public source identity and authorization before requesting fixes.
Independent review and a representative GitHub trial remain mandatory before
activation. No automatic approval, successful pilot or main acceptance is implied
by local tests, source research or an installed app.

## Completion entrypoint and standing authority

The owner requested this full routine delivery setup on 2026-09-23 UTC. Controllers
may publish independently accepted, scoped task checkpoints without another
per-task commit/push/PR question. Worker proposal authority remains unchanged.
Run from an isolated task worktree after writing the completed receipt:

```bash
python3 tools/github/publish_task.py --repo /absolute/task/worktree --from-checkpoint --apply
```

The publisher derives paths and expected hashes from the receipt, requires its
independent review and NotebookLM disposition, and executes the checkpoint
validator from the task's parent commit. It refuses the primary checkout,
unrelated staged files, path traversal, symlinks, mismatched receipts and changed
candidate bytes. It creates a `codex/` branch when the worktree is detached,
keeps normal commit hooks, pushes with no force, and reuses an existing task PR.
A journal in the worktree Git directory records interruptions and submissions.
Untracked files outside the manifest remain local. There is no generic commit of
all dirty files and no background filesystem watcher.

Only PRs with `heleos-delivery` are eligible for the merge controller. The publisher
adds this enrollment and `autofix`. `automation-hold` blocks both fixes and merges.
Local task acceptance permits publication, not GitHub self-approval. An existing
closed task PR is not silently recreated.
