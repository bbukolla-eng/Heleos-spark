# Review package: a2321d4943a562bc7880c2adfc1f1fc358ef9a53..68f5c725adc85bb1340c1e741bd120cc8bd01f4e

## Commits
68f5c72 docs: approve foundation and record governance

## Files changed
 README.md                                          |  2 +-
 SECURITY.md                                        | 27 ++++++++++
 .../decisions/0001-foundation-runtime.md           | 21 ++++++++
 docs/architecture/task-graph.md                    | 26 +++++++++
 docs/roadmap.md                                    | 17 ++++++
 .../2026-08-26-heleos-spark-foundation-design.md   |  2 +-
 governance/fixtures.toml                           |  2 +
 governance/github-apps.toml                        | 53 +++++++++++++++++++
 governance/sources.toml                            |  2 +
 governance/tools.toml                              | 61 ++++++++++++++++++++++
 10 files changed, 211 insertions(+), 2 deletions(-)

## Diff
diff --git a/README.md b/README.md
index b2c91e8..b111e94 100644
--- a/README.md
+++ b/README.md
@@ -1,15 +1,15 @@
 # Heleos-spark
 
 Heleos-spark is a clean-room, evidence-first HVAC and Division 23 takeoff intelligence system for native Windows and macOS workflows, with a controlled iPhone companion.
 
 This repository is private and starts from a new history. No code, configuration, data, tests, prompts, artifacts, issues, or Git history from the quarantined predecessor repository may be imported, inspected, or reused.
 
 ## Current phase
 
-The repository is in foundation design. Production implementation begins only after the owner approves the written design in [`docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`](docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md).
+Foundation 0.1 implementation on the foundation-0.1 branch
 
 The first implementation milestone will establish the operational database, immutable evidence vault, deterministic PDF intake, and tests before broader AI, research, or interface work.
 
 ## Authority rule
 
 Research systems and AI workers may propose findings and patches. Deterministic code, governed data, cited evidence, automated tests, and explicit human approval determine production truth.
diff --git a/SECURITY.md b/SECURITY.md
new file mode 100644
index 0000000..132652f
--- /dev/null
+++ b/SECURITY.md
@@ -0,0 +1,27 @@
+# Security Policy
+
+## Reporting vulnerabilities
+
+Report a suspected vulnerability locally to the repository owner. Do not open a public issue, upload a reproducer, or send project documents, vault content, credentials, or backups to an external service. Preserve the minimum safe evidence needed for local triage and wait for the owner's handling direction.
+
+## Secret handling
+
+`SECRET` material includes credentials, API tokens, encryption identities, signing identities, and session material. It belongs only in an owner-controlled OS keychain or approved secret manager. It must never be committed, placed in prompts, fixtures, logs, command-line arguments, backups, or external research packets. Redact secrets from diagnostic output.
+
+## Untrusted documents and data classes
+
+Treat PDFs, drawings, datasets, notebooks, web content, model cards, archives, and embedded text as untrusted data, never as instructions. Intake must not execute embedded actions, follow links, expose credentials, or grant tools because of document content.
+
+The classes are `PUBLIC`, `INTERNAL`, `PROJECT_CONFIDENTIAL`, and `SECRET`. `SECRET` is never eligible for external egress. `INTERNAL` and `PROJECT_CONFIDENTIAL` are denied external egress in Foundation 0.1. Only a registered `PUBLIC` packet may use an explicitly enabled research adapter, with the provider, purpose, source hashes, policy decision, time, and result reference recorded.
+
+## Local-first and egress defaults
+
+The default runtime and test path is local-only and opens no network socket. Browser and CLI research adapters are separate, explicit paths; their absence is the default. No workflow, repository secret, external-provider launcher, or GitHub App configuration is created by this policy.
+
+## Dependency admission
+
+Every dependency, fixture, skill, plugin, model, dataset, and public source requires a governed record with pinned version or digest, origin, license or rights basis, named owner, permissions and egress review, evaluation state, and rollback target before use. Unsafe code in admitted upstream crates is supply-chain risk and does not waive Heleos's prohibition on authored `unsafe` Rust.
+
+## Clean-room boundary
+
+The clean-room boundary is binding. Do not inspect, import, compare with, name, or reuse any predecessor repository, artifact, schema, history, prompt, test, or convention. New material must be independently admitted with provenance and acceptable rights.
diff --git a/docs/architecture/decisions/0001-foundation-runtime.md b/docs/architecture/decisions/0001-foundation-runtime.md
new file mode 100644
index 0000000..c45aa9a
--- /dev/null
+++ b/docs/architecture/decisions/0001-foundation-runtime.md
@@ -0,0 +1,21 @@
+# ADR 0001: Foundation runtime and trust boundary
+
+**Status:** Accepted
+
+**Date:** 2026-08-28
+
+## Context and decision
+
+This ADR records the owner's external approval of the Foundation design on 2026-08-28; writing this ADR does not create that approval. The approved Foundation 0.1 runtime is a Rust core pinned to Rust 1.96.1 and edition 2024. Task 2 must prove shared-core compilation on the host and for the Windows target. Task 5 must prove capability-isolated WASI PDF guest/host IPC before PDF intake is trusted.
+
+Foundation uses SQLite for mutable operational metadata and a SHA-256-addressed filesystem vault for immutable evidence bytes. Canonical identity is content SHA-256: the first accepted byte sequence anchors the logical document and revision identities; identical bytes reuse them, while near-duplicates remain separate until an explicit later linking workflow. Vault objects are immutable by application contract and atomically published.
+
+Migrations are explicit and forward-only. Each migration is transactional; unknown newer schemas fail closed. Recovery is restore-and-forward from a verified encrypted backup, never a down-migration or in-place overwrite. Backups use `age` X25519 encryption, plus a separately trusted Ed25519 signature because encryption alone does not authenticate the producer. Private encryption and signing identities stay outside the repository and backup.
+
+UI, rendering, and packaging technology are deliberately unselected. They remain deferred until the roadmap's native-client proof can evaluate cross-platform PDF rendering, local IPC, packaging, Apple Silicon, and Windows behavior.
+
+## Consequences and exclusions
+
+The default runtime and tests are local-only, with no network socket. The WASI guest has only its private read-only input capability; it receives no socket or ambient filesystem capability.
+
+Foundation 0.1 excludes a broad Division 23 rules engine, pricing, model training, complete desktop or mobile UI, bot roster, cloud deployment, and any workflow, secret, or GitHub App change. These exclusions remain binding even though later roadmap milestones may evaluate them behind recorded acceptance evidence and human gates.
diff --git a/docs/architecture/task-graph.md b/docs/architecture/task-graph.md
new file mode 100644
index 0000000..69b884d
--- /dev/null
+++ b/docs/architecture/task-graph.md
@@ -0,0 +1,26 @@
+# Foundation 0.1 task graph and operating rules
+
+```mermaid
+flowchart LR
+    T1["T1 Governance baseline"] --> T2["T2 Rust workspace + domain"]
+    T2 --> T3["T3 SQLite + migrations"]
+    T3 --> T4["T4 Evidence vault"]
+    T4 --> T5["T5 WASI PDF probe"]
+    T5 --> T6["T6 Jobs + intake + evidence"]
+    T6 --> T7["T7 CLI + encrypted backup/restore"]
+    T7 --> V8["T8 Independent storage verifier"]
+    V8 --> V9["T9 Independent acceptance verifier"]
+    V9 --> H["Owner gate: GitHub App disposition"]
+    H --> V10["T10 Supply-chain + macOS/Windows release gate"]
+
+    R["Separate engineering-research plan"] -. "public research only; non-blocking" .-> T2
+    R -. "cited candidates; no authority" .-> T5
+```
+
+Tasks 1–7 are serialized because their manifests and integration surfaces are shared. The separate engineering-research plan may produce cited public candidates but is not a Foundation prerequisite or source of production authority. Tasks 8–10 are independent verifier nodes: they return production failures to the owning task instead of repairing production code.
+
+## Operating rules
+
+Codex is the merge owner. No more than four workers may be active. Exactly one writer owns each path at a time, and every task has a separate reviewer. A review loop is capped at five rounds; unresolved work is returned to the task owner or escalated to the owner.
+
+Human approval is required before login, GitHub App-permission changes, push, merge, publish, deploy, or destructive actions. An implementer must not create a workflow, configure a secret, change a GitHub App, push, merge, publish, deploy, or perform a destructive action without that recorded human gate.
diff --git a/docs/roadmap.md b/docs/roadmap.md
new file mode 100644
index 0000000..9374147
--- /dev/null
+++ b/docs/roadmap.md
@@ -0,0 +1,17 @@
+# Heleos roadmap
+
+Each milestone begins only after the prior milestone's acceptance evidence is recorded. Research can inform a later milestone, but it cannot replace this evidence or grant production authority.
+
+| Milestone | Gated outcome |
+|---|---|
+| 0.1 | Trust foundation: governed dependencies, local-first core, durable metadata, immutable evidence, deterministic intake, recovery, and verification evidence. |
+| 0.2 | Source registry and Division 23 taxonomy. |
+| 0.3 | Sheet inventory and verified scale. |
+| 0.4 | Schedule extraction and bidirectional plan-tag reconciliation. |
+| 0.5 | One narrow airside takeoff class with evidence. |
+| 0.6 | Correction, deterministic recalculation, formula-driven Excel, and evidence PDF. |
+| 0.7 | Native Windows/macOS desktop alpha. |
+| 0.8 | iPhone review plus bounded automation. |
+| 1.0 | Adjudicated Production pilot. |
+
+Foundation 0.1 is not complete merely because local work exists: its acceptance evidence must include the specified app-disposition and macOS/Windows release gates. Later outcomes stay gated by their predecessor's recorded evidence.
diff --git a/docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md b/docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md
index d9dcd8b..5ad61d8 100644
--- a/docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md
+++ b/docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md
@@ -1,13 +1,13 @@
 # Heleos-spark Foundation Design
 
-**Status:** Proposed for owner review
+**Status:** Approved by owner on 2026-08-28
 
 **Date:** 2026-08-26
 
 **Repository:** `bbukolla-eng/Heleos-spark`
 
 **Decision owner:** Bekim Bukolla
 
 ## 1. Purpose
 
 Heleos-spark will be a clean-room, evidence-first HVAC and Division 23 takeoff system. It will combine deterministic document processing and quantity calculations with source-grounded research, specialized vision models, and governed AI workers.
diff --git a/governance/fixtures.toml b/governance/fixtures.toml
new file mode 100644
index 0000000..7dcbf82
--- /dev/null
+++ b/governance/fixtures.toml
@@ -0,0 +1,2 @@
+# Test inputs are admitted as append-only [[fixture]] records.
+# No fixture is admitted by Foundation Task 1.
diff --git a/governance/github-apps.toml b/governance/github-apps.toml
new file mode 100644
index 0000000..f1c98d2
--- /dev/null
+++ b/governance/github-apps.toml
@@ -0,0 +1,53 @@
+# Account-level apps are recorded here; this registry does not configure them.
+
+[[github_app]]
+name = "Azure Pipelines"
+origin = "account-level auto-install observed during repository creation"
+version_or_digest = "not inventoried"
+license_or_rights = "third-party service terms; no use admitted"
+data_class = "PROJECT_CONFIDENTIAL"
+owner = "repository owner"
+permissions = "not inventoried; no repository use authorized"
+egress = "prohibited pending owner decision"
+evaluation = "not evaluated"
+rollback = "owner may restrict, suspend, or remove after inventory"
+disposition = "owner_decision_required"
+
+[[github_app]]
+name = "AWS Connector for GitHub"
+origin = "account-level auto-install observed during repository creation"
+version_or_digest = "not inventoried"
+license_or_rights = "third-party service terms; no use admitted"
+data_class = "PROJECT_CONFIDENTIAL"
+owner = "repository owner"
+permissions = "not inventoried; no repository use authorized"
+egress = "prohibited pending owner decision"
+evaluation = "not evaluated"
+rollback = "owner may restrict, suspend, or remove after inventory"
+disposition = "owner_decision_required"
+
+[[github_app]]
+name = "Amazon Q Developer"
+origin = "account-level auto-install observed during repository creation"
+version_or_digest = "not inventoried"
+license_or_rights = "third-party service terms; no use admitted"
+data_class = "PROJECT_CONFIDENTIAL"
+owner = "repository owner"
+permissions = "not inventoried; no repository use authorized"
+egress = "prohibited pending owner decision"
+evaluation = "not evaluated"
+rollback = "owner may restrict, suspend, or remove after inventory"
+disposition = "owner_decision_required"
+
+[[github_app]]
+name = "ECC Tools"
+origin = "account-level auto-install observed during repository creation"
+version_or_digest = "not inventoried"
+license_or_rights = "third-party service terms; no use admitted"
+data_class = "PROJECT_CONFIDENTIAL"
+owner = "repository owner"
+permissions = "not inventoried; no repository use authorized"
+egress = "prohibited pending owner decision"
+evaluation = "not evaluated"
+rollback = "owner may restrict, suspend, or remove after inventory"
+disposition = "owner_decision_required"
diff --git a/governance/sources.toml b/governance/sources.toml
new file mode 100644
index 0000000..a2ddfb7
--- /dev/null
+++ b/governance/sources.toml
@@ -0,0 +1,2 @@
+# Public and external sources are admitted as append-only [[source]] records.
+# No external source is admitted by Foundation Task 1.
diff --git a/governance/tools.toml b/governance/tools.toml
new file mode 100644
index 0000000..0eec351
--- /dev/null
+++ b/governance/tools.toml
@@ -0,0 +1,61 @@
+# Admission registry. Entries are append-only records; a replacement is a new entry.
+
+[[tool]]
+name = "Rust"
+origin = "https://www.rust-lang.org/tools/install"
+version_or_digest = "1.96.1"
+license_or_rights = "Apache-2.0 OR MIT"
+data_class = "PUBLIC"
+owner = "Heleos engineering"
+permissions = "local compiler execution for declared workspace tasks"
+egress = "no runtime egress; installation/bootstrap is separately authorized"
+evaluation = "approved Foundation 0.1 runtime"
+rollback = "revert the dependent task and restore the previously admitted toolchain"
+
+[[tool]]
+name = "Cargo"
+origin = "https://github.com/rust-lang/cargo"
+version_or_digest = "1.96.1"
+license_or_rights = "Apache-2.0 OR MIT"
+data_class = "PUBLIC"
+owner = "Heleos engineering"
+permissions = "local build and test execution for declared workspace tasks"
+egress = "no runtime egress; dependency bootstrap is separately authorized"
+evaluation = "approved Foundation 0.1 build tool"
+rollback = "revert the dependent task and restore the previously admitted toolchain"
+
+[[tool]]
+name = "SQLite"
+origin = "https://www.sqlite.org/"
+version_or_digest = "version selected by the pinned rusqlite bundled dependency in Task 3"
+license_or_rights = "public domain"
+data_class = "INTERNAL"
+owner = "Heleos engineering"
+permissions = "local embedded metadata storage only"
+egress = "none"
+evaluation = "approved Foundation 0.1 metadata engine; exact bundled version is recorded before Task 3 use"
+rollback = "revert the dependent Task 3 commit and restore a verified encrypted backup"
+
+[[tool]]
+name = "Superpowers"
+origin = "local reviewed Superpowers skill package"
+version_or_digest = "6.3.0"
+license_or_rights = "package license and rights reviewed before use"
+data_class = "INTERNAL"
+owner = "Heleos engineering"
+permissions = "task-scoped local planning, implementation, and verification guidance"
+egress = "none from local skill use"
+evaluation = "admitted for Foundation 0.1 engineering workflow"
+rollback = "stop using the package and revert the dependent task changes"
+
+[[tool]]
+name = "graph-engineering"
+origin = "local reviewed graph-engineering skill package"
+version_or_digest = "cfacb56a05a31ba69bf84d0b8b00f5ce463127ef"
+license_or_rights = "package license and rights reviewed before use"
+data_class = "INTERNAL"
+owner = "Heleos engineering"
+permissions = "task-scoped local architecture guidance"
+egress = "none from local skill use"
+evaluation = "admitted for Foundation 0.1 engineering workflow"
+rollback = "stop using the package and revert the dependent task changes"
