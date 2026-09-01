# Skills, agents, plugins, and MCP servers: admission register

**Status:** Proposed for owner review
**Date:** 2026-09-01
**Owner:** Bekim Bukolla
**Governs:** every skill, agent pack, plugin, MCP server, and coding harness used with Heleos-spark

Design spec section 11 makes skills and plugins executable supply chain: admission
requires provenance, a pinned version or digest, a license, a permissions and
egress review, evaluation evidence, a named owner, a concrete task, and a
rollback target. Nothing on this page is admitted by being listed. Each row
carries a status; only the owner moves a row to **Admitted**.

Statuses: **Authorized by spec** (section 11 already names it),
**Repository-owned** (versioned and tested in this repo), **Proposed: design
phase** (usable now, read-only or advisory, no production effect),
**Proposed: Foundation 0.1** (needed once coding starts), **Later** (named by
the spec for a later milestone), **Not authorized** (present but unreviewed).

## Register

| Tool | Provenance (repo, version, commit, license) | What it is | Concrete task in Heleos-spark | Permissions and egress | Pin and rollback | Status |
|---|---|---|---|---|---|---|
| Lane guard | This repository: `.claude/hooks/`, `.githooks/`, `tests/hooks/` | PreToolUse and git-layer hooks that keep Claude Code on the designated branch | Enforce the single-lane rule (spec section 10 worker boundary) | Local only; reads git state; no network | Versioned with the repo; revert the commit to roll back | Repository-owned |
| Superpowers | `github.com/obra/superpowers` 6.3.0, commit b36e082 (2026-08-12), MIT | Process skills: brainstorming, writing-plans, executing-plans, test-driven-development, systematic-debugging, verification-before-completion, requesting-code-review, finishing-a-development-branch, using-git-worktrees | Opens and closes every piece of work: brainstorm before design, plan before code, TDD, verify before claiming done | Prompts only; no tools of its own; no network | Pin the plugin to 6.3.0 in the marketplace config; rollback by pinning the previous release | Authorized by spec (section 11: "reviewed Superpowers workflows") |
| ECC (everything-claude-code) | `github.com/affaan-m/ECC` 2.2.1, commit ca185ef (2026-08-31), MIT | 68 agents, 286 skills, rules and hooks | Rules `rules/common` and `rules/python`; agents `planner`, `tdd-guide`, `code-reviewer`, `security-reviewer`, `python-reviewer`, `database-reviewer`, `silent-failure-hunter`; commands `/plan`, `/tdd`, `/code-review`, `/security-scan`, `/test-coverage` | Rules and agents are prompts. ECC hooks run local Node scripts and some optional integrations; install with hooks disabled (`hooks_enabled: false`) until each hook is reviewed | Pin 2.2.1; install the `minimal` profile with `rules/common` + `rules/python`, not `full`; rollback by removing the plugin | Proposed: design phase (rules and review agents); hooks **Not authorized** until reviewed one by one |
| Council of High Intelligence | `github.com/bbukolla-eng/council-of-high-intelligence` fork, plugin 1.2.0, commit c1395a6 (2026-07-27), MIT | `/council` multi-persona deliberation, 18 personas, full/quick/duo modes, `.council.yaml` project overrides | Pressure-test architecture and policy decisions before they reach the owner (for example storage interfaces, PDF library choice, worker controller design); outcomes recorded under `docs/decisions/` | Prompts only when auto-routing is off; multi-provider routing (`--models`) would send prompts to other providers and is **not** enabled | Pin the commit; add a repo `.council.yaml` with `no_auto_route: true` once admitted; rollback by deleting the skill | Proposed: design phase |
| Loop engineering | `github.com/cobusgreyling/loop-engineering` fork, commit ab6a1aa (2026-08-02), MIT; npm `@cobusgreyling/loop*` | Patterns and skills for unattended agent loops: `loop-constraints`, `loop-budget`, `loop-verifier`, `minimal-fix`, `gate.yaml` denylists, kill switches | Template `loop-constraints.md`, `loop-budget.md`, and `gate.yaml` for the first unattended loops (daily triage report-only, PR babysitter with verifier) once CI exists in Foundation 0.1 | Repo files are inert until a loop runs; npm tools fetch from the registry and are **not** installed in the design phase | Copy only the three template files, cite the commit; rollback by deleting them | Proposed: Foundation 0.1 (templates), Later (running loops) |
| Graph engineering | `github.com/codejunkie99/graph-engineering` fork, commit cfacb56 (2026-07-23), MIT | Skill: 9-stage knowledge-graph pipeline and task-graph orchestration patterns (diamond pattern, stop rule, human gate) | Task graphs: shape every multi-agent job as plan, parallel workers, separate verifier, single merge owner, owner gate. Knowledge graphs: model the knowledge lane (source registry, taxonomy, cited facts, candidate rules) with the `/kg-scope` to `/kg-eval` prompts in Phase 2 | Prompts only; no tools; no network | Copy the skill directory at the pinned commit into `~/.claude/skills/`; rollback by deleting it | Proposed: design phase |
| Codebase Memory MCP | `github.com/DeusData/codebase-memory-mcp` 0.8.1, commit d6be58e (2026-07-31), MIT; single static binary or npm/pypi `codebase-memory-mcp` | Local code-intelligence MCP server (15 tools: search, trace, architecture, impact, Cypher) | Structural queries over `src/` once code exists; reduces file-by-file reads | Reads the repository; writes agent config files on install; 100% local per its README; hooks are fail-open context-only. Its installer edits `~/.claude.json` and hook config, so run it only after the lane guard's owner-managed files are excluded from its scope | Pin release 0.8.1 by checksum; rollback by removing the server entry and hooks | Proposed: Foundation 0.1 (after first code lands) |
| Claude Cookbooks | `github.com/anthropics/claude-cookbooks` fork, commit bbfab1b (2026-08-28), MIT | Reference notebooks | Read-only patterns for PDF handling (`misc/pdf_upload_summarization.ipynb`), citations (`misc/using_citations.ipynb`), evals (`misc/building_evals.ipynb`), vision (`multimodal/best_practices_for_vision.ipynb`), structured output (`tool_use/extracting_structured_json.ipynb`). Adapters only; never authority (spec section 3, principle 7) | None; nothing installed | Not applicable | Proposed: design phase (reference only) |
| Codex CLI | OpenAI Codex CLI; repo-local config `.codex/config.toml`, `.codex/AGENTS.md` (ECC Tools generated) | The orchestrator named in spec section 10 | Task definition, routing, review, controlled git integration; a deterministic controller (not an LLM) owns permissions and budgets, which does not exist yet and is Phase 1 work | The generated `.codex/config.toml` enables GitHub, Context7, Exa, Memory, Playwright, and Sequential Thinking MCP servers via `npx -y` (unpinned, network-fetched). Pin each before use | Pin exact package versions in `.codex/config.toml`; rollback by reverting the file | Proposed: design phase, pins required first |
| GitHub connector (MCP) | Claude Code GitHub MCP server, provided by the harness | Pull requests, reviews, checks | The current repository control path (spec section 11) | Repository-scoped by the harness; the lane guard denies branch creation, merges, approvals, and writes to other branches | Managed by the harness | Authorized by spec (section 11) |
| Context7 | Claude Code connector, provided by the harness | Library documentation lookup | Verify library APIs before adopting a dependency (PDF library, pydantic, SQLite features) | Outbound documentation queries; send no project content | Managed by the harness | Proposed: design phase |
| Exa search, Hugging Face MCP | Claude Code connectors, provided by the harness | Web search; model and dataset discovery | Research lane only (spec section 4.3): candidate models and datasets for the section 8 bakeoff; results land in staging with provenance | Outbound queries; only `PUBLIC` class content may be sent (spec section 5) | Managed by the harness | Later (Phase 2 research lane) |
| Codex Security | Named in spec section 11 as the preferred next plugin | Code review and dependency gates | Dependency and security gates before Foundation 0.1 merges | To be reviewed on admission | To be pinned on admission | Later (owner to evaluate) |
| ECC Tools app, Amazon Q Developer, Azure Pipelines, AWS Connector | Account-level GitHub App installs (spec section 11) | Auto-installed apps | None authorized. ECC Tools generated `.claude/ecc-tools.json` and the repo skill; Amazon Q comments on pull requests | Inventory each app's permissions; retain, restrict, suspend, or remove by owner decision | Not applicable | Not authorized (inventory pending) |

## Notes on the generated files already in the repository

- `.claude/ecc-tools.json` cites `tests/test_engine_evaluator.py` and
  `docs/architecture/agentic-layer-boundary.md`, which do not exist on `main`.
  Treat its readiness score as informational only.
- `.claude/skills/Heleos-spark/SKILL.md` and `.agents/skills/Heleos-spark/SKILL.md`
  were generated from two commits and contain TypeScript placeholders. `CLAUDE.md`
  states the Python conventions that apply. Regenerate or hand-edit them after
  Foundation 0.1 lands real code.
- `.codex/config.toml` sets `approval_policy = "on-request"` and
  `sandbox_mode = "workspace-write"` and launches six MCP servers with `npx -y`.
  Section 11 requires pinned versions before these run against project data.

## Admission checklist (copy per tool into `docs/decisions/`)

1. Source reviewed at the pinned commit; no runtime fetch of unpinned code.
2. License recorded and compatible.
3. Permissions and egress listed; data classes it may touch named (spec section 5).
4. Concrete task and named owner.
5. Evaluation evidence: what was tried, on which fixture, with what result.
6. Rollback target: the exact command or file revert that removes it.
7. Owner approval recorded with date.
