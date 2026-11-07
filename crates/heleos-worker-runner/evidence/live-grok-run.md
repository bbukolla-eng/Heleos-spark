# Live Grok Build guarded write

Recorded: `2026-09-09`

This evidence belongs to the local candidate branch only. It does not merge,
push, deploy, approve Foundation 0.1, or give Grok production authority.

## Frozen execution boundary

- Runner and adapter commit:
  `56758ddd995c9412bea4841941fa413a2a4948fb` (`feat: admit guarded Grok
  worker`).
- Exact source tree:
  `5b6378bfe666fc524a2ff5f9ee70018c6c9cae0f`.
- Runner binary SHA-256:
  `b39473487a1b3ea6b6105501c1e64428e9f391f48279bcefa7fc4ddd32fee7f8`.
- Adapter SHA-256:
  `688f45c709ca324bda81179e55bbf46e189457bb50b8d6b528b7c415e18e04e1`.
- Grok Build CLI: `0.2.111 (94172f2aa4e5) [stable]`; binary SHA-256:
  `e1fafdfffe14f339460befaf194360e8f90bfd02efe8a4f24cfa1c7aea657ffe`.
- Provider model argument: `grok-4.6`; the result identified model usage as
  `grok-4.6-build`.
- Adapter interpreter for the accepted run: Homebrew Python `3.14.6` at its
  exact framework executable, not a shell.

The adapter supplied the prompt through a randomly named mode-0600 file inside
the runner's ephemeral `TMPDIR`. It fixed the provider arguments to
`--permission-mode bypassPermissions`, built-in tool allowlist `Write`,
streaming JSON, model `grok-4.6`, eight maximum turns, and disabled web search,
subagents, memory, plan mode, and automatic updates. The prompt file was absent
after the provider exited.

The runner inherited only the explicitly named `GROK_AUTH_PATH` authority. Its
value was not placed in the prompt or run report, and credential bytes were not
copied into the run. Grok runtime/configuration state was written beneath the
fresh runner-owned home. The macOS Seatbelt profile restricted path writes to
the isolated checkout, fresh home, and fresh temp roots. Reads, network, and
inherited external authority were not restricted; macOS process-tree
containment and provider-internal action counts were not attested.

## Rejected first candidate

Task `grok-live-seatbelt-write-001` ran once from the frozen base and returned
zero after `9,279` milliseconds. The runner inventoried exactly the allowed
path, but the provider inserted the six bytes `Then: ` at the beginning of line
two because the natural-language content separator was ambiguous.

- Canonical task digest:
  `c8485e195900b508ec6b901deebed6b84065ac1ad887c65daf3f782f07b68790`.
- Retained run:
  `/Users/bekim/Heleos-spark/.worktrees/provider-runs/heleos-worker-0nF5Sj/`.
- Candidate SHA-256:
  `431c6167d163561a67ca6440a8338aae4c5917e75c13f1a1483f3a815f64834a`.
- Expected SHA-256:
  `fe8e15e6d8c4ded8a4e6bd29238a0a23130d1f3df200565b8a6b198738d983f4`.
- `run.json` SHA-256:
  `f066d23d94ada5d1fc469569e12f0f05d777db7001f664af0f51a4e296be648f`.
- Pending `handoff.json` SHA-256:
  `e3d1ee67f12f3b50fa90b5b6fa2bafb036fb163e9fe931e6950af5899f7d1a02`.

The controller executed the declared hash check, received exit `1`, and did not
integrate the candidate. The same task was not repeated. The next task used a
new identity and an exact byte-count/hash contract.

## Accepted second candidate

Task `grok-live-seatbelt-write-002` ran once from the same frozen base and
returned zero after `14,837` milliseconds. The retained checkout contained
exactly one untracked regular file at
`tests/fixtures/runner/live/grok-seatbelt.txt`, exactly `84` bytes with SHA-256
`fe8e15e6d8c4ded8a4e6bd29238a0a23130d1f3df200565b8a6b198738d983f4`.

- Task file SHA-256:
  `dba7e7ba44aa9ceb3642e76d8204a98992a541825f85bdadcf44cc08fece5e19`.
- Canonical task digest:
  `48f5a8cdf05fd6329794a84a37a4354795cee51aec9612a9ee5eff5046b5a9b6`.
- Retained run:
  `/Users/bekim/Heleos-spark/.worktrees/provider-runs/heleos-worker-DnJ2uL/`.
- `run.json` SHA-256:
  `8663ee3ba85c828db579dca24d08c9b84670665e39ee1c52339a9472187069f1`.
- Pending `handoff.json` SHA-256:
  `4c0c70c009d15785068d4f88e39c84493d21983cd32d0b73f2126f9bc7080ee5`.
- `stdout.bin` SHA-256:
  `aa3836671c29fd94d69903a52efe8f7405c96a1d3c28dd8e40d1baea1fe21ea7`
  (`4,594` retained bytes, not truncated).
- Empty `stderr.bin` SHA-256:
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.

The provider reported two model calls, `5,578` uncached input tokens, `4,992`
cached input tokens, `772` output tokens, and USD `0.00310828`. Those are
provider-reported usage fields, not independent billing evidence.

The controller independently ran the predeclared hash check and the exact
84-byte check, confirmed the retained checkout's complete Git inventory,
integrated the exact bytes through the controller path, and compared source and
candidate byte-for-byte. The completed handoff validates against the immutable
task with canonical digest
`4945513735647d58979426e352fd9e90a19f37f4e4e3d5c33ec0cebd1e691faf`;
its recorded JSON SHA-256 is
`bea7a42a6c6307fc997c97160ab5c4a2f044a45663c041259b58021b00a01d2b`.

This proves one bounded, authenticated Grok Build write can traverse the
repository-owned adapter and guarded runner, remain inside the tested macOS
write boundary, fail closed on an inexact candidate, and integrate only after
deterministic controller acceptance.

## Repository verification after integration

The first full `./scripts/verify-foundation` execution exposed two pre-existing
parallel-test isolation defects: three tests compared the complete shared macOS
temporary directory before and after an operation, so unrelated concurrent
temporary-directory cleanup could change the observed inventory. Production
error classification and cleanup behaved as designed. The tests now retain
their exact full-inventory assertions inside fresh child-process temporary
roots; the fix is committed as `9a62481` (`test: isolate temporary inventory
assertions`).

After that repair, the controller ran `./scripts/verify-foundation` on the
combined source and evidence tree. It exited `0`: provenance reported `pass`
over `172` files, the full locked/offline workspace tests passed, the guest PDF
build reproduced across two distinct roots, and the clean/offline acceptance
rerun passed. The Grok adapter black-box suite also passed all `23` tests under
both Homebrew Python `3.14.6` and Apple Python `3.9.6`. `git diff --check`
reported no whitespace errors.
