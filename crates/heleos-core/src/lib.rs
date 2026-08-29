#![forbid(unsafe_code)]

pub mod domain;
mod error;
pub mod pdf;
pub mod store;
pub mod vault;

pub use crate::domain::{
    ActorId, Clock, DataClass, DocumentId, EvidenceId, IdGenerator, IngestEventId, IngestOutcome,
    JobId, JobState, PdfLimits, ProjectId, RevisionId, Sha256Digest, SheetId, can_transition,
    canonical_document_ids, canonical_json, page_id,
};
pub use crate::error::{HeleosError, Result};
pub use crate::pdf::{
    ApprovedPdfGuest, PageMetadata, PageTransform, PageUnit, PdfActiveFeature, PdfInspection,
    PdfLimitKind, PdfProbe, PdfProbeOutcome, PdfProbeProvenance, PdfQuarantine,
    PdfQuarantineReason, PdfSandboxConfig, WasiPdfProbe,
};
pub use crate::store::{
    FOUNDATION_SCHEMA_VERSION, INTEGRITY_VIOLATION_LIMIT, IntegrityReport, MigrationReport, Store,
    WriterLock, apply_private_permissions, verify_private_permissions,
};
pub use crate::vault::{
    EncodedVaultPath, PutOutcome, ReconciliationFinding, ReconciliationReport, StoredObject, Vault,
    VaultConfig, VaultInventory, VaultInventoryEntry, VaultOpenMode, VaultVerification,
    VaultWriteBudget, VerifiedObject,
};
