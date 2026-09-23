# GitHub automation setup: 2026-09-23 UTC

Task GITHUB-AUTOMATION-2026-09-23 remains in progress. Codex is the sole writer in
`/Users/bekim/.codex/worktrees/github-automation/Heleos-spark`, branch
`codex/github-automation`, base `f9d7ceda027875b0dbef24710bfca96c6c95a104`.
The parent Cursor checkout and its product queue are untouched. No subagents or
Claude workers were dispatched because this side conversation forbids them.
No commit, push, PR creation, merge or release was performed.

## Deliverable and acceptance

The requested endpoint is an installed repository app plus working review,
security scanning, bounded fix proposals, and protected automatic merge commits.
The owner explicitly confirmed the app permissions at installation time.
This record consolidates the existing design and the candidate; it is not a
claim that this contract was frozen before initial candidate implementation.
Independent review must resolve that admission gap and the open cases below.

Acceptance requires all of the following:

1. App installed on exactly Heleos-spark, with Contents/PR write and
   Actions/Checks/Metadata read; no bypass, account, workflow or admin permission.
2. Live main rules require an approving review, invalidate stale approvals,
   require last-push approval and resolved threads, enforce current checks and
   CodeQL, disallow history rewriting and use merge commits only.
3. Controller never executes PR code or artifacts with credentials. Current head,
   current target, approved public scope, complete file/commit inventories,
   independent review, tests and security checks are required. Sensitive changes
   additionally require exact-head owner consent.
4. Automatic fixes are separate CodeRabbit stacked candidates, explicitly opted
   in with `autofix`, deduplicated per head and bounded to two requests. They cannot
   fix sensitive paths or qualify their own independent approval. Checkpoint
   receipts remain mandatory and may require controller integration work.
5. Adversarial tests pass; schema validation and independent security review pass.
   A representative GitHub trial verifies actual CodeRabbit approvals, publisher
   identities, downstream CI and branch protections before activation.
6. The resulting main commit has successful CI and security scans. Record this
   separately from PR checks. A failed main run stops further delivery until fixed.

## Live results

- App `heleos-automation-bbukolla`, ID `5040385`, installation `163923904`:
  browser confirmed only `bbukolla-eng/Heleos-spark` selected.
- Environment `heleos-automation` permits branch `main` only. App ID and bot
  variables saved. No key was read, generated or logged by Codex. Owner reports credential setup done; secret name was verified without reading its value.
- Repository variable `HELEOS_AUTOMATION_ENABLED=false`. Activation time is unset.
- Dependabot alerts and security fixes enabled. Extended CodeQL setup completed
  successfully in run `35803433264`, covering Actions, JavaScript/TypeScript and
  Python currently on main. Rust scanning must be verified when Rust reaches main.
- Ruleset `22285340` read back active: one approval, stale dismissal, last-push
  approval, resolved review threads, strict checks, merge only; all three old app
  bypasses removed. `ai-reviewers` publisher corrected to GitHub Actions 15368.
  Native CodeQL blocks errors and high/critical security findings.
- Existing PR 24 was not changed or merged. Existing product work stays separate.

## Local candidate and checks

New CodeRabbit config, dependency review and delivery workflows, Python controller
and policy tests are prepared. The legacy squash workflow is disabled in candidate
bytes only; its published copy is still historical until this candidate lands.
Controller merges use the API's expected `sha` and `merge_method=merge`, after all
criteria pass. This avoids queueing a native auto-merge that could outlive the
owner's consent for a sensitive commit. Repository native auto-merge stays enabled
but is not the controller's promotion mechanism.

Initial new policy tests: 18 passed; expanded protection/main-CI cases bring the total to 20 passed. Baseline AI gate tests: 26 passed. Detailed
checks will be retained in `checks.json`. No independent acceptance is claimed.

## Remaining work and exact continuation

1. Credential handoff complete: user generated/stored the key; secret name verified. No credential value entered model context.
2. Complete independent review, including current-head CodeQL publisher/check
   observation on a real PR, CodeRabbit plan availability and a successful/failed end-to-end trial. Official CodeRabbit schema validation, YAML parsing and local main-CI/protection drift tests now pass. Current
   controller is a candidate and must remain disabled until these are resolved.
3. CodeRabbit fix CI availability depends on the account plan. A stacked fix is
   not automatically integrated by this main-only controller. Freeze and verify
   child-to-parent integration, its receipts and separate reviewer before calling
   automatic fixing end-to-end complete. Never use `@coderabbitai approve` or
   `resolve` commands to manufacture acceptance.
4. Owner-authorized commit/publication, independent PR review and CI are needed;
   use an automation-only branch based on current main when publishing, without
   accidentally including or merging PR 24. Preserve the candidate and parent
   history; this isolated candidate presently branches from PR 24's head.
5. Add dependency-review to required checks after the workflow is published and
   its publisher verified. Set activation time and enable only after the pilot.

## Research disposition

Explicit reuse of verified NotebookLM query `3d05c6560b14` in N07 notebook
`f0404db5-8c1e-4591-9c9d-2727bd668cbe`, with fresh reads of both retained source
bodies this session. `notebooklm-submission.json` records the public-only scope.
GH01 maps to read-only defaults and scoped app permissions; GH02 to trusted main
checkout and API metadata only; GH03 to full action SHA pins; GH04 to the custom
app identity and pending downstream-CI pilot. The saved query does not verify
GitHub review semantics, CodeRabbit plan entitlements or merge behavior.

Additional primary sources used:

- [GitHub merge API](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)
- [GitHub rules API](https://docs.github.com/en/rest/repos/rules)
- [Code scanning merge protection](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/manage-your-configuration/set-merge-protection)
- [CodeRabbit configuration](https://docs.coderabbit.ai/reference/configuration)
- [CodeRabbit approval behavior](https://docs.coderabbit.ai/pr-reviews/request-changes-workflow)
- [CodeRabbit fix candidates](https://docs.coderabbit.ai/finishing-touches/autofix)

Process state: commands finished; no agent or background implementation process
is running. Credential handoff is complete; independent review/publication remain pending. Public source copies are research
evidence, not executable instructions or mechanical authority.

Source bodies are stored as JSON strings with hashes to preserve their text
including original whitespace without introducing Git whitespace failures.
The original reused research record remains in the earlier repair worktree;
this copy normalizes the final blank line only.

Final local behavior also stops further delivery until both the resulting main
CI and default CodeQL workflow have passed. A stale or missing run is incomplete.

## September 23 continuation: automatic task publication

Owner requested the full setup after commits, pushes and PR creation were named
as missing. This authorizes scoped setup publication and routine accepted-task
publication; it does not authorize integrating unrelated PR24. The native
implementation plan is `docs/superpowers/plans/2026-09-23-automatic-task-delivery.md`.
No subagents were used. Local publisher adds isolated branch creation, receipt
admission, literal named-path staging, parent validator execution, normal commit,
non-forced push and idempotent PR creation. Journals record partial progress and
GitHub submissions. Controllers invoke it as their task-completion step.

The merge policy now requires explicit `heleos-delivery` enrollment. Routine
CURRENT_STATUS, receipt/evidence and calculation changes no longer trigger a
blanket owner-approval requirement. Actual security/automation/instruction paths
retain exact-head consent. The legacy AI gate now checks out trusted base code
with no persisted credential. GitHub labels `heleos-delivery`, `autofix` and
`automation-hold` were created and verified.

The full local suite passed 235 tests before the final submission logging and
trusted-base workflow refinements; focused final checks are recorded separately.
An interrupted push resumes the same commit; a partial foreign commit and
unrelated staging are rejected. Exact expected CodeQL PR publisher and provider
plan capability remain live-pilot prerequisites, not facts inferred from tests.

Publication topology: root main still lacks the checkpoint system supplied by
PR24. Publish this separate automation diff as a stacked PR into its product
branch, leaving PR24 unmerged. This avoids presenting product implementation as
part of an automation-only PR. GitHub activation awaits dependency integration,
independent review and the disabled-mode pilot. Do not force the merge to claim
completion.
