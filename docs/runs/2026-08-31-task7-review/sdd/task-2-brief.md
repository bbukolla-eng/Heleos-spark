### Task 2: Create the Rust Workspace and Typed Domain Contracts

**Files:**
- Create: `rust-toolchain.toml`
- Create: `Cargo.toml`
- Create: `Cargo.lock`
- Create: `rustfmt.toml`
- Modify: `.gitignore`
- Create: `crates/heleos-core/Cargo.toml`
- Create: `crates/heleos-core/src/lib.rs`
- Create: `crates/heleos-core/src/error.rs`
- Create: `crates/heleos-core/src/domain/mod.rs`
- Create: `crates/heleos-core/src/domain/digest.rs`
- Create: `crates/heleos-core/src/domain/identity.rs`
- Create: `crates/heleos-core/src/domain/state.rs`
- Create: `crates/heleos-core/src/domain/time.rs`
- Create: `crates/heleos-core/tests/domain_contracts.rs`
- Modify: `governance/tools.toml`

**Interfaces:**
- Consumes: Task 1 governance registries.
- Produces: `Result<T>`, `HeleosError`, `Sha256Digest`, `ProjectId`, `ActorId`, `JobId`, `IngestEventId`, `EvidenceId`, `DocumentId`, `RevisionId`, `SheetId`, `JobState`, `IngestOutcome`, `DataClass`, `Clock`, `IdGenerator`, `PdfLimits`, `page_id`, `canonical_document_ids`, and RFC 8785 canonical serialization.

- [ ] **Step 1: Create the pinned build scaffold**

Use this root shape, with exact requirements and a generated lockfile:

```toml
[workspace]
resolver = "3"
members = ["crates/heleos-core"]

[workspace.package]
edition = "2024"
rust-version = "1.96.1"
license = "LicenseRef-Proprietary"

[workspace.dependencies]
serde = { version = "=1.0.229", features = ["derive"] }
serde_json = "=1.0.151"
serde_jcs = "=0.2.0"
sha2 = "=0.11.0"
thiserror = "=2.0.20"
uuid = { version = "=1.26.0", features = ["serde", "v4"] }
```

`rust-toolchain.toml` pins channel `1.96.1`, targets `aarch64-apple-darwin`, `x86_64-pc-windows-msvc`, and `wasm32-wasip1`, and components `rustfmt` and `clippy`. Every crate root begins with `#![forbid(unsafe_code)]`. Add `/target/`, `/.heleos/`, and `/research-cache/` to `.gitignore`. Run `cargo generate-lockfile`, `cargo check -p heleos-core`, and `cargo check -p heleos-core --target x86_64-pc-windows-msvc`; both checks must pass before domain behavior is added.

- [ ] **Step 2: Write failing domain-contract and canonicalization tests**

Create tests that require: a digest parser accepts exactly 64 lowercase hex characters; uppercase, traversal text, and incorrect lengths fail; page IDs are stable and page-index-sensitive; identical content produces identical document/revision IDs; legal job transitions succeed and all other pairs fail; every exact `IngestOutcome` below round-trips; `SECRET`, `INTERNAL`, and `PROJECT_CONFIDENTIAL` are distinct from `PUBLIC`; fake clocks and ID generators return injected values; and RFC 8785/JCS golden inputs produce committed canonical UTF-8 bytes and SHA-256.

Run: `cargo test -p heleos-core --test domain_contracts`

Expected: FAIL with unresolved typed-domain imports.

- [ ] **Step 3: Implement the minimal domain contracts**

`Sha256Digest` owns `[u8; 32]`, implements `FromStr`, `Display`, serde as lowercase hex, and exposes `from_bytes`, `as_bytes`, and `hash_reader`. Generated IDs wrap UUIDs and never accept empty text. `ActorId` accepts 1..128 UTF-8 bytes, rejects control characters, and preserves the supplied Unicode scalar sequence. `DocumentId` and `RevisionId` wrap `Sha256Digest`. `SheetId` wraps the page digest. `IngestOutcome` persists exactly `accepted_new`, `accepted_duplicate`, `idempotent_replay`, `quarantined_corrupt`, `quarantined_encrypted`, `quarantined_unsupported`, `quarantined_suspicious`, `quarantined_limit`, `interrupted`, or `denied_conflict`. Implement:

```rust
pub fn canonical_document_ids(content: Sha256Digest) -> (DocumentId, RevisionId);
pub fn page_id(content: Sha256Digest, zero_based_page_index: u32) -> SheetId;
pub trait Clock { fn now_unix_ms(&self) -> i64; }
pub trait IdGenerator { fn next_uuid(&self) -> uuid::Uuid; }
pub fn can_transition(from: JobState, to: JobState) -> bool;
pub fn canonical_json<T: serde::Serialize>(value: &T) -> Result<Vec<u8>>;
```

`PdfLimits::default()` returns the exact limits in Global Constraints. `HeleosError` has typed variants for I/O, invalid digest/ID, database, migration, integrity, corrupt PDF, encrypted PDF, unsupported PDF, suspicious PDF, resource limit, quota, timeout, invalid state transition, idempotency conflict, quarantine, not found, backup decryption/integrity, invalid backup container, policy denial, sandbox trap, and serialization errors. Error displays must not include file bytes or secrets.

- [ ] **Step 4: Reach green and run quality checks**

Run: `cargo fmt --all --check`

Run: `cargo clippy --workspace --all-targets --all-features -- -D warnings`

Run: `cargo test --locked --workspace --all-targets`

Expected: all commands pass without warnings.

- [ ] **Step 5: Record dependency provenance and commit**

Add an entry for every direct dependency with its exact version, crates.io origin, registry checksum from `Cargo.lock`, SPDX license from crate metadata, no runtime egress, owner `Heleos engineering`, evaluation `admitted for Foundation 0.1`, and rollback `revert Task 2 commit and regenerate Cargo.lock`.

Stage only `.gitignore`, `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`, `rustfmt.toml`, the nine declared `crates/heleos-core` paths, and `governance/tools.toml`; inspect the staged diff and path list.

Commit: `git commit -m "feat: establish typed Rust foundation"`

---

