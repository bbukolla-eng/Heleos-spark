### Task 8: Add Independent Storage and Recovery Verification

**Files:**
- Create: `tests/verification/Cargo.toml`
- Create: `tests/verification/src/lib.rs`
- Create: `tests/verification/src/bin/hold-writer.rs`
- Create: `tests/verification/tests/storage_recovery.rs`
- Create: `tests/verification/tests/hostile_storage.rs`
- Modify: `Cargo.toml`
- Modify: `Cargo.lock`

**Interfaces:**
- Consumes: only public APIs from the built `heleos-core`; does not modify production crates.
- Produces: black-box verifier evidence for schema, vault, audit, backup, and restore invariants.

- [ ] **Step 1: Add a verifier-only workspace crate**

The package is named exactly `heleos-verification`, contains no exported production API, and depends on `heleos-core` as a normal consumer. Add it to workspace members. Its writer must not edit any production crate; failures are returned to the owning Task 3–7 implementer through the review loop.

- [ ] **Step 2: Write the black-box storage verification matrix**

From empty temporary directories and public APIs, verify fresh migration, reopen, future-schema refusal by preparing a higher-version database, foreign-key enforcement, parameterized SQL payloads, append-only audit/intake events, JCS golden audit-chain validation, two-process writer-lock denial, duplicate/concurrent vault publication, corruption, hardlink/symlink/reparse rejection, missing reference, file-only orphan, externally prepared partial staging file, online backup during WAL activity, encrypted restore, wrong identity, tampered/forged/oversized/duplicate/trailing container entries, existing/destination-race rejection, and full post-restore reconciliation. Failed-migration and injected-crash proof remains in the owning Tasks 3 and 6 tests and is not claimed here.

- [ ] **Step 3: Run verification and route failures**

Run: `cargo test -p heleos-verification --test storage_recovery --test hostile_storage`

Expected: PASS without warning or ignored test. If a test exposes a production defect, record the exact failing command and return it to the owning implementer; do not patch production paths from this verifier task.

- [ ] **Step 4: Commit verifier evidence**

Run: `cargo fmt --all --check && cargo clippy --workspace --all-targets --all-features -- -D warnings`

Stage only the seven declared paths, run the staged checks, then commit.

Commit: `git commit -m "test: verify storage and recovery boundaries"`

---

