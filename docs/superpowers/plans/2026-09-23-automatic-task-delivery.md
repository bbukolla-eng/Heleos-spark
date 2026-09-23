# Automatic Task Delivery Implementation Plan

> **For agentic workers:** Execute natively with the executing-plans skill. This side conversation forbids subagents. External GitHub review supplies the independent review; do not claim self-acceptance.

**Goal:** Connect independently accepted task checkpoints to scoped commits, pushes, PRs, independent GitHub reviews and protected merge commits.

**Architecture:** A local publisher consumes an explicit task request in an isolated checkout. It validates exact staged bytes using the parent checkpoint validator, commits only the declared scope and resumes idempotently after failures. A trusted-main GitHub controller evaluates reviewed PRs and never runs candidate code with its app token.

**Tech Stack:** Python standard library, Git, gh CLI, GitHub Actions, CodeRabbit.

**Spec:** [Existing setup contract](../../operations/github-automation-2026-09-23/README.md), supplemented by the owner's current instruction to automate branches, commits, pushes, PR creation, reviews and merges.

## Global Constraints

- Preserve the parent Cursor checkout and product task queue.
- No force push, squash, rebase, bypass, deployments or releases.
- Secrets never enter code, prompts, reports or logs.
- NotebookLM disposition: reuse GH01-GH04 in the retained verified research; no new mechanical claim. Git/gh exact-byte semantics verified with local commands and primary documentation.
- The current instruction authorizes routine task publication through this bounded process. Sensitive policy/security changes retain exact-head owner consent before merge.
- Do not silently merge PR24. Automation is a separate PR stacked on its checkpoint foundation; activation waits for required code on main and passing pilot.

## Review Focus

- Unrelated staged or unstaged files: preserve them and reject ambiguous staging.
- Crash after commit/push: recover the same commit and PR, never duplicate either.
- Changed task evidence: reject drift, incomplete outcome or absent independent review.
- Routine checkpoint updates: do not turn every task into a new owner approval.
- Existing PRs and external work: operate only on explicitly enrolled delivery PRs.

### Task 1: Local task publisher

**Files:** Create `tools/github/publish_task.py`, `tests/test_publish_task.py`.
**Interface:** `publish(repo: Path, request: dict, apply: bool) -> dict`; CLI `--repo PATH --request FILE [--apply]`.
**Request:** schema_version=1, task_id, base_commit, branch (`codex/`), title, body, paths (relative path to SHA256/null). The receipt and CURRENT_STATUS are included. The request is local and explicit; it is never inferred from all dirty files.

- [x] Write tests using temporary isolated Git worktrees. Expected outcomes: dry run changes nothing; completed exact receipt permits commit; incomplete receipt, drift, invalid path, primary checkout and unrelated staging fail; interruption after commit resumes without another commit; push uses no force; PR lookup is idempotent.
- [x] Implement manifest admission and baseline validator execution:
  ```python
  assert receipt['outcome'] == 'complete'
  assert receipt['review']['outcome'] == 'accepted'
  assert receipt['task_id'] == request['task_id']
  # Hash each allowed file; reject .git, traversal and symlinks.
  # Stage literal named paths, validate using parent script, bind index tree.
  ```
- [x] Commit with normal hooks, push only the declared branch to the approved origin, create/reuse one PR using structured arguments. Save local journal in Git's worktree directory after each irreversible step. The publisher never merges or approves.
- [x] Run `python3 -m unittest discover -s tests -p test_publish_task.py -v`.

### Task 2: Delivery routing and protection

**Files:** Modify `tools/github/delivery_controller.py`, `tests/test_delivery_controller.py`, `.github/workflows/delivery.yml`, `.coderabbit.yaml`, `docs/policies/github-automation.md`; add `.github/workflows/task-publication.yml` if a remote branch trigger is needed.
**Interface:** Existing controller `evaluate`, `fix_request`, `protection_ready` remain API-only; publisher PRs use label `heleos-delivery` and marker with task ID.

- [x] Add failing tests for ordinary CURRENT_STATUS/evidence files and missing enrollment.
- [x] Narrow sensitive paths to actual workflow, instruction, access-control and policy surfaces. Preserve rename checks. Enrollment and holds apply to fixes and merges.
- [x] Ensure retry limit, exact-head review, native protections and resulting main CI/security remain mandatory.
- [x] Verify retained NotebookLM GH01-GH04 bindings and official CodeRabbit schema.
- [x] Run controller tests and the existing AI gate tests.

### Task 3: Publish, independently review and activate

**Files:** Update setup ledger, isolated CURRENT_STATUS, checkpoint receipt and provider workflow guidance only where needed to invoke the publisher after task acceptance.

- [ ] Record authority and exact candidate checks; validate staged checkpoint; commit scoped setup as in-progress rather than fake acceptance.
- [ ] Push the new automation branch and open a scoped PR against the current product branch. Its parent PR24 remains unchanged and unmerged. Attach created PR to this Codex task.
- [ ] Verify actual CodeRabbit review/check identities and CI; fix concrete findings within scope. No self-approval or force-approval commands.
- [ ] Activate only after the workflow exists on main, required publisher checks are verified, the app authenticates, a disabled-mode pilot passes, and independent acceptance is recorded. If parent integration or review is unavailable, record that exact dependency and preserve the tested candidate.

## Execution ruling

The owner explicitly requested setup after the process was explained; execute in this session without another plan approval loop. The side-conversation ban on subagents overrides the skill's review-agent dispatch. The existing request authorizes publication of this scoped setup candidate, not integration of unrelated product PRs.
