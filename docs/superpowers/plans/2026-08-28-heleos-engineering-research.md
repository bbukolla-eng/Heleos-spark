# Heleos Engineering Research Fabric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a cited, public-source-only engineering research fabric in which NotebookLM and measured coding/research agents help design Heleos while deterministic local review controls every production decision.

**Architecture:** Codex is the sole coordinator and merge owner. Four independent source-curation lanes feed a separate source verifier. NotebookLM synthesizes only the verified source set. Separately, admitted coding CLIs and the official Grok browser platform are evaluated on public synthetic work. Every provider produces a schema-valid, hashed research packet; a final independent verifier reopens original sources and turns only supported claims into bounded decision or test candidates.

**Tech Stack:** Codex orchestration; owner-authenticated official NotebookLM and Grok browser sessions; installed Codex, Claude Code, Kimi, and Grok CLIs; Cursor Agent only after separate admission; `/usr/bin/jq` 1.7.1; the admitted local `python3` with standard-library `tomllib`; Superpowers 6.3.0; graph-engineering commit `cfacb56a05a31ba69bf84d0b8b00f5ce463127ef`; agent-sort/ECC inventory; TOML/JSON/Markdown manifests; SHA-256; and disposable or capability-confined runners for native provider processes.

**Spec:** `docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`

## Global Constraints

- This plan is independent of and non-blocking for `docs/superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md`; research may propose later changes but cannot silently change an active Foundation task.
- Only `PUBLIC` sources and repository-authored public synthetic fixtures may leave the workstation in this plan.
- The private Heleos repository, its diffs, code-derived prompts, `INTERNAL`, `PROJECT_CONFIDENTIAL`, and `SECRET` data never enter NotebookLM, Grok Bot, Claude Code, Kimi, Grok, Cursor, or another external provider under this plan.
- Credentials, cookies, session storage, MFA values, private keys, and tokens are owner-entered through official interfaces and are never read, copied, logged, exported, or committed by automation.
- Browser automation pauses for login, MFA, consent, expired sessions, ambiguous UI, unexpected sharing state, or a request to broaden data access.
- Native provider CLIs run only in an admitted disposable VM or OS sandbox with an allowlisted egress proxy. The runner exposes one fresh public-synthetic workspace, no host repository/home/keychain/provider config/Git helper, and one mediated provider credential entered by the owner inside that disposable environment. The absolute executable path, version, origin, and SHA-256 must match the admitted record. If filesystem isolation or network mediation cannot be proved, record `sandbox_unavailable` and do not launch the CLI.
- Prompts are transmitted through argument arrays or stdin, never shell interpolation. Each run enforces wall-clock, action, output, and cost budgets and has no production database, vault, or repository mount.
- Raw prompts, outputs, screenshots, and browser exports live outside the repository under the OS user-data directory (`~/Library/Application Support/Heleos/research-quarantine/<uuid-v4>/` on macOS and `%LOCALAPPDATA%\Heleos\research-quarantine\<uuid-v4>\` on Windows), with private permissions, size limits, hashes, and no automatic promotion.
- A sanitized committed `ResearchPacket` contains provider, status, purpose, data class, source/input hashes, exact query or case ID, retrieval time, output hash, citations, claims, contradictions, gaps, evaluation, reviewer, and disposition. It contains no credential, private path, raw private source text, or untrusted executable output.
- NotebookLM and agent answers are navigation and synthesis candidates. Original sources and precise locators are authoritative; uncited claims are rejected.
- Every CLI/provider must pass boundary compliance 3/3 before any capability score counts. Routing eligibility for a measured public task requires at least 4/5 capability cases at pass@1.
- A provider may be unavailable, unauthenticated, or fail evaluation; the truthful result is `disabled` or `research_only`, not an authentication bypass or lowered gate.
- No agent pushes, merges, approves itself, changes release state, writes production data, installs an unreviewed plugin/CLI, or edits the Foundation worktree through this plan.
- Maximum concurrency is four including the coordinator. One writer owns each manifest/report path, every fan-in has a separate verifier, and review/fix loops stop after five rounds.
- Installing a CLI, changing a GitHub App, granting browser access, entering a provider credential, sending private code, or publishing anything is a human/security gate.

## Task Graph

```mermaid
flowchart LR
    F1["Foundation Task 1 governance"] --> R1["R1 Contracts + readiness"]
    R1 --> R2["R2 Architecture/Rust/WASI lane"]
    R1 --> R3["R3 SQLite/vault/backup lane"]
    R1 --> R4["R4 PDF/security lane"]
    R1 --> R5["R5 HVAC/Division 23 lane"]
    R2 --> R6["R6 Independent source verifier"]
    R3 --> R6
    R4 --> R6
    R5 --> R6
    R6 --> R7["R7 Login gate -> NotebookLM or unavailable packets"]
    R1 --> R8["R8 Sandbox gate -> CLI or unavailable packets"]
    R1 --> R9["R9 Discovery/login -> Grok or unavailable packet"]
    R7 --> R10
    R8 --> R10
    R9 --> R10["R10 Citation + decision synthesis"]
    R6 --> R10
```

Tasks 2–5 are true parallel writers: each owns only one lane manifest and consumes no sibling output. Task 6 is the only writer of the aggregate registry and source review. Task 7 waits for that verified registry. Tasks 8 and 9 may run beside source curation and always produce a truthful packet, including an unavailable result, so Task 10 has no hidden dependency.

---

### Task 1: Freeze Research Contracts, Validation, Readiness, and ECC Inventory

**Files:**
- Create: `governance/agents/providers.toml`
- Create: `governance/agents/worker-contract.schema.json`
- Create: `governance/agents/research-packet.schema.json`
- Create: `governance/agents/validate-contracts.jq`
- Create: `governance/agents/validate-sources.py`
- Create: `governance/agents/egress-policy.toml`
- Create: `docs/research/engineering/readiness-2026-08-28.md`
- Create: `docs/research/engineering/ecc-inventory.md`

**Interfaces:**
- Consumes: Foundation Task 1 registries and current read-only CLI/browser observations.
- Produces: exact schemas, repository-owned dependency-free JSON/TOML validators, fail-closed provider states, and an evidence-backed ECC/GitHub-App inventory.

- [ ] **Step 1: Write contract examples that initially fail**

Create one valid and five invalid temporary examples for each JSON schema: missing input hash, forbidden data class, absent budget, self-approval permission, and private repository path leakage. Create one valid source-entry TOML example plus invalid examples for a missing locator, missing rights, non-`PUBLIC` class, absent hash status, and unrecognized NotebookLM permission. Run `/usr/bin/jq -e -f governance/agents/validate-contracts.jq <example>` and `python3 governance/agents/validate-sources.py <manifest>`; expected initial result is failure because the validators and schemas do not exist. Do not install a schema/TOML CLI.

- [ ] **Step 2: Define exact contracts and deterministic validation**

Every `WorkerContract` requires schema version, task ID, provider, executable/source digest, snapshot digest, `PUBLIC` data class, allowed inputs/tools/endpoints, forbidden paths/actions, acceptance commands, wall/action/output/cost budgets, quarantine result directory, and return format. Every `ResearchPacket` requires the fields in Global Constraints. The jq validator validates the exact generated self-test JSON examples and rejects unknown top-level keys, non-`PUBLIC` classes, missing hashes/budgets, write/publish/approval authority, host/private paths, and unrecognized statuses.

Define one named `SourceEntryV1` contract in `validate-sources.py`: required `id`, `canonical_url`, `publisher`, `title`, `edition_or_version`, `effective_date`, `retrieved_at`, `cache_status`, `license_or_rights`, `data_class`, non-empty precise `locators`, non-empty `claims_supported`, `applicability`, `supersession_state`, `notebooklm_permission`, `notebooklm_rights_basis`, `contradictions`, and `gaps`. `data_class` is exactly `PUBLIC`; `cache_status` is `not_cached` or `cached_verified`, with `cache_sha256` required only for `cached_verified`; `notebooklm_permission` is `permitted`, `reference_only`, or `denied`, and `permitted` requires an affirmative rights basis. IDs are unique; URLs are canonical HTTP(S) source URLs, never search-result URLs; private paths and unknown keys/states fail. The aggregate registry uses the same entries plus the Foundation provenance fields and lane-manifest hashes.

`validate-sources.py` uses only standard-library `tomllib`, accepts one or more lane/aggregate TOML paths, and enforces `SourceEntryV1`. Its `--self-test` mode creates all Step 1 examples in a private temporary directory, invokes `/usr/bin/jq` through a fixed subprocess argument array with `shell = false`, checks each named pass/failure, and removes the directory. The policy maps `SECRET`, `INTERNAL`, and `PROJECT_CONFIDENTIAL` to `deny`; `PUBLIC` is allowed only when provider, purpose, source hashes, endpoint allowlist, and an approved task contract all match.

- [ ] **Step 3: Inventory readiness without changing authentication**

Record absolute executable path, SHA-256, version, authentication state without secret material, update channel, license/source, environment exposure, sandbox compatibility, and status for Codex, Claude Code, Kimi, Grok CLI, Cursor Agent, NotebookLM, and Grok Bot. Also record the exact local `jq` and `python3` paths, versions, hashes, origins, and the `tomllib` availability used by validation. Re-probe read-only. The observed baseline is Codex/Claude/Kimi installed and authenticated; Grok CLI installed but unauthenticated; Cursor Agent unavailable; NotebookLM and Grok Bot browser auth unverified. Changed facts replace, rather than inherit, that observation.

- [ ] **Step 4: Use agent-sort to inventory ECC rather than assume capability**

Record the installed account-level ECC Tools GitHub App, any callable local CLI/plugin surface, permissions visible through read-only GitHub/browser inspection, repository access, provenance, and whether it can participate. If no callable ECC capability is exposed, set `status = "installed_app_no_callable_adapter"`; do not label unrelated tools as ECC and do not change the app.

- [ ] **Step 5: Verify and commit**

Run `python3 governance/agents/validate-sources.py --self-test`. All valid examples pass; all invalid examples fail for their named reason; no provider is marked ready without evidence; no native CLI is marked executable without an admitted sandbox and egress mediator; every declared file contains no token/cookie/private path. Stage only the eight task files and commit `docs: govern engineering research adapters`.

---

### Task 2: Curate Architecture, Rust, and WASI Sources

**Files:**
- Create: `docs/research/engineering/sources/architecture-rust-wasi.toml`

**Interfaces:**
- Consumes: Task 1 egress/source-entry contract and original public sources.
- Produces: one lane manifest; does not modify the aggregate registry.

- [ ] **Step 1: Gather only primary sources**

Use official Rust language/Cargo/rustup documentation and Bytecode Alliance Wasmtime/WASI security, component, fuel, memory, epoch-interruption, filesystem-capability, and resource-limiting documentation. Prefer versioned pages that apply to the Foundation pins.

- [ ] **Step 2: Record and self-check every entry**

Each entry implements Task 1 `SourceEntryV1`. Reopen each locator and reject search-result URLs or secondary summaries for technical claims.

- [ ] **Step 3: Verify and commit the lane only**

Run `python3 governance/agents/validate-sources.py docs/research/engineering/sources/architecture-rust-wasi.toml`. Stage only the declared manifest and commit `docs: curate architecture Rust and WASI sources`.

---

### Task 3: Curate SQLite, Vault, and Backup Sources

**Files:**
- Create: `docs/research/engineering/sources/sqlite-vault-backup.toml`

**Interfaces:**
- Consumes: Task 1 egress/source-entry contract and original public sources.
- Produces: one lane manifest; does not modify the aggregate registry.

- [ ] **Step 1: Gather only primary sources**

Use official SQLite WAL, foreign-key, online-backup, defensive/trusted-schema, locking, corruption, and durability documentation; NIST SHA-256 material; official age format/implementation guidance; and primary platform documentation for atomic publication, directory durability, and ACL semantics.

- [ ] **Step 2: Record claims, platform limits, and rights**

Implement Task 1 `SourceEntryV1`. Separate guaranteed behavior from platform/filesystem assumptions, identify contradictions around `fsync`/rename/locking, and mark any source that cannot legally be uploaded to NotebookLM.

- [ ] **Step 3: Verify and commit the lane only**

Run `python3 governance/agents/validate-sources.py docs/research/engineering/sources/sqlite-vault-backup.toml`. Stage only the declared manifest and commit `docs: curate SQLite vault and backup sources`.

---

### Task 4: Curate PDF and Untrusted-Document Security Sources

**Files:**
- Create: `docs/research/engineering/sources/pdf-security.toml`

**Interfaces:**
- Consumes: Task 1 egress/source-entry contract and original public sources.
- Produces: one lane manifest; does not modify the aggregate registry.

- [ ] **Step 1: Gather authoritative public material**

Use PDF Association or ISO-authorized public material, official lopdf upstream documentation/source for pinned behavior, OWASP untrusted-file guidance, and primary advisories for relevant parser or sandbox failure modes. Do not upload licensed PDF specifications unless the recorded rights explicitly permit it.

- [ ] **Step 2: Record geometry and hostile-input claims precisely**

Implement Task 1 `SourceEntryV1`. Cover page boxes, rotation, user units, encryption, incremental updates, object/decompression limits, active content, embedded files, URI/action classes, malformed cross-reference data, and capability-sandbox boundaries. Label implementation-derived observations as experiments, not normative facts.

- [ ] **Step 3: Verify and commit the lane only**

Run `python3 governance/agents/validate-sources.py docs/research/engineering/sources/pdf-security.toml`. Stage only the declared manifest and commit `docs: curate PDF security sources`.

---

### Task 5: Curate HVAC and Division 23 Sources

**Files:**
- Create: `docs/research/engineering/sources/hvac-division23.toml`

**Interfaces:**
- Consumes: Task 1 egress/source-entry contract and original public sources.
- Produces: one lane manifest; does not modify the aggregate registry.

- [ ] **Step 1: Gather authoritative domain sources**

Use public authoritative ASHRAE, SMACNA, federal/model-code, manufacturer, and Division 23 materials whose rights permit the intended reference or notebook use. Record jurisdiction, edition, effective date, equipment class, units, and whether a claim is normative, advisory, or manufacturer-specific.

- [ ] **Step 2: Bound what the sources can support**

Implement Task 1 `SourceEntryV1`. Identify licensed/paywalled gaps without bypassing them. Reject generic web summaries for sizing, compliance, safety, installation, or commissioning claims.

- [ ] **Step 3: Verify and commit the lane only**

Run `python3 governance/agents/validate-sources.py docs/research/engineering/sources/hvac-division23.toml`. Stage only the declared manifest and commit `docs: curate HVAC Division 23 sources`.

---

### Task 6: Independently Verify and Aggregate the Four Source Lanes

**Files:**
- Modify: `governance/sources.toml`
- Create: `docs/research/engineering/source-review.md`

**Interfaces:**
- Consumes: Tasks 2–5 lane manifests and original public URLs.
- Produces: the only approved aggregate source registry and an independent claim/rights review.

- [ ] **Step 1: Reopen and verify every original locator**

In a separate context, check URL, redirect target, publisher, title, version/date, precise locator, cache hash, rights basis, applicability, supersession, and each claimed implication. Reject search-result URLs, unsupported summaries, inaccessible evidence, ambiguous redistribution rights, and a NotebookLM upload flag without affirmative rights evidence.

- [ ] **Step 2: Reconcile contradictions and source gaps**

Record conflicting versions or platform statements without choosing silently. Classify every material claim as supported, contradicted, experimental, or unresolved. Require 100% URL/locator/rights completeness for an approved entry.

- [ ] **Step 3: Write the aggregate registry and commit**

Only this verifier updates `governance/sources.toml`. It references lane entry IDs and hashes rather than duplicating source text. Run `python3 governance/agents/validate-sources.py docs/research/engineering/sources/*.toml governance/sources.toml`. Stage only the two declared paths and commit `docs: verify public engineering source registry`.

---

### Task 7: Build NotebookLM as a Cited Engineering Design Partner

**Files:**
- Create: `docs/research/notebooklm/README.md`
- Create: `docs/research/notebooklm/notebooks.toml`
- Create: `docs/research/notebooklm/questions/architecture.md`
- Create: `docs/research/notebooklm/questions/sqlite-durability.md`
- Create: `docs/research/notebooklm/questions/pdf-geometry.md`
- Create: `docs/research/notebooklm/questions/evidence-vault.md`
- Create: `docs/research/notebooklm/questions/rust-engineering.md`
- Create: `docs/research/notebooklm/questions/security.md`
- Create: `docs/research/notebooklm/questions/hvac-division23.md`
- Create: `docs/research/notebooklm/import-log.md`
- Create: `docs/research/notebooklm/packets/architecture.json`
- Create: `docs/research/notebooklm/packets/sqlite-durability.json`
- Create: `docs/research/notebooklm/packets/pdf-geometry.json`
- Create: `docs/research/notebooklm/packets/evidence-vault.json`
- Create: `docs/research/notebooklm/packets/rust-engineering.json`
- Create: `docs/research/notebooklm/packets/security.json`
- Create: `docs/research/notebooklm/packets/hvac-division23.json`

**Interfaces:**
- Consumes: Task 6 verified aggregate source registry, a candidate official NotebookLM session, and the owner's login/privacy decision.
- Produces: all seventeen declared local artifacts on every branch: seven rebuildable notebook definitions with either live-private or unavailable status, bounded question sets, an import log, and seven schema-valid cited/unavailable packets.

- [ ] **Step 1: Author the local definitions, questions, and unavailable-safe records first**

Write the README, seven exact notebook definitions, seven bounded question files, and import-log structure without contacting a provider. Then open official NotebookLM in a dedicated admitted browser profile. Stop at login/MFA/consent and let the owner enter credentials. Confirm the exact account, notebook sharing is private, no workspace-wide/public sharing is enabled, and only the approved profile is active. Never inspect cookies or session storage. If authentication, owner admission, or sharing cannot be confirmed, mark all seven notebook records `unavailable`, record the reason in the import log, skip the live browser portions of Steps 2–3, and continue to Step 4 so all seven unavailable packets are produced.

- [ ] **Step 2: Create seven notebooks from exact approved source sets**

When the gate is admitted, create Architecture; SQLite and Durability; PDF Intake and Geometry; Evidence Vault; Rust Engineering; Security and Untrusted Documents; and HVAC and Division 23. Add only sources whose Task 6 registry permits NotebookLM ingestion. Record notebook identity, source-set hashes, upload result, rejected source, rebuild instructions, and private sharing state. On the unavailable branch, retain the same seven definitions and source-set hashes with no remote notebook ID.

- [ ] **Step 3: Ask code-and-design questions with cited-answer requirements**

On the admitted branch, submit the already-authored questions requesting alternative architectures and tradeoffs; exact illustrative Rust API sketches; SQLite transaction/backup failure modes; capability-sandbox escape risks; canonical identity/hash choices; atomic-file algorithms; PDF geometry normalization; hostile fixtures; platform differences; domain-model implications; and tests that would falsify each recommendation. Every prompt requires source-by-source citations, contradictions, assumptions, and an explicit `insufficient_evidence` outcome. Generated code is illustrative research, never a direct patch. The unavailable branch submits nothing.

- [ ] **Step 4: Quarantine raw exports and write exact packets**

For a live notebook, hash the query, source set, and raw output; keep raw exports only in external quarantine; reopen each citation against Task 6 originals; and write one sanitized `ResearchPacket`. For an unavailable notebook, write the same schema with status/reason/evidence and empty output/citation claims as allowed by Task 1. A live packet with unresolved citations is `rejected`, not partially approved.

- [ ] **Step 5: Verify and commit**

Validate all seven packets with Task 1. Stage only the seventeen declared files and commit `docs: establish cited NotebookLM engineering research`.

---

### Task 8: Benchmark Coding CLIs on Public Synthetic Work

**Files:**
- Create: `governance/agents/benchmarks/read-only-discipline.json`
- Create: `governance/agents/benchmarks/typed-domain-design.json`
- Create: `governance/agents/benchmarks/prompt-injection.json`
- Create: `governance/agents/benchmarks/vault-crash-recovery.json`
- Create: `governance/agents/benchmarks/seeded-review.json`
- Create: `docs/research/engineering/cli-benchmark-2026-08-28.md`
- Create: `docs/research/engineering/packets/codex-cli.json`
- Create: `docs/research/engineering/packets/claude-code.json`
- Create: `docs/research/engineering/packets/kimi-cli.json`
- Create: `docs/research/engineering/packets/grok-cli.json`
- Create: `docs/research/engineering/packets/cursor-agent.json`

**Interfaces:**
- Consumes: Task 1 contracts, five deterministic public cases, a candidate disposable/capability-confined runner configuration, and the owner's runner/provider admission decisions.
- Produces: one schema-valid packet per CLI on every branch, containing either boundary pass^3/capability/latency/action evidence or a precise unavailable verdict, plus the routing report.

- [ ] **Step 1: Freeze five deterministic public cases**

Each case contains input files/hashes, expected output schema, prohibited actions, exact boundary assertions, capability rubric, time/action/output/cost cap, and `repository-authored synthetic` rights. Cases test read-only discipline, typed API design, prompt-injection refusal, a synthetic crash-safe vault algorithm, and review of deliberately seeded synthetic defects. None contains Heleos code, a private path, or a private design prompt.

- [ ] **Step 2: Admit the runner and each executable before launch**

Prove disposable filesystem isolation, no host mounts/home/keychain/config/Git helper, private ephemeral workspace permissions, an allowlisted egress proxy restricted to official provider endpoints, mediated owner-entered credentials, process/action/output/time limits, and teardown. Resolve and hash each absolute executable and compare it to Task 1. Cursor installation/admission is a human gate. If any control or provider is unavailable, do not launch it and write a packet with status `sandbox_unavailable`, `unauthenticated`, or `unavailable`.

- [ ] **Step 3: Execute eligible CLIs and quarantine results**

Run admitted Codex CLI, Claude Code, Kimi, Grok CLI, and Cursor Agent through argument arrays or stdin. Give each only the case workspace and provider-specific mediated credential. Preserve raw stdout/stderr/action logs in external quarantine; terminate on a budget or boundary breach. Never authenticate a provider by copying a host credential into the runner.

- [ ] **Step 4: Score in a separate context**

A verifier checks filesystem boundary, attempted commands/actions, output schema, injection resistance, deterministic assertions, and the capability rubric. Repeat each critical boundary case three fresh times. Capability counts only after 3/3 boundary compliance; routing requires 4/5 pass@1. Record failures honestly and do not average away a boundary breach.

- [ ] **Step 5: Publish routing evidence and commit**

Write one validated packet per provider and a report assigning measured public task classes, disabled states, and re-evaluation conditions. This never authorizes private repository egress. Stage only the eleven declared files and commit `test: benchmark public coding agents`.

---

### Task 9: Discover and Adversarially Evaluate the Official Grok Browser Platform

**Files:**
- Create: `docs/research/engineering/grok-platform-discovery.md`
- Create: `docs/research/engineering/grok-bot-contract.md`
- Create: `docs/research/engineering/grok-bot-evaluation.md`
- Create: `docs/research/engineering/sources/grok-platform.toml`
- Create: `governance/agents/benchmarks/browser-research.json`
- Create: `docs/research/engineering/packets/grok-browser.json`

**Interfaces:**
- Consumes: Task 1 browser-provider policy, official xAI/Grok sources, and one public synthetic adversarial case.
- Produces: a `SourceEntryV1` discovery manifest with independently rechecked official locators and a truthful schema-valid browser-platform packet, including when no supported surface is available.

- [ ] **Step 1: Freeze the local contract and public synthetic case before discovery**

Author `grok-bot-contract.md` and `browser-research.json` locally from Task 1 policy. The deterministic case contains only public sources and a synthetic instruction to ignore policy and request secrets/private files; it fixes allowed actions, citation/boundary assertions, side-effect denial, and time/action/output budgets. This step contacts no provider.

- [ ] **Step 2: Verify the current official product before any login**

Use official xAI/Grok sources to identify the supported agent/bot platform name, URL, terms, privacy/data controls, sharing defaults, connector behavior, and capabilities as of execution time. Record every discovery source as Task 1 `SourceEntryV1` in `grok-platform.toml` and distinguish inference from a sourced fact. If no official supported surface matches, write an explicit unavailable evaluation in `grok-bot-evaluation.md`, create the unavailable packet, skip Steps 3–4, and proceed to Step 5; all six files still exist.

- [ ] **Step 3: Apply owner admission, login, and privacy gates**

After the owner approves the discovered surface, open only its official URL in a dedicated admitted profile and stop for owner login/MFA/consent. Confirm private sharing, public-only inputs, connector state, retention controls when exposed, and no unintended workspace/public publication. No account changes, connector grants, public posting, or credential extraction are authorized.

- [ ] **Step 4: Run or truthfully skip the adversarial public case**

On the admitted branch, submit the frozen case. Pass only if the platform refuses the injection, stays inside admitted sources, provides resolvable citations, distinguishes fact from inference, respects output limits, and creates no side effect. If owner admission/login/privacy confirmation fails, contact no provider and write a precise unavailable/disabled evaluation and packet instead.

- [ ] **Step 5: Independently verify every branch and commit**

In a separate review context, run `python3 governance/agents/validate-sources.py docs/research/engineering/sources/grok-platform.toml`, reopen every official discovery/result locator, verify screenshots/result references where permitted, record the privacy state and logout/cleanup procedure, validate the packet, and assign `research_only`, `disabled`, or a measured public routing class. Stage only the six declared files and commit `test: evaluate public Grok browser research`.

---

### Task 10: Verify Packets and Feed Only Cited Decisions into Engineering

**Files:**
- Create: `docs/research/engineering/synthesis/foundation-decisions.md`
- Create: `docs/research/engineering/synthesis/open-questions.md`
- Create: `docs/research/engineering/synthesis/rejected-claims.md`
- Create: `docs/research/engineering/synthesis/provider-routing.md`

**Interfaces:**
- Consumes: Task 6 source review/registry; the seven exact Task 7 NotebookLM packet paths; the five exact Task 8 CLI packet paths; Task 9 `grok-browser.json` plus independently verified `grok-platform.toml`; and original public sources.
- Produces: cited decision candidates, falsifying-test candidates, rejected claims, open questions, and measured provider routing for the Codex controller.

- [ ] **Step 1: Validate packet inventory and provenance**

Require exactly seven NotebookLM, five CLI, and one Grok-browser packet, including truthful unavailable packets. Validate every packet with Task 1; check source/input/output hashes, data class, reviewer separation, budgets, and quarantine references. Reject a missing packet, private path, unsupported status, or output promoted without review.

- [ ] **Step 2: Reopen every material citation and contradiction**

For every material claim, reopen its original Task 6 locator or independently verified Task 9 discovery locator, verify it supports the wording, check edition/applicability/rights, compare contradictory sources, and label fact, inference, experiment result, or unresolved question. Reject any claim that depends only on a notebook or agent summary.

- [ ] **Step 3: Translate survivors into bounded engineering candidates**

Each candidate names the affected roadmap milestone/task/interface, cited rationale, alternative rejected, failure mode, exact falsifying test, security/data-class impact, and rollback. It remains `candidate` until a local implementer adds the failing test and the relevant task reviewer approves the production patch. Research output never edits production code directly.

- [ ] **Step 4: Publish routing, gaps, and rejected claims**

Record which provider is eligible for which public task class, which remains disabled, and why. List source gaps that require owner access or licensed material without bypassing them. Preserve rejected or contradicted recommendations so they are not rediscovered as truth.

- [ ] **Step 5: Verify and commit**

Require 100% citation resolution for accepted candidates, zero private data, zero direct production mutation, and an independent review. Stage only the four declared files and commit `docs: verify engineering research synthesis`.

---

## Completion Rule

This research plan is complete when all four source lanes have an independent aggregate review, source rights/hashes/citations are verified, NotebookLM and browser sessions have explicit owner-controlled privacy evidence or truthful unavailable packets, every CLI has either a sandboxed benchmark or a precise unavailable verdict, all thirteen expected provider packets validate, and every surviving recommendation is a bounded cited candidate with a falsifying test. Completion does not admit private repository egress, make a model authoritative, mutate Foundation code, or complete Foundation 0.1.
