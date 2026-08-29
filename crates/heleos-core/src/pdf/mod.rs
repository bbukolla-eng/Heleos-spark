mod geometry;
mod wasi_host;

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::{IngestOutcome, PdfLimits, Result, RevisionId, Sha256Digest, VerifiedObject};

pub use geometry::{PageMetadata, PageTransform, PageUnit};

pub trait PdfProbe: Send + Sync {
    fn probe(
        &self,
        input: VerifiedObject,
        revision: RevisionId,
        limits: PdfLimits,
    ) -> Result<PdfProbeOutcome>;
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PdfSandboxConfig {
    pub staging_parent: PathBuf,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "outcome", content = "value", rename_all = "snake_case")]
pub enum PdfProbeOutcome {
    Accepted(PdfInspection),
    Quarantined(PdfQuarantine),
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PdfInspection {
    pub revision_id: RevisionId,
    pub content_sha256: Sha256Digest,
    pub byte_length: u64,
    pub provenance: PdfProbeProvenance,
    pub pages: Vec<PageMetadata>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PdfQuarantine {
    pub content_sha256: Sha256Digest,
    pub byte_length: u64,
    pub provenance: PdfProbeProvenance,
    pub reason: PdfQuarantineReason,
}

impl PdfQuarantine {
    pub const fn ingest_outcome(&self) -> IngestOutcome {
        match self.reason {
            PdfQuarantineReason::BadMagic
            | PdfQuarantineReason::Corrupt
            | PdfQuarantineReason::InvalidGeometry => IngestOutcome::QuarantinedCorrupt,
            PdfQuarantineReason::Encrypted => IngestOutcome::QuarantinedEncrypted,
            PdfQuarantineReason::UnsupportedUserUnit => IngestOutcome::QuarantinedUnsupported,
            PdfQuarantineReason::ActiveFeature(_)
            | PdfQuarantineReason::SandboxTrap
            | PdfQuarantineReason::ProtocolBreach => IngestOutcome::QuarantinedSuspicious,
            PdfQuarantineReason::LimitExceeded(_) => IngestOutcome::QuarantinedLimit,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PdfProbeProvenance {
    pub parser_name: String,
    pub parser_version: String,
    pub guest_wasm_sha256: Sha256Digest,
    pub guest_source_tree_sha256: Sha256Digest,
    pub guest_dependency_graph_sha256: Sha256Digest,
    pub protocol_version: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", content = "detail", rename_all = "snake_case")]
pub enum PdfQuarantineReason {
    BadMagic,
    Corrupt,
    Encrypted,
    InvalidGeometry,
    UnsupportedUserUnit,
    ActiveFeature(PdfActiveFeature),
    LimitExceeded(PdfLimitKind),
    SandboxTrap,
    ProtocolBreach,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PdfActiveFeature {
    OpenAction,
    AdditionalActions,
    JavaScriptAbbreviation,
    JavaScript,
    Launch,
    Uri,
    GoToRemote,
    SubmitForm,
    ImportData,
    RichMedia,
    EmbeddedFiles,
    AssociatedFiles,
    Xfa,
    AcroForm,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PdfLimitKind {
    InputBytes,
    Pages,
    IndirectObjects,
    NestedReferences,
    MetadataBytes,
    PageAxisPoints,
    GuestMemoryBytes,
    Memories,
    Instances,
    Tables,
    TableElements,
    Fuel,
    HostCalls,
    Timeout,
    ProtocolOutputBytes,
}

pub use wasi_host::{ApprovedPdfGuest, WasiPdfProbe};
