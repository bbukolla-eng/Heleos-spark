#![forbid(unsafe_code)]

use std::env;
#[cfg(any(windows, all(unix, not(target_os = "macos"))))]
use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Cursor, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdout, Command, Stdio};
use std::sync::{Arc, Barrier, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use heleos_core::{
    EncodedVaultPath, HeleosError, PutOutcome, ReconciliationFinding, Sha256Digest, StoredObject,
    Vault, VaultConfig, VaultInventory, VaultInventoryEntry, VaultOpenMode, VaultVerification,
    VaultWriteBudget, canonical_json,
};
use proptest::prelude::*;

const MIB: u64 = 1024 * 1024;
const GIB: u64 = 1024 * MIB;
const FOUNDATION_INPUT_CAP: u64 = 256 * MIB;
const FOUNDATION_STORE_CAP: u64 = 500 * GIB;

struct TestVault {
    parent: tempfile::TempDir,
    root: PathBuf,
    vault: Vault,
}

impl TestVault {
    fn new(label: &str) -> Self {
        let parent = tempfile::Builder::new()
            .prefix(label)
            .tempdir()
            .expect("create private vault parent");
        heleos_core::apply_private_permissions(parent.path()).expect("harden private vault parent");
        let parent_path = fs::canonicalize(parent.path()).expect("canonicalize vault parent");
        let root = parent_path.join("vault");
        let vault = Vault::open(VaultConfig {
            root: root.clone(),
            open_mode: VaultOpenMode::CreateNew,
        })
        .expect("create vault");
        Self {
            parent,
            root,
            vault,
        }
    }

    fn budget(input: u64, retain: u64) -> VaultWriteBudget {
        VaultWriteBudget::new(input, retain).expect("construct test write budget")
    }
}

fn stored(outcome: PutOutcome) -> StoredObject {
    match outcome {
        PutOutcome::Stored(stored) => stored,
        PutOutcome::QuotaRejected { .. } => panic!("expected stored object, got quota rejection"),
    }
}

fn digest(bytes: &[u8]) -> Sha256Digest {
    Sha256Digest::hash_reader(Cursor::new(bytes)).expect("hash test bytes")
}

fn final_path(root: &Path, digest: Sha256Digest) -> PathBuf {
    root.join(Vault::object_key(digest))
}

fn staging_entries(root: &Path) -> Vec<PathBuf> {
    let mut entries: Vec<_> = fs::read_dir(root.join(".staging"))
        .expect("read staging directory")
        .map(|entry| entry.expect("read staging entry").path())
        .collect();
    entries.sort();
    entries
}

fn canonical_object_files(root: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let sha_root = root.join("objects/sha256");
    for first in fs::read_dir(sha_root).expect("read first digest shard level") {
        let first = first.expect("read first digest shard");
        for second in fs::read_dir(first.path()).expect("read second digest shard level") {
            let second = second.expect("read second digest shard");
            for object in fs::read_dir(second.path()).expect("read final digest objects") {
                files.push(object.expect("read final object entry").path());
            }
        }
    }
    files.sort();
    files
}

fn assert_exact_objects(root: &Path, vault: &Vault, expected: &[(Sha256Digest, &[u8])]) {
    let mut expected_paths: Vec<_> = expected
        .iter()
        .map(|(digest, _)| final_path(root, *digest))
        .collect();
    expected_paths.sort();
    assert_eq!(canonical_object_files(root), expected_paths);
    assert!(staging_entries(root).is_empty());
    for (digest, bytes) in expected {
        let path = final_path(root, *digest);
        assert_eq!(
            fs::read(&path).expect("read canonical final object"),
            *bytes
        );
        heleos_core::verify_private_permissions(&path).expect("verify final object policy");
        assert!(matches!(
            vault.verify(*digest).expect("verify canonical object"),
            VaultVerification::Verified {
                digest: verified_digest,
                byte_length,
                ..
            } if verified_digest == *digest && byte_length == bytes.len() as u64
        ));
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            assert_eq!(fs::metadata(path).expect("read final metadata").nlink(), 1);
        }
    }
}

fn create_private_dir(path: &Path) {
    fs::create_dir(path).expect("create private directory");
    heleos_core::apply_private_permissions(path).expect("harden private directory");
}

fn create_private_file(path: &Path, bytes: &[u8]) {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path).expect("create private file");
    file.write_all(bytes).expect("write private file");
    file.sync_all().expect("sync private file");
    drop(file);
    heleos_core::apply_private_permissions(path).expect("harden private file");
}

#[cfg(unix)]
fn make_permission_violation(path: &Path) {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o644))
        .expect("broaden Unix fixture permissions");
}

#[cfg(windows)]
fn make_permission_violation(path: &Path) {
    use std::os::windows::fs::OpenOptionsExt;
    use windows_permissions::constants::{SeObjectType, SecurityInformation};
    use windows_permissions::wrappers::{
        ConvertStringSecurityDescriptorToSecurityDescriptor, GetSecurityDescriptorDacl,
        SetSecurityInfo,
    };

    const GENERIC_READ: u32 = 0x8000_0000;
    const READ_CONTROL: u32 = 0x0002_0000;
    const WRITE_DAC: u32 = 0x0004_0000;
    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const OPEN_REPARSE_POINT: u32 = 0x0020_0000;

    let mut file = OpenOptions::new()
        .access_mode(GENERIC_READ | READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(OPEN_REPARSE_POINT)
        .open(path)
        .expect("open retained Windows permission fixture handle");
    let descriptor = ConvertStringSecurityDescriptorToSecurityDescriptor("D:(A;;FA;;;WD)")
        .expect("parse broad Windows fixture DACL");
    let dacl = match GetSecurityDescriptorDacl(descriptor.as_ref())
        .expect("extract broad Windows fixture DACL")
    {
        Some(dacl) => dacl,
        None => panic!("broad fixture SDDL did not declare a DACL"),
    };
    SetSecurityInfo(
        &mut file,
        SeObjectType::SE_FILE_OBJECT,
        SecurityInformation::Dacl | SecurityInformation::UnprotectedDacl,
        None,
        None,
        Some(dacl),
        None,
    )
    .expect("install broad Windows fixture DACL");
}

#[derive(Clone)]
struct CountingReader {
    bytes: Arc<Vec<u8>>,
    offset: usize,
    consumed: Arc<Mutex<usize>>,
}

impl CountingReader {
    fn new(bytes: Vec<u8>) -> (Self, Arc<Mutex<usize>>) {
        let consumed = Arc::new(Mutex::new(0));
        (
            Self {
                bytes: Arc::new(bytes),
                offset: 0,
                consumed: Arc::clone(&consumed),
            },
            consumed,
        )
    }
}

impl Read for CountingReader {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        let remaining = &self.bytes[self.offset..];
        let read = remaining.len().min(buffer.len());
        buffer[..read].copy_from_slice(&remaining[..read]);
        self.offset += read;
        *self.consumed.lock().expect("lock consumed byte count") += read;
        Ok(read)
    }
}

struct FailingReader {
    emitted: bool,
}

struct PanicReader;

impl Read for PanicReader {
    fn read(&mut self, _: &mut [u8]) -> std::io::Result<usize> {
        panic!("injected reader panic")
    }
}

impl Read for FailingReader {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        if self.emitted {
            return Err(std::io::Error::other("injected reader failure"));
        }
        self.emitted = true;
        let bytes = b"partial-reader-bytes";
        buffer[..bytes.len()].copy_from_slice(bytes);
        Ok(bytes.len())
    }
}

#[test]
fn secure_open_modes_and_exact_lowercase_object_key_are_enforced() {
    let parent_guard = tempfile::Builder::new()
        .prefix("heleos-vault-open-modes-")
        .tempdir()
        .expect("create parent");
    heleos_core::apply_private_permissions(parent_guard.path()).expect("harden parent");
    let parent = fs::canonicalize(parent_guard.path()).expect("canonicalize parent");
    let root = parent.join("vault");

    assert!(
        Vault::open(VaultConfig {
            root: root.clone(),
            open_mode: VaultOpenMode::ExistingOnly,
        })
        .is_err()
    );

    let vault = Vault::open(VaultConfig {
        root: root.clone(),
        open_mode: VaultOpenMode::CreateNew,
    })
    .expect("create new vault");
    let abc = digest(b"abc");
    assert_eq!(
        abc.to_string(),
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    );
    assert_eq!(
        Vault::object_key(abc),
        "objects/sha256/ba/78/ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    );

    assert!(
        Vault::open(VaultConfig {
            root: root.clone(),
            open_mode: VaultOpenMode::CreateNew,
        })
        .is_err()
    );
    Vault::open(VaultConfig {
        root: root.clone(),
        open_mode: VaultOpenMode::ExistingOnly,
    })
    .expect("open existing vault");
    Vault::open(VaultConfig {
        root,
        open_mode: VaultOpenMode::CreateOrOpen,
    })
    .expect("create-or-open existing vault");

    Vault::open(VaultConfig {
        root: parent.join("created-by-create-or-open"),
        open_mode: VaultOpenMode::CreateOrOpen,
    })
    .expect("create-or-open absent vault");

    let simultaneous_root = parent.join("simultaneous-create-or-open");
    let barrier = Arc::new(Barrier::new(2));
    let creators: Vec<_> = (0..2)
        .map(|_| {
            let barrier = Arc::clone(&barrier);
            let root = simultaneous_root.clone();
            thread::spawn(move || {
                barrier.wait();
                Vault::open(VaultConfig {
                    root,
                    open_mode: VaultOpenMode::CreateOrOpen,
                })
            })
        })
        .collect();
    for creator in creators {
        creator
            .join()
            .expect("join simultaneous creator")
            .expect("simultaneous create-or-open succeeds");
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        for directory in [
            parent.join("vault"),
            parent.join("vault/objects"),
            parent.join("vault/objects/sha256"),
            parent.join("vault/.staging"),
        ] {
            assert_eq!(
                fs::metadata(directory)
                    .expect("read vault directory permissions")
                    .permissions()
                    .mode()
                    & 0o777,
                0o700
            );
        }
        assert_eq!(
            fs::metadata(parent.join("vault/.vault.lock"))
                .expect("read vault lock permissions")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
    drop(vault);
}

#[test]
fn invalid_or_untrusted_root_shapes_fail_closed() {
    assert!(matches!(
        Vault::open(VaultConfig {
            root: PathBuf::from("relative-vault"),
            open_mode: VaultOpenMode::CreateNew,
        }),
        Err(HeleosError::PolicyDenied)
    ));

    let parent_guard = tempfile::Builder::new()
        .prefix("heleos-vault-root-shape-")
        .tempdir()
        .expect("create parent");
    heleos_core::apply_private_permissions(parent_guard.path()).expect("harden parent");
    let parent = fs::canonicalize(parent_guard.path()).expect("canonicalize parent");
    let filesystem_root = parent
        .ancestors()
        .last()
        .expect("absolute path has a filesystem root")
        .to_owned();
    for invalid in [
        filesystem_root,
        parent.join("."),
        parent.join("vault").join(".."),
    ] {
        assert!(matches!(
            Vault::open(VaultConfig {
                root: invalid,
                open_mode: VaultOpenMode::CreateNew,
            }),
            Err(HeleosError::PolicyDenied)
        ));
    }
    assert!(
        Vault::open(VaultConfig {
            root: parent.join("absent-parent").join("vault"),
            open_mode: VaultOpenMode::CreateNew,
        })
        .is_err()
    );

    #[cfg(unix)]
    {
        use std::os::unix::fs::{PermissionsExt, symlink};

        let broad_parent = parent.join("broad-parent");
        fs::create_dir(&broad_parent).expect("create broad parent");
        fs::set_permissions(&broad_parent, fs::Permissions::from_mode(0o755))
            .expect("broaden parent");
        assert!(matches!(
            Vault::open(VaultConfig {
                root: broad_parent.join("vault"),
                open_mode: VaultOpenMode::CreateNew,
            }),
            Err(HeleosError::PolicyDenied)
        ));

        let real_parent = parent.join("real-parent");
        create_private_dir(&real_parent);
        let linked_parent = parent.join("linked-parent");
        symlink(&real_parent, &linked_parent).expect("create parent symlink");
        let linked_result = Vault::open(VaultConfig {
            root: linked_parent.join("vault"),
            open_mode: VaultOpenMode::CreateNew,
        });
        assert!(matches!(linked_result, Err(HeleosError::PolicyDenied)));
    }
}

#[test]
fn repeated_and_near_duplicate_writes_preserve_source_and_publish_once() {
    let test = TestVault::new("heleos-vault-repeat-");
    let source = test.parent.path().join("source.bin");
    fs::write(&source, b"foundation evidence").expect("write source fixture");
    let before = fs::read(&source).expect("read source before vault write");

    let first = stored(
        test.vault
            .put_reader(
                File::open(&source).expect("open source"),
                TestVault::budget(1024, 1024),
            )
            .expect("store source"),
    );
    let second = stored(
        test.vault
            .put_reader(Cursor::new(before.clone()), TestVault::budget(1024, 0))
            .expect("deduplicate with zero retain budget"),
    );
    let near = stored(
        test.vault
            .put_reader(
                Cursor::new(b"foundation evidencf"),
                TestVault::budget(1024, 1024),
            )
            .expect("store near duplicate"),
    );

    assert!(first.newly_published);
    assert!(!second.newly_published);
    assert_eq!(first.digest, second.digest);
    assert_eq!(first.vault_key, second.vault_key);
    assert_ne!(first.digest, near.digest);
    assert_eq!(
        fs::read(&source).expect("read source after vault write"),
        before
    );
}

#[test]
fn supplied_budgets_are_bounded_and_quota_rejections_retain_exact_evidence() {
    assert!(matches!(
        VaultWriteBudget::new(FOUNDATION_INPUT_CAP + 1, 1),
        Err(HeleosError::ResourceLimit)
    ));
    assert!(matches!(
        VaultWriteBudget::new(1, FOUNDATION_STORE_CAP + 1),
        Err(HeleosError::Quota)
    ));

    let empty = TestVault::new("heleos-vault-empty-zero-budget-");
    let empty_stored = stored(
        empty
            .vault
            .put_reader(
                Cursor::new([]),
                VaultWriteBudget::new(0, 0).expect("zero-byte budget"),
            )
            .expect("store a novel empty object within a zero-byte budget"),
    );
    assert_eq!(empty_stored.byte_length, 0);
    let empty_duplicate = stored(
        empty
            .vault
            .put_reader(
                Cursor::new([]),
                VaultWriteBudget::new(0, 0).expect("duplicate zero-byte budget"),
            )
            .expect("deduplicate an empty object with zero budget"),
    );
    assert!(!empty_duplicate.newly_published);
    assert_eq!(empty_duplicate.digest, empty_stored.digest);
    assert_exact_objects(&empty.root, &empty.vault, &[(empty_stored.digest, &[])]);

    let test = TestVault::new("heleos-vault-budget-");
    let payload = vec![0x5a; 32 * 1024];
    let expected = digest(&payload);
    let (reader, consumed) = CountingReader::new(payload.clone());
    let outcome = test
        .vault
        .put_reader(
            reader,
            VaultWriteBudget::new(payload.len() as u64, 0).expect("zero-retain budget"),
        )
        .expect("return bounded quota evidence");
    assert!(matches!(
        outcome,
        PutOutcome::QuotaRejected {
            digest,
            byte_length
        } if digest == expected && byte_length == payload.len() as u64
    ));
    assert_eq!(
        *consumed.lock().expect("read consumed byte count"),
        payload.len(),
        "quota rejection must continue bounded hashing to EOF"
    );
    assert!(!final_path(&test.root, expected).exists());
    assert!(staging_entries(&test.root).is_empty());
    assert!(canonical_object_files(&test.root).is_empty());

    let (reader, consumed) = CountingReader::new(vec![7; 1024]);
    assert!(matches!(
        test.vault.put_reader(
            reader,
            VaultWriteBudget::new(127, 1024).expect("tight input budget")
        ),
        Err(HeleosError::ResourceLimit)
    ));
    assert_eq!(
        *consumed.lock().expect("read consumed byte count"),
        128,
        "resource-limit detection must stop at max_input_bytes + 1"
    );
    assert!(staging_entries(&test.root).is_empty());
    assert!(canonical_object_files(&test.root).is_empty());

    assert!(matches!(
        test.vault.put_reader(
            FailingReader { emitted: false },
            VaultWriteBudget::new(1024, 1024).expect("failing-reader budget")
        ),
        Err(HeleosError::Io(_))
    ));
    assert!(staging_entries(&test.root).is_empty());
    assert!(canonical_object_files(&test.root).is_empty());
}

#[test]
fn a_panicking_reader_releases_the_os_lock_for_an_independent_vault_instance() {
    let test = TestVault::new("heleos-vault-reader-panic-");
    let second = Vault::open(VaultConfig {
        root: test.root.clone(),
        open_mode: VaultOpenMode::ExistingOnly,
    })
    .expect("open independent vault before panic");
    let panic = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let _ = test.vault.put_reader(
            PanicReader,
            VaultWriteBudget::new(1024, 1024).expect("panic-reader budget"),
        );
    }));
    assert!(panic.is_err());
    let stored = stored(
        second
            .put_reader(
                Cursor::new(b"after reader panic"),
                VaultWriteBudget::new(1024, 1024).expect("post-panic budget"),
            )
            .expect("independent instance proceeds after panic"),
    );
    assert_exact_objects(
        &test.root,
        &second,
        &[(stored.digest, b"after reader panic")],
    );
}

#[test]
fn concurrent_threads_and_separate_vault_instances_publish_one_equal_object() {
    let test = TestVault::new("heleos-vault-thread-race-");
    let root = test.root.clone();
    let vault = Arc::new(test.vault);
    let barrier = Arc::new(Barrier::new(8));
    let mut threads = Vec::new();
    for _ in 0..8 {
        let vault = Arc::clone(&vault);
        let barrier = Arc::clone(&barrier);
        threads.push(thread::spawn(move || {
            barrier.wait();
            stored(
                vault
                    .put_reader(
                        Cursor::new(b"thread-equal-evidence"),
                        VaultWriteBudget::new(1024, 1024).expect("thread budget"),
                    )
                    .expect("concurrent put"),
            )
        }));
    }
    let thread_results: Vec<_> = threads
        .into_iter()
        .map(|thread| thread.join().expect("join publisher"))
        .collect();
    assert_eq!(
        thread_results
            .iter()
            .filter(|stored| stored.newly_published)
            .count(),
        1
    );
    assert_exact_objects(
        &root,
        vault.as_ref(),
        &[(digest(b"thread-equal-evidence"), b"thread-equal-evidence")],
    );

    let first = Arc::new(
        Vault::open(VaultConfig {
            root: root.clone(),
            open_mode: VaultOpenMode::ExistingOnly,
        })
        .expect("open first independent instance"),
    );
    let second = Arc::new(
        Vault::open(VaultConfig {
            root: root.clone(),
            open_mode: VaultOpenMode::ExistingOnly,
        })
        .expect("open second independent instance"),
    );
    let barrier = Arc::new(Barrier::new(2));
    let publishers: Vec<_> = [first, second]
        .into_iter()
        .map(|vault| {
            let barrier = Arc::clone(&barrier);
            thread::spawn(move || {
                barrier.wait();
                stored(
                    vault
                        .put_reader(
                            Cursor::new(b"instance-equal-evidence"),
                            VaultWriteBudget::new(1024, 1024).expect("instance budget"),
                        )
                        .expect("independent-instance put"),
                )
            })
        })
        .collect();
    let results: Vec<_> = publishers
        .into_iter()
        .map(|publisher| publisher.join().expect("join instance publisher"))
        .collect();
    assert_eq!(
        results
            .iter()
            .filter(|stored| stored.newly_published)
            .count(),
        1
    );
    assert_exact_objects(
        &root,
        vault.as_ref(),
        &[
            (digest(b"thread-equal-evidence"), b"thread-equal-evidence"),
            (
                digest(b"instance-equal-evidence"),
                b"instance-equal-evidence",
            ),
        ],
    );
}

#[test]
fn independent_process_writer_helper() {
    let Some(root) = env::var_os("HELEOS_TEST_VAULT_PROCESS_ROOT") else {
        return;
    };
    let release = PathBuf::from(
        env::var_os("HELEOS_TEST_VAULT_PROCESS_RELEASE").expect("process release path"),
    );
    let vault = Vault::open(VaultConfig {
        root: PathBuf::from(root),
        open_mode: VaultOpenMode::ExistingOnly,
    })
    .expect("open process vault");
    println!("HELEOS_VAULT_PROCESS_READY");
    std::io::stdout().flush().expect("flush process readiness");
    let deadline = Instant::now() + Duration::from_secs(30);
    while !release.exists() {
        assert!(
            Instant::now() < deadline,
            "timed out waiting for process release marker"
        );
        thread::sleep(Duration::from_millis(1));
    }
    let result = stored(
        vault
            .put_reader(
                Cursor::new(b"process-equal-evidence"),
                VaultWriteBudget::new(1024, 1024).expect("process budget"),
            )
            .expect("process put"),
    );
    println!("HELEOS_VAULT_PROCESS_RESULT:{}", result.newly_published);
}

struct ProcessPublisher {
    child: Option<Child>,
    output: BufReader<ChildStdout>,
}

impl ProcessPublisher {
    fn wait_for_ready(&mut self) {
        for _ in 0..64 {
            let mut line = String::new();
            let read = self
                .output
                .read_line(&mut line)
                .expect("read publisher readiness output");
            assert_ne!(read, 0, "publisher exited before readiness marker");
            if line.contains("HELEOS_VAULT_PROCESS_READY") {
                return;
            }
        }
        panic!("publisher did not emit readiness marker within 64 lines");
    }

    fn finish(mut self) -> String {
        let mut output = String::new();
        self.output
            .read_to_string(&mut output)
            .expect("read publisher result");
        let mut child = self.child.take().expect("publisher child present");
        assert!(child.wait().expect("wait publisher").success());
        output
    }
}

impl Drop for ProcessPublisher {
    fn drop(&mut self) {
        if let Some(child) = &mut self.child {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn spawn_process_publisher(root: &Path, release: &Path) -> ProcessPublisher {
    let mut child = Command::new(env::current_exe().expect("locate vault test executable"))
        .arg("--exact")
        .arg("independent_process_writer_helper")
        .arg("--nocapture")
        .env("HELEOS_TEST_VAULT_PROCESS_ROOT", root)
        .env("HELEOS_TEST_VAULT_PROCESS_RELEASE", release)
        .stdout(Stdio::piped())
        .spawn()
        .expect("spawn vault publisher");
    let stdout = child.stdout.take().expect("capture publisher stdout");
    let mut publisher = ProcessPublisher {
        child: Some(child),
        output: BufReader::new(stdout),
    };
    publisher.wait_for_ready();
    publisher
}

#[test]
fn independent_processes_publish_one_equal_object_without_replacement() {
    let test = TestVault::new("heleos-vault-process-race-");
    let release = test.parent.path().join("release");
    let first = spawn_process_publisher(&test.root, &release);
    let second = spawn_process_publisher(&test.root, &release);
    fs::write(&release, b"release").expect("release process publishers");

    let results = [first.finish(), second.finish()];
    assert_eq!(
        results
            .iter()
            .filter(|output| output.contains("HELEOS_VAULT_PROCESS_RESULT:true"))
            .count(),
        1
    );
    assert_eq!(
        results
            .iter()
            .filter(|output| output.contains("HELEOS_VAULT_PROCESS_RESULT:false"))
            .count(),
        1
    );
    assert_exact_objects(
        &test.root,
        &test.vault,
        &[(digest(b"process-equal-evidence"), b"process-equal-evidence")],
    );
}

#[test]
fn verified_reads_stream_one_handle_from_byte_zero() {
    let test = TestVault::new("heleos-vault-verified-read-");
    let bytes = b"streamed verified evidence";
    let stored = stored(
        test.vault
            .put_reader(Cursor::new(bytes), TestVault::budget(1024, 1024))
            .expect("store verified-read fixture"),
    );
    let mut verified = test
        .vault
        .open_verified(stored.digest)
        .expect("open verified object");
    assert_eq!(verified.digest(), stored.digest);
    assert_eq!(verified.byte_length(), bytes.len() as u64);
    assert_eq!(verified.vault_key(), stored.vault_key);
    let mut read = Vec::new();
    verified
        .read_to_end(&mut read)
        .expect("stream verified bytes");
    assert_eq!(read, bytes);
    verified
        .seek(SeekFrom::Start(0))
        .expect("rewind verified object");
    let mut prefix = [0_u8; 8];
    verified
        .read_exact(&mut prefix)
        .expect("read verified prefix");
    assert_eq!(&prefix, b"streamed");
}

#[test]
fn verification_reports_corruption_nonregular_permissions_and_link_counts() {
    let corrupt = TestVault::new("heleos-vault-corrupt-");
    let stored_corrupt = stored(
        corrupt
            .vault
            .put_reader(Cursor::new(b"original"), TestVault::budget(1024, 1024))
            .expect("store corruption fixture"),
    );
    fs::write(
        final_path(&corrupt.root, stored_corrupt.digest),
        b"bit-flipped",
    )
    .expect("corrupt object");
    assert!(matches!(
        corrupt.vault.verify(stored_corrupt.digest).expect("report corruption"),
        VaultVerification::Corrupt {
            expected_digest,
            actual_digest: Some(_),
            actual_byte_length: Some(11),
            ..
        } if expected_digest == stored_corrupt.digest
    ));
    assert!(matches!(
        corrupt.vault.open_verified(stored_corrupt.digest),
        Err(HeleosError::Integrity)
    ));

    let truncated = TestVault::new("heleos-vault-truncated-");
    let stored_truncated = stored(
        truncated
            .vault
            .put_reader(Cursor::new(b"truncate-me"), TestVault::budget(1024, 1024))
            .expect("store truncation fixture"),
    );
    OpenOptions::new()
        .write(true)
        .open(final_path(&truncated.root, stored_truncated.digest))
        .expect("open object for truncation")
        .set_len(3)
        .expect("truncate object");
    assert!(matches!(
        truncated
            .vault
            .verify(stored_truncated.digest)
            .expect("report truncation"),
        VaultVerification::Corrupt {
            actual_byte_length: Some(3),
            ..
        }
    ));

    let linked = TestVault::new("heleos-vault-link-count-");
    let stored_linked = stored(
        linked
            .vault
            .put_reader(Cursor::new(b"linked"), TestVault::budget(1024, 1024))
            .expect("store link-count fixture"),
    );
    fs::hard_link(
        final_path(&linked.root, stored_linked.digest),
        linked.parent.path().join("hostile-alias"),
    )
    .expect("add hostile hardlink");
    assert!(matches!(
        linked.vault.verify(stored_linked.digest).expect("report link count"),
        VaultVerification::UnexpectedLinkCount {
            digest,
            link_count: 2,
            ..
        } if digest == stored_linked.digest
    ));

    let nonregular = TestVault::new("heleos-vault-nonregular-");
    let nonregular_digest = digest(b"directory-at-object-key");
    fs::create_dir_all(
        final_path(&nonregular.root, nonregular_digest)
            .parent()
            .expect("object parent"),
    )
    .expect("create object shards");
    let nonregular_hex = nonregular_digest.to_string();
    for shard in [
        nonregular
            .root
            .join("objects/sha256")
            .join(&nonregular_hex[..2]),
        nonregular
            .root
            .join("objects/sha256")
            .join(&nonregular_hex[..2])
            .join(&nonregular_hex[2..4]),
    ] {
        heleos_core::apply_private_permissions(&shard).expect("harden nonregular shard");
    }
    create_private_dir(&final_path(&nonregular.root, nonregular_digest));
    assert!(matches!(
        nonregular
            .vault
            .verify(nonregular_digest)
            .expect("report nonregular object"),
        VaultVerification::NonRegular { digest, .. } if digest == nonregular_digest
    ));

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        let permissions = TestVault::new("heleos-vault-permissions-");
        let stored_permissions = stored(
            permissions
                .vault
                .put_reader(Cursor::new(b"permissions"), TestVault::budget(1024, 1024))
                .expect("store permission fixture"),
        );
        fs::set_permissions(
            final_path(&permissions.root, stored_permissions.digest),
            fs::Permissions::from_mode(0o644),
        )
        .expect("broaden final permissions");
        assert!(matches!(
            permissions
                .vault
                .verify(stored_permissions.digest)
                .expect("report permission violation"),
            VaultVerification::PermissionViolation { digest, .. }
                if digest == stored_permissions.digest
        ));
    }
}

#[test]
fn sparse_over_cap_object_is_bounded_corrupt_and_blocks_write_preflight() {
    const OBJECT_CAP: u64 = 256 * 1024 * 1024;

    let test = TestVault::new("heleos-vault-over-cap-object-");
    let over_cap_digest = digest(b"sparse over-cap fixture identity");
    let over_cap_path = final_path(&test.root, over_cap_digest);
    fs::create_dir_all(over_cap_path.parent().expect("over-cap parent"))
        .expect("create over-cap shards");
    let hex = over_cap_digest.to_string();
    for shard in [
        test.root.join("objects/sha256").join(&hex[..2]),
        test.root
            .join("objects/sha256")
            .join(&hex[..2])
            .join(&hex[2..4]),
    ] {
        heleos_core::apply_private_permissions(&shard).expect("harden over-cap shard");
    }
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&over_cap_path)
        .expect("create sparse over-cap object");
    file.set_len(OBJECT_CAP + 1)
        .expect("size sparse over-cap object");
    drop(file);
    heleos_core::apply_private_permissions(&over_cap_path).expect("harden over-cap object");

    assert!(matches!(
        test.vault.verify(over_cap_digest).expect("classify over-cap object"),
        VaultVerification::Corrupt {
            expected_digest,
            actual_digest: None,
            actual_byte_length: Some(length),
            ..
        } if expected_digest == over_cap_digest && length == OBJECT_CAP + 1
    ));
    assert!(matches!(
        test.vault.open_verified(over_cap_digest),
        Err(HeleosError::Integrity)
    ));
    let report = test
        .vault
        .reconcile(
            &VaultInventory::try_from_entries([VaultInventoryEntry {
                digest: over_cap_digest,
                expected_byte_length: OBJECT_CAP + 1,
                vault_key: Vault::object_key(over_cap_digest),
            }])
            .expect("over-cap inventory"),
        )
        .expect("reconcile over-cap object");
    assert!(report.findings.iter().any(|finding| matches!(
        finding,
        ReconciliationFinding::CorruptObject {
            verification: VaultVerification::Corrupt {
                expected_digest,
                actual_digest: None,
                actual_byte_length: Some(length),
                ..
            }
        } if *expected_digest == over_cap_digest && *length == OBJECT_CAP + 1
    )));
    assert!(matches!(
        test.vault.put_reader(
            Cursor::new(b"must not write through over-cap state"),
            TestVault::budget(1024, 1024),
        ),
        Err(HeleosError::ResourceLimit)
    ));
    assert!(staging_entries(&test.root).is_empty());
}

#[cfg(unix)]
#[test]
fn verify_and_reconcile_classify_unix_socket_and_unreadable_file_without_data_open() {
    use std::os::unix::fs::PermissionsExt;
    use std::os::unix::net::UnixListener;

    let test = TestVault::new("heleos-vault-unix-classification-");
    let socket_digest = digest(b"socket candidate identity");
    let denied_digest = digest(b"denied candidate identity");
    for candidate in [socket_digest, denied_digest] {
        let hex = candidate.to_string();
        fs::create_dir_all(
            final_path(&test.root, candidate)
                .parent()
                .expect("candidate parent"),
        )
        .expect("create candidate shards");
        for shard in [
            test.root.join("objects/sha256").join(&hex[..2]),
            test.root
                .join("objects/sha256")
                .join(&hex[..2])
                .join(&hex[2..4]),
        ] {
            heleos_core::apply_private_permissions(&shard).expect("harden candidate shard");
        }
    }

    let socket_path = final_path(&test.root, socket_digest);
    let socket_source = test.parent.path().join("s");
    let socket = UnixListener::bind(&socket_source).expect("bind short Unix socket source");
    fs::hard_link(&socket_source, &socket_path).expect("link Unix socket into canonical key");
    fs::set_permissions(&socket_path, fs::Permissions::from_mode(0o600))
        .expect("set private socket mode");
    let denied_path = final_path(&test.root, denied_digest);
    create_private_file(&denied_path, b"unreadable candidate bytes");
    fs::set_permissions(&denied_path, fs::Permissions::from_mode(0o000))
        .expect("deny candidate data access");

    assert!(matches!(
        test.vault.verify(socket_digest).expect("classify socket"),
        VaultVerification::NonRegular { digest, .. } if digest == socket_digest
    ));
    assert!(matches!(
        test.vault.verify(denied_digest).expect("classify unreadable file"),
        VaultVerification::PermissionViolation { digest, .. } if digest == denied_digest
    ));
    let report = test
        .vault
        .reconcile(
            &VaultInventory::try_from_entries([
                VaultInventoryEntry {
                    digest: socket_digest,
                    expected_byte_length: 0,
                    vault_key: Vault::object_key(socket_digest),
                },
                VaultInventoryEntry {
                    digest: denied_digest,
                    expected_byte_length: 26,
                    vault_key: Vault::object_key(denied_digest),
                },
            ])
            .expect("classification inventory"),
        )
        .expect("reconcile classified candidates");
    assert!(report.findings.iter().any(|finding| matches!(
        finding,
        ReconciliationFinding::NonRegularEntry { path }
            if path.value == Vault::object_key(socket_digest)
    )));
    assert!(report.findings.iter().any(|finding| matches!(
        finding,
        ReconciliationFinding::PermissionViolation { path }
            if path.value == Vault::object_key(denied_digest)
    )));
    drop(socket);
}

#[cfg(unix)]
#[test]
fn intermediate_shards_preserve_permission_and_nonregular_typed_states() {
    use std::os::unix::fs::PermissionsExt;

    let denied = TestVault::new("heleos-vault-denied-shard-");
    let denied_digest = digest(b"permission-invalid intermediate shard");
    let denied_hex = denied_digest.to_string();
    let denied_shard = denied.root.join("objects/sha256").join(&denied_hex[..2]);
    create_private_dir(&denied_shard);
    fs::set_permissions(&denied_shard, fs::Permissions::from_mode(0o000))
        .expect("deny intermediate shard access");
    assert!(matches!(
        denied
            .vault
            .verify(denied_digest)
            .expect("classify denied shard"),
        VaultVerification::PermissionViolation {
            digest,
            byte_length: None,
            ..
        } if digest == denied_digest
    ));
    assert!(matches!(
        denied.vault.open_verified(denied_digest),
        Err(HeleosError::PolicyDenied)
    ));

    let nonregular = TestVault::new("heleos-vault-nonregular-shard-");
    let nonregular_digest = digest(b"nonregular intermediate shard");
    let nonregular_hex = nonregular_digest.to_string();
    create_private_file(
        &nonregular
            .root
            .join("objects/sha256")
            .join(&nonregular_hex[..2]),
        b"not a directory",
    );
    assert!(matches!(
        nonregular
            .vault
            .verify(nonregular_digest)
            .expect("classify nonregular shard"),
        VaultVerification::NonRegular { digest, .. } if digest == nonregular_digest
    ));
    assert!(matches!(
        nonregular.vault.open_verified(nonregular_digest),
        Err(HeleosError::PolicyDenied)
    ));
}

#[test]
fn hostile_existing_winner_and_symlinked_layout_are_never_mutated() {
    let wrong = TestVault::new("heleos-vault-wrong-winner-");
    let intended = b"intended immutable bytes";
    let intended_digest = digest(intended);
    let hostile_final = final_path(&wrong.root, intended_digest);
    fs::create_dir_all(hostile_final.parent().expect("winner parent"))
        .expect("create winner shards");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for path in [
            wrong
                .root
                .join("objects/sha256")
                .join(&intended_digest.to_string()[..2]),
            wrong
                .root
                .join("objects/sha256")
                .join(&intended_digest.to_string()[..2])
                .join(&intended_digest.to_string()[2..4]),
        ] {
            fs::set_permissions(path, fs::Permissions::from_mode(0o700))
                .expect("harden winner shard");
        }
    }
    create_private_file(&hostile_final, b"hostile existing bytes");
    assert!(
        wrong
            .vault
            .put_reader(Cursor::new(intended), TestVault::budget(1024, 1024))
            .is_err()
    );
    assert_eq!(
        fs::read(&hostile_final).expect("read hostile winner"),
        b"hostile existing bytes"
    );
    assert!(staging_entries(&wrong.root).is_empty());
    assert_eq!(canonical_object_files(&wrong.root), [hostile_final]);

    #[cfg(unix)]
    {
        use std::os::unix::fs::symlink;

        let parent_guard = tempfile::Builder::new()
            .prefix("heleos-vault-symlink-layout-")
            .tempdir()
            .expect("create symlink parent");
        heleos_core::apply_private_permissions(parent_guard.path()).expect("harden symlink parent");
        let parent = fs::canonicalize(parent_guard.path()).expect("canonicalize symlink parent");
        let root = parent.join("vault");
        create_private_dir(&root);
        let outside = parent.join("outside");
        create_private_dir(&outside);
        symlink(&outside, root.join("objects")).expect("install objects symlink");
        assert!(
            Vault::open(VaultConfig {
                root,
                open_mode: VaultOpenMode::ExistingOnly,
            })
            .is_err()
        );

        let final_link = TestVault::new("heleos-vault-final-symlink-");
        let link_digest = digest(b"symlink destination digest");
        let link_path = final_path(&final_link.root, link_digest);
        fs::create_dir_all(link_path.parent().expect("link parent")).expect("create link shards");
        let link_hex = link_digest.to_string();
        for shard in [
            final_link.root.join("objects/sha256").join(&link_hex[..2]),
            final_link
                .root
                .join("objects/sha256")
                .join(&link_hex[..2])
                .join(&link_hex[2..4]),
        ] {
            heleos_core::apply_private_permissions(&shard).expect("harden symlink shard");
        }
        let target = final_link.parent.path().join("outside-file");
        create_private_file(&target, b"outside");
        symlink(&target, &link_path).expect("install final symlink");
        assert!(matches!(
            final_link.vault.verify(link_digest),
            Ok(VaultVerification::NonRegular { .. }) | Err(HeleosError::PolicyDenied)
        ));
        assert_eq!(fs::read(target).expect("read symlink target"), b"outside");
    }
}

#[test]
fn reconciliation_reports_sorted_typed_findings_without_mutation() {
    let test = TestVault::new("heleos-vault-reconcile-");
    let stored = stored(
        test.vault
            .put_reader(Cursor::new(b"unreferenced"), TestVault::budget(1024, 1024))
            .expect("store unreferenced object"),
    );
    let partial_path = test.root.join(".staging/abandoned.partial");
    create_private_file(&partial_path, b"partial");
    let missing_digest = digest(b"missing-reference");
    let inventory = VaultInventory::try_from_entries([
        VaultInventoryEntry {
            digest: stored.digest,
            expected_byte_length: stored.byte_length + 1,
            vault_key: "objects/sha256/00/00/wrong".to_owned(),
        },
        VaultInventoryEntry {
            digest: missing_digest,
            expected_byte_length: 17,
            vault_key: Vault::object_key(missing_digest),
        },
    ])
    .expect("construct reconciliation inventory");
    let object_before =
        fs::read(final_path(&test.root, stored.digest)).expect("read object before");
    let partial_before = fs::read(&partial_path).expect("read partial before");

    let report = test.vault.reconcile(&inventory).expect("reconcile vault");
    assert!(matches!(
        report.findings.first(),
        Some(ReconciliationFinding::StagingPartial {
            path: EncodedVaultPath { encoding, value }
        }) if encoding == "utf8" && value == ".staging/abandoned.partial"
    ));
    assert!(report.findings.iter().any(|finding| matches!(
        finding,
        ReconciliationFinding::InventoryKeyMismatch { inventory, expected_vault_key }
            if inventory.digest == stored.digest
                && expected_vault_key == &Vault::object_key(stored.digest)
    )));
    assert!(report.findings.iter().any(|finding| matches!(
        finding,
        ReconciliationFinding::InventoryLengthMismatch {
            inventory,
            actual_byte_length
        } if inventory.digest == stored.digest && *actual_byte_length == stored.byte_length
    )));
    assert!(report.findings.iter().any(|finding| matches!(
        finding,
        ReconciliationFinding::MissingReference { inventory }
            if inventory.digest == missing_digest
    )));
    assert_eq!(
        fs::read(final_path(&test.root, stored.digest)).expect("read object after"),
        object_before
    );
    assert_eq!(
        fs::read(partial_path).expect("read partial after"),
        partial_before
    );
}

#[test]
fn reconciliation_covers_the_complete_typed_matrix_in_frozen_order() {
    let test = TestVault::new("heleos-vault-reconcile-matrix-");
    let orphan = stored(
        test.vault
            .put_reader(Cursor::new(b"orphan"), TestVault::budget(1024, 1024))
            .expect("store orphan"),
    );
    let referenced = stored(
        test.vault
            .put_reader(Cursor::new(b"referenced"), TestVault::budget(1024, 1024))
            .expect("store referenced object"),
    );
    let corrupt = stored(
        test.vault
            .put_reader(Cursor::new(b"corrupt"), TestVault::budget(1024, 1024))
            .expect("store corrupt fixture"),
    );
    let linked = stored(
        test.vault
            .put_reader(Cursor::new(b"linked-matrix"), TestVault::budget(1024, 1024))
            .expect("store link fixture"),
    );
    let permissions = stored(
        test.vault
            .put_reader(
                Cursor::new(b"permission-matrix"),
                TestVault::budget(1024, 1024),
            )
            .expect("store permission fixture"),
    );

    fs::write(final_path(&test.root, corrupt.digest), b"changed").expect("corrupt matrix object");
    fs::hard_link(
        final_path(&test.root, linked.digest),
        test.parent.path().join("matrix-hardlink"),
    )
    .expect("add matrix hardlink");
    make_permission_violation(&final_path(&test.root, permissions.digest));
    create_private_file(&test.root.join(".staging/matrix.partial"), b"partial");
    create_private_file(
        &test.root.join("objects/sha256/not-a-shard"),
        b"invalid layout",
    );
    let nonregular_digest = digest(b"matrix-nonregular");
    let nonregular_path = final_path(&test.root, nonregular_digest);
    fs::create_dir_all(nonregular_path.parent().expect("nonregular parent"))
        .expect("create nonregular shards");
    let hex = nonregular_digest.to_string();
    for shard in [
        test.root.join("objects/sha256").join(&hex[..2]),
        test.root
            .join("objects/sha256")
            .join(&hex[..2])
            .join(&hex[2..4]),
    ] {
        heleos_core::apply_private_permissions(&shard)
            .expect("harden nonregular shard with platform policy");
    }
    create_private_dir(&nonregular_path);

    let missing_digest = digest(b"matrix-missing");
    let entries = vec![
        VaultInventoryEntry {
            digest: referenced.digest,
            expected_byte_length: referenced.byte_length + 4,
            vault_key: "objects/sha256/00/00/wrong-matrix-key".to_owned(),
        },
        VaultInventoryEntry {
            digest: corrupt.digest,
            expected_byte_length: corrupt.byte_length,
            vault_key: Vault::object_key(corrupt.digest),
        },
        VaultInventoryEntry {
            digest: linked.digest,
            expected_byte_length: linked.byte_length,
            vault_key: Vault::object_key(linked.digest),
        },
        VaultInventoryEntry {
            digest: nonregular_digest,
            expected_byte_length: 0,
            vault_key: Vault::object_key(nonregular_digest),
        },
        VaultInventoryEntry {
            digest: missing_digest,
            expected_byte_length: 99,
            vault_key: Vault::object_key(missing_digest),
        },
        VaultInventoryEntry {
            digest: permissions.digest,
            expected_byte_length: permissions.byte_length,
            vault_key: Vault::object_key(permissions.digest),
        },
    ];
    let inventory = VaultInventory::try_from_entries(entries).expect("build matrix inventory");

    let report = test.vault.reconcile(&inventory).expect("reconcile matrix");
    let ordinals: Vec<_> = report
        .findings
        .iter()
        .map(|finding| match finding {
            ReconciliationFinding::StagingPartial { .. } => 0,
            ReconciliationFinding::UnreferencedObject { .. } => 1,
            ReconciliationFinding::CorruptObject { .. } => 2,
            ReconciliationFinding::NonRegularEntry { .. } => 3,
            ReconciliationFinding::PermissionViolation { .. } => 4,
            ReconciliationFinding::UnexpectedLinkCount { .. } => 5,
            ReconciliationFinding::InvalidLayout { .. } => 6,
            ReconciliationFinding::MissingReference { .. } => 7,
            ReconciliationFinding::InventoryKeyMismatch { .. } => 8,
            ReconciliationFinding::InventoryLengthMismatch { .. } => 9,
        })
        .collect();
    assert!(ordinals.windows(2).all(|pair| pair[0] <= pair[1]));
    for expected in 0..=9 {
        assert!(
            ordinals.contains(&expected),
            "missing finding ordinal {expected}"
        );
    }
    assert!(report.findings.iter().any(|finding| matches!(
        finding,
        ReconciliationFinding::UnreferencedObject {
            verification: VaultVerification::Verified { digest, .. }
        } if *digest == orphan.digest
    )));
    assert!(report.findings.iter().any(|finding| matches!(
        finding,
        ReconciliationFinding::MissingReference { inventory }
            if inventory.digest == missing_digest
    )));
    assert!(!final_path(&test.root, missing_digest).exists());

    let encoded = canonical_json(&report).expect("canonicalize complete matrix report");
    assert_eq!(
        encoded,
        br#"{"findings":[{"kind":"staging_partial","path":{"encoding":"utf8","value":".staging/matrix.partial"}},{"kind":"unreferenced_object","verification":{"byte_length":6,"digest":"88f6811ab5d8fc6d3177f9b7609ae0fcebfda187e5046b62d38bb539e88b74d7","kind":"verified","vault_key":"objects/sha256/88/f6/88f6811ab5d8fc6d3177f9b7609ae0fcebfda187e5046b62d38bb539e88b74d7"}},{"kind":"corrupt_object","verification":{"actual_byte_length":7,"actual_digest":"d67e2e944994496c8d8ec76eed0cf9f09679448d584b532bebf941852a37f5ed","expected_digest":"11d510e067d2cdcd7559bd86d27a2f4c20babd43670346b97af99b522c1f0075","kind":"corrupt","vault_key":"objects/sha256/11/d5/11d510e067d2cdcd7559bd86d27a2f4c20babd43670346b97af99b522c1f0075"}},{"kind":"non_regular_entry","path":{"encoding":"utf8","value":"objects/sha256/0e/59/0e59a2b9b90f47ecaade74100b7a1691fb98210cdab620c578db13ec3538e784"}},{"kind":"permission_violation","path":{"encoding":"utf8","value":"objects/sha256/92/f7/92f74c48e4af47d4c379d05f75f80e96486fcad3acff00f0db1e7814f89582c7"}},{"kind":"unexpected_link_count","link_count":2,"path":{"encoding":"utf8","value":"objects/sha256/b4/fb/b4fb2d0680d2e84ad91544af892918fa55f3d3676810ace22ade0fb4969ed0ec"}},{"kind":"invalid_layout","path":{"encoding":"utf8","value":"objects/sha256/not-a-shard"}},{"inventory":{"digest":"408fb088e7f73a791d32b3e07d49ba4bfc23f5116a443ce16f4580ed4e914e9b","expected_byte_length":99,"vault_key":"objects/sha256/40/8f/408fb088e7f73a791d32b3e07d49ba4bfc23f5116a443ce16f4580ed4e914e9b"},"kind":"missing_reference"},{"expected_vault_key":"objects/sha256/b3/10/b310be061b80cb749185ebc7022f46db5f9e675aad7cf3c7c87abedb2f01ef43","inventory":{"digest":"b310be061b80cb749185ebc7022f46db5f9e675aad7cf3c7c87abedb2f01ef43","expected_byte_length":14,"vault_key":"objects/sha256/00/00/wrong-matrix-key"},"kind":"inventory_key_mismatch"},{"actual_byte_length":10,"inventory":{"digest":"b310be061b80cb749185ebc7022f46db5f9e675aad7cf3c7c87abedb2f01ef43","expected_byte_length":14,"vault_key":"objects/sha256/00/00/wrong-matrix-key"},"kind":"inventory_length_mismatch"}]}"#
    );
    assert_eq!(
        fs::read(final_path(&test.root, orphan.digest)).expect("orphan remains"),
        b"orphan"
    );
    assert!(test.root.join(".staging/matrix.partial").exists());
    assert!(test.root.join("objects/sha256/not-a-shard").exists());
}

#[test]
fn reconciliation_canonical_json_and_duplicate_inventory_rules_are_stable() {
    let test = TestVault::new("heleos-vault-reconcile-json-");
    create_private_file(&test.root.join(".staging/abandoned.partial"), b"abandoned");
    let report = test
        .vault
        .reconcile(&VaultInventory::try_from_entries([]).expect("empty inventory"))
        .expect("reconcile abandoned staging");
    assert_eq!(
        canonical_json(&report).expect("canonicalize reconciliation report"),
        br#"{"findings":[{"kind":"staging_partial","path":{"encoding":"utf8","value":".staging/abandoned.partial"}}]}"#
    );

    let digest = digest(b"duplicate inventory");
    let entry = VaultInventoryEntry {
        digest,
        expected_byte_length: 19,
        vault_key: Vault::object_key(digest),
    };
    VaultInventory::try_from_entries([entry.clone(), entry]).expect("deduplicate exact rows");
    assert!(
        VaultInventory::try_from_entries([
            VaultInventoryEntry {
                digest,
                expected_byte_length: 19,
                vault_key: Vault::object_key(digest),
            },
            VaultInventoryEntry {
                digest,
                expected_byte_length: 20,
                vault_key: Vault::object_key(digest),
            },
        ])
        .is_err()
    );
}

#[test]
fn reconciliation_reports_unexpected_fixed_layout_children_without_mutation() {
    let test = TestVault::new("heleos-vault-reconcile-fixed-layout-");
    let root_rogue = test.root.join("rogue-root");
    let objects_rogue = test.root.join("objects/rogue-objects");
    create_private_file(&root_rogue, b"root rogue");
    create_private_file(&objects_rogue, b"objects rogue");

    let report = test
        .vault
        .reconcile(&VaultInventory::try_from_entries([]).expect("empty inventory"))
        .expect("reconcile unexpected fixed-layout children");
    assert_eq!(
        canonical_json(&report).expect("canonicalize fixed-layout findings"),
        br#"{"findings":[{"kind":"invalid_layout","path":{"encoding":"utf8","value":"objects/rogue-objects"}},{"kind":"invalid_layout","path":{"encoding":"utf8","value":"rogue-root"}}]}"#
    );
    assert_eq!(
        fs::read(root_rogue).expect("root rogue remains"),
        b"root rogue"
    );
    assert_eq!(
        fs::read(objects_rogue).expect("objects rogue remains"),
        b"objects rogue"
    );

    let rejected = b"must not publish through an invalid fixed layout";
    let rejected_digest = digest(rejected);
    assert!(matches!(
        test.vault
            .put_reader(Cursor::new(rejected), TestVault::budget(1024, 1024),),
        Err(HeleosError::PolicyDenied)
    ));
    assert!(!final_path(&test.root, rejected_digest).exists());
    assert!(staging_entries(&test.root).is_empty());
}

#[cfg(all(unix, not(target_os = "macos")))]
#[test]
fn reconciliation_encodes_hostile_unix_names_losslessly() {
    use std::os::unix::ffi::OsStringExt;

    let test = TestVault::new("heleos-vault-hostile-name-");
    let hostile = OsString::from_vec(vec![0xff, b'.', b'p', b'a', b'r', b't', b'i', b'a', b'l']);
    create_private_file(&test.root.join(".staging").join(hostile), b"hostile");
    let report = test
        .vault
        .reconcile(&VaultInventory::try_from_entries([]).expect("empty inventory"))
        .expect("reconcile hostile name");
    assert!(report.findings.iter().any(|finding| matches!(
        finding,
        ReconciliationFinding::StagingPartial {
            path: EncodedVaultPath { encoding, value }
        } if encoding == "unix_bytes_hex"
            && value == "2e73746167696e672fff2e7061727469616c"
    )));
}

#[cfg(unix)]
#[test]
fn reconciliation_preserves_literal_utf8_backslashes_without_path_collision() {
    let test = TestVault::new("heleos-vault-unix-backslash-");
    create_private_file(
        &test.root.join(".staging").join(r"a\b.partial"),
        b"backslash",
    );
    create_private_file(&test.root.join(".staging").join("a-b.partial"), b"hyphen");
    let report = test
        .vault
        .reconcile(&VaultInventory::try_from_entries([]).expect("empty inventory"))
        .expect("reconcile Unix backslash names");
    let staging_paths: Vec<_> = report
        .findings
        .iter()
        .filter_map(|finding| match finding {
            ReconciliationFinding::StagingPartial { path } => Some(path.value.as_str()),
            _ => None,
        })
        .collect();
    assert!(staging_paths.contains(&r".staging/a\b.partial"));
    assert!(staging_paths.contains(&".staging/a-b.partial"));
    assert_eq!(
        staging_paths
            .iter()
            .collect::<std::collections::BTreeSet<_>>()
            .len(),
        2
    );
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(16))]

    #[test]
    fn arbitrary_bytes_round_trip_hash_length_key_and_content(bytes in prop::collection::vec(any::<u8>(), 0..=64 * 1024)) {
        let test = TestVault::new("heleos-vault-property-");
        let expected = digest(&bytes);
        let stored = stored(test.vault.put_reader(
            Cursor::new(bytes.clone()),
            VaultWriteBudget::new(64 * 1024, 64 * 1024).expect("property budget"),
        ).expect("store property bytes"));
        prop_assert_eq!(stored.digest, expected);
        prop_assert_eq!(stored.byte_length, bytes.len() as u64);
        prop_assert_eq!(&stored.vault_key, &Vault::object_key(expected));
        let mut verified = test.vault.open_verified(expected).expect("open property object");
        let mut actual = Vec::new();
        verified.read_to_end(&mut actual).expect("read property object");
        prop_assert_eq!(actual, bytes);
    }
}

#[cfg(windows)]
mod windows_tests {
    use super::*;

    fn open_windows_dacl_fixture(path: &Path, is_directory: bool) -> File {
        use std::os::windows::fs::OpenOptionsExt;

        const GENERIC_READ: u32 = 0x8000_0000;
        const READ_CONTROL: u32 = 0x0002_0000;
        const WRITE_DAC: u32 = 0x0004_0000;
        const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
        const FILE_SHARE_READ: u32 = 0x0000_0001;
        const FILE_SHARE_WRITE: u32 = 0x0000_0002;
        const OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        const BACKUP_SEMANTICS: u32 = 0x0200_0000;

        OpenOptions::new()
            .access_mode(GENERIC_READ | READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(OPEN_REPARSE_POINT | if is_directory { BACKUP_SEMANTICS } else { 0 })
            .open(path)
            .expect("open retained Windows DACL fixture handle")
    }

    fn install_protected_windows_dacl(file: &mut File, sddl: &str) {
        use windows_permissions::constants::{SeObjectType, SecurityInformation};
        use windows_permissions::wrappers::{
            ConvertStringSecurityDescriptorToSecurityDescriptor, GetSecurityDescriptorDacl,
            SetSecurityInfo,
        };

        let descriptor = ConvertStringSecurityDescriptorToSecurityDescriptor(sddl)
            .expect("parse Windows fixture DACL");
        let dacl = match GetSecurityDescriptorDacl(descriptor.as_ref())
            .expect("extract Windows fixture DACL")
        {
            Some(dacl) => dacl,
            None => panic!("fixture SDDL did not declare a DACL"),
        };
        SetSecurityInfo(
            file,
            SeObjectType::SE_FILE_OBJECT,
            SecurityInformation::Dacl | SecurityInformation::ProtectedDacl,
            None,
            None,
            Some(dacl),
            None,
        )
        .expect("install protected Windows fixture DACL");
    }

    fn exact_windows_test_dacl() -> String {
        let process_sid = stellar_agent_windows_identity::current_user_sid_string()
            .expect("read process TokenUser SID");
        if process_sid == "S-1-5-18" {
            format!("D:P(A;;FA;;;{process_sid})")
        } else {
            format!("D:P(A;;FA;;;{process_sid})(A;;FA;;;S-1-5-18)")
        }
    }

    fn create_windows_junction(link: &Path, target: &Path) {
        let output = Command::new("cmd")
            .arg("/C")
            .arg("mklink")
            .arg("/J")
            .arg(link)
            .arg(target)
            .output()
            .expect("invoke mklink /J for native junction fixture");
        assert!(
            output.status.success(),
            "mklink /J failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    fn open_retained_cap_file(root: &Path, relative: &[&str]) -> cap_std::fs::File {
        use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt, OpenOptionsMaybeDirExt};
        use cap_std::fs::{Dir, OpenOptions as CapOpenOptions};
        use std::os::windows::fs::OpenOptionsExt as _;

        const GENERIC_READ: u32 = 0x8000_0000;
        const READ_CONTROL: u32 = 0x0002_0000;
        const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
        const FILE_SHARE_READ: u32 = 0x0000_0001;
        const FILE_SHARE_WRITE: u32 = 0x0000_0002;
        const OPEN_REPARSE: u32 = 0x0020_0000;
        const BACKUP_SEMANTICS: u32 = 0x0200_0000;

        let root_file = OpenOptions::new()
            .access_mode(GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(OPEN_REPARSE | BACKUP_SEMANTICS)
            .open(root)
            .expect("open retained Windows root capability");
        let mut directory = Dir::from_std_file(root_file);
        for component in &relative[..relative.len() - 1] {
            use cap_std::fs::OpenOptionsExt as _;

            let mut options = CapOpenOptions::new();
            options
                .read(true)
                .follow(FollowSymlinks::No)
                .maybe_dir(true)
                .access_mode(GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES)
                .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
                .custom_flags(OPEN_REPARSE | BACKUP_SEMANTICS);
            directory = Dir::from_std_file(
                directory
                    .open_with(component, &options)
                    .expect("open retained Windows directory component")
                    .into_std(),
            );
        }
        use cap_std::fs::OpenOptionsExt as _;
        let mut options = CapOpenOptions::new();
        options
            .read(true)
            .follow(FollowSymlinks::No)
            .access_mode(GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(OPEN_REPARSE);
        directory
            .open_with(relative[relative.len() - 1], &options)
            .expect("open retained Windows final component")
    }

    #[test]
    fn retained_root_lock_and_verified_final_handles_deny_delete_or_rebind() {
        let test = TestVault::new("heleos-vault-windows-no-delete-");
        let stored = stored(
            test.vault
                .put_reader(
                    Cursor::new(b"windows no-delete"),
                    TestVault::budget(1024, 1024),
                )
                .expect("store Windows no-delete fixture"),
        );
        let verified = test
            .vault
            .open_verified(stored.digest)
            .expect("retain final handle");
        let final_path = final_path(&test.root, stored.digest);
        let replacement_final = test.parent.path().join("replacement-final");
        let replacement_lock = test.parent.path().join("replacement-lock");
        create_private_file(&replacement_final, b"replacement final");
        create_private_file(&replacement_lock, b"replacement lock");
        assert!(fs::remove_file(&final_path).is_err());
        assert!(fs::rename(&final_path, test.root.join("moved-final")).is_err());
        assert!(fs::rename(&replacement_final, &final_path).is_err());
        assert!(fs::remove_file(test.root.join(".vault.lock")).is_err());
        assert!(fs::rename(test.root.join(".vault.lock"), test.root.join("moved.lock")).is_err());
        assert!(fs::rename(&replacement_lock, test.root.join(".vault.lock")).is_err());
        assert!(fs::rename(&test.root, test.parent.path().join("moved-vault")).is_err());
        assert!(
            fs::rename(
                test.parent.path(),
                test.parent.path().with_file_name("moved-vault-parent")
            )
            .is_err()
        );
        drop(verified);
        drop(test.vault);

        fs::remove_file(&final_path).expect("delete final after retained handles drop");
        fs::rename(&replacement_final, &final_path)
            .expect("replace final after retained handles drop");
        fs::remove_file(test.root.join(".vault.lock"))
            .expect("delete lock after retained handles drop");
        fs::rename(&replacement_lock, test.root.join(".vault.lock"))
            .expect("replace lock after retained handles drop");
        let moved_root = test.parent.path().join("moved-vault-after-drop");
        fs::rename(&test.root, &moved_root).expect("rename root after retained handles drop");
        assert!(moved_root.exists());
        let original_parent = test.parent.path().to_owned();
        let moved_parent = original_parent.with_extension("moved");
        fs::rename(&original_parent, &moved_parent)
            .expect("rename parent after retained handles drop");
        fs::rename(&moved_parent, &original_parent)
            .expect("restore test parent for recoverable cleanup");
    }

    #[test]
    fn windows_vault_layout_has_exact_private_dacls_and_single_link_finals() {
        let test = TestVault::new("heleos-vault-windows-dacl-");
        let stored = stored(
            test.vault
                .put_reader(Cursor::new(b"windows DACL"), TestVault::budget(1024, 1024))
                .expect("store Windows DACL fixture"),
        );
        for path in [
            test.root.clone(),
            test.root.join("objects"),
            test.root.join("objects/sha256"),
            test.root.join(".staging"),
            test.root.join(".vault.lock"),
            final_path(&test.root, stored.digest),
        ] {
            heleos_core::verify_private_permissions(&path).expect("verify exact Windows DACL");
        }
        let verified = test
            .vault
            .open_verified(stored.digest)
            .expect("open Windows final");
        assert!(matches!(
            test.vault.verify(stored.digest).expect("verify Windows final"),
            VaultVerification::Verified { digest, .. } if digest == stored.digest
        ));
        use cap_fs_ext::MetadataExt;
        let hex = stored.digest.to_string();
        let retained = open_retained_cap_file(
            &test.root,
            &["objects", "sha256", &hex[..2], &hex[2..4], &hex],
        );
        assert_eq!(
            retained
                .metadata()
                .expect("read retained cap File metadata")
                .nlink(),
            1
        );
        drop(verified);
    }

    #[test]
    fn windows_root_intermediate_and_final_reparse_points_fail_closed() {
        use std::os::windows::fs::{symlink_dir, symlink_file};

        let parent = tempfile::Builder::new()
            .prefix("heleos-vault-windows-reparse-")
            .tempdir()
            .expect("create Windows reparse parent");
        heleos_core::apply_private_permissions(parent.path())
            .expect("harden Windows reparse parent");
        let parent_path = fs::canonicalize(parent.path()).expect("canonicalize parent");
        let outside = parent_path.join("outside");
        create_private_dir(&outside);
        let root_link = parent_path.join("vault");
        symlink_dir(&outside, &root_link).expect("create root junction/reparse fixture");
        assert!(
            Vault::open(VaultConfig {
                root: root_link,
                open_mode: VaultOpenMode::ExistingOnly,
            })
            .is_err()
        );

        let junction_target = parent_path.join("junction-target");
        create_private_dir(&junction_target);
        let root_junction = parent_path.join("vault-junction");
        create_windows_junction(&root_junction, &junction_target);
        assert!(
            Vault::open(VaultConfig {
                root: root_junction,
                open_mode: VaultOpenMode::ExistingOnly,
            })
            .is_err()
        );

        let intermediate_root = parent_path.join("intermediate-vault");
        create_private_dir(&intermediate_root);
        let intermediate_outside = parent_path.join("intermediate-outside");
        create_private_dir(&intermediate_outside);
        symlink_dir(&intermediate_outside, intermediate_root.join("objects"))
            .expect("create intermediate reparse fixture");
        assert!(
            Vault::open(VaultConfig {
                root: intermediate_root,
                open_mode: VaultOpenMode::ExistingOnly,
            })
            .is_err()
        );

        let junction_root = parent_path.join("intermediate-junction-vault");
        create_private_dir(&junction_root);
        let intermediate_junction_target = parent_path.join("intermediate-junction-target");
        create_private_dir(&intermediate_junction_target);
        create_windows_junction(
            &junction_root.join("objects"),
            &intermediate_junction_target,
        );
        assert!(
            Vault::open(VaultConfig {
                root: junction_root,
                open_mode: VaultOpenMode::ExistingOnly,
            })
            .is_err()
        );

        let test = TestVault::new("heleos-vault-windows-final-reparse-");
        let digest = digest(b"Windows final reparse");
        let final_path = final_path(&test.root, digest);
        fs::create_dir_all(final_path.parent().expect("final parent")).expect("create shards");
        let hex = digest.to_string();
        for shard in [
            test.root.join("objects/sha256").join(&hex[..2]),
            test.root
                .join("objects/sha256")
                .join(&hex[..2])
                .join(&hex[2..4]),
        ] {
            heleos_core::apply_private_permissions(&shard)
                .expect("harden Windows final reparse shard");
        }
        let outside_file = test.parent.path().join("outside-file");
        create_private_file(&outside_file, b"outside");
        symlink_file(&outside_file, &final_path).expect("create final reparse fixture");
        assert!(matches!(
            test.vault.verify(digest).expect("classify final reparse"),
            VaultVerification::NonRegular {
                digest: reported,
                ..
            } if reported == digest
        ));
        assert!(matches!(
            test.vault.open_verified(digest),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn windows_reconciliation_classifies_a_reparse_shard_as_nonregular() {
        use std::os::windows::fs::symlink_dir;

        let test = TestVault::new("heleos-vault-windows-reparse-shard-");
        let outside = test.parent.path().join("outside-shard");
        create_private_dir(&outside);
        symlink_dir(&outside, test.root.join("objects/sha256/aa"))
            .expect("create reparse shard fixture");
        let report = test
            .vault
            .reconcile(&VaultInventory::try_from_entries([]).expect("empty inventory"))
            .expect("classify reparse shard without following it");
        assert!(report.findings.iter().any(|finding| matches!(
            finding,
            ReconciliationFinding::NonRegularEntry {
                path: EncodedVaultPath { encoding, value }
            } if encoding == "utf8" && value == "objects/sha256/aa"
        )));
        assert!(!report.findings.iter().any(|finding| matches!(
            finding,
            ReconciliationFinding::PermissionViolation {
                path: EncodedVaultPath { value, .. }
            } if value == "objects/sha256/aa"
        )));
    }

    #[test]
    fn windows_denied_attributes_and_broad_intermediate_dacl_are_typed_permission_findings() {
        let denied = TestVault::new("heleos-vault-windows-denied-attributes-");
        let denied_stored = stored(
            denied
                .vault
                .put_reader(
                    Cursor::new(b"Windows denied attributes"),
                    TestVault::budget(1024, 1024),
                )
                .expect("store denied-attributes fixture"),
        );
        let denied_path = final_path(&denied.root, denied_stored.digest);
        let process_sid = stellar_agent_windows_identity::current_user_sid_string()
            .expect("read process TokenUser SID");
        let denied_sddl = format!(
            "D:P(D;;0x00000080;;;{process_sid})(A;;0x00020000;;;{process_sid})(A;;FA;;;S-1-5-18)"
        );
        let mut retained = open_windows_dacl_fixture(&denied_path, false);
        install_protected_windows_dacl(&mut retained, &denied_sddl);
        assert!(matches!(
            denied
                .vault
                .verify(denied_stored.digest)
                .expect("classify denied FILE_READ_ATTRIBUTES"),
            VaultVerification::PermissionViolation {
                digest,
                byte_length: None,
                ..
            } if digest == denied_stored.digest
        ));
        assert!(matches!(
            denied.vault.open_verified(denied_stored.digest),
            Err(HeleosError::PolicyDenied)
        ));
        let report = denied
            .vault
            .reconcile(
                &VaultInventory::try_from_entries([VaultInventoryEntry {
                    digest: denied_stored.digest,
                    expected_byte_length: denied_stored.byte_length,
                    vault_key: denied_stored.vault_key.clone(),
                }])
                .expect("build denied-attributes inventory"),
            )
            .expect("reconcile denied-attributes candidate");
        assert!(report.findings.iter().any(|finding| matches!(
            finding,
            ReconciliationFinding::PermissionViolation { path }
                if path.value == denied_stored.vault_key
        )));
        install_protected_windows_dacl(&mut retained, &exact_windows_test_dacl());
        drop(retained);

        let intermediate = TestVault::new("heleos-vault-windows-broad-shard-");
        let intermediate_digest = digest(b"Windows broad intermediate shard");
        let hex = intermediate_digest.to_string();
        let shard = intermediate.root.join("objects/sha256").join(&hex[..2]);
        create_private_dir(&shard);
        make_permission_violation(&shard);
        assert!(matches!(
            intermediate
                .vault
                .verify(intermediate_digest)
                .expect("classify broad intermediate DACL"),
            VaultVerification::PermissionViolation {
                digest,
                byte_length: None,
                ..
            } if digest == intermediate_digest
        ));
        assert!(matches!(
            intermediate.vault.open_verified(intermediate_digest),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn hostile_windows_staging_name_is_losslessly_encoded_as_utf16_units() {
        use std::os::windows::ffi::OsStringExt;

        let test = TestVault::new("heleos-vault-windows-hostile-name-");
        let hostile = OsString::from_wide(&[0xd800, 0x0061, 0xdc00]);
        create_private_file(&test.root.join(".staging").join(hostile), b"hostile");
        let report = test
            .vault
            .reconcile(&VaultInventory::try_from_entries([]).expect("empty inventory"))
            .expect("reconcile hostile Windows name");
        assert!(report.findings.iter().any(|finding| matches!(
            finding,
            ReconciliationFinding::StagingPartial {
                path: EncodedVaultPath { encoding, value }
            } if encoding == "windows_utf16_units_hex"
                && value.ends_with("d8000061dc00")
        )));
    }
}
