# Windows worker-containment candidate evidence

Recorded: `2026-09-09T03:11:00Z`

Status: implementation committed and cross-compiled; native Windows/NTFS gate
pending.

## Frozen candidate

- Implementation base: `82161f5501b0fd35c2454d5c6bb4eeeeb71d8dec`.
- Implementation commit: `5f3619158ffb56e0df61a68bebd267de468b2f61`.
- Native-gate commit: `e190567eae05ec876049508ff35eeef28ec92320`.
- Branch: `build/agent-control-foundation`.
- Platform writer report SHA-256:
  `6735dd9de30646fdb4391859e01ce8ca4c98c90ae196e5a88badfaf358e74496`.
- Runner writer report SHA-256:
  `12c53fea6cfc7f9b390e9faf92be273e4c81819cd98e47ee7e2a789d665f6056`.
- Native gate script SHA-256:
  `690c44304dc74cc43a506f2b0cfb3d9bd5d7c87a27b04fc82db017fc6a5341c0`.

No reviewer agent was dispatched, per owner direction. Three Astra writers used
non-overlapping implementation paths; Codex ran controller checks and created
the commits. Every writer and controller process named here is terminal. Nothing
was merged into Foundation 0.1, pushed, deployed, published as a workflow, or
given production authority.

## Test-first evidence

The platform task retained three causal portable RED stages before production
behavior: seven encoder/capture/error assertions failed; three constructor/root
validation assertions failed; and one nonempty-root preparation assertion
failed. Their matching GREEN runs passed 7, 10, and then 12 portable cases. A
focused mutation of expired-deadline validation failed the exact test and passed
again after restoration. Windows-only tests first produced a real missing-backend
compile failure. That is compile evidence, not a native behavioral RED result.

The runner task first failed two platform-mode assertions, then failed its exact
containment-evidence assertion. Additional focused RED cases caught unsafe
Windows PATH construction, inherited fixed-variable aliases, and case-folded
forbidden-path aliases. The matching implementations passed every focused test.
No test was disabled or converted into a skip.

## Implemented boundary

`heleos-worker-windows` exposes safe `WritableRoots`, `CommandSpec`, and
`execute` APIs. Its Windows-only system module installs and verifies the Write
Restricted Code SID grant on three empty local NTFS roots, retains identity and
no-delete handles, creates a write-restricted token, constructs literal command
and environment blocks, inherits exactly the three standard pipes, starts the
provider suspended, assigns and verifies a kill-on-close Job Object, resumes,
drains bounded output, and terminates/reaps the Job tree on timeout and normal
provider exit. The crate root and runner forbid unsafe code; raw Win32 FFI is
isolated in the platform system module.

The runner accepts only `windows_restricted_token_job` on Windows. It prepares
checkout/home/tmp before Git materialization, keeps evidence outside those roots,
uses Windows identity-aware inventory and cleanup, and records
`process_tree_contained=true`. It rejects reparse points, hard-linked changed
files, identity drift, unsafe command/environment forms, case-folded forbidden
aliases, and every uncontained Windows mode.

This is provider write/lifecycle containment. It does not restrict reads,
network, credentials, inherited external authority, controller Git, or a more
privileged actor, and it is not an AppContainer or regulatory-isolation claim.

## Controller results on macOS ARM64

All commands below used Rust `1.96.1`, locked/offline dependency resolution, and
the implementation bytes later committed as `5f36191`:

| Command scope | Result |
| --- | --- |
| `heleos-worker-windows` host tests | 12 passed; 0 failed or ignored; Windows-native targets ran 0 on macOS |
| `heleos-worker-runner` host tests | 35 passed; 0 failed or ignored |
| Both packages, host strict Clippy | passed with `-D warnings` |
| Both packages, x86_64 MSVC strict Clippy/check | passed; compile-only |
| Workspace formatting | passed |
| Locked/offline metadata | passed |
| `git diff --check` | passed |

The first normal provenance invocation stopped with exact reason `build snapshot
drift: Cargo.lock`. The verifier binary embeds workspace manifest/lock bytes and
had been built before the intentional workspace-member/lock change. Codex rebuilt
the unchanged verifier with `cargo +1.96.1 build --frozen -p
heleos-verification --bin verify-provenance`; the rerun passed with 167 scanned
files and workspace lock SHA-256
`5ebe1507b3046637631b23e0f5cd39d8fe148fc00577f4c7ab37835a5d422ab1`.
The stop and causal correction are retained; the gate was not bypassed.

The PowerShell gate parses under PowerShell 7.6.4. On this non-Windows host its
normal and preflight routes exit 1 before any command, emit `error_code` equal to
`non_windows`, keep `native_evidence=false`, and report an empty command list.
Its required-test parser rejects missing, ignored, duplicated, listed-only, or
unexecuted evidence.

## Whole-workspace cross-platform closure

The honest command against the unmodified candidate,
`cargo +1.96.1 check --locked --offline --workspace --all-targets --target
x86_64-pc-windows-msvc`, stopped inside upstream
`wasmtime-internal-fiber 48.0.1` because this macOS host has no Windows SDK
`windows.h`. Supplying a deliberately minimal header with SHA-256
`998d03d3a9c0fc18c65ace24d09a50194b9b941b28753f0b2dbbe1b57ed934d4`
advanced the unchanged graph to the next SDK boundary: bundled
`libsqlite3-sys` required `stdlib.h`.

Codex then made a throwaway Git archive of the exact candidate, changed only
the probe copy's `rusqlite` feature from `bundled` to `modern_sqlite`, and let
offline lock normalization remove only the now-unused `libsqlite3-sys -> cc`
edge. With Apple Clang and the Rust 1.96.1 `rust-lld` pinned explicitly, the
probe command with `--workspace --all-targets --all-features`, the MSVC target,
and `-D warnings` passed. Its amd64 COFF fiber object had only the expected
defined wrapper and unresolved `GetCurrentFiber`; the object SHA-256 was
`1b343f334b9e8d54d077e6bc8b4e906bc1ec4f55dd7fe0f8268d8796442082b0`
and archive SHA-256 was
`20ba3e6f4a642ee340ab5c57ed6bf2bf55cb29a00eaef0a0541c47d07f7fdc22`.

The first full host-workspace test run exposed a verifier extensibility defect:
its local-package guard assumed exactly seven total workspace packages and
therefore rejected the three separately classified worker-tool packages. The
test-first fix now requires the exact seven Foundation identities and exact
three tooling identities, each at `0.1.0`, while rejecting unknown, missing,
duplicate, substituted, malformed, registry-substituted, or misversioned local
packages. All 27 verifier unit tests pass, including five package-scope mutation
tests. The frozen secret-scan baseline remained byte-identical at SHA-256
`9824d4222256872805395cd2d898a308486b513146c626594a8b3a91d8d05d9b`.
The final verifier source SHA-256
`c1068213e2cae8f39fa1198f3e0d8b3618721f1a09a9f2a184447805fcc98b2a`
was copied exactly into the throwaway probe, and the complete cross-platform
check passed again.

The first post-repair aggregate test invocation omitted the repository's
required `HELEOS_BIN` harness input. Every suite through the 27 verifier tests
passed, then `foundation_acceptance` stopped immediately with `HELEOS_BIN is
required`. Codex built the pinned CLI (SHA-256
`c4a3079e8bec830985fcec4801baf96e07c51a47c48a1679893cf54c2c3999a5`)
and reproducible guest (SHA-256
`c4a39659129a3d7fe97b3cf01753eec85e2474f514d50cb82b379ccf0f0aedaf`),
set both exact absolute harness paths, and reran
`cargo +1.96.1 test --locked --offline --workspace --all-targets
--all-features` with one test thread. The complete workspace passed with zero
failed or ignored host-applicable tests, including Foundation acceptance, all
seven hostile-intake cases, 20 hostile-storage cases, 18 storage-recovery cases,
15 worker-protocol cases, 35 runner cases, and 12 Windows portable cases. The
missing environment input was retained as a causal invocation correction, not
hidden or reclassified as a passing run.

The throwaway probe is evidence that all workspace Rust conditional code closes
under the MSVC target after explicitly traversing the two documented SDK-only
dependency boundaries. It does not replace an exact-dependency Windows build,
native test execution, or the mandatory clean Windows/NTFS gate below.

## Mandatory native suites

The native gate requires each name to appear exactly once in test listing and
exactly once as a passing executed test.

Platform crate:

1. `native_containment_write_boundary_and_inherited_acl`
2. `native_containment_timeout_kills_descendant_and_drains_output`
3. `native_containment_provider_exit_kills_lingering_descendant`
4. `native_containment_reparse_escape_denied`
5. `native_containment_rejects_reparse_roots_before_launch`
6. `sys::tests::native_containment_unrelated_inheritable_handle_excluded`
7. `sys::tests::native_containment_acl_setup_failure_stops_before_execution`
8. `sys::tests::native_containment_junction_escape_denied`

Runner:

1. `windows_containment_allowed_roots_and_escape_denial_preserve_evidence`
2. `windows_containment_timeout_stops_parent_and_descendant`
3. `windows_containment_literal_argv_and_bounded_output`
4. `windows_containment_inventory_rejects_hardlinks_and_reparse_points`
5. `windows_containment_explicit_mode_and_cleanup_fail_closed`

## Remaining gate

Run `pwsh -File scripts/verify-windows-worker-containment.ps1` from a clean
checkout of the final candidate on a native, matching-architecture Windows host
whose repository and temp paths are local NTFS. The gate freezes the exact Git
SHA, rejects Cargo/Git override or configuration drift, proves all 13 tests ran
natively, executes package and whole-workspace checks, and emits a terminal JSON
summary. Until that exact run passes, no native Windows containment, Windows
parity, or Foundation acceptance is claimed.
