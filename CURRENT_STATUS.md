# Current repository status

Updated: 2026-09-07. This is a main-checkout snapshot, not a live process monitor or a production-release acceptance statement.

## Completed work now visible here

The owner requested local integration of completed work. The main checkout was fast-forwarded from `427c7117a14afd3daf3976ba0942584cf77dfae8` to the already accepted Foundation baseline `6469508a0c56b9b306fa47f86b8982d965fb31d5`: 36 existing commits, 60 changed files, and no copied uncommitted candidate files.

This exposes the Rust workspace, typed domain contracts, SQLite migrations/Store, immutable vault, PDF protocol/guest/sandbox, crash-safe intake, integration tests, governance registries, and architecture/operations documents. The completion plan explicitly treats Foundation Tasks 1–6 as completed; this landing does not mean all of Foundation 0.1 is complete.

Fresh post-landing verification passed in this checkout: `cargo +1.96.1 test --locked --offline --workspace --all-targets --all-features -- --test-threads=1` exited 0, with 363 primary-suite tests passed, zero failed and zero ignored. Nested subprocess rechecks are not double-counted. Formatting and strict all-workspace/all-target/all-feature Clippy (`-D warnings`) also passed. These are local macOS results, not native Windows or final release acceptance.

## Work locations

Root agent instructions are available in [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), [KIMI.md](KIMI.md), [GROK.md](GROK.md), [CURSOR.md](CURSOR.md), and [GROKBOTS.md](GROKBOTS.md), with the workflow index in [SKILLS.md](SKILLS.md). These documents define scoped writing, handoff, and continuity; their creation does not install, authenticate, or invoke those providers.

| Location under this project | Purpose |
| --- | --- |
| `.` | Accepted baseline code and this visible status |
| `.worktrees/foundation-0.1-build/` | Newer Store/backup/CLI integration work; exact Task 7 atomic gate still applies |
| `.worktrees/claude-task4/` | Independently accepted CLI candidate; exact six files integrated into the build worktree |
| `.worktrees/actual-build-plan/docs/superpowers/plans/2026-09-06-foundation-completion.md` | Current completion plan |
| `.worktrees/WORKSPACES.md` | Workspace and recovery-copy map |

The detailed checkpoint is `.worktrees/foundation-0.1-build/.superpowers/sdd/2026-09-06-foundation-completion/COMPACTION_RECOVERY.md`. In Finder, use Command-Shift-G to open a `.worktrees` path; its leading dot hides the folder.

## Where staleness occurred

1. The main checkout remained 36 accepted commits behind while development happened in a hidden worktree. The local landing fixes that visibility/history gap.
2. Progress records still called Claude session `6654` and Windows probe `4554` running after both terminated. Those records now distinguish completed implementation, pending review, and failed verification.
3. Older temporary-worktree registrations still have broken Git links. They are not active build authority and have not been deleted or pruned.

## Remaining blockers and next work

- CLI source work is accepted: the final scoped review approved both specification compliance and quality, closing the remaining source findings. The controller independently passed 52 unit and 43 integration tests on that exact candidate. Real headless Claude Code implemented the CLI and earlier corrections; a fresh Codex writer completed the final correction, with independent review afterward.
- Exactly six accepted CLI/guide/dependency files are now integrated in `.worktrees/foundation-0.1-build/`. Independent identity checks verified all six copied files, 59 other tracked files, and six held source blobs, preserving the corrected live core/Store/platform. The build has the exact eighteen authorized changed paths and an empty index; no Task 7 commit or main-branch landing has occurred. New checks on this combined build passed 52 unit and 43 integration tests (zero failed/ignored); the full host and Windows gates remain incomplete.
- Windows diagnostic: the prescribed cross-check got past the native C dependency boundary, then unchanged CLI `build.rs` rejected `AR_x86_64_pc_windows_msvc`, which that protocol requires. Resolve that build-gate/protocol contradiction explicitly; do not repeat the identical failing command or bypass the guard. Native Windows execution remains unverified.
- The newer Task 7 changes are not merged or committed as completed work. Their exact eighteen-path feature gate, subsequent independent verifiers, and release gates remain in force.
- GitHub publication is separate: the locally known `origin/main` has divergent history. No fetch or push was performed for this landing. Review convergence before any owner-authorized publication; never force-push as a shortcut.

Next: finish the combined-build checks, finalize the Task 7 governance record, and execute the remaining host gates. An existing native Windows command connection is still needed to settle the separate platform gate. Consult the execution checkpoint for live handles and exact results; do not restart completed Foundation tasks or CLI fixes.
