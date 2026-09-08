# Current repository status

Updated: 2026-09-08 20:10 UTC. This is the live local integration snapshot; it is not a production-release acceptance statement.

## Completed work now visible here

The owner requested that completed implementation be placed in the visible repository. Local `main` now contains the accepted baseline, the four documentation/continuity commits, and the complete Task 7-9 implementation chain. The local merge commit is `dee9179ff99f78864248f62bceb80bd2c4e595cf`; its parents are documentation head `f216ed53380da63dd33e5cf1b51753a89f711e25` and Task 9 head `dab026f990652b71120c3cbeded2bbc4092a27a8`.

The visible checkout therefore includes the Rust workspace, typed domain contracts, SQLite migrations/Store, immutable vault, PDF protocol/guest/sandbox, crash-safe intake, encrypted backup/verification/restore CLI, platform publication helper, independent storage and hostile-input verifiers, portable Foundation entry points, governance registries, and architecture/operations documents. The merge added 31 implementation paths and about 36,600 changed lines. This landing does not mean Foundation 0.1 release acceptance is complete.

Fresh post-merge checks passed here: locked/offline metadata, formatting, normal provenance, frozen verifier build, strict all-workspace/all-target/all-feature Clippy, the CLI build, and the frozen Task 9 black-box suites (`1/1` Foundation plus `7/7` hostile intake, zero failed or ignored). The first CLI attempt intentionally stopped while pinned tool installation was concurrently changing Cargo's seed cache; once installers were terminal, the exact isolated rerun passed. Both the causal stop and the passing rerun are preserved. These are local macOS results, not native Windows or final release acceptance.

## Work locations

Root agent instructions are available in [AGENTS.md](AGENTS.md), [CLAUDE.md](CLAUDE.md), [KIMI.md](KIMI.md), [GROK.md](GROK.md), [CURSOR.md](CURSOR.md), and [GROKBOTS.md](GROKBOTS.md), with the workflow index in [SKILLS.md](SKILLS.md). These documents define scoped writing, handoff, and continuity; their creation does not install, authenticate, or invoke those providers.

| Location under this project | Purpose |
| --- | --- |
| `.` | Visible local `main` with the integrated Task 7-9 release candidate and this status |
| `.worktrees/foundation-0.1-build/` | Closed, clean Task 7-9 implementation chain and immutable execution evidence |
| `.worktrees/foundation-0.1-release/` | Active `build/foundation-0.1-release-gate` Task 10 supply-chain/SBOM work, based on `dee9179...` |
| `.worktrees/claude-task4/` | Independently accepted CLI candidate; exact six files integrated into the build worktree |
| `.worktrees/actual-build-plan/docs/superpowers/plans/2026-09-06-foundation-completion.md` | Current completion plan |
| `.worktrees/WORKSPACES.md` | Workspace and recovery-copy map |

The detailed checkpoint is `.worktrees/foundation-0.1-build/.superpowers/sdd/2026-09-06-foundation-completion/COMPACTION_RECOVERY.md`. In Finder, use Command-Shift-G to open a `.worktrees` path; its leading dot hides the folder.

## Where staleness occurred and how it is prevented

1. The main checkout previously lagged while development happened in a hidden worktree. Merge `dee9179...` fixes that visibility/history gap.
2. Progress records still called Claude session `6654` and Windows probe `4554` running after both terminated. Those records now distinguish completed implementation, pending review, and failed verification.
3. Older temporary-worktree registrations still have broken Git links. They are not active build authority and have not been deleted or pruned.
4. The compaction checkpoint now records the exact completed chain, convergence evidence, visible-main merge, post-merge gates, and Task 10 worktree. Completed Tasks 7-9 must not be recreated after compaction.

## Current build and next work

- Tasks 7-9 are complete, committed, converged, and locally integrated. The exact post-baseline path counts are `18/2/1/8/8`; amended convergence passed with no merge inside that chain. No reviewer agents remain active or required by the owner's current direction.
- Task 10 is actively implementing the local supply-chain gate. Exact `cargo-deny 0.20.2`, `cargo-audit 0.22.2`, `cargo-cyclonedx 0.5.9`, and Gitleaks `8.30.1` are installed. A clean RustSec snapshot is frozen at commit `bf25f6575a93a35f30796c65c0ed91bee7fa19fd`; `cargo audit` inspected 405 lock dependencies and reported zero vulnerabilities and zero warnings.
- The pinned SBOM generator's raw seven member files cover only 390 of 405 packages and leak physical workspace paths. Astra has produced a deterministic normalized candidate covering all 405 packages and 1,033 dependency edges; its negative checks and durable verifier integration are still in progress. Partial raw SBOMs will not be mislabeled complete.
- `governance/github-apps.toml` still marks Azure Pipelines, AWS Connector for GitHub, Amazon Q Developer, and ECC Tools as `owner_decision_required`. Task 10 may build local policies/scripts before that gate, but it may not create a workflow until the owner provides dispositions.
- The locally known `origin/main` remains divergent. No fetch, push, force-push, workflow publication, deployment, or account-level mutation occurred.

Next: finish and execute Task 10's local policy, supply-chain scripts, governed full-graph SBOM, and deterministic verifier. Then obtain the four GitHub App dispositions before creating CI. Native Windows/NTFS attestation and any push remain separate owner gates. Do not restart completed Tasks 7-9, dispatch review agents, reset/clean, or bypass a failing guard.
