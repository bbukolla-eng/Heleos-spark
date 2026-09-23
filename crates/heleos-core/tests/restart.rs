#![forbid(unsafe_code)]
#![cfg(unix)]

// These quota-boundary regressions require sparse files to represent 8 GiB without
// materializing it. Native Windows coverage needs an NTFS sparse-file fixture.

use std::{
    fs,
    io::Write,
    os::unix::fs::MetadataExt,
    process::Command,
    str::FromStr,
    sync::{
        Mutex,
        atomic::{AtomicI64, Ordering},
    },
};

use heleos_core::{
    ActorId, Clock, DataClass, EVIDENCE_MANIFEST_SCHEMA_V1, EvidenceContentV1, EvidenceManifestV1,
    FaultInjector, FaultPoint, FoundationReader, HeleosError, IdGenerator, IdempotencyKey,
    IngestEngine, IngestOutcome, IngestRequest, IntakeQuarantineReasonV1, IntakeSource, JobState,
    PDF_MEDIA_TYPE, PageMetadata, PageTransform, PageUnit, PdfInspection, PdfLimits, PdfProbe,
    PdfProbeOutcome, PdfProbeProvenance, PdfQuarantine, PdfQuarantineReason, ProjectCreateRequest,
    ProjectId, ProjectService, PutOutcome, ReconciliationFinding, Result, RevisionId, Sha256Digest,
    Store, Vault, VaultConfig, VaultInventory, VaultInventoryEntry, VaultOpenMode,
    VaultVerification, VaultWriteBudget, VerifiedObject, canonical_document_ids, canonical_json,
    page_id,
};
use tempfile::TempDir;
use uuid::Uuid;

const NOW_MS: i64 = 1_700_000_000_000;
const STORE_QUARANTINE_BYTES: u64 = 8 * 1024 * 1024 * 1024;
const MAX_OBJECT_BYTES: u64 = 256 * 1024 * 1024;

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

struct SequenceIds {
    values: Mutex<std::vec::IntoIter<Uuid>>,
}

impl SequenceIds {
    fn new() -> Self {
        Self::starting_at(2)
    }

    fn starting_at(first: u128) -> Self {
        Self {
            values: Mutex::new(
                (first..first.checked_add(998).expect("bounded test ID range"))
                    .map(|value| Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value))
                    .collect::<Vec<_>>()
                    .into_iter(),
            ),
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

#[derive(Clone)]
struct AcceptedProbe {
    provenance: PdfProbeProvenance,
}

#[derive(Clone)]
struct QuarantinedProbe {
    provenance: PdfProbeProvenance,
    reason: PdfQuarantineReason,
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
            pages: vec![accepted_page(digest)],
        }))
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

fn accepted_page(digest: Sha256Digest) -> PageMetadata {
    PageMetadata {
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
        pages: vec![accepted_page(original)],
        probe_provenance: accepted_probe().provenance,
        requested_limits: PdfLimits::default().into(),
    };
    let bytes = canonical_json(&manifest).expect("encode accepted manifest");
    let digest = Sha256Digest::hash_reader(bytes.as_slice()).expect("hash accepted manifest");
    (digest, bytes)
}

fn database_path(parent: &TempDir) -> std::path::PathBuf {
    heleos_core::apply_private_permissions(parent.path()).expect("harden database parent");
    fs::canonicalize(parent.path())
        .expect("canonicalize database parent")
        .join("foundation.sqlite3")
}

fn open_new_vault(parent: &TempDir) -> Vault {
    heleos_core::apply_private_permissions(parent.path()).expect("harden vault parent");
    Vault::open(VaultConfig {
        root: fs::canonicalize(parent.path())
            .expect("canonicalize vault parent")
            .join("vault"),
        open_mode: VaultOpenMode::CreateNew,
    })
    .expect("open new vault")
}

fn reopen_vault(parent: &TempDir) -> Vault {
    Vault::open(VaultConfig {
        root: fs::canonicalize(parent.path())
            .expect("canonicalize vault parent")
            .join("vault"),
        open_mode: VaultOpenMode::ExistingOnly,
    })
    .expect("reopen vault")
}

fn create_project(
    store: &mut Store,
    clock: &ManualClock,
    ids: &SequenceIds,
    project_id: ProjectId,
) {
    ProjectService::new(store, clock, ids)
        .expect("construct project service")
        .create(ProjectCreateRequest {
            project_id: Some(project_id),
            name: "Terminal orphan boundary".to_owned(),
            actor: ActorId::from_str("creator").expect("actor"),
            data_class: DataClass::Internal,
        })
        .expect("create project");
}

fn padded_pdf(label: &str, minimum_length: usize) -> Vec<u8> {
    let mut bytes = format!("%PDF-1.7\n{label}\n").into_bytes();
    let suffix = b"\n%%EOF\n";
    if bytes.len() + suffix.len() < minimum_length {
        bytes.resize(minimum_length - suffix.len(), b'x');
    }
    bytes.extend_from_slice(suffix);
    bytes
}

fn create_sparse_orphans(parent: &TempDir, mut total_bytes: u64, excluded: &[Sha256Digest]) {
    let vault_root = fs::canonicalize(parent.path())
        .expect("canonicalize sparse-orphan parent")
        .join("vault");
    let mut index = 0_u64;
    while total_bytes > 0 {
        let digest = loop {
            let mut bytes = [0xa7; 32];
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
        let second_shard = path.parent().expect("second digest shard");
        let first_shard = second_shard.parent().expect("first digest shard");
        fs::create_dir_all(second_shard).expect("create sparse orphan shards");
        heleos_core::apply_private_permissions(first_shard).expect("harden first orphan shard");
        heleos_core::apply_private_permissions(second_shard).expect("harden second orphan shard");
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

fn write_process_sentinel(path: &std::path::Path, bytes: &[u8]) {
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .expect("create process sentinel");
    file.write_all(bytes).expect("write process sentinel");
    file.sync_all().expect("sync process sentinel");
}

#[test]
fn pre_job_vault_publish_process_helper() {
    let Some(vault_root) = std::env::var_os("HELEOS_TEST_PRE_JOB_VAULT_ROOT") else {
        return;
    };
    let source_path =
        std::env::var_os("HELEOS_TEST_PRE_JOB_SOURCE").expect("child source path is present");
    let started = std::env::var_os("HELEOS_TEST_PRE_JOB_STARTED")
        .expect("child start sentinel path is present");
    let published = std::env::var_os("HELEOS_TEST_PRE_JOB_PUBLISHED")
        .expect("child publication sentinel path is present");

    let vault = Vault::open(VaultConfig {
        root: vault_root.into(),
        open_mode: VaultOpenMode::ExistingOnly,
    })
    .expect("child opens existing vault");
    let source = fs::File::open(source_path).expect("child opens source");
    let byte_length = source
        .metadata()
        .expect("child reads source metadata")
        .len();
    write_process_sentinel(std::path::Path::new(&started), b"started\n");
    let stored = match vault
        .put_reader(
            source,
            VaultWriteBudget::new(byte_length, byte_length).expect("child publication budget"),
        )
        .expect("child publishes original")
    {
        PutOutcome::Stored(stored) => stored,
        PutOutcome::QuotaRejected { .. } => panic!("child publication unexpectedly rejected"),
    };
    assert!(stored.newly_published);
    assert_eq!(stored.byte_length, byte_length);
    write_process_sentinel(
        std::path::Path::new(&published),
        stored.digest.to_string().as_bytes(),
    );
    std::process::exit(0);
}

#[test]
fn manifest_precheckpoint_process_helper() {
    let Some(database_path) = std::env::var_os("HELEOS_TEST_MANIFEST_DATABASE") else {
        return;
    };
    let vault_root =
        std::env::var_os("HELEOS_TEST_MANIFEST_VAULT_ROOT").expect("child vault root is present");
    let source_path = std::path::PathBuf::from(
        std::env::var_os("HELEOS_TEST_MANIFEST_SOURCE").expect("child source path is present"),
    );
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str(
            &std::env::var("HELEOS_TEST_MANIFEST_PROJECT").expect("child project is present"),
        )
        .expect("parse child project"),
    );
    let started = std::env::var_os("HELEOS_TEST_MANIFEST_STARTED")
        .expect("child running sentinel path is present");
    let published = std::env::var_os("HELEOS_TEST_MANIFEST_PUBLISHED")
        .expect("child publication sentinel path is present");

    let mut store = Store::open_writer(database_path).expect("child opens writer");
    let vault = Vault::open(VaultConfig {
        root: vault_root.into(),
        open_mode: VaultOpenMode::ExistingOnly,
    })
    .expect("child opens existing vault");
    let probe = accepted_probe();
    let clock = ManualClock::new(NOW_MS);
    let ids = SequenceIds::starting_at(100_000);
    let fault = FailAt(FaultPoint::AfterJobStart);
    let result =
        IngestEngine::with_fault_injector(&mut store, &vault, &probe, &clock, &ids, &fault)
            .expect("construct child intake engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("child opens intake source"),
                idempotency_key: IdempotencyKey::try_from("manifest-precheckpoint-process")
                    .expect("child idempotency key"),
                actor: ActorId::from_str("child-actor").expect("child actor"),
                pdf_limits: PdfLimits::default(),
            });
    assert!(matches!(result, Err(HeleosError::FaultInjected)));
    let inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("child inspects running job");
    assert_eq!(inspection.jobs.len(), 1);
    assert_eq!(inspection.jobs[0].state, JobState::Running);
    assert_eq!(inspection.jobs[0].attempt, 1);
    assert_eq!(inspection.counts.content_objects, 0);
    assert_eq!(inspection.counts.ingest_events, 0);
    write_process_sentinel(
        std::path::Path::new(&started),
        inspection.jobs[0]
            .job_id
            .as_uuid()
            .hyphenated()
            .to_string()
            .as_bytes(),
    );

    let source_bytes = fs::read(source_path).expect("child rereads deterministic fixture bytes");
    let (expected_digest, manifest_bytes) = accepted_manifest_bytes(&source_bytes);
    let manifest_length = u64::try_from(manifest_bytes.len()).expect("manifest length fits");
    let stored = match vault
        .put_reader(
            manifest_bytes.as_slice(),
            VaultWriteBudget::new(manifest_length, manifest_length).expect("child manifest budget"),
        )
        .expect("child publishes manifest before checkpoint")
    {
        PutOutcome::Stored(stored) => stored,
        PutOutcome::QuotaRejected { .. } => panic!("child manifest unexpectedly rejected"),
    };
    assert!(stored.newly_published);
    assert_eq!(stored.digest, expected_digest);
    assert_eq!(stored.byte_length, manifest_length);
    write_process_sentinel(
        std::path::Path::new(&published),
        stored.digest.to_string().as_bytes(),
    );
    std::process::exit(0);
}

struct IngestFixture<'a> {
    probe: &'a AcceptedProbe,
    clock: &'a ManualClock,
    ids: &'a SequenceIds,
    project_id: ProjectId,
}

impl IngestFixture<'_> {
    fn ingest_with_fault(
        &self,
        store: &mut Store,
        vault: &Vault,
        source_path: &std::path::Path,
        key: &str,
        point: FaultPoint,
    ) {
        let fault = FailAt(point);
        let result = IngestEngine::with_fault_injector(
            store, vault, self.probe, self.clock, self.ids, &fault,
        )
        .expect("construct faulting engine")
        .ingest(IngestRequest {
            project_id: self.project_id,
            source: IntakeSource::open(source_path).expect("open source"),
            idempotency_key: IdempotencyKey::try_from(key).expect("idempotency key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
        assert!(matches!(result, Err(HeleosError::FaultInjected)));
    }

    fn fail_job_at_deadline(&self, store: &mut Store, vault: &Vault) {
        let job_id = FoundationReader::new(store, vault)
            .inspect_foundation(self.project_id)
            .expect("inspect abandoned checkpoint")
            .jobs[0]
            .job_id;
        self.clock.set(NOW_MS + 300_000);
        let result = IngestEngine::new(store, vault, self.probe, self.clock, self.ids)
            .expect("construct deadline engine")
            .resume(job_id, ActorId::from_str("resumer").expect("actor"));
        assert!(matches!(result, Err(HeleosError::Timeout)));
        let inspection = FoundationReader::new(store, vault)
            .inspect_foundation(self.project_id)
            .expect("inspect terminal job");
        assert_eq!(inspection.jobs.len(), 1);
        assert_eq!(inspection.jobs[0].state, JobState::Failed);
        assert_eq!(inspection.counts.content_objects, 0);
    }

    fn assert_next_publication_is_orphan_charged(
        &self,
        store: &mut Store,
        vault: &Vault,
        source_path: &std::path::Path,
        source_digest: Sha256Digest,
        key: &str,
    ) {
        let receipt = IngestEngine::new(store, vault, self.probe, self.clock, self.ids)
            .expect("construct next intake engine")
            .ingest(IngestRequest {
                project_id: self.project_id,
                source: IntakeSource::open(source_path).expect("open next source"),
                idempotency_key: IdempotencyKey::try_from(key).expect("idempotency key"),
                actor: ActorId::from_str("tester").expect("actor"),
                pdf_limits: PdfLimits::default(),
            })
            .expect("exact-boundary pressure is a finite result");
        assert_eq!(receipt.outcome, IngestOutcome::QuarantinedLimit);
        assert!(matches!(
            receipt.quarantine.expect("quota quarantine").reason,
            IntakeQuarantineReasonV1::OriginalRetentionQuota
        ));
        assert_eq!(receipt.content_sha256, Some(source_digest));
        assert!(receipt.preexisting_vault_digests.is_empty());
        assert!(matches!(
            vault.open_verified(source_digest),
            Err(HeleosError::NotFound)
        ));
    }
}

#[test]
fn failed_original_checkpoint_is_excluded_from_inventory_and_charged_as_an_orphan() {
    // Break caught: a Failed job continuing to reserve its abandoned original in inventory.
    let database_parent = TempDir::new().expect("create database parent");
    let database_path = database_path(&database_parent);
    let mut store = Store::open_writer(&database_path).expect("open writer");
    store.migrate().expect("migrate store");
    let vault_parent = TempDir::new().expect("create vault parent");
    let vault = open_new_vault(&vault_parent);
    let clock = ManualClock::new(NOW_MS);
    let ids = SequenceIds::new();
    let probe = accepted_probe();
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    create_project(&mut store, &clock, &ids, project_id);
    let fixture = IngestFixture {
        probe: &probe,
        clock: &clock,
        ids: &ids,
        project_id,
    };
    let source_parent = TempDir::new().expect("create source parent");
    let abandoned_path = source_parent.path().join("abandoned-original.pdf");
    let abandoned_bytes = padded_pdf("abandoned original", 4_096);
    fs::write(&abandoned_path, &abandoned_bytes).expect("write abandoned original");
    let abandoned_digest =
        Sha256Digest::hash_reader(abandoned_bytes.as_slice()).expect("hash abandoned original");
    fixture.ingest_with_fault(
        &mut store,
        &vault,
        &abandoned_path,
        "abandoned-original",
        FaultPoint::AfterVaultPublish,
    );
    fixture.fail_job_at_deadline(&mut store, &vault);
    assert_eq!(
        vault
            .open_verified(abandoned_digest)
            .expect("verify abandoned original")
            .byte_length(),
        u64::try_from(abandoned_bytes.len()).expect("abandoned length fits")
    );

    let next_path = source_parent.path().join("next-original.pdf");
    let next_bytes = padded_pdf("next", 64);
    fs::write(&next_path, &next_bytes).expect("write next source");
    let next_digest = Sha256Digest::hash_reader(next_bytes.as_slice()).expect("hash next source");
    let abandoned_length = u64::try_from(abandoned_bytes.len()).expect("abandoned length fits");
    drop(store);
    drop(vault);
    create_sparse_orphans(
        &vault_parent,
        STORE_QUARANTINE_BYTES
            .checked_sub(abandoned_length)
            .expect("abandoned original fits boundary"),
        &[abandoned_digest, next_digest],
    );

    let mut store = Store::open_writer(&database_path).expect("reopen writer");
    let vault = reopen_vault(&vault_parent);
    fixture.assert_next_publication_is_orphan_charged(
        &mut store,
        &vault,
        &next_path,
        next_digest,
        "after-failed-original",
    );
    assert!(vault.open_verified(abandoned_digest).is_ok());
    let inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect after orphan accounting");
    assert_eq!(inspection.counts.content_objects, 0);
    assert_eq!(inspection.jobs.len(), 2);
    assert!(
        inspection
            .jobs
            .iter()
            .any(|job| job.state == JobState::Failed)
    );
}

#[test]
fn failed_processing_checkpoint_excludes_original_and_manifest_from_inventory() {
    // Break caught: a Failed processing job retaining either abandoned object as inventory.
    let database_parent = TempDir::new().expect("create database parent");
    let database_path = database_path(&database_parent);
    let mut store = Store::open_writer(&database_path).expect("open writer");
    store.migrate().expect("migrate store");
    let vault_parent = TempDir::new().expect("create vault parent");
    let vault = open_new_vault(&vault_parent);
    let clock = ManualClock::new(NOW_MS);
    let ids = SequenceIds::new();
    let probe = accepted_probe();
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    create_project(&mut store, &clock, &ids, project_id);
    let fixture = IngestFixture {
        probe: &probe,
        clock: &clock,
        ids: &ids,
        project_id,
    };
    let source_parent = TempDir::new().expect("create source parent");
    let abandoned_path = source_parent.path().join("abandoned-processing.pdf");
    let abandoned_bytes = padded_pdf("abandoned original and manifest", 4_096);
    fs::write(&abandoned_path, &abandoned_bytes).expect("write abandoned source");
    let abandoned_digest =
        Sha256Digest::hash_reader(abandoned_bytes.as_slice()).expect("hash abandoned original");
    let (manifest_digest, manifest_bytes) = accepted_manifest_bytes(&abandoned_bytes);
    fixture.ingest_with_fault(
        &mut store,
        &vault,
        &abandoned_path,
        "abandoned-processing",
        FaultPoint::AfterCheckpointCommit,
    );
    fixture.fail_job_at_deadline(&mut store, &vault);
    assert!(vault.open_verified(abandoned_digest).is_ok());
    assert_eq!(
        vault
            .open_verified(manifest_digest)
            .expect("verify abandoned manifest")
            .byte_length(),
        u64::try_from(manifest_bytes.len()).expect("manifest length fits")
    );

    let next_path = source_parent.path().join("next-processing.pdf");
    let next_bytes = padded_pdf("next", 64);
    fs::write(&next_path, &next_bytes).expect("write next source");
    let next_digest = Sha256Digest::hash_reader(next_bytes.as_slice()).expect("hash next source");
    let abandoned_length = u64::try_from(abandoned_bytes.len()).expect("original length fits");
    let manifest_length = u64::try_from(manifest_bytes.len()).expect("manifest length fits");
    let checkpoint_bytes = abandoned_length
        .checked_add(manifest_length)
        .expect("checkpoint byte total");
    drop(store);
    drop(vault);
    create_sparse_orphans(
        &vault_parent,
        STORE_QUARANTINE_BYTES
            .checked_sub(checkpoint_bytes)
            .expect("processing objects fit boundary"),
        &[abandoned_digest, manifest_digest, next_digest],
    );

    let mut store = Store::open_writer(&database_path).expect("reopen writer");
    let vault = reopen_vault(&vault_parent);
    fixture.assert_next_publication_is_orphan_charged(
        &mut store,
        &vault,
        &next_path,
        next_digest,
        "after-failed-processing",
    );
    assert!(vault.open_verified(abandoned_digest).is_ok());
    assert!(vault.open_verified(manifest_digest).is_ok());
    let inspection = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect after processing orphan accounting");
    assert_eq!(inspection.counts.content_objects, 0);
    assert_eq!(inspection.jobs.len(), 2);
    assert!(
        inspection
            .jobs
            .iter()
            .any(|job| job.state == JobState::Failed)
    );
}

#[test]
fn subprocess_original_link_before_job_is_reported_and_charged_without_double_debit() {
    // Break caught: a process loss after the original link but before the first job transaction
    // escaping reconciliation/quarantine accounting or being repaired as a new object.
    let database_parent = TempDir::new().expect("create database parent");
    let database_path = database_path(&database_parent);
    let mut store = Store::open_writer(&database_path).expect("open writer");
    store.migrate().expect("migrate store");
    let vault_parent = TempDir::new().expect("create vault parent");
    let vault = open_new_vault(&vault_parent);
    let clock = ManualClock::new(NOW_MS);
    let ids = SequenceIds::new();
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    create_project(&mut store, &clock, &ids, project_id);

    let source_parent = TempDir::new().expect("create source parent");
    let source_path = source_parent.path().join("pre-job-loss.pdf");
    let source_bytes = padded_pdf("pre-job process loss", 4_096);
    fs::write(&source_path, &source_bytes).expect("write pre-job source");
    let source_digest =
        Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash pre-job source");
    let source_length = u64::try_from(source_bytes.len()).expect("source length fits");
    let unrelated_path = source_parent.path().join("unrelated.pdf");
    let unrelated_bytes = padded_pdf("unrelated after pre-job loss", 64);
    fs::write(&unrelated_path, &unrelated_bytes).expect("write unrelated source");
    let unrelated_digest =
        Sha256Digest::hash_reader(unrelated_bytes.as_slice()).expect("hash unrelated source");

    drop(store);
    drop(vault);
    let control = TempDir::new().expect("create child sentinel directory");
    let started = control.path().join("child-started");
    let published = control.path().join("child-published");
    let status = Command::new(std::env::current_exe().expect("locate restart test executable"))
        .arg("--exact")
        .arg("pre_job_vault_publish_process_helper")
        .arg("--nocapture")
        .env(
            "HELEOS_TEST_PRE_JOB_VAULT_ROOT",
            fs::canonicalize(vault_parent.path())
                .expect("canonicalize vault parent")
                .join("vault"),
        )
        .env("HELEOS_TEST_PRE_JOB_SOURCE", &source_path)
        .env("HELEOS_TEST_PRE_JOB_STARTED", &started)
        .env("HELEOS_TEST_PRE_JOB_PUBLISHED", &published)
        .status()
        .expect("spawn pre-job loss child");
    assert!(status.success(), "pre-job loss child failed: {status:?}");
    assert!(started.is_file(), "child did not emit its start sentinel");
    assert_eq!(
        fs::read_to_string(&published).expect("read child publication sentinel"),
        source_digest.to_string()
    );

    let store = Store::open_writer(&database_path).expect("reopen writer after child loss");
    let vault = reopen_vault(&vault_parent);
    let before = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect pre-job loss");
    assert_eq!(before.counts.jobs, 0);
    assert_eq!(before.counts.ingest_events, 0);
    assert_eq!(before.counts.content_objects, 0);
    assert_eq!(
        vault
            .open_verified(source_digest)
            .expect("verify child-published original")
            .byte_length(),
        source_length
    );
    let empty_inventory = VaultInventory::try_from_entries([]).expect("empty inventory");
    let reconciliation = vault
        .reconcile(&empty_inventory)
        .expect("reconcile child-published orphan");
    assert_eq!(reconciliation.findings.len(), 1);
    assert!(matches!(
        &reconciliation.findings[0],
        ReconciliationFinding::UnreferencedObject {
            verification: VaultVerification::Verified {
                digest,
                byte_length,
                vault_key,
            },
        } if *digest == source_digest
            && *byte_length == source_length
            && vault_key == &Vault::object_key(source_digest)
    ));
    assert!(vault.open_verified(source_digest).is_ok());

    drop(store);
    drop(vault);
    create_sparse_orphans(
        &vault_parent,
        STORE_QUARANTINE_BYTES
            .checked_sub(source_length)
            .expect("child original fits quarantine boundary"),
        &[source_digest, unrelated_digest],
    );
    let mut store = Store::open_writer(&database_path).expect("reopen writer for accounting");
    let vault = reopen_vault(&vault_parent);
    let unrelated = IngestEngine::new(&mut store, &vault, &accepted_probe(), &clock, &ids)
        .expect("construct unrelated intake engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&unrelated_path).expect("open unrelated source"),
            idempotency_key: IdempotencyKey::try_from("unrelated-after-loss").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("unrelated orphan pressure is a finite outcome");
    assert_eq!(unrelated.outcome, IngestOutcome::QuarantinedLimit);
    assert!(matches!(
        unrelated.quarantine.expect("quota result").reason,
        IntakeQuarantineReasonV1::OriginalRetentionQuota
    ));
    assert!(matches!(
        vault.open_verified(unrelated_digest),
        Err(HeleosError::NotFound)
    ));

    let quarantine_probe = QuarantinedProbe {
        provenance: accepted_probe().provenance,
        reason: PdfQuarantineReason::Corrupt,
    };
    let reused = IngestEngine::new(&mut store, &vault, &quarantine_probe, &clock, &ids)
        .expect("construct exact orphan reuse engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("reopen child source"),
            idempotency_key: IdempotencyKey::try_from("reuse-pre-job-orphan").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        })
        .expect("exact child orphan is a zero-debit duplicate");
    assert_eq!(reused.outcome, IngestOutcome::QuarantinedCorrupt);
    assert_eq!(reused.content_sha256, Some(source_digest));
    assert_eq!(reused.preexisting_vault_digests, vec![source_digest]);
    let after = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect post-loss reuse");
    assert_eq!(after.counts.jobs, 2);
    assert_eq!(after.counts.content_objects, 1);
    assert_eq!(after.content_objects[0].sha256, source_digest);
}

#[test]
fn subprocess_manifest_link_before_processing_checkpoint_is_recovered_without_double_debit() {
    // Break caught: a manifest linked after a durable VaultPublished checkpoint but before the
    // processing checkpoint escaping reconciliation or being charged twice during recovery.
    let database_parent = TempDir::new().expect("create database parent");
    let database_path = database_path(&database_parent);
    let mut store = Store::open_writer(&database_path).expect("open writer");
    store.migrate().expect("migrate store");
    let vault_parent = TempDir::new().expect("create vault parent");
    let vault = open_new_vault(&vault_parent);
    let clock = ManualClock::new(NOW_MS);
    let ids = SequenceIds::new();
    let project_id = ProjectId::from_uuid(
        Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("project UUID"),
    );
    create_project(&mut store, &clock, &ids, project_id);
    let source_parent = TempDir::new().expect("create source parent");
    let source_path = source_parent.path().join("manifest-precheckpoint.pdf");
    let source_bytes = padded_pdf("manifest precheckpoint process loss", 4_096);
    fs::write(&source_path, &source_bytes).expect("write manifest-loss source");
    let original_digest =
        Sha256Digest::hash_reader(source_bytes.as_slice()).expect("hash original");
    let original_length = u64::try_from(source_bytes.len()).expect("original length fits");
    let (manifest_digest, manifest_bytes) = accepted_manifest_bytes(&source_bytes);
    let manifest_length = u64::try_from(manifest_bytes.len()).expect("manifest length fits");
    let unrelated_path = source_parent.path().join("manifest-loss-unrelated.pdf");
    let unrelated_bytes = padded_pdf("unrelated after manifest loss", 64);
    fs::write(&unrelated_path, &unrelated_bytes).expect("write unrelated source");
    let unrelated_digest =
        Sha256Digest::hash_reader(unrelated_bytes.as_slice()).expect("hash unrelated source");

    drop(store);
    drop(vault);
    let control = TempDir::new().expect("create child sentinel directory");
    let started = control.path().join("child-started");
    let published = control.path().join("child-manifest-published");
    let status = Command::new(std::env::current_exe().expect("locate restart test executable"))
        .arg("--exact")
        .arg("manifest_precheckpoint_process_helper")
        .arg("--nocapture")
        .env("HELEOS_TEST_MANIFEST_DATABASE", &database_path)
        .env(
            "HELEOS_TEST_MANIFEST_VAULT_ROOT",
            fs::canonicalize(vault_parent.path())
                .expect("canonicalize vault parent")
                .join("vault"),
        )
        .env("HELEOS_TEST_MANIFEST_SOURCE", &source_path)
        .env(
            "HELEOS_TEST_MANIFEST_PROJECT",
            project_id.as_uuid().to_string(),
        )
        .env("HELEOS_TEST_MANIFEST_STARTED", &started)
        .env("HELEOS_TEST_MANIFEST_PUBLISHED", &published)
        .status()
        .expect("spawn manifest-precheckpoint child");
    assert!(status.success(), "manifest-loss child failed: {status:?}");
    assert!(started.is_file(), "child did not emit its running sentinel");
    assert_eq!(
        fs::read_to_string(&published).expect("read manifest publication sentinel"),
        manifest_digest.to_string()
    );

    let store = Store::open_writer(&database_path).expect("reopen writer after child loss");
    let vault = reopen_vault(&vault_parent);
    let before = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect manifest-precheckpoint loss");
    assert_eq!(before.jobs.len(), 1);
    assert_eq!(before.jobs[0].state, JobState::Running);
    assert_eq!(before.jobs[0].attempt, 1);
    let recovering_job_id = before.jobs[0].job_id;
    assert_eq!(
        fs::read_to_string(&started).expect("read child running sentinel"),
        before.jobs[0].job_id.as_uuid().hyphenated().to_string()
    );
    assert_eq!(before.counts.ingest_events, 0);
    assert_eq!(before.counts.content_objects, 0);
    assert_eq!(
        vault
            .open_verified(original_digest)
            .expect("verify checkpointed original")
            .byte_length(),
        original_length
    );
    assert_eq!(
        vault
            .open_verified(manifest_digest)
            .expect("verify precheckpoint manifest")
            .byte_length(),
        manifest_length
    );
    let referenced_original = VaultInventory::try_from_entries([VaultInventoryEntry {
        digest: original_digest,
        expected_byte_length: original_length,
        vault_key: Vault::object_key(original_digest),
    }])
    .expect("original-only checkpoint inventory");
    let reconciliation = vault
        .reconcile(&referenced_original)
        .expect("reconcile manifest-precheckpoint loss");
    assert_eq!(reconciliation.findings.len(), 1);
    assert!(matches!(
        &reconciliation.findings[0],
        ReconciliationFinding::UnreferencedObject {
            verification: VaultVerification::Verified {
                digest,
                byte_length,
                vault_key,
            },
        } if *digest == manifest_digest
            && *byte_length == manifest_length
            && vault_key == &Vault::object_key(manifest_digest)
    ));
    assert!(vault.open_verified(manifest_digest).is_ok());
    let manifest_path = fs::canonicalize(vault_parent.path())
        .expect("canonicalize manifest vault parent")
        .join("vault")
        .join(Vault::object_key(manifest_digest));
    let manifest_metadata = fs::metadata(&manifest_path).expect("stat precheckpoint manifest");
    let manifest_marker = (
        manifest_metadata.dev(),
        manifest_metadata.ino(),
        manifest_metadata.len(),
    );

    drop(store);
    drop(vault);
    create_sparse_orphans(
        &vault_parent,
        STORE_QUARANTINE_BYTES
            .checked_sub(original_length)
            .and_then(|remaining| remaining.checked_sub(manifest_length))
            .expect("original and manifest fit exact orphan allowance"),
        &[original_digest, manifest_digest, unrelated_digest],
    );
    let mut store = Store::open_writer(&database_path).expect("reopen exact-boundary writer");
    let vault = reopen_vault(&vault_parent);

    let probe = accepted_probe();
    let fixture = IngestFixture {
        probe: &probe,
        clock: &clock,
        ids: &ids,
        project_id,
    };
    fixture.assert_next_publication_is_orphan_charged(
        &mut store,
        &vault,
        &unrelated_path,
        unrelated_digest,
        "after-manifest-process-loss",
    );
    let manifest_after_rejection =
        fs::metadata(&manifest_path).expect("stat manifest after unrelated rejection");
    assert_eq!(
        (
            manifest_after_rejection.dev(),
            manifest_after_rejection.ino(),
            manifest_after_rejection.len(),
        ),
        manifest_marker
    );
    assert!(vault.open_verified(manifest_digest).is_ok());

    clock.set(NOW_MS + 30_000);
    let resumed = IngestEngine::new(&mut store, &vault, &accepted_probe(), &clock, &ids)
        .expect("construct manifest recovery engine")
        .resume(
            recovering_job_id,
            ActorId::from_str("resumer").expect("actor"),
        )
        .expect("resume manifest-precheckpoint job at exact lease expiry");
    assert_eq!(resumed.resumed_attempt, Some(2));
    assert!(resumed.interrupted_event_id.is_some());
    assert_eq!(resumed.receipt.outcome, IngestOutcome::AcceptedNew);
    let mut expected_preexisting = vec![original_digest, manifest_digest];
    expected_preexisting.sort_unstable();
    assert_eq!(
        resumed.receipt.preexisting_vault_digests,
        expected_preexisting
    );
    let after = FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id)
        .expect("inspect recovered manifest authority");
    assert_eq!(after.jobs.len(), 2);
    let recovered_job = after
        .jobs
        .iter()
        .find(|job| job.job_id == recovering_job_id)
        .expect("recovered manifest-precheckpoint job");
    assert_eq!(recovered_job.state, JobState::Succeeded);
    assert_eq!(recovered_job.attempt, 2);
    assert_eq!(after.counts.ingest_events, 3);
    assert_eq!(after.counts.content_objects, 2);
    let manifest_after_recovery =
        fs::metadata(&manifest_path).expect("stat manifest after zero-debit recovery");
    assert_eq!(
        (
            manifest_after_recovery.dev(),
            manifest_after_recovery.ino(),
            manifest_after_recovery.len(),
        ),
        manifest_marker
    );
}
