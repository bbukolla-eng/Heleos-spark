# Heleos roadmap

The [approved Foundation design](superpowers/specs/2026-08-26-heleos-spark-foundation-design.md) is binding. The quarantined predecessor and all its artifacts remain outside this clean-room project: no inspection, comparison, import, or reuse. Introduced sources, dependencies, datasets, models, and assets require independent provenance and rights records.

This roadmap defines gated outcomes, not dates or completed-work claims. [CURRENT_STATUS.md](../CURRENT_STATUS.md) owns volatile progress; [AGENTS.md](../AGENTS.md) owns assignments and handoffs. Product implementation begins only after the preceding milestone is accepted and the next scoped implementation plan is recorded. Isolated research and engineering preparation may proceed as specified below without promoting their outputs into the product.

Every milestone's acceptance record binds the candidate commit, frozen input hashes, rule/software/model versions, exact commands and results, platform, evidence locations, unresolved limitations, and owner disposition. Deterministic results must reproduce from those inputs. Evaluation thresholds and the adjudicated dataset are frozen before a bakeoff or pilot, never selected after seeing results. A material input or candidate change invalidates only the affected evidence and requires its checks again.

Models and workers propose; deterministic controls and approved humans authorize. Evidence bytes remain immutable, quantities retain source lineage, and uncertainty blocks the affected item or release. Default product operation remains local and offline. Approved public research may leave the machine with source, rights, and egress logging; internal or project-confidential data requires the design's explicit policy approval, and secrets never enter prompts or artifacts.

## CURRENT

### 0.1 — Trust foundation and release acceptance

- **Entry gate:** Owner-approved clean-room design and Foundation implementation plan; scoped local implementation is already authorized.
- **Build outcome:** Shared Rust core, typed contracts, SQLite/WAL metadata and migrations, immutable SHA-256 vault, deterministic PDF intake, audited idempotency and crash recovery, encrypted backup/restore, and governed supply-chain verification.
- **Current evidence:** Baseline `af526a3c9c7ad93f360b6629e9b592a81787b341` is the documentation-only child of Task 10 local implementation checkpoint `6157458c6566d8ad26a2ec2c6ba1c7d8a697e2b6`. It contains the locally integrated Tasks 7–9 and the recorded native macOS ARM64 `SUPPLY_CHAIN_LOCAL_PASS`, including the complete 405-package/1,035-edge graph and reproducible SBOM. Neither commit is `release_candidate_sha`. Foundation 0.1 remains unaccepted; workflow publication, remote CI, and native Windows/NTFS acceptance are not evidenced here.
- **Deterministic acceptance:** The public/synthetic fixture double-ingest must preserve original bytes and stable hashes, produce one canonical content object/revision and two auditable attempts, preserve page identity/order/geometry/lineage, and pass migration/recovery, interrupted-ingest, corruption/encryption, evidence retrieval, encrypted restore, and default network-denial checks. The full [Task 10 contract](superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md#task-10-gate-supply-chain-windows-ci-and-foundation-release-evidence) additionally requires the ordered gates below and clean-checkout evidence for the same candidate SHA.
- **Parallel work allowed:** Public source curation, frozen-fixture preparation, and the isolated agent-protocol branch below. Preserve completed Tasks 7–9, the one-time lock normalization, the accepted PDF source closure, and admitted SBOM. Continue authorized independent local work while an affected human/platform gate is open.
- **Non-goals / blocked authority:** No broad rules/pricing engine, full desktop UI, model training, bot roster, or cloud deployment. No new review rounds or reviewer dispatches under the owner's current direction. No worker may bypass an App decision, push, publish workflows, claim Windows parity, or declare acceptance from a local pass.

The first unfinished finish line is the App disposition. The remaining release sequence is fixed:

| Gate | Required action and evidence | State at `af526a3` |
| --- | --- | --- |
| 1. GitHub Apps | Owner inventories permissions/access and records retain, restrict, suspend, or remove for Azure Pipelines, AWS Connector for GitHub, Amazon Q Developer, and ECC Tools in the [App registry](../governance/github-apps.toml), with decision evidence. | All four are `owner_decision_required`; workflow writing is blocked. The registry itself does not configure account installations. |
| 2. Workflow and candidate | After all dispositions, prepare the Task 10 no-secret workflow with pinned checkout/toolchain, read-only contents permission, and governed macOS/Windows entry points; pass scoped checks and commit/freeze `release_candidate_sha`. Exclude the later dossier. | No workflow or release-candidate SHA is claimed. The local implementation checkpoint must not be relabeled as the candidate. |
| 3. Owner-authorized Git handoff | Controller receives separate owner authorization for the exact branch/candidate push and remote CI. Resolve the recorded divergent remote history through that handoff; local integration grants no push or history-rewrite authority. | Open; no remote publication is evidenced. |
| 4. Same-SHA platform attestation | macOS and native Windows CI check out and attest the exact `release_candidate_sha`. Preserve immutable run/job URLs and all required native NTFS permission, no-replace publication, retained-handle, crash, restore, and supply-chain results. | Open. Local macOS checks, cross-compilation, or simulated Windows cases cannot supply this evidence. |
| 5. Acceptance dossier | Only after every required gate passes, commit the documentation-only `docs/verification/foundation-0.1.md`, recording the tested SHA, evidence, adjudications, limitations, and 0.2 entry gate. Its later documentation commit is not the CI-tested artifact. | Open. The dossier may say `Accepted` only when every required local, security, App, and platform gate is evidenced. |

A discovered production/helper defect returns to its owning implementation task under the Task 10 contract; affected verification and candidate/platform attestations must be repeated for the repaired SHA. Task 10 does not acquire production-repair authority. The dossier must retain the documented Windows power-loss namespace-durability limitation.

### Parallel engineering — Agent coordination foundation

The [agent-coordination implementation plan](superpowers/plans/2026-09-08-agent-coordination-foundation.md) runs on `build/agent-control-foundation` in `.worktrees/agent-control-foundation`, branched from `af526a3c9c7ad93f360b6629e9b592a81787b341`. Its output is an engineering candidate and must not merge into or alter the Foundation 0.1 candidate before that candidate is accepted.

- **Entry and outcome:** `heleos-worker-protocol` and `heleos-worker-runner` are implemented on the isolated branch using already-pinned dependencies. They provide strict task/handoff JSON, canonical SHA-256 identities, an exact-base no-hardlink clone, direct provider argv/stdin invocation, elapsed-time/process-group control, bounded logs, independent changed-path inventory, retained evidence, and validated pending handoffs. A bounded Kimi stdin adapter bridges its argument-only prompt interface without a shell.
- **Acceptance evidence:** Protocol tests pass 15 cases. Runner tests pass 24 library/integration and 2 CLI cases, and the Kimi adapter passes 19 black-box cases. Locked/offline workspace check, strict workspace Clippy, formatting, reproducible PDF-guest build, and provenance pass. Authenticated Claude Code and Kimi each completed one PUBLIC-only exact-scope write; controller content checks and completed protocol handoffs are retained under `crates/heleos-worker-runner/evidence/`.
- **Routing and authority:** Claude Code and Kimi are installed, authenticated, and now proven write-capable for bounded PUBLIC tasks through the runner. Grok Build and Cursor Agent remain installed but unauthenticated and therefore disabled. NotebookLM and GrokBots/Athena remain research-only. Every dispatch still requires a validated task, explicit provider/data/egress decision, relevant instruction hashes, and controller acceptance; secrets are rejected.
- **Later tooling sequence:** After 0.1 acceptance and a scoped integration plan, persist validated identities through a migration; then prove deterministic leases, idempotent claims, deadlines, cancellation, and action/cost accounting. Add macOS host-write containment and a Windows Job Object backend before making containment or parity claims, followed by quarantined patch/artifact ingestion with exact-base application and test-gated promotion. Add status/approval UI only after the controller behavior is proven headlessly.
- **Non-goals / blocked authority:** The runner is not an OS filesystem/network sandbox, does not guarantee external cancellation or Windows execution, and does not attest provider-internal actions or cost. It never executes acceptance commands, self-approves, merges, pushes, writes product truth, changes release state, or grants implicit egress permission. Parallel tooling cannot change the frozen 0.1 source closure, SBOM, release scripts, workflow gate, or App registry.

## FUTURE

The product order is 0.2 → 0.3 → 0.4 → 0.5 → 0.6 → 0.7 → 0.8 → 1.0. Every entry gate includes its predecessor's recorded acceptance. Preparatory work listed as parallel remains isolated and cannot satisfy that gate by itself.

### 0.2 — Source registry, Division 23 taxonomy, and evaluation baseline

- **Entry gate:** Accepted 0.1 dossier; approved source/rights scope and taxonomy plan. Resolve the Task 10 dependency-duplicate convergence obligation and re-adjudicate the secret-scan baseline before 0.2 begins, preserving historical evidence.
- **Build outcome:** Separate knowledge-lane source snapshots with class, rights, jurisdiction, edition/effective date, retrieval date, hash, precise locator, applicability, and supersession; versioned Division 23 vocabulary; candidate-rule promotion records; frozen dataset and model/worker bakeoff harness.
- **Deterministic acceptance:** Rebuild the source manifest from hashes; reject missing rights/citations and conflicting or retired applicability; replay taxonomy mappings and supersession fixtures. Bakeoff manifests bind licensed data, splits, pinned assets/runtime, leakage checks, and predeclared accuracy/latency/memory criteria; repeated scoring yields the same reported metrics from saved outputs.
- **Parallel work allowed:** Cited public research, quarantined model/dataset candidates, and sheet/scale fixture annotation.
- **Non-goals / blocked authority:** No source or notebook answer directly changes quantities. Candidate rules need citations, tests, independent rule review, rollback identity, and owner promotion. No unpinned model, private drawing upload, live pricing engine, or general model training.

### 0.3 — Sheet inventory and verified scale

- **Entry gate:** Accepted 0.2 registry/taxonomy and evaluation baseline; frozen sheet/coordinate/scale fixtures and approved extraction scope.
- **Build outcome:** Revision-linked sheet inventory, page dimensions/rotation/transforms, and declared/detected/calibrated/verified/rejected scale facts with evidence and reviewer decisions.
- **Deterministic acceptance:** Golden fixtures reproduce sheet identities, page order, coordinate round trips, and calibrated distances in defined units, including rotated pages and differing sheet/view scales. Missing, conflicting, rejected, or unverified scale must block every dependent measurement; a correction invalidates only dependent results.
- **Parallel work allowed:** Schedule/tag annotation and bounded OCR/layout bakeoffs on admitted fixtures.
- **Non-goals / blocked authority:** No inferred scale becomes verified automatically; no scale-dependent quantity on an unverified region, full takeoff engine, or required cloud processing.

### 0.4 — Schedule extraction and bidirectional plan-tag reconciliation

- **Entry gate:** Accepted 0.3 sheet/scale contract; frozen adjudicated schedule/tag fixtures and supported schedule fields in the scoped plan.
- **Build outcome:** Evidence-linked equipment schedule rows and plan tags, normalized identities, and reconciliation edges in both directions, with duplicate, missing, revision-conflicting, and ambiguous cases routed to decision.
- **Deterministic acceptance:** Expected rows, field values, and edge sets match golden fixtures; every extracted field resolves to its source crop/text/coordinates. Tests identify schedule-only and plan-only tags, prevent duplicate authoritative matches, and preserve unresolved conflicts through replay and revision changes.
- **Parallel work allowed:** Narrow airside-class annotation and geometry experiments against accepted coordinate contracts.
- **Non-goals / blocked authority:** No silent fuzzy-match acceptance, schedule-derived overwrite of measured M-drawing quantities, automatic resolution of contractual precedence, or expansion to every schedule type.

### 0.5 — One evidence-backed airside takeoff class

- **Entry gate:** Accepted 0.4 reconciliation; owner-approved single airside class, units, inclusion/exclusion rules, and adjudicated fixture set recorded in the implementation plan before extraction begins. Selection is confined to one class; broader system scope requires a later decision.
- **Build outcome:** Deterministic quantities for that class with document revision, verified scale where needed, evidence crops/overlays, geometry, rule/run versions, and separate measured, rule-derived, context-required, allowance, clarification, and accepted-estimate states.
- **Deterministic acceptance:** Frozen geometry/rule inputs reproduce exact expected quantities and evidence links; model proposals meet the predeclared bakeoff thresholds before admission. Missing scale, ambiguous geometry/tags, broken lineage, or material context conflict blocks affected lines. Replay cannot duplicate an authoritative result.
- **Parallel work allowed:** Correction/export fixtures and isolated research on later classes.
- **Non-goals / blocked authority:** No all-airside claim, unsupported routing/topology, pricing/labor expansion, guessed hidden lengths, or model-produced final quantities. Research on a second class does not admit it.

### 0.6 — Correction, recalculation, and estimator exports

- **Entry gate:** Accepted 0.5 class and evidence contract; approved correction dependencies, workbook schema, and evidence-PDF layout.
- **Build outcome:** Audited before/after corrections, deterministic recalculation of affected lines, estimator-usable Excel with live formulas, and an evidence PDF that resolves every material line to its original document evidence.
- **Deterministic acceptance:** Correct/replay/undo fixtures preserve history and recompute expected dependent values without changing unrelated lines. Exported formulas and evaluated totals match core results in declared units/rounding; PDF locators/hashes resolve. Revision, stale-evidence, missing-evidence, and unresolved-material-conflict cases block affected final export content.
- **Parallel work allowed:** Native PDF-rendering, local-API/IPC, packaging, and keychain proofs on macOS Apple Silicon and Windows.
- **Non-goals / blocked authority:** No spreadsheet-only calculation authority, formula replacement by unexplained constants, historical evidence overwrite, automatic correction-to-rule promotion, or final bid release by an agent.

### 0.7 — Native Windows/macOS desktop alpha

- **Entry gate:** Accepted 0.6 workflow; cross-platform proof of PDF rendering, versioned local API/IPC, packaging, and native behavior. Record shell/adapter technology choices from that proof before full UI implementation.
- **Build outcome:** Native Windows and Apple Silicon macOS clients using the same core, with parity for intake, drawing/takeoff review, evidence, correction, estimating, research control, export, and final human approval within the accepted class scope.
- **Deterministic acceptance:** Run the same frozen offline end-to-end fixture and approval/export sequence on both native platforms; compare authoritative quantities and evidence manifests. Exercise filesystem/keychain boundaries, install/update/recovery behavior, and permission failures with platform-specific evidence tied to the candidate.
- **Parallel work allowed:** iPhone review prototypes and bounded automation contract tests using synthetic data.
- **Non-goals / blocked authority:** No platform-specific business rules, Windows parity claim from a macOS run, broader takeoff-class coverage implied by the UI, mandatory cloud connection, or unauthorised signing/publication/account changes.

### 0.8 — iPhone review and bounded automation

- **Entry gate:** Accepted 0.7 desktop parity; approved companion approval scope, synchronization/conflict contract, and allowed automation actions/data classes.
- **Build outcome:** Native iPhone evidence review and scoped approvals through versioned contracts; bounded idempotent notification/research/approval-routing jobs with leases, budgets, deadlines, cancellation, and resumable state. Desktop retains the final-estimate workflow.
- **Deterministic acceptance:** Device and controller fixtures prove stale-revision rejection, conflict handling, offline reconnect, duplicate-delivery idempotency, cancellation/deadline limits, and recoverable failures. Every accepted action records actor, inputs, prior/new state, reason, and time; replay cannot duplicate approval or quantity effects.
- **Parallel work allowed:** Pilot dataset adjudication, operating procedures, and separately approved optional-cloud experiments.
- **Non-goals / blocked authority:** No unrestricted shells, automation-calculated quantities, production authority in n8n/NotebookLM/GrokBots, raw private drawings in an automation store, or external message/deployment/account mutation without its explicit owner authorization.

### 1.0 — Adjudicated Production pilot

- **Entry gate:** Accepted 0.8; owner freezes the pilot projects and supported class/system scope, rights/egress permissions, adjudicated truth set, accuracy/completeness/time/cost thresholds, operators, rollback procedure, and release decision criteria before running the pilot.
- **Build outcome:** A supported local-first estimating workflow proven on those drawing sets, with evidence-linked accepted lines, corrections, reproducible exports, operational recovery, and a recorded human release decision.
- **Deterministic acceptance:** Reproduce quantities and evidence from frozen revisions/rules/versions; score saved pilot outputs against the frozen truth set and thresholds; record human adjudications and blocked items. Pass native platform parity, migration/restore, interruption/replay, supply-chain, and export gates for the release candidate. Every material accepted line has resolvable evidence and no unresolved release-blocking conflict.
- **Parallel work allowed:** Quarantined proposals for additional systems/classes, model improvements, and optional infrastructure, each assigned its own measured promotion gate.
- **Non-goals / blocked authority:** No general superiority claim beyond the measured pilot, unrestricted autonomous bidding, automatic rule/model promotion, general foundation-model training, or multi-cloud rollout. Additional data scope, cloud services, provider egress, and production expansion require recorded owner decisions.
