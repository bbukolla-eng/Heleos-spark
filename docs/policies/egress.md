# Egress policy

**Owner:** Bekim Bukolla
**Status:** Proposed. It takes effect when the owner signs the decision at `docs/decisions/2026-09-03-egress-policy.md`, and not before. The draft prepared for that signature is `docs/roadmap/decision-12-egress-draft.md`.
**Authorized by:** that decision record (Decision 12 of `ROADMAP.md`, task P0.9).
**Version:** 1. **Effective:** on the date of the decision record.
**Implements:** section 5 of `docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`.

`tools/egress/record.py` hashes this file into every submission record it writes, so the exact bytes of the version in force are provable after the fact. Amending this file is a new version with a new row in section 7, never a silent edit, because records that hash an older version must stay verifiable.

## 1. Purpose and authority

This file is task P0.9 of `ROADMAP.md` and implements spec section 5. It binds every session, workflow, connector, GitHub Action, and AI worker in this repository. Where this file and `CLAUDE.md` differ, the stricter reading applies until the owner reconciles them. Nothing here authorizes a provider that section 4 does not name.

Egress means any content leaving this machine or this repository's GitHub organization to a third party. Traffic between the repository and GitHub itself, including Actions runners, is not egress under this policy, because the content is already held by GitHub as the repository host.

## 2. The four data classes

| Class | What it is here | Default external rule | Who may reclassify |
|---|---|---|---|
| `PUBLIC` | Published standards text, public manufacturer literature, public datasets and model cards, open-source code and its documentation | May go to an admitted provider in section 4, with source and license logged in the admission register | Any worker may treat a source as `PUBLIC` when the admission register records its license and origin |
| `INTERNAL` | This repository's own design documents, roadmap, policies, workflow scripts, CI configuration, run records, test fixtures that contain no project data, and the pull request diffs that carry them | Local by default. A provider may receive it only where section 4 names that provider and section 3 names the workflow, and only with a submission record | Owner only |
| `PROJECT_CONFIDENTIAL` | Bid drawings, specifications, addenda, quotes, evidence crops, extracted quantities, customer names, anything derived from a real project | Local by default. Never leaves without a project-specific and provider-specific decision record naming that project and that provider. None exists, so today the answer is that it never leaves | Owner only, per project |
| `SECRET` | API keys, OAuth tokens, `GITHUB_TOKEN`, signing material, the browser profile and its cookies | Never submitted anywhere, never written to a record, never placed in a prompt, dataset, notebook, log, or the repository | Nobody. A `SECRET` value cannot be downgraded |

Ordinary repository content is `INTERNAL`. That is the class the two GitHub Actions workflows in section 3 submit.

A value shaped like a credential appearing in a submission record is a failure of the record, not something to redact and continue. `tools/egress/record.py` refuses to serialise such a record, and the run fails.

**The reclassification trigger.** The moment any file classed `PROJECT_CONFIDENTIAL` is committed to this repository, the classification in section 3 is wrong, because a checkout then contains project data and the workflows there submit from a checkout. Section 3 states what happens on that day.

## 3. Which workflows may call which providers

| Caller | Provider | Class it may send | Record | Status |
|---|---|---|---|---|
| `.github/workflows/claude-review.yml` | Anthropic | `INTERNAL` and `PUBLIC` | `tools/egress/record.py`, both modes | Permitted once the decision record and the credential of section 4 exist |
| `.github/workflows/claude.yml` | Anthropic | `INTERNAL` and `PUBLIC` | `tools/egress/record.py`, both modes | Permitted on the same terms |
| `.github/workflows/ci.yml` (`checks`) | none | none | not applicable | Sends nothing outside GitHub |
| `.github/workflows/auto-merge.yml` | none | none | not applicable | Sends nothing outside GitHub |
| Dependabot | GitHub | none of this repository's content | not applicable | Reads public action metadata |
| Claude Code sessions (interactive, including this one) | Anthropic | `INTERNAL` and `PUBLIC`; never `PROJECT_CONFIDENTIAL` in a web session | The session's run record under `docs/runs/`, written by hand | The owner's own use of the tool, not an admission of an automated caller. Determination (f) of the decision record settles this row; determination (c) settles the two workflow rows above |
| Research lane (`research-triage`, NotebookLM, Exa, Hugging Face, Kaggle, Context7) | as admitted in `docs/roadmap/skills-and-plugins.md` | `PUBLIC` only | The seven-field record required by Phase 2 | Not yet admitted; Phase 2 |
| Anything not listed | none | none | not applicable | Refused |

**Two kinds of caller, two determinations.** The first two rows are automated callers: a GitHub event starts them, no person is present, and `tools/egress/record.py` produces their record. The interactive row is the owner typing into their own tool. Determination (c) of the decision record admits the automated callers and names those two workflow files only; determination (f) covers the interactive row separately. Reading (c) as covering interactive sessions would be wrong, and reading it as forbidding them would be equally wrong, because this file was itself written in such a session.

Both Claude workflows run `tools/egress/record.py check` before the provider step. If this policy file or the decision record is missing from the checkout, or the declared class is not one the caller may send, the job fails and the provider step never runs. The record is a gate, not a log.

**On the day `PROJECT_CONFIDENTIAL` content enters the repository**, both Claude workflows must be disabled, or restricted by path so they cannot run on a pull request touching such content, before that content is committed. This is not a review item for later. Until the owner records a decision covering that case, the correct action is to remove the two workflows.

## 4. Admitted providers and credentials

| Provider | What it receives | Credential | Terms that govern it |
|---|---|---|---|
| Anthropic | The prompt, the repository content the model reads from the checkout, and pull request or issue text | `ANTHROPIC_API_KEY`, a Console API key | Commercial Terms of Service, under which Anthropic does not train models on customer content |

**The credential is part of the policy, not an implementation detail.** A Claude subscription OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`, produced by `claude setup-token`) runs the same workflows under Anthropic's Consumer Terms instead, where model training is opt-out rather than excluded, and where content flagged by safety classifiers is used for training and retained even after opting out. That is a different bargain from the one this policy makes. The evidence for both readings, with quoted terms, is in section 3 of the decision record.

Therefore: these workflows use `ANTHROPIC_API_KEY`. Setting `CLAUDE_CODE_OAUTH_TOKEN` instead is a change to this policy and needs a new decision record.

No other provider is admitted for repository content. Multi-provider or council-style routing of prompts is off.

## 5. The submission record

Every external submission records the seven fields spec section 5 names, and only those:

| Field | What this repository puts in it |
|---|---|
| `provider` | `anthropic`, declared by the calling workflow |
| `purpose` | One sentence naming what the run is for |
| `data_class` | `INTERNAL`, declared and checked against the classes the caller may send |
| `source_hashes` | The git tree SHA of the checkout, the commit SHA, and the SHA-256 of the event payload. This is what was **available** to the provider, which is an upper bound on what it read, not a record of what it read |
| `policy_decision` | The path and SHA-256 of this file and of the decision record as checked out, the rule applied, and `allow` or `refused` |
| `time` | UTC, second precision |
| `result_ref` | The Actions run URL with attempt, the job, the workflow ref, the provider session id where the action reports one, and the conclusion |

The record never contains prompt text, model output, comment bodies, file contents, or any environment variable outside an eight-name allowlist of `GITHUB_*` values. Sources are named by hash, never by content.

**Where records live.** The job summary of the run, a workflow artifact holding one JSON line, and, after a periodic roll-up, `docs/runs/egress/index.jsonl` in the repository. The workflow does not commit the record itself: on a `pull_request` event the checked-out ref is not a pushable branch, branch protection on `main` forbids a job push, and a push to the head branch would re-trigger the review workflow.

**Where records are read.** `tools/ci/checks.py` validates every line of the committed ledger on every pull request, so a malformed or `refused` line fails a required check. The roll-up itself is a session task producing a run record under `docs/runs/`, on a cadence shorter than the artifact retention window, and it reports any run of the two workflows that produced no record.

## 6. What this policy does not achieve

Stated plainly, because a policy that overstates its own reach is worse than one with named gaps.

1. The ledger records what was **available**, not what was read. The model chooses what to read from the checkout, and that choice is not observable from inside the run. Every line is an upper bound.
2. For `claude.yml` the bound is looser still: tag mode fetches issue and pull request comments during the run, after the event payload was written, so text created in between is covered by no hash in the record.
3. The recorder lives inside the thing it records. A pull request that deletes the record steps still triggers `claude-review`, because `pull_request` runs the workflow file from the pull request head, not from `main`. The `checks` job fails such a pull request, but by then the submission has happened. Two owner-set controls stand in front of that, and neither is automatic. `claude-review` declares the `claude-egress` GitHub Environment, so once the owner adds required reviewers to that environment in Settings the job halts before it can read `ANTHROPIC_API_KEY` and waits for a person. Separately, Settings, Actions, "Require approval for all external contributors" stops a fork pull request from running at all. Without both set, an edited workflow file on a pull request head runs as written. Reading the diff before merge does not cover this, because the run happens before the merge. `claude.yml` is safer, because `issue_comment` runs the workflow file from the default branch, where branch protection applies.
4. Comment text written by anyone who can comment on a pull request reaches the provider through these workflows. That text is not this repository's to classify.
5. The policy binds this repository. It does not bind what the owner pastes into a chat window.
6. The interactive session row of section 3 has no automated record. `tools/egress/record.py` runs in the two workflows only; a session's run record under `docs/runs/` is written by hand and can simply be omitted. The ledger check in `tools/ci/checks.py` validates the lines that exist and cannot know about a submission nobody wrote down.

## 7. Version history

| Version | Effective | SHA-256 of this file | Change |
|---|---|---|---|
| 1 | on the decision record's date | recorded in the first submission record that hashes it | First version, from the decision at `docs/decisions/2026-09-03-egress-policy.md` |
