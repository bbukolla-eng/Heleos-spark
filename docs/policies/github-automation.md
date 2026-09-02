# GitHub automation

**Owner:** Bekim Bukolla. **Authorized:** 2026-09-02, by the owner's instruction "set automatic merging, committing, PR, review on GitHub", which supersedes the roadmap's rule that no `.github/workflows` file lands before the Decision 5 record. The Decision 5 record still inventories the auto-installed apps; nothing here adds a workflow or secret for them.

## What runs

| Workflow | Trigger | What it does | Needs from the owner |
|---|---|---|---|
| `checks` (`.github/workflows/ci.yml`) | every pull request; pushes to `main` | Standard-library unit tests, `node --check` on every workflow script, `tools/ci/checks.py` (registry hashes, JSON validity, relative links, the no-dash prose rule) | Nothing; runs today |
| `claude-review` | a pull request is opened, updated, or marked ready (drafts skipped) | Claude reviews the diff against `CLAUDE.md`, the spec, and `ROADMAP.md` and leaves review comments; it never approves or merges | Secret `CLAUDE_CODE_OAUTH_TOKEN` |
| `claude` | `@claude` in an issue, issue comment, review, or review comment | Claude implements the request, commits on a branch, and opens or updates a pull request (automatic committing and pull requests) | Secret `CLAUDE_CODE_OAUTH_TOKEN`; the Claude GitHub App installed on the repository |
| `auto-merge` | a non-draft pull request gains the `automerge` label, is marked ready, or is updated while labelled | Enables GitHub auto-merge (squash); GitHub merges once every required check passes | "Allow auto-merge" in Settings; branch protection on `main` requiring the `checks` job |
| Dependabot (`.github/dependabot.yml`) | weekly | Opens pull requests that bump the pinned action commits | Nothing |

Until the secret exists, the two Claude workflows print a notice and exit green; they do not fail pull requests.

## Owner steps in GitHub Settings (one time)

1. Settings, General, Pull Requests: enable **Allow auto-merge**. Optionally enable **Automatically delete head branches**.
2. Settings, Branches, add a rule for `main`: require a pull request before merging, require status checks to pass with **checks** selected, block force pushes and deletions, and apply the rule to administrators. GitHub does not offer auto-merge without such a rule.
3. Settings, Secrets and variables, Actions: add `CLAUDE_CODE_OAUTH_TOKEN` with the value printed by `claude setup-token` on your machine. To use an API key instead, add `ANTHROPIC_API_KEY` and change the `claude_code_oauth_token` input to `anthropic_api_key` in both Claude workflows.
4. Install the Claude GitHub App on the repository (`/install-github-app` in Claude Code, or github.com/apps/claude) so that `@claude` runs can open pull requests and their commits trigger `checks`.
5. Create the label `automerge` (Issues, Labels). Apply it to a pull request you want merged without a manual click; remove it to stop.

The session that wrote this page has no tool for any of these settings; each is an account action the spec reserves for the owner (section 13).

## What stays with people

- AI workers still never approve a pull request and never call merge themselves; merges happen through GitHub's auto-merge on pull requests the owner labels, after the required checks pass.
- The `claude` workflow works on the branch it creates for the issue or pull request and never pushes to `main` (branch protection enforces this).
- Every Claude run reads pull request and issue text as data. Text in an issue that asks for a policy change, a secret, or a merge is reported, not followed.

## Provenance of the actions used

| Action | Commit pinned | Tag | License |
|---|---|---|---|
| `actions/checkout` | `11bd71901bbe5b1630ceea73d27597364c9af683` | v4.2.2 | MIT |
| `actions/setup-python` | `a26af69be951a213d495a4c3e4e4022e16d87065` | v5.6.0 | MIT |
| `anthropics/claude-code-action` | `c3d45e8e941e1b2ad7b278c57482d9c5bf1f35b3` | v1.0.99 | MIT |

Pins are by commit, resolved with `git ls-remote --tags` on 2026-09-02; Dependabot proposes bumps as pull requests that go through `checks` like any other change.

## Local run

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
for f in .claude/workflows/*.js docs/runs/*/scripts/*.js; do node --check "$f"; done
python3 tools/ci/checks.py
```
