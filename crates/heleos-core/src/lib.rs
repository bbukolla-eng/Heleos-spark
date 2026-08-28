#![forbid(unsafe_code)]

pub mod domain;
mod error;
pub mod store;

pub use crate::domain::{
    ActorId, Clock, DataClass, DocumentId, EvidenceId, IdGenerator, IngestEventId, IngestOutcome,
    JobId, JobState, PdfLimits, ProjectId, RevisionId, Sha256Digest, SheetId, can_transition,
    canonical_document_ids, canonical_json, page_id,
};
pub use crate::error::{HeleosError, Result};
pub use crate::store::{
    FOUNDATION_SCHEMA_VERSION, INTEGRITY_VIOLATION_LIMIT, IntegrityReport, MigrationReport, Store,
    WriterLock, apply_private_permissions, verify_private_permissions,
};
