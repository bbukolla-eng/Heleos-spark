# Task 4 implementation report — immutable evidence vault

## Identity and scope

- Date: 2026-08-28
- Authoring host: macOS 26.6.1 (25G76), `aarch64-apple-darwin`
- Rust: `rustc 1.96.1 (31fca3adb 2026-06-26)`, LLVM 22.1.2
- Cargo: `cargo 1.96.1 (356927216 2026-06-26)`
- Exact reviewed base: `4f84ebdd599dec21475a2c6201f35538aa362e70`
- Implementation commit: `4aba05a371809b48cac5526c2a428165ca905ddc`
- Commit subject: `feat: add immutable evidence vault`
- Authorized implementation paths: exactly the eleven Task 4 paths in the plan and brief.
- This ignored report is evidence only and is never staged. No plan, ledger, brief, or other `.superpowers` path was edited or staged.

## Outcome

The implementation adds a capability-rooted immutable SHA-256 evidence vault with exact create/open modes, validated budgets, bounded streamed hashing, private preallocated staging, atomic no-replace hard-link publication, verified streaming reads, typed non-mutating reconciliation, same-root in-process serialization, cross-process `fs2` locking, strict no-follow/type/reparse/permission/link-count checks, reserve enforcement, panic-safe unlocking, and frozen Windows DACL/no-delete/native-test source.

The only environment limitation is the normal macOS-to-MSVC bundled-SQLite C build: it stops in `libsqlite3-sys` because this host has no Windows C SDK/sysroot (`stdlib.h` is absent). A reproducible Rust-only MSVC probe compiles every Task 4 library/unit/integration-test `cfg(windows)` target successfully. Native NTFS execution remains the explicit Task 10 gate.

## TDD evidence

### Initial RED

- `cargo +1.96.1 test --locked -p heleos-core --test vault`
  - Exit 101 before production implementation: unresolved public vault contracts/imports.
- `cargo +1.96.1 test --locked -p heleos-core --lib`
  - Exit 101 before production implementation: private vault helper/fault contracts did not exist.

### Focused review REDs captured before each corresponding fix

- Empty-object allocation path: the focused private zero-allocation test initially failed to compile with missing `allocate_if_nonzero` (`E0425`); after wiring the helper, it proved a zero-length object never invokes `fs2::FileExt::allocate`.
- Real cleanup/capacity observation: replacing synthetic narratives with production-wired operation observation initially produced 38 missing-event/helper compile errors; the focused cleanup, distinct publication-boundary, and capacity-stop tests then passed.
- Unix socket/unreadable classification:
  - `cargo +1.96.1 test --locked -p heleos-core --test vault verify_and_reconcile_classify_unix_socket_and_unreadable_file_without_data_open -- --exact --nocapture`
  - RED exit 101: a real Unix socket escaped as raw `Io` with macOS code 102 (`Unsupported`) instead of `NonRegular`.
  - GREEN: 1 passed; socket is `NonRegular`, mode `000` regular file is `PermissionViolation`, and reconciliation preserves those typed states without a data open.
- Intermediate shard classification:
  - `cargo +1.96.1 test --locked -p heleos-core --test vault intermediate_shards_preserve_permission_and_nonregular_typed_states -- --exact --nocapture`
  - RED exit 101: denied shard returned raw `Io` code 13 (`PermissionDenied`).
  - GREEN: 1 passed; denied/broad intermediate policy is `PermissionViolation { byte_length: None }`, while a wrong-type/reparse shard is `NonRegular`; `open_verified` fails closed.
- Lock-boundary depth:
  - `cargo +1.96.1 test --locked -p heleos-core --lib vault::tests::same_root_instance_cannot_enter_verification_while_writer_holds_exclusive_lock -- --exact --nocapture`
  - RED exit 101 with 24 missing private hook/event errors (`BlockingOperationFault`, `EventChannelFault`, lock events, and private verification entry did not exist).
  - GREEN focused runs: same-root instance 1/1, independent-process boundary 1/1, and verified-reader two-link exclusion 1/1. The writer pauses immediately after the actual exclusive OS-lock acquisition and before staging/scan; readers record `LockAttempt` but cannot record `LockAcquired` or `OperationEntered` until release. The publication-window test opens the retained staging capability and proves `nlink == 2` while the verified reader is blocked.
- Rust-only Windows source preflight:
  - `cargo +1.96.1 check --locked --offline --all-targets --target x86_64-pc-windows-msvc`
  - RED exit 101 in the retained preprobe: private `FileMarker` fields were inaccessible (`E0616`) and a host-only test helper was referenced under Windows (`E0425`).
  - GREEN after a private Windows-only marker perturbation helper and target-neutral canonical-path assertion: exit 0, no warnings.
- Process-kill pre-link boundary:
  - `cargo +1.96.1 test --locked -p heleos-core --lib vault::tests::windows_process_kill_before_link_after_link_and_after_unlink_is_reconciled -- --exact --nocapture`
  - RED exit 101: the corrected expectation required an abandoned pre-link partial, but the old mislabeled hook paused immediately after exclusive-lock acquisition (`left: false`, `right: true`).
  - GREEN: 1/1 on the macOS host after mapping a distinct private fault to the real production `OperationEvent::BeforeFinalLink`. Before-link kill now leaves exactly one `.partial`, no final, and `StagingPartial`; after-link kill leaves the two-link findings; after-unlink kill leaves a verified unreferenced final. The separate after-lock barrier remains unchanged.

### Additional focused GREEN coverage

- Novel and duplicate empty objects with zero budgets.
- Actual allocation granularity and exact capacity boundaries; zero granularity fails closed.
- Any preallocation error and streamed write `StorageFull`: caller partial cleanup, continued bounded hash/count, exact quota evidence.
- First-use parent-sync sequence and each injected sync failure.
- Post-shard reserve recheck immediately before the final hard link.
- Staging UUID collision retries without unlinking the preexisting name.
- Panic/catch-unwind followed by a successful cross-instance operation.
- Distinct before-link, after-link-after-dir-sync, staging-unlink, staging-dir-sync, and final-reopen windows recorded by production-wired observers.
- Fixed-layout root/objects extras block writes and are reported without mutation.
- Sparse object above 256 MiB rejected from retained metadata before any read; verify/reconcile/write-preflight remain bounded and typed.
- Complete frozen reconciliation matrix, canonical JCS bytes, lossless Unix backslash/raw-byte and Windows UTF-16 path encoding, and unexpected I/O propagation.
- Committed `cfg(windows)` source for true junctions, final/intermediate reparse, denied `FILE_READ_ATTRIBUTES`, broad intermediate DACL, exact private DACL, no-delete/replacement and post-drop success, marker mismatch, retained staging `nlink 1 -> 2 -> 1`, process kill before link/after link/after unlink, capacity-root substitution attempts around actual total/available queries, and actual retained-directory `sync_all` result classification.

## Final host verification

All commands below were rerun on the final source corresponding to the recorded blob/SHA-256 identities.

| Command | Result |
|---|---|
| `cargo +1.96.1 fmt --all --check` | exit 0 |
| `cargo +1.96.1 clippy --locked --workspace --all-targets --all-features -- -D warnings` | exit 0, no warnings |
| `cargo +1.96.1 test --locked -p heleos-core --test migrations` | exit 0, 33/33 |
| `cargo +1.96.1 test --locked -p heleos-core --lib --test vault` | exit 0, 39/39 library + 20/20 public vault |
| `cargo +1.96.1 test --locked --workspace --all-targets --all-features` | exit 0, 108/108 top-level tests: 39 library + 16 domain + 33 migrations + 20 vault |
| `cargo +1.96.1 test --locked -p heleos-core --doc` | exit 0, 1/1 |
| `cargo +1.96.1 check --locked -p heleos-core --all-targets` | exit 0 |
| `cargo +1.96.1 metadata --locked --offline --format-version 1 --no-deps` | exit 0 |

Subprocess helper invocations visible inside the library/migration output also passed; their nested counts are not added to the top-level totals above.

## Normal bundled-SQLite MSVC attempt

Command:

```text
cargo +1.96.1 check --locked -p heleos-core --all-targets --target x86_64-pc-windows-msvc
```

Result: exit 101 before Heleos Rust compilation completes. `libsqlite3-sys v0.38.2` invokes host `cc` with `--target=x86_64-pc-windows-msvc`; Xcode Clang reaches `mm_malloc.h:13:10` and fails with `fatal error: 'stdlib.h' file not found`. Relevant environment was unset: `CC`, target `CC`, `CFLAGS`, `LIBSQLITE3_*`, `VCINSTALLDIR`. No cross-C toolchain, SDK, bundled-SQLite change, or feature weakening was attempted.

## Reproducible Rust-only MSVC probe

### Retained location and canonical inputs

- Probe directory retained for review: `/private/tmp/heleos-task4-windows-probe-final.eC2kwT`
- Production source HEAD during probe: `4f84ebdd599dec21475a2c6201f35538aa362e70` plus the exact uncommitted/staged-equivalent input hashes below.
- Canonical library: `/Users/bekim/Heleos-spark/.worktrees/foundation-0.1/crates/heleos-core/src/lib.rs`
- Canonical domain test: `/Users/bekim/Heleos-spark/.worktrees/foundation-0.1/crates/heleos-core/tests/domain_contracts.rs`
- Canonical migrations test: `/Users/bekim/Heleos-spark/.worktrees/foundation-0.1/crates/heleos-core/tests/migrations.rs`
- Canonical vault test: `/Users/bekim/Heleos-spark/.worktrees/foundation-0.1/crates/heleos-core/tests/vault.rs`

Compiled-input SHA-256 and Git blob identities:

| Input | SHA-256 | Git blob |
|---|---|---|
| `src/lib.rs` | `5c860866be8c91c49d64148b87b4e9391be472d89ccda08301bfa561b9876fa0` | `2b92a92d464028d9f7c7ca0b3ef790ac6e469a04` |
| `src/vault/mod.rs` | `6b5c14c8b15c21e007f164ac0e1b9a34ccb4ba874f3d953c1c1e7e02c35f7b68` | `b6f5511230bdb281b9435284a90d0d1b5e218b7e` |
| `src/vault/path.rs` | `4322b58ef6a4ddee48d311d76a8ddc3488e9bb7f02ee2b86284f408507e8a91f` | `f053e091564013acdecb6059ad672a1fe9a19684` |
| `src/vault/reconcile.rs` | `d5029aaf2c6b56a277167811d2f9aac389989e0929c8849deaf4bfb849001e5b` | `aca87a03ac9c9cb0e02df33ec100fec12e7a44ad` |
| `tests/domain_contracts.rs` | `c98b4b2ecd81b15098a3c76eeae92beee1015817b7a1c82f127a605159a6b9d0` | `5db2058d335bfe65684737047463ce255e9d5143` |
| `tests/migrations.rs` | `48b2a262ae366a8e681646e064a62ed4af5945cde4982727db896ad2893b5eb3` | `1d936d3901d57fa79f78e1ba7e0e0009786a906f` |
| `tests/vault.rs` | `07f43391e2c69190f1ff27d692beacd446b3403826383b71f899080c35d16484` | `c89d2cc775334b0166017ee1a5817a6d7f3e5305` |

### Complete probe manifest

Manifest SHA-256: `295dc2c871655165d257df8037b6f0883e0f05c1207d7e6a6422502686b1c736`

```toml
[package]
name = "heleos-core"
version = "0.1.0"
edition = "2024"
rust-version = "1.96.1"
license = "LicenseRef-Proprietary"

[lib]
name = "heleos_core"
path = "/Users/bekim/Heleos-spark/.worktrees/foundation-0.1/crates/heleos-core/src/lib.rs"

[[test]]
name = "domain_contracts"
path = "/Users/bekim/Heleos-spark/.worktrees/foundation-0.1/crates/heleos-core/tests/domain_contracts.rs"

[[test]]
name = "migrations"
path = "/Users/bekim/Heleos-spark/.worktrees/foundation-0.1/crates/heleos-core/tests/migrations.rs"

[[test]]
name = "vault"
path = "/Users/bekim/Heleos-spark/.worktrees/foundation-0.1/crates/heleos-core/tests/vault.rs"

[dependencies]
cap-fs-ext = { version = "=4.0.3", default-features = false, features = ["std"] }
cap-std = { version = "=4.0.3", default-features = false }
fs2 = "=0.4.3"
rusqlite = { version = "=0.40.2", features = ["modern_sqlite", "backup", "functions"] }
serde = { version = "=1.0.229", features = ["derive"] }
serde_json = "=1.0.151"
serde_jcs = "=0.2.0"
sha2 = "=0.11.0"
thiserror = "=2.0.20"
tempfile = "=3.27.0"
uuid = { version = "=1.26.0", features = ["serde", "v4"] }

[dev-dependencies]
proptest = { version = "=1.11.0", default-features = false, features = ["std"] }

[target.'cfg(unix)'.dependencies]
libc = "=0.2.189"

[target.'cfg(windows)'.dependencies]
stellar-agent-windows-identity = "=0.1.0-alpha.6"
windows-acl = "=0.3.0"
windows-permissions = "=0.2.4"
```

The manifest mirrors every production direct/target/dev dependency and feature. The only deliberate change is `rusqlite` feature `bundled` to `modern_sqlite`; `backup`, `functions`, and defaults remain active.

### Lock normalization and semantic comparison

- Production lock SHA-256 before seed: `17fdce3b4ce237281003ed7023b3154f38f4906f7514c6980a87827c9cf4e5e7`.
- Normalized probe lock SHA-256: `1e3deab6f259dcd07309f78789cd4fa4504a5511370516c59dea29e419d35f46`.
- The byte diff contains one semantic edge removal only: `"cc"` is removed from `libsqlite3-sys 0.38.2` dependencies.
- Filtered Windows production resolve: 87 active package nodes.
- Filtered Windows probe resolve: 84 active package nodes.
- Shared active package identities: 84; every shared name/version/source matches. Because the normalized lock differs only by the single dependency line above, every shared registry checksum is byte-identical.
- Probe-only active packages/nodes after normalizing the root path ID: none.
- Production-only active packages are exactly the bundled SQLite C build branch:
  - `cc 1.4.4`, checksum `0ad534f4357a5264cce5019c989cf66a4f0dc4e0d1b1d15f8aacec0ff7360273`;
  - `find-msvc-tools 0.1.11`, checksum `d45db016d36b838f563236e9193d0ee6ce38f3f68b6c94e914b4929c96bbb890`;
  - `shlex 2.0.1`, checksum `f8fadd59c855ef2080decdef8ff161eb6661b86933c9d82e5ba29dc602a55aba`.
- Active dependency proof: `libsqlite3-sys 0.38.2 -> cc 1.4.4` is the sole incoming active edge to `cc` and has kind `build`; `cc` alone reaches `find-msvc-tools` and `shlex`.
- Active feature delta is exactly:
  - `rusqlite`: production adds `bundled`; both retain `backup`, `functions`, `modern_sqlite`, and the same defaults;
  - `libsqlite3-sys`: production adds `bundled` and `cc`; all other features match;
  - no other shared registry-node feature or dependency edge differs.

Metadata comparison used active `resolve.nodes`, dependency kinds, targets, and sorted feature sets—not only `packages[].dependencies`. Root package IDs were normalized only for the canonical-path difference between the repository and external probe.

### Exact probe commands and results

1. Seed: byte-for-byte copy of production `Cargo.lock` into the fresh owner-private probe.
2. One offline unlocked normalization/typecheck:

   ```text
   cargo +1.96.1 check --offline --all-targets --target x86_64-pc-windows-msvc
   ```

   Result: exit 0; full Rust target graph and all three integration tests compiled.

3. Production and probe metadata:

   ```text
   cargo +1.96.1 metadata --manifest-path /Users/bekim/Heleos-spark/.worktrees/foundation-0.1/Cargo.toml --locked --offline --format-version 1 --filter-platform x86_64-pc-windows-msvc
   cargo +1.96.1 metadata --locked --offline --format-version 1 --filter-platform x86_64-pc-windows-msvc
   ```

   Result: both exit 0; semantic comparison is exactly as recorded above.

4. Frozen locked probe:

   ```text
   cargo +1.96.1 check --locked --offline --all-targets --target x86_64-pc-windows-msvc
   ```

   Result: exit 0, `Finished dev profile`, no warnings. This command was rerun after the corrected real pre-link child-kill hook and compiled that final source.

The probe remains intact at the retained path. It must not be deleted until controller/reviewer release.

## Staging evidence

A final post-approval cached review was performed against the final hashes above:

- `git diff --cached --check`: exit 0.
- `git diff --cached --name-only`: exactly the eleven authorized paths, no plan/scratch/report/probe path.
- Cached stat: 6,838 insertions, 13 deletions across exactly 11 files.

No `.superpowers` report/brief/plan/ledger path and no probe path was staged. Commit and exact commit-SHA update were the only remaining actions at this checkpoint.

## Limitations

- No native Windows test executed on this macOS host. Task 10 owns native local NTFS link, reparse/junction, DACL, delete-sharing, process-kill, ENOSPC, and directory-sync execution.
- The Rust-only probe proves source/type compatibility and nothing about native linking, runtime behavior, or power-loss durability.
- Windows namespace sync is claimed only as best effort for exact raw errors 1, 5, and 6, consistent with the plan; no power-loss namespace guarantee is made.
