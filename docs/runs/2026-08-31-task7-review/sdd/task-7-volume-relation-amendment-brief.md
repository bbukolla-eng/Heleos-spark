# Task 7 retained-volume relation amendment brief

## Authority and scope

- Base plan Git blob: `233462d3e6aee74f2b2c8fa72a675f6542df09cd`.
- Base plan SHA-256: `909cc47d2b2fae5a3fdf8ac15e13db4d58028df3f94ac2fcbc46a069819e9cb1`.
- Modify only `docs/superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md`.
- Do not inspect or edit moving Task 7 implementation bytes. Do not stage or commit.
- Preserve the exact seventeen-path Task 7 feature boundary and all exact public `BackupService` signatures/DTO fields.

## Proven blocker

The frozen plan requires retained staging/default-temporary anchors to choose exact `Same` versus `Distinct` capacity arithmetic and reject relation drift. macOS can compare retained-handle `dev()` values. Windows core cannot obtain a fallible volume identity: stable Rust 1.96.1 getters are unavailable; `fs2 0.4.3` supplies path-based statistics but no identity; `cap-fs-ext 4.0.3::MetadataExt::dev()` calls `expect`; `heleos-core` cannot name the helper-only direct `cap-primitives` edge; and the helper's current exact surface keeps its safe optional-field extractor private. Path equality or coincident capacity statistics is never volume authority.

## Exact amended helper API

Extend the helper's exact safe surface with only:

```rust
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RetainedVolumeRelation {
    Same,
    Distinct,
}

#[derive(Debug)]
pub enum RetainedVolumeRelationError {
    Unsupported,
    IdentityUnavailable,
    Io(std::io::Error),
}

pub fn retained_volume_relation(
    left: &std::fs::File,
    right: &std::fs::File,
) -> std::result::Result<RetainedVolumeRelation, RetainedVolumeRelationError>;
```

The error implements `Display` and `std::error::Error`; only `Io` exposes a source. No raw device, volume serial, file index, path, handle, or metadata object escapes.

## Exact implementation authority to freeze

- macOS: query both borrowed retained handles with `File::metadata`; compare stable `std::os::unix::fs::MetadataExt::dev()` values. Metadata error becomes `Io`.
- Windows: use the already governed private safe adapter with `cap_primitives::fs::Metadata::from_file` and `_WindowsByHandle::volume_serial_number` only. Adapter error becomes `Io`; either absent value becomes `IdentityUnavailable`; compare concrete values.
- Other targets: return `Unsupported` without opening a path or mutating anything.
- The function borrows and never closes either handle. It performs no path open, filesystem-type discrimination, capacity query, mutation, FFI, unsafe operation, or fallback.
- Keep the existing complete cap-primitives five-symbol allowlist unchanged: `Metadata::from_file` plus `_WindowsByHandle::{file_attributes, volume_serial_number, number_of_links, file_index}`. This API merely reuses two already admitted symbols.
- Core maps every `Unsupported`, `IdentityUnavailable`, or `Io` from this identity gate to `HeleosError::PolicyDenied`; it does not route them through the staging-mutation classifier.
- For every `fs2::{total_space, available_space, allocation_granularity}` bracket, core retains/rechecks each anchor's existing strong file identity/name binding and requires the relation result before and after the bracket to be identical. `Same -> Distinct` or `Distinct -> Same` is `PolicyDenied`.
- Only `Same` selects one combined term/reserve. Only `Distinct` selects independent staging/temporary terms and reserves. Equal paths, names, total space, available space, allocation granularity, or full statistics tuples never infer `Same`.

## Plan sections to reconcile

Reconcile the Task 7 Interfaces, Step 1 dependency/governance prose, Step 2 helper/core tests, Step 4 verify/restore capacity flow, exact helper API block, helper platform implementations, final Task 7 gates, and Task 10 native/API/source gates. Replace statements that the private extractor adds no public helper API with the narrower truth: the extractor remains private; only an opaque `Same`/`Distinct` relation escapes through the exact comparator.

Governance must state:

- `fs2` supplies statistics only and never supplies volume identity.
- The local helper has read-only retained-volume comparison authority in addition to publication authority.
- The existing helper-only `cfg(windows)` featureless cap-primitives edge remains sole; `heleos-core` adds no direct edge.
- No dependency, feature, registry package identity, unsafe site, FFI, Store/Vault API, implementation/test path, or additional `Cargo.lock` delta is added.
- Rollback removes this comparator and refuses verification/restore when relation cannot be proven; it never falls back to paths, statistics, panicking adapters, direct FFI, or a core cap-primitives edge.

## Exact tests and gates to freeze

- Helper-private deterministic tests: `Same`, `Distinct`, left/right metadata error, left/right Windows missing `volume_serial_number`, unsupported target, borrowed handles remain usable, and no panic/path/fallback/mutation.
- Core-private tests: comparator error maps `PolicyDenied`; `Same -> Distinct` and `Distinct -> Same` drift reject; equal statistics do not influence relation; existing exact-equality/one-byte-short same/distinct arithmetic remains unchanged.
- Native Task 10: APFS and NTFS same-volume retained handles; real distinct mounted-volume/VHD coverage when available, with deterministic distinct coverage mandatory regardless; all handles remain retained across before/after statistics brackets.
- Source/metadata gates: exact helper API, exact five-symbol cap-primitives allowlist, helper-only direct edge, zero core cap-primitives/unsafe/direct identity FFI, and zero path/statistics relation inference.

## Handoff contract

Write an immutable Git blob for the amended plan and report its Git blob ID, SHA-256, byte length, line count, diffstat versus the base blob, `git diff --check`, exact Task 7 path count, and confirmation that the public BackupService/DTO block and final feature commit wording are unchanged. Stop unstaged/uncommitted under HOLD for two independent read-only reviews.
