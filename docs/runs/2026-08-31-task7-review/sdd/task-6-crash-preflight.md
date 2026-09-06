# Task 6 crash/idempotency/security preflight

## Identity and scope

- Date: 2026-08-29
- Reviewed HEAD: `d90a1639c3b5e4276d6164157c2507565d4eab74`
- Review mode: read-only preflight; no tracked file, index, commit, or branch mutation
- Primary contract: `task-6-brief.md` and Task 6 at plan lines 821-886
- Existing boundaries inspected: Foundation design, migration 1, `Store`, `Vault`, vault reconciliation, `PdfProbe`, job-state domain types, errors, and existing Task 3-5 reports/tests

## Verdict

**NOT APPROVED.** Task 6 has four Critical contradictions and six Important contract gaps that should be amended before implementation. The most direct contradiction is that the plan requires `resume(job_id, actor)` to recover faults which occur before the only durable source digest exists. The second is that it requires an already committed, terminal successful attempt to be expired and materialized as interrupted.

No Task 6 implementation or tests exist at this HEAD, so this is a static contract preflight rather than a code review.

## Critical findings

### C1. `AfterJobStart` and `AfterVaultPublish` are not resumable under the specified ordering

Evidence:

- Plan line 850 requires every injected fault, including `AfterJobStart` and `AfterVaultPublish`, to be recovered solely through `resume(job_id, actor)`.
- Plan line 858 orders the work as: create/resolve job, acquire lease, stream source into the vault, then checkpoint the frozen digest.
- `resume` receives no source handle or source path (lines 863-864), and line 876 correctly forbids trusting changed source bytes.
- The existing `Vault::put_reader` returns the digest only after the final object has been fully published and verified (`vault/mod.rs` lines 377-379 and 717-722). It has no callback capable of atomically checkpointing SQLite while publication is in progress.

Consequences:

- A crash after job start but before vault publication leaves a running job with neither source bytes nor a durable digest/object. Resume cannot reconstruct the input without reopening the path, which line 858 forbids.
- A crash after publication but before checkpoint leaves an unreferenced vault object whose digest is not associated with the job. Selecting an arbitrary unreferenced object would be unsafe and nondeterministic.

Minimal plan amendment:

1. Make source fingerprinting a bounded first pass over the single retained no-follow handle, rewind that same handle, and compare the later vault result to the frozen digest/length.
2. For a new scoped key, publish the original before making a job resumably `running`; then create/start the job and persist the digest, length, exact limits/budget/version identity, lease, checkpoint audit event, and checkpoint in one immediate transaction.
3. Define `AfterJobStart`, `AfterVaultPublish`, and `AfterCheckpointCommit` only after that recoverable transaction. If a distinct fault is required between vault publication and job creation, name it explicitly (for example `AfterVaultPublishBeforeJobCreate`): it has no resumable job and is recovered by repeating `ingest` with the same request, not by `resume`.
4. If the current job-before-vault ordering must remain, Task 6 instead needs a durable, job-addressable source capsule/staging API. The current Vault API cannot supply it.

Required tests:

- Kill after persisted job start and prove the checkpoint already names a verified vault object before `resume` is allowed.
- Kill after vault publication but before job creation: prove no job exists, reconciliation reports exactly the verified orphan, and repeating `ingest` deduplicates it and creates one job/result.
- Replace or modify the pathname after `IntakeSource::open`; recovery must never open it.
- Mutate bytes between the fingerprint pass and vault streaming; the vault digest/length mismatch must be a hard integrity failure with no authoritative rows.

### C2. `AfterAuthoritativeCommit` cannot create an interrupted attempt, and commit errors are outcome-ambiguous

Evidence:

- Plan line 850 applies lease expiry and “one immutable `interrupted` event for each recovered attempt” to `AfterAuthoritativeCommit` too.
- The authoritative transaction is required to include the terminal event, terminal job state, and audit events (line 858).
- `JobState::Succeeded` has no outgoing transition (`domain/state.rs` lines 14-29), correctly making it terminal.
- The current transaction helper commits first and can still return an error during post-commit lock/sidecar/identity checks (`store/mod.rs` lines 269-274). Therefore ordinary production errors, not only the injected fault, can mean “commit may already be authoritative.”

Consequences:

- A committed successful attempt has no live lease to expire and cannot legally transition to interrupted.
- Adding an interrupted event would falsely give one attempt two terminal outcomes and contradict the immutable accepted event.
- Mapping every helper error to “rolled back” risks a second authoritative attempt after an acknowledged-unknown commit.

Minimal plan amendment:

- Split the fault matrix into pre-authoritative and post-authoritative cases. Pre-commit running attempts are expired, materialized interrupted, audited, and retried. `AfterAuthoritativeCommit` is an outcome-unknown acknowledgement failure: reopen, observe `succeeded` with cleared lease, return the original authoritative IDs, and optionally append a separate `idempotent_replay`/recovery-observation event. It must not append `interrupted` for the committed attempt.
- Define a typed commit-outcome-unknown error or result path. Add `crates/heleos-core/src/error.rs` to Task 6 scope if a new variant is used. A caller must be instructed to retry by the same scoped idempotency key; never issue a fresh key after this error.
- Fire `AfterAuthoritativeCommit` only after `with_immediate_transaction` has returned success, including its post-commit checks. Separately inject failure in each post-commit check to prove the same reconciliation behavior.

Required tests:

- For each pre-commit point: exactly one interrupted event for the expired attempt and one later authoritative terminal event.
- For `AfterAuthoritativeCommit`: one authoritative terminal event, zero interrupted events for that attempt, terminal `succeeded`, cleared lease, and original IDs returned on resume/replay.
- Inject a post-commit sidecar/identity failure in the Store wrapper and prove a same-key retry cannot create another authoritative result.

### C3. The promised atomic audit boundary is bypassable, and the prescribed hash omits `project_id`

Evidence:

- Task 6 line 876 says no job, ingest, project, or evidence transition may bypass audit append.
- `Store::with_immediate_transaction` is public and hands any downstream caller a raw `rusqlite::Transaction` (`store/mod.rs` lines 256-274). It can directly insert/update `projects`, `job_runs`, `project_documents`, `scales`, `source_records`, or `corrections` without audit. Several of those tables have no immutability trigger; migration 1 only protects the six tables at lines 257-309.
- The prescribed audit hash fields at plan line 876 omit the nullable `audit_events.project_id`, although that column is security-relevant (`0001_foundation.sql` lines 192-210). Reassigning project scope would not change the prescribed hash.

Consequences:

- Repository encapsulation alone cannot establish the stated production boundary while raw public transactions remain available.
- An audit row’s project association is not tamper-evident under the frozen hash recipe.

Minimal plan amendment:

- Make the raw transaction primitive `pub(crate)` and expose only audited public services/repositories. Amend Task 3’s produced interface and rewrite its integration fixtures to prepare hostile file databases with verifier/test-only direct `rusqlite`, rather than retaining a production raw-write escape hatch. Add `crates/heleos-core/tests/migrations.rs` to the authorized scope for this compatibility edit.
- Define the audit preimage as one domain-separated canonical JCS object with explicit schema/version and every stored security-relevant field except `event_hash`: sequence, event ID, nullable project ID, actor, action, subject type/ID, before/after JSON values (not JSON strings), reason, occurred-at integer, and previous hash. Freeze the all-zero genesis hash and contiguous sequence rule.
- Repository transactions must return committed business outcomes (accepted, quarantine, replay, conflict) from the closure and translate conflict/quarantine to the public API only after commit. Returning `Err(IdempotencyConflict)` from inside the transaction would roll back the required denied event and audit record.

Required tests:

- A downstream compile-fail test proves raw transaction access is unavailable.
- Hostile direct-SQL fixtures mutate only `project_id`, sequence, previous hash, before/after JSON, or reason; every mutation makes verification non-clean.
- Fixed JCS/hash vectors cover nullable and non-null project IDs and multiple events in one transaction.
- Inject failure between every lifecycle row and audit append and prove complete rollback; conflict and quarantine must still return their public outcome after their event/audit transaction commits.

### C4. Resume has no specified immutable trust anchor for digest, limits, or parser version

Evidence:

- `job_runs.input_json` and `checkpoint_json` are mutable columns with no trigger (`0001_foundation.sql` lines 62-86).
- Line 850 requires modified frozen hashes to fail closed, but line 876 merely says resume “uses the checkpointed vault digest.” It does not say what authenticates that mutable checkpoint.
- `PdfProbe` exposes only `probe(...)` (`pdf/mod.rs` lines 12-19). After a pre-probe crash, a reopened engine cannot verify that it is resuming with the frozen parser/WASM/source/dependency/protocol identity. The actual provenance is available only in the outcome.
- The public trait permits a faulty/malicious adapter to return a mismatched digest, length, revision, page index/ID, excessive page vector, or unbounded provenance strings. Task 5 validates its concrete WASI implementation, but Task 6 is defined against the trait.

Consequences:

- Changing both mutable job JSON fields can redirect resume to a different valid vault object unless resume cross-checks an immutable record.
- A parser upgrade between attempts can silently change the authoritative manifest/result for one supposedly frozen job.
- A custom `PdfProbe` can violate resource and lineage invariants before manifest serialization.

Minimal plan amendment:

- The job-start/checkpoint audit event must contain the canonical frozen request: source digest/length, scoped key identity, exact limits and budget, absolute deadline, and exact probe provenance/version identity. Resume first verifies the audit chain, locates that immutable checkpoint event, requires exact equality with both job JSON copies, then opens/re-hashes the named vault object.
- Extend `PdfProbe` with a bounded immutable identity/provenance method, or require an independently supplied expected `PdfProbeProvenance` at engine construction and compare it to every returned outcome. This requires adding `pdf/mod.rs` (and the WASI implementation) to Task 6 scope.
- Validate every trait outcome again at the authority boundary: digest/length/revision echo, ordered contiguous indices, canonical page IDs, page count and geometries within frozen limits, bounded provenance strings, and a hard maximum canonical manifest byte length.

Required tests:

- Independently mutate input JSON only, checkpoint JSON only, both to another valid vault digest, and the matching audit event; all cases fail closed at the correct boundary.
- Resume with a different probe identity and reject before probing/authority.
- Malicious fake probes return mismatched echo fields, duplicate/out-of-order pages, wrong page IDs, and oversized provenance/manifest data; all leave no authoritative rows.

## Important findings

### I1. Idempotency namespace, equality, and bounded over-limit behavior are contradictory or incomplete

The database uniqueness rule is `(project_id, kind, idempotency_key)` (`0001_foundation.sql` line 80), so project and kind define the namespace and cannot also be “conflicts” for a lookup under that key as line 858 states. Also, digest alone is insufficient: changing limits, quota/budget, deadline, or frozen probe identity can change the result/manifest. Finally, an over-input-limit file cannot be fully hashed without violating the input bound, so it has no authoritative full digest to compare on replay.

Minimal amendment:

- Keep the existing project+kind namespace and remove project/kind from conflict comparison; or change the schema to globally unique keys. The scoped choice is smaller and avoids cross-project key disclosure.
- Define equality over digest plus every authority-affecting frozen field. Filename and actor remain attempt metadata and do not conflict.
- For a terminal over-limit attempt without a full digest, make any later same-key call a fail-closed conflict after at most `N+1` bytes; do not pretend two unbounded inputs are equal.
- Quarantine, replay, and conflict persistence must use the committed-business-outcome pattern from C3.

Tests: same literal key in two projects/kinds follows the chosen namespace; same scoped key/same bytes/different filename replays; changed digest/limits/budget/probe identity conflicts and persists one denied event+audit; digestless over-limit keys never become an unchecked replay.

### I2. Lease ownership, expiry, attempts, events, deadlines, and terminal invariants need exact semantics

The schema requires only that lease owner/expiry are both null or both non-null (`0001_foundation.sql` lines 70-85). It permits leases on queued/terminal jobs and null leases on running jobs. `ingest_events` has no attempt column or uniqueness constraint (lines 88-116). `resume(job_id, actor)` does not identify the lease claimant, and the error enum has no distinct lease-unavailable result (`error.rs` lines 7-56). The approved design also requires heartbeats, deadlines, budgets, and cancellation, while Task 6 exposes none of those semantics.

Minimal amendment:

- Give each engine a bounded opaque lease-owner ID distinct from actor. Define running iff lease fields are present; all other states clear them. Define expiry as `now >= lease_expires_at_ms`, checked arithmetic for expiry/deadline/attempt, queued attempt `0`, and increment exactly once when entering running.
- Materialize the old attempt’s interrupted event and both audited transitions (`running -> interrupted -> running`) in one immediate transaction. Put the attempt number in canonical event details and reject an existing terminal event for that `(job, attempt)` in repository logic; preferably add a schema column/unique constraint while migration 1 is still unreleased.
- Add a distinct typed `LeaseUnavailable` outcome. Freeze an absolute job deadline; recovery cannot refresh it. Either add heartbeat/cancel APIs now or explicitly amend the Foundation design/milestone to defer them.

Tests: exact `expiry-1` unavailable and `expiry` recoverable boundaries; foreign versus same-owner behavior; two sequential recoveries produce one interrupted event per old attempt; terminal jobs have no lease; overflow/clock-regression/deadline cases fail closed; a second recovery cannot duplicate an attempt event.

### I3. Quota is per `put_reader`, but intake writes two objects and has no aggregate/reservation rule

`VaultWriteBudget` is consumed independently by each call (`vault/mod.rs` lines 54-80), and each call serializes its own scan/check/publication under the vault lock (`lines 435-450, 945-988`). Task 6 must write the original and then a manifest. Reusing the full budget twice can retain more novel bytes than an intake budget; reserving outside the vault is a check-then-act race. A manifest quota failure after original publication necessarily leaves a valid unreferenced original.

Minimal amendment:

- State whether `max_retain_bytes` is per object or per intake. If per intake, debit only `newly_published` bytes from one checked remaining budget and pass that remainder to the manifest put. Do not claim an atomic two-object reservation with the current Vault API.
- State the lock hierarchy: the lifetime Store application lock is acquired before Vault use; no SQLite transaction may be open while acquiring a Vault lock or running `PdfProbe`; vault calls finish before each SQLite transaction. Never perform a separate capacity precheck—only `PutOutcome` decides.
- If all-or-nothing quota admission of both novel objects is required, add a bounded multi-object Vault reservation/publication API and Task 4 files/tests to scope. Otherwise explicitly permit a verified reported orphan when the second put loses capacity.

Tests: duplicate objects debit zero novel bytes; original-new/manifest-new exact `N/N+1` aggregate boundaries; two engines with distinct databases sharing one nearly-full vault cannot overshoot global quota; manifest quota failure commits only a quarantine/failure event+audit and no revision/sheet/evidence rows; lock barriers prove no SQLite transaction waits on Vault/PDF work.

### I4. Source-handle and Windows behavior need a frozen, testable OS contract

“One no-follow regular-file handle” is not enough detail for parity. The implementation must not use `canonicalize` or metadata-following prechecks as authority. On Unix it needs read-only `O_NOFOLLOW` plus same-handle regular-file/identity checks. On Windows it needs a read/attributes access mask, `FILE_FLAG_OPEN_REPARSE_POINT`, opened-handle rejection of every reparse tag, and share flags that deny delete and preferably write while intake owns the handle. The display name needs a bounded lossless encoding for non-UTF-8/UTF-16 names. Migration 1 still requires raw `source_path` strings in ingest/source rows (`0001_foundation.sql` lines 107-109 and 144-150), which conflicts with deriving “only” a safe display name and unnecessarily persists confidential local paths.

Minimal amendment:

- Specify exact Unix and Windows opens/rechecks and close-on-every-path RAII. Reuse the reviewed handle-bound reparse/identity patterns rather than path metadata.
- Freeze maximum bytes for idempotency keys, lease owners, source display names, project names, reasons, and every persisted JSON document.
- Persist only the bounded escaped display name; write a fixed redacted/empty sentinel to legacy `source_path`, or amend migration 1 to remove/redesign that column. Never serialize the ambient source path into public inspection, audit details, or errors.

Tests: final symlink, Windows symlink and non-symlink reparse point, directory/FIFO/device, non-UTF name, `N/N+1` display length, pathname replacement after open, attempted rename/delete/write on Windows while live, source bytes/hash unchanged, and no raw absolute path in any inspection/audit/event JSON.

### I5. “Safe orphan reporting” and cleanup/resource behavior are unspecified

The existing reconciliation is intentionally report-only. It enumerates staging and all objects into unbounded vectors/maps (`vault/reconcile.rs` lines 14-48 and 129 onward) and never repairs or deletes. A crash inside vault hard-link publication can leave a two-link final/staging inode; `open_verified` rejects non-single-link objects, so Task 6 resume cannot automatically use it. Task 6’s high-level fault points occur only around a completed Vault call and do not cover this lower-level state.

Minimal amendment:

- Define where orphan information is returned (a new `IngestEngine::reconcile_vault`, `ResumeReceipt`, or a bounded job-known orphan list). `FoundationInspection` currently does not include it.
- Resume must never adopt an arbitrary orphan. It may reuse only a digest authenticated by the frozen checkpoint and must call `open_verified`.
- State explicitly that Task 6 performs no automatic evidence deletion or repair. Either classify vault-internal two-link/staging crash states as a blocked owner-recovery condition, or add a separately authorized, fully enumerated Vault repair API and corresponding Task 4 scope/tests.
- Do not invoke the current full reconciliation automatically on an untrusted resume path unless enumeration/findings/bytes are capped or streamed with a non-clean truncation signal.

Tests: original-only and original+manifest unreferenced objects are reported deterministically without adoption/deletion; unknown or corrupt orphans are never attached; missing checkpoint reference fails; abandoned staging/two-link publication returns the documented blocked outcome; reconciliation at entry/finding limits terminates bounded and reports truncation.

### I6. Global immutable content admission is order-dependent under request-specific limits

`content_objects` has one immutable `accepted`/`quarantined` state per digest (`0001_foundation.sql` lines 18-34 and 257-264), while the evidence manifest deliberately includes exact request-specific limits (plan line 870). The same PDF can therefore quarantine under strict page/input/geometry limits and be accepted under looser valid limits. If the strict request inserts a quarantined content row first, the later accepted request cannot update it and the revision trigger rejects it (migration lines 213-222). If acceptance happens first, a later strict-limit quarantine leaves the same object globally accepted. The result depends on ingestion order.

Minimal amendment:

- Choose and freeze one of two models before coding. The smallest within the current schema is: no `content_objects` row is inserted for a quarantined attempt; `ingest_events.content_sha256` remains null and bounded canonical details record the computed digest/length/reason, while the vault blob is a reported unreferenced object. A later accepted policy may then insert the immutable accepted row. The more expressive model is a separate immutable admission table keyed by digest plus policy/probe fingerprint, which requires changing migration 1.
- Do not reinterpret “stored safely in the vault” as globally “accepted.” Preserve the existing trigger’s guarantee that hostile raw SQL cannot attach quarantined content to a revision.

Tests: ingest the same limit-sensitive PDF strict-then-loose and loose-then-strict; both orders produce the same accepted canonical revision plus the correct immutable per-attempt outcomes. Intrinsically corrupt/encrypted inputs never create an attachable accepted content row. Replays of each terminal policy outcome remain deterministic.

## Minimum plan/scope edits before implementation

At minimum, amend Task 6 to resolve C1-C4 and add these already-existing paths if the recommended contracts are selected:

- `crates/heleos-core/src/error.rs` for typed lease/outcome-unknown errors;
- `crates/heleos-core/src/pdf/mod.rs` and `src/pdf/wasi_host.rs` for frozen probe identity;
- `crates/heleos-core/tests/migrations.rs` when narrowing the public raw transaction API;
- `crates/heleos-core/migrations/0001_foundation.sql` if attempt uniqueness/admission modeling is fixed in schema;
- Vault source/tests only if bounded reconciliation, repair, or atomic multi-object reservation is required.

The “stage only eleven declared files” instruction at plan line 884 must be updated to match the adjudicated scope. Task 3/4 regression gates and the Windows Rust-only/native gates must be rerun for any touched shared boundary.

## Preflight acceptance condition

Task 6 is ready to implement when the authoritative plan states, without ambiguity:

1. which crash points are resumable versus retry-by-idempotency;
2. the post-commit outcome-unknown behavior;
3. the immutable resume trust anchor and frozen probe identity;
4. the idempotency namespace/equality and digestless over-limit rule;
5. exact lease/attempt/event/deadline semantics;
6. an enforceable non-bypassable audit API and complete hash preimage;
7. aggregate quota and lock ordering;
8. source-handle/Windows/path-redaction behavior; and
9. bounded orphan reporting plus the explicit no-repair or authorized-repair policy;
10. order-independent content-admission semantics for request-specific limits.
