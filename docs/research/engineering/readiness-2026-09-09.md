# Research provider readiness — 2026-09-09

Status: observation only; all seven providers are `not_admitted` for this research plan.
The installed CLI paths below are capability observations, not permission to launch.
No provider job, authentication flow, browser operation, installation, account change,
or external research submission was performed for this record.

This snapshot is based on `485b54e9b0915b81e564425e9b7981c5ea1e0590` in branch
`build/research-contracts-2026-09-09`. The exact machine-readable record is
[providers.toml](../../../governance/agents/providers.toml); its absolute executable
paths are local host inventory and must never be included in provider submissions.
`installed = true` means the recorded CLI entrypoint was found and hashed. Browser
records use `installed = false` because no executable installation was verified;
that value does not claim the browser service is absent.

| Provider | Current non-secret observation | Research blocker |
| --- | --- | --- |
| Codex | CLI entrypoint present; `codex-cli 0.153.4`; version exit 0 | Authentication unverified; containment and egress not admitted |
| Claude Code | Executable present; `2.1.261 (Claude Code)`; version exit 0 | Authentication unverified; containment and egress not admitted |
| Kimi | Executable present; nonlaunching probe reports reviewed `0.34.0`; exit 78 | `combined_auth_runtime_root`; authentication unverified; no egress admission |
| Grok | Executable present; bounded version command exit 0; output failed the safe version grammar and was discarded | Version and authentication unverified; containment and egress not admitted |
| Cursor Agent | Entrypoint present; `2026.09.02-c22c1a3`; version exit 0 | Authentication unverified; containment and egress not admitted |
| NotebookLM | No current browser or authentication probe | Research-only; browser authentication, containment, and egress unverified |
| GrokBots | No current app or browser authentication probe | Research-only; browser authentication, containment, and egress unverified |

CLI discovery used fixed command names via `command -v`; each observed absolute
entrypoint was resolved and hashed directly, never selected by a repository value.
Versions used fixed argument arrays ending in `--version`, `shell = false`, closed
stdin, a five-second deadline, and a 4,096-byte output limit. Only short version
text matching a conservative alphanumeric punctuation grammar was retained.
Kimi was not launched: the specifically admitted `scripts/provider-adapters/kimi-stdin.py`
`--kimi-executable` / `--probe-state-layout` path hashed the fixed executable and
returned `available = false`, `read_only_auth_writable_runtime = false`, and
`reason = combined_auth_runtime_root`. Its reviewed version is a local identity
classification, not a fresh Kimi runtime response.

Hashes identify entrypoint bytes only. Codex and Cursor can use launchers, and these
hashes do not prove the identity of transitive runtime components, dependencies,
remote models, or update channels. Origin strings record observed package/system
paths; upstream provenance, licenses, service rights, and update channels remain
unverified where no already-reviewed local admission was found. No config contents
or environment values were inspected to fill those gaps. Authentication remains
unverified because no safe status-only command was established and executed in this
task. Earlier authenticated write evidence in `CURRENT_STATUS.md` is historical
context and does not admit any provider for this research plan.

| Fixed validator | Fresh version | SHA-256 / TOML capability |
| --- | --- | --- |
| `/opt/homebrew/bin/python3` | Python 3.14.6 | `4f00ea2ad53d62437a6a3946b73c73614a97e8accdc5b96dc095ea1a0d9c6a56`; standard-library `tomllib` available |
| `/usr/bin/python3` | Python 3.9.6 | `44a68ddc1983d6cff3fd35ba3f9ba5f82004216f1dcde69892b3d1b06e408698`; `tomllib` unavailable |
| `/usr/bin/jq` | jq-1.7.1-apple | `e52747c4a03c38fe5929bea959f206743f0bcf0b9ce941acd1bcfa8851f7ecae`; not a TOML parser |

The readiness test runs under both Python interpreters. On 3.9 it invokes the fixed
`/opt/homebrew/bin/python3 -I -B` parser with TOML bytes on stdin. It installs nothing,
uses only standard-library modules, and never executes an executable path from a
manifest. This compatibility check depends on that fixed newer interpreter being
present; it does not claim native Python 3.9 TOML support or general host portability.

[Egress policy](../../../governance/agents/egress-policy.toml) denies `SECRET`,
`INTERNAL`, and `PROJECT_CONFIDENTIAL`. `PUBLIC` requires exact provider, purpose,
source-byte hash set, endpoint allowlist, owner-approved contract-byte identity,
quarantine destination, and an explicit matching policy decision. No approved
contracts exist in this snapshot. Parsing a policy or validating a contract cannot
enforce network isolation, prove owner approval, or confer authority. Production,
repository writes, pushes, merges, approvals, credentials, and launches are denied.

Current finish line: local records and deterministic rejection tests. Later gates:
verify complete executable/runtime provenance and rights; establish current safe
authentication evidence; admit containment and the egress mediator; approve an exact
PUBLIC task contract and quarantine destination before a provider launch. Re-probe
on executable, contract, provider, or policy drift. Host observations are never
reusable permission.

<!-- readiness-identities:v1
{"providers_sha256":"c415eea46d9cb9fc33e0cbca01245dc347ad46931802eee99c18838760700223","egress_policy_sha256":"b928fe1d9b84b6ac8b4b28eb1818054ef46fbedcfe01c68c8ac5fdfe75886e66","github_apps_sha256":"8c06940f5309f39148a4d821a7684306d1a0c31802b64299fc83e29f9ef926b0"}
-->
