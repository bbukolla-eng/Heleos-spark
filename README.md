# Heleos-spark

Heleos-spark is a clean-room, evidence-first HVAC and Division 23 takeoff intelligence system for native Windows and macOS workflows, with a controlled iPhone companion.

This repository is private and starts from a new history. No code, configuration, data, tests, prompts, artifacts, issues, or Git history from the quarantined predecessor repository may be imported, inspected, or reused.

## Current phase

This checkout contains the Rust Foundation implementation and the agent-controller candidate. Foundation 0.1 is not yet a completed release. [CURRENT_STATUS.md](CURRENT_STATUS.md) records the local integration snapshot, completed work, active build location, and remaining blockers; verify its recorded identities against live Git before continuing work.

The Foundation establishes typed domain contracts, SQLite migrations and Store, the immutable evidence vault, deterministic PDF processing, crash-safe intake, and tests before broader AI, research, or interface work. [ADR 0001](docs/architecture/decisions/0001-foundation-runtime.md) records the owner approval and Rust runtime. The design in [`docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`](docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md) and the owner decisions govern approval. [`ROADMAP.md`](ROADMAP.md) retains the historical Phase 0 plan, exit criteria, and proposed later work; its older phase and runtime claims are not the current implementation snapshot. Local implementation or integration does not waive any unfulfilled owner, native Windows/NTFS, GitHub App, CI, publication, or release-acceptance gate, including outstanding Phase 0 decisions under `docs/decisions/`. [`docs/roadmap/phase-0-evidence.md`](docs/roadmap/phase-0-evidence.md) retains the Phase 0 evidence record.

Shared agent rules and all provider entrypoints are in [AGENTS.md](AGENTS.md); project workflows are indexed in [SKILLS.md](SKILLS.md). For active-build discovery, run `python3 scripts/active-build-status.py --human` from a registered checkout. The visible main checkout's ignored `ACTIVE_BUILD` pointer is a navigation aid to the real active worktree, not a duplicate source tree or an authority override.

- [Core implementation](crates/heleos-core/src/lib.rs)
- [Integration tests](crates/heleos-core/tests/)
- [Governance registries](governance/)
- [Foundation implementation contract](docs/superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md)

Foundation's original exact-path commits and evidence remain in `.worktrees/foundation-0.1-build`; use [CURRENT_STATUS.md](CURRENT_STATUS.md) to determine what has been locally integrated and what gates remain. Agent candidates and recovery copies stay separate inside `.worktrees`; they are not temporary directories and must not be flattened into this checkout.

## Authority rule

Research systems and AI workers may propose findings and patches. Deterministic code, governed data, cited evidence, automated tests, and explicit human approval determine production truth.

[Decision 12](docs/decisions/2026-09-03-egress-policy.md) and the [egress policy](docs/policies/egress.md) govern external submissions, named callers and providers, credentials, and submission records. Shared provider instructions do not expand those permissions. Secrets never enter prompts or records, and private bid data must not be uploaded.
