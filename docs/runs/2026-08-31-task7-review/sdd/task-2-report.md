# Task 2 Report: Typed Rust Foundation

## Revision

- Base: `0d7180d12b4e55cf00d80324c049bc805ff313c9`
- Initial Task 2 commit: `f2683b1272215ba7e3bc762b98ac33b0a2247dbd` (`feat: establish typed Rust foundation`)
- Head: `673a3f13c3e35ed86e7342859a23576c4e34d21e`
- Review round 1 fix commit: `fix: strengthen typed domain contracts`

## Files

- `.gitignore`
- `Cargo.toml`
- `Cargo.lock`
- `rust-toolchain.toml`
- `rustfmt.toml`
- `crates/heleos-core/Cargo.toml`
- `crates/heleos-core/src/lib.rs`
- `crates/heleos-core/src/error.rs`
- `crates/heleos-core/src/domain/mod.rs`
- `crates/heleos-core/src/domain/digest.rs`
- `crates/heleos-core/src/domain/identity.rs`
- `crates/heleos-core/src/domain/state.rs`
- `crates/heleos-core/src/domain/time.rs`
- `crates/heleos-core/tests/domain_contracts.rs`
- `governance/tools.toml`

## RED evidence

Command:

```text
cargo test -p heleos-core --test domain_contracts
```

Result: exit 101, as expected. The test compile failed with `E0432` unresolved imports for the required typed-domain root exports (`canonical_document_ids`, `canonical_json`, `can_transition`, `page_id`, IDs, states, limits, and traits). No domain implementation existed at that point.

## GREEN evidence

Command:

```text
cargo test -p heleos-core --test domain_contracts
```

Result: exit 0. All 11 contract tests passed: digest parsing and streaming hash, stable/index-sensitive page IDs, canonical document/revision IDs, all state transitions, exact ingest outcomes, data classes, injected clock/ID sources, actor validation, JCS golden bytes/hash, and PDF limits.

## Scaffold evidence

```text
cargo generate-lockfile
cargo check -p heleos-core
cargo check -p heleos-core --target x86_64-pc-windows-msvc
```

Result: exit 0. Cargo generated the lockfile and both host and Windows target checks succeeded before domain behavior was added.

## Full verification

```text
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --locked --workspace --all-targets
cargo check -p heleos-core --target x86_64-pc-windows-msvc
git diff --cached --check
```

Result: all commands exited 0. Locked workspace tests passed with 11 integration tests passed and no warnings. The staged path list contained exactly the 15 declared Task 2 paths.

## Review round 1

The independent review requested stronger immutable contract coverage and removal of an avoidable digest-display panic branch. Commit `673a3f13c3e35ed86e7342859a23576c4e34d21e` changes only `crates/heleos-core/src/domain/digest.rs` and `crates/heleos-core/tests/domain_contracts.rs`.

- Added `#![forbid(unsafe_code)]` as the integration-test crate's first line.
- Added digest serde, UUID-backed ID, and ActorId byte-boundary coverage.
- Added RFC 8785 numeric, negative-zero, control/string escaping, UTF-16 property-order, non-finite-float, committed-byte, and SHA-256 vectors. The UTF-16 vector proves ordinary `serde_json` output differs from JCS.
- Added frozen page-ID goldens. The review-supplied `58a4…`/`11f7…` values were independently rejected because they do not equal the plan's formula for the fixed `012345…` digest. The committed correct formula outputs are `a7c0c134d8a5b374df92a59aaaced89310e3567a07474b0582d30baed94b435f` and `c5b845cee03cc3b6093d4434fa4c746ef811baed7d859589dbbe81d2582a7478`.
- Replaced the digest `Display` conversion `expect` with formatter writes that propagate `fmt::Error`.

Review round 1 verification:

```text
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --locked --workspace --all-targets
cargo check --locked -p heleos-core --target x86_64-pc-windows-msvc
git diff --cached --check
```

Result: all commands exited 0. The locked workspace suite contains 16 passing contract tests, no ignored tests, and no warnings. The focused staged path list contained exactly the two changed Task 2 paths.

## Dependency governance

Added one append-only record each for direct `serde`, `serde_json`, `serde_jcs`, `sha2`, `thiserror`, and `uuid` dependencies. Each record records its exact version, crates.io origin, Cargo.lock registry checksum, SPDX license expression, local-only permission, no runtime egress, Foundation 0.1 evaluation, and Task 2 rollback.

## Limitations

This task intentionally establishes only the typed core contracts. SQLite storage, vault, PDF guest/host isolation, intake orchestration, backup, and CLI behavior remain later task responsibilities.
