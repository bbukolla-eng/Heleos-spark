# Live Kimi Code guarded-write evidence

Recorded: `2026-09-09T01:30:02Z`

- Runner implementation commit: `cad8e4cec76e4fe2afbf30275ba31bddd0541fa3`.
- Final stdin-adapter commit used by the successful run: `81be0ac`.
- Provider: Kimi Code `0.34.0`, managed OAuth provider `kimi-code/k3-256k`.
- Authorization basis: the owner explicitly directed Codex to use headless AI workers with write ability.
- Submitted data: `PUBLIC`; egress policy: `approved_external`.
- Source: a dedicated PUBLIC-only Git fixture at `.worktrees/kimi-public-smoke-source`, base `a862c761c89fe8719508b70b8eebbfc7bc003535`. The proprietary Heleos checkout was not supplied to Kimi.
- Submitted instruction SHA-256: `542835cff8ff010a61a9e4b3211d7d3350968e995970846e0b60052bb957161b`.

Kimi 0.34 rejected the first two adapter attempts before provider work because prompt mode cannot be combined with explicit `--auto` or `--yolo`. Both runs changed no source path and remain at `.worktrees/provider-runs/heleos-worker-2ADVP1/` and `.worktrees/provider-runs/heleos-worker-lyDP7W/`; their `failure.json` SHA-256 values are `db14c9ebc1dca0f9a02cea3e616f5a62f72ae783aebcdf52379315639ef05ca8` and `90fce76e126e9e49a2f9ccd41c8813e18e0713980c64911c74ebdf15d7e26ba6`. The corrected adapter follows the official prompt-mode rule that noninteractive execution supplies its own automatic permission policy: <https://moonshotai.github.io/kimi-code/en/reference/kimi-command.html> (retrieved 2026-09-09).

The successful task identity is `e9d03473e354fd0266fd9e4c644fd0124fdd62ff8389ebd2103c285ff6fbb6a9`. It exited zero in 22,827 ms. The independent inventory contained exactly `proof/kimi-headless.txt`, SHA-256 `6109053c330d9df1cb2711a6d032f3b491273558035a4fb1fa385b596d9f8640`, non-executable. Its retained run is `.worktrees/provider-runs/heleos-worker-KnZAGm/`; `run.json` has SHA-256 `19c0873a5dc2e5fe39a16df4aa66c1b276e5fb0b8ad73d11a22341ee583e25a7`. `live-kimi-output.txt` is an exact-byte tracked copy of that candidate.

The controller executed the predeclared content-hash check in the retained checkout; it exited zero with empty output. The completed handoff validates against the original task with canonical digest `2a44c5deb931489974f2fd41de7fdf20bcf8a9685d8b2e9de3ff4e2b9eb78b76`. The PUBLIC source repository remained clean at its base. No merge, push, deployment, production write, or release decision occurred.
