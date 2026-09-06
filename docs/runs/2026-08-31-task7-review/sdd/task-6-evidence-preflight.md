# Task 6 evidence and verification preflight

Date: 2026-08-29  
Audited HEAD/base: `d90a1639c3b5e4276d6164157c2507565d4eab74`  
Mode: strictly read-only except this ignored report  
Scope: the complete Foundation 0.1 plan and design, Task 6 and Tasks 7–10 consumers, and the committed domain/store/vault/PDF/schema contracts

## Verdict

**BLOCKED pending plan clarification.** Task 6 is implementable on the committed Task 2–5 APIs and schema only after the plan freezes the public ownership model, canonical evidence bytes, audit hash input/report, inspection DTO, quota accounting, and hard-error semantics. The current prose leaves multiple incompatible implementations that would all appear plan-compliant but would produce different public JSON, hashes, rows, and Task 7–9 behavior.

The highest-risk defects are:

1. the fault matrix requires `resume(job_id, actor)` to recover states in which no durable vault checkpoint can exist, and incorrectly requires an interrupted event after an already committed authoritative transaction;
2. caller-varying tightening-only `PdfLimits` are embedded in the allegedly canonical manifest, so the same revision can have multiple manifest digests and multiple evidence rows;
3. immutable global `content_objects.admission_state` has no defined first-admission rule when the same bytes are quarantined under one requested policy and accepted under another;
4. audit hashing omits `project_id`, has no domain separator or exact typed hash document, and returns an undefined `AuditChainReport`;
5. `FoundationInspection` is a downstream release contract but has no schema, sort order, count definition, row cap, or cross-project lineage rule;
6. Foundation quota categories cannot safely be selected until after probing, while probing requires a permanently published `VerifiedObject`; Task 6 must either use a conservative pre-admission rule or reopen an earlier storage design;
7. the exact public constructors, borrows, receipts, idempotency-key type, read-only reader, and repository operations are absent.

No tracked-file change should begin until the Critical amendments below are adopted.

## Evidence from the committed contracts

- Task 6 promises public methods and DTOs at plan lines 836–876, but supplies only five method signatures. It does not define constructors, `AuditChainReport`, receipt fields, or any repository method despite promising “the exact persistence methods.”
- `Store` owns its writer or reader lock and exposes `with_immediate_transaction(&mut self, ...)`; it is not cloneable. A writer lock is held for the writer `Store` lifetime. A read-only `Store` rejects immediate transactions. This favors borrowed composition (`&mut Store` for commands and `&Store` for queries), not an unspecified owning engine.
- `Vault` is not cloneable. `Vault::put_reader` takes `&self`, internally locks the vault, and reports either `Stored` or `QuotaRejected { digest, byte_length }`. A duplicate may succeed with zero retainable bytes. `VerifiedObject` is consumed by `PdfProbe::probe`.
- `PdfLimits` is `Copy + Eq` but is not serializable. A Task 6-owned serializable snapshot DTO is therefore required; modifying `domain/state.rs` is not authorized by Task 6.
- `PdfProbeProvenance`, `PageMetadata`, `PageTransform`, and all PDF quarantine reasons are already serializable and exact. `PdfQuarantine::ingest_outcome()` is the authoritative PDF-reason mapping and must not be duplicated.
- `content_objects` has an immutable global `admission_state` and no media-type column. `evidence_objects` is immutable and unique only on `(project_id, document_revision_id, content_sha256)`. Thus different manifest digests for one revision are currently allowed.
- `ingest_events.content_sha256` is nullable but, when present, must reference `content_objects`. This supports a quota/over-limit event whose known digest/length lives only in `details_json`, but it cannot point at bytes that were not retained.
- `job_runs` is mutable and has one row per `(project_id, kind, idempotency_key)`. `ingest_events` and `audit_events` are immutable; there is no `failed_internal` ingest outcome.
- `audit_events` stores nullable `project_id`, JCS `before_json`/`after_json`, previous hash, and event hash. The current Task 6 hash prose does not include `project_id` even though it is authority-bearing state.
- Tasks 7–9 consume the Task 6 DTOs through CLI inspection, revision verification, backup verification, and black-box acceptance. They cannot independently repair ambiguous ordering or lineage.

## Critical findings and exact amendments

### C1. Freeze canonical ingest limits or the evidence manifest is not canonical

The plan says the manifest includes the “exact requested limits” and that equal frozen inputs and versions produce one digest. Task 5 permits callers to tighten every public PDF limit. Two different tightening profiles can both accept the same PDF and therefore produce two different manifests for one canonical revision. The current evidence uniqueness constraint allows both rows. `evidence_manifest_for_revision(revision)` then has no unique answer.

**Required amendment:** Task 6 ingestion must accept exactly `PdfLimits::default()`. Task 5’s direct probe API remains tightening-capable for focused callers and tests, but Foundation 0.1 authoritative intake rejects any non-default `IngestRequest.pdf_limits` with `HeleosError::PolicyDenied` before job creation, vault publication, or event creation. The manifest still records the exact requested limits, which are therefore the one frozen Foundation profile. A future migration may add versioned intake profiles.

Replace the relevant Step 3 sentence with:

> `IngestRequest.pdf_limits` must equal `PdfLimits::default()` component-for-component. Task 6 rejects a tightened or enlarged profile with `PolicyDenied` before creating a job or publishing bytes. This makes the requested-limit block part of one canonical Foundation evidence identity; Task 5 remains independently tightening-capable.

### C2. Define first admission for immutable `content_objects`

The current global/frozen prose says rejected bytes become `content_objects.admission_state = quarantined` and are never promoted. Without a first-admission rule, a later attempt could try to accept the same digest, which cannot pass the revision trigger. Timeouts and requested limits make this more than a theoretical case.

**Required amendment:** a retained digest’s first committed admission is sticky in Foundation 0.1.

> Before probing a digest already represented by `content_objects`, Task 6 loads its immutable admission. `accepted` content resolves through its canonical revision/manifest lineage and is handled as an accepted duplicate after project-association quota checks. `quarantined` content is never reprobed or promoted; a new idempotency key records the same typed quarantine class and first-admission reason/provenance in a new terminal attempt. Bytes present only as a vault orphan have no admission and are independently verified/probed before first authoritative commit. Quota-rejected bytes have no content row and may be retried later.

This rule must be tested across projects and after a crash-left vault orphan.

### C3. Correct the fault matrix and durable checkpoint ordering

The existing Step 2 requires every injected point, including `AfterJobStart`, `AfterVaultPublish`, and `AfterAuthoritativeCommit`, to expire a lease, resume the same job, and materialize one interrupted event. That is impossible in two cases:

- a real crash can occur after starting a job but before the original has been durably published/checkpointed; `resume(job_id, actor)` has no source handle and is forbidden to reopen the source;
- after `AfterAuthoritativeCommit`, the job and authoritative rows are already committed, so there is no running attempt to interrupt.

**Required replacement fault sequence:**

1. Freeze digest/length by hashing the already opened source handle, rewind it, and compare those frozen values with the independent vault copy result. Resolve an existing idempotency key before new publication.
2. Publish/verify the original in the vault. A process crash before the first job/checkpoint transaction leaves only a reportable orphan; retry is through `ingest` with the request, not `resume`.
3. In one immediate transaction create the queued job with its frozen input and initial vault-digest checkpoint plus a `job_created` audit event. `AfterVaultPublish` fires only after this transaction, so the named job is resumable.
4. Transition `queued -> running`, set attempt `1` and the 30-second lease, append the audit event, then fire `AfterJobStart`.
5. Probe, build/publish the manifest when accepted, and commit a bounded processing checkpoint sufficient to resume only from verified vault digests; then fire `AfterCheckpointCommit`.
6. Fire `BeforeAuthoritativeCommit`, then atomically commit the authoritative outcome, terminal job state, immutable ingest event, and audit events.
7. Fire `AfterAuthoritativeCommit` only after commit. A subsequent `resume` returns the stored authoritative receipt and creates no interrupted event.

Replace Step 2’s universal assertion with:

> `AfterVaultPublish`, `AfterJobStart`, `AfterCheckpointCommit`, and `BeforeAuthoritativeCommit` are reopened. A queued checkpoint resumes without an interrupted event. An expired running attempt first produces exactly one immutable `interrupted` ingest event and audit event, then resumes with the incremented attempt. `AfterAuthoritativeCommit` proves that the caller may observe an injected error after commit while `resume` returns the one committed result without interruption or duplication. A separate subprocess crash between vault publication and the initial job/checkpoint transaction proves an orphan plus safe same-request `ingest` retry; it is not represented as resumable by job ID.

### C4. Freeze the exact evidence manifest schema, media types, canonical bytes, and hash

The prose currently does not freeze nested field names, media types, manifest byte framing, size cap, page revalidation, or hash domain. Task 7 and Task 9 cannot independently infer these.

Add these Task 6-owned DTOs in `ingest/evidence.rs`, all `Serialize + Deserialize`, `#[serde(deny_unknown_fields)]`, and re-export them:

```rust
pub const EVIDENCE_MANIFEST_SCHEMA_V1: &str = "heleos.evidence-manifest/v1";
pub const PDF_MEDIA_TYPE: &str = "application/pdf";
pub const EVIDENCE_MANIFEST_MEDIA_TYPE: &str =
    "application/vnd.heleos.evidence-manifest+json;version=1";
pub const MAX_EVIDENCE_MANIFEST_BYTES: usize = 16 * 1024 * 1024;

pub struct EvidenceContentV1 {
    pub sha256: Sha256Digest,
    pub byte_length: u64,
    pub media_type: String,
}

pub struct EvidencePdfLimitsV1 {
    pub max_input_bytes: u64,
    pub max_pages: u32,
    pub max_indirect_objects: u32,
    pub max_nested_references: u32,
    pub max_metadata_bytes: u64,
    pub max_page_axis_points: u32,
    pub max_guest_memory_bytes: u64,
    pub max_instances: u32,
    pub max_tables: u32,
    pub max_fuel: u64,
    pub timeout_seconds: u64,
    pub max_protocol_output_bytes: u64,
}

pub struct EvidenceManifestV1 {
    pub schema: String,
    pub original: EvidenceContentV1,
    pub document_id: DocumentId,
    pub revision_id: RevisionId,
    pub pages: Vec<PageMetadata>,
    pub probe_provenance: PdfProbeProvenance,
    pub requested_limits: EvidencePdfLimitsV1,
}
```

Exact rules:

- `schema`, `original.media_type`, and every provenance literal must equal the constants/Task 5 contract.
- `document_id` and `revision_id` must both equal the canonical IDs of `original.sha256`.
- Pages are sorted by `index` ascending before construction and must already be a contiguous `0..n-1` sequence with no duplicate; each page ID is recomputed from the original digest/index, and all geometry is revalidated against the accepted `PdfInspection`.
- The page vector has at most 10,000 entries. No map or filesystem enumeration order enters it.
- `requested_limits` is a field-for-field Task 6-owned copy of the exact `PdfLimits`; Task 6 does not modify `PdfLimits` to add serde.
- Manifest bytes are exactly `canonical_json(&manifest)`, with no LF, BOM, or trailing byte. Reject empty output or output over 16 MiB.
- The vault/content digest is ordinary content addressing: `SHA256(exact_manifest_bytes)` with no domain prefix. The schema literal is the format/version domain; adding a prefix would make the digest differ from vault verification.
- The manifest contains no manifest digest, vault key, project, actor, job, evidence ID, idempotency key, source name/path, clock value, review state, or creation time.
- The accepted evidence row uses `extraction_method = "heleos.pdf-probe/v1"`, `review_state = "accepted"`, and bounded JCS `parameters_json` containing exactly `schema`, `original_media_type`, `manifest_media_type`, `probe_provenance`, and `requested_limits`. It points from the manifest content hash to the original parent hash and first authoritative job.

Because `content_objects` has no media column and Task 6 may not edit the Task 3 migration, media is durably bound in the manifest plus evidence parameters for accepted input and in ingest-event details for quarantine. `FoundationInspection` derives only the two exact media constants from those typed contexts. This is a narrow compatibility rule, not a claim that schema v1’s `content_objects` itself stores media type; the design’s broader table description should be corrected in a later migration.

### C5. Make revision evidence lookup unambiguous across projects

`evidence_manifest_for_revision(revision)` is ambiguous because evidence rows are project-scoped. The canonical manifest digest should be identical across projects, while evidence/job IDs differ.

Freeze this receipt:

```rust
pub struct EvidenceManifestLineage {
    pub project_id: ProjectId,
    pub evidence_id: EvidenceId,
    pub originating_job_id: JobId,
}

pub struct EvidenceManifestReceipt {
    pub manifest: EvidenceManifestV1,
    pub manifest_content_sha256: Sha256Digest,
    pub manifest_byte_length: u64,
    pub manifest_media_type: String,
    pub manifest_vault_key: String,
    pub original_vault_key: String,
    pub lineages: Vec<EvidenceManifestLineage>,
}
```

`lineages` is sorted by `(project UUID bytes, evidence UUID bytes, job UUID bytes)`. The query must require every accepted canonical evidence row for the revision to agree on manifest digest, parent digest, extraction method, parameters, parsed manifest, and database sheet geometry. Any disagreement is `Integrity`, never “pick first.” Receipt construction opens and verifies both vault objects, reads the manifest through a 16-MiB bounded reader, requires strict typed deserialization plus byte-for-byte JCS equality, and then validates database lineage. This makes the existing one-argument lookup usable by Task 7.

### C6. Freeze the audit hash input, domain, genesis, sequence, and report

Add a Task 6-owned `AuditEventId` UUID newtype and exact DTOs in `ingest/audit.rs`:

```rust
pub struct AuditEvent {
    pub id: AuditEventId,
    pub sequence: u64,
    pub project_id: Option<ProjectId>,
    pub actor: ActorId,
    pub action: AuditAction,
    pub subject_type: AuditSubjectType,
    pub subject_id: String,
    pub before: serde_json::Value,
    pub after: serde_json::Value,
    pub reason: String,
    pub occurred_at_ms: i64,
    pub previous_hash: Sha256Digest,
    pub event_hash: Sha256Digest,
}
```

The hash input is a private `AuditHashDocumentV1` with exactly the same fields except `event_hash`, and with `id` serialized under the field name `event_id`. It **must include `project_id`**. The hash is:

```text
SHA256("heleos-audit-event-v1\0" || JCS(AuditHashDocumentV1))
```

The JCS document contains `previous_hash` as lowercase hex and `before`/`after` as JSON values, not JSON-encoded strings. There is no LF. The first sequence is exactly `1`, the first previous hash is exactly 32 zero bytes, every next sequence is predecessor `+ 1`, and every next previous hash is the predecessor’s stored/recomputed event hash. Sequence values must fit both positive SQLite `INTEGER` and JCS’s exact integer range. An empty database is a valid chain with count `0` and zero head hash.

Freeze `AuditAction` to these snake-case values: `project_created`, `job_created`, `job_started`, `job_checkpointed`, `job_interrupted`, `job_resumed`, `job_succeeded`, `job_failed`, `ingest_accepted`, `ingest_quarantined`, `ingest_replayed`, `ingest_conflict_denied`, and `evidence_created`. Freeze `AuditSubjectType` to `project`, `job`, `ingest_attempt`, and `evidence`. Each mutation transaction appends the smallest complete set needed to bind its job transition and authority result; the accepted commit has both `ingest_accepted` and `evidence_created`, and terminal job state has its own event.

Freeze the verifier report:

```rust
pub struct AuditChainReport {
    pub schema: String, // exactly "heleos.audit-chain-report/v1"
    pub valid: bool,
    pub checked_event_count: u64,
    pub first_sequence: Option<u64>,
    pub last_sequence: Option<u64>,
    pub head_hash: Sha256Digest,
    pub findings: Vec<AuditChainFinding>,
    pub findings_truncated: bool,
}
```

`AuditChainFinding` is a tagged enum in stable declaration order: `InvalidRow`, `SequenceMismatch`, `PreviousHashMismatch`, `EventHashMismatch`, `NonCanonicalBefore`, and `NonCanonicalAfter`. Findings are emitted in `(observed sequence if valid else i64::MAX, event ID bytes if valid else empty, variant ordinal)` order, capped at 128; a 129th sets `findings_truncated`, and any finding or truncation makes `valid = false`. Verification streams rows ordered by `sequence, id`, does not load the chain into memory, counts with checked `u64`, and returns `Err` only for database/I/O inability to perform verification. Stored malformed values and cryptographic disagreements are report findings.

The global sentence saying “Audit events and ingest attempts are ... hash-chained” should be narrowed to: audit events are the one hash chain; every immutable ingest attempt has a corresponding audit event committed in the same transaction. `ingest_events` has no chain columns.

### C7. Freeze quota accounting before writing bytes

The current plan states category caps but not whether they count physical blobs, project associations, duplicate associations, or unclassified bytes. `VaultWriteBudget` enforces only novel physical bytes and cannot enforce project association quota for a zero-byte duplicate.

Adopt these exact Task 6 rules:

- Store totals are the sum of **distinct physical content-object lengths** in the category. A digest referenced by multiple projects is counted once in store totals.
- Project totals are the sum of **distinct digests associated with that project** through accepted revision lineage, accepted evidence lineage, or retained quarantine ingest events. Repeated attempts in one project add zero; first association in another project charges that project’s logical quota even when the physical blob is a vault duplicate.
- The overall project cap is 50 GiB. The evidence-derivative subset is 5 GiB/project and 50 GiB/store. The retained-quarantine subset is 2 GiB/project and 8 GiB/store. All arithmetic is checked `u64`; overflow is `Integrity`.
- While the `Store`’s exclusive writer lock is retained, snapshot category usage and association membership, then call the vault. The lock order is writer lock before vault lock. Do not hold a SQLite transaction open across sandbox work.
- For a novel original whose PDF admission is not yet known, `max_retain_bytes` is the minimum remaining overall-project, quarantine-project, and quarantine-store allowance. This conservative rule is required because Task 5 can probe only a permanently published `VerifiedObject` and the vault has no deletion/promotion API. It may reject an otherwise acceptable PDF when quarantine headroom is exhausted; that is the explicit Foundation 0.1 fail-closed tradeoff.
- For a manifest, the budget is the minimum remaining overall-project, evidence-project, and evidence-store allowance. A physical duplicate still undergoes the project-association check.
- `PutOutcome::QuotaRejected` creates no `content_objects` row and sets `ingest_events.content_sha256 = NULL`; bounded JCS details and the receipt carry the known digest and length. It is `QuarantinedLimit` and a terminal successful classification, not a hard `Quota` error.
- A Foundation-input-length violation known from the opened regular-file handle is also `QuarantinedLimit`; when the digest was not safely computed, both row digest and receipt digest are `None`, while the known length remains in details.
- If original bytes were published but the manifest cannot be retained under derivative quota, the original is committed as quarantined with exact reason `evidence_manifest_quota`, the job ends as a successful quarantine classification, and no revision/sheet/evidence row is created. This first admission remains sticky.

If the owner rejects the conservative unclassified-byte rule, Task 6 cannot solve the quota contract in its authorized files; an earlier task must add a deletable private probe-staging capability or a two-phase vault admission design.

### C8. Separate typed quarantine from trusted hard errors

Use this precedence and terminal mapping:

1. Invalid source type/no-follow open, invalid IDs, missing project, non-default limits, or malformed request: hard `Err`, no job/event/audit because no intake authority was created.
2. Existing idempotency key: exact project/kind/digest match records `IdempotentReplay`; any mismatch records `DeniedConflict` plus audit in one committed transaction and then returns `IdempotencyConflict`. No vault write or probe occurs.
3. Existing immutable content admission: sticky accepted duplicate or sticky quarantine, before a new probe.
4. Novel-byte vault quota/input cap: `QuarantinedLimit` as specified above.
5. `PdfProbeOutcome::Quarantined`: use only `PdfQuarantine::ingest_outcome()`; persist the complete typed reason/provenance in bounded JCS details. The job state is `Succeeded`, because intake classification completed, while no revision/sheet/evidence row is created.
6. Accepted probe followed by project/category association or manifest quota denial: `QuarantinedLimit`, job `Succeeded`, no authoritative document/evidence rows.
7. Task 5 hard errors (artifact/manifest mismatch, revision mismatch, local I/O/capacity/permission/sync/cleanup, trusted-host integrity/policy setup) remain hard `Err`, never quarantine. If a job exists and the database remains safe, transition running to `Failed` and append `job_failed` audit data before returning the original error. There is no ingest event because the exact Task 2 enum has no internal-failure outcome. If persistence itself is unsafe, leave the leased job for expired-attempt recovery.
8. Injected faults deliberately bypass normal failure terminalization so restart tests model process loss.

Amend the broad design statement “every attempted intake” to “every classifiable terminal intake plus every recovered interruption”; pre-job validation failures and trusted internal failures are represented by no ingest row or by `job_runs.failed` plus audit. Do not overload `DeniedConflict`, `Interrupted`, or a quarantine outcome for internal errors.

Accepted outcome names are based on authoritative database identity, not `StoredObject.newly_published`: `AcceptedNew` means this commit creates the canonical revision; `AcceptedDuplicate` means it reuses a preexisting canonical revision. A crash-left vault orphan can therefore be physically duplicate while authoritatively `AcceptedNew`.

## Important findings and amendments

### I1. Freeze constructors, borrows, and read-only composition

The public API should borrow the existing non-cloneable resources:

```rust
pub struct ProjectService<'a> { /* private */ }
impl<'a> ProjectService<'a> {
    pub fn new(
        store: &'a mut Store,
        clock: &'a dyn Clock,
        ids: &'a dyn IdGenerator,
    ) -> Result<Self>;
    pub fn create(&mut self, request: ProjectCreateRequest) -> Result<ProjectReceipt>;
}

pub struct IngestEngine<'a> { /* private */ }
impl<'a> IngestEngine<'a> {
    pub fn new(
        store: &'a mut Store,
        vault: &'a Vault,
        probe: &'a dyn PdfProbe,
        clock: &'a dyn Clock,
        ids: &'a dyn IdGenerator,
    ) -> Result<Self>;
    pub fn with_fault_injector(
        store: &'a mut Store,
        vault: &'a Vault,
        probe: &'a dyn PdfProbe,
        clock: &'a dyn Clock,
        ids: &'a dyn IdGenerator,
        faults: &'a dyn FaultInjector,
    ) -> Result<Self>;
}

pub struct FoundationReader<'a> { /* private */ }
impl<'a> FoundationReader<'a> {
    pub fn new(store: &'a Store, vault: &'a Vault) -> Self;
    pub fn verify_audit_chain(&self) -> Result<AuditChainReport>;
    pub fn evidence_manifest_for_revision(
        &self,
        revision: RevisionId,
    ) -> Result<EvidenceManifestReceipt>;
    pub fn inspect_foundation(&self, project: ProjectId) -> Result<FoundationInspection>;
}
```

`IngestEngine` may delegate its three read methods to `FoundationReader` for the originally promised surface, but Task 7 inspection/verification must use `FoundationReader` with `Store::open_read_only`; it must not acquire a writer solely to inspect. `new` requires a writer-capable `Store`, and `FoundationReader` accepts either reader or writer.

`FaultInjector::inject(FaultPoint) -> Result<()>` is invoked only at the exact boundaries. The normal constructor uses an internal no-op injector. Task 7 exposes no fault option. The injected error is propagated without normal terminalization.

### I2. Add the missing Task 6-owned request and receipt types

Add without touching Task 2 domain files:

- `IdempotencyKey`: private `String`, only `TryFrom<&str>/String`, exactly 1..128 UTF-8 bytes, rejects Unicode control characters, `as_str()` accessor, serde as a string.
- `AuditEventId`: UUID newtype in `ingest/audit.rs`.
- `ProjectCreateRequest { project_id: Option<ProjectId>, name, actor, data_class }` and `ProjectReceipt { project_id, created_at_ms, audit_event_id }`.
- `IngestRequest { project_id, source: IntakeSource, idempotency_key: IdempotencyKey, actor, pdf_limits }`.
- `IngestReceipt { ingest_event_id, authoritative_job_id, attempt, outcome, content_sha256: Option<Sha256Digest>, byte_length, document_id: Option<DocumentId>, revision_id: Option<RevisionId>, sheet_ids, evidence_manifest: Option<EvidenceManifestReceipt> }`.
- `ResumeReceipt { job_id, resumed_attempt: Option<u32>, interrupted_event_id: Option<IngestEventId>, receipt: IngestReceipt }`. Completed jobs use both optional fields as `None` and return the stored receipt.

`IntakeSource` owns exactly one opened no-follow regular-file handle, a lossless encoded source-path DTO, a safely escaped display name, and its opened-handle identity. It is consumed by `IngestRequest`, has no `Clone`, serde, path reopen method, raw-handle export, or write implementation. The `source_path` database column stores the canonical JCS text of `EncodedIntakePath { encoding, value }` using `utf8`, `unix_bytes_hex`, or `windows_utf16_units_hex`; inspection never returns it. The display name is diagnostic-only and escaped before persistence/output.

### I3. Freeze `FoundationInspection` now, not in Task 9

Use these exact top-level fields, all strict serializable DTOs:

```rust
pub struct FoundationInspection {
    pub schema: String, // exactly "heleos.foundation-inspection/v1"
    pub project_id: ProjectId,
    pub counts: FoundationCounts,
    pub content_objects: Vec<FoundationContentObject>,
    pub document_ids: Vec<DocumentId>,
    pub revision_ids: Vec<RevisionId>,
    pub sheets: Vec<FoundationSheet>,
    pub evidence: Vec<FoundationEvidenceLineage>,
    pub intake_events: Vec<FoundationIntakeEvent>,
    pub jobs: Vec<FoundationJob>,
}

pub struct FoundationCounts {
    pub content_objects: u64,
    pub documents: u64,
    pub revisions: u64,
    pub project_documents: u64,
    pub sheets: u64,
    pub evidence_objects: u64,
    pub ingest_events: u64,
    pub jobs: u64,
    pub audit_events: u64,
}
```

Required nested fields:

- `FoundationContentObject`: digest, byte length, derived exact media type, admission state, vault key, and optional typed quarantine reason.
- `FoundationSheet`: sheet ID, revision ID, zero-based index, dimensions, unit, rotation, transform, and parent digest.
- `FoundationEvidenceLineage`: evidence ID, originating job ID, document/revision IDs, original digest/length/vault key/media type, manifest digest/length/vault key/media type, extraction method, requested limits, probe provenance, and review state.
- `FoundationIntakeEvent`: event ID, optional job ID, optional retained content digest, outcome, and terminal time. It excludes source name/path, idempotency key, actor, and raw details.
- `FoundationJob`: job ID, fixed kind, state, attempt, created/updated times, and optional terminal reason. It excludes lease owner, idempotency key, source input, budget, and checkpoint JSON.

Project-scoped content is the distinct union of accepted revision originals, accepted evidence manifests, and retained quarantine digests referenced by that project. It never includes another project’s unshared content or quota-rejected non-retained digests. `counts` are database counts under those same definitions and must equal the corresponding vector lengths where a vector exists.

Exact ordering:

1. content objects by digest bytes;
2. document and revision IDs by digest bytes;
3. sheets by `(revision digest, page index, sheet digest)`;
4. evidence by `(revision digest, manifest digest, evidence UUID bytes)`;
5. intake events by `(terminal_at_ms, event UUID bytes)`;
6. jobs by `(created_at_ms, job UUID bytes)`.

Reject more than 100,000 total emitted nested rows or canonical inspection JSON over 16 MiB with `ResourceLimit`; no silent truncation. SQL insertion order must never affect JSON. Task 9’s black-box test must compare the exact schema and ordering, not merely selected fields.

### I4. Define the repository boundary promised by Task 6

`store/ingest_repository.rs` should expose only crate-private command/query methods on `Store`, taking task-owned command structs rather than long tuples. Freeze these responsibilities/names in the plan:

- `project_create_with_audit`
- `idempotency_lookup`
- `quota_snapshot`
- `job_create_with_checkpoint_and_audit`
- `job_start_or_resume_with_audit`
- `job_checkpoint_with_audit`
- `job_recover_expired_attempt_with_audit`
- `accepted_intake_commit`
- `quarantined_intake_commit`
- `replay_attempt_commit`
- `conflict_attempt_commit`
- `job_fail_with_audit`
- `audit_chain_rows`
- `revision_evidence_rows`
- `foundation_inspection_rows`
- `vault_inventory_rows`

Every `*_commit` method owns exactly one `Store::with_immediate_transaction` call and performs all relevant row writes plus audit append inside it. Query methods perform no mutation and work on a read-only `Store`. No production caller receives `rusqlite::Connection`, `Transaction`, or raw SQL access.

### I5. Bind job attempts and lease behavior

- A new queued job has attempt `0` and no lease.
- First start is `queued -> running`, sets attempt `1`, a generated opaque UUID lease owner, and `lease_expires_at_ms = checked(now + 30_000)`.
- Recovery of an expired running attempt atomically sets `interrupted`, clears the lease, appends exactly one interrupted ingest event for that attempt, and audits it. Repeated recovery is idempotent.
- Resume then performs `interrupted -> running`, increments attempt by one with checked arithmetic, installs a new 30-second lease, and audits it.
- A queued checkpoint may start without an interrupted event.
- An unexpired foreign lease returns exactly `HeleosError::WriterBusy` in Foundation 0.1; this is the only available typed contention error without editing Task 3’s error file.
- Success, quarantine classification, and handled hard failure clear the lease. Quarantine is `Succeeded`; trusted internal failure is `Failed`.
- `Succeeded`, `Failed`, and `Cancelled` are terminal. `resume` on `Succeeded` returns the stored receipt; `resume` on `Failed` or `Cancelled` returns `InvalidStateTransition`.

### I6. Resolve wording contradictions in the plan/design

- Frozen “Rejected bytes are stored” conflicts with quota rejection not retaining bytes. Add: “except quota/input-cap rejection when retention is forbidden; those attempts record known evidence in immutable event details with a null content foreign key.”
- “Reconciliation reports and quarantines metadata inconsistencies” should say “reports typed findings”; Task 4 reconciliation performs no database write or permission mutation.
- The approved design’s final section still calls the design “proposed” and says approval is required. This is outside Task 6 implementation but contradicts its approved status and should be corrected in a documentation-owned task.

## Exact minimum caps

Add named Task 6 constants and boundary tests for:

| Item | Exact cap/behavior |
|---|---|
| idempotency key | 1..128 UTF-8 bytes; controls rejected |
| project name | 1..256 UTF-8 bytes; controls rejected |
| encoded source display name | 4 KiB UTF-8 |
| encoded source path JCS | 32 KiB |
| budget/input/details/parameters JSON | 1 MiB each |
| checkpoint JSON | 8 MiB |
| audit `before` and `after` JCS | 1 MiB each |
| audit action/subject type | fixed enums only |
| audit subject ID | 256 UTF-8 bytes, no controls |
| audit reason | 1 KiB UTF-8, no controls except ordinary whitespace policy explicitly tested |
| audit findings | 128; 129th sets truncation and invalid |
| evidence pages | 10,000 |
| evidence manifest bytes | 16 MiB, no LF/trailing byte |
| evidence lineages per revision | 100,000 and also subject to inspection cap |
| inspection nested rows | 100,000 total, no truncation |
| inspection canonical JSON | 16 MiB |

All sizes are checked before allocation where possible, all additions/multiplications are checked, and exact `N` succeeds while `N + 1` returns the specified typed error/outcome.

## Minimum adversarial test matrix

### Evidence bytes and retrieval

1. Golden exact manifest JCS bytes and raw SHA-256; no LF/BOM/trailing byte.
2. Declaration/input permutation yields identical page order, bytes, and digest.
3. Wrong/gapped/duplicate page index, wrong page ID, wrong content/revision/document digest, wrong media literal, wrong provenance field, unknown/missing field, noncanonical JSON, and one-byte geometry change all fail.
4. Every requested-limit field is present in the golden; any non-default intake profile is denied before a job/vault write.
5. Exact 10,000-page and 16-MiB boundaries use bounded/private seams; `N + 1` fails.
6. Retrieval detects manifest/object bit flip, truncation, wrong DB length/key/parent, wrong evidence parameters, wrong sheet geometry, wrong review state, and conflicting cross-project manifest rows.
7. Two projects share one manifest digest but retain distinct sorted lineage records; two attempts in one project retain the first evidence job.
8. Manifest media and original media survive inspection, backup verify, restore, and revision verify through typed lineage.

### Audit chain

1. Golden first and second event JCS/hash vectors, including zero genesis and project ID.
2. Mutate each hash-input field independently, especially `project_id`, `before`, `after`, sequence, and previous hash.
3. Gap, reorder, duplicate sequence/event hash, wrong genesis, wrong head, invalid UUID/digest, noncanonical stored JSON, and malformed row produce deterministic findings.
4. Empty chain is valid; exact 128 findings are not truncated; a 129th sets truncation; insertion order does not alter report order.
5. Every project/job/checkpoint/interruption/replay/conflict/quarantine/accepted/evidence/failed mutation has its corresponding same-transaction audit event; forced transaction rollback leaves neither side.

### Inspection

1. Unrelated-project rows are excluded; shared global revision/content appears only when associated with the requested project.
2. Randomized insertion order produces byte-identical canonical inspection JSON.
3. Counts match definitions and vectors after accepted new, accepted duplicate, replay, cross-project duplicate, near duplicate, retained quarantine, non-retained quota rejection, interruption/resume, and hard failure.
4. Source paths/names, raw bytes, idempotency keys, leases, checkpoint/input JSON, and actors are absent from inspection JSON.
5. Exact nested-row/byte caps fail closed without partial DTOs.

### Quarantine, hard errors, quotas, and precedence

1. Every `PdfQuarantineReason` maps only through `ingest_outcome()` and persists exact reason/provenance.
2. Vault quota and input cap yield `QuarantinedLimit`, a successful terminal job, null event FK, and known digest/length only when safely known.
3. Artifact/provenance/revision mismatch, local I/O, permission, capacity, sync, cleanup, and trusted-host integrity remain hard errors and never create a quarantine event.
4. Idempotency replay/conflict is decided before new quota/probe work; conflict commits event/audit before returning `IdempotencyConflict`.
5. Sticky accepted/quarantined admission is tested with a new key and another project.
6. Project/store overall, evidence, and quarantine caps cover exact `N`, `N + 1`, zero-byte physical duplicate, new project association to an existing blob, same-project repeated association, checked overflow, and conservative unclassified-byte rejection.
7. Manifest quota failure never leaves a revision/sheet/evidence row; the original is either the exact specified quarantine row or a reportable orphan when failure occurred before any authoritative transaction.

### Crash/restart and source safety

1. Corrected behavior at every fault point: queued resume without interruption, expired running resume with one interruption, and post-authoritative resume without interruption.
2. Subprocess crash between vault publication and initial job/checkpoint transaction yields only an orphan and safe request retry.
3. Resume opens only checkpoint digests from the vault and never the original source path; mutate/replace/delete source after checkpoint.
4. Eager source hash, vault copy hash/length, and final source-handle identity disagreeing at any bracket is a hard failure.
5. Hostile Unicode/control/non-UTF-8 source names and SQL payloads remain bound data; source bytes and metadata are unchanged.

## Suggested copy-ready Task 6 interface paragraph

Replace the existing Interfaces paragraph with:

> **Consumes:** one writer `Store` borrowed mutably for project/intake commands; one reader or writer `Store` borrowed immutably for verification; a borrowed non-cloneable `Vault`; a borrowed `PdfProbe`; borrowed injected `Clock` and `IdGenerator`; `ProjectCreateRequest`; consumed `IngestRequest` owning one `IntakeSource`; and a test-only supplied `FaultInjector`. Task 6 authoritative intake accepts only the exact default Foundation PDF limit profile.
>
> **Produces:** `ProjectService::{new,create}`, `IngestEngine::{new,with_fault_injector,ingest,resume,verify_audit_chain,evidence_manifest_for_revision,inspect_foundation}`, `FoundationReader::{new,verify_audit_chain,evidence_manifest_for_revision,inspect_foundation}`, the strict request/receipt/inspection/evidence/audit DTOs frozen below, exact media/schema constants, and only the crate-private repository operations named below. No API exposes a source reopen path, mutable evidence, raw SQL, or production fault flag.

## Final preflight disposition

Task 6 should not start RED tests from the current prose because tests would themselves choose unresolved public truth. Amend C1–C8 first, then freeze the DTO/JCS/hash golden tests before implementation. With the conservative quota rule and default-only authoritative limits, all recommended behavior is implementable in Task 6’s declared files on the committed schema. Without those two choices, an earlier storage/schema task must be reopened; Task 6 alone cannot safely delete a post-probe object, promote quarantined immutable content, or choose one canonical manifest among caller-varying limits.
