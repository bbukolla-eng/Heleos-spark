# Native GitHub review and fix setup

Owner approved on 2026-09-23: automatic reviews, small fixes, and native guarded
merge commits for new ready PRs only. Activation cutoff: 2026-09-23T01:55:48Z.
This is the replacement for withdrawn PR26, not a continuation of its custom app.

## Existing integrations

- CodeRabbit repository 1347892149: automatic and incremental review enabled,
  drafts excluded, default branch only, auto-pause disabled, request-changes
  workflow enabled and Autofix enabled. UI changes apply only to Heleos-spark.
- Cursor automation 490dd69e-b548-11f1-bb68-864e54d14197 is inactive by the
  owner's subsequent instruction. Its run history showed no active jobs when
  disabled. Do not activate it or use it as an automatic-fix fallback.
- GitHub-only automatic fix triggering is not implemented. Enabling CodeRabbit
  Autofix makes the feature available; it does not prove unattended invocation,
  entitlement, a deterministic two-attempt limit or a successful fix/merge cycle.
- Native GitHub auto-merge is enabled; only merge commits are allowed. Existing
  required reviews, checks, resolved threads, stale dismissal, last-push approval
  and CodeQL protection remain. Fix authors cannot supply independent approval.

Never skip tests, weaken protections, force push or change approved mechanical
rules/security/project scope automatically. Ask the owner for those decisions.
Notify on completed merges, exhausted fixes or required decisions. Resulting main
CI/security must be verified separately. Unchanged/non-actionable events stay quiet.

## Verification limits and next action

UI configuration is not an end-to-end successful fix/merge. CodeRabbit shows the
workspace as Free; Autofix entitlement must be verified on an eligible PR. No
subscription or trial was purchased. No custom app or Cursor fallback is authorized.
The GitHub-only fix trigger and deterministic attempt cap remain unfinished.

Current main 63ae39c has a pre-existing failing checks run 34061515864. This
candidate includes the same punctuation-only repair independently proposed by PR25;
it changes no decision and does not merge that old PR or waive approvals. The
new setup PR is sensitive workflow configuration and must receive independent review.
The legacy workflow now requests merge commits and binds the expected PR head; its
PR-number activation guard (numbers greater than 26) does not automatically enroll older PRs.

Local verification: workflow YAML parsed; diff whitespace check passed. Repository
checks initially reproduced the existing docs/decisions/README.md:13 em dash failure.
Replacing it with a colon repairs the prose defect without weakening the check.
A reviewed recovery PR whose current checks prove it repairs the exact main failure
may proceed through native protections; unrelated merges wait for healthy main CI.
After the punctuation repair: repository checks passed (64 tracked files, zero
failures) and all 101 Python tests passed on 2026-09-23 UTC.

## Authority and research

These settings record the owner's explicit side-conversation choices: reviews,
fixes and guarded auto-merge; initial new-PR-only scope, subsequently corrected to GitHub/CodeRabbit only,
direct PR-branch fixes and two failed attempts before escalation. They
supersede the rejected custom-app proposal only; product acceptance is unchanged.
Repository content submitted to the already installed CodeRabbit and Cursor
services remains INTERNAL. Secrets and private project drawings are excluded.

NotebookLM disposition: reused_verified_findings. N07 notebook
f0404db5-8c1e-4591-9c9d-2727bd668cbe, query 3d05c6560b14, GH01-GH04 least-privilege,
trusted execution and downstream-CI findings were reused from retained research.
No new repository upload to NotebookLM. The prior recommendation to create a custom
app is not adopted; existing installed providers must demonstrate their capabilities.

Primary documentation used:
- https://docs.coderabbit.ai/finishing-touches/autofix
- https://docs.coderabbit.ai/pr-reviews/request-changes-workflow
- https://docs.coderabbit.ai/reference/configuration
- https://cursor.com/docs/cloud-agent/automations

Historical settings activity summary (not a schema-validated submission receipt): provider CodeRabbit and Cursor;
purpose configure owner-requested repository review/fix workflow; data_class INTERNAL;
source_hashes bound to this record's Git commit and saved UI prompt evidence;
policy_decision owner authorization above, allow for this repository only;
time 2026-09-23 UTC; result_ref the repository settings and automation URLs below.
- https://app.coderabbit.ai/repository/1347892149/settings
- https://cursor.com/automations/490dd69e-b548-11f1-bb68-864e54d14197

## Backlog handling

The owner subsequently asked to address all 14 open PRs. This authorizes individual
triage and repair; it does not silently enroll the backlog into automatic merging.
See [the PR queue](pull-request-backlog.md) for per-PR finish lines and blockers.
The external-submission receipt and provider-admission reconciliation findings
remain open; this document does not fabricate historical hashes or dismiss reviews.
