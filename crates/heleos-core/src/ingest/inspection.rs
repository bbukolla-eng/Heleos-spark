use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    marker::PhantomData,
};

use serde::{
    Deserialize, Deserializer, Serialize,
    de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor},
};

use crate::{
    DocumentId, EvidenceId, HeleosError, IngestEventId, IngestOutcome, JobId, JobState,
    PageMetadata, PageTransform, PageUnit, PdfLimits, PdfProbeProvenance, ProjectId, Result,
    RevisionId, Sha256Digest, SheetId, Vault, canonical_document_ids, page_id,
};

use super::{
    IntakeQuarantineReasonV1, IntakeQuarantineV1,
    audit::JCS_SAFE_INTEGER_MAX,
    evidence::{
        EVIDENCE_MANIFEST_MEDIA_TYPE, EvidencePdfLimitsV1, MAX_EVIDENCE_MANIFEST_BYTES,
        PDF_MEDIA_TYPE, bounded_canonical_json,
    },
};

pub const FOUNDATION_INSPECTION_SCHEMA_V1: &str = "heleos.foundation-inspection/v1";
pub(crate) const MAX_FOUNDATION_INSPECTION_ROWS: usize = 100_000;
pub(crate) const MAX_FOUNDATION_INSPECTION_BYTES: usize = 16 * 1024 * 1024;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FoundationAdmissionState {
    Accepted,
    Quarantined,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FoundationJobTerminalReason {
    Completed,
    DeadlineExpired,
    AttemptLimit,
    InternalFailure,
    Cancelled,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct FoundationCounts {
    pub content_objects: u64,
    pub documents: u64,
    pub revisions: u64,
    pub project_documents: u64,
    pub sheets: u64,
    pub evidence_objects: u64,
    pub ingest_events: u64,
    pub jobs: u64,
    pub audit_events: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawFoundationCounts {
    content_objects: u64,
    documents: u64,
    revisions: u64,
    project_documents: u64,
    sheets: u64,
    evidence_objects: u64,
    ingest_events: u64,
    jobs: u64,
    audit_events: u64,
}

impl FoundationCounts {
    pub(crate) fn total_rows(&self) -> Result<u64> {
        [
            self.content_objects,
            self.documents,
            self.revisions,
            self.project_documents,
            self.sheets,
            self.evidence_objects,
            self.ingest_events,
            self.jobs,
            self.audit_events,
        ]
        .into_iter()
        .try_fold(0_u64, |total, count| {
            if count > JCS_SAFE_INTEGER_MAX {
                return Err(HeleosError::Integrity);
            }
            total.checked_add(count).ok_or(HeleosError::Integrity)
        })
    }

    pub(crate) fn validate(&self) -> Result<()> {
        if self.total_rows()?
            > u64::try_from(MAX_FOUNDATION_INSPECTION_ROWS).map_err(|_| HeleosError::Integrity)?
        {
            return Err(HeleosError::ResourceLimit);
        }
        Ok(())
    }
}

impl TryFrom<RawFoundationCounts> for FoundationCounts {
    type Error = HeleosError;

    fn try_from(raw: RawFoundationCounts) -> Result<Self> {
        let value = Self {
            content_objects: raw.content_objects,
            documents: raw.documents,
            revisions: raw.revisions,
            project_documents: raw.project_documents,
            sheets: raw.sheets,
            evidence_objects: raw.evidence_objects,
            ingest_events: raw.ingest_events,
            jobs: raw.jobs,
            audit_events: raw.audit_events,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for FoundationCounts {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawFoundationCounts::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct FoundationContentObject {
    pub sha256: Sha256Digest,
    pub byte_length: u64,
    pub media_type: String,
    pub admission_state: FoundationAdmissionState,
    pub vault_key: String,
    pub quarantine: Option<IntakeQuarantineV1>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawFoundationContentObject {
    sha256: Sha256Digest,
    byte_length: u64,
    #[serde(deserialize_with = "deserialize_text_256")]
    media_type: String,
    admission_state: FoundationAdmissionState,
    #[serde(deserialize_with = "deserialize_text_256")]
    vault_key: String,
    quarantine: Option<IntakeQuarantineV1>,
}

impl FoundationContentObject {
    pub(crate) fn validate(&self) -> Result<()> {
        let byte_limit = match self.media_type.as_str() {
            PDF_MEDIA_TYPE => PdfLimits::default().max_input_bytes,
            EVIDENCE_MANIFEST_MEDIA_TYPE => {
                u64::try_from(MAX_EVIDENCE_MANIFEST_BYTES).map_err(|_| HeleosError::Integrity)?
            }
            _ => return Err(HeleosError::Integrity),
        };
        let shape = match self.admission_state {
            FoundationAdmissionState::Accepted => self.quarantine.is_none(),
            FoundationAdmissionState::Quarantined => match &self.quarantine {
                Some(quarantine) if self.media_type == PDF_MEDIA_TYPE => {
                    quarantine.validate()?;
                    matches!(
                        &quarantine.reason,
                        IntakeQuarantineReasonV1::Pdf(_)
                            | IntakeQuarantineReasonV1::EvidenceManifestQuota
                    )
                }
                Some(_) | None => false,
            },
        };
        if self.byte_length > JCS_SAFE_INTEGER_MAX
            || self.byte_length > byte_limit
            || self.vault_key != Vault::object_key(self.sha256)
            || self.vault_key.len() > 256
            || !shape
        {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }
}

impl TryFrom<RawFoundationContentObject> for FoundationContentObject {
    type Error = HeleosError;

    fn try_from(raw: RawFoundationContentObject) -> Result<Self> {
        let value = Self {
            sha256: raw.sha256,
            byte_length: raw.byte_length,
            media_type: raw.media_type,
            admission_state: raw.admission_state,
            vault_key: raw.vault_key,
            quarantine: raw.quarantine,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for FoundationContentObject {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawFoundationContentObject::deserialize(deserializer)?)
            .map_err(de::Error::custom)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct FoundationSheet {
    pub sheet_id: SheetId,
    pub revision_id: RevisionId,
    pub index: u32,
    pub width_micropoints: u64,
    pub height_micropoints: u64,
    pub unit: PageUnit,
    pub rotation_degrees: u16,
    pub transform: PageTransform,
    pub parent_content_sha256: Sha256Digest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawFoundationSheet {
    sheet_id: SheetId,
    revision_id: RevisionId,
    index: u32,
    width_micropoints: u64,
    height_micropoints: u64,
    unit: PageUnit,
    rotation_degrees: u16,
    transform: PageTransform,
    parent_content_sha256: Sha256Digest,
}

impl FoundationSheet {
    fn validate(&self) -> Result<()> {
        let limits = PdfLimits::default();
        let axis_cap = u64::from(limits.max_page_axis_points)
            .checked_mul(1_000_000)
            .ok_or(HeleosError::Integrity)?;
        let transform = self.transform;
        if self.index >= limits.max_pages
            || self.revision_id != RevisionId::from(self.parent_content_sha256)
            || self.sheet_id != page_id(self.parent_content_sha256, self.index)
            || self.width_micropoints == 0
            || self.height_micropoints == 0
            || self.width_micropoints > axis_cap
            || self.height_micropoints > axis_cap
            || self.width_micropoints > JCS_SAFE_INTEGER_MAX
            || self.height_micropoints > JCS_SAFE_INTEGER_MAX
            || transform.tx_micropoints.unsigned_abs() > JCS_SAFE_INTEGER_MAX
            || transform.ty_micropoints.unsigned_abs() > JCS_SAFE_INTEGER_MAX
            || crate::pdf::geometry::expected_rotation_matrix(self.rotation_degrees)
                != Some((transform.m11, transform.m12, transform.m21, transform.m22))
        {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }
}

impl TryFrom<RawFoundationSheet> for FoundationSheet {
    type Error = HeleosError;

    fn try_from(raw: RawFoundationSheet) -> Result<Self> {
        let value = Self {
            sheet_id: raw.sheet_id,
            revision_id: raw.revision_id,
            index: raw.index,
            width_micropoints: raw.width_micropoints,
            height_micropoints: raw.height_micropoints,
            unit: raw.unit,
            rotation_degrees: raw.rotation_degrees,
            transform: raw.transform,
            parent_content_sha256: raw.parent_content_sha256,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for FoundationSheet {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawFoundationSheet::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct FoundationEvidenceContent {
    pub sha256: Sha256Digest,
    pub byte_length: u64,
    pub vault_key: String,
    pub media_type: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawFoundationEvidenceContent {
    sha256: Sha256Digest,
    byte_length: u64,
    #[serde(deserialize_with = "deserialize_text_256")]
    vault_key: String,
    #[serde(deserialize_with = "deserialize_text_256")]
    media_type: String,
}

impl FoundationEvidenceContent {
    fn validate(&self) -> Result<()> {
        let maximum = match self.media_type.as_str() {
            PDF_MEDIA_TYPE => PdfLimits::default().max_input_bytes,
            EVIDENCE_MANIFEST_MEDIA_TYPE => {
                u64::try_from(MAX_EVIDENCE_MANIFEST_BYTES).map_err(|_| HeleosError::Integrity)?
            }
            _ => return Err(HeleosError::Integrity),
        };
        if self.byte_length > maximum {
            return Err(HeleosError::ResourceLimit);
        }
        self.validate_media(&self.media_type)
    }

    fn validate_media(&self, expected_media_type: &str) -> Result<()> {
        if self.byte_length > JCS_SAFE_INTEGER_MAX
            || self.vault_key != Vault::object_key(self.sha256)
            || self.vault_key.len() > 256
            || self.media_type != expected_media_type
        {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }
}

impl TryFrom<RawFoundationEvidenceContent> for FoundationEvidenceContent {
    type Error = HeleosError;

    fn try_from(raw: RawFoundationEvidenceContent) -> Result<Self> {
        let value = Self {
            sha256: raw.sha256,
            byte_length: raw.byte_length,
            vault_key: raw.vault_key,
            media_type: raw.media_type,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for FoundationEvidenceContent {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawFoundationEvidenceContent::deserialize(deserializer)?)
            .map_err(de::Error::custom)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct FoundationEvidenceLineage {
    pub evidence_id: EvidenceId,
    pub originating_job_id: JobId,
    pub document_id: DocumentId,
    pub revision_id: RevisionId,
    pub original: FoundationEvidenceContent,
    pub manifest: FoundationEvidenceContent,
    pub extraction_method: String,
    pub requested_limits: EvidencePdfLimitsV1,
    pub probe_provenance: PdfProbeProvenance,
    pub review_state: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawFoundationEvidenceLineage {
    evidence_id: EvidenceId,
    originating_job_id: JobId,
    document_id: DocumentId,
    revision_id: RevisionId,
    original: FoundationEvidenceContent,
    manifest: FoundationEvidenceContent,
    #[serde(deserialize_with = "deserialize_text_256")]
    extraction_method: String,
    requested_limits: EvidencePdfLimitsV1,
    probe_provenance: PdfProbeProvenance,
    #[serde(deserialize_with = "deserialize_text_16")]
    review_state: String,
}

impl FoundationEvidenceLineage {
    pub(crate) fn validate(&self) -> Result<()> {
        self.original.validate_media(PDF_MEDIA_TYPE)?;
        self.manifest.validate_media(EVIDENCE_MANIFEST_MEDIA_TYPE)?;
        let (document_id, revision_id) = canonical_document_ids(self.original.sha256);
        if self.document_id != document_id
            || self.revision_id != revision_id
            || self.extraction_method != "heleos.pdf-probe/v1"
            || self.review_state != "accepted"
            || self.requested_limits != EvidencePdfLimitsV1::from(PdfLimits::default())
        {
            return Err(HeleosError::Integrity);
        }
        self.probe_provenance.validate()
    }
}

impl TryFrom<RawFoundationEvidenceLineage> for FoundationEvidenceLineage {
    type Error = HeleosError;

    fn try_from(raw: RawFoundationEvidenceLineage) -> Result<Self> {
        let value = Self {
            evidence_id: raw.evidence_id,
            originating_job_id: raw.originating_job_id,
            document_id: raw.document_id,
            revision_id: raw.revision_id,
            original: raw.original,
            manifest: raw.manifest,
            extraction_method: raw.extraction_method,
            requested_limits: raw.requested_limits,
            probe_provenance: raw.probe_provenance,
            review_state: raw.review_state,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for FoundationEvidenceLineage {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawFoundationEvidenceLineage::deserialize(deserializer)?)
            .map_err(de::Error::custom)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct FoundationIntakeEvent {
    pub ingest_event_id: IngestEventId,
    pub job_id: Option<JobId>,
    pub content_sha256: Option<Sha256Digest>,
    pub outcome: IngestOutcome,
    pub attempt: Option<u32>,
    pub terminal_at_ms: i64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawFoundationIntakeEvent {
    ingest_event_id: IngestEventId,
    job_id: Option<JobId>,
    content_sha256: Option<Sha256Digest>,
    outcome: IngestOutcome,
    attempt: Option<u32>,
    terminal_at_ms: i64,
}

impl FoundationIntakeEvent {
    pub(crate) fn validate(&self) -> Result<()> {
        let authoritative = !matches!(
            self.outcome,
            IngestOutcome::IdempotentReplay | IngestOutcome::DeniedConflict
        );
        if self.terminal_at_ms < 0
            || u64::try_from(self.terminal_at_ms).map_or(true, |value| value > JCS_SAFE_INTEGER_MAX)
            || (authoritative && (self.job_id.is_none() || !matches!(self.attempt, Some(1..=16))))
            || (!authoritative && self.attempt.is_some())
        {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }
}

impl TryFrom<RawFoundationIntakeEvent> for FoundationIntakeEvent {
    type Error = HeleosError;

    fn try_from(raw: RawFoundationIntakeEvent) -> Result<Self> {
        let value = Self {
            ingest_event_id: raw.ingest_event_id,
            job_id: raw.job_id,
            content_sha256: raw.content_sha256,
            outcome: raw.outcome,
            attempt: raw.attempt,
            terminal_at_ms: raw.terminal_at_ms,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for FoundationIntakeEvent {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawFoundationIntakeEvent::deserialize(deserializer)?)
            .map_err(de::Error::custom)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct FoundationJob {
    pub job_id: JobId,
    pub kind: String,
    pub state: JobState,
    pub attempt: u32,
    pub created_at_ms: i64,
    pub updated_at_ms: i64,
    pub terminal_reason: Option<FoundationJobTerminalReason>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawFoundationJob {
    job_id: JobId,
    #[serde(deserialize_with = "deserialize_text_16")]
    kind: String,
    state: JobState,
    attempt: u32,
    created_at_ms: i64,
    updated_at_ms: i64,
    terminal_reason: Option<FoundationJobTerminalReason>,
}

impl FoundationJob {
    pub(crate) fn validate(&self) -> Result<()> {
        let state_shape = match self.state {
            JobState::Queued => self.attempt == 0 && self.terminal_reason.is_none(),
            JobState::Running => (1..=16).contains(&self.attempt) && self.terminal_reason.is_none(),
            JobState::Interrupted => {
                (1..16).contains(&self.attempt) && self.terminal_reason.is_none()
            }
            JobState::Succeeded => {
                (1..=16).contains(&self.attempt)
                    && self.terminal_reason == Some(FoundationJobTerminalReason::Completed)
            }
            JobState::Failed => {
                (1..=16).contains(&self.attempt)
                    && matches!(
                        self.terminal_reason,
                        Some(
                            FoundationJobTerminalReason::DeadlineExpired
                                | FoundationJobTerminalReason::AttemptLimit
                                | FoundationJobTerminalReason::InternalFailure
                        )
                    )
            }
            JobState::Cancelled => {
                self.attempt <= 16
                    && self.terminal_reason == Some(FoundationJobTerminalReason::Cancelled)
            }
        };
        if self.kind != "pdf_ingest"
            || self.created_at_ms < 0
            || self.updated_at_ms < self.created_at_ms
            || u64::try_from(self.updated_at_ms).map_or(true, |value| value > JCS_SAFE_INTEGER_MAX)
            || !state_shape
        {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }
}

impl TryFrom<RawFoundationJob> for FoundationJob {
    type Error = HeleosError;

    fn try_from(raw: RawFoundationJob) -> Result<Self> {
        let value = Self {
            job_id: raw.job_id,
            kind: raw.kind,
            state: raw.state,
            attempt: raw.attempt,
            created_at_ms: raw.created_at_ms,
            updated_at_ms: raw.updated_at_ms,
            terminal_reason: raw.terminal_reason,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for FoundationJob {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawFoundationJob::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct FoundationInspection {
    pub schema: String,
    pub project_id: ProjectId,
    pub counts: FoundationCounts,
    pub content_objects: Vec<FoundationContentObject>,
    pub document_ids: Vec<DocumentId>,
    pub revision_ids: Vec<RevisionId>,
    pub sheets: Vec<FoundationSheet>,
    pub evidence: Vec<FoundationEvidenceLineage>,
    pub intake_events: Vec<FoundationIntakeEvent>,
    pub jobs: Vec<FoundationJob>,
}

pub(crate) struct FoundationInspectionParts {
    pub counts: FoundationCounts,
    pub content_objects: Vec<FoundationContentObject>,
    pub document_ids: Vec<DocumentId>,
    pub revision_ids: Vec<RevisionId>,
    pub sheets: Vec<FoundationSheet>,
    pub evidence: Vec<FoundationEvidenceLineage>,
    pub intake_events: Vec<FoundationIntakeEvent>,
    pub jobs: Vec<FoundationJob>,
}

impl FoundationInspection {
    pub(crate) fn new(project_id: ProjectId, parts: FoundationInspectionParts) -> Result<Self> {
        let value = Self {
            schema: FOUNDATION_INSPECTION_SCHEMA_V1.to_owned(),
            project_id,
            counts: parts.counts,
            content_objects: parts.content_objects,
            document_ids: parts.document_ids,
            revision_ids: parts.revision_ids,
            sheets: parts.sheets,
            evidence: parts.evidence,
            intake_events: parts.intake_events,
            jobs: parts.jobs,
        };
        value.validate()?;
        Ok(value)
    }

    pub(crate) fn validate(&self) -> Result<()> {
        self.counts.validate()?;
        if self.schema != FOUNDATION_INSPECTION_SCHEMA_V1
            || usize_count(self.counts.content_objects)? != self.content_objects.len()
            || usize_count(self.counts.documents)? != self.document_ids.len()
            || usize_count(self.counts.revisions)? != self.revision_ids.len()
            || usize_count(self.counts.project_documents)? != self.document_ids.len()
            || usize_count(self.counts.sheets)? != self.sheets.len()
            || usize_count(self.counts.evidence_objects)? != self.evidence.len()
            || usize_count(self.counts.ingest_events)? != self.intake_events.len()
            || usize_count(self.counts.jobs)? != self.jobs.len()
        {
            return Err(HeleosError::Integrity);
        }

        let nested_rows = [
            self.content_objects.len(),
            self.document_ids.len(),
            self.revision_ids.len(),
            self.sheets.len(),
            self.evidence.len(),
            self.intake_events.len(),
            self.jobs.len(),
        ]
        .into_iter()
        .try_fold(0_usize, |total, count| {
            total.checked_add(count).ok_or(HeleosError::Integrity)
        })?;
        if nested_rows > MAX_FOUNDATION_INSPECTION_ROWS {
            return Err(HeleosError::ResourceLimit);
        }

        ensure_strictly_sorted_by(&self.content_objects, |value| value.sha256)?;
        ensure_strictly_sorted_by(&self.document_ids, |value| *value.as_digest())?;
        ensure_strictly_sorted_by(&self.revision_ids, |value| *value.as_digest())?;
        ensure_strictly_sorted_by(&self.sheets, |value| {
            (
                *value.revision_id.as_digest(),
                value.index,
                *value.sheet_id.as_digest(),
            )
        })?;
        ensure_strictly_sorted_by(&self.evidence, |value| {
            (
                *value.revision_id.as_digest(),
                value.manifest.sha256,
                *value.evidence_id.as_uuid().as_bytes(),
            )
        })?;
        ensure_strictly_sorted_by(&self.intake_events, |value| {
            (
                value.terminal_at_ms,
                *value.ingest_event_id.as_uuid().as_bytes(),
            )
        })?;
        ensure_strictly_sorted_by(&self.jobs, |value| {
            (value.created_at_ms, *value.job_id.as_uuid().as_bytes())
        })?;

        for content in &self.content_objects {
            content.validate()?;
        }
        for evidence in &self.evidence {
            evidence.validate()?;
        }
        for event in &self.intake_events {
            event.validate()?;
        }
        for job in &self.jobs {
            job.validate()?;
        }

        let documents = self
            .document_ids
            .iter()
            .map(|value| *value.as_digest())
            .collect::<Vec<_>>();
        let revisions = self
            .revision_ids
            .iter()
            .map(|value| *value.as_digest())
            .collect::<Vec<_>>();
        if documents != revisions {
            return Err(HeleosError::Integrity);
        }

        let content_by_digest = self
            .content_objects
            .iter()
            .map(|content| (content.sha256, content))
            .collect::<BTreeMap<_, _>>();
        let mut required_accepted = BTreeMap::new();
        for revision in &self.revision_ids {
            insert_required_media(
                &mut required_accepted,
                *revision.as_digest(),
                PDF_MEDIA_TYPE,
            )?;
        }
        let mut evidence_revisions = BTreeSet::new();
        for evidence in &self.evidence {
            if self
                .document_ids
                .binary_search_by(|value| value.as_digest().cmp(evidence.document_id.as_digest()))
                .is_err()
                || self
                    .revision_ids
                    .binary_search_by(|value| {
                        value.as_digest().cmp(evidence.revision_id.as_digest())
                    })
                    .is_err()
            {
                return Err(HeleosError::Integrity);
            }
            if !evidence_revisions.insert(*evidence.revision_id.as_digest()) {
                return Err(HeleosError::Integrity);
            }
            insert_required_media(
                &mut required_accepted,
                evidence.original.sha256,
                PDF_MEDIA_TYPE,
            )?;
            insert_required_media(
                &mut required_accepted,
                evidence.manifest.sha256,
                EVIDENCE_MANIFEST_MEDIA_TYPE,
            )?;
            let original = content_by_digest
                .get(&evidence.original.sha256)
                .ok_or(HeleosError::Integrity)?;
            let manifest = content_by_digest
                .get(&evidence.manifest.sha256)
                .ok_or(HeleosError::Integrity)?;
            if original.byte_length != evidence.original.byte_length
                || original.vault_key != evidence.original.vault_key
                || original.media_type != evidence.original.media_type
                || manifest.byte_length != evidence.manifest.byte_length
                || manifest.vault_key != evidence.manifest.vault_key
                || manifest.media_type != evidence.manifest.media_type
            {
                return Err(HeleosError::Integrity);
            }
        }
        let revision_digests = self
            .revision_ids
            .iter()
            .map(|revision| *revision.as_digest())
            .collect::<BTreeSet<_>>();
        if evidence_revisions != revision_digests {
            return Err(HeleosError::Integrity);
        }

        let mut grouped_pages: BTreeMap<(Sha256Digest, Sha256Digest), Vec<PageMetadata>> =
            BTreeMap::new();
        for sheet in &self.sheets {
            if sheet.parent_content_sha256 != *sheet.revision_id.as_digest()
                || self
                    .revision_ids
                    .binary_search_by(|value| value.as_digest().cmp(sheet.revision_id.as_digest()))
                    .is_err()
            {
                return Err(HeleosError::Integrity);
            }
            grouped_pages
                .entry((*sheet.revision_id.as_digest(), sheet.parent_content_sha256))
                .or_default()
                .push(PageMetadata {
                    index: sheet.index,
                    page_id: sheet.sheet_id,
                    width_micropoints: sheet.width_micropoints,
                    height_micropoints: sheet.height_micropoints,
                    unit: sheet.unit,
                    rotation_degrees: sheet.rotation_degrees,
                    transform: sheet.transform,
                });
        }
        if grouped_pages
            .keys()
            .map(|(revision, _)| *revision)
            .collect::<BTreeSet<_>>()
            != revision_digests
        {
            return Err(HeleosError::Integrity);
        }
        for ((_, parent), pages) in grouped_pages {
            crate::pdf::geometry::validate_page_metadata(&pages, parent, PdfLimits::default())?;
        }

        let required_manifest_digests = self
            .evidence
            .iter()
            .map(|evidence| evidence.manifest.sha256)
            .collect::<BTreeSet<_>>();
        for (digest, expected_media_type) in required_accepted {
            let content = content_by_digest
                .get(&digest)
                .ok_or(HeleosError::Integrity)?;
            if content.admission_state != FoundationAdmissionState::Accepted
                || content.media_type != expected_media_type
            {
                return Err(HeleosError::Integrity);
            }
        }
        for content in &self.content_objects {
            if content.admission_state == FoundationAdmissionState::Accepted
                && content.media_type == PDF_MEDIA_TYPE
                && self
                    .revision_ids
                    .binary_search_by_key(&content.sha256, |value| *value.as_digest())
                    .is_err()
            {
                return Err(HeleosError::Integrity);
            }
            if content.admission_state == FoundationAdmissionState::Accepted
                && content.media_type == EVIDENCE_MANIFEST_MEDIA_TYPE
                && !required_manifest_digests.contains(&content.sha256)
            {
                return Err(HeleosError::Integrity);
            }
        }

        let jobs = self
            .jobs
            .iter()
            .map(|job| *job.job_id.as_uuid().as_bytes())
            .collect::<BTreeSet<_>>();
        for event in &self.intake_events {
            if let Some(job_id) = event.job_id
                && !jobs.contains(job_id.as_uuid().as_bytes())
            {
                return Err(HeleosError::Integrity);
            }
            if let Some(content_sha256) = event.content_sha256
                && !content_by_digest.contains_key(&content_sha256)
            {
                return Err(HeleosError::Integrity);
            }
        }
        for evidence in &self.evidence {
            if !jobs.contains(evidence.originating_job_id.as_uuid().as_bytes()) {
                return Err(HeleosError::Integrity);
            }
        }
        let event_content = self
            .intake_events
            .iter()
            .filter_map(|event| event.content_sha256)
            .collect::<BTreeSet<_>>();
        for content in &self.content_objects {
            if content.admission_state == FoundationAdmissionState::Quarantined
                && !event_content.contains(&content.sha256)
            {
                return Err(HeleosError::Integrity);
            }
        }

        bounded_canonical_json(self, MAX_FOUNDATION_INSPECTION_BYTES)?;
        Ok(())
    }
}

impl<'de> Deserialize<'de> for FoundationInspection {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        deserializer.deserialize_map(FoundationInspectionVisitor)
    }
}

#[derive(Deserialize)]
#[serde(field_identifier, rename_all = "snake_case")]
enum FoundationInspectionField {
    Schema,
    ProjectId,
    Counts,
    ContentObjects,
    DocumentIds,
    RevisionIds,
    Sheets,
    Evidence,
    IntakeEvents,
    Jobs,
}

struct FoundationInspectionVisitor;

impl<'de> Visitor<'de> for FoundationInspectionVisitor {
    type Value = FoundationInspection;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("a strict heleos.foundation-inspection/v1 object")
    }

    fn visit_map<A: MapAccess<'de>>(
        self,
        mut map: A,
    ) -> std::result::Result<Self::Value, A::Error> {
        let mut remaining = MAX_FOUNDATION_INSPECTION_ROWS;
        let mut schema = None;
        let mut project_id = None;
        let mut counts = None;
        let mut content_objects = None;
        let mut document_ids = None;
        let mut revision_ids = None;
        let mut sheets = None;
        let mut evidence = None;
        let mut intake_events = None;
        let mut jobs = None;

        while let Some(field) = map.next_key::<FoundationInspectionField>()? {
            match field {
                FoundationInspectionField::Schema => {
                    reject_duplicate(&schema, "schema")?;
                    schema = Some(map.next_value_seed(BoundedStringSeed::<256>)?);
                }
                FoundationInspectionField::ProjectId => {
                    reject_duplicate(&project_id, "project_id")?;
                    project_id = Some(map.next_value()?);
                }
                FoundationInspectionField::Counts => {
                    reject_duplicate(&counts, "counts")?;
                    counts = Some(map.next_value()?);
                }
                FoundationInspectionField::ContentObjects => {
                    reject_duplicate(&content_objects, "content_objects")?;
                    content_objects =
                        Some(map.next_value_seed(
                            BoundedVecSeed::<FoundationContentObject>::new(&mut remaining),
                        )?);
                }
                FoundationInspectionField::DocumentIds => {
                    reject_duplicate(&document_ids, "document_ids")?;
                    document_ids = Some(
                        map.next_value_seed(BoundedVecSeed::<DocumentId>::new(&mut remaining))?,
                    );
                }
                FoundationInspectionField::RevisionIds => {
                    reject_duplicate(&revision_ids, "revision_ids")?;
                    revision_ids = Some(
                        map.next_value_seed(BoundedVecSeed::<RevisionId>::new(&mut remaining))?,
                    );
                }
                FoundationInspectionField::Sheets => {
                    reject_duplicate(&sheets, "sheets")?;
                    sheets =
                        Some(map.next_value_seed(BoundedVecSeed::<FoundationSheet>::new(
                            &mut remaining,
                        ))?);
                }
                FoundationInspectionField::Evidence => {
                    reject_duplicate(&evidence, "evidence")?;
                    evidence =
                        Some(map.next_value_seed(
                            BoundedVecSeed::<FoundationEvidenceLineage>::new(&mut remaining),
                        )?);
                }
                FoundationInspectionField::IntakeEvents => {
                    reject_duplicate(&intake_events, "intake_events")?;
                    intake_events = Some(map.next_value_seed(BoundedVecSeed::<
                        FoundationIntakeEvent,
                    >::new(
                        &mut remaining
                    ))?);
                }
                FoundationInspectionField::Jobs => {
                    reject_duplicate(&jobs, "jobs")?;
                    jobs = Some(
                        map.next_value_seed(BoundedVecSeed::<FoundationJob>::new(&mut remaining))?,
                    );
                }
            }
        }

        let value = FoundationInspection {
            schema: schema.ok_or_else(|| de::Error::missing_field("schema"))?,
            project_id: project_id.ok_or_else(|| de::Error::missing_field("project_id"))?,
            counts: counts.ok_or_else(|| de::Error::missing_field("counts"))?,
            content_objects: content_objects
                .ok_or_else(|| de::Error::missing_field("content_objects"))?,
            document_ids: document_ids.ok_or_else(|| de::Error::missing_field("document_ids"))?,
            revision_ids: revision_ids.ok_or_else(|| de::Error::missing_field("revision_ids"))?,
            sheets: sheets.ok_or_else(|| de::Error::missing_field("sheets"))?,
            evidence: evidence.ok_or_else(|| de::Error::missing_field("evidence"))?,
            intake_events: intake_events
                .ok_or_else(|| de::Error::missing_field("intake_events"))?,
            jobs: jobs.ok_or_else(|| de::Error::missing_field("jobs"))?,
        };
        value.validate().map_err(de::Error::custom)?;
        Ok(value)
    }
}

struct BoundedVecSeed<'a, T> {
    remaining: &'a mut usize,
    marker: PhantomData<T>,
}

impl<'a, T> BoundedVecSeed<'a, T> {
    const fn new(remaining: &'a mut usize) -> Self {
        Self {
            remaining,
            marker: PhantomData,
        }
    }
}

impl<'de, T: Deserialize<'de>> DeserializeSeed<'de> for BoundedVecSeed<'_, T> {
    type Value = Vec<T>;

    fn deserialize<D: Deserializer<'de>>(
        self,
        deserializer: D,
    ) -> std::result::Result<Self::Value, D::Error> {
        deserializer.deserialize_seq(BoundedVecVisitor::<T> {
            remaining: self.remaining,
            marker: PhantomData,
        })
    }
}

struct BoundedVecVisitor<'a, T> {
    remaining: &'a mut usize,
    marker: PhantomData<T>,
}

impl<'de, T: Deserialize<'de>> Visitor<'de> for BoundedVecVisitor<'_, T> {
    type Value = Vec<T>;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("an inspection sequence within the shared 100000-row cap")
    }

    fn visit_seq<A: SeqAccess<'de>>(
        self,
        mut sequence: A,
    ) -> std::result::Result<Self::Value, A::Error> {
        if sequence
            .size_hint()
            .is_some_and(|hint| hint > *self.remaining)
        {
            return Err(de::Error::custom(
                "inspection rows exceed maximum of 100000",
            ));
        }
        let maximum = *self.remaining;
        let mut values = Vec::with_capacity(sequence.size_hint().unwrap_or(0).min(maximum));
        while values.len() < maximum {
            match sequence.next_element()? {
                Some(value) => {
                    values.push(value);
                    *self.remaining -= 1;
                }
                None => return Ok(values),
            }
        }
        if sequence.next_element::<de::IgnoredAny>()?.is_some() {
            return Err(de::Error::custom(
                "inspection rows exceed maximum of 100000",
            ));
        }
        Ok(values)
    }
}

struct BoundedStringSeed<const MAXIMUM: usize>;

impl<'de, const MAXIMUM: usize> DeserializeSeed<'de> for BoundedStringSeed<MAXIMUM> {
    type Value = String;

    fn deserialize<D: Deserializer<'de>>(
        self,
        deserializer: D,
    ) -> std::result::Result<Self::Value, D::Error> {
        super::deserialize_bounded_string::<D, MAXIMUM>(deserializer)
    }
}

fn reject_duplicate<E: de::Error, T>(
    value: &Option<T>,
    field: &'static str,
) -> std::result::Result<(), E> {
    if value.is_some() {
        return Err(E::duplicate_field(field));
    }
    Ok(())
}

fn ensure_strictly_sorted_by<T, K: Ord>(values: &[T], mut key: impl FnMut(&T) -> K) -> Result<()> {
    let mut previous = None;
    for value in values {
        let current = key(value);
        if previous
            .as_ref()
            .is_some_and(|previous| previous >= &current)
        {
            return Err(HeleosError::Integrity);
        }
        previous = Some(current);
    }
    Ok(())
}

fn insert_required_media<'a>(
    required: &mut BTreeMap<Sha256Digest, &'a str>,
    digest: Sha256Digest,
    media_type: &'a str,
) -> Result<()> {
    if required
        .insert(digest, media_type)
        .is_some_and(|previous| previous != media_type)
    {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn usize_count(value: u64) -> Result<usize> {
    usize::try_from(value).map_err(|_| HeleosError::Integrity)
}

fn deserialize_text_16<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<String, D::Error> {
    super::deserialize_bounded_string::<D, 16>(deserializer)
}

fn deserialize_text_256<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<String, D::Error> {
    super::deserialize_bounded_string::<D, 256>(deserializer)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{INTAKE_QUARANTINE_SCHEMA_V1, IntakeQuarantineReasonV1, PdfQuarantineReason};

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

    fn quarantine(reason: IntakeQuarantineReasonV1) -> IntakeQuarantineV1 {
        let probe_provenance = match reason {
            IntakeQuarantineReasonV1::Pdf(_) | IntakeQuarantineReasonV1::EvidenceManifestQuota => {
                Some(provenance())
            }
            IntakeQuarantineReasonV1::InputBytes { .. }
            | IntakeQuarantineReasonV1::OriginalRetentionQuota
            | IntakeQuarantineReasonV1::ProjectAssociationQuota => None,
        };
        IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason,
            probe_provenance,
        }
    }

    fn null_array(count: usize) -> String {
        let mut json = String::with_capacity(count.saturating_mul(5).saturating_add(2));
        json.push('[');
        for index in 0..count {
            if index != 0 {
                json.push(',');
            }
            json.push_str("null");
        }
        json.push(']');
        json
    }

    #[test]
    fn inspection_deserializer_enforces_one_shared_exact_row_cap() {
        // Break caught: each inspection vector receiving an independent 100,000-row budget.
        let mut remaining = MAX_FOUNDATION_INSPECTION_ROWS;
        let first = null_array(50_000);
        let second = null_array(50_000);
        let mut deserializer = serde_json::Deserializer::from_str(&first);
        let values = BoundedVecSeed::<serde_json::Value>::new(&mut remaining)
            .deserialize(&mut deserializer)
            .expect("first half of exact cap");
        assert_eq!(values.len(), 50_000);
        let mut deserializer = serde_json::Deserializer::from_str(&second);
        let values = BoundedVecSeed::<serde_json::Value>::new(&mut remaining)
            .deserialize(&mut deserializer)
            .expect("second half of exact cap");
        assert_eq!(values.len(), 50_000);
        assert_eq!(remaining, 0);

        let sentinel = null_array(1);
        let mut deserializer = serde_json::Deserializer::from_str(&sentinel);
        assert!(
            BoundedVecSeed::<serde_json::Value>::new(&mut remaining)
                .deserialize(&mut deserializer)
                .is_err()
        );
    }

    #[test]
    fn inspection_writer_accepts_exactly_sixteen_mib_and_rejects_the_next_byte() {
        // Break caught: an off-by-one bounded canonical writer or an untested oversized image.
        let at_cap = "x".repeat(MAX_FOUNDATION_INSPECTION_BYTES - 2);
        let bytes = bounded_canonical_json(&at_cap, MAX_FOUNDATION_INSPECTION_BYTES)
            .expect("exact 16 MiB canonical value");
        assert_eq!(bytes.len(), MAX_FOUNDATION_INSPECTION_BYTES);

        let over_cap = "x".repeat(MAX_FOUNDATION_INSPECTION_BYTES - 1);
        assert!(matches!(
            bounded_canonical_json(&over_cap, MAX_FOUNDATION_INSPECTION_BYTES),
            Err(HeleosError::ResourceLimit)
        ));
    }

    #[test]
    fn inspection_content_accepts_only_valid_sticky_quarantines() {
        // Break caught: preflight-only, non-retained quarantine reasons entering inspection.
        let digest = Sha256Digest::from_bytes([9; 32]);
        let content = |reason| FoundationContentObject {
            sha256: digest,
            byte_length: 7,
            media_type: PDF_MEDIA_TYPE.to_owned(),
            admission_state: FoundationAdmissionState::Quarantined,
            vault_key: Vault::object_key(digest),
            quarantine: Some(quarantine(reason)),
        };

        for reason in [
            IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic),
            IntakeQuarantineReasonV1::EvidenceManifestQuota,
        ] {
            let encoded = serde_json::to_value(content(reason)).expect("sticky quarantine JSON");
            serde_json::from_value::<FoundationContentObject>(encoded)
                .expect("valid sticky quarantine");
        }

        for reason in [
            IntakeQuarantineReasonV1::InputBytes {
                limit_bytes: PdfLimits::default().max_input_bytes,
                observed_bytes: PdfLimits::default().max_input_bytes + 1,
            },
            IntakeQuarantineReasonV1::OriginalRetentionQuota,
            IntakeQuarantineReasonV1::ProjectAssociationQuota,
        ] {
            let encoded =
                serde_json::to_value(content(reason)).expect("non-sticky quarantine JSON");
            assert!(serde_json::from_value::<FoundationContentObject>(encoded).is_err());
        }
    }

    #[test]
    fn inspection_canonical_writer_rejects_sixteen_mib_plus_one_output() {
        // Break caught: building an unbounded canonical inspection buffer after row validation.
        let jobs = (1_u128..=100_000)
            .map(|value| FoundationJob {
                job_id: JobId::from_uuid(uuid::Uuid::from_u128(
                    (4_u128 << 76) | (2_u128 << 62) | value,
                )),
                kind: "pdf_ingest".to_owned(),
                state: JobState::Queued,
                attempt: 0,
                created_at_ms: i64::try_from(JCS_SAFE_INTEGER_MAX).expect("JCS time"),
                updated_at_ms: i64::try_from(JCS_SAFE_INTEGER_MAX).expect("JCS time"),
                terminal_reason: None,
            })
            .collect::<Vec<_>>();
        let result = FoundationInspection::new(
            ProjectId::from_uuid(uuid::Uuid::from_u128(
                (4_u128 << 76) | (2_u128 << 62) | 100_001,
            )),
            FoundationInspectionParts {
                counts: FoundationCounts {
                    content_objects: 0,
                    documents: 0,
                    revisions: 0,
                    project_documents: 0,
                    sheets: 0,
                    evidence_objects: 0,
                    ingest_events: 0,
                    jobs: 100_000,
                    audit_events: 0,
                },
                content_objects: Vec::new(),
                document_ids: Vec::new(),
                revision_ids: Vec::new(),
                sheets: Vec::new(),
                evidence: Vec::new(),
                intake_events: Vec::new(),
                jobs,
            },
        );
        assert!(
            matches!(result, Err(HeleosError::ResourceLimit)),
            "unexpected result: {:?}",
            result.as_ref().err()
        );
    }
}
