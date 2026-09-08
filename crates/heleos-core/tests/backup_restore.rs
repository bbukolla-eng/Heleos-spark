#![forbid(unsafe_code)]

use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;

use heleos_core::backup::BackupService;
use heleos_core::{
    BackupContainerSummary, BackupReceipt, BackupService as RootBackupService,
    BackupVerificationReport, RestoreReceipt, Result, Sha256Digest, Store, Vault,
};

type CreateBackup = fn(
    &mut Store,
    &Vault,
    &Path,
    &age::x25519::Recipient,
    &ed25519_dalek::SigningKey,
) -> Result<BackupReceipt>;
type VerifyBackup = fn(File, &age::x25519::Identity, &[u8; 32]) -> Result<BackupVerificationReport>;
type RestoreBackup = fn(File, &age::x25519::Identity, &[u8; 32], &Path) -> Result<RestoreReceipt>;

fn inspect_container_summary(container: &BackupContainerSummary) {
    let BackupContainerSummary {
        manifest_schema,
        signer_key_sha256,
        entry_table_sha256,
        entry_table_byte_length,
        entry_count,
        decoded_payload_byte_length,
        database_sha256,
        database_byte_length,
    } = container;

    assert_eq!(manifest_schema, "heleos.backup-manifest/v1");
    let _: &Sha256Digest = signer_key_sha256;
    let _: &Sha256Digest = entry_table_sha256;
    let _: &u64 = entry_table_byte_length;
    let _: &u64 = entry_count;
    let _: &u64 = decoded_payload_byte_length;
    let _: &Sha256Digest = database_sha256;
    let _: &u64 = database_byte_length;
}

fn inspect_backup_receipt(receipt: &BackupReceipt) {
    let BackupReceipt {
        schema,
        container,
        ciphertext_byte_length,
    } = receipt;
    assert_eq!(schema, "heleos.backup-receipt/v1");
    inspect_container_summary(container);
    let _: &u64 = ciphertext_byte_length;
}

fn inspect_verification_report(report: &BackupVerificationReport) {
    let BackupVerificationReport {
        schema,
        container,
        ciphertext_byte_length,
        checked_audit_event_count,
        audit_head_hash,
    } = report;
    assert_eq!(schema, "heleos.backup-verification-report/v1");
    inspect_container_summary(container);
    let _: &u64 = ciphertext_byte_length;
    let _: &u64 = checked_audit_event_count;
    let _: &Sha256Digest = audit_head_hash;
}

fn inspect_restore_receipt(receipt: &RestoreReceipt) {
    let RestoreReceipt {
        schema,
        verification,
    } = receipt;
    assert_eq!(schema, "heleos.restore-receipt/v1");
    inspect_verification_report(verification);
}

#[test]
fn public_backup_contract_has_exact_typed_signatures_and_path_free_dtos() {
    let _: Option<RootBackupService> = None;
    let create: CreateBackup = BackupService::create;
    let verify: VerifyBackup = BackupService::verify_container;
    let restore: RestoreBackup = BackupService::restore;

    let _: fn(&BackupReceipt) = inspect_backup_receipt;
    let _: fn(&BackupVerificationReport) = inspect_verification_report;
    let _: fn(&RestoreReceipt) = inspect_restore_receipt;
    let _ = (create, verify, restore);
}

struct Fixture {
    _root: tempfile::TempDir,
    root: std::path::PathBuf,
    store: Store,
    vault: Vault,
    identity: age::x25519::Identity,
    signer: ed25519_dalek::SigningKey,
}

struct BackupTestClock;
impl heleos_core::Clock for BackupTestClock {
    fn now_unix_ms(&self) -> i64 {
        1_700_000_000_000
    }
}

struct BackupTestIds;
impl heleos_core::IdGenerator for BackupTestIds {
    fn next_uuid(&self) -> uuid::Uuid {
        uuid::Uuid::new_v4()
    }
}

struct BackupTestProbe;
impl heleos_core::PdfProbe for BackupTestProbe {
    fn provenance(&self) -> Result<heleos_core::PdfProbeProvenance> {
        Ok(heleos_core::PdfProbeProvenance {
            parser_name: "backup-fixture".to_owned(),
            parser_version: "1.0.0".to_owned(),
            guest_wasm_sha256: Sha256Digest::from_bytes([1; 32]),
            guest_source_tree_sha256: Sha256Digest::from_bytes([2; 32]),
            guest_dependency_graph_sha256: Sha256Digest::from_bytes([3; 32]),
            protocol_version: "heleos.pdf-probe/v1".to_owned(),
        })
    }
    fn probe(
        &self,
        input: heleos_core::VerifiedObject,
        revision: heleos_core::RevisionId,
        _: heleos_core::PdfLimits,
    ) -> Result<heleos_core::PdfProbeOutcome> {
        Ok(heleos_core::PdfProbeOutcome::Accepted(
            heleos_core::PdfInspection {
                revision_id: revision,
                content_sha256: input.digest(),
                byte_length: input.byte_length(),
                provenance: self.provenance()?,
                pages: vec![heleos_core::PageMetadata {
                    index: 0,
                    page_id: heleos_core::page_id(input.digest(), 0),
                    width_micropoints: 612_000_000,
                    height_micropoints: 792_000_000,
                    unit: heleos_core::PageUnit::Point,
                    rotation_degrees: 0,
                    transform: heleos_core::PageTransform {
                        m11: 1,
                        m12: 0,
                        m21: 0,
                        m22: -1,
                        tx_micropoints: 0,
                        ty_micropoints: 792_000_000,
                    },
                }],
            },
        ))
    }
}

impl Fixture {
    fn new() -> Self {
        let temporary = tempfile::tempdir().unwrap();
        heleos_core::apply_private_permissions(temporary.path()).unwrap();
        let root = std::fs::canonicalize(temporary.path()).unwrap();
        let mut store = Store::open_writer(root.join("source.sqlite3")).unwrap();
        store.migrate().unwrap();
        let vault = Vault::open(heleos_core::VaultConfig {
            root: root.join("source-vault"),
            open_mode: heleos_core::VaultOpenMode::CreateNew,
        })
        .unwrap();
        Self {
            _root: temporary,
            root,
            store,
            vault,
            identity: age::x25519::Identity::generate(),
            signer: ed25519_dalek::SigningKey::from_bytes(&[42; 32]),
        }
    }

    fn create(&mut self) -> BackupReceipt {
        BackupService::create(
            &mut self.store,
            &self.vault,
            &self.root.join("backup.age"),
            &self.identity.to_public(),
            &self.signer,
        )
        .unwrap()
    }

    fn open(&self) -> File {
        let mut options = std::fs::OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW);
        }
        options.open(self.root.join("backup.age")).unwrap()
    }

    fn plaintext(&self) -> Vec<u8> {
        let decryptor = age::Decryptor::new(self.open()).unwrap();
        let mut reader = decryptor
            .decrypt(std::iter::once(&self.identity as &dyn age::Identity))
            .unwrap();
        let mut bytes = Vec::new();
        reader.read_to_end(&mut bytes).unwrap();
        bytes
    }

    fn replace_plaintext(&self, bytes: &[u8]) {
        let recipient = self.identity.to_public();
        let encryptor =
            age::Encryptor::with_recipients(std::iter::once(&recipient as &dyn age::Recipient))
                .unwrap();
        let mut output = Vec::new();
        let mut writer = encryptor.wrap_output(&mut output).unwrap();
        writer.write_all(bytes).unwrap();
        writer.finish().unwrap();
        std::fs::write(self.root.join("backup.age"), output).unwrap();
    }

    fn resign_table(&self, bytes: &[u8], change: impl FnOnce(&mut [u8])) -> Vec<u8> {
        use ed25519_dalek::Signer;
        let manifest_length = u64::from_be_bytes(bytes[8..16].try_into().unwrap()) as usize;
        let manifest_end = 16 + manifest_length;
        let table_start = manifest_end + 32 + 64 + 8;
        let table_length =
            u64::from_be_bytes(bytes[table_start - 8..table_start].try_into().unwrap()) as usize;
        let mut table = bytes[table_start..table_start + table_length].to_vec();
        change(&mut table);
        let mut manifest: serde_json::Value =
            serde_json::from_slice(&bytes[16..manifest_end]).unwrap();
        manifest["entry_table_sha256"] =
            serde_json::to_value(Sha256Digest::hash_reader(table.as_slice()).unwrap()).unwrap();
        let manifest = serde_jcs::to_vec(&manifest).unwrap();
        let mut signature_input = b"heleos-backup-v1\0".to_vec();
        signature_input.extend_from_slice(&manifest);
        let signature = self.signer.sign(&signature_input);
        let mut result = b"HELEOSB1".to_vec();
        result.extend_from_slice(&(manifest.len() as u64).to_be_bytes());
        result.extend_from_slice(&manifest);
        result.extend_from_slice(&self.signer.verifying_key().to_bytes());
        result.extend_from_slice(&signature.to_bytes());
        result.extend_from_slice(&(table.len() as u64).to_be_bytes());
        result.extend_from_slice(&table);
        result.extend_from_slice(&bytes[table_start + table_length..]);
        result
    }

    fn ingest_projects(&mut self) -> Vec<heleos_core::RevisionId> {
        use heleos_core::{
            ActorId, DataClass, IdempotencyKey, IngestEngine, IngestRequest, IntakeSource,
            PdfLimits, ProjectCreateRequest, ProjectId, ProjectService,
        };
        let mut revisions = Vec::new();
        for project in [20_u128, 10] {
            let project_id = ProjectId::from_uuid(uuid::Uuid::from_u128(project));
            ProjectService::new(&mut self.store, &BackupTestClock, &BackupTestIds)
                .unwrap()
                .create(ProjectCreateRequest {
                    project_id: Some(project_id),
                    name: format!("Project {project}"),
                    actor: "backup-test".parse::<ActorId>().unwrap(),
                    data_class: DataClass::Internal,
                })
                .unwrap();
            for revision in 0..2 {
                let path = self.root.join(format!("{project}-{revision}.pdf"));
                std::fs::write(&path, format!("%PDF-1.7\n{project}-{revision}\n%%EOF\n")).unwrap();
                let receipt = IngestEngine::new(
                    &mut self.store,
                    &self.vault,
                    &BackupTestProbe,
                    &BackupTestClock,
                    &BackupTestIds,
                )
                .unwrap()
                .ingest(IngestRequest {
                    project_id,
                    source: IntakeSource::open(&path).unwrap(),
                    idempotency_key: IdempotencyKey::try_from(
                        format!("{project}-{revision}").as_str(),
                    )
                    .unwrap(),
                    actor: "backup-test".parse().unwrap(),
                    pdf_limits: PdfLimits::default(),
                })
                .unwrap();
                revisions.push(receipt.evidence_manifest.unwrap().manifest.revision_id);
            }
        }
        revisions
    }
}

#[test]
fn public_backup_round_trip_has_path_free_receipts_and_complete_layout() {
    let mut fixture = Fixture::new();
    let revisions = fixture.ingest_projects();
    let created = fixture.create();
    inspect_backup_receipt(&created);
    let verified = BackupService::verify_container(
        fixture.open(),
        &fixture.identity,
        &fixture.signer.verifying_key().to_bytes(),
    )
    .unwrap();
    inspect_verification_report(&verified);
    assert_eq!(created.container, verified.container);
    assert_eq!(
        created.ciphertext_byte_length,
        verified.ciphertext_byte_length
    );
    let destination = fixture.root.join("restored");
    let restored = BackupService::restore(
        fixture.open(),
        &fixture.identity,
        &fixture.signer.verifying_key().to_bytes(),
        &destination,
    )
    .unwrap();
    inspect_restore_receipt(&restored);
    assert_eq!(restored.verification, verified);
    let names = |path: &Path| {
        let mut entries = std::fs::read_dir(path)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().into_string().unwrap())
            .collect::<Vec<_>>();
        entries.sort();
        entries
    };
    assert_eq!(
        names(&destination),
        [
            "foundation.sqlite3",
            "foundation.sqlite3.writer.lock",
            "vault"
        ]
    );
    assert_eq!(
        names(&destination.join("vault")),
        [".staging", ".vault.lock", "objects"]
    );
    assert!(destination.join("vault/objects/sha256").is_dir());
    assert!(names(&destination.join("vault/.staging")).is_empty());
    assert!(verified.checked_audit_event_count > 2);
    let restored_store = Store::open_read_only(destination.join("foundation.sqlite3")).unwrap();
    let restored_vault = Vault::open(heleos_core::VaultConfig {
        root: destination.join("vault"),
        open_mode: heleos_core::VaultOpenMode::ExistingOnly,
    })
    .unwrap();
    let reader = heleos_core::FoundationReader::new(&restored_store, &restored_vault);
    for revision in revisions {
        reader.evidence_manifest_for_revision(revision).unwrap();
    }
    let json = serde_json::to_value(&restored).unwrap();
    assert_eq!(json.as_object().unwrap().len(), 2);
    assert_eq!(json["verification"].as_object().unwrap().len(), 5);
    assert_eq!(
        json["verification"]["container"].as_object().unwrap().len(),
        8
    );
    assert!(
        !serde_json::to_string(&json)
            .unwrap()
            .contains(fixture.root.to_str().unwrap())
    );
}

#[test]
fn authenticated_evidence_disagreement_in_later_project_is_rejected() {
    let mut fixture = Fixture::new();
    fixture.ingest_projects();
    let database = rusqlite::Connection::open(fixture.root.join("source.sqlite3")).unwrap();
    let (trigger_name, trigger_sql): (String, String) = database.query_row("SELECT name, sql FROM sqlite_schema WHERE type = 'trigger' AND tbl_name = 'sheets' AND sql LIKE '%BEFORE UPDATE%'", [], |row| Ok((row.get(0)?, row.get(1)?))).unwrap();
    assert!(
        trigger_name
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || character == '_')
    );
    database
        .execute_batch(&format!("DROP TRIGGER {trigger_name}"))
        .unwrap();
    database.execute("UPDATE sheets SET width_micropoints = width_micropoints + 1 WHERE id = (SELECT id FROM sheets ORDER BY id DESC LIMIT 1)", []).unwrap();
    database.execute_batch(&trigger_sql).unwrap();
    drop(database);
    fixture.create();
    assert!(matches!(
        BackupService::verify_container(
            fixture.open(),
            &fixture.identity,
            &fixture.signer.verifying_key().to_bytes()
        ),
        Err(heleos_core::HeleosError::Integrity)
    ));
    let destination = fixture.root.join("rejected");
    assert!(matches!(
        BackupService::restore(
            fixture.open(),
            &fixture.identity,
            &fixture.signer.verifying_key().to_bytes(),
            &destination
        ),
        Err(heleos_core::HeleosError::Integrity)
    ));
    assert!(!destination.exists());
}

#[test]
fn verify_container_rewinds_preseeked_ciphertext() {
    let mut fixture = Fixture::new();
    fixture.create();
    let mut file = fixture.open();
    assert!(file.seek(SeekFrom::End(0)).unwrap() > 0);
    BackupService::verify_container(
        file,
        &fixture.identity,
        &fixture.signer.verifying_key().to_bytes(),
    )
    .unwrap();
}

#[test]
fn restore_rewinds_preseeked_ciphertext() {
    let mut fixture = Fixture::new();
    fixture.create();
    let mut file = fixture.open();
    assert!(file.seek(SeekFrom::End(0)).unwrap() > 0);
    BackupService::restore(
        file,
        &fixture.identity,
        &fixture.signer.verifying_key().to_bytes(),
        &fixture.root.join("restored"),
    )
    .unwrap();
}

#[test]
fn wrong_identity_truncated_stream_and_untrusted_signer_are_distinct() {
    let mut fixture = Fixture::new();
    fixture.create();
    let trusted = fixture.signer.verifying_key().to_bytes();
    assert!(matches!(
        BackupService::verify_container(
            fixture.open(),
            &age::x25519::Identity::generate(),
            &trusted
        ),
        Err(heleos_core::HeleosError::BackupDecryption)
    ));
    assert!(matches!(
        BackupService::verify_container(fixture.open(), &fixture.identity, &[0; 32]),
        Err(heleos_core::HeleosError::BackupIntegrity)
    ));
    let bytes = std::fs::read(fixture.root.join("backup.age")).unwrap();
    std::fs::write(fixture.root.join("backup.age"), &bytes[..bytes.len() - 1]).unwrap();
    assert!(matches!(
        BackupService::verify_container(fixture.open(), &fixture.identity, &trusted),
        Err(heleos_core::HeleosError::BackupDecryption)
    ));
}

#[test]
fn existing_file_and_directory_destinations_are_preserved() {
    let mut fixture = Fixture::new();
    fixture.create();
    let before = std::fs::read(fixture.root.join("backup.age")).unwrap();
    assert!(
        BackupService::create(
            &mut fixture.store,
            &fixture.vault,
            &fixture.root.join("backup.age"),
            &fixture.identity.to_public(),
            &fixture.signer
        )
        .is_err()
    );
    assert_eq!(
        std::fs::read(fixture.root.join("backup.age")).unwrap(),
        before
    );
    let destination = fixture.root.join("existing");
    std::fs::create_dir(&destination).unwrap();
    std::fs::write(destination.join("sentinel"), b"preserve").unwrap();
    assert!(
        BackupService::restore(
            fixture.open(),
            &fixture.identity,
            &fixture.signer.verifying_key().to_bytes(),
            &destination
        )
        .is_err()
    );
    assert_eq!(
        std::fs::read(destination.join("sentinel")).unwrap(),
        b"preserve"
    );
}

#[test]
fn authenticated_container_grammar_signature_payload_and_entry_order_are_checked() {
    let mut fixture = Fixture::new();
    fixture.ingest_projects();
    fixture.create();
    let baseline = fixture.plaintext();
    let signer = fixture.signer.verifying_key().to_bytes();
    let mut malformed = baseline.clone();
    malformed[0] = b'X';
    fixture.replace_plaintext(&malformed);
    assert!(matches!(
        BackupService::verify_container(fixture.open(), &fixture.identity, &signer),
        Err(heleos_core::HeleosError::InvalidBackupContainer)
    ));
    let mut signature = baseline.clone();
    let manifest_length = u64::from_be_bytes(signature[8..16].try_into().unwrap()) as usize;
    signature[16 + manifest_length + 32] ^= 1;
    fixture.replace_plaintext(&signature);
    assert!(matches!(
        BackupService::verify_container(fixture.open(), &fixture.identity, &signer),
        Err(heleos_core::HeleosError::BackupIntegrity)
    ));
    let mut payload = baseline.clone();
    *payload.last_mut().unwrap() ^= 1;
    fixture.replace_plaintext(&payload);
    assert!(matches!(
        BackupService::verify_container(fixture.open(), &fixture.identity, &signer),
        Err(heleos_core::HeleosError::BackupIntegrity)
    ));
    for case in 0..3 {
        let malformed = fixture.resign_table(&baseline, |table| match case {
            0 => table[41] = 0,
            1 => {
                let first = table[41..82].to_vec();
                table[82..123].copy_from_slice(&first);
            }
            _ => {
                for index in 0..41 {
                    table.swap(41 + index, 82 + index);
                }
            }
        });
        fixture.replace_plaintext(&malformed);
        assert!(
            matches!(
                BackupService::verify_container(fixture.open(), &fixture.identity, &signer),
                Err(heleos_core::HeleosError::InvalidBackupContainer)
            ),
            "entry case {case}"
        );
    }
}

#[test]
fn concurrent_public_file_and_directory_publication_have_one_winner() {
    let mut fixture = Fixture::new();
    fixture.create();
    let file_destination = fixture.root.join("race.age");
    let file_results = std::thread::scope(|scope| {
        let workers = (0..2)
            .map(|_| {
                let destination = &file_destination;
                scope.spawn(move || {
                    let mut source = Fixture::new();
                    BackupService::create(
                        &mut source.store,
                        &source.vault,
                        destination,
                        &source.identity.to_public(),
                        &source.signer,
                    )
                })
            })
            .collect::<Vec<_>>();
        workers
            .into_iter()
            .map(|worker| worker.join().unwrap())
            .collect::<Vec<_>>()
    });
    assert_eq!(
        file_results.iter().filter(|result| result.is_ok()).count(),
        1
    );
    let directory_destination = fixture.root.join("race-restore");
    let files = [fixture.open(), fixture.open()];
    let results = std::thread::scope(|scope| {
        let workers = files
            .into_iter()
            .map(|file| {
                let identity = &fixture.identity;
                let signer = fixture.signer.verifying_key().to_bytes();
                let destination = &directory_destination;
                scope.spawn(move || BackupService::restore(file, identity, &signer, destination))
            })
            .collect::<Vec<_>>();
        workers
            .into_iter()
            .map(|worker| worker.join().unwrap())
            .collect::<Vec<_>>()
    });
    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert!(directory_destination.join("foundation.sqlite3").is_file());
    assert!(directory_destination.join("vault/.staging").is_dir());
}

#[test]
fn non_eof_age_source_io_preserves_the_underlying_error_kind() {
    let mut fixture = Fixture::new();
    fixture.create();
    let mut write_only = std::fs::OpenOptions::new()
        .write(true)
        .open(fixture.root.join("backup.age"))
        .unwrap();
    let expected_kind = write_only.read(&mut [0_u8; 1]).unwrap_err().kind();
    assert_ne!(expected_kind, std::io::ErrorKind::UnexpectedEof);
    assert!(
        matches!(BackupService::verify_container(write_only, &fixture.identity, &fixture.signer.verifying_key().to_bytes()), Err(heleos_core::HeleosError::Io(error)) if error.kind() == expected_kind)
    );
}

#[test]
fn declared_container_caps_are_invalid_at_public_verify_and_restore_boundaries() {
    let mut fixture = Fixture::new();
    fixture.create();
    let plaintext = fixture.plaintext();
    let manifest_length = u64::from_be_bytes(plaintext[8..16].try_into().unwrap()) as usize;

    let mut oversized_manifest_claim = b"HELEOSB1".to_vec();
    oversized_manifest_claim.extend_from_slice(&(16 * 1024 * 1024 + 1_u64).to_be_bytes());
    let mut oversized_table_claim = plaintext;
    let table_length_offset = 16 + manifest_length + 32 + 64;
    oversized_table_claim[table_length_offset..table_length_offset + 8]
        .copy_from_slice(&(80 * 1024 * 1024 + 1_u64).to_be_bytes());

    let signer = fixture.signer.verifying_key().to_bytes();
    let mut observed = Vec::new();
    for (label, bytes) in [
        ("manifest", oversized_manifest_claim),
        ("entry-table", oversized_table_claim),
    ] {
        assert!(
            bytes.len() < 1024 * 1024,
            "claims require no large allocation"
        );
        fixture.replace_plaintext(&bytes);
        let verify =
            BackupService::verify_container(fixture.open(), &fixture.identity, &signer).map(|_| ());
        let destination = fixture.root.join(format!("rejected-{label}"));
        let restore =
            BackupService::restore(fixture.open(), &fixture.identity, &signer, &destination)
                .map(|_| ());
        assert!(
            !destination.exists(),
            "rejected input must not publish a tree"
        );
        observed.push((label, verify, restore));
    }
    assert!(
        observed.iter().all(|(_, verify, restore)| {
            matches!(
                verify,
                Err(heleos_core::HeleosError::InvalidBackupContainer)
            ) && matches!(
                restore,
                Err(heleos_core::HeleosError::InvalidBackupContainer)
            )
        }),
        "declared-cap boundary classifications: {observed:?}"
    );
}
