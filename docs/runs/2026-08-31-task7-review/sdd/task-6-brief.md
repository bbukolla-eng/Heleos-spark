### Task 6: Implement Crash-Safe Jobs, Audit, and Intake

**Files:**
- Modify: `crates/heleos-core/migrations/0001_foundation.sql`
- Modify: `crates/heleos-core/src/lib.rs`
- Modify: `crates/heleos-core/src/error.rs`
- Modify: `crates/heleos-core/src/pdf/mod.rs`
- Modify: `crates/heleos-core/src/pdf/geometry.rs`
- Modify: `crates/heleos-core/src/pdf/wasi_host.rs`
- Modify: `crates/heleos-core/src/store/mod.rs`
- Modify: `crates/heleos-core/src/vault/mod.rs`
- Create: `crates/heleos-core/src/store/ingest_repository.rs`
- Create: `crates/heleos-core/src/ingest/mod.rs`
- Create: `crates/heleos-core/src/ingest/job.rs`
- Create: `crates/heleos-core/src/ingest/audit.rs`
- Create: `crates/heleos-core/src/ingest/evidence.rs`
- Create: `crates/heleos-core/src/ingest/inspection.rs`
- Create: `crates/heleos-core/src/ingest/fault.rs`
- Modify: `crates/heleos-core/tests/migrations.rs`
- Create: `crates/heleos-core/tests/ingest.rs`
- Create: `crates/heleos-core/tests/restart.rs`

**Interfaces:**
- Consumes: one writer-capable `Store`, its paired `Vault`, `PdfProbe`, `Clock`, `IdGenerator`, a project, one already opened source, a scoped idempotency key, actor, the exact Foundation PDF profile, and an optional test fault injector.
- Produces: `ProjectService::{new,create}`, `IngestEngine::{new,with_fault_injector,ingest,resume,verify_audit_chain,evidence_manifest_for_revision,inspect_foundation}`, `FoundationReader::{new,verify_audit_chain,evidence_manifest_for_revision,inspect_foundation}`, strict request/receipt/inspection/evidence/audit DTOs, `FaultPoint`, `FaultInjector`, and only the crate-private repository operations named below. No public API exposes raw SQL, a source reopen path, mutable evidence, or a production fault flag.

**Controller rulings from the Task 6 preflight:**

- Foundation authoritative intake accepts only `PdfLimits::default()` component-for-component. Task 5 remains tightening-capable when used directly, but Task 6 returns `PolicyDenied` before a job, event, audit row, or vault write for any other profile. This makes one canonical evidence manifest possible for a revision.
- Idempotency keys are scoped by the existing `(project_id, kind)` uniqueness tuple; Task 6's only kind is the literal `pdf_ingest`. The same key text in another project or kind is independent. Within the tuple, equality covers the complete source digest/length, default limit DTO, fixed budget/deadline profile, and expected probe provenance. Actor and safe display name are attempt metadata and do not conflict. A first terminal input-cap attempt without a complete digest can never prove equality; a later same-key call is a persisted `DeniedConflict`.
- A retained digest's first committed admission is sticky. Before reuse, its database digest/length/key/media/reason and every required original/manifest vault object are opened no-follow and re-hashed through `Vault`; missing, corrupt, or inconsistent authority is hard `Integrity`. Accepted content then resolves through its canonical revision/manifest lineage and becomes an accepted duplicate after project-association quota checks. Quarantined content is never re-probed or promoted and a new key records the same typed reason/provenance. A verified vault-only object has no admission and may be independently probed. An input-cap, original-retention-quota, or project-association-quota outcome has no new content/project association and may be retried under a new key. An evidence-manifest-quota outcome instead follows the retained sticky-quarantine rule above.
- The lifetime `Store` writer lock is the serialized database authority and is always acquired before a vault lock. No SQLite transaction spans source hashing, a vault operation, or PDF sandbox work. Task 6 assumes one authoritative database/vault pair; sharing one vault among unrelated metadata databases is not a supported quota domain.
- Task 6 adds a crate-private, bounded `VaultInventory` query authority. It is the distinct union of every committed `content_objects` row and every verified original or manifest named by a queued, running, or interrupted job checkpoint. The query obtains a count first, rejects `100_001`, fetches at most `count + 1`, and requires exact count agreement. It binds each nonterminal row to its latest immutable job-audit snapshot, validates canonical input/budget/checkpoint JSON and exact digest/key/length/media relationships, deduplicates by digest, and returns `Integrity` on any conflicting key, length, media, checkpoint, or audit authority. Every SQLite statement/row handle is dropped before the caller enters `Vault`; no database transaction spans the vault operation.
- Task 6 also adds one crate-private atomic Vault scan-and-publish operation. A single exclusive vault lock is acquired before inventory validation and remains held through the complete no-follow scan, bounded current-source hash, staging, fsync/link publication, winner validation, and cleanup. The scan streams canonical sharded leaves and never materializes a filesystem-sized `Vec`; it accepts at most `100_000` final objects and returns `ResourceLimit` on the `100_001`st. For every leaf it parses the canonical digest pathname, capability-opens the leaf, and validates the fixed layout, regular/non-reparse/non-symlink type, private permissions, `nlink == 1`, frozen no-delete-share retained-handle contract, and opened-handle length at most 256 MiB, while computing checked physical byte/count totals no greater than 500 GiB/100,000. Inventory-bound objects must match the observed canonical key and opened-handle length and be observed exactly once; a missing, duplicate, or conflicting inventory member is `Integrity`. Unrelated final leaves are not content-rehashed on every intake. Unknown leaves conservatively count their complete opened-handle length even if a later explicit content verification discovers corruption, so corruption can never reduce quota usage. Only the current requested object or existing winner is fully opened and hashed during this atomic publication operation. A physical object absent from `VaultInventory` is never adopted, deleted, or repaired: it is a crash orphan and its bytes conservatively debit the retained-quarantine category.
- The atomic operation receives the remaining orphan allowance derived under the Store writer lock from committed quarantine content plus distinct nonterminal quarantine reservations. Existing crash-orphan bytes greater than that supplied allowance are `Integrity`. At the exact allowance, with zero remainder, the exact current request already present as a fully verified orphan succeeds as a zero-debit duplicate; a novel request is still completely bounded-hashed, returns `PutOutcome::QuotaRejected`, and leaves no staged/final object. The current orphan is counted once rather than debited again. This operation governs both original-PDF and evidence-manifest Task 6 publications and prevents two `Vault` instances for the same root from racing the scan and link. Physical 500-GiB overall authority remains the Vault scan; evidence and project/category logical authority remains the database committed-plus-reservation snapshot. Lock order stays writer then vault, with no SQLite transaction held.
- `Store::with_immediate_transaction` becomes crate-private. Task 3's external schema tests prepare hostile rows through verifier-only direct `rusqlite` after releasing the application store; Store-specific transaction/configuration checks move to private unit coverage. Direct out-of-band database writes remain outside the global same-principal threat boundary, but no normal public core API can bypass the audited repository.
- Task 6 and Task 9's exact two-object acceptance—one original PDF plus one canonical evidence manifest, with no duplicate copy of either—supersedes the design document's stale shorthand “one stored content object.” The canonical document/revision still derives only from the original PDF digest.

- [ ] **Step 1: Write failing state, audit, and double-ingest tests**

Use a fake clock/ID source and synthetic fixtures. Require audited project creation; one accepted PDF content object, one canonical JCS evidence-manifest content object, one document, one revision, one project-document link, one evidence row, stable sheet rows, and two immutable terminal intake events after two distinct keys ingest identical bytes. The manifest points to the original; its evidence row points to the revision and first authoritative job. Same-key replay creates an auditable replay attempt without a second job/result. Same bytes/new filename deduplicates, a cross-project duplicate creates only the new project association/evidence lineage, and a near duplicate creates new content/document/revision/manifest. Corrupt, encrypted, active-content, project/store/category quota, input-cap, and manifest-quota cases create the exact terminal quarantine outcome and no revision/sheet/accepted-evidence row. Test sticky accepted/quarantined admission, non-retained quota-rejection retry under a new key, default-limit enforcement, every typed receipt invariant, insertion-order-independent inspection, cross-project revision receipt lineages, and unchanged source bytes/hash.

Freeze and test these schema repairs while migration 1 is still unreleased:

- `content_objects.media_type` is non-null and exactly `application/pdf` or `application/vnd.heleos.evidence-manifest+json;version=1`; an accepted row has null quarantine reason, while a quarantined row has a nonempty canonical JCS typed reason capped at 1 MiB.
- `ingest_events.attempt` is nullable; authoritative/interrupted outcomes require a positive attempt and are unique by `(job_id, attempt)`, while replay/conflict rows require null. Both legacy `ingest_events.source_path` and `source_records.source_path` are always the literal `<redacted>`.
- A queued job is attempt `0`, has no lease/terminal reason, and has a non-null deadline. Running is the only state with both lease fields and has positive attempt. Terminal states clear the lease and require a reason. Triggers reject delete, frozen identity/input/budget changes, illegal state transitions, attempt jumps, and same-state mutation except the exact running checkpoint update. The legal transition set remains Task 2's set.
- Job input/budget/details/parameters JSON is capped at 1 MiB, checkpoint JSON at 8 MiB, audit before/after at 1 MiB each, and every field remains valid byte-exact JCS. SQLite byte caps use UTF-8 byte length rather than character count. Migration checks also freeze project name, idempotency key, safe source name, audit subject/reason, fixed action/subject/kind/terminal literals, attempt `0..=16`, UUID lease-owner shape, and every authoritative timestamp/sequence to its Task 6/JCS-safe range. Migration tests cover every new check/trigger and the now-private raw transaction boundary.

Run: `cargo test -p heleos-core --test ingest`

Expected: FAIL because `IngestEngine` does not exist.

- [ ] **Step 2: Write failing fault-matrix and audit-chain tests**

Inject exactly `AfterVaultPublish`, `AfterJobStart`, `AfterCheckpointCommit`, `BeforeAuthoritativeCommit`, and `AfterAuthoritativeCommit` at the boundaries below. Reopen database and vault after each. The named `AfterVaultPublish` occurs only after the queued job and frozen vault checkpoint commit, so queued resume creates no interrupted event. For `AfterJobStart`, `AfterCheckpointCommit`, and `BeforeAuthoritativeCommit`, advance the fake clock to the exact lease expiry; resume atomically materializes one interrupted event for that old attempt, then starts the next attempt. `AfterAuthoritativeCommit` is an acknowledgement failure after the helper and all post-commit checks succeeded: reopen observes `succeeded`, returns the original IDs through an auditable replay, and creates no interruption or duplicate authority. A separate subprocess publishes the original and exits before the initial job/checkpoint transaction; no job exists, inventory reconciliation reports the verified orphan without adopting/deleting it, its bytes remain conservatively charged to the quarantine category, and repeating `ingest` with the same source safely reuses only that request digest without double-debiting it.

Prove exact `expiry-1` lease denial and `expiry` recovery; queued/running/interrupted/terminal invariants; attempt overflow and the fixed deadline; missing/corrupt checkpoint object; input/checkpoint/audit-hash mutation; probe-identity change; illegal SQL transition; SQL payloads; source mutation between fingerprint/vault/post-read; all five fault points; and a Store post-commit-check failure returning `CommitOutcomeUnknown`. The inventory/atomic-publication matrix pins exact inventory and filesystem counts `N`/`N + 1`, count drift, duplicate digest agreement, digest/key/length/media conflicts, missing inventory objects, inventory key/length/layout/type/permission/link structural corruption, current requested object/existing-winner content corruption, unrelated same-length content corruption that remains fully byte-debited by Task 6 and is rejected by Task 7's full referenced-object rehash, object-size and checked-total overflow, `crash-after-original-link-before-job`, `crash-after-manifest-link-before-processing-checkpoint`, original and manifest objects orphaned by failed jobs, current-orphan no-double-debit at zero headroom, two independent `Vault` handles racing one root, and atomic publication of both originals and manifests. Every crash/orphan case reopens the Vault and proves the next publication debits the orphan; the manifest-loss window additionally retries the exact same manifest and proves zero-debit recovery. A retry after an unknown outcome uses only the same job/scoped key and can never create a second result. Injected faults deliberately bypass ordinary failure terminalization so they model process loss.

Run: `cargo test -p heleos-core --test restart`

Expected: FAIL with missing fault and resume contracts.

- [ ] **Step 3: Implement finite jobs and idempotency**

`IntakeSource::open` owns one handle and never retains or reopens the ambient path. Unix uses a read-only `O_NOFOLLOW | O_CLOEXEC` open and same-handle regular-file/device/inode checks. Windows uses `GENERIC_READ | READ_CONTROL`, `FILE_FLAG_OPEN_REPARSE_POINT`, only `FILE_SHARE_READ` (no write/delete sharing), and opened-handle regular/non-reparse plus volume/file identity checks through safe `cap_std`/`cap_fs_ext`; unsupported targets return `PolicyDenied`. Before authority, hash at most the exact input cap plus one byte and bracket the pass with the retained handle's type/identity/exact-length marker. Reaching `cap + 1` with an unchanged marker takes the terminal input-limit path below and never invokes the vault or probe. Otherwise rewind the same handle, stream that handle to the vault, compare vault digest/length, rewind, and hash/recheck the same bounded complete input and marker again. Any marker, length, or digest mutation is hard `Integrity`. The basename becomes a lossless bounded display string: valid UTF-8 bytes are prefixed `utf8:` and percent-escape every byte outside ASCII `[A-Za-z0-9._-]`; invalid Unix bytes use `unix-bytes:` plus lowercase hex; Windows uses `windows-utf16:` plus four lowercase hex digits per UTF-16 unit. The encoded display is at most 4 KiB. Only it and `<redacted>` enter legacy source columns; no absolute path enters JSON, audit, inspection, errors, or `Debug`.

The exact durable sequence is:

1. Validate the project/request and exact default PDF profile. Fingerprint the retained handle. Resolve the scoped idempotency key before any new publication/probe work: conflict is committed first; replay verifies every vault object required by the stored receipt, commits its replay record/audit, and only then returns it.
2. Resolve sticky admission. Otherwise compute the serialized committed-plus-nonterminal quota snapshot and exact bounded `VaultInventory` under the writer lock, drop every SQLite statement, and publish/verify the original through the single-lock atomic Vault operation with the conservative orphan allowance below. A real crash in the interval before the next transaction leaves only a reportable request-digest orphan that remains charged until a later exact checkpoint/content authority binds it.
3. In one immediate transaction insert a queued attempt-0 job whose canonical input/budget/checkpoint names the verified original, append `job_created`, and retain the result. Fire `AfterVaultPublish` only now.
4. In one transaction transition `queued -> running`, set attempt `1`, a generated opaque UUID lease owner, and `lease_expires_at_ms = checked(now + 30_000)`, append `job_started`, then fire `AfterJobStart`.
5. Open/re-hash only the checkpointed object, probe with the frozen default limits/identity, independently validate every trait echo/page/geometry, build and publish the accepted manifest or freeze the typed quarantine, and commit a processing checkpoint plus `job_checkpointed`; then fire `AfterCheckpointCommit`.
6. Fire `BeforeAuthoritativeCommit`. In one immediate transaction commit either accepted content/document/revision/project/sheets/evidence/source rows or the quarantine rows, the one immutable terminal intake event for this attempt, terminal `succeeded` job/checkpoint with cleared lease, and all audit events. `AcceptedNew` means this database commit creates the revision; `AcceptedDuplicate` means it reuses one, regardless of physical vault deduplication.
7. Fire `AfterAuthoritativeCommit` only after `with_immediate_transaction` returned success including post-commit checks.

Handled paths that do not need PDF work still use the same finite job lifecycle rather than fabricating a terminal insertion. An input known to exceed the cap, an original-object `PutOutcome::QuotaRejected`, a sticky admission, or a completed scoped replay is resolved only after the validation/idempotency precedence above. A new handled attempt creates the queued attempt-0 job and its bounded checkpoint, transitions it to running attempt 1, and commits its terminal result through the named repository methods; those transitions may share one immediate transaction only when no external operation or fault boundary lies between them. Only input-cap, original-retention-quota, and project-association-quota checkpoints use the exact `preflight_rejected` tag, carry only the optional complete digest, exact observed length, and typed reason, and bypass probing without naming a retained vault object; `vault_published` requires a retained verified object. `EvidenceManifestQuota` instead uses `processing_complete`, names and re-verifies the retained original plus typed quarantine candidate, and never names or retains the rejected manifest. A same-key replay creates no second job. A sticky retained admission creates a new job for a new key but does no new vault write or probe. Every legal terminal path therefore satisfies the queued/running/terminal constraints, while a pre-request hard error creates no job.

Every resume first verifies the complete audit chain and exact input/budget/checkpoint hashes against the immutable job audit anchor; any branch that consumes an object opens/re-hashes only its checkpointed vault key. Queued resume then starts attempt 1 with no interruption. Expired running resume atomically records `running -> interrupted`, one interrupted event for that attempt, and `interrupted -> running` with checked attempt increment/new lease. The absolute deadline is `checked(created_at_ms + 300_000)` and never extends; deadline is checked before lease availability. At/after it, a running job transitions directly to audited `failed` with reason `deadline_expired`, without a false interruption; a queued job makes the legal `queued -> running(attempt 1) -> failed` transitions in one transaction, and both return `Timeout`. Maximum running attempts is 16. An expired running attempt 16 transitions directly to audited `failed` with reason `attempt_limit`, creates no interrupted event that could not legally resume, and returns `ResourceLimit`; no attempt 17 exists. Before those terminal boundaries, an unexpired lease returns exact `HeleosError::LeaseUnavailable`. `Succeeded` resume additionally verifies every object bound by the stored receipt, returns that receipt, and appends only the bounded replay audit observation described below; `Failed`/`Cancelled` returns `InvalidStateTransition`.

`Store::with_immediate_transaction` maps a commit error or any failure after a successful commit begins to exact `HeleosError::CommitOutcomeUnknown`; it never returns the closure value after that boundary. The caller must reopen and retry only by the same job/scoped key. The existing private Store fault harness proves committed rows cannot be mistaken for rollback. Pre-transaction identity/policy errors preserve their original type.

Use this public shape:

```rust
impl<'a> ProjectService<'a> {
    pub fn new(
        store: &'a mut Store,
        clock: &'a dyn Clock,
        ids: &'a dyn IdGenerator,
    ) -> Result<Self>;
    pub fn create(&mut self, request: ProjectCreateRequest) -> Result<ProjectReceipt>;
}

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
    pub fn ingest(&mut self, request: IngestRequest) -> Result<IngestReceipt>;
    pub fn resume(&mut self, job_id: JobId, actor: ActorId) -> Result<ResumeReceipt>;
    pub fn verify_audit_chain(&self) -> Result<AuditChainReport>;
    pub fn evidence_manifest_for_revision(
        &self,
        revision: RevisionId,
    ) -> Result<EvidenceManifestReceipt>;
    pub fn inspect_foundation(&self, project: ProjectId) -> Result<FoundationInspection>;
}

impl<'a> FoundationReader<'a> {
    pub fn new(store: &'a Store, vault: &'a Vault) -> Self;
    pub fn verify_audit_chain(&self) -> Result<AuditChainReport>;
    pub fn evidence_manifest_for_revision(
        &self,
        revision: RevisionId,
    ) -> Result<EvidenceManifestReceipt>;
    pub fn inspect_foundation(&self, project: ProjectId) -> Result<FoundationInspection>;
}

impl IntakeSource {
    pub fn open(path: &std::path::Path) -> Result<Self>;
}

pub trait FaultInjector: Send + Sync {
    fn inject(&self, point: FaultPoint) -> Result<()>;
}
```

All public DTOs derive strict serde where serializable and reject unknown fields on input. Every code-block `#[serde(...)]` attribute is part of the public representation; types with literal or cross-field invariants deserialize through a private deny-unknown raw DTO plus the same validating `TryFrom` used for database reconstruction, never an unchecked public derive. `IdempotencyKey` is a Task 6 newtype over 1..128 UTF-8 bytes with no Unicode controls. The request and receipt fields are exact:

```rust
#[serde(deny_unknown_fields)]
pub struct ProjectCreateRequest {
    pub project_id: Option<ProjectId>,
    pub name: String,
    pub actor: ActorId,
    pub data_class: DataClass,
}

#[serde(deny_unknown_fields)]
pub struct ProjectReceipt {
    pub project_id: ProjectId,
    pub created_at_ms: i64,
    pub audit_event_id: AuditEventId,
}

pub struct IngestRequest {
    pub project_id: ProjectId,
    pub source: IntakeSource,
    pub idempotency_key: IdempotencyKey,
    pub actor: ActorId,
    pub pdf_limits: PdfLimits,
}

#[serde(deny_unknown_fields)]
pub struct IngestReceipt {
    pub ingest_event_id: IngestEventId,
    pub authoritative_job_id: JobId,
    pub attempt: u32,
    pub outcome: IngestOutcome,
    pub content_sha256: Option<Sha256Digest>,
    pub byte_length: u64,
    pub quarantine: Option<IntakeQuarantineV1>,
    pub document_id: Option<DocumentId>,
    pub revision_id: Option<RevisionId>,
    pub sheet_ids: Vec<SheetId>,
    pub evidence_manifest: Option<EvidenceManifestReceipt>,
    pub preexisting_vault_digests: Vec<Sha256Digest>,
}

#[serde(deny_unknown_fields)]
pub struct ResumeReceipt {
    pub job_id: JobId,
    pub resumed_attempt: Option<u32>,
    pub interrupted_event_id: Option<IngestEventId>,
    pub receipt: IngestReceipt,
}
```

`ProjectCreateRequest.name` is 1..256 UTF-8 bytes with no controls; an omitted project ID comes from `IdGenerator`. `IngestRequest` is consumed, non-`Clone`, and non-serializable because `IntakeSource` owns the sole handle. Receipt deserialization and every database reconstruction go through the same validating constructor. `AcceptedNew`/`AcceptedDuplicate` require content/document/revision/evidence, 1..10,000 ordered sheet IDs, and no quarantine. A quarantine outcome requires the typed quarantine and no document/revision/sheets/evidence. Its receipt `content_sha256` is `Some` whenever a complete digest was safely established—even for a non-retained vault/project-quota result—and `None` only for the bounded incomplete input-cap case; the database event content foreign key is non-null only when that project actually retains/associates the original. `IdempotentReplay` must reproduce the authoritative receipt's accepted-or-quarantine option shape and attempt while exposing its own replay event ID; `DeniedConflict` and `Interrupted` are never returned as `IngestReceipt`. `preexisting_vault_digests` is a sorted/deduplicated digest-byte list of at most the request's original and manifest that existed before this call. For `ResumeReceipt`, queued recovery is `(Some(1), None)`, expired-running recovery is `(Some(n >= 2), Some(event))`, and terminal replay is `(None, None)`; every other combination is invalid.

The typed quarantine record is exact JCS/strict serde:

```rust
pub const INTAKE_QUARANTINE_SCHEMA_V1: &str = "heleos.intake-quarantine/v1";

#[serde(tag = "kind", content = "detail", rename_all = "snake_case")]
pub enum IntakeQuarantineReasonV1 {
    Pdf(PdfQuarantineReason),
    InputBytes { limit_bytes: u64, observed_bytes: u64 },
    OriginalRetentionQuota,
    ProjectAssociationQuota,
    EvidenceManifestQuota,
}

#[serde(deny_unknown_fields)]
pub struct IntakeQuarantineV1 {
    pub schema: String,
    pub reason: IntakeQuarantineReasonV1,
    pub probe_provenance: Option<PdfProbeProvenance>,
}
```

The enum uses adjacent `kind`/`detail` snake-case representation. `Pdf` and `EvidenceManifestQuota` require the exact frozen provenance; `InputBytes`, `OriginalRetentionQuota`, and `ProjectAssociationQuota` require it absent. Only `Pdf` and `EvidenceManifestQuota` can be retained as a sticky `content_objects.quarantine_reason`; the others have no new project/content association and a null event content foreign key, while their safely known digest remains in receipt/details only.

Task 6 tests hard-code literal canonical JSON bytes (no BOM/LF/trailing byte) for project creation; accepted-new, accepted-duplicate, retained-PDF-quarantine, non-retained-limit, and idempotent-replay receipts; all three valid `ResumeReceipt` option shapes; empty and populated `FoundationInspection`; and every `AuditChainFinding` variant. The populated vectors use the same fixed UUIDs/digests/clock/pages as Tasks 7 and 9, which must compare their public JSON byte-for-byte rather than importing a production-private encoder. One missing/unknown field, option-shape mutation, enum-tag mutation, list permutation, or scalar N/N+1 mutation is rejected.

Task 6 owns and tests these allocation-before-parse caps: safe encoded source display 4 KiB UTF-8; audit subject ID 256 UTF-8 bytes without controls; audit reason 1 KiB UTF-8 bytes without controls other than the explicitly accepted ordinary whitespace set; every job input, budget, event-details, evidence-parameters, audit-before, and audit-after JCS document 1 MiB independently; checkpoint JCS 8 MiB; evidence pages 10,000; evidence lineages returned for one revision 100,000; evidence manifest 16 MiB; inspection nested rows 100,000 and canonical inspection JSON 16 MiB; audit findings 128. Existing typed actors retain their 128-byte bound. `PdfProbeProvenance` string fields are each 1..256 UTF-8 bytes without controls and must also equal the engine's frozen identity exactly. All checked counts/sizes are rejected before proportional allocation or indexing where possible, use checked arithmetic, and have exact `N`/`N + 1` tests with the specified `ResourceLimit`, validation error, or terminal input-limit outcome.

`PdfProbe` gains `fn provenance(&self) -> Result<PdfProbeProvenance>`. Engine construction validates/fixes that identity; every returned outcome must match it. Before accepting trait output, Task 6 independently requires exact content digest/length/revision echoes, 1..10,000 contiguous pages, canonical page IDs, axes within default limits/JCS-safe integers, the exact rotation matrix, and bounded provenance strings. Mismatch is hard `Integrity`; it cannot create authority. Shared `pub(crate)` page-validation code in `pdf/geometry.rs` is reused by the WASI host and intake instead of duplicating the classifier.

Use exactly these evidence constants and fields in `ingest/evidence.rs`:

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

Manifest bytes are exactly nonempty `canonical_json(&manifest)` with no BOM/LF/trailing byte and at most 16 MiB; its content address is ordinary SHA-256 of those exact bytes with no prefix. Both IDs equal the canonical original IDs. Pages are index-sorted/contiguous and independently revalidated. The manifest excludes its own digest/key, project, actor, job, evidence ID, idempotency key, source, clock, review, and time. The accepted evidence row uses method `heleos.pdf-probe/v1`, state `accepted`, and exact bounded JCS parameters containing only schema, both media types, actual probe provenance, and requested limits. Its derivative/parent are manifest/original. `content_objects` stores the corresponding exact media type.

The revision receipt shape is exact:

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

Sort lineages by `(project UUID bytes, evidence UUID bytes, job UUID bytes)`. The reader opens/re-hashes both vault objects, reads at most 16 MiB, requires strict typed parse plus byte-for-byte JCS equality, and requires every project evidence row for the revision to agree on manifest/parent/method/parameters and database sheets. Any disagreement is `Integrity`; it never picks the first row.

Before materializing revision evidence, its query obtains a bounded count, rejects 100,001, fetches at most `count + 1` in the exact sort order, and validates every text/JSON length before conversion; count drift, a 100,001st row, or oversized scalar fails closed. Manifest bytes are read through the same 16-MiB bounded reader, never `read_to_end` without a cap.

Job JSON is exact JCS with these schemas/caps: `heleos.ingest-input/v1` (project, fixed kind/key, complete-or-over-limit source fingerprint, the bounded safe source display, default limits, expected probe provenance, fixed 300,000-ms deadline profile), `heleos.ingest-budget/v1` (fixed quota ceilings, 30,000-ms lease, 16 attempts, checked source reservation), and tagged `heleos.ingest-checkpoint/v1` phases `preflight_rejected`, `vault_published`, `processing_complete`, and `terminal`. The safe display is persisted solely so a source-less resume can produce its eventual terminal row and bounded diagnostics; it is excluded from idempotency equality, while the raw/encoded ambient path is never persisted. A checkpoint contains only verified digest/length/key records and the bounded typed candidate/receipt needed to resume; never source bytes/path. `preflight_rejected` is valid only for handled input-cap, original-retention-quota, or project-association-quota results and never names a retained vault object. `EvidenceManifestQuota` is valid only in `processing_complete`, which names and re-verifies the retained original and typed quarantine candidate but never names or retains the rejected manifest. Input and budget are immutable and at most 1 MiB; checkpoint is at most 8 MiB. Every job audit snapshot contains exact SHA-256 values of their canonical bytes, state/attempt/deadline/lease scalars, and the terminal result IDs, so resume can bind mutable checkpoint storage back to the immutable audit chain.

Quota accounting is exact:

- Store-category totals sum distinct committed content digests plus distinct nonterminal checkpoint reservations once. Project totals sum distinct digests associated through accepted revision/evidence or retained quarantine events plus their project-scoped nonterminal reservations; repeated attempts add zero, while first association in another project charges full logical length even for a physical duplicate. Checked overflow or conflicting reservation authority is `Integrity`.
- Overall is 50 GiB/project and 500 GiB/vault; evidence is 5 GiB/project and 50 GiB/store; retained quarantine is 2 GiB/project and 8 GiB/store. The database writer lock remains held while snapshotting membership/usage and during subsequent vault calls, but no SQLite transaction remains open.
- A novel unclassified original must conservatively fit the minimum remaining overall-project, quarantine-project, and quarantine-store allowance before Task 5 can inspect its permanently published object. The quarantine-store allowance passed to Vault subtracts committed retained quarantine, distinct nonterminal quarantine reservations, and every metadata-validated unbound crash orphan found by the same locked scan. The current already-present orphan is a zero-debit duplicate, while a different novel object cannot consume its reserved bytes. This may reject an otherwise acceptable PDF when quarantine headroom is exhausted; that is the explicit 0.1 fail-closed tradeoff. A manifest must fit remaining overall/evidence project/store allowance, debiting the original association first, and uses the same atomic inventory-bound publication so a crash-created unbound manifest is conservatively orphan-charged. Vault physical duplicates still undergo project-association/category checks; `newly_published` decides only physical debit. The Vault scan alone enforces 500-GiB physical overall usage; evidence and other logical category totals never infer physical usage from orphan files.
- An original-object `PutOutcome::QuotaRejected`, an input-length violation, or a denied new-project association to an existing digest is `QuarantinedLimit`, `Succeeded`, no new content/project association, and a null event content FK; receipt/details carry exact length and a digest only if safely complete. If an accepted original was published but its manifest cannot be retained, commit that original as sticky quarantined with typed `evidence_manifest_quota`, no revision/sheet/evidence, and a successful quarantine event.

Repository event details are strict tagged `heleos.ingest-event-details/v1`: accepted (attempt and result IDs), quarantined (attempt, optional digest, length, typed record), interrupted (attempt and checkpoint hash), replay (authoritative job/attempt/event), or conflict (authoritative job plus sorted names of mismatching frozen fields, never submitted values). Replay/conflict event `attempt` is null; the others are positive. Every classifiable terminal call to `ingest` has one source record, immutable ingest event, and corresponding audit in the same transaction. A recovered interruption has only its immutable interrupted ingest event plus corresponding audit in the recovery transaction and refers to the job's already frozen source provenance; it does not invent a second source record. A terminal `resume` appends a bounded replay audit observation using frozen job/source hashes but creates neither a new source record nor a second ingest result/event. Pre-request validation and trusted internal failures are not mislabeled as intake outcomes.

Business precedence is validation/missing-project/nondefault-profile hard error; scoped idempotency replay/conflict; sticky admission; vault input/quota result; exact `PdfQuarantine::ingest_outcome`; manifest/association quota; then accepted new/duplicate. Conflict commits `DeniedConflict` plus audit and only then returns `IdempotencyConflict`. PDF quarantine and handled quota are terminal `Succeeded`. Trusted host/artifact/revision/I/O/permission/sync/cleanup/integrity failures remain hard errors; if a running job exists and database authority is safe, commit audited `Failed` without an ingest event before returning the original error, otherwise leave the lease for recovery. Injected faults return exact `HeleosError::FaultInjected` and never terminalize. The other new exact variants are `LeaseUnavailable` and `CommitOutcomeUnknown`; none is collapsed into a business quarantine.

The inspection DTOs are exact strict-serde records:

```rust
pub const FOUNDATION_INSPECTION_SCHEMA_V1: &str = "heleos.foundation-inspection/v1";

#[serde(rename_all = "snake_case")]
pub enum FoundationAdmissionState { Accepted, Quarantined }

#[serde(rename_all = "snake_case")]
pub enum FoundationJobTerminalReason {
    Completed,
    DeadlineExpired,
    AttemptLimit,
    InternalFailure,
    Cancelled,
}

#[serde(deny_unknown_fields)]
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

#[serde(deny_unknown_fields)]
pub struct FoundationContentObject {
    pub sha256: Sha256Digest,
    pub byte_length: u64,
    pub media_type: String,
    pub admission_state: FoundationAdmissionState,
    pub vault_key: String,
    pub quarantine: Option<IntakeQuarantineV1>,
}

#[serde(deny_unknown_fields)]
pub struct FoundationSheet {
    pub sheet_id: SheetId,
    pub revision_id: RevisionId,
    pub index: u32,
    pub width_micropoints: u64,
    pub height_micropoints: u64,
    pub unit: PageUnit,
    pub rotation_degrees: u16,
    pub transform: PageTransform,
    pub parent_content_sha256: Sha256Digest,
}

#[serde(deny_unknown_fields)]
pub struct FoundationEvidenceContent {
    pub sha256: Sha256Digest,
    pub byte_length: u64,
    pub vault_key: String,
    pub media_type: String,
}

#[serde(deny_unknown_fields)]
pub struct FoundationEvidenceLineage {
    pub evidence_id: EvidenceId,
    pub originating_job_id: JobId,
    pub document_id: DocumentId,
    pub revision_id: RevisionId,
    pub original: FoundationEvidenceContent,
    pub manifest: FoundationEvidenceContent,
    pub extraction_method: String,
    pub requested_limits: EvidencePdfLimitsV1,
    pub probe_provenance: PdfProbeProvenance,
    pub review_state: String,
}

#[serde(deny_unknown_fields)]
pub struct FoundationIntakeEvent {
    pub ingest_event_id: IngestEventId,
    pub job_id: Option<JobId>,
    pub content_sha256: Option<Sha256Digest>,
    pub outcome: IngestOutcome,
    pub attempt: Option<u32>,
    pub terminal_at_ms: i64,
}

#[serde(deny_unknown_fields)]
pub struct FoundationJob {
    pub job_id: JobId,
    pub kind: String,
    pub state: JobState,
    pub attempt: u32,
    pub created_at_ms: i64,
    pub updated_at_ms: i64,
    pub terminal_reason: Option<FoundationJobTerminalReason>,
}

#[serde(deny_unknown_fields)]
pub struct FoundationInspection {
    pub schema: String,
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
```

`schema`, media, kind, extraction method, and review state are validated against their exact literals (`heleos.foundation-inspection/v1`, the two media constants, `pdf_ingest`, `heleos.pdf-probe/v1`, and `accepted`). Accepted content requires no quarantine; quarantined content requires one valid sticky record. Replay/conflict intake attempts have null `attempt`; authoritative/interrupted attempts have a positive attempt. Nonterminal jobs require no terminal reason; succeeded/cancelled require `Completed`/`Cancelled`; failed requires exactly `DeadlineExpired`, `AttemptLimit`, or `InternalFailure`. The records exclude source, actor, key, raw JSON/bytes, lease identity, and mutation/SQL capability.

Sort content by digest bytes; documents/revisions by digest; sheets by `(revision,index,sheet)`; evidence by `(revision,manifest,evidence UUID)`; intake by `(terminal_at_ms,event UUID)`; jobs by `(created_at_ms,job UUID)`. Project content is the distinct union of its accepted originals/manifests and retained quarantines. Counts are project-scoped under those definitions and equal each corresponding vector length where a vector exists. Before materializing rows, repository queries obtain bounded counts with checked sums, reject a total over 100,000, and fetch each ordered relation with `remaining + 1`/exact-count guards; every database text/blob/JSON length is checked against its field cap before conversion or deserialization. Canonical inspection JSON is emitted through a 16-MiB bounded writer. Any N+1 row, count drift, oversized scalar, or serializer overflow is `ResourceLimit` or `Integrity` as appropriate; inspection never truncates, over-allocates from hostile declarations, or depends on insertion order.

- [ ] **Step 4: Implement leases, restart, and audit chaining**

Add `AuditEventId` as a UUID newtype. The public event shape is exact:

```rust
#[serde(rename_all = "snake_case")]
pub enum AuditAction {
    ProjectCreated,
    JobCreated,
    JobStarted,
    JobCheckpointed,
    JobInterrupted,
    JobResumed,
    JobSucceeded,
    JobFailed,
    IngestAccepted,
    IngestQuarantined,
    IngestReplayed,
    IngestConflictDenied,
    EvidenceCreated,
}

#[serde(rename_all = "snake_case")]
pub enum AuditSubjectType { Project, Job, IngestAttempt, Evidence }

#[serde(deny_unknown_fields)]
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

Sequence is positive and JCS-safe; `occurred_at_ms` and every other persisted/public timestamp are nonnegative JCS-safe integers; the subject, values, and reason obey the exact caps above. Hash exactly:

```text
SHA256("heleos-audit-event-v1\0" || JCS({
  action, actor, after, before, event_id, occurred_at_ms, previous_hash,
  project_id, reason, sequence, subject_id, subject_type
}))
```

JCS orders the named fields canonically; `before`/`after` are values, hashes are lowercase hex, and there is no LF. Sequence starts at 1, is gapless, and the first previous hash is 64 zero hex characters. The literal 492-byte first-vector JCS is `{"action":"project_created","actor":"tester","after":{"data_class":"INTERNAL","name":"Demo","project_id":"00000000-0000-4000-8000-000000000001"},"before":null,"event_id":"00000000-0000-4000-8000-000000000002","occurred_at_ms":1700000000000,"previous_hash":"0000000000000000000000000000000000000000000000000000000000000000","project_id":"00000000-0000-4000-8000-000000000001","reason":"project created","sequence":1,"subject_id":"00000000-0000-4000-8000-000000000001","subject_type":"project"}` and its domain-separated SHA-256 is `266987a9f2db93655d060542189a830bb76fbc5e9e88ea93add8fc5958614578`.

Actions are exactly `project_created`, `job_created`, `job_started`, `job_checkpointed`, `job_interrupted`, `job_resumed`, `job_succeeded`, `job_failed`, `ingest_accepted`, `ingest_quarantined`, `ingest_replayed`, `ingest_conflict_denied`, and `evidence_created`; subjects are `project`, `job`, `ingest_attempt`, and `evidence`. The same-transaction event sets are exact: project creation has `project_created`; queued creation has `job_created`; first start has `job_started`; processing checkpoint has `job_checkpointed`; recover-and-resume has `job_interrupted` then `job_resumed`; accepted authority has `job_succeeded` plus `ingest_accepted` and also `evidence_created` iff that transaction inserts a project evidence lineage; quarantine has `job_succeeded` plus `ingest_quarantined`; replay has `ingest_replayed`; conflict has `ingest_conflict_denied`; trusted terminal failure has `job_failed`. A deadline-expired queued job records `job_started` then `job_failed` in transition order. Raw transaction access is crate-private and no public service mutation bypasses repository append.

The public report shape is exact:

```rust
#[serde(rename_all = "snake_case")]
pub enum AuditInvalidField {
    Id,
    Sequence,
    ProjectId,
    Actor,
    Action,
    SubjectType,
    SubjectId,
    Before,
    After,
    Reason,
    OccurredAt,
    PreviousHash,
    EventHash,
}

#[serde(tag = "kind", content = "detail", rename_all = "snake_case")]
pub enum AuditChainFinding {
    InvalidRow {
        row_ordinal: u64,
        event_id: Option<AuditEventId>,
        sequence: Option<u64>,
        field: AuditInvalidField,
    },
    SequenceMismatch {
        event_id: AuditEventId,
        expected: u64,
        observed: u64,
    },
    PreviousHashMismatch {
        event_id: AuditEventId,
        sequence: u64,
        expected: Sha256Digest,
        observed: Sha256Digest,
    },
    EventHashMismatch {
        event_id: AuditEventId,
        sequence: u64,
        expected: Sha256Digest,
        observed: Sha256Digest,
    },
    NonCanonicalBefore { event_id: AuditEventId, sequence: u64 },
    NonCanonicalAfter { event_id: AuditEventId, sequence: u64 },
}

#[serde(deny_unknown_fields)]
pub struct AuditChainReport {
    pub schema: String,
    pub valid: bool,
    pub checked_event_count: u64,
    pub first_sequence: Option<u64>,
    pub last_sequence: Option<u64>,
    pub head_hash: Sha256Digest,
    pub findings: Vec<AuditChainFinding>,
    pub findings_truncated: bool,
}
```

Its schema is `heleos.audit-chain-report/v1`. Stream `ORDER BY sequence,id`; `row_ordinal` is the one-based streamed row number. The verifier checks SQLite text/JSON byte lengths before converting each streamed field. Invalid hostile values are represented only by the fixed field enum and optional successfully parsed ID/sequence, never raw strings or JSON. Emit findings in observed row order and, within one row, finding-variant ordinal then `AuditInvalidField` ordinal. Malformed/cryptographic disagreement is a finding, while inability to query is `Err`. Publish at most 128 findings; continue bounded validation, and the 129th finding sets truncation and invalid without being emitted. `checked_event_count` counts every streamed row with checked arithmetic; first/last sequence are the first/last successfully parsed positive sequence. `head_hash` is the recomputed hash of the final structurally parseable row, or 32 zero bytes if none; a valid chain's value necessarily equals its final stored hash. Empty is valid with zero checked rows, absent first/last sequences, and a zero head. Tests mutate every hashed field, row order/sequence/canonical JSON, and fixed vectors independently.

`store/ingest_repository.rs` exposes only crate-private command/query methods with task-owned command structs: `project_create_with_audit`, `idempotency_lookup`, `quota_snapshot`, `job_create_with_checkpoint_and_audit`, `job_start_or_resume_with_audit`, `job_checkpoint_with_audit`, `job_recover_expired_attempt_with_audit`, `accepted_intake_commit`, `quarantined_intake_commit`, `replay_attempt_commit`, `conflict_attempt_commit`, `job_fail_with_audit`, `audit_chain_rows`, `revision_evidence_rows`, `foundation_inspection_rows`, and `vault_inventory_rows`. Each commit owns exactly one immediate transaction and returns a committed business result; query methods mutate nothing and support read-only Store. The existing crate-private Store authority `vault_inventory_rows` returns the bounded `VaultInventory` containing every content row and every verified original/manifest named by a nonterminal job checkpoint, with exact latest-audit binding and conflict checks; this introduces no public API. Task 6 publication and Task 7 backup/reconciliation share that one inventory definition and cannot discard resumable inputs.

Task 7 inspection/verification must compose `FoundationReader` with `Store::open_read_only`; it never obtains a writer merely to inspect. Task 7 backup/reconciliation reuses the exact `VaultInventory` query and Vault layout validator rather than defining another content/nonterminal union, and it opens and rehashes every referenced inventory object before backup/reconciliation authority. Task 8 uses only public readers/services and verifier-only direct `rusqlite` for hostile file preparation. Task 9 compares the exact inspection schema/order, audit vector, receipt lineage, and default-limit evidence bytes. Task 10 executes the committed Windows source-handle/reparse/share-mode and atomic same-root publication-race tests natively, including directory/file reparse substitution and the no-delete-share retained-handle brackets.

- [ ] **Step 5: Reach green and commit**

Run: `cargo +1.96.1 fmt --all --check && cargo +1.96.1 clippy --locked --workspace --all-targets --all-features -- -D warnings && cargo +1.96.1 test --locked -p heleos-core --test migrations --test ingest --test restart && cargo +1.96.1 test --locked --workspace --all-targets --all-features && cargo +1.96.1 check --locked --workspace --all-targets --all-features`

Expected: all tests pass without warnings, including every fault point, exact JCS/hash vector, 128/129 audit boundary, JSON/page/inspection N/N+1 caps, quota duplicate/association/N/N+1 matrix, malicious probe echoes, raw-SQL compile-fail boundary, sticky admission, and corrected crash subprocess matrix. Run the honest locked/offline `x86_64-pc-windows-msvc` all-target check. If it stops only in the already governed fiber/SQLite SDK translations, repeat Task 5's exact source/hash/compiler/poison-header/modern_sqlite/rust-lld check-only probe against these staged core bytes and require exit 0/no warnings; retain native source-handle/share/reparse execution as Task 10 ownership.

Stage only the eighteen declared files, run the complete matrix from the staged bytes, inspect the exact cached names, then commit.

Commit: `git commit -m "feat: add crash-safe deterministic intake"`

---

