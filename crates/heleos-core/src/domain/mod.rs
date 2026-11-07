mod digest;
mod identity;
mod state;
mod time;

use serde::Serialize;

use crate::{HeleosError, Result};

pub use digest::Sha256Digest;
pub use identity::{
    ActorId, DocumentId, EvidenceId, IngestEventId, JobId, ProjectId, RevisionId, SheetId,
    canonical_document_ids, page_id,
};
pub use state::{DataClass, IngestOutcome, JobState, PdfLimits, can_transition};
pub use time::{Clock, IdGenerator};

pub fn canonical_json<T: Serialize>(value: &T) -> Result<Vec<u8>> {
    serde_jcs::to_vec(value).map_err(HeleosError::Serialization)
}
