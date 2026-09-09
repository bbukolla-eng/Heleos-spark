# Kimi working instructions

Kimi is a bounded implementation, test, documentation, or independent-review provider for this repository. Shared project policy remains in [AGENTS.md](AGENTS.md), the dated status snapshot is [CURRENT_STATUS.md](CURRENT_STATUS.md), and workflow routing is described in [SKILLS.md](SKILLS.md). Read these files and supply the approved instruction context explicitly when the active runtime cannot confirm loading; respect the provider's egress scope.

Before dispatch, verify the actual Kimi CLI, API, or adapter capability, its authentication state, and the exact checkout and allowed paths. A read-only Kimi plugin can propose or review work, but it cannot be represented as write-capable. Do not invent commands, infer login, or treat this file as proof that a tool is installed, authenticated, sandboxed, or automatically governed.

For the repository adapter, `python3 scripts/provider-adapters/kimi-stdin.py --kimi-executable /absolute/path/to/kimi --probe-state-layout` is the only admitted state-layout probe. It hashes bounded executable bytes without launching Kimi, reading a prompt, or opening authentication/config files. Exit 78 with `combined_auth_runtime_root` or `unverified_executable` means contained writes are unavailable. `--worker-state-root` also refuses before touching the path. Never copy credentials or make a combined authentication/runtime root writable to force a task through.

For a write-capable route, give Kimi one scoped assignment with an explicit base, owned paths, acceptance checks, and prohibited paths. That assignment authorizes candidate writes only within its scope. Kimi may implement a small source patch and its tests or documentation, then report changed paths, commands, results, and unresolved risks. It must preserve unrelated work and stop before broadening scope. Kimi does not merge to the main branch, push, deploy, approve its own output, or declare production acceptance.

For review, freeze the reviewed bytes or commit and require findings tied to exact files and evidence. The controller independently inspects the candidate and reruns relevant checks before integration.

Never submit secrets or credential material. Internal code, private evidence, or confidential project content requires the applicable project/provider approval. Prefer public or minimized inputs. Provider output is a candidate, not proof; independently verify citations, claims, and changed bytes.
