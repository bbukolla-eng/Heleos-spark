use std::str::FromStr;

use serde::{Deserialize, Deserializer, Serialize, de};
use serde_json::Value as JsonValue;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::{ActorId, HeleosError, ProjectId, Result, Sha256Digest, canonical_json};

pub const AUDIT_CHAIN_REPORT_SCHEMA_V1: &str = "heleos.audit-chain-report/v1";
pub(crate) const MAX_AUDIT_FINDINGS: usize = 128;
pub(crate) const JCS_SAFE_INTEGER_MAX: u64 = 9_007_199_254_740_991;
const AUDIT_DOMAIN_V1: &[u8] = b"heleos-audit-event-v1\0";

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(transparent)]
pub struct AuditEventId(Uuid);

impl AuditEventId {
    pub const fn from_uuid(value: Uuid) -> Self {
        Self(value)
    }

    pub const fn as_uuid(&self) -> &Uuid {
        &self.0
    }
}

impl From<Uuid> for AuditEventId {
    fn from(value: Uuid) -> Self {
        Self::from_uuid(value)
    }
}

impl FromStr for AuditEventId {
    type Err = HeleosError;

    fn from_str(value: &str) -> Result<Self> {
        let parsed = Uuid::parse_str(value).map_err(|_| HeleosError::InvalidId)?;
        if parsed.hyphenated().to_string() != value {
            return Err(HeleosError::InvalidId);
        }
        Ok(Self::from_uuid(parsed))
    }
}

impl<'de> Deserialize<'de> for AuditEventId {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let value = String::deserialize(deserializer)?;
        Self::from_str(&value).map_err(de::Error::custom)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AuditAction {
    ProjectCreated,
    JobCreated,
    JobStarted,
    JobCheckpointed,
    JobInterrupted,
    JobResumed,
    JobSucceeded,
    JobFailed,
    IngestAccepted,
    IngestQuarantined,
    IngestReplayed,
    IngestConflictDenied,
    EvidenceCreated,
}

impl AuditAction {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::ProjectCreated => "project_created",
            Self::JobCreated => "job_created",
            Self::JobStarted => "job_started",
            Self::JobCheckpointed => "job_checkpointed",
            Self::JobInterrupted => "job_interrupted",
            Self::JobResumed => "job_resumed",
            Self::JobSucceeded => "job_succeeded",
            Self::JobFailed => "job_failed",
            Self::IngestAccepted => "ingest_accepted",
            Self::IngestQuarantined => "ingest_quarantined",
            Self::IngestReplayed => "ingest_replayed",
            Self::IngestConflictDenied => "ingest_conflict_denied",
            Self::EvidenceCreated => "evidence_created",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "project_created" => Self::ProjectCreated,
            "job_created" => Self::JobCreated,
            "job_started" => Self::JobStarted,
            "job_checkpointed" => Self::JobCheckpointed,
            "job_interrupted" => Self::JobInterrupted,
            "job_resumed" => Self::JobResumed,
            "job_succeeded" => Self::JobSucceeded,
            "job_failed" => Self::JobFailed,
            "ingest_accepted" => Self::IngestAccepted,
            "ingest_quarantined" => Self::IngestQuarantined,
            "ingest_replayed" => Self::IngestReplayed,
            "ingest_conflict_denied" => Self::IngestConflictDenied,
            "evidence_created" => Self::EvidenceCreated,
            _ => return None,
        })
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AuditSubjectType {
    Project,
    Job,
    IngestAttempt,
    Evidence,
}

impl AuditSubjectType {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::Project => "project",
            Self::Job => "job",
            Self::IngestAttempt => "ingest_attempt",
            Self::Evidence => "evidence",
        }
    }

    fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "project" => Self::Project,
            "job" => Self::Job,
            "ingest_attempt" => Self::IngestAttempt,
            "evidence" => Self::Evidence,
            _ => return None,
        })
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AuditEvent {
    pub id: AuditEventId,
    pub sequence: u64,
    pub project_id: Option<ProjectId>,
    pub actor: ActorId,
    pub action: AuditAction,
    pub subject_type: AuditSubjectType,
    pub subject_id: String,
    pub before: JsonValue,
    pub after: JsonValue,
    pub reason: String,
    pub occurred_at_ms: i64,
    pub previous_hash: Sha256Digest,
    pub event_hash: Sha256Digest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawAuditEvent {
    id: AuditEventId,
    sequence: u64,
    project_id: Option<ProjectId>,
    actor: ActorId,
    action: AuditAction,
    subject_type: AuditSubjectType,
    #[serde(deserialize_with = "deserialize_subject_id")]
    subject_id: String,
    before: JsonValue,
    after: JsonValue,
    #[serde(deserialize_with = "deserialize_reason")]
    reason: String,
    occurred_at_ms: i64,
    previous_hash: Sha256Digest,
    event_hash: Sha256Digest,
}

fn deserialize_subject_id<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<String, D::Error> {
    super::deserialize_bounded_string::<D, 256>(deserializer)
}

fn deserialize_reason<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<String, D::Error> {
    super::deserialize_bounded_string::<D, 1024>(deserializer)
}

impl TryFrom<RawAuditEvent> for AuditEvent {
    type Error = HeleosError;

    fn try_from(raw: RawAuditEvent) -> Result<Self> {
        let stored_hash = raw.event_hash;
        let event = Self::build(AuditEventInput {
            id: raw.id,
            sequence: raw.sequence,
            project_id: raw.project_id,
            actor: raw.actor,
            action: raw.action,
            subject_type: raw.subject_type,
            subject_id: raw.subject_id,
            before: raw.before,
            after: raw.after,
            reason: raw.reason,
            occurred_at_ms: raw.occurred_at_ms,
            previous_hash: raw.previous_hash,
        })?;
        if event.event_hash != stored_hash {
            return Err(HeleosError::Integrity);
        }
        Ok(event)
    }
}

impl<'de> Deserialize<'de> for AuditEvent {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let raw = RawAuditEvent::deserialize(deserializer)?;
        Self::try_from(raw).map_err(de::Error::custom)
    }
}

#[derive(Serialize)]
struct AuditHashDocumentV1<'a> {
    action: AuditAction,
    actor: &'a ActorId,
    after: &'a JsonValue,
    before: &'a JsonValue,
    event_id: AuditEventId,
    occurred_at_ms: i64,
    previous_hash: Sha256Digest,
    project_id: Option<ProjectId>,
    reason: &'a str,
    sequence: u64,
    subject_id: &'a str,
    subject_type: AuditSubjectType,
}

impl AuditEvent {
    pub(crate) fn build(input: AuditEventInput) -> Result<Self> {
        validate_audit_text(&input.subject_id, 256, false)?;
        validate_audit_text(&input.reason, 1024, true)?;
        validate_timestamp(input.occurred_at_ms)?;
        if input.sequence == 0 || input.sequence > JCS_SAFE_INTEGER_MAX {
            return Err(HeleosError::Integrity);
        }
        ensure_canonical_value_size(&input.before)?;
        ensure_canonical_value_size(&input.after)?;
        let mut event = Self {
            id: input.id,
            sequence: input.sequence,
            project_id: input.project_id,
            actor: input.actor,
            action: input.action,
            subject_type: input.subject_type,
            subject_id: input.subject_id,
            before: input.before,
            after: input.after,
            reason: input.reason,
            occurred_at_ms: input.occurred_at_ms,
            previous_hash: input.previous_hash,
            event_hash: Sha256Digest::from_bytes([0; 32]),
        };
        event.event_hash = event.recomputed_hash()?;
        Ok(event)
    }

    pub(crate) fn recomputed_hash(&self) -> Result<Sha256Digest> {
        let document = AuditHashDocumentV1 {
            action: self.action,
            actor: &self.actor,
            after: &self.after,
            before: &self.before,
            event_id: self.id,
            occurred_at_ms: self.occurred_at_ms,
            previous_hash: self.previous_hash,
            project_id: self.project_id,
            reason: &self.reason,
            sequence: self.sequence,
            subject_id: &self.subject_id,
            subject_type: self.subject_type,
        };
        let bytes = canonical_json(&document)?;
        let mut hasher = Sha256::new();
        hasher.update(AUDIT_DOMAIN_V1);
        hasher.update(bytes);
        Ok(Sha256Digest::from_bytes(hasher.finalize().into()))
    }
}

pub(crate) struct AuditEventInput {
    pub id: AuditEventId,
    pub sequence: u64,
    pub project_id: Option<ProjectId>,
    pub actor: ActorId,
    pub action: AuditAction,
    pub subject_type: AuditSubjectType,
    pub subject_id: String,
    pub before: JsonValue,
    pub after: JsonValue,
    pub reason: String,
    pub occurred_at_ms: i64,
    pub previous_hash: Sha256Digest,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AuditInvalidField {
    Id,
    Sequence,
    ProjectId,
    Actor,
    Action,
    SubjectType,
    SubjectId,
    Before,
    After,
    Reason,
    OccurredAt,
    PreviousHash,
    EventHash,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", content = "detail", rename_all = "snake_case")]
pub enum AuditChainFinding {
    InvalidRow {
        row_ordinal: u64,
        event_id: Option<AuditEventId>,
        sequence: Option<u64>,
        field: AuditInvalidField,
    },
    SequenceMismatch {
        event_id: AuditEventId,
        expected: u64,
        observed: u64,
    },
    PreviousHashMismatch {
        event_id: AuditEventId,
        sequence: u64,
        expected: Sha256Digest,
        observed: Sha256Digest,
    },
    EventHashMismatch {
        event_id: AuditEventId,
        sequence: u64,
        expected: Sha256Digest,
        observed: Sha256Digest,
    },
    NonCanonicalBefore {
        event_id: AuditEventId,
        sequence: u64,
    },
    NonCanonicalAfter {
        event_id: AuditEventId,
        sequence: u64,
    },
}

#[derive(Deserialize)]
#[serde(
    tag = "kind",
    content = "detail",
    rename_all = "snake_case",
    deny_unknown_fields
)]
enum RawAuditChainFinding {
    InvalidRow(RawInvalidRowFinding),
    SequenceMismatch(RawSequenceMismatchFinding),
    PreviousHashMismatch(RawHashMismatchFinding),
    EventHashMismatch(RawHashMismatchFinding),
    NonCanonicalBefore(RawCanonicalFinding),
    NonCanonicalAfter(RawCanonicalFinding),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawInvalidRowFinding {
    row_ordinal: u64,
    event_id: Option<AuditEventId>,
    sequence: Option<u64>,
    field: AuditInvalidField,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawSequenceMismatchFinding {
    event_id: AuditEventId,
    expected: u64,
    observed: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawHashMismatchFinding {
    event_id: AuditEventId,
    sequence: u64,
    expected: Sha256Digest,
    observed: Sha256Digest,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawCanonicalFinding {
    event_id: AuditEventId,
    sequence: u64,
}

impl TryFrom<RawAuditChainFinding> for AuditChainFinding {
    type Error = HeleosError;

    fn try_from(raw: RawAuditChainFinding) -> Result<Self> {
        Ok(match raw {
            RawAuditChainFinding::InvalidRow(raw) => {
                validate_positive_jcs(raw.row_ordinal)?;
                if let Some(sequence) = raw.sequence {
                    validate_positive_jcs(sequence)?;
                }
                if (raw.field == AuditInvalidField::Id && raw.event_id.is_some())
                    || (raw.field == AuditInvalidField::Sequence && raw.sequence.is_some())
                {
                    return Err(HeleosError::Integrity);
                }
                Self::InvalidRow {
                    row_ordinal: raw.row_ordinal,
                    event_id: raw.event_id,
                    sequence: raw.sequence,
                    field: raw.field,
                }
            }
            RawAuditChainFinding::SequenceMismatch(raw) => {
                validate_positive_jcs(raw.expected)?;
                validate_positive_jcs(raw.observed)?;
                if raw.expected == raw.observed {
                    return Err(HeleosError::Integrity);
                }
                Self::SequenceMismatch {
                    event_id: raw.event_id,
                    expected: raw.expected,
                    observed: raw.observed,
                }
            }
            RawAuditChainFinding::PreviousHashMismatch(raw) => {
                validate_positive_jcs(raw.sequence)?;
                if raw.expected == raw.observed {
                    return Err(HeleosError::Integrity);
                }
                Self::PreviousHashMismatch {
                    event_id: raw.event_id,
                    sequence: raw.sequence,
                    expected: raw.expected,
                    observed: raw.observed,
                }
            }
            RawAuditChainFinding::EventHashMismatch(raw) => {
                validate_positive_jcs(raw.sequence)?;
                if raw.expected == raw.observed {
                    return Err(HeleosError::Integrity);
                }
                Self::EventHashMismatch {
                    event_id: raw.event_id,
                    sequence: raw.sequence,
                    expected: raw.expected,
                    observed: raw.observed,
                }
            }
            RawAuditChainFinding::NonCanonicalBefore(raw) => {
                validate_positive_jcs(raw.sequence)?;
                Self::NonCanonicalBefore {
                    event_id: raw.event_id,
                    sequence: raw.sequence,
                }
            }
            RawAuditChainFinding::NonCanonicalAfter(raw) => {
                validate_positive_jcs(raw.sequence)?;
                Self::NonCanonicalAfter {
                    event_id: raw.event_id,
                    sequence: raw.sequence,
                }
            }
        })
    }
}

impl<'de> Deserialize<'de> for AuditChainFinding {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let raw = RawAuditChainFinding::deserialize(deserializer)?;
        Self::try_from(raw).map_err(de::Error::custom)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AuditChainReport {
    pub schema: String,
    pub valid: bool,
    pub checked_event_count: u64,
    pub first_sequence: Option<u64>,
    pub last_sequence: Option<u64>,
    pub head_hash: Sha256Digest,
    pub findings: Vec<AuditChainFinding>,
    pub findings_truncated: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawAuditChainReport {
    schema: String,
    valid: bool,
    checked_event_count: u64,
    first_sequence: Option<u64>,
    last_sequence: Option<u64>,
    head_hash: Sha256Digest,
    #[serde(deserialize_with = "deserialize_findings")]
    findings: Vec<AuditChainFinding>,
    findings_truncated: bool,
}

fn deserialize_findings<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Vec<AuditChainFinding>, D::Error> {
    super::deserialize_bounded_vec::<D, AuditChainFinding, MAX_AUDIT_FINDINGS>(deserializer)
}

impl TryFrom<RawAuditChainReport> for AuditChainReport {
    type Error = HeleosError;

    fn try_from(raw: RawAuditChainReport) -> Result<Self> {
        let report = Self {
            schema: raw.schema,
            valid: raw.valid,
            checked_event_count: raw.checked_event_count,
            first_sequence: raw.first_sequence,
            last_sequence: raw.last_sequence,
            head_hash: raw.head_hash,
            findings: raw.findings,
            findings_truncated: raw.findings_truncated,
        };
        validate_report(&report)?;
        Ok(report)
    }
}

impl<'de> Deserialize<'de> for AuditChainReport {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let raw = RawAuditChainReport::deserialize(deserializer)?;
        Self::try_from(raw).map_err(de::Error::custom)
    }
}

#[derive(Clone, Debug)]
pub(crate) enum RawAuditValue {
    Null,
    Integer(i64),
    Text(String),
    Invalid,
}

#[derive(Clone, Debug)]
pub(crate) struct RawAuditRow {
    pub id: RawAuditValue,
    pub sequence: RawAuditValue,
    pub project_id: RawAuditValue,
    pub actor: RawAuditValue,
    pub action: RawAuditValue,
    pub subject_type: RawAuditValue,
    pub subject_id: RawAuditValue,
    pub before_json: RawAuditValue,
    pub after_json: RawAuditValue,
    pub reason: RawAuditValue,
    pub occurred_at_ms: RawAuditValue,
    pub previous_hash: RawAuditValue,
    pub event_hash: RawAuditValue,
}

pub(crate) struct AuditVerifier {
    report: AuditChainReport,
    expected_sequence: u64,
    expected_previous: Sha256Digest,
}

impl AuditVerifier {
    pub(crate) fn new() -> Self {
        let zero = Sha256Digest::from_bytes([0; 32]);
        Self {
            report: AuditChainReport {
                schema: AUDIT_CHAIN_REPORT_SCHEMA_V1.to_owned(),
                valid: true,
                checked_event_count: 0,
                first_sequence: None,
                last_sequence: None,
                head_hash: zero,
                findings: Vec::new(),
                findings_truncated: false,
            },
            expected_sequence: 1,
            expected_previous: zero,
        }
    }

    pub(crate) fn push(&mut self, row: RawAuditRow) -> Result<()> {
        self.report.checked_event_count = self
            .report
            .checked_event_count
            .checked_add(1)
            .ok_or(HeleosError::Integrity)?;
        let ordinal = self.report.checked_event_count;
        let id = value_text(&row.id).and_then(|value| AuditEventId::from_str(value).ok());
        let sequence = value_positive_u64(&row.sequence);
        if let Some(sequence) = sequence {
            self.report.first_sequence.get_or_insert(sequence);
            self.report.last_sequence = Some(sequence);
        }

        let mut invalid = Vec::new();
        if id.is_none() {
            invalid.push(AuditInvalidField::Id);
        }
        if sequence.is_none() {
            invalid.push(AuditInvalidField::Sequence);
        }
        let project_id = match &row.project_id {
            RawAuditValue::Null => Some(None),
            value => value_text(value)
                .and_then(|text| ProjectId::from_str(text).ok())
                .map(Some),
        };
        if project_id.is_none() {
            invalid.push(AuditInvalidField::ProjectId);
        }
        let actor = value_text(&row.actor).and_then(|value| ActorId::from_str(value).ok());
        if actor.is_none() {
            invalid.push(AuditInvalidField::Actor);
        }
        let action = value_text(&row.action).and_then(AuditAction::parse);
        if action.is_none() {
            invalid.push(AuditInvalidField::Action);
        }
        let subject_type = value_text(&row.subject_type).and_then(AuditSubjectType::parse);
        if subject_type.is_none() {
            invalid.push(AuditInvalidField::SubjectType);
        }
        let subject_id = value_text(&row.subject_id)
            .filter(|value| validate_audit_text(value, 256, false).is_ok());
        if subject_id.is_none() {
            invalid.push(AuditInvalidField::SubjectId);
        }
        let before = parse_json_field(&row.before_json);
        if before.is_none() {
            invalid.push(AuditInvalidField::Before);
        }
        let after = parse_json_field(&row.after_json);
        if after.is_none() {
            invalid.push(AuditInvalidField::After);
        }
        let reason =
            value_text(&row.reason).filter(|value| validate_audit_text(value, 1024, true).is_ok());
        if reason.is_none() {
            invalid.push(AuditInvalidField::Reason);
        }
        let occurred_at_ms = value_nonnegative_i64(&row.occurred_at_ms);
        if occurred_at_ms.is_none() {
            invalid.push(AuditInvalidField::OccurredAt);
        }
        let previous_hash =
            value_text(&row.previous_hash).and_then(|value| Sha256Digest::from_str(value).ok());
        if previous_hash.is_none() {
            invalid.push(AuditInvalidField::PreviousHash);
        }
        let stored_hash =
            value_text(&row.event_hash).and_then(|value| Sha256Digest::from_str(value).ok());
        if stored_hash.is_none() {
            invalid.push(AuditInvalidField::EventHash);
        }

        for field in invalid {
            push_finding(
                &mut self.report,
                AuditChainFinding::InvalidRow {
                    row_ordinal: ordinal,
                    event_id: id,
                    sequence,
                    field,
                },
            );
        }

        if let Some(sequence) = sequence {
            if let Some(id) = id
                && sequence != self.expected_sequence
            {
                push_finding(
                    &mut self.report,
                    AuditChainFinding::SequenceMismatch {
                        event_id: id,
                        expected: self.expected_sequence,
                        observed: sequence,
                    },
                );
            }
            self.expected_sequence = sequence.checked_add(1).ok_or(HeleosError::Integrity)?;
            if let (Some(id), Some(previous_hash)) = (id, previous_hash)
                && previous_hash != self.expected_previous
            {
                push_finding(
                    &mut self.report,
                    AuditChainFinding::PreviousHashMismatch {
                        event_id: id,
                        sequence,
                        expected: self.expected_previous,
                        observed: previous_hash,
                    },
                );
            }
        }

        let before_noncanonical = before.as_ref().is_some_and(|(_, canonical)| !canonical);
        let after_noncanonical = after.as_ref().is_some_and(|(_, canonical)| !canonical);

        let (
            Some(id),
            Some(sequence),
            Some(project_id),
            Some(actor),
            Some(action),
            Some(subject_type),
            Some(subject_id),
            Some((before, _)),
            Some((after, _)),
            Some(reason),
            Some(occurred_at_ms),
            Some(previous_hash),
            Some(stored_hash),
        ) = (
            id,
            sequence,
            project_id,
            actor,
            action,
            subject_type,
            subject_id,
            before,
            after,
            reason,
            occurred_at_ms,
            previous_hash,
            stored_hash,
        )
        else {
            if let (Some(id), Some(sequence)) = (id, sequence) {
                if before_noncanonical {
                    push_finding(
                        &mut self.report,
                        AuditChainFinding::NonCanonicalBefore {
                            event_id: id,
                            sequence,
                        },
                    );
                }
                if after_noncanonical {
                    push_finding(
                        &mut self.report,
                        AuditChainFinding::NonCanonicalAfter {
                            event_id: id,
                            sequence,
                        },
                    );
                }
            }
            return Ok(());
        };
        let event = AuditEvent {
            id,
            sequence,
            project_id,
            actor,
            action,
            subject_type,
            subject_id: subject_id.to_owned(),
            before,
            after,
            reason: reason.to_owned(),
            occurred_at_ms,
            previous_hash,
            event_hash: stored_hash,
        };
        let recomputed = event.recomputed_hash()?;
        if recomputed != stored_hash {
            push_finding(
                &mut self.report,
                AuditChainFinding::EventHashMismatch {
                    event_id: id,
                    sequence,
                    expected: recomputed,
                    observed: stored_hash,
                },
            );
        }
        if before_noncanonical {
            push_finding(
                &mut self.report,
                AuditChainFinding::NonCanonicalBefore {
                    event_id: id,
                    sequence,
                },
            );
        }
        if after_noncanonical {
            push_finding(
                &mut self.report,
                AuditChainFinding::NonCanonicalAfter {
                    event_id: id,
                    sequence,
                },
            );
        }
        self.report.head_hash = recomputed;
        self.expected_previous = recomputed;
        Ok(())
    }

    pub(crate) fn finish(mut self) -> Result<AuditChainReport> {
        self.report.valid = self.report.findings.is_empty() && !self.report.findings_truncated;
        validate_report(&self.report)?;
        Ok(self.report)
    }
}

#[cfg(test)]
pub(crate) fn verify_rows(rows: impl IntoIterator<Item = RawAuditRow>) -> Result<AuditChainReport> {
    let mut verifier = AuditVerifier::new();
    for row in rows {
        verifier.push(row)?;
    }
    verifier.finish()
}

fn push_finding(report: &mut AuditChainReport, finding: AuditChainFinding) {
    if report.findings.len() < MAX_AUDIT_FINDINGS {
        report.findings.push(finding);
    } else {
        report.findings_truncated = true;
    }
}

fn value_text(value: &RawAuditValue) -> Option<&str> {
    match value {
        RawAuditValue::Text(value) => Some(value),
        _ => None,
    }
}

fn value_positive_u64(value: &RawAuditValue) -> Option<u64> {
    match value {
        RawAuditValue::Integer(value) if *value > 0 => u64::try_from(*value)
            .ok()
            .filter(|value| *value <= JCS_SAFE_INTEGER_MAX),
        _ => None,
    }
}

fn value_nonnegative_i64(value: &RawAuditValue) -> Option<i64> {
    match value {
        RawAuditValue::Integer(value) if *value >= 0 && (*value as u64) <= JCS_SAFE_INTEGER_MAX => {
            Some(*value)
        }
        _ => None,
    }
}

fn parse_json_field(value: &RawAuditValue) -> Option<(JsonValue, bool)> {
    let text = value_text(value)?;
    if text.len() > 1024 * 1024 {
        return None;
    }
    let parsed: JsonValue = serde_json::from_str(text).ok()?;
    let canonical = canonical_json(&parsed).ok()? == text.as_bytes();
    Some((parsed, canonical))
}

fn validate_positive_jcs(value: u64) -> Result<()> {
    if value == 0 || value > JCS_SAFE_INTEGER_MAX {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn validate_report(report: &AuditChainReport) -> Result<()> {
    if report.schema != AUDIT_CHAIN_REPORT_SCHEMA_V1
        || report.checked_event_count > JCS_SAFE_INTEGER_MAX
        || report.findings.len() > MAX_AUDIT_FINDINGS
        || (report.findings_truncated && report.findings.len() != MAX_AUDIT_FINDINGS)
        || report.valid != (report.findings.is_empty() && !report.findings_truncated)
    {
        return Err(HeleosError::Integrity);
    }
    if let Some(first) = report.first_sequence {
        validate_positive_jcs(first)?;
    }
    if let Some(last) = report.last_sequence {
        validate_positive_jcs(last)?;
    }
    if matches!((report.first_sequence, report.last_sequence), (Some(first), Some(last)) if first > last)
        || matches!(
            (report.first_sequence, report.last_sequence),
            (Some(_), None) | (None, Some(_))
        )
    {
        return Err(HeleosError::Integrity);
    }
    let zero = Sha256Digest::from_bytes([0; 32]);
    if report.checked_event_count == 0
        && (report.first_sequence.is_some()
            || report.last_sequence.is_some()
            || report.head_hash != zero
            || !report.findings.is_empty()
            || report.findings_truncated)
    {
        return Err(HeleosError::Integrity);
    }
    if report.valid
        && report.checked_event_count > 0
        && (report.first_sequence != Some(1)
            || report.last_sequence != Some(report.checked_event_count))
    {
        return Err(HeleosError::Integrity);
    }
    validate_finding_bounds_and_order(report)?;
    Ok(())
}

fn validate_finding_bounds_and_order(report: &AuditChainReport) -> Result<()> {
    let bounds = report.first_sequence.zip(report.last_sequence);
    let mut previous_invalid = None;
    let mut previous_row_key = None;
    let mut segment_key = None;
    let mut segment_rank = None;
    let mut segment_invalid_row = None;
    let mut inferred_segment_count = 0_u64;
    for finding in &report.findings {
        let invalid_row = if let AuditChainFinding::InvalidRow {
            row_ordinal,
            event_id,
            sequence,
            field,
        } = finding
        {
            if *row_ordinal > report.checked_event_count
                || previous_invalid.is_some_and(
                    |(previous_row, previous_field, previous_identity)| {
                        *row_ordinal < previous_row
                            || (*row_ordinal == previous_row
                                && ((*event_id, *sequence) != previous_identity
                                    || *field <= previous_field))
                    },
                )
            {
                return Err(HeleosError::Integrity);
            }
            Some((*row_ordinal, *field, (*event_id, *sequence)))
        } else {
            None
        };

        let sequence = finding_observed_sequence(finding);
        if let Some(sequence) = sequence
            && !matches!(bounds, Some((first, last)) if (first..=last).contains(&sequence))
        {
            return Err(HeleosError::Integrity);
        }

        let row_key = sequence.zip(finding_event_id(finding));
        if let Some(row_key) = row_key {
            if previous_row_key.is_some_and(|previous| previous > row_key) {
                return Err(HeleosError::Integrity);
            }
            previous_row_key = Some(row_key);
        }

        let rank = finding_rank(finding);
        let current_segment_key = match (row_key, invalid_row) {
            (Some(row_key), _) => FindingSegmentKey::Row(row_key),
            (None, Some((row_ordinal, _, _))) => FindingSegmentKey::InvalidOnly(row_ordinal),
            (None, None) => return Err(HeleosError::Integrity),
        };
        let starts_new_segment = match (segment_key, current_segment_key) {
            (None, _) => true,
            (Some(FindingSegmentKey::Row(previous)), FindingSegmentKey::Row(current)) => {
                if current > previous {
                    true
                } else if current < previous {
                    return Err(HeleosError::Integrity);
                } else if let Some((row_ordinal, _, _)) = invalid_row {
                    if segment_rank == Some(0) && segment_invalid_row == Some(row_ordinal) {
                        false
                    } else if previous_invalid
                        .is_none_or(|(previous_row, _, _)| row_ordinal > previous_row)
                    {
                        true
                    } else {
                        return Err(HeleosError::Integrity);
                    }
                } else if rank == 1 && segment_rank.is_some_and(|previous| previous >= 1) {
                    true
                } else if segment_rank.is_some_and(|previous| rank > previous) {
                    false
                } else {
                    return Err(HeleosError::Integrity);
                }
            }
            (
                Some(FindingSegmentKey::InvalidOnly(previous_row)),
                FindingSegmentKey::InvalidOnly(current_row),
            ) => {
                if current_row > previous_row {
                    true
                } else if current_row == previous_row && segment_rank == Some(0) {
                    false
                } else {
                    return Err(HeleosError::Integrity);
                }
            }
            (Some(_), _) => true,
        };

        if starts_new_segment {
            inferred_segment_count = inferred_segment_count
                .checked_add(1)
                .ok_or(HeleosError::Integrity)?;
            if inferred_segment_count > report.checked_event_count {
                return Err(HeleosError::Integrity);
            }
            segment_key = Some(current_segment_key);
            segment_invalid_row = invalid_row.map(|(row_ordinal, _, _)| row_ordinal);
        } else if rank == 0
            && segment_invalid_row != invalid_row.map(|(row_ordinal, _, _)| row_ordinal)
        {
            return Err(HeleosError::Integrity);
        }
        segment_rank = Some(rank);
        if let Some(invalid) = invalid_row {
            previous_invalid = Some(invalid);
        }
    }
    Ok(())
}

#[derive(Clone, Copy)]
enum FindingSegmentKey {
    Row((u64, AuditEventId)),
    InvalidOnly(u64),
}

fn finding_rank(finding: &AuditChainFinding) -> u8 {
    match finding {
        AuditChainFinding::InvalidRow { .. } => 0,
        AuditChainFinding::SequenceMismatch { .. } => 1,
        AuditChainFinding::PreviousHashMismatch { .. } => 2,
        AuditChainFinding::EventHashMismatch { .. } => 3,
        AuditChainFinding::NonCanonicalBefore { .. } => 4,
        AuditChainFinding::NonCanonicalAfter { .. } => 5,
    }
}

fn finding_observed_sequence(finding: &AuditChainFinding) -> Option<u64> {
    match finding {
        AuditChainFinding::InvalidRow { sequence, .. } => *sequence,
        AuditChainFinding::SequenceMismatch { observed, .. } => Some(*observed),
        AuditChainFinding::PreviousHashMismatch { sequence, .. }
        | AuditChainFinding::EventHashMismatch { sequence, .. }
        | AuditChainFinding::NonCanonicalBefore { sequence, .. }
        | AuditChainFinding::NonCanonicalAfter { sequence, .. } => Some(*sequence),
    }
}

fn finding_event_id(finding: &AuditChainFinding) -> Option<AuditEventId> {
    match finding {
        AuditChainFinding::InvalidRow { event_id, .. } => *event_id,
        AuditChainFinding::SequenceMismatch { event_id, .. }
        | AuditChainFinding::PreviousHashMismatch { event_id, .. }
        | AuditChainFinding::EventHashMismatch { event_id, .. }
        | AuditChainFinding::NonCanonicalBefore { event_id, .. }
        | AuditChainFinding::NonCanonicalAfter { event_id, .. } => Some(*event_id),
    }
}

fn validate_timestamp(value: i64) -> Result<()> {
    if value < 0 || (value as u64) > JCS_SAFE_INTEGER_MAX {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn validate_audit_text(value: &str, max_bytes: usize, ordinary_whitespace: bool) -> Result<()> {
    let valid_control =
        |character: char| ordinary_whitespace && matches!(character, '\n' | '\r' | '\t');
    if value.is_empty()
        || value.len() > max_bytes
        || value
            .chars()
            .any(|character| character.is_control() && !valid_control(character))
    {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn ensure_canonical_value_size(value: &JsonValue) -> Result<()> {
    if canonical_json(value)?.len() > 1024 * 1024 {
        return Err(HeleosError::ResourceLimit);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(value: impl Into<String>) -> RawAuditValue {
        RawAuditValue::Text(value.into())
    }

    fn row_with_id(id: &str) -> RawAuditRow {
        RawAuditRow {
            id: text(id),
            sequence: RawAuditValue::Integer(1),
            project_id: RawAuditValue::Null,
            actor: text("tester"),
            action: text("project_created"),
            subject_type: text("project"),
            subject_id: text("subject"),
            before_json: text("null"),
            after_json: text("null"),
            reason: text("reason"),
            occurred_at_ms: RawAuditValue::Integer(0),
            previous_hash: text("0".repeat(64)),
            event_hash: text("0".repeat(64)),
        }
    }

    #[test]
    fn same_row_findings_follow_the_frozen_variant_order() {
        // Break caught: canonical-JSON findings emitted before chain mismatches for one row.
        let mut row = row_with_id("00000000-0000-4000-8000-000000000001");
        row.sequence = RawAuditValue::Integer(2);
        row.previous_hash = text("b".repeat(64));
        row.event_hash = text("c".repeat(64));
        row.before_json = text(" null");
        row.after_json = text("null ");

        let report = verify_rows([row]).expect("verify hostile row");
        assert!(matches!(
            report.findings.as_slice(),
            [
                AuditChainFinding::SequenceMismatch { .. },
                AuditChainFinding::PreviousHashMismatch { .. },
                AuditChainFinding::EventHashMismatch { .. },
                AuditChainFinding::NonCanonicalBefore { .. },
                AuditChainFinding::NonCanonicalAfter { .. },
            ]
        ));
    }

    #[test]
    fn public_findings_and_reports_reject_impossible_cross_field_shapes() {
        // Break caught: externally constructed findings claiming a mismatch that did not occur.
        for finding in [
            serde_json::json!({
                "kind": "sequence_mismatch",
                "detail": {
                    "event_id": "00000000-0000-4000-8000-000000000001",
                    "expected": 1,
                    "observed": 1
                }
            }),
            serde_json::json!({
                "kind": "previous_hash_mismatch",
                "detail": {
                    "event_id": "00000000-0000-4000-8000-000000000001",
                    "sequence": 1,
                    "expected": "1111111111111111111111111111111111111111111111111111111111111111",
                    "observed": "1111111111111111111111111111111111111111111111111111111111111111"
                }
            }),
            serde_json::json!({
                "kind": "event_hash_mismatch",
                "detail": {
                    "event_id": "00000000-0000-4000-8000-000000000001",
                    "sequence": 1,
                    "expected": "2222222222222222222222222222222222222222222222222222222222222222",
                    "observed": "2222222222222222222222222222222222222222222222222222222222222222"
                }
            }),
            serde_json::json!({
                "kind": "invalid_row",
                "detail": {
                    "row_ordinal": 1,
                    "event_id": "00000000-0000-4000-8000-000000000001",
                    "sequence": 1,
                    "field": "id"
                }
            }),
            serde_json::json!({
                "kind": "invalid_row",
                "detail": {
                    "row_ordinal": 1,
                    "event_id": null,
                    "sequence": 1,
                    "field": "sequence"
                }
            }),
        ] {
            assert!(serde_json::from_value::<AuditChainFinding>(finding).is_err());
        }

        let finding = serde_json::json!({
            "kind": "non_canonical_before",
            "detail": {
                "event_id": "00000000-0000-4000-8000-000000000001",
                "sequence": 1
            }
        });
        let short_truncated = serde_json::json!({
            "schema": AUDIT_CHAIN_REPORT_SCHEMA_V1,
            "valid": false,
            "checked_event_count": 1,
            "first_sequence": 1,
            "last_sequence": 1,
            "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
            "findings": [finding.clone()],
            "findings_truncated": true
        });
        assert!(serde_json::from_value::<AuditChainReport>(short_truncated).is_err());

        let exact_truncated = serde_json::json!({
            "schema": AUDIT_CHAIN_REPORT_SCHEMA_V1,
            "valid": false,
            "checked_event_count": 129,
            "first_sequence": 1,
            "last_sequence": 129,
            "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
            "findings": (1..=MAX_AUDIT_FINDINGS).map(|ordinal| serde_json::json!({
                "kind": "invalid_row",
                "detail": {
                    "row_ordinal": ordinal,
                    "event_id": null,
                    "sequence": ordinal,
                    "field": "id"
                }
            })).collect::<Vec<_>>(),
            "findings_truncated": true
        });
        serde_json::from_value::<AuditChainReport>(exact_truncated)
            .expect("truncation at the exact published cap is representable");
    }

    #[test]
    fn verifier_emits_each_finding_from_only_its_required_fields() {
        // Break caught: one unrelated invalid field suppressing independently provable defects.
        let mut row = row_with_id("00000000-0000-4000-8000-000000000001");
        row.sequence = RawAuditValue::Integer(2);
        row.actor = RawAuditValue::Invalid;
        row.previous_hash = text("b".repeat(64));
        row.before_json = text(" null");

        let report = verify_rows([row]).expect("verify partially malformed row");
        assert!(matches!(
            report.findings.as_slice(),
            [
                AuditChainFinding::InvalidRow {
                    field: AuditInvalidField::Actor,
                    ..
                },
                AuditChainFinding::SequenceMismatch { .. },
                AuditChainFinding::PreviousHashMismatch { .. },
                AuditChainFinding::NonCanonicalBefore { .. },
            ]
        ));
    }

    #[test]
    fn verifier_resynchronizes_sequence_after_an_unrelated_invalid_field() {
        // Break caught: an invalid actor at sequence 1 making valid sequence 2 look like a gap.
        let mut first = row_with_id("00000000-0000-4000-8000-000000000001");
        first.actor = RawAuditValue::Invalid;
        let mut second = row_with_id("00000000-0000-4000-8000-000000000002");
        second.sequence = RawAuditValue::Integer(2);

        let report = verify_rows([first, second]).expect("verify two-row sequence");
        assert!(report.findings.iter().any(|finding| matches!(
            finding,
            AuditChainFinding::InvalidRow {
                field: AuditInvalidField::Actor,
                ..
            }
        )));
        assert!(
            !report
                .findings
                .iter()
                .any(|finding| matches!(finding, AuditChainFinding::SequenceMismatch { .. }))
        );
    }

    #[test]
    fn duplicate_hostile_row_keys_produce_an_invalid_report_not_an_error() {
        // Break caught: public ordering mistaking a new duplicate row for a variant permutation.
        let id = "00000000-0000-4000-8000-000000000001";
        let mut first = row_with_id(id);
        first.event_hash = text("c".repeat(64));
        first.before_json = text(" null");
        let mut second = row_with_id(id);
        second.event_hash = text("c".repeat(64));

        let report = verify_rows([first, second]).expect("verify duplicate hostile row keys");
        assert_eq!(report.checked_event_count, 2);
        assert!(!report.valid);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| matches!(finding, AuditChainFinding::SequenceMismatch { .. }))
        );
        let encoded = serde_json::to_value(&report).expect("serialize generated duplicate report");
        let decoded = serde_json::from_value::<AuditChainReport>(encoded)
            .expect("generated duplicate report must round trip");
        assert_eq!(decoded, report);
    }

    #[test]
    fn public_report_binds_finding_bounds_and_canonical_order() {
        // Break caught: externally supplied reports moving findings to nonexistent rows/sequences.
        let event_one = "00000000-0000-4000-8000-000000000001";
        let event_two = "00000000-0000-4000-8000-000000000002";
        let report = |checked_event_count: u64,
                      first_sequence: Option<u64>,
                      last_sequence: Option<u64>,
                      findings: Vec<serde_json::Value>| {
            serde_json::json!({
                "schema": AUDIT_CHAIN_REPORT_SCHEMA_V1,
                "valid": false,
                "checked_event_count": checked_event_count,
                "first_sequence": first_sequence,
                "last_sequence": last_sequence,
                "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
                "findings": findings,
                "findings_truncated": false
            })
        };
        let invalid = |row_ordinal: u64, field: &str, sequence: Option<u64>| {
            serde_json::json!({
                "kind": "invalid_row",
                "detail": {
                    "row_ordinal": row_ordinal,
                    "event_id": event_one,
                    "sequence": sequence,
                    "field": field
                }
            })
        };
        let noncanonical = |event_id: &str, sequence: u64| {
            serde_json::json!({
                "kind": "non_canonical_before",
                "detail": {"event_id": event_id, "sequence": sequence}
            })
        };
        let mismatch = serde_json::json!({
            "kind": "event_hash_mismatch",
            "detail": {
                "event_id": event_one,
                "sequence": 1,
                "expected": "1111111111111111111111111111111111111111111111111111111111111111",
                "observed": "2222222222222222222222222222222222222222222222222222222222222222"
            }
        });
        let sequence_mismatch = |expected: u64| {
            serde_json::json!({
                "kind": "sequence_mismatch",
                "detail": {
                    "event_id": event_one,
                    "expected": expected,
                    "observed": 1
                }
            })
        };

        for hostile in [
            report(1, Some(1), Some(1), vec![invalid(2, "actor", Some(1))]),
            report(1, Some(1), Some(1), vec![invalid(1, "actor", Some(2))]),
            report(1, Some(1), Some(1), vec![noncanonical(event_one, 2)]),
            report(
                2,
                Some(1),
                Some(2),
                vec![noncanonical(event_two, 2), noncanonical(event_one, 1)],
            ),
            report(
                1,
                Some(1),
                Some(1),
                vec![invalid(1, "action", Some(1)), invalid(1, "actor", Some(1))],
            ),
            report(
                1,
                Some(1),
                Some(1),
                vec![noncanonical(event_one, 1), mismatch.clone()],
            ),
            report(
                1,
                Some(1),
                Some(1),
                vec![sequence_mismatch(2), sequence_mismatch(3)],
            ),
            report(
                2,
                Some(1),
                Some(1),
                vec![mismatch.clone(), mismatch.clone()],
            ),
        ] {
            assert!(serde_json::from_value::<AuditChainReport>(hostile).is_err());
        }

        serde_json::from_value::<AuditChainReport>(report(
            2,
            Some(1),
            Some(1),
            vec![sequence_mismatch(2), sequence_mismatch(3)],
        ))
        .expect("two visible duplicate-row segments fit two checked rows");

        serde_json::from_value::<AuditChainReport>(report(
            1,
            Some(1),
            Some(1),
            vec![
                invalid(1, "actor", Some(1)),
                invalid(1, "action", Some(1)),
                mismatch,
                noncanonical(event_one, 1),
            ],
        ))
        .expect("canonical same-row finding order");
    }

    #[test]
    fn public_report_binds_each_invalid_row_ordinal_to_one_identity() {
        // Break caught: a later visible row reusing an ordinal with another event identity.
        let event_one = "00000000-0000-4000-8000-000000000001";
        let event_two = "00000000-0000-4000-8000-000000000002";
        let report =
            |checked_event_count: u64, last_sequence: u64, findings: Vec<serde_json::Value>| {
                serde_json::json!({
                    "schema": AUDIT_CHAIN_REPORT_SCHEMA_V1,
                    "valid": false,
                    "checked_event_count": checked_event_count,
                    "first_sequence": 1,
                    "last_sequence": last_sequence,
                    "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
                    "findings": findings,
                    "findings_truncated": false
                })
            };
        let invalid = |event_id: &str, sequence: u64, field: &str| {
            serde_json::json!({
                "kind": "invalid_row",
                "detail": {
                    "row_ordinal": 1,
                    "event_id": event_id,
                    "sequence": sequence,
                    "field": field
                }
            })
        };

        serde_json::from_value::<AuditChainReport>(report(
            1,
            1,
            vec![
                invalid(event_one, 1, "actor"),
                invalid(event_one, 1, "action"),
            ],
        ))
        .expect("multiple invalid fields from one exact row identity");

        for hostile in [
            report(
                2,
                1,
                vec![
                    invalid(event_one, 1, "actor"),
                    invalid(event_two, 1, "action"),
                ],
            ),
            report(
                2,
                2,
                vec![
                    invalid(event_one, 1, "actor"),
                    invalid(event_one, 2, "action"),
                ],
            ),
        ] {
            assert!(serde_json::from_value::<AuditChainReport>(hostile).is_err());
        }
    }

    #[test]
    fn the_129th_finding_sets_truncation_without_being_published() {
        // Break caught: an attacker growing audit findings beyond the fixed public cap.
        let rows = (0..129).map(|index| {
            let mut row = row_with_id("not-a-uuid");
            row.sequence = RawAuditValue::Integer(i64::from(index + 1));
            row
        });
        let report = verify_rows(rows).expect("verify bounded hostile rows");
        assert_eq!(report.checked_event_count, 129);
        assert_eq!(report.findings.len(), 128);
        assert!(report.findings_truncated);
        assert!(!report.valid);
    }

    #[test]
    fn public_report_deserialization_stops_at_the_128_finding_cap() {
        // Break caught: derived Vec deserialization allocated every hostile finding first.
        let findings = (1..=MAX_AUDIT_FINDINGS)
            .map(|ordinal| {
                serde_json::json!({
                    "kind": "invalid_row",
                    "detail": {
                        "row_ordinal": ordinal,
                        "event_id": null,
                        "sequence": ordinal,
                        "field": "id"
                    }
                })
            })
            .collect::<Vec<_>>();
        let at_cap = serde_json::json!({
            "schema": AUDIT_CHAIN_REPORT_SCHEMA_V1,
            "valid": false,
            "checked_event_count": 128,
            "first_sequence": 1,
            "last_sequence": 128,
            "head_hash": "0000000000000000000000000000000000000000000000000000000000000000",
            "findings": findings,
            "findings_truncated": false
        });
        serde_json::from_value::<AuditChainReport>(at_cap.clone())
            .expect("the exact finding cap is accepted");
        let mut over_cap = at_cap;
        over_cap["findings"]
            .as_array_mut()
            .expect("findings array")
            .push(serde_json::json!({
                "kind": "invalid_row",
                "detail": {
                    "row_ordinal": 129,
                    "event_id": null,
                    "sequence": 129,
                    "field": "id"
                }
            }));
        let error = serde_json::from_value::<AuditChainReport>(over_cap)
            .expect_err("the 129th finding must fail during sequence decoding");
        assert!(error.to_string().contains("maximum of 128"));
    }

    #[test]
    fn public_event_deserialization_checks_subject_and_reason_bytes_before_copy() {
        // Break caught: oversized audit scalars allocated before their public DTO caps ran.
        let event = AuditEvent::build(AuditEventInput {
            id: AuditEventId::from_uuid(
                Uuid::parse_str("00000000-0000-4000-8000-000000000001").expect("audit UUID"),
            ),
            sequence: 1,
            project_id: None,
            actor: ActorId::from_str("tester").expect("actor"),
            action: AuditAction::ProjectCreated,
            subject_type: AuditSubjectType::Project,
            subject_id: "s".repeat(256),
            before: JsonValue::Null,
            after: JsonValue::Null,
            reason: "r".repeat(1024),
            occurred_at_ms: 0,
            previous_hash: Sha256Digest::from_bytes([0; 32]),
        })
        .expect("event at exact string caps");
        let at_cap = serde_json::to_value(event).expect("event JSON");
        serde_json::from_value::<AuditEvent>(at_cap.clone()).expect("exact string caps round-trip");

        let mut subject_over = at_cap.clone();
        subject_over["subject_id"] = serde_json::Value::String("s".repeat(257));
        let error = serde_json::from_value::<AuditEvent>(subject_over)
            .expect_err("subject N+1 must fail during string decoding");
        assert!(error.to_string().contains("maximum of 256 bytes"));

        let mut reason_over = at_cap;
        reason_over["reason"] = serde_json::Value::String("r".repeat(1025));
        let error = serde_json::from_value::<AuditEvent>(reason_over)
            .expect_err("reason N+1 must fail during string decoding");
        assert!(error.to_string().contains("maximum of 1024 bytes"));
    }
}
