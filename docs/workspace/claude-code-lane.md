# Claude Code lane: the designated work branch

**Status:** Active repository policy
**Date:** 2026-09-01
**Owner:** Bekim Bukolla

## Purpose

Claude Code (and any other AI worker driven through it) works in exactly one
branch of this repository, the *lane*. Everything else, `main` included, is
read-only for it. This keeps AI-produced changes reviewable as a single stream
and enforces the design rule that workers propose while the owner decides what
becomes production truth (design spec, sections 3 and 10).

The lane is named in one place: [`.claude/work-branch`](../../.claude/work-branch).
Current value: `claude/heleos-spark-branch-60gd5e`.

## How it is enforced

Three layers, each independent of the others:

| Layer | Where | What it does |
|---|---|---|
| Instructions | [`CLAUDE.md`](../../CLAUDE.md) | States the rule and the integration path so the model plans around it |
| Claude Code hooks | [`.claude/settings.json`](../../.claude/settings.json) → [`.claude/hooks/branch_guard.py`](../../.claude/hooks/branch_guard.py), [`.claude/hooks/session_start.sh`](../../.claude/hooks/session_start.sh) | Denies tool calls that would leave the lane or write outside it; reports lane status at session start |
| Git hooks | [`.githooks/`](../../.githooks/) (`pre-commit`, `pre-push`, `post-checkout`) | Refuses commits and pushes off the lane at the git layer. Active only when `CLAUDECODE` or `CLAUDE_CODE_REMOTE` is set, so humans are never blocked |

The session-start hook sets `core.hooksPath=.githooks` in the local clone so
the git layer is armed without any manual step.

### PreToolUse guard (`branch_guard.py`)

Runs before every `Bash`, `Edit`, `Write`, `MultiEdit`, `NotebookEdit`, and
GitHub MCP (`mcp__github__*`) call. Exit code 2 denies the call and the
`[BranchGuard]` message is fed back to the model.

**On the lane** the guard denies only what would break the lane:

- `git checkout` / `git switch` to any other ref, `-b`/`-c`, `--detach`, `-`
- `git branch <new>`, renames, copies, deleting or force-resetting `main` or the lane
- `git worktree add|move|remove`
- `git push` to any branch other than the lane, to any remote other than `origin`,
  force pushes (`--force`, `-f`, `--force-with-lease`, `+refspec`), deletions,
  `--all`, `--mirror`, `--tags`, `--repo`
- `git remote add|set-url|rename|remove`, `git config` writes to `remote.*`,
  `push.*`, `branch.*`, `url.*`, `alias.*`, `core.hooksPath`, `-c` overrides of
  the same keys, config injection via `HOME`/`GIT_CONFIG*`
- `git fetch` / `git pull` from a URL that is not `origin` (clean-room boundary)
- `--no-verify`, `git symbolic-ref` writes, `update-ref`, `filter-branch`
- Anything that writes into `.git/` directly
- `gh pr merge`, `gh pr review --approve`, `gh pr create --head <other>`,
  `gh api` writes to refs or contents
- GitHub MCP: `push_files` / `create_or_update_file` / `delete_file` to another
  branch, `create_branch`, `merge_pull_request`, `update_pull_request_branch`,
  approving reviews, pull requests whose head is not the lane
- Opaque forms it cannot check: `git $SUB`, command substitution next to a
  branch operation, inline `python -c` / `node -e` payloads that call git

**Off the lane** (any other branch or a detached HEAD) the guard allows only
read-only commands plus the way back: `git checkout <lane>` or `git switch
<lane>`, and `git stash` to park a dirty tree first. Every file edit, commit,
push, merge, or shell write inside the repository is denied.

Commands that clearly target a *different* repository (`cd /other/repo &&
git checkout main`, `git -C /other/repo ...`) are out of scope and allowed.
When the working directory cannot be tracked (subshells, `cd -`, command
substitution) the guard assumes this repository and stays strict.

### SessionStart (`session_start.sh`)

- Prints the lane, the current branch, and how far the lane is ahead of or
  behind `origin/main`.
- In Claude Code on the web (`CLAUDE_CODE_REMOTE=true`) with no modified
  tracked files, checks out the lane automatically (creating it from `origin`
  or from the current `HEAD` if it does not exist yet). Set
  `HELEOS_LANE_AUTOSWITCH=0` in the environment to disable this.
- In local sessions it never switches branches; it warns, and the guard keeps
  the session read-only until the lane is checked out.
- Installs dependencies in remote sessions when `pyproject.toml` (via `uv`) or
  `requirements.txt` exists. Nothing is installed yet in the design phase.

## Day-to-day flow

1. Session starts on the lane (automatically on the web, `git checkout <lane>` locally).
2. Work, test, commit with Conventional Commits.
3. `git push -u origin claude/heleos-spark-branch-60gd5e`.
4. Open or update the draft pull request into `main`.
5. The owner reviews and merges. To catch the lane up afterwards:
   `git fetch origin main && git merge origin/main`.

## Changing the lane

Edit the first non-comment line of `.claude/work-branch` while on the current
lane, commit, push, and merge through review. The next session picks up the new
name. Because the guard blocks branch creation, the owner creates the new
branch on GitHub (or locally) before switching the file.

## Multi-repository sessions

Project hooks load from the directory Claude Code was opened in. If a session
is opened in a parent folder that contains several repositories, add the same
two hook entries to `~/.claude/settings.json` with absolute paths to this
repository's hook scripts. The guard already ignores commands aimed at other
repositories, so it is safe to run globally.

## Kill switch and testing

- Owner kill switch: `HELEOS_BRANCH_GUARD=off` in the harness environment
  before Claude Code starts. Setting it from a Bash command inside a session
  has no effect on the hook process.
- Tests (standard library only):

  ```bash
  python3 -m unittest discover -s tests -p 'test_*.py' -v
  ```

  The suite builds throwaway repositories on the lane and on `main` and checks
  the allow/deny tables above, the shell scanner, the launcher, and the
  kill switch.

## Limits

This is a drift control for an AI worker, not a security sandbox. Known gaps,
accepted for now:

- A script or program that internally calls `git checkout` cannot be inspected
  by the PreToolUse guard. The git layer still refuses commits and pushes off
  the lane, and the post-checkout hook warns.
- On the lane, file writes are unrestricted; the lane itself is the boundary.
- The guard relies on the local `.claude/work-branch` and hook files being
  present. A clone that removes them has no guard.

Recommended complement (owner action on GitHub): protect `main` so that direct
pushes are rejected and changes arrive only through reviewed pull requests.
That closes the remaining path for any tool with repository credentials.
