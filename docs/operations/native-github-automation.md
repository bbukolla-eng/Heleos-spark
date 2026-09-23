# Native GitHub review and fix setup

Owner approved on 2026-09-23: automatic reviews, small fixes, and native guarded
merge commits for new ready PRs only. Activation cutoff: 2026-09-23T01:55:48Z.
This is the replacement for withdrawn PR26, not a continuation of its custom app.

## Existing integrations

- CodeRabbit repository 1347892149: automatic and incremental review enabled,
  drafts excluded, default branch only, auto-pause disabled, request-changes
  workflow enabled and Autofix enabled. UI changes apply only to Heleos-spark.
- Existing Cursor automation 490dd69e-b548-11f1-bb68-864e54d14197: GitHub PR-opened,
  review-submitted and workflow-completed triggers, restricted by instructions to
  new same-repository ready PRs targeting main. Older PRs are excluded.
- Cursor requests CodeRabbit fixes for its findings and handles CI/other findings.
  Fixes go to the existing PR branch. Two total fix attempts per PR, shared across
  providers; persistent attempt/lease records and notifications are agent instructions,
  not a new deterministic GitHub enforcement layer.
- Cursor cannot approve via its configured PR-comment tool. Slack read/send tools
  and the daily/main-only repair triggers were removed. No new credential or app.
- Native GitHub auto-merge is enabled; only merge commits are allowed. Existing
  required reviews, checks, resolved threads, stale dismissal, last-push approval
  and CodeQL protection remain. Fix authors cannot supply independent approval.

Never skip tests, weaken protections, force push or change approved mechanical
rules/security/project scope automatically. Ask the owner for those decisions.
Notify on completed merges, exhausted fixes or required decisions. Resulting main
CI/security must be verified separately. Unchanged/non-actionable events stay quiet.

## Verification limits and next action

UI configuration is not an end-to-end successful fix/merge. CodeRabbit shows the
workspace as Free; Autofix entitlement and accepting a Cursor-authored trigger must
be verified on an eligible PR. No subscription or trial was purchased. If unavailable,
report that prerequisite rather than treating a settings toggle as paid entitlement.
Native auto-merge capability under Cursor's existing identity likewise needs a live
trial; do not create another app if it lacks permission.

Current main 63ae39c has a pre-existing failing checks run 34061515864. PR25 proposes
its repair. This task does not merge that old PR or waive required approvals. The
new setup PR is sensitive workflow configuration and must receive independent review.
The legacy workflow now requests merge commits and binds the expected PR head; its
PR-number activation guard (numbers greater than 26) does not automatically enroll older PRs.

Local verification: workflow YAML parsed; diff whitespace check passed. Repository
checks fail on the unchanged docs/decisions/README.md:13 em dash already reported
by main CI. That baseline failure was not hidden or changed in this setup.

## Authority and research

These settings record the owner's explicit side-conversation choices: reviews,
fixes and guarded auto-merge; recommended new-PR-only scope, CodeRabbit/Cursor
split, direct PR-branch fixes and two failed attempts before escalation. They
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

External submission record for settings: provider CodeRabbit and Cursor;
purpose configure owner-requested repository review/fix workflow; data_class INTERNAL;
source_hashes bound to this record's Git commit and saved UI prompt evidence;
policy_decision owner authorization above, allow for this repository only;
time 2026-09-23 UTC; result_ref the repository settings and automation URLs below.
- https://app.coderabbit.ai/repository/1347892149/settings
- https://cursor.com/automations/490dd69e-b548-11f1-bb68-864e54d14197
