# Guarded Worker Runner Implementation Plan

**Status:** Runner implemented; Claude live write completed; Kimi live write next

**Branch:** `build/agent-control-foundation`

**Starting commit:** `967de37`

## Current finish line

Build a local, deterministic runner for write-capable AI coding workers. The runner consumes a validated `heleos.worker-task/v1` packet, proves the requested base commit exists, creates an isolated disposable checkout at exactly that commit, invokes one explicitly configured local provider process without shell interpolation, inventories all Git changes, enforces allowed and forbidden paths, and emits a task-bound handoff. It never applies, merges, or pushes the result.

Initial write-capable providers are Claude Code and Kimi. Grok and Cursor remain disabled until authenticated. NotebookLM and GrokBots/Athena remain research-only.

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

Run one small Claude Code implementation task against a synthetic, allowlisted file in a disposable exact-base checkout. Validate and preserve its handoff and candidate commit as evidence. Then repeat with Kimi through the same runner without expanding its authority.

## Implementation checkpoint

- Runner implementation commit: `cad8e4cec76e4fe2afbf30275ba31bddd0541fa3`.
- Controller gates passed: 24 runner integration tests, 2 runner CLI tests, 19 Kimi-adapter tests, workspace formatting, locked/offline workspace check, strict workspace Clippy, reproducible two-build PDF guest provenance, and the normal provenance scan.
- Live Claude task digest: `7e8d98e428ea1ddf4d0152a142ebb3651d805092f9227b50c1492138ffd21e5d`.
- Claude changed exactly `tests/fixtures/runner/live/claude-headless.txt`; its expected and observed SHA-256 is `d88b56eb7d68ea2ebc4b2b191c965470a5272268caa10ce471b5893692fbaf35`.
- The controller acceptance check exited zero. Completed handoff digest: `f8182e7f7021fe7ba53d9c6ec9f2eb1768949b94d61c39e36c31066b80581145`.
- No merge, push, deployment, production write, or Foundation 0.1 candidate change occurred.
- Next executable action: perform one equivalent PUBLIC-only Kimi live write through the committed runner and stdin adapter, preserving a separate task and run identity.
