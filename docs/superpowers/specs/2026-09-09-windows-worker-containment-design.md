# Windows guarded-worker containment design

**Status:** Active implementation baseline

**Date:** 2026-09-09

**Repository lane:** `build/agent-control-foundation`

## Objective

Add a Windows backend for the guarded local worker runner that can launch one
approved console provider under an operating-system-enforced write boundary and
terminate the complete provider process tree. The backend must fail closed. A
macOS cross-compile proves only build portability; native Windows/NTFS tests are
required before this backend is described as accepted Windows containment.

This work does not alter Foundation 0.1 release state, merge the agent-control
lane into the Foundation candidate, push Git history, or authorize remote use.

## Security contract

The controller remains privileged relative to the provider. It creates and
validates the run directory, materializes the exact-base checkout, writes frozen
assignment evidence, launches the provider, inventories the result, and writes
the final handoff. Only the provider process tree receives the restricted token.

When `windows_restricted_token_job` is selected on Windows:

1. The controller creates three provider-writable roots: the exact-base
   `checkout`, an ephemeral `home`, and an ephemeral `tmp`.
2. Before any child content is created, each root receives an inheritable allow
   ACE for the Windows Write Restricted Code SID. The ACE grants access; it does
   not replace or broaden the caller's ordinary access check.
3. The provider token is derived from the current process token with
   `DISABLE_MAX_PRIVILEGE | WRITE_RESTRICTED` and the Write Restricted Code SID
   in its restricting-SID list. Windows must therefore pass both the ordinary
   enabled-SID access check and, for write access, the restricting-SID check.
4. Evidence at the run-directory root, the source repository, sibling paths,
   and ordinary user-profile locations do not receive that ACE. Provider writes
   to those locations must be denied by the kernel even when the ordinary user
   token could write them.
5. The exact executable path is passed as `lpApplicationName`; the mutable
   command-line buffer is built with the documented Microsoft C/C++ argument
   quoting rules. No shell, command interpreter, `.cmd`, or `.bat` adapter is
   introduced.
6. The child is created suspended with `CreateProcessAsUserW`, an explicit
   Unicode environment block, redirected standard handles, and a
   `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` containing only its three pipe handles.
7. Before the primary thread is resumed, the child is assigned to a fresh Job
   Object configured with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Breakaway is not
   enabled. Descendants therefore remain in the job by default.
8. Provider exit, timeout, error, unwind, or controller-side early return closes
   or terminates the job and reaps the primary process. Output pipes are drained
   concurrently and retained bytes remain independently bounded per stream.
9. Backend setup is verified before resume: root ACL installation/readback,
   restricted-token status, Job Object limit readback, and successful process
   assignment. Any ambiguous or failed step returns a typed failure without an
   uncontained fallback.

The backend restricts filesystem writes reachable through normal Windows access
checks and contains the spawned process tree. It does not claim read isolation,
network isolation, secret redaction inside an explicitly supplied environment,
provider-internal action accounting, AppContainer isolation, Hyper-V isolation,
regulatory sandboxing, or protection from a more-privileged process.

## Unsafe-code boundary

The existing `heleos-worker-runner` keeps `#![forbid(unsafe_code)]`. Win32 FFI is
isolated in a new `heleos-worker-windows` crate. Its crate root denies unsafe
code, and only one Windows-only `sys` module receives a narrowly scoped
`#[allow(unsafe_code)]` annotation. Public constructors validate all lengths,
NULs, absolute paths, path object types, environment names, and output bounds
before reaching that module. Raw handles are owned by small RAII types and are
closed exactly once.

## Filesystem requirements

- Writable roots are controller-created, canonical local directories with no
  reparse-point component at the final path.
- The checkout ACL is installed before clone/materialization so descendant files
  inherit the restricting-SID ACE. `home` and `tmp` are treated the same way.
- The run-directory root never receives the provider ACE. Assignment, prompt,
  ownership, stdout/stderr, and run/failure evidence therefore remain outside
  provider write authority.
- ACL application uses a retained directory handle where Windows permits it,
  preserves the controller's existing DACL, and verifies one effective
  inheritable allow entry for the restricting SID.
- Windows inventory uses opened-handle identity and rejects reparse points,
  hard-linked regular files, special objects, identity changes between path and
  handle, invalid relative names, and configured inventory limits.
- Cleanup remains explicit and identity-checked. A replaced run directory is
  retained and reported; the replacement is never recursively removed.

## Process and environment requirements

- Provider application, argument, current-directory, and environment values are
  data, never shell source.
- Argument quoting round-trips empty values, spaces, tabs, quotes, and runs of
  backslashes immediately before a quote or the closing delimiter.
- Environment names are non-empty, contain neither NUL nor `=`, and compare
  case-insensitively so aliases such as `PATH` and `Path` are rejected.
- Values contain no NUL. The encoded block is case-insensitively sorted,
  `name=value\0` per entry, and terminated with a second NUL.
- The runner supplies only its fixed platform variables plus explicitly approved
  provider variables. It does not inherit the ambient environment wholesale.
- Output readers drain to EOF even after the retention limit is reached. A busy
  stream cannot prevent deadline enforcement.
- The provider cannot inherit unrelated controller handles.

## Public integration

`ContainmentMode` gains `WindowsRestrictedTokenJob`. It is accepted only on
Windows. `MacosSeatbelt` remains macOS-only. A mismatched explicit mode returns
`containment_unavailable`; it never silently becomes `none`.

Successful evidence records the exact mode and these claims:

- `host_path_writes_restricted: true`
- `process_tree_contained: true` only for the Windows backend
- `write_scope: ["checkout", "home", "tmp"]`
- `reads_restricted: false`
- `network_restricted: false`
- `inherited_external_authority_restricted: false`

Default Windows execution remains unavailable until the runner's Windows
filesystem and process path is selected explicitly through the new mode. There
is no uncontained Windows launch path in this milestone.

## Acceptance evidence

Portable tests must first fail against missing behavior and then pass for:

- exact Windows command-line quoting vectors;
- environment validation, case-insensitive duplicate rejection, ordering, and
  double-NUL encoding;
- bounded capture and typed error mapping that do not require Windows to run;
- runner mode serialization, evidence claims, and platform mismatch behavior;
  and
- the entire workspace's format, strict Clippy, locked/offline tests, provenance,
  and Windows MSVC cross-compilation.

Native Windows/NTFS tests must additionally prove:

- writes succeed in checkout, home, and tmp;
- direct, sibling, source-repository, ordinary-profile, descendant-process, and
  junction/symlink escape writes fail;
- assignment evidence cannot be rewritten;
- only the three pipe handles are inherited;
- a timed-out parent and its descendant are both terminated;
- literal metacharacter-bearing paths and arguments remain literal;
- ACL/token/Job setup failures stop before provider execution; and
- the source checkout and configured outside sentinel bytes remain unchanged.

Until those native tests run against the frozen candidate SHA, documentation
must say `Windows backend candidate: cross-compiled, native gate pending`.
