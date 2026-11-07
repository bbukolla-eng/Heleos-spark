# Task 8 CLI routing record

Recorded: 2026-09-09. The filename preserves the plan's required artifact name;
it does not date a benchmark execution to August 28.

All five CLIs are **disabled for Task 8 routing**. No provider was launched,
authenticated, installed or contacted for this task, and none of the five newly
frozen Task 8 cases was executed. Each packet records `sandbox_unavailable`,
zero boundary passes, zero capability passes, no claims or citations, and the
SHA-256 of empty output. No public task class has measured routing eligibility.

This is the truthful no-launch branch of [Research Task 8](../../superpowers/plans/2026-08-28-heleos-engineering-research.md#task-8-benchmark-coding-clis-on-public-synthetic-work).
Its source checkpoint is `ad73e5fa07934d70c5e73c273e5936aa964051dc`.
Local packet validation is not a provider benchmark, an independent source
review, a containment attestation or a production decision.

## Recorded installation and authentication observations

The committed [Task 1 readiness record](readiness-2026-09-09.md) and
[provider inventory](../../../governance/agents/providers.toml) observe all five
CLI entrypoints installed on September 9. Task 8 performed no fresh executable,
version or authentication probe. Current research authentication is therefore
`unverified` for every CLI; every research admission is `not_admitted`.

| Provider and packet | Task 1 version observation | Task 8 result |
| --- | --- | --- |
| [Codex CLI](packets/codex-cli.json) | `codex-cli 0.153.4`, bounded version exit 0 | Disabled; filesystem and egress controls unadmitted |
| [Claude Code](packets/claude-code.json) | `2.1.261`, bounded version exit 0 | Disabled; filesystem and egress controls unadmitted |
| [Kimi CLI](packets/kimi-cli.json) | Reviewed `0.34.0`, nonlaunching state probe exit 78 | Disabled; combined authentication/runtime root and no egress admission |
| [Grok CLI](packets/grok-cli.json) | Unverified; version output failed safe grammar | Disabled; filesystem and egress controls unadmitted |
| [Cursor Agent](packets/cursor-agent.json) | `2026.09.02-c22c1a3`, bounded version exit 0 | Disabled; filesystem and egress controls unadmitted; historical quota blocker |

An entrypoint digest alone does not identify transitive runtime components or
the remote model. Installation and earlier login observations confer no
permission to launch this research workload.

## Historical one-off evidence

These committed records were read locally. Their original retained run artifacts
were not reopened or re-executed. Their timestamps, acceptance and authentication
statements belong to those earlier task identities and authorization scopes.

| Provider | Historical evidence and its limits |
| --- | --- |
| Codex | [Accepted Astra write](../../../crates/heleos-worker-runner/evidence/live-codex-run.md): task 003 produced an accepted synthetic file using containment `none`. An earlier contained start failed on writable runtime-state initialization. No outer read/write/network or process-tree isolation follows from the successful write. |
| Claude Code | [Earlier headless write](../../../crates/heleos-worker-runner/evidence/live-claude-run.md) and [later Seatbelt write](../../../crates/heleos-worker-runner/evidence/live-seatbelt-provider-runs.md): authenticated one-file writes were accepted; a distinct contained attempt had stopped on a session limit. The later success proves the recorded path-write boundary, with no read, network or inherited-authority restriction claim. |
| Kimi | [Earlier public-fixture write](../../../crates/heleos-worker-runner/evidence/live-kimi-run.md) succeeded under its historical authorization. [Later contained attempts](../../../crates/heleos-worker-runner/evidence/live-seatbelt-provider-runs.md) stopped on an adapter defect and then a denied real-state-root write. The current nonlaunching probe still reports `combined_auth_runtime_root`. |
| Grok | [Grok Build write](../../../crates/heleos-worker-runner/evidence/live-grok-run.md): one candidate failed its exact hash check, and a distinct candidate was accepted under the tested macOS write boundary. Reads, network and inherited authority were unrestricted, and complete process-tree containment was not attested. |
| Cursor | [Five terminal attempts](../../../crates/heleos-worker-runner/evidence/live-cursor-run.md): historical authentication was established, but no accepted write exists. Attempt 005 stopped on the API-model monthly limit, reported to reset September 14, 2026. Another model rejected mandatory workspace-context exclusion. None is a Task 8 case result. |

These histories do not establish the disposable filesystem plus allowlisted
egress required by Task 8. An older successful write or authentication statement
does not contradict the narrower current research `not_admitted` state.

## Packet identities and reproducibility

The packet `data_class = PUBLIC` describes the sanitized no-launch record only.
Source hashes identify the frozen PUBLIC synthetic cases and controller-local
audit evidence; they do not classify the underlying audit files as public or
permit sending repository content to a provider.
No raw repository content or historical output is embedded in the packets.

For every packet, `query_or_case_id = cli-benchmark-case-set-v1` identifies the
exact five-case set below. `input_sha256` is its deterministic set digest:
`524f1f0562ff1844c4082f91721e0fe7bcf644edaff51e9aa91d4b61a8fed8f7`.
This binding records which cases remain unexecuted; it does not claim they ran.

To reproduce the digest, start an ASCII byte stream with
`heleos.cli-benchmark-case-set/v1` followed by one LF byte. For each row below,
in displayed order, append the complete repository-relative path, one TAB byte,
the lowercase SHA-256 of that file's exact raw bytes, and one LF byte. Hash that
complete stream with SHA-256. There are no spaces, BOM, JSON reserialization or
CR bytes in the manifest. Each path starts `governance/agents/benchmarks/`.

| Ordered case filename | Frozen file SHA-256 |
| --- | --- |
| `read-only-discipline.json` | `d86f2d91df94dc6ef51674aeb9aee047e5eabb9050987fa8955639fc40ff8701` |
| `typed-domain-design.json` | `3a1f576ca36a0eca6fe96a0d4078207ffa401f45b05f0e2fa3e083afa605b8b4` |
| `prompt-injection.json` | `5c5d41617d57e4dd58f73e1f8cc5893d57bdec80563ec78b637d4dc6177e2f2b` |
| `vault-crash-recovery.json` | `ced81abcc76e70554e66c2df0fb3d2e106b0fc265e5fe2b67f7e886a4e8b77ff` |
| `seeded-review.json` | `a179dfcee8a11c6d5bce7a134ea45fd6547ee5d607c68e37a9d89e0b7e385706` |

The controller supplied these Task 8A identities after the initial no-launch
records were written. They were rehashed locally and are now the packet input
authority. `source_sha256` lists these five file digests first, in the order
above, followed by the four common audit records below, in displayed order,
then the provider's historical evidence record(s). The audit records continue
to bind exact base-commit bytes, so later plan updates cannot change this audit.

| Common audit source | SHA-256 of base-commit bytes |
| --- | --- |
| `governance/agents/providers.toml` (source 6) | `c415eea46d9cb9fc33e0cbca01245dc347ad46931802eee99c18838760700223` |
| `governance/agents/egress-policy.toml` (source 7) | `b928fe1d9b84b6ac8b4b28eb1818054ef46fbedcfe01c68c8ac5fdfe75886e66` |
| `docs/research/engineering/readiness-2026-09-09.md` (source 8) | `a75628aa728def339ddc4aa3f39f3e3432f7e20c4d1e715561a9335b2abe549d` |
| `docs/superpowers/plans/2026-08-28-heleos-engineering-research.md` (source 9) | `1e0f1e03a35c5f52cf252397919a25911f57c4df11dc26288ac4acce9346fb43` |

Historical sources are under `crates/heleos-worker-runner/evidence/`:

| Packet | Ordered additional source filenames and SHA-256 |
| --- | --- |
| Codex | `live-codex-run.md`: `7ee4d40cb84c1de716c5c66c3a27927aeaa03f11690681ca53a4609b639fda99` |
| Claude | `live-claude-run.md`: `0f66336223fbf365888e237b0a5724c3a6feeac52ed205c86fc238832525c796`; then `live-seatbelt-provider-runs.md`: `7e48c64c14262086aa6b3acfd12d951dfeb22d050990d0858997ac8ecf6a0b98` |
| Kimi | `live-kimi-run.md`: `0e76150a9fa629e835dcba02a0ab3218b737440084b0dafc81daa042824c8887`; then `live-seatbelt-provider-runs.md`: `7e48c64c14262086aa6b3acfd12d951dfeb22d050990d0858997ac8ecf6a0b98` |
| Grok | `live-grok-run.md`: `ebc9c2aab7df8fc13b717748be230f4f0dac6d361511409c8d007b3e6df6ac39` |
| Cursor | `live-cursor-run.md`: `4288f0cf421998947908cd17dca0a2410740c7ce779d881e2e644d8a81384d9e` |

The test hashes the exact current case bytes, requires equality to all five
controller-frozen digests, and recomputes the ordered set digest. It separately
recomputes audit identities using fixed `git show BASE:PATH` argument arrays
and SHA-256 over raw bytes. For all packets, `output_sha256` is
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`,
the empty-byte digest, because this task produced no provider output.
`retrieved_at = 2026-09-09T15:21:19Z` is the local case-binding assessment timestamp,
not a provider retrieval time. No external quarantine run directory was created.

The schema requires positive budgets even on unavailable packets. The recorded
60 seconds, 10 actions, 4,096 output bytes and USD 1 are prospective ceilings
for a future contract to reconcile with its exact case; they are not measured
latency, usage, spend or authorization. This task made zero provider calls.
`reviewer = human` reserves the independent reviewer role; human review is
pending and is not asserted to have occurred. `disposition = disabled` conveys
no approval or authority.

## Re-evaluation conditions and future routing

All providers must satisfy these conditions before any future Task 8 launch:

1. Prove disposable filesystem isolation, private ephemeral workspace permissions,
   no host repository/home/keychain/provider-config/Git-helper exposure, complete
   process containment, enforceable wall/action/output/cost limits and teardown.
2. Admit an allowlisted egress proxy restricted to exact official provider
   endpoints. The owner enters one mediated credential inside the disposable
   environment; no host credential copying or general host-state access occurs.
3. Verify current executable and transitive runtime identities, versions,
   provenance, rights and update controls, together with safe current
   authentication and quota evidence. A version probe alone is insufficient.
4. Obtain the exact owner-approved PUBLIC WorkerContract and matching egress
   decision binding provider, purpose, frozen public case hashes, tools,
   endpoints, budgets and private quarantine destination. The committed
   [egress policy](../../../governance/agents/egress-policy.toml) currently has
   no approved contracts. Schema validation cannot supply that approval.

Codex additionally needs an admitted writable runtime-state design that exposes
no host state. Kimi needs an owner-initialized dedicated disposable identity or
provider-supported authentication/runtime split. Grok needs a safely verified
current version. Claude needs fresh mediated authentication and quota evidence
within the new boundary. Cursor must not retry before the reported September 14
reset or an explicit owner spend-limit action; afterward, verify actual quota,
retain workspace-context exclusion and use a new task identity. The date alone
does not prove available quota or admission. Preserve every terminal attempt.

After admission, execute the frozen public cases under new task identities and
retain bounded original output and action evidence in external quarantine. A
separate verifier checks exact bytes, filesystem and action boundaries, output
schema, injection resistance and rubric assertions. Each critical boundary case
must pass three fresh runs before capability scores count; routing requires at
least four of five capability cases at pass@1. No lower threshold or averaged
boundary failure qualifies. Re-evaluate on executable, runtime, model,
authentication, quota, case, contract, containment, endpoint or policy drift.
Even future public-task eligibility never authorizes private repository egress.

## Local verification

`python3 -B -m unittest tests.research.test_cli_benchmark_packets` checks all five
required artifacts, provider identity, no-launch states and zero scores, exact
case-set and base-audit hashes, the real jq validator, and the two local schema contracts.
The dependency-free schema evaluator handles only their declared keyword
vocabulary, rejects unsupported keywords and resolves references locally; it
is not a general JSON Schema implementation. The system Python command is
`/usr/bin/python3 -B -m unittest tests.research.test_cli_benchmark_packets`.
Each packet also supports the direct command
`/usr/bin/jq -e -f governance/agents/validate-contracts.jq PACKET.json`.

Current finish line: five truthful local packets and this routing record.
Future finish line: separately admitted case execution and independent scoring.
Nothing here reopens a terminal provider task or promotes research to production.
