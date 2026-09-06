### Task 9: Add Independent Foundation Acceptance Verification

**Files:**
- Create: `tests/verification/tests/foundation_acceptance.rs`
- Create: `tests/verification/tests/hostile_intake.rs`
- Create: `scripts/verify-foundation`
- Create: `scripts/verify-foundation.ps1`
- Create: `scripts/verify-provenance`
- Create: `tests/verification/src/bin/verify-provenance.rs`

**Interfaces:**
- Consumes: public core/CLI APIs, built `heleos` and WASI guest paths, committed fixture factories/registries, and Task 8 verifier results.
- Produces: black-box Foundation behavior evidence, portable offline verification entry points, and a machine-readable provenance verifier.

- [ ] **Step 1: Write the end-to-end acceptance test before the verification script**

The test consumes absolute `HELEOS_BIN` and `HELEOS_PDF_GUEST` paths and drives the real CLI from empty directories: create a project; ingest the synthetic PDF twice; assert unchanged expected source hash, exactly two content objects (original plus canonical manifest), one canonical document/revision, two immutable intake events, stable page ordering/dimensions/rotation/unit/IDs/transforms/lineage; ingest equal bytes under a new filename; ingest a near-duplicate; exercise corrupt, encrypted, active-content, quota, and over-limit quarantine; retrieve/re-hash original plus evidence manifest; verify audit/vault/database; create encrypted backup; restore; and verify the restored system. Injected-crash/resume proof is consumed from Task 6's test evidence rather than exposed through a production CLI fault switch.

Run: `cargo build -p heleos-cli --bin heleos && cargo build -p heleos-pdf-guest --target wasm32-wasip1 --release`

Run: `HELEOS_BIN=target/debug/heleos HELEOS_PDF_GUEST=target/wasm32-wasip1/release/heleos_pdf_guest.wasm cargo test -p heleos-verification --test foundation_acceptance --test hostile_intake`

Expected: PASS with zero ignored cases.

- [ ] **Step 2: Implement reproducible verification commands**

The cross-platform Rust `verify-provenance` binary fails on any direct dependency, fixture, skill, or source missing a complete governed record; lockfile drift; mutable Git/URL dependency; unexpected Git remote; prohibited networking crate/module in Foundation production paths; repository secret pattern; unapproved workflow; or `#[ignore]` under acceptance/verifier tests. It prints only safe identifiers. The Bash wrapper uses `#!/usr/bin/env bash`; PowerShell is native and uses `$ErrorActionPreference = 'Stop'`.

Both scripts require a previously fetched locked cache, set Cargo offline mode, resolve the platform-specific CLI path, build the WASI guest, and run the equivalent of:

```sh
cargo fmt --all --check
cargo clippy --frozen --workspace --all-targets --all-features -- -D warnings
cargo build --frozen -p heleos-cli --bin heleos
cargo build --frozen -p heleos-pdf-guest --target wasm32-wasip1 --release
cargo test --frozen --workspace --all-targets
cargo run --frozen -p heleos-verification --bin verify-provenance
```

On macOS, the Bash entry point re-executes the runtime/acceptance portion under `/usr/bin/sandbox-exec` with `(deny network*)`; on other platforms, the verifier proves the Foundation production graph contains no networking crate/module and the WASI guest imports no sockets. This evidence is reported precisely rather than described as universal OS firewall enforcement. Neither script installs tools, invokes a provider, or skips a verifier test.

- [ ] **Step 3: Run independent acceptance and commit**

Run: `./scripts/verify-foundation`

Run: `git diff --check`

Run on Windows when CI is authorized: `pwsh -File scripts/verify-foundation.ps1`

Expected: all local gates pass at the exact head with zero ignored verifier tests. This task establishes a local release candidate only; Foundation acceptance waits for Task 10.

Stage only the six declared paths, run the staged checks, then commit.

Commit: `git commit -m "test: prove local Foundation behavior"`

---

