# Task 7 preflight — stable CLI and encrypted backup/restore

## Verdict

Task 7 is technically decomposable and the Task 5/6 APIs needed for almost all of it are present in the held Task 6 bytes, but it is **not dispatch-ready yet**. Task 6 is still an unstaged, uncommitted in-progress handoff that overlaps Task 7's `crates/heleos-core/src/lib.rs`. In addition, the controller must resolve one real implementation-authority conflict before RED: Task 7 requires atomic no-replace publication of a directory, while the admitted safe APIs and the five exact dependency additions provide no cross-platform no-clobber directory rename.

No other plan change is indicated. Once Task 6 is committed/reviewed and the no-replace primitive is explicitly authorized, Task 7 can proceed inside its fifteen declared paths.

## Authenticated base state

- Worktree: `/Users/bekim/Heleos-spark/.worktrees/foundation-0.1`
- Branch: `foundation-0.1`
- Observed HEAD: `f67b9b7d67e1f726defd72b0c3ca9a23c42f785d` (`docs: account for crash-orphan vault bytes`). This exactly matches the latest Task 6 controller-contract commit recorded in the ledger.
- Authoritative plan git blob: `bd855d63fec241df80cebc877345a4a273c53429`.
- Task 7 brief SHA-256: `24be4c13d26001165175074a5a9678c7e03ff3c2814d4fae211eeea7cf463a4d`.
- Ledger SHA-256: `58403728495bf7dade3f76460260fd2f6d86ea642b77a695bac2122508d24b0f`. It names Task 6 as `in progress` and Task 7 as `pending`.
- Index: empty.
- Worktree: Task 6 is held unstaged in nine modified tracked paths: `crates/heleos-core/migrations/0001_foundation.sql`, `crates/heleos-core/src/error.rs`, `crates/heleos-core/src/lib.rs`, `crates/heleos-core/src/pdf/{geometry.rs,mod.rs,wasi_host.rs}`, `crates/heleos-core/src/store/mod.rs`, `crates/heleos-core/src/vault/mod.rs`, and `crates/heleos-core/tests/migrations.rs`. Its nine untracked files are `crates/heleos-core/src/ingest/{audit.rs,evidence.rs,fault.rs,inspection.rs,job.rs,mod.rs}`, `crates/heleos-core/src/store/ingest_repository.rs`, `crates/heleos-core/tests/ingest.rs`, and `crates/heleos-core/tests/restart.rs`. The overlapping Task 7 path is `crates/heleos-core/src/lib.rs`; no Task 7 implementation may start from this state.
- Observed Task 6 held source identities relevant to Task 7: core `lib.rs` `f3f54889812c661ce540ed5217c4ea7c3ed817a1886181aeee7576aac94515d2`; Store `d997fd4c754cf128418cfd0b9ddeabad7fb0acd97425e56c2cb1c6ab77706ad3`; repository `d1768dc430b2e975159dc73a78e3e1ede745a46e1a9d6fa852dc846b2eabac9e`; Vault `376b38980ec4096c83e58653692e3f02fd78ef6915eaf6b19aa614b82a6949ed`.
- Cargo/governance baseline: root manifest `2688ee42b5afaea247ae40a3df29a2e4f0bbe066d25f05b4b2e7243e456ddb01`; production lock `d06571ece5da554829b071c585e26ade7d4bf8af9ed83ee083a1009397b3f8fa`; core manifest `aeaaf33e6318931ff44e9c3db48c585080a9052657f96ab17d0f7df111f7fc5c`; governance `8da9d9b2ff316da2e0245a501258e5201f95c9013679afd4c80f3835002c3819`.
- Task 5 artifact authority is present: manifest SHA-256 `216d1ffca1a0bfc6a808fe9c427e19b69cc1338af5e32a0aa7be8cb27d5e7e10`; ignored canonical `target/wasm32-wasip1/release/heleos_pdf_guest.wasm` is exactly 1,018,666 bytes and SHA-256 `c4a39659129a3d7fe97b3cf01753eec85e2474f514d50cb82b379ccf0f0aedaf`. Other `.wasm` files exist under `target`; build logic must select this exact underscore-named canonical artifact and reject ambiguity/mismatch, never scan-and-pick a candidate.

The actual Task 7 base SHA is therefore not yet knowable: it must be the reviewed Task 6 implementation commit, with the ledger advanced to Task 7 and a clean index/worktree boundary.

## Dependency and API readiness

### Cargo and governance

- Root workspace currently has four members and no `heleos-cli`. The new member and explicit `[[bin]] name = "heleos"` are unambiguous.
- All five exact requested versions exist and are not yanked in the current crates.io sparse index: `age 0.12.1`, `ed25519-dalek 3.0.0`, `clap 4.6.6`, `tracing 0.1.44`, and `tracing-subscriber 0.3.23`. Their declared MSRVs are at or below the pinned Rust 1.96.1 toolchain.
- `tracing 0.1.44` is already in `Cargo.lock` and the Task 5 aggregate governance record; making it a direct CLI edge changes authority/features but not its locked identity. The other four direct packages are absent from the production lock. Only `tracing-0.1.44.crate` is presently in the local archive cache, so one controlled dependency bootstrap/fetch is required before the new locked/offline gates.
- `age 0.12.1` has no default features and already exposes the required classic `x25519::{Recipient,Identity}`, `Encryptor::with_recipients`, streaming `wrap_output`/mandatory `finish`, and streaming `Decryptor`. Do not enable `armor`, `async`, `cli-common`, `plugin`, or `ssh`.
- `ed25519-dalek 3.0.0` accepts raw 32-byte `SigningKey::from_bytes`, raw 32-byte `VerifyingKey::from_bytes`, 64-byte signatures, and strict verification. Use `verify_strict`; do not enable `hazmat`, key conversion, PKCS#8, PEM, batch, or signing-key generation features.
- `age` brings a separate SHA-2 0.10/curve25519-dalek 4 cryptographic graph while Ed25519 3 uses SHA-2 0.11/curve25519-dalek 5. The lock/governance review must preserve both identities rather than force unification.
- `governance/tools.toml` is append-only. Add direct records for the five Task 7 pins/feature sets and one complete Task 7 resolved-package record covering every newly locked identity, checksum, license, MSRV, build script, unsafe/native surface, active features, egress, and rollback. Record the direct-edge extension for already-locked `tracing` and any explicitly authorized no-replace primitive separately. Do not rewrite the Task 4/5 records.
- Existing workspace packages will also be direct CLI/build dependencies: at minimum `heleos-core`, `heleos-pdf-protocol` with build-only `artifact-host`, `serde`, `serde_json`, `sha2`, `toml`, `tempfile`, and `uuid`; capability/no-follow support must use only already admitted edges unless the controller authorizes an extension. These are not new registry identities but their new direct uses and features belong in the Task 7 governance record.

### Task 5 artifact-policy surface

The shared `heleos-pdf-protocol/artifact-host` API is sufficient and must remain the sole policy authority:

- `guest_resolution_workspace_spec_v1` supplies the exact three-member resolution root and seven-file inventory.
- `validate_guest_source_closure_v1`, `validate_guest_resolution_lock_projection_v1`, `guest_resolution_cache_plan_v1`, `guest_resolution_lock_sha256`, and `guest_resolution_cache_plan_sha256` cover closure/projection/cache identities.
- `normalize_dependency_graph_v1` supplies the dependency records and active registry roots from the independent locked guest metadata plus supplemental full-workspace metadata.
- `artifact_build_policy_v1` supplies the exact Cargo identity, argv, environment, remaps, and scan prefixes; `verify_no_physical_prefixes_v1` enforces leakage policy.
- `validate_pdf_guest_module_policy_v1`, the record-digest helpers, and `source_tree_sha256` cover WASM imports/exports and source/manifest comparisons.
- `validate_guest_build_evidence_v1` must not be called: Task 7 does not build the guest and cannot claim build evidence.

The process runner, capability enumeration, independent resolution-tree materialization, and cache lifecycle currently live as private Task 5 integration-test machinery, not callable production helpers. The brief deliberately assigns those mechanics to `crates/heleos-cli/build.rs`; it may implement mechanics, but it must call the shared protocol functions for every policy decision and must not copy the pure policy algorithms. The build script must concurrently drain stdout/stderr with the 32 MiB cap, use no shell, retain/recheck roots and files, clean both resolution children and the independent cache explicitly, and emit recursive `rerun-if-changed` coverage for all three governed source roots.

### Task 6 Store/FoundationReader/Vault surface

The held Task 6 bytes provide the intended Task 7 bridge:

- `Store::connection` is crate-visible, so `backup` can drive `rusqlite::backup::Backup` on the exact live writer connection without changing `store/mod.rs`.
- `Store::writer_lock`, `require_writer_capability`, and identity rechecks let creation reject in-memory/read-only stores and retain/recheck the exclusive application lock. `BackupService::create` should take `&mut Store` so no application operation can interleave between the inventory read and online snapshot.
- `Store::vault_inventory_rows` is crate-private, bounded at 100,000/100,001, includes committed content plus all nonterminal original/manifest checkpoints, and validates latest-audit binding/conflicts. `VaultInventory.entries` is crate-visible. Task 7 can iterate this exact owned inventory and must not restate its SQL union.
- With `&mut Store`, exact inventory read immediately adjacent to the online backup and no intervening mutation is consistency-equivalent to querying the resulting snapshot while the writer lock is held. If review requires reopening the staged DB and rerunning the inventory query there, current APIs cannot do that inside the fifteen-path scope without adding a Store constructor/query hook; the controller should rule on this interpretation before implementation rather than permit duplicate SQL.
- `Vault::open_verified` rehashes a referenced object under the Vault lock and returns a retained reader with digest, length, and canonical key. Creation must call it exactly once for every inventory entry, compare all three fields, and checked-sum payload bytes. Do not use `reconcile` as a substitute because reconciliation reports unrelated orphan/layout state and does not bind the backup payload stream.
- The lock order is ready: writer Store first, then short Vault operations, never Vault-to-Store. No SQLite transaction may span Vault hashing or age I/O.
- `Store::verify_integrity`, `FoundationReader::verify_audit_chain`, `FoundationReader::evidence_manifest_for_revision`, and `FoundationReader::inspect_foundation` provide semantic verification. `FoundationInspection.revision_ids/evidence` and the evidence reader provide original/manifest lineage. A staged authenticated DB can be given its required checked lock sidecar and opened with `Store::open_read_only`; do not call `open_read_only` on the live source during backup creation.
- `Vault::open(CreateNew)`, `put_reader`, `open_verified`, and the canonical `Vault::object_key` are sufficient to construct and verify the staged restored Vault. No archive path is needed.
- Core already has backup-specific error variants (`BackupDecryption`, `BackupIntegrity`, `InvalidBackupContainer`) and the policy/resource/conflict variants needed for CLI exit mapping.
- Task 6 intentionally provides only traits for `Clock` and `IdGenerator`. The CLI must define its small system-clock and UUID-v4 implementations locally; this is ordinary composition, not a core API gap.

## Conflict and authority rulings required before RED

1. **Task 6 handoff (blocking):** wait for a reviewed Task 6 implementation commit, updated ledger, empty index, and clean declared-path boundary. Re-authenticate every API/hash above at that commit. Do not merge Task 7's `lib.rs` edit into the held Task 6 diff.
2. **Atomic no-replace restored-directory publication (blocking, actual plan/dependency contradiction):** `std::fs::rename` may replace an existing destination on Unix; `tempfile::persist_noclobber` is file-only; cap-std rename is replacement-capable; the current safe public core APIs expose no cross-platform no-clobber directory rename. The brief simultaneously requires an atomic no-replace directory rename and exactly names the new dependency additions. The controller must explicitly authorize a safe primitive/direct-edge extension (for example a governed Unix `rustix 1.1.4` `renameat_with(..., NOREPLACE)` edge plus an independently validated Windows no-replace operation) or provide an already-reviewed core helper. A precheck followed by ordinary rename, reserving then replacing an empty directory, or file-by-file publication is not acceptable and will fail the destination-swap/interruption contract.
3. **Inventory/snapshot interpretation (ruling, not a plan amendment if accepted):** freeze `&mut Store` + exact `vault_inventory_rows()` immediately adjacent to the same-connection online backup as the required snapshot inventory. If that consistency-equivalent reading is rejected, a Store API outside the fifteen paths is required; do not duplicate the Task 6 inventory SQL in `backup`.

All other issues are implementation choices inside the existing plan and path set.

## Proposed contracts and implementation slices

Keep the public surface narrow. `backup/mod.rs` should expose `BackupService::{create,verify_container,restore}`, `BackupManifestV1`, and bounded serializable result DTOs; archive descriptors/parser/fault seams remain private. File-bearing APIs should receive retained `File` handles for ciphertext and key/identity inputs so the CLI performs no-follow opens and core performs same-handle length/type/private-permission checks. Recipient input may be the typed `age::x25519::Recipient`. Creation takes `&mut Store`, `&Vault`, a destination path, recipient, and retained raw signing-key handle. Verification/restoration take retained backup, X25519 identity, and independently supplied raw trusted verifying-key handles. No method accepts inline private keys, a trust-on-first-use signer, overwrite, live cutover, or guest override.

Use these slices, with one writer and review between high-risk slices:

1. **Dependency/build-gate scaffold:** only the two Cargo manifests, lock, core `lib.rs`, empty backup files, the CLI manifest/build/main/args skeleton, and governance. Implement the complete Task 5-derived build gate now, embed only `OUT_DIR` copied canonical bytes, run the Task 5 launcher first, then `cargo check --workspace`. Do not start backup behavior until this slice is reviewed.
2. **Complete backup RED:** write all `backup_restore.rs` contracts before behavior. Within the test file, order RED cases as: fixed grammar/JCS/signature/table/payload bounds; happy create/verify/restore with WAL and held-writer proof; wrong identity/trusted signer/recipient-only forgery; bit flips/truncation/order/kind/length/count/trailing bytes; database/audit/lineage/object semantic corruption; free-space/existing destination/swap; then every create/verify/restore cleanup fault. Record one compile-contract RED for the absent API and retain the complete test matrix before GREEN work.
3. **Complete CLI RED:** write `cli.rs` before CLI behavior. Order: binary/help/command tree and exit codes; unknown/duplicate/conflicting/guest-override rejection; typed IDs/digests/recipient/idempotency caps; adversarial text/`--`/JSON escaping; file type/no-follow/reparse/private-permission cases; restore destination policy; build mismatch negative. Record the expected binary/contract failure before CLI implementation.
4. **Manifest/archive core:** `manifest.rs` owns only the bounded JCS schema/domain-separated Ed25519 bytes; `archive.rs` owns the fixed `HELEOSB1` grammar and streaming parser. Make hostile-input validation order explicit and test parser/signature/table/payload layers without SQLite first.
5. **Create/verify service:** same-writer online DB snapshot; exact Task 6 inventory; per-object rehash; verified plaintext staging; age streaming/finalization; ciphertext sync; file `persist_noclobber`; parent sync; full-decrypt-to-RAII staging and authenticated EOF before parse.
6. **Staged restore:** authenticated DB/Vault construction, defensive read-only integrity/audit/evidence checks, complete sync, authorized atomic no-replace directory publish, parent sync, and cleanup on every return. No existing destination is ever touched.
7. **Thin CLI:** `args.rs` only typed clap grammar and conflicts; `main.rs` owns no-follow retained opens, non-sensitive tracing, service composition, exactly one stdout JSON envelope, escaped stderr JSON, and the frozen exit map. Keep the Task 5 artifact logic exclusively in `build.rs`.
8. **Operations/final:** write the eight required operational topics, then final and staged gates. Stage exactly the fifteen Task 7 paths.

The immutable implementation/staging allowlist is exactly:

```text
Cargo.toml
Cargo.lock
crates/heleos-core/Cargo.toml
crates/heleos-core/src/lib.rs
crates/heleos-core/src/backup/mod.rs
crates/heleos-core/src/backup/archive.rs
crates/heleos-core/src/backup/manifest.rs
crates/heleos-core/tests/backup_restore.rs
crates/heleos-cli/Cargo.toml
crates/heleos-cli/build.rs
crates/heleos-cli/src/main.rs
crates/heleos-cli/src/args.rs
crates/heleos-cli/tests/cli.rs
docs/operations/backup-restore.md
governance/tools.toml
```

Any need to touch a sixteenth path stops implementation for controller authorization; the no-replace and alternate snapshot-query possibilities above are not silently permitted scope growth.

## Exact test and gate matrix

### Handoff and dependency gates

- Authenticate Task 6 commit/ledger and `git diff --cached --quiet`; verify only ignored evidence/artifact paths remain.
- Controlled bootstrap once, then require `cargo +1.96.1 metadata --locked --offline --format-version 1` and exact direct versions in `cargo tree --locked`.
- Assert no `tar` or `zip` package in the Task 7 normal graph; no CLI runtime edge to `heleos-pdf-guest`, `lopdf`, `wasmparser`, or artifact-host policy packages.
- Validate `governance/tools.toml`, every Cargo manifest, artifact manifest, and production/resolution lock as bounded TOML.

### Mandatory Task 5 launcher/build-script gates

Run first at Step 1 and again immediately before final workspace gates:

```text
cargo +1.96.1 test --locked --offline -p heleos-core --test pdf_probe tracked_guest_build_is_reproducible_across_distinct_roots -- --exact --nocapture
```

Then verify the canonical file remains 1,018,666 bytes/SHA-256 `c4a39659129a3d7fe97b3cf01753eec85e2474f514d50cb82b379ccf0f0aedaf`, no `.wasm` is tracked/staged, and `cargo check --workspace` passes. CLI integration must independently exercise build failure in an isolated copied workspace for wrong WASM bytes, wrong manifest fields, an added governed-source file, malformed/oversized subprocess output, path leakage, and an extra/missing cache entry; production exposes no override environment variable.

### RED/GREEN focused gates

```text
cargo +1.96.1 test --locked -p heleos-core --test backup_restore -- --nocapture
cargo +1.96.1 test --locked -p heleos-cli --test cli -- --nocapture
```

The first recorded executions after complete tests must fail because contracts/binary behavior are absent. After implementation, rerun each locked and offline after the controlled fetch. Use exact test-name filters during development for: grammar bounds; signer/identity separation; source writer-lock retention; inventory/nonterminal objects; every create fault; decrypted-staging cleanup; authenticated SQLite/audit/evidence verification; destination swap/no-clobber; CLI usage/exit map; no-follow keys/identity/PDF; and build mismatch.

### Final host gates

Run the Task 5 launcher first, then the exact plan gates:

```text
cargo +1.96.1 fmt --all --check
cargo +1.96.1 clippy --workspace --all-targets --all-features -- -D warnings
cargo +1.96.1 test --locked --workspace --all-targets
```

Also run `cargo +1.96.1 check --locked --offline --workspace --all-targets --all-features`, both focused integrations locked/offline, `git diff --check`, `git diff --cached --check`, `git ls-files '*.wasm'`, and a staged-name equality check against the exact fifteen declared paths. Re-run the focused backup/CLI tests from the staged bytes before commit.

### Windows honest gate and governed fallback

First run the honest command:

```text
cargo +1.96.1 check --locked --offline --workspace --all-targets --all-features --target x86_64-pc-windows-msvc
```

On this macOS host it is expected to stop before Heleos Rust in the already governed missing Windows SDK/CRT headers (`wasmtime-internal-fiber`/bundled SQLite). Preserve the exact first native errors; do not weaken features.

Then use `/private/tmp/heleos-task5-windows-probe-final.bMzCqE` only under the existing Task 5 authority. Before mutation, require its manifest, lock, and sole poison header to match respectively `276356191d289529c476495557bed28fdc6168dbf2aa8c5f3569f7b23afc16a4`, `686d37bf5a2540b491df7bafbd83ef95f2ad8d8b2f1f323ac99f2d20c7c43a2c`, and `998d03d3a9c0fc18c65ace24d09a50194b9b941b28753f0b2dbbe1b57ed934d4`; the header bytes are exactly `typedef void *LPVOID;\nLPVOID GetCurrentFiber(void);\n`. Transiently add exact Task 7 core dependencies and the explicit `backup_restore` target, then compile the real Task 7 core library/test bytes with:

```text
CC_x86_64_pc_windows_msvc=/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang
CFLAGS_x86_64_pc_windows_msvc=-I/private/tmp/heleos-task5-windows-probe-final.bMzCqE/poison-include
AR_x86_64_pc_windows_msvc=/Users/bekim/.rustup/toolchains/1.96.1-aarch64-apple-darwin/lib/rustlib/aarch64-apple-darwin/bin/rust-lld -flavor link /lib
RUSTFLAGS=-Dwarnings
cargo +1.96.1 check --locked --offline --all-targets --target x86_64-pc-windows-msvc
```

A companion transient CLI probe must compile the real Task 7 `args.rs`/`main.rs` Windows target with the same locked dependency identities and environment while supplying only a build-time dummy `OUT_DIR` payload; the real host build script is separately covered by the host artifact gates and cannot supply `cfg(windows)` build-script evidence when cross-compiling. Run focused core/CLI compile targets, then all targets. Restore the retained probe manifest and lock byte-for-byte, reassert all three hashes, remove `RUSTFLAGS`, and rerun the original exact Task 5 all-target command with the same CC/CFLAGS/AR values:

```text
cargo +1.96.1 check --locked --offline --all-targets --target x86_64-pc-windows-msvc
```

Require exit 0/no warnings. This remains compile-only evidence; Task 10 retains real MSVC/Windows-SDK linking and native NTFS reparse/share/no-replace execution.

## Security, lock-order, and format risks

- **Trust separation:** the embedded signer must equal the independently supplied trusted raw public key before signature acceptance. An age recipient holder is not a signer. Creation never claims decryptability.
- **Cryptographic order:** ciphertext cap/free-space check; complete age decryption to owner-only RAII staging; authenticated ciphertext EOF; fixed grammar; trusted signer equality; strict Ed25519 signature over `heleos-backup-v1\0 || manifest`; table caps/hash/count/order; every payload hash/length; exact EOF; only then SQLite. Do not interleave native SQLite with hostile parsing.
- **Format arithmetic:** use checked `u64`/`usize` conversions for every BE length/count/total. The table is exactly `41 * count`; record zero is the one database entry; remaining kinds are blobs in strict digest order with no duplicate. Manifest bytes must round-trip to identical RFC 8785/JCS bytes and fit their own cap.
- **Resource bounds:** cap ciphertext, decrypted container, DB, manifest, table, per-object, count, total decoded payload, and free-space reserve before allocation/copy. Never allocate from an unauthenticated length.
- **Lock order:** take/retain writer Store first; obtain/release Vault locks only through `open_verified`; never acquire Store after Vault. Do not hold SQLite transactions, Vault locks, or key files across external processes. Build-script locks/caches are independent of runtime locks.
- **Consistency:** take `&mut Store`; recheck writer/database identity; query the exact Task 6 inventory; online-copy through that same connection without an intervening service call; recheck identity; then hash exactly the owned inventory. Reject `open_in_memory`, read-only, missing lock, drift, or any inventory/object mismatch.
- **Plaintext exposure:** DB snapshot and plaintext container/decrypted staging are owner-only, same-volume where publication requires it, and guarded before first write. Age `finish`, ciphertext `sync_all`, no-replace publication, and parent sync are separate fault points. Every return path removes plaintext/decrypted staging; diagnostic text never includes keys, recipient secrets, paths containing secrets, SQL, or decrypted bytes.
- **Filesystem races:** no-follow opens must be retained and rechecked for backup/key/identity/PDF inputs. Reject directory/FIFO/device/symlink/reparse and unexpected link count/permissions. Pre-existence checks are advisory only; final file and directory publication must be atomic no-clobber.
- **Restore semantics:** no live cutover or overwrite. Build a sibling complete store, sync every DB/Vault/metadata directory, verify from the staged store, publish once, then sync parent. Failure after successful publish but before parent-sync acknowledgement must return an unknown/durability failure without deleting or replacing the published destination.
- **Artifact build:** multiple target WASM files already exist. Only the exact canonical underscore path plus full manifest/policy validation is acceptable. Build script must not trust filename, mtime, Cargo stdout, seed cache, or production lock alone.
- **CLI output:** clap usage exits 2; integrity 20; quarantine 21; conflict/policy 22; not found 23; internal/process 70; success 0. Exactly one JSON object goes to stdout on handled commands and one escaped JSON diagnostic to stderr on failure. Tracing must not add a second human-formatted stderr record or ANSI output.

## Bounded dispatch recommendation

Do not dispatch an implementer while Task 6 or the no-replace ruling is open. After both close, use one write-owning implementer for all fifteen paths because Cargo/lock/governance, core reexports, build script, and CLI are tightly coupled. Permit at most two concurrent read-only reviewers at slice boundaries: one limited to Task 5 build-script/cache/artifact equivalence, and one limited to backup parser/crypto/filesystem/Windows no-clobber behavior. A third implementation writer would create unsafe overlap in `Cargo.lock`, `Cargo.toml`, `lib.rs`, and governance. Review and commit only after the staged-path and full gate matrix is green.
