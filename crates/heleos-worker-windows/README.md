# Windows worker containment

Windows backend candidate: cross-compiled, native gate pending.

This crate is the safe Windows platform boundary for the guarded worker runner.
The crate root denies unsafe code. Only the Windows-only `sys` module may use
Win32 FFI; no raw handle or pointer escapes its API.

Create three empty canonical local directories, then call
`WritableRoots::prepare(checkout, home, tmp)` **before** materializing any child
content. On Windows this preserves existing DACLs, adds the inheritable Write
Restricted Code SID grant, verifies the effective ACE, and retains handles that
prevent renaming the roots and their ancestors. Only local NTFS volumes are
supported. Run-root evidence must remain outside these three roots.

Pass those roots to `CommandSpec::new(executable, arguments, directory,
environment, deadline, output_limit, roots)`, then call `execute(spec, stdin)`.
Arguments are literal strings, cwd must be in checkout, and the environment is
explicit. The process starts suspended under a write-restricted token, with only
three pipe handles inherited, and enters a verified kill-on-close Job Object
before resume. Success, timeout, setup errors and unwind all terminate the tree
and reap the primary before pipe-reader joins. Each output stream retains at
most its independent configured byte cap while draining to EOF. The maximum
retention and stdin length are 64 MiB each; command/environment buffers are
limited to 32,767 UTF-16 units including terminators.

Non-Windows execution returns `containment_unavailable`. Portable validation and
encoding remain available for tests. Windows environment ordering uses the OS's
invariant ordinal comparison; non-Windows tests use conservative Unicode
uppercase comparison and are not authority for non-ASCII Windows equivalence.

Native tests in `tests/native.rs` exercise inherited ACL writes, protected
outside/evidence writes, timeout and normal-exit descendant termination, and
symlink escape/root rejection. The sys unit tests exercise a real unrelated
inheritable file-handle exclusion, junction escape denial, and ACL-removal setup failure. Symlink tests
require Windows Developer Mode or `SeCreateSymbolicLinkPrivilege`; absence fails
the gate. They are never silently skipped. Cross-compilation is not native
execution evidence.

This boundary does not claim read, network, environment-secret, or AppContainer
isolation. Controller access remains privileged. It introduces no shell adapter,
ambient environment inheritance, or uncontained fallback.
