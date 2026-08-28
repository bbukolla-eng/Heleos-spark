use std::io;

use thiserror::Error;

pub type Result<T> = std::result::Result<T, HeleosError>;

#[derive(Debug, Error)]
pub enum HeleosError {
    #[error("I/O operation failed")]
    Io(#[source] io::Error),
    #[error("invalid SHA-256 digest")]
    InvalidDigest,
    #[error("invalid identifier")]
    InvalidId,
    #[error("database operation failed")]
    Database,
    #[error("migration failed")]
    Migration,
    #[error("integrity verification failed")]
    Integrity,
    #[error("corrupt PDF")]
    CorruptPdf,
    #[error("encrypted PDF")]
    EncryptedPdf,
    #[error("unsupported PDF")]
    UnsupportedPdf,
    #[error("suspicious PDF")]
    SuspiciousPdf,
    #[error("resource limit exceeded")]
    ResourceLimit,
    #[error("quota exceeded")]
    Quota,
    #[error("operation timed out")]
    Timeout,
    #[error("invalid job state transition")]
    InvalidStateTransition,
    #[error("idempotency conflict")]
    IdempotencyConflict,
    #[error("content quarantined")]
    Quarantine,
    #[error("requested item was not found")]
    NotFound,
    #[error("backup decryption failed")]
    BackupDecryption,
    #[error("backup integrity verification failed")]
    BackupIntegrity,
    #[error("invalid backup container")]
    InvalidBackupContainer,
    #[error("operation denied by policy")]
    PolicyDenied,
    #[error("sandbox trapped")]
    SandboxTrap,
    #[error("serialization failed")]
    Serialization(#[source] serde_json::Error),
}
