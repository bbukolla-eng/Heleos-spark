# Cursor guarded-runner attempts and current blocker

Recorded: 2026-09-09. Branch: `build/agent-control-foundation`.

Cursor is installed, authenticated, and admitted for Implementation tasks.
Five separately identified PUBLIC-only attempts are terminal; none produced
an accepted write or Cursor fixture. The current blocker is the Cursor
API-model monthly usage limit, reported to reset September 14, 2026. Do not
retry before that reset or an explicit owner action changing the spend limit.
Do not reuse task identities 001-005. `composer-2.5` also rejected required
workspace-context exclusion, which remains enabled.

## Installation, authentication, and implementation

- Installed Cursor Agent: `2026.09.02-c22c1a3`, verified through its version/help
  interface; installation is under
  `/Users/bekim/.local/share/cursor-agent/versions/2026.09.02-c22c1a3/`.
- The controller completed the owner-authorized official login and verified
  authenticated status. Credentials were not extracted, copied, or placed in
  task prompts, logs, or the repository. Status probes established that real
  HOME preserves authenticated lookup while mutable data/config/cache roots
  can live beneath the runner's writable temp root.
- Installed wrapper SHA-256: `2ccc9a8e167797641448b5e5c936f006ba137a2555f117f38c5eb76a5238a233`.
- Installed index.js SHA-256: `9be0f8f812ee102d237e7b3a55f79cb90ecb15616bc23601598314958445de5c`.
- Installed 4347.index.js SHA-256: `555dbf83a7ac2c6190caa6301ccfc32b4b4921e26d72d2582cd16d89f6dc8d0a`.

| Commit | Implemented change |
| --- | --- |
| `042c9c048b4304f42e4ecbdb1b8998039c2eb84d` | Admitted Cursor with bounded stdin adapter and exact argv. |
| `7d3166572dd2a33faa82fca8812a9f3427a3b7ac` | Direct Node/entrypoint launch, project/context controls, contained runtime/cache roots and length guard. |
| `f14d9b69b761046bb4b8c5a43771412ae570e929` | Isolated mutable Cursor configuration in the runtime root. |
| `965928055baa41cb2280abb27614cf7f8ef22055` | Switched from usage-limited `gpt-5.6-sol-high` to `composer-2.5`. |
| `02e5d2858c8d08d9d3e68741ee18037c515fcc77` | Switched to `gpt-5.6-terra-high`, preserving mandatory workspace exclusion. |

The current adapter invokes an explicit absolute Node binary and regular
index.js directly, without the provider shell wrapper. It fixes project-config
disabling, workspace-context exclusion, print mode, force, enabled provider
sandbox, stream JSON, disabled auto-update, and model `gpt-5.6-terra-high`.
Prompt bytes remain on stdin with the 65,536-byte UTF-8/NUL checks. The adapter
offers no caller-selected provider flags, additional roots, endpoints,
credentials, plugins, MCP approvals, output paths, or model overrides.

The controller uses Homebrew Python, explicitly inherited HOME for authenticated
lookup, and `macos_seatbelt`. `CURSOR_DATA_DIR` equals the validated temp root;
`CURSOR_CONFIG_DIR` and `NODE_COMPILE_CACHE` are private direct children.
The ASCII root limit of 75 prevents Cursor's long-path `/tmp/.cursor` fallback.
Symlink and unsafe runtime/config/cache targets fail before launch. No existing
config or credentials are copied into those fresh directories.

## Attempt outcomes and immutable identities

All task packets are under
`.superpowers/sdd/2026-09-09-cursor-worker-adapter/`; each retained run holds
original/canonical task bytes, prompt, bounded captures, and failure JSON.
Every retained checkout remained clean at its task's exact base. Hashes below
identify the evidence, not accepted output. No accepted Cursor handoff or
`tests/fixtures/runner/live/cursor-seatbelt.txt` was integrated.

### cursor-live-seatbelt-write-001

Base: `042c9c048b4304f42e4ecbdb1b8998039c2eb84d`.
Retained run: `/Users/bekim/Heleos-spark/.worktrees/provider-runs/heleos-worker-dpuF8o`.

Failed before model/write: Apple Python attempted a denied xcrun-cache write,
the Cursor shell wrapper attempted denied `/dev/null` writes, and the fresh
HOME could not locate the authenticated session. The direct Node/runtime repair
followed; no extra Seatbelt write root was admitted.

- Raw task: `11579ad743680753fb527f8862ecf1a1ccdb460f28dc8615887276121592bb98`
- Canonical task: `bab542f30f7de888d0e462443e5cd25de79f6cb7ffb8e2cc840b3875f0102e83`
- Failure JSON: `d082ac611e70cf3e4ab92958b94159d4ea78c0136a9d07d428c0a64605ab8b0f`
- Stdout (empty): `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- Stderr: `b2075b2d18f205a164acd6bea9fe953c7db7a8915ddd2fd7f6135c2eec9245a6`

### cursor-live-seatbelt-write-002

Base: `7d3166572dd2a33faa82fca8812a9f3427a3b7ac`.
Retained run: `/Users/bekim/Heleos-spark/.worktrees/provider-runs/heleos-worker-tYgvD5`.

Failed before model/write on an EPERM opening an atomic `cli-config.json`
temporary file in the real HOME. The separate `CURSOR_CONFIG_DIR` repair
followed, preserving HOME for authentication and retaining outer write denial.

- Raw task: `f3cc421a066d684a52094e2c670cd5bb5ebc88df1824f44efce7bde95206c47b`
- Canonical task: `c861d720cdff3977295a190e83e59a9dd57658282e9b8a3e41856979e0ed1cd5`
- Failure JSON: `4a03542775a1acbdb20da6acc802eeb14f742460d7e3f4f0f0b34b69348c3228`
- Stdout (empty): `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- Stderr: `fd8093b2b6feba486ec7190a067e9cb7a04fd8dd5ddc161f9aacc896d9f9fbd0`

### cursor-live-seatbelt-write-003

Base: `f14d9b69b761046bb4b8c5a43771412ae570e929`.
Retained run: `/Users/bekim/Heleos-spark/.worktrees/provider-runs/heleos-worker-5mrsdM`.

Reached the authenticated model service under Seatbelt, then stopped before
tool/write because `gpt-5.6-sol-high` had reached its usage limit. The provider
instructed switching models and reported a September 14, 2026 reset.

- Raw task: `6928425bda521456fe3b82747f287abcc0fe57e430e95d7f70b04f4e78c77d01`
- Canonical task: `d0e6f8a11f175f826cc8f607603d84414e12105e78c8ad0a04329c9ccb63dc2e`
- Failure JSON: `5df23459cf08a422c3b285ba0cd4df2272379204a7abba076e83b319b410e155`
- Stdout: `be0339a7bfa1ace26862429e1b26206054e5d03963aad11b3278eedaedfd221a`
- Stderr: `e2d6e7dfbb5e33e3ba105a8662842a54bb2007cdf30971b609e01693beceb830`

### cursor-live-seatbelt-write-004

Base: `965928055baa41cb2280abb27614cf7f8ef22055`.
Retained run: `/Users/bekim/Heleos-spark/.worktrees/provider-runs/heleos-worker-AxZ7ry`.

`composer-2.5` failed before any write with
`Workspace context exclusion is not allowed for this user, team, or selected model`.
The required exclusion flag was preserved; the next distinct task selected
`gpt-5.6-terra-high` from the authenticated inventory.

- Raw task: `0b305cc7229a2d4a44ba310ea611cda37a53f017aa7c2aa51c42df772bca186b`
- Canonical task: `08fb808bf23438419df38ac8b8898e43b57deffaf631f625b0fff040f6325af7`
- Failure JSON: `147f5055dbb614aea505ce36dff02eb2c662978e7f7cec9571a5b5257189d856`
- Stdout: `d1209936a9a8bcbc39c7a4835b02bcfb024067c66b41d7f4fdd1608f283dc629`
- Stderr: `1efb3a7aed93bdbcbc5308a4ccd1154389d73453a781b2b1d77c5037a7771aec`

### cursor-live-seatbelt-write-005

Base: `02e5d2858c8d08d9d3e68741ee18037c515fcc77`.
Retained run: `/Users/bekim/Heleos-spark/.worktrees/provider-runs/heleos-worker-UxCc7j`.

Reached `GPT-5.6 Terra 272K High` under macOS Seatbelt, then failed before
tool/write at the Cursor API-model monthly usage limit. The provider reports
the monthly cycle resets September 14, 2026. No additional attempt is queued
or authorized before reset or an explicit owner spend-limit action.

- Raw task: `a738a5c8c8ad9037e92619cc7960f9e929792736c98829ae7bf14fac166916c0`
- Canonical task: `7ad8c78c5993e4082b3702198aa26a75340360927ace0cb579d60e8f73e53d60`
- Failure JSON: `799350f841180d3051dfd2b6177d539eac2169d8f7dc740eb8cdeecdd8e724ee`
- Stdout: `be9e9522f52531c545f91cc07c91a2f82b6cb3b2912cb3c8c71ea9b650028890`
- Stderr: `e2d6e7dfbb5e33e3ba105a8662842a54bb2007cdf30971b609e01693beceb830`

## Verification, containment, and resumption

The final adapter passed 23 black-box tests under both Python 3.14.6 and 3.9.6.
The runner passed 44 host tests; strict Clippy, formatting, and whitespace checks
passed for commit `02e5d28`. These checks cover the code and local executable
fixtures. They do not establish a successful Cursor model write.

Every live attempt selected `macos_seatbelt`; none used an uncontained fallback.
Seatbelt restricts provider-tree path writes to the canonical checkout and
fresh home/temp roots. Explicit real HOME does not permit real-home writes.
Fixed `--force` bypasses provider confirmations and is not containment. Neither
the adapter nor these failures proves read/network/inherited-authority isolation,
provider-internal action/cost accounting, or complete process-tree containment.
Native Windows/NTFS execution remains pending. No production authority,
Foundation acceptance, merge, push, or deployment is implied.

Resume safe independent engineering while Cursor is quota-blocked. After the
reported reset or an explicit owner spend-limit action, the controller may
verify current capability and assign one new PUBLIC-only task identity against
a new exact base. Preserve all five retained attempts and their causal repairs.
