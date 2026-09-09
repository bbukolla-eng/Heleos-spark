# ADR 0001: Foundation runtime and trust boundary

**Status:** Accepted

**Date:** 2026-08-28

## Context and decision

This ADR records the owner's external approval of the Foundation design on 2026-08-28; writing this ADR does not create that approval. The approved Foundation 0.1 runtime is a Rust core pinned to Rust 1.96.1 and edition 2024. Task 2 must prove shared-core compilation on the host and for the Windows target. Task 5 must prove capability-isolated WASI PDF guest/host IPC before PDF intake is trusted.

Foundation uses SQLite for mutable operational metadata and a SHA-256-addressed filesystem vault for immutable evidence bytes. Canonical identity is content SHA-256: the first accepted byte sequence anchors the logical document and revision identities; identical bytes reuse them, while near-duplicates remain separate until an explicit later linking workflow. Vault objects are immutable by application contract and atomically published.

Migrations are explicit and forward-only. Each migration is transactional; unknown newer schemas fail closed. Recovery is restore-and-forward from a verified encrypted backup, never a down-migration or in-place overwrite. Backups use `age` X25519 encryption, plus a separately trusted Ed25519 signature because encryption alone does not authenticate the producer. Private encryption and signing identities stay outside the repository and backup.

UI, rendering, and packaging technology are deliberately unselected. They remain deferred until the roadmap's native-client proof can evaluate cross-platform PDF rendering, local IPC, packaging, Apple Silicon, and Windows behavior.

## Consequences and exclusions

The default runtime and tests are local-only, with no network socket. The WASI guest has only its private read-only input capability; it receives no socket or ambient filesystem capability.

Foundation 0.1 excludes a broad Division 23 rules engine, pricing, model training, complete desktop or mobile UI, bot roster, cloud deployment, and any workflow, secret, or GitHub App change. These exclusions remain binding even though later roadmap milestones may evaluate them behind recorded acceptance evidence and human gates.
