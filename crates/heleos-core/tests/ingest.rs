#![forbid(unsafe_code)]

use std::{
    fs,
    str::FromStr,
    sync::atomic::{AtomicI64, Ordering},
};
#[cfg(unix)]
use std::{
    io::{Seek, SeekFrom, Write},
    sync::{Mutex, atomic::AtomicBool},
};

#[cfg(unix)]
use heleos_core::VaultWriteBudget;
use heleos_core::{
    ActorId, AuditChainFinding, AuditChainReport, AuditEvent, AuditEventId, Clock, DataClass,
    EVIDENCE_MANIFEST_MEDIA_TYPE, EVIDENCE_MANIFEST_SCHEMA_V1, EvidenceContentV1,
    EvidenceManifestV1, FaultInjector, FaultPoint, FoundationAdmissionState, FoundationInspection,
    FoundationJobTerminalReason, FoundationReader, HeleosError, IdGenerator, IdempotencyKey,
    IngestEngine, IngestOutcome, IngestReceipt, IngestRequest, IntakeQuarantineReasonV1,
    IntakeSource, JobState, PDF_MEDIA_TYPE, PageMetadata, PageTransform, PageUnit, PdfInspection,
    PdfLimits, PdfProbe, PdfProbeOutcome, PdfProbeProvenance, PdfQuarantine, PdfQuarantineReason,
    ProjectCreateRequest, ProjectId, ProjectReceipt, ProjectService, Result, RevisionId,
    Sha256Digest, Store, Vault, VaultConfig, VaultOpenMode, VerifiedObject, canonical_document_ids,
    canonical_json, page_id,
};
use tempfile::TempDir;
use uuid::Uuid;

const NOW_MS: i64 = 1_700_000_000_000;

struct FixedClock;

impl Clock for FixedClock {
    fn now_unix_ms(&self) -> i64 {
        NOW_MS
    }
}

struct ManualClock(AtomicI64);

impl ManualClock {
    fn new(now_ms: i64) -> Self {
        Self(AtomicI64::new(now_ms))
    }

    fn set(&self, now_ms: i64) {
        self.0.store(now_ms, Ordering::SeqCst);
    }
}

impl Clock for ManualClock {
    fn now_unix_ms(&self) -> i64 {
        self.0.load(Ordering::SeqCst)
    }
}

struct FailAt(FaultPoint);

impl FaultInjector for FailAt {
    fn inject(&self, point: FaultPoint) -> Result<()> {
        if point == self.0 {
            Err(HeleosError::FaultInjected)
        } else {
            Ok(())
        }
    }
}

#[cfg(unix)]
struct MutatingClock {
    file: Mutex<fs::File>,
    replacement: Vec<u8>,
    fired: AtomicBool,
}

#[cfg(unix)]
impl MutatingClock {
    fn new(file: fs::File, replacement: &[u8]) -> Self {
        Self {
            file: Mutex::new(file),
            replacement: replacement.to_vec(),
            fired: AtomicBool::new(false),
        }
    }
}

#[cfg(unix)]
impl Clock for MutatingClock {
    fn now_unix_ms(&self) -> i64 {
        if !self.fired.swap(true, Ordering::SeqCst) {
            let mut file = self.file.lock().expect("lock source mutator");
            file.seek(SeekFrom::Start(0))
                .expect("rewind source mutator");
            file.write_all(&self.replacement)
                .expect("replace retained source bytes");
            file.sync_all().expect("sync replaced source bytes");
        }
        NOW_MS
    }
}

struct SequenceIds {
    values: std::sync::Mutex<std::vec::IntoIter<Uuid>>,
}

#[derive(Clone)]
struct AcceptedProbe {
    provenance: PdfProbeProvenance,
}

#[derive(Clone)]
struct QuarantinedProbe {
    provenance: PdfProbeProvenance,
    reason: PdfQuarantineReason,
}

#[derive(Clone)]
struct FailingProbe {
    provenance: PdfProbeProvenance,
}

impl PdfProbe for FailingProbe {
    fn provenance(&self) -> Result<PdfProbeProvenance> {
        Ok(self.provenance.clone())
    }

    fn probe(&self, _: VerifiedObject, _: RevisionId, _: PdfLimits) -> Result<PdfProbeOutcome> {
        Err(HeleosError::Integrity)
    }
}

impl PdfProbe for QuarantinedProbe {
    fn provenance(&self) -> Result<PdfProbeProvenance> {
        Ok(self.provenance.clone())
    }

    fn probe(&self, input: VerifiedObject, _: RevisionId, _: PdfLimits) -> Result<PdfProbeOutcome> {
        Ok(PdfProbeOutcome::Quarantined(PdfQuarantine {
            content_sha256: input.digest(),
            byte_length: input.byte_length(),
            provenance: self.provenance.clone(),
            reason: self.reason,
        }))
    }
}

impl PdfProbe for AcceptedProbe {
    fn provenance(&self) -> Result<PdfProbeProvenance> {
        Ok(self.provenance.clone())
    }

    fn probe(
        &self,
        input: VerifiedObject,
        revision: RevisionId,
        _: PdfLimits,
    ) -> Result<PdfProbeOutcome> {
        let digest = input.digest();
        Ok(PdfProbeOutcome::Accepted(PdfInspection {
            revision_id: revision,
            content_sha256: digest,
            byte_length: input.byte_length(),
            provenance: self.provenance.clone(),
            pages: vec![PageMetadata {
                index: 0,
                page_id: page_id(digest, 0),
                width_micropoints: 612_000_000,
                height_micropoints: 792_000_000,
                unit: PageUnit::Point,
                rotation_degrees: 0,
                transform: PageTransform {
                    m11: 1,
                    m12: 0,
                    m21: 0,
                    m22: -1,
                    tx_micropoints: 0,
                    ty_micropoints: 792_000_000,
                },
            }],
        }))
    }
}

fn accepted_probe() -> AcceptedProbe {
    AcceptedProbe {
        provenance: PdfProbeProvenance {
            parser_name: "fixture-parser".to_owned(),
            parser_version: "1.0.0".to_owned(),
            guest_wasm_sha256: Sha256Digest::from_bytes([1; 32]),
            guest_source_tree_sha256: Sha256Digest::from_bytes([2; 32]),
            guest_dependency_graph_sha256: Sha256Digest::from_bytes([3; 32]),
            protocol_version: "heleos.pdf-probe/v1".to_owned(),
        },
    }
}

impl SequenceIds {
    fn new(values: impl IntoIterator<Item = Uuid>) -> Self {
        Self {
            values: std::sync::Mutex::new(values.into_iter().collect::<Vec<_>>().into_iter()),
        }
    }
}

impl IdGenerator for SequenceIds {
    fn next_uuid(&self) -> Uuid {
        self.values
            .lock()
            .expect("test ID source lock")
            .next()
            .expect("test ID source exhausted")
    }
}

fn opened_vault(root: &TempDir) -> Vault {
    heleos_core::apply_private_permissions(root.path()).expect("harden vault parent");
    let parent = fs::canonicalize(root.path()).expect("canonicalize vault parent");
    Vault::open(VaultConfig {
        root: parent.join("vault"),
        open_mode: VaultOpenMode::CreateNew,
    })
    .expect("open test vault")
}

fn reopened_vault(root: &TempDir) -> Vault {
    let parent = fs::canonicalize(root.path()).expect("canonicalize vault parent");
    Vault::open(VaultConfig {
        root: parent.join("vault"),
        open_mode: VaultOpenMode::CreateOrOpen,
    })
    .expect("reopen test vault")
}

#[cfg(unix)]
fn create_sparse_vault_orphans(root: &TempDir, mut total_bytes: u64, excluded: &[Sha256Digest]) {
    const MAX_OBJECT_BYTES: u64 = 256 * 1024 * 1024;

    let vault_root = fs::canonicalize(root.path())
        .expect("canonicalize sparse-orphan parent")
        .join("vault");
    let mut index = 0_u64;
    while total_bytes > 0 {
        let digest = loop {
            let mut bytes = [0xa5; 32];
            bytes[24..].copy_from_slice(&index.to_be_bytes());
            index = index.checked_add(1).expect("bounded orphan index");
            let candidate = Sha256Digest::from_bytes(bytes);
            if !excluded.contains(&candidate)
                && !vault_root.join(Vault::object_key(candidate)).exists()
            {
                break candidate;
            }
        };
        let path = vault_root.join(Vault::object_key(digest));
        let second = path.parent().expect("second digest shard");
        let first = second.parent().expect("first digest shard");
        fs::create_dir_all(second).expect("create sparse orphan shards");
        heleos_core::apply_private_permissions(first).expect("harden first orphan shard");
        heleos_core::apply_private_permissions(second).expect("harden second orphan shard");
        let object_bytes = total_bytes.min(MAX_OBJECT_BYTES);
        let object = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .expect("create sparse orphan");
        object
            .set_len(object_bytes)
            .expect("size sparse orphan without materializing bytes");
        object.sync_all().expect("sync sparse orphan metadata");
        drop(object);
        heleos_core::apply_private_permissions(&path).expect("harden sparse orphan");
        total_bytes = total_bytes
            .checked_sub(object_bytes)
            .expect("bounded sparse orphan total");
    }
}

fn accepted_manifest_bytes(source: &[u8]) -> (Sha256Digest, Vec<u8>) {
    let original = Sha256Digest::hash_reader(source).expect("hash manifest original");
    let (document_id, revision_id) = canonical_document_ids(original);
    let manifest = EvidenceManifestV1 {
        schema: EVIDENCE_MANIFEST_SCHEMA_V1.to_owned(),
        original: EvidenceContentV1 {
            sha256: original,
            byte_length: u64::try_from(source.len()).expect("source length fits"),
            media_type: PDF_MEDIA_TYPE.to_owned(),
        },
        document_id,
        revision_id,
        pages: vec![PageMetadata {
            index: 0,
            page_id: page_id(original, 0),
            width_micropoints: 612_000_000,
            height_micropoints: 792_000_000,
            unit: PageUnit::Point,
            rotation_degrees: 0,
            transform: PageTransform {
                m11: 1,
                m12: 0,
                m21: 0,
                m22: -1,
                tx_micropoints: 0,
                ty_micropoints: 792_000_000,
            },
        }],
        probe_provenance: accepted_probe().provenance,
        requested_limits: PdfLimits::default().into(),
    };
    let bytes = canonical_json(&manifest).expect("encode accepted manifest");
    let digest = Sha256Digest::hash_reader(bytes.as_slice()).expect("hash accepted manifest");
    (digest, bytes)
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ExpectedIntakeEvent {
    event_id: heleos_core::IngestEventId,
    job_id: Option<heleos_core::JobId>,
    outcome: IngestOutcome,
    attempt: Option<u32>,
}

fn intake_event_vector(inspection: &FoundationInspection) -> Vec<ExpectedIntakeEvent> {
    inspection
        .intake_events
        .iter()
        .map(|event| ExpectedIntakeEvent {
            event_id: event.ingest_event_id,
            job_id: event.job_id,
            outcome: event.outcome,
            attempt: event.attempt,
        })
        .collect()
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ExpectedAuditIdentity {
    action: &'static str,
    subject_type: &'static str,
    subject_id: String,
}

fn audit_identity(
    action: &'static str,
    subject_type: &'static str,
    subject_id: impl ToString,
) -> ExpectedAuditIdentity {
    ExpectedAuditIdentity {
        action,
        subject_type,
        subject_id: subject_id.to_string(),
    }
}

fn audit_identity_vector(database_path: &std::path::Path) -> Vec<ExpectedAuditIdentity> {
    let connection = rusqlite::Connection::open(database_path).expect("open audit observer");
    let mut statement = connection
        .prepare(
            "SELECT action, subject_type, subject_id
             FROM audit_events
             ORDER BY sequence, id",
        )
        .expect("prepare audit identity query");
    statement
        .query_map([], |row| {
            Ok(ExpectedAuditIdentity {
                action: match row.get::<_, String>(0)?.as_str() {
                    "project_created" => "project_created",
                    "job_created" => "job_created",
                    "job_started" => "job_started",
                    "job_checkpointed" => "job_checkpointed",
                    "job_interrupted" => "job_interrupted",
                    "job_resumed" => "job_resumed",
                    "job_succeeded" => "job_succeeded",
                    "job_failed" => "job_failed",
                    "ingest_accepted" => "ingest_accepted",
                    "ingest_quarantined" => "ingest_quarantined",
                    "ingest_replayed" => "ingest_replayed",
                    "ingest_conflict_denied" => "ingest_conflict_denied",
                    "evidence_created" => "evidence_created",
                    other => panic!("unexpected audit action {other}"),
                },
                subject_type: match row.get::<_, String>(1)?.as_str() {
                    "project" => "project",
                    "job" => "job",
                    "ingest_attempt" => "ingest_attempt",
                    "evidence" => "evidence",
                    other => panic!("unexpected audit subject type {other}"),
                },
                subject_id: row.get(2)?,
            })
        })
        .expect("query audit identities")
        .collect::<std::result::Result<Vec<_>, _>>()
        .expect("read audit identities")
}

fn source_record_image(database_path: &std::path::Path) -> Vec<Vec<rusqlite::types::Value>> {
    const TEST_ROW_CAP: i64 = 8;

    let connection = rusqlite::Connection::open(database_path).expect("open source observer");
    let count = connection
        .query_row("SELECT COUNT(*) FROM source_records", [], |row| {
            row.get::<_, i64>(0)
        })
        .expect("count source records");
    assert!((0..=TEST_ROW_CAP).contains(&count));
    let mut statement = connection
        .prepare(
            "SELECT id, project_id, job_id, source_name, source_path, content_sha256,
                    metadata_json, created_at_ms
             FROM source_records
             ORDER BY id
             LIMIT ?1",
        )
        .expect("prepare bounded source-record image");
    let column_count = statement.column_count();
    let rows = statement
        .query_map([TEST_ROW_CAP + 1], |row| {
            (0..column_count)
                .map(|index| row.get::<_, rusqlite::types::Value>(index))
                .collect::<rusqlite::Result<Vec<_>>>()
        })
        .expect("query bounded source-record image")
        .collect::<rusqlite::Result<Vec<_>>>()
        .expect("read bounded source-record image");
    assert_eq!(i64::try_from(rows.len()).expect("row count fits"), count);
    rows
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ExpectedJobImage {
    job_id: heleos_core::JobId,
    state: JobState,
    attempt: u32,
    terminal_reason: Option<FoundationJobTerminalReason>,
}

fn job_image(
    job_id: heleos_core::JobId,
    state: JobState,
    attempt: u32,
    terminal_reason: Option<FoundationJobTerminalReason>,
) -> ExpectedJobImage {
    ExpectedJobImage {
        job_id,
        state,
        attempt,
        terminal_reason,
    }
}

fn job_image_vector(inspection: &FoundationInspection) -> Vec<ExpectedJobImage> {
    inspection
        .jobs
        .iter()
        .map(|job| ExpectedJobImage {
            job_id: job.job_id,
            state: job.state,
            attempt: job.attempt,
            terminal_reason: job.terminal_reason,
        })
        .collect()
}

fn assert_receipt_matches_terminal_checkpoint(
    database_path: &std::path::Path,
    receipt: &IngestReceipt,
    expected_attempt: u32,
) {
    let stored = terminal_receipt_from_database(database_path, receipt.authoritative_job_id);
    assert_eq!(receipt.attempt, expected_attempt);
    assert_eq!(stored.attempt, expected_attempt);
    assert_eq!(receipt, &stored);
    assert_eq!(
        canonical_json(receipt).expect("canonical returned terminal receipt"),
        canonical_json(&stored).expect("canonical stored terminal receipt")
    );
}

fn assert_accepted_receipt_authority(
    receipt: &IngestReceipt,
    source_bytes: &[u8],
    project_id: ProjectId,
    originating_job_id: heleos_core::JobId,
    inspection: &FoundationInspection,
) {
    let original = Sha256Digest::hash_reader(source_bytes).expect("hash accepted source fixture");
    let byte_length = u64::try_from(source_bytes.len()).expect("source length fits");
    let (document_id, revision_id) = canonical_document_ids(original);
    let (manifest_digest, manifest_bytes) = accepted_manifest_bytes(source_bytes);
    let manifest_length = u64::try_from(manifest_bytes.len()).expect("manifest length fits");

    assert_eq!(receipt.content_sha256, Some(original));
    assert_eq!(receipt.byte_length, byte_length);
    assert_eq!(receipt.quarantine, None);
    assert_eq!(receipt.document_id, Some(document_id));
    assert_eq!(receipt.revision_id, Some(revision_id));
    assert_eq!(receipt.sheet_ids, vec![page_id(original, 0)]);
    let evidence = receipt
        .evidence_manifest
        .as_ref()
        .expect("accepted receipt evidence");
    assert_eq!(
        canonical_json(&evidence.manifest).expect("canonical receipt manifest"),
        manifest_bytes
    );
    assert_eq!(evidence.manifest_content_sha256, manifest_digest);
    assert_eq!(evidence.manifest_byte_length, manifest_length);
    assert_eq!(evidence.manifest_media_type, EVIDENCE_MANIFEST_MEDIA_TYPE);
    assert_eq!(
        evidence.manifest_vault_key,
        Vault::object_key(manifest_digest)
    );
    assert_eq!(evidence.original_vault_key, Vault::object_key(original));

    assert_eq!(inspection.sheets.len(), 1);
    let sheet = &inspection.sheets[0];
    assert_eq!(sheet.sheet_id, page_id(original, 0));
    assert_eq!(sheet.revision_id, revision_id);
    assert_eq!(sheet.index, 0);
    assert_eq!(sheet.width_micropoints, 612_000_000);
    assert_eq!(sheet.height_micropoints, 792_000_000);
    assert_eq!(sheet.unit, PageUnit::Point);
    assert_eq!(sheet.rotation_degrees, 0);
    assert_eq!(
        sheet.transform,
        PageTransform {
            m11: 1,
            m12: 0,
            m21: 0,
            m22: -1,
            tx_micropoints: 0,
            ty_micropoints: 792_000_000,
        }
    );
    assert_eq!(sheet.parent_content_sha256, original);

    assert_eq!(inspection.evidence.len(), 1);
    let authority = &inspection.evidence[0];
    assert_eq!(authority.originating_job_id, originating_job_id);
    assert_eq!(authority.document_id, document_id);
    assert_eq!(authority.revision_id, revision_id);
    assert_eq!(authority.original.sha256, original);
    assert_eq!(authority.original.byte_length, byte_length);
    assert_eq!(authority.original.vault_key, Vault::object_key(original));
    assert_eq!(authority.original.media_type, PDF_MEDIA_TYPE);
    assert_eq!(authority.manifest.sha256, manifest_digest);
    assert_eq!(authority.manifest.byte_length, manifest_length);
    assert_eq!(
        authority.manifest.vault_key,
        Vault::object_key(manifest_digest)
    );
    assert_eq!(authority.manifest.media_type, EVIDENCE_MANIFEST_MEDIA_TYPE);
    assert_eq!(authority.probe_provenance, accepted_probe().provenance);
    assert_eq!(evidence.lineages.len(), 1);
    assert_eq!(evidence.lineages[0].project_id, project_id);
    assert_eq!(evidence.lineages[0].evidence_id, authority.evidence_id);
    assert_eq!(evidence.lineages[0].originating_job_id, originating_job_id);
}

fn assert_quarantine_receipt_authority(
    receipt: &IngestReceipt,
    source_bytes: &[u8],
    expected_reason: IntakeQuarantineReasonV1,
    expected_provenance: &PdfProbeProvenance,
    inspection: &FoundationInspection,
) {
    let original = Sha256Digest::hash_reader(source_bytes).expect("hash quarantine source fixture");
    let byte_length = u64::try_from(source_bytes.len()).expect("source length fits");
    assert_eq!(receipt.content_sha256, Some(original));
    assert_eq!(receipt.byte_length, byte_length);
    let quarantine = receipt.quarantine.as_ref().expect("quarantine receipt");
    assert_eq!(quarantine.schema, heleos_core::INTAKE_QUARANTINE_SCHEMA_V1);
    assert_eq!(quarantine.reason, expected_reason);
    assert_eq!(
        quarantine.probe_provenance.as_ref(),
        Some(expected_provenance)
    );
    assert_eq!(receipt.document_id, None);
    assert_eq!(receipt.revision_id, None);
    assert!(receipt.sheet_ids.is_empty());
    assert_eq!(receipt.evidence_manifest, None);
    assert_eq!(inspection.counts.content_objects, 1);
    assert_eq!(inspection.counts.documents, 0);
    assert_eq!(inspection.counts.revisions, 0);
    assert_eq!(inspection.counts.project_documents, 0);
    assert_eq!(inspection.counts.sheets, 0);
    assert_eq!(inspection.counts.evidence_objects, 0);
    assert!(inspection.document_ids.is_empty());
    assert!(inspection.revision_ids.is_empty());
    assert!(inspection.sheets.is_empty());
    assert!(inspection.evidence.is_empty());
    assert_eq!(inspection.content_objects.len(), 1);
    let authority = inspection
        .content_objects
        .iter()
        .find(|content| content.sha256 == original)
        .expect("quarantine content authority");
    assert_eq!(authority.byte_length, byte_length);
    assert_eq!(authority.media_type, PDF_MEDIA_TYPE);
    assert_eq!(
        authority.admission_state,
        FoundationAdmissionState::Quarantined
    );
    assert_eq!(authority.vault_key, Vault::object_key(original));
    assert_eq!(authority.quarantine.as_ref(), Some(quarantine));
}

fn terminal_receipt_from_database(
    database_path: &std::path::Path,
    job_id: heleos_core::JobId,
) -> IngestReceipt {
    let connection = rusqlite::Connection::open(database_path).expect("open receipt observer");
    let checkpoint_json: String = connection
        .query_row(
            "SELECT checkpoint_json FROM job_runs WHERE id = ?1",
            [job_id.as_uuid().to_string()],
            |row| row.get(0),
        )
        .expect("read terminal checkpoint");
    let checkpoint: serde_json::Value =
        serde_json::from_str(&checkpoint_json).expect("parse terminal checkpoint");
    serde_json::from_value(checkpoint["detail"]["receipt"].clone()).expect("parse terminal receipt")
}

fn completed_job_audit_identities(
    project_id: ProjectId,
    receipt: &IngestReceipt,
    include_project_creation: bool,
    include_evidence_creation: bool,
) -> Vec<ExpectedAuditIdentity> {
    let mut identities = Vec::new();
    if include_project_creation {
        identities.push(audit_identity(
            "project_created",
            "project",
            project_id.as_uuid(),
        ));
    }
    for action in [
        "job_created",
        "job_started",
        "job_checkpointed",
        "job_succeeded",
    ] {
        identities.push(audit_identity(
            action,
            "job",
            receipt.authoritative_job_id.as_uuid(),
        ));
    }
    identities.push(audit_identity(
        if receipt.evidence_manifest.is_some() {
            "ingest_accepted"
        } else {
            "ingest_quarantined"
        },
        "ingest_attempt",
        receipt.ingest_event_id.as_uuid(),
    ));
    if include_evidence_creation {
        let evidence_id = receipt
            .evidence_manifest
            .as_ref()
            .expect("evidence-bearing receipt")
            .lineages
            .iter()
            .find(|lineage| lineage.project_id == project_id)
            .expect("receipt project lineage")
            .evidence_id;
        identities.push(audit_identity(
            "evidence_created",
            "evidence",
            evidence_id.as_uuid(),
        ));
    }
    identities
}

fn fault_job_final_audit_identities(
    point: FaultPoint,
    receipt: &IngestReceipt,
    include_evidence_creation: bool,
) -> Vec<ExpectedAuditIdentity> {
    let job_id = receipt.authoritative_job_id;
    let mut identities = vec![audit_identity("job_created", "job", job_id.as_uuid())];
    identities.push(audit_identity("job_started", "job", job_id.as_uuid()));
    match point {
        FaultPoint::AfterVaultPublish => {
            identities.push(audit_identity("job_checkpointed", "job", job_id.as_uuid()));
        }
        FaultPoint::AfterJobStart => {
            identities.push(audit_identity("job_interrupted", "job", job_id.as_uuid()));
            identities.push(audit_identity("job_resumed", "job", job_id.as_uuid()));
            identities.push(audit_identity("job_checkpointed", "job", job_id.as_uuid()));
        }
        FaultPoint::AfterCheckpointCommit | FaultPoint::BeforeAuthoritativeCommit => {
            identities.push(audit_identity("job_checkpointed", "job", job_id.as_uuid()));
            identities.push(audit_identity("job_interrupted", "job", job_id.as_uuid()));
            identities.push(audit_identity("job_resumed", "job", job_id.as_uuid()));
        }
        FaultPoint::AfterAuthoritativeCommit => {
            identities.push(audit_identity("job_checkpointed", "job", job_id.as_uuid()));
        }
    }
    identities.push(audit_identity("job_succeeded", "job", job_id.as_uuid()));
    identities.push(audit_identity(
        if receipt.evidence_manifest.is_some() {
            "ingest_accepted"
        } else {
            "ingest_quarantined"
        },
        "ingest_attempt",
        receipt.ingest_event_id.as_uuid(),
    ));
    if include_evidence_creation {
        let evidence_id = receipt
            .evidence_manifest
            .as_ref()
            .expect("evidence-bearing receipt")
            .lineages
            .first()
            .expect("receipt lineage")
            .evidence_id;
        identities.push(audit_identity(
            "evidence_created",
            "evidence",
            evidence_id.as_uuid(),
        ));
    }
    if point == FaultPoint::AfterAuthoritativeCommit {
        identities.push(audit_identity(
            "ingest_replayed",
            "ingest_attempt",
            receipt.ingest_event_id.as_uuid(),
        ));
    }
    identities
}

fn accepted_reader_fixture() -> (
    Store,
    Vault,
    TempDir,
    RevisionId,
    Sha256Digest,
    Sha256Digest,
) {
    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let ids = SequenceIds::new(
        (2_u128..=40).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
    );
    ProjectService::new(&mut store, &FixedClock, &ids)
        .expect("construct project service")
        .create(ProjectCreateRequest {
            project_id: Some(project_id),
            name: "Demo".to_owned(),
            actor: ActorId::from_str("tester").expect("actor"),
            data_class: DataClass::Internal,
        })
        .expect("create project");
    let source_root = TempDir::new().expect("create source directory");
    let source_path = source_root.path().join("fixture.pdf");
    let source_bytes = b"%PDF-1.7\nfixture\n%%EOF\n";
    fs::write(&source_path, source_bytes).expect("write source fixture");
    let original = Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
    let (_, revision) = canonical_document_ids(original);
    let receipt = IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
        .expect("construct intake engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open retained intake source"),
            idempotency_key: IdempotencyKey::try_from("fixture-key").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("ingest fixture");
    let manifest = receipt
        .evidence_manifest
        .expect("accepted evidence receipt")
        .manifest_content_sha256;
    (store, vault, vault_root, revision, original, manifest)
}

#[test]
fn audited_project_creation_matches_the_frozen_hash_vector() {
    // Break caught: project creation that omits its same-transaction, domain-separated audit row.
    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let audit_id = AuditEventId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000002").expect("audit UUID"),
    );
    let ids = SequenceIds::new([*audit_id.as_uuid()]);
    let request = ProjectCreateRequest {
        project_id: Some(project_id),
        name: "Demo".to_owned(),
        actor: ActorId::from_str("tester").expect("actor"),
        data_class: DataClass::Internal,
    };

    let receipt = ProjectService::new(&mut store, &FixedClock, &ids)
        .expect("construct project service")
        .create(request)
        .expect("create audited project");

    assert_eq!(receipt.project_id, project_id);
    assert_eq!(receipt.created_at_ms, NOW_MS);
    assert_eq!(receipt.audit_event_id, audit_id);
    assert_eq!(
        canonical_json(&receipt).expect("canonical receipt"),
        br#"{"audit_event_id":"00000000-0000-4000-8000-000000000002","created_at_ms":1700000000000,"project_id":"00000000-0000-4000-8000-000000000001"}"#
    );

    let report = FoundationReader::new(&store, &vault)
        .verify_audit_chain()
        .expect("verify audit chain");
    assert!(report.valid);
    assert_eq!(report.checked_event_count, 1);
    assert_eq!(report.first_sequence, Some(1));
    assert_eq!(report.last_sequence, Some(1));
    assert_eq!(
        report.head_hash,
        Sha256Digest::from_str("266987a9f2db93655d060542189a830bb76fbc5e9e88ea93add8fc5958614578")
            .expect("frozen hash")
    );
    assert!(report.findings.is_empty());
    assert!(!report.findings_truncated);
}

#[test]
fn empty_foundation_inspection_has_the_frozen_bounded_image() {
    // Break caught: inspection depending on insertion order or leaking unscoped foundation rows.
    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let audit_id = AuditEventId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000002").expect("audit UUID"),
    );
    let ids = SequenceIds::new([*audit_id.as_uuid()]);
    ProjectService::new(&mut store, &FixedClock, &ids)
        .expect("construct project service")
        .create(ProjectCreateRequest {
            project_id: Some(project_id),
            name: "Demo".to_owned(),
            actor: ActorId::from_str("tester").expect("actor"),
            data_class: DataClass::Internal,
        })
        .expect("create project");

    let inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect empty project");
    let bytes = canonical_json(&inspection).expect("canonical inspection");
    assert_eq!(
        bytes,
        br#"{"content_objects":[],"counts":{"audit_events":1,"content_objects":0,"documents":0,"evidence_objects":0,"ingest_events":0,"jobs":0,"project_documents":0,"revisions":0,"sheets":0},"document_ids":[],"evidence":[],"intake_events":[],"jobs":[],"project_id":"00000000-0000-4000-8000-000000000001","revision_ids":[],"schema":"heleos.foundation-inspection/v1","sheets":[]}"#
    );
    let decoded = serde_json::from_slice::<FoundationInspection>(&bytes)
        .expect("strict inspection round trip");
    assert_eq!(decoded, inspection);
    let missing_project = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000003").expect("missing project UUID"),
    );
    assert!(matches!(
        FoundationReader::new(&store, &vault).inspect_foundation(missing_project),
        Err(HeleosError::NotFound)
    ));
    assert!(matches!(
        FoundationReader::new(&store, &vault)
            .evidence_manifest_for_revision(RevisionId::from(Sha256Digest::from_bytes([9; 32]))),
        Err(HeleosError::NotFound)
    ));
}

#[test]
fn idempotency_keys_reject_controls_and_utf8_byte_overflow() {
    // Break caught: an unbounded/control-bearing key reaching SQL or canonical job input.
    assert!(IdempotencyKey::try_from("stable-key").is_ok());
    assert!(IdempotencyKey::try_from("").is_err());
    assert!(IdempotencyKey::try_from("bad\nkey").is_err());
    assert!(IdempotencyKey::try_from("x".repeat(128)).is_ok());
    assert!(IdempotencyKey::try_from("x".repeat(129)).is_err());
    assert!(IdempotencyKey::try_from("é".repeat(64)).is_ok());
    assert!(IdempotencyKey::try_from(format!("{}x", "é".repeat(64))).is_err());
}

#[test]
fn public_audit_and_project_dtos_reject_invalid_cross_field_states() {
    // Break caught: unchecked serde constructing impossible authoritative/public DTO states.
    let invalid_project_receipt = serde_json::json!({
        "project_id": "00000000-0000-4000-8000-000000000001",
        "created_at_ms": -1,
        "audit_event_id": "00000000-0000-4000-8000-000000000002"
    });
    assert!(serde_json::from_value::<ProjectReceipt>(invalid_project_receipt).is_err());

    let invalid_event = serde_json::json!({
        "id": "00000000-0000-4000-8000-000000000002",
        "sequence": 0,
        "project_id": null,
        "actor": "tester",
        "action": "project_created",
        "subject_type": "project",
        "subject_id": "subject",
        "before": null,
        "after": null,
        "reason": "reason",
        "occurred_at_ms": 0,
        "previous_hash": "0000000000000000000000000000000000000000000000000000000000000000",
        "event_hash": "0000000000000000000000000000000000000000000000000000000000000000"
    });
    assert!(serde_json::from_value::<AuditEvent>(invalid_event).is_err());

    let invalid_report = serde_json::json!({
        "schema": "wrong",
        "valid": true,
        "checked_event_count": 0,
        "first_sequence": null,
        "last_sequence": null,
        "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
        "findings": [],
        "findings_truncated": false
    });
    assert!(serde_json::from_value::<AuditChainReport>(invalid_report).is_err());

    let finding_with_unknown_field = serde_json::json!({
        "kind": "non_canonical_before",
        "detail": {
            "event_id": "00000000-0000-4000-8000-000000000002",
            "sequence": 1,
            "unknown": true
        }
    });
    assert!(serde_json::from_value::<AuditChainFinding>(finding_with_unknown_field).is_err());
    let finding_with_unknown_outer_field = serde_json::json!({
        "kind": "non_canonical_before",
        "detail": {
            "event_id": "00000000-0000-4000-8000-000000000002",
            "sequence": 1
        },
        "unknown": true
    });
    assert!(serde_json::from_value::<AuditChainFinding>(finding_with_unknown_outer_field).is_err());
}

#[test]
fn missing_project_is_rejected_before_any_retained_source_work() {
    // Break caught: source fingerprint I/O or mutation masking the frozen NotFound precedence.
    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let source_root = TempDir::new().expect("create source directory");
    let source_path = source_root.path().join("mutated.pdf");
    let oversized = fs::File::create(&source_path).expect("create hostile sparse source");
    oversized
        .set_len(PdfLimits::default().max_input_bytes + 1)
        .expect("size hostile sparse source");
    drop(oversized);
    let missing_project = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let ids = SequenceIds::new(std::iter::empty::<Uuid>());
    let mut nondefault_limits = PdfLimits::default();
    nondefault_limits.max_pages -= 1;
    let nondefault = IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
        .expect("construct intake engine")
        .ingest(IngestRequest {
            project_id: missing_project,
            source: IntakeSource::open(&source_path).expect("open retained intake source"),
            idempotency_key: IdempotencyKey::try_from("missing-nondefault").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: nondefault_limits,
        });
    assert!(matches!(nondefault, Err(HeleosError::NotFound)));

    let result = IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
        .expect("construct intake engine")
        .ingest(IngestRequest {
            project_id: missing_project,
            source: IntakeSource::open(&source_path).expect("open retained intake source"),
            idempotency_key: IdempotencyKey::try_from("missing-project").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });

    assert!(matches!(result, Err(HeleosError::NotFound)));
    assert!(
        FoundationReader::new(&store, &vault)
            .verify_audit_chain()
            .expect("verify unchanged audit chain")
            .checked_event_count
            == 0
    );
}

#[test]
#[cfg(unix)]
fn unrelated_orphan_pressure_rejects_a_novel_original_without_residue() {
    // Break caught: original publication ignoring unbound vault bytes charged to quarantine.
    const QUARANTINE_BYTES: u64 = 8 * 1024 * 1024 * 1024;

    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_parent = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_parent);
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let ids = SequenceIds::new(
        (2_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
    );
    ProjectService::new(&mut store, &FixedClock, &ids)
        .expect("construct project service")
        .create(ProjectCreateRequest {
            project_id: Some(project_id),
            name: "Original orphan pressure".to_owned(),
            actor: ActorId::from_str("creator").expect("actor"),
            data_class: DataClass::Internal,
        })
        .expect("create project");
    let source_root = TempDir::new().expect("create source parent");
    let source_path = source_root.path().join("novel.pdf");
    let source_bytes = b"%PDF-1.7\nnovel under orphan pressure\n%%EOF\n";
    fs::write(&source_path, source_bytes).expect("write source");
    let source_digest =
        Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source fixture");
    create_sparse_vault_orphans(&vault_parent, QUARANTINE_BYTES, &[source_digest]);

    let probe = accepted_probe();
    let receipt = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
        .expect("construct intake engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open source"),
            idempotency_key: IdempotencyKey::try_from("orphan-original").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("orphan pressure is a finite result");

    assert_eq!(receipt.outcome, IngestOutcome::QuarantinedLimit);
    assert!(matches!(
        receipt
            .quarantine
            .as_ref()
            .expect("quota quarantine")
            .reason,
        IntakeQuarantineReasonV1::OriginalRetentionQuota
    ));
    assert_eq!(receipt.content_sha256, Some(source_digest));
    assert_eq!(receipt.preexisting_vault_digests, Vec::new());
    assert!(matches!(
        vault.open_verified(source_digest),
        Err(HeleosError::NotFound)
    ));
    let no_probe = FailingProbe {
        provenance: probe.provenance.clone(),
    };
    let replay = IngestEngine::new(&mut store, &vault, &no_probe, &FixedClock, &ids)
        .expect("construct nonretained replay engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("reopen source"),
            idempotency_key: IdempotencyKey::try_from("orphan-original").expect("key"),
            actor: ActorId::from_str("replayer").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("replay nonretained original-quota authority");
    assert_eq!(replay.outcome, IngestOutcome::IdempotentReplay);
    assert_eq!(replay.content_sha256, Some(source_digest));
    assert_eq!(replay.quarantine, receipt.quarantine);
    assert!(replay.preexisting_vault_digests.is_empty());
    let staging = fs::canonicalize(vault_parent.path())
        .expect("canonicalize vault parent")
        .join("vault/.staging");
    assert_eq!(fs::read_dir(staging).expect("read staging").count(), 0);
    let inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect finite quota outcome");
    assert_eq!(inspection.counts.jobs, 1);
    assert_eq!(inspection.counts.ingest_events, 2);
    assert_eq!(inspection.counts.content_objects, 0);
}

#[test]
#[cfg(unix)]
fn unrelated_orphan_pressure_rejects_a_novel_manifest_but_retains_the_original() {
    // Break caught: manifest publication ignoring orphans created before its processing checkpoint.
    const QUARANTINE_BYTES: u64 = 8 * 1024 * 1024 * 1024;

    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_parent = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_parent);
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let ids = SequenceIds::new(
        (2_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
    );
    ProjectService::new(&mut store, &FixedClock, &ids)
        .expect("construct project service")
        .create(ProjectCreateRequest {
            project_id: Some(project_id),
            name: "Manifest orphan pressure".to_owned(),
            actor: ActorId::from_str("creator").expect("actor"),
            data_class: DataClass::Internal,
        })
        .expect("create project");
    let source_root = TempDir::new().expect("create source parent");
    let source_path = source_root.path().join("accepted.pdf");
    let source_bytes = b"%PDF-1.7\nmanifest under orphan pressure\n%%EOF\n";
    fs::write(&source_path, source_bytes).expect("write source");
    let source_digest =
        Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source fixture");
    let (manifest_digest, _) = accepted_manifest_bytes(source_bytes);
    let orphan_bytes = QUARANTINE_BYTES
        .checked_sub(u64::try_from(source_bytes.len()).expect("source length fits"))
        .expect("source fits quarantine allowance");
    create_sparse_vault_orphans(
        &vault_parent,
        orphan_bytes,
        &[source_digest, manifest_digest],
    );

    let receipt = IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
        .expect("construct intake engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open source"),
            idempotency_key: IdempotencyKey::try_from("orphan-manifest").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("manifest pressure is a finite result");

    assert_eq!(receipt.outcome, IngestOutcome::QuarantinedLimit);
    assert!(matches!(
        receipt
            .quarantine
            .expect("manifest quota quarantine")
            .reason,
        IntakeQuarantineReasonV1::EvidenceManifestQuota
    ));
    assert_eq!(receipt.content_sha256, Some(source_digest));
    assert_eq!(receipt.preexisting_vault_digests, Vec::new());
    assert!(vault.open_verified(source_digest).is_ok());
    assert!(matches!(
        vault.open_verified(manifest_digest),
        Err(HeleosError::NotFound)
    ));
    let inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect retained original");
    assert_eq!(inspection.counts.content_objects, 1);
    assert_eq!(inspection.content_objects[0].sha256, source_digest);
    assert!(matches!(
        inspection.content_objects[0]
            .quarantine
            .as_ref()
            .expect("sticky manifest quota")
            .reason,
        IntakeQuarantineReasonV1::EvidenceManifestQuota
    ));
}

#[test]
#[cfg(unix)]
fn current_original_orphan_is_not_double_debited_at_the_exact_boundary() {
    // Break caught: charging the requested orphan once in the scan and again as a novel debit.
    const QUARANTINE_BYTES: u64 = 8 * 1024 * 1024 * 1024;

    for excess_bytes in [0_u64, 1] {
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let vault_parent = TempDir::new().expect("create vault parent");
        let vault = opened_vault(&vault_parent);
        let project_id = ProjectId::from_uuid(
            Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
        );
        let ids = SequenceIds::new(
            (2_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
        );
        ProjectService::new(&mut store, &FixedClock, &ids)
            .expect("construct project service")
            .create(ProjectCreateRequest {
                project_id: Some(project_id),
                name: "Current original orphan".to_owned(),
                actor: ActorId::from_str("creator").expect("actor"),
                data_class: DataClass::Internal,
            })
            .expect("create project");
        let source_root = TempDir::new().expect("create source parent");
        let source_path = source_root.path().join("orphan.pdf");
        let source_bytes = b"%PDF-1.7\ncurrent physical orphan\n%%EOF\n";
        fs::write(&source_path, source_bytes).expect("write source");
        let source_length = u64::try_from(source_bytes.len()).expect("source length fits");
        let source_digest =
            Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
        let published = vault
            .put_reader(
                source_bytes.as_slice(),
                VaultWriteBudget::new(source_length, source_length).expect("orphan write budget"),
            )
            .expect("publish pre-job orphan");
        assert!(matches!(
            published,
            heleos_core::PutOutcome::Stored(ref stored)
                if stored.digest == source_digest && stored.newly_published
        ));
        create_sparse_vault_orphans(
            &vault_parent,
            QUARANTINE_BYTES
                .checked_sub(source_length)
                .and_then(|value| value.checked_add(excess_bytes))
                .expect("bounded orphan pressure"),
            &[source_digest],
        );
        let probe = QuarantinedProbe {
            provenance: accepted_probe().provenance,
            reason: PdfQuarantineReason::Corrupt,
        };
        let result = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
            .expect("construct intake engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("open source"),
                idempotency_key: IdempotencyKey::try_from("current-original-orphan").expect("key"),
                actor: ActorId::from_str("tester").expect("actor"),
                pdf_limits: PdfLimits::default(),
            });

        if excess_bytes == 0 {
            let receipt = result.expect("exact orphan allowance admits verified duplicate");
            assert_eq!(receipt.outcome, IngestOutcome::QuarantinedCorrupt);
            assert_eq!(receipt.preexisting_vault_digests, vec![source_digest]);
            assert_eq!(
                FoundationReader::new(&store, &vault)
                    .inspect_foundation(project_id)
                    .expect("inspect exact-boundary authority")
                    .counts
                    .content_objects,
                1
            );
        } else {
            assert!(matches!(result, Err(HeleosError::Integrity)));
            let inspection = FoundationReader::new(&store, &vault)
                .inspect_foundation(project_id)
                .expect("inspect rejected overage");
            assert_eq!(inspection.counts.jobs, 0);
            assert_eq!(inspection.counts.content_objects, 0);
            assert_eq!(
                vault
                    .open_verified(source_digest)
                    .expect("existing orphan is not repaired or removed")
                    .byte_length(),
                source_length
            );
        }
    }
}

#[test]
#[cfg(unix)]
fn current_manifest_orphan_recovers_at_the_exact_boundary_only() {
    // Break caught: manifest resume double-debiting its current orphan or ignoring other orphans.
    const QUARANTINE_BYTES: u64 = 8 * 1024 * 1024 * 1024;

    for excess_bytes in [0_u64, 1] {
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let vault_parent = TempDir::new().expect("create vault parent");
        let vault = opened_vault(&vault_parent);
        let project_id = ProjectId::from_uuid(
            Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
        );
        let ids = SequenceIds::new(
            (2_u128..=160).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
        );
        ProjectService::new(&mut store, &FixedClock, &ids)
            .expect("construct project service")
            .create(ProjectCreateRequest {
                project_id: Some(project_id),
                name: "Current manifest orphan".to_owned(),
                actor: ActorId::from_str("creator").expect("actor"),
                data_class: DataClass::Internal,
            })
            .expect("create project");
        let source_root = TempDir::new().expect("create source parent");
        let source_path = source_root.path().join("accepted.pdf");
        let source_bytes = b"%PDF-1.7\nmanifest recovery orphan\n%%EOF\n";
        fs::write(&source_path, source_bytes).expect("write source");
        let source_length = u64::try_from(source_bytes.len()).expect("source length fits");
        let source_digest =
            Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
        let interrupted = IngestEngine::with_fault_injector(
            &mut store,
            &vault,
            &accepted_probe(),
            &FixedClock,
            &ids,
            &FailAt(FaultPoint::AfterVaultPublish),
        )
        .expect("construct faulting engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open source"),
            idempotency_key: IdempotencyKey::try_from("manifest-orphan-resume").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
        assert!(matches!(interrupted, Err(HeleosError::FaultInjected)));
        let job_id = FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect queued checkpoint")
            .jobs[0]
            .job_id;

        let (manifest_digest, manifest_bytes) = accepted_manifest_bytes(source_bytes);
        let manifest_length = u64::try_from(manifest_bytes.len()).expect("manifest length fits");
        let published = vault
            .put_reader(
                manifest_bytes.as_slice(),
                VaultWriteBudget::new(manifest_length, manifest_length)
                    .expect("manifest orphan budget"),
            )
            .expect("publish pre-checkpoint manifest orphan");
        assert!(matches!(
            published,
            heleos_core::PutOutcome::Stored(ref stored)
                if stored.digest == manifest_digest && stored.newly_published
        ));
        create_sparse_vault_orphans(
            &vault_parent,
            QUARANTINE_BYTES
                .checked_sub(source_length)
                .and_then(|value| value.checked_sub(manifest_length))
                .and_then(|value| value.checked_add(excess_bytes))
                .expect("bounded manifest orphan pressure"),
            &[source_digest, manifest_digest],
        );

        let resumed = IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
            .expect("construct resume engine")
            .resume(job_id, ActorId::from_str("resumer").expect("actor"));
        if excess_bytes == 0 {
            let receipt = resumed
                .expect("exact orphan allowance admits manifest duplicate")
                .receipt;
            assert_eq!(receipt.outcome, IngestOutcome::AcceptedNew);
            let mut expected = vec![source_digest, manifest_digest];
            expected.sort_unstable();
            assert_eq!(receipt.preexisting_vault_digests, expected);
        } else {
            assert!(matches!(resumed, Err(HeleosError::Integrity)));
            let inspection = FoundationReader::new(&store, &vault)
                .inspect_foundation(project_id)
                .expect("inspect failed overage recovery");
            assert_eq!(inspection.jobs.len(), 1);
            assert_eq!(inspection.jobs[0].state, heleos_core::JobState::Failed);
            assert_eq!(inspection.counts.content_objects, 0);
            assert!(vault.open_verified(source_digest).is_ok());
            assert!(vault.open_verified(manifest_digest).is_ok());
        }
    }
}

#[test]
fn an_inventory_bound_original_must_not_be_repaired_when_missing_or_corrupt() {
    // Break caught: treating a durable checkpoint object as a novel publication candidate.
    for remove_object in [true, false] {
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let vault_parent = TempDir::new().expect("create vault parent");
        let vault = opened_vault(&vault_parent);
        let project_id = ProjectId::from_uuid(
            Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
        );
        let ids = SequenceIds::new(
            (2_u128..=180).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
        );
        ProjectService::new(&mut store, &FixedClock, &ids)
            .expect("construct project service")
            .create(ProjectCreateRequest {
                project_id: Some(project_id),
                name: "Bound object integrity".to_owned(),
                actor: ActorId::from_str("creator").expect("actor"),
                data_class: DataClass::Internal,
            })
            .expect("create project");
        let source_root = TempDir::new().expect("create source parent");
        let source_path = source_root.path().join("bound.pdf");
        let source_bytes = b"%PDF-1.7\ninventory-bound original\n%%EOF\n";
        fs::write(&source_path, source_bytes).expect("write source");
        let source_digest =
            Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
        let interrupted = IngestEngine::with_fault_injector(
            &mut store,
            &vault,
            &accepted_probe(),
            &FixedClock,
            &ids,
            &FailAt(FaultPoint::AfterVaultPublish),
        )
        .expect("construct faulting engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open source"),
            idempotency_key: IdempotencyKey::try_from("reserved-original").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
        assert!(matches!(interrupted, Err(HeleosError::FaultInjected)));
        let object_path = fs::canonicalize(vault_parent.path())
            .expect("canonicalize vault parent")
            .join("vault")
            .join(Vault::object_key(source_digest));
        if remove_object {
            fs::remove_file(&object_path).expect("remove checkpoint object");
        } else {
            fs::write(&object_path, vec![0_u8; source_bytes.len()])
                .expect("replace checkpoint object at the same length");
            heleos_core::apply_private_permissions(&object_path)
                .expect("reharden corrupted object");
        }

        let second = IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
            .expect("construct second engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("reopen source"),
                idempotency_key: IdempotencyKey::try_from("must-not-repair").expect("key"),
                actor: ActorId::from_str("tester").expect("actor"),
                pdf_limits: PdfLimits::default(),
            });
        assert!(matches!(second, Err(HeleosError::Integrity)));
        let inspection = FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect unchanged nonterminal authority");
        assert_eq!(inspection.jobs.len(), 1);
        assert_eq!(inspection.jobs[0].state, heleos_core::JobState::Queued);
        assert_eq!(inspection.counts.content_objects, 0);
        if remove_object {
            assert!(!object_path.exists());
        } else {
            assert!(matches!(
                vault.open_verified(source_digest),
                Err(HeleosError::Integrity)
            ));
        }
    }
}

#[test]
fn an_inventory_bound_manifest_must_not_be_repaired_when_missing_or_corrupt() {
    // Break caught: self-healing a manifest after its ProcessingComplete authority is durable.
    for remove_object in [true, false] {
        let database_root = TempDir::new().expect("create database parent");
        heleos_core::apply_private_permissions(database_root.path())
            .expect("harden database parent");
        let database_path = fs::canonicalize(database_root.path())
            .expect("canonicalize database parent")
            .join("foundation.sqlite3");
        let mut store = Store::open_writer(&database_path).expect("open store");
        store.migrate().expect("migrate store");
        let vault_parent = TempDir::new().expect("create vault parent");
        let vault = opened_vault(&vault_parent);
        let project_id = ProjectId::from_uuid(
            Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
        );
        let ids = SequenceIds::new(
            (2_u128..=220).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
        );
        let clock = ManualClock::new(NOW_MS);
        ProjectService::new(&mut store, &clock, &ids)
            .expect("construct project service")
            .create(ProjectCreateRequest {
                project_id: Some(project_id),
                name: "Bound manifest integrity".to_owned(),
                actor: ActorId::from_str("creator").expect("actor"),
                data_class: DataClass::Internal,
            })
            .expect("create project");
        let source_root = TempDir::new().expect("create source parent");
        let source_path = source_root.path().join("bound-manifest.pdf");
        let source_bytes = b"%PDF-1.7\ninventory-bound manifest\n%%EOF\n";
        fs::write(&source_path, source_bytes).expect("write source");
        let source_digest =
            Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
        let (manifest_digest, manifest_bytes) = accepted_manifest_bytes(source_bytes);
        let interrupted = IngestEngine::with_fault_injector(
            &mut store,
            &vault,
            &accepted_probe(),
            &clock,
            &ids,
            &FailAt(FaultPoint::AfterCheckpointCommit),
        )
        .expect("construct checkpoint fault engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open source"),
            idempotency_key: IdempotencyKey::try_from("reserved-manifest").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
        assert!(matches!(interrupted, Err(HeleosError::FaultInjected)));
        let job_id = FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect processing checkpoint")
            .jobs[0]
            .job_id;
        let object_path = fs::canonicalize(vault_parent.path())
            .expect("canonicalize vault parent")
            .join("vault")
            .join(Vault::object_key(manifest_digest));
        drop(store);
        drop(vault);
        if remove_object {
            fs::remove_file(&object_path).expect("remove checkpoint manifest");
        } else {
            fs::write(&object_path, vec![0_u8; manifest_bytes.len()])
                .expect("replace checkpoint manifest at the same length");
            heleos_core::apply_private_permissions(&object_path)
                .expect("reharden corrupted manifest");
        }
        let mut store = Store::open_writer(&database_path).expect("reopen manifest writer");
        let vault = reopened_vault(&vault_parent);
        clock.set(NOW_MS + 30_000);

        let resumed = IngestEngine::new(&mut store, &vault, &accepted_probe(), &clock, &ids)
            .expect("construct resume engine")
            .resume(job_id, ActorId::from_str("resumer").expect("actor"));
        assert!(matches!(resumed, Err(HeleosError::Integrity)));
        let inspection = FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect failed recovery");
        assert_eq!(inspection.jobs.len(), 1);
        assert_eq!(inspection.jobs[0].state, heleos_core::JobState::Failed);
        assert_eq!(inspection.counts.content_objects, 0);
        assert!(vault.open_verified(source_digest).is_ok());
        if remove_object {
            assert!(!object_path.exists());
        } else {
            assert!(matches!(
                vault.open_verified(manifest_digest),
                Err(HeleosError::Integrity)
            ));
        }
    }
}

#[test]
fn reopened_resume_rejects_input_checkpoint_audit_provenance_and_original_corruption() {
    // Break caught: restart trusting a stale job anchor, a changed probe identity, or repairing a
    // missing/corrupt checkpoint original before returning authority.
    for mutation in [
        "input",
        "checkpoint",
        "audit_hash",
        "provenance",
        "missing_original",
        "corrupt_original",
    ] {
        let database_root = TempDir::new().expect("create database parent");
        heleos_core::apply_private_permissions(database_root.path())
            .expect("harden database parent");
        let database_path = fs::canonicalize(database_root.path())
            .expect("canonicalize database parent")
            .join("foundation.sqlite3");
        let mut store = Store::open_writer(&database_path).expect("open store");
        store.migrate().expect("migrate store");
        let vault_root = TempDir::new().expect("create vault parent");
        let vault = opened_vault(&vault_root);
        let project_id = ProjectId::from_uuid(
            Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
        );
        let ids = SequenceIds::new(
            (2_u128..=250).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
        );
        let clock = ManualClock::new(NOW_MS);
        ProjectService::new(&mut store, &clock, &ids)
            .expect("construct project service")
            .create(ProjectCreateRequest {
                project_id: Some(project_id),
                name: format!("Resume corruption {mutation}"),
                actor: ActorId::from_str("creator").expect("actor"),
                data_class: DataClass::Internal,
            })
            .expect("create project");
        let source_root = TempDir::new().expect("create source parent");
        let source_path = source_root.path().join("resume-corruption.pdf");
        let source_bytes = b"%PDF-1.7\nreopened resume corruption\n%%EOF\n";
        fs::write(&source_path, source_bytes).expect("write source");
        let source_digest =
            Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
        let interrupted = IngestEngine::with_fault_injector(
            &mut store,
            &vault,
            &accepted_probe(),
            &clock,
            &ids,
            &FailAt(FaultPoint::AfterJobStart),
        )
        .expect("construct fault engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open source"),
            idempotency_key: IdempotencyKey::try_from("resume-corruption").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
        assert!(matches!(interrupted, Err(HeleosError::FaultInjected)));
        let job_id = FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect running checkpoint")
            .jobs[0]
            .job_id;
        let object_path = fs::canonicalize(vault_root.path())
            .expect("canonicalize vault parent")
            .join("vault")
            .join(Vault::object_key(source_digest));
        drop(store);
        drop(vault);

        match mutation {
            "input" | "checkpoint" | "audit_hash" => {
                let connection = rusqlite::Connection::open(&database_path)
                    .expect("open hostile restart mutator");
                match mutation {
                    "input" => {
                        connection
                            .execute_batch(
                                "PRAGMA ignore_check_constraints = ON;
                                 DROP TRIGGER job_runs_frozen_identity;
                                 DROP TRIGGER job_runs_legal_transition;",
                            )
                            .expect("open input mutation fixture");
                        assert_eq!(
                            connection
                                .execute(
                                    "UPDATE job_runs
                                     SET input_json = replace(
                                         input_json,
                                         '\"parser_version\":\"1.0.0\"',
                                         '\"parser_version\":\"1.0.1\"'
                                     )
                                     WHERE id = ?1",
                                    [job_id.as_uuid().to_string()],
                                )
                                .expect("mutate canonical input"),
                            1
                        );
                    }
                    "checkpoint" => {
                        connection
                            .execute_batch(
                                "PRAGMA ignore_check_constraints = ON;
                                 DROP TRIGGER job_runs_legal_transition;",
                            )
                            .expect("open checkpoint mutation fixture");
                        assert_eq!(
                            connection
                                .execute(
                                    "UPDATE job_runs
                                     SET checkpoint_json = replace(checkpoint_json, ?1, ?2)
                                     WHERE id = ?3",
                                    rusqlite::params![
                                        source_digest.to_string(),
                                        Sha256Digest::from_bytes([9; 32]).to_string(),
                                        job_id.as_uuid().to_string(),
                                    ],
                                )
                                .expect("mutate canonical checkpoint"),
                            1
                        );
                    }
                    "audit_hash" => {
                        connection
                            .execute_batch("DROP TRIGGER audit_events_no_update;")
                            .expect("open audit mutation fixture");
                        assert_eq!(
                            connection
                                .execute(
                                    "UPDATE audit_events
                                     SET event_hash = ?1
                                     WHERE sequence = (SELECT MAX(sequence) FROM audit_events)",
                                    ["0".repeat(64)],
                                )
                                .expect("mutate latest audit hash"),
                            1
                        );
                    }
                    _ => unreachable!(),
                }
            }
            "missing_original" => {
                fs::remove_file(&object_path).expect("remove checkpoint original");
            }
            "corrupt_original" => {
                fs::write(&object_path, vec![0_u8; source_bytes.len()])
                    .expect("replace checkpoint original at same length");
                heleos_core::apply_private_permissions(&object_path)
                    .expect("reharden corrupt checkpoint original");
            }
            "provenance" => {}
            _ => unreachable!(),
        }

        let mut store = Store::open_writer(&database_path).expect("reopen writer");
        let vault = reopened_vault(&vault_root);
        clock.set(NOW_MS + 30_000);
        let mut probe = accepted_probe();
        if mutation == "provenance" {
            probe.provenance.parser_version = "1.0.1".to_owned();
        }
        let resumed = IngestEngine::new(&mut store, &vault, &probe, &clock, &ids)
            .expect("construct restarted engine")
            .resume(job_id, ActorId::from_str("resumer").expect("actor"));
        assert!(matches!(resumed, Err(HeleosError::Integrity)));
        let state: (String, i64) = rusqlite::Connection::open(&database_path)
            .expect("open state observer")
            .query_row(
                "SELECT state, attempt FROM job_runs WHERE id = ?1",
                [job_id.as_uuid().to_string()],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .expect("read post-rejection state");
        if matches!(mutation, "missing_original" | "corrupt_original") {
            assert_eq!(state, ("failed".to_owned(), 2));
        } else {
            assert_eq!(state, ("running".to_owned(), 1));
        }
        if mutation == "missing_original" {
            assert!(!object_path.exists());
        }
        if mutation == "corrupt_original" {
            assert!(matches!(
                vault.open_verified(source_digest),
                Err(HeleosError::Integrity)
            ));
        }
    }
}

#[test]
fn terminal_resume_rejects_missing_or_corrupt_receipt_objects_without_observation_rows() {
    // Break caught: a Succeeded resume appending ingest_replayed before re-verifying every object
    // named by the immutable terminal receipt.
    for accepted in [true, false] {
        let target_count = if accepted { 2 } else { 1 };
        for target_index in 0..target_count {
            for remove_object in [true, false] {
                let database_root = TempDir::new().expect("create database parent");
                heleos_core::apply_private_permissions(database_root.path())
                    .expect("harden database parent");
                let database_path = fs::canonicalize(database_root.path())
                    .expect("canonicalize database parent")
                    .join("foundation.sqlite3");
                let mut store = Store::open_writer(&database_path).expect("open store");
                store.migrate().expect("migrate store");
                let vault_root = TempDir::new().expect("create vault parent");
                let vault = opened_vault(&vault_root);
                let project_id = ProjectId::from_uuid(
                    Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
                );
                let ids = SequenceIds::new(
                    (2_u128..=240)
                        .map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
                );
                ProjectService::new(&mut store, &FixedClock, &ids)
                    .expect("construct project service")
                    .create(ProjectCreateRequest {
                        project_id: Some(project_id),
                        name: "Terminal object authority".to_owned(),
                        actor: ActorId::from_str("creator").expect("actor"),
                        data_class: DataClass::Internal,
                    })
                    .expect("create project");
                let source_root = TempDir::new().expect("create source parent");
                let source_path = source_root.path().join("terminal.pdf");
                let source_bytes = b"%PDF-1.7\nterminal object authority\n%%EOF\n";
                fs::write(&source_path, source_bytes).expect("write source");
                let receipt = if accepted {
                    IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
                        .expect("construct accepted engine")
                        .ingest(IngestRequest {
                            project_id,
                            source: IntakeSource::open(&source_path).expect("open accepted source"),
                            idempotency_key: IdempotencyKey::try_from("terminal-accepted")
                                .expect("key"),
                            actor: ActorId::from_str("tester").expect("actor"),
                            pdf_limits: PdfLimits::default(),
                        })
                        .expect("commit accepted authority")
                } else {
                    IngestEngine::new(
                        &mut store,
                        &vault,
                        &QuarantinedProbe {
                            provenance: accepted_probe().provenance,
                            reason: PdfQuarantineReason::Corrupt,
                        },
                        &FixedClock,
                        &ids,
                    )
                    .expect("construct quarantine engine")
                    .ingest(IngestRequest {
                        project_id,
                        source: IntakeSource::open(&source_path).expect("open quarantine source"),
                        idempotency_key: IdempotencyKey::try_from("terminal-quarantine")
                            .expect("key"),
                        actor: ActorId::from_str("tester").expect("actor"),
                        pdf_limits: PdfLimits::default(),
                    })
                    .expect("commit retained quarantine authority")
                };
                assert_eq!(
                    receipt.outcome,
                    if accepted {
                        IngestOutcome::AcceptedNew
                    } else {
                        IngestOutcome::QuarantinedCorrupt
                    }
                );
                let original = receipt.content_sha256.expect("receipt original");
                let (target_digest, target_length) = if target_index == 0 {
                    (original, receipt.byte_length)
                } else {
                    let evidence = receipt
                        .evidence_manifest
                        .as_ref()
                        .expect("accepted evidence receipt");
                    (
                        evidence.manifest_content_sha256,
                        evidence.manifest_byte_length,
                    )
                };
                let object_path = fs::canonicalize(vault_root.path())
                    .expect("canonicalize vault parent")
                    .join("vault")
                    .join(Vault::object_key(target_digest));
                drop(store);
                drop(vault);
                if remove_object {
                    fs::remove_file(&object_path).expect("remove receipt object");
                } else {
                    fs::write(
                        &object_path,
                        vec![0_u8; usize::try_from(target_length).expect("test object fits usize")],
                    )
                    .expect("replace receipt object at the same length");
                    heleos_core::apply_private_permissions(&object_path)
                        .expect("reharden corrupt receipt object");
                }

                let mut store = Store::open_writer(&database_path).expect("reopen terminal store");
                let vault = reopened_vault(&vault_root);
                let before = FoundationReader::new(&store, &vault)
                    .inspect_foundation(project_id)
                    .expect("inspect terminal database authority");
                let audit_before = FoundationReader::new(&store, &vault)
                    .verify_audit_chain()
                    .expect("verify terminal audit before resume");
                assert!(audit_before.valid);
                let source_records_before = source_record_image(&database_path);
                assert_eq!(source_records_before.len(), 1);
                assert_eq!(
                    source_records_before[0][1],
                    rusqlite::types::Value::Text(project_id.as_uuid().to_string())
                );
                assert_eq!(
                    source_records_before[0][2],
                    rusqlite::types::Value::Text(
                        receipt.authoritative_job_id.as_uuid().to_string()
                    )
                );
                let resumed = IngestEngine::new(
                    &mut store,
                    &vault,
                    &FailingProbe {
                        provenance: accepted_probe().provenance,
                    },
                    &FixedClock,
                    &ids,
                )
                .expect("construct terminal resume engine")
                .resume(
                    receipt.authoritative_job_id,
                    ActorId::from_str("resumer").expect("actor"),
                );
                assert!(matches!(resumed, Err(HeleosError::Integrity)));
                let after = FoundationReader::new(&store, &vault)
                    .inspect_foundation(project_id)
                    .expect("inspect unchanged terminal database authority");
                let audit_after = FoundationReader::new(&store, &vault)
                    .verify_audit_chain()
                    .expect("verify unchanged terminal audit");
                assert_eq!(after, before);
                assert_eq!(audit_after, audit_before);
                assert_eq!(source_record_image(&database_path), source_records_before);
                assert!(
                    after
                        .intake_events
                        .iter()
                        .all(|event| event.outcome != IngestOutcome::IdempotentReplay)
                );
                assert!(matches!(
                    vault.open_verified(target_digest),
                    Err(HeleosError::NotFound | HeleosError::Integrity)
                ));
            }
        }
    }
}

#[test]
fn completed_same_key_replays_and_conflicts_are_durable_without_a_second_job() {
    // Break caught: returning replay/conflict errors before their immutable event and audit commit.
    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let ids = SequenceIds::new(
        (2_u128..=80).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
    );
    ProjectService::new(&mut store, &FixedClock, &ids)
        .expect("construct project service")
        .create(ProjectCreateRequest {
            project_id: Some(project_id),
            name: "Replay project".to_owned(),
            actor: ActorId::from_str("creator").expect("actor"),
            data_class: DataClass::Internal,
        })
        .expect("create project");
    let source_root = TempDir::new().expect("create source directory");
    let first_path = source_root.path().join("first.pdf");
    let replay_path = source_root.path().join("renamed.pdf");
    let conflict_path = source_root.path().join("different.pdf");
    let bytes = b"%PDF-1.7\nreplay fixture\n%%EOF\n";
    fs::write(&first_path, bytes).expect("write first fixture");
    fs::write(&replay_path, bytes).expect("write replay fixture");
    fs::write(&conflict_path, b"%PDF-1.7\nconflict fixture\n%%EOF\n")
        .expect("write conflict fixture");
    let probe = accepted_probe();
    let key = IdempotencyKey::try_from("same-key").expect("key");
    let first = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
        .expect("construct intake engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&first_path).expect("open first source"),
            idempotency_key: key.clone(),
            actor: ActorId::from_str("first-actor").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("first accepted ingest");

    let replay = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
        .expect("construct replay engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&replay_path).expect("open replay source"),
            idempotency_key: key.clone(),
            actor: ActorId::from_str("second-actor").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("completed same-key replay");
    assert_eq!(replay.outcome, IngestOutcome::IdempotentReplay);
    assert_eq!(replay.authoritative_job_id, first.authoritative_job_id);
    assert_eq!(replay.attempt, first.attempt);
    assert_ne!(replay.ingest_event_id, first.ingest_event_id);
    assert_eq!(replay.content_sha256, first.content_sha256);
    assert_eq!(replay.evidence_manifest, first.evidence_manifest);
    assert_eq!(replay.preexisting_vault_digests.len(), 2);

    let conflict = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
        .expect("construct conflict engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&conflict_path).expect("open conflict source"),
            idempotency_key: key,
            actor: ActorId::from_str("third-actor").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
    assert!(matches!(conflict, Err(HeleosError::IdempotencyConflict)));

    let inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect replay/conflict authority");
    assert_eq!(inspection.counts.jobs, 1);
    assert_eq!(inspection.counts.ingest_events, 3);
    assert_eq!(inspection.counts.audit_events, 9);
    assert_eq!(
        inspection.intake_events[0].outcome,
        IngestOutcome::AcceptedNew
    );
    assert_eq!(
        inspection.intake_events[1].outcome,
        IngestOutcome::IdempotentReplay
    );
    assert_eq!(
        inspection.intake_events[2].outcome,
        IngestOutcome::DeniedConflict
    );
}

#[test]
fn input_cap_and_pdf_quarantine_are_finite_succeeded_jobs_and_pdf_admission_is_sticky() {
    // Break caught: handled limits escaping as hard errors or retained quarantine being re-probed.
    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let ids = SequenceIds::new(
        (2_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
    );
    ProjectService::new(&mut store, &FixedClock, &ids)
        .expect("construct project service")
        .create(ProjectCreateRequest {
            project_id: Some(project_id),
            name: "Quarantine project".to_owned(),
            actor: ActorId::from_str("creator").expect("actor"),
            data_class: DataClass::Internal,
        })
        .expect("create project");
    let source_root = TempDir::new().expect("create source directory");
    let oversized_path = source_root.path().join("oversized.pdf");
    let oversized = fs::File::create(&oversized_path).expect("create sparse source");
    oversized
        .set_len(PdfLimits::default().max_input_bytes + 1)
        .expect("size sparse source");
    drop(oversized);
    let probe = QuarantinedProbe {
        provenance: accepted_probe().provenance,
        reason: PdfQuarantineReason::BadMagic,
    };
    let capped = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
        .expect("construct intake engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&oversized_path).expect("open sparse source"),
            idempotency_key: IdempotencyKey::try_from("input-cap").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("input cap is a handled result");
    assert_eq!(capped.outcome, IngestOutcome::QuarantinedLimit);
    assert_eq!(capped.content_sha256, None);
    assert_eq!(capped.byte_length, PdfLimits::default().max_input_bytes + 1);
    assert!(matches!(
        capped.quarantine.as_ref().expect("input quarantine").reason,
        IntakeQuarantineReasonV1::InputBytes { .. }
    ));
    let complete_path = source_root.path().join("complete-after-cap.pdf");
    fs::write(&complete_path, b"%PDF-1.7\ncomplete after cap\n%%EOF\n")
        .expect("write complete same-key source");
    let overcap_conflict = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
        .expect("construct overcap conflict engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&complete_path).expect("open complete source"),
            idempotency_key: IdempotencyKey::try_from("input-cap").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
    assert!(matches!(
        overcap_conflict,
        Err(HeleosError::IdempotencyConflict)
    ));
    let no_probe = FailingProbe {
        provenance: probe.provenance.clone(),
    };

    let pdf_path = source_root.path().join("bad.pdf");
    fs::write(&pdf_path, b"not a PDF").expect("write quarantined source");
    let first = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
        .expect("construct quarantine engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&pdf_path).expect("open quarantined source"),
            idempotency_key: IdempotencyKey::try_from("bad-first").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("PDF quarantine is a handled result");
    assert_eq!(first.outcome, IngestOutcome::QuarantinedCorrupt);
    assert!(matches!(
        first.quarantine.as_ref().expect("PDF quarantine").reason,
        IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic)
    ));
    assert_eq!(first.preexisting_vault_digests, Vec::new());

    let quarantine_replay = IngestEngine::new(&mut store, &vault, &no_probe, &FixedClock, &ids)
        .expect("construct retained-quarantine replay engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&pdf_path).expect("reopen quarantined source"),
            idempotency_key: IdempotencyKey::try_from("bad-first").expect("key"),
            actor: ActorId::from_str("replayer").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("replay retained PDF quarantine");
    assert_eq!(quarantine_replay.outcome, IngestOutcome::IdempotentReplay);
    assert_eq!(quarantine_replay.quarantine, first.quarantine);
    assert_eq!(
        quarantine_replay.preexisting_vault_digests,
        vec![first.content_sha256.expect("retained digest")]
    );

    let sticky = IngestEngine::new(&mut store, &vault, &accepted_probe(), &FixedClock, &ids)
        .expect("construct sticky engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&pdf_path).expect("open sticky source"),
            idempotency_key: IdempotencyKey::try_from("bad-second").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("sticky quarantine result");
    assert_eq!(sticky.outcome, IngestOutcome::QuarantinedCorrupt);
    assert_eq!(sticky.quarantine, first.quarantine);
    assert_eq!(
        sticky.preexisting_vault_digests,
        vec![first.content_sha256.expect("retained digest")]
    );

    let inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect quarantine jobs");
    assert_eq!(inspection.counts.jobs, 3);
    assert_eq!(inspection.counts.ingest_events, 5);
    assert_eq!(inspection.counts.content_objects, 1);
    assert_eq!(inspection.counts.documents, 0);
    assert!(
        inspection
            .jobs
            .iter()
            .all(|job| job.state == heleos_core::JobState::Succeeded)
    );
}

#[test]
fn sticky_admission_requires_canonical_reason_and_exact_probe_identity_before_a_job() {
    // Break caught: trusting noncanonical stored authority or discovering provenance drift after
    // a new job already owns a live lease.
    for accepted_first in [false, true] {
        let database_root = TempDir::new().expect("create database parent");
        heleos_core::apply_private_permissions(database_root.path())
            .expect("harden database parent");
        let database_path = fs::canonicalize(database_root.path())
            .expect("canonicalize database parent")
            .join("foundation.sqlite3");
        let mut store = Store::open_writer(&database_path).expect("open writer");
        store.migrate().expect("migrate store");
        let vault_root = TempDir::new().expect("create vault parent");
        let vault = opened_vault(&vault_root);
        let project_id = ProjectId::from_uuid(
            Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
        );
        let ids = SequenceIds::new(
            (2_u128..=120).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
        );
        ProjectService::new(&mut store, &FixedClock, &ids)
            .expect("construct project service")
            .create(ProjectCreateRequest {
                project_id: Some(project_id),
                name: "Sticky integrity".to_owned(),
                actor: ActorId::from_str("creator").expect("actor"),
                data_class: DataClass::Internal,
            })
            .expect("create project");
        let source_root = TempDir::new().expect("create source directory");
        let source_path = source_root.path().join("sticky.pdf");
        fs::write(&source_path, b"%PDF-1.7\nsticky authority\n%%EOF\n").expect("write source");
        let initial_probe = accepted_probe();
        if accepted_first {
            IngestEngine::new(&mut store, &vault, &initial_probe, &FixedClock, &ids)
                .expect("construct accepted engine")
                .ingest(IngestRequest {
                    project_id,
                    source: IntakeSource::open(&source_path).expect("open source"),
                    idempotency_key: IdempotencyKey::try_from("first-accepted").expect("key"),
                    actor: ActorId::from_str("tester").expect("actor"),
                    pdf_limits: PdfLimits::default(),
                })
                .expect("commit accepted authority");
        } else {
            let quarantine_probe = QuarantinedProbe {
                provenance: initial_probe.provenance.clone(),
                reason: PdfQuarantineReason::BadMagic,
            };
            IngestEngine::new(&mut store, &vault, &quarantine_probe, &FixedClock, &ids)
                .expect("construct quarantine engine")
                .ingest(IngestRequest {
                    project_id,
                    source: IntakeSource::open(&source_path).expect("open source"),
                    idempotency_key: IdempotencyKey::try_from("first-quarantine").expect("key"),
                    actor: ActorId::from_str("tester").expect("actor"),
                    pdf_limits: PdfLimits::default(),
                })
                .expect("commit quarantine authority");
        }

        let mut changed_probe = accepted_probe();
        changed_probe.provenance.parser_version = "1.0.1".to_owned();
        let drift = IngestEngine::new(&mut store, &vault, &changed_probe, &FixedClock, &ids)
            .expect("construct drift engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("open drift source"),
                idempotency_key: IdempotencyKey::try_from("drift").expect("key"),
                actor: ActorId::from_str("tester").expect("actor"),
                pdf_limits: PdfLimits::default(),
            });
        assert!(matches!(drift, Err(HeleosError::Integrity)));
        assert_eq!(
            FoundationReader::new(&store, &vault)
                .inspect_foundation(project_id)
                .expect("inspect after provenance rejection")
                .counts
                .jobs,
            1
        );

        if !accepted_first {
            drop(store);
            let connection = rusqlite::Connection::open(&database_path)
                .expect("open hostile quarantine mutator");
            connection
                .execute_batch(
                    "PRAGMA ignore_check_constraints = ON;
                     DROP TRIGGER content_objects_no_update;",
                )
                .expect("authorize hostile quarantine mutation");
            let canonical: String = connection
                .query_row("SELECT quarantine_reason FROM content_objects", [], |row| {
                    row.get(0)
                })
                .expect("read canonical quarantine");
            connection
                .execute(
                    "UPDATE content_objects SET quarantine_reason = ?1",
                    [format!(" {canonical}")],
                )
                .expect("store noncanonical quarantine bytes");
            drop(connection);
            let mut reopened = Store::open_writer(&database_path).expect("reopen writer");
            let noncanonical =
                IngestEngine::new(&mut reopened, &vault, &initial_probe, &FixedClock, &ids)
                    .expect("construct noncanonical authority engine")
                    .ingest(IngestRequest {
                        project_id,
                        source: IntakeSource::open(&source_path).expect("open source"),
                        idempotency_key: IdempotencyKey::try_from("noncanonical").expect("key"),
                        actor: ActorId::from_str("tester").expect("actor"),
                        pdf_limits: PdfLimits::default(),
                    });
            assert!(matches!(noncanonical, Err(HeleosError::Integrity)));
        }
    }
}

#[test]
#[cfg(unix)]
fn no_publish_replay_rehashes_the_same_retained_source_before_authority() {
    // Break caught: replay/conflict and sticky branches relying on a stale first-pass digest.
    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let ids = SequenceIds::new(
        (2_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
    );
    ProjectService::new(&mut store, &FixedClock, &ids)
        .expect("construct project service")
        .create(ProjectCreateRequest {
            project_id: Some(project_id),
            name: "Mutation project".to_owned(),
            actor: ActorId::from_str("creator").expect("actor"),
            data_class: DataClass::Internal,
        })
        .expect("create project");
    let source_root = TempDir::new().expect("create source directory");
    let source_path = source_root.path().join("same.pdf");
    let original = b"%PDF-1.7\nsource alpha\n%%EOF\n";
    let replacement = b"%PDF-1.7\nsource omega\n%%EOF\n";
    assert_eq!(original.len(), replacement.len());
    fs::write(&source_path, original).expect("write source");
    let probe = accepted_probe();
    let key = IdempotencyKey::try_from("mutating-replay").expect("key");
    IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
        .expect("construct first engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open first source"),
            idempotency_key: key.clone(),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("commit first authority");

    let writer = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&source_path)
        .expect("open retained source mutator");
    let source = IntakeSource::open(&source_path).expect("open retained replay source");
    let clock = MutatingClock::new(writer, replacement);
    let replay = IngestEngine::new(&mut store, &vault, &probe, &clock, &ids)
        .expect("construct replay engine")
        .ingest(IngestRequest {
            project_id,
            source,
            idempotency_key: key,
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
    assert!(matches!(replay, Err(HeleosError::Integrity)));
    let inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect unchanged authority");
    assert_eq!(inspection.counts.jobs, 1);
    assert_eq!(inspection.counts.ingest_events, 1);
}

#[test]
fn every_in_process_fault_boundary_resumes_without_duplicate_authority() {
    // Break caught: process-loss boundaries either becoming Failed or duplicating authority.
    for point in [
        FaultPoint::AfterVaultPublish,
        FaultPoint::AfterJobStart,
        FaultPoint::AfterCheckpointCommit,
        FaultPoint::BeforeAuthoritativeCommit,
        FaultPoint::AfterAuthoritativeCommit,
    ] {
        let database_root = TempDir::new().expect("create database parent");
        heleos_core::apply_private_permissions(database_root.path())
            .expect("harden database parent");
        let database_path = fs::canonicalize(database_root.path())
            .expect("canonicalize database parent")
            .join("foundation.sqlite3");
        let mut store = Store::open_writer(&database_path).expect("open store");
        store.migrate().expect("migrate store");
        let vault_root = TempDir::new().expect("create vault parent");
        let vault = opened_vault(&vault_root);
        let project_id = ProjectId::from_uuid(
            Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
        );
        let ids = SequenceIds::new(
            (2_u128..=200).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
        );
        let clock = ManualClock::new(NOW_MS);
        ProjectService::new(&mut store, &clock, &ids)
            .expect("construct project service")
            .create(ProjectCreateRequest {
                project_id: Some(project_id),
                name: "Fault matrix".to_owned(),
                actor: ActorId::from_str("creator").expect("actor"),
                data_class: DataClass::Internal,
            })
            .expect("create project");
        let source_root = TempDir::new().expect("create source directory");
        let source_path = source_root.path().join("fault.pdf");
        let source_bytes = b"%PDF-1.7\nfault fixture\n%%EOF\n";
        fs::write(&source_path, source_bytes).expect("write source");
        let original_digest =
            Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash fault original");
        let (manifest_digest, _) = accepted_manifest_bytes(source_bytes);
        let probe = accepted_probe();
        let fault = FailAt(point);
        let interrupted =
            IngestEngine::with_fault_injector(&mut store, &vault, &probe, &clock, &ids, &fault)
                .expect("construct fault engine")
                .ingest(IngestRequest {
                    project_id,
                    source: IntakeSource::open(&source_path).expect("open retained source"),
                    idempotency_key: IdempotencyKey::try_from("fault-key").expect("key"),
                    actor: ActorId::from_str("tester").expect("actor"),
                    pdf_limits: PdfLimits::default(),
                });
        assert!(matches!(interrupted, Err(HeleosError::FaultInjected)));
        drop(store);
        drop(vault);
        let mut store = Store::open_writer(&database_path).expect("reopen store after fault");
        let vault = reopened_vault(&vault_root);
        let before = FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect fault checkpoint");
        assert_eq!(before.jobs.len(), 1);
        let job = before.jobs[0].clone();
        let expected_before_state = if point == FaultPoint::AfterVaultPublish {
            heleos_core::JobState::Queued
        } else if point == FaultPoint::AfterAuthoritativeCommit {
            heleos_core::JobState::Succeeded
        } else {
            heleos_core::JobState::Running
        };
        let expected_before_attempt = if point == FaultPoint::AfterVaultPublish {
            0
        } else {
            1
        };
        assert_eq!(job.state, expected_before_state);
        assert_eq!(job.attempt, expected_before_attempt);
        assert_eq!(
            job_image_vector(&before),
            vec![job_image(
                job.job_id,
                expected_before_state,
                expected_before_attempt,
                (point == FaultPoint::AfterAuthoritativeCommit)
                    .then_some(FoundationJobTerminalReason::Completed),
            )]
        );
        let expected_before_content = if point == FaultPoint::AfterAuthoritativeCommit {
            2
        } else {
            0
        };
        assert_eq!(before.counts.content_objects, expected_before_content);
        assert!(vault.open_verified(original_digest).is_ok());
        if matches!(
            point,
            FaultPoint::AfterCheckpointCommit
                | FaultPoint::BeforeAuthoritativeCommit
                | FaultPoint::AfterAuthoritativeCommit
        ) {
            assert!(vault.open_verified(manifest_digest).is_ok());
        } else {
            assert!(matches!(
                vault.open_verified(manifest_digest),
                Err(HeleosError::NotFound)
            ));
        }
        let connection = rusqlite::Connection::open(&database_path)
            .expect("open fault audit observation connection");
        let stored_terminal_receipt = if point == FaultPoint::AfterAuthoritativeCommit {
            let checkpoint_json: String = connection
                .query_row(
                    "SELECT checkpoint_json FROM job_runs WHERE id = ?1",
                    [job.job_id.as_uuid().to_string()],
                    |row| row.get(0),
                )
                .expect("read stored terminal checkpoint");
            let checkpoint: serde_json::Value =
                serde_json::from_str(&checkpoint_json).expect("parse terminal checkpoint");
            Some(
                serde_json::from_value::<IngestReceipt>(checkpoint["detail"]["receipt"].clone())
                    .expect("parse stored terminal receipt"),
            )
        } else {
            None
        };
        drop(connection);
        let expected_before_events = stored_terminal_receipt
            .as_ref()
            .map(|receipt| {
                vec![ExpectedIntakeEvent {
                    event_id: receipt.ingest_event_id,
                    job_id: Some(job.job_id),
                    outcome: IngestOutcome::AcceptedNew,
                    attempt: Some(1),
                }]
            })
            .unwrap_or_default();
        assert_eq!(intake_event_vector(&before), expected_before_events);
        let expected_before_audits = match point {
            FaultPoint::AfterVaultPublish => vec![
                audit_identity("project_created", "project", project_id.as_uuid()),
                audit_identity("job_created", "job", job.job_id.as_uuid()),
            ],
            FaultPoint::AfterJobStart => vec![
                audit_identity("project_created", "project", project_id.as_uuid()),
                audit_identity("job_created", "job", job.job_id.as_uuid()),
                audit_identity("job_started", "job", job.job_id.as_uuid()),
            ],
            FaultPoint::AfterCheckpointCommit | FaultPoint::BeforeAuthoritativeCommit => vec![
                audit_identity("project_created", "project", project_id.as_uuid()),
                audit_identity("job_created", "job", job.job_id.as_uuid()),
                audit_identity("job_started", "job", job.job_id.as_uuid()),
                audit_identity("job_checkpointed", "job", job.job_id.as_uuid()),
            ],
            FaultPoint::AfterAuthoritativeCommit => {
                let receipt = stored_terminal_receipt
                    .as_ref()
                    .expect("terminal receipt for exact audit vector");
                let evidence_id = before
                    .evidence
                    .first()
                    .expect("terminal evidence authority")
                    .evidence_id;
                vec![
                    audit_identity("project_created", "project", project_id.as_uuid()),
                    audit_identity("job_created", "job", job.job_id.as_uuid()),
                    audit_identity("job_started", "job", job.job_id.as_uuid()),
                    audit_identity("job_checkpointed", "job", job.job_id.as_uuid()),
                    audit_identity("job_succeeded", "job", job.job_id.as_uuid()),
                    audit_identity(
                        "ingest_accepted",
                        "ingest_attempt",
                        receipt.ingest_event_id.as_uuid(),
                    ),
                    audit_identity("evidence_created", "evidence", evidence_id.as_uuid()),
                ]
            }
        };
        assert_eq!(
            audit_identity_vector(&database_path),
            expected_before_audits
        );
        if matches!(
            point,
            FaultPoint::AfterJobStart
                | FaultPoint::AfterCheckpointCommit
                | FaultPoint::BeforeAuthoritativeCommit
        ) {
            clock.set(NOW_MS + 30_000);
        }
        let resumed = IngestEngine::new(&mut store, &vault, &probe, &clock, &ids)
            .expect("construct resume engine")
            .resume(job.job_id, ActorId::from_str("resumer").expect("actor"))
            .expect("resume durable checkpoint");
        match point {
            FaultPoint::AfterVaultPublish => {
                assert_eq!(resumed.resumed_attempt, Some(1));
                assert_eq!(resumed.interrupted_event_id, None);
            }
            FaultPoint::AfterJobStart
            | FaultPoint::AfterCheckpointCommit
            | FaultPoint::BeforeAuthoritativeCommit => {
                assert_eq!(resumed.resumed_attempt, Some(2));
                assert!(resumed.interrupted_event_id.is_some());
            }
            FaultPoint::AfterAuthoritativeCommit => {
                assert_eq!(resumed.resumed_attempt, None);
                assert_eq!(resumed.interrupted_event_id, None);
            }
        }
        assert_eq!(resumed.receipt.outcome, IngestOutcome::AcceptedNew);
        let expected_attempt = match point {
            FaultPoint::AfterVaultPublish | FaultPoint::AfterAuthoritativeCommit => 1,
            FaultPoint::AfterJobStart
            | FaultPoint::AfterCheckpointCommit
            | FaultPoint::BeforeAuthoritativeCommit => 2,
        };
        assert_eq!(resumed.receipt.attempt, expected_attempt);
        let mut original_and_manifest = vec![original_digest, manifest_digest];
        original_and_manifest.sort_unstable();
        let expected_preexisting = match point {
            FaultPoint::AfterVaultPublish | FaultPoint::AfterJobStart => vec![original_digest],
            FaultPoint::AfterCheckpointCommit | FaultPoint::BeforeAuthoritativeCommit => {
                original_and_manifest
            }
            FaultPoint::AfterAuthoritativeCommit => Vec::new(),
        };
        assert_eq!(
            resumed.receipt.preexisting_vault_digests,
            expected_preexisting
        );
        if let Some(stored) = stored_terminal_receipt {
            assert_eq!(resumed.receipt, stored);
            assert_eq!(
                canonical_json(&resumed.receipt).expect("canonical resumed terminal receipt"),
                canonical_json(&stored).expect("canonical stored terminal receipt")
            );
        }
        assert_receipt_matches_terminal_checkpoint(
            &database_path,
            &resumed.receipt,
            expected_attempt,
        );
        let after = FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect resumed authority");
        assert_eq!(after.counts.jobs, 1);
        assert_eq!(after.counts.documents, 1);
        assert_eq!(after.counts.revisions, 1);
        assert_eq!(after.counts.evidence_objects, 1);
        assert_eq!(after.counts.content_objects, 2);
        assert_eq!(after.jobs[0].attempt, expected_attempt);
        assert_eq!(
            job_image_vector(&after),
            vec![job_image(
                job.job_id,
                JobState::Succeeded,
                expected_attempt,
                Some(FoundationJobTerminalReason::Completed),
            )]
        );
        assert_accepted_receipt_authority(
            &resumed.receipt,
            source_bytes,
            project_id,
            job.job_id,
            &after,
        );
        let mut expected_after_events = Vec::new();
        if let Some(interrupted_event_id) = resumed.interrupted_event_id {
            expected_after_events.push(ExpectedIntakeEvent {
                event_id: interrupted_event_id,
                job_id: Some(job.job_id),
                outcome: IngestOutcome::Interrupted,
                attempt: Some(1),
            });
        }
        expected_after_events.push(ExpectedIntakeEvent {
            event_id: resumed.receipt.ingest_event_id,
            job_id: Some(job.job_id),
            outcome: IngestOutcome::AcceptedNew,
            attempt: Some(expected_attempt),
        });
        assert_eq!(intake_event_vector(&after), expected_after_events);
        let evidence_id = after
            .evidence
            .first()
            .expect("accepted evidence authority")
            .evidence_id;
        let expected_after_audits = match point {
            FaultPoint::AfterVaultPublish => vec![
                audit_identity("project_created", "project", project_id.as_uuid()),
                audit_identity("job_created", "job", job.job_id.as_uuid()),
                audit_identity("job_started", "job", job.job_id.as_uuid()),
                audit_identity("job_checkpointed", "job", job.job_id.as_uuid()),
                audit_identity("job_succeeded", "job", job.job_id.as_uuid()),
                audit_identity(
                    "ingest_accepted",
                    "ingest_attempt",
                    resumed.receipt.ingest_event_id.as_uuid(),
                ),
                audit_identity("evidence_created", "evidence", evidence_id.as_uuid()),
            ],
            FaultPoint::AfterJobStart => vec![
                audit_identity("project_created", "project", project_id.as_uuid()),
                audit_identity("job_created", "job", job.job_id.as_uuid()),
                audit_identity("job_started", "job", job.job_id.as_uuid()),
                audit_identity("job_interrupted", "job", job.job_id.as_uuid()),
                audit_identity("job_resumed", "job", job.job_id.as_uuid()),
                audit_identity("job_checkpointed", "job", job.job_id.as_uuid()),
                audit_identity("job_succeeded", "job", job.job_id.as_uuid()),
                audit_identity(
                    "ingest_accepted",
                    "ingest_attempt",
                    resumed.receipt.ingest_event_id.as_uuid(),
                ),
                audit_identity("evidence_created", "evidence", evidence_id.as_uuid()),
            ],
            FaultPoint::AfterCheckpointCommit | FaultPoint::BeforeAuthoritativeCommit => vec![
                audit_identity("project_created", "project", project_id.as_uuid()),
                audit_identity("job_created", "job", job.job_id.as_uuid()),
                audit_identity("job_started", "job", job.job_id.as_uuid()),
                audit_identity("job_checkpointed", "job", job.job_id.as_uuid()),
                audit_identity("job_interrupted", "job", job.job_id.as_uuid()),
                audit_identity("job_resumed", "job", job.job_id.as_uuid()),
                audit_identity("job_succeeded", "job", job.job_id.as_uuid()),
                audit_identity(
                    "ingest_accepted",
                    "ingest_attempt",
                    resumed.receipt.ingest_event_id.as_uuid(),
                ),
                audit_identity("evidence_created", "evidence", evidence_id.as_uuid()),
            ],
            FaultPoint::AfterAuthoritativeCommit => vec![
                audit_identity("project_created", "project", project_id.as_uuid()),
                audit_identity("job_created", "job", job.job_id.as_uuid()),
                audit_identity("job_started", "job", job.job_id.as_uuid()),
                audit_identity("job_checkpointed", "job", job.job_id.as_uuid()),
                audit_identity("job_succeeded", "job", job.job_id.as_uuid()),
                audit_identity(
                    "ingest_accepted",
                    "ingest_attempt",
                    resumed.receipt.ingest_event_id.as_uuid(),
                ),
                audit_identity("evidence_created", "evidence", evidence_id.as_uuid()),
                audit_identity(
                    "ingest_replayed",
                    "ingest_attempt",
                    resumed.receipt.ingest_event_id.as_uuid(),
                ),
            ],
        };
        assert_eq!(audit_identity_vector(&database_path), expected_after_audits);
        let audit = IngestEngine::new(&mut store, &vault, &probe, &clock, &ids)
            .expect("construct final audit engine")
            .verify_audit_chain()
            .expect("verify final fault audit chain");
        assert!(audit.valid);
        assert!(audit.findings.is_empty());
    }
}

#[test]
fn sticky_fault_checkpoints_resume_without_reprobing_admitted_content() {
    // Break caught: a sticky job checkpointed as ambiguous VaultPublished and reprobed on resume.
    for accepted in [true, false] {
        for point in [
            FaultPoint::AfterVaultPublish,
            FaultPoint::AfterJobStart,
            FaultPoint::AfterCheckpointCommit,
            FaultPoint::BeforeAuthoritativeCommit,
            FaultPoint::AfterAuthoritativeCommit,
        ] {
            let database_root = TempDir::new().expect("create database parent");
            heleos_core::apply_private_permissions(database_root.path())
                .expect("harden database parent");
            let database_path = fs::canonicalize(database_root.path())
                .expect("canonicalize database parent")
                .join("foundation.sqlite3");
            let mut store = Store::open_writer(&database_path).expect("open store");
            store.migrate().expect("migrate store");
            let vault_root = TempDir::new().expect("create vault parent");
            let vault = opened_vault(&vault_root);
            let project_id = ProjectId::from_uuid(
                Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
            );
            let ids = SequenceIds::new(
                (2_u128..=400)
                    .map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
            );
            let clock = ManualClock::new(NOW_MS);
            ProjectService::new(&mut store, &clock, &ids)
                .expect("construct project service")
                .create(ProjectCreateRequest {
                    project_id: Some(project_id),
                    name: "Sticky recovery".to_owned(),
                    actor: ActorId::from_str("creator").expect("actor"),
                    data_class: DataClass::Internal,
                })
                .expect("create project");
            let source_root = TempDir::new().expect("create source directory");
            let source_path = source_root.path().join("sticky.pdf");
            let source_bytes = b"%PDF-1.7\nsticky recovery\n%%EOF\n";
            fs::write(&source_path, source_bytes).expect("write source");
            let provenance = accepted_probe().provenance;
            let first = if accepted {
                IngestEngine::new(
                    &mut store,
                    &vault,
                    &AcceptedProbe {
                        provenance: provenance.clone(),
                    },
                    &clock,
                    &ids,
                )
                .expect("construct accepted engine")
                .ingest(IngestRequest {
                    project_id,
                    source: IntakeSource::open(&source_path).expect("open source"),
                    idempotency_key: IdempotencyKey::try_from("first-authority").expect("key"),
                    actor: ActorId::from_str("tester").expect("actor"),
                    pdf_limits: PdfLimits::default(),
                })
            } else {
                IngestEngine::new(
                    &mut store,
                    &vault,
                    &QuarantinedProbe {
                        provenance: provenance.clone(),
                        reason: PdfQuarantineReason::Corrupt,
                    },
                    &clock,
                    &ids,
                )
                .expect("construct quarantine engine")
                .ingest(IngestRequest {
                    project_id,
                    source: IntakeSource::open(&source_path).expect("open source"),
                    idempotency_key: IdempotencyKey::try_from("first-authority").expect("key"),
                    actor: ActorId::from_str("tester").expect("actor"),
                    pdf_limits: PdfLimits::default(),
                })
            }
            .expect("establish sticky authority");
            let first_job_id = first.authoritative_job_id;
            let no_probe = FailingProbe {
                provenance: provenance.clone(),
            };
            let interrupted = IngestEngine::with_fault_injector(
                &mut store,
                &vault,
                &no_probe,
                &clock,
                &ids,
                &FailAt(point),
            )
            .expect("construct sticky fault engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("open sticky source"),
                idempotency_key: IdempotencyKey::try_from("second-key").expect("key"),
                actor: ActorId::from_str("tester").expect("actor"),
                pdf_limits: PdfLimits::default(),
            });
            assert!(matches!(interrupted, Err(HeleosError::FaultInjected)));
            drop(store);
            drop(vault);
            let mut store = Store::open_writer(&database_path).expect("reopen sticky store");
            let vault = reopened_vault(&vault_root);
            let inspection = FoundationReader::new(&store, &vault)
                .inspect_foundation(project_id)
                .expect("inspect sticky checkpoint");
            let job_id = inspection
                .jobs
                .iter()
                .find(|job| job.job_id != first_job_id)
                .expect("second sticky job")
                .job_id;
            let second = inspection
                .jobs
                .iter()
                .find(|job| job.job_id == job_id)
                .expect("second sticky job image");
            assert_eq!(inspection.jobs.len(), 2);
            assert_eq!(
                second.state,
                match point {
                    FaultPoint::AfterVaultPublish => heleos_core::JobState::Queued,
                    FaultPoint::AfterAuthoritativeCommit => heleos_core::JobState::Succeeded,
                    _ => heleos_core::JobState::Running,
                }
            );
            assert_eq!(
                second.attempt,
                if point == FaultPoint::AfterVaultPublish {
                    0
                } else {
                    1
                }
            );
            let second_before_state = match point {
                FaultPoint::AfterVaultPublish => JobState::Queued,
                FaultPoint::AfterAuthoritativeCommit => JobState::Succeeded,
                _ => JobState::Running,
            };
            let second_before_attempt = if point == FaultPoint::AfterVaultPublish {
                0
            } else {
                1
            };
            assert_eq!(
                job_image_vector(&inspection),
                vec![
                    job_image(
                        first_job_id,
                        JobState::Succeeded,
                        first.attempt,
                        Some(FoundationJobTerminalReason::Completed),
                    ),
                    job_image(
                        job_id,
                        second_before_state,
                        second_before_attempt,
                        (point == FaultPoint::AfterAuthoritativeCommit)
                            .then_some(FoundationJobTerminalReason::Completed),
                    ),
                ]
            );
            let second_terminal_before = (point == FaultPoint::AfterAuthoritativeCommit)
                .then(|| terminal_receipt_from_database(&database_path, job_id));
            let mut expected_before_events = vec![ExpectedIntakeEvent {
                event_id: first.ingest_event_id,
                job_id: Some(first_job_id),
                outcome: first.outcome,
                attempt: Some(first.attempt),
            }];
            if let Some(receipt) = &second_terminal_before {
                expected_before_events.push(ExpectedIntakeEvent {
                    event_id: receipt.ingest_event_id,
                    job_id: Some(job_id),
                    outcome: receipt.outcome,
                    attempt: Some(receipt.attempt),
                });
            }
            assert_eq!(intake_event_vector(&inspection), expected_before_events);
            let mut expected_before_audits =
                completed_job_audit_identities(project_id, &first, true, accepted);
            expected_before_audits.push(audit_identity("job_created", "job", job_id.as_uuid()));
            if point != FaultPoint::AfterVaultPublish {
                expected_before_audits.push(audit_identity("job_started", "job", job_id.as_uuid()));
            }
            if matches!(
                point,
                FaultPoint::AfterCheckpointCommit
                    | FaultPoint::BeforeAuthoritativeCommit
                    | FaultPoint::AfterAuthoritativeCommit
            ) {
                expected_before_audits.push(audit_identity(
                    "job_checkpointed",
                    "job",
                    job_id.as_uuid(),
                ));
            }
            if let Some(receipt) = &second_terminal_before {
                expected_before_audits.push(audit_identity(
                    "job_succeeded",
                    "job",
                    job_id.as_uuid(),
                ));
                expected_before_audits.push(audit_identity(
                    if accepted {
                        "ingest_accepted"
                    } else {
                        "ingest_quarantined"
                    },
                    "ingest_attempt",
                    receipt.ingest_event_id.as_uuid(),
                ));
            }
            assert_eq!(
                audit_identity_vector(&database_path),
                expected_before_audits
            );
            if matches!(
                point,
                FaultPoint::AfterJobStart
                    | FaultPoint::AfterCheckpointCommit
                    | FaultPoint::BeforeAuthoritativeCommit
            ) {
                clock.set(NOW_MS + 30_000);
            }
            let resumed = IngestEngine::new(&mut store, &vault, &no_probe, &clock, &ids)
                .expect("construct sticky resume engine")
                .resume(job_id, ActorId::from_str("resumer").expect("actor"))
                .expect("resume typed sticky checkpoint");
            assert_eq!(
                resumed.resumed_attempt,
                match point {
                    FaultPoint::AfterVaultPublish => Some(1),
                    FaultPoint::AfterJobStart
                    | FaultPoint::AfterCheckpointCommit
                    | FaultPoint::BeforeAuthoritativeCommit => Some(2),
                    FaultPoint::AfterAuthoritativeCommit => None,
                }
            );
            assert_eq!(
                resumed.interrupted_event_id.is_some(),
                matches!(
                    point,
                    FaultPoint::AfterJobStart
                        | FaultPoint::AfterCheckpointCommit
                        | FaultPoint::BeforeAuthoritativeCommit
                )
            );
            let expected_attempt = match point {
                FaultPoint::AfterVaultPublish | FaultPoint::AfterAuthoritativeCommit => 1,
                FaultPoint::AfterJobStart
                | FaultPoint::AfterCheckpointCommit
                | FaultPoint::BeforeAuthoritativeCommit => 2,
            };
            assert_eq!(resumed.receipt.attempt, expected_attempt);
            if let Some(stored) = &second_terminal_before {
                assert_eq!(&resumed.receipt, stored);
                assert_eq!(
                    canonical_json(&resumed.receipt).expect("canonical resumed receipt"),
                    canonical_json(stored).expect("canonical stored receipt")
                );
            }
            assert_receipt_matches_terminal_checkpoint(
                &database_path,
                &resumed.receipt,
                expected_attempt,
            );
            if accepted {
                assert_eq!(resumed.receipt.outcome, IngestOutcome::AcceptedDuplicate);
                let evidence = first.evidence_manifest.as_ref().expect("accepted evidence");
                let mut expected_preexisting = vec![
                    first.content_sha256.expect("original digest"),
                    evidence.manifest_content_sha256,
                ];
                expected_preexisting.sort_unstable();
                assert_eq!(
                    resumed.receipt.preexisting_vault_digests,
                    expected_preexisting
                );
            } else {
                assert_eq!(resumed.receipt.outcome, IngestOutcome::QuarantinedCorrupt);
                assert_eq!(
                    resumed.receipt.preexisting_vault_digests,
                    vec![first.content_sha256.expect("quarantined digest")]
                );
            }
            let after = FoundationReader::new(&store, &vault)
                .inspect_foundation(project_id)
                .expect("inspect resumed sticky authority");
            assert_eq!(after.jobs.len(), 2);
            assert_eq!(
                job_image_vector(&after),
                vec![
                    job_image(
                        first_job_id,
                        JobState::Succeeded,
                        first.attempt,
                        Some(FoundationJobTerminalReason::Completed),
                    ),
                    job_image(
                        job_id,
                        JobState::Succeeded,
                        expected_attempt,
                        Some(FoundationJobTerminalReason::Completed),
                    ),
                ]
            );
            if accepted {
                assert_eq!(after.counts.documents, 1);
                assert_eq!(after.counts.revisions, 1);
                assert_eq!(after.counts.evidence_objects, 1);
                assert_eq!(after.evidence.len(), 1);
                assert_eq!(after.evidence[0].originating_job_id, first_job_id);
                let reused_evidence = resumed
                    .receipt
                    .evidence_manifest
                    .as_ref()
                    .expect("reused accepted evidence");
                assert_eq!(reused_evidence.lineages.len(), 1);
                assert_eq!(
                    reused_evidence.lineages[0].evidence_id,
                    after.evidence[0].evidence_id
                );
                assert_eq!(reused_evidence.lineages[0].originating_job_id, first_job_id);
                assert_eq!(resumed.receipt.content_sha256, first.content_sha256);
                assert_eq!(resumed.receipt.byte_length, first.byte_length);
                assert_eq!(resumed.receipt.quarantine, first.quarantine);
                assert_eq!(resumed.receipt.document_id, first.document_id);
                assert_eq!(resumed.receipt.revision_id, first.revision_id);
                assert_eq!(resumed.receipt.sheet_ids, first.sheet_ids);
                assert_eq!(resumed.receipt.evidence_manifest, first.evidence_manifest);
                assert_accepted_receipt_authority(
                    &resumed.receipt,
                    source_bytes,
                    project_id,
                    first_job_id,
                    &after,
                );
            } else {
                assert_eq!(resumed.receipt.content_sha256, first.content_sha256);
                assert_eq!(resumed.receipt.byte_length, first.byte_length);
                assert_eq!(resumed.receipt.quarantine, first.quarantine);
                assert_eq!(resumed.receipt.document_id, first.document_id);
                assert_eq!(resumed.receipt.revision_id, first.revision_id);
                assert_eq!(resumed.receipt.sheet_ids, first.sheet_ids);
                assert_eq!(resumed.receipt.evidence_manifest, first.evidence_manifest);
                assert_quarantine_receipt_authority(
                    &resumed.receipt,
                    source_bytes,
                    IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::Corrupt),
                    &provenance,
                    &after,
                );
            }
            let mut expected_after_events = vec![ExpectedIntakeEvent {
                event_id: first.ingest_event_id,
                job_id: Some(first_job_id),
                outcome: first.outcome,
                attempt: Some(first.attempt),
            }];
            if let Some(interrupted_event_id) = resumed.interrupted_event_id {
                expected_after_events.push(ExpectedIntakeEvent {
                    event_id: interrupted_event_id,
                    job_id: Some(job_id),
                    outcome: IngestOutcome::Interrupted,
                    attempt: Some(1),
                });
            }
            expected_after_events.push(ExpectedIntakeEvent {
                event_id: resumed.receipt.ingest_event_id,
                job_id: Some(job_id),
                outcome: resumed.receipt.outcome,
                attempt: Some(expected_attempt),
            });
            assert_eq!(intake_event_vector(&after), expected_after_events);
            let mut expected_after_audits =
                completed_job_audit_identities(project_id, &first, true, accepted);
            expected_after_audits.extend(fault_job_final_audit_identities(
                point,
                &resumed.receipt,
                false,
            ));
            assert_eq!(audit_identity_vector(&database_path), expected_after_audits);
            let audit = IngestEngine::new(&mut store, &vault, &no_probe, &clock, &ids)
                .expect("construct sticky audit engine")
                .verify_audit_chain()
                .expect("verify sticky audit chain");
            assert!(audit.valid);
            assert!(audit.findings.is_empty());
        }
    }
}

#[test]
fn queued_same_digest_job_yields_to_later_sticky_authority_without_reprobing() {
    // Break caught: resuming a VaultPublished job against the candidate it froze before another
    // key established the digest's immutable accepted or quarantined authority.
    for accepted_authority in [true, false] {
        let database_root = TempDir::new().expect("create database parent");
        heleos_core::apply_private_permissions(database_root.path())
            .expect("harden database parent");
        let database_path = fs::canonicalize(database_root.path())
            .expect("canonicalize database parent")
            .join("foundation.sqlite3");
        let mut store = Store::open_writer(&database_path).expect("open store");
        store.migrate().expect("migrate store");
        let vault_root = TempDir::new().expect("create vault parent");
        let vault = opened_vault(&vault_root);
        let project_id = ProjectId::from_uuid(
            Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
        );
        let ids = SequenceIds::new(
            (2_u128..=400).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
        );
        ProjectService::new(&mut store, &FixedClock, &ids)
            .expect("construct project service")
            .create(ProjectCreateRequest {
                project_id: Some(project_id),
                name: "Competing sticky authority".to_owned(),
                actor: ActorId::from_str("creator").expect("actor"),
                data_class: DataClass::Internal,
            })
            .expect("create project");
        let source_root = TempDir::new().expect("create source directory");
        let source_path = source_root.path().join("competing.pdf");
        fs::write(
            &source_path,
            b"%PDF-1.7\ncompeting sticky authority\n%%EOF\n",
        )
        .expect("write source");
        let provenance = accepted_probe().provenance;

        let first = IngestEngine::with_fault_injector(
            &mut store,
            &vault,
            &AcceptedProbe {
                provenance: provenance.clone(),
            },
            &FixedClock,
            &ids,
            &FailAt(FaultPoint::AfterVaultPublish),
        )
        .expect("construct first faulting engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open first source"),
            idempotency_key: IdempotencyKey::try_from("first-pending-key").expect("key"),
            actor: ActorId::from_str("first").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
        assert!(matches!(first, Err(HeleosError::FaultInjected)));
        let before_authority = FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect queued first job");
        assert_eq!(before_authority.jobs.len(), 1);
        assert_eq!(
            before_authority.jobs[0].state,
            heleos_core::JobState::Queued
        );
        assert_eq!(before_authority.jobs[0].attempt, 0);
        let first_job_id = before_authority.jobs[0].job_id;

        let authority = if accepted_authority {
            IngestEngine::new(
                &mut store,
                &vault,
                &AcceptedProbe {
                    provenance: provenance.clone(),
                },
                &FixedClock,
                &ids,
            )
            .expect("construct accepted authority engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("open authority source"),
                idempotency_key: IdempotencyKey::try_from("winning-key").expect("key"),
                actor: ActorId::from_str("winner").expect("actor"),
                pdf_limits: PdfLimits::default(),
            })
        } else {
            IngestEngine::new(
                &mut store,
                &vault,
                &QuarantinedProbe {
                    provenance: provenance.clone(),
                    reason: PdfQuarantineReason::Corrupt,
                },
                &FixedClock,
                &ids,
            )
            .expect("construct quarantine authority engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("open authority source"),
                idempotency_key: IdempotencyKey::try_from("winning-key").expect("key"),
                actor: ActorId::from_str("winner").expect("actor"),
                pdf_limits: PdfLimits::default(),
            })
        }
        .expect("establish later sticky authority");

        drop(store);
        drop(vault);
        let mut store = Store::open_writer(&database_path).expect("reopen competing store");
        let vault = reopened_vault(&vault_root);
        let no_probe = FailingProbe {
            provenance: provenance.clone(),
        };
        let resumed = IngestEngine::new(&mut store, &vault, &no_probe, &FixedClock, &ids)
            .expect("construct zero-probe resume engine")
            .resume(first_job_id, ActorId::from_str("resumer").expect("actor"))
            .expect("resume against later authority");
        assert_eq!(resumed.resumed_attempt, Some(1));
        assert_eq!(resumed.interrupted_event_id, None);
        if accepted_authority {
            assert_eq!(authority.outcome, IngestOutcome::AcceptedNew);
            assert_eq!(resumed.receipt.outcome, IngestOutcome::AcceptedDuplicate);
            let mut expected_preexisting = vec![
                authority.content_sha256.expect("accepted original"),
                authority
                    .evidence_manifest
                    .as_ref()
                    .expect("accepted manifest")
                    .manifest_content_sha256,
            ];
            expected_preexisting.sort_unstable();
            assert_eq!(
                resumed.receipt.preexisting_vault_digests,
                expected_preexisting
            );
            assert_eq!(resumed.receipt.document_id, authority.document_id);
            assert_eq!(resumed.receipt.revision_id, authority.revision_id);
        } else {
            assert_eq!(authority.outcome, IngestOutcome::QuarantinedCorrupt);
            assert_eq!(resumed.receipt.outcome, IngestOutcome::QuarantinedCorrupt);
            assert_eq!(resumed.receipt.quarantine, authority.quarantine);
            assert_eq!(
                resumed.receipt.preexisting_vault_digests,
                vec![authority.content_sha256.expect("retained original")]
            );
        }
        let after = FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect competing authority");
        assert_eq!(after.jobs.len(), 2);
        assert!(
            after
                .jobs
                .iter()
                .all(|job| job.state == heleos_core::JobState::Succeeded)
        );
        assert_eq!(
            after
                .intake_events
                .iter()
                .filter(|event| event.outcome != IngestOutcome::Interrupted)
                .count(),
            2
        );
        if accepted_authority {
            assert_eq!(after.counts.documents, 1);
            assert_eq!(after.counts.revisions, 1);
            assert_eq!(after.counts.evidence_objects, 1);
            assert_eq!(after.evidence.len(), 1);
            assert_eq!(
                after.evidence[0].originating_job_id,
                authority.authoritative_job_id
            );
            let authority_lineage = authority
                .evidence_manifest
                .as_ref()
                .expect("later winner evidence receipt")
                .lineages
                .iter()
                .find(|lineage| lineage.project_id == project_id)
                .expect("later winner project lineage");
            assert_eq!(authority_lineage.evidence_id, after.evidence[0].evidence_id);
            assert_eq!(
                authority_lineage.originating_job_id,
                authority.authoritative_job_id
            );
            let resumed_lineage = resumed
                .receipt
                .evidence_manifest
                .as_ref()
                .expect("queued winner evidence receipt")
                .lineages
                .iter()
                .find(|lineage| lineage.project_id == project_id)
                .expect("queued winner project lineage");
            assert_eq!(resumed_lineage.evidence_id, after.evidence[0].evidence_id);
            assert_eq!(
                resumed_lineage.originating_job_id,
                authority.authoritative_job_id
            );
            assert_eq!(resumed_lineage, authority_lineage);
        } else {
            assert_eq!(after.counts.documents, 0);
            assert_eq!(after.counts.evidence_objects, 0);
        }
        let audit = IngestEngine::new(&mut store, &vault, &no_probe, &FixedClock, &ids)
            .expect("construct competing audit engine")
            .verify_audit_chain()
            .expect("verify competing audit chain");
        assert!(audit.valid);
        assert!(audit.findings.is_empty());
    }
}

#[test]
#[cfg(unix)]
fn sticky_manifest_quota_faults_resume_from_every_boundary_without_reprobing() {
    // Break caught: the sticky manifest-quota candidate being confused with a novel PDF after
    // any durable fault boundary.
    const QUARANTINE_BYTES: u64 = 8 * 1024 * 1024 * 1024;

    for point in [
        FaultPoint::AfterVaultPublish,
        FaultPoint::AfterJobStart,
        FaultPoint::AfterCheckpointCommit,
        FaultPoint::BeforeAuthoritativeCommit,
        FaultPoint::AfterAuthoritativeCommit,
    ] {
        let database_root = TempDir::new().expect("create database parent");
        heleos_core::apply_private_permissions(database_root.path())
            .expect("harden database parent");
        let database_path = fs::canonicalize(database_root.path())
            .expect("canonicalize database parent")
            .join("foundation.sqlite3");
        let mut store = Store::open_writer(&database_path).expect("open store");
        store.migrate().expect("migrate store");
        let vault_root = TempDir::new().expect("create vault parent");
        let vault = opened_vault(&vault_root);
        let project_id = ProjectId::from_uuid(
            Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
        );
        let ids = SequenceIds::new(
            (2_u128..=500).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
        );
        let clock = ManualClock::new(NOW_MS);
        ProjectService::new(&mut store, &clock, &ids)
            .expect("construct project service")
            .create(ProjectCreateRequest {
                project_id: Some(project_id),
                name: "Sticky manifest quota recovery".to_owned(),
                actor: ActorId::from_str("creator").expect("actor"),
                data_class: DataClass::Internal,
            })
            .expect("create project");
        let source_root = TempDir::new().expect("create source directory");
        let source_path = source_root.path().join("sticky-manifest-quota.pdf");
        let source_bytes = b"%PDF-1.7\nsticky manifest quota recovery\n%%EOF\n";
        fs::write(&source_path, source_bytes).expect("write source");
        let source_digest =
            Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash sticky source");
        let source_length = u64::try_from(source_bytes.len()).expect("source length fits");
        create_sparse_vault_orphans(
            &vault_root,
            QUARANTINE_BYTES
                .checked_sub(source_length)
                .expect("source fits quarantine allowance"),
            &[source_digest],
        );
        let provenance = accepted_probe().provenance;
        let first = IngestEngine::new(
            &mut store,
            &vault,
            &AcceptedProbe {
                provenance: provenance.clone(),
            },
            &clock,
            &ids,
        )
        .expect("construct first authority engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open source"),
            idempotency_key: IdempotencyKey::try_from("manifest-quota-authority").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("establish sticky manifest-quota authority");
        assert_eq!(first.outcome, IngestOutcome::QuarantinedLimit);
        assert!(matches!(
            first.quarantine.as_ref().expect("quarantine").reason,
            IntakeQuarantineReasonV1::EvidenceManifestQuota
        ));
        assert!(vault.open_verified(source_digest).is_ok());

        let no_probe = FailingProbe {
            provenance: provenance.clone(),
        };
        let interrupted = IngestEngine::with_fault_injector(
            &mut store,
            &vault,
            &no_probe,
            &clock,
            &ids,
            &FailAt(point),
        )
        .expect("construct sticky manifest-quota fault engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open sticky source"),
            idempotency_key: IdempotencyKey::try_from("manifest-quota-second-key").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
        assert!(matches!(interrupted, Err(HeleosError::FaultInjected)));

        drop(store);
        drop(vault);
        let mut store = Store::open_writer(&database_path).expect("reopen sticky quota store");
        let vault = reopened_vault(&vault_root);
        let before = FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect sticky manifest-quota checkpoint");
        let job = before
            .jobs
            .iter()
            .find(|job| job.job_id != first.authoritative_job_id)
            .expect("second sticky manifest-quota job");
        assert_eq!(
            job.state,
            match point {
                FaultPoint::AfterVaultPublish => heleos_core::JobState::Queued,
                FaultPoint::AfterAuthoritativeCommit => heleos_core::JobState::Succeeded,
                _ => heleos_core::JobState::Running,
            }
        );
        assert_eq!(
            job.attempt,
            if point == FaultPoint::AfterVaultPublish {
                0
            } else {
                1
            }
        );
        let second_before_state = match point {
            FaultPoint::AfterVaultPublish => JobState::Queued,
            FaultPoint::AfterAuthoritativeCommit => JobState::Succeeded,
            _ => JobState::Running,
        };
        let second_before_attempt = if point == FaultPoint::AfterVaultPublish {
            0
        } else {
            1
        };
        assert_eq!(
            job_image_vector(&before),
            vec![
                job_image(
                    first.authoritative_job_id,
                    JobState::Succeeded,
                    first.attempt,
                    Some(FoundationJobTerminalReason::Completed),
                ),
                job_image(
                    job.job_id,
                    second_before_state,
                    second_before_attempt,
                    (point == FaultPoint::AfterAuthoritativeCommit)
                        .then_some(FoundationJobTerminalReason::Completed),
                ),
            ]
        );
        let second_terminal_before = (point == FaultPoint::AfterAuthoritativeCommit)
            .then(|| terminal_receipt_from_database(&database_path, job.job_id));
        let mut expected_before_events = vec![ExpectedIntakeEvent {
            event_id: first.ingest_event_id,
            job_id: Some(first.authoritative_job_id),
            outcome: first.outcome,
            attempt: Some(first.attempt),
        }];
        if let Some(receipt) = &second_terminal_before {
            expected_before_events.push(ExpectedIntakeEvent {
                event_id: receipt.ingest_event_id,
                job_id: Some(job.job_id),
                outcome: receipt.outcome,
                attempt: Some(receipt.attempt),
            });
        }
        assert_eq!(intake_event_vector(&before), expected_before_events);
        let mut expected_before_audits =
            completed_job_audit_identities(project_id, &first, true, false);
        expected_before_audits.push(audit_identity("job_created", "job", job.job_id.as_uuid()));
        if point != FaultPoint::AfterVaultPublish {
            expected_before_audits.push(audit_identity("job_started", "job", job.job_id.as_uuid()));
        }
        if matches!(
            point,
            FaultPoint::AfterCheckpointCommit
                | FaultPoint::BeforeAuthoritativeCommit
                | FaultPoint::AfterAuthoritativeCommit
        ) {
            expected_before_audits.push(audit_identity(
                "job_checkpointed",
                "job",
                job.job_id.as_uuid(),
            ));
        }
        if let Some(receipt) = &second_terminal_before {
            expected_before_audits.push(audit_identity(
                "job_succeeded",
                "job",
                job.job_id.as_uuid(),
            ));
            expected_before_audits.push(audit_identity(
                "ingest_quarantined",
                "ingest_attempt",
                receipt.ingest_event_id.as_uuid(),
            ));
        }
        assert_eq!(
            audit_identity_vector(&database_path),
            expected_before_audits
        );
        if matches!(
            point,
            FaultPoint::AfterJobStart
                | FaultPoint::AfterCheckpointCommit
                | FaultPoint::BeforeAuthoritativeCommit
        ) {
            clock.set(NOW_MS + 30_000);
        }
        let resumed = IngestEngine::new(&mut store, &vault, &no_probe, &clock, &ids)
            .expect("construct sticky manifest-quota resume engine")
            .resume(job.job_id, ActorId::from_str("resumer").expect("actor"))
            .expect("resume sticky manifest-quota checkpoint");
        assert_eq!(resumed.receipt.outcome, IngestOutcome::QuarantinedLimit);
        assert!(matches!(
            resumed
                .receipt
                .quarantine
                .as_ref()
                .expect("manifest quota quarantine")
                .reason,
            IntakeQuarantineReasonV1::EvidenceManifestQuota
        ));
        assert_eq!(
            resumed.receipt.preexisting_vault_digests,
            vec![source_digest]
        );
        assert_eq!(
            resumed.resumed_attempt,
            match point {
                FaultPoint::AfterVaultPublish => Some(1),
                FaultPoint::AfterJobStart
                | FaultPoint::AfterCheckpointCommit
                | FaultPoint::BeforeAuthoritativeCommit => Some(2),
                FaultPoint::AfterAuthoritativeCommit => None,
            }
        );
        assert_eq!(
            resumed.interrupted_event_id.is_some(),
            matches!(
                point,
                FaultPoint::AfterJobStart
                    | FaultPoint::AfterCheckpointCommit
                    | FaultPoint::BeforeAuthoritativeCommit
            )
        );
        let expected_attempt = match point {
            FaultPoint::AfterVaultPublish | FaultPoint::AfterAuthoritativeCommit => 1,
            FaultPoint::AfterJobStart
            | FaultPoint::AfterCheckpointCommit
            | FaultPoint::BeforeAuthoritativeCommit => 2,
        };
        assert_eq!(resumed.receipt.attempt, expected_attempt);
        if let Some(stored) = &second_terminal_before {
            assert_eq!(&resumed.receipt, stored);
            assert_eq!(
                canonical_json(&resumed.receipt).expect("canonical resumed manifest quota"),
                canonical_json(stored).expect("canonical stored manifest quota")
            );
        }
        assert_receipt_matches_terminal_checkpoint(
            &database_path,
            &resumed.receipt,
            expected_attempt,
        );
        let after = FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect sticky manifest-quota recovery");
        assert_eq!(after.jobs.len(), 2);
        assert_eq!(
            job_image_vector(&after),
            vec![
                job_image(
                    first.authoritative_job_id,
                    JobState::Succeeded,
                    first.attempt,
                    Some(FoundationJobTerminalReason::Completed),
                ),
                job_image(
                    job.job_id,
                    JobState::Succeeded,
                    expected_attempt,
                    Some(FoundationJobTerminalReason::Completed),
                ),
            ]
        );
        assert_eq!(resumed.receipt.content_sha256, first.content_sha256);
        assert_eq!(resumed.receipt.byte_length, first.byte_length);
        assert_eq!(resumed.receipt.quarantine, first.quarantine);
        assert_eq!(resumed.receipt.document_id, first.document_id);
        assert_eq!(resumed.receipt.revision_id, first.revision_id);
        assert_eq!(resumed.receipt.sheet_ids, first.sheet_ids);
        assert_eq!(resumed.receipt.evidence_manifest, first.evidence_manifest);
        assert_quarantine_receipt_authority(
            &resumed.receipt,
            source_bytes,
            IntakeQuarantineReasonV1::EvidenceManifestQuota,
            &provenance,
            &after,
        );
        let mut expected_after_events = vec![ExpectedIntakeEvent {
            event_id: first.ingest_event_id,
            job_id: Some(first.authoritative_job_id),
            outcome: first.outcome,
            attempt: Some(first.attempt),
        }];
        if let Some(interrupted_event_id) = resumed.interrupted_event_id {
            expected_after_events.push(ExpectedIntakeEvent {
                event_id: interrupted_event_id,
                job_id: Some(job.job_id),
                outcome: IngestOutcome::Interrupted,
                attempt: Some(1),
            });
        }
        expected_after_events.push(ExpectedIntakeEvent {
            event_id: resumed.receipt.ingest_event_id,
            job_id: Some(job.job_id),
            outcome: resumed.receipt.outcome,
            attempt: Some(expected_attempt),
        });
        assert_eq!(intake_event_vector(&after), expected_after_events);
        let mut expected_after_audits =
            completed_job_audit_identities(project_id, &first, true, false);
        expected_after_audits.extend(fault_job_final_audit_identities(
            point,
            &resumed.receipt,
            false,
        ));
        assert_eq!(audit_identity_vector(&database_path), expected_after_audits);
        let audit = IngestEngine::new(&mut store, &vault, &no_probe, &clock, &ids)
            .expect("construct sticky manifest-quota audit engine")
            .verify_audit_chain()
            .expect("verify sticky manifest-quota audit chain");
        assert!(audit.valid);
        assert!(audit.findings.is_empty());
    }
}

#[test]
fn trusted_post_start_failure_is_audited_failed_while_injected_loss_is_recoverable() {
    // Break caught: trusted probe/object failures leaking an indefinitely live Running lease.
    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let ids = SequenceIds::new(
        (2_u128..=80).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
    );
    ProjectService::new(&mut store, &FixedClock, &ids)
        .expect("construct project service")
        .create(ProjectCreateRequest {
            project_id: Some(project_id),
            name: "Failure project".to_owned(),
            actor: ActorId::from_str("creator").expect("actor"),
            data_class: DataClass::Internal,
        })
        .expect("create project");
    let source_root = TempDir::new().expect("create source directory");
    let source_path = source_root.path().join("failure.pdf");
    fs::write(&source_path, b"%PDF-1.7\ntrusted failure\n%%EOF\n").expect("write source");
    let probe = FailingProbe {
        provenance: accepted_probe().provenance,
    };
    let failure = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
        .expect("construct failure engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open retained source"),
            idempotency_key: IdempotencyKey::try_from("trusted-failure").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
    assert!(matches!(failure, Err(HeleosError::Integrity)));
    let inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect failed job");
    assert_eq!(inspection.counts.jobs, 1);
    assert_eq!(inspection.counts.ingest_events, 0);
    assert_eq!(inspection.counts.audit_events, 4);
    assert_eq!(inspection.jobs[0].state, heleos_core::JobState::Failed);
    assert_eq!(
        inspection.jobs[0].terminal_reason,
        Some(heleos_core::FoundationJobTerminalReason::InternalFailure)
    );
}

#[test]
fn accepted_intake_commits_two_objects_and_reader_visible_evidence() {
    // Break caught: accepted intake lacking its finite job, manifest lineage, and stable reader.
    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let ids = SequenceIds::new(
        (2_u128..=40).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
    );
    ProjectService::new(&mut store, &FixedClock, &ids)
        .expect("construct project service")
        .create(ProjectCreateRequest {
            project_id: Some(project_id),
            name: "Demo".to_owned(),
            actor: ActorId::from_str("tester").expect("actor"),
            data_class: DataClass::Internal,
        })
        .expect("create project");

    let source_root = TempDir::new().expect("create source directory");
    let source_path = source_root.path().join("fixture.pdf");
    let source_bytes = b"%PDF-1.7\nfixture\n%%EOF\n";
    fs::write(&source_path, source_bytes).expect("write source fixture");
    let source_digest = Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
    let (expected_document, expected_revision) = canonical_document_ids(source_digest);
    let probe = accepted_probe();
    let receipt = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
        .expect("construct intake engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open retained intake source"),
            idempotency_key: IdempotencyKey::try_from("request-one").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("ingest accepted fixture");

    assert_eq!(receipt.outcome, IngestOutcome::AcceptedNew);
    assert_eq!(receipt.content_sha256, Some(source_digest));
    assert_eq!(receipt.document_id, Some(expected_document));
    assert_eq!(receipt.revision_id, Some(expected_revision));
    assert_eq!(receipt.sheet_ids, vec![page_id(source_digest, 0)]);
    assert!(receipt.quarantine.is_none());
    assert!(receipt.evidence_manifest.is_some());
    assert_eq!(fs::read(&source_path).expect("reread source"), source_bytes);

    let reader = FoundationReader::new(&store, &vault);
    let evidence = reader
        .evidence_manifest_for_revision(expected_revision)
        .expect("read revision evidence");
    assert_eq!(evidence.manifest.original.sha256, source_digest);
    assert_eq!(evidence.lineages.len(), 1);
    let inspection = reader
        .inspect_foundation(project_id)
        .expect("inspect project foundation");
    assert_eq!(
        canonical_json(&inspection).expect("canonical populated inspection"),
        br#"{"content_objects":[{"admission_state":"accepted","byte_length":1354,"media_type":"application/vnd.heleos.evidence-manifest+json;version=1","quarantine":null,"sha256":"310106e8067cf1f28f777684bb188d6a60b8d30828dc3bc856ce356c00f12be8","vault_key":"objects/sha256/31/01/310106e8067cf1f28f777684bb188d6a60b8d30828dc3bc856ce356c00f12be8"},{"admission_state":"accepted","byte_length":23,"media_type":"application/pdf","quarantine":null,"sha256":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","vault_key":"objects/sha256/c6/3e/c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248"}],"counts":{"audit_events":7,"content_objects":2,"documents":1,"evidence_objects":1,"ingest_events":1,"jobs":1,"project_documents":1,"revisions":1,"sheets":1},"document_ids":["c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248"],"evidence":[{"document_id":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","evidence_id":"00000000-0000-4000-8000-00000000000a","extraction_method":"heleos.pdf-probe/v1","manifest":{"byte_length":1354,"media_type":"application/vnd.heleos.evidence-manifest+json;version=1","sha256":"310106e8067cf1f28f777684bb188d6a60b8d30828dc3bc856ce356c00f12be8","vault_key":"objects/sha256/31/01/310106e8067cf1f28f777684bb188d6a60b8d30828dc3bc856ce356c00f12be8"},"original":{"byte_length":23,"media_type":"application/pdf","sha256":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","vault_key":"objects/sha256/c6/3e/c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248"},"originating_job_id":"00000000-0000-4000-8000-000000000003","probe_provenance":{"guest_dependency_graph_sha256":"0303030303030303030303030303030303030303030303030303030303030303","guest_source_tree_sha256":"0202020202020202020202020202020202020202020202020202020202020202","guest_wasm_sha256":"0101010101010101010101010101010101010101010101010101010101010101","parser_name":"fixture-parser","parser_version":"1.0.0","protocol_version":"heleos.pdf-probe/v1"},"requested_limits":{"max_fuel":5000000000,"max_guest_memory_bytes":805306368,"max_indirect_objects":250000,"max_input_bytes":268435456,"max_instances":1,"max_metadata_bytes":16777216,"max_nested_references":64,"max_page_axis_points":14400,"max_pages":10000,"max_protocol_output_bytes":4194304,"max_tables":4,"timeout_seconds":120},"review_state":"accepted","revision_id":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248"}],"intake_events":[{"attempt":1,"content_sha256":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","ingest_event_id":"00000000-0000-4000-8000-000000000008","job_id":"00000000-0000-4000-8000-000000000003","outcome":"accepted_new","terminal_at_ms":1700000000000}],"jobs":[{"attempt":1,"created_at_ms":1700000000000,"job_id":"00000000-0000-4000-8000-000000000003","kind":"pdf_ingest","state":"succeeded","terminal_reason":"completed","updated_at_ms":1700000000000}],"project_id":"00000000-0000-4000-8000-000000000001","revision_ids":["c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248"],"schema":"heleos.foundation-inspection/v1","sheets":[{"height_micropoints":792000000,"index":0,"parent_content_sha256":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","revision_id":"c63eacfdc2e885fda2cffe9a1535f6a059136b4ed396ff851a21e162803a8248","rotation_degrees":0,"sheet_id":"781c897d999f036e5ceaffbeb1c5b90053472024df18667778451cfe5e21a0c7","transform":{"m11":1,"m12":0,"m21":0,"m22":-1,"tx_micropoints":0,"ty_micropoints":792000000},"unit":"pt","width_micropoints":612000000}]}"#,
    );
    assert_eq!(inspection.counts.content_objects, 2);
    assert_eq!(inspection.counts.documents, 1);
    assert_eq!(inspection.counts.revisions, 1);
    assert_eq!(inspection.counts.sheets, 1);
    assert_eq!(inspection.counts.evidence_objects, 1);
    assert_eq!(inspection.counts.ingest_events, 1);
    assert_eq!(inspection.counts.jobs, 1);
}

#[test]
fn evidence_reader_reconstructs_cross_project_lineage_in_canonical_order() {
    // Break caught: a revision reader selecting one project row or trusting insertion order.
    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let project_a = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project A UUID"),
    );
    let project_b = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000002").expect("project B UUID"),
    );
    let ids = SequenceIds::new(
        (10_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
    );
    for (project_id, name) in [(project_b, "Project B"), (project_a, "Project A")] {
        ProjectService::new(&mut store, &FixedClock, &ids)
            .expect("construct project service")
            .create(ProjectCreateRequest {
                project_id: Some(project_id),
                name: name.to_owned(),
                actor: ActorId::from_str("tester").expect("actor"),
                data_class: DataClass::Internal,
            })
            .expect("create project");
    }

    let source_root = TempDir::new().expect("create source directory");
    let source_path = source_root.path().join("shared.pdf");
    let source_bytes = b"%PDF-1.7\nshared fixture\n%%EOF\n";
    fs::write(&source_path, source_bytes).expect("write source fixture");
    let source_digest = Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
    let (_, revision_id) = canonical_document_ids(source_digest);
    let probe = accepted_probe();
    for project_id in [project_b, project_a] {
        IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
            .expect("construct intake engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("open retained intake source"),
                idempotency_key: IdempotencyKey::try_from("shared-cross-project-key").expect("key"),
                actor: ActorId::from_str("tester").expect("actor"),
                pdf_limits: PdfLimits::default(),
            })
            .expect("ingest shared fixture");
    }

    let evidence = FoundationReader::new(&store, &vault)
        .evidence_manifest_for_revision(revision_id)
        .expect("reconstruct cross-project evidence");
    assert_eq!(evidence.lineages.len(), 2);
    assert_eq!(evidence.lineages[0].project_id, project_a);
    assert_eq!(evidence.lineages[1].project_id, project_b);
    assert!(evidence.lineages[0].evidence_id != evidence.lineages[1].evidence_id);
    assert_eq!(
        serde_json::from_slice::<heleos_core::EvidenceManifestReceipt>(
            &canonical_json(&evidence).expect("canonical evidence receipt")
        )
        .expect("strict evidence receipt round trip"),
        evidence
    );
}

#[test]
fn evidence_reader_rehashes_both_committed_vault_objects() {
    // Break caught: trusting database digests without reopening immutable object bytes.
    let (store, vault, vault_root, revision, original, _) = accepted_reader_fixture();
    fs::write(
        vault_root
            .path()
            .join("vault")
            .join(Vault::object_key(original)),
        b"corrupt original",
    )
    .expect("corrupt original object");
    assert!(matches!(
        FoundationReader::new(&store, &vault).evidence_manifest_for_revision(revision),
        Err(HeleosError::Integrity)
    ));

    let (store, vault, vault_root, revision, _, manifest) = accepted_reader_fixture();
    fs::write(
        vault_root
            .path()
            .join("vault")
            .join(Vault::object_key(manifest)),
        b"corrupt manifest",
    )
    .expect("corrupt manifest object");
    assert!(matches!(
        FoundationReader::new(&store, &vault).evidence_manifest_for_revision(revision),
        Err(HeleosError::Integrity)
    ));
}

#[test]
fn ingest_engine_read_only_forwarders_match_foundation_reader_exactly() {
    // Break caught: the convenience API applying different project, vault, or audit semantics
    // from the authoritative FoundationReader implementation.
    let (mut store, vault, _vault_root, revision, _, _) = accepted_reader_fixture();
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let expected_audit = FoundationReader::new(&store, &vault)
        .verify_audit_chain()
        .expect("read audit directly");
    let expected_evidence = FoundationReader::new(&store, &vault)
        .evidence_manifest_for_revision(revision)
        .expect("read evidence directly");
    let expected_inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect directly");
    let ids = SequenceIds::new(std::iter::empty::<Uuid>());
    let probe = FailingProbe {
        provenance: accepted_probe().provenance,
    };
    let engine = IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
        .expect("construct read-only forwarding engine");

    assert_eq!(
        engine.verify_audit_chain().expect("forward audit read"),
        expected_audit
    );
    assert_eq!(
        engine
            .evidence_manifest_for_revision(revision)
            .expect("forward evidence read"),
        expected_evidence
    );
    assert_eq!(
        engine
            .inspect_foundation(project_id)
            .expect("forward inspection"),
        expected_inspection
    );
}

#[test]
fn evidence_reader_rejects_one_disagreeing_cross_project_row() {
    // Break caught: selecting the first lineage instead of requiring every row to agree.
    let database_root = TempDir::new().expect("create database parent");
    heleos_core::apply_private_permissions(database_root.path()).expect("harden database parent");
    let database_path = fs::canonicalize(database_root.path())
        .expect("canonicalize database parent")
        .join("foundation.sqlite3");
    let mut store = Store::open_writer(&database_path).expect("open writer");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let project_a = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project A UUID"),
    );
    let project_b = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000002").expect("project B UUID"),
    );
    let ids = SequenceIds::new(
        (10_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
    );
    for (project_id, name) in [(project_a, "Project A"), (project_b, "Project B")] {
        ProjectService::new(&mut store, &FixedClock, &ids)
            .expect("construct project service")
            .create(ProjectCreateRequest {
                project_id: Some(project_id),
                name: name.to_owned(),
                actor: ActorId::from_str("tester").expect("actor"),
                data_class: DataClass::Internal,
            })
            .expect("create project");
    }
    let source_root = TempDir::new().expect("create source directory");
    let source_path = source_root.path().join("shared.pdf");
    let source_bytes = b"%PDF-1.7\nshared hostile fixture\n%%EOF\n";
    fs::write(&source_path, source_bytes).expect("write source fixture");
    let source_digest = Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash source");
    let (_, revision_id) = canonical_document_ids(source_digest);
    let probe = accepted_probe();
    for (project_id, key) in [(project_a, "project-a"), (project_b, "project-b")] {
        IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
            .expect("construct intake engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("open retained intake source"),
                idempotency_key: IdempotencyKey::try_from(key).expect("key"),
                actor: ActorId::from_str("tester").expect("actor"),
                pdf_limits: PdfLimits::default(),
            })
            .expect("ingest shared fixture");
    }
    drop(store);

    let read_only = Store::open_read_only(&database_path).expect("open clean read-only snapshot");
    let reader = FoundationReader::new(&read_only, &vault);
    assert_eq!(
        reader
            .evidence_manifest_for_revision(revision_id)
            .expect("read evidence from read-only snapshot")
            .lineages
            .len(),
        2
    );
    assert_eq!(
        reader
            .inspect_foundation(project_a)
            .expect("inspect read-only snapshot")
            .counts
            .evidence_objects,
        1
    );
    drop(read_only);

    let connection = rusqlite::Connection::open(&database_path).expect("open hostile verifier");
    connection
        .execute_batch(
            "PRAGMA ignore_check_constraints = ON;
             DROP TRIGGER evidence_objects_no_update;",
        )
        .expect("authorize hostile verifier mutation");
    connection
        .execute(
            "UPDATE evidence_objects
             SET extraction_method = 'hostile.other/v1'
             WHERE project_id = ?1",
            [project_b.as_uuid().to_string()],
        )
        .expect("mutate one lineage");
    drop(connection);

    let store = Store::open_read_only(&database_path).expect("open read-only snapshot");
    assert!(matches!(
        FoundationReader::new(&store, &vault).evidence_manifest_for_revision(revision_id),
        Err(HeleosError::Integrity)
    ));
}

#[test]
fn populated_inspection_is_canonically_sorted_and_rejects_permutations() {
    // Break caught: query/insertion order leaking into the public foundation image.
    let mut store = Store::open_in_memory().expect("open store");
    store.migrate().expect("migrate store");
    let vault_root = TempDir::new().expect("create vault parent");
    let vault = opened_vault(&vault_root);
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    let ids = SequenceIds::new(
        (2_u128..=100).map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)),
    );
    ProjectService::new(&mut store, &FixedClock, &ids)
        .expect("construct project service")
        .create(ProjectCreateRequest {
            project_id: Some(project_id),
            name: "Demo".to_owned(),
            actor: ActorId::from_str("tester").expect("actor"),
            data_class: DataClass::Internal,
        })
        .expect("create project");

    let source_root = TempDir::new().expect("create source directory");
    let mut sources = [
        ("first.pdf", b"%PDF-1.7\nfirst\n%%EOF\n".as_slice()),
        ("second.pdf", b"%PDF-1.7\nsecond\n%%EOF\n".as_slice()),
    ]
    .into_iter()
    .map(|(name, bytes)| {
        (
            Sha256Digest::hash_reader(bytes).expect("hash source"),
            name,
            bytes,
        )
    })
    .collect::<Vec<_>>();
    sources.sort_by_key(|item| std::cmp::Reverse(item.0));
    let probe = accepted_probe();
    for (ordinal, (_, name, bytes)) in sources.iter().enumerate() {
        let path = source_root.path().join(name);
        fs::write(&path, bytes).expect("write source fixture");
        IngestEngine::new(&mut store, &vault, &probe, &FixedClock, &ids)
            .expect("construct intake engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&path).expect("open retained intake source"),
                idempotency_key: IdempotencyKey::try_from(format!("request-{ordinal}"))
                    .expect("key"),
                actor: ActorId::from_str("tester").expect("actor"),
                pdf_limits: PdfLimits::default(),
            })
            .expect("ingest source fixture");
    }

    let inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect populated foundation");
    assert_eq!(inspection.content_objects.len(), 4);
    assert_eq!(inspection.document_ids.len(), 2);
    assert_eq!(inspection.revision_ids.len(), 2);
    assert_eq!(inspection.sheets.len(), 2);
    assert_eq!(inspection.evidence.len(), 2);
    assert_eq!(inspection.intake_events.len(), 2);
    assert_eq!(inspection.jobs.len(), 2);
    assert!(
        inspection
            .content_objects
            .windows(2)
            .all(|pair| pair[0].sha256 < pair[1].sha256)
    );
    assert!(
        inspection
            .revision_ids
            .windows(2)
            .all(|pair| pair[0].as_digest() < pair[1].as_digest())
    );

    let canonical = canonical_json(&inspection).expect("canonical populated inspection");
    let mut permuted = serde_json::from_slice::<serde_json::Value>(&canonical)
        .expect("decode public inspection image");
    permuted["content_objects"]
        .as_array_mut()
        .expect("content array")
        .swap(0, 1);
    assert!(serde_json::from_value::<FoundationInspection>(permuted).is_err());

    let mut permuted = serde_json::from_slice::<serde_json::Value>(&canonical)
        .expect("decode public inspection image");
    permuted["evidence"]
        .as_array_mut()
        .expect("evidence array")
        .swap(0, 1);
    assert!(serde_json::from_value::<FoundationInspection>(permuted).is_err());

    let mut over_cap = serde_json::from_slice::<serde_json::Value>(&canonical)
        .expect("decode public inspection image");
    over_cap["counts"]["audit_events"] = serde_json::json!(100_001);
    assert!(serde_json::from_value::<FoundationInspection>(over_cap).is_err());

    let mut unknown = serde_json::from_slice::<serde_json::Value>(&canonical)
        .expect("decode public inspection image");
    unknown["unknown"] = serde_json::json!(true);
    assert!(serde_json::from_value::<FoundationInspection>(unknown).is_err());

    let mut missing = serde_json::from_slice::<serde_json::Value>(&canonical)
        .expect("decode public inspection image");
    missing
        .as_object_mut()
        .expect("inspection object")
        .remove("schema");
    assert!(serde_json::from_value::<FoundationInspection>(missing).is_err());

    let mut invalid_sheet = serde_json::from_slice::<serde_json::Value>(&canonical)
        .expect("decode public inspection image");
    invalid_sheet["sheets"][0]["width_micropoints"] = serde_json::json!(0);
    assert!(serde_json::from_value::<FoundationInspection>(invalid_sheet).is_err());
}
