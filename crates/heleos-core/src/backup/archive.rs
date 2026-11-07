//! Bounded backup-container framing.

use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};

use ed25519_dalek::SigningKey;
use sha2::{Digest, Sha256};

use crate::{HeleosError, Result, Sha256Digest};

use super::manifest::{
    BACKUP_ENTRY_RECORD_BYTES, BackupDatabaseDescriptorV1, BackupManifestV1, MAX_BACKUP_BLOB_COUNT,
    MAX_BACKUP_DATABASE_BYTES, MAX_BACKUP_DECODED_BYTES, MAX_BACKUP_ENTRY_COUNT,
    MAX_BACKUP_ENTRY_TABLE_BYTES, MAX_BACKUP_MANIFEST_BYTES, sign_manifest,
    verify_manifest_signature,
};
use super::staging_error::{StagingMutationWriter, classify_direct_staging_io};
use super::{OwnedSiblingFile, OwnedSiblingFilePurpose};

const BACKUP_CONTAINER_MAGIC: &[u8; 8] = b"HELEOSB1";
const MAX_BACKUP_CIPHERTEXT_BYTES: u64 = 522 * 1024 * 1024 * 1024;
const DATABASE_ENTRY_KIND: u8 = 0;
const BLOB_ENTRY_KIND: u8 = 1;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum BackupEntryKind {
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

    pub(super) const fn kind(&self) -> BackupEntryKind {
        self.kind
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

/// An inert descriptor plan. It owns no payload handle; the matching reader is
/// opened only while that one record is being emitted.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct BackupRecordPlan {
    records: Vec<BackupEntryRecord>,
}

impl BackupRecordPlan {
    pub(super) fn new(records: Vec<BackupEntryRecord>) -> Result<Self> {
        validate_entry_records(&records)?;
        Ok(Self { records })
    }

    pub(super) fn records(&self) -> &[BackupEntryRecord] {
        &self.records
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct AuthenticatedPayloadExtent {
    record: BackupEntryRecord,
    byte_offset: u64,
}

impl AuthenticatedPayloadExtent {
    pub(super) const fn record(self) -> BackupEntryRecord {
        self.record
    }

    pub(super) const fn byte_offset(self) -> u64 {
        self.byte_offset
    }
}

pub(super) struct AuthenticatedPayloadExtents<'a> {
    records: std::slice::Iter<'a, BackupEntryRecord>,
    next_byte_offset: u128,
}

impl Iterator for AuthenticatedPayloadExtents<'_> {
    type Item = AuthenticatedPayloadExtent;

    fn next(&mut self) -> Option<Self::Item> {
        let record = *self.records.next()?;
        let byte_offset = u64::try_from(self.next_byte_offset).ok()?;
        self.next_byte_offset += u128::from(record.byte_length);
        Some(AuthenticatedPayloadExtent {
            record,
            byte_offset,
        })
    }

    fn size_hint(&self) -> (usize, Option<usize>) {
        self.records.size_hint()
    }
}

impl ExactSizeIterator for AuthenticatedPayloadExtents<'_> {}

/// Metadata which can only be produced after the signer, canonical manifest,
/// entry table, every declared payload extent, and terminal EOF all verify.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct AuthenticatedPlaintextContainer {
    manifest: BackupManifestV1,
    entry_table: BackupEntryTable,
    payload_byte_offset: u64,
    plaintext_byte_length: u64,
}

impl AuthenticatedPlaintextContainer {
    pub(super) fn manifest_schema(&self) -> &str {
        &self.manifest.schema
    }

    pub(super) const fn signer_key_sha256(&self) -> Sha256Digest {
        self.manifest.signer_key_sha256
    }

    pub(super) const fn entry_table_sha256(&self) -> Sha256Digest {
        self.manifest.entry_table_sha256
    }

    pub(super) const fn entry_table_byte_length(&self) -> u64 {
        self.manifest.entry_table_byte_length
    }

    pub(super) const fn entry_count(&self) -> u64 {
        self.manifest.entry_count
    }

    pub(super) const fn decoded_payload_byte_length(&self) -> u64 {
        self.manifest.decoded_payload_byte_length
    }

    pub(super) const fn database_sha256(&self) -> Sha256Digest {
        self.manifest.database.sha256
    }

    pub(super) const fn database_byte_length(&self) -> u64 {
        self.manifest.database.byte_length
    }

    pub(super) fn extents(&self) -> AuthenticatedPayloadExtents<'_> {
        AuthenticatedPayloadExtents {
            records: self.entry_table.records.iter(),
            next_byte_offset: u128::from(self.payload_byte_offset),
        }
    }

    pub(super) const fn plaintext_byte_length(&self) -> u64 {
        self.plaintext_byte_length
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
    ciphertext: OwnedSiblingFile,
    authenticated: AuthenticatedPlaintextContainer,
    ciphertext_byte_length: u64,
}

impl EncryptedBackupStaging {
    pub(super) fn ciphertext_handle(&self) -> Result<&File> {
        self.ciphertext.handle()
    }

    pub(super) const fn authenticated(&self) -> &AuthenticatedPlaintextContainer {
        &self.authenticated
    }

    pub(super) const fn ciphertext_byte_length(&self) -> u64 {
        self.ciphertext_byte_length
    }

    pub(super) fn into_parts(self) -> (OwnedSiblingFile, AuthenticatedPlaintextContainer, u64) {
        (
            self.ciphertext,
            self.authenticated,
            self.ciphertext_byte_length,
        )
    }
}

#[cfg(test)]
#[derive(Clone, Copy)]
pub(super) enum BackupStagingTestFault {
    CorruptPlaintextBeforeVerification,
    CiphertextByteLimit(u64),
    BeforePlaintextWrite,
    AfterPlaintextWritten,
    AfterPlaintextSynced,
    AfterAuthenticated,
    BeforeAgeEncryption,
    AfterAgeFinished,
    AfterCiphertextSynced,
}

pub(super) struct ParsedPlaintextContainerHeader<R> {
    reader: R,
    manifest: BackupManifestV1,
    entry_table: BackupEntryTable,
    payload_byte_offset: u64,
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
    let payload_byte_offset =
        plaintext_payload_byte_offset(manifest_byte_length, entry_table_byte_length)?;
    let total = payload_byte_offset
        .checked_add(decoded_payload_byte_length)
        .ok_or(HeleosError::ResourceLimit)?;
    if total > MAX_BACKUP_DECODED_BYTES {
        return Err(HeleosError::ResourceLimit);
    }
    Ok(total)
}

fn plaintext_payload_byte_offset(
    manifest_byte_length: u64,
    entry_table_byte_length: u64,
) -> Result<u64> {
    let fixed_byte_length = u64::try_from(BACKUP_CONTAINER_MAGIC.len() + 8 + 32 + 64 + 8)
        .map_err(|_| HeleosError::ResourceLimit)?;
    fixed_byte_length
        .checked_add(manifest_byte_length)
        .and_then(|length| length.checked_add(entry_table_byte_length))
        .ok_or(HeleosError::ResourceLimit)
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
        manifest,
        entry_table,
        payload_byte_offset: plaintext_payload_byte_offset(
            manifest_byte_length,
            entry_table_byte_length,
        )?,
    })
}

fn write_all_container(writer: &mut impl Write, bytes: &[u8]) -> Result<()> {
    writer.write_all(bytes).map_err(HeleosError::Io)
}

pub(super) fn write_plaintext_container<R, O>(
    writer: &mut impl Write,
    signing_key: &SigningKey,
    record_plan: &BackupRecordPlan,
    mut open_payload: O,
) -> Result<()>
where
    R: Read,
    O: FnMut(usize, BackupEntryRecord) -> Result<R>,
{
    let records = record_plan.records();
    let entry_count = u64::try_from(records.len()).map_err(|_| HeleosError::ResourceLimit)?;
    let entry_table_byte_length = entry_count
        .checked_mul(BACKUP_ENTRY_RECORD_BYTES)
        .ok_or(HeleosError::ResourceLimit)?;
    validate_entry_table_claim(entry_count, entry_table_byte_length)?;
    let decoded_payload_byte_length = validate_entry_records(records)?;
    let entry_table_bytes = encode_entry_table(records)?;
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
    for (index, record) in records.iter().copied().enumerate() {
        // The reader is intentionally scoped to one loop iteration. Dropping it
        // before the next opener call is part of the bounded-handle contract.
        let mut reader = open_payload(index, record)?;
        let mut remaining = record.byte_length;
        let mut hasher = Sha256::new();
        while remaining != 0 {
            let chunk_length = usize::try_from(remaining.min(buffer.len() as u64))
                .map_err(|_| HeleosError::ResourceLimit)?;
            reader
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
        match reader.read(&mut trailing) {
            Ok(0) => {}
            Ok(_) => return Err(HeleosError::Integrity),
            Err(error) => return Err(HeleosError::Io(error)),
        }
        if Sha256Digest::from_bytes(hasher.finalize().into()) != record.sha256 {
            return Err(HeleosError::Integrity);
        }
        drop(reader);
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

fn cleanup_failed_staging<T>(
    mut plaintext: OwnedSiblingFile,
    mut ciphertext: OwnedSiblingFile,
    operation_error: HeleosError,
) -> Result<T> {
    let plaintext_cleanup = plaintext.cleanup();
    let ciphertext_cleanup = ciphertext.cleanup();
    match (plaintext_cleanup, ciphertext_cleanup) {
        (Err(error), _) | (Ok(()), Err(error)) => Err(error),
        (Ok(()), Ok(())) => Err(operation_error),
    }
}

#[cfg(test)]
fn inject_staging_fault(
    fault: Option<BackupStagingTestFault>,
    point: BackupStagingTestFault,
) -> Result<()> {
    if matches!(fault, Some(observed) if std::mem::discriminant(&observed) == std::mem::discriminant(&point))
    {
        Err(HeleosError::FaultInjected)
    } else {
        Ok(())
    }
}

fn stage_encrypted_backup_inner<R, O>(
    mut plaintext_staging: OwnedSiblingFile,
    mut ciphertext_staging: OwnedSiblingFile,
    signing_key: &SigningKey,
    recipient: &age::x25519::Recipient,
    record_plan: BackupRecordPlan,
    open_payload: O,
    #[cfg(test)] fault: Option<BackupStagingTestFault>,
) -> Result<EncryptedBackupStaging>
where
    R: Read,
    O: FnMut(usize, BackupEntryRecord) -> Result<R>,
{
    #[cfg(test)]
    let (corrupt_plaintext_before_verification, ciphertext_byte_limit) = match fault {
        Some(BackupStagingTestFault::CorruptPlaintextBeforeVerification) => {
            (true, MAX_BACKUP_CIPHERTEXT_BYTES)
        }
        Some(BackupStagingTestFault::CiphertextByteLimit(limit)) => (false, limit),
        Some(_) | None => (false, MAX_BACKUP_CIPHERTEXT_BYTES),
    };
    #[cfg(not(test))]
    let ciphertext_byte_limit = MAX_BACKUP_CIPHERTEXT_BYTES;

    if !plaintext_staging.has_same_parent(&ciphertext_staging)
        || plaintext_staging.purpose() != OwnedSiblingFilePurpose::Plaintext
        || ciphertext_staging.purpose() != OwnedSiblingFilePurpose::Ciphertext
    {
        return cleanup_failed_staging(
            plaintext_staging,
            ciphertext_staging,
            HeleosError::Integrity,
        );
    }
    let operation = (|| {
        #[cfg(test)]
        inject_staging_fault(fault, BackupStagingTestFault::BeforePlaintextWrite)?;

        {
            let mut plaintext_writer = StagingMutationWriter::new(plaintext_staging.handle_mut()?);
            let write_result = write_plaintext_container(
                &mut plaintext_writer,
                signing_key,
                &record_plan,
                open_payload,
            );
            plaintext_writer.classify_result(write_result)?;
        }
        #[cfg(test)]
        inject_staging_fault(fault, BackupStagingTestFault::AfterPlaintextWritten)?;
        // Self-verification reparses the authenticated table. Release the
        // emission plan first so the maximum-size record vector is never held
        // twice by this pipeline.
        drop(record_plan);
        plaintext_staging
            .handle()?
            .sync_all()
            .map_err(classify_direct_staging_io)?;
        #[cfg(test)]
        inject_staging_fault(fault, BackupStagingTestFault::AfterPlaintextSynced)?;

        #[cfg(test)]
        if corrupt_plaintext_before_verification {
            let plaintext = plaintext_staging.handle_mut()?;
            plaintext.seek(SeekFrom::End(-1)).map_err(HeleosError::Io)?;
            plaintext
                .write_all(&[0])
                .map_err(classify_direct_staging_io)?;
            plaintext.sync_all().map_err(classify_direct_staging_io)?;
        }

        plaintext_staging
            .handle_mut()?
            .seek(SeekFrom::Start(0))
            .map_err(HeleosError::Io)?;
        let trusted_signer = signing_key.verifying_key().to_bytes();
        let authenticated =
            verify_plaintext_container(plaintext_staging.handle_mut()?, &trusted_signer)?;
        #[cfg(test)]
        inject_staging_fault(fault, BackupStagingTestFault::AfterAuthenticated)?;
        let plaintext_byte_length = plaintext_staging
            .handle()?
            .metadata()
            .map_err(HeleosError::Io)?
            .len();
        validate_staged_plaintext_file_length(
            plaintext_byte_length,
            authenticated.plaintext_byte_length(),
        )?;
        plaintext_staging
            .handle_mut()?
            .seek(SeekFrom::Start(0))
            .map_err(HeleosError::Io)?;

        #[cfg(test)]
        inject_staging_fault(fault, BackupStagingTestFault::BeforeAgeEncryption)?;
        {
            let mut ciphertext_writer =
                StagingMutationWriter::new(ciphertext_staging.handle_mut()?);
            let encryption_result = encrypt_age_stream_with_limit(
                plaintext_staging.handle_mut()?,
                &mut ciphertext_writer,
                recipient,
                plaintext_byte_length,
                ciphertext_byte_limit,
            )
            .map(|_| ());
            ciphertext_writer.classify_result(encryption_result)?;
        }
        #[cfg(test)]
        inject_staging_fault(fault, BackupStagingTestFault::AfterAgeFinished)?;
        plaintext_staging.cleanup()?;
        ciphertext_staging
            .handle()?
            .sync_all()
            .map_err(classify_direct_staging_io)?;
        #[cfg(test)]
        inject_staging_fault(fault, BackupStagingTestFault::AfterCiphertextSynced)?;
        let ciphertext_byte_length = ciphertext_staging
            .handle()?
            .metadata()
            .map_err(HeleosError::Io)?
            .len();
        if ciphertext_byte_length > MAX_BACKUP_CIPHERTEXT_BYTES {
            return Err(HeleosError::ResourceLimit);
        }

        Ok((authenticated, ciphertext_byte_length))
    })();

    match operation {
        Ok((authenticated, ciphertext_byte_length)) => Ok(EncryptedBackupStaging {
            ciphertext: ciphertext_staging,
            authenticated,
            ciphertext_byte_length,
        }),
        Err(error) => cleanup_failed_staging(plaintext_staging, ciphertext_staging, error),
    }
}

pub(super) fn validate_staged_plaintext_file_length(
    actual_byte_length: u64,
    authenticated_byte_length: u64,
) -> Result<()> {
    if actual_byte_length > MAX_BACKUP_DECODED_BYTES {
        return Err(HeleosError::ResourceLimit);
    }
    if actual_byte_length != authenticated_byte_length {
        return Err(HeleosError::BackupIntegrity);
    }
    Ok(())
}

pub(super) fn stage_encrypted_backup<R, O>(
    plaintext_staging: OwnedSiblingFile,
    ciphertext_staging: OwnedSiblingFile,
    signing_key: &SigningKey,
    recipient: &age::x25519::Recipient,
    record_plan: BackupRecordPlan,
    open_payload: O,
) -> Result<EncryptedBackupStaging>
where
    R: Read,
    O: FnMut(usize, BackupEntryRecord) -> Result<R>,
{
    stage_encrypted_backup_inner(
        plaintext_staging,
        ciphertext_staging,
        signing_key,
        recipient,
        record_plan,
        open_payload,
        #[cfg(test)]
        None,
    )
}

#[cfg(test)]
pub(super) fn stage_encrypted_backup_with_test_fault<R, O>(
    plaintext_staging: OwnedSiblingFile,
    ciphertext_staging: OwnedSiblingFile,
    signing_key: &SigningKey,
    recipient: &age::x25519::Recipient,
    record_plan: BackupRecordPlan,
    open_payload: O,
    fault: BackupStagingTestFault,
) -> Result<EncryptedBackupStaging>
where
    R: Read,
    O: FnMut(usize, BackupEntryRecord) -> Result<R>,
{
    stage_encrypted_backup_inner(
        plaintext_staging,
        ciphertext_staging,
        signing_key,
        recipient,
        record_plan,
        open_payload,
        Some(fault),
    )
}

pub(super) fn verify_plaintext_container<R: Read>(
    reader: R,
    trusted_signer: &[u8; 32],
) -> Result<AuthenticatedPlaintextContainer> {
    let ParsedPlaintextContainerHeader {
        mut reader,
        manifest,
        entry_table,
        payload_byte_offset,
    } = parse_plaintext_container_header(reader, trusted_signer)?;
    let mut buffer = [0_u8; 64 * 1024];
    for record in &entry_table.records {
        let mut remaining = record.byte_length;
        let mut hasher = Sha256::new();
        while remaining != 0 {
            let chunk_length = usize::try_from(remaining.min(buffer.len() as u64))
                .map_err(|_| HeleosError::ResourceLimit)?;
            reader
                .read_exact(&mut buffer[..chunk_length])
                .map_err(|error| {
                    if error.kind() == io::ErrorKind::UnexpectedEof {
                        HeleosError::BackupIntegrity
                    } else {
                        HeleosError::Io(error)
                    }
                })?;
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
        Ok(0) => {
            let plaintext_byte_length = payload_byte_offset
                .checked_add(entry_table.decoded_payload_byte_length)
                .ok_or(HeleosError::ResourceLimit)?;
            Ok(AuthenticatedPlaintextContainer {
                manifest,
                entry_table,
                payload_byte_offset,
                plaintext_byte_length,
            })
        }
        Ok(_) => Err(HeleosError::InvalidBackupContainer),
        Err(error) => Err(HeleosError::Io(error)),
    }
}

pub(super) fn copy_authenticated_extent<R: Read + Seek, W: Write>(
    plaintext: &mut R,
    extent: AuthenticatedPayloadExtent,
    output: &mut W,
) -> Result<AuthenticatedPayloadExtent> {
    plaintext
        .seek(SeekFrom::Start(extent.byte_offset))
        .map_err(HeleosError::Io)?;

    let mut remaining = extent.record.byte_length;
    let mut hasher = Sha256::new();
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
        output
            .write_all(&buffer[..chunk_length])
            .map_err(HeleosError::Io)?;
        hasher.update(&buffer[..chunk_length]);
        remaining = remaining
            .checked_sub(chunk_length as u64)
            .ok_or(HeleosError::ResourceLimit)?;
    }
    if Sha256Digest::from_bytes(hasher.finalize().into()) != extent.record.sha256 {
        return Err(HeleosError::BackupIntegrity);
    }
    Ok(extent)
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
