# Windows guarded-worker containment implementation plan

**Status:** Active

**Design:** `docs/superpowers/specs/2026-09-09-windows-worker-containment-design.md`

**Starting branch/commit:** `build/agent-control-foundation` at
`d6e2c3d83403e1771fac014cc789d7ff0ed6069e`

## Global constraints

- Implement; do not dispatch reviewer agents or reopen architecture approval.
- Use one Astra implementation writer per path at a time. Codex owns integration,
  deterministic verification, evidence, and commits.
- Follow test-first RED -> GREEN -> REFACTOR for every behavior change and retain
  the actual failing-command evidence.
- Keep `heleos-worker-runner` free of unsafe code; isolate Win32 FFI in the new
  platform crate and expose a safe API.
- Use the pinned Rust toolchain and locked/offline resolution. Do not add an
  unpinned dependency or normalize the lockfile beyond the new workspace member.
- Do not merge into Foundation 0.1, push, deploy, change workflows, change GitHub
  Apps, read credentials, or inspect the quarantined predecessor repository.
- A macOS cross-compile is not native Windows acceptance. Record that gate
  honestly and continue safe local work.

## Task 1: Build the restricted-token/Job Object platform crate

**Writer paths:**

- `Cargo.toml`
- `Cargo.lock`
- `crates/heleos-worker-windows/**`

Create `heleos-worker-windows` with a safe validated command specification,
portable command-line/environment encoders, bounded output types, typed errors,
and a Windows-only FFI backend. The backend must create the Write Restricted
Code SID and token, install and verify inheritable write-root ACEs, build a
kill-on-close Job Object, create the provider suspended with an explicit handle
allowlist and environment, assign it to the job before resume, drain all pipes,
enforce the deadline, and terminate/reap on every exit path. Reject NULs,
relative/non-file executable paths, `.cmd`/`.bat`, invalid environment names,
duplicate case-insensitive names, invalid/non-directory/reparse write roots,
zero/oversized limits, and already-expired deadlines.

Tests first cover literal quoting vectors, environment byte layout, validation,
capture truncation, and error stability on macOS. Windows-only tests compile for
the target and exercise real ACL/token/Job/process behavior when run natively.
Write the full RED/GREEN command record to this plan's ignored task report.

**Task 1 checks:**

```text
cargo +1.96.1 test --locked --offline -p heleos-worker-windows
cargo +1.96.1 clippy --locked --offline -p heleos-worker-windows --all-targets --all-features -- -D warnings
cargo +1.96.1 check --locked --offline -p heleos-worker-windows --all-targets --target x86_64-pc-windows-msvc
cargo +1.96.1 fmt --all -- --check
```

## Task 2: Port the guarded runner onto the Windows backend

**Writer paths:**

- `crates/heleos-worker-runner/Cargo.toml`
- `crates/heleos-worker-runner/src/**`
- `crates/heleos-worker-runner/tests/**`

Add `WindowsRestrictedTokenJob`, portable runner orchestration, Windows-safe
workspace identity/inventory, and safe error mapping into the existing runner.
Create and authorize checkout/home/tmp before child materialization; keep the
run-directory evidence root unauthorized. Preserve the current Unix and macOS
behavior byte-for-behavior except for additive evidence fields. Windows accepts
only the explicit Windows containment mode and has no uncontained fallback.

Tests first cover mode round-tripping, mismatch failure, evidence fields, exact
scope, inventory and cleanup semantics, then native Windows end-to-end denial,
descendant termination, output, argv, and evidence-integrity cases.

**Task 2 checks:**

```text
cargo +1.96.1 test --locked --offline -p heleos-worker-runner
cargo +1.96.1 clippy --locked --offline -p heleos-worker-runner --all-targets --all-features -- -D warnings
cargo +1.96.1 check --locked --offline -p heleos-worker-runner --all-targets --target x86_64-pc-windows-msvc
cargo +1.96.1 fmt --all -- --check
```

## Task 3: Add native-gate entry points and operator evidence

**Writer paths:**

- `crates/heleos-worker-runner/README.md`
- `crates/heleos-worker-runner/evidence/windows-*`
- `docs/operations/guarded-worker-runner.md`
- `scripts/verify-windows-worker-containment.ps1`
- `CURRENT_STATUS.md`
- this plan's ignored SDD ledger/reports

Add a PowerShell native test entry point that resolves the pinned toolchain,
requires NTFS, records the exact Git SHA and architecture, runs the platform and
runner native suites, and refuses to label cross-compile output as native proof.
Document the opt-in mode, exact claims, failure behavior, and non-goals. Update
status and the compaction ledger with commit IDs, file identities, process state,
checks, limits, and the next unfinished gate.

**Task 3 checks:**

```text
cargo +1.96.1 test --locked --offline --workspace
cargo +1.96.1 clippy --locked --offline --workspace --all-targets --all-features -- -D warnings
cargo +1.96.1 check --locked --offline --workspace --all-targets --target x86_64-pc-windows-msvc
cargo +1.96.1 fmt --all -- --check
scripts/verify-provenance
git diff --check
```

Native Windows execution of the PowerShell entry point is the final acceptance
check for this plan. If no authorized native host is available, commit the
cross-compiled candidate and leave exactly that one gate open; do not weaken or
replace it.
