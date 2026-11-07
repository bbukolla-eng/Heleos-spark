# Guarded runner implementation handoff

Recorded 2026-09-09 01:00 UTC on macOS ARM64.

- Task/worker: guarded runner implementation, `/root/guarded_runner_code`.
- Checkout: `/Users/bekim/Heleos-spark/.worktrees/agent-control-foundation`.
- Branch: `build/agent-control-foundation`.
- Base/unchanged HEAD: `967de3758ceec817ff0fe9b852bc5ac24d69a3b9`.
- Writer scope: only `crates/heleos-worker-runner/**`. Root workspace and lock edits belong to the coordinating agent.
- State: implementation finished; local checks passed; no independent acceptance, integration, commit, push, or live provider invocation by this worker.
- Process state: all test/build processes terminal; no worker process remains running from this task.

## Delivered behavior

Typed Rust library and CLI admit explicitly configured Claude Code and Kimi implementation commands, validate the immutable protocol packet, make an independent local no-hardlink clone, detach at the exact base, verify instruction bytes, and send a bounded prompt through stdin with literal argv. All runner Git operations disable hooks, external filters/diff, optional locking, global/system config, and non-file protocols as applicable. No acceptance commands run.

Unix nonblocking pipe pumping bounds retained output and handles elapsed-time limits and managed same-group descendants. Successful and failed proposals retain their owned workspace by default. Explicit failure cleanup revalidates containment and device/inode, refuses replacement directories, and never cleans the source checkout.

NUL Git inventory includes ignored/untracked, staged, unstaged, committed, deleted, and renamed paths. Independent SHA-256/mode snapshots detect writes hidden by index flags; policy validation rejects forbidden/out-of-allowlist and unsafe paths. Evidence creation uses directory-anchored no-follow/exclusive opens. Original/canonical task, prompt, digest, and ownership bytes are checked before handoff. Every successful proposal produces a validated `blocked` handoff with empty checks pending controller acceptance.

## Verification

`cargo test -p heleos-worker-runner --locked --offline` exited 0: 24 runner integration tests plus 2 CLI integration tests passed, no failures or ignored tests. Tests use real local Git and fake executable providers and verify source file, index, HEAD, and worktree-registration preservation.

`cargo clippy -p heleos-worker-runner --all-targets --locked --offline -- -D warnings` exited 0. `cargo fmt -p heleos-worker-runner -- --check` exited 0. Initial RED output and three causal edge-case RED records are retained beside `final-green.txt`.

## Changed paths

Added `Cargo.toml`, `README.md`, `src/{lib,main,process,filesystem}.rs`, `tests/{runner,cli}.rs`, `tests/common/mod.rs`, and `evidence/{initial-red,evidence-symlink-red,evidence-integrity-red,ownership-red,final-green}.txt` plus this report, all below this crate.

Final manifest/document/source/test SHA-256 identities:

```text
635d80ef9e38501c693c5a13577af627ffb084a23e1e4e1e6e3a77b2271c17cb  crates/heleos-worker-runner/Cargo.toml
d5acf5dcc3d79c43a5fbf820408c56a338139cf09e5b2a18d04eb97c7976498c  crates/heleos-worker-runner/README.md
72027177372c61f0c5d36e83f5bc96d2f9bb84c234c07ee41228c8708169200c  crates/heleos-worker-runner/src/filesystem.rs
a6d9baf8c82844614d95de029394b19eb3d6ff703abe1b08a5cb834f3e925cfc  crates/heleos-worker-runner/src/lib.rs
3abad985f1efdc1c7a2e7edda0ec7e6371de895b2e6dfeb3ea2cf690cac9a00e  crates/heleos-worker-runner/src/main.rs
03f1ba67258d690d46bca3a40a8e934ce07bbb673a1ea1f8a6ff7a881b08f773  crates/heleos-worker-runner/src/process.rs
b67bd056f5967ec980c6f781aabff9909fc7ac83ca7509f93a620ff52100dada  crates/heleos-worker-runner/tests/cli.rs
3eb8c0772089428e2b018ec66e6acc30c6fee6c7a5fd5b8de462fe16c2d50550  crates/heleos-worker-runner/tests/common/mod.rs
dae821d27c8ef75ffd679de373fc86e9f695b70ff90ce7d9b0aba45373cac61f  crates/heleos-worker-runner/tests/runner.rs
```

## Limits and next action

Native evidence is macOS ARM64 only. Non-Unix execution returns `UnsupportedPlatform`; Windows Job Objects are future work. External cancellation of the CLI is not guaranteed, and deliberate process-group escape needs external containment. This is not an OS filesystem/network sandbox: reverted writes, arbitrary host writes, internal provider tool counts, and cost limits are not attested. Snapshot reads fail closed beyond 256 MiB or 100,000 files/directories; symlinks, hardlinks, and special files are rejected. No authenticated Claude/Kimi smoke run was performed.

Next action: controller runs repository-level integration checks and then the authorized single-provider synthetic smoke task using the documented CLI, preserving its retained candidate and handoff. The runner's proposal result is not acceptance authority.
