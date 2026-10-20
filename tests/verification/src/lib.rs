#![forbid(unsafe_code)]
//! Test-only consumers of the public Foundation API; no production package depends on this crate.

pub mod hostile_backup;

use std::{
    cell::Cell,
    fs::{self, File},
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::mpsc,
    time::{Duration, Instant},
};

use heleos_core::{
    Clock, DataClass, IdGenerator, IngestEngine, IngestReceipt, IngestRequest, IntakeSource,
    PageMetadata, PageTransform, PageUnit, PdfInspection, PdfLimits, PdfProbe, PdfProbeOutcome,
    PdfProbeProvenance, ProjectCreateRequest, ProjectId, ProjectService, PutOutcome, Result,
    RevisionId, Sha256Digest, Store, StoredObject, Vault, VaultConfig, VaultOpenMode,
    VaultWriteBudget, VerifiedObject,
};

/// Private disposable filesystem and ephemeral keys for verifier fixtures only.
pub struct Sandbox {
    _temporary: tempfile::TempDir,
    pub root: PathBuf,
    pub identity: age::x25519::Identity,
    pub signer: ed25519_dalek::SigningKey,
    ids: SequentialIds,
}

impl Default for Sandbox {
    fn default() -> Self {
        Self::new()
    }
}

impl Sandbox {
    pub fn new() -> Self {
        let temporary = tempfile::tempdir().unwrap();
        heleos_core::apply_private_permissions(temporary.path()).unwrap();
        let root = fs::canonicalize(temporary.path()).unwrap();
        Self {
            _temporary: temporary,
            root,
            identity: age::x25519::Identity::generate(),
            signer: ed25519_dalek::SigningKey::from_bytes(&[0x28; 32]),
            ids: SequentialIds(Cell::new(1)),
        }
    }

    pub fn trusted_signer(&self) -> [u8; 32] {
        self.signer.verifying_key().to_bytes()
    }

    pub fn store(&self, name: &str) -> Store {
        let mut store = Store::open_writer(self.root.join(name)).unwrap();
        store.migrate().unwrap();
        store
    }

    pub fn vault(&self, name: &str) -> Vault {
        Vault::open(VaultConfig {
            root: self.root.join(name),
            open_mode: VaultOpenMode::CreateNew,
        })
        .unwrap()
    }

    pub fn open_vault(&self, path: &Path) -> Vault {
        Vault::open(VaultConfig {
            root: path.to_owned(),
            open_mode: VaultOpenMode::ExistingOnly,
        })
        .unwrap()
    }

    pub fn project(&self, store: &mut Store, id: ProjectId, name: &str) {
        let receipt = ProjectService::new(store, &FixedClock, &self.ids)
            .unwrap()
            .create(ProjectCreateRequest {
                project_id: Some(id),
                name: name.to_owned(),
                actor: "verifier".parse().unwrap(),
                data_class: DataClass::Public,
            })
            .unwrap();
        assert_eq!(receipt.project_id, id);
    }

    pub fn ingest(
        &self,
        store: &mut Store,
        vault: &Vault,
        project: ProjectId,
        name: &str,
        bytes: &[u8],
        key: &str,
    ) -> Result<IngestReceipt> {
        let path = self.root.join(name);
        fs::write(&path, bytes).unwrap();
        heleos_core::apply_private_permissions(&path).unwrap();
        IngestEngine::new(store, vault, &StorageProbe, &FixedClock, &self.ids)?.ingest(
            IngestRequest {
                project_id: project,
                source: IntakeSource::open(&path)?,
                idempotency_key: key.parse().unwrap(),
                actor: "verifier".parse().unwrap(),
                pdf_limits: PdfLimits::default(),
            },
        )
    }

    pub fn encrypt(&self, name: &str, plaintext: &[u8]) -> PathBuf {
        let path = self.root.join(name);
        let file = File::create(&path).unwrap();
        heleos_core::apply_private_permissions(&path).unwrap();
        let recipient = self.identity.to_public();
        let encryptor =
            age::Encryptor::with_recipients(std::iter::once(&recipient as &dyn age::Recipient))
                .unwrap();
        let mut output = encryptor.wrap_output(file).unwrap();
        output.write_all(plaintext).unwrap();
        output.finish().unwrap().sync_all().unwrap();
        path
    }

    pub fn assert_no_backup_staging(&self) {
        for entry in fs::read_dir(&self.root).unwrap() {
            let name = entry.unwrap().file_name();
            assert!(
                !name.to_string_lossy().starts_with(".heleos-"),
                "left staging entry: {name:?}"
            );
        }
    }
}

pub fn put(vault: &Vault, bytes: &[u8]) -> StoredObject {
    match vault
        .put_reader(
            bytes,
            VaultWriteBudget::new(1024 * 1024, 1024 * 1024).unwrap(),
        )
        .unwrap()
    {
        PutOutcome::Stored(object) => object,
        PutOutcome::QuotaRejected { .. } => panic!("small verifier object exceeded budget"),
    }
}

struct FixedClock;
impl Clock for FixedClock {
    fn now_unix_ms(&self) -> i64 {
        1_700_000_000_000
    }
}

struct SequentialIds(Cell<u128>);
impl IdGenerator for SequentialIds {
    fn next_uuid(&self) -> uuid::Uuid {
        let next = self.0.get();
        self.0.set(next.checked_add(1).unwrap());
        uuid::Uuid::from_u128(0x0000_0000_0000_4000_8000_0000_0000_0000 | next)
    }
}

/// This storage fixture supplies known page metadata; it does not test PDF parsing.
struct StorageProbe;
impl PdfProbe for StorageProbe {
    fn provenance(&self) -> Result<PdfProbeProvenance> {
        Ok(PdfProbeProvenance {
            parser_name: "independent-storage-fixture".to_owned(),
            parser_version: "1.0.0".to_owned(),
            guest_wasm_sha256: Sha256Digest::from_bytes([1; 32]),
            guest_source_tree_sha256: Sha256Digest::from_bytes([2; 32]),
            guest_dependency_graph_sha256: Sha256Digest::from_bytes([3; 32]),
            protocol_version: "heleos.pdf-probe/v1".to_owned(),
        })
    }

    fn probe(
        &self,
        mut input: VerifiedObject,
        revision_id: RevisionId,
        _: PdfLimits,
    ) -> Result<PdfProbeOutcome> {
        let mut bytes = Vec::new();
        input.read_to_end(&mut bytes).unwrap();
        assert!(bytes.starts_with(b"%PDF-"));
        Ok(PdfProbeOutcome::Accepted(PdfInspection {
            revision_id,
            content_sha256: input.digest(),
            byte_length: input.byte_length(),
            provenance: self.provenance()?,
            pages: vec![PageMetadata {
                index: 0,
                page_id: heleos_core::page_id(input.digest(), 0),
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

/// An actual second process retains Store ownership until an explicit stdin release.
pub struct WriterProcess {
    child: Child,
}
impl WriterProcess {
    pub fn start(binary: &str, database: &Path) -> Self {
        let mut child = Command::new(binary)
            .arg(database)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .unwrap();
        let stdout = child.stdout.take().unwrap();
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let mut line = String::new();
            let result = BufReader::new(stdout).read_line(&mut line);
            let _ = sender.send((result, line));
        });
        let process = Self { child };
        let (read, line) = receiver
            .recv_timeout(Duration::from_secs(20))
            .expect("writer helper readiness deadline");
        read.unwrap();
        assert_eq!(line, "ready\n");
        process
    }

    pub fn release(mut self) {
        self.child
            .stdin
            .take()
            .unwrap()
            .write_all(b"release\n")
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(20);
        loop {
            if let Some(status) = self.child.try_wait().unwrap() {
                assert!(status.success());
                break;
            }
            assert!(Instant::now() < deadline, "writer helper release deadline");
            std::thread::sleep(Duration::from_millis(5));
        }
    }
}
impl Drop for WriterProcess {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}
