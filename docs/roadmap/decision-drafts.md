# Decision drafts for Phase 0

**Status:** Drafts for the owner; none of these is a decision until the owner copies it to `docs/decisions/YYYY-MM-DD-<slug>.md`, fills in the Decision line, and commits it
**Date:** 2026-09-02
**Format:** question, options, evidence, recommended answer, and the fields the owner completes. Design-level changes still require the owner's recorded approval per spec section 16.

Each draft is self-contained so that a decision record can be made from it without reading anything else. Evidence pointers name the page and section.

---

## Draft 1: Approve the foundation design

**Question.** Is the foundation design of 2026-08-26 approved and frozen as the initial architecture baseline (spec section 16)?

**Options.** (a) Approve as written. (b) Approve with named amendments, each recorded as its own decision. (c) Do not approve; return to design.

**Evidence.** The spec on `main` is still headed "Proposed for owner review". Spec sections 14 and 16 make its approval the precondition for Foundation 0.1. Revision 2 of the roadmap details the spec and proposes no amendment to it.

**Recommended.** (a).

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 2: Plan of record for Foundation 0.1

**Question.** Which plan governs Phase 1: Option A (the 2026-08-27 reconciliation plan, repaired) or Option B (adopt PR #1 behind a gate)?

**Options.** (a) Option A after the repair commit listed in `docs/roadmap/plan-of-record-audit.md` section 2; PR #1 stays open as a reference branch. (b) Option B, only with a decision that approves the 2026-09-01 enriched design and supersedes the spec's section 14 ordering and its closing rule (audit finding B13), that itself lists which section 14 items and bullets move to which milestone, and only behind the gate in section 4 of that page, including a section 16-form task plan for the deferred items approved before Phase 1 starts. (c) Neither; write a new plan. Decision 1 (approval of the spec) is a prerequisite of this decision.

**Evidence.** Option A covers every section 14 item but is not executable as written (three tasks cannot pass their own steps; the migration order breaks its own discovery code); 29 repairs are enumerated. PR #1 implements neither the evidence vault nor PDF intake, can demonstrate none of the ten acceptance bullets, fails its own declared suite from a clean checkout (two defects with known root causes), transplants recovered migrations 001 to 012 unchanged against the custody record's rule, and asserts its approval only inside the branch. `docs/roadmap/phase-0-evidence.md` sections 3 to 6.

**Recommended.** (a).

**Plan commit approved:** ____ (fill in after the repair commit, or the Option B task set, has been reviewed; Phase 1 does not start until this field is filled)

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 3: Branch policy for Claude Code

**Question.** Does Claude Code work on one fixed branch (PR #3's lane) or on one branch per session matching a pattern?

**Options.** (a) One fixed lane branch; every web session is moved onto it by the session-start hook; per-session branches are denied. (b) A pattern (`claude/*`): each session works on its own branch, pushes only to that branch, opens a draft pull request, and the owner merges; the guard is revised to the pattern, allows workflow-managed linked worktrees for workers, and protects `.claude/workflows/` and `.claude/agents/`; branch protection on `main` is the outer boundary. (c) No guard; branch protection only.

**Evidence.** Claude Code on the web creates a `claude/<slug>-<id>` branch for every session (the lane name itself follows that pattern); revision 2 was written on `claude/dynamic-workflow-roadmap-156gdn`. Simulation of PR #3's guard after merge: a clean web session on another branch is moved to the lane with a printed notice (PR #3's documented reconciliation), an unswitched session has every edit, commit, and push denied, `git worktree add` is denied on the lane, which blocks the isolated workers spec section 10 requires, and a lane deleted on `origin` is recreated from whatever HEAD the next clean session has. `docs/roadmap/phase-0-evidence.md` section 8; operating model section 2.4.

**Recommended.** (b).

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 4: Disposition of pull request #3

**Question.** What happens to PR #3 (lane guard, `CLAUDE.md`, roadmap revision 1)?

**Options.** (a) Merge after the guard is revised per Decision 3; replace its roadmap pages with revision 2. (b) Merge as-is. (c) Close.

**Evidence.** The guard is tested (27 tests pass in the current harness) and its git-layer hooks are useful under either branch policy; its roadmap pages are superseded by revision 2 and its evidence page has four wrong numbers.

**Recommended.** (a).

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 5: The four auto-installed GitHub Apps

**Question.** For each of Azure Pipelines, AWS Connector for GitHub, Amazon Q Developer, and ECC Tools: retain, restrict, suspend, or remove?

**Options.** Per app: retain as-is; restrict to read-only; suspend; remove.

**Evidence.** ECC Tools holds at least `contents:write` and `pull_requests:write`: it authored 12 commits and opened PR #2, whose generated files describe another branch and would egress by default if run. Amazon Q posts reviews on pull requests. Spec section 11 says their presence is not approval to run them, and no workflow or secret may be added for them until this inventory is done. `docs/roadmap/phase-0-evidence.md` section 9.

**Recommended.** Inventory permissions from the repository's Settings, Integrations, GitHub Apps page, or with `gh api /user/installations` (each row's `app_slug` and `permissions`; the `/repos/:owner/:repo/installation` endpoint needs the app's own JWT and returns nothing for a user login), and paste the output into the record; remove or restrict ECC Tools to read-only; suspend the other three until a task needs them; state that no app-triggered result is authoritative.

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 6: Protect `main`

**Question.** Which branch-protection rules apply to `main`?

**Recommended.** Pull request required; no force push; no deletion; required status checks `hooks-and-lint`, `core (macos-14)`, `core (windows-latest)` once CI exists; dismiss stale approvals; administrators not exempt. Paste `gh api repos/:owner/:repo/branches/main/protection` into the record.

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 7: Python floor

**Question.** Which Python version is the floor for project code?

**Evidence.** Both candidate plans require 3.12; the harness defaults to 3.11.15 and provides 3.12 through `uv`; the owner's machines run 3.12.

**Recommended.** 3.12, provisional until the Task 1 platform proof confirms it as the section 12 core-technology selection (the proof must show the core rendering PDFs, running SQLite in WAL mode, packaging, and answering a loopback IPC probe on `macos-14`, `windows-latest`, and the owner's Apple Silicon Mac; the proof record is filed under `docs/decisions/`; a failure reopens this decision before Task 2 starts); pinned in `pyproject.toml`, `uv.lock`, and CI; repository tooling under `tools/` and the hook scripts stay standard-library and run on the host interpreter, 3.11 or later.

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 8: Package name

**Question.** What is the canonical Python package and distribution name?

**Evidence.** Option A uses `heleos_spark` / `heleos-spark`. PR #1 keeps `helios_takeoff_core` / `helios-takeoff-core==0.1.0`, which the custody record says must not be published as canonical; renaming it touches 78 files.

**Recommended.** `heleos_spark` under Option A. Under Option B, keep `helios_takeoff_core` with a documented product-name mapping, or rename before merge and accept the provenance cost; keeping the name needs a recorded owner exception to the custody record's statement that no recovered package may be published as canonical, and the version leaves 0.1.0 either way.

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 9: PDF backend

**Question.** Which PDF backend does Foundation 0.1 use?

**Recommended.** Decide by the Task 1 platform proof between `pypdfium2` and `pypdf`, with license as a hard filter (verify each license from package metadata before admission); PyMuPDF is AGPL-3.0 and excluded unless the owner accepts that license. Record the chosen package, version, digest, and license in `dependencies/manifest.toml`.

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 10: Deterministic controller

**Question.** What is the non-LLM controller spec section 10 requires, and when is it built?

**Options.** (a) The `tools/wf/` build tooling of the operating model, built in Phases 0 and 1, with the workflow script owning control flow and budget, the harness owning isolation and tool permissions, and the guard, hooks, branch protection, and CI owning authority. (b) PR #1's build fabric (`tools/helios_build`), which exists but lacks leases, heartbeats, and cost budgets and enforces review independence only at profile level. (c) Codex as orchestrator now.

**Recommended.** (a); evaluate (b) only under Option B and as two candidates, `tools/helios_build` for the section 10 worker controller and the P1A agentic spine for the section 13 runtime job model, each with its recorded gaps (leases, heartbeats, deadlines, cost accounting); defer (c) until a measured comparison exists. Nothing about the controller may gate Foundation 0.1.

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 11: The ECC-generated files

**Question.** What is done with each of the 12 files the ECC Tools app generated on `main`?

**Recommended.** The dispositions in `docs/roadmap/skills-and-plugins.md` section 3: hand-edit the two skill copies, `openai.yaml`, `.codex/config.toml`, and `.codex/AGENTS.md`; pin the agent configuration models; delete `identity.json` and the instincts file; keep the research playbook with one added data-class line; keep `ecc-tools.json` as a provenance record behind a note.

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 12: Egress policy for prompts, connectors, and sessions

**Superseded by a prepared record.** This draft is answered in full at `docs/roadmap/decision-12-egress-draft.md`, which carries the evidence, the recommendation, and the blank determinations. That file's own header says how the owner turns it into `docs/decisions/2026-09-03-egress-policy.md`. Decide there, not here. What follows is the original draft, kept for the record.

**Question.** What may leave the machine, to whom, from which kind of session, with what record?

**Recommended.** Write `docs/policies/egress.md`: `PUBLIC` content may go to admitted providers with source and license logging; `INTERNAL` is local by default and needs an explicit approved policy per provider; `PROJECT_CONFIDENTIAL` never enters a web session and never reaches a provider without a project- and provider-specific record; `SECRET` never appears anywhere; every external submission records provider, purpose, data class, source hashes, policy decision, time, and result reference; multi-provider routing of prompts is off.

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 13: Budgets and known limits

**Question.** What token ceilings, agent caps, and Actions-minutes ceilings apply, and which controls that this harness cannot enforce are accepted as known limits?

**Recommended.** The per-phase ceilings in the operating model section 6.1, tuned after the first ten run records; a monthly GitHub Actions ceiling of 3000 billed minutes with the macOS and Windows matrix on lane pushes and pull requests only; per-agent wall clock, heartbeats, money and input-token budgets, and Bash-level egress in web sessions accepted in `docs/runs/KNOWN-LIMITS.md` at Phase 1 exit, or a repository daemon scheduled in a named phase.

**Decision:** ____ **Decided by:** ____ **Date:** ____

---

## Draft 14: CI runners and the acceptance oracle

**Question.** Where do the automated checks and the Foundation 0.1 acceptance run?

**Recommended.** `ubuntu-latest` for hooks, lint, and ledger checks; `macos-14` and `windows-latest` for the core suite; the owner's Apple Silicon Mac and Windows machine as the acceptance oracle through local `acceptance-verify` runs; four run ids cited by the acceptance page.

**Decision:** ____ **Decided by:** ____ **Date:** ____
