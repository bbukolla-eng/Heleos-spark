# Guarded Worker Runner Implementation Plan

**Status:** Codex adapter admitted in `ba33106`; live Claude and Grok writes accepted under macOS Seatbelt; live Codex write accepted with runner containment `none`; post-Codex Foundation gate passed and native Windows/NTFS gate pending

**Branch:** `build/agent-control-foundation`

**Starting commit:** `967de37`

## Current finish line

Build a local, deterministic runner for write-capable AI coding workers. The runner consumes a validated `heleos.worker-task/v1` packet, proves the requested base commit exists, creates an isolated disposable checkout at exactly that commit, invokes one explicitly configured local provider process without shell interpolation, inventories all Git changes, enforces allowed and forbidden paths, and emits a task-bound handoff. It never applies, merges, or pushes the result.

Claude Code and Grok Build 0.2.111 are authenticated and have completed accepted PUBLIC-only writes under macOS Seatbelt. Codex CLI 0.153.4 completed one accepted PUBLIC-only `gpt-6-astra` write using runner containment `none` and Codex's own `workspace-write` mode; that success is not proof of outer host/process/read/network/authority containment. Kimi completed an earlier uncontained write, but remains write-disabled under Seatbelt because its combined credential/runtime data root attempts a denied real-home write. Cursor remains unavailable for dispatch and is not authenticated. NotebookLM and GrokBots/Athena remain research-only.

## Implementation tasks

1. Add `crates/heleos-worker-runner` with a typed library and thin CLI.
2. Write failing tests first against temporary local Git repositories and fake provider executables.
3. Clone from the source checkout into a caller-selected, project-controlled workspace root and detach at the exact task base.
4. Invoke the provider using an executable plus an explicit argument vector and a generated prompt on standard input. Never pass provider input through a shell.
5. Apply the task duration limit, terminate timed-out processes, and bound retained standard output and error output.
6. Inventory tracked modifications, deletions, renames, and untracked files with machine-readable Git output.
7. Reject any changed path outside `allowed_paths` or within `forbidden_paths`; retain successful and failed workspaces for inspection and evidence. Any cleanup is a separate explicit action after ownership and containment are revalidated.
8. Emit a protocol-compatible handoff that is validated against the original task before output.
9. Add public synthetic fixtures and an operator guide.
10. Run locked/offline tests, strict Clippy, formatting, provenance, and exact changed-file checks before committing.

## Non-goals

- No acceptance-command execution in this slice.
- No provider authentication, credential extraction, or session copying.
- No network policy decision by the runner; provider access must already be authorized by the task and operator.
- No production database writes, release authority, merge, push, or authoritative-branch mutation.
- No direct write role for NotebookLM or GrokBots/Athena.

## Acceptance evidence

- Tests prove an allowed write succeeds and produces a valid handoff.
- Tests prove forbidden and out-of-scope writes fail closed.
- Tests prove nonzero provider exit and timeout are typed failures.
- Tests prove shell metacharacters in arguments cannot execute an injected command.
- Tests prove the source checkout and its branch/HEAD remain unchanged.
- Tests prove the isolated checkout is exactly the task base even when the source checkout is on a newer commit.
- Tests prove captured logs are bounded and no task input is echoed by fixed diagnostics.
- A real authenticated provider smoke task runs only after the fake-provider boundary is green.

## Next after this slice

Commit the accepted Codex fixture/evidence and synchronized status after the passing post-Codex Foundation gate. The macOS Seatbelt profile and Windows restricted-token/Job Object backend are implemented. Execute `scripts/verify-windows-worker-containment.ps1` from a clean frozen candidate on native Windows/NTFS; host tests and cross-compilation do not satisfy that pending gate. Kimi needs an owner-initialized dedicated worker data root or provider-supported split read-only-auth/writable-runtime capability before contained writes resume. Preserve the completed Claude, Grok, and Codex task identities and outcomes; do not repeat them.

Persist validated task/run/handoff identities through the governed operational store only after Foundation 0.1 acceptance and an explicit migration plan. Before then, additional provider work remains a retained candidate in this isolated engineering lane. Foundation 0.1 remains unaccepted, and this branch must not merge into its candidate before acceptance. No merge, push, or deployment has occurred for this branch.

## Initial implementation checkpoint

- Runner implementation commit: `cad8e4cec76e4fe2afbf30275ba31bddd0541fa3`.
- Controller gates passed: 24 runner integration tests, 2 runner CLI tests, 19 Kimi-adapter tests, workspace formatting, locked/offline workspace check, strict workspace Clippy, reproducible two-build PDF guest provenance, and the normal provenance scan.
- Live Claude task digest: `7e8d98e428ea1ddf4d0152a142ebb3651d805092f9227b50c1492138ffd21e5d`.
- Claude changed exactly `tests/fixtures/runner/live/claude-headless.txt`; its expected and observed SHA-256 is `d88b56eb7d68ea2ebc4b2b191c965470a5272268caa10ce471b5893692fbaf35`.
- The controller acceptance check exited zero. Completed handoff digest: `f8182e7f7021fe7ba53d9c6ec9f2eb1768949b94d61c39e36c31066b80581145`.
- Live Kimi task digest: `e9d03473e354fd0266fd9e4c644fd0124fdd62ff8389ebd2103c285ff6fbb6a9`.
- Kimi changed exactly `proof/kimi-headless.txt` in a dedicated PUBLIC-only source repository; its expected and observed SHA-256 is `6109053c330d9df1cb2711a6d032f3b491273558035a4fb1fa385b596d9f8640`.
- The controller acceptance check exited zero. Completed Kimi handoff digest: `2a44c5deb931489974f2fd41de7fdf20bcf8a9685d8b2e9de3ff4e2b9eb78b76`.
- No merge, push, deployment, production write, or Foundation 0.1 candidate change occurred.

## Current implementation checkpoint

- `56758dd` admits the guarded Grok adapter and runner path. The assembled runner passes 39 host tests; the Grok adapter passes all 23 black-box tests under Python 3.14.6 and 3.9.6.
- Authenticated Grok Build `0.2.111` ran two separately identified PUBLIC-only tasks under macOS Seatbelt. The first candidate failed the declared hash check and was rejected without integration. The second produced exactly `tests/fixtures/runner/live/grok-seatbelt.txt`, 84 bytes with SHA-256 `fe8e15e6d8c4ded8a4e6bd29238a0a23130d1f3df200565b8a6b198738d983f4`, and passed controller acceptance. Both processes are terminal; exact accepted bytes and [live evidence](../../../crates/heleos-worker-runner/evidence/live-grok-run.md) are committed as `fb18a39`.
- `9a62481` isolates three shared-temporary-directory inventory assertions in child-process temporary roots, retaining their complete inventory checks. After the repair, `./scripts/verify-foundation` exited `0`, including provenance `pass` over 172 files, the full locked/offline workspace tests, two-root reproducible PDF-guest build, and clean/offline acceptance rerun.
- `ba3310620a4aa9ff2bb7ce6d93c7931708de4f8a` admits the bounded Codex stdin adapter and runner path. All 14 adapter tests pass under Python 3.14.6 and 3.9.6; the assembled runner passes 42 tests. The post-Codex `./scripts/verify-foundation` gate exited `0`, including provenance `pass` over 176 files, the complete locked/offline workspace suite, the reproducible two-root PDF guest, and the clean/offline acceptance rerun.
- The official Codex CLI was upgraded from `0.147.0` to exact `0.153.4` because the old version rejected `gpt-6-astra`. The first separately identified PUBLIC-only task failed closed under macOS Seatbelt before the model when runtime state needed a denied write. The second selected runner containment `none`, authenticated with the old CLI, and failed with the model/version HTTP 400. The third new task identity used `0.153.4` and succeeded once: exactly `tests/fixtures/runner/live/codex-astra-runner.txt`, 81 bytes, SHA-256 `195846e4eab5d1882207d9e18512172c37254e6cfea9a2d3d97d63a2114340a0`. Both controller acceptance checks passed, and its completed handoff validates. The accepted candidate and [live Codex evidence](../../../crates/heleos-worker-runner/evidence/live-codex-run.md) are included in this checkpoint. No task was repeated.
- Successful Codex execution used runner containment `none` with Codex's own `workspace-write` mode. It proves the bounded task's accepted write, not outer host/process/read/network/authority containment. Native Windows/NTFS execution remains pending. Kimi remains write-disabled under Seatbelt; Cursor remains unavailable and unauthenticated. These accepted worker results grant no production authority, Foundation acceptance, merge, push, or deployment authority.
