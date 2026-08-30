//! Bounded backup-container framing.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use ed25519_dalek::SigningKey;
use sha2::{Digest, Sha256};

use crate::{
    HeleosError, Result, Sha256Digest, apply_private_permissions, verify_private_permissions,
};

use super::manifest::{
    BACKUP_ENTRY_RECORD_BYTES, BackupDatabaseDescriptorV1, BackupManifestV1, MAX_BACKUP_BLOB_COUNT,
    MAX_BACKUP_DATABASE_BYTES, MAX_BACKUP_DECODED_BYTES, MAX_BACKUP_ENTRY_COUNT,
    MAX_BACKUP_ENTRY_TABLE_BYTES, MAX_BACKUP_MANIFEST_BYTES, sign_manifest,
    verify_manifest_signature,
};

const BACKUP_CONTAINER_MAGIC: &[u8; 8] = b"HELEOSB1";
const MAX_BACKUP_CIPHERTEXT_BYTES: u64 = 522 * 1024 * 1024 * 1024;
const DATABASE_ENTRY_KIND: u8 = 0;
const BLOB_ENTRY_KIND: u8 = 1;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum BackupEntryKind {
    Database,
    Blob,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct BackupEntryRecord {
    kind: BackupEntryKind,
    pub(super) sha256: Sha256Digest,
    pub(super) byte_length: u64,
}

impl BackupEntryRecord {
    pub(super) fn database(sha256: Sha256Digest, byte_length: u64) -> Result<Self> {
        if byte_length > MAX_BACKUP_DATABASE_BYTES {
            return Err(HeleosError::ResourceLimit);
        }
        Ok(Self {
            kind: BackupEntryKind::Database,
            sha256,
            byte_length,
        })
    }

    pub(super) fn blob(sha256: Sha256Digest, byte_length: u64) -> Result<Self> {
        if byte_length > 256 * 1024 * 1024 {
            return Err(HeleosError::ResourceLimit);
        }
        Ok(Self {
            kind: BackupEntryKind::Blob,
            sha256,
            byte_length,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct BackupEntryTable {
    records: Vec<BackupEntryRecord>,
    decoded_payload_byte_length: u64,
}

impl BackupEntryTable {
    pub(super) fn records(&self) -> &[BackupEntryRecord] {
        &self.records
    }

    pub(super) const fn decoded_payload_byte_length(&self) -> u64 {
        self.decoded_payload_byte_length
    }
}

pub(super) struct BackupPayload<'a> {
    record: BackupEntryRecord,
    reader: &'a mut dyn Read,
}

impl<'a> BackupPayload<'a> {
    pub(super) fn new<R: Read + 'a>(record: BackupEntryRecord, reader: &'a mut R) -> Self {
        Self { record, reader }
    }
}

struct CiphertextBoundedWriter<W> {
    inner: W,
    bytes_written: u64,
    maximum_bytes: u64,
}

impl<W> CiphertextBoundedWriter<W> {
    const fn new(inner: W, maximum_bytes: u64) -> Self {
        Self {
            inner,
            bytes_written: 0,
            maximum_bytes,
        }
    }

    fn into_inner(self) -> W {
        self.inner
    }
}

impl<W: Write> Write for CiphertextBoundedWriter<W> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let remaining = self.maximum_bytes.saturating_sub(self.bytes_written);
        if remaining == 0 && !bytes.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::FileTooLarge,
                "backup ciphertext exceeds its fixed bound",
            ));
        }
        let allowed = usize::try_from(remaining.min(bytes.len() as u64)).unwrap_or(bytes.len());
        let written = self.inner.write(&bytes[..allowed])?;
        self.bytes_written = self
            .bytes_written
            .checked_add(written as u64)
            .ok_or_else(|| io::Error::other("backup ciphertext length overflow"))?;
        Ok(written)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

pub(super) struct EncryptedBackupStaging {
    _ciphertext: File,
    ciphertext_path: PathBuf,
    directory: tempfile::TempDir,
}

impl EncryptedBackupStaging {
    #[cfg(test)]
    pub(super) fn ciphertext_path(&self) -> &Path {
        &self.ciphertext_path
    }

    #[cfg(test)]
    pub(super) fn root_path(&self) -> &Path {
        self.directory.path()
    }
}

#[cfg(test)]
#[derive(Clone, Copy)]
pub(super) enum BackupStagingTestFault {
    CorruptPlaintextBeforeVerification,
    CiphertextByteLimit(u64),
}

pub(super) struct ParsedPlaintextContainerHeader<R> {
    reader: R,
    entry_table: BackupEntryTable,
}

impl<R> ParsedPlaintextContainerHeader<R> {
    pub(super) const fn entry_table(&self) -> &BackupEntryTable {
        &self.entry_table
    }
}

fn read_exact_container(reader: &mut impl Read, bytes: &mut [u8]) -> Result<()> {
    reader.read_exact(bytes).map_err(|error| {
        if error.kind() == io::ErrorKind::UnexpectedEof {
            HeleosError::InvalidBackupContainer
        } else {
            HeleosError::Io(error)
        }
    })
}

fn read_u64(reader: &mut impl Read) -> Result<u64> {
    let mut bytes = [0_u8; 8];
    read_exact_container(reader, &mut bytes)?;
    Ok(u64::from_be_bytes(bytes))
}

fn read_bounded_bytes(reader: &mut impl Read, byte_length: u64, maximum: u64) -> Result<Vec<u8>> {
    if byte_length == 0 || byte_length > maximum {
        return Err(HeleosError::ResourceLimit);
    }
    let byte_length = usize::try_from(byte_length).map_err(|_| HeleosError::ResourceLimit)?;
    let mut bytes = Vec::new();
    bytes
        .try_reserve_exact(byte_length)
        .map_err(|_| HeleosError::ResourceLimit)?;
    bytes.resize(byte_length, 0);
    read_exact_container(reader, &mut bytes)?;
    Ok(bytes)
}

pub(super) fn validate_plaintext_container_length(
    manifest_byte_length: u64,
    entry_table_byte_length: u64,
    decoded_payload_byte_length: u64,
) -> Result<u64> {
    let fixed_byte_length = u64::try_from(BACKUP_CONTAINER_MAGIC.len() + 8 + 32 + 64 + 8)
        .map_err(|_| HeleosError::ResourceLimit)?;
    let total = fixed_byte_length
        .checked_add(manifest_byte_length)
        .and_then(|length| length.checked_add(entry_table_byte_length))
        .and_then(|length| length.checked_add(decoded_payload_byte_length))
        .ok_or(HeleosError::ResourceLimit)?;
    if total > MAX_BACKUP_DECODED_BYTES {
        return Err(HeleosError::ResourceLimit);
    }
    Ok(total)
}

pub(super) fn parse_plaintext_container_header<R: Read>(
    mut reader: R,
    trusted_signer: &[u8; 32],
) -> Result<ParsedPlaintextContainerHeader<R>> {
    let mut magic = [0_u8; BACKUP_CONTAINER_MAGIC.len()];
    read_exact_container(&mut reader, &mut magic)?;
    if &magic != BACKUP_CONTAINER_MAGIC {
        return Err(HeleosError::InvalidBackupContainer);
    }

    let manifest_byte_length = read_u64(&mut reader)?;
    let manifest_bytes = read_bounded_bytes(
        &mut reader,
        manifest_byte_length,
        u64::try_from(MAX_BACKUP_MANIFEST_BYTES).map_err(|_| HeleosError::ResourceLimit)?,
    )?;
    let manifest = BackupManifestV1::parse_canonical(&manifest_bytes)?;

    let mut embedded_signer = [0_u8; 32];
    read_exact_container(&mut reader, &mut embedded_signer)?;
    let mut signature = [0_u8; 64];
    read_exact_container(&mut reader, &mut signature)?;
    if embedded_signer != *trusted_signer
        || Sha256Digest::hash_reader(embedded_signer.as_slice())? != manifest.signer_key_sha256
    {
        return Err(HeleosError::BackupIntegrity);
    }
    verify_manifest_signature(&embedded_signer, &manifest_bytes, &signature)?;
    validate_plaintext_container_length(
        manifest_byte_length,
        manifest.entry_table_byte_length,
        manifest.decoded_payload_byte_length,
    )?;

    let entry_table_byte_length = read_u64(&mut reader)?;
    let table_capacity = validate_entry_table_claim(manifest.entry_count, entry_table_byte_length)?;
    if entry_table_byte_length != manifest.entry_table_byte_length {
        return Err(HeleosError::BackupIntegrity);
    }
    let mut entry_table_bytes = Vec::new();
    entry_table_bytes
        .try_reserve_exact(table_capacity)
        .map_err(|_| HeleosError::ResourceLimit)?;
    entry_table_bytes.resize(table_capacity, 0);
    read_exact_container(&mut reader, &mut entry_table_bytes)?;
    if Sha256Digest::hash_reader(entry_table_bytes.as_slice())? != manifest.entry_table_sha256 {
        return Err(HeleosError::BackupIntegrity);
    }

    let entry_table = parse_entry_table(&entry_table_bytes)?;
    let first_record = entry_table
        .records
        .first()
        .ok_or(HeleosError::InvalidBackupContainer)?;
    if u64::try_from(entry_table.records.len()).map_err(|_| HeleosError::ResourceLimit)?
        != manifest.entry_count
        || entry_table.decoded_payload_byte_length != manifest.decoded_payload_byte_length
        || first_record.sha256 != manifest.database.sha256
        || first_record.byte_length != manifest.database.byte_length
    {
        return Err(HeleosError::BackupIntegrity);
    }

    Ok(ParsedPlaintextContainerHeader {
        reader,
        entry_table,
    })
}

fn write_all_container(writer: &mut impl Write, bytes: &[u8]) -> Result<()> {
    writer.write_all(bytes).map_err(HeleosError::Io)
}

pub(super) fn write_plaintext_container(
    writer: &mut impl Write,
    signing_key: &SigningKey,
    payloads: &mut [BackupPayload<'_>],
) -> Result<()> {
    let entry_count = u64::try_from(payloads.len()).map_err(|_| HeleosError::ResourceLimit)?;
    let entry_table_byte_length = entry_count
        .checked_mul(BACKUP_ENTRY_RECORD_BYTES)
        .ok_or(HeleosError::ResourceLimit)?;
    validate_entry_table_claim(entry_count, entry_table_byte_length)?;
    let mut records = Vec::new();
    records
        .try_reserve_exact(payloads.len())
        .map_err(|_| HeleosError::ResourceLimit)?;
    records.extend(payloads.iter().map(|payload| payload.record));
    let decoded_payload_byte_length = validate_entry_records(&records)?;
    let entry_table_bytes = encode_entry_table(&records)?;
    if usize::try_from(entry_table_byte_length).map_err(|_| HeleosError::ResourceLimit)?
        != entry_table_bytes.len()
    {
        return Err(HeleosError::Integrity);
    }
    let signer = signing_key.verifying_key().to_bytes();
    let database = records
        .first()
        .copied()
        .ok_or(HeleosError::InvalidBackupContainer)?;
    let manifest = BackupManifestV1::new(
        Sha256Digest::hash_reader(signer.as_slice())?,
        Sha256Digest::hash_reader(entry_table_bytes.as_slice())?,
        entry_table_byte_length,
        entry_count,
        decoded_payload_byte_length,
        BackupDatabaseDescriptorV1::new(database.sha256, database.byte_length)?,
    )?;
    let manifest_bytes = manifest.canonical_bytes()?;
    let manifest_byte_length =
        u64::try_from(manifest_bytes.len()).map_err(|_| HeleosError::ResourceLimit)?;
    validate_plaintext_container_length(
        manifest_byte_length,
        entry_table_byte_length,
        decoded_payload_byte_length,
    )?;
    let signature = sign_manifest(signing_key, &manifest_bytes)?;

    write_all_container(writer, BACKUP_CONTAINER_MAGIC)?;
    write_all_container(writer, &manifest_byte_length.to_be_bytes())?;
    write_all_container(writer, &manifest_bytes)?;
    write_all_container(writer, &signer)?;
    write_all_container(writer, &signature)?;
    write_all_container(writer, &entry_table_byte_length.to_be_bytes())?;
    write_all_container(writer, &entry_table_bytes)?;

    let mut buffer = [0_u8; 64 * 1024];
    for payload in payloads {
        let mut remaining = payload.record.byte_length;
        let mut hasher = Sha256::new();
        while remaining != 0 {
            let chunk_length = usize::try_from(remaining.min(buffer.len() as u64))
                .map_err(|_| HeleosError::ResourceLimit)?;
            payload
                .reader
                .read_exact(&mut buffer[..chunk_length])
                .map_err(|error| {
                    if error.kind() == io::ErrorKind::UnexpectedEof {
                        HeleosError::Integrity
                    } else {
                        HeleosError::Io(error)
                    }
                })?;
            write_all_container(writer, &buffer[..chunk_length])?;
            hasher.update(&buffer[..chunk_length]);
            remaining = remaining
                .checked_sub(chunk_length as u64)
                .ok_or(HeleosError::ResourceLimit)?;
        }
        let mut trailing = [0_u8; 1];
        match payload.reader.read(&mut trailing) {
            Ok(0) => {}
            Ok(_) => return Err(HeleosError::Integrity),
            Err(error) => return Err(HeleosError::Io(error)),
        }
        if Sha256Digest::from_bytes(hasher.finalize().into()) != payload.record.sha256 {
            return Err(HeleosError::Integrity);
        }
    }
    Ok(())
}

fn map_age_io_error(error: io::Error) -> HeleosError {
    if error.kind() == io::ErrorKind::FileTooLarge {
        HeleosError::ResourceLimit
    } else {
        HeleosError::Io(error)
    }
}

pub(super) fn encrypt_age_stream<R: Read, W: Write>(
    plaintext: R,
    ciphertext: W,
    recipient: &age::x25519::Recipient,
    plaintext_byte_length: u64,
) -> Result<W> {
    encrypt_age_stream_with_limit(
        plaintext,
        ciphertext,
        recipient,
        plaintext_byte_length,
        MAX_BACKUP_CIPHERTEXT_BYTES,
    )
}

fn encrypt_age_stream_with_limit<R: Read, W: Write>(
    mut plaintext: R,
    ciphertext: W,
    recipient: &age::x25519::Recipient,
    plaintext_byte_length: u64,
    ciphertext_byte_limit: u64,
) -> Result<W> {
    if plaintext_byte_length > MAX_BACKUP_DECODED_BYTES {
        return Err(HeleosError::ResourceLimit);
    }
    let encryptor =
        age::Encryptor::with_recipients(std::iter::once(recipient as &dyn age::Recipient))
            .map_err(|_| HeleosError::PolicyDenied)?;
    let bounded = CiphertextBoundedWriter::new(ciphertext, ciphertext_byte_limit);
    let mut encrypted = encryptor.wrap_output(bounded).map_err(map_age_io_error)?;
    let mut remaining = plaintext_byte_length;
    let mut buffer = [0_u8; 64 * 1024];
    while remaining != 0 {
        let chunk_length = usize::try_from(remaining.min(buffer.len() as u64))
            .map_err(|_| HeleosError::ResourceLimit)?;
        plaintext
            .read_exact(&mut buffer[..chunk_length])
            .map_err(|error| {
                if error.kind() == io::ErrorKind::UnexpectedEof {
                    HeleosError::BackupIntegrity
                } else {
                    HeleosError::Io(error)
                }
            })?;
        encrypted
            .write_all(&buffer[..chunk_length])
            .map_err(map_age_io_error)?;
        remaining = remaining
            .checked_sub(chunk_length as u64)
            .ok_or(HeleosError::ResourceLimit)?;
    }
    let mut trailing = [0_u8; 1];
    match plaintext.read(&mut trailing) {
        Ok(0) => {}
        Ok(_) => return Err(HeleosError::BackupIntegrity),
        Err(error) => return Err(HeleosError::Io(error)),
    }
    let bounded = encrypted.finish().map_err(map_age_io_error)?;
    Ok(bounded.into_inner())
}

fn create_private_staging_handle(path: &Path) -> Result<File> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;

        options.mode(0o600);
    }
    let mut file = options.open(path).map_err(HeleosError::Io)?;
    crate::store::apply_private_permissions_to_handle(&mut file)?;
    Ok(file)
}

fn cleanup_failed_staging<T>(
    directory: tempfile::TempDir,
    operation_error: HeleosError,
) -> Result<T> {
    directory.close().map_err(HeleosError::Io)?;
    Err(operation_error)
}

fn stage_encrypted_backup_inner(
    staging_parent: &Path,
    signing_key: &SigningKey,
    recipient: &age::x25519::Recipient,
    payloads: &mut [BackupPayload<'_>],
    #[cfg(test)] fault: Option<BackupStagingTestFault>,
) -> Result<EncryptedBackupStaging> {
    #[cfg(test)]
    let (corrupt_plaintext_before_verification, ciphertext_byte_limit) = match fault {
        Some(BackupStagingTestFault::CorruptPlaintextBeforeVerification) => {
            (true, MAX_BACKUP_CIPHERTEXT_BYTES)
        }
        Some(BackupStagingTestFault::CiphertextByteLimit(limit)) => (false, limit),
        None => (false, MAX_BACKUP_CIPHERTEXT_BYTES),
    };
    #[cfg(not(test))]
    let ciphertext_byte_limit = MAX_BACKUP_CIPHERTEXT_BYTES;

    let canonical_staging_parent = fs::canonicalize(staging_parent).map_err(HeleosError::Io)?;
    if canonical_staging_parent != staging_parent {
        return Err(HeleosError::PolicyDenied);
    }
    verify_private_permissions(&canonical_staging_parent)?;
    if !fs::symlink_metadata(&canonical_staging_parent)
        .map_err(HeleosError::Io)?
        .is_dir()
    {
        return Err(HeleosError::PolicyDenied);
    }

    let directory = tempfile::Builder::new()
        .prefix(".heleos-backup-container-")
        .tempdir_in(&canonical_staging_parent)
        .map_err(HeleosError::Io)?;
    if let Err(error) = apply_private_permissions(directory.path())
        .and_then(|()| verify_private_permissions(directory.path()))
    {
        return cleanup_failed_staging(directory, error);
    }

    let plaintext_path = directory.path().join("container.plaintext.partial");
    let ciphertext_path = directory.path().join("container.age.partial");
    let operation = (|| {
        let mut plaintext = create_private_staging_handle(&plaintext_path)?;
        write_plaintext_container(&mut plaintext, signing_key, payloads)?;
        plaintext.sync_all().map_err(HeleosError::Io)?;

        #[cfg(test)]
        if corrupt_plaintext_before_verification {
            plaintext.seek(SeekFrom::End(-1)).map_err(HeleosError::Io)?;
            plaintext.write_all(&[0]).map_err(HeleosError::Io)?;
            plaintext.sync_all().map_err(HeleosError::Io)?;
        }

        plaintext
            .seek(SeekFrom::Start(0))
            .map_err(HeleosError::Io)?;
        let trusted_signer = signing_key.verifying_key().to_bytes();
        verify_plaintext_container(&mut plaintext, &trusted_signer)?;
        let plaintext_byte_length = plaintext.metadata().map_err(HeleosError::Io)?.len();
        if plaintext_byte_length > MAX_BACKUP_DECODED_BYTES {
            return Err(HeleosError::ResourceLimit);
        }
        plaintext
            .seek(SeekFrom::Start(0))
            .map_err(HeleosError::Io)?;

        let ciphertext = create_private_staging_handle(&ciphertext_path)?;
        let ciphertext = encrypt_age_stream_with_limit(
            &mut plaintext,
            ciphertext,
            recipient,
            plaintext_byte_length,
            ciphertext_byte_limit,
        )?;
        ciphertext.sync_all().map_err(HeleosError::Io)?;
        if ciphertext.metadata().map_err(HeleosError::Io)?.len() > MAX_BACKUP_CIPHERTEXT_BYTES {
            return Err(HeleosError::ResourceLimit);
        }

        drop(plaintext);
        fs::remove_file(&plaintext_path).map_err(HeleosError::Io)?;
        Ok(ciphertext)
    })();

    match operation {
        Ok(ciphertext) => Ok(EncryptedBackupStaging {
            _ciphertext: ciphertext,
            ciphertext_path,
            directory,
        }),
        Err(error) => cleanup_failed_staging(directory, error),
    }
}

pub(super) fn stage_encrypted_backup(
    staging_parent: &Path,
    signing_key: &SigningKey,
    recipient: &age::x25519::Recipient,
    payloads: &mut [BackupPayload<'_>],
) -> Result<EncryptedBackupStaging> {
    stage_encrypted_backup_inner(
        staging_parent,
        signing_key,
        recipient,
        payloads,
        #[cfg(test)]
        None,
    )
}

#[cfg(test)]
pub(super) fn stage_encrypted_backup_with_test_fault(
    staging_parent: &Path,
    signing_key: &SigningKey,
    recipient: &age::x25519::Recipient,
    payloads: &mut [BackupPayload<'_>],
    fault: BackupStagingTestFault,
) -> Result<EncryptedBackupStaging> {
    stage_encrypted_backup_inner(
        staging_parent,
        signing_key,
        recipient,
        payloads,
        Some(fault),
    )
}

pub(super) fn verify_plaintext_container<R: Read>(
    reader: R,
    trusted_signer: &[u8; 32],
) -> Result<()> {
    let ParsedPlaintextContainerHeader {
        mut reader,
        entry_table,
    } = parse_plaintext_container_header(reader, trusted_signer)?;
    let mut buffer = [0_u8; 64 * 1024];
    for record in &entry_table.records {
        let mut remaining = record.byte_length;
        let mut hasher = Sha256::new();
        while remaining != 0 {
            let chunk_length = usize::try_from(remaining.min(buffer.len() as u64))
                .map_err(|_| HeleosError::ResourceLimit)?;
            read_exact_container(&mut reader, &mut buffer[..chunk_length])?;
            hasher.update(&buffer[..chunk_length]);
            remaining = remaining
                .checked_sub(chunk_length as u64)
                .ok_or(HeleosError::ResourceLimit)?;
        }
        let actual_digest = Sha256Digest::from_bytes(hasher.finalize().into());
        if actual_digest != record.sha256 {
            return Err(HeleosError::BackupIntegrity);
        }
    }

    let mut trailing = [0_u8; 1];
    match reader.read(&mut trailing) {
        Ok(0) => Ok(()),
        Ok(_) => Err(HeleosError::InvalidBackupContainer),
        Err(error) => Err(HeleosError::Io(error)),
    }
}

fn validate_entry_records(records: &[BackupEntryRecord]) -> Result<u64> {
    let count = u64::try_from(records.len()).map_err(|_| HeleosError::ResourceLimit)?;
    if count == 0 || count > MAX_BACKUP_ENTRY_COUNT {
        return Err(HeleosError::ResourceLimit);
    }
    if records[0].kind != BackupEntryKind::Database {
        return Err(HeleosError::InvalidBackupContainer);
    }

    let mut previous_blob = None;
    let mut blob_count = 0_u64;
    let mut decoded_payload_byte_length = 0_u64;
    for (index, record) in records.iter().enumerate() {
        match record.kind {
            BackupEntryKind::Database if index == 0 => {
                if record.byte_length > MAX_BACKUP_DATABASE_BYTES {
                    return Err(HeleosError::ResourceLimit);
                }
            }
            BackupEntryKind::Database => return Err(HeleosError::InvalidBackupContainer),
            BackupEntryKind::Blob => {
                blob_count = blob_count
                    .checked_add(1)
                    .ok_or(HeleosError::ResourceLimit)?;
                if blob_count > MAX_BACKUP_BLOB_COUNT || record.byte_length > 256 * 1024 * 1024 {
                    return Err(HeleosError::ResourceLimit);
                }
                if previous_blob.is_some_and(|previous| record.sha256 <= previous) {
                    return Err(HeleosError::InvalidBackupContainer);
                }
                previous_blob = Some(record.sha256);
            }
        }
        decoded_payload_byte_length = decoded_payload_byte_length
            .checked_add(record.byte_length)
            .ok_or(HeleosError::ResourceLimit)?;
        if decoded_payload_byte_length > MAX_BACKUP_DECODED_BYTES {
            return Err(HeleosError::ResourceLimit);
        }
    }
    Ok(decoded_payload_byte_length)
}

pub(super) fn validate_entry_table_claim(
    entry_count: u64,
    entry_table_byte_length: u64,
) -> Result<usize> {
    if entry_count == 0 || entry_count > MAX_BACKUP_ENTRY_COUNT {
        return Err(HeleosError::ResourceLimit);
    }
    if entry_table_byte_length > MAX_BACKUP_ENTRY_TABLE_BYTES {
        return Err(HeleosError::ResourceLimit);
    }
    let expected_byte_length = entry_count
        .checked_mul(BACKUP_ENTRY_RECORD_BYTES)
        .ok_or(HeleosError::ResourceLimit)?;
    if entry_table_byte_length != expected_byte_length {
        return Err(HeleosError::InvalidBackupContainer);
    }
    usize::try_from(entry_table_byte_length).map_err(|_| HeleosError::ResourceLimit)
}

fn checked_entry_table_length(entry_count: u64) -> Result<usize> {
    let byte_length = entry_count
        .checked_mul(BACKUP_ENTRY_RECORD_BYTES)
        .ok_or(HeleosError::ResourceLimit)?;
    validate_entry_table_claim(entry_count, byte_length)
}

pub(super) fn encode_entry_table(records: &[BackupEntryRecord]) -> Result<Vec<u8>> {
    validate_entry_records(records)?;
    let entry_count = u64::try_from(records.len()).map_err(|_| HeleosError::ResourceLimit)?;
    let byte_length = checked_entry_table_length(entry_count)?;
    let mut bytes = Vec::new();
    bytes
        .try_reserve_exact(byte_length)
        .map_err(|_| HeleosError::ResourceLimit)?;
    for record in records {
        bytes.push(match record.kind {
            BackupEntryKind::Database => DATABASE_ENTRY_KIND,
            BackupEntryKind::Blob => BLOB_ENTRY_KIND,
        });
        bytes.extend_from_slice(record.sha256.as_bytes());
        bytes.extend_from_slice(&record.byte_length.to_be_bytes());
    }
    Ok(bytes)
}

pub(super) fn parse_entry_table(bytes: &[u8]) -> Result<BackupEntryTable> {
    let byte_length = u64::try_from(bytes.len()).map_err(|_| HeleosError::ResourceLimit)?;
    if byte_length > MAX_BACKUP_ENTRY_TABLE_BYTES {
        return Err(HeleosError::ResourceLimit);
    }
    if byte_length == 0 || byte_length % BACKUP_ENTRY_RECORD_BYTES != 0 {
        return Err(HeleosError::InvalidBackupContainer);
    }
    let entry_count = byte_length
        .checked_div(BACKUP_ENTRY_RECORD_BYTES)
        .ok_or(HeleosError::ResourceLimit)?;
    let expected_length = validate_entry_table_claim(entry_count, byte_length)?;
    if expected_length != bytes.len() {
        return Err(HeleosError::InvalidBackupContainer);
    }

    let capacity = usize::try_from(entry_count).map_err(|_| HeleosError::ResourceLimit)?;
    let mut records = Vec::new();
    records
        .try_reserve_exact(capacity)
        .map_err(|_| HeleosError::ResourceLimit)?;
    for encoded in bytes.chunks_exact(BACKUP_ENTRY_RECORD_BYTES as usize) {
        let mut digest = [0_u8; 32];
        digest.copy_from_slice(&encoded[1..33]);
        let mut length = [0_u8; 8];
        length.copy_from_slice(&encoded[33..41]);
        let digest = Sha256Digest::from_bytes(digest);
        let byte_length = u64::from_be_bytes(length);
        records.push(match encoded[0] {
            DATABASE_ENTRY_KIND => BackupEntryRecord::database(digest, byte_length)?,
            BLOB_ENTRY_KIND => BackupEntryRecord::blob(digest, byte_length)?,
            _ => return Err(HeleosError::InvalidBackupContainer),
        });
    }
    let decoded_payload_byte_length = validate_entry_records(&records)?;
    Ok(BackupEntryTable {
        records,
        decoded_payload_byte_length,
    })
}
