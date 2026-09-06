# Task 7 retained-volume relation amendment report

## Result

- Status: complete under HOLD; plan remains unstaged and uncommitted.
- Base plan Git blob: `233462d3e6aee74f2b2c8fa72a675f6542df09cd`.
- Base plan SHA-256: `909cc47d2b2fae5a3fdf8ac15e13db4d58028df3f94ac2fcbc46a069819e9cb1`.
- Amended plan Git blob: `df28e3b651c45236d4ec0a75cccd5099ab5455a6`.
- Amended plan SHA-256: `95ad6ff7b67532e150b634ee66e441ddfa3bf5f93625285823a66aea5d0041be`.
- Amended plan byte length: `361904`.
- Amended plan line count: `1857`.
- Diffstat versus the base blob: `1 file changed, 44 insertions(+), 20 deletions(-)`.

## Amendment coverage

- Reconciled the global capacity constraint, file responsibility map, Task 7 Interfaces, Step 1 dependency/governance authority, Step 2 helper/core tests, Step 4 capacity flow, exact helper API, macOS/Windows/other-target implementations, final Task 7 gates, and Task 10 native/API/source/capacity gates.
- Added only the exact opaque `RetainedVolumeRelation::{Same, Distinct}`, exact `RetainedVolumeRelationError::{Unsupported, IdentityUnavailable, Io}`, and exact borrowed-handle `retained_volume_relation` signature required by the brief.
- Froze `Display`/`Error` behavior, with only `Io` exposing a source, and prohibited raw identity, path, handle, or metadata escape.
- Froze macOS `File::metadata` plus stable `MetadataExt::dev()`, Windows reuse of the private safe `Metadata::from_file`/`volume_serial_number` adapter, and typed `Unsupported` on other targets.
- Froze direct `PolicyDenied` mapping for every comparator error and both relation-drift directions, outside the staging-mutation classifier.
- Froze `fs2` as statistics-only, retained identity/name plus relation brackets, and the rule that only stable `Same` selects combined arithmetic while only stable `Distinct` selects independent arithmetic.
- Preserved the unchanged complete five-symbol cap-primitives allowlist, helper-only Windows direct edge, zero core edge/use, zero new dependency/feature/package/unsafe/FFI/Store/Vault/path/lock delta, and rollback without fallback.
- Added the exact deterministic helper/core cases and Task 10 APFS/NTFS same-volume plus conditional real distinct-volume/VHD coverage, with deterministic distinct coverage mandatory regardless.
- Preserved Task 10 as an execution/evidence owner only; it adds no implementation, seam, or test path.

## Verification evidence

- Base blob type, SHA-256, byte length, and line count were verified before editing.
- `git diff --no-index --check` from the base blob to the amended plan emitted zero diagnostics.
- Exact Task 7 Files block is byte-for-byte unchanged and contains exactly `17` implementation paths.
- Exact public `BackupService` signatures and DTO block are byte-for-byte unchanged.
- Final feature commit wording remains exactly `git commit -m "feat: add encrypted recovery CLI"` and occurs once in both base and amended plans.
- Exact comparator enum/error/function declarations each occur once.
- Task 10 retains exactly the five declared Task 7 test-host paths.
- Stale wording scan found zero occurrences of the prior no-public-helper-API claim or path/statistics/implicit-filesystem relation language.
- Placeholder scan found zero forbidden plan placeholders; Markdown code-fence count remains even.
- No moving implementation bytes, staged content, or commit were inspected or changed.

## Concerns

None. The amendment is bounded to the controller brief and is ready for two independent read-only reviews under HOLD.

---

## Round 1/5: three-state least-authority repair

This section supersedes the prior binary-relation result and records the reviewed three-state repair.

### Result

- Status: complete under HOLD; plan remains unstaged and uncommitted.
- Round base Git blob: `df28e3b651c45236d4ec0a75cccd5099ab5455a6`.
- Round base SHA-256: `95ad6ff7b67532e150b634ee66e441ddfa3bf5f93625285823a66aea5d0041be`.
- New amended plan Git blob: `f0c08da7ecf5a91351369d5545d689855d76b42c`.
- New amended plan SHA-256: `69c01cd19a6f391417a77a4ea1423553d81808a5259f4689e71e13fde1145620`.
- New amended plan byte length: `368131`.
- New amended plan line count: `1857`.
- Diffstat versus the round base: `1 file changed, 19 insertions(+), 19 deletions(-)`.

### Repair coverage

- Replaced the binary result with exact `RetainedVolumeRelation::{Same, Distinct, Unknown}` and reduced the error surface to exact `RetainedVolumeRelationError::{Unsupported, Io}`; the borrowed-handle comparator signature is unchanged.
- Froze macOS equal/unequal retained-handle `dev()` as `Same`/`Distinct`; Windows unequal concrete serials as `Distinct`, equal or missing serials as `Unknown`, and never `Same`; other targets remain `Unsupported`.
- Recorded the least-authority rationale that u32-formatted serial inequality is a valid negative proof while equality is not collision-resistant, with no file-index/path/name/statistics inference and no GUID/device query.
- Froze the Unknown remaining-work union witness: both `A_s >= T(G_s) + S(G_s) + R_s` and `A_t >= S(G_t) + R_t`, each recomputed with its anchor's own granularity, with no pooling, Distinct substitution, or symmetric full-tree temporary requirement.
- Froze Unknown reserve-only checks, exact equality, either-anchor one-byte shortfall, overflow, different-granularity cases, and the `70` tree / `70` scratch / `20` reserve / `100 + 100` free unsafe-Distinct counterexample.
- Enumerated and rejected all six directed relation transitions while allowing stable Unknown through its union arithmetic; equal paths/names/statistics never infer relation.
- Corrected timing: initial bracket failures deny before any staging path exists; later bracket failures are prepublication `PolicyDenied` with full RAII cleanup and no report, acknowledgement, or destination.
- Corrected governance so cap-primitives has inspection-only and zero mutation/publication authority; `SetFileInformationByHandle(FileRenameInfo)` remains solely windows-sys/local-helper publication authority.
- Reconciled the global constraint, Task 7 Interfaces and Steps 1/2/4, exact helper API/platform behavior/final gates, and Task 10 source/native/capacity/reserve matrices.

### Verification evidence

- Exact public `BackupService` signatures/DTO block and exact Task 7 Files block are byte-for-byte unchanged from the round base.
- Task 7 retains exactly `17` implementation paths; Task 10 retains exactly five Task 7 test-host paths.
- Final feature commit wording remains byte-for-byte unchanged and occurs once in both blobs.
- Comparator signature hash is unchanged; its API-block diff contains only `Unknown` addition and `IdentityUnavailable` removal.
- Plan index check found zero staged changes for the plan.
- Stale binary/error/Windows-Same/governance wording scan and placeholder scan each found zero hits.
- `git diff --no-index --check` emitted zero diagnostics; Markdown code-fence count is even.
- No moving implementation bytes, staged implementation content, or commit were inspected or changed.

### Concerns

None. The round-1 repair is bounded to the controller findings and is ready for the next independent read-only review under HOLD.
