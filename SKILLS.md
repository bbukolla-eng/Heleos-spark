# Heleos-spark skills and workflow index

Read [AGENTS.md](AGENTS.md) for shared authority and [CURRENT_STATUS.md](CURRENT_STATUS.md) for current progress. Provider entrypoints are indexed in AGENTS.md.

This file is a project workflow index, not an executable skill package, installed plugin, or permission mechanism. It does not claim any external skill is installed or admitted. Runtime skill packages use their own `SKILL.md` instructions and must be discovered and read through the actual runtime before use. Do not create case-only duplicates of this index.

## Workflow routing

| Work | Required outcome | Starting point |
| --- | --- | --- |
| Resume or compaction recovery | Correct checkout, verified checkpoint, first unfinished action; no duplicate dispatch | [AGENTS.md](AGENTS.md) and [CURRENT_STATUS.md](CURRENT_STATUS.md) |
| Foundation 0.1 release routing | Deterministic five-gate status, exact candidate/evidence binding, and the first unfinished release action | [Foundation release status](docs/operations/foundation-release-status.md) and `python3 scripts/foundation-release-status.py --human` |
| Native return binding before packaging | Bind the returned summary to the candidate and original outbound manifest/bundle; all authority claims false; complements packaging without changing exporter semantics or the acceptance ledger | [Native return binding check](docs/operations/foundation-native-evidence.md#native-return-binding-before-packaging) and `python3 scripts/verify-foundation-native-return-binding.py --repo PATH --candidate FULL_SHA --handoff PATH --summary PATH [--human]` |
| Native evidence return packaging | Deterministic archive of original returned bytes with structural and hash consistency checks; no independent native authentication or release authority | [Native evidence return guide](docs/operations/foundation-native-evidence.md) and `python3 scripts/export-foundation-native-evidence.py --help` |
| GitHub App owner decision recording | Exact owner packet, stale-state binding, dry-run, and one-file atomic registry update without account mutation | [GitHub App decision guide](docs/operations/github-app-decisions.md) and `python3 scripts/apply-github-app-decisions.py --help` |
| GitHub App unresolved draft preparation | Ignored visible-main draft with 29 null fields, exact state binding, and atomic creation without overwrite or decision authority | [Owner action required](OWNER_ACTION_REQUIRED/README.md), [preparation guide](docs/operations/github-app-decisions.md#prepare-an-unresolved-draft), and `python3 scripts/prepare-github-app-decisions.py --help` |
| Scoped coding | Tests for changed behavior, minimal implementation, exact changed-file/check evidence | Assigned task from the current implementation plan |
| Debugging | Concrete observed failure, cause, smallest repair, and affected regression check | Existing failed command/report; no wholesale restart |
| Worker dispatch | Verified provider capability, exact brief, single writer, bounded output | Shared assignment contract in [AGENTS.md](AGENTS.md) and the provider file |
| Review and integration | Findings tied to exact bytes, scoped fixes, independently checked local integration | Current task's acceptance contract and Git identities |
| PDF/evidence work | Immutable inputs, deterministic identifiers, traceable source-to-result lineage | [Foundation contract](docs/superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md) and [fixture policy](tests/fixtures/pdf/README.md) |
| Source curation/research | Cited candidate findings, rights basis, recorded egress, no production promotion | [Source registry](governance/sources.toml), [tool registry](governance/tools.toml), and [GROKBOTS.md](GROKBOTS.md) |

## Using available skills

Match a skill to the actual task; read its full instructions and required references before executing it. Use the minimum applicable set. Existing runtime workflows for test-driven development, debugging, worktrees, code review, verification, PDF inspection, or provider collaboration may help when available and approved. Their names here are not an installation list or a production admission decision.

An approved existing plan is the implementation starting point. Planning skills do not reopen completed architecture decisions by default. Generic cleanup, commit, or publication recipes do not override this project's preservation, exact-path, atomic-commit, clean-room, or owner-authorization rules.

## Admitting a new executable skill or plugin

Before introducing one, record its purpose and owner, original source, pinned version/commit/digest, license or usage basis, required permissions and egress, task-specific evaluation evidence, and rollback target. Review its instructions, scripts, dependencies, and assets as supply-chain inputs. Installation or enablement requires the appropriate authorization; a discovered or installed skill is not automatically production-approved.

Keep task results in the task ledger. Update this index only when workflows or admitted capabilities change, not whenever a test completes or an agent's session expires. If a skill is unavailable, report the capability gap and use an authorized existing workflow; do not claim it ran or install a replacement silently.
