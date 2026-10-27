# Live Claude Code guarded-write evidence

Recorded: `2026-09-09T01:17:54Z`

- Runner/base commit: `cad8e4cec76e4fe2afbf30275ba31bddd0541fa3`.
- Provider: authenticated Claude Code `2.1.261`, first-party `claude.ai` session.
- Authorization basis: the owner explicitly directed Codex to use headless AI workers with write ability.
- Submitted data: `PUBLIC`; egress policy: `approved_external`.
- Submitted instruction: public synthetic runner fixture README, SHA-256 `4041ce5fc07a19b172852c9c9f637eab9c61d8d3e3456260375c625ced66e832`.
- Provider tools: Claude restricted/safe mode with only its `Write` tool exposed; no shell, Git, browser, MCP, or subagent tool was exposed.

The first task identity, `206e8ca9d8261bead307a317381f7558d6a8bc6e0252c1d185e1910d114a2633`, exited before provider work because the scrubbed environment lacked the identity names needed to recover the authorized keychain session. It changed no project path. The retained failure evidence is at `.worktrees/provider-runs/heleos-worker-aWBgky/`; `failure.json` has SHA-256 `2fcb56246c6455a33d956bf380879f25588937ef9125c82c4e54ffc97532afc0`.

The second task identity, `7e8d98e428ea1ddf4d0152a142ebb3651d805092f9227b50c1492138ffd21e5d`, explicitly inherited `HOME`, `USER`, `LOGNAME`, and `SHELL`. It exited zero in 7,869 ms. The independent inventory contained exactly `tests/fixtures/runner/live/claude-headless.txt`, SHA-256 `d88b56eb7d68ea2ebc4b2b191c965470a5272268caa10ce471b5893692fbaf35`, non-executable. The retained run is at `.worktrees/provider-runs/heleos-worker-nJ1WdF/`; `run.json` has SHA-256 `ab885013ff374be93b56cce6564f39a137d4ee930fc90f928bc0eb5faca16d01`.

The controller executed the task's predeclared content-hash check in the retained checkout; it exited zero with empty output. The resulting completed handoff validates against the original task with canonical digest `f8182e7f7021fe7ba53d9c6ec9f2eb1768949b94d61c39e36c31066b80581145`. The source checkout remained clean at the runner/base commit. No merge, push, deployment, production write, or release decision occurred.
