mod path;
mod reconcile;

use std::collections::BTreeMap;
use std::ffi::{OsStr, OsString};
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock, Weak};

use cap_fs_ext::MetadataExt;
use cap_std::fs::{Dir, File as CapFile};
use fs2::FileExt;
use serde::Serialize;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::store::verify_private_permissions_on_handle;
use crate::{HeleosError, Result, Sha256Digest};
use path::{
    ClassifiedEntry, FileMarker, HandleKind, StagingCreateError, TrustAnchor,
    classify_and_open_entry, create_staging_file, open_directory, open_final_file,
    open_or_create_directory, open_or_create_file, open_trust_anchor, recheck_directory_name,
    recheck_file_name, recheck_trust_anchor,
};

const MIBIBYTE: u64 = 1024 * 1024;
const GIBIBYTE: u64 = 1024 * MIBIBYTE;
const FOUNDATION_MAX_INPUT_BYTES: u64 = 256 * MIBIBYTE;
const FOUNDATION_MAX_STORE_BYTES: u64 = 500 * GIBIBYTE;
const MINIMUM_FREE_RESERVE_BYTES: u64 = 20 * GIBIBYTE;
const STREAM_BUFFER_BYTES: usize = 64 * 1024;
const LOCK_NAME: &str = ".vault.lock";
const OBJECTS_NAME: &str = "objects";
const SHA256_NAME: &str = "sha256";
const STAGING_NAME: &str = ".staging";
const MAX_STAGING_NAME_ATTEMPTS: usize = 128;
#[cfg(test)]
const TEST_COLLIDING_STAGING_NAME: &str = "00000000-0000-4000-8000-000000000000.partial";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct VaultConfig {
    pub root: PathBuf,
    pub open_mode: VaultOpenMode,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VaultOpenMode {
    ExistingOnly,
    CreateOrOpen,
    CreateNew,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct VaultWriteBudget {
    max_input_bytes: u64,
    max_retain_bytes: u64,
}

impl VaultWriteBudget {
    pub fn new(max_input_bytes: u64, max_retain_bytes: u64) -> Result<Self> {
        if max_input_bytes > FOUNDATION_MAX_INPUT_BYTES {
            return Err(HeleosError::ResourceLimit);
        }
        if max_retain_bytes > FOUNDATION_MAX_STORE_BYTES {
            return Err(HeleosError::Quota);
        }
        Ok(Self {
            max_input_bytes,
            max_retain_bytes,
        })
    }

    pub const fn max_input_bytes(&self) -> u64 {
        self.max_input_bytes
    }

    pub const fn max_retain_bytes(&self) -> u64 {
        self.max_retain_bytes
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PutOutcome {
    Stored(StoredObject),
    QuotaRejected {
        digest: Sha256Digest,
        byte_length: u64,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct StoredObject {
    pub digest: Sha256Digest,
    pub byte_length: u64,
    pub vault_key: String,
    pub newly_published: bool,
}

pub struct VerifiedObject {
    file: CapFile,
    digest: Sha256Digest,
    byte_length: u64,
    vault_key: String,
}

impl VerifiedObject {
    pub const fn digest(&self) -> Sha256Digest {
        self.digest
    }

    pub const fn byte_length(&self) -> u64 {
        self.byte_length
    }

    pub fn vault_key(&self) -> &str {
        &self.vault_key
    }
}

impl Read for VerifiedObject {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        self.file.read(buffer)
    }
}

impl Seek for VerifiedObject {
    fn seek(&mut self, position: SeekFrom) -> io::Result<u64> {
        self.file.seek(position)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum VaultVerification {
    Verified {
        digest: Sha256Digest,
        byte_length: u64,
        vault_key: String,
    },
    Missing {
        digest: Sha256Digest,
        vault_key: String,
    },
    Corrupt {
        expected_digest: Sha256Digest,
        actual_digest: Option<Sha256Digest>,
        actual_byte_length: Option<u64>,
        vault_key: String,
    },
    NonRegular {
        digest: Sha256Digest,
        vault_key: String,
    },
    UnexpectedLinkCount {
        digest: Sha256Digest,
        byte_length: u64,
        vault_key: String,
        link_count: u64,
    },
    PermissionViolation {
        digest: Sha256Digest,
        byte_length: Option<u64>,
        vault_key: String,
    },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct VaultInventoryEntry {
    pub digest: Sha256Digest,
    pub expected_byte_length: u64,
    pub vault_key: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct VaultInventory {
    pub(super) entries: BTreeMap<Sha256Digest, VaultInventoryEntry>,
}

impl VaultInventory {
    pub fn try_from_entries<I>(entries: I) -> Result<Self>
    where
        I: IntoIterator<Item = VaultInventoryEntry>,
    {
        let mut indexed = BTreeMap::new();
        for entry in entries {
            match indexed.get(&entry.digest) {
                Some(existing) if existing == &entry => {}
                Some(_) => return Err(HeleosError::Integrity),
                None => {
                    indexed.insert(entry.digest, entry);
                }
            }
        }
        Ok(Self { entries: indexed })
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct EncodedVaultPath {
    pub encoding: String,
    pub value: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ReconciliationFinding {
    StagingPartial {
        path: EncodedVaultPath,
    },
    UnreferencedObject {
        verification: VaultVerification,
    },
    CorruptObject {
        verification: VaultVerification,
    },
    NonRegularEntry {
        path: EncodedVaultPath,
    },
    PermissionViolation {
        path: EncodedVaultPath,
    },
    UnexpectedLinkCount {
        path: EncodedVaultPath,
        link_count: u64,
    },
    InvalidLayout {
        path: EncodedVaultPath,
    },
    MissingReference {
        inventory: VaultInventoryEntry,
    },
    InventoryKeyMismatch {
        inventory: VaultInventoryEntry,
        expected_vault_key: String,
    },
    InventoryLengthMismatch {
        inventory: VaultInventoryEntry,
        actual_byte_length: u64,
    },
}

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize)]
pub struct ReconciliationReport {
    pub findings: Vec<ReconciliationFinding>,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct RegistryKey {
    root: PathBuf,
    lock: FileMarker,
}

static PROCESS_LOCKS: OnceLock<Mutex<BTreeMap<RegistryKey, Weak<Mutex<()>>>>> = OnceLock::new();

pub struct Vault {
    trust_anchor: TrustAnchor,
    root_dir: Dir,
    root_marker: FileMarker,
    objects_dir: Dir,
    objects_marker: FileMarker,
    sha256_dir: Dir,
    sha256_marker: FileMarker,
    staging_dir: Dir,
    staging_marker: FileMarker,
    lock_file: File,
    lock_marker: FileMarker,
    process_lock: Arc<Mutex<()>>,
}

struct HeldVaultLock<'a> {
    vault: &'a Vault,
    held: bool,
}

enum ClassifiedDigestDirectory {
    Opened(Dir),
    NonRegular,
    PermissionViolation,
}

impl HeldVaultLock<'_> {
    fn finish(mut self) -> Result<()> {
        let final_recheck = self
            .vault
            .recheck_lock()
            .and_then(|()| self.vault.recheck_fixed_layout());
        let unlock = FileExt::unlock(&self.vault.lock_file).map_err(HeleosError::Io);
        if unlock.is_ok() {
            self.held = false;
        }
        match (final_recheck, unlock) {
            (Err(error), _) | (Ok(()), Err(error)) => Err(error),
            (Ok(()), Ok(())) => Ok(()),
        }
    }
}

impl Drop for HeldVaultLock<'_> {
    fn drop(&mut self) {
        if self.held {
            let _ = self
                .vault
                .recheck_lock()
                .and_then(|()| self.vault.recheck_fixed_layout());
            if FileExt::unlock(&self.vault.lock_file).is_ok() {
                self.held = false;
            }
        }
    }
}

impl Vault {
    pub fn open(config: VaultConfig) -> Result<Self> {
        Self::open_internal(config, NoFault)
    }

    fn open_internal<F: FaultInjector>(config: VaultConfig, faults: F) -> Result<Self> {
        let trust_anchor = open_trust_anchor(&config.root)?;
        let root_opened = match config.open_mode {
            VaultOpenMode::ExistingOnly => {
                open_directory(&trust_anchor.parent, &trust_anchor.root_name, true)?
            }
            VaultOpenMode::CreateNew => {
                path::create_directory(&trust_anchor.parent, &trust_anchor.root_name)?
            }
            VaultOpenMode::CreateOrOpen => {
                match path::create_directory(&trust_anchor.parent, &trust_anchor.root_name) {
                    Ok(opened) => opened,
                    Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::AlreadyExists => {
                        open_directory(&trust_anchor.parent, &trust_anchor.root_name, true)?
                    }
                    Err(error) => return Err(error),
                }
            }
        };
        sync_initial_directory(&trust_anchor.parent, InitialSyncPoint::Root, faults)?;
        recheck_trust_anchor(&trust_anchor)?;
        let objects = open_or_create_directory(&root_opened.dir, OsStr::new(OBJECTS_NAME))?;
        sync_initial_directory(&root_opened.dir, InitialSyncPoint::Objects, faults)?;
        let sha256 = open_or_create_directory(&objects.dir, OsStr::new(SHA256_NAME))?;
        sync_initial_directory(&objects.dir, InitialSyncPoint::Sha256, faults)?;
        let staging = open_or_create_directory(&root_opened.dir, OsStr::new(STAGING_NAME))?;
        sync_initial_directory(&root_opened.dir, InitialSyncPoint::Staging, faults)?;
        let lock = open_or_create_file(
            &root_opened.dir,
            OsStr::new(LOCK_NAME),
            HandleKind::LockApply,
        )?;
        sync_initial_directory(&root_opened.dir, InitialSyncPoint::Lock, faults)?;
        if link_count(&lock.file)? != 1 {
            return Err(HeleosError::PolicyDenied);
        }
        let lock_marker = lock.marker;
        let lock_file = lock.file.into_std();
        let registry_key = RegistryKey {
            root: config.root,
            lock: lock_marker,
        };
        let process_lock = process_lock_for(registry_key)?;
        Ok(Self {
            trust_anchor,
            root_dir: root_opened.dir,
            root_marker: root_opened.marker,
            objects_dir: objects.dir,
            objects_marker: objects.marker,
            sha256_dir: sha256.dir,
            sha256_marker: sha256.marker,
            staging_dir: staging.dir,
            staging_marker: staging.marker,
            lock_file,
            lock_marker,
            process_lock,
        })
    }

    pub fn put_reader<R: Read>(&self, reader: R, budget: VaultWriteBudget) -> Result<PutOutcome> {
        self.put_reader_internal(reader, budget, NoFault)
    }

    pub fn open_verified(&self, digest: Sha256Digest) -> Result<VerifiedObject> {
        self.with_lock(false, || {
            let key = Self::object_key(digest);
            let mut file = match self.open_digest_file(digest) {
                Ok(file) => file,
                Err(HeleosError::NotFound) => return Err(HeleosError::NotFound),
                Err(error) => return Err(error),
            };
            let link_count = link_count(&file)?;
            if link_count != 1 {
                return Err(HeleosError::PolicyDenied);
            }
            let (actual, length) = match hash_cap_file(&mut file) {
                Ok(result) => result,
                Err(HeleosError::ResourceLimit) => return Err(HeleosError::Integrity),
                Err(error) => return Err(error),
            };
            if actual != digest {
                return Err(HeleosError::Integrity);
            }
            file.seek(SeekFrom::Start(0)).map_err(HeleosError::Io)?;
            Ok(VerifiedObject {
                file,
                digest,
                byte_length: length,
                vault_key: key,
            })
        })
    }

    pub fn verify(&self, digest: Sha256Digest) -> Result<VaultVerification> {
        self.verify_internal(digest, NoFault)
    }

    pub fn reconcile(&self, inventory: &VaultInventory) -> Result<ReconciliationReport> {
        self.with_lock(true, || reconcile::reconcile_locked(self, inventory))
    }

    pub fn object_key(digest: Sha256Digest) -> String {
        let digest = digest.to_string();
        format!(
            "objects/sha256/{}/{}/{}",
            &digest[..2],
            &digest[2..4],
            digest
        )
    }

    fn put_reader_internal<R: Read, F: FaultInjector>(
        &self,
        mut reader: R,
        budget: VaultWriteBudget,
        faults: F,
    ) -> Result<PutOutcome> {
        self.with_lock_internal(true, faults, || {
            self.recheck_fixed_layout()?;
            let logical_bytes = self.scan_logical_store()?;
            let remaining_store = FOUNDATION_MAX_STORE_BYTES
                .checked_sub(logical_bytes)
                .ok_or(HeleosError::Quota)?;
            let retain_limit = budget.max_retain_bytes.min(remaining_store);
            let declared_staging = budget.max_input_bytes.min(retain_limit);
            let capacity = if faults.is_capacity_shortage() {
                false
            } else {
                self.has_staging_capacity(declared_staging, faults)?
            };
            if !capacity {
                let (digest, length) = hash_bounded_reader(&mut reader, budget.max_input_bytes)?;
                return self.duplicate_or_quota(digest, length);
            }

            let mut staging_created = None;
            for attempt in 0..MAX_STAGING_NAME_ATTEMPTS {
                let staging_name = faults.staging_name(attempt);
                match create_staging_file(&self.staging_dir, OsStr::new(&staging_name)) {
                    Ok(opened) => {
                        staging_created = Some((staging_name, opened.file));
                        break;
                    }
                    Err(StagingCreateError::NotCreated(HeleosError::Io(error)))
                        if error.kind() == io::ErrorKind::AlreadyExists =>
                    {
                        continue;
                    }
                    Err(StagingCreateError::NotCreated(error)) => return Err(error),
                    Err(StagingCreateError::Created(original)) => {
                        match self.staging_dir.remove_file(OsStr::new(&staging_name)) {
                            Ok(()) => sync_directory(&self.staging_dir)?,
                            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                            Err(error) => return Err(HeleosError::Io(error)),
                        }
                        return Err(original);
                    }
                }
            }
            let (staging_name, staging_file) = staging_created.ok_or(HeleosError::ResourceLimit)?;
            let mut staging = StagingGuard::new(
                &self.staging_dir,
                OsString::from(&staging_name),
                staging_file,
            );
            if let Err(error) = sync_directory(&self.staging_dir) {
                return cleanup_with_error(&mut staging, error);
            }
            let allocation = match faults.allocation_error_kind() {
                Some(kind) => Err(io::Error::new(kind, "injected staging allocation failure")),
                None => allocate_cap_file(staging.file()?, declared_staging),
            };
            if allocation.is_err() {
                staging.cleanup()?;
                let (digest, length) = hash_bounded_reader(&mut reader, budget.max_input_bytes)?;
                return self.duplicate_or_quota(digest, length);
            }

            let stream_result = stream_to_staging(
                &mut reader,
                staging.file_mut()?,
                budget.max_input_bytes,
                retain_limit,
                &faults,
            );
            let (digest, length, retained_all) = match stream_result {
                Ok(result) => result,
                Err(error) => {
                    staging.cleanup()?;
                    return Err(error);
                }
            };
            if !retained_all {
                staging.cleanup()?;
                return self.duplicate_or_quota(digest, length);
            }
            if let Err(error) = staging.file()?.set_len(length) {
                return cleanup_with_error(&mut staging, HeleosError::Io(error));
            }
            if faults.fail_at(FaultPoint::Flush) {
                return cleanup_with_error(
                    &mut staging,
                    HeleosError::Io(io::Error::other("injected staging flush failure")),
                );
            }
            if let Err(error) = staging.file_mut()?.flush() {
                return cleanup_with_error(&mut staging, HeleosError::Io(error));
            }
            if faults.fail_at(FaultPoint::FileSync) {
                return cleanup_with_error(
                    &mut staging,
                    HeleosError::Io(io::Error::other("injected staging sync failure")),
                );
            }
            if let Err(error) = staging.file()?.sync_all() {
                return cleanup_with_error(&mut staging, HeleosError::Io(error));
            }
            let (rehash, recount) = match hash_cap_file(staging.file_mut()?) {
                Ok(result) => result,
                Err(error) => return cleanup_with_error(&mut staging, error),
            };
            let staging_links = match link_count(staging.file()?) {
                Ok(count) => count,
                Err(error) => return cleanup_with_error(&mut staging, error),
            };
            if rehash != digest || recount != length || staging_links != 1 {
                return cleanup_with_error(&mut staging, HeleosError::Integrity);
            }
            if let Err(error) = verify_cap_permissions(staging.file()?) {
                return cleanup_with_error(&mut staging, error);
            }
            #[cfg(test)]
            faults.observe(OperationEvent::StagingVerified);
            match self.open_digest_file(digest) {
                Ok(existing) => {
                    let outcome = self.verify_existing_winner(digest, length, existing);
                    staging.cleanup()?;
                    return outcome;
                }
                Err(HeleosError::NotFound) => {}
                Err(error) => return cleanup_with_error(&mut staging, error),
            }
            let has_reserve = match self
                .has_publication_reserve(faults, PublicationCapacityPhase::BeforeShards)
            {
                Ok(has_reserve) => has_reserve,
                Err(error) => return cleanup_with_error(&mut staging, error),
            };
            if !has_reserve {
                staging.cleanup()?;
                return Ok(PutOutcome::QuotaRejected {
                    digest,
                    byte_length: length,
                });
            }
            let (first, second, final_name) = digest_components(digest);
            let first_dir = match open_or_create_directory(&self.sha256_dir, OsStr::new(&first)) {
                Ok(directory) => directory,
                Err(error) => return cleanup_with_error(&mut staging, error),
            };
            if let Err(error) = sync_directory(&self.sha256_dir) {
                return cleanup_with_error(&mut staging, error);
            }
            let second_dir = match open_or_create_directory(&first_dir.dir, OsStr::new(&second)) {
                Ok(directory) => directory,
                Err(error) => return cleanup_with_error(&mut staging, error),
            };
            if let Err(error) = sync_directory(&first_dir.dir) {
                return cleanup_with_error(&mut staging, error);
            }
            #[cfg(test)]
            if faults.fail_at(FaultPoint::WinnerSubstitution) {
                let mut hostile =
                    match create_staging_file(&second_dir.dir, OsStr::new(&final_name)) {
                        Ok(opened) => opened.file,
                        Err(error) => {
                            return cleanup_with_error(&mut staging, error.into_error());
                        }
                    };
                hostile
                    .write_all(b"substituted winner")
                    .map_err(HeleosError::Io)?;
                hostile.sync_all().map_err(HeleosError::Io)?;
                drop(hostile);
            }
            let has_final_reserve =
                match self.has_publication_reserve(faults, PublicationCapacityPhase::BeforeLink) {
                    Ok(has_reserve) => has_reserve,
                    Err(error) => return cleanup_with_error(&mut staging, error),
                };
            if !has_final_reserve {
                staging.cleanup()?;
                return Ok(PutOutcome::QuotaRejected {
                    digest,
                    byte_length: length,
                });
            }
            #[cfg(test)]
            faults.observe(OperationEvent::BeforeFinalLink);
            if faults.fail_at(FaultPoint::BeforeLink) {
                return cleanup_with_error(
                    &mut staging,
                    HeleosError::Io(io::Error::other("injected pre-link interruption")),
                );
            }
            let link_result = self.staging_dir.hard_link(
                OsStr::new(&staging_name),
                &second_dir.dir,
                OsStr::new(&final_name),
            );
            match link_result {
                Ok(()) => {}
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                    let outcome = match open_final_file(&second_dir.dir, OsStr::new(&final_name)) {
                        Ok(opened) => self.verify_existing_winner(digest, length, opened.file),
                        Err(error) => Err(error),
                    };
                    staging.cleanup()?;
                    return outcome;
                }
                Err(error) => {
                    staging.cleanup()?;
                    return Err(HeleosError::Io(error));
                }
            }
            #[cfg(test)]
            faults.observe(OperationEvent::FinalLinkCreated);
            staging.preserve();
            if link_count(staging.file()?)? != 2 {
                return Err(HeleosError::Integrity);
            }
            if faults.fail_at(FaultPoint::FinalDirectorySync) {
                return Err(HeleosError::Io(io::Error::other(
                    "injected final-directory sync failure",
                )));
            }
            sync_directory(&second_dir.dir)?;
            #[cfg(test)]
            faults.observe(OperationEvent::FinalDirectorySynced);
            if faults.fail_at(FaultPoint::AfterLink) {
                return Err(HeleosError::Io(io::Error::other(
                    "injected post-link interruption",
                )));
            }
            staging.close_handle();
            #[cfg(test)]
            faults.observe(OperationEvent::StagingHandleClosed);
            if faults.fail_at(FaultPoint::StagingUnlink) {
                return Err(HeleosError::Io(io::Error::other(
                    "injected staging-unlink failure",
                )));
            }
            self.staging_dir
                .remove_file(OsStr::new(&staging_name))
                .map_err(HeleosError::Io)?;
            #[cfg(test)]
            faults.observe(OperationEvent::StagingNameRemoved);
            if faults.fail_at(FaultPoint::StagingDirectorySync) {
                return Err(HeleosError::Io(io::Error::other(
                    "injected staging-directory sync failure",
                )));
            }
            sync_directory(&self.staging_dir)?;
            #[cfg(test)]
            faults.observe(OperationEvent::StagingDirectorySynced);
            staging.disarm();
            #[cfg(test)]
            if faults.fail_at(FaultPoint::DestinationSubstitution) {
                second_dir
                    .dir
                    .remove_file(OsStr::new(&final_name))
                    .map_err(HeleosError::Io)?;
                let mut hostile = create_staging_file(&second_dir.dir, OsStr::new(&final_name))
                    .map_err(StagingCreateError::into_error)?
                    .file;
                hostile
                    .write_all(b"substituted destination")
                    .map_err(HeleosError::Io)?;
                hostile.sync_all().map_err(HeleosError::Io)?;
                drop(hostile);
            }
            if faults.fail_at(FaultPoint::FinalReopen) {
                return Err(HeleosError::Io(io::Error::other(
                    "injected final-reopen failure",
                )));
            }
            let mut final_file = open_final_file(&second_dir.dir, OsStr::new(&final_name))?.file;
            #[cfg(test)]
            faults.observe(OperationEvent::FinalReopened);
            if link_count(&final_file)? != 1 {
                return Err(HeleosError::Integrity);
            }
            let (final_digest, final_length) = hash_cap_file(&mut final_file)?;
            if final_digest != digest || final_length != length {
                return Err(HeleosError::Integrity);
            }
            verify_cap_permissions(&final_file)?;
            #[cfg(test)]
            faults.observe(OperationEvent::FinalVerified);
            Ok(PutOutcome::Stored(StoredObject {
                digest,
                byte_length: length,
                vault_key: Self::object_key(digest),
                newly_published: true,
            }))
        })
    }

    fn verify_existing_winner(
        &self,
        digest: Sha256Digest,
        expected_length: u64,
        mut file: CapFile,
    ) -> Result<PutOutcome> {
        if link_count(&file)? != 1 {
            return Err(HeleosError::PolicyDenied);
        }
        let (actual_digest, actual_length) = match hash_cap_file(&mut file) {
            Ok(result) => result,
            Err(HeleosError::ResourceLimit) => return Err(HeleosError::Integrity),
            Err(error) => return Err(error),
        };
        if actual_digest != digest || actual_length != expected_length {
            return Err(HeleosError::Integrity);
        }
        Ok(PutOutcome::Stored(StoredObject {
            digest,
            byte_length: expected_length,
            vault_key: Self::object_key(digest),
            newly_published: false,
        }))
    }

    fn duplicate_or_quota(&self, digest: Sha256Digest, length: u64) -> Result<PutOutcome> {
        match self.open_digest_file(digest) {
            Ok(file) => self.verify_existing_winner(digest, length, file),
            Err(HeleosError::NotFound) => Ok(PutOutcome::QuotaRejected {
                digest,
                byte_length: length,
            }),
            Err(error) => Err(error),
        }
    }

    fn verify_locked(&self, digest: Sha256Digest) -> Result<VaultVerification> {
        let vault_key = Self::object_key(digest);
        let mut file = match self.classify_digest_candidate(digest) {
            Ok(ClassifiedEntry::Regular(file)) => file,
            Ok(ClassifiedEntry::Directory | ClassifiedEntry::NonRegular) => {
                return Ok(VaultVerification::NonRegular { digest, vault_key });
            }
            Ok(ClassifiedEntry::PermissionViolation { byte_length }) => {
                return Ok(VaultVerification::PermissionViolation {
                    digest,
                    byte_length,
                    vault_key,
                });
            }
            Err(HeleosError::NotFound) => {
                return Ok(VaultVerification::Missing { digest, vault_key });
            }
            Err(HeleosError::PolicyDenied) => {
                return Ok(VaultVerification::NonRegular { digest, vault_key });
            }
            Err(error) => return Err(error),
        };
        let metadata = file.metadata().map_err(HeleosError::Io)?;
        if !cap_metadata_is_regular(&metadata) {
            return Ok(VaultVerification::NonRegular { digest, vault_key });
        }
        let byte_length = metadata.len();
        if byte_length > FOUNDATION_MAX_INPUT_BYTES {
            return Ok(VaultVerification::Corrupt {
                expected_digest: digest,
                actual_digest: None,
                actual_byte_length: Some(byte_length),
                vault_key,
            });
        }
        match verify_cap_permissions(&file) {
            Ok(()) => {}
            Err(HeleosError::PolicyDenied) => {
                return Ok(VaultVerification::PermissionViolation {
                    digest,
                    byte_length: Some(byte_length),
                    vault_key,
                });
            }
            Err(error) => return Err(error),
        }
        let link_count = metadata.nlink();
        if link_count != 1 {
            return Ok(VaultVerification::UnexpectedLinkCount {
                digest,
                byte_length,
                vault_key,
                link_count,
            });
        }
        match hash_cap_file(&mut file) {
            Ok((actual_digest, actual_byte_length)) if actual_digest == digest => {
                Ok(VaultVerification::Verified {
                    digest,
                    byte_length: actual_byte_length,
                    vault_key,
                })
            }
            Ok((actual_digest, actual_byte_length)) => Ok(VaultVerification::Corrupt {
                expected_digest: digest,
                actual_digest: Some(actual_digest),
                actual_byte_length: Some(actual_byte_length),
                vault_key,
            }),
            Err(HeleosError::Io(_)) => Ok(VaultVerification::Corrupt {
                expected_digest: digest,
                actual_digest: None,
                actual_byte_length: Some(byte_length),
                vault_key,
            }),
            Err(HeleosError::ResourceLimit) => Ok(VaultVerification::Corrupt {
                expected_digest: digest,
                actual_digest: None,
                actual_byte_length: Some(byte_length),
                vault_key,
            }),
            Err(error) => Err(error),
        }
    }

    fn open_digest_file(&self, digest: Sha256Digest) -> Result<CapFile> {
        let file = self.open_digest_candidate(digest)?;
        if !cap_metadata_is_regular(&file.metadata().map_err(HeleosError::Io)?) {
            return Err(HeleosError::PolicyDenied);
        }
        verify_cap_permissions(&file)?;
        Ok(file)
    }

    fn open_digest_candidate(&self, digest: Sha256Digest) -> Result<CapFile> {
        match self.classify_digest_candidate(digest)? {
            ClassifiedEntry::Regular(file) => Ok(file),
            ClassifiedEntry::Directory
            | ClassifiedEntry::NonRegular
            | ClassifiedEntry::PermissionViolation { .. } => Err(HeleosError::PolicyDenied),
        }
    }

    fn classify_digest_candidate(&self, digest: Sha256Digest) -> Result<ClassifiedEntry> {
        let (first, second, name) = digest_components(digest);
        let first_dir = match classify_digest_directory(&self.sha256_dir, OsStr::new(&first)) {
            Ok(ClassifiedDigestDirectory::Opened(dir)) => dir,
            Ok(ClassifiedDigestDirectory::NonRegular) => {
                return Ok(ClassifiedEntry::NonRegular);
            }
            Ok(ClassifiedDigestDirectory::PermissionViolation) => {
                return Ok(ClassifiedEntry::PermissionViolation { byte_length: None });
            }
            Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::NotFound => {
                return Err(HeleosError::NotFound);
            }
            Err(error) => return Err(error),
        };
        let second_dir = match classify_digest_directory(&first_dir, OsStr::new(&second)) {
            Ok(ClassifiedDigestDirectory::Opened(dir)) => dir,
            Ok(ClassifiedDigestDirectory::NonRegular) => {
                return Ok(ClassifiedEntry::NonRegular);
            }
            Ok(ClassifiedDigestDirectory::PermissionViolation) => {
                return Ok(ClassifiedEntry::PermissionViolation { byte_length: None });
            }
            Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::NotFound => {
                return Err(HeleosError::NotFound);
            }
            Err(error) => return Err(error),
        };
        match classify_and_open_entry(&second_dir, OsStr::new(&name)) {
            Ok(classified) => Ok(classified),
            Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::NotFound => {
                Err(HeleosError::NotFound)
            }
            Err(error) => Err(error),
        }
    }

    fn recheck_fixed_layout(&self) -> Result<()> {
        recheck_trust_anchor(&self.trust_anchor)?;
        recheck_directory_name(
            &self.trust_anchor.parent,
            &self.trust_anchor.root_name,
            &self.root_dir,
            self.root_marker,
        )?;
        recheck_directory_name(
            &self.root_dir,
            OsStr::new(OBJECTS_NAME),
            &self.objects_dir,
            self.objects_marker,
        )?;
        recheck_directory_name(
            &self.objects_dir,
            OsStr::new(SHA256_NAME),
            &self.sha256_dir,
            self.sha256_marker,
        )?;
        recheck_directory_name(
            &self.root_dir,
            OsStr::new(STAGING_NAME),
            &self.staging_dir,
            self.staging_marker,
        )
    }

    fn recheck_lock(&self) -> Result<()> {
        let cap_file = CapFile::from_std(self.lock_file.try_clone().map_err(HeleosError::Io)?);
        recheck_file_name(
            &self.root_dir,
            OsStr::new(LOCK_NAME),
            &cap_file,
            self.lock_marker,
            HandleKind::LockApply,
        )?;
        if link_count(&cap_file)? != 1 {
            return Err(HeleosError::PolicyDenied);
        }
        Ok(())
    }

    fn with_lock<T>(&self, exclusive: bool, operation: impl FnOnce() -> Result<T>) -> Result<T> {
        self.with_lock_internal(exclusive, NoFault, operation)
    }

    fn with_lock_internal<T, F: FaultInjector>(
        &self,
        exclusive: bool,
        faults: F,
        operation: impl FnOnce() -> Result<T>,
    ) -> Result<T> {
        #[cfg(test)]
        faults.observe(OperationEvent::LockAttempt { exclusive });
        #[cfg(not(test))]
        let _ = faults;
        let _process_guard = match self.process_lock.lock() {
            Ok(guard) => guard,
            Err(poisoned) => {
                self.process_lock.clear_poison();
                poisoned.into_inner()
            }
        };
        if exclusive {
            FileExt::lock_exclusive(&self.lock_file).map_err(HeleosError::Io)?;
        } else {
            FileExt::lock_shared(&self.lock_file).map_err(HeleosError::Io)?;
        }
        #[cfg(test)]
        faults.observe(OperationEvent::LockAcquired { exclusive });
        let lock_guard = HeldVaultLock {
            vault: self,
            held: true,
        };
        let initial = self
            .recheck_lock()
            .and_then(|()| self.recheck_fixed_layout());
        let result = initial.and_then(|()| {
            #[cfg(test)]
            faults.observe(OperationEvent::OperationEntered { exclusive });
            operation()
        });
        match (lock_guard.finish(), result) {
            (Err(error), _) => Err(error),
            (Ok(()), result) => result,
        }
    }

    fn verify_internal<F: FaultInjector>(
        &self,
        digest: Sha256Digest,
        faults: F,
    ) -> Result<VaultVerification> {
        self.with_lock_internal(false, faults, || self.verify_locked(digest))
    }

    fn scan_logical_store(&self) -> Result<u64> {
        reconcile::validate_fixed_layout(self)?;
        let mut total = 0_u64;
        for first in self.sha256_dir.entries().map_err(HeleosError::Io)? {
            let first = first.map_err(HeleosError::Io)?;
            let first_name = first.file_name();
            if !is_lower_hex_component(&first_name, 2) {
                return Err(HeleosError::PolicyDenied);
            }
            let first_dir = open_directory(&self.sha256_dir, &first_name, true)?;
            for second in first_dir.dir.entries().map_err(HeleosError::Io)? {
                let second = second.map_err(HeleosError::Io)?;
                let second_name = second.file_name();
                if !is_lower_hex_component(&second_name, 2) {
                    return Err(HeleosError::PolicyDenied);
                }
                let second_dir = open_directory(&first_dir.dir, &second_name, true)?;
                for object in second_dir.dir.entries().map_err(HeleosError::Io)? {
                    let object = object.map_err(HeleosError::Io)?;
                    let name = object.file_name();
                    let Some(text) = name.to_str() else {
                        return Err(HeleosError::PolicyDenied);
                    };
                    let digest: Sha256Digest = text.parse()?;
                    let (expected_first, expected_second, expected_name) =
                        digest_components(digest);
                    if first_name != OsStr::new(&expected_first)
                        || second_name != OsStr::new(&expected_second)
                        || name != OsStr::new(&expected_name)
                    {
                        return Err(HeleosError::PolicyDenied);
                    }
                    let file = open_final_file(&second_dir.dir, &name)?.file;
                    if link_count(&file)? != 1 {
                        return Err(HeleosError::PolicyDenied);
                    }
                    let length = file.metadata().map_err(HeleosError::Io)?.len();
                    if length > FOUNDATION_MAX_INPUT_BYTES {
                        return Err(HeleosError::ResourceLimit);
                    }
                    total = total.checked_add(length).ok_or(HeleosError::Quota)?;
                    if total > FOUNDATION_MAX_STORE_BYTES {
                        return Err(HeleosError::Quota);
                    }
                }
            }
        }
        Ok(total)
    }

    fn has_staging_capacity<F: FaultInjector>(
        &self,
        declared_staging: u64,
        faults: F,
    ) -> Result<bool> {
        let total = self.capacity_query(CapacityQuery::Total, faults)?;
        let granularity = self.capacity_query(CapacityQuery::AllocationGranularity, faults)?;
        let available = self.capacity_query(CapacityQuery::Available, faults)?;
        staging_capacity_is_sufficient(declared_staging, total, available, granularity)
    }

    fn has_publication_reserve<F: FaultInjector>(
        &self,
        faults: F,
        phase: PublicationCapacityPhase,
    ) -> Result<bool> {
        if faults.publication_capacity_shortage(phase) {
            return Ok(false);
        }
        let total = self.capacity_query(CapacityQuery::Total, faults)?;
        let granularity = self.capacity_query(CapacityQuery::AllocationGranularity, faults)?;
        let available = self.capacity_query(CapacityQuery::Available, faults)?;
        publication_capacity_is_sufficient(total, available, granularity)
    }

    fn capacity_query<F: FaultInjector>(&self, query: CapacityQuery, faults: F) -> Result<u64> {
        self.recheck_fixed_layout()?;
        let mismatch = faults.capacity_mismatch();
        if mismatch == Some(CapacityMismatch::BeforeTotal) && query == CapacityQuery::Total {
            return Err(HeleosError::PolicyDenied);
        }
        if mismatch == Some(CapacityMismatch::BeforeAvailable) && query == CapacityQuery::Available
        {
            return Err(HeleosError::PolicyDenied);
        }
        #[cfg(test)]
        faults.observe(OperationEvent::CapacityPathQuery(
            query,
            self.trust_anchor.configured_root.clone(),
        ));
        let value = match query {
            CapacityQuery::Total => fs2::total_space(&self.trust_anchor.configured_root),
            CapacityQuery::Available => fs2::available_space(&self.trust_anchor.configured_root),
            CapacityQuery::AllocationGranularity => {
                fs2::allocation_granularity(&self.trust_anchor.configured_root)
            }
        }
        .map_err(HeleosError::Io)?;
        self.recheck_fixed_layout()?;
        Ok(value)
    }
}

fn classify_digest_directory(parent: &Dir, name: &OsStr) -> Result<ClassifiedDigestDirectory> {
    match classify_and_open_entry(parent, name)? {
        ClassifiedEntry::Directory => match open_directory(parent, name, false) {
            Ok(opened) => Ok(ClassifiedDigestDirectory::Opened(opened.dir)),
            Err(HeleosError::PolicyDenied) => Ok(ClassifiedDigestDirectory::PermissionViolation),
            Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::PermissionDenied => {
                Ok(ClassifiedDigestDirectory::PermissionViolation)
            }
            Err(error) => Err(error),
        },
        ClassifiedEntry::Regular(_) | ClassifiedEntry::NonRegular => {
            Ok(ClassifiedDigestDirectory::NonRegular)
        }
        ClassifiedEntry::PermissionViolation { .. } => {
            Ok(ClassifiedDigestDirectory::PermissionViolation)
        }
    }
}

fn process_lock_for(key: RegistryKey) -> Result<Arc<Mutex<()>>> {
    let registry = PROCESS_LOCKS.get_or_init(|| Mutex::new(BTreeMap::new()));
    let mut registry = registry.lock().map_err(|_| HeleosError::PolicyDenied)?;
    registry.retain(|_, value| value.strong_count() != 0);
    if let Some(existing) = registry.get(&key).and_then(Weak::upgrade) {
        return Ok(existing);
    }
    let lock = Arc::new(Mutex::new(()));
    registry.insert(key, Arc::downgrade(&lock));
    Ok(lock)
}

fn digest_components(digest: Sha256Digest) -> (String, String, String) {
    let digest = digest.to_string();
    (digest[..2].to_owned(), digest[2..4].to_owned(), digest)
}

fn is_lower_hex_component(name: &OsStr, length: usize) -> bool {
    let Some(name) = name.to_str() else {
        return false;
    };
    name.len() == length
        && name
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn link_count(file: &CapFile) -> Result<u64> {
    Ok(file.metadata().map_err(HeleosError::Io)?.nlink())
}

fn cap_metadata_is_regular(metadata: &cap_std::fs::Metadata) -> bool {
    if !metadata.is_file() {
        return false;
    }
    #[cfg(windows)]
    {
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
        if cap_std::fs::MetadataExt::file_attributes(metadata) & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return false;
        }
    }
    true
}

fn verify_cap_permissions(file: &CapFile) -> Result<()> {
    let std_file = file.try_clone().map_err(HeleosError::Io)?.into_std();
    verify_private_permissions_on_handle(&std_file)
}

fn allocate_cap_file(file: &CapFile, length: u64) -> io::Result<()> {
    allocate_if_nonzero(length, || {
        let std_file = file.try_clone()?.into_std();
        FileExt::allocate(&std_file, length)
    })
}

fn allocate_if_nonzero(length: u64, allocate: impl FnOnce() -> io::Result<()>) -> io::Result<()> {
    if length == 0 { Ok(()) } else { allocate() }
}

fn hash_cap_file(file: &mut CapFile) -> Result<(Sha256Digest, u64)> {
    if file.metadata().map_err(HeleosError::Io)?.len() > FOUNDATION_MAX_INPUT_BYTES {
        return Err(HeleosError::ResourceLimit);
    }
    file.seek(SeekFrom::Start(0)).map_err(HeleosError::Io)?;
    let mut hasher = Sha256::new();
    let mut length = 0_u64;
    let mut buffer = [0_u8; STREAM_BUFFER_BYTES];
    loop {
        let remaining = FOUNDATION_MAX_INPUT_BYTES
            .saturating_add(1)
            .saturating_sub(length);
        if remaining == 0 {
            return Err(HeleosError::ResourceLimit);
        }
        let request = buffer.len().min(remaining as usize);
        let read = file.read(&mut buffer[..request]).map_err(HeleosError::Io)?;
        if read == 0 {
            break;
        }
        length = length
            .checked_add(read as u64)
            .ok_or(HeleosError::ResourceLimit)?;
        hasher.update(&buffer[..read]);
    }
    Ok((Sha256Digest::from_bytes(hasher.finalize().into()), length))
}

fn hash_bounded_reader<R: Read>(reader: &mut R, max_input: u64) -> Result<(Sha256Digest, u64)> {
    let mut hasher = Sha256::new();
    let mut length = 0_u64;
    let mut buffer = [0_u8; STREAM_BUFFER_BYTES];
    loop {
        let remaining = max_input.saturating_add(1).saturating_sub(length);
        if remaining == 0 {
            return Err(HeleosError::ResourceLimit);
        }
        let request = buffer.len().min(remaining as usize);
        let read = reader
            .read(&mut buffer[..request])
            .map_err(HeleosError::Io)?;
        if read == 0 {
            break;
        }
        length += read as u64;
        if length > max_input {
            return Err(HeleosError::ResourceLimit);
        }
        hasher.update(&buffer[..read]);
    }
    Ok((Sha256Digest::from_bytes(hasher.finalize().into()), length))
}

fn stream_to_staging<R: Read, F: FaultInjector>(
    reader: &mut R,
    staging: &mut CapFile,
    max_input: u64,
    retain_limit: u64,
    faults: &F,
) -> Result<(Sha256Digest, u64, bool)> {
    let mut hasher = Sha256::new();
    let mut length = 0_u64;
    let mut retained_all = true;
    let mut buffer = [0_u8; STREAM_BUFFER_BYTES];
    loop {
        let remaining = max_input.saturating_add(1).saturating_sub(length);
        if remaining == 0 {
            return Err(HeleosError::ResourceLimit);
        }
        let request = buffer.len().min(remaining as usize);
        let read = reader
            .read(&mut buffer[..request])
            .map_err(HeleosError::Io)?;
        if read == 0 {
            break;
        }
        length += read as u64;
        if length > max_input {
            return Err(HeleosError::ResourceLimit);
        }
        hasher.update(&buffer[..read]);
        if retained_all && length <= retain_limit {
            let write_result = match faults.write_error_kind() {
                Some(kind) => Err(io::Error::new(kind, "injected staging write failure")),
                None => staging.write_all(&buffer[..read]),
            };
            match write_result {
                Ok(()) => {}
                Err(error) if error.kind() == io::ErrorKind::StorageFull => {
                    retained_all = false;
                }
                Err(error) => return Err(HeleosError::Io(error)),
            }
        } else {
            retained_all = false;
        }
    }
    Ok((
        Sha256Digest::from_bytes(hasher.finalize().into()),
        length,
        retained_all,
    ))
}

fn ceil_tenth(total: u64) -> Result<u64> {
    total
        .checked_add(9)
        .map(|value| value / 10)
        .ok_or(HeleosError::ResourceLimit)
}

fn staging_capacity_is_sufficient(
    declared_staging: u64,
    total: u64,
    available: u64,
    allocation_granularity: u64,
) -> Result<bool> {
    if allocation_granularity == 0 {
        return Err(HeleosError::PolicyDenied);
    }
    let reserve = MINIMUM_FREE_RESERVE_BYTES.max(ceil_tenth(total)?);
    let needed = declared_staging
        .checked_add(reserve)
        .and_then(|value| value.checked_add(allocation_granularity))
        .ok_or(HeleosError::ResourceLimit)?;
    Ok(available >= needed)
}

fn publication_capacity_is_sufficient(
    total: u64,
    available: u64,
    allocation_granularity: u64,
) -> Result<bool> {
    if allocation_granularity == 0 {
        return Err(HeleosError::PolicyDenied);
    }
    let needed = MINIMUM_FREE_RESERVE_BYTES
        .max(ceil_tenth(total)?)
        .checked_add(allocation_granularity)
        .ok_or(HeleosError::ResourceLimit)?;
    Ok(available >= needed)
}

fn sync_directory(directory: &Dir) -> Result<()> {
    let file = directory
        .try_clone()
        .map_err(HeleosError::Io)?
        .into_std_file();
    match file.sync_all() {
        Ok(()) => Ok(()),
        #[cfg(windows)]
        Err(error) if windows_directory_sync_is_unsupported(error.raw_os_error().unwrap_or(0)) => {
            Ok(())
        }
        Err(error) => Err(HeleosError::Io(error)),
    }
}

fn sync_initial_directory<F: FaultInjector>(
    directory: &Dir,
    point: InitialSyncPoint,
    faults: F,
) -> Result<()> {
    if faults.fail_initial_sync(point) {
        return Err(HeleosError::Io(io::Error::other(
            "injected initial directory sync failure",
        )));
    }
    sync_directory(directory)
}

struct StagingGuard<'a> {
    directory: &'a Dir,
    name: OsString,
    file: Option<CapFile>,
    armed: bool,
    preserve: bool,
}

impl<'a> StagingGuard<'a> {
    fn new(directory: &'a Dir, name: OsString, file: CapFile) -> Self {
        Self {
            directory,
            name,
            file: Some(file),
            armed: true,
            preserve: false,
        }
    }

    fn file(&self) -> Result<&CapFile> {
        self.file.as_ref().ok_or(HeleosError::Integrity)
    }

    fn file_mut(&mut self) -> Result<&mut CapFile> {
        self.file.as_mut().ok_or(HeleosError::Integrity)
    }

    fn close_handle(&mut self) {
        self.file.take();
    }

    fn cleanup(&mut self) -> Result<()> {
        self.close_handle();
        if self.armed {
            self.directory
                .remove_file(&self.name)
                .map_err(HeleosError::Io)?;
            sync_directory(self.directory)?;
            self.armed = false;
        }
        Ok(())
    }

    fn preserve(&mut self) {
        self.preserve = true;
    }

    fn disarm(&mut self) {
        self.armed = false;
        self.preserve = false;
    }
}

impl Drop for StagingGuard<'_> {
    fn drop(&mut self) {
        self.close_handle();
        if self.armed && !self.preserve {
            let _ = self.directory.remove_file(&self.name);
            let _ = sync_directory(self.directory);
        }
    }
}

fn cleanup_with_error<T>(staging: &mut StagingGuard<'_>, error: HeleosError) -> Result<T> {
    staging.cleanup()?;
    Err(error)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum CapacityQuery {
    Total,
    Available,
    AllocationGranularity,
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum CapacityMismatch {
    BeforeTotal,
    BeforeAvailable,
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum PublicationCapacityPhase {
    BeforeShards,
    BeforeLink,
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum FaultPoint {
    Flush,
    FileSync,
    BeforeLink,
    AfterLink,
    StagingUnlink,
    FinalDirectorySync,
    StagingDirectorySync,
    FinalReopen,
    #[cfg(test)]
    DestinationSubstitution,
    #[cfg(test)]
    WinnerSubstitution,
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum InitialSyncPoint {
    Root,
    Objects,
    Sha256,
    Staging,
    Lock,
}

trait FaultInjector: Copy {
    fn fail_at(self, _: FaultPoint) -> bool {
        false
    }

    fn is_capacity_shortage(self) -> bool {
        false
    }

    fn capacity_mismatch(self) -> Option<CapacityMismatch> {
        None
    }

    fn allocation_error_kind(self) -> Option<io::ErrorKind> {
        None
    }

    fn write_error_kind(self) -> Option<io::ErrorKind> {
        None
    }

    fn fail_initial_sync(self, _: InitialSyncPoint) -> bool {
        false
    }

    fn publication_capacity_shortage(self, _: PublicationCapacityPhase) -> bool {
        false
    }

    fn staging_name(self, _: usize) -> String {
        format!("{}.partial", Uuid::new_v4())
    }

    #[cfg(test)]
    fn observe(self, _: OperationEvent) {}
}

#[derive(Clone, Copy)]
struct NoFault;

impl FaultInjector for NoFault {}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TestFault {
    CapacityShortage,
    AllocationFailure,
    ArbitraryAllocationFailure,
    WriteFailure,
    WriteEnospc,
    FlushFailure,
    FileSyncFailure,
    BeforeLink,
    AfterLink,
    StagingUnlinkFailure,
    FinalDirectorySyncFailure,
    StagingDirectorySyncFailure,
    FinalReopenFailure,
    DestinationSubstitution,
    WinnerSubstitution,
    RootMismatchBeforeTotalSpace,
    RootMismatchBeforeAvailableSpace,
    RootParentSyncFailure,
    ObjectsParentSyncFailure,
    Sha256ParentSyncFailure,
    StagingParentSyncFailure,
    LockParentSyncFailure,
    PublicationReserveShortageAfterShards,
    StagingNameCollision,
    ProcessPauseAfterExclusiveLock,
    ProcessPauseBeforeFinalLink,
    ProcessPauseAfterFinalLink,
    ProcessPauseAfterStagingUnlink,
}

#[cfg(test)]
impl FaultInjector for TestFault {
    fn fail_at(self, point: FaultPoint) -> bool {
        matches!(
            (self, point),
            (Self::FlushFailure, FaultPoint::Flush)
                | (Self::FileSyncFailure, FaultPoint::FileSync)
                | (Self::BeforeLink, FaultPoint::BeforeLink)
                | (Self::AfterLink, FaultPoint::AfterLink)
                | (Self::StagingUnlinkFailure, FaultPoint::StagingUnlink)
                | (
                    Self::FinalDirectorySyncFailure,
                    FaultPoint::FinalDirectorySync
                )
                | (
                    Self::StagingDirectorySyncFailure,
                    FaultPoint::StagingDirectorySync
                )
                | (Self::FinalReopenFailure, FaultPoint::FinalReopen)
                | (
                    Self::DestinationSubstitution,
                    FaultPoint::DestinationSubstitution
                )
                | (Self::WinnerSubstitution, FaultPoint::WinnerSubstitution)
        )
    }

    fn is_capacity_shortage(self) -> bool {
        matches!(self, Self::CapacityShortage)
    }

    fn capacity_mismatch(self) -> Option<CapacityMismatch> {
        match self {
            Self::RootMismatchBeforeTotalSpace => Some(CapacityMismatch::BeforeTotal),
            Self::RootMismatchBeforeAvailableSpace => Some(CapacityMismatch::BeforeAvailable),
            _ => None,
        }
    }

    fn allocation_error_kind(self) -> Option<io::ErrorKind> {
        match self {
            Self::AllocationFailure => Some(io::ErrorKind::StorageFull),
            Self::ArbitraryAllocationFailure => Some(io::ErrorKind::Other),
            _ => None,
        }
    }

    fn write_error_kind(self) -> Option<io::ErrorKind> {
        match self {
            Self::WriteFailure => Some(io::ErrorKind::Other),
            Self::WriteEnospc => Some(io::ErrorKind::StorageFull),
            _ => None,
        }
    }

    fn fail_initial_sync(self, point: InitialSyncPoint) -> bool {
        matches!(
            (self, point),
            (Self::RootParentSyncFailure, InitialSyncPoint::Root)
                | (Self::ObjectsParentSyncFailure, InitialSyncPoint::Objects)
                | (Self::Sha256ParentSyncFailure, InitialSyncPoint::Sha256)
                | (Self::StagingParentSyncFailure, InitialSyncPoint::Staging)
                | (Self::LockParentSyncFailure, InitialSyncPoint::Lock)
        )
    }

    fn publication_capacity_shortage(self, phase: PublicationCapacityPhase) -> bool {
        matches!(
            (self, phase),
            (
                Self::PublicationReserveShortageAfterShards,
                PublicationCapacityPhase::BeforeLink
            )
        )
    }

    fn staging_name(self, attempt: usize) -> String {
        if self == Self::StagingNameCollision && attempt == 0 {
            TEST_COLLIDING_STAGING_NAME.to_owned()
        } else {
            format!("{}.partial", Uuid::new_v4())
        }
    }

    fn observe(self, event: OperationEvent) {
        let should_pause = matches!(
            (self, event),
            (
                Self::ProcessPauseAfterExclusiveLock,
                OperationEvent::LockAcquired { exclusive: true }
            ) | (
                Self::ProcessPauseBeforeFinalLink,
                OperationEvent::BeforeFinalLink
            ) | (
                Self::ProcessPauseAfterFinalLink,
                OperationEvent::FinalLinkCreated
            ) | (
                Self::ProcessPauseAfterStagingUnlink,
                OperationEvent::StagingNameRemoved
            )
        );
        if should_pause {
            let ready = PathBuf::from(
                std::env::var_os("HELEOS_PRIVATE_VAULT_LOCK_READY")
                    .expect("child lock-ready marker path"),
            );
            let release = PathBuf::from(
                std::env::var_os("HELEOS_PRIVATE_VAULT_LOCK_RELEASE")
                    .expect("child lock-release marker path"),
            );
            std::fs::write(&ready, b"ready").expect("publish child lock-ready marker");
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
            while !release.exists() {
                assert!(
                    std::time::Instant::now() < deadline,
                    "timed out waiting for child lock release"
                );
                std::thread::sleep(std::time::Duration::from_millis(1));
            }
        }
    }
}

#[cfg(test)]
#[derive(Clone, Debug, Eq, PartialEq)]
enum OperationEvent {
    LockAttempt { exclusive: bool },
    LockAcquired { exclusive: bool },
    OperationEntered { exclusive: bool },
    StagingVerified,
    BeforeFinalLink,
    FinalLinkCreated,
    FinalDirectorySynced,
    StagingHandleClosed,
    StagingNameRemoved,
    StagingDirectorySynced,
    FinalReopened,
    FinalVerified,
    CapacityPathQuery(CapacityQuery, PathBuf),
}

#[cfg(test)]
fn is_publication_operation_event(event: &OperationEvent) -> bool {
    !matches!(
        event,
        OperationEvent::LockAttempt { .. }
            | OperationEvent::LockAcquired { .. }
            | OperationEvent::OperationEntered { .. }
            | OperationEvent::StagingVerified
            | OperationEvent::CapacityPathQuery(_, _)
    )
}

#[cfg(test)]
#[derive(Clone, Copy)]
enum LockPausePoint {
    ExclusiveLockAcquired,
    FinalLinkCreated,
}

#[cfg(test)]
#[derive(Clone, Copy)]
struct BlockingOperationFault<'a> {
    pause_at: LockPausePoint,
    reached: &'a std::sync::Barrier,
    release: &'a std::sync::Barrier,
    events: &'a std::sync::Mutex<Vec<OperationEvent>>,
}

#[cfg(test)]
impl FaultInjector for BlockingOperationFault<'_> {
    fn observe(self, event: OperationEvent) {
        self.events
            .lock()
            .expect("lock operation event log")
            .push(event.clone());
        let pauses = matches!(
            (self.pause_at, event),
            (
                LockPausePoint::ExclusiveLockAcquired,
                OperationEvent::LockAcquired { exclusive: true }
            ) | (
                LockPausePoint::FinalLinkCreated,
                OperationEvent::FinalLinkCreated
            )
        );
        if pauses {
            self.reached.wait();
            self.release.wait();
        }
    }
}

#[cfg(test)]
#[derive(Clone, Copy)]
struct EventChannelFault<'a> {
    sender: &'a std::sync::mpsc::Sender<OperationEvent>,
}

#[cfg(test)]
impl FaultInjector for EventChannelFault<'_> {
    fn observe(self, event: OperationEvent) {
        let _ = self.sender.send(event);
    }
}

#[cfg(all(test, windows))]
#[derive(Clone, Copy)]
struct WindowsCapacitySubstitutionFault<'a> {
    root: &'a std::path::Path,
    alternate: &'a std::path::Path,
    events: &'a std::sync::Mutex<Vec<OperationEvent>>,
}

#[cfg(all(test, windows))]
impl FaultInjector for WindowsCapacitySubstitutionFault<'_> {
    fn observe(self, event: OperationEvent) {
        self.events
            .lock()
            .expect("lock Windows capacity event log")
            .push(event.clone());
        if matches!(
            event,
            OperationEvent::CapacityPathQuery(CapacityQuery::Total, _)
                | OperationEvent::CapacityPathQuery(CapacityQuery::Available, _)
        ) {
            assert!(
                std::fs::rename(self.root, self.alternate).is_err(),
                "retained no-delete root must reject capacity-query substitution"
            );
            assert!(!self.alternate.exists());
        }
    }
}

#[cfg(all(test, windows))]
#[derive(Clone, Copy)]
struct WindowsLinkTransitionFault<'a> {
    reached: &'a [std::sync::Barrier; 3],
    release: &'a [std::sync::Barrier; 3],
}

#[cfg(all(test, windows))]
impl FaultInjector for WindowsLinkTransitionFault<'_> {
    fn observe(self, event: OperationEvent) {
        let stage = match event {
            OperationEvent::StagingVerified => Some(0),
            OperationEvent::FinalLinkCreated => Some(1),
            OperationEvent::FinalVerified => Some(2),
            _ => None,
        };
        if let Some(stage) = stage {
            self.reached[stage].wait();
            self.release[stage].wait();
        }
    }
}

#[cfg(test)]
struct TestChildGuard(Option<std::process::Child>);

#[cfg(test)]
impl TestChildGuard {
    fn wait(&mut self) -> io::Result<std::process::ExitStatus> {
        self.0
            .take()
            .ok_or_else(|| io::Error::other("child already waited"))?
            .wait()
    }

    fn kill_and_wait(&mut self) -> io::Result<std::process::ExitStatus> {
        let mut child = self
            .0
            .take()
            .ok_or_else(|| io::Error::other("child already waited"))?;
        child.kill()?;
        child.wait()
    }
}

#[cfg(test)]
impl Drop for TestChildGuard {
    fn drop(&mut self) {
        if let Some(child) = &mut self.0 {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
#[derive(Clone, Copy)]
struct RecordingFault<'a> {
    fault: Option<TestFault>,
    events: &'a std::cell::RefCell<Vec<OperationEvent>>,
}

#[cfg(test)]
impl FaultInjector for RecordingFault<'_> {
    fn fail_at(self, point: FaultPoint) -> bool {
        self.fault.is_some_and(|fault| fault.fail_at(point))
    }

    fn is_capacity_shortage(self) -> bool {
        self.fault.is_some_and(TestFault::is_capacity_shortage)
    }

    fn capacity_mismatch(self) -> Option<CapacityMismatch> {
        self.fault.and_then(TestFault::capacity_mismatch)
    }

    fn allocation_error_kind(self) -> Option<io::ErrorKind> {
        self.fault.and_then(TestFault::allocation_error_kind)
    }

    fn write_error_kind(self) -> Option<io::ErrorKind> {
        self.fault.and_then(TestFault::write_error_kind)
    }

    fn fail_initial_sync(self, point: InitialSyncPoint) -> bool {
        self.fault
            .is_some_and(|fault| fault.fail_initial_sync(point))
    }

    fn publication_capacity_shortage(self, phase: PublicationCapacityPhase) -> bool {
        self.fault
            .is_some_and(|fault| fault.publication_capacity_shortage(phase))
    }

    fn staging_name(self, attempt: usize) -> String {
        self.fault.map_or_else(
            || format!("{}.partial", Uuid::new_v4()),
            |fault| fault.staging_name(attempt),
        )
    }

    fn observe(self, event: OperationEvent) {
        self.events.borrow_mut().push(event);
    }
}

#[cfg(test)]
impl Vault {
    fn open_with_test_fault(config: VaultConfig, fault: TestFault) -> Result<Self> {
        Self::open_internal(config, fault)
    }

    fn put_reader_with_test_fault<R: Read>(
        &self,
        reader: R,
        budget: VaultWriteBudget,
        fault: TestFault,
    ) -> Result<PutOutcome> {
        self.put_reader_internal(reader, budget, fault)
    }

    fn verify_with_test_fault<F: FaultInjector>(
        &self,
        digest: Sha256Digest,
        fault: F,
    ) -> Result<VaultVerification> {
        self.verify_internal(digest, fault)
    }

    fn put_reader_fault_events_for_test<R: Read>(
        &self,
        reader: R,
        budget: VaultWriteBudget,
        fault: TestFault,
    ) -> (Result<PutOutcome>, Vec<OperationEvent>) {
        let events = std::cell::RefCell::new(Vec::new());
        let result = self.put_reader_internal(
            reader,
            budget,
            RecordingFault {
                fault: Some(fault),
                events: &events,
            },
        );
        let mut events = events.into_inner();
        events.retain(is_publication_operation_event);
        (result, events)
    }

    fn put_reader_operation_events_for_test<R: Read>(
        &self,
        reader: R,
        budget: VaultWriteBudget,
    ) -> Result<Vec<OperationEvent>> {
        let (result, events) = {
            let events = std::cell::RefCell::new(Vec::new());
            let result = self.put_reader_internal(
                reader,
                budget,
                RecordingFault {
                    fault: None,
                    events: &events,
                },
            );
            let mut events = events.into_inner();
            events.retain(is_publication_operation_event);
            (result, events)
        };
        match result? {
            PutOutcome::Stored(_) => Ok(events),
            PutOutcome::QuotaRejected { .. } => Err(HeleosError::Quota),
        }
    }

    fn capacity_query_events_for_test(
        &self,
        fault: TestFault,
    ) -> (Result<bool>, Vec<OperationEvent>) {
        let events = std::cell::RefCell::new(Vec::new());
        let result = self.has_staging_capacity(
            1,
            RecordingFault {
                fault: Some(fault),
                events: &events,
            },
        );
        (result, events.into_inner())
    }
}

#[cfg(windows)]
const fn windows_directory_sync_is_unsupported(code: i32) -> bool {
    matches!(code, 1 | 5 | 6)
}

#[cfg(all(test, windows))]
#[derive(Clone, Copy)]
enum WindowsVaultHandleKind {
    DirectoryApply,
    LockApply,
    FinalVerify,
}

#[cfg(all(test, windows))]
#[derive(Debug, Eq, PartialEq)]
struct WindowsHandlePolicyForTest {
    access: u32,
    share: u32,
    flags: u32,
}

#[cfg(all(test, windows))]
const fn windows_handle_policy_for_test(
    kind: WindowsVaultHandleKind,
) -> WindowsHandlePolicyForTest {
    let handle_kind = match kind {
        WindowsVaultHandleKind::DirectoryApply => HandleKind::DirectoryApply,
        WindowsVaultHandleKind::LockApply => HandleKind::LockApply,
        WindowsVaultHandleKind::FinalVerify => HandleKind::FinalVerify,
    };
    WindowsHandlePolicyForTest {
        access: path::windows_access_mask(handle_kind),
        share: 0x0000_0003,
        flags: 0x0020_0000
            | if matches!(kind, WindowsVaultHandleKind::DirectoryApply) {
                0x0200_0000
            } else {
                0
            },
    }
}

#[cfg(all(test, windows))]
fn encode_relative_os_path_for_test(path: &std::path::Path) -> EncodedVaultPath {
    reconcile::encode_relative_os_path_for_test(path)
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;
    use std::path::{Path, PathBuf};

    use super::*;
    use crate::canonical_json;

    fn private_test_vault(label: &str) -> (tempfile::TempDir, PathBuf, Vault) {
        let parent = tempfile::Builder::new()
            .prefix(label)
            .tempdir()
            .expect("create private unit-test parent");
        crate::apply_private_permissions(parent.path()).expect("harden private unit-test parent");
        let parent_path = std::fs::canonicalize(parent.path()).expect("canonicalize parent");
        let root = parent_path.join("vault");
        let vault = Vault::open(VaultConfig {
            root: root.clone(),
            open_mode: VaultOpenMode::CreateNew,
        })
        .expect("create private unit-test vault");
        (parent, root, vault)
    }

    fn partial_names(root: &Path) -> Vec<PathBuf> {
        let mut paths: Vec<_> = std::fs::read_dir(root.join(".staging"))
            .expect("read staging directory")
            .map(|entry| entry.expect("read staging entry").path())
            .collect();
        paths.sort();
        paths
    }

    fn budget() -> VaultWriteBudget {
        VaultWriteBudget::new(4096, 4096).expect("construct unit-test budget")
    }

    #[test]
    fn same_root_instance_cannot_enter_verification_while_writer_holds_exclusive_lock() {
        let (_parent, root, writer) = private_test_vault("heleos-vault-thread-lock-boundary-");
        let reader = Vault::open(VaultConfig {
            root: root.clone(),
            open_mode: VaultOpenMode::ExistingOnly,
        })
        .expect("open second same-root vault instance");
        let bytes = b"thread lock boundary";
        let expected = Sha256Digest::hash_reader(Cursor::new(bytes)).expect("hash bytes");
        let reached = std::sync::Barrier::new(2);
        let release = std::sync::Barrier::new(2);
        let writer_events = std::sync::Mutex::new(Vec::new());
        let (reader_events_tx, reader_events_rx) = std::sync::mpsc::channel();

        std::thread::scope(|scope| {
            let writer_thread = scope.spawn(|| {
                writer.put_reader_internal(
                    Cursor::new(bytes),
                    budget(),
                    BlockingOperationFault {
                        pause_at: LockPausePoint::ExclusiveLockAcquired,
                        reached: &reached,
                        release: &release,
                        events: &writer_events,
                    },
                )
            });
            reached.wait();
            assert!(partial_names(&root).is_empty());

            let reader_thread = scope.spawn(|| {
                reader.verify_with_test_fault(
                    expected,
                    EventChannelFault {
                        sender: &reader_events_tx,
                    },
                )
            });
            assert_eq!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_secs(5))
                    .expect("reader reaches lock attempt"),
                OperationEvent::LockAttempt { exclusive: false }
            );
            assert!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_millis(200))
                    .is_err(),
                "reader must not acquire the lock or enter verification"
            );
            release.wait();
            assert!(matches!(
                writer_thread.join().expect("join writer"),
                Ok(PutOutcome::Stored(_))
            ));
            assert_eq!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_secs(5))
                    .expect("reader acquires shared lock after release"),
                OperationEvent::LockAcquired { exclusive: false }
            );
            assert_eq!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_secs(5))
                    .expect("reader enters verification after release"),
                OperationEvent::OperationEntered { exclusive: false }
            );
            assert!(matches!(
                reader_thread.join().expect("join reader"),
                Ok(VaultVerification::Verified { digest, .. }) if digest == expected
            ));
        });
        let writer_events: Vec<_> = writer_events
            .lock()
            .expect("lock writer events")
            .iter()
            .filter(|event| !matches!(event, OperationEvent::CapacityPathQuery(_, _)))
            .cloned()
            .collect();
        assert_eq!(
            writer_events,
            vec![
                OperationEvent::LockAttempt { exclusive: true },
                OperationEvent::LockAcquired { exclusive: true },
                OperationEvent::OperationEntered { exclusive: true },
                OperationEvent::StagingVerified,
                OperationEvent::BeforeFinalLink,
                OperationEvent::FinalLinkCreated,
                OperationEvent::FinalDirectorySynced,
                OperationEvent::StagingHandleClosed,
                OperationEvent::StagingNameRemoved,
                OperationEvent::StagingDirectorySynced,
                OperationEvent::FinalReopened,
                OperationEvent::FinalVerified,
            ]
        );
    }

    #[test]
    fn verified_reader_cannot_observe_the_two_link_publication_window() {
        let (_parent, root, writer) = private_test_vault("heleos-vault-link-window-");
        let reader = Vault::open(VaultConfig {
            root: root.clone(),
            open_mode: VaultOpenMode::ExistingOnly,
        })
        .expect("open reader instance");
        let bytes = b"two-link publication window";
        let expected = Sha256Digest::hash_reader(Cursor::new(bytes)).expect("hash bytes");
        let reached = std::sync::Barrier::new(2);
        let release = std::sync::Barrier::new(2);
        let writer_events = std::sync::Mutex::new(Vec::new());
        let (reader_events_tx, reader_events_rx) = std::sync::mpsc::channel();

        std::thread::scope(|scope| {
            let writer_thread = scope.spawn(|| {
                writer.put_reader_internal(
                    Cursor::new(bytes),
                    budget(),
                    BlockingOperationFault {
                        pause_at: LockPausePoint::FinalLinkCreated,
                        reached: &reached,
                        release: &release,
                        events: &writer_events,
                    },
                )
            });
            reached.wait();
            let partial = partial_names(&root);
            assert_eq!(partial.len(), 1);
            let partial_name = partial[0].file_name().expect("partial basename");
            let partial_handle = open_final_file(&writer.staging_dir, partial_name)
                .expect("open retained partial during link window")
                .file;
            assert_eq!(
                link_count(&partial_handle).expect("read retained link count"),
                2
            );

            let reader_thread = scope.spawn(|| {
                reader.verify_with_test_fault(
                    expected,
                    EventChannelFault {
                        sender: &reader_events_tx,
                    },
                )
            });
            assert_eq!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_secs(5))
                    .expect("reader reaches lock attempt"),
                OperationEvent::LockAttempt { exclusive: false }
            );
            assert!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_millis(200))
                    .is_err(),
                "verified reader must remain outside the publication interval"
            );
            drop(partial_handle);
            release.wait();
            assert!(matches!(
                writer_thread.join().expect("join writer"),
                Ok(PutOutcome::Stored(_))
            ));
            assert_eq!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_secs(5))
                    .expect("reader acquires shared lock after publication"),
                OperationEvent::LockAcquired { exclusive: false }
            );
            assert_eq!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_secs(5))
                    .expect("reader enters after publication"),
                OperationEvent::OperationEntered { exclusive: false }
            );
            assert!(matches!(
                reader_thread.join().expect("join reader"),
                Ok(VaultVerification::Verified { digest, .. }) if digest == expected
            ));
        });
        assert!(partial_names(&root).is_empty());
    }

    #[test]
    fn vault_process_lock_barrier_helper() {
        let Some(root) = std::env::var_os("HELEOS_PRIVATE_VAULT_LOCK_ROOT") else {
            return;
        };
        let vault = Vault::open(VaultConfig {
            root: PathBuf::from(root),
            open_mode: VaultOpenMode::ExistingOnly,
        })
        .expect("child opens existing vault");
        let fault = match std::env::var("HELEOS_PRIVATE_VAULT_LOCK_PHASE").as_deref() {
            Ok("before-link") => TestFault::ProcessPauseBeforeFinalLink,
            Ok("after-link") => TestFault::ProcessPauseAfterFinalLink,
            Ok("after-unlink") => TestFault::ProcessPauseAfterStagingUnlink,
            Ok("after-lock") | Err(std::env::VarError::NotPresent) => {
                TestFault::ProcessPauseAfterExclusiveLock
            }
            Ok(_) | Err(std::env::VarError::NotUnicode(_)) => {
                panic!("invalid private process lock phase")
            }
        };
        vault
            .put_reader_with_test_fault(Cursor::new(b"process lock boundary"), budget(), fault)
            .expect("child publishes after release");
    }

    #[test]
    fn independent_process_blocks_before_verification_until_writer_releases_lock() {
        let (_parent, root, reader) = private_test_vault("heleos-vault-process-lock-boundary-");
        let ready = root.parent().expect("root parent").join("child-ready");
        let release = root.parent().expect("root parent").join("child-release");
        let child = std::process::Command::new(
            std::env::current_exe().expect("locate library test executable"),
        )
        .arg("--exact")
        .arg("vault::tests::vault_process_lock_barrier_helper")
        .arg("--nocapture")
        .env("HELEOS_PRIVATE_VAULT_LOCK_ROOT", &root)
        .env("HELEOS_PRIVATE_VAULT_LOCK_READY", &ready)
        .env("HELEOS_PRIVATE_VAULT_LOCK_RELEASE", &release)
        .spawn()
        .expect("spawn lock-holder child");
        let mut child = TestChildGuard(Some(child));
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        while !ready.exists() {
            assert!(
                std::time::Instant::now() < deadline,
                "child did not acquire its exclusive lock"
            );
            std::thread::sleep(std::time::Duration::from_millis(1));
        }
        assert!(partial_names(&root).is_empty());

        let expected = Sha256Digest::hash_reader(Cursor::new(b"process lock boundary"))
            .expect("hash process bytes");
        let (reader_events_tx, reader_events_rx) = std::sync::mpsc::channel();
        std::thread::scope(|scope| {
            let reader_thread = scope.spawn(|| {
                reader.verify_with_test_fault(
                    expected,
                    EventChannelFault {
                        sender: &reader_events_tx,
                    },
                )
            });
            assert_eq!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_secs(5))
                    .expect("parent reaches shared-lock attempt"),
                OperationEvent::LockAttempt { exclusive: false }
            );
            assert!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_millis(200))
                    .is_err(),
                "parent must block on the child process's exclusive lock"
            );
            std::fs::write(&release, b"release").expect("release lock-holder child");
            assert!(child.wait().expect("wait for lock-holder child").success());
            assert_eq!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_secs(5))
                    .expect("parent acquires shared lock after child release"),
                OperationEvent::LockAcquired { exclusive: false }
            );
            assert_eq!(
                reader_events_rx
                    .recv_timeout(std::time::Duration::from_secs(5))
                    .expect("parent enters verification after child release"),
                OperationEvent::OperationEntered { exclusive: false }
            );
            assert!(matches!(
                reader_thread.join().expect("join parent reader"),
                Ok(VaultVerification::Verified { digest, .. }) if digest == expected
            ));
        });
    }

    #[test]
    fn capacity_shortage_before_staging_hashes_to_quota_evidence_without_a_partial() {
        let (_parent, root, vault) = private_test_vault("heleos-vault-capacity-fault-");
        let bytes = b"capacity evidence";
        let expected = crate::Sha256Digest::hash_reader(Cursor::new(bytes)).expect("hash bytes");
        let outcome = vault
            .put_reader_with_test_fault(Cursor::new(bytes), budget(), TestFault::CapacityShortage)
            .expect("capacity shortage returns quota evidence");
        assert!(matches!(
            outcome,
            PutOutcome::QuotaRejected {
                digest,
                byte_length
            } if digest == expected && byte_length == bytes.len() as u64
        ));
        assert!(partial_names(&root).is_empty());
        assert!(!root.join(Vault::object_key(expected)).exists());
    }

    #[test]
    fn reported_allocation_granularity_controls_the_exact_capacity_boundary() {
        let total = 100 * GIBIBYTE;
        let declared_staging = 1;
        let granularity = 64 * 1024;
        let required = MINIMUM_FREE_RESERVE_BYTES + declared_staging + granularity;
        assert!(
            staging_capacity_is_sufficient(declared_staging, total, required, granularity)
                .expect("exact capacity boundary is valid")
        );
        assert!(
            !staging_capacity_is_sufficient(declared_staging, total, required - 1, granularity)
                .expect("one byte below the capacity boundary is valid arithmetic")
        );
        assert!(matches!(
            staging_capacity_is_sufficient(declared_staging, total, required, 0),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn final_link_capacity_includes_one_reported_allocation_unit() {
        let total = 100 * GIBIBYTE;
        let granularity = 64 * 1024;
        let required = MINIMUM_FREE_RESERVE_BYTES + granularity;
        assert!(
            publication_capacity_is_sufficient(total, required, granularity)
                .expect("exact publication boundary is valid")
        );
        assert!(
            !publication_capacity_is_sufficient(total, required - 1, granularity)
                .expect("one byte below publication boundary is valid arithmetic")
        );
    }

    #[test]
    fn zero_declared_staging_never_invokes_the_platform_allocator() {
        let called = std::cell::Cell::new(false);
        allocate_if_nonzero(0, || {
            called.set(true);
            Err(io::Error::other("allocator must not be called"))
        })
        .expect("zero bytes require no platform allocation");
        assert!(!called.get());
    }

    #[test]
    fn over_cap_retained_file_is_rejected_before_seek_or_read() {
        let file = tempfile::tempfile().expect("create sparse over-cap file");
        file.set_len(FOUNDATION_MAX_INPUT_BYTES + 1)
            .expect("size sparse over-cap file");
        let mut file = CapFile::from_std(file);
        file.seek(SeekFrom::Start(17))
            .expect("park sparse file cursor");
        assert!(matches!(
            hash_cap_file(&mut file),
            Err(HeleosError::ResourceLimit)
        ));
        assert_eq!(
            file.stream_position().expect("read cursor after rejection"),
            17
        );
    }

    #[test]
    fn allocation_enospc_continues_hashing_and_removes_its_partial() {
        let (_parent, root, vault) = private_test_vault("heleos-vault-allocation-fault-");
        let bytes = b"allocation evidence";
        let expected = crate::Sha256Digest::hash_reader(Cursor::new(bytes)).expect("hash bytes");
        let outcome = vault
            .put_reader_with_test_fault(Cursor::new(bytes), budget(), TestFault::AllocationFailure)
            .expect("allocation failure returns quota evidence");
        assert!(matches!(
            outcome,
            PutOutcome::QuotaRejected {
                digest,
                byte_length
            } if digest == expected && byte_length == bytes.len() as u64
        ));
        assert!(partial_names(&root).is_empty());
    }

    #[test]
    fn arbitrary_allocation_error_continues_hashing_and_removes_its_partial() {
        let (_parent, root, vault) = private_test_vault("heleos-vault-allocation-other-");
        let bytes = b"arbitrary allocation evidence";
        let expected = crate::Sha256Digest::hash_reader(Cursor::new(bytes)).expect("hash bytes");
        let outcome = vault
            .put_reader_with_test_fault(
                Cursor::new(bytes),
                budget(),
                TestFault::ArbitraryAllocationFailure,
            )
            .expect("any allocation failure returns quota evidence");
        assert!(matches!(
            outcome,
            PutOutcome::QuotaRejected {
                digest,
                byte_length
            } if digest == expected && byte_length == bytes.len() as u64
        ));
        assert!(partial_names(&root).is_empty());
        assert!(!root.join(Vault::object_key(expected)).exists());
    }

    #[test]
    fn staging_write_enospc_continues_bounded_hashing_to_exact_quota_evidence() {
        let (_parent, root, vault) = private_test_vault("heleos-vault-write-enospc-");
        let bytes = vec![0x5a; STREAM_BUFFER_BYTES * 2 + 17];
        let expected =
            crate::Sha256Digest::hash_reader(Cursor::new(&bytes)).expect("hash full input");
        let outcome = vault
            .put_reader_with_test_fault(
                Cursor::new(&bytes),
                VaultWriteBudget::new(bytes.len() as u64, bytes.len() as u64)
                    .expect("write-ENOSPC budget"),
                TestFault::WriteEnospc,
            )
            .expect("write ENOSPC returns known evidence");
        assert!(matches!(
            outcome,
            PutOutcome::QuotaRejected {
                digest,
                byte_length
            } if digest == expected && byte_length == bytes.len() as u64
        ));
        assert!(partial_names(&root).is_empty());
        assert!(!root.join(Vault::object_key(expected)).exists());
    }

    #[test]
    fn generated_staging_name_collision_retries_without_deleting_the_existing_partial() {
        let (_parent, root, vault) = private_test_vault("heleos-vault-staging-collision-");
        let hostile_path = root.join(STAGING_NAME).join(TEST_COLLIDING_STAGING_NAME);
        std::fs::write(&hostile_path, b"preexisting abandoned bytes")
            .expect("create colliding partial fixture");
        crate::apply_private_permissions(&hostile_path).expect("harden colliding partial fixture");

        let bytes = b"caller-owned evidence";
        let expected = crate::Sha256Digest::hash_reader(Cursor::new(bytes)).expect("hash bytes");
        let stored = vault
            .put_reader_with_test_fault(
                Cursor::new(bytes),
                budget(),
                TestFault::StagingNameCollision,
            )
            .expect("retry after staging UUID collision");
        assert!(matches!(
            stored,
            PutOutcome::Stored(StoredObject {
                digest,
                newly_published: true,
                ..
            }) if digest == expected
        ));
        assert_eq!(
            std::fs::read(&hostile_path).expect("read preexisting partial after collision"),
            b"preexisting abandoned bytes"
        );
        assert_eq!(partial_names(&root), vec![hostile_path]);
    }

    #[test]
    fn reserve_exhaustion_after_shard_creation_prevents_the_final_link() {
        let (_parent, root, vault) = private_test_vault("heleos-vault-final-reserve-");
        let bytes = b"final publication reserve";
        let expected = crate::Sha256Digest::hash_reader(Cursor::new(bytes)).expect("hash bytes");
        let outcome = vault
            .put_reader_with_test_fault(
                Cursor::new(bytes),
                budget(),
                TestFault::PublicationReserveShortageAfterShards,
            )
            .expect("post-shard reserve shortage returns evidence");
        assert!(matches!(
            outcome,
            PutOutcome::QuotaRejected {
                digest,
                byte_length
            } if digest == expected && byte_length == bytes.len() as u64
        ));
        assert!(partial_names(&root).is_empty());
        assert!(!root.join(Vault::object_key(expected)).exists());
    }

    #[test]
    fn first_use_syncs_each_mutated_parent_in_order_and_propagates_failures() {
        let cases = [
            (
                TestFault::RootParentSyncFailure,
                [true, false, false, false, false],
            ),
            (
                TestFault::ObjectsParentSyncFailure,
                [true, true, false, false, false],
            ),
            (
                TestFault::Sha256ParentSyncFailure,
                [true, true, true, false, false],
            ),
            (
                TestFault::StagingParentSyncFailure,
                [true, true, true, true, false],
            ),
            (
                TestFault::LockParentSyncFailure,
                [true, true, true, true, true],
            ),
        ];
        for (fault, expected) in cases {
            let parent = tempfile::Builder::new()
                .prefix("heleos-vault-first-use-sync-")
                .tempdir()
                .expect("create sync-fault parent");
            crate::apply_private_permissions(parent.path()).expect("harden sync-fault parent");
            let root = std::fs::canonicalize(parent.path())
                .expect("canonicalize sync-fault parent")
                .join("vault");
            let result = Vault::open_with_test_fault(
                VaultConfig {
                    root: root.clone(),
                    open_mode: VaultOpenMode::CreateNew,
                },
                fault,
            );
            assert!(matches!(result, Err(HeleosError::Io(_))));
            assert_eq!(
                [
                    root.exists(),
                    root.join(OBJECTS_NAME).exists(),
                    root.join(OBJECTS_NAME).join(SHA256_NAME).exists(),
                    root.join(STAGING_NAME).exists(),
                    root.join(LOCK_NAME).exists(),
                ],
                expected
            );
        }
    }

    #[test]
    fn write_flush_file_sync_and_prelink_interruptions_clean_the_caller_partial() {
        for fault in [
            TestFault::WriteFailure,
            TestFault::FlushFailure,
            TestFault::FileSyncFailure,
            TestFault::BeforeLink,
        ] {
            let (_parent, root, vault) = private_test_vault("heleos-vault-prelink-fault-");
            let result =
                vault.put_reader_with_test_fault(Cursor::new(b"prelink evidence"), budget(), fault);
            assert!(result.is_err());
            assert!(partial_names(&root).is_empty());
            assert!(
                !root
                    .join(Vault::object_key(
                        crate::Sha256Digest::hash_reader(Cursor::new(b"prelink evidence"))
                            .expect("hash bytes")
                    ))
                    .exists()
            );
        }
    }

    #[test]
    fn publication_faults_stop_at_distinct_real_operation_boundaries() {
        let cases: &[(TestFault, &[OperationEvent], bool)] = &[
            (
                TestFault::BeforeLink,
                &[OperationEvent::BeforeFinalLink],
                false,
            ),
            (
                TestFault::FinalDirectorySyncFailure,
                &[
                    OperationEvent::BeforeFinalLink,
                    OperationEvent::FinalLinkCreated,
                ],
                true,
            ),
            (
                TestFault::AfterLink,
                &[
                    OperationEvent::BeforeFinalLink,
                    OperationEvent::FinalLinkCreated,
                    OperationEvent::FinalDirectorySynced,
                ],
                true,
            ),
            (
                TestFault::StagingUnlinkFailure,
                &[
                    OperationEvent::BeforeFinalLink,
                    OperationEvent::FinalLinkCreated,
                    OperationEvent::FinalDirectorySynced,
                    OperationEvent::StagingHandleClosed,
                ],
                true,
            ),
            (
                TestFault::StagingDirectorySyncFailure,
                &[
                    OperationEvent::BeforeFinalLink,
                    OperationEvent::FinalLinkCreated,
                    OperationEvent::FinalDirectorySynced,
                    OperationEvent::StagingHandleClosed,
                    OperationEvent::StagingNameRemoved,
                ],
                false,
            ),
            (
                TestFault::FinalReopenFailure,
                &[
                    OperationEvent::BeforeFinalLink,
                    OperationEvent::FinalLinkCreated,
                    OperationEvent::FinalDirectorySynced,
                    OperationEvent::StagingHandleClosed,
                    OperationEvent::StagingNameRemoved,
                    OperationEvent::StagingDirectorySynced,
                ],
                false,
            ),
        ];
        for (fault, expected_events, partial_survives) in cases {
            let (_parent, root, vault) = private_test_vault("heleos-vault-postlink-fault-");
            let (result, events) = vault.put_reader_fault_events_for_test(
                Cursor::new(b"postlink evidence"),
                budget(),
                *fault,
            );
            assert!(result.is_err());
            assert_eq!(&events, expected_events);
            assert_eq!(!partial_names(&root).is_empty(), *partial_survives);
            let report = vault
                .reconcile(&VaultInventory::try_from_entries([]).expect("empty inventory"))
                .expect("reconcile postlink state");
            assert_eq!(
                report
                    .findings
                    .iter()
                    .any(|finding| matches!(finding, ReconciliationFinding::StagingPartial { .. })),
                *partial_survives
            );
            assert_eq!(
                report.findings.iter().any(|finding| matches!(
                    finding,
                    ReconciliationFinding::UnexpectedLinkCount { link_count: 2, .. }
                )),
                *partial_survives
            );
        }
    }

    #[test]
    fn destination_and_winner_substitution_windows_fail_closed_without_overwrite() {
        for fault in [
            TestFault::DestinationSubstitution,
            TestFault::WinnerSubstitution,
        ] {
            let (_parent, root, vault) = private_test_vault("heleos-vault-substitution-fault-");
            let result = vault.put_reader_with_test_fault(
                Cursor::new(b"substitution evidence"),
                budget(),
                fault,
            );
            assert!(matches!(
                result,
                Err(crate::HeleosError::PolicyDenied | crate::HeleosError::Integrity)
            ));
            let report = vault
                .reconcile(&VaultInventory::try_from_entries([]).expect("empty inventory"))
                .expect("reconcile substituted state");
            canonical_json(&report).expect("canonicalize substituted report");
            assert!(!report.findings.is_empty());
            assert!(root.exists());
        }
    }

    #[test]
    fn cleanup_closes_before_unlink_and_syncs_after_namespace_removal() {
        let (_parent, root, vault) = private_test_vault("heleos-vault-cleanup-order-");
        let events = vault
            .put_reader_operation_events_for_test(Cursor::new(b"cleanup evidence"), budget())
            .expect("record private cleanup events");
        assert_eq!(
            events,
            vec![
                OperationEvent::BeforeFinalLink,
                OperationEvent::FinalLinkCreated,
                OperationEvent::FinalDirectorySynced,
                OperationEvent::StagingHandleClosed,
                OperationEvent::StagingNameRemoved,
                OperationEvent::StagingDirectorySynced,
                OperationEvent::FinalReopened,
                OperationEvent::FinalVerified,
            ]
        );
        assert!(partial_names(&root).is_empty());
    }

    #[test]
    fn capacity_queries_stop_before_using_an_identity_mismatched_root_path() {
        for fault in [
            TestFault::RootMismatchBeforeTotalSpace,
            TestFault::RootMismatchBeforeAvailableSpace,
        ] {
            let (_parent, root, vault) = private_test_vault("heleos-vault-capacity-race-");
            let (result, events) = vault.capacity_query_events_for_test(fault);
            assert!(matches!(result, Err(HeleosError::PolicyDenied)));
            let expected = if fault == TestFault::RootMismatchBeforeTotalSpace {
                Vec::new()
            } else {
                vec![
                    OperationEvent::CapacityPathQuery(CapacityQuery::Total, root.clone()),
                    OperationEvent::CapacityPathQuery(
                        CapacityQuery::AllocationGranularity,
                        root.clone(),
                    ),
                ]
            };
            assert_eq!(events, expected);
            assert!(events.iter().all(|event| matches!(
                event,
                OperationEvent::CapacityPathQuery(_, path) if path == &root
            )));
        }
    }

    #[cfg(unix)]
    #[test]
    fn hostile_unix_names_encode_losslessly_without_filesystem_normalization() {
        use std::os::unix::ffi::OsStringExt;

        let path = PathBuf::from(OsString::from_vec(vec![
            0xff, b'.', b'p', b'a', b'r', b't', b'i', b'a', b'l',
        ]));
        let encoded = reconcile::encode_relative_os_path_for_test(&path);
        assert_eq!(encoded.encoding, "unix_bytes_hex");
        assert_eq!(encoded.value, "ff2e7061727469616c");
    }

    #[cfg(windows)]
    #[test]
    fn windows_vault_handle_masks_flags_and_sharing_are_exact() {
        assert_eq!(
            windows_handle_policy_for_test(WindowsVaultHandleKind::DirectoryApply),
            WindowsHandlePolicyForTest {
                access: 0x8006_0080,
                share: 0x0000_0003,
                flags: 0x0220_0000,
            }
        );
        assert_eq!(
            windows_handle_policy_for_test(WindowsVaultHandleKind::LockApply),
            WindowsHandlePolicyForTest {
                access: 0xc006_0080,
                share: 0x0000_0003,
                flags: 0x0020_0000,
            }
        );
        assert_eq!(
            windows_handle_policy_for_test(WindowsVaultHandleKind::FinalVerify),
            WindowsHandlePolicyForTest {
                access: 0x8002_0080,
                share: 0x0000_0003,
                flags: 0x0020_0000,
            }
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_lock_marker_mismatch_is_rejected_on_the_retained_handle() {
        let (_parent, _root, vault) = private_test_vault("heleos-vault-windows-marker-");
        let retained = CapFile::from_std(
            vault
                .lock_file
                .try_clone()
                .expect("clone retained vault lock handle"),
        );
        let wrong_marker = vault.lock_marker.mismatched_for_test();
        assert!(matches!(
            recheck_file_name(
                &vault.root_dir,
                OsStr::new(LOCK_NAME),
                &retained,
                wrong_marker,
                HandleKind::LockApply,
            ),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[cfg(windows)]
    #[test]
    fn windows_retained_staging_handle_observes_exact_one_two_one_link_transition() {
        let (_parent, root, vault) = private_test_vault("heleos-vault-windows-links-");
        let bytes = b"Windows retained link transition";
        let expected = Sha256Digest::hash_reader(Cursor::new(bytes)).expect("hash link bytes");
        let reached: [std::sync::Barrier; 3] = std::array::from_fn(|_| std::sync::Barrier::new(2));
        let release: [std::sync::Barrier; 3] = std::array::from_fn(|_| std::sync::Barrier::new(2));
        std::thread::scope(|scope| {
            let writer = scope.spawn(|| {
                vault.put_reader_internal(
                    Cursor::new(bytes),
                    budget(),
                    WindowsLinkTransitionFault {
                        reached: &reached,
                        release: &release,
                    },
                )
            });

            reached[0].wait();
            let partial = partial_names(&root);
            assert_eq!(partial.len(), 1);
            let partial_name = partial[0].file_name().expect("staging basename");
            let retained = open_final_file(&vault.staging_dir, partial_name)
                .expect("open retained one-link staging source")
                .file;
            assert_eq!(link_count(&retained).expect("staging nlink before link"), 1);
            assert!(!root.join(Vault::object_key(expected)).exists());
            drop(retained);
            release[0].wait();

            reached[1].wait();
            let partial = partial_names(&root);
            assert_eq!(partial.len(), 1);
            let retained = open_final_file(
                &vault.staging_dir,
                partial[0].file_name().expect("linked staging basename"),
            )
            .expect("open retained two-link staging source")
            .file;
            assert_eq!(link_count(&retained).expect("staging nlink after link"), 2);
            assert!(root.join(Vault::object_key(expected)).exists());
            drop(retained);
            release[1].wait();

            reached[2].wait();
            assert!(partial_names(&root).is_empty());
            let retained = vault
                .open_digest_file(expected)
                .expect("open retained final after staging unlink");
            assert_eq!(link_count(&retained).expect("final nlink after unlink"), 1);
            drop(retained);
            release[2].wait();
            assert!(matches!(
                writer.join().expect("join link-transition writer"),
                Ok(PutOutcome::Stored(StoredObject {
                    digest,
                    newly_published: true,
                    ..
                })) if digest == expected
            ));
        });
    }

    #[cfg(windows)]
    #[test]
    fn windows_capacity_queries_bracket_actual_root_substitution_attempts() {
        let (_parent, root, vault) = private_test_vault("heleos-vault-windows-capacity-");
        let alternate = root.with_file_name("substituted-vault");
        let events = std::sync::Mutex::new(Vec::new());
        let _ = vault
            .has_staging_capacity(
                1,
                WindowsCapacitySubstitutionFault {
                    root: &root,
                    alternate: &alternate,
                    events: &events,
                },
            )
            .expect("query only the retained configured-root volume");
        let events = events.lock().expect("lock capacity events");
        assert_eq!(events.len(), 3);
        assert!(events.iter().all(|event| matches!(
            event,
            OperationEvent::CapacityPathQuery(_, path) if path == &root
        )));
        assert!(matches!(
            events[0],
            OperationEvent::CapacityPathQuery(CapacityQuery::Total, _)
        ));
        assert!(matches!(
            events[1],
            OperationEvent::CapacityPathQuery(CapacityQuery::AllocationGranularity, _)
        ));
        assert!(matches!(
            events[2],
            OperationEvent::CapacityPathQuery(CapacityQuery::Available, _)
        ));
        assert!(!alternate.exists());
    }

    #[test]
    fn windows_process_kill_before_link_after_link_and_after_unlink_is_reconciled() {
        for (phase, expect_partial, expect_two_links, expect_verified) in [
            ("before-link", true, false, false),
            ("after-link", true, true, false),
            ("after-unlink", false, false, true),
        ] {
            let (_parent, root, vault) = private_test_vault("heleos-vault-windows-kill-");
            let expected = Sha256Digest::hash_reader(Cursor::new(b"process lock boundary"))
                .expect("hash process-kill fixture");
            let ready = root
                .parent()
                .expect("root parent")
                .join(format!("{phase}-ready"));
            let release = root
                .parent()
                .expect("root parent")
                .join(format!("{phase}-release"));
            let child = std::process::Command::new(
                std::env::current_exe().expect("locate library test executable"),
            )
            .arg("--exact")
            .arg("vault::tests::vault_process_lock_barrier_helper")
            .arg("--nocapture")
            .env("HELEOS_PRIVATE_VAULT_LOCK_ROOT", &root)
            .env("HELEOS_PRIVATE_VAULT_LOCK_READY", &ready)
            .env("HELEOS_PRIVATE_VAULT_LOCK_RELEASE", &release)
            .env("HELEOS_PRIVATE_VAULT_LOCK_PHASE", phase)
            .spawn()
            .expect("spawn native process-kill fixture");
            let mut child = TestChildGuard(Some(child));
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
            while !ready.exists() {
                assert!(
                    std::time::Instant::now() < deadline,
                    "child did not reach {phase} boundary"
                );
                std::thread::sleep(std::time::Duration::from_millis(1));
            }
            let expected_partial_count = usize::from(expect_partial);
            assert_eq!(partial_names(&root).len(), expected_partial_count);
            assert_eq!(
                root.join(Vault::object_key(expected)).exists(),
                phase != "before-link"
            );
            let status = child.kill_and_wait().expect("kill and reap child");
            assert!(!status.success());

            let report = vault
                .reconcile(&VaultInventory::try_from_entries([]).expect("empty inventory"))
                .expect("reconcile process-kill boundary");
            assert_eq!(
                report
                    .findings
                    .iter()
                    .filter(|finding| {
                        matches!(finding, ReconciliationFinding::StagingPartial { .. })
                    })
                    .count(),
                expected_partial_count
            );
            assert_eq!(
                report.findings.iter().any(|finding| matches!(
                    finding,
                    ReconciliationFinding::UnexpectedLinkCount { link_count: 2, .. }
                )),
                expect_two_links
            );
            assert_eq!(
                report.findings.iter().any(|finding| matches!(
                    finding,
                    ReconciliationFinding::UnreferencedObject {
                        verification: VaultVerification::Verified { .. }
                    }
                )),
                expect_verified
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn windows_directory_sync_only_classifies_exact_unsupported_errors() {
        for code in [1, 5, 6] {
            assert!(windows_directory_sync_is_unsupported(code));
        }
        for code in [0, 2, 3, 4, 7, 87, 112] {
            assert!(!windows_directory_sync_is_unsupported(code));
        }
    }

    #[cfg(windows)]
    #[test]
    fn windows_actual_directory_sync_result_is_supported_or_exactly_classified() {
        let (_parent, _root, vault) = private_test_vault("heleos-vault-windows-dir-sync-");
        let directory = vault
            .staging_dir
            .try_clone()
            .expect("clone retained staging directory")
            .into_std_file();
        match directory.sync_all() {
            Ok(()) => {}
            Err(error) => assert!(
                windows_directory_sync_is_unsupported(error.raw_os_error().unwrap_or(0)),
                "unexpected native directory sync error: {error}"
            ),
        }
        sync_directory(&vault.staging_dir)
            .expect("production directory-sync policy accepts only supported/classified result");
    }

    #[cfg(windows)]
    #[test]
    fn hostile_windows_names_are_encoded_as_exact_utf16_units() {
        use std::os::windows::ffi::OsStringExt;

        let name = std::ffi::OsString::from_wide(&[0xd800, 0x0061, 0xdc00]);
        let encoded = encode_relative_os_path_for_test(Path::new(&name));
        assert_eq!(encoded.encoding, "windows_utf16_units_hex");
        assert_eq!(encoded.value, "d8000061dc00");
    }
}
