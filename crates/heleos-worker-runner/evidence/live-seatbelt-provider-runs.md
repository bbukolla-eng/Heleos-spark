# Live authenticated provider launches under macOS Seatbelt

Recorded: `2026-09-09T02:15:00Z`

- Containment implementation: `28daf4c0f213540a05f1928f4936ee641250686e`.
- Kimi drained-stdin fix: `df0233b6812d1f46de3a714ec1a647f7a245a81f`.
- Authorization basis: the owner explicitly directed Codex to use headless AI
  workers with write capability.
- Submitted data: authored `PUBLIC` synthetic text only; egress policy:
  `approved_external`.
- Mode requested for every launch: `macos_seatbelt`; no uncontained fallback.
- No credential, cookie, token, or authenticated state was read into evidence,
  copied into a run directory, or sent in a task prompt.

## Claude Code

Task `claude-live-seatbelt-write-001` validated with canonical digest
`ca7760d9b36fb1465dc7fd480f392cbd27a16a37455afd7999ffc1df43a387c9`.
The task file SHA-256 is
`bae2221ea53fe0a8623f9ef39f082391a7247f5e12b48741b80c88ff276441f2`.
It used source commit `28daf4c0f213540a05f1928f4936ee641250686e`
and exposed only Claude's `Write` tool.

The contained Claude Code 2.1.261 process initialized its existing authorized
session and reached the provider, but exited before consuming task tokens or
running a tool because the provider returned HTTP 429 for the account session
limit. Its bounded JSON reports zero input/output tokens, zero iterations, zero
permission denials, and the provider message that the limit resets at 11 p.m.
America/New_York. The runner retained `provider_exit` at
`.worktrees/provider-runs/heleos-worker-q1xfVc/`.

- `failure.json` SHA-256:
  `be5cba0cdf592d5940ce4f083cbe2b8ad0777f84050dfd836e3cf5449e6fb9c9`.
- `stdout.bin` SHA-256:
  `beebc654c7d03197affa1f74d5eeeaa35dcc3f6e061217d26f90926d2ed1fb2f`.
- Provider exit: `1`; retained stderr: zero bytes.
- Retained checkout: clean; requested output absent.

This proves a live authenticated Claude process can initialize and reach its
service under the policy. It is not a successful write proof; retry only after
the provider limit resets and keep the same containment boundary.

## Claude Code contained write success

After the reported limit reset, task `claude-live-seatbelt-write-002` ran from
source commit `82161f5501b0fd35c2454d5c6bb4eeeeb71d8dec` with canonical task digest
`e686fff9cf9f67413b2ed4a79735c7c3b69e4089548d6ec64f6805351a60f1a8`.
The task file SHA-256 is
`d35673db953c7d1a8e63cce7bf20ee31d9559c14459f793cd8c27d89ace328e2`.
It admitted exactly one `PUBLIC` synthetic output path and exposed only Claude's
`Write` tool. Claude Code 2.1.261 exited zero after 6,232 milliseconds under
`macos_seatbelt`.

The runner retained the complete candidate and evidence at
`.worktrees/provider-runs/heleos-worker-4zqjZW/`. Its inventory contains exactly
`tests/fixtures/runner/live/claude-seatbelt.txt`, SHA-256
`c5e8b3c1ac3acc430840eef4831b2c5352737e9d516607a1bb3de2c2bc9df59f`.
The runner recorded one provider invocation, 2,306 retained stdout bytes, zero
stderr bytes, no truncation, host-path writes restricted to checkout/home/tmp,
and no claim of read, network, credential, or inherited-authority restriction.

- `run.json` SHA-256:
  `cf6e7341bf6215a18783c0195e1ba8148ff49aaae15e43dadb71c7775e1f2167`.
- Pending runner `handoff.json` SHA-256:
  `c2b8b592da9f831e108e6297487aa51fc81da640eeda8ec121d7e6b3abde1ed9`.
- `stdout.bin` SHA-256:
  `bfb19d0ac2b06c089cd750cc79682c1e147490d393fc1c069183c63f57556c48`.
- Empty `stderr.bin` SHA-256:
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

The controller compared the retained candidate to the exact base, confirmed
that this one regular file was the complete change inventory, copied the exact
bytes into the source through the controller path, and reran its declared
SHA-256 acceptance command with exit zero. The resulting completed handoff has
digest `2bdde5a65e2dd0de1de0c4aec950669547f905674978cc81561606430c9aaed5`
and file SHA-256
`873daa3375bfd2601e8ef716d3a5e7c5541879f5e0843aef669d8ba602bd28af`.
This proves the guarded route can produce and integrate a bounded authenticated
Claude write under the tested macOS policy. It does not grant Claude authority
to accept, merge, push, deploy, or write production truth.

## Kimi Code adapter defect and repair

Task `kimi-live-seatbelt-write-001` validated with canonical digest
`bb8426b2126b8725bd42e4dc1e6884773be1ece7399689274e215eaaa913488f`.
Its task file SHA-256 is
`798e6cbb06366257de53b0c8db1cbaae8a86d00944caf4edbafc6686899dd41d`.
The first launch stopped in the repository-owned adapter with fixed exit `70`.
The controller reproduced the exact cause: Python 3.9 implements
`subprocess.DEVNULL` by opening `/dev/null` read-write, which the strict policy
denies. The retained failure is
`.worktrees/provider-runs/heleos-worker-4ezBHf/`; its `failure.json` SHA-256 is
`b067e703458d134bb126c406a459c5a8ad005c0fe1ca7dfc03e589081ab9d63f`.

Commit `df0233b` fixes the source of the incompatibility without widening the
profile: the adapter passes the prompt as one argument and gives Kimi the input
stream it has already drained to EOF. A causal regression denies every
`/dev/null` open and proves the fixture child still launches, receives exact
argv, and reads EOF twice. All 20 adapter tests pass under both Python 3.14.6
and Apple Python 3.9.6; normal provenance passes.

## Kimi Code provider-local storage boundary

The post-fix task `kimi-live-seatbelt-write-002` validated with canonical digest
`4d98eab43e1a3814cb2bc27299685d7bbfeb992c6e679d64530ce3f40e1d60ee`.
Its task file SHA-256 is
`9df22523d9dfa6b692fbecf1026c7b16914789925329897431b3f6fa1ca58c9c`.
Kimi Code 0.34.0 launched, emitted its structured version event, and then exited
because it attempted a storage write under the real `~/.kimi-code` data root.
Seatbelt denied that write as designed. The runner retained `provider_exit` at
`.worktrees/provider-runs/heleos-worker-38IOPS/`.

- `failure.json` SHA-256:
  `2b1072c59ded481b004d5c93ece245abbc41823459e1614c35cc991bf517475c`.
- `stdout.bin` SHA-256:
  `9cc92edee18a5d516b89a07284fa6a4c5c5564cccf6a920436dae245b7fce9b9`.
- `stderr.bin` SHA-256:
  `ec4e63e774931e1696c777e3e07fa21448add0ac42fad8564a96a55802f598f9`.
- Provider exit: `1`; retained checkout and PUBLIC source: clean; requested
  output absent.

Kimi's official configuration documents `KIMI_CODE_HOME` as one data root for
configuration, OAuth credentials, sessions, logs, and other runtime state:
<https://moonshotai.github.io/kimi-code/en/configuration/env-vars.html>. The
current CLI exposes no prompt-mode switch that separates read-only credentials
from writable ephemeral state. A safe next implementation must use an
owner-initialized, dedicated worker identity/data root or a provider-supported
split-state capability. Do not copy credentials into a run, retain credential
symlinks as evidence, or allow writes to the owner's general Kimi home merely to
force a green smoke result.

## Terminal state and next action

All runner, provider, and diagnostic processes are terminal. The successful
Claude retained checkout contains exactly its accepted one-file candidate; the
failed retained checkouts and source checkout remain unchanged by their provider
runs. The controller integrated the Claude file locally, but nothing was merged,
pushed, deployed, or written to production. Do not repeat either Claude task.
Keep Kimi write-disabled under Seatbelt until a dedicated owner-authorized state
root is implemented and authenticated without credential copying.
