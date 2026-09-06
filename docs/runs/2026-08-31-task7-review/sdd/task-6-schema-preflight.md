# Task 6 schema/contract preflight

HEAD audited: `d90a1639c3b5e4276d6164157c2507565d4eab74`

Source aliases below: `plan` = `docs/superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md`; `design.md` = `docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`; `0001_foundation.sql` = `crates/heleos-core/migrations/0001_foundation.sql`.

## Critical

### 1. The fault matrix requires resume before any durable frozen-source locator exists

Evidence: Task 6 orders job creation/lease acquisition before vault streaming and only checkpoints the digest after publication (`docs/superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md:858`). It nevertheless requires reopen plus `resume(job_id, actor)` after both `AfterJobStart` and `AfterVaultPublish` (`:850`), while `resume` receives no source (`:864`) and must use only the checkpointed vault digest, never changed source bytes (`:876`). The only vault write API returns the digest as `PutOutcome` after `put_reader` (`crates/heleos-core/src/vault/mod.rs:84-90,377-379`); it exposes no durable job-addressable staging token. The database has generic `input_json`/`checkpoint_json` (`crates/heleos-core/migrations/0001_foundation.sql:74-76`) but the plan has not made a digest available to persist before either early fault.

Consequently, `AfterJobStart` leaves no frozen bytes/digest to resume, and `AfterVaultPublish` can leave an unidentifiable orphan before checkpoint commit. Scanning the vault cannot safely associate an arbitrary orphan with this job. This contradicts the restart acceptance requirement (`design.md:255`).

Minimal amendment: replace the Step 3 order and fault semantics with: “Create only a non-resumable queued row first; freeze/publish the opened source; then in one immediate transaction persist a versioned frozen-input checkpoint (digest, length, vault key, exact limits and quota reservation), acquire the lease, and transition to running. `AfterJobStart` occurs only after this commit. A crash at `AfterVaultPublish` is recovered by replaying `ingest` with the still-required source, not by `resume`; it must report the orphan safely. `resume` is promised only once the frozen-input checkpoint exists.” Alternatively authorize and specify a Task-4-owned durable staging API that can be checkpointed before publication.

### 2. The “no transition may bypass audit” contract is incompatible with the exported raw transaction API and the frozen schema

Evidence: Task 6 says no job, ingest, project, or evidence transition may bypass audit append (`plan:876`). Yet `Store::with_immediate_transaction` is public and hands every downstream crate a raw `rusqlite::Transaction` (`crates/heleos-core/src/store/mod.rs:256-275`), and Task 3 explicitly freezes it as a produced public interface (`plan:258-260`). The migration has no job-transition trigger or audit-coupling constraint: `job_runs.state` only checks enum membership and lease null-pairing (`0001_foundation.sql:62-86`). The only lifecycle-related triggers enforce accepted lineage, while the remaining triggers only reject updates/deletes on immutable tables (`:213-309`). A normal public API consumer can therefore perform `queued -> succeeded`, retain a lease on a terminal job, mutate a project, or insert/update authority rows without an audit event.

Task 6 cannot repair this boundary: its declared files exclude `0001_foundation.sql`, `store/schema.rs`, `store/migration.rs`, and `tests/migrations.rs` (`plan:823-834`), and the global path rule forbids staging undeclared paths (`plan:43-44`).

Minimal amendment: insert a Task-3-owned repair before Task 6 that seals raw SQL mutation from production consumers (make the raw transaction facility crate-private and relocate its external schema tests to a private test surface), adds database checks/triggers for legal job state/lease/terminal invariants, and exposes only repository units of work that append the audit row in the same transaction. Explicitly authorize every migration/schema/test path changed by that repair. If raw transactions intentionally remain public, narrow `:876` to an application-service convention and remove the claim that transitions cannot bypass audit.

## Important

### 3. Lease, checkpoint, result, and quota-reservation persistence are unversioned and therefore not interoperable or safely recoverable

Evidence: the schema supplies only JCS-syntax checks for opaque `budget_json`, `input_json`, and `checkpoint_json` (`0001_foundation.sql:74-76`); it does not constrain their schemas or phases. Task 6 requires leases/recovery and original authoritative-ID replay (`plan:858,876`), while the global contract requires project/store, derivative, and quarantine quota categories (`plan:34`) and Task 4 explicitly assigns Task 6 responsibility for computing and reserving quota while holding database-writer-before-vault lock order (`plan:394`). No plan clause defines the persisted reservation fields, whether pending/orphan bytes count, checkpoint phase transitions, compare-and-swap predicates, terminal result IDs, initial attempt number, lease-owner identity, or which error an unexpired foreign lease returns. A crash can otherwise release an in-memory reservation while leaving retained bytes, and `AfterAuthoritativeCommit` replay has no frozen result shape to return.

Minimal amendment: add exact `heleos.ingest-input/v1`, `heleos.ingest-budget/v1`, and `heleos.ingest-checkpoint/v1` JCS schemas. The checkpoint must enumerate phases and persist digest/length/vault key, total/project/derivative/quarantine reservations, probe/manifest completion, and terminal authoritative IDs. Specify initial attempt, lease owner/expiry/renewal, CAS `WHERE id/state/attempt/lease_owner` rules, release/consumption of every reservation, orphan accounting, and the typed error for an unexpired foreign lease. Require every DB-to-vault path to acquire the retained Store writer lock before the Vault lock and never reacquire DB state while holding only the Vault lock.

### 4. Idempotency scope contradicts the promised project/kind conflict behavior

Evidence: the required and implemented uniqueness key is `(project_id, kind, idempotency_key)` (`plan:282-292`; `0001_foundation.sql:62-81`). Task 6 then says a replay whose project or kind conflicts with the frozen request must return `IdempotencyConflict` (`plan:858`). Under the declared unique key, changing project or kind is a different key and may create a second authoritative job; lookup by the tuple cannot observe the claimed conflict.

Minimal amendment: choose one rule explicitly. The smallest schema-preserving amendment is: “Idempotency keys are scoped to `(project_id, kind)`; only a digest/frozen-request mismatch for the same tuple is `IdempotencyConflict`; the same text key in another project or kind is independent.” If project/kind mismatch must be denied, add a Task-3-owned global uniqueness/index and define the global lookup before Task 6.

### 5. The audit hash is not specified tightly enough for Task 8’s independent golden verifier

Evidence: Task 6 lists fields included in canonical hashing (`plan:876`) but does not define an exact serialized object, field names, whether `before_json`/`after_json` are embedded JSON values or strings, whether the previous hash is hex text or raw bytes, or a domain separator. It also omits `project_id`, although that field is persisted (`0001_foundation.sql:192-210`). Task 8 requires independent JCS golden audit-chain validation without importing production-private encoders (`plan:987-997`). The global text only supplies zero genesis plus JCS (`plan:31`). Multiple incompatible implementations satisfy that prose.

Minimal amendment: freeze the exact v1 preimage, for example `SHA256("heleos-audit-event-v1\0" || JCS({sequence,event_id,project_id,actor,action,subject_type,subject_id,before,after,reason,occurred_at_ms,previous_hash}))`, with `before`/`after` parsed JSON values, lowercase 64-hex `previous_hash`, nullable `project_id`, and a 64-zero genesis. Add literal JCS bytes and resulting hash to the plan/test contract.

### 6. The promised public API/DTO contract is not defined enough for Tasks 7-9 to compile independently

Evidence: Task 6 claims “the exact persistence methods” but names none (`plan:836-838`). Its code block defines only five method signatures (`:860-868`); it gives no constructor/ownership/lifetime shape for `ProjectService` or `IngestEngine`, no signature at all for `ProjectService::create`, and no fields/serde contract for `IngestRequest`, `IntakeSource`, `IngestReceipt`, `ResumeReceipt`, `AuditChainReport`, `EvidenceManifestReceipt`, or `FoundationInspection`. The inspection DTO is described only semantically (`:872`). Task 7 must serialize it as stable CLI JSON and resolve both objects by revision (`:931-959`); Task 8 is a normal external core consumer (`:987-997`); Task 9 asserts exact black-box counts/order/lineage and is forbidden from inspecting SQLite directly (`:1027-1033,1057`). Quarantine/replay signaling is also ambiguous: `ingest` returns `Result<IngestReceipt>`, but Task 7 assigns quarantine a distinct process exit while Task 6 requires its durable receipt/event.

Minimal amendment: add one compileable public-contract block specifying constructors and ownership/borrowing of Store/Vault/probe/clock/IDs/faults; every request/receipt/report field and serde name; stable schema/version fields; ordering and project-scope rules; replay and quarantine return semantics; and the complete repository method list with visibility. Provide a read-only inspection/audit/manifest reader constructible from a read-only `Store` without requiring a writer, `PdfProbe`, clock, ID generator, or fault injector.

### 7. The first schema cannot record a required content media type, and immutable admission state is undefined across different valid limit sets

Evidence: design section 6 assigns `content_objects` durable media type (`design.md:104-110`), and Task 6 stores both the original PDF and JCS evidence manifest in that table (`plan:870`), but the table has no media-type column (`0001_foundation.sql:18-34`). More seriously, one immutable row exists per digest (`:18-20`, immutable triggers at `:257-264`) and revisions require that row to be `accepted` (`:213-222`), while probe classification depends on caller-supplied limits and the evidence manifest records those exact limits (`plan:870`). The same bytes first quarantined under a tighter valid limit can never later be accepted under a broader valid limit, but no “first classification permanently wins” policy is declared.

Minimal amendment: route a Task-3-owned forward migration before Task 6 that adds a constrained `media_type` and separates immutable blob identity from per-attempt admission/classification (the accepted-revision trigger should require a qualifying accepted intake classification, not mutate the blob row). Alternatively explicitly freeze admission as content-global and permanent across all future limits/probe versions, and test that policy. Add the migration and migration-test paths to the owning task rather than silently editing them in Task 6.

### 8. Foundation acceptance disagrees on the authoritative content-object count

Evidence: design acceptance says duplicate ingest yields “one stored content object” (`design.md:248-257`), whereas Tasks 6 and 9 require exactly two content objects—original plus canonical evidence manifest (`plan:842,1033`)—and Task 7 verifies both (`:959`).

Minimal amendment: change design section 14 to “one stored original content object plus one canonical evidence-manifest content object, with no duplicate copy of either,” matching Tasks 6-9.
