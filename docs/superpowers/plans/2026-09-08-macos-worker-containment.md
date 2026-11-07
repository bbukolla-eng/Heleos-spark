# macOS Worker Host-Write Containment Implementation Plan

**Status:** Implemented and locally verified as an isolated candidate

**Branch:** `build/agent-control-foundation`

**Starting commit:** `94cf73dd3ee347f93b5e2d5b8aa9a59580a36155`

## Current finish line

Add an opt-in macOS host-write containment backend to `heleos-worker-runner`.
When selected, the provider and every descendant inherit a macOS Seatbelt policy
that permits filesystem writes only inside the runner-created exact-base
checkout, ephemeral `home`, and ephemeral `tmp` directories. Reads and network
access remain unchanged. Existing post-run Git and filesystem inventory remains
mandatory and continues to control whether a proposal is accepted for further
controller checks.

This slice is isolated engineering preparation. It does not merge into the
Foundation 0.1 candidate, declare Foundation acceptance, grant production
authority, or claim Windows parity.

## Kernel policy decision

Use the installed, root-owned `/usr/bin/sandbox-exec` backend with a static
profile and literal `-D` parameters. The policy is an `allow default` profile
with `file-write*` denied only when the resolved target is outside all three
allowed roots. All roots must be canonicalized before invocation; macOS resolves
temporary paths through `/private/var`, so uncanonicalized `/var` parameters
fail closed even for intended writes.

The provider executable and arguments are passed directly after the Seatbelt
options. No shell constructs task data, paths, parameters, or provider argv.

## Implementation tasks

1. Add a public, serializable containment mode with `none` and
   `macos_seatbelt` values; default to `none` to preserve the existing API.
2. Add a typed containment-unavailable failure for an unsupported platform or
   unusable system backend.
3. On macOS, validate that `/usr/bin/sandbox-exec` resolves to that exact path,
   is a regular root-owned file, and is not group- or other-writable.
4. Canonicalize the checkout, run home, and run temp directories after creation,
   then wrap only the untrusted provider process with the static profile.
5. Keep runner Git setup, evidence writes, inventory, and acceptance behavior
   outside the provider sandbox and unchanged.
6. Record the active containment mode and its narrow guarantees in successful
   run evidence. Failure evidence must retain a typed terminal reason.
7. Expose the opt-in mode through the CLI without accepting an arbitrary sandbox
   executable or profile.
8. Update the runner guide and roadmap status without describing read, network,
   credential, App Sandbox, Windows, or regulatory containment.

## Test-driven acceptance

- RED evidence shows the new API/tests fail before implementation.
- A real macOS integration test proves a provider can write in `checkout`,
  `home`, and `tmp` while an attempted sibling/source-host write is denied.
- A descendant process inherits the denial and cannot create an outside file.
- A path containing spaces and shell metacharacters remains literal.
- Run evidence reports `macos_seatbelt` only when that backend was applied.
- Existing `none` behavior and all current runner tests remain green.
- Non-macOS requests compile and fail closed as containment unavailable.
- `cargo +1.96.1 test --locked --offline -p heleos-worker-runner`, formatting,
  strict package Clippy, `git diff --check`, and the normal provenance gate pass.

## Explicit limits and future

This backend prevents new path-based filesystem writes outside the three
canonical roots for the sandboxed process tree on the tested macOS host. It does
not restrict reads, network access, inherited external authority, provider
internal actions, or cost. Provider compatibility with authenticated Claude and
Kimi remains a separate smoke gate because their real authentication state is
not copied into the ephemeral home. Windows requires a separately implemented
and natively tested Job Object/restricted-process backend.

## Local implementation checkpoint

- Causal RED evidence and the final implementation report are retained under
  `crates/heleos-worker-runner/evidence/macos-containment-*`.
- Native macOS execution passes 3 CLI and 26 runner tests, including the real
  system Seatbelt test; zero tests failed or were ignored.
- Locked/offline strict package Clippy, package formatting, Windows MSVC target
  cross-compilation, `git diff --check`, and the normal repository provenance
  gate pass. Cross-compilation is not native Windows execution evidence.
- Live contained launches are recorded in
  `crates/heleos-worker-runner/evidence/live-seatbelt-provider-runs.md`. Claude
  reached its service but was account-rate-limited before a tool turn. Kimi
  launched after the drained-stdin adapter fix, then its combined credential and
  runtime data root attempted a denied real-home write. No contained provider
  write success is claimed.
- No merge, push, workflow publication, production write, Foundation acceptance,
  or Windows containment claim occurred.
