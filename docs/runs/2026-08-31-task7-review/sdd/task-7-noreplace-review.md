# Task 7 atomic no-replace directory publication review

## Status and ruling

Base `70484577209ea563d43a4538c35ede8940e9ec35` is clean and Task 6 is complete, so the Task 6 handoff blocker recorded in `task-7-preflight.md` is closed.

Task 7 is still **blocked before RED** on one implementation-authority decision. None of the APIs currently admitted for Heleos can publish a directory atomically, without replacement, on both macOS and Windows while binding the operation to a retained parent. The exact ruling recommended here is:

1. Add one reviewed local platform helper crate, proposed name `heleos-platform-fs`, whose only public mutation is a safe `publish_directory_noreplace` operation over retained handles and one-component names.
2. On the admitted Unix implementation, use an exact direct `rustix = 1.1.4` edge with only `std` and `fs`, and call `renameat_with(parent_fd, staged_name, parent_fd, final_name, RenameFlags::NOREPLACE)`. Never use `CWD`, an absolute path, or a fallback.
3. On Windows, have the isolated helper call `SetFileInformationByHandle` with `FileRenameInfo`, the already opened staged-directory handle as the subject, the retained destination-parent handle in `RootDirectory`, a single relative UTF-16 final component in `FileName`, and `ReplaceIfExists = false`. Do not set `FILE_RENAME_FLAG_REPLACE_IF_EXISTS` or `FILE_RENAME_FLAG_POSIX_SEMANTICS`. Do not fall back to `std::fs::rename`, `MoveFileExW`, copy, or file-by-file publication.
4. Preserve `#![forbid(unsafe_code)]` in `heleos-core`. The small Windows FFI and variable-length `FILE_RENAME_INFO` buffer must be confined to, documented in, and independently reviewed as the only unsafe surface of the helper crate. If a separately reviewed safe dependency exposes this exact handle-relative contract before implementation, it may replace the local helper; no current dependency does.
5. Foundation 0.1 publication support is exact for `target_os = "macos"` and `cfg(windows)`. Other targets return a typed unsupported/policy error without attempting publication. If the controller wants Linux as a supported release target, the same reviewed rustix branch may be explicitly enabled for `target_os = "linux"`; it must not be silently inferred from broad `cfg(unix)`.

This is the smallest safe extension because `rustix 1.1.4`, `windows-sys 0.61.2`, and `windows-link 0.2.1` are already locked and locally present. It adds direct authority/features, not new registry identities. It does add two local helper paths and a narrowly governed authored-unsafe exception.

## Required publication contract

The restore destination must be an absolute path with one nonempty normal final component and no raw `.`/`..` component. The service opens the destination parent no-follow, rejects a symlink/reparse point or non-directory, applies/verifies the existing owner-private permission policy, records its handle identity, and retains that exact parent for staging, publication, cleanup, final reopen, and sync. After that open, ambient parent or destination paths are never publication authority.

The complete restored store is built under an unpredictable bare sibling name in that retained parent, using capability-relative operations. The staged root is owner-private before the first plaintext byte. All child files and directories are closed after verification and synchronization. The staged-root handle remains retained; on Windows it is opened no-follow/non-reparse with at least `DELETE | READ_CONTROL | FILE_READ_ATTRIBUTES`, `FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT`, and share read/write but not share-delete. The retained Windows parent likewise omits share-delete. The final component is encoded losslessly as UTF-16, contains no NUL, and is passed relative to that parent handle.

Immediately before publication, recheck the retained parent, staged-root type/permissions/identity, staging name-to-handle binding, and destination-parent capacity policy. An earlier destination-absence check is only diagnostic. The single kernel rename is the sole decision point:

- macOS: retained-parent `renameat_with(..., NOREPLACE)` using `RENAME_EXCL` through rustix;
- Windows: retained-source `SetFileInformationByHandle(FileRenameInfo)` with retained `RootDirectory` and replacement disabled.

Both names are siblings, so cross-volume operation is impossible by construction. Unsupported kernel/filesystem behavior fails closed. No implementation may reserve an empty destination and later replace it, rename each child, hard-link a directory, or perform precheck plus ordinary rename.

On success, retain the source/final handle so Windows delete sharing prevents a post-rename replacement until verification and parent sync finish. Reopen the final name no-follow relative to the same parent and recheck identity and the complete staged-store invariant before reporting success. A parent-path recheck may detect that the configured display path moved, but it cannot redirect the handle-relative operation; if this is detected after publication, return an unknown/policy outcome and preserve the published directory.

## Error taxonomy and interruption behavior

- Invalid/traversing destination, unsafe parent, parent identity drift, symlink/reparse point, wrong type/permissions, unsupported platform/filesystem, `ENOSYS`, unsupported-flag `EINVAL`, `ENOTSUP`/`EOPNOTSUPP`, or a Windows unsupported-API result: `PolicyDenied` and CLI exit 22.
- Destination collision of any type: `PolicyDenied`/conflict and CLI exit 22. Unix `EEXIST` is authoritative. Windows `ERROR_FILE_EXISTS` (80) and `ERROR_ALREADY_EXISTS` (183) are authoritative; any additional NTFS collision code admitted by native tests must be listed exactly, not inferred from all access-denied errors.
- Missing or replaced staging source, identity mismatch, or an impossible same-parent `EXDEV`: integrity/policy failure; never retry through another primitive.
- Other pre-publication I/O: `Io` and CLI exit 70, with capability-relative cleanup attempted. Cleanup failure supersedes success and is reported without touching any pre-existing destination.
- Failure or injected interruption before the rename leaves the final name absent and removes decrypted staging on every ordinary return/unwind path. Abrupt process death can leave only an owner-private, unmistakably partial sibling; no API can promise process-death cleanup. Recovery must identify it by the private staging grammar and retained-parent rules and never treat it as a restore.
- Once the rename succeeds, the destination is published and must never be deleted by error cleanup. A fault before parent-sync acknowledgement returns `CommitOutcomeUnknown` (or a Task-7-specific durability-unknown variant if separately authorized), CLI exit 70, and preserves the intact destination for owner inspection. A process death has the same state split: either only the private staging sibling exists, or the whole final directory exists; no partial final tree is observable.

## Durability boundary

Before rename, flush and `sync_all` every restored regular file, close SQLite and all Vault handles, then sync mutated directories bottom-up, including the staged root. After rename, `sync_all` the retained destination parent.

On macOS, any file/directory/parent sync failure propagates. Success claims content and namespace durability only after the final parent sync succeeds. On Windows, file content sync is mandatory. Retained-directory sync follows the already reviewed Task 4 rule: only raw errors 1, 5, and 6 may be classified as the platform's unsupported best-effort case; every other error propagates. Even after a successful call, Foundation makes no Windows power-loss namespace-durability claim. The operations guide must state that limitation.

## Why the alternatives are rejected

### Direct libc plus std/Win32 in core

The current `libc 0.2.189` authority permits constants only and expressly says Heleos calls no libc function. Direct `renameat2`/`renameatx_np` requires authored unsafe code, per-OS ABI and availability logic, and would violate core's `#![forbid(unsafe_code)]`. Rustix already supplies the reviewed safe wrapper, including Linux syscall fallback and the macOS weak-symbol `NOSYS` result.

Rust 1.96.1 documents `std::fs::rename` as replacement-capable. Its Windows implementation first calls `MoveFileExW(..., MOVEFILE_REPLACE_EXISTING)` and may fall back to `SetFileInformationByHandle(FileRenameInfoEx)` with both `FILE_RENAME_FLAG_REPLACE_IF_EXISTS` and POSIX semantics. It can replace an existing empty directory on supported Windows/filesystem combinations. It is not a no-replace primitive.

Calling `MoveFileExW` without replacement would avoid final-name clobber, but it remains an ambient full-path operation and does not bind the destination to the retained parent. Calling raw `SetFileInformationByHandle` directly in core would have the right semantics but breaks the existing unsafe boundary. That is why the reviewed isolated helper is required.

### New direct rustix edge alone

This is the correct macOS primitive. Local `rustix 1.1.4` exposes `renameat_with` and `RenameFlags::NOREPLACE` only on Apple, Linux-kernel, and Redox cfgs; its `fs` module is not public on Windows. Therefore rustix cannot close the Windows half by itself. `NOSYS`, `INVAL`, or filesystem non-support must fail closed; tempfile's hard-link fallback must not be copied because directories cannot be hard-linked.

### Existing cap-std, cap-fs-ext, and tempfile

`cap_std::fs::Dir::rename` is documented as replacing an existing target. On Unix it reaches ordinary `renameat`; on Windows cap-primitives reconstructs paths and calls `std::fs::rename`. Current governance also expressly prohibits `Dir::rename` publication.

`tempfile::TempPath::persist_noclobber` is a file API, uses ambient paths, and documents no containing-directory sync. Its Unix fallback is hard-link plus unlink, which cannot publish directories, and its Windows directory behavior is not a public contract. `TempDir` has no atomic no-clobber directory persist API. These APIs remain useful for disposable staging only, not final directory publication.

### Existing Heleos helpers

The Vault and PDF code contains reviewed no-follow, reparse, retained-parent, marker, permission, and directory-sync patterns, but no no-replace directory rename. The Vault path helpers are `pub(super)` and the PDF `ParentAnchor` is private, so Task 7 cannot reuse them without either duplicating security logic in `backup/mod.rs` or expanding scope. Prefer extracting only the common retained-parent/open/recheck/sync mechanics into the new helper or explicitly adding the existing helper path to Task 7 scope; do not weaken visibility ad hoc or use an ambient pathname between rechecks.

## Required tests and ownership

Task 7's existing `backup_restore.rs` path must contain platform-neutral fault/race tests around the exact event sequence: staged tree fully synced; before rename; kernel rename success/failure; after rename; parent sync; final retained/reopened verification. Every injected return before rename removes decrypted staging and leaves an existing destination byte-for-byte unchanged. Every injected return after rename preserves the complete final directory and reports durability unknown.

The real macOS test matrix runs in Task 7 and covers:

- absent destination success and exact restored contents;
- pre-existing empty/nonempty directory, regular file, symlink, and dangling symlink, all unchanged;
- deterministic concurrent destination creation at the pre-rename barrier, with exactly one winner and no replacement;
- retained-parent displacement/replacement, proving the syscall remains bound to the original parent and never publishes into the replacement tree;
- staged-name substitution/identity rejection;
- injected `NOSYS`/`INVAL`/not-supported, rename, and parent-sync failures with no fallback;
- process or subprocess termination immediately before and after rename, demonstrating the two allowed crash states.

The Rust-only MSVC probe in Task 7 must type-check the real helper, exact feature set, buffer builder, error mapping, and `cfg(windows)` integration with `-D warnings`. It is not runtime proof.

Task 10 retains native Windows/NTFS ownership. Its Windows CI must execute the same public restore tests plus committed Windows-only cases for:

- `SetFileInformationByHandle(FileRenameInfo)` absent-destination success and collisions against empty/nonempty directory, file, symlink, junction, and another reparse point;
- exact native collision error codes and fail-closed unsupported-filesystem behavior;
- source and parent access masks, no-delete sharing, retained-handle identity, and attempts to rename/delete/rebind the source, parent, and destination during publication;
- deterministic final-name race with exactly one winner and an unchanged losing destination;
- Unicode/WTF-16 final components, NUL rejection, and bounded `FILE_RENAME_INFO` size/arithmetic;
- kill points before/after rename and before/after parent sync;
- actual directory `sync_all` success or only the exact already governed raw 1/5/6 best-effort classification.

Until that native run passes at the release commit, Task 7 may be a macOS-tested local release candidate but must not claim Windows restore acceptance or Foundation completion.

## Exact plan, dependency, and governance amendment

Amend Task 7 before RED as follows:

- Extend the declared/staging allowlist from fifteen to seventeen paths with `crates/heleos-platform-fs/Cargo.toml` and `crates/heleos-platform-fs/src/lib.rs`; add the local crate to the workspace and make it a private dependency of `heleos-core`.
- Add workspace pins `rustix = { version = "=1.1.4", default-features = false, features = ["std", "fs"] }` for the exact admitted Unix cfg and `windows-sys = { version = "=0.61.2", default-features = false, features = ["Win32_Foundation", "Win32_Storage_FileSystem"] }` for `cfg(windows)`. These reuse locked checksums `b6fe4565b9518b83ef4f91bb47ce29620ca828bd32cb7e408f0062e9930ba190` and the existing locked `windows-sys 0.61.2` identity; lock changes should be dependency-edge/feature changes only.
- Add append-only governance entries for both new direct authorities and for the helper's exact unsafe block, ABI layout, Windows API, access/share masks, no-egress behavior, native-test owner, and rollback. Preserve the existing libc constants-only admission and core `forbid(unsafe_code)` claim.
- Amend the Task 7 implementation text to name retained-handle publication, no fallback, the exact post-rename unknown-outcome rule, and the Windows best-effort namespace-sync limitation.
- Amend the Task 10 Windows matrix to own the native NTFS cases listed above. No separate Task 10 implementation path is required if the Windows-only cases are committed in Task 7's existing test paths and merely executed by Task 10 CI.

If the controller does not authorize the helper crate/two-path scope extension and its narrow Windows unsafe governance exception (or an independently reviewed safe dependency with identical semantics), Task 7 remains blocked. A precheck-plus-rename implementation is not an acceptable temporary compromise.
