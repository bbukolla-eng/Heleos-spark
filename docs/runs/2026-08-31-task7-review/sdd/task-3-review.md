# Task 3 Independent Review

**Verdict: CHANGES_REQUESTED**

- Amended base: `55186c0bc4a0c2d7d087ac40481d791675cc0913`
- Reviewed head: `99696f5c8c1f3f742e51180bcb5bf6df211470f0`
- Scope: all 13 changed paths in the regenerated Task 3 brief, plus the binding Foundation design/plan and Task 3 review package
- Findings: 0 Critical, 8 Important, 1 Minor

## Findings

### Important — The checked database handle is not the file SQLite opens

**Location:** `crates/heleos-core/src/store/mod.rs:67`

`open_writer` first opens and identifies `database_file`, but line 73 asks SQLite to open the pathname a second time. The checks at lines 74 and 76 compare the pathname only with the first Rust `File`; they never establish that SQLite's internal main-database handle refers to that file. A regular-file swap can therefore present file A to `open_checked_regular_file`, file B while `Connection::open_with_flags` runs, and file A again before the recheck. `SQLITE_OPEN_NOFOLLOW` prevents following a final symlink; it does not bind the second pathname open to the already checked handle. The same pattern exists in `open_read_only` at line 91.

This defeats the requested TOCTOU/database-identity invariant and can cause policy checks and later identity checks to describe one file while SQLite reads or writes another.

**Concrete fix:** open SQLite through a safe handle/capability-bound VFS, or use a safe upstream API that obtains the actual SQLite main-file OS identity and compare it with the retained handle before any pragma or migration. Keep the no-follow checks, and add a synchronized replacement test that swaps regular files specifically between the checked open and SQLite open.

### Important — Retaining the lock handle does not retain the lock pathname invariant

**Location:** `crates/heleos-core/src/store/mod.rs:31`

`WriterLock::acquire` validates the lock pathname only during acquisition. After `Store` is returned, renaming/unlinking `<database>.writer.lock` detaches the retained locked handle from that pathname. A second process can create the original pathname, lock its new file, and successfully open the same database while the first `Store` still holds the old lock. The write and migration paths recheck only `database_identity_handle` (`mod.rs:129`, `migration.rs:65`), never the retained lock identity.

This breaks the exact one-cross-process-writer contract under lock-file replacement even though the ordinary two-process happy-path test passes.

**Concrete fix:** retain the lock's original `FileIdentity`, recheck the lock handle/path before opening SQLite and before every migration/immediate transaction, and use a lock namespace or OS primitive whose name cannot silently be rebound while a writer is active. Add a two-process test that replaces the lock path after process one announces readiness and prove process two is denied while process one fails closed rather than continuing to write.

### Important — Windows `FileIdentity` is not a unique file identity

**Location:** `crates/heleos-core/src/store/mod.rs:407`

The Windows identity key is only `file_attributes` plus `creation_time`. Distinct files can share both values, and a process with attribute-write access can deliberately reproduce the timestamp. Consequently, the comparisons in `recheck_file_identity` can accept a replacement file. This is an obvious static defect in the `cfg(windows)` path; the macOS-to-MSVC environment failure does not defer or excuse it.

**Concrete fix:** use a safe upstream Windows API/crate to compare the stable file identifier together with its volume identity from the opened handles, while preserving `#![forbid(unsafe_code)]`. Add native Windows tests for same-timestamp regular-file replacement, reparse points, and cross-volume identity. Task 10 must still provide native Windows execution evidence.

### Important — Windows permission readback does not prove the required DACL

**Location:** `crates/heleos-core/src/store/permissions.rs:93`

`verify_private_permissions` checks only that current-user and `SYSTEM` allow ACEs exist and that no other SID/type is enumerated. It does not verify that the DACL is protected from inheritance, that the ACEs are explicit/non-inherited, or that their masks grant the intended `FILE_ALL_ACCESS`. An unprotected DACL that currently contains only those principals, or even zero/restricted allow masks for those principals, passes this verifier. Although `windows-acl` 0.3.0 applies `PROTECTED_DACL_SECURITY_INFORMATION` when it writes a DACL, the standalone readback contract must independently prove that state rather than infer it from an earlier call.

**Concrete fix:** use a safe Windows security-descriptor API that exposes the DACL protection/control bits; require a protected DACL, exact allowed principals, explicit non-inherited ACEs, and the required masks. Keep the current post-apply readback and add native Windows negative tests for an unprotected but otherwise matching DACL, inherited ACEs, wrong masks, extra allow/deny ACEs, reparse points, and non-regular paths.

### Important — Permission policy is applied at the wrong boundary for readers and SQLite sidecars

**Location:** `crates/heleos-core/src/store/mod.rs:87`

`open_read_only` calls `harden_parent` and then `open_checked_regular_file`, which applies permissions at line 346. It therefore mutates both the directory and database even though the contract says the read-only/query-only open never obtains mutation capability. Conversely, `open_writer` returns after `configure_connection` without hardening or verifying an existing `-wal` or `-shm`; `harden_sqlite_sidecars` is reached only after a later successful migration/transaction. A pre-existing broad, symlink/reparse, or non-regular sidecar is therefore consumed before the permission/no-follow policy is checked, and permission-hardening failure does not abort the open as required.

**Concrete fix:** split apply-and-verify creation/writer behavior from verify-only reader behavior. A reader must verify the parent, main database, WAL, and SHM without changing them. A writer must establish private creation defaults for SQLite-created sidecars and validate/harden existing sidecars before returning or consuming them, using identity-bound handles and no-follow/reparse checks. Add independent mode/DACL snapshots proving read-only open makes no filesystem changes and negative tests for hostile/broad sidecars.

### Important — Integrity checks silently truncate the promised violation set

**Location:** `crates/heleos-core/src/store/mod.rs:144`

The pinned SQLite 3.53.2 implementation documents and implements a default maximum of 100 errors for both bare `PRAGMA integrity_check` and bare `PRAGMA quick_check`. `collect_single_column_check` faithfully collects those rows, but the resulting `IntegrityReport` is not "all violations" as the Task 3 contract requires. The current test only proves a clean database and cannot detect truncation or reporting behavior on a damaged database.

**Concrete fix:** invoke both checks with an explicit maximum that satisfies the agreed complete-report contract (or change the public contract to a bounded/truncated report with an explicit truncation field), retain every returned row, and add independently corrupted fixtures proving non-clean results, more-than-100 behavior, foreign-key detail collection, and `is_clean == false`.

### Important — The faulty-migration entry point is public in every debug build

**Location:** `crates/heleos-core/src/store/migration.rs:76`

`#[cfg(debug_assertions)]` is not test-only isolation. The method is public on `Store` for every dev/debug build, and `#[doc(hidden)]` only hides documentation. The integration test is a separate crate and demonstrates that any downstream debug binary can call this API with arbitrary static SQL and persist it as a new migration. Foundation's later local verification flow builds debug binaries, so this support is neither owner-task-only nor unreachable from production code merely because release optimization omits it.

**Concrete fix:** remove the public method. Test `apply_migration`/rollback from a private `#[cfg(test)]` unit module, or prove rollback through the existing public transaction boundary without exposing a migration injector. If a dedicated harness is unavoidable, gate it behind an explicit test-only feature that release/provenance checks reject and that no production target enables. Add a release/API-surface check proving the hook is absent.

### Important — JSON checks accept non-JCS text despite the canonical-storage contract

**Location:** `crates/heleos-core/migrations/0001_foundation.sql:74`

All JSON columns use only `json_valid`, including the explicitly JCS `sheets.transform_json` at line 127. `json_valid` checks syntax, not RFC 8785 canonical bytes: for example, `{ "b": 2, "a": 1 }` is valid but is not the JCS serialization. Because the public `with_immediate_transaction` surface permits direct inserts and Task 3 provides no typed canonicalizing repository, the database currently accepts non-canonical budget/input/checkpoint, ingest details, transform, metadata, evidence parameters, correction, and audit JSON.

This weakens the frozen reproducibility and audit/evidence byte contracts before Task 6 starts relying on them.

**Concrete fix:** enforce a single canonical insertion boundary. One viable design is to register a deterministic, innocuous JCS-validation scalar before migration and add `CHECK(json_valid(value) AND is_jcs(value))`; another is to restrict writes to typed repository methods that serialize with `canonical_json` and remove the raw public bypass. Add negative tests with whitespace, reversed UTF-16 key order, non-JCS number spellings, and escaped forms for every canonical JSON class, especially `transform_json` and audit values.

### Minor — The migration suite is mostly happy-path and self-referential at key policy boundaries

**Location:** `crates/heleos-core/tests/migrations.rs:263`

The suite passes, but it does not independently assert the 5-second busy timeout, reader `query_only`, DQS/attach settings, required uniqueness collisions/delete behavior, exact Unix modes, hostile permission readback, dirty integrity reporting, path replacement races, or sidecar policy. `private_permissions_are_applied_and_verified_by_readback` merely calls the implementation and its own verifier without independently reading the mode, so the same defect can satisfy both halves. Windows execution remains properly deferred to Task 10, but platform-neutral and Unix negative coverage belongs here.

**Concrete fix:** add independent PRAGMA/config assertions, direct read-only write attempts, collision/delete matrices for every declared unique/FK relation, `PermissionsExt` mode assertions, non-clean integrity fixtures, and synchronized database/lock/sidecar replacement tests. Keep native Windows DACL/reparse execution as an explicit Task 10 gate.

## Confirmed implementation strengths

- The diff contains exactly the 13 declared Task 3 paths, and the worktree was clean before report creation.
- The schema creates exactly the 14 named tables. The declared uniqueness constraints, Task 2 persisted enums, `json_valid` syntax checks, listed immutable triggers, quarantine-to-revision trigger, and explicit FK delete clauses are present.
- Migration 1 is embedded and hash-checked; ordinary reopen is idempotent; each applied migration uses an immediate transaction; hash mismatch and future versions fail closed; the current failing-migration test proves rollback in the dev configuration.
- File-writer connections configure WAL, foreign keys, synchronous FULL, trusted schema off, defensive mode, DQS off, attach-create/write off, private cache, and a 5-second busy timeout. Readers use SQLite read-only flags and set `query_only`.
- Dynamic SQL values in production store code are bound; identifiers/PRAGMA names are compile-time strings. No Heleos-authored `unsafe` block was found.
- Ordinary cross-process contention maps to the distinct `HeleosError::WriterBusy` and retains the acquired handle for `Store` lifetime.
- Recovery documentation covers preservation of DB/WAL/SHM, verified encrypted backup, new staging path, defensive read-only checks, forward migration, manifest comparison, owner-approved cutover, no overwrite, and no down-migration.
- Dependency pins, lockfile checksums, bundled SQLite provenance/hash/version, licenses, upstream unsafe surfaces, permissions/egress, and rollback records are internally consistent. The bundled amalgamation hash independently matched `0a409f1633283fa31a9126b11fbfd64a1991c5d30defad07e5745d4667f5e23d` and identifies SQLite `3.53.2`.

## Verification evidence

Passed at reviewed head:

- `cargo fmt --all --check`
- `cargo clippy --workspace --all-targets --all-features -- -D warnings`
- `cargo test -p heleos-core --test migrations` — 17 passed, 0 failed, 0 ignored
- `cargo test --locked --workspace --all-targets` — 33 passed, 0 failed, 0 ignored
- `cargo metadata --locked --offline --format-version 1 --no-deps`
- `git diff --check 55186c0bc4a0c2d7d087ac40481d791675cc0913..99696f5c8c1f3f742e51180bcb5bf6df211470f0`

Not passed, and not represented as a pass:

- `cargo check --locked -p heleos-core --target x86_64-pc-windows-msvc` exited 101 while compiling bundled `libsqlite3-sys 0.38.2`; macOS `cc --target=x86_64-pc-windows-msvc` could not find `stdlib.h`. This occurs before Heleos `cfg(windows)` Rust type checking. It is the acknowledged environment limitation, not Windows evidence. Native Windows code, DACL behavior, SQLite bundling, locking, and tests remain a Task 10 proof obligation; the static Windows findings above must be fixed before that proof is meaningful.
