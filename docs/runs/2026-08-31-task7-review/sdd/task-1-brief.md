### Task 1: Approve the Baseline and Establish Governance

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`
- Create: `SECURITY.md`
- Create: `docs/architecture/decisions/0001-foundation-runtime.md`
- Create: `docs/architecture/task-graph.md`
- Create: `docs/roadmap.md`
- Create: `governance/tools.toml`
- Create: `governance/sources.toml`
- Create: `governance/fixtures.toml`
- Create: `governance/github-apps.toml`

**Interfaces:**
- Consumes: The approved design and this plan.
- Produces: Machine-readable admission registries and the binding current/future roadmap used by every later task.

- [ ] **Step 1: Capture the pre-change approval failure**

Run: `rg -n "Proposed for owner review|Production implementation begins only" README.md docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`

Expected: both stale pre-approval statements are found.

- [ ] **Step 2: Record the approval and runtime ADR**

Change the design status to exactly `**Status:** Approved by owner on 2026-08-28` and make the README current phase exactly `Foundation 0.1 implementation on the foundation-0.1 branch`. The ADR must state that it records the owner's external approval rather than creating approval, and must record the Rust 1.96.1/edition 2024 decision, Task 2 host/Windows compilation proof, Task 5 WASI isolation/IPC proof, explicit deferral of UI/rendering/packaging selection, SQLite plus filesystem vault, canonical identity rules, forward-only migrations, `age` X25519 backup encryption, and the exclusions from Global Constraints.

- [ ] **Step 3: Write the security and provenance contracts**

`SECURITY.md` must define local vulnerability reporting, secret handling, untrusted-document handling, data classes, external-egress denial defaults, dependency admission, and the clean-room boundary. The TOML registries use stable arrays named `[[tool]]`, `[[source]]`, `[[fixture]]`, and `[[github_app]]`; every entry contains `name`, `origin`, `version_or_digest`, `license_or_rights`, `data_class`, `owner`, `permissions`, `egress`, `evaluation`, and `rollback`.

Seed `governance/tools.toml` with Rust 1.96.1, Cargo 1.96.1, SQLite, Superpowers 6.3.0, and graph-engineering commit `cfacb56a05a31ba69bf84d0b8b00f5ce463127ef`. Seed the four app names from design section 11 with `disposition = "owner_decision_required"`; do not add workflow or secret configuration.

- [ ] **Step 4: Publish the task graph and future roadmap**

`docs/architecture/task-graph.md` must reproduce the dependency graph above, identify Codex as merge owner, cap active workers at four, require one writer per path, use a separate reviewer for every task, cap each review loop at five rounds, and place human gates before login, app-permission changes, push, merge, publish, deploy, or destructive actions.

`docs/roadmap.md` must define these gated outcomes: 0.1 trust foundation; 0.2 source registry and Division 23 taxonomy; 0.3 sheet inventory and verified scale; 0.4 schedule extraction and bidirectional plan-tag reconciliation; 0.5 one narrow airside takeoff class with evidence; 0.6 correction, deterministic recalculation, formula-driven Excel, and evidence PDF; 0.7 native Windows/macOS desktop alpha; 0.8 iPhone review plus bounded automation; 1.0 adjudicated production pilot. Each milestone starts only after the prior acceptance evidence is recorded.

- [ ] **Step 5: Verify and commit**

Run: `rg -n "Approved by owner on 2026-08-28|Rust 1.96.1|owner_decision_required|Foundation 0.1|Production pilot" README.md SECURITY.md docs governance`

Expected: all approval, runtime, app-gate, and roadmap terms are present; `rg -n "Proposed for owner review|Production implementation begins only" README.md docs/superpowers/specs` returns no matches.

Stage only the ten paths declared in this task, then run `git diff --cached --check` and inspect `git diff --cached --name-only`.

Commit: `git commit -m "docs: approve foundation and record governance"`

---

