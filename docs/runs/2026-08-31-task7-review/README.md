# Task 7 review evidence, 2026-08-31

Verbatim copies of the Foundation 0.1 completion ledger that lived only in the git-ignored directory `.superpowers/sdd/2026-08-28-heleos-spark-foundation-0.1/` of the `foundation-0.1` worktree. Copied on 2026-09-06 without edits; `sdd-sha256.txt` lists the SHA-256 of every file as copied.

What matters most:

| File | Why it is here |
|---|---|
| `sdd/progress.md` | The only completion ledger for Tasks 1 to 7: per-slice review verdicts, immutable snapshot commits, tree and blob hashes, and the rulings that rejected candidate patches (the first Store candidate, the Kimi container candidate, the Claude helper candidate). |
| `sdd/task-7-snapshot-review.md` | Ruling that rejected the unqualified snapshot-equivalence claim and required a DEFERRED read transaction. |
| `sdd/task-7-noreplace-review.md` | Ruling that blocked Task 7 before RED on atomic no-replace directory publication and required the `heleos-platform-fs` helper. |
| `sdd/task-7-preflight.md`, `sdd/task-7-brief.md` | The frozen Task 7 contract and its preflight. |
| `sdd/orchestration-preflight.md` | Multi-engine continuity design, design-only, never authorized. |
| `sdd/review-*.diff` | Frozen review diffs between Task 6 snapshots. |

The commit that carries this directory sits on `foundation-0.1-task7-candidate`, whose parent `6469508` is the last commit of `foundation-0.1`. The four review candidate commits named in the ledger are preserved as tags `evidence/task7-claude-platform` (`d31ce47`), `evidence/task7-kimi-container` (`d2b6b21`), `evidence/platform-rereview` (`dd2b6f5`), and `evidence/platform-review` (`36a5139`).

These files are evidence, not policy. They contain en dashes and machine-local paths as written on 2026-08-30 and 2026-08-31; the repository prose rule (`tools/ci/checks.py`) would fail on them, so they must not be merged to `main` without a recorded exception or a wrapper that keeps them out of the prose scope.
