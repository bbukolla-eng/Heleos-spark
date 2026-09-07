# Heleos-spark

Heleos-spark is a clean-room, evidence-first HVAC and Division 23 takeoff intelligence system for native Windows and macOS workflows, with a controlled iPhone companion.

This repository is private and starts from a new history. No code, configuration, data, tests, prompts, artifacts, issues, or Git history from the quarantined predecessor repository may be imported, inspected, or reused.

## Current phase

The main checkout now contains the accepted Foundation Tasks 1–6: the Rust workspace, typed domain contracts, SQLite migrations and Store, immutable evidence vault, deterministic PDF processing, crash-safe intake, and tests. Foundation 0.1 is not yet a completed release.

Start with [CURRENT_STATUS.md](CURRENT_STATUS.md) for completed work, the active build location, and remaining blockers. Agent continuity rules are in [AGENTS.md](AGENTS.md).

- [Core implementation](crates/heleos-core/src/lib.rs)
- [Integration tests](crates/heleos-core/tests/)
- [Governance registries](governance/)
- [Foundation implementation contract](docs/superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md)

The newer encrypted-backup and CLI candidate remains in `.worktrees/foundation-0.1-build` until its integration gates pass. Agent candidates and recovery copies stay separate inside `.worktrees`; they are not temporary directories and must not be flattened into this checkout.

## Authority rule

Research systems and AI workers may propose findings and patches. Deterministic code, governed data, cited evidence, automated tests, and explicit human approval determine production truth.
