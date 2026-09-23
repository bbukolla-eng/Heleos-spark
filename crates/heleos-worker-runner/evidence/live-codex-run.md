# Live Codex Astra guarded-runner write

Recorded: `2026-09-09`

This evidence belongs to the isolated engineering branch. It does not merge,
push, deploy, approve Foundation 0.1, or grant Codex production authority.

## Frozen implementation boundary

- Runner/adapter commit: `ba3310620a4aa9ff2bb7ce6d93c7931708de4f8a`
  (`feat: admit guarded Codex Astra worker`).
- Runner binary SHA-256:
  `2bef5c11303ec67c3566705c620a0749cdd4ade8f4fc86e425adcfa3b012383e`.
- Adapter implementation SHA-256 at that commit:
  `e74d0d4ac5ee27f8a06ec4115224de7174d8129de703fac482549df527335337`.
- Initial Codex CLI: `0.147.0`; native binary SHA-256:
  `19c4f144c5226a9f17c58e6f0fa854843b0f77a6eb420f40e2745a12f10f5d37`.
- Upgraded Codex CLI: `0.153.4`; native binary SHA-256:
  `b973d440acac501fd2594a43e7ca9ce41e0a65b9dfb28d0d7a7837c99e1261e3`.
- Requested model: `gpt-6-astra`.

The repository-owned Python adapter supplied the generated prompt through
standard input and fixed `codex exec` to ephemeral mode, ignored user config
and rules, strict config parsing, model `gpt-6-astra`, Codex
`workspace-write`, JSON output, and no color. It exposed no caller-selected
provider arguments, output paths, profiles, plugins, MCP configuration, extra
write roots, or permission-bypass flags. The runner inherited only the
explicitly named `CODEX_HOME` authority; credential bytes were never read,
copied, printed, or placed in the prompt by the controller.

## Failed contained startup

Task `codex-live-seatbelt-write-001` used `macos_seatbelt`. Codex exited `1`
before a thread or model turn and wrote no project file. The retained error was
`Operation not permitted` while Codex initialized its in-process app-server;
it also warned that it could not create PATH aliases. This shows Codex 0.147.0
requires writable provider runtime state before a turn. The runner's Seatbelt
profile intentionally admitted writes only beneath the isolated checkout,
fresh home, and fresh temp roots, so the task failed closed rather than adding
the real Codex state directory as another write root.

- Retained run:
  `/Users/bekim/Heleos-spark/.worktrees/provider-runs/heleos-worker-PswVB8/`.
- Task file SHA-256:
  `28e11b67e1d04b9392653936c675b88b2990ec8ae4f9828569a988101f56b5bc`.
- Canonical task digest:
  `a5ad02707dd5ced98e00d1cb94ed07afec22a3864207afca4c16bf6c474c713a`.
- Failure JSON SHA-256:
  `1d1b6237d9b4c2b6d5817c652d7dccd3bcfb8bc33d66b121da43c06f3c79d7c8`.
- Empty stdout SHA-256:
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- Stderr SHA-256:
  `2d8b902fb51c4fb0108c58a40b037f3ed52641f00cdae67901d7cbda9b4b8473`.

The same task identity was not repeated.

## Failed old-CLI model start

Task `codex-live-runner-write-002` used the runner's explicit `none` containment
mode and Codex's own `workspace-write` setting. It authenticated and created a
Codex thread, but CLI 0.147.0 received an HTTP 400 saying `gpt-6-astra`
requires a newer Codex version. It exited `1` without a tool call or project
write. The controller upgraded the official npm package to exact version
`0.153.4`; the registry reported integrity
`sha512-wbHDmit7S/YvBGVX1DQmk13xtWblZ2cApeJ/pB7xDZ10Cna+DZc5ij7f0F4OxdsXN4FW1oLT48OpogUI1+8Y2w==`.

- Retained run:
  `/Users/bekim/Heleos-spark/.worktrees/provider-runs/heleos-worker-UhUymN/`.
- Task file SHA-256:
  `de725fff577af985b6b065ab90dcdf903dd2910f2fcc8140f6f39949f2de1378`.
- Canonical task digest:
  `e8855ba08262c8f64ccf80c74863822179298d207c48dbbdf678f0c2829d568a`.
- Failure JSON SHA-256:
  `74893a367cc6845f6f4b70b06ed34e1049c78f0ef8c0d5260e74897a080945ce`.
- Stdout SHA-256:
  `a4b9ec43ba3f1fc6f87f52d397f4e9c43bca642a22a3b15f085525ac2dd2c252`.
- Stderr SHA-256:
  `c4007d12651b2154aa5ac3c40f6439953ca264191cc615d471a0b053523d00c1`.

The same task identity was not repeated.

## Accepted upgraded-CLI candidate

Task `codex-live-runner-write-003` ran once from the same exact Git base with
Codex 0.153.4 and returned zero after `11,475` milliseconds. The retained
checkout contained exactly one untracked regular file at
`tests/fixtures/runner/live/codex-astra-runner.txt`, exactly `81` bytes with
SHA-256
`195846e4eab5d1882207d9e18512172c37254e6cfea9a2d3d97d63a2114340a0`.

- Retained run:
  `/Users/bekim/Heleos-spark/.worktrees/provider-runs/heleos-worker-6nVl9W/`.
- Task file SHA-256:
  `0cc171bfecd8f1f0ca3f5cceb25f0a39093f758cd5ec6c198bb07c1c8d13228b`.
- Canonical task digest:
  `901d262e0442f9dcd22e53e22cb9045e2f8e9313f90b8173c4bfbfae6f560e27`.
- `run.json` SHA-256:
  `50def14d6d9638450a2c86c165cd5dd3a0c417cc7f425669562906e0049b9414`.
- Pending `handoff.json` SHA-256:
  `23f6270bdf94517f05db8fbbe786475331511340c97c4514ad42441d9ffa470e`.
- `stdout.bin` SHA-256:
  `4848d4e80e9d28de5eed16455fce66a86090c84a13281876368b84de5081026c`
  (`1,185` bytes, not truncated).
- Empty `stderr.bin` SHA-256:
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

The provider event stream reported one file change, `44,794` input tokens,
`33,664` cached input tokens, and `150` output tokens. These are
provider-reported usage fields, not independent action, cost, or billing
evidence. The runner records one provider invocation and does not attest
provider-internal action counts.

The controller executed both declared acceptance commands, independently
verified the SHA-256 and byte count, confirmed the complete Git inventory,
integrated the exact bytes through the controller path, and compared source and
candidate byte-for-byte. The completed handoff validates against the immutable
task with canonical digest
`2cae4d4c365e553c29d75703c88478f9f30aa403c8952b9af59abe7602537bff`;
its recorded JSON SHA-256 is
`c8fc9ee48d4cfeef80050a1a88abad19ed9558a68353b321dc3132e4c459eae4`.

This proves one authenticated headless Codex/Astra write can traverse the
repository-owned adapter and guarded runner and integrate only after
deterministic controller acceptance. It does **not** prove outer host-write or
process-tree containment: the successful task truthfully records containment
mode `none`, with false read, write, network, inherited-authority, and
process-tree restriction claims. A future contained Codex run requires a
separately designed provider runtime-state boundary; the failed task is not a
reason to widen the current three Seatbelt write roots.

## Repository verification

After integrating the accepted bytes and this evidence, the controller ran
`./scripts/verify-foundation` to completion with exit code `0`. The gate
reported provenance `pass` over 176 files and passed the complete locked/offline
workspace suite, two-root reproducible PDF-guest build, and clean/offline
acceptance rerun. This repository verification does not change the containment
limitations above or satisfy the still-pending native Windows/NTFS gate.
