# Task 4 Brief: Immutable Evidence Vault

The authoritative contract is Task 4 in `docs/superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md`. Read that complete task and the Global Constraints before editing. This brief is a routing summary, not permission to weaken or reinterpret the plan.

## Exact path scope

- Modify `Cargo.toml`
- Modify `Cargo.lock`
- Modify `crates/heleos-core/Cargo.toml`
- Modify `crates/heleos-core/src/lib.rs`
- Modify `crates/heleos-core/src/store/mod.rs`
- Modify `crates/heleos-core/src/store/permissions.rs`
- Create `crates/heleos-core/src/vault/mod.rs`
- Create `crates/heleos-core/src/vault/path.rs`
- Create `crates/heleos-core/src/vault/reconcile.rs`
- Create `crates/heleos-core/tests/vault.rs`
- Modify `governance/tools.toml`

Do not stage or commit this ignored brief, reports, probes, or any `.superpowers` path.

## Frozen dependencies

- Confirm existing runtime `tempfile = "=3.27.0"`; do not add it again.
- Runtime `cap-std = { version = "=4.0.3", default-features = false }`.
- Runtime `cap-fs-ext = { version = "=4.0.3", default-features = false, features = ["std"] }`.
- Dev-only `proptest = { version = "=1.11.0", default-features = false, features = ["std"] }`.
- No additional publication/link-count dependency. Govern the exact direct and transitive graph, build scripts, unsafe/platform authority, checksums, licenses, no egress, rollback, and allowed APIs.

## Public contract

- `Vault::open(VaultConfig)` with `ExistingOnly`, `CreateOrOpen`, and `CreateNew`.
- `VaultWriteBudget::new(max_input_bytes, max_retain_bytes)` with private fields/accessors.
- `Vault::put_reader(reader, budget) -> Result<PutOutcome>` where a stored result says whether it was newly published and every quota rejection carries its bounded digest/length.
- `Vault::object_key`, `open_verified`, `verify`, and `reconcile` exactly as frozen in the plan.
- `VerifiedObject` is one private read-only verified handle implementing only `Read`, `Seek`, and metadata accessors—no path, reopen, clone, write, or raw handle.
- Use the exact tagged `VaultVerification`, `VaultInventoryEntry`, `EncodedVaultPath`, `ReconciliationFinding`, and sorted `ReconciliationReport` fields/variants in the plan.

## Security and durability invariants

- Root is one normal final component beneath an existing stable owner-private parent. Open the parent/root and every fixed/shard/file component one at a time with OS no-follow plus opened-handle type/reparse validation.
- Reuse only Task 3's crate-private same-handle permission apply/verify helpers. New root/directories/lock/staging are apply-and-verify; existing immutable finals are verify-only.
- Keep a private single-link `.vault.lock`, one retained `fs2` handle, and a process-wide weak `Arc<Mutex>` registry keyed by validated root/lock identity so separate same-root Vault instances serialize. Writers/reconciliation take the cross-process exclusive lock and verified reads take the shared lock. Database-writer-before-vault is the global lock order.
- Keys are only `objects/sha256/aa/bb/<digest>`. Never use a caller filename, arbitrary string, directory entry, or unparsed digest to derive an object path.
- Enforce 256 MiB input, supplied retain budget, fixed 500 GiB logical store cap, and free reserve `max(20 GiB, ceil(total/10))`. The validated root path may be reused only for bracketed `fs2` capacity queries; never for content I/O.
- Preallocate staging with no fallback unreserved writes, stream/hash/count, truncate to exact length, flush/sync, re-hash the same handle, and require private regular non-reparse `nlink == 1`.
- Publish only by capability-relative hard-link no-replace. Under the exclusive lock require `1 -> 2 -> 1`, close Windows no-delete staging handles before unlink, reopen the final no-follow/no-delete, and fully reverify before success. Existing winners are never mutated or replaced.
- Any crash/unlink/sync failure after link is not success and leaves reportable evidence; reconciliation never repairs or deletes it.
- Unix directory sync failures propagate. Windows content sync is mandatory; directory sync is best effort only for raw errors 1, 5, and 6. Claim no Windows power-loss namespace durability.
- On Windows, call `nlink` only on `cap_std::fs::File::metadata()` from an opened handle. Final and directory handles omit delete sharing. Support only owner-private local hard-link-capable storage; no copy/rename/network fallback.

## TDD and evidence

Write public integration RED tests plus private `cfg(test)` race/fault RED tests before implementation. Cover every case in the authoritative plan, including independent-process equal publication, quota evidence, hostile names, DACL/reparse/link-count states, destination substitution, crash states, and non-mutating reconciliation. No production fault hook or test feature.

Required host gates:

```text
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test -p heleos-core --lib --test vault
cargo test --workspace --all-targets --all-features
```

Attempt the normal locked MSVC all-target check, then—if bundled SQLite C stops before Rust—run and fully record the Task 3-style locked/offline Rust-only MSVC probe updated for Task 4 and all three integration tests. Task 10 owns native NTFS execution and the exact Windows matrix in the plan, including `.vault.lock` no-delete/rebind checks and root identity/reparse bracketing around both capacity queries.

Stage exactly the eleven declared paths, run staged-source gates and `git diff --cached --check`, inspect the cached path list, and commit exactly:

```text
feat: add immutable evidence vault
```
