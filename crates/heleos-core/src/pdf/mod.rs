pub(crate) mod geometry;
mod wasi_host;

use std::path::PathBuf;

use serde::{Deserialize, Deserializer, Serialize, de};

use crate::{
    HeleosError, IngestOutcome, PdfLimits, Result, RevisionId, Sha256Digest, VerifiedObject,
};

pub use geometry::{PageMetadata, PageTransform, PageUnit};

pub trait PdfProbe: Send + Sync {
    fn provenance(&self) -> Result<PdfProbeProvenance>;

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

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct PdfProbeProvenance {
    pub parser_name: String,
    pub parser_version: String,
    pub guest_wasm_sha256: Sha256Digest,
    pub guest_source_tree_sha256: Sha256Digest,
    pub guest_dependency_graph_sha256: Sha256Digest,
    pub protocol_version: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawPdfProbeProvenance {
    #[serde(deserialize_with = "deserialize_provenance_text")]
    parser_name: String,
    #[serde(deserialize_with = "deserialize_provenance_text")]
    parser_version: String,
    guest_wasm_sha256: Sha256Digest,
    guest_source_tree_sha256: Sha256Digest,
    guest_dependency_graph_sha256: Sha256Digest,
    #[serde(deserialize_with = "deserialize_provenance_text")]
    protocol_version: String,
}

fn deserialize_provenance_text<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<String, D::Error> {
    crate::ingest::deserialize_bounded_string::<D, 256>(deserializer)
}

impl TryFrom<RawPdfProbeProvenance> for PdfProbeProvenance {
    type Error = HeleosError;

    fn try_from(raw: RawPdfProbeProvenance) -> Result<Self> {
        let value = Self {
            parser_name: raw.parser_name,
            parser_version: raw.parser_version,
            guest_wasm_sha256: raw.guest_wasm_sha256,
            guest_source_tree_sha256: raw.guest_source_tree_sha256,
            guest_dependency_graph_sha256: raw.guest_dependency_graph_sha256,
            protocol_version: raw.protocol_version,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for PdfProbeProvenance {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawPdfProbeProvenance::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

impl PdfProbeProvenance {
    pub(crate) fn validate(&self) -> Result<()> {
        if [
            self.parser_name.as_str(),
            self.parser_version.as_str(),
            self.protocol_version.as_str(),
        ]
        .iter()
        .any(|field| field.is_empty() || field.len() > 256 || field.chars().any(char::is_control))
        {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(
    tag = "kind",
    content = "detail",
    rename_all = "snake_case",
    deny_unknown_fields
)]
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
