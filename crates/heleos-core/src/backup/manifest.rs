//! Signed backup-manifest representation.

use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use serde::{Deserialize, Serialize};

use crate::{HeleosError, Result, Sha256Digest};

pub(super) const BACKUP_MANIFEST_SCHEMA_V1: &str = "heleos.backup-manifest/v1";
pub(super) const MAX_BACKUP_MANIFEST_BYTES: usize = 16 * 1024 * 1024;
pub(super) const MAX_BACKUP_DATABASE_BYTES: u64 = 16 * 1024 * 1024 * 1024;
pub(super) const MAX_BACKUP_BLOB_COUNT: u64 = 2_000_000;
pub(super) const MAX_BACKUP_ENTRY_COUNT: u64 = MAX_BACKUP_BLOB_COUNT + 1;
pub(super) const BACKUP_ENTRY_RECORD_BYTES: u64 = 41;
pub(super) const MAX_BACKUP_ENTRY_TABLE_BYTES: u64 = 80 * 1024 * 1024;
pub(super) const MAX_BACKUP_DECODED_BYTES: u64 = 520 * 1024 * 1024 * 1024;
const BACKUP_SIGNATURE_DOMAIN: &[u8] = b"heleos-backup-v1\0";

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct BackupDatabaseDescriptorV1 {
    pub(super) sha256: Sha256Digest,
    pub(super) byte_length: u64,
}

impl BackupDatabaseDescriptorV1 {
    pub(super) fn new(sha256: Sha256Digest, byte_length: u64) -> Result<Self> {
        if byte_length > MAX_BACKUP_DATABASE_BYTES {
            return Err(HeleosError::ResourceLimit);
        }
        Ok(Self {
            sha256,
            byte_length,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct BackupManifestV1 {
    schema: String,
    pub(super) signer_key_sha256: Sha256Digest,
    pub(super) entry_table_sha256: Sha256Digest,
    pub(super) entry_table_byte_length: u64,
    pub(super) entry_count: u64,
    pub(super) decoded_payload_byte_length: u64,
    pub(super) database: BackupDatabaseDescriptorV1,
}

impl BackupManifestV1 {
    pub(super) fn new(
        signer_key_sha256: Sha256Digest,
        entry_table_sha256: Sha256Digest,
        entry_table_byte_length: u64,
        entry_count: u64,
        decoded_payload_byte_length: u64,
        database: BackupDatabaseDescriptorV1,
    ) -> Result<Self> {
        let value = Self {
            schema: BACKUP_MANIFEST_SCHEMA_V1.to_owned(),
            signer_key_sha256,
            entry_table_sha256,
            entry_table_byte_length,
            entry_count,
            decoded_payload_byte_length,
            database,
        };
        value.validate()?;
        Ok(value)
    }

    fn validate(&self) -> Result<()> {
        if self.schema != BACKUP_MANIFEST_SCHEMA_V1 {
            return Err(HeleosError::InvalidBackupContainer);
        }
        if self.database.byte_length > MAX_BACKUP_DATABASE_BYTES
            || self.entry_count == 0
            || self.entry_count > MAX_BACKUP_ENTRY_COUNT
            || self.entry_table_byte_length > MAX_BACKUP_ENTRY_TABLE_BYTES
            || self.decoded_payload_byte_length > MAX_BACKUP_DECODED_BYTES
        {
            return Err(HeleosError::ResourceLimit);
        }
        let expected_table_length = self
            .entry_count
            .checked_mul(BACKUP_ENTRY_RECORD_BYTES)
            .ok_or(HeleosError::ResourceLimit)?;
        if self.entry_table_byte_length != expected_table_length
            || self.decoded_payload_byte_length < self.database.byte_length
            || (self.entry_count == 1
                && self.decoded_payload_byte_length != self.database.byte_length)
        {
            return Err(HeleosError::InvalidBackupContainer);
        }
        Ok(())
    }

    pub(super) fn canonical_bytes(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let bytes = serde_jcs::to_vec(self).map_err(HeleosError::Serialization)?;
        if bytes.len() > MAX_BACKUP_MANIFEST_BYTES {
            return Err(HeleosError::ResourceLimit);
        }
        Ok(bytes)
    }

    pub(super) fn parse_canonical(bytes: &[u8]) -> Result<Self> {
        if bytes.len() > MAX_BACKUP_MANIFEST_BYTES {
            return Err(HeleosError::ResourceLimit);
        }
        let value = serde_json::from_slice::<Self>(bytes)
            .map_err(|_| HeleosError::InvalidBackupContainer)?;
        value.validate()?;
        if value.canonical_bytes()? != bytes {
            return Err(HeleosError::InvalidBackupContainer);
        }
        Ok(value)
    }
}

fn manifest_signature_message(manifest_bytes: &[u8]) -> Result<Vec<u8>> {
    BackupManifestV1::parse_canonical(manifest_bytes)?;
    let length = BACKUP_SIGNATURE_DOMAIN
        .len()
        .checked_add(manifest_bytes.len())
        .ok_or(HeleosError::ResourceLimit)?;
    let mut message = Vec::new();
    message
        .try_reserve_exact(length)
        .map_err(|_| HeleosError::ResourceLimit)?;
    message.extend_from_slice(BACKUP_SIGNATURE_DOMAIN);
    message.extend_from_slice(manifest_bytes);
    Ok(message)
}

pub(super) fn sign_manifest(signing_key: &SigningKey, manifest_bytes: &[u8]) -> Result<[u8; 64]> {
    let message = manifest_signature_message(manifest_bytes)?;
    Ok(signing_key.sign(&message).to_bytes())
}

pub(super) fn verify_manifest_signature(
    verifying_key: &[u8; 32],
    manifest_bytes: &[u8],
    signature: &[u8; 64],
) -> Result<()> {
    let verifying_key =
        VerifyingKey::from_bytes(verifying_key).map_err(|_| HeleosError::BackupIntegrity)?;
    let message = manifest_signature_message(manifest_bytes)?;
    verifying_key
        .verify_strict(&message, &Signature::from_bytes(signature))
        .map_err(|_| HeleosError::BackupIntegrity)
}
