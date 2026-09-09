use std::fmt;
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use cap_fs_ext::MetadataExt as _;
use serde::{Deserialize, Deserializer, Serialize, de};
use sha2::{Digest, Sha256};

use crate::{
    ActorId, DocumentId, EvidenceId, HeleosError, IdempotencyKey, IngestEventId, IngestOutcome,
    JobId, PdfLimits, PdfProbeProvenance, PdfQuarantineReason, ProjectId, Result, RevisionId,
    Sha256Digest, SheetId, StoredObject,
};

use super::audit::JCS_SAFE_INTEGER_MAX;
use super::evidence::{
    EvidenceManifestReceipt, EvidenceManifestV1, EvidencePdfLimitsV1, validate_provenance,
};

pub(crate) const INGEST_KIND: &str = "pdf_ingest";
pub(crate) const INGEST_INPUT_SCHEMA_V1: &str = "heleos.ingest-input/v1";
pub(crate) const INGEST_BUDGET_SCHEMA_V1: &str = "heleos.ingest-budget/v1";
pub(crate) const INGEST_CHECKPOINT_SCHEMA_V1: &str = "heleos.ingest-checkpoint/v1";
pub(crate) const INGEST_EVENT_DETAILS_SCHEMA_V1: &str = "heleos.ingest-event-details/v1";
pub(crate) const LEASE_DURATION_MS: i64 = 30_000;
pub(crate) const JOB_DEADLINE_MS: i64 = 300_000;
pub(crate) const MAX_JOB_ATTEMPTS: u32 = 16;
pub(crate) const PROJECT_OVERALL_QUOTA_BYTES: u64 = 50 * 1024 * 1024 * 1024;
pub(crate) const STORE_OVERALL_QUOTA_BYTES: u64 = 500 * 1024 * 1024 * 1024;
pub(crate) const PROJECT_EVIDENCE_QUOTA_BYTES: u64 = 5 * 1024 * 1024 * 1024;
pub(crate) const STORE_EVIDENCE_QUOTA_BYTES: u64 = 50 * 1024 * 1024 * 1024;
pub(crate) const PROJECT_QUARANTINE_QUOTA_BYTES: u64 = 2 * 1024 * 1024 * 1024;
pub(crate) const STORE_QUARANTINE_QUOTA_BYTES: u64 = 8 * 1024 * 1024 * 1024;
pub(crate) const MAX_SOURCE_DISPLAY_BYTES: usize = 4 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct SourceMarker {
    device: u64,
    inode: u64,
    length: u64,
}

pub struct IntakeSource {
    file: File,
    marker: SourceMarker,
    display_name: String,
    #[cfg(test)]
    after_vault_read: Option<std::sync::Arc<dyn Fn() -> Result<()> + Send + Sync>>,
}

impl fmt::Debug for IntakeSource {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("IntakeSource")
            .field("display_name", &self.display_name)
            .finish_non_exhaustive()
    }
}

impl IntakeSource {
    pub fn open(path: &Path) -> Result<Self> {
        let mut options = OpenOptions::new();
        options.read(true);
        configure_source_open(&mut options);
        let file = options.open(path).map_err(map_source_open_error)?;
        let marker = source_marker(&file)?;
        let display_name =
            encode_source_display(path.file_name().ok_or(HeleosError::PolicyDenied)?)?;
        Ok(Self {
            file,
            marker,
            display_name,
            #[cfg(test)]
            after_vault_read: None,
        })
    }

    pub(crate) fn fingerprint(&mut self, maximum: u64) -> Result<SourceFingerprint> {
        if maximum == 0 {
            return Err(HeleosError::PolicyDenied);
        }
        self.recheck()?;
        self.file
            .seek(SeekFrom::Start(0))
            .map_err(HeleosError::Io)?;
        let wanted = maximum.checked_add(1).ok_or(HeleosError::Integrity)?;
        let mut remaining = wanted;
        let mut observed = 0_u64;
        let mut hasher = Sha256::new();
        let mut buffer = [0_u8; 64 * 1024];
        while remaining > 0 {
            let amount = usize::try_from(remaining.min(buffer.len() as u64))
                .map_err(|_| HeleosError::Integrity)?;
            let read = self
                .file
                .read(&mut buffer[..amount])
                .map_err(HeleosError::Io)?;
            if read == 0 {
                break;
            }
            hasher.update(&buffer[..read]);
            let read = u64::try_from(read).map_err(|_| HeleosError::Integrity)?;
            observed = observed.checked_add(read).ok_or(HeleosError::Integrity)?;
            remaining = remaining.checked_sub(read).ok_or(HeleosError::Integrity)?;
        }
        self.recheck()?;
        if self.marker.length > maximum {
            if observed != wanted {
                return Err(HeleosError::Integrity);
            }
            self.rewind()?;
            return Ok(SourceFingerprint {
                digest: None,
                byte_length: self.marker.length,
                display_name: self.display_name.clone(),
            });
        }
        if observed != self.marker.length {
            return Err(HeleosError::Integrity);
        }
        let mut trailing = [0_u8; 1];
        if self.file.read(&mut trailing).map_err(HeleosError::Io)? != 0 {
            return Err(HeleosError::Integrity);
        }
        let digest = Sha256Digest::from_bytes(hasher.finalize().into());
        self.rewind()?;
        Ok(SourceFingerprint {
            digest: Some(digest),
            byte_length: observed,
            display_name: self.display_name.clone(),
        })
    }

    pub(crate) fn publish(
        &mut self,
        vault: &crate::Vault,
        budget: crate::VaultWriteBudget,
        inventory: &crate::VaultInventory,
        orphan_budget: crate::vault::VaultOrphanBudget,
        expected: Sha256Digest,
        expected_length: u64,
    ) -> Result<crate::PutOutcome> {
        self.recheck()?;
        self.rewind()?;
        let outcome =
            vault.put_reader_accounted(&mut self.file, budget, inventory, orphan_budget)?;
        #[cfg(test)]
        if let Some(observer) = &self.after_vault_read {
            observer()?;
        }
        let (digest, byte_length) = match &outcome {
            crate::PutOutcome::Stored(stored) => (stored.digest, stored.byte_length),
            crate::PutOutcome::QuotaRejected {
                digest,
                byte_length,
            } => (*digest, *byte_length),
        };
        if digest != expected || byte_length != expected_length {
            return Err(HeleosError::Integrity);
        }
        self.recheck()?;
        let repeated = self.fingerprint(PdfLimits::default().max_input_bytes)?;
        if repeated.digest != Some(expected) || repeated.byte_length != expected_length {
            return Err(HeleosError::Integrity);
        }
        Ok(outcome)
    }

    #[cfg(all(test, unix))]
    fn inject_after_vault_read_for_test(
        &mut self,
        observer: std::sync::Arc<dyn Fn() -> Result<()> + Send + Sync>,
    ) {
        self.after_vault_read = Some(observer);
    }

    pub(crate) fn verify_unchanged(&mut self, expected: &SourceFingerprint) -> Result<()> {
        let repeated = self.fingerprint(PdfLimits::default().max_input_bytes)?;
        if &repeated != expected {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }

    fn rewind(&mut self) -> Result<()> {
        self.file
            .seek(SeekFrom::Start(0))
            .map(|_| ())
            .map_err(HeleosError::Io)
    }

    fn recheck(&self) -> Result<()> {
        if source_marker(&self.file)? != self.marker {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }
}

#[cfg(unix)]
fn configure_source_open(options: &mut OpenOptions) {
    use std::os::unix::fs::OpenOptionsExt;

    options.custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK);
}

#[cfg(windows)]
fn configure_source_open(options: &mut OpenOptions) {
    use std::os::windows::fs::OpenOptionsExt;

    const GENERIC_READ: u32 = 0x8000_0000;
    const READ_CONTROL: u32 = 0x0002_0000;
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    options
        .access_mode(GENERIC_READ | READ_CONTROL)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
}

#[cfg(not(any(unix, windows)))]
fn configure_source_open(_: &mut OpenOptions) {}

fn map_source_open_error(error: std::io::Error) -> HeleosError {
    #[cfg(unix)]
    if matches!(error.raw_os_error(), Some(code) if code == libc::ELOOP
        || code == libc::EOPNOTSUPP
        || code == libc::ENXIO
        || code == libc::ENODEV)
    {
        return HeleosError::PolicyDenied;
    }
    if matches!(
        error.kind(),
        std::io::ErrorKind::NotFound | std::io::ErrorKind::PermissionDenied
    ) {
        HeleosError::PolicyDenied
    } else {
        HeleosError::Io(error)
    }
}

fn source_marker(file: &File) -> Result<SourceMarker> {
    let metadata = file.metadata().map_err(HeleosError::Io)?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err(HeleosError::PolicyDenied);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;

        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(HeleosError::PolicyDenied);
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
        return Err(HeleosError::PolicyDenied);
    }
    #[cfg(any(unix, windows))]
    {
        let retained = cap_std::fs::File::from_std(file.try_clone().map_err(HeleosError::Io)?);
        let retained_metadata = retained.metadata().map_err(HeleosError::Io)?;
        Ok(SourceMarker {
            device: retained_metadata.dev(),
            inode: retained_metadata.ino(),
            length: metadata.len(),
        })
    }
}

#[cfg(unix)]
fn encode_source_display(name: &std::ffi::OsStr) -> Result<String> {
    use std::os::unix::ffi::OsStrExt;

    match name.to_str() {
        Some(value) => encode_utf8_display(value.as_bytes()),
        None => {
            let bytes = name.as_bytes();
            let mut value = String::with_capacity(11 + bytes.len().saturating_mul(2));
            value.push_str("unix-bytes:");
            for byte in bytes {
                use std::fmt::Write as _;
                write!(value, "{byte:02x}").map_err(|_| HeleosError::Integrity)?;
            }
            validate_display(value)
        }
    }
}

#[cfg(windows)]
fn encode_source_display(name: &std::ffi::OsStr) -> Result<String> {
    use std::os::windows::ffi::OsStrExt;

    let units = name.encode_wide();
    let (lower, _) = units.size_hint();
    let mut value = String::with_capacity(14 + lower.saturating_mul(4));
    value.push_str("windows-utf16:");
    for unit in units {
        use std::fmt::Write as _;
        write!(value, "{unit:04x}").map_err(|_| HeleosError::Integrity)?;
    }
    validate_display(value)
}

#[cfg(not(any(unix, windows)))]
fn encode_source_display(_: &std::ffi::OsStr) -> Result<String> {
    Err(HeleosError::PolicyDenied)
}

fn encode_utf8_display(bytes: &[u8]) -> Result<String> {
    let mut value = String::with_capacity(5 + bytes.len());
    value.push_str("utf8:");
    for byte in bytes {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-') {
            value.push(char::from(*byte));
        } else {
            use std::fmt::Write as _;
            write!(value, "%{byte:02x}").map_err(|_| HeleosError::Integrity)?;
        }
    }
    validate_display(value)
}

fn validate_display(value: String) -> Result<String> {
    if value.len() > MAX_SOURCE_DISPLAY_BYTES || value.chars().any(char::is_control) {
        return Err(HeleosError::ResourceLimit);
    }
    Ok(value)
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct SourceFingerprint {
    pub digest: Option<Sha256Digest>,
    pub byte_length: u64,
    pub display_name: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(
    tag = "kind",
    content = "detail",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum IntakeQuarantineReasonV1 {
    Pdf(PdfQuarantineReason),
    InputBytes {
        limit_bytes: u64,
        observed_bytes: u64,
    },
    OriginalRetentionQuota,
    ProjectAssociationQuota,
    EvidenceManifestQuota,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawIntakeQuarantineReasonV1 {
    kind: RawIntakeQuarantineReasonKind,
    #[serde(default)]
    detail: RawIntakeQuarantineReasonDetailField,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum RawIntakeQuarantineReasonKind {
    Pdf,
    InputBytes,
    OriginalRetentionQuota,
    ProjectAssociationQuota,
    EvidenceManifestQuota,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum RawIntakeQuarantineReasonDetail {
    Pdf(PdfQuarantineReason),
    InputBytes(RawInputBytesReason),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawInputBytesReason {
    limit_bytes: u64,
    observed_bytes: u64,
}

#[derive(Default)]
enum RawIntakeQuarantineReasonDetailField {
    #[default]
    Missing,
    Present(RawIntakeQuarantineReasonDetail),
}

impl<'de> Deserialize<'de> for RawIntakeQuarantineReasonDetailField {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        RawIntakeQuarantineReasonDetail::deserialize(deserializer).map(Self::Present)
    }
}

impl TryFrom<RawIntakeQuarantineReasonV1> for IntakeQuarantineReasonV1 {
    type Error = HeleosError;

    fn try_from(raw: RawIntakeQuarantineReasonV1) -> Result<Self> {
        let value = match (raw.kind, raw.detail) {
            (
                RawIntakeQuarantineReasonKind::Pdf,
                RawIntakeQuarantineReasonDetailField::Present(
                    RawIntakeQuarantineReasonDetail::Pdf(reason),
                ),
            ) => Self::Pdf(reason),
            (
                RawIntakeQuarantineReasonKind::InputBytes,
                RawIntakeQuarantineReasonDetailField::Present(
                    RawIntakeQuarantineReasonDetail::InputBytes(RawInputBytesReason {
                        limit_bytes,
                        observed_bytes,
                    }),
                ),
            ) => Self::InputBytes {
                limit_bytes,
                observed_bytes,
            },
            (
                RawIntakeQuarantineReasonKind::OriginalRetentionQuota,
                RawIntakeQuarantineReasonDetailField::Missing,
            ) => Self::OriginalRetentionQuota,
            (
                RawIntakeQuarantineReasonKind::ProjectAssociationQuota,
                RawIntakeQuarantineReasonDetailField::Missing,
            ) => Self::ProjectAssociationQuota,
            (
                RawIntakeQuarantineReasonKind::EvidenceManifestQuota,
                RawIntakeQuarantineReasonDetailField::Missing,
            ) => Self::EvidenceManifestQuota,
            _ => return Err(HeleosError::Integrity),
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for IntakeQuarantineReasonV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawIntakeQuarantineReasonV1::deserialize(deserializer)?)
            .map_err(de::Error::custom)
    }
}

impl IntakeQuarantineReasonV1 {
    fn validate(&self) -> Result<()> {
        match self {
            Self::InputBytes {
                limit_bytes,
                observed_bytes,
            } if *limit_bytes == PdfLimits::default().max_input_bytes
                && *observed_bytes > *limit_bytes
                && *observed_bytes <= JCS_SAFE_INTEGER_MAX => {}
            Self::Pdf(_)
            | Self::OriginalRetentionQuota
            | Self::ProjectAssociationQuota
            | Self::EvidenceManifestQuota => {}
            Self::InputBytes { .. } => return Err(HeleosError::Integrity),
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct IntakeQuarantineV1 {
    pub schema: String,
    pub reason: IntakeQuarantineReasonV1,
    pub probe_provenance: Option<PdfProbeProvenance>,
}

pub const INTAKE_QUARANTINE_SCHEMA_V1: &str = "heleos.intake-quarantine/v1";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawIntakeQuarantineV1 {
    schema: String,
    reason: IntakeQuarantineReasonV1,
    probe_provenance: Option<PdfProbeProvenance>,
}

impl TryFrom<RawIntakeQuarantineV1> for IntakeQuarantineV1 {
    type Error = HeleosError;

    fn try_from(raw: RawIntakeQuarantineV1) -> Result<Self> {
        let value = Self {
            schema: raw.schema,
            reason: raw.reason,
            probe_provenance: raw.probe_provenance,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for IntakeQuarantineV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawIntakeQuarantineV1::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

impl IntakeQuarantineV1 {
    pub(crate) fn validate(&self) -> Result<()> {
        if self.schema != INTAKE_QUARANTINE_SCHEMA_V1 {
            return Err(HeleosError::Integrity);
        }
        self.reason.validate()?;
        match (&self.reason, &self.probe_provenance) {
            (IntakeQuarantineReasonV1::Pdf(_), Some(provenance))
            | (IntakeQuarantineReasonV1::EvidenceManifestQuota, Some(provenance)) => {
                validate_provenance(provenance)?;
            }
            (IntakeQuarantineReasonV1::InputBytes { .. }, None) => {}
            (IntakeQuarantineReasonV1::OriginalRetentionQuota, None)
            | (IntakeQuarantineReasonV1::ProjectAssociationQuota, None) => {}
            _ => return Err(HeleosError::Integrity),
        }
        Ok(())
    }
}

pub struct IngestRequest {
    pub project_id: ProjectId,
    pub source: IntakeSource,
    pub idempotency_key: IdempotencyKey,
    pub actor: ActorId,
    pub pdf_limits: PdfLimits,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct IngestReceipt {
    pub ingest_event_id: IngestEventId,
    pub authoritative_job_id: JobId,
    pub attempt: u32,
    pub outcome: IngestOutcome,
    pub content_sha256: Option<Sha256Digest>,
    pub byte_length: u64,
    pub quarantine: Option<IntakeQuarantineV1>,
    pub document_id: Option<DocumentId>,
    pub revision_id: Option<RevisionId>,
    pub sheet_ids: Vec<SheetId>,
    pub evidence_manifest: Option<EvidenceManifestReceipt>,
    pub preexisting_vault_digests: Vec<Sha256Digest>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawIngestReceipt {
    ingest_event_id: IngestEventId,
    authoritative_job_id: JobId,
    attempt: u32,
    outcome: IngestOutcome,
    content_sha256: Option<Sha256Digest>,
    byte_length: u64,
    quarantine: Option<IntakeQuarantineV1>,
    document_id: Option<DocumentId>,
    revision_id: Option<RevisionId>,
    #[serde(deserialize_with = "deserialize_sheet_ids")]
    sheet_ids: Vec<SheetId>,
    evidence_manifest: Option<EvidenceManifestReceipt>,
    #[serde(deserialize_with = "deserialize_preexisting_digests")]
    preexisting_vault_digests: Vec<Sha256Digest>,
}

fn deserialize_sheet_ids<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Vec<SheetId>, D::Error> {
    super::deserialize_bounded_vec::<D, SheetId, 10_000>(deserializer)
}

fn deserialize_preexisting_digests<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Vec<Sha256Digest>, D::Error> {
    super::deserialize_bounded_vec::<D, Sha256Digest, 2>(deserializer)
}

impl TryFrom<RawIngestReceipt> for IngestReceipt {
    type Error = HeleosError;

    fn try_from(raw: RawIngestReceipt) -> Result<Self> {
        let value = Self {
            ingest_event_id: raw.ingest_event_id,
            authoritative_job_id: raw.authoritative_job_id,
            attempt: raw.attempt,
            outcome: raw.outcome,
            content_sha256: raw.content_sha256,
            byte_length: raw.byte_length,
            quarantine: raw.quarantine,
            document_id: raw.document_id,
            revision_id: raw.revision_id,
            sheet_ids: raw.sheet_ids,
            evidence_manifest: raw.evidence_manifest,
            preexisting_vault_digests: raw.preexisting_vault_digests,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for IngestReceipt {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawIngestReceipt::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

impl IngestReceipt {
    pub(crate) fn validate(&self) -> Result<()> {
        if !(1..=MAX_JOB_ATTEMPTS).contains(&self.attempt)
            || self.byte_length > JCS_SAFE_INTEGER_MAX
            || self.preexisting_vault_digests.len() > 2
            || !is_strictly_sorted(&self.preexisting_vault_digests)
        {
            return Err(HeleosError::Integrity);
        }
        let accepted_shape = self.content_sha256.is_some()
            && self.quarantine.is_none()
            && self.document_id.is_some()
            && self.revision_id.is_some()
            && !self.sheet_ids.is_empty()
            && self.sheet_ids.len() <= 10_000
            && self.evidence_manifest.is_some();
        let quarantine_shape = self.quarantine.is_some()
            && self.document_id.is_none()
            && self.revision_id.is_none()
            && self.sheet_ids.is_empty()
            && self.evidence_manifest.is_none();
        match self.outcome {
            IngestOutcome::AcceptedNew | IngestOutcome::AcceptedDuplicate if accepted_shape => {}
            IngestOutcome::IdempotentReplay if accepted_shape || quarantine_shape => {}
            IngestOutcome::QuarantinedCorrupt
            | IngestOutcome::QuarantinedEncrypted
            | IngestOutcome::QuarantinedUnsupported
            | IngestOutcome::QuarantinedSuspicious
            | IngestOutcome::QuarantinedLimit
                if quarantine_shape => {}
            _ => return Err(HeleosError::Integrity),
        }
        if let Some(quarantine) = &self.quarantine {
            quarantine.validate()?;
            let is_input_bytes = matches!(
                quarantine.reason,
                IntakeQuarantineReasonV1::InputBytes { .. }
            );
            if is_input_bytes != self.content_sha256.is_none()
                || !quarantine_observed_length_matches(&quarantine.reason, self.byte_length)
                || (self.outcome != IngestOutcome::IdempotentReplay
                    && self.outcome != quarantine_ingest_outcome(&quarantine.reason))
            {
                return Err(HeleosError::Integrity);
            }
        }
        if accepted_shape {
            let content = self.content_sha256.ok_or(HeleosError::Integrity)?;
            let (document, revision) = crate::canonical_document_ids(content);
            if self.document_id != Some(document)
                || self.revision_id != Some(revision)
                || !is_unique(&self.sheet_ids)
            {
                return Err(HeleosError::Integrity);
            }
            let evidence = self
                .evidence_manifest
                .as_ref()
                .ok_or(HeleosError::Integrity)?;
            evidence.validate()?;
            if evidence.manifest.original.sha256 != content
                || evidence.manifest.original.byte_length != self.byte_length
                || evidence.manifest.document_id != document
                || evidence.manifest.revision_id != revision
                || !self.sheet_ids.iter().copied().eq(evidence
                    .manifest
                    .pages
                    .iter()
                    .map(|page| page.page_id))
            {
                return Err(HeleosError::Integrity);
            }
        }
        let manifest_digest = self
            .evidence_manifest
            .as_ref()
            .map(|evidence| evidence.manifest_content_sha256);
        if self
            .preexisting_vault_digests
            .iter()
            .any(|digest| Some(*digest) != self.content_sha256 && Some(*digest) != manifest_digest)
        {
            return Err(HeleosError::Integrity);
        }
        if self.outcome == IngestOutcome::AcceptedDuplicate {
            let mut expected = vec![
                self.content_sha256.ok_or(HeleosError::Integrity)?,
                manifest_digest.ok_or(HeleosError::Integrity)?,
            ];
            expected.sort_unstable();
            expected.dedup();
            if self.preexisting_vault_digests != expected {
                return Err(HeleosError::Integrity);
            }
        }
        if self.outcome != IngestOutcome::IdempotentReplay
            && let Some(quarantine) = &self.quarantine
        {
            match quarantine.reason {
                IntakeQuarantineReasonV1::ProjectAssociationQuota => {
                    if self.preexisting_vault_digests
                        != [self.content_sha256.ok_or(HeleosError::Integrity)?]
                    {
                        return Err(HeleosError::Integrity);
                    }
                }
                IntakeQuarantineReasonV1::InputBytes { .. }
                | IntakeQuarantineReasonV1::OriginalRetentionQuota
                    if !self.preexisting_vault_digests.is_empty() =>
                {
                    return Err(HeleosError::Integrity);
                }
                _ => {}
            }
        }
        if self.outcome == IngestOutcome::IdempotentReplay {
            let mut expected = if accepted_shape {
                vec![
                    self.content_sha256.ok_or(HeleosError::Integrity)?,
                    manifest_digest.ok_or(HeleosError::Integrity)?,
                ]
            } else if self.quarantine.as_ref().is_some_and(|quarantine| {
                matches!(
                    quarantine.reason,
                    IntakeQuarantineReasonV1::Pdf(_)
                        | IntakeQuarantineReasonV1::EvidenceManifestQuota
                        | IntakeQuarantineReasonV1::ProjectAssociationQuota
                )
            }) {
                vec![self.content_sha256.ok_or(HeleosError::Integrity)?]
            } else {
                Vec::new()
            };
            expected.sort_unstable();
            expected.dedup();
            if self.preexisting_vault_digests != expected {
                return Err(HeleosError::Integrity);
            }
        }
        Ok(())
    }
}

pub(crate) fn quarantine_ingest_outcome(reason: &IntakeQuarantineReasonV1) -> IngestOutcome {
    match reason {
        IntakeQuarantineReasonV1::Pdf(reason) => match reason {
            PdfQuarantineReason::BadMagic
            | PdfQuarantineReason::Corrupt
            | PdfQuarantineReason::InvalidGeometry => IngestOutcome::QuarantinedCorrupt,
            PdfQuarantineReason::Encrypted => IngestOutcome::QuarantinedEncrypted,
            PdfQuarantineReason::UnsupportedUserUnit => IngestOutcome::QuarantinedUnsupported,
            PdfQuarantineReason::ActiveFeature(_)
            | PdfQuarantineReason::SandboxTrap
            | PdfQuarantineReason::ProtocolBreach => IngestOutcome::QuarantinedSuspicious,
            PdfQuarantineReason::LimitExceeded(_) => IngestOutcome::QuarantinedLimit,
        },
        IntakeQuarantineReasonV1::InputBytes { .. }
        | IntakeQuarantineReasonV1::OriginalRetentionQuota
        | IntakeQuarantineReasonV1::ProjectAssociationQuota
        | IntakeQuarantineReasonV1::EvidenceManifestQuota => IngestOutcome::QuarantinedLimit,
    }
}

fn quarantine_observed_length_matches(reason: &IntakeQuarantineReasonV1, byte_length: u64) -> bool {
    !matches!(
        reason,
        IntakeQuarantineReasonV1::InputBytes { observed_bytes, .. }
            if *observed_bytes != byte_length
    )
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ResumeReceipt {
    pub job_id: JobId,
    pub resumed_attempt: Option<u32>,
    pub interrupted_event_id: Option<IngestEventId>,
    pub receipt: IngestReceipt,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawResumeReceipt {
    job_id: JobId,
    resumed_attempt: Option<u32>,
    interrupted_event_id: Option<IngestEventId>,
    receipt: IngestReceipt,
}

impl TryFrom<RawResumeReceipt> for ResumeReceipt {
    type Error = HeleosError;

    fn try_from(raw: RawResumeReceipt) -> Result<Self> {
        let value = Self {
            job_id: raw.job_id,
            resumed_attempt: raw.resumed_attempt,
            interrupted_event_id: raw.interrupted_event_id,
            receipt: raw.receipt,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for ResumeReceipt {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawResumeReceipt::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

impl ResumeReceipt {
    fn validate(&self) -> Result<()> {
        self.receipt.validate()?;
        if self.job_id != self.receipt.authoritative_job_id
            || self.receipt.outcome == IngestOutcome::IdempotentReplay
        {
            return Err(HeleosError::Integrity);
        }
        match (self.resumed_attempt, self.interrupted_event_id) {
            (Some(1), None) if self.receipt.attempt == 1 => {}
            (Some(attempt), Some(_))
                if (2..=MAX_JOB_ATTEMPTS).contains(&attempt) && self.receipt.attempt == attempt => {
            }
            (None, None) => {}
            _ => return Err(HeleosError::Integrity),
        }
        Ok(())
    }
}

fn is_strictly_sorted(values: &[Sha256Digest]) -> bool {
    values.windows(2).all(|pair| pair[0] < pair[1])
}

fn is_unique(values: &[SheetId]) -> bool {
    let mut seen = std::collections::BTreeSet::new();
    values.iter().all(|value| seen.insert(*value.as_digest()))
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub(crate) struct IngestInputV1 {
    pub schema: String,
    pub project_id: ProjectId,
    pub kind: String,
    pub idempotency_key: IdempotencyKey,
    pub content_sha256: Option<Sha256Digest>,
    pub byte_length: u64,
    pub source_display: String,
    pub requested_limits: EvidencePdfLimitsV1,
    pub expected_probe_provenance: PdfProbeProvenance,
    pub deadline_profile_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawIngestInputV1 {
    schema: String,
    project_id: ProjectId,
    kind: String,
    idempotency_key: IdempotencyKey,
    content_sha256: Option<Sha256Digest>,
    byte_length: u64,
    #[serde(deserialize_with = "deserialize_source_display")]
    source_display: String,
    requested_limits: EvidencePdfLimitsV1,
    expected_probe_provenance: PdfProbeProvenance,
    deadline_profile_ms: u64,
}

fn deserialize_source_display<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<String, D::Error> {
    super::deserialize_bounded_string::<D, MAX_SOURCE_DISPLAY_BYTES>(deserializer)
}

impl TryFrom<RawIngestInputV1> for IngestInputV1 {
    type Error = HeleosError;

    fn try_from(raw: RawIngestInputV1) -> Result<Self> {
        let value = Self {
            schema: raw.schema,
            project_id: raw.project_id,
            kind: raw.kind,
            idempotency_key: raw.idempotency_key,
            content_sha256: raw.content_sha256,
            byte_length: raw.byte_length,
            source_display: raw.source_display,
            requested_limits: raw.requested_limits,
            expected_probe_provenance: raw.expected_probe_provenance,
            deadline_profile_ms: raw.deadline_profile_ms,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for IngestInputV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawIngestInputV1::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

impl IngestInputV1 {
    pub(crate) fn validate(&self) -> Result<()> {
        let content_is_complete = self.content_sha256.is_some();
        if self.schema != INGEST_INPUT_SCHEMA_V1
            || self.kind != INGEST_KIND
            || self.byte_length > JCS_SAFE_INTEGER_MAX
            || self.requested_limits != EvidencePdfLimitsV1::from(PdfLimits::default())
            || self.deadline_profile_ms != JOB_DEADLINE_MS as u64
            || content_is_complete != (self.byte_length <= PdfLimits::default().max_input_bytes)
            || !source_display_is_valid(&self.source_display)
        {
            return Err(HeleosError::Integrity);
        }
        validate_provenance(&self.expected_probe_provenance)
    }

    pub(crate) fn frozen_eq(&self, other: &Self) -> bool {
        self.schema == other.schema
            && self.project_id == other.project_id
            && self.kind == other.kind
            && self.idempotency_key == other.idempotency_key
            && self.content_sha256.is_some()
            && self.content_sha256 == other.content_sha256
            && self.byte_length == other.byte_length
            && self.requested_limits == other.requested_limits
            && self.expected_probe_provenance == other.expected_probe_provenance
            && self.deadline_profile_ms == other.deadline_profile_ms
    }

    pub(crate) fn frozen_mismatching_fields(
        &self,
        other: &Self,
        budget_matches: bool,
    ) -> Vec<String> {
        let mut fields = Vec::with_capacity(FROZEN_MISMATCH_FIELDS.len());
        if !budget_matches {
            fields.push("budget".to_owned());
        }
        if self.byte_length != other.byte_length {
            fields.push("byte_length".to_owned());
        }
        if self.content_sha256 != other.content_sha256 || self.content_sha256.is_none() {
            fields.push("content_sha256".to_owned());
        }
        if self.deadline_profile_ms != other.deadline_profile_ms {
            fields.push("deadline_profile_ms".to_owned());
        }
        if self.expected_probe_provenance != other.expected_probe_provenance {
            fields.push("expected_probe_provenance".to_owned());
        }
        if self.requested_limits != other.requested_limits {
            fields.push("requested_limits".to_owned());
        }
        fields
    }
}

fn source_display_is_valid(value: &str) -> bool {
    if value.is_empty()
        || value.len() > MAX_SOURCE_DISPLAY_BYTES
        || value.chars().any(char::is_control)
    {
        return false;
    }
    if let Some(encoded) = value.strip_prefix("unix-bytes:") {
        let Some(bytes) = decode_lower_hex(encoded, 2) else {
            return false;
        };
        return !bytes.is_empty()
            && std::str::from_utf8(&bytes).is_err()
            && !bytes.contains(&0)
            && !bytes.contains(&b'/');
    }
    if let Some(encoded) = value.strip_prefix("windows-utf16:") {
        let Some(bytes) = decode_lower_hex(encoded, 4) else {
            return false;
        };
        let units: Vec<u16> = bytes
            .chunks_exact(2)
            .map(|pair| u16::from_be_bytes([pair[0], pair[1]]))
            .collect();
        return !units.is_empty()
            && units != [u16::from(b'.')]
            && units != [u16::from(b'.'), u16::from(b'.')]
            && !units.iter().any(|unit| matches!(*unit, 0 | 0x2f | 0x5c));
    }
    let Some(encoded) = value.strip_prefix("utf8:") else {
        return false;
    };
    let encoded_bytes = encoded.as_bytes();
    let mut decoded = Vec::with_capacity(encoded_bytes.len());
    let mut index = 0;
    while index < encoded_bytes.len() {
        let byte = encoded_bytes[index];
        if byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-') {
            decoded.push(byte);
            index += 1;
        } else if byte == b'%'
            && index + 2 < encoded_bytes.len()
            && lower_hex_value(encoded_bytes[index + 1]).is_some()
            && lower_hex_value(encoded_bytes[index + 2]).is_some()
        {
            let high = lower_hex_value(encoded_bytes[index + 1]).unwrap_or(0);
            let low = lower_hex_value(encoded_bytes[index + 2]).unwrap_or(0);
            decoded.push((high << 4) | low);
            index += 3;
        } else {
            return false;
        }
    }
    !decoded.is_empty()
        && decoded.as_slice() != b"."
        && decoded.as_slice() != b".."
        && std::str::from_utf8(&decoded).is_ok()
        && !decoded.contains(&0)
        && !decoded.contains(&b'/')
        && encode_utf8_display(&decoded).is_ok_and(|canonical| canonical == value)
}

fn decode_lower_hex(value: &str, encoded_unit_width: usize) -> Option<Vec<u8>> {
    if value.is_empty() || !value.len().is_multiple_of(encoded_unit_width) {
        return None;
    }
    let bytes = value.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len() / 2);
    for pair in bytes.chunks_exact(2) {
        decoded.push((lower_hex_value(pair[0])? << 4) | lower_hex_value(pair[1])?);
    }
    Some(decoded)
}

const fn lower_hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        _ => None,
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub(crate) struct IngestBudgetV1 {
    pub schema: String,
    pub project_overall_bytes: u64,
    pub store_overall_bytes: u64,
    pub project_evidence_bytes: u64,
    pub store_evidence_bytes: u64,
    pub project_quarantine_bytes: u64,
    pub store_quarantine_bytes: u64,
    pub lease_duration_ms: u64,
    pub max_attempts: u32,
    pub source_reservation_bytes: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawIngestBudgetV1 {
    schema: String,
    project_overall_bytes: u64,
    store_overall_bytes: u64,
    project_evidence_bytes: u64,
    store_evidence_bytes: u64,
    project_quarantine_bytes: u64,
    store_quarantine_bytes: u64,
    lease_duration_ms: u64,
    max_attempts: u32,
    source_reservation_bytes: u64,
}

impl TryFrom<RawIngestBudgetV1> for IngestBudgetV1 {
    type Error = HeleosError;

    fn try_from(raw: RawIngestBudgetV1) -> Result<Self> {
        let value = Self {
            schema: raw.schema,
            project_overall_bytes: raw.project_overall_bytes,
            store_overall_bytes: raw.store_overall_bytes,
            project_evidence_bytes: raw.project_evidence_bytes,
            store_evidence_bytes: raw.store_evidence_bytes,
            project_quarantine_bytes: raw.project_quarantine_bytes,
            store_quarantine_bytes: raw.store_quarantine_bytes,
            lease_duration_ms: raw.lease_duration_ms,
            max_attempts: raw.max_attempts,
            source_reservation_bytes: raw.source_reservation_bytes,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for IngestBudgetV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawIngestBudgetV1::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

impl IngestBudgetV1 {
    pub(crate) fn new(source_reservation_bytes: u64) -> Self {
        Self {
            schema: INGEST_BUDGET_SCHEMA_V1.to_owned(),
            project_overall_bytes: PROJECT_OVERALL_QUOTA_BYTES,
            store_overall_bytes: STORE_OVERALL_QUOTA_BYTES,
            project_evidence_bytes: PROJECT_EVIDENCE_QUOTA_BYTES,
            store_evidence_bytes: STORE_EVIDENCE_QUOTA_BYTES,
            project_quarantine_bytes: PROJECT_QUARANTINE_QUOTA_BYTES,
            store_quarantine_bytes: STORE_QUARANTINE_QUOTA_BYTES,
            lease_duration_ms: LEASE_DURATION_MS as u64,
            max_attempts: MAX_JOB_ATTEMPTS,
            source_reservation_bytes,
        }
    }

    pub(crate) fn validate(&self) -> Result<()> {
        if self.schema != INGEST_BUDGET_SCHEMA_V1
            || self.project_overall_bytes != PROJECT_OVERALL_QUOTA_BYTES
            || self.store_overall_bytes != STORE_OVERALL_QUOTA_BYTES
            || self.project_evidence_bytes != PROJECT_EVIDENCE_QUOTA_BYTES
            || self.store_evidence_bytes != STORE_EVIDENCE_QUOTA_BYTES
            || self.project_quarantine_bytes != PROJECT_QUARANTINE_QUOTA_BYTES
            || self.store_quarantine_bytes != STORE_QUARANTINE_QUOTA_BYTES
            || self.lease_duration_ms != LEASE_DURATION_MS as u64
            || self.max_attempts != MAX_JOB_ATTEMPTS
            || self.source_reservation_bytes > JCS_SAFE_INTEGER_MAX
        {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }

    pub(crate) fn validate_for_input(&self, input: &IngestInputV1) -> Result<()> {
        self.validate()?;
        if self.source_reservation_bytes != input.byte_length {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub(crate) struct StoredObjectV1 {
    pub digest: Sha256Digest,
    pub byte_length: u64,
    pub vault_key: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawStoredObjectV1 {
    digest: Sha256Digest,
    byte_length: u64,
    vault_key: String,
}

impl TryFrom<RawStoredObjectV1> for StoredObjectV1 {
    type Error = HeleosError;

    fn try_from(raw: RawStoredObjectV1) -> Result<Self> {
        let value = Self {
            digest: raw.digest,
            byte_length: raw.byte_length,
            vault_key: raw.vault_key,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for StoredObjectV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawStoredObjectV1::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

impl StoredObjectV1 {
    pub(crate) fn validate(&self) -> Result<()> {
        if self.byte_length > JCS_SAFE_INTEGER_MAX
            || self.vault_key != crate::Vault::object_key(self.digest)
        {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }
}

impl From<&StoredObject> for StoredObjectV1 {
    fn from(value: &StoredObject) -> Self {
        Self {
            digest: value.digest,
            byte_length: value.byte_length,
            vault_key: value.vault_key.clone(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(
    tag = "kind",
    content = "detail",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub(crate) enum ProcessingCandidateV1 {
    Accepted {
        manifest: Box<EvidenceManifestV1>,
        manifest_object: StoredObjectV1,
    },
    Quarantined {
        outcome: IngestOutcome,
        quarantine: IntakeQuarantineV1,
    },
}

#[derive(Deserialize)]
#[serde(
    tag = "kind",
    content = "detail",
    rename_all = "snake_case",
    deny_unknown_fields
)]
enum RawProcessingCandidateV1 {
    Accepted {
        manifest: Box<EvidenceManifestV1>,
        manifest_object: StoredObjectV1,
    },
    Quarantined {
        outcome: IngestOutcome,
        quarantine: IntakeQuarantineV1,
    },
}

impl TryFrom<RawProcessingCandidateV1> for ProcessingCandidateV1 {
    type Error = HeleosError;

    fn try_from(raw: RawProcessingCandidateV1) -> Result<Self> {
        let value = match raw {
            RawProcessingCandidateV1::Accepted {
                manifest,
                manifest_object,
            } => Self::Accepted {
                manifest,
                manifest_object,
            },
            RawProcessingCandidateV1::Quarantined {
                outcome,
                quarantine,
            } => Self::Quarantined {
                outcome,
                quarantine,
            },
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for ProcessingCandidateV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawProcessingCandidateV1::deserialize(deserializer)?)
            .map_err(de::Error::custom)
    }
}

impl ProcessingCandidateV1 {
    pub(crate) fn validate(&self) -> Result<()> {
        match self {
            Self::Accepted {
                manifest,
                manifest_object,
            } => {
                manifest.validate()?;
                manifest_object.validate()?;
                let bytes = manifest.canonical_bytes()?;
                let length = u64::try_from(bytes.len()).map_err(|_| HeleosError::Integrity)?;
                let digest = Sha256Digest::hash_reader(bytes.as_slice())?;
                if manifest_object.digest != digest || manifest_object.byte_length != length {
                    return Err(HeleosError::Integrity);
                }
            }
            Self::Quarantined {
                outcome,
                quarantine,
            } => {
                quarantine.validate()?;
                if !matches!(
                    quarantine.reason,
                    IntakeQuarantineReasonV1::Pdf(_)
                        | IntakeQuarantineReasonV1::EvidenceManifestQuota
                ) || *outcome != quarantine_ingest_outcome(&quarantine.reason)
                {
                    return Err(HeleosError::Integrity);
                }
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(
    tag = "phase",
    content = "detail",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub(crate) enum IngestCheckpointPhaseV1 {
    PreflightRejected {
        content_sha256: Option<Sha256Digest>,
        byte_length: u64,
        quarantine: IntakeQuarantineV1,
    },
    VaultPublished {
        original: StoredObjectV1,
    },
    ProcessingComplete {
        original: StoredObjectV1,
        candidate: ProcessingCandidateV1,
    },
    Terminal {
        receipt: Box<IngestReceipt>,
    },
}

#[derive(Deserialize)]
#[serde(
    tag = "phase",
    content = "detail",
    rename_all = "snake_case",
    deny_unknown_fields
)]
enum RawIngestCheckpointPhaseV1 {
    PreflightRejected {
        content_sha256: Option<Sha256Digest>,
        byte_length: u64,
        quarantine: IntakeQuarantineV1,
    },
    VaultPublished {
        original: StoredObjectV1,
    },
    ProcessingComplete {
        original: StoredObjectV1,
        candidate: ProcessingCandidateV1,
    },
    Terminal {
        receipt: Box<IngestReceipt>,
    },
}

impl From<RawIngestCheckpointPhaseV1> for IngestCheckpointPhaseV1 {
    fn from(raw: RawIngestCheckpointPhaseV1) -> Self {
        match raw {
            RawIngestCheckpointPhaseV1::PreflightRejected {
                content_sha256,
                byte_length,
                quarantine,
            } => Self::PreflightRejected {
                content_sha256,
                byte_length,
                quarantine,
            },
            RawIngestCheckpointPhaseV1::VaultPublished { original } => {
                Self::VaultPublished { original }
            }
            RawIngestCheckpointPhaseV1::ProcessingComplete {
                original,
                candidate,
            } => Self::ProcessingComplete {
                original,
                candidate,
            },
            RawIngestCheckpointPhaseV1::Terminal { receipt } => Self::Terminal { receipt },
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub(crate) struct IngestCheckpointV1 {
    pub schema: String,
    #[serde(flatten)]
    pub phase: IngestCheckpointPhaseV1,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawIngestCheckpointV1 {
    schema: String,
    #[serde(flatten)]
    phase: RawIngestCheckpointPhaseV1,
}

impl TryFrom<RawIngestCheckpointV1> for IngestCheckpointV1 {
    type Error = HeleosError;

    fn try_from(raw: RawIngestCheckpointV1) -> Result<Self> {
        let value = Self {
            schema: raw.schema,
            phase: raw.phase.into(),
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for IngestCheckpointV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawIngestCheckpointV1::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

impl IngestCheckpointV1 {
    pub(crate) fn validate(&self) -> Result<()> {
        if self.schema != INGEST_CHECKPOINT_SCHEMA_V1 {
            return Err(HeleosError::Integrity);
        }
        match &self.phase {
            IngestCheckpointPhaseV1::PreflightRejected {
                content_sha256,
                byte_length,
                quarantine,
            } => {
                quarantine.validate()?;
                if *byte_length > JCS_SAFE_INTEGER_MAX
                    || !matches!(
                        quarantine.reason,
                        IntakeQuarantineReasonV1::InputBytes { .. }
                            | IntakeQuarantineReasonV1::OriginalRetentionQuota
                            | IntakeQuarantineReasonV1::ProjectAssociationQuota
                    )
                    || matches!(
                        quarantine.reason,
                        IntakeQuarantineReasonV1::InputBytes { .. }
                    ) != content_sha256.is_none()
                    || !quarantine_observed_length_matches(&quarantine.reason, *byte_length)
                {
                    return Err(HeleosError::Integrity);
                }
            }
            IngestCheckpointPhaseV1::VaultPublished { original } => original.validate()?,
            IngestCheckpointPhaseV1::ProcessingComplete {
                original,
                candidate,
            } => {
                original.validate()?;
                candidate.validate()?;
                if let ProcessingCandidateV1::Accepted { manifest, .. } = candidate
                    && (manifest.original.sha256 != original.digest
                        || manifest.original.byte_length != original.byte_length)
                {
                    return Err(HeleosError::Integrity);
                }
            }
            IngestCheckpointPhaseV1::Terminal { receipt } => {
                receipt.validate()?;
                if receipt.outcome == IngestOutcome::IdempotentReplay {
                    return Err(HeleosError::Integrity);
                }
            }
        }
        Ok(())
    }

    pub(crate) fn validate_for_input(&self, input: &IngestInputV1) -> Result<()> {
        self.validate()?;
        let matches_original = |original: &StoredObjectV1| {
            input.content_sha256 == Some(original.digest)
                && input.byte_length == original.byte_length
        };
        match &self.phase {
            IngestCheckpointPhaseV1::PreflightRejected {
                content_sha256,
                byte_length,
                ..
            } if *content_sha256 == input.content_sha256 && *byte_length == input.byte_length => {}
            IngestCheckpointPhaseV1::VaultPublished { original } if matches_original(original) => {}
            IngestCheckpointPhaseV1::ProcessingComplete {
                original,
                candidate,
            } if matches_original(original)
                && candidate_provenance(candidate) == Some(&input.expected_probe_provenance) => {}
            IngestCheckpointPhaseV1::Terminal { receipt }
                if receipt.content_sha256 == input.content_sha256
                    && receipt.byte_length == input.byte_length
                    && receipt_provenance(receipt).is_none_or(|provenance| {
                        provenance == &input.expected_probe_provenance
                    }) => {}
            _ => return Err(HeleosError::Integrity),
        }
        Ok(())
    }
}

fn candidate_provenance(candidate: &ProcessingCandidateV1) -> Option<&PdfProbeProvenance> {
    match candidate {
        ProcessingCandidateV1::Accepted { manifest, .. } => Some(&manifest.probe_provenance),
        ProcessingCandidateV1::Quarantined { quarantine, .. } => {
            quarantine.probe_provenance.as_ref()
        }
    }
}

fn receipt_provenance(receipt: &IngestReceipt) -> Option<&PdfProbeProvenance> {
    receipt
        .evidence_manifest
        .as_ref()
        .map(|evidence| &evidence.manifest.probe_provenance)
        .or_else(|| {
            receipt
                .quarantine
                .as_ref()
                .and_then(|quarantine| quarantine.probe_provenance.as_ref())
        })
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(
    tag = "kind",
    content = "detail",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub(crate) enum IngestEventDetailV1 {
    Accepted {
        schema: String,
        attempt: u32,
        document_id: DocumentId,
        revision_id: RevisionId,
        evidence_id: EvidenceId,
    },
    Quarantined {
        schema: String,
        attempt: u32,
        content_sha256: Option<Sha256Digest>,
        byte_length: u64,
        quarantine: IntakeQuarantineV1,
    },
    Interrupted {
        schema: String,
        attempt: u32,
        checkpoint_sha256: Sha256Digest,
    },
    Replay {
        schema: String,
        authoritative_job_id: JobId,
        authoritative_attempt: u32,
        authoritative_event_id: IngestEventId,
    },
    Conflict {
        schema: String,
        authoritative_job_id: JobId,
        #[serde(deserialize_with = "deserialize_conflict_fields")]
        mismatching_fields: Vec<String>,
    },
}

#[derive(Deserialize)]
#[serde(
    tag = "kind",
    content = "detail",
    rename_all = "snake_case",
    deny_unknown_fields
)]
enum RawIngestEventDetailV1 {
    Accepted {
        schema: String,
        attempt: u32,
        document_id: DocumentId,
        revision_id: RevisionId,
        evidence_id: EvidenceId,
    },
    Quarantined {
        schema: String,
        attempt: u32,
        content_sha256: Option<Sha256Digest>,
        byte_length: u64,
        quarantine: IntakeQuarantineV1,
    },
    Interrupted {
        schema: String,
        attempt: u32,
        checkpoint_sha256: Sha256Digest,
    },
    Replay {
        schema: String,
        authoritative_job_id: JobId,
        authoritative_attempt: u32,
        authoritative_event_id: IngestEventId,
    },
    Conflict {
        schema: String,
        authoritative_job_id: JobId,
        #[serde(deserialize_with = "deserialize_conflict_fields")]
        mismatching_fields: Vec<String>,
    },
}

impl From<RawIngestEventDetailV1> for IngestEventDetailV1 {
    fn from(raw: RawIngestEventDetailV1) -> Self {
        match raw {
            RawIngestEventDetailV1::Accepted {
                schema,
                attempt,
                document_id,
                revision_id,
                evidence_id,
            } => Self::Accepted {
                schema,
                attempt,
                document_id,
                revision_id,
                evidence_id,
            },
            RawIngestEventDetailV1::Quarantined {
                schema,
                attempt,
                content_sha256,
                byte_length,
                quarantine,
            } => Self::Quarantined {
                schema,
                attempt,
                content_sha256,
                byte_length,
                quarantine,
            },
            RawIngestEventDetailV1::Interrupted {
                schema,
                attempt,
                checkpoint_sha256,
            } => Self::Interrupted {
                schema,
                attempt,
                checkpoint_sha256,
            },
            RawIngestEventDetailV1::Replay {
                schema,
                authoritative_job_id,
                authoritative_attempt,
                authoritative_event_id,
            } => Self::Replay {
                schema,
                authoritative_job_id,
                authoritative_attempt,
                authoritative_event_id,
            },
            RawIngestEventDetailV1::Conflict {
                schema,
                authoritative_job_id,
                mismatching_fields,
            } => Self::Conflict {
                schema,
                authoritative_job_id,
                mismatching_fields,
            },
        }
    }
}

impl<'de> Deserialize<'de> for IngestEventDetailV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let value = Self::from(RawIngestEventDetailV1::deserialize(deserializer)?);
        value.validate().map_err(de::Error::custom)?;
        Ok(value)
    }
}

impl IngestEventDetailV1 {
    pub(crate) fn validate(&self) -> Result<()> {
        let valid_attempt = |attempt: u32| (1..=MAX_JOB_ATTEMPTS).contains(&attempt);
        match self {
            Self::Accepted {
                schema,
                attempt,
                document_id,
                revision_id,
                ..
            } if schema == INGEST_EVENT_DETAILS_SCHEMA_V1
                && valid_attempt(*attempt)
                && document_id.as_digest() == revision_id.as_digest() => {}
            Self::Quarantined {
                schema,
                attempt,
                content_sha256,
                byte_length,
                quarantine,
            } => {
                quarantine.validate()?;
                let is_input_bytes = matches!(
                    quarantine.reason,
                    IntakeQuarantineReasonV1::InputBytes { .. }
                );
                if schema != INGEST_EVENT_DETAILS_SCHEMA_V1
                    || !valid_attempt(*attempt)
                    || *byte_length > JCS_SAFE_INTEGER_MAX
                    || is_input_bytes != content_sha256.is_none()
                    || !quarantine_observed_length_matches(&quarantine.reason, *byte_length)
                {
                    return Err(HeleosError::Integrity);
                }
            }
            Self::Interrupted {
                schema, attempt, ..
            } if schema == INGEST_EVENT_DETAILS_SCHEMA_V1 && valid_attempt(*attempt) => {}
            Self::Replay {
                schema,
                authoritative_attempt,
                ..
            } if schema == INGEST_EVENT_DETAILS_SCHEMA_V1
                && valid_attempt(*authoritative_attempt) => {}
            Self::Conflict {
                schema,
                mismatching_fields,
                ..
            } if schema == INGEST_EVENT_DETAILS_SCHEMA_V1
                && !mismatching_fields.is_empty()
                && mismatching_fields.len() <= FROZEN_MISMATCH_FIELDS.len()
                && mismatching_fields.windows(2).all(|pair| pair[0] < pair[1])
                && mismatching_fields
                    .iter()
                    .all(|field| FROZEN_MISMATCH_FIELDS.contains(&field.as_str())) => {}
            _ => return Err(HeleosError::Integrity),
        }
        Ok(())
    }
}

const FROZEN_MISMATCH_FIELDS: [&str; 6] = [
    "budget",
    "byte_length",
    "content_sha256",
    "deadline_profile_ms",
    "expected_probe_provenance",
    "requested_limits",
];

fn deserialize_conflict_fields<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Vec<String>, D::Error> {
    super::deserialize_bounded_vec::<D, String, 6>(deserializer)
}

pub(crate) fn json_text<T: Serialize>(value: &T, maximum: usize) -> Result<String> {
    let bytes = super::evidence::bounded_canonical_json(value, maximum)?;
    String::from_utf8(bytes).map_err(|_| HeleosError::Integrity)
}

struct QuarantineCompletion<'a> {
    job_id: JobId,
    attempt: u32,
    write_checkpoint: bool,
    input: &'a IngestInputV1,
    actor: ActorId,
    retained: Option<StoredObjectV1>,
    quarantine: IntakeQuarantineV1,
    outcome: IngestOutcome,
    preexisting_vault_digests: Vec<Sha256Digest>,
}

struct AcceptedCompletion<'a> {
    job_id: JobId,
    attempt: u32,
    write_checkpoint: bool,
    input: &'a IngestInputV1,
    actor: ActorId,
    original: StoredObjectV1,
    manifest_object: StoredObjectV1,
    manifest: EvidenceManifestV1,
    preexisting_vault_digests: Vec<Sha256Digest>,
}

struct AdmittedResume<'a> {
    job_id: JobId,
    attempt: u32,
    input: &'a IngestInputV1,
    actor: ActorId,
    original: StoredObjectV1,
    quota: crate::store::ingest_repository::QuotaSnapshot,
    existing_candidate: Option<ProcessingCandidateV1>,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum AccountedPublicationTestPoint {
    Original,
    Manifest,
}

pub struct IngestEngine<'a> {
    store: &'a mut crate::Store,
    vault: &'a crate::Vault,
    probe: &'a dyn crate::PdfProbe,
    clock: &'a dyn crate::Clock,
    ids: &'a dyn crate::IdGenerator,
    faults: &'a dyn super::FaultInjector,
    probe_provenance: PdfProbeProvenance,
    #[cfg(test)]
    before_accounted_publication:
        Option<&'a dyn Fn(AccountedPublicationTestPoint, Sha256Digest) -> Result<()>>,
}

impl<'a> IngestEngine<'a> {
    pub fn new(
        store: &'a mut crate::Store,
        vault: &'a crate::Vault,
        probe: &'a dyn crate::PdfProbe,
        clock: &'a dyn crate::Clock,
        ids: &'a dyn crate::IdGenerator,
    ) -> Result<Self> {
        Self::with_fault_injector(store, vault, probe, clock, ids, &super::fault::NO_FAULTS)
    }

    pub fn with_fault_injector(
        store: &'a mut crate::Store,
        vault: &'a crate::Vault,
        probe: &'a dyn crate::PdfProbe,
        clock: &'a dyn crate::Clock,
        ids: &'a dyn crate::IdGenerator,
        faults: &'a dyn super::FaultInjector,
    ) -> Result<Self> {
        store.require_writer_capability()?;
        let probe_provenance = probe.provenance()?;
        validate_provenance(&probe_provenance)?;
        Ok(Self {
            store,
            vault,
            probe,
            clock,
            ids,
            faults,
            probe_provenance,
            #[cfg(test)]
            before_accounted_publication: None,
        })
    }

    #[cfg(test)]
    fn observe_before_accounted_publication(
        &self,
        point: AccountedPublicationTestPoint,
        digest: Sha256Digest,
    ) -> Result<()> {
        if let Some(observer) = self.before_accounted_publication {
            observer(point, digest)?;
        }
        Ok(())
    }

    pub fn ingest(&mut self, mut request: IngestRequest) -> Result<IngestReceipt> {
        use crate::store::ingest_repository::{
            ConflictAttemptCommand, IdempotencyLookup, ReplayAttemptCommand,
        };

        let idempotency = match self
            .store
            .idempotency_lookup(request.project_id, &request.idempotency_key)?
        {
            IdempotencyLookup::MissingProject => return Err(HeleosError::NotFound),
            lookup => lookup,
        };
        if request.pdf_limits != PdfLimits::default() {
            return Err(HeleosError::PolicyDenied);
        }
        let fingerprint = request
            .source
            .fingerprint(PdfLimits::default().max_input_bytes)?;
        let input = IngestInputV1 {
            schema: INGEST_INPUT_SCHEMA_V1.to_owned(),
            project_id: request.project_id,
            kind: INGEST_KIND.to_owned(),
            idempotency_key: request.idempotency_key.clone(),
            content_sha256: fingerprint.digest,
            byte_length: fingerprint.byte_length,
            source_display: fingerprint.display_name.clone(),
            requested_limits: PdfLimits::default().into(),
            expected_probe_provenance: self.probe_provenance.clone(),
            deadline_profile_ms: JOB_DEADLINE_MS as u64,
        };
        input.validate()?;
        let budget = IngestBudgetV1::new(fingerprint.byte_length);
        match idempotency {
            IdempotencyLookup::Existing(record) => {
                if !record.input.frozen_eq(&input) || record.budget != budget {
                    let now_ms = checked_jcs_time(self.clock.now_unix_ms())?;
                    request.source.verify_unchanged(&fingerprint)?;
                    self.store.conflict_attempt_commit(ConflictAttemptCommand {
                        authoritative_job_id: record.job_id,
                        project_id: request.project_id,
                        submitted_input: input,
                        submitted_budget: budget,
                        actor: request.actor,
                        source_display: fingerprint.display_name,
                        ingest_event_id: IngestEventId::from_uuid(self.ids.next_uuid()),
                        source_record_id: self.ids.next_uuid().hyphenated().to_string(),
                        now_ms,
                        audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
                    })?;
                    return Err(HeleosError::IdempotencyConflict);
                }
                if record.state != crate::JobState::Succeeded {
                    return Err(HeleosError::InvalidStateTransition);
                }
                let IngestCheckpointPhaseV1::Terminal { receipt } = &record.checkpoint.phase else {
                    return Err(HeleosError::Integrity);
                };
                let preexisting_vault_digests =
                    verify_receipt_objects(self.store, self.vault, request.project_id, receipt)?;
                let now_ms = checked_jcs_time(self.clock.now_unix_ms())?;
                request.source.verify_unchanged(&fingerprint)?;
                return self.store.replay_attempt_commit(ReplayAttemptCommand {
                    authoritative_job_id: record.job_id,
                    project_id: request.project_id,
                    idempotency_key: request.idempotency_key,
                    actor: request.actor,
                    source_display: Some(fingerprint.display_name),
                    ingest_event_id: Some(IngestEventId::from_uuid(self.ids.next_uuid())),
                    source_record_id: Some(self.ids.next_uuid().hyphenated().to_string()),
                    preexisting_vault_digests,
                    now_ms,
                    audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
                });
            }
            IdempotencyLookup::Vacant => {}
            IdempotencyLookup::MissingProject => unreachable!("missing project handled above"),
        }

        let Some(expected_digest) = fingerprint.digest else {
            let quarantine = IntakeQuarantineV1 {
                schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                reason: IntakeQuarantineReasonV1::InputBytes {
                    limit_bytes: PdfLimits::default().max_input_bytes,
                    observed_bytes: fingerprint.byte_length,
                },
                probe_provenance: None,
            };
            quarantine.validate()?;
            request.source.verify_unchanged(&fingerprint)?;
            return self.complete_preflight_quarantine(
                input,
                budget,
                request.actor,
                quarantine,
                Vec::new(),
            );
        };
        let quota = self
            .store
            .quota_snapshot(request.project_id, Some(expected_digest))?;
        if let Some(admission) = quota.admission.clone() {
            if admission.digest != expected_digest
                || admission.byte_length != fingerprint.byte_length
                || admission.media_type != super::evidence::PDF_MEDIA_TYPE
                || admission.vault_key != crate::Vault::object_key(expected_digest)
            {
                return Err(HeleosError::Integrity);
            }
            let original = StoredObjectV1 {
                digest: admission.digest,
                byte_length: admission.byte_length,
                vault_key: admission.vault_key,
            };
            drop(open_checkpoint_object(self.vault, &original)?);
            match admission.admission_state.as_str() {
                "quarantined" => {
                    let quarantine = admission.quarantine.ok_or(HeleosError::Integrity)?;
                    if !matches!(
                        quarantine.reason,
                        IntakeQuarantineReasonV1::Pdf(_)
                            | IntakeQuarantineReasonV1::EvidenceManifestQuota
                    ) {
                        return Err(HeleosError::Integrity);
                    }
                    if quarantine.probe_provenance.as_ref()
                        != Some(&input.expected_probe_provenance)
                    {
                        return Err(HeleosError::Integrity);
                    }
                    if (!quota.project_overall_accounted
                        && !prospective_fits(
                            quota.project_overall_bytes,
                            original.byte_length,
                            PROJECT_OVERALL_QUOTA_BYTES,
                        )?)
                        || (!quota.project_quarantine_accounted
                            && !prospective_fits(
                                quota.project_quarantine_bytes,
                                original.byte_length,
                                PROJECT_QUARANTINE_QUOTA_BYTES,
                            )?)
                    {
                        request.source.verify_unchanged(&fingerprint)?;
                        return self.complete_preflight_quarantine(
                            input,
                            budget,
                            request.actor,
                            IntakeQuarantineV1 {
                                schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                                reason: IntakeQuarantineReasonV1::ProjectAssociationQuota,
                                probe_provenance: None,
                            },
                            vec![original.digest],
                        );
                    }
                    request.source.verify_unchanged(&fingerprint)?;
                    let (job_id, attempt) = self.create_started_job(
                        input.clone(),
                        budget,
                        IngestCheckpointV1 {
                            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                            phase: IngestCheckpointPhaseV1::VaultPublished {
                                original: original.clone(),
                            },
                        },
                        &request.actor,
                        true,
                    )?;
                    let failure_actor = request.actor.clone();
                    let fresh_quota = self
                        .store
                        .quota_snapshot(input.project_id, Some(original.digest))?;
                    let result = self.resume_admitted_original(AdmittedResume {
                        job_id,
                        attempt,
                        input: &input,
                        actor: request.actor,
                        original,
                        quota: fresh_quota,
                        existing_candidate: None,
                    });
                    return self.resolve_running_result(
                        job_id,
                        input.project_id,
                        attempt,
                        failure_actor,
                        result,
                    );
                }
                "accepted" => {
                    if admission.quarantine.is_some() {
                        return Err(HeleosError::Integrity);
                    }
                    let (_, revision_id) = crate::canonical_document_ids(original.digest);
                    let evidence = super::FoundationReader::new(self.store, self.vault)
                        .evidence_manifest_for_revision(revision_id)
                        .map_err(|error| match error {
                            HeleosError::NotFound => HeleosError::Integrity,
                            other => other,
                        })?;
                    if evidence.manifest.original.sha256 != original.digest
                        || evidence.manifest.original.byte_length != original.byte_length
                        || evidence.original_vault_key != original.vault_key
                        || evidence.manifest.probe_provenance != input.expected_probe_provenance
                    {
                        return Err(HeleosError::Integrity);
                    }
                    let manifest_object = StoredObjectV1 {
                        digest: evidence.manifest_content_sha256,
                        byte_length: evidence.manifest_byte_length,
                        vault_key: evidence.manifest_vault_key.clone(),
                    };
                    let manifest_quota = self
                        .store
                        .quota_snapshot(request.project_id, Some(manifest_object.digest))?;
                    let has_project_lineage = evidence
                        .lineages
                        .iter()
                        .any(|lineage| lineage.project_id == request.project_id);
                    let original_debit = if quota.project_overall_accounted {
                        0
                    } else {
                        original.byte_length
                    };
                    let manifest_overall_debit =
                        if manifest_quota.project_overall_accounted || has_project_lineage {
                            0
                        } else {
                            manifest_object.byte_length
                        };
                    let manifest_evidence_debit =
                        if manifest_quota.project_evidence_accounted || has_project_lineage {
                            0
                        } else {
                            manifest_object.byte_length
                        };
                    let overall_debit = original_debit
                        .checked_add(manifest_overall_debit)
                        .ok_or(HeleosError::Integrity)?;
                    if !prospective_fits(
                        quota.project_overall_bytes,
                        overall_debit,
                        PROJECT_OVERALL_QUOTA_BYTES,
                    )? || !prospective_fits(
                        manifest_quota.project_evidence_bytes,
                        manifest_evidence_debit,
                        PROJECT_EVIDENCE_QUOTA_BYTES,
                    )? {
                        request.source.verify_unchanged(&fingerprint)?;
                        return self.complete_preflight_quarantine(
                            input,
                            budget,
                            request.actor,
                            IntakeQuarantineV1 {
                                schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                                reason: IntakeQuarantineReasonV1::ProjectAssociationQuota,
                                probe_provenance: None,
                            },
                            vec![original.digest],
                        );
                    }
                    request.source.verify_unchanged(&fingerprint)?;
                    let (job_id, attempt) = self.create_started_job(
                        input.clone(),
                        budget,
                        IngestCheckpointV1 {
                            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                            phase: IngestCheckpointPhaseV1::VaultPublished {
                                original: original.clone(),
                            },
                        },
                        &request.actor,
                        true,
                    )?;
                    let failure_actor = request.actor.clone();
                    let fresh_quota = self
                        .store
                        .quota_snapshot(input.project_id, Some(original.digest))?;
                    let result = self.resume_admitted_original(AdmittedResume {
                        job_id,
                        attempt,
                        input: &input,
                        actor: request.actor,
                        original,
                        quota: fresh_quota,
                        existing_candidate: None,
                    });
                    return self.resolve_running_result(
                        job_id,
                        input.project_id,
                        attempt,
                        failure_actor,
                        result,
                    );
                }
                _ => return Err(HeleosError::Integrity),
            }
        }
        let orphan_preexisting = match self.vault.open_verified(expected_digest) {
            Ok(verified) => {
                if verified.byte_length() != fingerprint.byte_length
                    || verified.vault_key() != crate::Vault::object_key(expected_digest)
                {
                    return Err(HeleosError::Integrity);
                }
                true
            }
            Err(HeleosError::NotFound) if !quota.store_overall_accounted => false,
            Err(HeleosError::NotFound) => return Err(HeleosError::Integrity),
            Err(error) => return Err(error),
        };
        let project_overall_debit = if quota.project_overall_accounted {
            0
        } else {
            fingerprint.byte_length
        };
        let project_quarantine_debit = if quota.project_quarantine_accounted {
            0
        } else {
            fingerprint.byte_length
        };
        let project_capacity = prospective_fits(
            quota.project_overall_bytes,
            project_overall_debit,
            PROJECT_OVERALL_QUOTA_BYTES,
        )? && prospective_fits(
            quota.project_quarantine_bytes,
            project_quarantine_debit,
            PROJECT_QUARANTINE_QUOTA_BYTES,
        )?;
        if !project_capacity {
            let (reason, preexisting) = if orphan_preexisting {
                (
                    IntakeQuarantineReasonV1::ProjectAssociationQuota,
                    vec![expected_digest],
                )
            } else {
                (IntakeQuarantineReasonV1::OriginalRetentionQuota, Vec::new())
            };
            request.source.verify_unchanged(&fingerprint)?;
            return self.complete_preflight_quarantine(
                input,
                budget,
                request.actor,
                IntakeQuarantineV1 {
                    schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                    reason,
                    probe_provenance: None,
                },
                preexisting,
            );
        }
        let current_is_orphan = orphan_preexisting && !quota.store_overall_accounted;
        let original_quarantine_store_debit =
            if quota.store_quarantine_accounted || current_is_orphan {
                0
            } else {
                fingerprint.byte_length
            };
        let store_capacity = prospective_fits(
            quota.store_quarantine_bytes,
            original_quarantine_store_debit,
            STORE_QUARANTINE_QUOTA_BYTES,
        )?;
        if !store_capacity {
            let (reason, preexisting) = if orphan_preexisting {
                (
                    IntakeQuarantineReasonV1::ProjectAssociationQuota,
                    vec![expected_digest],
                )
            } else {
                (IntakeQuarantineReasonV1::OriginalRetentionQuota, Vec::new())
            };
            request.source.verify_unchanged(&fingerprint)?;
            return self.complete_preflight_quarantine(
                input,
                budget,
                request.actor,
                IntakeQuarantineV1 {
                    schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                    reason,
                    probe_provenance: None,
                },
                preexisting,
            );
        }
        let original_retain_budget = remaining_min(&[
            (PROJECT_OVERALL_QUOTA_BYTES, quota.project_overall_bytes),
            (
                PROJECT_QUARANTINE_QUOTA_BYTES,
                quota.project_quarantine_bytes,
            ),
        ])?;
        #[cfg(test)]
        self.observe_before_accounted_publication(
            AccountedPublicationTestPoint::Original,
            expected_digest,
        )?;
        let inventory = self.store.vault_inventory_rows()?;
        let orphan_budget = remaining_vault_orphan_budget(&quota)?;
        let put = request.source.publish(
            self.vault,
            crate::VaultWriteBudget::new(
                PdfLimits::default().max_input_bytes,
                original_retain_budget,
            )?,
            &inventory,
            orphan_budget,
            expected_digest,
            fingerprint.byte_length,
        )?;
        let stored_original = match put {
            crate::PutOutcome::Stored(stored) => stored,
            crate::PutOutcome::QuotaRejected {
                digest,
                byte_length,
            } => {
                if digest != expected_digest || byte_length != fingerprint.byte_length {
                    return Err(HeleosError::Integrity);
                }
                return self.complete_preflight_quarantine(
                    input,
                    budget,
                    request.actor,
                    IntakeQuarantineV1 {
                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                        reason: IntakeQuarantineReasonV1::OriginalRetentionQuota,
                        probe_provenance: None,
                    },
                    Vec::new(),
                );
            }
        };
        if orphan_preexisting && stored_original.newly_published {
            return Err(HeleosError::Integrity);
        }
        let original_preexisting = orphan_preexisting || !stored_original.newly_published;
        let original = StoredObjectV1::from(&stored_original);
        let (job_id, attempt) = self.create_started_job(
            input.clone(),
            budget,
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished {
                    original: original.clone(),
                },
            },
            &request.actor,
            true,
        )?;
        let failure_actor = request.actor.clone();
        let processing_result = (|| -> Result<IngestReceipt> {
            let verified = open_checkpoint_object(self.vault, &original)?;
            let (_, revision_id) = crate::canonical_document_ids(original.digest);
            let inspection = match self
                .probe
                .probe(verified, revision_id, PdfLimits::default())?
            {
                crate::PdfProbeOutcome::Accepted(inspection) => inspection,
                crate::PdfProbeOutcome::Quarantined(pdf_quarantine) => {
                    if pdf_quarantine.content_sha256 != original.digest
                        || pdf_quarantine.byte_length != original.byte_length
                        || pdf_quarantine.provenance != self.probe_provenance
                    {
                        return Err(HeleosError::Integrity);
                    }
                    let outcome = pdf_quarantine.ingest_outcome();
                    let quarantine = IntakeQuarantineV1 {
                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                        reason: IntakeQuarantineReasonV1::Pdf(pdf_quarantine.reason),
                        probe_provenance: Some(pdf_quarantine.provenance),
                    };
                    let preexisting = if original_preexisting {
                        vec![original.digest]
                    } else {
                        Vec::new()
                    };
                    return self.complete_quarantine(QuarantineCompletion {
                        job_id,
                        attempt,
                        write_checkpoint: true,
                        input: &input,
                        actor: request.actor,
                        retained: Some(original),
                        quarantine,
                        outcome,
                        preexisting_vault_digests: preexisting,
                    });
                }
            };
            if inspection.revision_id != revision_id
                || inspection.content_sha256 != original.digest
                || inspection.byte_length != original.byte_length
                || inspection.provenance != self.probe_provenance
            {
                return Err(HeleosError::Integrity);
            }
            let manifest = EvidenceManifestV1::new(
                original.digest,
                original.byte_length,
                inspection.pages,
                inspection.provenance,
            )?;
            let manifest_bytes = manifest.canonical_bytes()?;
            let manifest_byte_length =
                u64::try_from(manifest_bytes.len()).map_err(|_| HeleosError::Integrity)?;
            let manifest_digest = Sha256Digest::hash_reader(manifest_bytes.as_slice())?;
            let original_quota = self
                .store
                .quota_snapshot(request.project_id, Some(original.digest))?;
            let manifest_quota = self
                .store
                .quota_snapshot(request.project_id, Some(manifest_digest))?;
            if let Some(admission) = &manifest_quota.admission
                && (admission.digest != manifest_digest
                    || admission.byte_length != manifest_byte_length
                    || admission.media_type != super::evidence::EVIDENCE_MANIFEST_MEDIA_TYPE
                    || admission.admission_state != "accepted"
                    || admission.vault_key != crate::Vault::object_key(manifest_digest)
                    || admission.quarantine.is_some())
            {
                return Err(HeleosError::Integrity);
            }
            let manifest_observed_preexisting = match self.vault.open_verified(manifest_digest) {
                Ok(verified) => {
                    if verified.byte_length() != manifest_byte_length
                        || verified.vault_key() != crate::Vault::object_key(manifest_digest)
                    {
                        return Err(HeleosError::Integrity);
                    }
                    true
                }
                Err(HeleosError::NotFound)
                    if manifest_quota.admission.is_none()
                        && !manifest_quota.store_overall_accounted
                        && !manifest_quota.store_evidence_accounted =>
                {
                    false
                }
                Err(HeleosError::NotFound) => return Err(HeleosError::Integrity),
                Err(error) => return Err(error),
            };
            let original_project_debit = if original_quota.project_overall_accounted {
                0
            } else {
                original.byte_length
            };
            let manifest_project_debit = if manifest_quota.project_overall_accounted {
                0
            } else {
                manifest_byte_length
            };
            let manifest_project_evidence_debit = if manifest_quota.project_evidence_accounted {
                0
            } else {
                manifest_byte_length
            };
            let project_debit = original_project_debit
                .checked_add(manifest_project_debit)
                .ok_or(HeleosError::Integrity)?;
            let manifest_store_evidence_debit = if manifest_quota.store_evidence_accounted {
                0
            } else {
                manifest_byte_length
            };
            let manifest_fits = prospective_fits(
                original_quota.project_overall_bytes,
                project_debit,
                PROJECT_OVERALL_QUOTA_BYTES,
            )? && prospective_fits(
                manifest_quota.project_evidence_bytes,
                manifest_project_evidence_debit,
                PROJECT_EVIDENCE_QUOTA_BYTES,
            )? && prospective_fits(
                manifest_quota.store_evidence_bytes,
                manifest_store_evidence_debit,
                STORE_EVIDENCE_QUOTA_BYTES,
            )?;
            if !manifest_fits {
                return self.complete_quarantine(QuarantineCompletion {
                    job_id,
                    attempt,
                    write_checkpoint: true,
                    input: &input,
                    actor: request.actor,
                    retained: Some(original.clone()),
                    quarantine: IntakeQuarantineV1 {
                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                        reason: IntakeQuarantineReasonV1::EvidenceManifestQuota,
                        probe_provenance: Some(self.probe_provenance.clone()),
                    },
                    outcome: IngestOutcome::QuarantinedLimit,
                    preexisting_vault_digests: if original_preexisting {
                        vec![original.digest]
                    } else {
                        Vec::new()
                    },
                });
            }
            let projected_project_overall = original_quota
                .project_overall_bytes
                .checked_add(original_project_debit)
                .ok_or(HeleosError::Integrity)?;
            let manifest_retain_budget = remaining_min(&[
                (PROJECT_OVERALL_QUOTA_BYTES, projected_project_overall),
                (
                    PROJECT_EVIDENCE_QUOTA_BYTES,
                    manifest_quota.project_evidence_bytes,
                ),
                (
                    STORE_EVIDENCE_QUOTA_BYTES,
                    manifest_quota.store_evidence_bytes,
                ),
            ])?;
            #[cfg(test)]
            self.observe_before_accounted_publication(
                AccountedPublicationTestPoint::Manifest,
                manifest_digest,
            )?;
            let inventory = self.store.vault_inventory_rows()?;
            let orphan_budget = remaining_vault_orphan_budget(&manifest_quota)?;
            let manifest_put = self.vault.put_reader_accounted(
                manifest_bytes.as_slice(),
                crate::VaultWriteBudget::new(manifest_byte_length, manifest_retain_budget)?,
                &inventory,
                orphan_budget,
            )?;
            let stored_manifest = match manifest_put {
                crate::PutOutcome::Stored(stored) => stored,
                crate::PutOutcome::QuotaRejected {
                    digest,
                    byte_length,
                } => {
                    if digest != manifest_digest || byte_length != manifest_byte_length {
                        return Err(HeleosError::Integrity);
                    }
                    return self.complete_quarantine(QuarantineCompletion {
                        job_id,
                        attempt,
                        write_checkpoint: true,
                        input: &input,
                        actor: request.actor,
                        retained: Some(original.clone()),
                        quarantine: IntakeQuarantineV1 {
                            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                            reason: IntakeQuarantineReasonV1::EvidenceManifestQuota,
                            probe_provenance: Some(self.probe_provenance.clone()),
                        },
                        outcome: IngestOutcome::QuarantinedLimit,
                        preexisting_vault_digests: if original_preexisting {
                            vec![original.digest]
                        } else {
                            Vec::new()
                        },
                    });
                }
            };
            if manifest_observed_preexisting && stored_manifest.newly_published {
                return Err(HeleosError::Integrity);
            }
            let manifest_preexisting =
                manifest_observed_preexisting || !stored_manifest.newly_published;
            let manifest_object = StoredObjectV1::from(&stored_manifest);
            let mut preexisting_vault_digests = Vec::new();
            if original_preexisting {
                preexisting_vault_digests.push(stored_original.digest);
            }
            if manifest_preexisting {
                preexisting_vault_digests.push(stored_manifest.digest);
            }
            self.complete_accepted(AcceptedCompletion {
                job_id,
                attempt,
                write_checkpoint: true,
                input: &input,
                actor: request.actor,
                original,
                manifest_object,
                manifest,
                preexisting_vault_digests,
            })
        })();
        self.resolve_running_result(
            job_id,
            input.project_id,
            attempt,
            failure_actor,
            processing_result,
        )
    }

    fn create_started_job(
        &mut self,
        input: IngestInputV1,
        budget: IngestBudgetV1,
        checkpoint: IngestCheckpointV1,
        actor: &ActorId,
        fire_vault_boundary: bool,
    ) -> Result<(JobId, u32)> {
        use crate::store::ingest_repository::{JobCreateCommand, JobStartCommand};

        let created_at_ms = checked_jcs_time(self.clock.now_unix_ms())?;
        let deadline_at_ms = checked_jcs_add(created_at_ms, JOB_DEADLINE_MS)?;
        let job_id = JobId::from_uuid(self.ids.next_uuid());
        let project_id = input.project_id;
        self.store
            .job_create_with_checkpoint_and_audit(JobCreateCommand {
                job_id,
                project_id,
                input,
                budget,
                checkpoint,
                actor: actor.clone(),
                created_at_ms,
                deadline_at_ms,
                audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
            })?;
        if fire_vault_boundary {
            self.faults.inject(super::FaultPoint::AfterVaultPublish)?;
        }
        let now_ms = checked_jcs_time(self.clock.now_unix_ms())?;
        let attempt = self.store.job_start_or_resume_with_audit(JobStartCommand {
            job_id,
            project_id,
            actor: actor.clone(),
            lease_owner: self.ids.next_uuid(),
            now_ms,
            lease_expires_at_ms: checked_jcs_add(now_ms, LEASE_DURATION_MS)?,
            audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
        })?;
        self.faults.inject(super::FaultPoint::AfterJobStart)?;
        Ok((job_id, attempt))
    }

    fn resolve_running_result<T>(
        &mut self,
        job_id: JobId,
        project_id: ProjectId,
        attempt: u32,
        actor: ActorId,
        result: Result<T>,
    ) -> Result<T> {
        use crate::store::ingest_repository::JobFailCommand;

        let Err(error) = result else {
            return result;
        };
        if matches!(
            error,
            HeleosError::FaultInjected
                | HeleosError::CommitOutcomeUnknown
                | HeleosError::Database
                | HeleosError::WriterBusy
                | HeleosError::Migration
        ) {
            return Err(error);
        }
        let Ok(now_ms) = checked_jcs_time(self.clock.now_unix_ms()) else {
            return Err(error);
        };
        match self.store.job_fail_with_audit(JobFailCommand {
            job_id,
            project_id,
            attempt,
            actor,
            now_ms,
            audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
        }) {
            Ok(()) => Err(error),
            Err(HeleosError::CommitOutcomeUnknown) => Err(HeleosError::CommitOutcomeUnknown),
            Err(_) => Err(error),
        }
    }

    fn complete_preflight_quarantine(
        &mut self,
        input: IngestInputV1,
        budget: IngestBudgetV1,
        actor: ActorId,
        quarantine: IntakeQuarantineV1,
        preexisting_vault_digests: Vec<Sha256Digest>,
    ) -> Result<IngestReceipt> {
        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::PreflightRejected {
                content_sha256: input.content_sha256,
                byte_length: input.byte_length,
                quarantine: quarantine.clone(),
            },
        };
        let (job_id, attempt) =
            self.create_started_job(input.clone(), budget, checkpoint, &actor, false)?;
        let failure_actor = actor.clone();
        let result = self.complete_quarantine(QuarantineCompletion {
            job_id,
            attempt,
            write_checkpoint: false,
            input: &input,
            actor,
            retained: None,
            quarantine,
            outcome: IngestOutcome::QuarantinedLimit,
            preexisting_vault_digests,
        });
        self.resolve_running_result(job_id, input.project_id, attempt, failure_actor, result)
    }

    fn complete_quarantine(
        &mut self,
        completion: QuarantineCompletion<'_>,
    ) -> Result<IngestReceipt> {
        use crate::store::ingest_repository::{JobCheckpointCommand, QuarantinedIntakeCommand};

        let QuarantineCompletion {
            job_id,
            attempt,
            write_checkpoint,
            input,
            actor,
            retained,
            quarantine,
            outcome,
            preexisting_vault_digests,
        } = completion;

        if write_checkpoint && let Some(original) = &retained {
            let checkpoint = IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::ProcessingComplete {
                    original: original.clone(),
                    candidate: ProcessingCandidateV1::Quarantined {
                        outcome,
                        quarantine: quarantine.clone(),
                    },
                },
            };
            self.store.job_checkpoint_with_audit(JobCheckpointCommand {
                job_id,
                project_id: input.project_id,
                actor: actor.clone(),
                attempt,
                checkpoint,
                now_ms: checked_jcs_time(self.clock.now_unix_ms())?,
                audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
            })?;
            self.faults
                .inject(super::FaultPoint::AfterCheckpointCommit)?;
        }
        self.faults
            .inject(super::FaultPoint::BeforeAuthoritativeCommit)?;
        let receipt = self
            .store
            .quarantined_intake_commit(QuarantinedIntakeCommand {
                job_id,
                project_id: input.project_id,
                attempt,
                actor,
                idempotency_key: input.idempotency_key.clone(),
                source_display: input.source_display.clone(),
                content_sha256: input.content_sha256,
                byte_length: input.byte_length,
                retained,
                quarantine,
                outcome,
                ingest_event_id: IngestEventId::from_uuid(self.ids.next_uuid()),
                source_record_id: self.ids.next_uuid().hyphenated().to_string(),
                now_ms: checked_jcs_time(self.clock.now_unix_ms())?,
                preexisting_vault_digests,
                audit_event_ids: [
                    super::AuditEventId::from_uuid(self.ids.next_uuid()),
                    super::AuditEventId::from_uuid(self.ids.next_uuid()),
                ],
            })?;
        self.faults
            .inject(super::FaultPoint::AfterAuthoritativeCommit)?;
        Ok(receipt)
    }

    fn complete_accepted(&mut self, completion: AcceptedCompletion<'_>) -> Result<IngestReceipt> {
        use crate::store::ingest_repository::{AcceptedIntakeCommand, JobCheckpointCommand};

        let AcceptedCompletion {
            job_id,
            attempt,
            write_checkpoint,
            input,
            actor,
            original,
            manifest_object,
            manifest,
            mut preexisting_vault_digests,
        } = completion;

        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::ProcessingComplete {
                original: original.clone(),
                candidate: ProcessingCandidateV1::Accepted {
                    manifest: Box::new(manifest.clone()),
                    manifest_object: manifest_object.clone(),
                },
            },
        };
        if write_checkpoint {
            self.store.job_checkpoint_with_audit(JobCheckpointCommand {
                job_id,
                project_id: input.project_id,
                actor: actor.clone(),
                attempt,
                checkpoint,
                now_ms: checked_jcs_time(self.clock.now_unix_ms())?,
                audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
            })?;
            self.faults
                .inject(super::FaultPoint::AfterCheckpointCommit)?;
        }
        self.faults
            .inject(super::FaultPoint::BeforeAuthoritativeCommit)?;
        preexisting_vault_digests.sort_unstable();
        preexisting_vault_digests.dedup();
        let receipt = self.store.accepted_intake_commit(AcceptedIntakeCommand {
            job_id,
            project_id: input.project_id,
            attempt,
            actor,
            idempotency_key: input.idempotency_key.clone(),
            source_display: input.source_display.clone(),
            original,
            manifest_object,
            manifest: manifest.clone(),
            pages: manifest.pages.clone(),
            ingest_event_id: IngestEventId::from_uuid(self.ids.next_uuid()),
            source_record_id: self.ids.next_uuid().hyphenated().to_string(),
            evidence_id: EvidenceId::from_uuid(self.ids.next_uuid()),
            now_ms: checked_jcs_time(self.clock.now_unix_ms())?,
            preexisting_vault_digests,
            audit_event_ids: [
                super::AuditEventId::from_uuid(self.ids.next_uuid()),
                super::AuditEventId::from_uuid(self.ids.next_uuid()),
                super::AuditEventId::from_uuid(self.ids.next_uuid()),
            ],
        })?;
        self.faults
            .inject(super::FaultPoint::AfterAuthoritativeCommit)?;
        Ok(receipt)
    }

    pub fn resume(&mut self, job_id: JobId, actor: ActorId) -> Result<ResumeReceipt> {
        use crate::store::ingest_repository::{
            JobRecoverCommand, JobRecoveryOutcome, ReplayAttemptCommand,
        };

        let audit = self.verify_audit_chain()?;
        if !audit.valid {
            return Err(HeleosError::Integrity);
        }
        let now_ms = checked_jcs_time(self.clock.now_unix_ms())?;
        let recovery = self
            .store
            .job_recover_expired_attempt_with_audit(JobRecoverCommand {
                job_id,
                actor: actor.clone(),
                lease_owner: self.ids.next_uuid(),
                now_ms,
                expected_probe_provenance: self.probe_provenance.clone(),
                interrupted_event_id: IngestEventId::from_uuid(self.ids.next_uuid()),
                audit_event_ids: [
                    super::AuditEventId::from_uuid(self.ids.next_uuid()),
                    super::AuditEventId::from_uuid(self.ids.next_uuid()),
                ],
            })?;
        match recovery {
            JobRecoveryOutcome::DeadlineExpired => Err(HeleosError::Timeout),
            JobRecoveryOutcome::AttemptLimit => Err(HeleosError::ResourceLimit),
            JobRecoveryOutcome::Terminal(record) => {
                let IngestCheckpointPhaseV1::Terminal { receipt } = &record.checkpoint.phase else {
                    return Err(HeleosError::Integrity);
                };
                verify_receipt_objects(self.store, self.vault, record.input.project_id, receipt)?;
                let receipt = self.store.replay_attempt_commit(ReplayAttemptCommand {
                    authoritative_job_id: record.job_id,
                    project_id: record.input.project_id,
                    idempotency_key: record.input.idempotency_key,
                    actor,
                    source_display: None,
                    ingest_event_id: None,
                    source_record_id: None,
                    preexisting_vault_digests: Vec::new(),
                    now_ms,
                    audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
                })?;
                Ok(ResumeReceipt {
                    job_id,
                    resumed_attempt: None,
                    interrupted_event_id: None,
                    receipt,
                })
            }
            JobRecoveryOutcome::Started {
                record,
                interrupted_event_id,
            } => {
                let resumed_attempt = record.attempt;
                let project_id = record.input.project_id;
                let failure_actor = actor.clone();
                let result = self
                    .faults
                    .inject(super::FaultPoint::AfterJobStart)
                    .and_then(|()| self.resume_running_record(record, actor));
                let receipt = self.resolve_running_result(
                    job_id,
                    project_id,
                    resumed_attempt,
                    failure_actor,
                    result,
                )?;
                Ok(ResumeReceipt {
                    job_id,
                    resumed_attempt: Some(resumed_attempt),
                    interrupted_event_id,
                    receipt,
                })
            }
        }
    }

    pub fn verify_audit_chain(&self) -> Result<super::AuditChainReport> {
        super::FoundationReader::new(self.store, self.vault).verify_audit_chain()
    }

    pub fn evidence_manifest_for_revision(
        &self,
        revision: RevisionId,
    ) -> Result<EvidenceManifestReceipt> {
        super::FoundationReader::new(self.store, self.vault)
            .evidence_manifest_for_revision(revision)
    }

    pub fn inspect_foundation(&self, project: ProjectId) -> Result<super::FoundationInspection> {
        super::FoundationReader::new(self.store, self.vault).inspect_foundation(project)
    }

    fn resume_running_record(
        &mut self,
        record: crate::store::ingest_repository::JobRecord,
        actor: ActorId,
    ) -> Result<IngestReceipt> {
        let job_id = record.job_id;
        let attempt = record.attempt;
        let input = record.input;
        match record.checkpoint.phase {
            IngestCheckpointPhaseV1::PreflightRejected {
                content_sha256,
                byte_length,
                quarantine,
            } => {
                if content_sha256 != input.content_sha256 || byte_length != input.byte_length {
                    return Err(HeleosError::Integrity);
                }
                let preexisting = match quarantine.reason {
                    IntakeQuarantineReasonV1::ProjectAssociationQuota => {
                        let digest = content_sha256.ok_or(HeleosError::Integrity)?;
                        drop(open_checkpoint_object(
                            self.vault,
                            &StoredObjectV1 {
                                digest,
                                byte_length,
                                vault_key: crate::Vault::object_key(digest),
                            },
                        )?);
                        vec![digest]
                    }
                    IntakeQuarantineReasonV1::InputBytes { .. }
                    | IntakeQuarantineReasonV1::OriginalRetentionQuota => Vec::new(),
                    _ => return Err(HeleosError::Integrity),
                };
                self.complete_quarantine(QuarantineCompletion {
                    job_id,
                    attempt,
                    write_checkpoint: false,
                    input: &input,
                    actor,
                    retained: None,
                    quarantine,
                    outcome: IngestOutcome::QuarantinedLimit,
                    preexisting_vault_digests: preexisting,
                })
            }
            IngestCheckpointPhaseV1::VaultPublished { original } => {
                self.resume_vault_published(job_id, attempt, &input, actor, original)
            }
            IngestCheckpointPhaseV1::ProcessingComplete {
                original,
                candidate,
            } => {
                drop(open_checkpoint_object(self.vault, &original)?);
                let quota = self
                    .store
                    .quota_snapshot(input.project_id, Some(original.digest))?;
                if quota.admission.is_some() {
                    return self.resume_admitted_original(AdmittedResume {
                        job_id,
                        attempt,
                        input: &input,
                        actor,
                        original,
                        quota,
                        existing_candidate: Some(candidate),
                    });
                }
                match candidate {
                    ProcessingCandidateV1::Accepted {
                        manifest,
                        manifest_object,
                    } => {
                        if manifest.original.sha256 != original.digest
                            || manifest.original.byte_length != original.byte_length
                            || manifest.probe_provenance != input.expected_probe_provenance
                            || Sha256Digest::hash_reader(manifest.canonical_bytes()?.as_slice())?
                                != manifest_object.digest
                        {
                            return Err(HeleosError::Integrity);
                        }
                        drop(open_checkpoint_object(self.vault, &manifest_object)?);
                        self.complete_accepted(AcceptedCompletion {
                            job_id,
                            attempt,
                            write_checkpoint: false,
                            input: &input,
                            actor,
                            original: original.clone(),
                            manifest_object: manifest_object.clone(),
                            manifest: *manifest,
                            preexisting_vault_digests: vec![
                                original.digest,
                                manifest_object.digest,
                            ],
                        })
                    }
                    ProcessingCandidateV1::Quarantined {
                        outcome,
                        quarantine,
                    } => {
                        if quarantine.probe_provenance.as_ref()
                            != Some(&input.expected_probe_provenance)
                            || outcome != quarantine_ingest_outcome(&quarantine.reason)
                        {
                            return Err(HeleosError::Integrity);
                        }
                        self.complete_quarantine(QuarantineCompletion {
                            job_id,
                            attempt,
                            write_checkpoint: false,
                            input: &input,
                            actor,
                            retained: Some(original.clone()),
                            quarantine,
                            outcome,
                            preexisting_vault_digests: vec![original.digest],
                        })
                    }
                }
            }
            IngestCheckpointPhaseV1::Terminal { .. } => Err(HeleosError::Integrity),
        }
    }

    fn resume_admitted_original(&mut self, resume: AdmittedResume<'_>) -> Result<IngestReceipt> {
        let AdmittedResume {
            job_id,
            attempt,
            input,
            actor,
            original,
            quota,
            existing_candidate,
        } = resume;
        let admission = quota.admission.ok_or(HeleosError::Integrity)?;
        if admission.digest != original.digest
            || admission.byte_length != original.byte_length
            || admission.media_type != super::evidence::PDF_MEDIA_TYPE
            || admission.vault_key != original.vault_key
        {
            return Err(HeleosError::Integrity);
        }
        match admission.admission_state.as_str() {
            "quarantined" => {
                let quarantine = admission.quarantine.ok_or(HeleosError::Integrity)?;
                if !matches!(
                    quarantine.reason,
                    IntakeQuarantineReasonV1::Pdf(_)
                        | IntakeQuarantineReasonV1::EvidenceManifestQuota
                ) || quarantine.probe_provenance.as_ref()
                    != Some(&input.expected_probe_provenance)
                {
                    return Err(HeleosError::Integrity);
                }
                let overall_debit = if quota.project_overall_accounted {
                    0
                } else {
                    original.byte_length
                };
                let quarantine_debit = if quota.project_quarantine_accounted {
                    0
                } else {
                    original.byte_length
                };
                if !prospective_fits(
                    quota.project_overall_bytes,
                    overall_debit,
                    PROJECT_OVERALL_QUOTA_BYTES,
                )? || !prospective_fits(
                    quota.project_quarantine_bytes,
                    quarantine_debit,
                    PROJECT_QUARANTINE_QUOTA_BYTES,
                )? {
                    return self.complete_running_project_association_quota(
                        job_id,
                        attempt,
                        input,
                        actor,
                        original.digest,
                    );
                }
                let outcome = quarantine_ingest_outcome(&quarantine.reason);
                let desired_candidate = ProcessingCandidateV1::Quarantined {
                    outcome,
                    quarantine: quarantine.clone(),
                };
                self.complete_quarantine(QuarantineCompletion {
                    job_id,
                    attempt,
                    write_checkpoint: existing_candidate.as_ref() != Some(&desired_candidate),
                    input,
                    actor,
                    retained: Some(original.clone()),
                    quarantine,
                    outcome,
                    preexisting_vault_digests: vec![original.digest],
                })
            }
            "accepted" => {
                if admission.quarantine.is_some() {
                    return Err(HeleosError::Integrity);
                }
                let (_, revision_id) = crate::canonical_document_ids(original.digest);
                let evidence = super::FoundationReader::new(self.store, self.vault)
                    .evidence_manifest_for_revision(revision_id)
                    .map_err(|error| match error {
                        HeleosError::NotFound => HeleosError::Integrity,
                        other => other,
                    })?;
                if evidence.manifest.original.sha256 != original.digest
                    || evidence.manifest.original.byte_length != original.byte_length
                    || evidence.original_vault_key != original.vault_key
                    || evidence.manifest.probe_provenance != input.expected_probe_provenance
                {
                    return Err(HeleosError::Integrity);
                }
                let manifest_object = StoredObjectV1 {
                    digest: evidence.manifest_content_sha256,
                    byte_length: evidence.manifest_byte_length,
                    vault_key: evidence.manifest_vault_key.clone(),
                };
                let manifest_quota = self
                    .store
                    .quota_snapshot(input.project_id, Some(manifest_object.digest))?;
                let original_debit = if quota.project_overall_accounted {
                    0
                } else {
                    original.byte_length
                };
                let manifest_overall_debit = if manifest_quota.project_overall_accounted {
                    0
                } else {
                    manifest_object.byte_length
                };
                let manifest_evidence_debit = if manifest_quota.project_evidence_accounted {
                    0
                } else {
                    manifest_object.byte_length
                };
                let overall_debit = original_debit
                    .checked_add(manifest_overall_debit)
                    .ok_or(HeleosError::Integrity)?;
                if !prospective_fits(
                    quota.project_overall_bytes,
                    overall_debit,
                    PROJECT_OVERALL_QUOTA_BYTES,
                )? || !prospective_fits(
                    manifest_quota.project_evidence_bytes,
                    manifest_evidence_debit,
                    PROJECT_EVIDENCE_QUOTA_BYTES,
                )? {
                    return self.complete_running_project_association_quota(
                        job_id,
                        attempt,
                        input,
                        actor,
                        original.digest,
                    );
                }
                let desired_candidate = ProcessingCandidateV1::Accepted {
                    manifest: Box::new(evidence.manifest.clone()),
                    manifest_object: manifest_object.clone(),
                };
                self.complete_accepted(AcceptedCompletion {
                    job_id,
                    attempt,
                    write_checkpoint: existing_candidate.as_ref() != Some(&desired_candidate),
                    input,
                    actor,
                    original: original.clone(),
                    manifest_object: manifest_object.clone(),
                    manifest: evidence.manifest,
                    preexisting_vault_digests: vec![original.digest, manifest_object.digest],
                })
            }
            _ => Err(HeleosError::Integrity),
        }
    }

    fn complete_running_project_association_quota(
        &mut self,
        job_id: JobId,
        attempt: u32,
        input: &IngestInputV1,
        actor: ActorId,
        digest: Sha256Digest,
    ) -> Result<IngestReceipt> {
        use crate::store::ingest_repository::JobCheckpointCommand;

        let quarantine = IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason: IntakeQuarantineReasonV1::ProjectAssociationQuota,
            probe_provenance: None,
        };
        self.store.job_checkpoint_with_audit(JobCheckpointCommand {
            job_id,
            project_id: input.project_id,
            actor: actor.clone(),
            attempt,
            checkpoint: IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::PreflightRejected {
                    content_sha256: Some(digest),
                    byte_length: input.byte_length,
                    quarantine: quarantine.clone(),
                },
            },
            now_ms: checked_jcs_time(self.clock.now_unix_ms())?,
            audit_event_id: super::AuditEventId::from_uuid(self.ids.next_uuid()),
        })?;
        self.faults
            .inject(super::FaultPoint::AfterCheckpointCommit)?;
        self.complete_quarantine(QuarantineCompletion {
            job_id,
            attempt,
            write_checkpoint: false,
            input,
            actor,
            retained: None,
            quarantine,
            outcome: IngestOutcome::QuarantinedLimit,
            preexisting_vault_digests: vec![digest],
        })
    }

    fn resume_vault_published(
        &mut self,
        job_id: JobId,
        attempt: u32,
        input: &IngestInputV1,
        actor: ActorId,
        original: StoredObjectV1,
    ) -> Result<IngestReceipt> {
        let verified = open_checkpoint_object(self.vault, &original)?;
        let original_quota = self
            .store
            .quota_snapshot(input.project_id, Some(original.digest))?;
        if original_quota.admission.is_some() {
            drop(verified);
            return self.resume_admitted_original(AdmittedResume {
                job_id,
                attempt,
                input,
                actor,
                original,
                quota: original_quota,
                existing_candidate: None,
            });
        }
        let (_, revision_id) = crate::canonical_document_ids(original.digest);
        let inspection = match self
            .probe
            .probe(verified, revision_id, PdfLimits::default())?
        {
            crate::PdfProbeOutcome::Accepted(inspection) => inspection,
            crate::PdfProbeOutcome::Quarantined(pdf_quarantine) => {
                if pdf_quarantine.content_sha256 != original.digest
                    || pdf_quarantine.byte_length != original.byte_length
                    || pdf_quarantine.provenance != self.probe_provenance
                {
                    return Err(HeleosError::Integrity);
                }
                let outcome = pdf_quarantine.ingest_outcome();
                return self.complete_quarantine(QuarantineCompletion {
                    job_id,
                    attempt,
                    write_checkpoint: true,
                    input,
                    actor,
                    retained: Some(original.clone()),
                    quarantine: IntakeQuarantineV1 {
                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                        reason: IntakeQuarantineReasonV1::Pdf(pdf_quarantine.reason),
                        probe_provenance: Some(pdf_quarantine.provenance),
                    },
                    outcome,
                    preexisting_vault_digests: vec![original.digest],
                });
            }
        };
        if inspection.revision_id != revision_id
            || inspection.content_sha256 != original.digest
            || inspection.byte_length != original.byte_length
            || inspection.provenance != self.probe_provenance
        {
            return Err(HeleosError::Integrity);
        }
        let manifest = EvidenceManifestV1::new(
            original.digest,
            original.byte_length,
            inspection.pages,
            inspection.provenance,
        )?;
        let manifest_bytes = manifest.canonical_bytes()?;
        let manifest_length =
            u64::try_from(manifest_bytes.len()).map_err(|_| HeleosError::Integrity)?;
        let manifest_digest = Sha256Digest::hash_reader(manifest_bytes.as_slice())?;
        let manifest_quota = self
            .store
            .quota_snapshot(input.project_id, Some(manifest_digest))?;
        if let Some(admission) = &manifest_quota.admission
            && (admission.digest != manifest_digest
                || admission.byte_length != manifest_length
                || admission.media_type != super::evidence::EVIDENCE_MANIFEST_MEDIA_TYPE
                || admission.admission_state != "accepted"
                || admission.vault_key != crate::Vault::object_key(manifest_digest)
                || admission.quarantine.is_some())
        {
            return Err(HeleosError::Integrity);
        }
        let manifest_observed_preexisting = match self.vault.open_verified(manifest_digest) {
            Ok(verified) => {
                if verified.byte_length() != manifest_length
                    || verified.vault_key() != crate::Vault::object_key(manifest_digest)
                {
                    return Err(HeleosError::Integrity);
                }
                true
            }
            Err(HeleosError::NotFound)
                if manifest_quota.admission.is_none()
                    && !manifest_quota.store_overall_accounted
                    && !manifest_quota.store_evidence_accounted =>
            {
                false
            }
            Err(HeleosError::NotFound) => return Err(HeleosError::Integrity),
            Err(error) => return Err(error),
        };
        let original_project_debit = if original_quota.project_overall_accounted {
            0
        } else {
            original.byte_length
        };
        let manifest_project_debit = if manifest_quota.project_overall_accounted {
            0
        } else {
            manifest_length
        };
        let manifest_project_evidence_debit = if manifest_quota.project_evidence_accounted {
            0
        } else {
            manifest_length
        };
        let project_debit = original_project_debit
            .checked_add(manifest_project_debit)
            .ok_or(HeleosError::Integrity)?;
        let manifest_store_evidence_debit = if manifest_quota.store_evidence_accounted {
            0
        } else {
            manifest_length
        };
        if !prospective_fits(
            original_quota.project_overall_bytes,
            project_debit,
            PROJECT_OVERALL_QUOTA_BYTES,
        )? || !prospective_fits(
            manifest_quota.project_evidence_bytes,
            manifest_project_evidence_debit,
            PROJECT_EVIDENCE_QUOTA_BYTES,
        )? || !prospective_fits(
            manifest_quota.store_evidence_bytes,
            manifest_store_evidence_debit,
            STORE_EVIDENCE_QUOTA_BYTES,
        )? {
            return self.complete_quarantine(QuarantineCompletion {
                job_id,
                attempt,
                write_checkpoint: true,
                input,
                actor,
                retained: Some(original.clone()),
                quarantine: IntakeQuarantineV1 {
                    schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                    reason: IntakeQuarantineReasonV1::EvidenceManifestQuota,
                    probe_provenance: Some(self.probe_provenance.clone()),
                },
                outcome: IngestOutcome::QuarantinedLimit,
                preexisting_vault_digests: vec![original.digest],
            });
        }
        let projected_project = original_quota
            .project_overall_bytes
            .checked_add(original_project_debit)
            .ok_or(HeleosError::Integrity)?;
        let retain_budget = remaining_min(&[
            (PROJECT_OVERALL_QUOTA_BYTES, projected_project),
            (
                PROJECT_EVIDENCE_QUOTA_BYTES,
                manifest_quota.project_evidence_bytes,
            ),
            (
                STORE_EVIDENCE_QUOTA_BYTES,
                manifest_quota.store_evidence_bytes,
            ),
        ])?;
        #[cfg(test)]
        self.observe_before_accounted_publication(
            AccountedPublicationTestPoint::Manifest,
            manifest_digest,
        )?;
        let inventory = self.store.vault_inventory_rows()?;
        let orphan_budget = remaining_vault_orphan_budget(&manifest_quota)?;
        let stored_manifest = match self.vault.put_reader_accounted(
            manifest_bytes.as_slice(),
            crate::VaultWriteBudget::new(manifest_length, retain_budget)?,
            &inventory,
            orphan_budget,
        )? {
            crate::PutOutcome::Stored(stored) => stored,
            crate::PutOutcome::QuotaRejected {
                digest,
                byte_length,
            } => {
                if digest != manifest_digest || byte_length != manifest_length {
                    return Err(HeleosError::Integrity);
                }
                return self.complete_quarantine(QuarantineCompletion {
                    job_id,
                    attempt,
                    write_checkpoint: true,
                    input,
                    actor,
                    retained: Some(original.clone()),
                    quarantine: IntakeQuarantineV1 {
                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                        reason: IntakeQuarantineReasonV1::EvidenceManifestQuota,
                        probe_provenance: Some(self.probe_provenance.clone()),
                    },
                    outcome: IngestOutcome::QuarantinedLimit,
                    preexisting_vault_digests: vec![original.digest],
                });
            }
        };
        if manifest_observed_preexisting && stored_manifest.newly_published {
            return Err(HeleosError::Integrity);
        }
        let manifest_preexisting =
            manifest_observed_preexisting || !stored_manifest.newly_published;
        let manifest_object = StoredObjectV1::from(&stored_manifest);
        let mut preexisting = vec![original.digest];
        if manifest_preexisting {
            preexisting.push(stored_manifest.digest);
        }
        self.complete_accepted(AcceptedCompletion {
            job_id,
            attempt,
            write_checkpoint: true,
            input,
            actor,
            original,
            manifest_object,
            manifest,
            preexisting_vault_digests: preexisting,
        })
    }
}

fn verify_receipt_objects(
    store: &crate::Store,
    vault: &crate::Vault,
    project_id: ProjectId,
    receipt: &IngestReceipt,
) -> Result<Vec<Sha256Digest>> {
    receipt.validate()?;
    let mut required = Vec::with_capacity(2);
    if let Some(original) = receipt.content_sha256 {
        let requires_original = receipt.evidence_manifest.is_some()
            || receipt.quarantine.as_ref().is_some_and(|quarantine| {
                matches!(
                    quarantine.reason,
                    IntakeQuarantineReasonV1::Pdf(_)
                        | IntakeQuarantineReasonV1::EvidenceManifestQuota
                        | IntakeQuarantineReasonV1::ProjectAssociationQuota
                )
            });
        if requires_original {
            let expected_original = StoredObjectV1 {
                digest: original,
                byte_length: receipt.byte_length,
                vault_key: crate::Vault::object_key(original),
            };
            let verified = open_checkpoint_object(vault, &expected_original)?;
            drop(verified);
            if let Some(quarantine) = &receipt.quarantine
                && matches!(
                    quarantine.reason,
                    IntakeQuarantineReasonV1::Pdf(_)
                        | IntakeQuarantineReasonV1::EvidenceManifestQuota
                )
            {
                let admission = store
                    .quota_snapshot(project_id, Some(original))?
                    .admission
                    .ok_or(HeleosError::Integrity)?;
                if admission.digest != original
                    || admission.byte_length != receipt.byte_length
                    || admission.media_type != super::evidence::PDF_MEDIA_TYPE
                    || admission.admission_state != "quarantined"
                    || admission.vault_key != expected_original.vault_key
                    || admission.quarantine.as_ref() != Some(quarantine)
                {
                    return Err(HeleosError::Integrity);
                }
            }
            required.push(original);
        }
    }
    if let Some(expected) = &receipt.evidence_manifest {
        let manifest_object = StoredObjectV1 {
            digest: expected.manifest_content_sha256,
            byte_length: expected.manifest_byte_length,
            vault_key: expected.manifest_vault_key.clone(),
        };
        let verified = open_checkpoint_object(vault, &manifest_object)?;
        drop(verified);
        let revision = receipt.revision_id.ok_or(HeleosError::Integrity)?;
        let current =
            super::FoundationReader::new(store, vault).evidence_manifest_for_revision(revision)?;
        if current.manifest != expected.manifest
            || current.manifest_content_sha256 != expected.manifest_content_sha256
            || current.manifest_byte_length != expected.manifest_byte_length
            || current.manifest_media_type != expected.manifest_media_type
            || current.manifest_vault_key != expected.manifest_vault_key
            || current.original_vault_key != expected.original_vault_key
        {
            return Err(HeleosError::Integrity);
        }
        if !expected
            .lineages
            .iter()
            .any(|lineage| lineage.project_id == project_id)
            || !lineages_are_subset(&expected.lineages, &current.lineages)
        {
            return Err(HeleosError::Integrity);
        }
        let original = receipt.content_sha256.ok_or(HeleosError::Integrity)?;
        let original_admission = store
            .quota_snapshot(project_id, Some(original))?
            .admission
            .ok_or(HeleosError::Integrity)?;
        if original_admission.digest != original
            || original_admission.byte_length != receipt.byte_length
            || original_admission.media_type != super::evidence::PDF_MEDIA_TYPE
            || original_admission.admission_state != "accepted"
            || original_admission.vault_key != expected.original_vault_key
            || original_admission.quarantine.is_some()
        {
            return Err(HeleosError::Integrity);
        }
        let manifest_admission = store
            .quota_snapshot(project_id, Some(expected.manifest_content_sha256))?
            .admission
            .ok_or(HeleosError::Integrity)?;
        if manifest_admission.digest != expected.manifest_content_sha256
            || manifest_admission.byte_length != expected.manifest_byte_length
            || manifest_admission.media_type != super::evidence::EVIDENCE_MANIFEST_MEDIA_TYPE
            || manifest_admission.admission_state != "accepted"
            || manifest_admission.vault_key != expected.manifest_vault_key
            || manifest_admission.quarantine.is_some()
        {
            return Err(HeleosError::Integrity);
        }
        required.push(expected.manifest_content_sha256);
    }
    required.sort_unstable();
    required.dedup();
    Ok(required)
}

fn lineages_are_subset(
    expected: &[super::evidence::EvidenceManifestLineage],
    current: &[super::evidence::EvidenceManifestLineage],
) -> bool {
    let key = |lineage: &super::evidence::EvidenceManifestLineage| {
        (
            *lineage.project_id.as_uuid().as_bytes(),
            *lineage.evidence_id.as_uuid().as_bytes(),
            *lineage.originating_job_id.as_uuid().as_bytes(),
        )
    };
    let mut current_index = 0_usize;
    for expected_lineage in expected {
        let expected_key = key(expected_lineage);
        while current_index < current.len() && key(&current[current_index]) < expected_key {
            current_index += 1;
        }
        if current_index == current.len() || key(&current[current_index]) != expected_key {
            return false;
        }
        current_index += 1;
    }
    true
}

fn open_checkpoint_object(
    vault: &crate::Vault,
    expected: &StoredObjectV1,
) -> Result<crate::VerifiedObject> {
    expected.validate()?;
    let verified = match vault.open_verified(expected.digest) {
        Ok(verified) => verified,
        Err(HeleosError::NotFound | HeleosError::ResourceLimit) => {
            return Err(HeleosError::Integrity);
        }
        Err(error) => return Err(error),
    };
    if verified.byte_length() != expected.byte_length || verified.vault_key() != expected.vault_key
    {
        return Err(HeleosError::Integrity);
    }
    Ok(verified)
}

fn remaining_min(quotas: &[(u64, u64)]) -> Result<u64> {
    quotas
        .iter()
        .map(|(limit, used)| limit.checked_sub(*used).ok_or(HeleosError::Integrity))
        .collect::<Result<Vec<_>>>()?
        .into_iter()
        .min()
        .ok_or(HeleosError::Integrity)
}

fn remaining_vault_orphan_budget(
    quota: &crate::store::ingest_repository::QuotaSnapshot,
) -> Result<crate::vault::VaultOrphanBudget> {
    let remaining = STORE_QUARANTINE_QUOTA_BYTES
        .checked_sub(quota.store_quarantine_bytes)
        .ok_or(HeleosError::Integrity)?;
    crate::vault::VaultOrphanBudget::new(remaining)
}

fn prospective_fits(used: u64, debit: u64, limit: u64) -> Result<bool> {
    Ok(used.checked_add(debit).ok_or(HeleosError::Integrity)? <= limit)
}

fn checked_jcs_time(value: i64) -> Result<i64> {
    if value < 0 || u64::try_from(value).map_err(|_| HeleosError::Integrity)? > JCS_SAFE_INTEGER_MAX
    {
        return Err(HeleosError::Integrity);
    }
    Ok(value)
}

fn checked_jcs_add(value: i64, delta: i64) -> Result<i64> {
    checked_jcs_time(value.checked_add(delta).ok_or(HeleosError::Integrity)?)
}

#[cfg(test)]
mod tests {
    use std::{cell::Cell, fs, str::FromStr};

    use uuid::Uuid;

    use super::*;
    use crate::{
        EvidenceManifestLineage, PageMetadata, PageTransform, PageUnit, PdfProbeProvenance, page_id,
    };

    fn uuid(value: u128) -> Uuid {
        Uuid::from_u128((4_u128 << 76) | (2_u128 << 62) | value)
    }

    fn provenance() -> PdfProbeProvenance {
        PdfProbeProvenance {
            parser_name: "fixture-parser".to_owned(),
            parser_version: "1.0.0".to_owned(),
            guest_wasm_sha256: Sha256Digest::from_bytes([2; 32]),
            guest_source_tree_sha256: Sha256Digest::from_bytes([3; 32]),
            guest_dependency_graph_sha256: Sha256Digest::from_bytes([4; 32]),
            protocol_version: "heleos.pdf-probe/v1".to_owned(),
        }
    }

    fn accepted_receipt() -> IngestReceipt {
        accepted_receipt_with_pages(1)
    }

    fn accepted_receipt_with_pages(page_count: u32) -> IngestReceipt {
        let content = Sha256Digest::from_bytes([1; 32]);
        let manifest = EvidenceManifestV1::new(
            content,
            7,
            (0..page_count)
                .map(|index| PageMetadata {
                    index,
                    page_id: page_id(content, index),
                    width_micropoints: 612_000_000,
                    height_micropoints: 792_000_000,
                    unit: PageUnit::Point,
                    rotation_degrees: 0,
                    transform: PageTransform {
                        m11: 1,
                        m12: 0,
                        m21: 0,
                        m22: -1,
                        tx_micropoints: 0,
                        ty_micropoints: 792_000_000,
                    },
                })
                .collect(),
            provenance(),
        )
        .expect("valid manifest");
        let manifest_bytes = manifest.canonical_bytes().expect("canonical manifest");
        let manifest_digest =
            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest");
        let (document_id, revision_id) = crate::canonical_document_ids(content);
        let job_id = JobId::from_uuid(uuid(2));
        IngestReceipt {
            ingest_event_id: IngestEventId::from_uuid(uuid(1)),
            authoritative_job_id: job_id,
            attempt: 1,
            outcome: IngestOutcome::AcceptedNew,
            content_sha256: Some(content),
            byte_length: 7,
            quarantine: None,
            document_id: Some(document_id),
            revision_id: Some(revision_id),
            sheet_ids: (0..page_count)
                .map(|index| page_id(content, index))
                .collect(),
            evidence_manifest: Some(EvidenceManifestReceipt {
                manifest,
                manifest_content_sha256: manifest_digest,
                manifest_byte_length: u64::try_from(manifest_bytes.len())
                    .expect("manifest length fits"),
                manifest_media_type: crate::EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
                manifest_vault_key: crate::Vault::object_key(manifest_digest),
                original_vault_key: crate::Vault::object_key(content),
                lineages: vec![EvidenceManifestLineage {
                    project_id: ProjectId::from_uuid(uuid(3)),
                    evidence_id: EvidenceId::from_uuid(uuid(4)),
                    originating_job_id: job_id,
                }],
            }),
            preexisting_vault_digests: Vec::new(),
        }
    }

    fn valid_input() -> IngestInputV1 {
        IngestInputV1 {
            schema: INGEST_INPUT_SCHEMA_V1.to_owned(),
            project_id: ProjectId::from_uuid(uuid(3)),
            kind: INGEST_KIND.to_owned(),
            idempotency_key: IdempotencyKey::try_from("request-one").expect("key"),
            content_sha256: Some(Sha256Digest::from_bytes([1; 32])),
            byte_length: 7,
            source_display: "utf8:file.pdf".to_owned(),
            requested_limits: PdfLimits::default().into(),
            expected_probe_provenance: provenance(),
            deadline_profile_ms: JOB_DEADLINE_MS as u64,
        }
    }

    struct EngineTestClock;

    impl crate::Clock for EngineTestClock {
        fn now_unix_ms(&self) -> i64 {
            1_700_000_000_000
        }
    }

    struct EngineTestIds {
        next: Cell<u128>,
    }

    impl EngineTestIds {
        fn new(first: u128) -> Self {
            Self {
                next: Cell::new(first),
            }
        }
    }

    impl crate::IdGenerator for EngineTestIds {
        fn next_uuid(&self) -> Uuid {
            let value = self.next.get();
            self.next
                .set(value.checked_add(1).expect("bounded test ID sequence"));
            uuid(value)
        }
    }

    #[derive(Clone)]
    struct EngineAcceptedProbe {
        provenance: PdfProbeProvenance,
    }

    impl crate::PdfProbe for EngineAcceptedProbe {
        fn provenance(&self) -> Result<PdfProbeProvenance> {
            Ok(self.provenance.clone())
        }

        fn probe(
            &self,
            input: crate::VerifiedObject,
            revision: RevisionId,
            _: PdfLimits,
        ) -> Result<crate::PdfProbeOutcome> {
            let digest = input.digest();
            Ok(crate::PdfProbeOutcome::Accepted(crate::PdfInspection {
                revision_id: revision,
                content_sha256: digest,
                byte_length: input.byte_length(),
                provenance: self.provenance.clone(),
                pages: vec![engine_test_page(digest)],
            }))
        }
    }

    struct EngineFailAt(super::super::FaultPoint);

    impl super::super::FaultInjector for EngineFailAt {
        fn inject(&self, point: super::super::FaultPoint) -> Result<()> {
            if point == self.0 {
                Err(HeleosError::FaultInjected)
            } else {
                Ok(())
            }
        }
    }

    fn engine_test_page(digest: Sha256Digest) -> PageMetadata {
        PageMetadata {
            index: 0,
            page_id: page_id(digest, 0),
            width_micropoints: 612_000_000,
            height_micropoints: 792_000_000,
            unit: PageUnit::Point,
            rotation_degrees: 0,
            transform: PageTransform {
                m11: 1,
                m12: 0,
                m21: 0,
                m22: -1,
                tx_micropoints: 0,
                ty_micropoints: 792_000_000,
            },
        }
    }

    fn engine_test_manifest_bytes(source: &[u8]) -> (Sha256Digest, Vec<u8>) {
        let digest = Sha256Digest::hash_reader(source).expect("hash test original");
        let manifest = EvidenceManifestV1::new(
            digest,
            u64::try_from(source.len()).expect("source length fits"),
            vec![engine_test_page(digest)],
            provenance(),
        )
        .expect("construct test manifest");
        let bytes = manifest.canonical_bytes().expect("encode test manifest");
        let manifest_digest =
            Sha256Digest::hash_reader(bytes.as_slice()).expect("hash test manifest");
        (manifest_digest, bytes)
    }

    fn engine_test_vault(parent: &tempfile::TempDir) -> crate::Vault {
        crate::apply_private_permissions(parent.path()).expect("harden test vault parent");
        let root = fs::canonicalize(parent.path())
            .expect("canonicalize test vault parent")
            .join("vault");
        crate::Vault::open(crate::VaultConfig {
            root,
            open_mode: crate::VaultOpenMode::CreateNew,
        })
        .expect("open test vault")
    }

    fn engine_test_project(store: &mut crate::Store, ids: &EngineTestIds, project_id: ProjectId) {
        crate::ProjectService::new(store, &EngineTestClock, ids)
            .expect("construct project service")
            .create(crate::ProjectCreateRequest {
                project_id: Some(project_id),
                name: "Publication race fixture".to_owned(),
                actor: ActorId::from_str("creator").expect("creator actor"),
                data_class: crate::DataClass::Internal,
            })
            .expect("create test project");
    }

    #[test]
    fn same_digest_winner_after_preliminary_original_observation_is_reported_preexisting() {
        // Break caught: a winner linked after the preliminary miss disappearing from the receipt.
        let mut store = crate::Store::open_in_memory().expect("open test store");
        store.migrate().expect("migrate test store");
        let vault_parent = tempfile::TempDir::new().expect("create vault parent");
        let vault = engine_test_vault(&vault_parent);
        let ids = EngineTestIds::new(100);
        let project_id = ProjectId::from_uuid(uuid(90));
        engine_test_project(&mut store, &ids, project_id);
        let source_parent = tempfile::TempDir::new().expect("create source parent");
        let source_path = source_parent.path().join("fresh.pdf");
        let source = b"%PDF-1.7\nfresh same-digest winner\n%%EOF\n";
        fs::write(&source_path, source).expect("write test source");
        let source_digest = Sha256Digest::hash_reader(source.as_slice()).expect("hash test source");
        let winner_calls = Cell::new(0_u8);
        let publish_winner = |point, digest| {
            if point != AccountedPublicationTestPoint::Original {
                return Ok(());
            }
            if winner_calls.replace(1) != 0 || digest != source_digest {
                return Err(HeleosError::Integrity);
            }
            let length = u64::try_from(source.len()).map_err(|_| HeleosError::Integrity)?;
            match vault.put_reader(
                source.as_slice(),
                crate::VaultWriteBudget::new(length, length)?,
            )? {
                crate::PutOutcome::Stored(stored)
                    if stored.digest == source_digest && stored.newly_published =>
                {
                    Ok(())
                }
                _ => Err(HeleosError::Integrity),
            }
        };
        let probe = EngineAcceptedProbe {
            provenance: provenance(),
        };
        let mut engine = IngestEngine::new(&mut store, &vault, &probe, &EngineTestClock, &ids)
            .expect("construct intake engine");
        engine.before_accounted_publication = Some(&publish_winner);

        let receipt = engine
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("open test source"),
                idempotency_key: IdempotencyKey::try_from("fresh-winner").expect("key"),
                actor: ActorId::from_str("tester").expect("actor"),
                pdf_limits: PdfLimits::default(),
            })
            .expect("ingest with deterministic winner");

        assert_eq!(winner_calls.get(), 1);
        assert_eq!(receipt.outcome, IngestOutcome::AcceptedNew);
        assert_eq!(receipt.preexisting_vault_digests, vec![source_digest]);
    }

    #[test]
    fn same_digest_winner_after_resumed_manifest_observation_is_reported_preexisting() {
        // Break caught: a resumed manifest winner linked after the preliminary miss being omitted.
        let mut store = crate::Store::open_in_memory().expect("open test store");
        store.migrate().expect("migrate test store");
        let vault_parent = tempfile::TempDir::new().expect("create vault parent");
        let vault = engine_test_vault(&vault_parent);
        let ids = EngineTestIds::new(200);
        let project_id = ProjectId::from_uuid(uuid(190));
        engine_test_project(&mut store, &ids, project_id);
        let source_parent = tempfile::TempDir::new().expect("create source parent");
        let source_path = source_parent.path().join("resume.pdf");
        let source = b"%PDF-1.7\nresumed manifest same-digest winner\n%%EOF\n";
        fs::write(&source_path, source).expect("write test source");
        let source_digest = Sha256Digest::hash_reader(source.as_slice()).expect("hash test source");
        let probe = EngineAcceptedProbe {
            provenance: provenance(),
        };
        let fault = EngineFailAt(super::super::FaultPoint::AfterVaultPublish);
        let interrupted = IngestEngine::with_fault_injector(
            &mut store,
            &vault,
            &probe,
            &EngineTestClock,
            &ids,
            &fault,
        )
        .expect("construct faulting engine")
        .ingest(IngestRequest {
            project_id,
            source: IntakeSource::open(&source_path).expect("open test source"),
            idempotency_key: IdempotencyKey::try_from("resume-winner").expect("key"),
            actor: ActorId::from_str("tester").expect("actor"),
            pdf_limits: PdfLimits::default(),
        });
        assert!(matches!(interrupted, Err(HeleosError::FaultInjected)));
        let job_id = crate::FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect queued job")
            .jobs[0]
            .job_id;
        let (manifest_digest, manifest_bytes) = engine_test_manifest_bytes(source);
        let winner_calls = Cell::new(0_u8);
        let publish_winner = |point, digest| {
            if point != AccountedPublicationTestPoint::Manifest {
                return Ok(());
            }
            if winner_calls.replace(1) != 0 || digest != manifest_digest {
                return Err(HeleosError::Integrity);
            }
            let length = u64::try_from(manifest_bytes.len()).map_err(|_| HeleosError::Integrity)?;
            match vault.put_reader(
                manifest_bytes.as_slice(),
                crate::VaultWriteBudget::new(length, length)?,
            )? {
                crate::PutOutcome::Stored(stored)
                    if stored.digest == manifest_digest && stored.newly_published =>
                {
                    Ok(())
                }
                _ => Err(HeleosError::Integrity),
            }
        };
        let mut engine = IngestEngine::new(&mut store, &vault, &probe, &EngineTestClock, &ids)
            .expect("construct resume engine");
        engine.before_accounted_publication = Some(&publish_winner);

        let receipt = engine
            .resume(job_id, ActorId::from_str("resumer").expect("actor"))
            .expect("resume with deterministic manifest winner")
            .receipt;
        let mut expected = vec![source_digest, manifest_digest];
        expected.sort_unstable();

        assert_eq!(winner_calls.get(), 1);
        assert_eq!(receipt.outcome, IngestOutcome::AcceptedNew);
        assert_eq!(receipt.preexisting_vault_digests, expected);
    }

    #[test]
    fn committed_authority_with_unknown_outcome_reopens_as_one_replayable_result() {
        // Break caught: a post-commit check failure causing a durable accepted authority to be
        // retried as a second job/result instead of a same-key replay after restart.
        let database_parent = tempfile::TempDir::new().expect("create database parent");
        crate::apply_private_permissions(database_parent.path()).expect("harden database parent");
        let database_path = fs::canonicalize(database_parent.path())
            .expect("canonicalize database parent")
            .join("foundation.sqlite3");
        let mut store = crate::Store::open_writer(&database_path).expect("open writer");
        store.migrate().expect("migrate store");
        let vault_parent = tempfile::TempDir::new().expect("create vault parent");
        let vault = engine_test_vault(&vault_parent);
        let ids = EngineTestIds::new(300);
        let project_id = ProjectId::from_uuid(uuid(290));
        engine_test_project(&mut store, &ids, project_id);
        let source_parent = tempfile::TempDir::new().expect("create source parent");
        let source_path = source_parent.path().join("unknown-commit.pdf");
        let source = b"%PDF-1.7\nunknown accepted commit\n%%EOF\n";
        fs::write(&source_path, source).expect("write test source");
        let source_digest = Sha256Digest::hash_reader(source.as_slice()).expect("hash source");
        let probe = EngineAcceptedProbe {
            provenance: provenance(),
        };
        store.inject_commit_outcome_unknown_after_for_test(3);
        let result = IngestEngine::new(&mut store, &vault, &probe, &EngineTestClock, &ids)
            .expect("construct intake engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("open source"),
                idempotency_key: IdempotencyKey::try_from("unknown-authority").expect("key"),
                actor: ActorId::from_str("tester").expect("actor"),
                pdf_limits: PdfLimits::default(),
            });
        assert!(matches!(result, Err(HeleosError::CommitOutcomeUnknown)));
        let before = crate::FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect committed unknown outcome");
        assert_eq!(before.jobs.len(), 1);
        assert_eq!(before.jobs[0].state, crate::JobState::Succeeded);
        assert_eq!(before.intake_events.len(), 1);
        assert_eq!(before.intake_events[0].outcome, IngestOutcome::AcceptedNew);
        assert_eq!(before.content_objects.len(), 2);
        assert!(
            before
                .content_objects
                .iter()
                .any(|object| object.sha256 == source_digest)
        );
        let authoritative_job_id = before.jobs[0].job_id;
        let authoritative_event_id = before.intake_events[0].ingest_event_id;
        let document_ids = before.document_ids.clone();
        let revision_ids = before.revision_ids.clone();
        let evidence = before.evidence.clone();

        drop(store);
        drop(vault);
        let mut store = crate::Store::open_writer(&database_path).expect("reopen writer");
        let vault = crate::Vault::open(crate::VaultConfig {
            root: fs::canonicalize(vault_parent.path())
                .expect("canonicalize vault parent")
                .join("vault"),
            open_mode: crate::VaultOpenMode::ExistingOnly,
        })
        .expect("reopen vault");
        let replay = IngestEngine::new(&mut store, &vault, &probe, &EngineTestClock, &ids)
            .expect("construct replay engine")
            .ingest(IngestRequest {
                project_id,
                source: IntakeSource::open(&source_path).expect("reopen source"),
                idempotency_key: IdempotencyKey::try_from("unknown-authority").expect("key"),
                actor: ActorId::from_str("replayer").expect("actor"),
                pdf_limits: PdfLimits::default(),
            })
            .expect("retry resolves as replay");
        assert_eq!(replay.outcome, IngestOutcome::IdempotentReplay);
        assert_eq!(replay.authoritative_job_id, authoritative_job_id);
        assert_ne!(replay.ingest_event_id, authoritative_event_id);
        let after = crate::FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id)
            .expect("inspect replayed authority");
        assert_eq!(after.jobs.len(), 1);
        assert_eq!(after.content_objects.len(), 2);
        assert_eq!(after.document_ids, document_ids);
        assert_eq!(after.revision_ids, revision_ids);
        assert_eq!(after.evidence, evidence);
        assert_eq!(after.intake_events.len(), 2);
        assert_eq!(
            after
                .intake_events
                .iter()
                .filter(|event| event.outcome == IngestOutcome::AcceptedNew)
                .count(),
            1
        );
        assert_eq!(
            after
                .intake_events
                .iter()
                .filter(|event| event.outcome == IngestOutcome::IdempotentReplay)
                .count(),
            1
        );
    }

    #[test]
    fn project_association_preflight_resume_rehashes_its_claimed_existing_object() {
        // Break caught: a resumed association denial claiming call-relative preexistence after
        // its checkpointed duplicate disappeared or changed following the commit-boundary fault.
        use crate::store::ingest_repository::JobCreateCommand;

        struct ManualEngineClock(Cell<i64>);

        impl crate::Clock for ManualEngineClock {
            fn now_unix_ms(&self) -> i64 {
                self.0.get()
            }
        }

        for remove_object in [true, false] {
            let mut store = crate::Store::open_in_memory().expect("open test store");
            store.migrate().expect("migrate test store");
            let vault_parent = tempfile::TempDir::new().expect("create vault parent");
            let vault = engine_test_vault(&vault_parent);
            let ids = EngineTestIds::new(800);
            let project_id = ProjectId::from_uuid(uuid(790));
            engine_test_project(&mut store, &ids, project_id);
            let source = b"%PDF-1.7\nassociation checkpoint object\n%%EOF\n";
            let digest = Sha256Digest::hash_reader(source.as_slice()).expect("hash source");
            let byte_length = u64::try_from(source.len()).expect("source length");
            let stored = match vault
                .put_reader(
                    source.as_slice(),
                    crate::VaultWriteBudget::new(byte_length, byte_length)
                        .expect("object write budget"),
                )
                .expect("publish claimed duplicate")
            {
                crate::PutOutcome::Stored(stored) => stored,
                crate::PutOutcome::QuotaRejected { .. } => panic!("object must fit"),
            };
            let mut input = valid_input();
            input.project_id = project_id;
            input.idempotency_key = IdempotencyKey::try_from("association-resume").expect("key");
            input.content_sha256 = Some(digest);
            input.byte_length = byte_length;
            input.source_display = "utf8:association.pdf".to_owned();
            let quarantine = IntakeQuarantineV1 {
                schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                reason: IntakeQuarantineReasonV1::ProjectAssociationQuota,
                probe_provenance: None,
            };
            let job_id = JobId::from_uuid(uuid(900));
            let created_at_ms = 1_700_000_000_000_i64;
            store
                .job_create_with_checkpoint_and_audit(JobCreateCommand {
                    job_id,
                    project_id,
                    input,
                    budget: IngestBudgetV1::new(byte_length),
                    checkpoint: IngestCheckpointV1 {
                        schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                        phase: IngestCheckpointPhaseV1::PreflightRejected {
                            content_sha256: Some(digest),
                            byte_length,
                            quarantine,
                        },
                    },
                    actor: ActorId::from_str("tester").expect("actor"),
                    created_at_ms,
                    deadline_at_ms: created_at_ms + JOB_DEADLINE_MS,
                    audit_event_id: crate::AuditEventId::from_uuid(uuid(901)),
                })
                .expect("create preflight job");
            let clock = ManualEngineClock(Cell::new(created_at_ms));
            let probe = EngineAcceptedProbe {
                provenance: provenance(),
            };
            let interrupted = IngestEngine::with_fault_injector(
                &mut store,
                &vault,
                &probe,
                &clock,
                &ids,
                &EngineFailAt(super::super::FaultPoint::BeforeAuthoritativeCommit),
            )
            .expect("construct faulting resume engine")
            .resume(job_id, ActorId::from_str("first-resumer").expect("actor"));
            assert!(matches!(interrupted, Err(HeleosError::FaultInjected)));

            let object_path = vault_parent
                .path()
                .join("vault")
                .join(crate::Vault::object_key(digest));
            if remove_object {
                fs::remove_file(&object_path).expect("remove checkpoint object");
            } else {
                fs::write(&object_path, vec![0_u8; source.len()])
                    .expect("replace checkpoint object at same length");
                crate::apply_private_permissions(&object_path)
                    .expect("reharden corrupt checkpoint object");
            }
            clock.0.set(created_at_ms + LEASE_DURATION_MS);
            let resumed = IngestEngine::new(&mut store, &vault, &probe, &clock, &ids)
                .expect("construct second resume engine")
                .resume(job_id, ActorId::from_str("second-resumer").expect("actor"));
            assert!(matches!(resumed, Err(HeleosError::Integrity)));
            let inspection = crate::FoundationReader::new(&store, &vault)
                .inspect_foundation(project_id)
                .expect("inspect rejected association recovery");
            assert_eq!(inspection.jobs.len(), 1);
            assert_eq!(inspection.jobs[0].state, crate::JobState::Failed);
            assert_eq!(inspection.jobs[0].attempt, 2);
            assert_eq!(inspection.counts.content_objects, 0);
            assert_eq!(
                inspection
                    .intake_events
                    .iter()
                    .filter(|event| event.outcome == IngestOutcome::Interrupted)
                    .count(),
                1
            );
            if remove_object {
                assert!(!object_path.exists());
            } else {
                assert!(matches!(
                    vault.open_verified(stored.digest),
                    Err(HeleosError::Integrity)
                ));
            }
        }
    }

    #[test]
    #[cfg(unix)]
    fn fresh_source_mutation_before_vault_and_after_vault_read_never_commits_authority() {
        // Break caught: same-inode/same-length source replacement escaping either the frozen
        // fingerprint-to-Vault binding or the Vault-read-to-final-source-check binding.
        for timing in ["before_vault", "after_vault_read"] {
            use std::io::{Seek, SeekFrom, Write};
            use std::sync::{Arc, Mutex};

            let mut store = crate::Store::open_in_memory().expect("open test store");
            store.migrate().expect("migrate test store");
            let vault_parent = tempfile::TempDir::new().expect("create vault parent");
            let vault = engine_test_vault(&vault_parent);
            let ids = EngineTestIds::new(500);
            let project_id = ProjectId::from_uuid(uuid(490));
            engine_test_project(&mut store, &ids, project_id);
            let source_parent = tempfile::TempDir::new().expect("create source parent");
            let source_path = source_parent.path().join("mutating-fresh.pdf");
            let original = b"%PDF-1.7\nfresh source alpha\n%%EOF\n";
            let replacement = b"%PDF-1.7\nfresh source omega\n%%EOF\n";
            assert_eq!(original.len(), replacement.len());
            fs::write(&source_path, original).expect("write original source");
            let original_digest =
                Sha256Digest::hash_reader(original.as_slice()).expect("hash original");
            let replacement_digest =
                Sha256Digest::hash_reader(replacement.as_slice()).expect("hash replacement");
            let writer = fs::OpenOptions::new()
                .read(true)
                .write(true)
                .open(&source_path)
                .expect("open retained source mutator");
            let writer = Arc::new(Mutex::new(writer));
            let mutate: Arc<dyn Fn() -> Result<()> + Send + Sync> = {
                let writer = Arc::clone(&writer);
                Arc::new(move || -> Result<()> {
                    let mut writer = writer.lock().map_err(|_| HeleosError::Integrity)?;
                    writer.seek(SeekFrom::Start(0)).map_err(HeleosError::Io)?;
                    writer.write_all(replacement).map_err(HeleosError::Io)?;
                    writer.sync_all().map_err(HeleosError::Io)
                })
            };
            let mut source = IntakeSource::open(&source_path).expect("open retained source");
            if timing == "after_vault_read" {
                source.inject_after_vault_read_for_test(Arc::clone(&mutate));
            }
            let probe = EngineAcceptedProbe {
                provenance: provenance(),
            };
            let before_vault = {
                let mutate = Arc::clone(&mutate);
                move |point: AccountedPublicationTestPoint, _: Sha256Digest| -> Result<()> {
                    if timing == "before_vault" && point == AccountedPublicationTestPoint::Original
                    {
                        mutate()?;
                    }
                    Ok(())
                }
            };
            let mut engine = IngestEngine::new(&mut store, &vault, &probe, &EngineTestClock, &ids)
                .expect("construct mutation engine");
            engine.before_accounted_publication = Some(&before_vault);
            let result = engine.ingest(IngestRequest {
                project_id,
                source,
                idempotency_key: IdempotencyKey::try_from(format!("fresh-{timing}")).expect("key"),
                actor: ActorId::from_str("tester").expect("actor"),
                pdf_limits: PdfLimits::default(),
            });
            assert!(matches!(result, Err(HeleosError::Integrity)));
            let inspection = crate::FoundationReader::new(&store, &vault)
                .inspect_foundation(project_id)
                .expect("inspect rejected mutation");
            assert_eq!(inspection.counts.jobs, 0);
            assert_eq!(inspection.counts.ingest_events, 0);
            assert_eq!(inspection.counts.content_objects, 0);
            if timing == "before_vault" {
                assert!(matches!(
                    vault.open_verified(original_digest),
                    Err(HeleosError::NotFound)
                ));
                assert!(vault.open_verified(replacement_digest).is_ok());
            } else {
                assert!(vault.open_verified(original_digest).is_ok());
                assert!(matches!(
                    vault.open_verified(replacement_digest),
                    Err(HeleosError::NotFound)
                ));
            }
        }
    }

    #[test]
    fn accepted_receipt_binds_sheets_length_and_preexisting_object_authority() {
        // Break caught: valid-looking receipt scalars drifting from manifest/vault authority.
        let mut receipt = accepted_receipt();
        receipt.sheet_ids[0] = SheetId::from(Sha256Digest::from_bytes([8; 32]));
        assert!(receipt.validate().is_err());

        let mut receipt = accepted_receipt();
        receipt.byte_length += 1;
        assert!(receipt.validate().is_err());

        let mut receipt = accepted_receipt();
        receipt.preexisting_vault_digests = vec![Sha256Digest::from_bytes([9; 32])];
        assert!(receipt.validate().is_err());

        let mut duplicate = accepted_receipt();
        duplicate.outcome = IngestOutcome::AcceptedDuplicate;
        let original = duplicate.content_sha256.expect("accepted original");
        let manifest = duplicate
            .evidence_manifest
            .as_ref()
            .expect("accepted evidence")
            .manifest_content_sha256;
        duplicate.preexisting_vault_digests = vec![original, manifest];
        duplicate.preexisting_vault_digests.sort_unstable();
        duplicate.validate().expect("exact duplicate authority set");
        duplicate.preexisting_vault_digests.pop();
        assert!(matches!(duplicate.validate(), Err(HeleosError::Integrity)));
    }

    #[test]
    fn replay_receipts_require_the_exact_call_relative_preexisting_object_set() {
        // Break caught: a replay receipt omitting an already-published authoritative object.
        let mut accepted = accepted_receipt();
        accepted.outcome = IngestOutcome::IdempotentReplay;
        let original = accepted.content_sha256.expect("accepted original digest");
        let manifest = accepted
            .evidence_manifest
            .as_ref()
            .expect("accepted manifest receipt")
            .manifest_content_sha256;
        accepted.preexisting_vault_digests = vec![original, manifest];
        accepted.preexisting_vault_digests.sort_unstable();
        accepted.validate().expect("exact accepted replay set");

        for missing in [original, manifest] {
            let mut receipt = accepted.clone();
            receipt
                .preexisting_vault_digests
                .retain(|digest| *digest != missing);
            assert!(matches!(receipt.validate(), Err(HeleosError::Integrity)));
        }

        for reason in [
            IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic),
            IntakeQuarantineReasonV1::EvidenceManifestQuota,
        ] {
            let mut retained = accepted_receipt();
            let original = retained.content_sha256.expect("retained original digest");
            retained.outcome = IngestOutcome::IdempotentReplay;
            retained.quarantine = Some(IntakeQuarantineV1 {
                schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                reason,
                probe_provenance: Some(provenance()),
            });
            retained.document_id = None;
            retained.revision_id = None;
            retained.sheet_ids.clear();
            retained.evidence_manifest = None;
            retained.preexisting_vault_digests = vec![original];
            retained.validate().expect("exact retained replay set");

            retained.preexisting_vault_digests.clear();
            assert!(matches!(retained.validate(), Err(HeleosError::Integrity)));
        }

        let mut association_denied = accepted_receipt();
        let original = association_denied
            .content_sha256
            .expect("association-denied original digest");
        association_denied.outcome = IngestOutcome::IdempotentReplay;
        association_denied.quarantine = Some(IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason: IntakeQuarantineReasonV1::ProjectAssociationQuota,
            probe_provenance: None,
        });
        association_denied.document_id = None;
        association_denied.revision_id = None;
        association_denied.sheet_ids.clear();
        association_denied.evidence_manifest = None;
        association_denied.preexisting_vault_digests = vec![original];
        association_denied
            .validate()
            .expect("association-denied replay reports preexisting original");

        association_denied.preexisting_vault_digests.clear();
        assert!(matches!(
            association_denied.validate(),
            Err(HeleosError::Integrity)
        ));

        association_denied.outcome = IngestOutcome::QuarantinedLimit;
        association_denied.preexisting_vault_digests = vec![original];
        association_denied
            .validate()
            .expect("authoritative association denial reports existing original");
        association_denied.preexisting_vault_digests.clear();
        assert!(matches!(
            association_denied.validate(),
            Err(HeleosError::Integrity)
        ));

        association_denied.quarantine = Some(IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason: IntakeQuarantineReasonV1::OriginalRetentionQuota,
            probe_provenance: None,
        });
        association_denied.preexisting_vault_digests = vec![original];
        assert!(matches!(
            association_denied.validate(),
            Err(HeleosError::Integrity)
        ));
    }

    #[test]
    fn receipt_deserialization_stops_at_sheet_and_preexisting_digest_caps() {
        // Break caught: public receipt arrays allocating beyond their finite authority sets.
        let at_sheet_cap = accepted_receipt_with_pages(10_000);
        let mut value = serde_json::to_value(&at_sheet_cap).expect("receipt JSON");
        assert!(serde_json::from_value::<IngestReceipt>(value.clone()).is_ok());
        let extra = value["sheet_ids"][9_999].clone();
        value["sheet_ids"]
            .as_array_mut()
            .expect("sheet IDs array")
            .push(extra);
        let error = serde_json::from_value::<IngestReceipt>(value)
            .expect_err("10,001st sheet ID must fail during sequence deserialization");
        assert!(
            error
                .to_string()
                .contains("sequence exceeds maximum of 10000")
        );

        let mut at_digest_cap = accepted_receipt();
        let original = at_digest_cap.content_sha256.expect("original digest");
        let manifest = at_digest_cap
            .evidence_manifest
            .as_ref()
            .expect("manifest receipt")
            .manifest_content_sha256;
        at_digest_cap.preexisting_vault_digests = vec![original, manifest];
        at_digest_cap.preexisting_vault_digests.sort_unstable();
        let mut value = serde_json::to_value(&at_digest_cap).expect("receipt JSON");
        assert!(serde_json::from_value::<IngestReceipt>(value.clone()).is_ok());
        value["preexisting_vault_digests"]
            .as_array_mut()
            .expect("preexisting digest array")
            .push(serde_json::json!(Sha256Digest::from_bytes([9; 32])));
        let error = serde_json::from_value::<IngestReceipt>(value)
            .expect_err("third preexisting digest must fail during sequence deserialization");
        assert!(error.to_string().contains("sequence exceeds maximum of 2"));
    }

    #[test]
    fn quarantine_receipt_binds_reason_outcome_and_digest_shape() {
        // Break caught: authoritative quarantine reason disagreeing with event outcome/digest.
        let mut receipt = accepted_receipt();
        receipt.outcome = IngestOutcome::QuarantinedEncrypted;
        receipt.quarantine = Some(IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason: IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic),
            probe_provenance: Some(provenance()),
        });
        receipt.document_id = None;
        receipt.revision_id = None;
        receipt.sheet_ids.clear();
        receipt.evidence_manifest = None;
        assert!(receipt.validate().is_err());

        receipt.outcome = IngestOutcome::QuarantinedLimit;
        receipt.quarantine = Some(IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason: IntakeQuarantineReasonV1::InputBytes {
                limit_bytes: PdfLimits::default().max_input_bytes,
                observed_bytes: PdfLimits::default().max_input_bytes + 1,
            },
            probe_provenance: None,
        });
        assert!(receipt.validate().is_err());
    }

    #[test]
    fn standalone_quarantine_reason_deserialization_enforces_input_bounds() {
        // Break caught: bypassing the wrapper to deserialize an impossible input-cap reason.
        let maximum = PdfLimits::default().max_input_bytes;
        serde_json::from_value::<IntakeQuarantineReasonV1>(serde_json::json!({
            "kind": "input_bytes",
            "detail": {"limit_bytes": maximum, "observed_bytes": maximum + 1}
        }))
        .expect("exact standalone input-cap reason");

        for hostile in [
            serde_json::json!({
                "kind": "input_bytes",
                "detail": {"limit_bytes": maximum - 1, "observed_bytes": maximum + 1}
            }),
            serde_json::json!({
                "kind": "input_bytes",
                "detail": {"limit_bytes": maximum, "observed_bytes": maximum}
            }),
            serde_json::json!({
                "kind": "input_bytes",
                "detail": {"limit_bytes": maximum, "observed_bytes": JCS_SAFE_INTEGER_MAX + 1}
            }),
            serde_json::json!({
                "kind": "original_retention_quota",
                "detail": null
            }),
        ] {
            assert!(serde_json::from_value::<IntakeQuarantineReasonV1>(hostile).is_err());
        }
    }

    #[test]
    fn accepted_receipt_rejects_an_original_larger_than_its_requested_input_limit() {
        // Break caught: a receipt authorizing an accepted PDF impossible under its own limits.
        let mut receipt = accepted_receipt();
        let evidence = receipt
            .evidence_manifest
            .as_mut()
            .expect("accepted evidence receipt");
        let over_limit = PdfLimits::default().max_input_bytes + 1;
        receipt.byte_length = over_limit;
        evidence.manifest.original.byte_length = over_limit;
        let bytes = crate::canonical_json(&evidence.manifest).expect("raw manifest JSON");
        let digest = Sha256Digest::hash_reader(bytes.as_slice()).expect("manifest digest");
        evidence.manifest_content_sha256 = digest;
        evidence.manifest_byte_length = u64::try_from(bytes.len()).expect("manifest length");
        evidence.manifest_vault_key = crate::Vault::object_key(digest);
        assert!(matches!(receipt.validate(), Err(HeleosError::Integrity)));
    }

    #[test]
    fn resume_receipt_binds_the_returned_receipt_to_the_resumed_attempt() {
        // Break caught: resume claiming attempt N while returning attempt N-1 authority.
        let receipt = accepted_receipt();
        let resume = ResumeReceipt {
            job_id: receipt.authoritative_job_id,
            resumed_attempt: Some(2),
            interrupted_event_id: Some(IngestEventId::from_uuid(uuid(9))),
            receipt,
        };
        assert!(resume.validate().is_err());

        for (resumed_attempt, interrupted_event_id, receipt_attempt) in [
            (Some(1), None, 1),
            (Some(2), Some(IngestEventId::from_uuid(uuid(10))), 2),
            (None, None, 1),
        ] {
            let mut receipt = accepted_receipt();
            receipt.outcome = IngestOutcome::IdempotentReplay;
            receipt.attempt = receipt_attempt;
            let resume = ResumeReceipt {
                job_id: receipt.authoritative_job_id,
                resumed_attempt,
                interrupted_event_id,
                receipt,
            };
            assert!(resume.validate().is_err());
            let value = serde_json::to_value(&resume).expect("resume JSON");
            assert!(serde_json::from_value::<ResumeReceipt>(value).is_err());
        }
    }

    #[test]
    fn public_receipt_jcs_vectors_are_literal_and_stable() {
        // Break caught: a public authority or recovery receipt changing its canonical wire bytes.
        let accepted_new = accepted_receipt();
        let mut accepted_duplicate = accepted_new.clone();
        accepted_duplicate.outcome = IngestOutcome::AcceptedDuplicate;
        accepted_duplicate.preexisting_vault_digests = vec![
            accepted_duplicate
                .content_sha256
                .expect("duplicate original"),
            accepted_duplicate
                .evidence_manifest
                .as_ref()
                .expect("duplicate manifest")
                .manifest_content_sha256,
        ];
        accepted_duplicate.preexisting_vault_digests.sort_unstable();
        accepted_duplicate.validate().expect("duplicate receipt");

        let mut pdf_quarantine = accepted_new.clone();
        pdf_quarantine.outcome = IngestOutcome::QuarantinedCorrupt;
        pdf_quarantine.quarantine = Some(IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason: IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic),
            probe_provenance: Some(provenance()),
        });
        pdf_quarantine.document_id = None;
        pdf_quarantine.revision_id = None;
        pdf_quarantine.sheet_ids.clear();
        pdf_quarantine.evidence_manifest = None;
        pdf_quarantine.validate().expect("PDF quarantine receipt");

        let mut nonretained = pdf_quarantine.clone();
        nonretained.outcome = IngestOutcome::QuarantinedLimit;
        nonretained.quarantine = Some(IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason: IntakeQuarantineReasonV1::OriginalRetentionQuota,
            probe_provenance: None,
        });
        nonretained.validate().expect("nonretained limit receipt");

        let mut replay = accepted_new.clone();
        replay.outcome = IngestOutcome::IdempotentReplay;
        replay.preexisting_vault_digests = accepted_duplicate.preexisting_vault_digests.clone();
        replay.validate().expect("accepted replay receipt");

        let resumed_initial = ResumeReceipt {
            job_id: pdf_quarantine.authoritative_job_id,
            resumed_attempt: Some(1),
            interrupted_event_id: None,
            receipt: pdf_quarantine.clone(),
        };
        resumed_initial.validate().expect("initial resume receipt");
        let mut interrupted_receipt = pdf_quarantine.clone();
        interrupted_receipt.attempt = 2;
        let resumed_interrupted = ResumeReceipt {
            job_id: interrupted_receipt.authoritative_job_id,
            resumed_attempt: Some(2),
            interrupted_event_id: Some(IngestEventId::from_uuid(uuid(9))),
            receipt: interrupted_receipt,
        };
        resumed_interrupted
            .validate()
            .expect("interrupted resume receipt");
        let resumed_terminal = ResumeReceipt {
            job_id: pdf_quarantine.authoritative_job_id,
            resumed_attempt: None,
            interrupted_event_id: None,
            receipt: pdf_quarantine.clone(),
        };
        resumed_terminal
            .validate()
            .expect("terminal resume receipt");

        let actual = [
            crate::canonical_json(&accepted_new).expect("accepted-new JCS"),
            crate::canonical_json(&accepted_duplicate).expect("accepted-duplicate JCS"),
            crate::canonical_json(&pdf_quarantine).expect("PDF quarantine JCS"),
            crate::canonical_json(&nonretained).expect("nonretained JCS"),
            crate::canonical_json(&replay).expect("replay JCS"),
            crate::canonical_json(&resumed_initial).expect("initial resume JCS"),
            crate::canonical_json(&resumed_interrupted).expect("interrupted resume JCS"),
            crate::canonical_json(&resumed_terminal).expect("terminal resume JCS"),
        ];

        const ACCEPTED_PREFIX: &[u8] = br#"{"attempt":1,"authoritative_job_id":"00000000-0000-4000-8000-000000000002","byte_length":7,"content_sha256":"0101010101010101010101010101010101010101010101010101010101010101","document_id":"0101010101010101010101010101010101010101010101010101010101010101","evidence_manifest":{"lineages":[{"evidence_id":"00000000-0000-4000-8000-000000000004","originating_job_id":"00000000-0000-4000-8000-000000000002","project_id":"00000000-0000-4000-8000-000000000003"}],"manifest":{"document_id":"0101010101010101010101010101010101010101010101010101010101010101","original":{"byte_length":7,"media_type":"application/pdf","sha256":"0101010101010101010101010101010101010101010101010101010101010101"},"pages":[{"height_micropoints":792000000,"index":0,"page_id":"afc77f9543d33514d6f70fa7c664189efb42292ec45c2ca7a935238d204aff72","rotation_degrees":0,"transform":{"m11":1,"m12":0,"m21":0,"m22":-1,"tx_micropoints":0,"ty_micropoints":792000000},"unit":"pt","width_micropoints":612000000}],"probe_provenance":{"guest_dependency_graph_sha256":"0404040404040404040404040404040404040404040404040404040404040404","guest_source_tree_sha256":"0303030303030303030303030303030303030303030303030303030303030303","guest_wasm_sha256":"0202020202020202020202020202020202020202020202020202020202020202","parser_name":"fixture-parser","parser_version":"1.0.0","protocol_version":"heleos.pdf-probe/v1"},"requested_limits":{"max_fuel":5000000000,"max_guest_memory_bytes":805306368,"max_indirect_objects":250000,"max_input_bytes":268435456,"max_instances":1,"max_metadata_bytes":16777216,"max_nested_references":64,"max_page_axis_points":14400,"max_pages":10000,"max_protocol_output_bytes":4194304,"max_tables":4,"timeout_seconds":120},"revision_id":"0101010101010101010101010101010101010101010101010101010101010101","schema":"heleos.evidence-manifest/v1"},"manifest_byte_length":1353,"manifest_content_sha256":"bf966017bc683b3c858c2fa32c796f55a59036215d865ed20abbec5cff30469b","manifest_media_type":"application/vnd.heleos.evidence-manifest+json;version=1","manifest_vault_key":"objects/sha256/bf/96/bf966017bc683b3c858c2fa32c796f55a59036215d865ed20abbec5cff30469b","original_vault_key":"objects/sha256/01/01/0101010101010101010101010101010101010101010101010101010101010101"},"ingest_event_id":"00000000-0000-4000-8000-000000000001","outcome":""#;
        const ACCEPTED_PREEXISTING_PREFIX: &[u8] = br#"","preexisting_vault_digests":"#;
        const ACCEPTED_SUFFIX: &[u8] = br#","quarantine":null,"revision_id":"0101010101010101010101010101010101010101010101010101010101010101","sheet_ids":["afc77f9543d33514d6f70fa7c664189efb42292ec45c2ca7a935238d204aff72"]}"#;
        const DUPLICATE_OBJECTS: &[u8] = br#"["0101010101010101010101010101010101010101010101010101010101010101","bf966017bc683b3c858c2fa32c796f55a59036215d865ed20abbec5cff30469b"]"#;
        const QUARANTINE_AFTER_ATTEMPT: &[u8] = br#","authoritative_job_id":"00000000-0000-4000-8000-000000000002","byte_length":7,"content_sha256":"0101010101010101010101010101010101010101010101010101010101010101","document_id":null,"evidence_manifest":null,"ingest_event_id":"00000000-0000-4000-8000-000000000001","outcome":""#;
        const QUARANTINE_BEFORE_BODY: &[u8] = br#"","preexisting_vault_digests":[],"quarantine":"#;
        const PDF_QUARANTINE_BODY: &[u8] = br#"{"probe_provenance":{"guest_dependency_graph_sha256":"0404040404040404040404040404040404040404040404040404040404040404","guest_source_tree_sha256":"0303030303030303030303030303030303030303030303030303030303030303","guest_wasm_sha256":"0202020202020202020202020202020202020202020202020202020202020202","parser_name":"fixture-parser","parser_version":"1.0.0","protocol_version":"heleos.pdf-probe/v1"},"reason":{"detail":{"kind":"bad_magic"},"kind":"pdf"},"schema":"heleos.intake-quarantine/v1"}"#;
        const NONRETAINED_BODY: &[u8] = br#"{"probe_provenance":null,"reason":{"kind":"original_retention_quota"},"schema":"heleos.intake-quarantine/v1"}"#;
        const QUARANTINE_SUFFIX: &[u8] = br#","revision_id":null,"sheet_ids":[]}"#;
        const RESUME_PREFIX: &[u8] = br#"{"interrupted_event_id":"#;
        const RESUME_BEFORE_RECEIPT: &[u8] =
            br#","job_id":"00000000-0000-4000-8000-000000000002","receipt":"#;
        const RESUME_BEFORE_ATTEMPT: &[u8] = br#","resumed_attempt":"#;

        fn literal(parts: &[&[u8]]) -> Vec<u8> {
            let length = parts.iter().map(|part| part.len()).sum();
            let mut bytes = Vec::with_capacity(length);
            for part in parts {
                bytes.extend_from_slice(part);
            }
            bytes
        }
        let accepted_literal = |outcome: &'static [u8], preexisting: &'static [u8]| {
            literal(&[
                ACCEPTED_PREFIX,
                outcome,
                ACCEPTED_PREEXISTING_PREFIX,
                preexisting,
                ACCEPTED_SUFFIX,
            ])
        };
        let quarantine_literal =
            |attempt: &'static [u8], outcome: &'static [u8], body: &'static [u8]| {
                literal(&[
                    br#"{"attempt":"#,
                    attempt,
                    QUARANTINE_AFTER_ATTEMPT,
                    outcome,
                    QUARANTINE_BEFORE_BODY,
                    body,
                    QUARANTINE_SUFFIX,
                ])
            };
        let resume_literal =
            |interrupted: &'static [u8], receipt: &[u8], resumed_attempt: &'static [u8]| {
                literal(&[
                    RESUME_PREFIX,
                    interrupted,
                    RESUME_BEFORE_RECEIPT,
                    receipt,
                    RESUME_BEFORE_ATTEMPT,
                    resumed_attempt,
                    b"}",
                ])
            };
        let accepted_new_literal = accepted_literal(b"accepted_new", b"[]");
        let accepted_duplicate_literal = accepted_literal(b"accepted_duplicate", DUPLICATE_OBJECTS);
        let pdf_literal = quarantine_literal(b"1", b"quarantined_corrupt", PDF_QUARANTINE_BODY);
        let interrupted_pdf_literal =
            quarantine_literal(b"2", b"quarantined_corrupt", PDF_QUARANTINE_BODY);
        let nonretained_literal = quarantine_literal(b"1", b"quarantined_limit", NONRETAINED_BODY);
        let replay_literal = accepted_literal(b"idempotent_replay", DUPLICATE_OBJECTS);
        let expected = [
            accepted_new_literal,
            accepted_duplicate_literal,
            pdf_literal.clone(),
            nonretained_literal,
            replay_literal,
            resume_literal(b"null", &pdf_literal, b"1"),
            resume_literal(
                br#""00000000-0000-4000-8000-000000000009""#,
                &interrupted_pdf_literal,
                b"2",
            ),
            resume_literal(b"null", &pdf_literal, b"null"),
        ];
        assert_eq!(actual, expected);

        for (bytes, receipt) in expected[..5].iter().zip([
            &accepted_new,
            &accepted_duplicate,
            &pdf_quarantine,
            &nonretained,
            &replay,
        ]) {
            assert_eq!(
                serde_json::from_slice::<IngestReceipt>(bytes).expect("literal receipt vector"),
                *receipt
            );
        }
        for (bytes, receipt) in
            expected[5..]
                .iter()
                .zip([&resumed_initial, &resumed_interrupted, &resumed_terminal])
        {
            assert_eq!(
                serde_json::from_slice::<ResumeReceipt>(bytes).expect("literal resume vector"),
                *receipt
            );
        }
    }

    #[test]
    fn nested_pdf_quarantine_reason_rejects_unknown_outer_fields() {
        // Break caught: Task 6 strict wrapper inheriting permissive adjacent-enum decoding.
        let value = serde_json::json!({
            "schema": INTAKE_QUARANTINE_SCHEMA_V1,
            "reason": {
                "kind": "pdf",
                "detail": {"kind": "bad_magic", "extra": true}
            },
            "probe_provenance": provenance(),
        });
        assert!(serde_json::from_value::<IntakeQuarantineV1>(value).is_err());
    }

    #[test]
    fn a_direct_vault_put_after_inventory_query_is_charged_as_an_orphan() {
        // Break caught: treating a DB inventory snapshot as a filesystem usage snapshot.
        let mut store = crate::Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let inventory = store
            .vault_inventory_rows()
            .expect("return owned inventory before vault lock");
        let root = tempfile::TempDir::new().expect("create vault parent");
        crate::apply_private_permissions(root.path()).expect("harden vault parent");
        let root = std::fs::canonicalize(root.path())
            .expect("canonicalize vault parent")
            .join("vault");
        let vault = crate::Vault::open(crate::VaultConfig {
            root,
            open_mode: crate::VaultOpenMode::CreateNew,
        })
        .expect("open vault");
        let first = b"a";
        let second = b"b";
        let first_digest = Sha256Digest::hash_reader(first.as_slice()).expect("first digest");
        let second_digest = Sha256Digest::hash_reader(second.as_slice()).expect("second digest");
        assert!(matches!(
            vault
                .put_reader(
                    first.as_slice(),
                    crate::VaultWriteBudget::new(1, 1).expect("legacy put budget"),
                )
                .expect("direct put after inventory query"),
            crate::PutOutcome::Stored(ref stored)
                if stored.digest == first_digest && stored.newly_published
        ));

        let outcome = vault
            .put_reader_accounted(
                second.as_slice(),
                crate::VaultWriteBudget::new(1, 1).expect("accounted put budget"),
                &inventory,
                crate::vault::VaultOrphanBudget::new(1).expect("one-byte orphan allowance"),
            )
            .expect("late direct put is counted, not mistaken for inventory authority");
        assert_eq!(
            outcome,
            crate::PutOutcome::QuotaRejected {
                digest: second_digest,
                byte_length: 1,
            }
        );
        assert!(vault.open_verified(first_digest).is_ok());
        assert!(matches!(
            vault.open_verified(second_digest),
            Err(HeleosError::NotFound)
        ));
    }

    #[cfg(unix)]
    #[test]
    fn fifo_source_open_process_helper() {
        let Some(path) = std::env::var_os("HELEOS_TEST_INTAKE_FIFO") else {
            return;
        };
        assert!(matches!(
            IntakeSource::open(Path::new(&path)),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[cfg(unix)]
    #[test]
    fn fifo_and_terminal_symlink_sources_fail_promptly_as_policy_denied() {
        use std::os::unix::fs::symlink;
        use std::process::Command;
        use std::time::{Duration, Instant};

        let root = tempfile::TempDir::new().expect("source fixture directory");
        let fifo = root.path().join("source.fifo");
        assert!(
            Command::new("mkfifo")
                .arg(&fifo)
                .status()
                .expect("run mkfifo")
                .success()
        );
        let mut child = Command::new(std::env::current_exe().expect("test executable"))
            .arg("--exact")
            .arg("ingest::job::tests::fifo_source_open_process_helper")
            .arg("--nocapture")
            .env("HELEOS_TEST_INTAKE_FIFO", &fifo)
            .spawn()
            .expect("spawn FIFO open helper");
        let deadline = Instant::now() + Duration::from_secs(2);
        let status = loop {
            if let Some(status) = child.try_wait().expect("poll FIFO helper") {
                break Some(status);
            }
            if Instant::now() >= deadline {
                child.kill().expect("kill blocked FIFO helper");
                child.wait().expect("reap blocked FIFO helper");
                break None;
            }
            std::thread::sleep(Duration::from_millis(10));
        };
        assert!(status.is_some_and(|status| status.success()));

        let target = root.path().join("target.pdf");
        std::fs::write(&target, b"%PDF").expect("write symlink target");
        let link = root.path().join("link.pdf");
        symlink(&target, &link).expect("create source symlink");
        assert!(matches!(
            IntakeSource::open(&link),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[cfg(unix)]
    #[test]
    fn unix_socket_source_fails_promptly_as_policy_denied() {
        use std::os::unix::net::UnixListener;
        use std::time::{Duration, Instant};

        let root = tempfile::TempDir::new().expect("socket fixture directory");
        let path = root.path().join("source.socket");
        let _listener = UnixListener::bind(&path).expect("bind source socket");
        let started = Instant::now();
        assert!(matches!(
            IntakeSource::open(&path),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn private_persisted_dtos_reject_wrong_schema_and_object_keys() {
        // Break caught: DB reconstruction accepting field-shaped but semantically alien JSON.
        let budget = IngestBudgetV1::new(7);
        let mut value = serde_json::to_value(budget).expect("budget JSON");
        value["schema"] = serde_json::Value::String("wrong".to_owned());
        assert!(serde_json::from_value::<IngestBudgetV1>(value).is_err());

        let object = StoredObjectV1 {
            digest: Sha256Digest::from_bytes([1; 32]),
            byte_length: 7,
            vault_key: "wrong".to_owned(),
        };
        let value = serde_json::to_value(object).expect("stored-object JSON");
        assert!(serde_json::from_value::<StoredObjectV1>(value).is_err());

        let event = IngestEventDetailV1::Conflict {
            schema: "wrong".to_owned(),
            authoritative_job_id: JobId::from_uuid(uuid(2)),
            mismatching_fields: vec!["project_id".to_owned()],
        };
        let value = serde_json::to_value(event).expect("event JSON");
        assert!(serde_json::from_value::<IngestEventDetailV1>(value).is_err());
    }

    #[test]
    fn persisted_source_display_requires_the_exact_encoder_image() {
        // Break caught: source-less resume accepting ambiguous/non-lossless display encodings.
        for hostile in ["utf8:%61", "utf8:%ff", "utf8:.", "utf8:..", "unix-bytes:61"] {
            let mut value = serde_json::to_value(valid_input()).expect("input JSON");
            value["source_display"] = serde_json::Value::String(hostile.to_owned());
            assert!(
                serde_json::from_value::<IngestInputV1>(value).is_err(),
                "accepted noncanonical safe display {hostile}"
            );
        }
    }

    #[test]
    fn persisted_source_display_deserialization_enforces_the_4096_byte_cap() {
        // Break caught: allocating an oversized persisted source marker before its field cap.
        let mut at_cap = serde_json::to_value(valid_input()).expect("input JSON");
        at_cap["source_display"] = serde_json::Value::String(format!("utf8:{}", "a".repeat(4091)));
        assert!(serde_json::from_value::<IngestInputV1>(at_cap).is_ok());

        let mut over_cap = serde_json::to_value(valid_input()).expect("input JSON");
        over_cap["source_display"] =
            serde_json::Value::String(format!("utf8:{}", "a".repeat(4092)));
        let error = serde_json::from_value::<IngestInputV1>(over_cap)
            .expect_err("4097-byte source marker must fail during string deserialization");
        assert!(
            error
                .to_string()
                .contains("string exceeds maximum of 4096 bytes")
        );
    }

    #[test]
    fn public_probe_provenance_rejects_unbounded_or_empty_text() {
        // Break caught: nested probe identity bypassing strict public DTO validation.
        let mut value = serde_json::to_value(provenance()).expect("provenance JSON");
        value["parser_name"] = serde_json::Value::String(String::new());
        assert!(serde_json::from_value::<PdfProbeProvenance>(value).is_err());
        let mut value = serde_json::to_value(provenance()).expect("provenance JSON");
        value["protocol_version"] = serde_json::Value::String("x".repeat(257));
        assert!(serde_json::from_value::<PdfProbeProvenance>(value).is_err());
    }

    #[test]
    fn task_six_evidence_rejects_zero_accepted_pages() {
        // Break caught: applying Task 5's valid zero-page response to Task 6 accepted intake.
        assert!(
            EvidenceManifestV1::new(
                Sha256Digest::from_bytes([1; 32]),
                7,
                Vec::new(),
                provenance(),
            )
            .is_err()
        );
    }

    #[test]
    fn checkpoint_and_event_cross_fields_bind_lengths_provenance_and_terminal_authority() {
        // Break caught: structurally valid persisted phases drifting from frozen job authority.
        let quarantine = IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason: IntakeQuarantineReasonV1::InputBytes {
                limit_bytes: PdfLimits::default().max_input_bytes,
                observed_bytes: PdfLimits::default().max_input_bytes + 1,
            },
            probe_provenance: None,
        };
        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::PreflightRejected {
                content_sha256: None,
                byte_length: PdfLimits::default().max_input_bytes + 2,
                quarantine: quarantine.clone(),
            },
        };
        assert!(checkpoint.validate().is_err());

        let event = IngestEventDetailV1::Quarantined {
            schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
            attempt: 1,
            content_sha256: None,
            byte_length: PdfLimits::default().max_input_bytes + 2,
            quarantine,
        };
        assert!(event.validate().is_err());

        let mut replay = accepted_receipt();
        replay.outcome = IngestOutcome::IdempotentReplay;
        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::Terminal {
                receipt: Box::new(replay),
            },
        };
        assert!(checkpoint.validate().is_err());

        let receipt = accepted_receipt();
        let evidence = receipt.evidence_manifest.expect("accepted evidence");
        let original = StoredObjectV1 {
            digest: evidence.manifest.original.sha256,
            byte_length: evidence.manifest.original.byte_length,
            vault_key: evidence.original_vault_key.clone(),
        };
        let manifest_object = StoredObjectV1 {
            digest: evidence.manifest_content_sha256,
            byte_length: evidence.manifest_byte_length,
            vault_key: evidence.manifest_vault_key.clone(),
        };
        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::ProcessingComplete {
                original,
                candidate: ProcessingCandidateV1::Accepted {
                    manifest: Box::new(evidence.manifest),
                    manifest_object,
                },
            },
        };
        let mut input = valid_input();
        input.expected_probe_provenance.parser_version = "2.0.0".to_owned();
        assert!(checkpoint.validate_for_input(&input).is_err());
    }

    #[test]
    fn persisted_candidate_and_checkpoint_jcs_shapes_are_frozen() {
        // Break caught: private allocation indirection changing persisted Serde/JCS bytes.
        let receipt = accepted_receipt();
        let evidence = receipt
            .evidence_manifest
            .as_ref()
            .expect("accepted evidence");
        let original = StoredObjectV1 {
            digest: evidence.manifest.original.sha256,
            byte_length: evidence.manifest.original.byte_length,
            vault_key: evidence.original_vault_key.clone(),
        };
        let manifest_object = StoredObjectV1 {
            digest: evidence.manifest_content_sha256,
            byte_length: evidence.manifest_byte_length,
            vault_key: evidence.manifest_vault_key.clone(),
        };
        let accepted = ProcessingCandidateV1::Accepted {
            manifest: Box::new(evidence.manifest.clone()),
            manifest_object,
        };
        let retained_quarantine = IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason: IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic),
            probe_provenance: Some(provenance()),
        };
        let quarantined = ProcessingCandidateV1::Quarantined {
            outcome: IngestOutcome::QuarantinedCorrupt,
            quarantine: retained_quarantine.clone(),
        };
        let input_quarantine = IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason: IntakeQuarantineReasonV1::InputBytes {
                limit_bytes: PdfLimits::default().max_input_bytes,
                observed_bytes: PdfLimits::default().max_input_bytes + 1,
            },
            probe_provenance: None,
        };
        let candidates = [accepted.clone(), quarantined.clone()];
        let checkpoints = [
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::PreflightRejected {
                    content_sha256: None,
                    byte_length: PdfLimits::default().max_input_bytes + 1,
                    quarantine: input_quarantine,
                },
            },
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished {
                    original: original.clone(),
                },
            },
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::ProcessingComplete {
                    original: original.clone(),
                    candidate: accepted,
                },
            },
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::ProcessingComplete {
                    original,
                    candidate: quarantined,
                },
            },
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::Terminal {
                    receipt: Box::new(receipt),
                },
            },
        ];

        let mut pins = Vec::new();
        for candidate in candidates {
            let bytes = crate::canonical_json(&candidate).expect("candidate JCS");
            let decoded: ProcessingCandidateV1 =
                serde_json::from_slice(&bytes).expect("candidate roundtrip");
            assert_eq!(
                crate::canonical_json(&decoded).expect("re-encode candidate"),
                bytes
            );
            pins.push(format!(
                "{}:{}",
                bytes.len(),
                Sha256Digest::hash_reader(bytes.as_slice()).expect("candidate pin")
            ));
        }
        for checkpoint in checkpoints {
            let bytes = crate::canonical_json(&checkpoint).expect("checkpoint JCS");
            let decoded: IngestCheckpointV1 =
                serde_json::from_slice(&bytes).expect("checkpoint roundtrip");
            assert_eq!(
                crate::canonical_json(&decoded).expect("re-encode checkpoint"),
                bytes
            );
            pins.push(format!(
                "{}:{}",
                bytes.len(),
                Sha256Digest::hash_reader(bytes.as_slice()).expect("checkpoint pin")
            ));
        }
        assert_eq!(
            pins,
            [
                "1610:c57ea2c417855452cf30830011a7e97178e143c512655cee2095d69947411f9c",
                "571:b8f65ff3538d9dc2997dfc61b06eb46d258da41c9018c5a39cd6a9b129d54303",
                "298:72ff1da1849be7bc02b92811343389976625f675faa199000fb0660e46dd411a",
                "282:ad6fc21d48cb6e3b72347d9369c01cc5762655fadfb0e4a7bc4653ce0729e674",
                "1909:a891922c985e60a566822076d5168c3d89b13f77f720ae66a1f4ccb2081887c7",
                "870:73b19fcce05a227fe907127d65d2a375e6ec318bf13bd7d1b6269f9c223f6939",
                "2616:bfa8ce9d648ae19ff2b9f062490f9a938d6b8682e11bce52bfef553bef927b06",
            ]
        );
    }

    #[test]
    fn actor_fixture_remains_strict() {
        assert!(ActorId::from_str("tester").is_ok());
    }
}
