use std::{collections::BTreeMap, str::FromStr};

use rusqlite::{Connection, Params, Row, Transaction, params, types::ValueRef};
use serde::{Serialize, de::DeserializeOwned};
use serde_json::{Value as JsonValue, json};
use sha2::{Digest, Sha256};

use crate::ingest::audit::{
    AuditAction, AuditEvent, AuditEventId, AuditEventInput, AuditSubjectType, JCS_SAFE_INTEGER_MAX,
    RawAuditRow, RawAuditValue,
};
use crate::ingest::evidence::{
    EVIDENCE_MANIFEST_MEDIA_TYPE, EvidenceManifestLineage, EvidenceManifestReceipt,
    EvidenceManifestV1, EvidenceParametersV1, PDF_MEDIA_TYPE,
};
use crate::ingest::inspection::{FoundationInspectionParts, MAX_FOUNDATION_INSPECTION_ROWS};
use crate::ingest::job::{
    INGEST_CHECKPOINT_SCHEMA_V1, INGEST_EVENT_DETAILS_SCHEMA_V1, IngestBudgetV1,
    IngestCheckpointPhaseV1, IngestCheckpointV1, IngestEventDetailV1, IngestInputV1, IngestReceipt,
    IntakeQuarantineV1, JOB_DEADLINE_MS, LEASE_DURATION_MS, MAX_JOB_ATTEMPTS, StoredObjectV1,
    json_text,
};
use crate::ingest::{
    FoundationAdmissionState, FoundationContentObject, FoundationCounts, FoundationEvidenceContent,
    FoundationEvidenceLineage, FoundationInspection, FoundationIntakeEvent, FoundationJob,
    FoundationJobTerminalReason, FoundationSheet, ProjectReceipt, project_name_is_valid,
};
use crate::{
    ActorId, DataClass, DocumentId, EvidenceId, HeleosError, IdempotencyKey, IngestEventId,
    IngestOutcome, JobId, JobState, PageMetadata, ProjectId, Result, RevisionId, Sha256Digest,
    SheetId, Store, VaultInventory, VaultInventoryEntry,
};

const ONE_MIB: usize = 1024 * 1024;
const EIGHT_MIB: usize = 8 * 1024 * 1024;
const LATEST_JOB_AUDIT_SQL: &str = "SELECT project_id, action, after_json
     FROM audit_events
     WHERE subject_type = 'job' AND subject_id = ?1
     ORDER BY sequence DESC
     LIMIT 1";
const VAULT_INVENTORY_COUNT_SQL: &str = "WITH inventory_candidates(digest) AS (
         SELECT sha256 FROM content_objects
         UNION ALL
         SELECT CASE
                    WHEN json_valid(checkpoint_json)
                    THEN json_extract(checkpoint_json, '$.detail.original.digest')
                    ELSE 'invalid-checkpoint:' || id
                END
         FROM job_runs
         WHERE state IN ('queued', 'running', 'interrupted')
           AND (
               NOT json_valid(checkpoint_json)
               OR CASE
                      WHEN json_valid(checkpoint_json)
                      THEN json_extract(checkpoint_json, '$.phase') = 'vault_published'
                           OR (
                               state IN ('running', 'interrupted')
                               AND json_extract(checkpoint_json, '$.phase') =
                                   'processing_complete'
                           )
                      ELSE 0
                  END
           )
         UNION ALL
         SELECT json_extract(
                    checkpoint_json,
                    '$.detail.candidate.detail.manifest_object.digest'
                )
         FROM job_runs
         WHERE state IN ('running', 'interrupted')
           AND json_valid(checkpoint_json)
           AND json_extract(checkpoint_json, '$.phase') = 'processing_complete'
           AND json_extract(checkpoint_json, '$.detail.candidate.kind') = 'accepted'
     )
     SELECT COUNT(*)
     FROM (SELECT digest FROM inventory_candidates GROUP BY digest)";

pub(crate) enum IdempotencyLookup {
    MissingProject,
    Vacant,
    Existing(Box<JobRecord>),
}

#[derive(Clone, Debug)]
pub(crate) struct JobRecord {
    pub job_id: JobId,
    pub state: JobState,
    pub attempt: u32,
    pub lease_expires_at_ms: Option<i64>,
    pub created_at_ms: i64,
    pub deadline_at_ms: i64,
    pub input: IngestInputV1,
    pub budget: IngestBudgetV1,
    pub checkpoint: IngestCheckpointV1,
}

impl JobRecord {
    fn validate_for_scope(&self, project_id: ProjectId, key: &IdempotencyKey) -> Result<()> {
        self.input.validate()?;
        self.budget.validate_for_input(&self.input)?;
        self.checkpoint.validate_for_input(&self.input)?;
        if self.input.project_id != project_id || &self.input.idempotency_key != key {
            return Err(HeleosError::Integrity);
        }
        if self.deadline_at_ms != checked_time_add(self.created_at_ms, JOB_DEADLINE_MS)? {
            return Err(HeleosError::Integrity);
        }
        let terminal_receipt = match &self.checkpoint.phase {
            IngestCheckpointPhaseV1::Terminal { receipt } => Some(receipt),
            _ => None,
        };
        if terminal_receipt.is_some_and(|receipt| {
            receipt.authoritative_job_id != self.job_id || receipt.attempt != self.attempt
        }) {
            return Err(HeleosError::Integrity);
        }
        let state_shape_is_valid = match self.state {
            JobState::Queued => {
                self.attempt == 0
                    && self.lease_expires_at_ms.is_none()
                    && terminal_receipt.is_none()
            }
            JobState::Running => {
                (1..=MAX_JOB_ATTEMPTS).contains(&self.attempt)
                    && self.lease_expires_at_ms.is_some()
                    && terminal_receipt.is_none()
            }
            JobState::Interrupted => {
                (1..MAX_JOB_ATTEMPTS).contains(&self.attempt)
                    && self.lease_expires_at_ms.is_none()
                    && terminal_receipt.is_none()
            }
            JobState::Succeeded => {
                (1..=MAX_JOB_ATTEMPTS).contains(&self.attempt)
                    && self.lease_expires_at_ms.is_none()
                    && terminal_receipt.is_some()
            }
            JobState::Failed => {
                (1..=MAX_JOB_ATTEMPTS).contains(&self.attempt)
                    && self.lease_expires_at_ms.is_none()
                    && terminal_receipt.is_none()
            }
            JobState::Cancelled => {
                self.attempt <= MAX_JOB_ATTEMPTS
                    && self.lease_expires_at_ms.is_none()
                    && terminal_receipt.is_none()
            }
        };
        if !state_shape_is_valid {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub(crate) struct AdmissionRecord {
    pub digest: Sha256Digest,
    pub byte_length: u64,
    pub media_type: String,
    pub admission_state: String,
    pub vault_key: String,
    pub quarantine: Option<IntakeQuarantineV1>,
}

#[derive(Clone, Debug)]
pub(crate) struct QuotaSnapshot {
    pub project_overall_bytes: u64,
    pub store_evidence_bytes: u64,
    pub project_evidence_bytes: u64,
    pub store_quarantine_bytes: u64,
    pub project_quarantine_bytes: u64,
    pub admission: Option<AdmissionRecord>,
    pub store_overall_accounted: bool,
    pub project_overall_accounted: bool,
    pub store_evidence_accounted: bool,
    pub project_evidence_accounted: bool,
    pub store_quarantine_accounted: bool,
    pub project_quarantine_accounted: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ReservedMedia {
    Pdf,
    EvidenceManifest,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ReservedObject {
    byte_length: u64,
    media: ReservedMedia,
}

#[derive(Default)]
struct NonterminalReservations {
    store_overall: BTreeMap<Sha256Digest, ReservedObject>,
    project_overall: BTreeMap<Sha256Digest, ReservedObject>,
    store_evidence: BTreeMap<Sha256Digest, ReservedObject>,
    project_evidence: BTreeMap<Sha256Digest, ReservedObject>,
    store_quarantine: BTreeMap<Sha256Digest, ReservedObject>,
    project_quarantine: BTreeMap<Sha256Digest, ReservedObject>,
}

#[derive(Clone, Copy)]
enum ProjectReservationCategory {
    Overall,
    Evidence,
    Quarantine,
}

impl NonterminalReservations {
    fn reserve_unadmitted_record(
        &mut self,
        project_id: ProjectId,
        record: &JobRecord,
    ) -> Result<()> {
        let reserve_for_project = record.input.project_id == project_id;
        match &record.checkpoint.phase {
            IngestCheckpointPhaseV1::PreflightRejected { .. } => {}
            IngestCheckpointPhaseV1::VaultPublished { original } => {
                let reserved = ReservedObject {
                    byte_length: original.byte_length,
                    media: ReservedMedia::Pdf,
                };
                insert_reservation(&mut self.store_overall, original.digest, reserved)?;
                insert_reservation(&mut self.store_quarantine, original.digest, reserved)?;
                if reserve_for_project {
                    insert_reservation(&mut self.project_overall, original.digest, reserved)?;
                    insert_reservation(&mut self.project_quarantine, original.digest, reserved)?;
                }
            }
            IngestCheckpointPhaseV1::ProcessingComplete {
                original,
                candidate,
            } => match candidate {
                crate::ingest::job::ProcessingCandidateV1::Accepted {
                    manifest_object, ..
                } => {
                    let original = ReservedObject {
                        byte_length: original.byte_length,
                        media: ReservedMedia::Pdf,
                    };
                    let manifest = ReservedObject {
                        byte_length: manifest_object.byte_length,
                        media: ReservedMedia::EvidenceManifest,
                    };
                    insert_reservation(&mut self.store_evidence, manifest_object.digest, manifest)?;
                    insert_reservation(
                        &mut self.store_overall,
                        record.input.content_sha256.ok_or(HeleosError::Integrity)?,
                        original,
                    )?;
                    insert_reservation(&mut self.store_overall, manifest_object.digest, manifest)?;
                    if reserve_for_project {
                        insert_reservation(
                            &mut self.project_overall,
                            record.input.content_sha256.ok_or(HeleosError::Integrity)?,
                            original,
                        )?;
                        insert_reservation(
                            &mut self.project_overall,
                            manifest_object.digest,
                            manifest,
                        )?;
                        insert_reservation(
                            &mut self.project_evidence,
                            manifest_object.digest,
                            manifest,
                        )?;
                    }
                }
                crate::ingest::job::ProcessingCandidateV1::Quarantined { .. } => {
                    let reserved = ReservedObject {
                        byte_length: original.byte_length,
                        media: ReservedMedia::Pdf,
                    };
                    insert_reservation(&mut self.store_overall, original.digest, reserved)?;
                    insert_reservation(&mut self.store_quarantine, original.digest, reserved)?;
                    if reserve_for_project {
                        insert_reservation(&mut self.project_overall, original.digest, reserved)?;
                        insert_reservation(
                            &mut self.project_quarantine,
                            original.digest,
                            reserved,
                        )?;
                    }
                }
            },
            IngestCheckpointPhaseV1::Terminal { .. } => return Err(HeleosError::Integrity),
        }
        Ok(())
    }

    fn reserve_admitted_original(
        &mut self,
        project_id: ProjectId,
        record: &JobRecord,
        original: ReservedObject,
        admission: &AdmissionRecord,
        accepted_manifest: Option<(Sha256Digest, ReservedObject)>,
    ) -> Result<()> {
        insert_reservation(&mut self.store_overall, admission.digest, original)?;
        match admission.admission_state.as_str() {
            "quarantined" => {
                if accepted_manifest.is_some() {
                    return Err(HeleosError::Integrity);
                }
                insert_reservation(&mut self.store_quarantine, admission.digest, original)?;
                if record.input.project_id == project_id {
                    insert_reservation(&mut self.project_overall, admission.digest, original)?;
                    insert_reservation(&mut self.project_quarantine, admission.digest, original)?;
                }
            }
            "accepted" => {
                let (manifest_digest, manifest) =
                    accepted_manifest.ok_or(HeleosError::Integrity)?;
                insert_reservation(&mut self.store_overall, manifest_digest, manifest)?;
                insert_reservation(&mut self.store_evidence, manifest_digest, manifest)?;
                if record.input.project_id == project_id {
                    insert_reservation(&mut self.project_overall, admission.digest, original)?;
                    insert_reservation(&mut self.project_overall, manifest_digest, manifest)?;
                    insert_reservation(&mut self.project_evidence, manifest_digest, manifest)?;
                }
            }
            _ => return Err(HeleosError::Integrity),
        }
        Ok(())
    }
}

fn insert_reservation(
    reservations: &mut BTreeMap<Sha256Digest, ReservedObject>,
    digest: Sha256Digest,
    value: ReservedObject,
) -> Result<()> {
    if let Some(existing) = reservations.insert(digest, value)
        && existing != value
    {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn validate_reserved_admission(
    admission: &AdmissionRecord,
    reserved: ReservedObject,
) -> Result<()> {
    let expected_media = match reserved.media {
        ReservedMedia::Pdf => PDF_MEDIA_TYPE,
        ReservedMedia::EvidenceManifest => EVIDENCE_MANIFEST_MEDIA_TYPE,
    };
    if admission.byte_length != reserved.byte_length
        || admission.media_type != expected_media
        || admission.vault_key != crate::Vault::object_key(admission.digest)
        || (reserved.media == ReservedMedia::EvidenceManifest
            && (admission.admission_state != "accepted" || admission.quarantine.is_some()))
    {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn bounded_nonterminal_job_count(value: i64) -> Result<usize> {
    let count = usize::try_from(value).map_err(|_| HeleosError::Integrity)?;
    if count > MAX_FOUNDATION_INSPECTION_ROWS {
        return Err(HeleosError::ResourceLimit);
    }
    Ok(count)
}

fn bounded_vault_inventory_count(value: i64) -> Result<usize> {
    let count = usize::try_from(value).map_err(|_| HeleosError::Integrity)?;
    if count > MAX_FOUNDATION_INSPECTION_ROWS {
        return Err(HeleosError::ResourceLimit);
    }
    Ok(count)
}

fn require_latest_job_audit(
    statement: &mut rusqlite::Statement<'_>,
    job_id: JobId,
    project_id: ProjectId,
    state: JobState,
    expected_after: &JsonValue,
) -> Result<()> {
    let mut rows = statement
        .query([job_id.as_uuid().to_string()])
        .map_err(|_| HeleosError::Database)?;
    let row = rows
        .next()
        .map_err(|_| HeleosError::Database)?
        .ok_or(HeleosError::Integrity)?;
    let audit_project_id = parse_uuid_text::<ProjectId>(row, 0, 36)?;
    let action = bounded_text(row, 1, 32)?;
    let after: JsonValue = parse_bounded_json(row, 2, ONE_MIB)?;
    let action_matches_state = match state {
        JobState::Queued => action == "job_created",
        JobState::Running => matches!(
            action.as_str(),
            "job_started" | "job_checkpointed" | "job_resumed"
        ),
        JobState::Interrupted => action == "job_interrupted",
        JobState::Succeeded | JobState::Failed | JobState::Cancelled => false,
    };
    if audit_project_id != project_id || !action_matches_state || &after != expected_after {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn insert_vault_inventory_object(
    inventory: &mut BTreeMap<Sha256Digest, (VaultInventoryEntry, ReservedMedia)>,
    object: &StoredObjectV1,
    media: ReservedMedia,
    declared_count: usize,
) -> Result<()> {
    object.validate()?;
    insert_vault_inventory_entry(
        inventory,
        VaultInventoryEntry {
            digest: object.digest,
            expected_byte_length: object.byte_length,
            vault_key: object.vault_key.clone(),
        },
        media,
        declared_count,
    )
}

fn insert_vault_inventory_entry(
    inventory: &mut BTreeMap<Sha256Digest, (VaultInventoryEntry, ReservedMedia)>,
    entry: VaultInventoryEntry,
    media: ReservedMedia,
    declared_count: usize,
) -> Result<()> {
    if entry.expected_byte_length > JCS_SAFE_INTEGER_MAX
        || entry.vault_key != crate::Vault::object_key(entry.digest)
    {
        return Err(HeleosError::Integrity);
    }
    if let Some(existing) = inventory.get(&entry.digest) {
        return if existing == &(entry, media) {
            Ok(())
        } else {
            Err(HeleosError::Integrity)
        };
    }
    if inventory.len() >= declared_count {
        return Err(HeleosError::Integrity);
    }
    inventory.insert(entry.digest, (entry, media));
    Ok(())
}

pub(crate) struct JobCreateCommand {
    pub job_id: JobId,
    pub project_id: ProjectId,
    pub input: IngestInputV1,
    pub budget: IngestBudgetV1,
    pub checkpoint: IngestCheckpointV1,
    pub actor: ActorId,
    pub created_at_ms: i64,
    pub deadline_at_ms: i64,
    pub audit_event_id: AuditEventId,
}

pub(crate) struct JobStartCommand {
    pub job_id: JobId,
    pub project_id: ProjectId,
    pub actor: ActorId,
    pub lease_owner: uuid::Uuid,
    pub now_ms: i64,
    pub lease_expires_at_ms: i64,
    pub audit_event_id: AuditEventId,
}

pub(crate) struct JobRecoverCommand {
    pub job_id: JobId,
    pub actor: ActorId,
    pub lease_owner: uuid::Uuid,
    pub now_ms: i64,
    pub expected_probe_provenance: crate::PdfProbeProvenance,
    pub interrupted_event_id: IngestEventId,
    pub audit_event_ids: [AuditEventId; 2],
}

pub(crate) enum JobRecoveryOutcome {
    Started {
        record: JobRecord,
        interrupted_event_id: Option<IngestEventId>,
    },
    Terminal(JobRecord),
    DeadlineExpired,
    AttemptLimit,
}

pub(crate) struct JobFailCommand {
    pub job_id: JobId,
    pub project_id: ProjectId,
    pub attempt: u32,
    pub actor: ActorId,
    pub now_ms: i64,
    pub audit_event_id: AuditEventId,
}

pub(crate) struct JobCheckpointCommand {
    pub job_id: JobId,
    pub project_id: ProjectId,
    pub actor: ActorId,
    pub attempt: u32,
    pub checkpoint: IngestCheckpointV1,
    pub now_ms: i64,
    pub audit_event_id: AuditEventId,
}

pub(crate) struct AcceptedIntakeCommand {
    pub job_id: JobId,
    pub project_id: ProjectId,
    pub attempt: u32,
    pub actor: ActorId,
    pub idempotency_key: IdempotencyKey,
    pub source_display: String,
    pub original: StoredObjectV1,
    pub manifest_object: StoredObjectV1,
    pub manifest: EvidenceManifestV1,
    pub pages: Vec<PageMetadata>,
    pub ingest_event_id: IngestEventId,
    pub source_record_id: String,
    pub evidence_id: EvidenceId,
    pub now_ms: i64,
    pub preexisting_vault_digests: Vec<Sha256Digest>,
    pub audit_event_ids: [AuditEventId; 3],
}

pub(crate) struct QuarantinedIntakeCommand {
    pub job_id: JobId,
    pub project_id: ProjectId,
    pub attempt: u32,
    pub actor: ActorId,
    pub idempotency_key: IdempotencyKey,
    pub source_display: String,
    pub content_sha256: Option<Sha256Digest>,
    pub byte_length: u64,
    pub retained: Option<StoredObjectV1>,
    pub quarantine: IntakeQuarantineV1,
    pub outcome: IngestOutcome,
    pub ingest_event_id: IngestEventId,
    pub source_record_id: String,
    pub now_ms: i64,
    pub preexisting_vault_digests: Vec<Sha256Digest>,
    pub audit_event_ids: [AuditEventId; 2],
}

pub(crate) struct ReplayAttemptCommand {
    pub authoritative_job_id: JobId,
    pub project_id: ProjectId,
    pub idempotency_key: IdempotencyKey,
    pub actor: ActorId,
    pub source_display: Option<String>,
    pub ingest_event_id: Option<IngestEventId>,
    pub source_record_id: Option<String>,
    pub preexisting_vault_digests: Vec<Sha256Digest>,
    pub now_ms: i64,
    pub audit_event_id: AuditEventId,
}

pub(crate) struct ConflictAttemptCommand {
    pub authoritative_job_id: JobId,
    pub project_id: ProjectId,
    pub submitted_input: IngestInputV1,
    pub submitted_budget: IngestBudgetV1,
    pub actor: ActorId,
    pub source_display: String,
    pub ingest_event_id: IngestEventId,
    pub source_record_id: String,
    pub now_ms: i64,
    pub audit_event_id: AuditEventId,
}

#[derive(Clone, Debug)]
pub(crate) struct RevisionEvidenceRow {
    pub lineage: EvidenceManifestLineage,
    pub document_id: DocumentId,
    pub revision_id: RevisionId,
    pub original: StoredObjectV1,
    pub manifest: StoredObjectV1,
    pub extraction_method: String,
    pub parameters: EvidenceParametersV1,
    pub review_state: String,
}

#[derive(Clone, Debug)]
pub(crate) struct RevisionEvidenceRows {
    pub evidence: Vec<RevisionEvidenceRow>,
    pub pages: Vec<PageMetadata>,
}

pub(crate) struct ProjectCreateCommand {
    pub project_id: ProjectId,
    pub name: String,
    pub actor: ActorId,
    pub data_class: DataClass,
    pub created_at_ms: i64,
    pub audit_event_id: AuditEventId,
}

impl Store {
    pub(crate) fn project_create_with_audit(
        &mut self,
        command: ProjectCreateCommand,
    ) -> Result<ProjectReceipt> {
        if !project_name_is_valid(&command.name) {
            return Err(HeleosError::InvalidId);
        }
        let project_id_text = command.project_id.as_uuid().to_string();
        let audit_event_id = command.audit_event_id;
        let created_at_ms = command.created_at_ms;
        self.with_immediate_transaction(|transaction| {
            transaction
                .execute(
                    "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                     VALUES (?1, ?2, ?3, ?4, ?5)",
                    params![
                        project_id_text,
                        command.name,
                        created_at_ms,
                        command.actor.as_str(),
                        data_class_text(command.data_class),
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            let after = json!({
                "data_class": data_class_text(command.data_class),
                "name": command.name,
                "project_id": project_id_text,
            });
            append_audit(
                transaction,
                AuditEventInput {
                    id: audit_event_id,
                    sequence: next_audit_sequence(transaction)?,
                    project_id: Some(command.project_id),
                    actor: command.actor,
                    action: AuditAction::ProjectCreated,
                    subject_type: AuditSubjectType::Project,
                    subject_id: project_id_text,
                    before: serde_json::Value::Null,
                    after,
                    reason: "project created".to_owned(),
                    occurred_at_ms: created_at_ms,
                    previous_hash: audit_head(transaction)?,
                },
            )?;
            Ok(ProjectReceipt {
                project_id: command.project_id,
                created_at_ms,
                audit_event_id,
            })
        })
    }

    pub(crate) fn idempotency_lookup(
        &self,
        project_id: ProjectId,
        key: &IdempotencyKey,
    ) -> Result<IdempotencyLookup> {
        let project = project_id.as_uuid().to_string();
        let exists = self
            .connection
            .query_row(
                "SELECT EXISTS(SELECT 1 FROM projects WHERE id = ?1)",
                [&project],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|_| HeleosError::Database)?;
        if exists != 1 {
            return Ok(IdempotencyLookup::MissingProject);
        }
        let mut statement = self
            .connection
            .prepare(
                "SELECT id, state, attempt, lease_expires_at_ms, created_at_ms, deadline_at_ms,
                        input_json, budget_json, checkpoint_json
                 FROM job_runs
                 WHERE project_id = ?1 AND kind = 'pdf_ingest' AND idempotency_key = ?2",
            )
            .map_err(|_| HeleosError::Database)?;
        let mut rows = statement
            .query(params![project, key.as_str()])
            .map_err(|_| HeleosError::Database)?;
        let Some(row) = rows.next().map_err(|_| HeleosError::Database)? else {
            return Ok(IdempotencyLookup::Vacant);
        };
        let job_id = parse_uuid_text::<JobId>(row, 0, 36)?;
        let state = parse_job_state(&bounded_text(row, 1, 16)?)?;
        let attempt = bounded_u32(row, 2, 0, 16)?;
        let lease_expires_at_ms = optional_nonnegative_i64(row, 3)?;
        let created_at_ms = nonnegative_i64(row, 4)?;
        let deadline_at_ms = nonnegative_i64(row, 5)?;
        let input: IngestInputV1 = parse_bounded_json(row, 6, ONE_MIB)?;
        let budget: IngestBudgetV1 = parse_bounded_json(row, 7, ONE_MIB)?;
        let checkpoint: IngestCheckpointV1 = parse_bounded_json(row, 8, EIGHT_MIB)?;
        if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
            return Err(HeleosError::Integrity);
        }
        let record = JobRecord {
            job_id,
            state,
            attempt,
            lease_expires_at_ms,
            created_at_ms,
            deadline_at_ms,
            input,
            budget,
            checkpoint,
        };
        record.validate_for_scope(project_id, key)?;
        Ok(IdempotencyLookup::Existing(Box::new(record)))
    }

    pub(crate) fn quota_snapshot(
        &self,
        project_id: ProjectId,
        digest: Option<Sha256Digest>,
    ) -> Result<QuotaSnapshot> {
        let project = project_id.as_uuid().to_string();
        let committed_store_evidence_bytes = checked_sum(
            &self.connection,
            "SELECT byte_length FROM content_objects
             WHERE media_type = 'application/vnd.heleos.evidence-manifest+json;version=1'
             ORDER BY sha256",
            [],
        )?;
        let committed_store_quarantine_bytes = checked_sum(
            &self.connection,
            "SELECT byte_length FROM content_objects
             WHERE admission_state = 'quarantined'
             ORDER BY sha256",
            [],
        )?;
        let committed_project_overall_bytes = checked_sum(
            &self.connection,
            "SELECT content.byte_length
             FROM content_objects AS content
             WHERE content.sha256 IN (
                 SELECT revision.content_sha256
                 FROM project_documents AS link
                 JOIN document_revisions AS revision ON revision.document_id = link.document_id
                 WHERE link.project_id = ?1
                 UNION
                 SELECT evidence.content_sha256 FROM evidence_objects AS evidence
                 WHERE evidence.project_id = ?1
                 UNION
                 SELECT event.content_sha256 FROM ingest_events AS event
                 WHERE event.project_id = ?1 AND event.content_sha256 IS NOT NULL
                   AND event.outcome LIKE 'quarantined_%'
             )
             ORDER BY content.sha256",
            [&project],
        )?;
        let committed_project_evidence_bytes = checked_sum(
            &self.connection,
            "SELECT content.byte_length
             FROM content_objects AS content
             WHERE content.sha256 IN (
                 SELECT evidence.content_sha256 FROM evidence_objects AS evidence
                 WHERE evidence.project_id = ?1
             )
             ORDER BY content.sha256",
            [&project],
        )?;
        let committed_project_quarantine_bytes = checked_sum(
            &self.connection,
            "SELECT content.byte_length
             FROM content_objects AS content
             WHERE content.sha256 IN (
                 SELECT event.content_sha256 FROM ingest_events AS event
                 WHERE event.project_id = ?1 AND event.content_sha256 IS NOT NULL
                   AND event.outcome LIKE 'quarantined_%'
             )
             ORDER BY content.sha256",
            [&project],
        )?;

        let reservations = self.nonterminal_reservations(project_id)?;
        let store_evidence_bytes = self
            .add_store_reservations(committed_store_evidence_bytes, &reservations.store_evidence)?;
        let store_quarantine_bytes = self.add_store_reservations(
            committed_store_quarantine_bytes,
            &reservations.store_quarantine,
        )?;
        let project_overall_bytes = self.add_project_reservations(
            project_id,
            committed_project_overall_bytes,
            &reservations.project_overall,
            ProjectReservationCategory::Overall,
        )?;
        let project_evidence_bytes = self.add_project_reservations(
            project_id,
            committed_project_evidence_bytes,
            &reservations.project_evidence,
            ProjectReservationCategory::Evidence,
        )?;
        let project_quarantine_bytes = self.add_project_reservations(
            project_id,
            committed_project_quarantine_bytes,
            &reservations.project_quarantine,
            ProjectReservationCategory::Quarantine,
        )?;

        let admission = match digest {
            Some(digest) => self.admission_record(digest)?,
            None => None,
        };
        let store_overall_accounted = digest.is_some_and(|digest| {
            admission.is_some() || reservations.store_overall.contains_key(&digest)
        });
        let store_evidence_accounted = digest.is_some_and(|digest| {
            admission.as_ref().is_some_and(|value| {
                value.media_type == EVIDENCE_MANIFEST_MEDIA_TYPE
                    && value.admission_state == "accepted"
            }) || reservations.store_evidence.contains_key(&digest)
        });
        let store_quarantine_accounted = digest.is_some_and(|digest| {
            admission
                .as_ref()
                .is_some_and(|value| value.admission_state == "quarantined")
                || reservations.store_quarantine.contains_key(&digest)
        });
        let project_overall_accounted = match digest {
            Some(digest) => {
                self.project_has_digest(project_id, digest, ProjectReservationCategory::Overall)?
                    || reservations.project_overall.contains_key(&digest)
            }
            None => false,
        };
        let project_evidence_accounted = match digest {
            Some(digest) => {
                self.project_has_digest(project_id, digest, ProjectReservationCategory::Evidence)?
                    || reservations.project_evidence.contains_key(&digest)
            }
            None => false,
        };
        let project_quarantine_accounted = match digest {
            Some(digest) => {
                self.project_has_digest(project_id, digest, ProjectReservationCategory::Quarantine)?
                    || reservations.project_quarantine.contains_key(&digest)
            }
            None => false,
        };
        Ok(QuotaSnapshot {
            project_overall_bytes,
            store_evidence_bytes,
            project_evidence_bytes,
            store_quarantine_bytes,
            project_quarantine_bytes,
            admission,
            store_overall_accounted,
            project_overall_accounted,
            store_evidence_accounted,
            project_evidence_accounted,
            store_quarantine_accounted,
            project_quarantine_accounted,
        })
    }

    fn nonterminal_job_count(&self) -> Result<usize> {
        let declared_count = self
            .connection
            .query_row(
                "SELECT COUNT(*) FROM job_runs
                 WHERE state IN ('queued', 'running', 'interrupted')",
                [],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|_| HeleosError::Database)?;
        bounded_nonterminal_job_count(declared_count)
    }

    fn visit_nonterminal_jobs<F>(&self, declared_count: usize, mut visit: F) -> Result<()>
    where
        F: FnMut(&JobRecord) -> Result<()>,
    {
        let fetch_limit = declared_count
            .checked_add(1)
            .and_then(|value| i64::try_from(value).ok())
            .ok_or(HeleosError::Integrity)?;
        let mut statement = self
            .connection
            .prepare(
                "SELECT job.id, job.project_id, job.idempotency_key, job.state, job.attempt,
                        job.lease_owner, job.lease_expires_at_ms, job.created_at_ms,
                        job.deadline_at_ms, job.input_json, job.budget_json,
                        job.checkpoint_json
                 FROM job_runs AS job
                 WHERE job.state IN ('queued', 'running', 'interrupted')
                 ORDER BY job.id
                 LIMIT ?1",
            )
            .map_err(|_| HeleosError::Database)?;
        let mut audit_statement = self
            .connection
            .prepare(LATEST_JOB_AUDIT_SQL)
            .map_err(|_| HeleosError::Database)?;
        let mut rows = statement
            .query([fetch_limit])
            .map_err(|_| HeleosError::Database)?;
        let mut observed_count = 0_usize;
        while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
            observed_count = observed_count
                .checked_add(1)
                .ok_or(HeleosError::Integrity)?;
            if observed_count > declared_count {
                return Err(HeleosError::Integrity);
            }
            let job_id = parse_uuid_text::<JobId>(row, 0, 36)?;
            let row_project_id = parse_uuid_text::<ProjectId>(row, 1, 36)?;
            let idempotency_key = IdempotencyKey::try_from(bounded_text(row, 2, 128)?)
                .map_err(|_| HeleosError::Integrity)?;
            let state_text = bounded_text(row, 3, 16)?;
            let state = parse_job_state(&state_text)?;
            let attempt = bounded_u32(row, 4, 0, MAX_JOB_ATTEMPTS)?;
            let lease_owner = optional_canonical_uuid_text(row, 5)?;
            let lease_expires_at_ms = optional_nonnegative_i64(row, 6)?;
            let created_at_ms = nonnegative_i64(row, 7)?;
            let deadline_at_ms = nonnegative_i64(row, 8)?;
            let input_json = bounded_text(row, 9, ONE_MIB)?;
            let budget_json = bounded_text(row, 10, ONE_MIB)?;
            let checkpoint_json = bounded_text(row, 11, EIGHT_MIB)?;
            let input: IngestInputV1 =
                serde_json::from_str(&input_json).map_err(|_| HeleosError::Integrity)?;
            let budget: IngestBudgetV1 =
                serde_json::from_str(&budget_json).map_err(|_| HeleosError::Integrity)?;
            let checkpoint: IngestCheckpointV1 =
                serde_json::from_str(&checkpoint_json).map_err(|_| HeleosError::Integrity)?;
            if json_text(&input, ONE_MIB)? != input_json
                || json_text(&budget, ONE_MIB)? != budget_json
                || json_text(&checkpoint, EIGHT_MIB)? != checkpoint_json
            {
                return Err(HeleosError::Integrity);
            }
            let record = JobRecord {
                job_id,
                state,
                attempt,
                lease_expires_at_ms,
                created_at_ms,
                deadline_at_ms,
                input,
                budget,
                checkpoint,
            };
            record.validate_for_scope(row_project_id, &idempotency_key)?;
            if (record.state == JobState::Running) != lease_owner.is_some() {
                return Err(HeleosError::Integrity);
            }
            if record.state == JobState::Queued
                && matches!(
                    &record.checkpoint.phase,
                    IngestCheckpointPhaseV1::ProcessingComplete { .. }
                )
            {
                return Err(HeleosError::Integrity);
            }
            let expected_audit_after = json!({
                "attempt": record.attempt,
                "budget_sha256": sha256_text(&budget_json),
                "checkpoint_sha256": sha256_text(&checkpoint_json),
                "deadline_at_ms": record.deadline_at_ms,
                "input_sha256": sha256_text(&input_json),
                "lease_expires_at_ms": record.lease_expires_at_ms,
                "lease_owner": lease_owner,
                "state": state_text,
                "terminal_result_ids": JsonValue::Null,
            });
            require_latest_job_audit(
                &mut audit_statement,
                record.job_id,
                row_project_id,
                record.state,
                &expected_audit_after,
            )?;
            visit(&record)?;
        }
        if observed_count != declared_count {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }

    fn nonterminal_reservations(&self, project_id: ProjectId) -> Result<NonterminalReservations> {
        let declared_count = self.nonterminal_job_count()?;
        let mut reservations = NonterminalReservations::default();
        self.visit_nonterminal_jobs(declared_count, |record| {
            let original = match &record.checkpoint.phase {
                IngestCheckpointPhaseV1::VaultPublished { original }
                | IngestCheckpointPhaseV1::ProcessingComplete { original, .. } => Some(original),
                IngestCheckpointPhaseV1::PreflightRejected { .. } => None,
                IngestCheckpointPhaseV1::Terminal { .. } => return Err(HeleosError::Integrity),
            };
            if let Some(original) = original
                && let Some(admission) = self.admission_record(original.digest)?
            {
                let reserved_original = ReservedObject {
                    byte_length: original.byte_length,
                    media: ReservedMedia::Pdf,
                };
                validate_reserved_admission(&admission, reserved_original)?;
                let accepted_manifest = if admission.admission_state == "accepted" {
                    Some(self.accepted_manifest_reservation(original.digest)?)
                } else {
                    None
                };
                reservations.reserve_admitted_original(
                    project_id,
                    record,
                    reserved_original,
                    &admission,
                    accepted_manifest,
                )?;
            } else {
                reservations.reserve_unadmitted_record(project_id, record)?;
            }
            Ok(())
        })?;
        Ok(reservations)
    }

    pub(crate) fn vault_inventory_rows(&self) -> Result<VaultInventory> {
        let live_job_count = self.nonterminal_job_count()?;
        let declared_count = self
            .connection
            .query_row(VAULT_INVENTORY_COUNT_SQL, [], |row| row.get::<_, i64>(0))
            .map_err(|_| HeleosError::Database)?;
        let declared_count = bounded_vault_inventory_count(declared_count)?;
        let fetch_limit = declared_count
            .checked_add(1)
            .and_then(|value| i64::try_from(value).ok())
            .ok_or(HeleosError::Integrity)?;
        let mut inventory = BTreeMap::new();

        {
            let mut statement = self
                .connection
                .prepare(
                    "SELECT sha256, byte_length, media_type, admission_state, vault_key,
                            quarantine_reason
                     FROM content_objects
                     ORDER BY sha256
                     LIMIT ?1",
                )
                .map_err(|_| HeleosError::Database)?;
            let mut rows = statement
                .query([fetch_limit])
                .map_err(|_| HeleosError::Database)?;
            let mut observed_content_count = 0_usize;
            while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
                observed_content_count = observed_content_count
                    .checked_add(1)
                    .ok_or(HeleosError::Integrity)?;
                if observed_content_count > declared_count {
                    return Err(HeleosError::Integrity);
                }
                let admission_state = match bounded_text(row, 3, 16)?.as_str() {
                    "accepted" => FoundationAdmissionState::Accepted,
                    "quarantined" => FoundationAdmissionState::Quarantined,
                    _ => return Err(HeleosError::Integrity),
                };
                let content = FoundationContentObject {
                    sha256: parse_digest_text(row, 0)?,
                    byte_length: nonnegative_u64(row, 1)?,
                    media_type: bounded_text(row, 2, 256)?,
                    admission_state,
                    vault_key: bounded_text(row, 4, 256)?,
                    quarantine: optional_bounded_json(row, 5, ONE_MIB)?,
                };
                content.validate()?;
                let media = match content.media_type.as_str() {
                    PDF_MEDIA_TYPE => ReservedMedia::Pdf,
                    EVIDENCE_MANIFEST_MEDIA_TYPE => ReservedMedia::EvidenceManifest,
                    _ => return Err(HeleosError::Integrity),
                };
                insert_vault_inventory_entry(
                    &mut inventory,
                    VaultInventoryEntry {
                        digest: content.sha256,
                        expected_byte_length: content.byte_length,
                        vault_key: content.vault_key,
                    },
                    media,
                    declared_count,
                )?;
            }
        }

        self.visit_nonterminal_jobs(live_job_count, |record| {
            match &record.checkpoint.phase {
                IngestCheckpointPhaseV1::PreflightRejected { .. } => {}
                IngestCheckpointPhaseV1::VaultPublished { original } => {
                    insert_vault_inventory_object(
                        &mut inventory,
                        original,
                        ReservedMedia::Pdf,
                        declared_count,
                    )?;
                }
                IngestCheckpointPhaseV1::ProcessingComplete {
                    original,
                    candidate,
                } => {
                    insert_vault_inventory_object(
                        &mut inventory,
                        original,
                        ReservedMedia::Pdf,
                        declared_count,
                    )?;
                    if let crate::ingest::job::ProcessingCandidateV1::Accepted {
                        manifest_object,
                        ..
                    } = candidate
                    {
                        insert_vault_inventory_object(
                            &mut inventory,
                            manifest_object,
                            ReservedMedia::EvidenceManifest,
                            declared_count,
                        )?;
                    }
                }
                IngestCheckpointPhaseV1::Terminal { .. } => {
                    return Err(HeleosError::Integrity);
                }
            }
            Ok(())
        })?;
        if inventory.len() != declared_count {
            return Err(HeleosError::Integrity);
        }
        VaultInventory::try_from_entries(inventory.into_values().map(|(entry, _)| entry))
    }

    fn accepted_manifest_reservation(
        &self,
        original: Sha256Digest,
    ) -> Result<(Sha256Digest, ReservedObject)> {
        let mut statement = self
            .connection
            .prepare(
                "SELECT DISTINCT evidence.content_sha256, content.byte_length,
                        content.media_type, content.admission_state, content.vault_key
                 FROM document_revisions AS revision
                 JOIN evidence_objects AS evidence
                   ON evidence.document_revision_id = revision.id
                 JOIN content_objects AS content ON content.sha256 = evidence.content_sha256
                 WHERE revision.content_sha256 = ?1
                   AND evidence.parent_content_sha256 = ?1
                   AND evidence.extraction_method = 'heleos.pdf-probe/v1'
                   AND evidence.review_state = 'accepted'
                 ORDER BY evidence.content_sha256
                 LIMIT 2",
            )
            .map_err(|_| HeleosError::Database)?;
        let mut rows = statement
            .query([original.to_string()])
            .map_err(|_| HeleosError::Database)?;
        let row = rows
            .next()
            .map_err(|_| HeleosError::Database)?
            .ok_or(HeleosError::Integrity)?;
        let digest = parse_digest_text(row, 0)?;
        let reserved = ReservedObject {
            byte_length: nonnegative_u64(row, 1)?,
            media: ReservedMedia::EvidenceManifest,
        };
        if bounded_text(row, 2, 256)? != EVIDENCE_MANIFEST_MEDIA_TYPE
            || bounded_text(row, 3, 16)? != "accepted"
            || bounded_text(row, 4, 256)? != crate::Vault::object_key(digest)
            || rows.next().map_err(|_| HeleosError::Database)?.is_some()
        {
            return Err(HeleosError::Integrity);
        }
        Ok((digest, reserved))
    }

    fn add_store_reservations(
        &self,
        mut committed: u64,
        reservations: &BTreeMap<Sha256Digest, ReservedObject>,
    ) -> Result<u64> {
        for (digest, reserved) in reservations {
            if let Some(admission) = self.admission_record(*digest)? {
                validate_reserved_admission(&admission, *reserved)?;
            } else {
                committed = committed
                    .checked_add(reserved.byte_length)
                    .ok_or(HeleosError::Integrity)?;
            }
        }
        Ok(committed)
    }

    fn add_project_reservations(
        &self,
        project_id: ProjectId,
        mut committed: u64,
        reservations: &BTreeMap<Sha256Digest, ReservedObject>,
        category: ProjectReservationCategory,
    ) -> Result<u64> {
        for (digest, reserved) in reservations {
            if self.project_has_digest(project_id, *digest, category)? {
                let admission = self
                    .admission_record(*digest)?
                    .ok_or(HeleosError::Integrity)?;
                validate_reserved_admission(&admission, *reserved)?;
            } else {
                committed = committed
                    .checked_add(reserved.byte_length)
                    .ok_or(HeleosError::Integrity)?;
            }
        }
        Ok(committed)
    }

    fn project_has_digest(
        &self,
        project_id: ProjectId,
        digest: Sha256Digest,
        category: ProjectReservationCategory,
    ) -> Result<bool> {
        let sql = match category {
            ProjectReservationCategory::Overall => {
                "SELECT EXISTS(
                    SELECT 1
                    FROM project_documents AS link
                    JOIN document_revisions AS revision ON revision.document_id = link.document_id
                    WHERE link.project_id = ?1 AND revision.content_sha256 = ?2
                    UNION ALL
                    SELECT 1 FROM evidence_objects
                    WHERE project_id = ?1 AND content_sha256 = ?2
                    UNION ALL
                    SELECT 1 FROM ingest_events
                    WHERE project_id = ?1 AND content_sha256 = ?2
                )"
            }
            ProjectReservationCategory::Evidence => {
                "SELECT EXISTS(
                    SELECT 1 FROM evidence_objects
                    WHERE project_id = ?1 AND content_sha256 = ?2
                )"
            }
            ProjectReservationCategory::Quarantine => {
                "SELECT EXISTS(
                    SELECT 1 FROM ingest_events
                    WHERE project_id = ?1 AND content_sha256 = ?2
                      AND outcome LIKE 'quarantined_%'
                )"
            }
        };
        let exists = self
            .connection
            .query_row(
                sql,
                params![project_id.as_uuid().to_string(), digest.to_string()],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|_| HeleosError::Database)?;
        match exists {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(HeleosError::Integrity),
        }
    }

    fn admission_record(&self, digest: Sha256Digest) -> Result<Option<AdmissionRecord>> {
        let mut statement = self
            .connection
            .prepare(
                "SELECT sha256, byte_length, media_type, admission_state, vault_key,
                        quarantine_reason
                 FROM content_objects WHERE sha256 = ?1",
            )
            .map_err(|_| HeleosError::Database)?;
        let mut rows = statement
            .query([digest.to_string()])
            .map_err(|_| HeleosError::Database)?;
        let Some(row) = rows.next().map_err(|_| HeleosError::Database)? else {
            return Ok(None);
        };
        let parsed_digest = parse_digest_text(row, 0)?;
        let byte_length = nonnegative_u64(row, 1)?;
        let media_type = bounded_text(row, 2, 80)?;
        let admission_state = bounded_text(row, 3, 16)?;
        let vault_key = bounded_text(row, 4, 256)?;
        let quarantine = match row.get_ref(5).map_err(|_| HeleosError::Database)? {
            ValueRef::Null => None,
            ValueRef::Text(bytes) if bytes.len() <= ONE_MIB => {
                let value = serde_json::from_slice::<IntakeQuarantineV1>(bytes)
                    .map_err(|_| HeleosError::Integrity)?;
                if crate::ingest::evidence::bounded_canonical_json(&value, ONE_MIB)?.as_slice()
                    != bytes
                {
                    return Err(HeleosError::Integrity);
                }
                Some(value)
            }
            _ => return Err(HeleosError::Integrity),
        };
        if rows.next().map_err(|_| HeleosError::Database)?.is_some() || parsed_digest != digest {
            return Err(HeleosError::Integrity);
        }
        Ok(Some(AdmissionRecord {
            digest,
            byte_length,
            media_type,
            admission_state,
            vault_key,
            quarantine,
        }))
    }

    pub(crate) fn job_create_with_checkpoint_and_audit(
        &mut self,
        command: JobCreateCommand,
    ) -> Result<()> {
        command.input.validate()?;
        command.budget.validate_for_input(&command.input)?;
        command.checkpoint.validate_for_input(&command.input)?;
        if command.input.project_id != command.project_id
            || command.deadline_at_ms != checked_time_add(command.created_at_ms, JOB_DEADLINE_MS)?
            || !matches!(
                command.checkpoint.phase,
                IngestCheckpointPhaseV1::PreflightRejected { .. }
                    | IngestCheckpointPhaseV1::VaultPublished { .. }
            )
        {
            return Err(HeleosError::Integrity);
        }
        let input_json = json_text(&command.input, ONE_MIB)?;
        let budget_json = json_text(&command.budget, ONE_MIB)?;
        let checkpoint_json = json_text(&command.checkpoint, EIGHT_MIB)?;
        self.with_immediate_transaction(|transaction| {
            transaction
                .execute(
                    "INSERT INTO job_runs
                        (id, project_id, kind, idempotency_key, state, attempt,
                         lease_owner, lease_expires_at_ms, deadline_at_ms, budget_json,
                         input_json, checkpoint_json, terminal_reason, created_at_ms,
                         updated_at_ms)
                     VALUES (?1, ?2, 'pdf_ingest', ?3, 'queued', 0, NULL, NULL, ?4,
                             ?5, ?6, ?7, NULL, ?8, ?8)",
                    params![
                        command.job_id.as_uuid().to_string(),
                        command.project_id.as_uuid().to_string(),
                        command.input.idempotency_key.as_str(),
                        command.deadline_at_ms,
                        budget_json,
                        input_json,
                        checkpoint_json,
                        command.created_at_ms,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            append_action(
                transaction,
                AppendAction {
                    id: command.audit_event_id,
                    project_id: command.project_id,
                    actor: command.actor,
                    action: AuditAction::JobCreated,
                    subject_type: AuditSubjectType::Job,
                    subject_id: command.job_id.as_uuid().to_string(),
                    before: JsonValue::Null,
                    after: job_audit_snapshot(transaction, command.job_id)?,
                    reason: "intake job created",
                    occurred_at_ms: command.created_at_ms,
                },
            )?;
            Ok(())
        })
    }

    pub(crate) fn job_start_or_resume_with_audit(
        &mut self,
        command: JobStartCommand,
    ) -> Result<u32> {
        if command.lease_expires_at_ms != checked_time_add(command.now_ms, LEASE_DURATION_MS)? {
            return Err(HeleosError::Integrity);
        }
        self.with_immediate_transaction(|transaction| {
            let before = job_audit_snapshot(transaction, command.job_id)?;
            let changed = transaction
                .execute(
                    "UPDATE job_runs
                     SET state = 'running', attempt = 1, lease_owner = ?2,
                         lease_expires_at_ms = ?3, updated_at_ms = ?4
                     WHERE id = ?1 AND project_id = ?5 AND state = 'queued' AND attempt = 0
                       AND deadline_at_ms > ?4",
                    params![
                        command.job_id.as_uuid().to_string(),
                        command.lease_owner.hyphenated().to_string(),
                        command.lease_expires_at_ms,
                        command.now_ms,
                        command.project_id.as_uuid().to_string(),
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            if changed != 1 {
                return Err(HeleosError::InvalidStateTransition);
            }
            append_action(
                transaction,
                AppendAction {
                    id: command.audit_event_id,
                    project_id: command.project_id,
                    actor: command.actor,
                    action: AuditAction::JobStarted,
                    subject_type: AuditSubjectType::Job,
                    subject_id: command.job_id.as_uuid().to_string(),
                    before,
                    after: job_audit_snapshot(transaction, command.job_id)?,
                    reason: "intake job started",
                    occurred_at_ms: command.now_ms,
                },
            )?;
            Ok(1)
        })
    }

    pub(crate) fn job_recover_expired_attempt_with_audit(
        &mut self,
        command: JobRecoverCommand,
    ) -> Result<JobRecoveryOutcome> {
        self.with_immediate_transaction(|transaction| {
            require_current_job_audit_anchor(transaction, command.job_id)?;
            let record = job_record_by_id(transaction, command.job_id)?;
            if record.input.expected_probe_provenance != command.expected_probe_provenance {
                return Err(HeleosError::Integrity);
            }
            let project_id = record.input.project_id;
            match record.state {
                JobState::Succeeded => return Ok(JobRecoveryOutcome::Terminal(record)),
                JobState::Failed | JobState::Cancelled | JobState::Interrupted => {
                    return Err(HeleosError::InvalidStateTransition);
                }
                JobState::Queued => {
                    let expires_at = match checked_time_add(command.now_ms, LEASE_DURATION_MS) {
                        Ok(value) => value,
                        Err(HeleosError::Integrity) if command.now_ms >= record.deadline_at_ms => {
                            command.now_ms
                        }
                        Err(error) => return Err(error),
                    };
                    let before = job_audit_snapshot(transaction, command.job_id)?;
                    let changed = transaction
                        .execute(
                            "UPDATE job_runs
                             SET state = 'running', attempt = 1, lease_owner = ?2,
                                 lease_expires_at_ms = ?3, updated_at_ms = ?4
                             WHERE id = ?1 AND state = 'queued' AND attempt = 0",
                            params![
                                command.job_id.as_uuid().to_string(),
                                command.lease_owner.hyphenated().to_string(),
                                expires_at,
                                command.now_ms,
                            ],
                        )
                        .map_err(|_| HeleosError::Database)?;
                    if changed != 1 {
                        return Err(HeleosError::InvalidStateTransition);
                    }
                    append_action(
                        transaction,
                        AppendAction {
                            id: command.audit_event_ids[0],
                            project_id,
                            actor: command.actor.clone(),
                            action: AuditAction::JobStarted,
                            subject_type: AuditSubjectType::Job,
                            subject_id: command.job_id.as_uuid().to_string(),
                            before,
                            after: job_audit_snapshot(transaction, command.job_id)?,
                            reason: "queued intake job started during resume",
                            occurred_at_ms: command.now_ms,
                        },
                    )?;
                    if command.now_ms >= record.deadline_at_ms {
                        fail_job_in_transaction(
                            transaction,
                            FailJob {
                                job_id: command.job_id,
                                project_id,
                                attempt: 1,
                                actor: &command.actor,
                                now_ms: command.now_ms,
                                terminal_reason: "deadline_expired",
                                audit_reason: "intake job deadline expired",
                                audit_event_id: command.audit_event_ids[1],
                            },
                        )?;
                        return Ok(JobRecoveryOutcome::DeadlineExpired);
                    }
                    return Ok(JobRecoveryOutcome::Started {
                        record: job_record_by_id(transaction, command.job_id)?,
                        interrupted_event_id: None,
                    });
                }
                JobState::Running => {}
            }

            if command.now_ms >= record.deadline_at_ms {
                fail_job_in_transaction(
                    transaction,
                    FailJob {
                        job_id: command.job_id,
                        project_id,
                        attempt: record.attempt,
                        actor: &command.actor,
                        now_ms: command.now_ms,
                        terminal_reason: "deadline_expired",
                        audit_reason: "intake job deadline expired",
                        audit_event_id: command.audit_event_ids[0],
                    },
                )?;
                return Ok(JobRecoveryOutcome::DeadlineExpired);
            }
            if record.lease_expires_at_ms.ok_or(HeleosError::Integrity)? > command.now_ms {
                return Err(HeleosError::LeaseUnavailable);
            }
            if record.attempt == MAX_JOB_ATTEMPTS {
                fail_job_in_transaction(
                    transaction,
                    FailJob {
                        job_id: command.job_id,
                        project_id,
                        attempt: record.attempt,
                        actor: &command.actor,
                        now_ms: command.now_ms,
                        terminal_reason: "attempt_limit",
                        audit_reason: "intake job attempt limit reached",
                        audit_event_id: command.audit_event_ids[0],
                    },
                )?;
                return Ok(JobRecoveryOutcome::AttemptLimit);
            }

            let before = job_audit_snapshot(transaction, command.job_id)?;
            let changed = transaction
                .execute(
                    "UPDATE job_runs
                     SET state = 'interrupted', lease_owner = NULL, lease_expires_at_ms = NULL,
                         updated_at_ms = ?2
                     WHERE id = ?1 AND state = 'running' AND attempt = ?3",
                    params![
                        command.job_id.as_uuid().to_string(),
                        command.now_ms,
                        i64::from(record.attempt),
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            if changed != 1 {
                return Err(HeleosError::InvalidStateTransition);
            }
            let checkpoint_json = json_text(&record.checkpoint, EIGHT_MIB)?;
            let details_json = json_text(
                &IngestEventDetailV1::Interrupted {
                    schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
                    attempt: record.attempt,
                    checkpoint_sha256: sha256_text(&checkpoint_json),
                },
                ONE_MIB,
            )?;
            transaction
                .execute(
                    "INSERT INTO ingest_events
                        (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
                         source_path, idempotency_key, actor, terminal_at_ms, details_json)
                     VALUES (?1, ?2, ?3, NULL, 'interrupted', ?4, ?5, '<redacted>',
                             ?6, ?7, ?8, ?9)",
                    params![
                        command.interrupted_event_id.as_uuid().to_string(),
                        project_id.as_uuid().to_string(),
                        command.job_id.as_uuid().to_string(),
                        i64::from(record.attempt),
                        record.input.source_display,
                        record.input.idempotency_key.as_str(),
                        command.actor.as_str(),
                        command.now_ms,
                        details_json,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            append_action(
                transaction,
                AppendAction {
                    id: command.audit_event_ids[0],
                    project_id,
                    actor: command.actor.clone(),
                    action: AuditAction::JobInterrupted,
                    subject_type: AuditSubjectType::Job,
                    subject_id: command.job_id.as_uuid().to_string(),
                    before,
                    after: job_audit_snapshot(transaction, command.job_id)?,
                    reason: "expired intake attempt interrupted",
                    occurred_at_ms: command.now_ms,
                },
            )?;
            let interrupted = job_audit_snapshot(transaction, command.job_id)?;
            let next_attempt = record
                .attempt
                .checked_add(1)
                .ok_or(HeleosError::Integrity)?;
            let expires_at = checked_time_add(command.now_ms, LEASE_DURATION_MS)?;
            let changed = transaction
                .execute(
                    "UPDATE job_runs
                     SET state = 'running', attempt = ?2, lease_owner = ?3,
                         lease_expires_at_ms = ?4, updated_at_ms = ?5
                     WHERE id = ?1 AND state = 'interrupted' AND attempt = ?6",
                    params![
                        command.job_id.as_uuid().to_string(),
                        i64::from(next_attempt),
                        command.lease_owner.hyphenated().to_string(),
                        expires_at,
                        command.now_ms,
                        i64::from(record.attempt),
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            if changed != 1 {
                return Err(HeleosError::InvalidStateTransition);
            }
            append_action(
                transaction,
                AppendAction {
                    id: command.audit_event_ids[1],
                    project_id,
                    actor: command.actor,
                    action: AuditAction::JobResumed,
                    subject_type: AuditSubjectType::Job,
                    subject_id: command.job_id.as_uuid().to_string(),
                    before: interrupted,
                    after: job_audit_snapshot(transaction, command.job_id)?,
                    reason: "interrupted intake job resumed",
                    occurred_at_ms: command.now_ms,
                },
            )?;
            Ok(JobRecoveryOutcome::Started {
                record: job_record_by_id(transaction, command.job_id)?,
                interrupted_event_id: Some(command.interrupted_event_id),
            })
        })
    }

    pub(crate) fn job_fail_with_audit(&mut self, command: JobFailCommand) -> Result<()> {
        self.with_immediate_transaction(|transaction| {
            require_current_job_audit_anchor(transaction, command.job_id)?;
            fail_job_in_transaction(
                transaction,
                FailJob {
                    job_id: command.job_id,
                    project_id: command.project_id,
                    attempt: command.attempt,
                    actor: &command.actor,
                    now_ms: command.now_ms,
                    terminal_reason: "internal_failure",
                    audit_reason: "trusted intake processing failure",
                    audit_event_id: command.audit_event_id,
                },
            )
        })
    }

    pub(crate) fn job_checkpoint_with_audit(
        &mut self,
        command: JobCheckpointCommand,
    ) -> Result<()> {
        if !matches!(
            &command.checkpoint.phase,
            IngestCheckpointPhaseV1::ProcessingComplete { .. }
                | IngestCheckpointPhaseV1::PreflightRejected {
                    content_sha256: Some(_),
                    quarantine: IntakeQuarantineV1 {
                        reason: crate::IntakeQuarantineReasonV1::ProjectAssociationQuota,
                        ..
                    },
                    ..
                }
        ) {
            return Err(HeleosError::Integrity);
        }
        command.checkpoint.validate()?;
        let checkpoint_json = json_text(&command.checkpoint, EIGHT_MIB)?;
        self.with_immediate_transaction(|transaction| {
            let before = job_audit_snapshot(transaction, command.job_id)?;
            let input = transaction
                .query_row(
                    "SELECT input_json FROM job_runs WHERE id = ?1 AND project_id = ?2",
                    params![
                        command.job_id.as_uuid().to_string(),
                        command.project_id.as_uuid().to_string(),
                    ],
                    |row| {
                        parse_bounded_json::<IngestInputV1>(row, 0, ONE_MIB)
                            .map_err(|_| rusqlite::Error::InvalidQuery)
                    },
                )
                .map_err(|_| HeleosError::Integrity)?;
            command.checkpoint.validate_for_input(&input)?;
            let changed = transaction
                .execute(
                    "UPDATE job_runs SET checkpoint_json = ?3, updated_at_ms = ?4
                     WHERE id = ?1 AND project_id = ?2 AND state = 'running'
                       AND attempt = ?5",
                    params![
                        command.job_id.as_uuid().to_string(),
                        command.project_id.as_uuid().to_string(),
                        checkpoint_json,
                        command.now_ms,
                        i64::from(command.attempt),
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            if changed != 1 {
                return Err(HeleosError::InvalidStateTransition);
            }
            append_action(
                transaction,
                AppendAction {
                    id: command.audit_event_id,
                    project_id: command.project_id,
                    actor: command.actor,
                    action: AuditAction::JobCheckpointed,
                    subject_type: AuditSubjectType::Job,
                    subject_id: command.job_id.as_uuid().to_string(),
                    before,
                    after: job_audit_snapshot(transaction, command.job_id)?,
                    reason: "intake processing checkpoint committed",
                    occurred_at_ms: command.now_ms,
                },
            )?;
            Ok(())
        })
    }

    pub(crate) fn accepted_intake_commit(
        &mut self,
        command: AcceptedIntakeCommand,
    ) -> Result<IngestReceipt> {
        command.original.validate()?;
        command.manifest_object.validate()?;
        command.manifest.validate()?;
        if command.original.digest != command.manifest.original.sha256
            || command.original.byte_length != command.manifest.original.byte_length
            || command.manifest_object.digest
                != Sha256Digest::hash_reader(command.manifest.canonical_bytes()?.as_slice())?
            || command.manifest_object.byte_length
                != u64::try_from(command.manifest.canonical_bytes()?.len())
                    .map_err(|_| HeleosError::Integrity)?
            || command.pages != command.manifest.pages
            || command.source_display.is_empty()
            || command.source_display.len() > 4096
        {
            return Err(HeleosError::Integrity);
        }
        let parameters = EvidenceParametersV1::new(
            command.manifest.probe_provenance.clone(),
            command.manifest.requested_limits,
        )?;
        let parameters_json = json_text(&parameters, ONE_MIB)?;
        let manifest = command.manifest.clone();
        let original = command.original.clone();
        let manifest_object = command.manifest_object.clone();
        self.with_immediate_transaction(|transaction| {
            require_accepted_job_authority(transaction, &command)?;
            let before_job = job_audit_snapshot(transaction, command.job_id)?;
            insert_or_verify_content_object(
                transaction,
                &original,
                PDF_MEDIA_TYPE,
                "accepted",
                None,
                command.now_ms,
                &command.actor,
            )?;
            insert_or_verify_content_object(
                transaction,
                &manifest_object,
                EVIDENCE_MANIFEST_MEDIA_TYPE,
                "accepted",
                None,
                command.now_ms,
                &command.actor,
            )?;

            let document_id = manifest.document_id;
            let revision_id = manifest.revision_id;
            transaction
                .execute(
                    "INSERT OR IGNORE INTO documents (id, created_at_ms, created_by)
                     VALUES (?1, ?2, ?3)",
                    params![
                        document_id.as_digest().to_string(),
                        command.now_ms,
                        command.actor.as_str(),
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            let revision_inserted = transaction
                .execute(
                    "INSERT OR IGNORE INTO document_revisions
                        (id, document_id, content_sha256, created_at_ms, created_by)
                     VALUES (?1, ?2, ?3, ?4, ?5)",
                    params![
                        revision_id.as_digest().to_string(),
                        document_id.as_digest().to_string(),
                        original.digest.to_string(),
                        command.now_ms,
                        command.actor.as_str(),
                    ],
                )
                .map_err(|_| HeleosError::Database)?
                == 1;
            require_revision_authority(transaction, document_id, revision_id, original.digest)?;
            transaction
                .execute(
                    "INSERT OR IGNORE INTO project_documents
                        (project_id, document_id, linked_at_ms, linked_by)
                     VALUES (?1, ?2, ?3, ?4)",
                    params![
                        command.project_id.as_uuid().to_string(),
                        document_id.as_digest().to_string(),
                        command.now_ms,
                        command.actor.as_str(),
                    ],
                )
                .map_err(|_| HeleosError::Database)?;

            for page in &command.pages {
                let transform_json = json_text(&page.transform, ONE_MIB)?;
                transaction
                    .execute(
                        "INSERT OR IGNORE INTO sheets
                            (id, revision_id, zero_based_page_index, width_micropoints,
                             height_micropoints, rotation_degrees, unit,
                             parent_content_sha256, transform_json)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'pt', ?7, ?8)",
                        params![
                            page.page_id.as_digest().to_string(),
                            revision_id.as_digest().to_string(),
                            i64::from(page.index),
                            i64::try_from(page.width_micropoints)
                                .map_err(|_| HeleosError::Integrity)?,
                            i64::try_from(page.height_micropoints)
                                .map_err(|_| HeleosError::Integrity)?,
                            i64::from(page.rotation_degrees),
                            original.digest.to_string(),
                            transform_json,
                        ],
                    )
                    .map_err(|_| HeleosError::Database)?;
            }
            require_sheet_authority(transaction, revision_id, original.digest, &command.pages)?;

            let evidence_inserted = transaction
                .execute(
                    "INSERT OR IGNORE INTO evidence_objects
                        (id, project_id, job_id, document_revision_id, content_sha256,
                         parent_content_sha256, extraction_method, parameters_json,
                         review_state, created_at_ms)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'heleos.pdf-probe/v1', ?7,
                             'accepted', ?8)",
                    params![
                        command.evidence_id.as_uuid().to_string(),
                        command.project_id.as_uuid().to_string(),
                        command.job_id.as_uuid().to_string(),
                        revision_id.as_digest().to_string(),
                        manifest_object.digest.to_string(),
                        original.digest.to_string(),
                        parameters_json,
                        command.now_ms,
                    ],
                )
                .map_err(|_| HeleosError::Database)?
                == 1;
            let lineages = evidence_lineages(transaction, revision_id)?;
            let current_evidence_id = lineages
                .iter()
                .find(|lineage| lineage.project_id == command.project_id)
                .ok_or(HeleosError::Integrity)?
                .evidence_id;
            require_evidence_authority(
                transaction,
                command.project_id,
                revision_id,
                manifest_object.digest,
                original.digest,
                &parameters_json,
            )?;

            let outcome = if revision_inserted {
                IngestOutcome::AcceptedNew
            } else {
                IngestOutcome::AcceptedDuplicate
            };
            let mut preexisting = command.preexisting_vault_digests.clone();
            preexisting.sort_unstable();
            preexisting.dedup();
            let receipt = IngestReceipt {
                ingest_event_id: command.ingest_event_id,
                authoritative_job_id: command.job_id,
                attempt: command.attempt,
                outcome,
                content_sha256: Some(original.digest),
                byte_length: original.byte_length,
                quarantine: None,
                document_id: Some(document_id),
                revision_id: Some(revision_id),
                sheet_ids: command.pages.iter().map(|page| page.page_id).collect(),
                evidence_manifest: Some(EvidenceManifestReceipt {
                    manifest: manifest.clone(),
                    manifest_content_sha256: manifest_object.digest,
                    manifest_byte_length: manifest_object.byte_length,
                    manifest_media_type: EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
                    manifest_vault_key: manifest_object.vault_key.clone(),
                    original_vault_key: original.vault_key.clone(),
                    lineages,
                }),
                preexisting_vault_digests: preexisting,
            };
            receipt.validate()?;
            let checkpoint = IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::Terminal {
                    receipt: Box::new(receipt.clone()),
                },
            };
            let checkpoint_json = json_text(&checkpoint, EIGHT_MIB)?;
            let details_json = json_text(
                &IngestEventDetailV1::Accepted {
                    schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
                    attempt: command.attempt,
                    document_id,
                    revision_id,
                    evidence_id: current_evidence_id,
                },
                ONE_MIB,
            )?;
            let source_metadata =
                json_text(&json!({"schema": "heleos.source-record/v1"}), ONE_MIB)?;
            transaction
                .execute(
                    "INSERT INTO source_records
                        (id, project_id, job_id, source_name, source_path, content_sha256,
                         metadata_json, created_at_ms)
                     VALUES (?1, ?2, ?3, ?4, '<redacted>', ?5, ?6, ?7)",
                    params![
                        command.source_record_id,
                        command.project_id.as_uuid().to_string(),
                        command.job_id.as_uuid().to_string(),
                        command.source_display,
                        original.digest.to_string(),
                        source_metadata,
                        command.now_ms,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            transaction
                .execute(
                    "INSERT INTO ingest_events
                        (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
                         source_path, idempotency_key, actor, terminal_at_ms, details_json)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, '<redacted>', ?8, ?9, ?10, ?11)",
                    params![
                        command.ingest_event_id.as_uuid().to_string(),
                        command.project_id.as_uuid().to_string(),
                        command.job_id.as_uuid().to_string(),
                        original.digest.to_string(),
                        ingest_outcome_text(outcome),
                        i64::from(command.attempt),
                        command.source_display,
                        command.idempotency_key.as_str(),
                        command.actor.as_str(),
                        command.now_ms,
                        details_json,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            let changed = transaction
                .execute(
                    "UPDATE job_runs
                     SET state = 'succeeded', lease_owner = NULL, lease_expires_at_ms = NULL,
                         checkpoint_json = ?4, terminal_reason = 'completed', updated_at_ms = ?5
                     WHERE id = ?1 AND project_id = ?2 AND state = 'running' AND attempt = ?3",
                    params![
                        command.job_id.as_uuid().to_string(),
                        command.project_id.as_uuid().to_string(),
                        i64::from(command.attempt),
                        checkpoint_json,
                        command.now_ms,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            if changed != 1 {
                return Err(HeleosError::InvalidStateTransition);
            }
            append_action(
                transaction,
                AppendAction {
                    id: command.audit_event_ids[0],
                    project_id: command.project_id,
                    actor: command.actor.clone(),
                    action: AuditAction::JobSucceeded,
                    subject_type: AuditSubjectType::Job,
                    subject_id: command.job_id.as_uuid().to_string(),
                    before: before_job,
                    after: job_audit_snapshot(transaction, command.job_id)?,
                    reason: "intake job completed",
                    occurred_at_ms: command.now_ms,
                },
            )?;
            append_action(
                transaction,
                AppendAction {
                    id: command.audit_event_ids[1],
                    project_id: command.project_id,
                    actor: command.actor.clone(),
                    action: AuditAction::IngestAccepted,
                    subject_type: AuditSubjectType::IngestAttempt,
                    subject_id: command.ingest_event_id.as_uuid().to_string(),
                    before: JsonValue::Null,
                    after: json!({
                        "attempt": command.attempt,
                        "authoritative_job_id": command.job_id,
                        "content_sha256": original.digest,
                        "document_id": document_id,
                        "evidence_id": current_evidence_id,
                        "ingest_event_id": command.ingest_event_id,
                        "manifest_content_sha256": manifest_object.digest,
                        "revision_id": revision_id,
                    }),
                    reason: "PDF intake accepted",
                    occurred_at_ms: command.now_ms,
                },
            )?;
            if evidence_inserted {
                append_action(
                    transaction,
                    AppendAction {
                        id: command.audit_event_ids[2],
                        project_id: command.project_id,
                        actor: command.actor.clone(),
                        action: AuditAction::EvidenceCreated,
                        subject_type: AuditSubjectType::Evidence,
                        subject_id: current_evidence_id.as_uuid().to_string(),
                        before: JsonValue::Null,
                        after: json!({
                            "evidence_id": current_evidence_id,
                            "manifest_content_sha256": manifest_object.digest,
                            "revision_id": revision_id,
                        }),
                        reason: "canonical PDF evidence created",
                        occurred_at_ms: command.now_ms,
                    },
                )?;
            }
            Ok(receipt)
        })
    }

    pub(crate) fn quarantined_intake_commit(
        &mut self,
        command: QuarantinedIntakeCommand,
    ) -> Result<IngestReceipt> {
        command.quarantine.validate()?;
        if command.source_display.is_empty()
            || command.source_display.len() > 4096
            || command.source_display.chars().any(char::is_control)
            || command.source_record_id.is_empty()
            || command.source_record_id.len() > 256
            || command.outcome
                != crate::ingest::job::quarantine_ingest_outcome(&command.quarantine.reason)
        {
            return Err(HeleosError::Integrity);
        }
        if let Some(retained) = &command.retained {
            retained.validate()?;
            if command.content_sha256 != Some(retained.digest)
                || command.byte_length != retained.byte_length
                || !matches!(
                    command.quarantine.reason,
                    crate::IntakeQuarantineReasonV1::Pdf(_)
                        | crate::IntakeQuarantineReasonV1::EvidenceManifestQuota
                )
            {
                return Err(HeleosError::Integrity);
            }
        } else if matches!(
            command.quarantine.reason,
            crate::IntakeQuarantineReasonV1::Pdf(_)
                | crate::IntakeQuarantineReasonV1::EvidenceManifestQuota
        ) {
            return Err(HeleosError::Integrity);
        }
        let quarantine_json = json_text(&command.quarantine, ONE_MIB)?;
        self.with_immediate_transaction(|transaction| {
            require_quarantined_job_authority(transaction, &command)?;
            let before_job = job_audit_snapshot(transaction, command.job_id)?;
            if let Some(retained) = &command.retained {
                insert_or_verify_content_object(
                    transaction,
                    retained,
                    PDF_MEDIA_TYPE,
                    "quarantined",
                    Some(&quarantine_json),
                    command.now_ms,
                    &command.actor,
                )?;
            }
            let mut preexisting = command.preexisting_vault_digests.clone();
            preexisting.sort_unstable();
            preexisting.dedup();
            let receipt = IngestReceipt {
                ingest_event_id: command.ingest_event_id,
                authoritative_job_id: command.job_id,
                attempt: command.attempt,
                outcome: command.outcome,
                content_sha256: command.content_sha256,
                byte_length: command.byte_length,
                quarantine: Some(command.quarantine.clone()),
                document_id: None,
                revision_id: None,
                sheet_ids: Vec::new(),
                evidence_manifest: None,
                preexisting_vault_digests: preexisting,
            };
            receipt.validate()?;
            let terminal_checkpoint = IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::Terminal {
                    receipt: Box::new(receipt.clone()),
                },
            };
            let checkpoint_json = json_text(&terminal_checkpoint, EIGHT_MIB)?;
            let source_metadata =
                json_text(&json!({"schema": "heleos.source-record/v1"}), ONE_MIB)?;
            let retained_digest = command
                .retained
                .as_ref()
                .map(|object| object.digest.to_string());
            transaction
                .execute(
                    "INSERT INTO source_records
                        (id, project_id, job_id, source_name, source_path, content_sha256,
                         metadata_json, created_at_ms)
                     VALUES (?1, ?2, ?3, ?4, '<redacted>', ?5, ?6, ?7)",
                    params![
                        command.source_record_id,
                        command.project_id.as_uuid().to_string(),
                        command.job_id.as_uuid().to_string(),
                        command.source_display,
                        retained_digest,
                        source_metadata,
                        command.now_ms,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            let details_json = json_text(
                &IngestEventDetailV1::Quarantined {
                    schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
                    attempt: command.attempt,
                    content_sha256: command.content_sha256,
                    byte_length: command.byte_length,
                    quarantine: command.quarantine.clone(),
                },
                ONE_MIB,
            )?;
            transaction
                .execute(
                    "INSERT INTO ingest_events
                        (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
                         source_path, idempotency_key, actor, terminal_at_ms, details_json)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, '<redacted>', ?8, ?9, ?10, ?11)",
                    params![
                        command.ingest_event_id.as_uuid().to_string(),
                        command.project_id.as_uuid().to_string(),
                        command.job_id.as_uuid().to_string(),
                        retained_digest,
                        ingest_outcome_text(command.outcome),
                        i64::from(command.attempt),
                        command.source_display,
                        command.idempotency_key.as_str(),
                        command.actor.as_str(),
                        command.now_ms,
                        details_json,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            let changed = transaction
                .execute(
                    "UPDATE job_runs
                     SET state = 'succeeded', lease_owner = NULL, lease_expires_at_ms = NULL,
                         checkpoint_json = ?4, terminal_reason = 'completed', updated_at_ms = ?5
                     WHERE id = ?1 AND project_id = ?2 AND state = 'running' AND attempt = ?3",
                    params![
                        command.job_id.as_uuid().to_string(),
                        command.project_id.as_uuid().to_string(),
                        i64::from(command.attempt),
                        checkpoint_json,
                        command.now_ms,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            if changed != 1 {
                return Err(HeleosError::InvalidStateTransition);
            }
            append_action(
                transaction,
                AppendAction {
                    id: command.audit_event_ids[0],
                    project_id: command.project_id,
                    actor: command.actor.clone(),
                    action: AuditAction::JobSucceeded,
                    subject_type: AuditSubjectType::Job,
                    subject_id: command.job_id.as_uuid().to_string(),
                    before: before_job,
                    after: job_audit_snapshot(transaction, command.job_id)?,
                    reason: "intake job completed",
                    occurred_at_ms: command.now_ms,
                },
            )?;
            append_action(
                transaction,
                AppendAction {
                    id: command.audit_event_ids[1],
                    project_id: command.project_id,
                    actor: command.actor,
                    action: AuditAction::IngestQuarantined,
                    subject_type: AuditSubjectType::IngestAttempt,
                    subject_id: command.ingest_event_id.as_uuid().to_string(),
                    before: JsonValue::Null,
                    after: json!({
                        "attempt": command.attempt,
                        "authoritative_job_id": command.job_id,
                        "content_sha256": command.content_sha256,
                        "ingest_event_id": command.ingest_event_id,
                        "quarantine_sha256": sha256_text(&quarantine_json),
                    }),
                    reason: "PDF intake quarantined",
                    occurred_at_ms: command.now_ms,
                },
            )?;
            Ok(receipt)
        })
    }

    pub(crate) fn replay_attempt_commit(
        &mut self,
        command: ReplayAttemptCommand,
    ) -> Result<IngestReceipt> {
        let records_ingest_attempt = match (
            command.source_display.as_ref(),
            command.ingest_event_id,
            command.source_record_id.as_ref(),
        ) {
            (Some(source), Some(_), Some(record))
                if !source.is_empty()
                    && source.len() <= 4096
                    && !source.chars().any(char::is_control)
                    && !record.is_empty()
                    && record.len() <= 256 =>
            {
                true
            }
            (None, None, None) if command.preexisting_vault_digests.is_empty() => false,
            _ => return Err(HeleosError::Integrity),
        };
        self.with_immediate_transaction(|transaction| {
            let record = job_record_by_id(transaction, command.authoritative_job_id)?;
            record.validate_for_scope(command.project_id, &command.idempotency_key)?;
            if record.state != JobState::Succeeded {
                return Err(HeleosError::InvalidStateTransition);
            }
            let IngestCheckpointPhaseV1::Terminal {
                receipt: authoritative,
            } = &record.checkpoint.phase
            else {
                return Err(HeleosError::Integrity);
            };
            let authoritative_content = authoritative_event_content(
                transaction,
                command.project_id,
                command.authoritative_job_id,
                authoritative,
            )?;
            if !records_ingest_attempt {
                append_action(
                    transaction,
                    AppendAction {
                        id: command.audit_event_id,
                        project_id: command.project_id,
                        actor: command.actor,
                        action: AuditAction::IngestReplayed,
                        subject_type: AuditSubjectType::IngestAttempt,
                        subject_id: authoritative.ingest_event_id.as_uuid().to_string(),
                        before: JsonValue::Null,
                        after: json!({
                            "authoritative_attempt": authoritative.attempt,
                            "authoritative_event_id": authoritative.ingest_event_id,
                            "authoritative_job_id": authoritative.authoritative_job_id,
                            "checkpoint_sha256": sha256_text(&json_text(&record.checkpoint, EIGHT_MIB)?),
                        }),
                        reason: "terminal intake result observed during resume",
                        occurred_at_ms: command.now_ms,
                    },
                )?;
                return Ok((**authoritative).clone());
            }

            let ingest_event_id = command.ingest_event_id.ok_or(HeleosError::Integrity)?;
            let source_record_id = command.source_record_id.ok_or(HeleosError::Integrity)?;
            let source_display = command.source_display.ok_or(HeleosError::Integrity)?;
            let mut receipt = (**authoritative).clone();
            receipt.ingest_event_id = ingest_event_id;
            receipt.outcome = IngestOutcome::IdempotentReplay;
            receipt.preexisting_vault_digests = command.preexisting_vault_digests;
            receipt.validate()?;
            let source_metadata =
                json_text(&json!({"schema": "heleos.source-record/v1"}), ONE_MIB)?;
            transaction
                .execute(
                    "INSERT INTO source_records
                        (id, project_id, job_id, source_name, source_path, content_sha256,
                         metadata_json, created_at_ms)
                     VALUES (?1, ?2, ?3, ?4, '<redacted>', ?5, ?6, ?7)",
                    params![
                        source_record_id,
                        command.project_id.as_uuid().to_string(),
                        command.authoritative_job_id.as_uuid().to_string(),
                        source_display,
                        authoritative_content.map(|digest| digest.to_string()),
                        source_metadata,
                        command.now_ms,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            let details_json = json_text(
                &IngestEventDetailV1::Replay {
                    schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
                    authoritative_job_id: command.authoritative_job_id,
                    authoritative_attempt: authoritative.attempt,
                    authoritative_event_id: authoritative.ingest_event_id,
                },
                ONE_MIB,
            )?;
            transaction
                .execute(
                    "INSERT INTO ingest_events
                        (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
                         source_path, idempotency_key, actor, terminal_at_ms, details_json)
                     VALUES (?1, ?2, ?3, ?4, 'idempotent_replay', NULL, ?5, '<redacted>',
                             ?6, ?7, ?8, ?9)",
                    params![
                        ingest_event_id.as_uuid().to_string(),
                        command.project_id.as_uuid().to_string(),
                        command.authoritative_job_id.as_uuid().to_string(),
                        authoritative_content.map(|digest| digest.to_string()),
                        source_display,
                        command.idempotency_key.as_str(),
                        command.actor.as_str(),
                        command.now_ms,
                        details_json,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            append_action(
                transaction,
                AppendAction {
                    id: command.audit_event_id,
                    project_id: command.project_id,
                    actor: command.actor,
                    action: AuditAction::IngestReplayed,
                    subject_type: AuditSubjectType::IngestAttempt,
                    subject_id: ingest_event_id.as_uuid().to_string(),
                    before: JsonValue::Null,
                    after: json!({
                        "authoritative_attempt": authoritative.attempt,
                        "authoritative_event_id": authoritative.ingest_event_id,
                        "authoritative_job_id": command.authoritative_job_id,
                        "ingest_event_id": ingest_event_id,
                    }),
                    reason: "completed intake replayed",
                    occurred_at_ms: command.now_ms,
                },
            )?;
            Ok(receipt)
        })
    }

    pub(crate) fn conflict_attempt_commit(
        &mut self,
        command: ConflictAttemptCommand,
    ) -> Result<()> {
        command.submitted_input.validate()?;
        command
            .submitted_budget
            .validate_for_input(&command.submitted_input)?;
        if command.source_display.is_empty()
            || command.source_display.len() > 4096
            || command.source_display.chars().any(char::is_control)
            || command.source_record_id.is_empty()
            || command.source_record_id.len() > 256
        {
            return Err(HeleosError::Integrity);
        }
        self.with_immediate_transaction(|transaction| {
            let authoritative = job_record_by_id(transaction, command.authoritative_job_id)?;
            if authoritative.input.project_id != command.project_id
                || authoritative.input.idempotency_key != command.submitted_input.idempotency_key
            {
                return Err(HeleosError::Integrity);
            }
            let mismatching_fields = authoritative.input.frozen_mismatching_fields(
                &command.submitted_input,
                authoritative.budget == command.submitted_budget,
            );
            if mismatching_fields.is_empty() {
                return Err(HeleosError::Integrity);
            }
            let details_json = json_text(
                &IngestEventDetailV1::Conflict {
                    schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
                    authoritative_job_id: command.authoritative_job_id,
                    mismatching_fields: mismatching_fields.clone(),
                },
                ONE_MIB,
            )?;
            let source_metadata =
                json_text(&json!({"schema": "heleos.source-record/v1"}), ONE_MIB)?;
            transaction
                .execute(
                    "INSERT INTO source_records
                        (id, project_id, job_id, source_name, source_path, content_sha256,
                         metadata_json, created_at_ms)
                     VALUES (?1, ?2, ?3, ?4, '<redacted>', NULL, ?5, ?6)",
                    params![
                        command.source_record_id,
                        command.project_id.as_uuid().to_string(),
                        command.authoritative_job_id.as_uuid().to_string(),
                        command.source_display,
                        source_metadata,
                        command.now_ms,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            transaction
                .execute(
                    "INSERT INTO ingest_events
                        (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
                         source_path, idempotency_key, actor, terminal_at_ms, details_json)
                     VALUES (?1, ?2, ?3, NULL, 'denied_conflict', NULL, ?4, '<redacted>',
                             ?5, ?6, ?7, ?8)",
                    params![
                        command.ingest_event_id.as_uuid().to_string(),
                        command.project_id.as_uuid().to_string(),
                        command.authoritative_job_id.as_uuid().to_string(),
                        command.source_display,
                        command.submitted_input.idempotency_key.as_str(),
                        command.actor.as_str(),
                        command.now_ms,
                        details_json,
                    ],
                )
                .map_err(|_| HeleosError::Database)?;
            append_action(
                transaction,
                AppendAction {
                    id: command.audit_event_id,
                    project_id: command.project_id,
                    actor: command.actor,
                    action: AuditAction::IngestConflictDenied,
                    subject_type: AuditSubjectType::IngestAttempt,
                    subject_id: command.ingest_event_id.as_uuid().to_string(),
                    before: JsonValue::Null,
                    after: json!({
                        "authoritative_job_id": command.authoritative_job_id,
                        "ingest_event_id": command.ingest_event_id,
                        "mismatching_fields": mismatching_fields,
                    }),
                    reason: "idempotency conflict denied",
                    occurred_at_ms: command.now_ms,
                },
            )?;
            Ok(())
        })
    }

    pub(crate) fn revision_evidence_rows(
        &self,
        revision_id: RevisionId,
    ) -> Result<RevisionEvidenceRows> {
        let count = self
            .connection
            .query_row(
                "SELECT COUNT(*) FROM evidence_objects
                 WHERE document_revision_id = ?1",
                [revision_id.as_digest().to_string()],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|_| HeleosError::Database)?;
        let count = usize::try_from(count).map_err(|_| HeleosError::Integrity)?;
        if count == 0 {
            return Err(HeleosError::NotFound);
        }
        if count > 100_000 {
            return Err(HeleosError::ResourceLimit);
        }
        let page_count = self
            .connection
            .query_row(
                "SELECT COUNT(*) FROM sheets WHERE revision_id = ?1",
                [revision_id.as_digest().to_string()],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|_| HeleosError::Database)?;
        let page_count = usize::try_from(page_count).map_err(|_| HeleosError::Integrity)?;
        if page_count == 0 || page_count > 10_000 {
            return Err(if page_count == 0 {
                HeleosError::Integrity
            } else {
                HeleosError::ResourceLimit
            });
        }
        let evidence = self.revision_evidence_rows_after_count(revision_id, count)?;
        let pages = self.revision_page_rows_after_count(revision_id, page_count)?;
        Ok(RevisionEvidenceRows { evidence, pages })
    }

    fn revision_page_rows_after_count(
        &self,
        revision_id: RevisionId,
        count: usize,
    ) -> Result<Vec<PageMetadata>> {
        if count == 0 || count > 10_000 {
            return Err(HeleosError::Integrity);
        }
        let fetch_limit = count.checked_add(1).ok_or(HeleosError::Integrity)?;
        let mut statement = self
            .connection
            .prepare(
                "SELECT id, zero_based_page_index, width_micropoints,
                        height_micropoints, unit, rotation_degrees,
                        transform_json, parent_content_sha256
                 FROM sheets
                 WHERE revision_id = ?1
                 ORDER BY zero_based_page_index, id
                 LIMIT ?2",
            )
            .map_err(|_| HeleosError::Database)?;
        let mut rows = statement
            .query(params![
                revision_id.as_digest().to_string(),
                i64::try_from(fetch_limit).map_err(|_| HeleosError::Integrity)?,
            ])
            .map_err(|_| HeleosError::Database)?;
        let mut pages = Vec::with_capacity(count);
        while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
            if pages.len() == count {
                return Err(HeleosError::Integrity);
            }
            if bounded_text(row, 4, 2)? != "pt"
                || parse_digest_text(row, 7)? != *revision_id.as_digest()
            {
                return Err(HeleosError::Integrity);
            }
            pages.push(PageMetadata {
                index: bounded_u32(row, 1, 0, 9_999)?,
                page_id: parse_digest_id::<SheetId>(row, 0)?,
                width_micropoints: nonnegative_u64(row, 2)?,
                height_micropoints: nonnegative_u64(row, 3)?,
                unit: crate::PageUnit::Point,
                rotation_degrees: u16::try_from(bounded_u32(row, 5, 0, 270)?)
                    .map_err(|_| HeleosError::Integrity)?,
                transform: parse_bounded_json(row, 6, ONE_MIB)?,
            });
        }
        if pages.len() != count {
            return Err(HeleosError::Integrity);
        }
        crate::pdf::geometry::validate_page_metadata(
            &pages,
            *revision_id.as_digest(),
            crate::PdfLimits::default(),
        )?;
        Ok(pages)
    }

    fn revision_evidence_rows_after_count(
        &self,
        revision_id: RevisionId,
        expected_count: usize,
    ) -> Result<Vec<RevisionEvidenceRow>> {
        if expected_count == 0 || expected_count > 100_000 {
            return Err(HeleosError::Integrity);
        }
        let fetch_limit = expected_count
            .checked_add(1)
            .ok_or(HeleosError::Integrity)?;
        let mut statement = self
            .connection
            .prepare(
                "SELECT evidence.id, evidence.project_id, evidence.job_id,
                        revision.document_id, revision.id,
                        parent.sha256, parent.byte_length, parent.vault_key,
                        parent.media_type, parent.admission_state,
                        manifest.sha256, manifest.byte_length, manifest.vault_key,
                        manifest.media_type, manifest.admission_state,
                        evidence.extraction_method, evidence.parameters_json,
                        evidence.review_state
                 FROM evidence_objects AS evidence
                 JOIN document_revisions AS revision
                   ON revision.id = evidence.document_revision_id
                 JOIN content_objects AS parent
                   ON parent.sha256 = evidence.parent_content_sha256
                 JOIN content_objects AS manifest
                   ON manifest.sha256 = evidence.content_sha256
                 WHERE evidence.document_revision_id = ?1
                 ORDER BY evidence.project_id, evidence.id, evidence.job_id
                 LIMIT ?2",
            )
            .map_err(|_| HeleosError::Database)?;
        let mut rows = statement
            .query(params![
                revision_id.as_digest().to_string(),
                i64::try_from(fetch_limit).map_err(|_| HeleosError::Integrity)?,
            ])
            .map_err(|_| HeleosError::Database)?;
        let mut result = Vec::with_capacity(expected_count);
        while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
            if result.len() == expected_count {
                return Err(HeleosError::Integrity);
            }
            let evidence_id = parse_uuid_text::<EvidenceId>(row, 0, 36)?;
            let project_id = parse_uuid_text::<ProjectId>(row, 1, 36)?;
            let originating_job_id = parse_uuid_text::<JobId>(row, 2, 36)?;
            let document_id = parse_digest_id::<DocumentId>(row, 3)?;
            let stored_revision_id = parse_digest_id::<RevisionId>(row, 4)?;
            let original_digest = parse_digest_text(row, 5)?;
            let original = StoredObjectV1 {
                digest: original_digest,
                byte_length: nonnegative_u64(row, 6)?,
                vault_key: bounded_text(row, 7, 256)?,
            };
            if bounded_text(row, 8, 256)? != PDF_MEDIA_TYPE
                || bounded_text(row, 9, 16)? != "accepted"
            {
                return Err(HeleosError::Integrity);
            }
            let manifest_digest = parse_digest_text(row, 10)?;
            let manifest = StoredObjectV1 {
                digest: manifest_digest,
                byte_length: nonnegative_u64(row, 11)?,
                vault_key: bounded_text(row, 12, 256)?,
            };
            if original.byte_length > crate::PdfLimits::default().max_input_bytes
                || manifest.byte_length
                    > u64::try_from(crate::MAX_EVIDENCE_MANIFEST_BYTES)
                        .map_err(|_| HeleosError::Integrity)?
            {
                return Err(HeleosError::ResourceLimit);
            }
            if bounded_text(row, 13, 256)? != EVIDENCE_MANIFEST_MEDIA_TYPE
                || bounded_text(row, 14, 16)? != "accepted"
            {
                return Err(HeleosError::Integrity);
            }
            original.validate()?;
            manifest.validate()?;
            let extraction_method = bounded_text(row, 15, 256)?;
            let parameters: EvidenceParametersV1 = parse_bounded_json(row, 16, ONE_MIB)?;
            let review_state = bounded_text(row, 17, 16)?;
            result.push(RevisionEvidenceRow {
                lineage: EvidenceManifestLineage {
                    project_id,
                    evidence_id,
                    originating_job_id,
                },
                document_id,
                revision_id: stored_revision_id,
                original,
                manifest,
                extraction_method,
                parameters,
                review_state,
            });
        }
        if result.len() != expected_count {
            return Err(HeleosError::Integrity);
        }
        Ok(result)
    }

    pub(crate) fn foundation_inspection_rows(
        &self,
        project_id: ProjectId,
    ) -> Result<FoundationInspection> {
        const CONTENT_PREDICATE: &str = "content.sha256 IN (
                SELECT revision.content_sha256
                FROM project_documents AS project_document
                JOIN document_revisions AS revision
                  ON revision.document_id = project_document.document_id
                WHERE project_document.project_id = ?1
                UNION
                SELECT evidence.content_sha256
                FROM evidence_objects AS evidence
                WHERE evidence.project_id = ?1
                UNION
                SELECT event.content_sha256
                FROM ingest_events AS event
                JOIN content_objects AS retained
                  ON retained.sha256 = event.content_sha256
                WHERE event.project_id = ?1
                  AND retained.admission_state = 'quarantined'
            )";

        let project = project_id.as_uuid().to_string();
        match bounded_relation_count(
            &self.connection,
            "SELECT COUNT(*) FROM projects WHERE id = ?1",
            &project,
        )? {
            0 => return Err(HeleosError::NotFound),
            1 => {}
            _ => return Err(HeleosError::Integrity),
        }

        let content_count = bounded_relation_count(
            &self.connection,
            &format!("SELECT COUNT(*) FROM content_objects AS content WHERE {CONTENT_PREDICATE}"),
            &project,
        )?;
        let document_count = bounded_relation_count(
            &self.connection,
            "SELECT COUNT(*)
             FROM documents AS document
             JOIN project_documents AS project_document
               ON project_document.document_id = document.id
             WHERE project_document.project_id = ?1",
            &project,
        )?;
        let revision_count = bounded_relation_count(
            &self.connection,
            "SELECT COUNT(*)
             FROM document_revisions AS revision
             JOIN project_documents AS project_document
               ON project_document.document_id = revision.document_id
             WHERE project_document.project_id = ?1",
            &project,
        )?;
        let project_document_count = bounded_relation_count(
            &self.connection,
            "SELECT COUNT(*) FROM project_documents WHERE project_id = ?1",
            &project,
        )?;
        let sheet_count = bounded_relation_count(
            &self.connection,
            "SELECT COUNT(*)
             FROM sheets AS sheet
             JOIN document_revisions AS revision ON revision.id = sheet.revision_id
             JOIN project_documents AS project_document
               ON project_document.document_id = revision.document_id
             WHERE project_document.project_id = ?1",
            &project,
        )?;
        let evidence_count = bounded_relation_count(
            &self.connection,
            "SELECT COUNT(*) FROM evidence_objects WHERE project_id = ?1",
            &project,
        )?;
        let ingest_event_count = bounded_relation_count(
            &self.connection,
            "SELECT COUNT(*) FROM ingest_events WHERE project_id = ?1",
            &project,
        )?;
        let job_count = bounded_relation_count(
            &self.connection,
            "SELECT COUNT(*) FROM job_runs WHERE project_id = ?1",
            &project,
        )?;
        let audit_event_count = bounded_relation_count(
            &self.connection,
            "SELECT COUNT(*) FROM audit_events WHERE project_id = ?1",
            &project,
        )?;
        let counts = FoundationCounts {
            content_objects: content_count,
            documents: document_count,
            revisions: revision_count,
            project_documents: project_document_count,
            sheets: sheet_count,
            evidence_objects: evidence_count,
            ingest_events: ingest_event_count,
            jobs: job_count,
            audit_events: audit_event_count,
        };
        counts.validate()?;
        if document_count != project_document_count {
            return Err(HeleosError::Integrity);
        }
        let mut remaining = MAX_FOUNDATION_INSPECTION_ROWS;

        let content_objects = fetch_foundation_content(
            &self.connection,
            &project,
            CONTENT_PREDICATE,
            count_as_usize(content_count)?,
            &mut remaining,
        )?;
        let document_ids = fetch_project_documents(
            &self.connection,
            &project,
            count_as_usize(document_count)?,
            &mut remaining,
        )?;
        let revision_ids = fetch_project_revisions(
            &self.connection,
            &project,
            count_as_usize(revision_count)?,
            &mut remaining,
        )?;
        let sheets = fetch_project_sheets(
            &self.connection,
            &project,
            count_as_usize(sheet_count)?,
            &mut remaining,
        )?;
        let evidence = fetch_project_evidence(
            &self.connection,
            &project,
            count_as_usize(evidence_count)?,
            &mut remaining,
        )?;
        let intake_events = fetch_project_intake_events(
            &self.connection,
            &project,
            count_as_usize(ingest_event_count)?,
            &mut remaining,
        )?;
        let jobs = fetch_project_jobs(
            &self.connection,
            &project,
            count_as_usize(job_count)?,
            &mut remaining,
        )?;
        verify_project_audit_count(
            &self.connection,
            &project,
            count_as_usize(audit_event_count)?,
            &mut remaining,
        )?;

        FoundationInspection::new(
            project_id,
            FoundationInspectionParts {
                counts,
                content_objects,
                document_ids,
                revision_ids,
                sheets,
                evidence,
                intake_events,
                jobs,
            },
        )
    }

    pub(crate) fn audit_chain_rows(
        &self,
        mut consume: impl FnMut(RawAuditRow) -> Result<()>,
    ) -> Result<()> {
        let mut statement = self
            .connection
            .prepare(
                "SELECT id, sequence, project_id, actor, action, subject_type, subject_id,
                        before_json, after_json, reason, occurred_at_ms, previous_hash, event_hash
                 FROM audit_events
                 ORDER BY sequence, id",
            )
            .map_err(|_| HeleosError::Database)?;
        let mut rows = statement.query([]).map_err(|_| HeleosError::Database)?;
        while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
            consume(read_audit_row(row)?)?;
        }
        Ok(())
    }
}

fn bounded_relation_count(connection: &Connection, sql: &str, project_id: &str) -> Result<u64> {
    let mut statement = connection.prepare(sql).map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query([project_id])
        .map_err(|_| HeleosError::Database)?;
    let row = rows
        .next()
        .map_err(|_| HeleosError::Database)?
        .ok_or(HeleosError::Integrity)?;
    let count = match row.get_ref(0).map_err(|_| HeleosError::Database)? {
        ValueRef::Integer(value)
            if value >= 0
                && u64::try_from(value).is_ok_and(|value| value <= JCS_SAFE_INTEGER_MAX) =>
        {
            u64::try_from(value).map_err(|_| HeleosError::Integrity)?
        }
        _ => return Err(HeleosError::Integrity),
    };
    if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
        return Err(HeleosError::Integrity);
    }
    Ok(count)
}

fn count_as_usize(count: u64) -> Result<usize> {
    usize::try_from(count).map_err(|_| HeleosError::Integrity)
}

fn inspection_fetch_limit(remaining: usize, expected: usize) -> Result<i64> {
    if expected > remaining {
        return Err(HeleosError::Integrity);
    }
    i64::try_from(remaining.checked_add(1).ok_or(HeleosError::Integrity)?)
        .map_err(|_| HeleosError::Integrity)
}

fn finish_inspection_relation(
    observed: usize,
    expected: usize,
    remaining: &mut usize,
) -> Result<()> {
    if observed != expected {
        return Err(HeleosError::Integrity);
    }
    *remaining = remaining
        .checked_sub(observed)
        .ok_or(HeleosError::Integrity)?;
    Ok(())
}

fn fetch_foundation_content(
    connection: &Connection,
    project_id: &str,
    predicate: &str,
    expected: usize,
    remaining: &mut usize,
) -> Result<Vec<FoundationContentObject>> {
    let sql = format!(
        "SELECT content.sha256, content.byte_length, content.media_type,
                content.admission_state, content.vault_key, content.quarantine_reason
         FROM content_objects AS content
         WHERE {predicate}
         ORDER BY content.sha256
         LIMIT ?2"
    );
    let mut statement = connection
        .prepare(&sql)
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query(params![
            project_id,
            inspection_fetch_limit(*remaining, expected)?,
        ])
        .map_err(|_| HeleosError::Database)?;
    let mut values = Vec::with_capacity(expected);
    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
        if values.len() == expected {
            return Err(HeleosError::Integrity);
        }
        let admission_state = match bounded_text(row, 3, 16)?.as_str() {
            "accepted" => FoundationAdmissionState::Accepted,
            "quarantined" => FoundationAdmissionState::Quarantined,
            _ => return Err(HeleosError::Integrity),
        };
        values.push(FoundationContentObject {
            sha256: parse_digest_text(row, 0)?,
            byte_length: nonnegative_u64(row, 1)?,
            media_type: bounded_text(row, 2, 256)?,
            admission_state,
            vault_key: bounded_text(row, 4, 256)?,
            quarantine: optional_bounded_json(row, 5, ONE_MIB)?,
        });
    }
    finish_inspection_relation(values.len(), expected, remaining)?;
    Ok(values)
}

fn fetch_project_documents(
    connection: &Connection,
    project_id: &str,
    expected: usize,
    remaining: &mut usize,
) -> Result<Vec<DocumentId>> {
    let mut statement = connection
        .prepare(
            "SELECT document.id
             FROM documents AS document
             JOIN project_documents AS project_document
               ON project_document.document_id = document.id
             WHERE project_document.project_id = ?1
             ORDER BY document.id
             LIMIT ?2",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query(params![
            project_id,
            inspection_fetch_limit(*remaining, expected)?,
        ])
        .map_err(|_| HeleosError::Database)?;
    let mut values = Vec::with_capacity(expected);
    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
        if values.len() == expected {
            return Err(HeleosError::Integrity);
        }
        values.push(parse_digest_id(row, 0)?);
    }
    finish_inspection_relation(values.len(), expected, remaining)?;
    Ok(values)
}

fn fetch_project_revisions(
    connection: &Connection,
    project_id: &str,
    expected: usize,
    remaining: &mut usize,
) -> Result<Vec<RevisionId>> {
    let mut statement = connection
        .prepare(
            "SELECT revision.id, revision.document_id, revision.content_sha256
             FROM document_revisions AS revision
             JOIN project_documents AS project_document
               ON project_document.document_id = revision.document_id
             WHERE project_document.project_id = ?1
             ORDER BY revision.id
             LIMIT ?2",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query(params![
            project_id,
            inspection_fetch_limit(*remaining, expected)?,
        ])
        .map_err(|_| HeleosError::Database)?;
    let mut values = Vec::with_capacity(expected);
    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
        if values.len() == expected {
            return Err(HeleosError::Integrity);
        }
        let revision = parse_digest_id::<RevisionId>(row, 0)?;
        let document = parse_digest_id::<DocumentId>(row, 1)?;
        let content = parse_digest_text(row, 2)?;
        let (expected_document, expected_revision) = crate::canonical_document_ids(content);
        if document != expected_document || revision != expected_revision {
            return Err(HeleosError::Integrity);
        }
        values.push(revision);
    }
    finish_inspection_relation(values.len(), expected, remaining)?;
    Ok(values)
}

fn fetch_project_sheets(
    connection: &Connection,
    project_id: &str,
    expected: usize,
    remaining: &mut usize,
) -> Result<Vec<FoundationSheet>> {
    let mut statement = connection
        .prepare(
            "SELECT sheet.id, sheet.revision_id, sheet.zero_based_page_index,
                    sheet.width_micropoints, sheet.height_micropoints, sheet.unit,
                    sheet.rotation_degrees, sheet.transform_json,
                    sheet.parent_content_sha256
             FROM sheets AS sheet
             JOIN document_revisions AS revision ON revision.id = sheet.revision_id
             JOIN project_documents AS project_document
               ON project_document.document_id = revision.document_id
             WHERE project_document.project_id = ?1
             ORDER BY sheet.revision_id, sheet.zero_based_page_index, sheet.id
             LIMIT ?2",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query(params![
            project_id,
            inspection_fetch_limit(*remaining, expected)?,
        ])
        .map_err(|_| HeleosError::Database)?;
    let mut values = Vec::with_capacity(expected);
    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
        if values.len() == expected {
            return Err(HeleosError::Integrity);
        }
        if bounded_text(row, 5, 2)? != "pt" {
            return Err(HeleosError::Integrity);
        }
        values.push(FoundationSheet {
            sheet_id: parse_digest_id(row, 0)?,
            revision_id: parse_digest_id(row, 1)?,
            index: bounded_u32(row, 2, 0, 9_999)?,
            width_micropoints: nonnegative_u64(row, 3)?,
            height_micropoints: nonnegative_u64(row, 4)?,
            unit: crate::PageUnit::Point,
            rotation_degrees: u16::try_from(bounded_u32(row, 6, 0, 270)?)
                .map_err(|_| HeleosError::Integrity)?,
            transform: parse_bounded_json(row, 7, ONE_MIB)?,
            parent_content_sha256: parse_digest_text(row, 8)?,
        });
    }
    finish_inspection_relation(values.len(), expected, remaining)?;
    Ok(values)
}

fn fetch_project_evidence(
    connection: &Connection,
    project_id: &str,
    expected: usize,
    remaining: &mut usize,
) -> Result<Vec<FoundationEvidenceLineage>> {
    let mut statement = connection
        .prepare(
            "SELECT evidence.id, evidence.job_id, revision.document_id, revision.id,
                    original.sha256, original.byte_length, original.vault_key,
                    original.media_type, original.admission_state,
                    manifest.sha256, manifest.byte_length, manifest.vault_key,
                    manifest.media_type, manifest.admission_state,
                    evidence.extraction_method, evidence.parameters_json,
                    evidence.review_state
             FROM evidence_objects AS evidence
             JOIN document_revisions AS revision
               ON revision.id = evidence.document_revision_id
             JOIN content_objects AS original
               ON original.sha256 = evidence.parent_content_sha256
             JOIN content_objects AS manifest
               ON manifest.sha256 = evidence.content_sha256
             WHERE evidence.project_id = ?1
             ORDER BY revision.id, manifest.sha256, evidence.id
             LIMIT ?2",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query(params![
            project_id,
            inspection_fetch_limit(*remaining, expected)?,
        ])
        .map_err(|_| HeleosError::Database)?;
    let mut values = Vec::with_capacity(expected);
    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
        if values.len() == expected {
            return Err(HeleosError::Integrity);
        }
        if bounded_text(row, 8, 16)? != "accepted" || bounded_text(row, 13, 16)? != "accepted" {
            return Err(HeleosError::Integrity);
        }
        let parameters: EvidenceParametersV1 = parse_bounded_json(row, 15, ONE_MIB)?;
        values.push(FoundationEvidenceLineage {
            evidence_id: parse_uuid_text(row, 0, 36)?,
            originating_job_id: parse_uuid_text(row, 1, 36)?,
            document_id: parse_digest_id(row, 2)?,
            revision_id: parse_digest_id(row, 3)?,
            original: FoundationEvidenceContent {
                sha256: parse_digest_text(row, 4)?,
                byte_length: nonnegative_u64(row, 5)?,
                vault_key: bounded_text(row, 6, 256)?,
                media_type: bounded_text(row, 7, 256)?,
            },
            manifest: FoundationEvidenceContent {
                sha256: parse_digest_text(row, 9)?,
                byte_length: nonnegative_u64(row, 10)?,
                vault_key: bounded_text(row, 11, 256)?,
                media_type: bounded_text(row, 12, 256)?,
            },
            extraction_method: bounded_text(row, 14, 256)?,
            requested_limits: parameters.requested_limits,
            probe_provenance: parameters.probe_provenance,
            review_state: bounded_text(row, 16, 16)?,
        });
    }
    finish_inspection_relation(values.len(), expected, remaining)?;
    Ok(values)
}

fn fetch_project_intake_events(
    connection: &Connection,
    project_id: &str,
    expected: usize,
    remaining: &mut usize,
) -> Result<Vec<FoundationIntakeEvent>> {
    let mut statement = connection
        .prepare(
            "SELECT id, job_id, content_sha256, outcome, attempt, terminal_at_ms
             FROM ingest_events
             WHERE project_id = ?1
             ORDER BY terminal_at_ms, id
             LIMIT ?2",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query(params![
            project_id,
            inspection_fetch_limit(*remaining, expected)?,
        ])
        .map_err(|_| HeleosError::Database)?;
    let mut values = Vec::with_capacity(expected);
    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
        if values.len() == expected {
            return Err(HeleosError::Integrity);
        }
        values.push(FoundationIntakeEvent {
            ingest_event_id: parse_uuid_text(row, 0, 36)?,
            job_id: optional_uuid_text(row, 1, 36)?,
            content_sha256: optional_digest_text(row, 2)?,
            outcome: parse_ingest_outcome(&bounded_text(row, 3, 32)?)?,
            attempt: optional_bounded_u32(row, 4, 1, MAX_JOB_ATTEMPTS)?,
            terminal_at_ms: nonnegative_i64(row, 5)?,
        });
    }
    finish_inspection_relation(values.len(), expected, remaining)?;
    Ok(values)
}

fn fetch_project_jobs(
    connection: &Connection,
    project_id: &str,
    expected: usize,
    remaining: &mut usize,
) -> Result<Vec<FoundationJob>> {
    let mut statement = connection
        .prepare(
            "SELECT id, kind, state, attempt, created_at_ms, updated_at_ms, terminal_reason
             FROM job_runs
             WHERE project_id = ?1
             ORDER BY created_at_ms, id
             LIMIT ?2",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query(params![
            project_id,
            inspection_fetch_limit(*remaining, expected)?,
        ])
        .map_err(|_| HeleosError::Database)?;
    let mut values = Vec::with_capacity(expected);
    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
        if values.len() == expected {
            return Err(HeleosError::Integrity);
        }
        values.push(FoundationJob {
            job_id: parse_uuid_text(row, 0, 36)?,
            kind: bounded_text(row, 1, 16)?,
            state: parse_job_state(&bounded_text(row, 2, 16)?)?,
            attempt: bounded_u32(row, 3, 0, MAX_JOB_ATTEMPTS)?,
            created_at_ms: nonnegative_i64(row, 4)?,
            updated_at_ms: nonnegative_i64(row, 5)?,
            terminal_reason: optional_terminal_reason(row, 6)?,
        });
    }
    finish_inspection_relation(values.len(), expected, remaining)?;
    Ok(values)
}

fn verify_project_audit_count(
    connection: &Connection,
    project_id: &str,
    expected: usize,
    remaining: &mut usize,
) -> Result<()> {
    let mut statement = connection
        .prepare(
            "SELECT id, sequence
             FROM audit_events
             WHERE project_id = ?1
             ORDER BY sequence, id
             LIMIT ?2",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query(params![
            project_id,
            inspection_fetch_limit(*remaining, expected)?,
        ])
        .map_err(|_| HeleosError::Database)?;
    let mut observed = 0_usize;
    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
        if observed == expected {
            return Err(HeleosError::Integrity);
        }
        let _: AuditEventId = parse_uuid_text(row, 0, 36)?;
        if nonnegative_u64(row, 1)? == 0 {
            return Err(HeleosError::Integrity);
        }
        observed = observed.checked_add(1).ok_or(HeleosError::Integrity)?;
    }
    finish_inspection_relation(observed, expected, remaining)
}

fn require_accepted_job_authority(
    transaction: &Transaction<'_>,
    command: &AcceptedIntakeCommand,
) -> Result<()> {
    let (state, attempt, input, checkpoint) = {
        let mut statement = transaction
            .prepare(
                "SELECT state, attempt, input_json, checkpoint_json
                 FROM job_runs WHERE id = ?1 AND project_id = ?2",
            )
            .map_err(|_| HeleosError::Database)?;
        let mut rows = statement
            .query(params![
                command.job_id.as_uuid().to_string(),
                command.project_id.as_uuid().to_string(),
            ])
            .map_err(|_| HeleosError::Database)?;
        let row = rows
            .next()
            .map_err(|_| HeleosError::Database)?
            .ok_or(HeleosError::Integrity)?;
        let state = parse_job_state(&bounded_text(row, 0, 16)?)?;
        let attempt = bounded_u32(row, 1, 0, MAX_JOB_ATTEMPTS)?;
        let input = parse_bounded_json::<IngestInputV1>(row, 2, ONE_MIB)?;
        let checkpoint = parse_bounded_json::<IngestCheckpointV1>(row, 3, EIGHT_MIB)?;
        if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
            return Err(HeleosError::Integrity);
        }
        (state, attempt, input, checkpoint)
    };
    input.validate()?;
    checkpoint.validate_for_input(&input)?;
    if state != JobState::Running
        || attempt != command.attempt
        || input.project_id != command.project_id
        || input.idempotency_key != command.idempotency_key
        || input.source_display != command.source_display
    {
        return Err(HeleosError::Integrity);
    }
    match &checkpoint.phase {
        IngestCheckpointPhaseV1::ProcessingComplete {
            original,
            candidate:
                crate::ingest::job::ProcessingCandidateV1::Accepted {
                    manifest,
                    manifest_object,
                },
        } if original == &command.original
            && **manifest == command.manifest
            && manifest_object == &command.manifest_object =>
        {
            Ok(())
        }
        _ => Err(HeleosError::Integrity),
    }
}

fn require_quarantined_job_authority(
    transaction: &Transaction<'_>,
    command: &QuarantinedIntakeCommand,
) -> Result<()> {
    let record = job_record_by_id(transaction, command.job_id)?;
    record.validate_for_scope(command.project_id, &command.idempotency_key)?;
    if record.state != JobState::Running
        || record.attempt != command.attempt
        || record.input.source_display != command.source_display
    {
        return Err(HeleosError::Integrity);
    }
    match &record.checkpoint.phase {
        IngestCheckpointPhaseV1::PreflightRejected {
            content_sha256,
            byte_length,
            quarantine,
        } if command.retained.is_none()
            && *content_sha256 == command.content_sha256
            && *byte_length == command.byte_length
            && quarantine == &command.quarantine =>
        {
            Ok(())
        }
        IngestCheckpointPhaseV1::ProcessingComplete {
            original,
            candidate:
                crate::ingest::job::ProcessingCandidateV1::Quarantined {
                    outcome,
                    quarantine,
                },
        } if command.retained.as_ref() == Some(original)
            && *outcome == command.outcome
            && quarantine == &command.quarantine =>
        {
            Ok(())
        }
        _ => Err(HeleosError::Integrity),
    }
}

fn read_audit_row(row: &Row<'_>) -> Result<RawAuditRow> {
    Ok(RawAuditRow {
        id: bounded_value(row, 0, 36)?,
        sequence: bounded_value(row, 1, 0)?,
        project_id: bounded_value(row, 2, 36)?,
        actor: bounded_value(row, 3, 128)?,
        action: bounded_value(row, 4, 32)?,
        subject_type: bounded_value(row, 5, 32)?,
        subject_id: bounded_value(row, 6, 256)?,
        before_json: bounded_value(row, 7, 1024 * 1024)?,
        after_json: bounded_value(row, 8, 1024 * 1024)?,
        reason: bounded_value(row, 9, 1024)?,
        occurred_at_ms: bounded_value(row, 10, 0)?,
        previous_hash: bounded_value(row, 11, 64)?,
        event_hash: bounded_value(row, 12, 64)?,
    })
}

fn bounded_value(row: &Row<'_>, index: usize, max_text_bytes: usize) -> Result<RawAuditValue> {
    let value = row.get_ref(index).map_err(|_| HeleosError::Database)?;
    Ok(match value {
        ValueRef::Null => RawAuditValue::Null,
        ValueRef::Integer(value) => RawAuditValue::Integer(value),
        ValueRef::Text(bytes) if bytes.len() <= max_text_bytes => {
            match std::str::from_utf8(bytes) {
                Ok(value) => RawAuditValue::Text(value.to_owned()),
                Err(_) => RawAuditValue::Invalid,
            }
        }
        ValueRef::Real(_) | ValueRef::Text(_) | ValueRef::Blob(_) => RawAuditValue::Invalid,
    })
}

fn bounded_text(row: &Row<'_>, index: usize, maximum: usize) -> Result<String> {
    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
        ValueRef::Text(bytes) if bytes.len() <= maximum => std::str::from_utf8(bytes)
            .map(str::to_owned)
            .map_err(|_| HeleosError::Integrity),
        _ => Err(HeleosError::Integrity),
    }
}

fn parse_uuid_text<T>(row: &Row<'_>, index: usize, maximum: usize) -> Result<T>
where
    T: FromStr<Err = HeleosError>,
{
    let value = bounded_text(row, index, maximum)?;
    let uuid = uuid::Uuid::parse_str(&value).map_err(|_| HeleosError::Integrity)?;
    if uuid.hyphenated().to_string() != value {
        return Err(HeleosError::Integrity);
    }
    T::from_str(&value).map_err(|_| HeleosError::Integrity)
}

fn optional_uuid_text<T>(row: &Row<'_>, index: usize, maximum: usize) -> Result<Option<T>>
where
    T: FromStr<Err = HeleosError>,
{
    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
        ValueRef::Null => Ok(None),
        ValueRef::Text(bytes) if bytes.len() <= maximum => {
            let value = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
            let uuid = uuid::Uuid::parse_str(value).map_err(|_| HeleosError::Integrity)?;
            if uuid.hyphenated().to_string() != value {
                return Err(HeleosError::Integrity);
            }
            T::from_str(value)
                .map(Some)
                .map_err(|_| HeleosError::Integrity)
        }
        _ => Err(HeleosError::Integrity),
    }
}

fn optional_canonical_uuid_text(row: &Row<'_>, index: usize) -> Result<Option<String>> {
    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
        ValueRef::Null => Ok(None),
        ValueRef::Text(bytes) if bytes.len() <= 36 => {
            let value = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
            let parsed = uuid::Uuid::parse_str(value).map_err(|_| HeleosError::Integrity)?;
            if parsed.hyphenated().to_string() != value {
                return Err(HeleosError::Integrity);
            }
            Ok(Some(value.to_owned()))
        }
        _ => Err(HeleosError::Integrity),
    }
}

fn parse_digest_text(row: &Row<'_>, index: usize) -> Result<Sha256Digest> {
    Sha256Digest::from_str(&bounded_text(row, index, 64)?).map_err(|_| HeleosError::Integrity)
}

fn optional_digest_text(row: &Row<'_>, index: usize) -> Result<Option<Sha256Digest>> {
    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
        ValueRef::Null => Ok(None),
        ValueRef::Text(bytes) if bytes.len() <= 64 => {
            let value = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
            Sha256Digest::from_str(value)
                .map(Some)
                .map_err(|_| HeleosError::Integrity)
        }
        _ => Err(HeleosError::Integrity),
    }
}

fn parse_digest_id<T: From<Sha256Digest>>(row: &Row<'_>, index: usize) -> Result<T> {
    parse_digest_text(row, index).map(T::from)
}

fn nonnegative_i64(row: &Row<'_>, index: usize) -> Result<i64> {
    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
        ValueRef::Integer(value)
            if value >= 0
                && u64::try_from(value).is_ok_and(|value| value <= JCS_SAFE_INTEGER_MAX) =>
        {
            Ok(value)
        }
        _ => Err(HeleosError::Integrity),
    }
}

fn optional_nonnegative_i64(row: &Row<'_>, index: usize) -> Result<Option<i64>> {
    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
        ValueRef::Null => Ok(None),
        ValueRef::Integer(value)
            if value >= 0
                && u64::try_from(value).is_ok_and(|value| value <= JCS_SAFE_INTEGER_MAX) =>
        {
            Ok(Some(value))
        }
        _ => Err(HeleosError::Integrity),
    }
}

fn nonnegative_u64(row: &Row<'_>, index: usize) -> Result<u64> {
    u64::try_from(nonnegative_i64(row, index)?).map_err(|_| HeleosError::Integrity)
}

fn bounded_u32(row: &Row<'_>, index: usize, minimum: u32, maximum: u32) -> Result<u32> {
    let value = u32::try_from(nonnegative_i64(row, index)?).map_err(|_| HeleosError::Integrity)?;
    if !(minimum..=maximum).contains(&value) {
        return Err(HeleosError::Integrity);
    }
    Ok(value)
}

fn optional_bounded_u32(
    row: &Row<'_>,
    index: usize,
    minimum: u32,
    maximum: u32,
) -> Result<Option<u32>> {
    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
        ValueRef::Null => Ok(None),
        ValueRef::Integer(value) => {
            let value = u32::try_from(value).map_err(|_| HeleosError::Integrity)?;
            if !(minimum..=maximum).contains(&value) {
                return Err(HeleosError::Integrity);
            }
            Ok(Some(value))
        }
        _ => Err(HeleosError::Integrity),
    }
}

fn parse_job_state(value: &str) -> Result<JobState> {
    match value {
        "queued" => Ok(JobState::Queued),
        "running" => Ok(JobState::Running),
        "interrupted" => Ok(JobState::Interrupted),
        "succeeded" => Ok(JobState::Succeeded),
        "failed" => Ok(JobState::Failed),
        "cancelled" => Ok(JobState::Cancelled),
        _ => Err(HeleosError::Integrity),
    }
}

fn parse_ingest_outcome(value: &str) -> Result<IngestOutcome> {
    match value {
        "accepted_new" => Ok(IngestOutcome::AcceptedNew),
        "accepted_duplicate" => Ok(IngestOutcome::AcceptedDuplicate),
        "idempotent_replay" => Ok(IngestOutcome::IdempotentReplay),
        "quarantined_corrupt" => Ok(IngestOutcome::QuarantinedCorrupt),
        "quarantined_encrypted" => Ok(IngestOutcome::QuarantinedEncrypted),
        "quarantined_unsupported" => Ok(IngestOutcome::QuarantinedUnsupported),
        "quarantined_suspicious" => Ok(IngestOutcome::QuarantinedSuspicious),
        "quarantined_limit" => Ok(IngestOutcome::QuarantinedLimit),
        "interrupted" => Ok(IngestOutcome::Interrupted),
        "denied_conflict" => Ok(IngestOutcome::DeniedConflict),
        _ => Err(HeleosError::Integrity),
    }
}

fn optional_terminal_reason(
    row: &Row<'_>,
    index: usize,
) -> Result<Option<FoundationJobTerminalReason>> {
    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
        ValueRef::Null => Ok(None),
        ValueRef::Text(bytes) if bytes.len() <= 32 => {
            let value = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
            match value {
                "completed" => Ok(Some(FoundationJobTerminalReason::Completed)),
                "deadline_expired" => Ok(Some(FoundationJobTerminalReason::DeadlineExpired)),
                "attempt_limit" => Ok(Some(FoundationJobTerminalReason::AttemptLimit)),
                "internal_failure" => Ok(Some(FoundationJobTerminalReason::InternalFailure)),
                "cancelled" => Ok(Some(FoundationJobTerminalReason::Cancelled)),
                _ => Err(HeleosError::Integrity),
            }
        }
        _ => Err(HeleosError::Integrity),
    }
}

fn parse_bounded_json<T>(row: &Row<'_>, index: usize, maximum: usize) -> Result<T>
where
    T: DeserializeOwned + Serialize,
{
    let bytes = match row.get_ref(index).map_err(|_| HeleosError::Database)? {
        ValueRef::Text(bytes) if !bytes.is_empty() && bytes.len() <= maximum => bytes,
        _ => return Err(HeleosError::Integrity),
    };
    let value = serde_json::from_slice::<T>(bytes).map_err(|_| HeleosError::Integrity)?;
    if crate::ingest::evidence::bounded_canonical_json(&value, maximum)?.as_slice() != bytes {
        return Err(HeleosError::Integrity);
    }
    Ok(value)
}

fn optional_bounded_json<T>(row: &Row<'_>, index: usize, maximum: usize) -> Result<Option<T>>
where
    T: DeserializeOwned + Serialize,
{
    match row.get_ref(index).map_err(|_| HeleosError::Database)? {
        ValueRef::Null => Ok(None),
        ValueRef::Text(bytes) if !bytes.is_empty() && bytes.len() <= maximum => {
            let value = serde_json::from_slice::<T>(bytes).map_err(|_| HeleosError::Integrity)?;
            if crate::ingest::evidence::bounded_canonical_json(&value, maximum)?.as_slice() != bytes
            {
                return Err(HeleosError::Integrity);
            }
            Ok(Some(value))
        }
        _ => Err(HeleosError::Integrity),
    }
}

fn checked_sum<P: Params>(connection: &Connection, sql: &str, parameters: P) -> Result<u64> {
    let mut statement = connection.prepare(sql).map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query(parameters)
        .map_err(|_| HeleosError::Database)?;
    let mut total = 0_u64;
    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
        let value = match row.get_ref(0).map_err(|_| HeleosError::Database)? {
            ValueRef::Integer(value) if value >= 0 => {
                u64::try_from(value).map_err(|_| HeleosError::Integrity)?
            }
            _ => return Err(HeleosError::Integrity),
        };
        total = total.checked_add(value).ok_or(HeleosError::Integrity)?;
    }
    Ok(total)
}

fn checked_time_add(value: i64, delta: i64) -> Result<i64> {
    if value < 0 || delta < 0 {
        return Err(HeleosError::Integrity);
    }
    let result = value.checked_add(delta).ok_or(HeleosError::Integrity)?;
    if u64::try_from(result).map_err(|_| HeleosError::Integrity)? > JCS_SAFE_INTEGER_MAX {
        return Err(HeleosError::Integrity);
    }
    Ok(result)
}

fn sha256_text(value: &str) -> Sha256Digest {
    let mut hasher = Sha256::new();
    hasher.update(value.as_bytes());
    Sha256Digest::from_bytes(hasher.finalize().into())
}

fn job_record_by_id(transaction: &Transaction<'_>, job_id: JobId) -> Result<JobRecord> {
    let mut statement = transaction
        .prepare(
            "SELECT state, attempt, lease_expires_at_ms, created_at_ms, deadline_at_ms,
                    input_json, budget_json, checkpoint_json
             FROM job_runs WHERE id = ?1",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query([job_id.as_uuid().to_string()])
        .map_err(|_| HeleosError::Database)?;
    let row = rows
        .next()
        .map_err(|_| HeleosError::Database)?
        .ok_or(HeleosError::NotFound)?;
    let record = JobRecord {
        job_id,
        state: parse_job_state(&bounded_text(row, 0, 16)?)?,
        attempt: bounded_u32(row, 1, 0, MAX_JOB_ATTEMPTS)?,
        lease_expires_at_ms: optional_nonnegative_i64(row, 2)?,
        created_at_ms: nonnegative_i64(row, 3)?,
        deadline_at_ms: nonnegative_i64(row, 4)?,
        input: parse_bounded_json(row, 5, ONE_MIB)?,
        budget: parse_bounded_json(row, 6, ONE_MIB)?,
        checkpoint: parse_bounded_json(row, 7, EIGHT_MIB)?,
    };
    if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
        return Err(HeleosError::Integrity);
    }
    record.validate_for_scope(record.input.project_id, &record.input.idempotency_key)?;
    Ok(record)
}

fn authoritative_event_content(
    transaction: &Transaction<'_>,
    project_id: ProjectId,
    job_id: JobId,
    receipt: &IngestReceipt,
) -> Result<Option<Sha256Digest>> {
    let mut statement = transaction
        .prepare(
            "SELECT content_sha256, outcome
             FROM ingest_events
             WHERE id = ?1 AND project_id = ?2 AND job_id = ?3 AND attempt = ?4",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query(params![
            receipt.ingest_event_id.as_uuid().to_string(),
            project_id.as_uuid().to_string(),
            job_id.as_uuid().to_string(),
            i64::from(receipt.attempt),
        ])
        .map_err(|_| HeleosError::Database)?;
    let row = rows
        .next()
        .map_err(|_| HeleosError::Database)?
        .ok_or(HeleosError::Integrity)?;
    let content = match row.get_ref(0).map_err(|_| HeleosError::Database)? {
        ValueRef::Null => None,
        ValueRef::Text(bytes) if bytes.len() == 64 => {
            let text = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
            Some(Sha256Digest::from_str(text).map_err(|_| HeleosError::Integrity)?)
        }
        _ => return Err(HeleosError::Integrity),
    };
    if bounded_text(row, 1, 32)? != ingest_outcome_text(receipt.outcome)
        || rows.next().map_err(|_| HeleosError::Database)?.is_some()
    {
        return Err(HeleosError::Integrity);
    }
    Ok(content)
}

fn job_audit_snapshot(transaction: &Transaction<'_>, job_id: JobId) -> Result<JsonValue> {
    let mut statement = transaction
        .prepare(
            "SELECT project_id, idempotency_key, state, attempt, created_at_ms, deadline_at_ms,
                    lease_owner, lease_expires_at_ms, input_json, budget_json, checkpoint_json
             FROM job_runs WHERE id = ?1",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query([job_id.as_uuid().to_string()])
        .map_err(|_| HeleosError::Database)?;
    let row = rows
        .next()
        .map_err(|_| HeleosError::Database)?
        .ok_or(HeleosError::Integrity)?;
    let project_id = parse_uuid_text::<ProjectId>(row, 0, 36)?;
    let idempotency_key =
        IdempotencyKey::try_from(bounded_text(row, 1, 128)?).map_err(|_| HeleosError::Integrity)?;
    let state_text = bounded_text(row, 2, 16)?;
    let state = parse_job_state(&state_text)?;
    let attempt = bounded_u32(row, 3, 0, 16)?;
    let created_at_ms = nonnegative_i64(row, 4)?;
    let deadline_at_ms = nonnegative_i64(row, 5)?;
    let lease_owner = match row.get_ref(6).map_err(|_| HeleosError::Database)? {
        ValueRef::Null => None,
        ValueRef::Text(bytes) if bytes.len() <= 36 => {
            let text = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
            let parsed = uuid::Uuid::parse_str(text).map_err(|_| HeleosError::Integrity)?;
            if parsed.hyphenated().to_string() != text {
                return Err(HeleosError::Integrity);
            }
            Some(text.to_owned())
        }
        _ => return Err(HeleosError::Integrity),
    };
    let lease_expires_at_ms = optional_nonnegative_i64(row, 7)?;
    let input_json = bounded_text(row, 8, ONE_MIB)?;
    let budget_json = bounded_text(row, 9, ONE_MIB)?;
    let checkpoint_json = bounded_text(row, 10, EIGHT_MIB)?;
    let input: IngestInputV1 =
        serde_json::from_str(&input_json).map_err(|_| HeleosError::Integrity)?;
    let budget: IngestBudgetV1 =
        serde_json::from_str(&budget_json).map_err(|_| HeleosError::Integrity)?;
    let checkpoint: IngestCheckpointV1 =
        serde_json::from_str(&checkpoint_json).map_err(|_| HeleosError::Integrity)?;
    JobRecord {
        job_id,
        state,
        attempt,
        lease_expires_at_ms,
        created_at_ms,
        deadline_at_ms,
        input,
        budget,
        checkpoint: checkpoint.clone(),
    }
    .validate_for_scope(project_id, &idempotency_key)?;
    let terminal_result_ids = match &checkpoint.phase {
        IngestCheckpointPhaseV1::Terminal { receipt } => terminal_result_ids(receipt, project_id)?,
        _ => JsonValue::Null,
    };
    if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
        return Err(HeleosError::Integrity);
    }
    Ok(json!({
        "attempt": attempt,
        "budget_sha256": sha256_text(&budget_json),
        "checkpoint_sha256": sha256_text(&checkpoint_json),
        "deadline_at_ms": deadline_at_ms,
        "input_sha256": sha256_text(&input_json),
        "lease_expires_at_ms": lease_expires_at_ms,
        "lease_owner": lease_owner,
        "state": state_text,
        "terminal_result_ids": terminal_result_ids,
    }))
}

fn require_current_job_audit_anchor(transaction: &Transaction<'_>, job_id: JobId) -> Result<()> {
    let mut statement = transaction
        .prepare(
            "SELECT after_json
             FROM audit_events
             WHERE subject_type = 'job' AND subject_id = ?1
               AND action IN (
                   'job_created', 'job_started', 'job_checkpointed', 'job_interrupted',
                   'job_resumed', 'job_succeeded', 'job_failed'
               )
             ORDER BY sequence DESC, id DESC
             LIMIT 1",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query([job_id.as_uuid().to_string()])
        .map_err(|_| HeleosError::Database)?;
    let row = rows
        .next()
        .map_err(|_| HeleosError::Database)?
        .ok_or(HeleosError::Integrity)?;
    let anchored: JsonValue = parse_bounded_json(row, 0, ONE_MIB)?;
    if rows.next().map_err(|_| HeleosError::Database)?.is_some()
        || anchored != job_audit_snapshot(transaction, job_id)?
    {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

struct FailJob<'a> {
    job_id: JobId,
    project_id: ProjectId,
    attempt: u32,
    actor: &'a ActorId,
    now_ms: i64,
    terminal_reason: &'a str,
    audit_reason: &'static str,
    audit_event_id: AuditEventId,
}

fn fail_job_in_transaction(transaction: &Transaction<'_>, command: FailJob<'_>) -> Result<()> {
    if !matches!(
        command.terminal_reason,
        "deadline_expired" | "attempt_limit" | "internal_failure"
    ) || !(1..=MAX_JOB_ATTEMPTS).contains(&command.attempt)
    {
        return Err(HeleosError::Integrity);
    }
    let before = job_audit_snapshot(transaction, command.job_id)?;
    let changed = transaction
        .execute(
            "UPDATE job_runs
             SET state = 'failed', lease_owner = NULL, lease_expires_at_ms = NULL,
                 terminal_reason = ?4, updated_at_ms = ?5
             WHERE id = ?1 AND project_id = ?2 AND state = 'running' AND attempt = ?3",
            params![
                command.job_id.as_uuid().to_string(),
                command.project_id.as_uuid().to_string(),
                i64::from(command.attempt),
                command.terminal_reason,
                command.now_ms,
            ],
        )
        .map_err(|_| HeleosError::Database)?;
    if changed != 1 {
        return Err(HeleosError::InvalidStateTransition);
    }
    append_action(
        transaction,
        AppendAction {
            id: command.audit_event_id,
            project_id: command.project_id,
            actor: command.actor.clone(),
            action: AuditAction::JobFailed,
            subject_type: AuditSubjectType::Job,
            subject_id: command.job_id.as_uuid().to_string(),
            before,
            after: job_audit_snapshot(transaction, command.job_id)?,
            reason: command.audit_reason,
            occurred_at_ms: command.now_ms,
        },
    )?;
    Ok(())
}

fn terminal_result_ids(receipt: &IngestReceipt, project_id: ProjectId) -> Result<JsonValue> {
    let evidence_id = match &receipt.evidence_manifest {
        Some(manifest) => {
            let mut matches = manifest
                .lineages
                .iter()
                .filter(|lineage| lineage.project_id == project_id);
            let evidence_id = matches.next().ok_or(HeleosError::Integrity)?.evidence_id;
            if matches.next().is_some() {
                return Err(HeleosError::Integrity);
            }
            Some(evidence_id)
        }
        None => None,
    };
    Ok(json!({
        "document_id": receipt.document_id,
        "evidence_id": evidence_id,
        "ingest_event_id": receipt.ingest_event_id,
        "revision_id": receipt.revision_id,
    }))
}

fn insert_or_verify_content_object(
    transaction: &Transaction<'_>,
    object: &StoredObjectV1,
    media_type: &str,
    admission_state: &str,
    quarantine_json: Option<&str>,
    now_ms: i64,
    actor: &ActorId,
) -> Result<()> {
    transaction
        .execute(
            "INSERT OR IGNORE INTO content_objects
                (sha256, byte_length, media_type, admission_state, vault_key, created_at_ms,
                 created_by, quarantine_reason)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                object.digest.to_string(),
                i64::try_from(object.byte_length).map_err(|_| HeleosError::Integrity)?,
                media_type,
                admission_state,
                object.vault_key,
                now_ms,
                actor.as_str(),
                quarantine_json,
            ],
        )
        .map_err(|_| HeleosError::Database)?;
    let mut statement = transaction
        .prepare(
            "SELECT byte_length, media_type, admission_state, vault_key, quarantine_reason
             FROM content_objects WHERE sha256 = ?1",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query([object.digest.to_string()])
        .map_err(|_| HeleosError::Database)?;
    let row = rows
        .next()
        .map_err(|_| HeleosError::Database)?
        .ok_or(HeleosError::Integrity)?;
    let stored_quarantine = match row.get_ref(4).map_err(|_| HeleosError::Database)? {
        ValueRef::Null => None,
        ValueRef::Text(bytes) if bytes.len() <= ONE_MIB => {
            Some(std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?)
        }
        _ => return Err(HeleosError::Integrity),
    };
    if nonnegative_u64(row, 0)? != object.byte_length
        || bounded_text(row, 1, 80)? != media_type
        || bounded_text(row, 2, 16)? != admission_state
        || bounded_text(row, 3, 256)? != object.vault_key
        || stored_quarantine != quarantine_json
        || rows.next().map_err(|_| HeleosError::Database)?.is_some()
    {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn require_revision_authority(
    transaction: &Transaction<'_>,
    document_id: DocumentId,
    revision_id: RevisionId,
    content: Sha256Digest,
) -> Result<()> {
    let exact = transaction
        .query_row(
            "SELECT EXISTS(
                SELECT 1 FROM document_revisions
                WHERE id = ?1 AND document_id = ?2 AND content_sha256 = ?3
             )",
            params![
                revision_id.as_digest().to_string(),
                document_id.as_digest().to_string(),
                content.to_string(),
            ],
            |row| row.get::<_, i64>(0),
        )
        .map_err(|_| HeleosError::Database)?;
    if exact != 1 {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn require_sheet_authority(
    transaction: &Transaction<'_>,
    revision_id: RevisionId,
    parent: Sha256Digest,
    expected: &[PageMetadata],
) -> Result<()> {
    let mut statement = transaction
        .prepare(
            "SELECT id, zero_based_page_index, width_micropoints, height_micropoints,
                    rotation_degrees, unit, parent_content_sha256, transform_json
             FROM sheets WHERE revision_id = ?1
             ORDER BY zero_based_page_index, id",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query([revision_id.as_digest().to_string()])
        .map_err(|_| HeleosError::Database)?;
    for page in expected {
        let row = rows
            .next()
            .map_err(|_| HeleosError::Database)?
            .ok_or(HeleosError::Integrity)?;
        let transform: crate::PageTransform = parse_bounded_json(row, 7, ONE_MIB)?;
        if parse_digest_id::<SheetId>(row, 0)? != page.page_id
            || bounded_u32(row, 1, 0, 9_999)? != page.index
            || nonnegative_u64(row, 2)? != page.width_micropoints
            || nonnegative_u64(row, 3)? != page.height_micropoints
            || bounded_u32(row, 4, 0, 270)? != u32::from(page.rotation_degrees)
            || bounded_text(row, 5, 2)? != "pt"
            || parse_digest_text(row, 6)? != parent
            || transform != page.transform
        {
            return Err(HeleosError::Integrity);
        }
    }
    if rows.next().map_err(|_| HeleosError::Database)?.is_some() {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn evidence_lineages(
    transaction: &Transaction<'_>,
    revision_id: RevisionId,
) -> Result<Vec<EvidenceManifestLineage>> {
    let mut statement = transaction
        .prepare(
            "SELECT project_id, id, job_id
             FROM evidence_objects
             WHERE document_revision_id = ?1 AND review_state = 'accepted'
             ORDER BY project_id, id, job_id",
        )
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement
        .query([revision_id.as_digest().to_string()])
        .map_err(|_| HeleosError::Database)?;
    let mut result = Vec::new();
    while let Some(row) = rows.next().map_err(|_| HeleosError::Database)? {
        if result.len() == 100_000 {
            return Err(HeleosError::ResourceLimit);
        }
        result.push(EvidenceManifestLineage {
            project_id: parse_uuid_text::<ProjectId>(row, 0, 36)?,
            evidence_id: parse_uuid_text::<EvidenceId>(row, 1, 36)?,
            originating_job_id: parse_uuid_text::<JobId>(row, 2, 36)?,
        });
    }
    if result.is_empty() {
        return Err(HeleosError::Integrity);
    }
    Ok(result)
}

fn require_evidence_authority(
    transaction: &Transaction<'_>,
    project_id: ProjectId,
    revision_id: RevisionId,
    manifest: Sha256Digest,
    parent: Sha256Digest,
    parameters_json: &str,
) -> Result<()> {
    let exact = transaction
        .query_row(
            "SELECT EXISTS(
                SELECT 1 FROM evidence_objects
                WHERE project_id = ?1 AND document_revision_id = ?2
                  AND content_sha256 = ?3 AND parent_content_sha256 = ?4
                  AND extraction_method = 'heleos.pdf-probe/v1'
                  AND parameters_json = ?5 AND review_state = 'accepted'
             )",
            params![
                project_id.as_uuid().to_string(),
                revision_id.as_digest().to_string(),
                manifest.to_string(),
                parent.to_string(),
                parameters_json,
            ],
            |row| row.get::<_, i64>(0),
        )
        .map_err(|_| HeleosError::Database)?;
    if exact != 1 {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

const fn ingest_outcome_text(outcome: IngestOutcome) -> &'static str {
    match outcome {
        IngestOutcome::AcceptedNew => "accepted_new",
        IngestOutcome::AcceptedDuplicate => "accepted_duplicate",
        IngestOutcome::IdempotentReplay => "idempotent_replay",
        IngestOutcome::QuarantinedCorrupt => "quarantined_corrupt",
        IngestOutcome::QuarantinedEncrypted => "quarantined_encrypted",
        IngestOutcome::QuarantinedUnsupported => "quarantined_unsupported",
        IngestOutcome::QuarantinedSuspicious => "quarantined_suspicious",
        IngestOutcome::QuarantinedLimit => "quarantined_limit",
        IngestOutcome::Interrupted => "interrupted",
        IngestOutcome::DeniedConflict => "denied_conflict",
    }
}

struct AppendAction<'a> {
    id: AuditEventId,
    project_id: ProjectId,
    actor: ActorId,
    action: AuditAction,
    subject_type: AuditSubjectType,
    subject_id: String,
    before: JsonValue,
    after: JsonValue,
    reason: &'a str,
    occurred_at_ms: i64,
}

fn append_action(transaction: &Transaction<'_>, input: AppendAction<'_>) -> Result<AuditEvent> {
    append_audit(
        transaction,
        AuditEventInput {
            id: input.id,
            sequence: next_audit_sequence(transaction)?,
            project_id: Some(input.project_id),
            actor: input.actor,
            action: input.action,
            subject_type: input.subject_type,
            subject_id: input.subject_id,
            before: input.before,
            after: input.after,
            reason: input.reason.to_owned(),
            occurred_at_ms: input.occurred_at_ms,
            previous_hash: audit_head(transaction)?,
        },
    )
}

fn data_class_text(value: DataClass) -> &'static str {
    match value {
        DataClass::Public => "PUBLIC",
        DataClass::Internal => "INTERNAL",
        DataClass::ProjectConfidential => "PROJECT_CONFIDENTIAL",
        DataClass::Secret => "SECRET",
    }
}

fn next_audit_sequence(transaction: &Transaction<'_>) -> Result<u64> {
    let previous = transaction
        .query_row("SELECT MAX(sequence) FROM audit_events", [], |row| {
            row.get::<_, Option<i64>>(0)
        })
        .map_err(|_| HeleosError::Database)?;
    let next = match previous {
        Some(value) if value > 0 => u64::try_from(value)
            .ok()
            .and_then(|value| value.checked_add(1))
            .ok_or(HeleosError::Integrity)?,
        None => 1,
        _ => return Err(HeleosError::Integrity),
    };
    if next > JCS_SAFE_INTEGER_MAX {
        return Err(HeleosError::Integrity);
    }
    Ok(next)
}

fn audit_head(transaction: &Transaction<'_>) -> Result<Sha256Digest> {
    let mut statement = transaction
        .prepare("SELECT event_hash FROM audit_events ORDER BY sequence DESC, id DESC LIMIT 1")
        .map_err(|_| HeleosError::Database)?;
    let mut rows = statement.query([]).map_err(|_| HeleosError::Database)?;
    let Some(row) = rows.next().map_err(|_| HeleosError::Database)? else {
        return Ok(Sha256Digest::from_bytes([0; 32]));
    };
    let bytes = match row.get_ref(0).map_err(|_| HeleosError::Database)? {
        ValueRef::Text(bytes) if bytes.len() == 64 => bytes,
        _ => return Err(HeleosError::Integrity),
    };
    let value = std::str::from_utf8(bytes).map_err(|_| HeleosError::Integrity)?;
    Sha256Digest::from_str(value).map_err(|_| HeleosError::Integrity)
}

pub(crate) fn append_audit(
    transaction: &Transaction<'_>,
    input: AuditEventInput,
) -> Result<AuditEvent> {
    let event = AuditEvent::build(input)?;
    let before_json = String::from_utf8(crate::canonical_json(&event.before)?)
        .map_err(|_| HeleosError::Integrity)?;
    let after_json = String::from_utf8(crate::canonical_json(&event.after)?)
        .map_err(|_| HeleosError::Integrity)?;
    transaction
        .execute(
            "INSERT INTO audit_events
                (id, sequence, project_id, actor, action, subject_type, subject_id, before_json,
                 after_json, reason, occurred_at_ms, previous_hash, event_hash)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![
                event.id.as_uuid().to_string(),
                i64::try_from(event.sequence).map_err(|_| HeleosError::Integrity)?,
                event.project_id.map(|id| id.as_uuid().to_string()),
                event.actor.as_str(),
                event.action.as_str(),
                event.subject_type.as_str(),
                event.subject_id,
                before_json,
                after_json,
                event.reason,
                event.occurred_at_ms,
                event.previous_hash.to_string(),
                event.event_hash.to_string(),
            ],
        )
        .map_err(|_| HeleosError::Database)?;
    Ok(event)
}

#[cfg(test)]
mod tests {
    use uuid::Uuid;

    use super::*;
    use crate::{
        INTAKE_QUARANTINE_SCHEMA_V1, IntakeQuarantineReasonV1, PageTransform, PageUnit, PdfLimits,
        PdfProbeProvenance, PdfQuarantineReason, VaultInventory, VaultInventoryEntry, page_id,
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

    fn accepted_receipt(
        authoritative_job_id: JobId,
        mut lineages: Vec<EvidenceManifestLineage>,
    ) -> IngestReceipt {
        let content = Sha256Digest::from_bytes([1; 32]);
        let manifest = EvidenceManifestV1::new(
            content,
            7,
            vec![PageMetadata {
                index: 0,
                page_id: page_id(content, 0),
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
            }],
            provenance(),
        )
        .expect("valid manifest");
        let manifest_bytes = manifest.canonical_bytes().expect("manifest bytes");
        let manifest_digest =
            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest");
        let mut preexisting_vault_digests = vec![content, manifest_digest];
        preexisting_vault_digests.sort_unstable();
        lineages.sort_by_key(|lineage| {
            (
                *lineage.project_id.as_uuid().as_bytes(),
                *lineage.evidence_id.as_uuid().as_bytes(),
                *lineage.originating_job_id.as_uuid().as_bytes(),
            )
        });
        let (document_id, revision_id) = crate::canonical_document_ids(content);
        IngestReceipt {
            ingest_event_id: IngestEventId::from_uuid(uuid(20)),
            authoritative_job_id,
            attempt: 1,
            outcome: IngestOutcome::AcceptedDuplicate,
            content_sha256: Some(content),
            byte_length: 7,
            quarantine: None,
            document_id: Some(document_id),
            revision_id: Some(revision_id),
            sheet_ids: vec![page_id(content, 0)],
            evidence_manifest: Some(EvidenceManifestReceipt {
                manifest,
                manifest_content_sha256: manifest_digest,
                manifest_byte_length: u64::try_from(manifest_bytes.len())
                    .expect("manifest length fits"),
                manifest_media_type: EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
                manifest_vault_key: crate::Vault::object_key(manifest_digest),
                original_vault_key: crate::Vault::object_key(content),
                lineages,
            }),
            preexisting_vault_digests,
        }
    }

    fn valid_input(project_id: ProjectId, key: &str) -> IngestInputV1 {
        IngestInputV1 {
            schema: crate::ingest::job::INGEST_INPUT_SCHEMA_V1.to_owned(),
            project_id,
            kind: "pdf_ingest".to_owned(),
            idempotency_key: IdempotencyKey::try_from(key).expect("idempotency key"),
            content_sha256: Some(Sha256Digest::from_bytes([1; 32])),
            byte_length: 7,
            source_display: "utf8:file.pdf".to_owned(),
            requested_limits: PdfLimits::default().into(),
            expected_probe_provenance: provenance(),
            deadline_profile_ms: 300_000,
        }
    }

    fn insert_project(store: &Store, project_id: ProjectId) {
        store
            .connection
            .execute(
                "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                 VALUES (?1, 'project', 0, 'actor', 'INTERNAL')",
                [project_id.as_uuid().to_string()],
            )
            .expect("insert project");
    }

    fn insert_job(
        store: &Store,
        project_id: ProjectId,
        job_id: JobId,
        key: &str,
        input: &IngestInputV1,
        budget: &IngestBudgetV1,
        checkpoint: &IngestCheckpointV1,
    ) {
        insert_job_with_times(
            (store, project_id, job_id, key, input, budget, checkpoint),
            0,
            300_000,
        );
    }

    type JobFixture<'a> = (
        &'a Store,
        ProjectId,
        JobId,
        &'a str,
        &'a IngestInputV1,
        &'a IngestBudgetV1,
        &'a IngestCheckpointV1,
    );

    fn insert_job_with_times(fixture: JobFixture<'_>, created_at_ms: i64, deadline_at_ms: i64) {
        let (store, project_id, job_id, key, input, budget, checkpoint) = fixture;
        store
            .connection
            .execute(
                "INSERT INTO job_runs
                    (id, project_id, kind, idempotency_key, state, attempt, lease_owner,
                     lease_expires_at_ms, deadline_at_ms, budget_json, input_json,
                     checkpoint_json, terminal_reason, created_at_ms, updated_at_ms)
                 VALUES (?1, ?2, 'pdf_ingest', ?3, 'succeeded', 1, NULL, NULL, ?4,
                         ?5, ?6, ?7, 'completed', ?8, ?9)",
                params![
                    job_id.as_uuid().to_string(),
                    project_id.as_uuid().to_string(),
                    key,
                    deadline_at_ms,
                    json_text(budget, ONE_MIB).expect("budget JSON"),
                    json_text(input, ONE_MIB).expect("input JSON"),
                    json_text(checkpoint, EIGHT_MIB).expect("checkpoint JSON"),
                    created_at_ms,
                    created_at_ms + 1,
                ],
            )
            .expect("insert job");
    }

    fn accepted_page(content: Sha256Digest) -> PageMetadata {
        PageMetadata {
            index: 0,
            page_id: page_id(content, 0),
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

    fn accepted_manifest(content: Sha256Digest) -> EvidenceManifestV1 {
        EvidenceManifestV1::new(content, 7, vec![accepted_page(content)], provenance())
            .expect("valid accepted manifest")
    }

    fn stored_object(byte: u8, byte_length: u64) -> StoredObjectV1 {
        let digest = Sha256Digest::from_bytes([byte; 32]);
        StoredObjectV1 {
            digest,
            byte_length,
            vault_key: crate::Vault::object_key(digest),
        }
    }

    fn inventory_entry(object: &StoredObjectV1) -> VaultInventoryEntry {
        VaultInventoryEntry {
            digest: object.digest,
            expected_byte_length: object.byte_length,
            vault_key: object.vault_key.clone(),
        }
    }

    fn input_for_object(
        project_id: ProjectId,
        key: &str,
        object: &StoredObjectV1,
    ) -> IngestInputV1 {
        let mut input = valid_input(project_id, key);
        input.content_sha256 = Some(object.digest);
        input.byte_length = object.byte_length;
        input
    }

    fn queue_inventory_job(
        store: &mut Store,
        project_id: ProjectId,
        job_id: JobId,
        input: IngestInputV1,
        checkpoint: IngestCheckpointV1,
        created_at_ms: i64,
        audit_event_id: AuditEventId,
    ) {
        store
            .job_create_with_checkpoint_and_audit(JobCreateCommand {
                job_id,
                project_id,
                budget: IngestBudgetV1::new(input.byte_length),
                input,
                checkpoint,
                actor: ActorId::from_str("fixture-actor").expect("actor"),
                created_at_ms,
                deadline_at_ms: created_at_ms + JOB_DEADLINE_MS,
                audit_event_id,
            })
            .expect("queue inventory fixture job");
    }

    fn start_inventory_job(
        store: &mut Store,
        project_id: ProjectId,
        job_id: JobId,
        now_ms: i64,
        lease_owner: Uuid,
        audit_event_id: AuditEventId,
    ) {
        store
            .job_start_or_resume_with_audit(JobStartCommand {
                job_id,
                project_id,
                actor: ActorId::from_str("fixture-actor").expect("actor"),
                lease_owner,
                now_ms,
                lease_expires_at_ms: now_ms + LEASE_DURATION_MS,
                audit_event_id,
            })
            .expect("start inventory fixture job");
    }

    fn replace_queued_checkpoint_and_audit(
        store: &mut Store,
        job_id: JobId,
        checkpoint: &IngestCheckpointV1,
    ) {
        store
            .connection
            .execute_batch(
                "DROP TRIGGER job_runs_legal_transition;
                 DROP TRIGGER audit_events_no_update;",
            )
            .expect("open hostile queued-checkpoint fixture");
        let checkpoint_json = json_text(checkpoint, EIGHT_MIB).expect("checkpoint JSON");
        store
            .with_immediate_transaction(|transaction| {
                transaction
                    .execute(
                        "UPDATE job_runs SET checkpoint_json = ?1 WHERE id = ?2",
                        params![checkpoint_json, job_id.as_uuid().to_string()],
                    )
                    .map_err(|_| HeleosError::Database)?;
                let audit_after = json_text(&job_audit_snapshot(transaction, job_id)?, ONE_MIB)?;
                let changed = transaction
                    .execute(
                        "UPDATE audit_events SET after_json = ?1
                         WHERE subject_type = 'job' AND subject_id = ?2
                           AND action = 'job_created'",
                        params![audit_after, job_id.as_uuid().to_string()],
                    )
                    .map_err(|_| HeleosError::Database)?;
                if changed != 1 {
                    return Err(HeleosError::Integrity);
                }
                Ok(())
            })
            .expect("forge matching queued checkpoint and audit");
    }

    fn recovery_command(job_id: JobId, now_ms: i64, id_base: u128) -> JobRecoverCommand {
        JobRecoverCommand {
            job_id,
            actor: ActorId::from_str("recovery-actor").expect("actor"),
            lease_owner: uuid(id_base),
            now_ms,
            expected_probe_provenance: provenance(),
            interrupted_event_id: IngestEventId::from_uuid(uuid(id_base + 1)),
            audit_event_ids: [
                AuditEventId::from_uuid(uuid(id_base + 2)),
                AuditEventId::from_uuid(uuid(id_base + 3)),
            ],
        }
    }

    fn queued_recovery_fixture(created_at_ms: i64, key: &str) -> (Store, ProjectId, JobId) {
        let mut store = Store::open_in_memory().expect("open recovery store");
        store.migrate().expect("migrate recovery store");
        let project_id = ProjectId::from_uuid(uuid(30_000));
        let job_id = JobId::from_uuid(uuid(30_001));
        insert_project(&store, project_id);
        let input = valid_input(project_id, key);
        let digest = input.content_sha256.expect("recovery digest");
        queue_inventory_job(
            &mut store,
            project_id,
            job_id,
            input.clone(),
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished {
                    original: StoredObjectV1 {
                        digest,
                        byte_length: input.byte_length,
                        vault_key: crate::Vault::object_key(digest),
                    },
                },
            },
            created_at_ms,
            AuditEventId::from_uuid(uuid(30_002)),
        );
        (store, project_id, job_id)
    }

    fn job_actions(store: &Store, job_id: JobId) -> Vec<String> {
        let mut statement = store
            .connection
            .prepare(
                "SELECT action FROM audit_events
                 WHERE subject_type = 'job' AND subject_id = ?1
                 ORDER BY sequence",
            )
            .expect("prepare job action query");
        statement
            .query_map([job_id.as_uuid().to_string()], |row| row.get(0))
            .expect("query job actions")
            .collect::<std::result::Result<Vec<_>, _>>()
            .expect("read job actions")
    }

    fn job_state_row(store: &Store, job_id: JobId) -> (String, i64, Option<i64>, Option<String>) {
        store
            .connection
            .query_row(
                "SELECT state, attempt, lease_expires_at_ms, terminal_reason
                 FROM job_runs WHERE id = ?1",
                [job_id.as_uuid().to_string()],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .expect("read job state")
    }

    fn query_value_rows_for_job(
        store: &Store,
        sql: &str,
        job_id: JobId,
    ) -> Vec<Vec<rusqlite::types::Value>> {
        let mut statement = store.connection.prepare(sql).expect("prepare row image");
        let column_count = statement.column_count();
        statement
            .query_map([job_id.as_uuid().to_string()], |row| {
                (0..column_count)
                    .map(|index| row.get::<_, rusqlite::types::Value>(index))
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .expect("query row image")
            .collect::<rusqlite::Result<Vec<_>>>()
            .expect("read row image")
    }

    fn query_all_value_rows(store: &Store, sql: &str) -> Vec<Vec<rusqlite::types::Value>> {
        let mut statement = store.connection.prepare(sql).expect("prepare table image");
        let column_count = statement.column_count();
        statement
            .query_map([], |row| {
                (0..column_count)
                    .map(|index| row.get::<_, rusqlite::types::Value>(index))
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .expect("query table image")
            .collect::<rusqlite::Result<Vec<_>>>()
            .expect("read table image")
    }

    fn recovery_authority_image(
        store: &Store,
        job_id: JobId,
    ) -> [Vec<Vec<rusqlite::types::Value>>; 3] {
        [
            query_value_rows_for_job(
                store,
                "SELECT id, project_id, kind, idempotency_key, state, attempt, lease_owner,
                        lease_expires_at_ms, deadline_at_ms, budget_json, input_json,
                        checkpoint_json, terminal_reason, created_at_ms, updated_at_ms
                 FROM job_runs WHERE id = ?1 ORDER BY id",
                job_id,
            ),
            query_value_rows_for_job(
                store,
                "SELECT id, project_id, job_id, content_sha256, outcome, attempt, source_name,
                        source_path, idempotency_key, actor, terminal_at_ms, details_json
                 FROM ingest_events WHERE job_id = ?1 ORDER BY terminal_at_ms, id",
                job_id,
            ),
            query_all_value_rows(
                store,
                "SELECT id, sequence, project_id, actor, action, subject_type, subject_id,
                        before_json, after_json, reason, occurred_at_ms, previous_hash, event_hash
                 FROM audit_events ORDER BY sequence, id",
            ),
        ]
    }

    struct ForgedJobState<'a> {
        state: &'a str,
        attempt: u32,
        lease_owner: Option<Uuid>,
        lease_expires_at_ms: Option<i64>,
        terminal_reason: Option<&'a str>,
        checkpoint: &'a IngestCheckpointV1,
        updated_at_ms: i64,
    }

    fn forge_job_state_and_matching_anchor(
        store: &mut Store,
        job_id: JobId,
        state: ForgedJobState<'_>,
    ) {
        store
            .connection
            .execute_batch(
                "DROP TRIGGER job_runs_legal_transition;
                 DROP TRIGGER audit_events_no_update;",
            )
            .expect("open bounded recovery fixture");
        let checkpoint_json = json_text(state.checkpoint, EIGHT_MIB).expect("checkpoint JSON");
        store
            .with_immediate_transaction(|transaction| {
                let changed = transaction
                    .execute(
                        "UPDATE job_runs
                         SET state = ?1, attempt = ?2, lease_owner = ?3,
                             lease_expires_at_ms = ?4, checkpoint_json = ?5,
                             terminal_reason = ?6, updated_at_ms = ?7
                         WHERE id = ?8",
                        params![
                            state.state,
                            i64::from(state.attempt),
                            state
                                .lease_owner
                                .map(|value| value.hyphenated().to_string()),
                            state.lease_expires_at_ms,
                            checkpoint_json,
                            state.terminal_reason,
                            state.updated_at_ms,
                            job_id.as_uuid().to_string(),
                        ],
                    )
                    .map_err(|_| HeleosError::Database)?;
                if changed != 1 {
                    return Err(HeleosError::Integrity);
                }
                let anchored = json_text(&job_audit_snapshot(transaction, job_id)?, ONE_MIB)?;
                let changed = transaction
                    .execute(
                        "UPDATE audit_events SET after_json = ?1
                         WHERE subject_type = 'job' AND subject_id = ?2
                           AND action = 'job_created'",
                        params![anchored, job_id.as_uuid().to_string()],
                    )
                    .map_err(|_| HeleosError::Database)?;
                if changed != 1 {
                    return Err(HeleosError::Integrity);
                }
                Ok(())
            })
            .expect("forge recovery state and matching latest anchor");
    }

    fn accepted_commit_fixture() -> (Store, AcceptedIntakeCommand) {
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let project_id = ProjectId::from_uuid(uuid(100));
        let job_id = JobId::from_uuid(uuid(101));
        insert_project(&store, project_id);
        let input = valid_input(project_id, "accepted-command");
        let original = StoredObjectV1 {
            digest: input.content_sha256.expect("content digest"),
            byte_length: input.byte_length,
            vault_key: crate::Vault::object_key(input.content_sha256.expect("content digest")),
        };
        let manifest = accepted_manifest(original.digest);
        let manifest_bytes = manifest.canonical_bytes().expect("manifest bytes");
        let manifest_digest =
            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest");
        let manifest_object = StoredObjectV1 {
            digest: manifest_digest,
            byte_length: u64::try_from(manifest_bytes.len()).expect("manifest length"),
            vault_key: crate::Vault::object_key(manifest_digest),
        };
        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::ProcessingComplete {
                original: original.clone(),
                candidate: crate::ingest::job::ProcessingCandidateV1::Accepted {
                    manifest: Box::new(manifest.clone()),
                    manifest_object: manifest_object.clone(),
                },
            },
        };
        store
            .connection
            .execute(
                "INSERT INTO job_runs
                    (id, project_id, kind, idempotency_key, state, attempt, lease_owner,
                     lease_expires_at_ms, deadline_at_ms, budget_json, input_json,
                     checkpoint_json, terminal_reason, created_at_ms, updated_at_ms)
                 VALUES (?1, ?2, 'pdf_ingest', ?3, 'running', 1, ?4, 30000, 300000,
                         ?5, ?6, ?7, NULL, 0, 0)",
                params![
                    job_id.as_uuid().to_string(),
                    project_id.as_uuid().to_string(),
                    input.idempotency_key.as_str(),
                    uuid(102).hyphenated().to_string(),
                    json_text(&IngestBudgetV1::new(input.byte_length), ONE_MIB)
                        .expect("budget JSON"),
                    json_text(&input, ONE_MIB).expect("input JSON"),
                    json_text(&checkpoint, EIGHT_MIB).expect("checkpoint JSON"),
                ],
            )
            .expect("insert running accepted job");
        let command = AcceptedIntakeCommand {
            job_id,
            project_id,
            attempt: 1,
            actor: ActorId::from_str("fixture-actor").expect("actor"),
            idempotency_key: input.idempotency_key,
            source_display: input.source_display,
            original,
            manifest_object,
            pages: manifest.pages.clone(),
            manifest,
            ingest_event_id: IngestEventId::from_uuid(uuid(103)),
            source_record_id: uuid(104).hyphenated().to_string(),
            evidence_id: EvidenceId::from_uuid(uuid(105)),
            now_ms: 1,
            preexisting_vault_digests: Vec::new(),
            audit_event_ids: [
                AuditEventId::from_uuid(uuid(106)),
                AuditEventId::from_uuid(uuid(107)),
                AuditEventId::from_uuid(uuid(108)),
            ],
        };
        (store, command)
    }

    fn authoritative_row_counts(store: &Store) -> [i64; 7] {
        [
            "content_objects",
            "documents",
            "sheets",
            "evidence_objects",
            "source_records",
            "ingest_events",
            "audit_events",
        ]
        .map(|table| {
            store
                .connection
                .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                    row.get(0)
                })
                .expect("count authoritative rows")
        })
    }

    fn assert_accepted_commit_rejected_without_authority(
        mut store: Store,
        command: AcceptedIntakeCommand,
    ) {
        let before = authoritative_row_counts(&store);
        assert!(matches!(
            store.accepted_intake_commit(command),
            Err(HeleosError::Integrity)
        ));
        assert_eq!(authoritative_row_counts(&store), before);
        assert_eq!(
            store
                .connection
                .query_row("SELECT state FROM job_runs", [], |row| row
                    .get::<_, String>(0),)
                .expect("read unchanged job state"),
            "running"
        );
    }

    fn replace_command_object_authority(command: &mut AcceptedIntakeCommand) {
        let original_digest = Sha256Digest::from_bytes([8; 32]);
        let manifest = accepted_manifest(original_digest);
        let manifest_bytes = manifest
            .canonical_bytes()
            .expect("alternate manifest bytes");
        let manifest_digest =
            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("alternate digest");
        command.original = StoredObjectV1 {
            digest: original_digest,
            byte_length: 7,
            vault_key: crate::Vault::object_key(original_digest),
        };
        command.manifest_object = StoredObjectV1 {
            digest: manifest_digest,
            byte_length: u64::try_from(manifest_bytes.len()).expect("alternate length"),
            vault_key: crate::Vault::object_key(manifest_digest),
        };
        command.pages = manifest.pages.clone();
        command.manifest = manifest;
    }

    fn prepopulate_revision_lineages(
        store: &mut Store,
        command: &AcceptedIntakeCommand,
        count: u32,
    ) {
        store
            .connection
            .pragma_update(None, "foreign_keys", "OFF")
            .expect("disable fixture foreign keys");
        let transaction = store
            .connection
            .transaction()
            .expect("begin lineage fixture");
        for (object, media_type) in [
            (&command.original, PDF_MEDIA_TYPE),
            (&command.manifest_object, EVIDENCE_MANIFEST_MEDIA_TYPE),
        ] {
            transaction
                .execute(
                    "INSERT INTO content_objects
                        (sha256, byte_length, media_type, admission_state, vault_key,
                         created_at_ms, created_by, quarantine_reason)
                     VALUES (?1, ?2, ?3, 'accepted', ?4, 0, 'fixture-actor', NULL)",
                    params![
                        object.digest.to_string(),
                        i64::try_from(object.byte_length).expect("object length"),
                        media_type,
                        object.vault_key,
                    ],
                )
                .expect("insert accepted content fixture");
        }
        transaction
            .execute(
                "INSERT INTO documents (id, created_at_ms, created_by)
                 VALUES (?1, 0, 'fixture-actor')",
                [command.manifest.document_id.as_digest().to_string()],
            )
            .expect("insert fixture document");
        transaction
            .execute(
                "INSERT INTO document_revisions
                    (id, document_id, content_sha256, created_at_ms, created_by)
                 VALUES (?1, ?2, ?3, 0, 'fixture-actor')",
                params![
                    command.manifest.revision_id.as_digest().to_string(),
                    command.manifest.document_id.as_digest().to_string(),
                    command.original.digest.to_string(),
                ],
            )
            .expect("insert fixture revision");
        let parameters = EvidenceParametersV1::new(
            command.manifest.probe_provenance.clone(),
            command.manifest.requested_limits,
        )
        .expect("fixture parameters");
        let parameters_json = json_text(&parameters, ONE_MIB).expect("parameters JSON");
        let mut statement = transaction
            .prepare(
                "INSERT INTO evidence_objects
                    (id, project_id, job_id, document_revision_id, content_sha256,
                     parent_content_sha256, extraction_method, parameters_json,
                     review_state, created_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'heleos.pdf-probe/v1', ?7,
                         'accepted', 0)",
            )
            .expect("prepare lineage insert");
        for index in 0..count {
            let offset = 20_000_u128 + u128::from(index) * 3;
            statement
                .execute(params![
                    uuid(offset).hyphenated().to_string(),
                    uuid(offset + 1).hyphenated().to_string(),
                    uuid(offset + 2).hyphenated().to_string(),
                    command.manifest.revision_id.as_digest().to_string(),
                    command.manifest_object.digest.to_string(),
                    command.original.digest.to_string(),
                    parameters_json,
                ])
                .expect("insert accepted lineage fixture");
        }
        drop(statement);
        transaction.commit().expect("commit lineage fixture");
        store
            .connection
            .pragma_update(None, "foreign_keys", "ON")
            .expect("restore fixture foreign keys");
    }

    fn minimal_revision_evidence_store(
        row_count: usize,
        valid_identifiers: bool,
    ) -> (Store, RevisionId) {
        assert!(row_count > 0);
        let store = Store::open_in_memory().expect("open minimal evidence store");
        store
            .connection
            .execute_batch(
                "CREATE TABLE content_objects (
                    sha256 TEXT, byte_length INTEGER, vault_key TEXT,
                    media_type TEXT, admission_state TEXT
                 );
                 CREATE TABLE document_revisions (id TEXT, document_id TEXT);
                 CREATE TABLE sheets (
                    id TEXT, revision_id TEXT, zero_based_page_index INTEGER,
                    width_micropoints INTEGER, height_micropoints INTEGER,
                    rotation_degrees INTEGER, unit TEXT,
                    parent_content_sha256 TEXT, transform_json TEXT
                 );
                 CREATE TABLE evidence_objects (
                    id TEXT, project_id TEXT, job_id TEXT, document_revision_id TEXT,
                    content_sha256 TEXT, parent_content_sha256 TEXT,
                    extraction_method TEXT, parameters_json TEXT, review_state TEXT
                 );",
            )
            .expect("create minimal evidence schema");
        let original = Sha256Digest::from_bytes([4; 32]);
        let manifest = Sha256Digest::from_bytes([2; 32]);
        let document = DocumentId::from(Sha256Digest::from_bytes([3; 32]));
        let revision = RevisionId::from(Sha256Digest::from_bytes([4; 32]));
        for digest in [original, manifest] {
            store
                .connection
                .execute(
                    "INSERT INTO content_objects VALUES (?1, 7, ?2, ?3, 'accepted')",
                    params![
                        digest.to_string(),
                        crate::Vault::object_key(digest),
                        if digest == original {
                            PDF_MEDIA_TYPE
                        } else {
                            EVIDENCE_MANIFEST_MEDIA_TYPE
                        },
                    ],
                )
                .expect("insert minimal content");
        }
        store
            .connection
            .execute(
                "INSERT INTO document_revisions VALUES (?1, ?2)",
                params![
                    revision.as_digest().to_string(),
                    document.as_digest().to_string(),
                ],
            )
            .expect("insert minimal revision");
        let page = accepted_page(original);
        store
            .connection
            .execute(
                "INSERT INTO sheets VALUES (?1, ?2, 0, ?3, ?4, 0, 'pt', ?5, ?6)",
                params![
                    page.page_id.as_digest().to_string(),
                    revision.as_digest().to_string(),
                    i64::try_from(page.width_micropoints).expect("page width"),
                    i64::try_from(page.height_micropoints).expect("page height"),
                    original.to_string(),
                    json_text(&page.transform, ONE_MIB).expect("page transform JSON"),
                ],
            )
            .expect("insert minimal sheet");
        let parameters = EvidenceParametersV1::new(provenance(), PdfLimits::default().into())
            .expect("minimal parameters");
        let identifier = |value: u128| {
            if valid_identifiers {
                uuid(value).hyphenated().to_string()
            } else {
                "invalid".to_owned()
            }
        };
        store
            .connection
            .execute(
                "INSERT INTO evidence_objects VALUES
                    (?1, ?2, ?3, ?4, ?5, ?6, 'heleos.pdf-probe/v1', ?7, 'accepted')",
                params![
                    identifier(300),
                    identifier(301),
                    identifier(302),
                    revision.as_digest().to_string(),
                    manifest.to_string(),
                    original.to_string(),
                    json_text(&parameters, ONE_MIB).expect("minimal parameters JSON"),
                ],
            )
            .expect("insert first minimal lineage");
        let mut populated = 1;
        while populated < row_count {
            let add = populated.min(row_count - populated);
            store
                .connection
                .execute(
                    "INSERT INTO evidence_objects
                     SELECT id, project_id, job_id, document_revision_id, content_sha256,
                            parent_content_sha256, extraction_method, parameters_json,
                            review_state
                     FROM evidence_objects LIMIT ?1",
                    [i64::try_from(add).expect("copy count")],
                )
                .expect("double minimal lineages");
            populated += add;
        }
        (store, revision)
    }

    fn minimal_foundation_inspection_store(audit_count: usize) -> (Store, ProjectId) {
        assert!(audit_count > 0);
        let store = Store::open_in_memory().expect("open minimal inspection store");
        store
            .connection
            .execute_batch(
                "CREATE TABLE projects (id TEXT);
                 CREATE TABLE content_objects (
                    sha256 TEXT, byte_length INTEGER, media_type TEXT,
                    admission_state TEXT, vault_key TEXT, quarantine_reason TEXT
                 );
                 CREATE TABLE documents (id TEXT);
                 CREATE TABLE document_revisions (
                    id TEXT, document_id TEXT, content_sha256 TEXT
                 );
                 CREATE TABLE project_documents (project_id TEXT, document_id TEXT);
                 CREATE TABLE sheets (
                    id TEXT, revision_id TEXT, zero_based_page_index INTEGER,
                    width_micropoints INTEGER, height_micropoints INTEGER, unit TEXT,
                    rotation_degrees INTEGER, transform_json TEXT,
                    parent_content_sha256 TEXT
                 );
                 CREATE TABLE evidence_objects (
                    id TEXT, project_id TEXT, job_id TEXT, document_revision_id TEXT,
                    content_sha256 TEXT, parent_content_sha256 TEXT,
                    extraction_method TEXT, parameters_json TEXT, review_state TEXT
                 );
                 CREATE TABLE ingest_events (
                    id TEXT, project_id TEXT, job_id TEXT, content_sha256 TEXT,
                    outcome TEXT, attempt INTEGER, terminal_at_ms INTEGER
                 );
                 CREATE TABLE job_runs (
                    id TEXT, project_id TEXT, kind TEXT, state TEXT, attempt INTEGER,
                    created_at_ms INTEGER, updated_at_ms INTEGER, terminal_reason TEXT
                 );
                 CREATE TABLE audit_events (id TEXT, sequence INTEGER, project_id TEXT);",
            )
            .expect("create minimal inspection schema");
        let project_id = ProjectId::from_uuid(uuid(900));
        store
            .connection
            .execute(
                "INSERT INTO projects VALUES (?1)",
                [project_id.as_uuid().to_string()],
            )
            .expect("insert inspection project");
        store
            .connection
            .execute(
                "WITH RECURSIVE generated(value) AS (
                    SELECT 1
                    UNION ALL
                    SELECT value + 1 FROM generated WHERE value < ?1
                 )
                 INSERT INTO audit_events
                 SELECT printf('%08x-0000-4000-8000-%012x', value, value),
                        value, ?2
                 FROM generated",
                params![
                    i64::try_from(audit_count).expect("audit count"),
                    project_id.as_uuid().to_string(),
                ],
            )
            .expect("insert bounded audit rows");
        (store, project_id)
    }

    fn inspection_store_with_quarantine(reason: IntakeQuarantineReasonV1) -> (Store, ProjectId) {
        let (store, project_id) = minimal_foundation_inspection_store(1);
        let digest = Sha256Digest::from_bytes([11; 32]);
        let job_id = JobId::from_uuid(uuid(910));
        let probe_provenance = match reason {
            IntakeQuarantineReasonV1::Pdf(_) | IntakeQuarantineReasonV1::EvidenceManifestQuota => {
                Some(provenance())
            }
            IntakeQuarantineReasonV1::InputBytes { .. }
            | IntakeQuarantineReasonV1::OriginalRetentionQuota
            | IntakeQuarantineReasonV1::ProjectAssociationQuota => None,
        };
        let quarantine = IntakeQuarantineV1 {
            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
            reason: reason.clone(),
            probe_provenance,
        };
        let outcome = if matches!(reason, IntakeQuarantineReasonV1::Pdf(_)) {
            "quarantined_corrupt"
        } else {
            "quarantined_limit"
        };
        store
            .connection
            .execute(
                "INSERT INTO content_objects VALUES (?1, 7, ?2, 'quarantined', ?3, ?4)",
                params![
                    digest.to_string(),
                    PDF_MEDIA_TYPE,
                    crate::Vault::object_key(digest),
                    json_text(&quarantine, ONE_MIB).expect("quarantine JSON"),
                ],
            )
            .expect("insert retained quarantine");
        store
            .connection
            .execute(
                "INSERT INTO job_runs VALUES (?1, ?2, 'pdf_ingest', 'succeeded', 1, 0, 0, 'completed')",
                params![
                    job_id.as_uuid().to_string(),
                    project_id.as_uuid().to_string(),
                ],
            )
            .expect("insert terminal quarantine job");
        store
            .connection
            .execute(
                "INSERT INTO ingest_events VALUES (?1, ?2, ?3, ?4, ?5, 1, 0)",
                params![
                    uuid(911).hyphenated().to_string(),
                    project_id.as_uuid().to_string(),
                    job_id.as_uuid().to_string(),
                    digest.to_string(),
                    outcome,
                ],
            )
            .expect("insert retained quarantine event");
        (store, project_id)
    }

    #[test]
    fn accepted_commit_requires_the_exact_durable_processing_authority() {
        // Break caught: terminal authority drifting from the frozen input/checkpoint job anchor.
        let (store, command) = accepted_commit_fixture();
        let vault_checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::VaultPublished {
                original: command.original.clone(),
            },
        };
        store
            .connection
            .execute(
                "UPDATE job_runs SET checkpoint_json = ?1, updated_at_ms = 1",
                [json_text(&vault_checkpoint, EIGHT_MIB).expect("vault checkpoint JSON")],
            )
            .expect("replace processing checkpoint with vault checkpoint");
        assert_accepted_commit_rejected_without_authority(store, command);

        let (store, mut command) = accepted_commit_fixture();
        command.idempotency_key = IdempotencyKey::try_from("different-key").expect("key");
        assert_accepted_commit_rejected_without_authority(store, command);

        let (store, mut command) = accepted_commit_fixture();
        command.source_display = "utf8:different.pdf".to_owned();
        assert_accepted_commit_rejected_without_authority(store, command);

        let (store, mut command) = accepted_commit_fixture();
        replace_command_object_authority(&mut command);
        assert_accepted_commit_rejected_without_authority(store, command);
    }

    #[test]
    fn revision_evidence_count_is_bounded_before_rows_and_detects_count_drift() {
        // Break caught: streaming hostile rows before the 100,000 count cap or trusting drift.
        let (store, revision) = minimal_revision_evidence_store(100_000, true);
        assert_eq!(
            store
                .revision_evidence_rows(revision)
                .expect("100,000 lineages are permitted")
                .evidence
                .len(),
            100_000
        );

        let (store, revision) = minimal_revision_evidence_store(100_001, false);
        assert!(matches!(
            store.revision_evidence_rows(revision),
            Err(HeleosError::ResourceLimit)
        ));

        let (store, revision) = minimal_revision_evidence_store(2, true);
        let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let function_calls = std::sync::Arc::clone(&calls);
        store
            .connection
            .create_scalar_function(
                "task6_drift_visible",
                1,
                rusqlite::functions::FunctionFlags::SQLITE_UTF8
                    | rusqlite::functions::FunctionFlags::SQLITE_INNOCUOUS,
                move |context| {
                    use std::sync::atomic::Ordering;

                    let rowid = context.get::<i64>(0)?;
                    let call = function_calls.fetch_add(1, Ordering::SeqCst);
                    Ok(call < 2 || rowid == 1)
                },
            )
            .expect("register deterministic drift seam");
        store
            .connection
            .execute_batch(
                "ALTER TABLE evidence_objects RENAME TO evidence_base;
                 CREATE VIEW evidence_objects AS
                 SELECT id, project_id, job_id, document_revision_id, content_sha256,
                        parent_content_sha256, extraction_method, parameters_json, review_state
                 FROM evidence_base
                 WHERE task6_drift_visible(rowid);",
            )
            .expect("install count-drift view");
        assert!(matches!(
            store.revision_evidence_rows(revision),
            Err(HeleosError::Integrity)
        ));
    }

    #[test]
    fn revision_evidence_page_count_accepts_n_and_rejects_n_plus_one_before_rows() {
        // Break caught: fetching page metadata before enforcing the exact 10,000-page cap.
        let (mut store, revision) = minimal_revision_evidence_store(1, true);
        store
            .connection
            .execute("DELETE FROM sheets", [])
            .expect("clear minimal sheet");
        let transaction = store.connection.transaction().expect("begin page fixture");
        {
            let mut insert = transaction
                .prepare("INSERT INTO sheets VALUES (?1, ?2, ?3, 1, 1, 0, 'pt', ?2, ?4)")
                .expect("prepare page insert");
            let transform = PageTransform {
                m11: 1,
                m12: 0,
                m21: 0,
                m22: -1,
                tx_micropoints: 0,
                ty_micropoints: 1,
            };
            let transform_json = json_text(&transform, ONE_MIB).expect("page transform JSON");
            for index in 0_u32..10_000 {
                insert
                    .execute(params![
                        page_id(*revision.as_digest(), index)
                            .as_digest()
                            .to_string(),
                        revision.as_digest().to_string(),
                        i64::from(index),
                        transform_json,
                    ])
                    .expect("insert page");
            }
        }
        transaction.commit().expect("commit page fixture");
        assert_eq!(
            store
                .revision_evidence_rows(revision)
                .expect("10,000 pages are permitted")
                .pages
                .len(),
            10_000
        );
        store
            .connection
            .execute("INSERT INTO sheets SELECT * FROM sheets LIMIT 1", [])
            .expect("insert page cap sentinel");
        assert!(matches!(
            store.revision_evidence_rows(revision),
            Err(HeleosError::ResourceLimit)
        ));
    }

    #[test]
    fn foundation_inspection_accepts_only_sticky_database_quarantines() {
        // Break caught: a preflight-only quarantine reason appearing as retained content.
        for reason in [
            IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::BadMagic),
            IntakeQuarantineReasonV1::EvidenceManifestQuota,
        ] {
            let (store, project_id) = inspection_store_with_quarantine(reason);
            store
                .foundation_inspection_rows(project_id)
                .expect("sticky quarantine is inspectable");
        }

        for reason in [
            IntakeQuarantineReasonV1::InputBytes {
                limit_bytes: PdfLimits::default().max_input_bytes,
                observed_bytes: PdfLimits::default().max_input_bytes + 1,
            },
            IntakeQuarantineReasonV1::OriginalRetentionQuota,
            IntakeQuarantineReasonV1::ProjectAssociationQuota,
        ] {
            let (store, project_id) = inspection_store_with_quarantine(reason);
            assert!(matches!(
                store.foundation_inspection_rows(project_id),
                Err(HeleosError::Integrity)
            ));
        }
    }

    #[test]
    fn foundation_inspection_counts_are_bounded_before_rows_and_detect_drift() {
        // Break caught: inspection allocating from row declarations or trusting count/fetch drift.
        let (store, project_id) =
            minimal_foundation_inspection_store(MAX_FOUNDATION_INSPECTION_ROWS);
        let inspection = store
            .foundation_inspection_rows(project_id)
            .expect("exact 100,000 total rows are permitted");
        assert_eq!(
            inspection.counts.audit_events,
            u64::try_from(MAX_FOUNDATION_INSPECTION_ROWS).expect("inspection cap")
        );

        let (store, project_id) =
            minimal_foundation_inspection_store(MAX_FOUNDATION_INSPECTION_ROWS + 1);
        assert!(matches!(
            store.foundation_inspection_rows(project_id),
            Err(HeleosError::ResourceLimit)
        ));

        let (store, project_id) = minimal_foundation_inspection_store(2);
        let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let function_calls = std::sync::Arc::clone(&calls);
        store
            .connection
            .create_scalar_function(
                "task6_inspection_drift_visible",
                1,
                rusqlite::functions::FunctionFlags::SQLITE_UTF8
                    | rusqlite::functions::FunctionFlags::SQLITE_INNOCUOUS,
                move |context| {
                    use std::sync::atomic::Ordering;

                    let sequence = context.get::<i64>(0)?;
                    let call = function_calls.fetch_add(1, Ordering::SeqCst);
                    Ok(call < 2 || sequence == 1)
                },
            )
            .expect("register inspection drift seam");
        store
            .connection
            .execute_batch(
                "ALTER TABLE audit_events RENAME TO audit_base;
                 CREATE VIEW audit_events AS
                 SELECT id, sequence, project_id
                 FROM audit_base
                 WHERE task6_inspection_drift_visible(sequence);",
            )
            .expect("install inspection count-drift view");
        assert!(matches!(
            store.foundation_inspection_rows(project_id),
            Err(HeleosError::Integrity)
        ));
    }

    #[test]
    fn accepted_commit_audits_a_bounded_summary_not_the_full_lineage_receipt() {
        // Break caught: a valid large lineage receipt exceeding the independent audit JSON cap.
        let (mut store, mut command) = accepted_commit_fixture();
        prepopulate_revision_lineages(&mut store, &command, 7_000);
        command.preexisting_vault_digests =
            vec![command.original.digest, command.manifest_object.digest];
        command.preexisting_vault_digests.sort_unstable();
        let receipt = store
            .accepted_intake_commit(command)
            .expect("large valid receipt commits");
        assert!(
            serde_json::to_vec(&receipt)
                .expect("serialize large receipt")
                .len()
                > ONE_MIB
        );
        let after_json = store
            .connection
            .query_row(
                "SELECT after_json FROM audit_events WHERE action = 'ingest_accepted'",
                [],
                |row| row.get::<_, String>(0),
            )
            .expect("read accepted audit summary");
        assert!(after_json.len() <= ONE_MIB);
        let after: JsonValue = serde_json::from_str(&after_json).expect("parse audit summary");
        let evidence = receipt
            .evidence_manifest
            .as_ref()
            .expect("accepted evidence receipt");
        let evidence_id = evidence
            .lineages
            .iter()
            .find(|lineage| lineage.project_id == ProjectId::from_uuid(uuid(100)))
            .expect("current project lineage")
            .evidence_id;
        assert_eq!(
            after,
            json!({
                "attempt": receipt.attempt,
                "authoritative_job_id": receipt.authoritative_job_id,
                "content_sha256": receipt.content_sha256,
                "document_id": receipt.document_id,
                "evidence_id": evidence_id,
                "ingest_event_id": receipt.ingest_event_id,
                "manifest_content_sha256": evidence.manifest_content_sha256,
                "revision_id": receipt.revision_id,
            })
        );
    }

    #[test]
    fn borrowed_audit_text_is_copied_only_within_the_field_cap() {
        // Break caught: row.get::<Value> allocating hostile audit text before its cap is checked.
        let connection = rusqlite::Connection::open_in_memory().expect("open test connection");
        let at_cap = "x".repeat(1024 * 1024);
        let over_cap = "x".repeat(1024 * 1024 + 1);
        let mut statement = connection
            .prepare("SELECT ?1 UNION ALL SELECT ?2")
            .expect("prepare bounded-value query");
        let mut rows = statement
            .query(params![at_cap, over_cap])
            .expect("query bounded values");
        let first = rows.next().expect("first row query").expect("first row");
        assert!(matches!(
            bounded_value(first, 0, 1024 * 1024).expect("read at-cap value"),
            RawAuditValue::Text(value) if value.len() == 1024 * 1024
        ));
        let second = rows.next().expect("second row query").expect("second row");
        assert!(matches!(
            bounded_value(second, 0, 1024 * 1024).expect("read over-cap value"),
            RawAuditValue::Invalid
        ));
    }

    #[test]
    fn persisted_job_reconstruction_binds_budget_checkpoint_and_terminal_job_authority() {
        // Break caught: individually valid JCS objects drifting from their containing job.
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let project_id = ProjectId::from_uuid(uuid(1));
        insert_project(&store, project_id);

        let job_id = JobId::from_uuid(uuid(2));
        let input = valid_input(project_id, "budget-drift");
        let budget = IngestBudgetV1::new(input.byte_length + 1);
        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::Terminal {
                receipt: Box::new(accepted_receipt(
                    job_id,
                    vec![EvidenceManifestLineage {
                        project_id,
                        evidence_id: EvidenceId::from_uuid(uuid(3)),
                        originating_job_id: job_id,
                    }],
                )),
            },
        };
        insert_job(
            &store,
            project_id,
            job_id,
            "budget-drift",
            &input,
            &budget,
            &checkpoint,
        );
        assert!(matches!(
            store.idempotency_lookup(project_id, &input.idempotency_key),
            Err(HeleosError::Integrity)
        ));

        let job_id = JobId::from_uuid(uuid(4));
        let input = valid_input(project_id, "job-drift");
        let budget = IngestBudgetV1::new(input.byte_length);
        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::Terminal {
                receipt: Box::new(accepted_receipt(
                    JobId::from_uuid(uuid(99)),
                    vec![EvidenceManifestLineage {
                        project_id,
                        evidence_id: EvidenceId::from_uuid(uuid(5)),
                        originating_job_id: JobId::from_uuid(uuid(99)),
                    }],
                )),
            },
        };
        insert_job(
            &store,
            project_id,
            job_id,
            "job-drift",
            &input,
            &budget,
            &checkpoint,
        );
        assert!(matches!(
            store.idempotency_lookup(project_id, &input.idempotency_key),
            Err(HeleosError::Integrity)
        ));

        let job_id = JobId::from_uuid(uuid(6));
        let input = valid_input(project_id, "deadline-drift");
        let budget = IngestBudgetV1::new(input.byte_length);
        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::Terminal {
                receipt: Box::new(accepted_receipt(
                    job_id,
                    vec![EvidenceManifestLineage {
                        project_id,
                        evidence_id: EvidenceId::from_uuid(uuid(7)),
                        originating_job_id: job_id,
                    }],
                )),
            },
        };
        insert_job_with_times(
            (
                &store,
                project_id,
                job_id,
                "deadline-drift",
                &input,
                &budget,
                &checkpoint,
            ),
            10,
            300_011,
        );
        assert!(matches!(
            store.idempotency_lookup(project_id, &input.idempotency_key),
            Err(HeleosError::Integrity)
        ));
    }

    #[test]
    fn repository_derives_fixed_deadline_and_lease_boundaries() {
        // Break caught: caller-selected recovery clocks escaping the frozen lifecycle profile.
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let project_id = ProjectId::from_uuid(uuid(30));
        insert_project(&store, project_id);
        let actor = ActorId::from_str("fixture-actor").expect("actor");
        let job_id = JobId::from_uuid(uuid(31));
        let input = valid_input(project_id, "deadline-command-drift");
        let original = StoredObjectV1 {
            digest: input.content_sha256.expect("content digest"),
            byte_length: input.byte_length,
            vault_key: crate::Vault::object_key(input.content_sha256.expect("content digest")),
        };
        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::VaultPublished { original },
        };
        assert!(matches!(
            store.job_create_with_checkpoint_and_audit(JobCreateCommand {
                job_id,
                project_id,
                input: input.clone(),
                budget: IngestBudgetV1::new(input.byte_length),
                checkpoint: checkpoint.clone(),
                actor: actor.clone(),
                created_at_ms: 10,
                deadline_at_ms: 300_011,
                audit_event_id: AuditEventId::from_uuid(uuid(32)),
            }),
            Err(HeleosError::Integrity)
        ));
        let near_jcs_max = i64::try_from(JCS_SAFE_INTEGER_MAX).expect("JCS maximum fits i64");
        assert!(matches!(
            store.job_create_with_checkpoint_and_audit(JobCreateCommand {
                job_id,
                project_id,
                input: input.clone(),
                budget: IngestBudgetV1::new(input.byte_length),
                checkpoint: checkpoint.clone(),
                actor: actor.clone(),
                created_at_ms: near_jcs_max - JOB_DEADLINE_MS + 1,
                deadline_at_ms: near_jcs_max,
                audit_event_id: AuditEventId::from_uuid(uuid(36)),
            }),
            Err(HeleosError::Integrity)
        ));

        store
            .job_create_with_checkpoint_and_audit(JobCreateCommand {
                job_id,
                project_id,
                input,
                budget: IngestBudgetV1::new(7),
                checkpoint,
                actor: actor.clone(),
                created_at_ms: 10,
                deadline_at_ms: 300_010,
                audit_event_id: AuditEventId::from_uuid(uuid(33)),
            })
            .expect("create job with fixed deadline");
        assert!(matches!(
            store.job_start_or_resume_with_audit(JobStartCommand {
                job_id,
                project_id,
                actor,
                lease_owner: uuid(34),
                now_ms: 20,
                lease_expires_at_ms: 30_021,
                audit_event_id: AuditEventId::from_uuid(uuid(35)),
            }),
            Err(HeleosError::Integrity)
        ));
        assert!(matches!(
            store.job_start_or_resume_with_audit(JobStartCommand {
                job_id,
                project_id,
                actor: ActorId::from_str("fixture-actor").expect("actor"),
                lease_owner: uuid(37),
                now_ms: near_jcs_max - LEASE_DURATION_MS + 1,
                lease_expires_at_ms: near_jcs_max,
                audit_event_id: AuditEventId::from_uuid(uuid(38)),
            }),
            Err(HeleosError::Integrity)
        ));
    }

    #[test]
    fn recovery_distinguishes_expiry_minus_one_from_exact_expiry() {
        // Break caught: an unexpired lease being interrupted, or equality failing to recover.
        let (mut store, project_id, job_id) =
            queued_recovery_fixture(10, "recovery-lease-boundary");
        start_inventory_job(
            &mut store,
            project_id,
            job_id,
            20,
            uuid(31_000),
            AuditEventId::from_uuid(uuid(31_001)),
        );
        let lease_expiry = 20 + LEASE_DURATION_MS;
        assert!(matches!(
            store.job_recover_expired_attempt_with_audit(recovery_command(
                job_id,
                lease_expiry - 1,
                31_010,
            )),
            Err(HeleosError::LeaseUnavailable)
        ));
        assert_eq!(
            job_state_row(&store, job_id),
            ("running".to_owned(), 1, Some(lease_expiry), None)
        );
        assert_eq!(job_actions(&store, job_id), ["job_created", "job_started"]);
        assert_eq!(
            store
                .connection
                .query_row("SELECT COUNT(*) FROM ingest_events", [], |row| row
                    .get::<_, i64>(0))
                .expect("count pre-recovery events"),
            0
        );

        let recovery = store
            .job_recover_expired_attempt_with_audit(recovery_command(job_id, lease_expiry, 31_020))
            .expect("recover at exact lease expiry");
        match recovery {
            JobRecoveryOutcome::Started {
                record,
                interrupted_event_id,
            } => {
                assert_eq!(record.state, JobState::Running);
                assert_eq!(record.attempt, 2);
                assert_eq!(
                    record.lease_expires_at_ms,
                    Some(lease_expiry + LEASE_DURATION_MS)
                );
                assert_eq!(
                    interrupted_event_id,
                    Some(IngestEventId::from_uuid(uuid(31_021)))
                );
            }
            _ => panic!("exact expiry did not resume the attempt"),
        }
        assert_eq!(
            job_actions(&store, job_id),
            [
                "job_created",
                "job_started",
                "job_interrupted",
                "job_resumed"
            ]
        );
        let event: (String, i64) = store
            .connection
            .query_row(
                "SELECT outcome, attempt FROM ingest_events WHERE job_id = ?1",
                [job_id.as_uuid().to_string()],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .expect("read interruption event");
        assert_eq!(event, ("interrupted".to_owned(), 1));
    }

    #[test]
    fn recovery_applies_deadline_before_lease_and_preserves_queued_action_order() {
        // Break caught: a running deadline being masked by a live lease, or queued failure
        // omitting its exact transient start image and audit order.
        let (mut running, project_id, job_id) =
            queued_recovery_fixture(10, "running-deadline-priority");
        let deadline = 10 + JOB_DEADLINE_MS;
        start_inventory_job(
            &mut running,
            project_id,
            job_id,
            deadline - 10_000,
            uuid(32_000),
            AuditEventId::from_uuid(uuid(32_001)),
        );
        assert!(matches!(
            running
                .job_recover_expired_attempt_with_audit(recovery_command(job_id, deadline, 32_010))
                .expect("deadline is a finite recovery result"),
            JobRecoveryOutcome::DeadlineExpired
        ));
        assert_eq!(
            job_state_row(&running, job_id),
            (
                "failed".to_owned(),
                1,
                None,
                Some("deadline_expired".to_owned())
            )
        );
        assert_eq!(
            job_actions(&running, job_id),
            ["job_created", "job_started", "job_failed"]
        );
        assert_eq!(
            running
                .connection
                .query_row("SELECT COUNT(*) FROM ingest_events", [], |row| row
                    .get::<_, i64>(0))
                .expect("count running deadline events"),
            0
        );

        let (mut queued, _, queued_job) = queued_recovery_fixture(10, "queued-deadline");
        assert!(matches!(
            queued
                .job_recover_expired_attempt_with_audit(recovery_command(
                    queued_job, deadline, 32_100,
                ))
                .expect("queued deadline is finite"),
            JobRecoveryOutcome::DeadlineExpired
        ));
        assert_eq!(
            job_actions(&queued, queued_job),
            ["job_created", "job_started", "job_failed"]
        );
        let started_after: String = queued
            .connection
            .query_row(
                "SELECT after_json FROM audit_events
                 WHERE subject_type = 'job' AND subject_id = ?1 AND action = 'job_started'",
                [queued_job.as_uuid().to_string()],
                |row| row.get(0),
            )
            .expect("read queued start audit image");
        let started_after: JsonValue =
            serde_json::from_str(&started_after).expect("parse queued start audit image");
        assert_eq!(
            started_after["lease_expires_at_ms"],
            json!(deadline + LEASE_DURATION_MS)
        );

        let maximum = i64::try_from(JCS_SAFE_INTEGER_MAX).expect("JCS maximum fits i64");
        let (mut maximum_queued, _, maximum_job) =
            queued_recovery_fixture(maximum - JOB_DEADLINE_MS, "queued-deadline-jcs-maximum");
        assert!(matches!(
            maximum_queued
                .job_recover_expired_attempt_with_audit(recovery_command(
                    maximum_job,
                    maximum,
                    32_200,
                ))
                .expect("maximum queued deadline is finite"),
            JobRecoveryOutcome::DeadlineExpired
        ));
        let maximum_started_after: String = maximum_queued
            .connection
            .query_row(
                "SELECT after_json FROM audit_events
                 WHERE subject_type = 'job' AND subject_id = ?1 AND action = 'job_started'",
                [maximum_job.as_uuid().to_string()],
                |row| row.get(0),
            )
            .expect("read maximum queued start image");
        let maximum_started_after: JsonValue =
            serde_json::from_str(&maximum_started_after).expect("parse maximum queued start image");
        assert_eq!(maximum_started_after["lease_expires_at_ms"], json!(maximum));
    }

    #[test]
    fn recovery_stops_at_attempt_sixteen_and_rejects_terminal_non_success_states() {
        // Break caught: an attempt 17 or interruption event escaping the terminal attempt cap.
        let (mut store, _, job_id) = queued_recovery_fixture(10, "attempt-sixteen");
        let checkpoint = store
            .idempotency_lookup(
                ProjectId::from_uuid(uuid(30_000)),
                &IdempotencyKey::try_from("attempt-sixteen").expect("key"),
            )
            .expect("read queued job");
        let IdempotencyLookup::Existing(record) = checkpoint else {
            panic!("queued job is present");
        };
        forge_job_state_and_matching_anchor(
            &mut store,
            job_id,
            ForgedJobState {
                state: "running",
                attempt: MAX_JOB_ATTEMPTS,
                lease_owner: Some(uuid(33_000)),
                lease_expires_at_ms: Some(100),
                terminal_reason: None,
                checkpoint: &record.checkpoint,
                updated_at_ms: 99,
            },
        );
        assert!(matches!(
            store
                .job_recover_expired_attempt_with_audit(recovery_command(job_id, 100, 33_010))
                .expect("attempt limit is finite"),
            JobRecoveryOutcome::AttemptLimit
        ));
        assert_eq!(
            job_state_row(&store, job_id),
            (
                "failed".to_owned(),
                i64::from(MAX_JOB_ATTEMPTS),
                None,
                Some("attempt_limit".to_owned())
            )
        );
        assert_eq!(job_actions(&store, job_id), ["job_created", "job_failed"]);
        assert_eq!(
            store
                .connection
                .query_row("SELECT COUNT(*) FROM ingest_events", [], |row| row
                    .get::<_, i64>(0))
                .expect("count attempt-limit events"),
            0
        );

        for (state, attempt, reason, base) in [
            ("failed", 1, "internal_failure", 33_100_u128),
            ("cancelled", 0, "cancelled", 33_200_u128),
        ] {
            let key = format!("terminal-{state}");
            let (mut terminal, _, terminal_job) = queued_recovery_fixture(10, &key);
            let IdempotencyLookup::Existing(record) = terminal
                .idempotency_lookup(
                    ProjectId::from_uuid(uuid(30_000)),
                    &IdempotencyKey::try_from(key).expect("key"),
                )
                .expect("read terminal fixture")
            else {
                panic!("terminal fixture exists");
            };
            forge_job_state_and_matching_anchor(
                &mut terminal,
                terminal_job,
                ForgedJobState {
                    state,
                    attempt,
                    lease_owner: None,
                    lease_expires_at_ms: None,
                    terminal_reason: Some(reason),
                    checkpoint: &record.checkpoint,
                    updated_at_ms: 20,
                },
            );
            let before_actions = job_actions(&terminal, terminal_job);
            assert!(matches!(
                terminal.job_recover_expired_attempt_with_audit(recovery_command(
                    terminal_job,
                    30,
                    base,
                )),
                Err(HeleosError::InvalidStateTransition)
            ));
            assert_eq!(job_actions(&terminal, terminal_job), before_actions);
        }

        let (mut interrupted, project_id, interrupted_job) =
            queued_recovery_fixture(10, "persisted-interrupted");
        start_inventory_job(
            &mut interrupted,
            project_id,
            interrupted_job,
            20,
            uuid(33_300),
            AuditEventId::from_uuid(uuid(33_301)),
        );
        let IdempotencyLookup::Existing(interrupted_record) = interrupted
            .idempotency_lookup(
                project_id,
                &IdempotencyKey::try_from("persisted-interrupted").expect("key"),
            )
            .expect("read running interruption fixture")
        else {
            panic!("running interruption fixture exists");
        };
        let interrupted_at = 20 + LEASE_DURATION_MS;
        let interrupted_event_id = IngestEventId::from_uuid(uuid(33_302));
        interrupted
            .with_immediate_transaction(|transaction| {
                let before = job_audit_snapshot(transaction, interrupted_job)?;
                let changed = transaction
                    .execute(
                        "UPDATE job_runs
                         SET state = 'interrupted', lease_owner = NULL,
                             lease_expires_at_ms = NULL, updated_at_ms = ?2
                         WHERE id = ?1 AND state = 'running' AND attempt = 1",
                        params![interrupted_job.as_uuid().to_string(), interrupted_at],
                    )
                    .map_err(|_| HeleosError::Database)?;
                if changed != 1 {
                    return Err(HeleosError::Integrity);
                }
                let checkpoint_json = json_text(&interrupted_record.checkpoint, EIGHT_MIB)?;
                let details_json = json_text(
                    &IngestEventDetailV1::Interrupted {
                        schema: INGEST_EVENT_DETAILS_SCHEMA_V1.to_owned(),
                        attempt: 1,
                        checkpoint_sha256: sha256_text(&checkpoint_json),
                    },
                    ONE_MIB,
                )?;
                transaction
                    .execute(
                        "INSERT INTO ingest_events
                            (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
                             source_path, idempotency_key, actor, terminal_at_ms, details_json)
                         VALUES (?1, ?2, ?3, NULL, 'interrupted', 1, ?4, '<redacted>',
                                 ?5, ?6, ?7, ?8)",
                        params![
                            interrupted_event_id.as_uuid().to_string(),
                            project_id.as_uuid().to_string(),
                            interrupted_job.as_uuid().to_string(),
                            interrupted_record.input.source_display,
                            interrupted_record.input.idempotency_key.as_str(),
                            "fixture-actor",
                            interrupted_at,
                            details_json,
                        ],
                    )
                    .map_err(|_| HeleosError::Database)?;
                append_action(
                    transaction,
                    AppendAction {
                        id: AuditEventId::from_uuid(uuid(33_303)),
                        project_id,
                        actor: ActorId::from_str("fixture-actor")?,
                        action: AuditAction::JobInterrupted,
                        subject_type: AuditSubjectType::Job,
                        subject_id: interrupted_job.as_uuid().to_string(),
                        before,
                        after: job_audit_snapshot(transaction, interrupted_job)?,
                        reason: "fixture persisted interruption",
                        occurred_at_ms: interrupted_at,
                    },
                )?;
                Ok(())
            })
            .expect("persist canonical interrupted authority");
        assert_eq!(
            job_state_row(&interrupted, interrupted_job),
            ("interrupted".to_owned(), 1, None, None)
        );
        assert_eq!(
            job_actions(&interrupted, interrupted_job),
            ["job_created", "job_started", "job_interrupted"]
        );
        let before = recovery_authority_image(&interrupted, interrupted_job);
        assert!(matches!(
            interrupted.job_recover_expired_attempt_with_audit(recovery_command(
                interrupted_job,
                interrupted_at,
                33_310,
            )),
            Err(HeleosError::InvalidStateTransition)
        ));
        assert_eq!(
            recovery_authority_image(&interrupted, interrupted_job),
            before
        );
    }

    #[test]
    fn recovery_avoids_unused_lease_arithmetic_for_terminal_and_rolls_back_overflow() {
        // Break caught: terminal replay or deadline precedence being masked by an unused
        // now+lease overflow, or a nonterminal overflow committing half an interruption.
        let maximum = i64::try_from(JCS_SAFE_INTEGER_MAX).expect("JCS maximum fits i64");
        let created_at = maximum - JOB_DEADLINE_MS;
        let (mut terminal, project_id, terminal_job) =
            queued_recovery_fixture(created_at, "terminal-at-jcs-maximum");
        let receipt = accepted_receipt(
            terminal_job,
            vec![EvidenceManifestLineage {
                project_id,
                evidence_id: EvidenceId::from_uuid(uuid(34_000)),
                originating_job_id: terminal_job,
            }],
        );
        let terminal_checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::Terminal {
                receipt: Box::new(receipt),
            },
        };
        forge_job_state_and_matching_anchor(
            &mut terminal,
            terminal_job,
            ForgedJobState {
                state: "succeeded",
                attempt: 1,
                lease_owner: None,
                lease_expires_at_ms: None,
                terminal_reason: Some("completed"),
                checkpoint: &terminal_checkpoint,
                updated_at_ms: maximum,
            },
        );
        match terminal
            .job_recover_expired_attempt_with_audit(recovery_command(terminal_job, maximum, 34_010))
            .expect("terminal replay ignores lease arithmetic")
        {
            JobRecoveryOutcome::Terminal(record) => {
                assert_eq!(record.state, JobState::Succeeded);
                assert_eq!(record.attempt, 1);
            }
            _ => panic!("terminal recovery did not return stored authority"),
        }
        assert_eq!(job_actions(&terminal, terminal_job), ["job_created"]);

        let overflow_now = maximum - LEASE_DURATION_MS + 1;
        let (mut queued, _, queued_job) =
            queued_recovery_fixture(created_at, "queued-lease-overflow");
        assert!(matches!(
            queued.job_recover_expired_attempt_with_audit(recovery_command(
                queued_job,
                overflow_now,
                34_100,
            )),
            Err(HeleosError::Integrity)
        ));
        assert_eq!(
            job_state_row(&queued, queued_job),
            ("queued".to_owned(), 0, None, None)
        );
        assert_eq!(job_actions(&queued, queued_job), ["job_created"]);

        let (mut running, _, running_job) =
            queued_recovery_fixture(created_at, "running-lease-overflow");
        let IdempotencyLookup::Existing(record) = running
            .idempotency_lookup(
                ProjectId::from_uuid(uuid(30_000)),
                &IdempotencyKey::try_from("running-lease-overflow").expect("key"),
            )
            .expect("read running overflow fixture")
        else {
            panic!("running overflow fixture exists");
        };
        forge_job_state_and_matching_anchor(
            &mut running,
            running_job,
            ForgedJobState {
                state: "running",
                attempt: 1,
                lease_owner: Some(uuid(34_200)),
                lease_expires_at_ms: Some(overflow_now),
                terminal_reason: None,
                checkpoint: &record.checkpoint,
                updated_at_ms: overflow_now,
            },
        );
        assert!(matches!(
            running.job_recover_expired_attempt_with_audit(recovery_command(
                running_job,
                overflow_now,
                34_210,
            )),
            Err(HeleosError::Integrity)
        ));
        assert_eq!(
            job_state_row(&running, running_job),
            ("running".to_owned(), 1, Some(overflow_now), None)
        );
        assert_eq!(job_actions(&running, running_job), ["job_created"]);
        assert_eq!(
            running
                .connection
                .query_row("SELECT COUNT(*) FROM ingest_events", [], |row| row
                    .get::<_, i64>(0))
                .expect("count rolled-back interruption events"),
            0
        );
    }

    #[test]
    fn quota_snapshot_reserves_nonterminal_vault_published_objects() {
        // Break caught: a crashed published object leaving quota headroom available to a rival job.
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let project_id = ProjectId::from_uuid(uuid(40));
        insert_project(&store, project_id);
        let input = valid_input(project_id, "reserved-original");
        let digest = input.content_sha256.expect("complete digest");
        store
            .job_create_with_checkpoint_and_audit(JobCreateCommand {
                job_id: JobId::from_uuid(uuid(41)),
                project_id,
                input: input.clone(),
                budget: IngestBudgetV1::new(input.byte_length),
                checkpoint: IngestCheckpointV1 {
                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                    phase: IngestCheckpointPhaseV1::VaultPublished {
                        original: StoredObjectV1 {
                            digest,
                            byte_length: input.byte_length,
                            vault_key: crate::Vault::object_key(digest),
                        },
                    },
                },
                actor: ActorId::from_str("fixture-actor").expect("actor"),
                created_at_ms: 10,
                deadline_at_ms: 300_010,
                audit_event_id: AuditEventId::from_uuid(uuid(42)),
            })
            .expect("create reserved job");

        let snapshot = store
            .quota_snapshot(project_id, None)
            .expect("snapshot durable reservations");
        assert_eq!(snapshot.project_overall_bytes, input.byte_length);
        assert_eq!(snapshot.project_quarantine_bytes, input.byte_length);
        assert_eq!(snapshot.store_quarantine_bytes, input.byte_length);
        assert_eq!(snapshot.project_evidence_bytes, 0);
        assert_eq!(snapshot.store_evidence_bytes, 0);
    }

    #[test]
    fn quota_snapshot_classifies_processing_reservations_without_double_counting() {
        // Break caught: processing checkpoints dropping original/manifest category reservations.
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let project_id = ProjectId::from_uuid(uuid(50));
        insert_project(&store, project_id);
        let actor = ActorId::from_str("fixture-actor").expect("actor");
        let input = valid_input(project_id, "accepted-reservation");
        let original = StoredObjectV1 {
            digest: input.content_sha256.expect("complete digest"),
            byte_length: input.byte_length,
            vault_key: crate::Vault::object_key(input.content_sha256.expect("complete digest")),
        };
        let manifest = accepted_manifest(original.digest);
        let manifest_bytes = manifest.canonical_bytes().expect("manifest bytes");
        let manifest_object = StoredObjectV1 {
            digest: Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest"),
            byte_length: u64::try_from(manifest_bytes.len()).expect("manifest length"),
            vault_key: crate::Vault::object_key(
                Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest"),
            ),
        };
        let job_id = JobId::from_uuid(uuid(51));
        store
            .job_create_with_checkpoint_and_audit(JobCreateCommand {
                job_id,
                project_id,
                input: input.clone(),
                budget: IngestBudgetV1::new(input.byte_length),
                checkpoint: IngestCheckpointV1 {
                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                    phase: IngestCheckpointPhaseV1::VaultPublished {
                        original: original.clone(),
                    },
                },
                actor: actor.clone(),
                created_at_ms: 10,
                deadline_at_ms: 300_010,
                audit_event_id: AuditEventId::from_uuid(uuid(52)),
            })
            .expect("create accepted reservation");
        store
            .job_start_or_resume_with_audit(JobStartCommand {
                job_id,
                project_id,
                actor: actor.clone(),
                lease_owner: uuid(53),
                now_ms: 11,
                lease_expires_at_ms: 30_011,
                audit_event_id: AuditEventId::from_uuid(uuid(54)),
            })
            .expect("start accepted reservation");
        store
            .job_checkpoint_with_audit(JobCheckpointCommand {
                job_id,
                project_id,
                actor,
                attempt: 1,
                checkpoint: IngestCheckpointV1 {
                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                    phase: IngestCheckpointPhaseV1::ProcessingComplete {
                        original: original.clone(),
                        candidate: crate::ingest::job::ProcessingCandidateV1::Accepted {
                            manifest: Box::new(manifest.clone()),
                            manifest_object: manifest_object.clone(),
                        },
                    },
                },
                now_ms: 12,
                audit_event_id: AuditEventId::from_uuid(uuid(55)),
            })
            .expect("checkpoint accepted reservation");

        let snapshot = store
            .quota_snapshot(project_id, Some(original.digest))
            .expect("snapshot accepted reservations");
        let combined = original
            .byte_length
            .checked_add(manifest_object.byte_length)
            .expect("combined length");
        assert_eq!(snapshot.project_overall_bytes, combined);
        assert_eq!(snapshot.store_evidence_bytes, manifest_object.byte_length);
        assert_eq!(snapshot.project_evidence_bytes, manifest_object.byte_length);
        assert_eq!(snapshot.store_quarantine_bytes, 0);
        assert_eq!(snapshot.project_quarantine_bytes, 0);
        assert!(snapshot.project_overall_accounted);
        assert!(!snapshot.project_quarantine_accounted);

        store
            .accepted_intake_commit(AcceptedIntakeCommand {
                job_id,
                project_id,
                attempt: 1,
                actor: ActorId::from_str("fixture-actor").expect("actor"),
                idempotency_key: input.idempotency_key.clone(),
                source_display: input.source_display.clone(),
                original: original.clone(),
                manifest_object: manifest_object.clone(),
                manifest,
                pages: vec![accepted_page(original.digest)],
                ingest_event_id: IngestEventId::from_uuid(uuid(56)),
                source_record_id: uuid(57).hyphenated().to_string(),
                evidence_id: EvidenceId::from_uuid(uuid(58)),
                now_ms: 20,
                preexisting_vault_digests: Vec::new(),
                audit_event_ids: [
                    AuditEventId::from_uuid(uuid(59)),
                    AuditEventId::from_uuid(uuid(60)),
                    AuditEventId::from_uuid(uuid(61)),
                ],
            })
            .expect("commit canonical accepted authority");
        let duplicate_input = valid_input(project_id, "accepted-reservation-duplicate");
        store
            .job_create_with_checkpoint_and_audit(JobCreateCommand {
                job_id: JobId::from_uuid(uuid(62)),
                project_id,
                input: duplicate_input.clone(),
                budget: IngestBudgetV1::new(duplicate_input.byte_length),
                checkpoint: IngestCheckpointV1 {
                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                    phase: IngestCheckpointPhaseV1::VaultPublished {
                        original: original.clone(),
                    },
                },
                actor: ActorId::from_str("fixture-actor").expect("actor"),
                created_at_ms: 21,
                deadline_at_ms: 300_021,
                audit_event_id: AuditEventId::from_uuid(uuid(63)),
            })
            .expect("create canonical duplicate reservation");
        let deduplicated = store
            .quota_snapshot(project_id, Some(original.digest))
            .expect("deduplicate committed and reserved original");
        assert_eq!(deduplicated.project_overall_bytes, combined);
    }

    #[test]
    fn quota_reservation_dedup_conflicts_caps_and_audit_anchors_fail_closed() {
        // Break caught: duplicate jobs multiplying usage or hostile reservations escaping bounds.
        assert_eq!(
            bounded_nonterminal_job_count(
                i64::try_from(MAX_FOUNDATION_INSPECTION_ROWS).expect("cap fits")
            )
            .expect("exact cap"),
            MAX_FOUNDATION_INSPECTION_ROWS
        );
        assert!(matches!(
            bounded_nonterminal_job_count(
                i64::try_from(MAX_FOUNDATION_INSPECTION_ROWS + 1).expect("N+1 fits")
            ),
            Err(HeleosError::ResourceLimit)
        ));
        let digest = Sha256Digest::from_bytes([7; 32]);
        let mut reservations = BTreeMap::new();
        insert_reservation(
            &mut reservations,
            digest,
            ReservedObject {
                byte_length: 7,
                media: ReservedMedia::Pdf,
            },
        )
        .expect("first reservation");
        insert_reservation(
            &mut reservations,
            digest,
            ReservedObject {
                byte_length: 7,
                media: ReservedMedia::Pdf,
            },
        )
        .expect("equal reservation deduplicates");
        assert_eq!(reservations.len(), 1);
        assert!(matches!(
            insert_reservation(
                &mut reservations,
                digest,
                ReservedObject {
                    byte_length: 8,
                    media: ReservedMedia::Pdf,
                },
            ),
            Err(HeleosError::Integrity)
        ));
        assert!(matches!(
            insert_reservation(
                &mut reservations,
                digest,
                ReservedObject {
                    byte_length: 7,
                    media: ReservedMedia::EvidenceManifest,
                },
            ),
            Err(HeleosError::Integrity)
        ));

        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let project_id = ProjectId::from_uuid(uuid(60));
        insert_project(&store, project_id);
        let input = valid_input(project_id, "missing-anchor");
        let budget = IngestBudgetV1::new(input.byte_length);
        let original = StoredObjectV1 {
            digest: input.content_sha256.expect("digest"),
            byte_length: input.byte_length,
            vault_key: crate::Vault::object_key(input.content_sha256.expect("digest")),
        };
        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::VaultPublished { original },
        };
        store
            .connection
            .execute(
                "INSERT INTO job_runs
                    (id, project_id, kind, idempotency_key, state, attempt, lease_owner,
                     lease_expires_at_ms, deadline_at_ms, budget_json, input_json,
                     checkpoint_json, terminal_reason, created_at_ms, updated_at_ms)
                 VALUES (?1, ?2, 'pdf_ingest', ?3, 'queued', 0, NULL, NULL, 300000,
                         ?4, ?5, ?6, NULL, 0, 0)",
                params![
                    JobId::from_uuid(uuid(61)).as_uuid().to_string(),
                    project_id.as_uuid().to_string(),
                    input.idempotency_key.as_str(),
                    json_text(&budget, ONE_MIB).expect("budget JSON"),
                    json_text(&input, ONE_MIB).expect("input JSON"),
                    json_text(&checkpoint, EIGHT_MIB).expect("checkpoint JSON"),
                ],
            )
            .expect("insert unaudited nonterminal job");
        assert!(matches!(
            store.quota_snapshot(project_id, None),
            Err(HeleosError::Integrity)
        ));
    }

    #[test]
    fn vault_inventory_unions_content_and_every_nonterminal_checkpoint_phase() {
        // Break caught: backup/reconciliation omitting a resumable original or accepted manifest.
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let project_id = ProjectId::from_uuid(uuid(70));
        insert_project(&store, project_id);

        let committed = stored_object(70, 7);
        store
            .connection
            .execute(
                "INSERT INTO content_objects
                    (sha256, byte_length, media_type, admission_state, vault_key,
                     created_at_ms, created_by, quarantine_reason)
                 VALUES (?1, ?2, ?3, 'accepted', ?4, 0, 'fixture-actor', NULL)",
                params![
                    committed.digest.to_string(),
                    i64::try_from(committed.byte_length).expect("length"),
                    PDF_MEDIA_TYPE,
                    committed.vault_key,
                ],
            )
            .expect("insert committed inventory object");

        let preflight = stored_object(71, 7);
        let preflight_input = input_for_object(project_id, "inventory-preflight", &preflight);
        queue_inventory_job(
            &mut store,
            project_id,
            JobId::from_uuid(uuid(71)),
            preflight_input,
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::PreflightRejected {
                    content_sha256: Some(preflight.digest),
                    byte_length: preflight.byte_length,
                    quarantine: IntakeQuarantineV1 {
                        schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                        reason: IntakeQuarantineReasonV1::OriginalRetentionQuota,
                        probe_provenance: None,
                    },
                },
            },
            10,
            AuditEventId::from_uuid(uuid(72)),
        );

        let published = stored_object(72, 7);
        queue_inventory_job(
            &mut store,
            project_id,
            JobId::from_uuid(uuid(73)),
            input_for_object(project_id, "inventory-published", &published),
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished {
                    original: published.clone(),
                },
            },
            20,
            AuditEventId::from_uuid(uuid(74)),
        );

        let accepted = stored_object(73, 7);
        let accepted_manifest = accepted_manifest(accepted.digest);
        let accepted_manifest_bytes = accepted_manifest.canonical_bytes().expect("manifest bytes");
        let accepted_manifest_digest =
            Sha256Digest::hash_reader(accepted_manifest_bytes.as_slice()).expect("manifest digest");
        let accepted_manifest_object = StoredObjectV1 {
            digest: accepted_manifest_digest,
            byte_length: u64::try_from(accepted_manifest_bytes.len()).expect("manifest length"),
            vault_key: crate::Vault::object_key(accepted_manifest_digest),
        };
        let accepted_job_id = JobId::from_uuid(uuid(75));
        queue_inventory_job(
            &mut store,
            project_id,
            accepted_job_id,
            input_for_object(project_id, "inventory-accepted", &accepted),
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished {
                    original: accepted.clone(),
                },
            },
            30,
            AuditEventId::from_uuid(uuid(76)),
        );
        start_inventory_job(
            &mut store,
            project_id,
            accepted_job_id,
            31,
            uuid(77),
            AuditEventId::from_uuid(uuid(78)),
        );
        store
            .job_checkpoint_with_audit(JobCheckpointCommand {
                job_id: accepted_job_id,
                project_id,
                actor: ActorId::from_str("fixture-actor").expect("actor"),
                attempt: 1,
                checkpoint: IngestCheckpointV1 {
                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                    phase: IngestCheckpointPhaseV1::ProcessingComplete {
                        original: accepted.clone(),
                        candidate: crate::ingest::job::ProcessingCandidateV1::Accepted {
                            manifest: Box::new(accepted_manifest),
                            manifest_object: accepted_manifest_object.clone(),
                        },
                    },
                },
                now_ms: 32,
                audit_event_id: AuditEventId::from_uuid(uuid(79)),
            })
            .expect("checkpoint accepted inventory job");

        let quarantined = stored_object(74, 7);
        let quarantined_job_id = JobId::from_uuid(uuid(80));
        queue_inventory_job(
            &mut store,
            project_id,
            quarantined_job_id,
            input_for_object(project_id, "inventory-quarantined", &quarantined),
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished {
                    original: quarantined.clone(),
                },
            },
            40,
            AuditEventId::from_uuid(uuid(81)),
        );
        start_inventory_job(
            &mut store,
            project_id,
            quarantined_job_id,
            41,
            uuid(82),
            AuditEventId::from_uuid(uuid(83)),
        );
        store
            .job_checkpoint_with_audit(JobCheckpointCommand {
                job_id: quarantined_job_id,
                project_id,
                actor: ActorId::from_str("fixture-actor").expect("actor"),
                attempt: 1,
                checkpoint: IngestCheckpointV1 {
                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                    phase: IngestCheckpointPhaseV1::ProcessingComplete {
                        original: quarantined.clone(),
                        candidate: crate::ingest::job::ProcessingCandidateV1::Quarantined {
                            outcome: IngestOutcome::QuarantinedCorrupt,
                            quarantine: IntakeQuarantineV1 {
                                schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                                reason: IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::Corrupt),
                                probe_provenance: Some(provenance()),
                            },
                        },
                    },
                },
                now_ms: 42,
                audit_event_id: AuditEventId::from_uuid(uuid(84)),
            })
            .expect("checkpoint quarantined inventory job");
        store
            .with_immediate_transaction(|transaction| {
                let before = job_audit_snapshot(transaction, quarantined_job_id)?;
                let changed = transaction
                    .execute(
                        "UPDATE job_runs
                         SET state = 'interrupted', lease_owner = NULL,
                             lease_expires_at_ms = NULL, updated_at_ms = 43
                         WHERE id = ?1 AND state = 'running' AND attempt = 1",
                        [quarantined_job_id.as_uuid().to_string()],
                    )
                    .map_err(|_| HeleosError::Database)?;
                if changed != 1 {
                    return Err(HeleosError::Integrity);
                }
                append_action(
                    transaction,
                    AppendAction {
                        id: AuditEventId::from_uuid(uuid(85)),
                        project_id,
                        actor: ActorId::from_str("fixture-actor").expect("actor"),
                        action: AuditAction::JobInterrupted,
                        subject_type: AuditSubjectType::Job,
                        subject_id: quarantined_job_id.as_uuid().to_string(),
                        before,
                        after: job_audit_snapshot(transaction, quarantined_job_id)?,
                        reason: "inventory fixture interruption",
                        occurred_at_ms: 43,
                    },
                )?;
                Ok(())
            })
            .expect("persist interrupted inventory fixture");

        let expected = VaultInventory::try_from_entries([
            inventory_entry(&committed),
            inventory_entry(&published),
            inventory_entry(&accepted),
            inventory_entry(&accepted_manifest_object),
            inventory_entry(&quarantined),
        ])
        .expect("build expected inventory");
        assert_eq!(
            store.vault_inventory_rows().expect("read vault inventory"),
            expected
        );
    }

    #[test]
    fn vault_inventory_excludes_failed_and_cancelled_job_objects() {
        // Break caught: a terminal failed/cancelled checkpoint being mistaken for resumable data.
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let project_id = ProjectId::from_uuid(uuid(90));
        insert_project(&store, project_id);

        let failed = stored_object(90, 7);
        let failed_job_id = JobId::from_uuid(uuid(91));
        queue_inventory_job(
            &mut store,
            project_id,
            failed_job_id,
            input_for_object(project_id, "inventory-failed", &failed),
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished { original: failed },
            },
            10,
            AuditEventId::from_uuid(uuid(92)),
        );
        start_inventory_job(
            &mut store,
            project_id,
            failed_job_id,
            11,
            uuid(93),
            AuditEventId::from_uuid(uuid(94)),
        );
        store
            .job_fail_with_audit(JobFailCommand {
                job_id: failed_job_id,
                project_id,
                attempt: 1,
                actor: ActorId::from_str("fixture-actor").expect("actor"),
                now_ms: 12,
                audit_event_id: AuditEventId::from_uuid(uuid(95)),
            })
            .expect("fail inventory fixture job");

        let cancelled = stored_object(91, 7);
        let cancelled_job_id = JobId::from_uuid(uuid(96));
        queue_inventory_job(
            &mut store,
            project_id,
            cancelled_job_id,
            input_for_object(project_id, "inventory-cancelled", &cancelled),
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished {
                    original: cancelled,
                },
            },
            20,
            AuditEventId::from_uuid(uuid(97)),
        );
        store
            .connection
            .execute(
                "UPDATE job_runs
                 SET state = 'cancelled', terminal_reason = 'cancelled', updated_at_ms = 21
                 WHERE id = ?1",
                [cancelled_job_id.as_uuid().to_string()],
            )
            .expect("cancel inventory fixture job");

        assert_eq!(
            store.vault_inventory_rows().expect("read empty inventory"),
            VaultInventory::try_from_entries([]).expect("empty inventory")
        );
    }

    #[test]
    fn vault_inventory_rejects_queued_processing_accepted_with_a_matching_audit() {
        // Break caught: a forged queued accepted checkpoint binding a manifest before job start.
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let project_id = ProjectId::from_uuid(uuid(100));
        insert_project(&store, project_id);
        let original = stored_object(100, 7);
        let job_id = JobId::from_uuid(uuid(101));
        queue_inventory_job(
            &mut store,
            project_id,
            job_id,
            input_for_object(project_id, "queued-accepted", &original),
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished {
                    original: original.clone(),
                },
            },
            10,
            AuditEventId::from_uuid(uuid(102)),
        );
        let manifest = accepted_manifest(original.digest);
        let manifest_bytes = manifest.canonical_bytes().expect("manifest bytes");
        let manifest_digest =
            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest");
        replace_queued_checkpoint_and_audit(
            &mut store,
            job_id,
            &IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::ProcessingComplete {
                    original,
                    candidate: crate::ingest::job::ProcessingCandidateV1::Accepted {
                        manifest: Box::new(manifest),
                        manifest_object: StoredObjectV1 {
                            digest: manifest_digest,
                            byte_length: u64::try_from(manifest_bytes.len())
                                .expect("manifest length"),
                            vault_key: crate::Vault::object_key(manifest_digest),
                        },
                    },
                },
            },
        );

        assert!(matches!(
            store.vault_inventory_rows(),
            Err(HeleosError::Integrity)
        ));
    }

    #[test]
    fn vault_inventory_rejects_queued_processing_quarantine_with_a_matching_audit() {
        // Break caught: a forged queued quarantine checkpoint becoming resumable authority.
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let project_id = ProjectId::from_uuid(uuid(103));
        insert_project(&store, project_id);
        let original = stored_object(103, 7);
        let job_id = JobId::from_uuid(uuid(104));
        queue_inventory_job(
            &mut store,
            project_id,
            job_id,
            input_for_object(project_id, "queued-quarantine", &original),
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished {
                    original: original.clone(),
                },
            },
            10,
            AuditEventId::from_uuid(uuid(105)),
        );
        replace_queued_checkpoint_and_audit(
            &mut store,
            job_id,
            &IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::ProcessingComplete {
                    original,
                    candidate: crate::ingest::job::ProcessingCandidateV1::Quarantined {
                        outcome: IngestOutcome::QuarantinedCorrupt,
                        quarantine: IntakeQuarantineV1 {
                            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
                            reason: IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::Corrupt),
                            probe_provenance: Some(provenance()),
                        },
                    },
                },
            },
        );

        assert!(matches!(
            store.vault_inventory_rows(),
            Err(HeleosError::Integrity)
        ));
    }

    #[test]
    fn vault_inventory_rejects_noncanonical_job_json_stale_audit_and_object_conflicts() {
        // Break caught: trusting mutable checkpoint bytes or silently choosing one object length.
        fn queued_store() -> (Store, ProjectId, StoredObjectV1) {
            let mut store = Store::open_in_memory().expect("open store");
            store.migrate().expect("migrate store");
            let project_id = ProjectId::from_uuid(uuid(110));
            insert_project(&store, project_id);
            let object = stored_object(110, 7);
            queue_inventory_job(
                &mut store,
                project_id,
                JobId::from_uuid(uuid(111)),
                input_for_object(project_id, "inventory-authority", &object),
                IngestCheckpointV1 {
                    schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                    phase: IngestCheckpointPhaseV1::VaultPublished {
                        original: object.clone(),
                    },
                },
                10,
                AuditEventId::from_uuid(uuid(112)),
            );
            (store, project_id, object)
        }

        for column in ["input_json", "budget_json", "checkpoint_json"] {
            let (store, _, _) = queued_store();
            store
                .connection
                .execute_batch(
                    "DROP TRIGGER job_runs_frozen_identity;
                     DROP TRIGGER job_runs_legal_transition;
                     PRAGMA ignore_check_constraints = ON;",
                )
                .expect("open hostile job fixture");
            let current = store
                .connection
                .query_row(&format!("SELECT {column} FROM job_runs"), [], |row| {
                    row.get::<_, String>(0)
                })
                .expect("read canonical job JSON");
            store
                .connection
                .execute(
                    &format!("UPDATE job_runs SET {column} = ?1"),
                    [format!(" {current}")],
                )
                .expect("write noncanonical job JSON");
            assert!(matches!(
                store.vault_inventory_rows(),
                Err(HeleosError::Integrity)
            ));
        }

        let (store, _, _) = queued_store();
        store
            .connection
            .execute_batch("DROP TRIGGER audit_events_no_update;")
            .expect("open hostile audit fixture");
        store
            .connection
            .execute(
                "UPDATE audit_events SET after_json = '{}' WHERE subject_type = 'job'",
                [],
            )
            .expect("replace latest audit anchor");
        assert!(matches!(
            store.vault_inventory_rows(),
            Err(HeleosError::Integrity)
        ));

        let (mut store, project_id, object) = queued_store();
        assert_eq!(
            store.vault_inventory_rows().expect("deduplicate exact row"),
            VaultInventory::try_from_entries([inventory_entry(&object)])
                .expect("expected deduplicated inventory")
        );
        let conflicting = StoredObjectV1 {
            byte_length: object.byte_length + 1,
            ..object.clone()
        };
        queue_inventory_job(
            &mut store,
            project_id,
            JobId::from_uuid(uuid(113)),
            input_for_object(project_id, "inventory-conflict", &conflicting),
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished {
                    original: conflicting,
                },
            },
            20,
            AuditEventId::from_uuid(uuid(114)),
        );
        assert!(matches!(
            store.vault_inventory_rows(),
            Err(HeleosError::Integrity)
        ));

        let (store, _, object) = queued_store();
        store
            .connection
            .execute(
                "INSERT INTO content_objects
                    (sha256, byte_length, media_type, admission_state, vault_key,
                     created_at_ms, created_by, quarantine_reason)
                 VALUES (?1, ?2, 'application/pdf', 'accepted', 'wrong/key',
                         0, 'fixture-actor', NULL)",
                params![
                    object.digest.to_string(),
                    i64::try_from(object.byte_length).expect("object length"),
                ],
            )
            .expect("insert wrong-key content authority");
        assert!(matches!(
            store.vault_inventory_rows(),
            Err(HeleosError::Integrity)
        ));

        let (store, _, object) = queued_store();
        store
            .connection
            .execute(
                "INSERT INTO content_objects
                    (sha256, byte_length, media_type, admission_state, vault_key,
                     created_at_ms, created_by, quarantine_reason)
                 VALUES (?1, ?2,
                         'application/vnd.heleos.evidence-manifest+json;version=1',
                         'accepted', ?3, 0, 'fixture-actor', NULL)",
                params![
                    object.digest.to_string(),
                    i64::try_from(object.byte_length).expect("object length"),
                    object.vault_key,
                ],
            )
            .expect("insert conflicting-media content authority");
        assert!(matches!(
            store.vault_inventory_rows(),
            Err(HeleosError::Integrity)
        ));
    }

    #[test]
    fn vault_inventory_caps_count_before_rows_detects_drift_and_bounds_scalars() {
        // Break caught: allocating hostile rows before the 100,000 cap or trusting count drift.
        fn content_store(count: usize) -> Store {
            let mut store = Store::open_in_memory().expect("open store");
            store.migrate().expect("migrate store");
            store
                .connection
                .execute(
                    "WITH RECURSIVE sequence(value) AS (
                         VALUES(1) UNION ALL SELECT value + 1 FROM sequence WHERE value < ?1
                     ), digests(digest) AS (
                         SELECT printf('%064x', value) FROM sequence
                     )
                     INSERT INTO content_objects
                         (sha256, byte_length, media_type, admission_state, vault_key,
                          created_at_ms, created_by, quarantine_reason)
                     SELECT digest, 1, 'application/pdf', 'accepted',
                            'objects/sha256/' || substr(digest, 1, 2) || '/' ||
                            substr(digest, 3, 2) || '/' || digest,
                            0, 'fixture-actor', NULL
                     FROM digests",
                    [i64::try_from(count).expect("count fits")],
                )
                .expect("insert bounded inventory rows");
            store
        }

        let store = content_store(MAX_FOUNDATION_INSPECTION_ROWS);
        store
            .vault_inventory_rows()
            .expect("exact 100,000 inventory objects are permitted");
        let store = content_store(MAX_FOUNDATION_INSPECTION_ROWS + 1);
        assert!(matches!(
            store.vault_inventory_rows(),
            Err(HeleosError::ResourceLimit)
        ));

        let store = content_store(2);
        let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let function_calls = std::sync::Arc::clone(&calls);
        store
            .connection
            .create_scalar_function(
                "task6_inventory_drift_visible",
                1,
                rusqlite::functions::FunctionFlags::SQLITE_UTF8
                    | rusqlite::functions::FunctionFlags::SQLITE_INNOCUOUS,
                move |context| {
                    use std::sync::atomic::Ordering;

                    let digest = context.get::<String>(0)?;
                    let call = function_calls.fetch_add(1, Ordering::SeqCst);
                    Ok(call < 2 || digest.ends_with('1'))
                },
            )
            .expect("register inventory drift seam");
        store
            .connection
            .execute_batch(
                "ALTER TABLE content_objects RENAME TO content_base;
                 CREATE VIEW content_objects AS
                 SELECT sha256, byte_length, media_type, admission_state, vault_key,
                        created_at_ms, created_by, quarantine_reason
                 FROM content_base WHERE task6_inventory_drift_visible(sha256);",
            )
            .expect("install inventory drift view");
        assert!(matches!(
            store.vault_inventory_rows(),
            Err(HeleosError::Integrity)
        ));

        for (label, digest, key) in [
            (
                "digest",
                "1".repeat(65),
                "objects/sha256/11/11/".to_owned() + &"1".repeat(64),
            ),
            ("key", "1".repeat(64), "k".repeat(257)),
        ] {
            let mut store = Store::open_in_memory().expect("open scalar store");
            store.migrate().expect("migrate scalar store");
            store
                .connection
                .execute_batch("ALTER TABLE content_objects RENAME TO content_base;")
                .expect("rename scalar content table");
            store
                .connection
                .execute_batch(&format!(
                    "CREATE VIEW content_objects AS
                     SELECT '{digest}' AS sha256, 1 AS byte_length,
                            'application/pdf' AS media_type, 'accepted' AS admission_state,
                            '{key}' AS vault_key, 0 AS created_at_ms,
                            'fixture-actor' AS created_by, NULL AS quarantine_reason"
                ))
                .unwrap_or_else(|error| panic!("create {label} scalar view: {error}"));
            assert!(matches!(
                store.vault_inventory_rows(),
                Err(HeleosError::Integrity)
            ));
        }
    }

    #[test]
    fn latest_job_audit_lookup_uses_the_subject_sequence_index() {
        // Break caught: a bounded live-job query still window-sorting all terminal history.
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let details = store
            .connection
            .prepare(&format!("EXPLAIN QUERY PLAN {LATEST_JOB_AUDIT_SQL}"))
            .expect("prepare latest-audit query plan")
            .query_map([uuid(120).hyphenated().to_string()], |row| {
                row.get::<_, String>(3)
            })
            .expect("query latest-audit plan")
            .collect::<rusqlite::Result<Vec<_>>>()
            .expect("collect latest-audit plan");
        assert!(
            details.iter().any(|detail| {
                detail.contains("audit_events_subject_sequence")
                    && detail.contains("subject_type")
                    && detail.contains("subject_id")
            }),
            "latest audit plan must use the bounded subject index: {details:?}"
        );

        let project_id = ProjectId::from_uuid(uuid(121));
        insert_project(&store, project_id);
        store
            .with_immediate_transaction(|transaction| {
                for ordinal in 0_u128..4_096 {
                    append_action(
                        transaction,
                        AppendAction {
                            id: AuditEventId::from_uuid(uuid(10_000 + ordinal)),
                            project_id,
                            actor: ActorId::from_str("fixture-actor")?,
                            action: AuditAction::JobFailed,
                            subject_type: AuditSubjectType::Job,
                            subject_id: JobId::from_uuid(uuid(122)).as_uuid().to_string(),
                            before: JsonValue::Null,
                            after: JsonValue::Null,
                            reason: "terminal history fixture",
                            occurred_at_ms: i64::try_from(ordinal)
                                .map_err(|_| HeleosError::Integrity)?,
                        },
                    )?;
                }
                Ok(())
            })
            .expect("append bounded terminal audit history");
        let live = stored_object(123, 7);
        queue_inventory_job(
            &mut store,
            project_id,
            JobId::from_uuid(uuid(124)),
            input_for_object(project_id, "inventory-after-history", &live),
            IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::VaultPublished {
                    original: live.clone(),
                },
            },
            5_000,
            AuditEventId::from_uuid(uuid(20_000)),
        );
        assert_eq!(
            store
                .vault_inventory_rows()
                .expect("read inventory without scanning terminal history"),
            VaultInventory::try_from_entries([inventory_entry(&live)])
                .expect("expected live inventory")
        );
    }

    #[test]
    fn terminal_audit_snapshot_selects_exactly_one_lineage_for_the_job_project() {
        // Break caught: a same-project duplicate legitimately reuses the first job's lineage.
        let mut store = Store::open_in_memory().expect("open store");
        store.migrate().expect("migrate store");
        let project_id = ProjectId::from_uuid(uuid(10));
        insert_project(&store, project_id);
        let current_job = JobId::from_uuid(uuid(11));
        let first_job = JobId::from_uuid(uuid(12));
        let evidence_id = EvidenceId::from_uuid(uuid(13));
        let input = valid_input(project_id, "reused-lineage");
        let budget = IngestBudgetV1::new(input.byte_length);
        let checkpoint = IngestCheckpointV1 {
            schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
            phase: IngestCheckpointPhaseV1::Terminal {
                receipt: Box::new(accepted_receipt(
                    current_job,
                    vec![EvidenceManifestLineage {
                        project_id,
                        evidence_id,
                        originating_job_id: first_job,
                    }],
                )),
            },
        };
        insert_job(
            &store,
            project_id,
            current_job,
            "reused-lineage",
            &input,
            &budget,
            &checkpoint,
        );
        let snapshot = store
            .with_immediate_transaction(|transaction| job_audit_snapshot(transaction, current_job))
            .expect("build terminal snapshot");
        assert_eq!(
            snapshot["terminal_result_ids"]["evidence_id"],
            json!(evidence_id)
        );

        for (suffix, lineages) in [
            (
                "missing",
                vec![EvidenceManifestLineage {
                    project_id: ProjectId::from_uuid(uuid(14)),
                    evidence_id: EvidenceId::from_uuid(uuid(15)),
                    originating_job_id: first_job,
                }],
            ),
            (
                "ambiguous",
                vec![
                    EvidenceManifestLineage {
                        project_id,
                        evidence_id: EvidenceId::from_uuid(uuid(16)),
                        originating_job_id: first_job,
                    },
                    EvidenceManifestLineage {
                        project_id,
                        evidence_id: EvidenceId::from_uuid(uuid(17)),
                        originating_job_id: JobId::from_uuid(uuid(18)),
                    },
                ],
            ),
        ] {
            let job_id = JobId::from_uuid(uuid(if suffix == "missing" { 19 } else { 21 }));
            let key = format!("lineage-{suffix}");
            let input = valid_input(project_id, &key);
            let checkpoint = IngestCheckpointV1 {
                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
                phase: IngestCheckpointPhaseV1::Terminal {
                    receipt: Box::new(accepted_receipt(job_id, lineages)),
                },
            };
            insert_job(
                &store,
                project_id,
                job_id,
                &key,
                &input,
                &IngestBudgetV1::new(input.byte_length),
                &checkpoint,
            );
            assert!(matches!(
                store.with_immediate_transaction(|transaction| {
                    job_audit_snapshot(transaction, job_id)
                }),
                Err(HeleosError::Integrity)
            ));
        }
    }

    #[test]
    fn checked_sum_reports_integrity_for_authoritative_total_overflow() {
        // Break caught: SQLite's SUM overflow escaping as a generic database failure.
        let connection = Connection::open_in_memory().expect("open sum fixture");
        connection
            .execute_batch(
                "CREATE TABLE quota_values (value INTEGER NOT NULL);
                 INSERT INTO quota_values VALUES (9223372036854775807);
                 INSERT INTO quota_values VALUES (9223372036854775807);
                 INSERT INTO quota_values VALUES (9223372036854775807);",
            )
            .expect("insert overflowing values");
        assert!(matches!(
            checked_sum(
                &connection,
                "SELECT value FROM quota_values ORDER BY rowid",
                [],
            ),
            Err(HeleosError::Integrity)
        ));
    }

    #[test]
    fn audit_head_validates_borrowed_text_type_and_exact_digest_length() {
        // Break caught: SQLite allocating an unbounded owned event-hash string before validation.
        fn read_head(value: impl rusqlite::ToSql) -> Result<Sha256Digest> {
            let mut connection = Connection::open_in_memory().expect("open audit-head fixture");
            connection
                .execute_batch(
                    "CREATE TABLE audit_events (
                        id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        event_hash
                    );",
                )
                .expect("create audit-head table");
            connection
                .execute(
                    "INSERT INTO audit_events (id, sequence, event_hash) VALUES ('event', 1, ?1)",
                    [&value],
                )
                .expect("insert audit head");
            let transaction = connection.transaction().expect("begin audit-head read");
            audit_head(&transaction)
        }

        let exact = "1".repeat(64);
        assert_eq!(
            read_head(exact.clone()).expect("read exact text digest"),
            Sha256Digest::from_str(&exact).expect("parse fixture digest")
        );
        assert!(matches!(
            read_head("1".repeat(65)),
            Err(HeleosError::Integrity)
        ));
        assert!(matches!(
            read_head(vec![b'1'; 64]),
            Err(HeleosError::Integrity)
        ));
    }
}
