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

All runner, provider, and diagnostic processes are terminal. Both source
repositories and all three retained checkouts are clean. No candidate was
accepted, merged, pushed, deployed, or written to production. Retry Claude after
its provider limit resets. Keep Kimi write-disabled under Seatbelt until a
dedicated owner-authorized state root is implemented and authenticated without
credential copying.
