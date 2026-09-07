# Heleos-spark working rules

## Start from verified state

1. Identify the exact checkout, branch, HEAD, and dirty paths before editing. Use explicit repository paths; this Codex task may start in `/Users/bekim`.
2. Read `CURRENT_STATUS.md` for the main-checkout snapshot. For the active Foundation completion work, read `.worktrees/foundation-0.1-build/.superpowers/sdd/2026-09-06-foundation-completion/COMPACTION_RECOVERY.md`, `progress.md`, and `resume-state.json`, then run that directory's `resume-verify.sh`.
3. Git identities and verified artifacts take precedence over old chat summaries. A completed task is not restarted unless exact-byte drift or a new concrete defect justifies it. Record that reason.
4. After an agent or command terminates, record its terminal result and next action immediately. Do not leave a completed or failed process marked running, and do not relaunch a quiet process without checking its actual state.

## Preserve boundaries

- Keep durable code, task reports, and recovery checkpoints under this project. Temporary runtime/test scratch and tool caches are not authoritative copies of work.
- Keep one writer per path. Agent output remains a candidate until independently checked and integrated.
- Preserve existing uncommitted work, recovery copies, and worktree registrations. Do not reset, clean, prune, or delete them as routine housekeeping.
- The main checkout contains the accepted Foundation baseline. The newer backup/CLI work retains its existing exact-path and atomic-commit rules in its build worktree; making work visible does not waive acceptance gates.
- Do not import, inspect, or reuse the quarantined predecessor repository or its artifacts. Do not put secrets or private project data into logs, prompts, or Git.
- Local integration does not authorize a GitHub push, force-push, remote-history rewrite, deployment, or account-level change.

## Verify and hand off

- Use the pinned Rust toolchain and locked, offline dependency resolution for ordinary checks.
- Run checks appropriate to the exact changed bytes. Keep new verification evidence separate from historical results; a passing local test is not native Windows or production-release acceptance.
- Record completed work, open findings, exact workspace/commit identities, and the next action before a handoff or compaction. Update `CURRENT_STATUS.md` when the main checkout or its stated blockers materially change.
