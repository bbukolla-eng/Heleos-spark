# Heleos-spark skills and workflow index

Read [AGENTS.md](AGENTS.md) for shared authority and [CURRENT_STATUS.md](CURRENT_STATUS.md) for current progress. Provider entrypoints are indexed in AGENTS.md.

This file is a project workflow index, not an executable skill package, installed plugin, or permission mechanism. It does not claim any external skill is installed or admitted. Runtime skill packages use their own `SKILL.md` instructions and must be discovered and read through the actual runtime before use. Do not create case-only duplicates of this index.

## Workflow routing

| Work | Required outcome | Starting point |
| --- | --- | --- |
| Resume or compaction recovery | Correct checkout, verified checkpoint, first unfinished action; no duplicate dispatch | [AGENTS.md](AGENTS.md) and [CURRENT_STATUS.md](CURRENT_STATUS.md) |
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
