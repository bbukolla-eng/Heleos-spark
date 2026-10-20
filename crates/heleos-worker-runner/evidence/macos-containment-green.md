# macOS containment implementation evidence

Worker: `macos_containment_code`, sole implementation writer.
Checkout: `/Users/bekim/Heleos-spark/.worktrees/agent-control-foundation`.
Branch: `build/agent-control-foundation`.
Base/unchanged HEAD: `94cf73dd3ee347f93b5e2d5b8aa9a59580a36155`.
Host: Darwin 25.6.0 arm64. Toolchain: Rust 1.96.1.

## Causal verification

The behavioral CLI RED run preceded implementation: one test passed and two
failed because the containment flag and default evidence field were missing.
The API RED run then failed compilation because the public mode and config field
did not exist. See `macos-containment-red.txt` for commands and terminal results.

Every command below ran from the checkout above, without network access:

| Command | Terminal result |
| --- | --- |
| `cargo +1.96.1 test --locked --offline -p heleos-worker-runner --test cli --test runner containment` | Exit 0; CLI 1 passed, 2 filtered; runner 2 passed, 24 filtered |
| `cargo +1.96.1 test --locked --offline -p heleos-worker-runner` | Exit 0; CLI 3/3, runner 26/26; zero failed/ignored; library, binary and doc tests each 0 |
| `cargo +1.96.1 fmt -p heleos-worker-runner` | Exit 0 |
| `cargo +1.96.1 fmt -p heleos-worker-runner -- --check` | Exit 0 |
| `cargo +1.96.1 clippy --locked --offline -p heleos-worker-runner --all-targets -- -D warnings` | Exit 0; no warnings |
| `cargo +1.96.1 check --locked --offline -p heleos-worker-runner --all-targets --target x86_64-pc-windows-msvc` | Exit 0; existing installed target, compilation only |
| `git diff --check` | Exit 0 |

The native test executes the real fixed `/usr/bin/sandbox-exec` backend. It
proves writes within all three roots; denial of direct source-host, run-sibling,
descendant and symlink escape writes; literal executable/root paths and provider
argv; unchanged `blocked` handoff state; and matching retained/stdout containment
evidence. The separate inventory test proves an out-of-scope checkout write still
fails as `out_of_scope` and retains that reason in `failure.json`. All 26 previous
runner/CLI tests remain green alongside the three new macOS/API tests.

## Changed paths and identities

All paths below are relative to `crates/heleos-worker-runner/`:

| Path | SHA-256 |
| --- | --- |
| `src/containment.rs` (new) | `27a27f8a6bc0e20f6208f45f94529172ce1d480ae4e8e00060a1f22d324d5e0c` |
| `src/lib.rs` | `b268c139a159d42f2ab386fee71ba231d7bbd6045f43690dd865ecb8474d39f3` |
| `src/main.rs` | `838fea59a7ca17851ee5c4ae79a7d3c1c020d09abf6ee186a835cb878100dfef` |
| `tests/cli.rs` | `a611b212f7592fa1362fc5e68f3eec2a875bebc2ea6372a4b2202f92ab6b93dd` |
| `tests/runner.rs` | `505ade85ff021ef620e7da9e0535cfbfdf7eb16491795d215b9230730a32edcc` |
| `README.md` | `369149a1bcdfbac0390b6dc5f3a2c29cf7b6164be10894664e8f5a5fae926f03` |
| `evidence/macos-containment-red.txt` (new) | `9ea28bfb589cf11903e9e0199c0953aea6f9774ef8e6f480fa8d77b41ea93863` |
| `evidence/macos-containment-green.md` (new) | This report; self-hash omitted |

## Limits and handoff

No implementation blocker remains. This is completed candidate implementation
with passing scoped checks, not independent acceptance, integration, Foundation
acceptance, or Windows containment. No review, commit, merge, push, remote call,
production/store mutation, or task acceptance-command execution occurred.

The default remains `none`. Seatbelt restricts new path-based filesystem writes
for the provider process tree, including descendants, to the three canonical
roots. Reads, network, credentials, inherited external authority, internal
provider actions and cost are unrestricted. Real authenticated Claude/Kimi
compatibility is a separate smoke gate; no authentication data was copied.
macOS App Sandbox and regulatory containment are not claimed.

`containment_unavailable` covers unsupported platforms and pre-launch backend
validation failures. Once sandbox-exec starts, a policy initialization failure
shares the provider exit-status channel and is retained as `provider_exit`; no
stderr heuristics or alternate fallback are used. Root system paths/ownership
are trusted, and this slice does not defend against privileged modification of
the system backend. Non-macOS failure behavior is encoded behind platform gates;
Windows cross-compilation passed, but no non-macOS runtime test was performed.

All launched check processes are terminal. The controller owns the next action:
normal provenance and integration verification, roadmap/recovery updates, and
the owner-authorized Git commit. No files outside the assigned crate were edited
by this worker; the pre-existing untracked plan belongs to the controller.
