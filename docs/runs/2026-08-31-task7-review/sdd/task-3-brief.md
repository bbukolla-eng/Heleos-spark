### Task 3: Implement SQLite Schema, Migrations, and Integrity Checks

**Files:**
- Modify: `Cargo.toml`
- Modify: `Cargo.lock`
- Modify: `crates/heleos-core/Cargo.toml`
- Modify: `crates/heleos-core/src/lib.rs`
- Modify: `crates/heleos-core/src/error.rs`
- Create: `crates/heleos-core/migrations/0001_foundation.sql`
- Create: `crates/heleos-core/src/store/mod.rs`
- Create: `crates/heleos-core/src/store/migration.rs`
- Create: `crates/heleos-core/src/store/schema.rs`
- Create: `crates/heleos-core/src/store/permissions.rs`
- Create: `crates/heleos-core/tests/migrations.rs`
- Create: `docs/operations/migration-recovery.md`
- Modify: `governance/tools.toml`

**Interfaces:**
- Consumes: Task 2 IDs, states, clock, and typed errors.
- Produces: `Store::open_writer`, `Store::open_read_only`, `Store::open_in_memory`, `Store::migrate`, `Store::schema_version`, `Store::verify_integrity`, `Store::with_immediate_transaction`, `apply_private_permissions`, `verify_private_permissions`, `WriterLock`, `MigrationReport`, the distinct `HeleosError::WriterBusy` variant, and schema version `1`.

- [ ] **Step 1: Add exact storage dependencies without implementation**

Add `rusqlite = { version = "=0.40.2", features = ["bundled", "backup"] }` and `fs2 = "=0.4.3"` at workspace level and to `heleos-core`, plus target-specific `windows-acl = "=0.3.0"` for Windows. Record their sources, checksums, SQLite bundling/locking/ACL behavior, unsafe transitive surface, licenses, and rollback in `governance/tools.toml`. Run `cargo check -p heleos-core` to prove dependency resolution only.

- [ ] **Step 2: Write failing migration and invariant tests**

Tests must prove: an empty database reaches version 1; reopening is idempotent; `foreign_keys` is `1`, journal mode is `wal` for a file database, `synchronous` is `2`, trusted schema is off, and defensive mode is on; every table below exists; SQL-injection payloads in names, actors, idempotency keys, paths, and JSON remain inert data; invalid foreign keys and enums fail; immutable tables reject update/delete; audit and ingest-event rows reject update/delete; a deliberately failing second migration leaves version 1 and schema unchanged; a stored version newer than the binary fails closed; two independent processes cannot both obtain a writer lock and the denied caller receives exactly `HeleosError::WriterBusy`; read-only verification cannot mutate; and both SQLite integrity checks report clean.

Run: `cargo test -p heleos-core --test migrations`

Expected: FAIL with unresolved `Store` contracts and no migration implementation.

- [ ] **Step 3: Create migration 1 in one transaction**

The migration creates `schema_migrations`, `projects`, `content_objects`, `documents`, `document_revisions`, `project_documents`, `ingest_events`, `sheets`, `scales`, `job_runs`, `source_records`, `evidence_objects`, `corrections`, and `audit_events`. Use `TEXT` IDs, `INTEGER` epoch milliseconds and byte counts, canonical JSON text with `json_valid` checks, exact enum `CHECK` clauses from Task 2, and foreign keys with explicit delete behavior. Required uniqueness is:

```text
content_objects.sha256
document_revisions.content_sha256
project_documents(project_id, document_id)
sheets(revision_id, zero_based_page_index)
job_runs(project_id, kind, idempotency_key)
evidence_objects(project_id, document_revision_id, content_sha256)
audit_events.sequence and audit_events.event_hash
```

`sheets` stores integer `width_micropoints`, `height_micropoints`, allowed rotation, unit `pt`, parent content hash, and JCS transform JSON. `job_runs` stores attempt, lease owner/expiry, deadline, budget/input/checkpoint JSON, and terminal reason. Each `ingest_events` row is inserted once with a terminal exact Task 2 outcome; interrupted attempts are materialized during recovery rather than updated in place. `content_objects.admission_state` is `accepted` or `quarantined`. Create triggers that reject update/delete on content objects, document revisions, sheets, evidence objects, ingest events, and audit events; rejected input does not create rows in document revisions or sheets.

- [ ] **Step 4: Implement connection and migration policy**

`Store::open_writer` creates or opens `<database>.writer.lock`, rejects symlinks/reparse points and non-regular files, obtains an exclusive `fs2` lock before opening SQLite, rechecks the database identity after open, and keeps the lock handle for the `Store` lifetime. A second process receives exactly `HeleosError::WriterBusy`; it must not be collapsed into `Database`, `PolicyDenied`, or a string-matched I/O error. `Store::open_read_only` uses SQLite read-only/query-only mode and never obtains mutation capability. Both apply safe connection settings; every value-bearing SQL statement uses rusqlite bound parameters, while only compile-time SQL supplies identifiers. `apply_private_permissions` sets Unix file/directory modes to `0600`/`0700`; on Windows it disables inherited access and admits only the current user and `SYSTEM`, then `verify_private_permissions` reads the DACL back. Permission-hardening failure aborts creation/open. `migrate` checks embedded SQL SHA-256, applies one migration per immediate transaction, and rejects an unknown newer version. `verify_integrity` runs both SQLite checks and returns all violations in an `IntegrityReport`. Faulty migration injection remains owner-task-only test support and is not claimed by later black-box verifiers.

- [ ] **Step 5: Document and test recovery**

`docs/operations/migration-recovery.md` prescribes: stop writers; preserve the failed DB and WAL/SHM; verify the last encrypted backup; restore into a newly created staging path; run integrity and foreign-key checks in defensive read-only mode; run forward migrations; compare manifest hashes; and perform an explicit owner-approved cutover. It must state that files are never overwritten in place and down-migrations are unsupported.

Run: `cargo fmt --all --check && cargo clippy --workspace --all-targets --all-features -- -D warnings && cargo test -p heleos-core --test migrations`

Expected: all pass without warnings.

- [ ] **Step 6: Commit**

Stage only the thirteen files declared by this task, run the staged checks, then commit.

Commit: `git commit -m "feat: add durable foundation schema"`

---

