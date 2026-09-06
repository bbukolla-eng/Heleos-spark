# Task 3 Fix Round 2 Evidence Report

- Reviewed round-2 base/controller HEAD: `ef375839e8eb9419b36a4b3f6b0bfc3824019f54`
- Earlier Task 3 implementation: `99696f5c8c1f3f742e51180bcb5bf6df211470f0`
- Earlier Task 3 fix: `1f3778f9edc03f7332207911db0446c2f152d972`
- Approved controller amendments, excluded from implementation scope: `c87f313` and `ef37583`
- Round-2 implementation commit: `5c731ae8d34bc503f79f7be7bf0892d7075d2f74` (`fix: close durable storage review gaps`)
- Scope: 8 modified implementation paths, all within Task 3's 13 authorized paths; this ignored report is the controller-approved evidence-only exception and is never staged

## Outcome

The round-2 implementation closes the frozen storage review gaps:

- Exact target dependencies are declared and governed: Unix `libc 0.2.189`; Windows `windows-acl 0.3.0`, `windows-permissions 0.2.4`, and `stellar-agent-windows-identity 0.1.0-alpha.6`; `rusqlite 0.40.2` retains `bundled`, `backup`, and `functions`.
- Writer and reader application locks are acquired before any database/WAL/SHM inspection or mutation. Existing-lock contention wins before permission policy evaluation. A synchronized actual-helper race forces two processes past the same absent-lock observation and proves one winner plus exact `HeleosError::WriterBusy`, with no raw `AlreadyExists` leak.
- Every retained lock/database/WAL/SHM open uses an OS no-follow primitive and a handle metadata check. Unix uses `O_NOFOLLOW`; Windows uses `FILE_FLAG_OPEN_REPARSE_POINT`, omits delete sharing, and combines required access and flags without relying on overwritten `OpenOptionsExt` settings.
- Permission mutation and verification now operate on the same retained handle. A Unix replacement analogue proves that handle mutation cannot harden a replacement pathname. Windows code validates handle metadata before DACL installation.
- Sheet insertion requires its exact accepted revision content. Accepted evidence requires accepted derivative and parent objects plus exact accepted revision-parent lineage. Hostile raw SQL and SHA-collision tests exercise the authoritative boundary.
- Windows identity comes only from process `TokenUser` through `stellar_agent_windows_identity::current_user_sid_string`. The SID remains allocated through an exact `ConvertStringSidToSid`/`ConvertSidToStringSid` `OsString`/`OsStr` canonical round trip. No account lookup, SID formatting/debugging, or leaking helper is used.
- Windows hardening constructs a fresh protected non-null DACL with one zero-flag `FA` allow ACE per deduplicated process/SYSTEM SID, installs it atomically on the same retained handle, then independently reads back protection and exact handle-bound ACE membership/type/flags/masks. It never enumerates or mutates a hostile prior ACL.
- Committed `cfg(windows)` source covers canonical domain/service/SYSTEM/cloud SIDs; malformed/noncanonical/injected identities; file/directory hardening; hostile prior DACL replacement; unprotected, inherited, wrong-mask, duplicate, extra, deny, object, callback, null, empty, and absent DACL cases; no-delete rename/delete/replacement; and reparse/nonregular paths. Native execution remains Task 10 ownership.

## Focused RED evidence

The following failures were captured before their corresponding minimal fixes:

```text
cargo +1.96.1 test -p heleos-core --test migrations \
  raw_transactions_enforce_sheet_scale_and_accepted_evidence_lineage -- --exact --nocapture
exit 101
panic: raw SQL attached invalid parent content to a sheet

cargo +1.96.1 test -p heleos-core --test migrations \
  reader_contends_on_existing_lock_before_database_or_sidecar_inspection -- --exact --nocapture
exit 101
reader returned a non-WriterBusy error before the existing exclusive lock was contended

cargo +1.96.1 test -p heleos-core --test migrations \
  writer_denied_by_live_reader_does_not_mutate_authoritative_metadata -- --exact --nocapture
exit 101
the denied writer changed WAL/SHM modes to 0600 and changed source/parent metadata

cargo +1.96.1 test -p heleos-core --lib \
  store::tests::retained_open_options_reject_a_final_symlink_at_the_os_boundary \
  -- --exact --nocapture
exit 101
panic: retained open followed a final symlink

cargo +1.96.1 test -p heleos-core --lib \
  store::tests::simultaneous_first_use_restarts_after_create_race -- --exact --nocapture
exit 101 with the pre-fix branch restored for RED
child panic: first-create caller returned unexpected error:
Io(Os { code: 17, kind: AlreadyExists, message: "File exists" })
parent panic: first-create caller exited without an outcome

cargo +1.96.1 test -p heleos-core --lib \
  store::tests::permission_mutation_targets_the_retained_handle_after_path_replacement \
  -- --exact --nocapture
exit 101
E0425: cannot find function `apply_private_permissions_to_handle` in module `permissions`
```

The reviewed starting Windows implementation additionally had the reported `cfg(windows)` E0308 at incremental `ACL::remove` use and reopened permission paths after retaining store handles. The normal bundled cross-check cannot reach Heleos Rust on this macOS host, so the final external Rust-only probe below is the fresh evidence that the replacement design eliminates that compiler error and type-checks all Windows library and test code.

During the first full GREEN pass, two fixtures correctly exposed their old ordering assumptions:

```text
read_only_open_rejects_broad_permissions_without_repairing_them:
  expected PolicyDenied while an active writer now correctly won as WriterBusy

symlink_and_non_regular_database_or_lock_paths_are_rejected:
  hostile lock fixture collided with the lock intentionally created before invalid DB inspection
```

The fixtures were corrected, not the lock-first implementation: broad source checks now use a crash-left database with no active writer, and the path-type test removes the earlier lock before installing the hostile lock fixture.

## Focused and host GREEN evidence

Fresh commands at the final source content:

```text
cargo +1.96.1 fmt --all --check
exit 0

cargo +1.96.1 clippy --workspace --all-targets --all-features -- -D warnings
exit 0; no warnings

cargo +1.96.1 test -p heleos-core --test migrations
exit 0; 33 passed, 0 failed, 0 ignored

cargo +1.96.1 test --locked -p heleos-core --lib
exit 0; 16 passed, 0 failed, 0 ignored

cargo +1.96.1 test --locked --workspace --all-targets
exit 0; 65 passed total
  heleos-core library: 16 passed
  domain_contracts: 16 passed
  migrations: 33 passed

cargo +1.96.1 test --locked --workspace --doc
exit 0; 1 compile-fail doctest passed
the private migration fault injector is absent from the public API

cargo +1.96.1 check --locked --workspace --all-targets
exit 0; no warnings

cargo +1.96.1 metadata --locked --offline --format-version 1
exit 0

git diff --check
exit 0
```

Focused GREEN results also include the exact RED tests above, content-object SHA collision coverage, raw sheet/scale/evidence lineage tests, reader lock-first exact busy, denied-writer metadata preservation, simultaneous first-create contention, Unix no-follow, same-retained-handle permission mutation, the exact 128/129 integrity boundary, dirty/corrupt and foreign-key integrity reports, snapshot size/capacity/recovery/copy/sync cleanup, and the actual mid-copy shared-lock subprocess barrier.

## Normal bundled MSVC check: honest environment limit

Exact command:

```text
cargo +1.96.1 check --locked -p heleos-core --all-targets \
  --target x86_64-pc-windows-msvc
```

Result:

```text
exit 101
Compiling libsqlite3-sys v0.38.2
clang .../mm_malloc.h:13:10: fatal error: 'stdlib.h' file not found
cc command used --target=x86_64-pc-windows-msvc while no Windows SDK/sysroot is installed
the build stopped in bundled sqlite3.c before Heleos cfg(windows) Rust checking
```

No cross C toolchain was installed, and bundled SQLite or its production features were not changed or weakened. Native bundled-SQLite linking/runtime and execution of the committed Windows negative tests remain the Task 10 Windows CI gate.

## Reproducible external Rust-only Windows probe

The final probe remained outside the repository at canonical path:

```text
/private/tmp/heleos-task3-windows-probe-final.RGzB29
```

Canonical production/source paths:

```text
/Users/bekim/Heleos-spark/.worktrees/foundation-0.1
/Users/bekim/Heleos-spark/.worktrees/foundation-0.1/crates/heleos-core/src/lib.rs
/Users/bekim/Heleos-spark/.worktrees/foundation-0.1/crates/heleos-core/tests/migrations.rs
```

Source HEAD at probe time was the reviewed controller base `ef375839e8eb9419b36a4b3f6b0bfc3824019f54`; because the implementation was intentionally uncommitted, the following hashes bind the compiled inputs rather than falsely attributing them to that commit:

| Input | SHA-256 | Git blob-equivalent ID |
|---|---|---|
| `Cargo.toml` | `4aceebb1c14cf490c34e65bd73f51a21d111b5dff7b55fa25d7970e0b7d94add` | `0548724837e90da6c80cd845589a5a3df125dc01` |
| `Cargo.lock` | `4b82a6a53b79659fc6da61c6211ca9efeb99339e50a819c4be0afacf2790b39d` | `3ce9637d48538a8c45d0976c6f5ef2cc77b0be16` |
| `crates/heleos-core/Cargo.toml` | `a354cd53daf6d1ab6abaa64921dd0b9a393b7e9d72c2eb569fe3c14aee442e01` | `3534e2838e5d661bdc661ec86941e752a95d5492` |
| `src/lib.rs` | `2d000c64f671ccba6de03edb8168c54d361387691b7902ee1323b21bc453bec1` | `2a4917afc5d97fc830b805b5c0fb293e7da43184` |
| `src/error.rs` | `9e2768e486e2119958cd3ac947b4bc9e82410cef196be1c486ef459498523b0c` | `a815fd81921bdc5c147e41e714164f0887bf7e45` |
| `src/store/mod.rs` | `741b3d8c08c7f175c1507bade40f24454f86903a17194a21f7ac64fa10358212` | `870a7b260c92531992b70565487962a9a0d4d887` |
| `src/store/migration.rs` | `9984ff0809147b210c971d6017c5cf1f5f883b58814a5f52d8d870905689af3f` | `23a30794af72448c919dbb67de0a211335de1b99` |
| `src/store/schema.rs` | `103af80f6878d280bb91484700f59f9621dd36eaf17e8752128193bca92a9a11` | `dbb6877fc244e8e59c59d2bb6c712968694c4146` |
| `src/store/permissions.rs` | `6ee0db80dfb6878eefcea0ef35bd5cfc3551436bef16ab4900a983d549aee6b3` | `00ebbc30f32fe71578b7e70e997a281c740cff4c` |
| `migrations/0001_foundation.sql` | `727d2f2a64c81ff200e5aed376706af974a9d27f8b2079ebe68a6e5fc20509c0` | `f3db6562809bd28df613a613a6158f165e662e68` |
| `tests/migrations.rs` | `48b2a262ae366a8e681646e064a62ed4af5945cde4982727db896ad2893b5eb3` | `1d936d3901d57fa79f78e1ba7e0e0009786a906f` |

### Complete probe manifest

Manifest SHA-256: `ac14981146c5b320573142da0b359d3a6459ca747b6411b0d6ffebd77b1bf44d`

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
name = "migrations"
path = "/Users/bekim/Heleos-spark/.worktrees/foundation-0.1/crates/heleos-core/tests/migrations.rs"

[dependencies]
fs2 = "=0.4.3"
rusqlite = { version = "=0.40.2", features = ["modern_sqlite", "backup", "functions"] }
serde = { version = "=1.0.229", features = ["derive"] }
serde_json = "=1.0.151"
serde_jcs = "=0.2.0"
sha2 = "=0.11.0"
thiserror = "=2.0.20"
tempfile = "=3.27.0"
uuid = { version = "=1.26.0", features = ["serde", "v4"] }

[target.'cfg(unix)'.dependencies]
libc = "=0.2.189"

[target.'cfg(windows)'.dependencies]
stellar-agent-windows-identity = "=0.1.0-alpha.6"
windows-acl = "=0.3.0"
windows-permissions = "=0.2.4"
```

The manifest mirrors every exact production direct dependency and target declaration. The only dependency-feature delta is production `rusqlite` `bundled` to probe `modern_sqlite`; `backup`, `functions`, and default features remain enabled.

### Lock normalization and exact semantic diff

The probe was seeded with a byte-for-byte copy of production `Cargo.lock`; `cmp -s` succeeded before the one unlocked normalization run.

```text
production Cargo.lock SHA-256:
4b82a6a53b79659fc6da61c6211ca9efeb99339e50a819c4be0afacf2790b39d

normalized probe Cargo.lock SHA-256:
1caeb7c95d2669029fa2cd015196dd4aee733f13e269b23607d16ab1f6a61689
```

The complete lock semantic diff is:

```diff
 [[package]]
 name = "libsqlite3-sys"
 version = "0.38.2"
 source = "registry+https://github.com/rust-lang/crates.io-index"
 checksum = "f1d20bef17f513b9b3004532233187769cd072d790971f4e4da0e346eb6401e8"
 dependencies = [
- "cc",
  "pkg-config",
  "vcpkg",
 ]
```

No package version, source, or checksum changed.

### Active resolved-graph comparison

Comparison used `cargo +1.96.1 metadata --locked --offline --format-version 1` for both projects, plus a second comparison with `--filter-platform x86_64-pc-windows-msvc`. It compared active `resolve.nodes`, node features, dependency kinds/targets, and name/version/source/checksum identities from each normalized lock. Root path IDs were normalized to `ROOT`.

```text
all-platform shared package identities: 80
all-platform production-only packages: []
all-platform probe-only packages: []
all-platform feature deltas:
  rusqlite 0.40.2: production adds only bundled
  libsqlite3-sys 0.38.2: production adds only bundled and cc-related features
all-platform production-only edges:
  libsqlite3-sys 0.38.2 --build--> cc 1.4.4
all-platform probe-only edges: []

windows-msvc shared package identities: 55
windows-msvc production-only packages:
  cc 1.4.4 / 0ad534f4357a5264cce5019c989cf66a4f0dc4e0d1b1d15f8aacec0ff7360273
  find-msvc-tools 0.1.11 / d45db016d36b838f563236e9193d0ee6ce38f3f68b6c94e914b4929c96bbb890
  shlex 2.0.1 / f8fadd59c855ef2080decdef8ff161eb6661b86933c9d82e5ba29dc602a55aba
windows-msvc probe-only packages: []
windows-msvc feature deltas:
  rusqlite 0.40.2: production adds only bundled
  libsqlite3-sys 0.38.2: production adds only bundled and cc-related features
windows-msvc production-only edges:
  libsqlite3-sys 0.38.2 --build--> cc 1.4.4
  cc 1.4.4 --> find-msvc-tools 0.1.11
  cc 1.4.4 --> shlex 2.0.1
windows-msvc probe-only edges: []

resolved graph comparison: PASS
```

This proves every delta is reachable solely from production bundled `libsqlite3-sys` and its `cc` build path; there is no probe-only graph.

### Probe commands and complete exit results

One offline unlocked normalization/type-check was run in the final probe directory:

```text
cargo +1.96.1 check --offline --all-targets --target x86_64-pc-windows-msvc
exit 0
compiled/checked the exact dependency graph, `heleos_core` library, and `migrations` test target
Finished `dev` profile [unoptimized + debuginfo]
no warnings
```

After graph acceptance, the normalized lockfile was frozen and the exact locked command was run again at the final source hashes above:

```text
cargo +1.96.1 check --locked --offline --all-targets \
  --target x86_64-pc-windows-msvc
exit 0
Checking heleos-core v0.1.0 (/private/tmp/heleos-task3-windows-probe-final.RGzB29)
Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.12s
no warnings
```

The supplemental probe type-checks every Heleos `cfg(windows)` library and test path but intentionally does not claim native link/runtime proof.

## Boundary and remaining limitation

Foundation 0.1 claims cooperating Heleos processes using one configured pathname inside an owner-controlled private directory. Safe rusqlite still opens by pathname rather than a retained application handle. Hostile out-of-band same-principal namespace mutation, hardlink aliases, and a capability VFS are explicitly outside Foundation 0.1 and remain post-0.1 hardening work. This report does not claim otherwise.

# Task 3 Fix Round 3 Evidence

- Reviewed round-3 base: `5c731ae8d34bc503f79f7be7bf0892d7075d2f74`
- Round-3 implementation commit: `8cd40c4428aa61edbaa437f455549dbdb8aaca13` (`fix: request Windows metadata access`)
- Committed scope: only `crates/heleos-core/src/store/permissions.rs`
- Evidence-only exception: this ignored report; it is never staged or committed
- Preserved final probe: `/private/tmp/heleos-task3-windows-probe-final.RGzB29` (retained unchanged as a project; its manifest and lockfile were not regenerated or deleted)

## Finding and bounded correction

Rust 1.96.1's Windows `OpenOptionsExt::access_mode` replaces normal `.read(...)` access. The standalone permission handle requested only `READ_CONTROL` for verification and `READ_CONTROL | WRITE_DAC` for mutation, then immediately called `File::metadata()`. Windows handle metadata through `GetFileInformationByHandle` requires `FILE_READ_ATTRIBUTES` (`0x0000_0080`) or broader generic read access.

The correction keeps the standalone handle least-privileged:

```text
Verify: FILE_READ_ATTRIBUTES | READ_CONTROL = 0x0002_0080
Apply:  FILE_READ_ATTRIBUTES | READ_CONTROL | WRITE_DAC = 0x0006_0080
```

It does not add `GENERIC_READ` or `GENERIC_WRITE`, and it does not alter the separately retained store-file access masks. Existing `FILE_FLAG_OPEN_REPARSE_POINT`, conditional directory `FILE_FLAG_BACKUP_SEMANTICS`, `FILE_SHARE_READ | FILE_SHARE_WRITE` without delete sharing, same-handle metadata validation, DACL mutation, and readback remain unchanged.

## Focused RED and platform limitation

A private `cfg(all(test, windows))` regression test independently asserts the two exact literal masks above. Before the helper/implementation was added, the preserved Rust-only probe compiled every Windows library and test target and failed exactly because the required access-mask contract did not exist:

```text
cd /private/tmp/heleos-task3-windows-probe-final.RGzB29
cargo +1.96.1 check --locked --offline --all-targets \
  --target x86_64-pc-windows-msvc
exit 101
error[E0425]: cannot find function `windows_permission_access_mask` in this scope
permissions.rs:332 and permissions.rs:336
could not compile `heleos-core` (lib test) due to 2 previous errors
```

This macOS host can type-check but cannot execute a Windows test binary. Therefore this is honest compile-time RED/GREEN evidence for the exact mask helper, not a claim of native execution. The existing file-and-directory apply/verify test remains committed for native Task 10 runtime proof.

## Final source identity

| Input | SHA-256 | Git blob ID |
|---|---|---|
| `crates/heleos-core/src/store/permissions.rs` | `3fb5f87c41cfa0251b5e0dabb2f21ac0855c366a9875392638e9c47456478c32` | `e3ad0310e52050a21f226d97105f9c5e3cb572b4` |

## Focused GREEN and host regression gates

All commands below were rerun against the final round-3 source content:

```text
cargo +1.96.1 fmt --all --check
exit 0

cargo +1.96.1 clippy --locked --workspace --all-targets --all-features -- -D warnings
exit 0; no warnings

cargo +1.96.1 test --locked -p heleos-core --test migrations
exit 0; 33 passed, 0 failed, 0 ignored

cargo +1.96.1 test --locked -p heleos-core --lib
exit 0; 16 passed, 0 failed, 0 ignored

cargo +1.96.1 test --locked --workspace --all-targets
exit 0; 65 passed total (16 library + 16 domain + 33 migrations)

cargo +1.96.1 test --locked --workspace --doc
exit 0; 1 compile-fail doctest passed

cargo +1.96.1 check --locked --workspace --all-targets
exit 0; no warnings

cargo +1.96.1 metadata --locked --offline --format-version 1
exit 0
```

The final exact Rust-only Windows probe is GREEN:

```text
cd /private/tmp/heleos-task3-windows-probe-final.RGzB29
cargo +1.96.1 check --locked --offline --all-targets \
  --target x86_64-pc-windows-msvc
exit 0
Checking heleos-core v0.1.0
Finished `dev` profile; no warnings
```

## Normal bundled MSVC check: unchanged honest environment limit

The production command was attempted without changing SQLite features or installing a cross C toolchain:

```text
cargo +1.96.1 check --locked -p heleos-core --all-targets \
  --target x86_64-pc-windows-msvc
exit 101
Compiling libsqlite3-sys v0.38.2
.../clang/21/include/mm_malloc.h:13:10: fatal error: 'stdlib.h' file not found
```

The macOS host lacks a Windows SDK/sysroot, so bundled `sqlite3.c` stops before Heleos Rust is checked. The preserved Rust-only probe supplies the supplemental cfg(windows) type-check; native bundled-SQLite linking and execution remain Task 10's Windows CI responsibility.

## Post-review disposition

Independent review round 4 approved Task 3 with no residual findings. After approval and after all manifest, lock, graph, source-hash, command, and result evidence above had been preserved, the final and WIP throwaway probe directories were moved from `/private/tmp` to the user's Trash as recoverable cleanup:

```text
/Users/bekim/.Trash/heleos-task3-windows-probe-final.RGzB29
/Users/bekim/.Trash/heleos-task3-windows-probe-wip.K7K8k0
```
