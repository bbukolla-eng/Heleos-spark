# Task 7 Store Scratch-Anchor Amendment Brief

## Authority and baseline

- Repository: `/Users/bekim/Heleos-spark/.worktrees/foundation-0.1`
- Baseline HEAD: `d513fa5ba5e209891d7c9cb8d0a47c22fb490582`
- Approved plan blob: `98e509babe56675dbb60ac4ff594f727a286e909`
- Approved full-WIP snapshot before direct-sibling staging: `9c0535c7582905d82d07adde627bf04b3110230e`
- Edit only the Task 7 plan. Do not inspect or modify moving implementation bytes, stage, or commit.

## Blocker

`Store::open_read_only` resolves `tempfile`'s global default directory inside the call and creates its defensive SQLite scratch there. Backup cannot bind that later allocation to the retained temporary anchor used for Task 7 relation/capacity evidence. A process-global tempfile override, ambient `tempdir_in`, or pre/post selector recheck does not bind creation or cleanup. The current plan simultaneously requires retained-anchor capacity evidence, forbids a Store path/API change, and freezes exactly seventeen paths; those requirements are incompatible.

## Required plan repair

Add `crates/heleos-core/src/store/mod.rs` to Task 7. The final atomic feature boundary becomes exactly eighteen paths. Public Store and Backup APIs stay unchanged; add only these crate-private Store surfaces, never re-exported:

```rust
pub(crate) struct ReadOnlySnapshotParent { /* private fields */ }

impl ReadOnlySnapshotParent {
    pub(crate) fn retain_default() -> Result<Self>;
    pub(crate) fn path(&self) -> &std::path::Path;
    pub(crate) fn retained_file(&self) -> &std::fs::File;
    pub(crate) fn recheck(&self) -> Result<()>;
}

impl Store {
    pub(crate) fn open_read_only_with_snapshot_parent(
        path: &std::path::Path,
        snapshot_parent: &ReadOnlySnapshotParent,
    ) -> Result<Self>;

    pub(crate) fn close_read_only(self) -> Result<()>;
}
```

`retain_default()` calls pinned `tempfile::env::temp_dir()` exactly once, owns that selector value, canonicalizes it, opens it no-follow/non-reparse as a directory, retains the handle, records the existing strong marker, and immediately rechecks ambient name/path binding. It never calls `override_temp_dir` or mutates process environment. Windows retains no-delete sharing; Unix uses device/inode. Backup owns the same anchor from before initial admission until the reader closes and the final reserve bracket completes.

Public `Store::open_read_only` remains source/API compatible and preserves its current source-validation/lock/error order. At the existing temporary-directory creation point it retains one default parent, then delegates to a shared private implementation in Store-managed-capacity mode. The crate-private opener uses caller-admitted-capacity mode. No lifetime escapes: Store fallibly clones the retained parent capability and owns its scratch state.

## Capability-owned scratch

Replace Store's `Option<tempfile::TempDir>` scratch field with a private `ReadSnapshotDirectory` guard. It owns:

- a fallible clone of the retained parent capability plus the captured canonical parent path/marker;
- one unpredictable normal child name;
- the retained no-follow/non-reparse child-directory handle/marker;
- the retained scratch `snapshot.sqlite3` handle/marker;
- the derived ambient child/database paths solely for APIs such as rusqlite that require paths;
- explicit ownership state and optional retained handles so checked close can drop them in the required order.

Use pinned tempfile only as a bounded random-name generator: `Builder::make_in` with exact prefix `heleos-read-snapshot-`, empty suffix, `rand_bytes(32)`, and `disable_cleanup(true)`. Before the closure, clone cleanup authority. Inside the closure do only: require the candidate strips to exactly one `Component::Normal`; atomically capability-create that one relative directory; construct an infallible provisional cleanup guard. `AlreadyExists` uses tempfile's bounded retry behavior. Perform every other fallible action only after `make_in` returns. Consume with `into_parts`; the disabled `TempPath` never creates, removes, closes, keeps, or persists anything.

Create with exact private mode on Unix. On Windows capability-open the new directory no-follow/non-reparse at share `0x3` with the already governed directory flags and enough `READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES` authority; apply and read back private permissions on the retained handle. Recheck child binding and require the directory empty before the first database or WAL byte.

Every scratch mutation other than rusqlite is capability-relative: database/WAL copy, file open, permissions, enumeration, validation, and cleanup. Retain the scratch database handle across Store lifetime. Immediately before and after ambient rusqlite open, after configuration/recovery, and before returning Store, recheck all three bindings: captured parent path to retained parent; child name/ambient child to retained child; relative/ambient `snapshot.sqlite3` to the retained database. Preserve the already frozen same-principal SQLite pathname limitation: these endpoint checks detect ordinary rebinding but do not claim continuous protection against a transient Unix ABA replacement; capability-aware SQLite VFS remains post-0.1.

Keep existing source database/WAL/SHM handles, reader lock, query-only SQLite configuration, source length/identity rechecks, hard caps, allowed entries, and recovery validation. Store-managed public mode preserves both existing capacity checks: pre-copy and post-recovery. Caller-admitted mode skips both Store-owned `fs2` query portions but preserves every logical size/growth/recovery allowance/layout validation; Task 7 owns the complete relation/statistics brackets and checks its reserve at actual scratch peak.

## Checked cleanup and error order

Field/drop ownership keeps `Connection` before every scratch handle/owner. `ReadSnapshotDirectory::close(self) -> Result<()>` drops the retained scratch database and child handles first, rechecks parent/child/database name and identity as applicable, removes only its exact one-component child through the retained parent capability, and verifies absence. On Windows truthfully disclose that pinned cap-std's one-component recursive removal internally resolves a handle/path and closes it before deletion; Heleos invokes only the retained-parent one-component API and never falls back to ambient `std::fs::remove_dir_all`.

`Store::close_read_only(self)` closes the rusqlite `Connection` first, releases Store-owned scratch users, then performs checked scratch close. Task 7 must call it before any verification report, receipt, restore publication, or acknowledgement. `Drop` remains best-effort unwind fallback only. Ordinary failures return the original error only if cleanup is certain; cleanup failure or binding uncertainty overrides success/primary failure with its exact `Io` or `PolicyDenied` outcome. Never delete a mismatched/decoy entry.

Atomic create/copy/open errors preserve exact `HeleosError::Io` and `ErrorKind`; only Task 7's existing staging-orchestration boundary may map preserved `StorageFull`/`QuotaExceeded` to `PolicyDenied`. Identity/type/reparse/name/emptiness/permission mismatch is `PolicyDenied`. SQLite stays `Database`. Do not broadly remap staging errors.

## Tests and governance

Private tests remain in existing source files; no new test path or public seam:

- `store/mod.rs`: tempfile selector captured exactly once in a subprocess; environment/global choice after capture cannot redirect; collision retry; exact normal child; no TempPath mutation; no leaked child after post-create failure; private permission and empty directory before first byte; parent/child/database rebind barriers; database handle retained through Store lifetime; public mode executes both capacity checks; caller-admitted mode executes zero Store-owned `fs2`; checked close order, cleanup failure, decoy preservation, Windows handle-drop order; scratch persists while reader lives and is absent after checked close; exact I/O/Database taxonomy.
- `backup/mod.rs`: relation/statistics brackets use the same `ReadOnlySnapshotParent`; Store scratch is created under that admitted anchor; anchor lives through actual scratch peak, checked Store close, and reserve recheck; comparator/capacity/staging classifiers retain their frozen behavior.

Update Task 7/Task 10 test-host declarations from five to six paths by adding `crates/heleos-core/src/store/mod.rs`; the full core library test gate executes them. Update cached-name/path-count/final feature commit wording to exactly eighteen paths.

Governance must extend tempfile authority only for `env::temp_dir`, `Builder::make_in`, bounded random-name generation, and disabled TempPath cleanup; cap-std owns one-component retained-parent create/open/enumerate/removal and accurately discloses pinned Windows internal path resolution. Replace every blanket “no Store/Vault API/path change” claim with “no public Store/Vault API/path change; only the exact crate-private retained snapshot-parent/open/checked-close exception.” No new dependency, lockfile identity, unsafe site, FFI, environment mutation, or Vault change is authorized.

## Stop rules

Stop and return for review on any public Store API change, new file/test path beyond `store/mod.rs`, dependency/feature/lock expansion, global temp override/environment mutation, ambient tempdir/tempdir_in creation, unretained SQLite scratch database, unchecked cleanup acknowledgement, blind removal, capability-VFS claim, or alteration of the frozen BackupService/DTO surface.
