### Task 10: Gate Supply Chain, Windows CI, and Foundation Release Evidence

**Files:**
- Create: `deny.toml`
- Create: `docs/operations/dependency-bootstrap.md`
- Create: `scripts/verify-supply-chain`
- Create: `scripts/verify-supply-chain.ps1`
- Create: `docs/verification/foundation-0.1.md`
- Modify after the human decision: `governance/github-apps.toml`
- Create after the human decision: `.github/workflows/core-ci.yml`
- Create: `artifacts/sbom/heleos-foundation-0.1.cdx.json`

**Interfaces:**
- Consumes: the final Task 9 head, cached advisory/license data, governed tool versions, a completed owner disposition for every listed GitHub App, and task/final security review findings.
- Produces: full transitive dependency/advisory/license/SBOM/secret-scan evidence, macOS and Windows CI evidence, and the Foundation 0.1 acceptance dossier.

- [ ] **Step 1: Establish pinned verification tooling outside runtime**

Document and record `cargo-deny 0.20.2`, `cargo-audit 0.22.2`, `cargo-cyclonedx 0.5.9`, and `gitleaks 8.30.1` with origin, checksum, license, permissions, advisory-data snapshot time/hash, owner, and rollback. The admitted gitleaks archive SHA-256 values are `b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5` for Darwin ARM64 and `d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e` for Windows x64. `docs/operations/dependency-bootstrap.md` separates the explicitly online bootstrap (`cargo fetch --locked`, exact-version tool installation, advisory/license database refresh) from all offline runtime tests. Verification scripts never install or update tools.

- [ ] **Step 2: Write full-graph supply-chain gates**

`deny.toml` allowlists licenses and registries, rejects yanked/advisory-denied crates, unknown Git sources, duplicate risk exceptions without rationale/expiry, and unmaintained exceptions without owner review. The cross-platform scripts run Cargo with `--frozen`, `cargo audit --no-fetch`, cargo-deny without fetching, the provenance verifier, a full-history secret scan, and `cargo cyclonedx` for the complete transitive graph. They verify the SBOM hash and fail on a missing transitive package/license/source, mutable reference, checksum drift, secret, unsafe exception, or registry fixture designed to violate policy.

- [ ] **Step 3: Apply the GitHub Apps human gate before any workflow write**

Inspect `governance/github-apps.toml`. If any app remains `owner_decision_required`, stop this task and report the security-sensitive decision to the controller; do not create or stage a workflow. After the owner records retain/restrict/suspend/remove for every app, record the decision evidence and create a no-secret workflow with top-level `permissions: contents: read`, `actions/checkout` pinned to commit `d23441a48e516b6c34aea4fa41551a30e30af803`, Rust 1.96.1, explicit online dependency bootstrap, then offline macOS Bash and Windows PowerShell verification. No pull-request/issue write, deployment, packages, OIDC, or secret permission is allowed.

- [ ] **Step 4: Run security and cross-platform release verification**

Run the task-scoped supply-chain commands locally, then the repository's standard Codex Security scan and whole-branch reviewer. Push/remote CI requires a separate owner-approved Git handoff and is performed by the controller, never an implementer. Task 10 remains open across that human gate; after the authorized feature-branch push and CI run, record immutable run/commit references and exact macOS/Windows pass counts before the acceptance dossier commit.

- [ ] **Step 5: Write the acceptance dossier and commit**

`docs/verification/foundation-0.1.md` records exact commit SHA, Rust/Cargo/SQLite/Wasmtime/guest-module versions and hashes, OS/architecture, commands and pass counts, fixture hashes, migration/fault-matrix/writer-lock evidence, vault/audit/evidence-manifest/backup results, macOS network-denial result, Windows CI result, SBOM/advisory/license/secret results, reviewer findings/rulings, known limitations, and the exact 0.2 gate. It may say `Accepted` only when every local, security, app, and macOS/Windows gate is evidenced at the same head.

Stage only the eight declared paths, run `git diff --cached --check`, inspect the staged names, then commit.

Commit: `git commit -m "chore: gate Foundation 0.1 release"`

---

## Completion Rule

Foundation 0.1 is complete only after every task-scoped review is approved, the independent whole-branch and security reviews are clean or have explicitly adjudicated non-load-bearing findings, both local verification scripts pass from the final head, the public/synthetic double-ingest and evidence-manifest criteria are evidenced, encrypted restore is proven, every GitHub App has an owner disposition, and macOS plus Windows CI pass at the same commit. An unresolved app, push, CI, or Windows gate means only `local release candidate`, never `Foundation 0.1 complete`. The broader production goal remains active: the next product execution plan begins with roadmap milestone 0.2, while the independent engineering-research plan may continue producing cited candidates that cannot become production truth directly.
