# GitHub automation

**Owner:** Bekim Bukolla. **Authorized:** 2026-09-02, by the owner's instruction "set automatic merging, committing, PR, review on GitHub", which supersedes the roadmap's earlier rule that no `.github/workflows` file lands before the Decision 5 record. The Decision 5 record still inventories the auto-installed apps; nothing here adds a workflow or secret for them.

## What runs

| Workflow | Trigger | What it does | Needs from the owner |
|---|---|---|---|
| `checks` (`.github/workflows/ci.yml`) | every pull request; pushes to `main` | Standard-library unit tests, `node --check` on every workflow script, `tools/ci/checks.py` (registry hashes, JSON validity, relative links and image destinations, the no-dash prose rule) | Nothing; runs today |
| `claude-review` | a pull request is opened, reopened, updated, or marked ready (drafts skipped) | Claude reviews the diff against `CLAUDE.md`, the spec, and `ROADMAP.md` and leaves review comments; it never approves or merges | The `claude-egress` environment and its `ANTHROPIC_API_KEY` secret |
| `claude` | `@claude` in an issue, issue comment, review, or review comment | Claude implements the request and commits: on an open pull request it pushes to that pull request's branch, so the pull request updates itself; from an issue it creates a branch and replies with a pre-filled pull request link a person must click. The action does not open pull requests itself (its `docs/security.md`, "Pull Request Creation") | The `claude-egress` environment and its `ANTHROPIC_API_KEY` secret; the Claude GitHub App installed on the repository |
| `auto-merge` | a non-draft pull request gains the `automerge` label, is reopened, is marked ready, or is updated while labelled | Enables GitHub auto-merge (squash); GitHub merges once every required check passes | "Allow auto-merge" in Settings; branch protection on `main` requiring the `checks` job |
| `ai-review-gate` (`.github/workflows/ai-review-gate.yml`), job `ai-reviewers` | non-draft pull request opened, reopened, synchronized, or marked ready | Polls until ChatGPT Codex and Copilot have acknowledged the current head (or Copilot's `copilot-pull-request-reviewer` check is success); posts `@codex review` once per head if Codex has not spoken. ECC Tools is printed as advisory and does not fail the job | After this lands on `main`, require the `ai-reviewers` check on the Sparky ruleset (owner or Github_pusher). Amazon Q stays a separate Sparky required check. No new secret. |
| `pr-automation` (`.github/workflows/pr-automation.yml`) | pushes to non-`main` branches; pull request open, reopen, sync, ready, and label changes | On branch pushes, opens a draft pull request to `main` when one does not already exist and refreshes the body on existing draft pull requests. On pull request events, applies `automerge` only when the opt-in label `automerge-request` is present and checks are green for `checks`, Codex, Copilot, ECC, and Amazon Q signals | Require the check contexts listed in step 8 on `main`; define and apply the `automerge-request` label on pull requests meant for hands-free merge |
| Dependabot (`.github/dependabot.yml`) | weekly | Opens pull requests that bump the pinned action commits | Nothing |

After `ai-review-gate` lands on `main`, Sparky must require the `ai-reviewers` check. The owner or Github_pusher adds that context to the ruleset. This gate reads existing Codex, ECC Tools, and Copilot reviews (and Copilot's check-run) through `GITHUB_TOKEN`; it does not add a secret or a workflow for those apps. The gate checks out the pull request base SHA, so with write permissions it runs trusted repository logic, not pull-request-controlled code. Codex and Copilot are required to pass. ECC Tools acknowledgement is advisory.

The `pr-automation` workflow also checks out the pull request base SHA before it runs repository logic in a write-capable job. Its automerge policy step requires green signals for `checks`, Codex, Copilot, ECC, and Amazon Q before it applies the `automerge` label.

Until the secret exists, the two Claude workflows print a notice and exit green; they do not fail pull requests. Once it exists they are additionally gated twice: by the `claude-egress` environment, whose required reviewers hold the job before it reads the secret, and by `tools/egress/record.py check`, which fails the job if the egress policy or a signed decision record is missing from the checkout.

## Owner steps in GitHub Settings (one time)

1. Settings, General, Pull Requests: enable **Allow auto-merge**. Optionally enable **Automatically delete head branches**.
2. Settings, Branches, add a rule for `main`: require a pull request before merging, require status checks to pass with **checks** selected, block force pushes and deletions, and apply the rule to administrators. GitHub does not offer auto-merge without such a rule.
3. Settings, Environments, New environment, named exactly `claude-egress`. Both Claude workflows declare it, so a job cannot read the provider secret without it. Under **Deployment protection rules** tick **Required reviewers** and add yourself. Leave the wait timer at 0. This is what stops a pull request that edits `.github/workflows/claude-review.yml` from running the edited file against the secret before anyone has read the diff, which `docs/policies/egress.md` section 6 names as gap 3.
4. Settings, Actions, General, Fork pull request workflows from outside collaborators: select **Require approval for all external contributors**. Step 3 gates the secret; this gates the run.
5. The secret. Put it on the environment, not on the repository, so the protection rule in step 3 applies to it:
   - Settings, Environments, `claude-egress`, **Environment secrets**, Add secret.
   - Name: `ANTHROPIC_API_KEY`, spelled exactly that way. The workflows read `secrets.ANTHROPIC_API_KEY` and test it for emptiness, so a misspelled name is not an error, it is a silent skip: the jobs print a notice and pass.
   - Value: a Console API key from console.anthropic.com, Settings, API keys, Create key. It begins `sk-ant-api03-`. Paste it with no quotes, no `Bearer` prefix, and no trailing newline. GitHub shows it once and never again; keep your own copy in your password manager, not in this repository, not in an issue, and not in a chat window.
   - Do **not** add `CLAUDE_CODE_OAUTH_TOKEN`. The subscription token runs the same workflows under Anthropic's Consumer Terms, where model training is opt out rather than excluded and content flagged by safety classifiers is used and retained regardless. The Console key runs them under the Commercial Terms, which prohibit training on customer content. Section 3 of the decision record sets out the difference with quoted terms.
   - Do this **only after** signing the decision record, for the reason in "Egress, and the credential" below. The order is not advisory: `tools/egress/record.py check` fails the job while `docs/decisions/2026-09-03-egress-policy.md` is absent or unsigned, so adding the secret first buys nothing except red pull requests.
   - To rotate or revoke: the same Environment secrets panel, Update or Remove. Removing it returns both workflows to the notice-and-pass state.
   - No other secret is needed. `GITHUB_TOKEN` is minted per run by GitHub; never create one by hand.
6. Install the Claude GitHub App on the repository (`/install-github-app` in Claude Code, or github.com/apps/claude) so that `@claude` runs can push the branch they work on and their commits trigger `checks`.
7. Create the label `automerge` (Issues, Labels). Apply it to a pull request you want merged without a manual click; remove it to stop.
8. For hands-free merge policy with `pr-automation`, create the label `automerge-request` and require these checks on `main`: `checks`, `ai-reviewers`, the Copilot check (`copilot-pull-request-reviewer`), the ECC check context used in your repository, and the Amazon Q check context used in your repository. The policy step matches ECC and Amazon Q by check name text, so keep those check names stable.

The session that wrote this page has no tool for any of these settings; each is an account action the spec reserves for the owner (section 13).

## What stays with people

- AI workers still never approve a pull request and never call merge themselves; merges happen through GitHub's auto-merge on pull requests the owner labels, after the required checks pass.
- The `claude` workflow works on the branch it creates for the issue or pull request and never pushes to `main` (branch protection enforces this).
- Every Claude run reads pull request and issue text as data. Text in an issue that asks for a policy change, a secret, or a merge is reported, not followed.

## Egress, and the credential

The two Claude workflows send `INTERNAL` repository content to Anthropic once they can run: the pull request diff, and whatever the model reads from the checkout, which for the review workflow reliably includes `CLAUDE.md`, `ROADMAP.md`, and the spec. Spec section 5 governs that traffic and requires a seven-field record for every external submission.

`docs/policies/egress.md` is the policy that permits it, and `docs/decisions/2026-09-03-egress-policy.md` is the decision that will put the policy in force. That decision record does not exist yet. What exists is the prepared draft at `docs/roadmap/decision-12-egress-draft.md`, because only the owner writes under `docs/decisions/`. To record it: copy that draft to `docs/decisions/2026-09-03-egress-policy.md`, answer its six determinations, replace the draft banner with a line reading exactly `**Status:** APPROVED`, sign the `**Decided by:**` line, and commit. The gate reads only ordinary prose: a `**Status:** APPROVED` line inside a fenced block, an HTML comment, or an indented code block is an illustration and is ignored, so an example of how to sign cannot sign the record. Until then neither the policy nor the decision is in effect and the secret must not be added. `tools/egress/record.py` enforces this from inside the job: its `check` mode runs before the provider step and fails the job when the policy or the decision record is missing from the checkout, so the record is a gate rather than a log, and its `write` mode emits the seven fields to the job summary and a workflow artifact. The gate reads the record as well as looking for it: a file without `**Status:** APPROVED` and a named signer fails the check, so a prepared draft copied into place unsigned cannot open it.

The credential is part of the policy. These workflows use `ANTHROPIC_API_KEY` under Anthropic's Commercial Terms, which exclude training on customer content and treat it as confidential. A subscription OAuth token would run the same workflows under the Consumer Terms instead. The `checks` and `auto-merge` workflows send nothing outside GitHub and are unaffected.

## Provenance of the actions used

| Action | Commit pinned | Tag | License |
|---|---|---|---|
| `actions/checkout` | `11bd71901bbe5b1630ceea73d27597364c9af683` | v4.2.2 | MIT |
| `actions/setup-python` | `a26af69be951a213d495a4c3e4e4022e16d87065` | v5.6.0 | MIT |
| `anthropics/claude-code-action` | `833fb0f8c9f6686b33d963a8bae0a94f4936ab2a` | v1.0.211 | MIT |
| `actions/upload-artifact` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | v7.0.1 | MIT |

Pins are by commit, resolved with `git ls-remote --tags` on 2026-09-02; Dependabot proposes bumps as pull requests that go through `checks` like any other change. A bump of `claude-code-action` is not a routine bump: what the action prefetches before the first model call is the evidence base of the egress record, and v1.0.211 changed it. Re-read the prefetch query and re-verify section 2 of that record before merging one.

## Local run

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
for f in .claude/workflows/*.js docs/runs/*/scripts/*.js; do node --check "$f"; done
python3 tools/ci/checks.py
```
