pub(crate) mod audit;
pub(crate) mod evidence;
mod fault;
pub(crate) mod inspection;
pub(crate) mod job;

use std::{fmt, io::Read, marker::PhantomData, str::FromStr};

use serde::{Deserialize, Deserializer, Serialize, Serializer, de};

use crate::store::ingest_repository::ProjectCreateCommand;
use crate::{ActorId, Clock, DataClass, HeleosError, IdGenerator, ProjectId, Result, Store, Vault};

pub use audit::{
    AUDIT_CHAIN_REPORT_SCHEMA_V1, AuditAction, AuditChainFinding, AuditChainReport, AuditEvent,
    AuditEventId, AuditInvalidField, AuditSubjectType,
};
pub use evidence::{
    EVIDENCE_MANIFEST_MEDIA_TYPE, EVIDENCE_MANIFEST_SCHEMA_V1, EvidenceContentV1,
    EvidenceManifestLineage, EvidenceManifestReceipt, EvidenceManifestV1, EvidencePdfLimitsV1,
    MAX_EVIDENCE_MANIFEST_BYTES, PDF_MEDIA_TYPE,
};
pub use fault::{FaultInjector, FaultPoint};
pub use inspection::{
    FOUNDATION_INSPECTION_SCHEMA_V1, FoundationAdmissionState, FoundationContentObject,
    FoundationCounts, FoundationEvidenceContent, FoundationEvidenceLineage, FoundationInspection,
    FoundationIntakeEvent, FoundationJob, FoundationJobTerminalReason, FoundationSheet,
};
pub use job::{
    INTAKE_QUARANTINE_SCHEMA_V1, IngestEngine, IngestReceipt, IngestRequest,
    IntakeQuarantineReasonV1, IntakeQuarantineV1, IntakeSource, ResumeReceipt,
};

pub(crate) fn deserialize_bounded_vec<'de, D, T, const MAXIMUM: usize>(
    deserializer: D,
) -> std::result::Result<Vec<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    struct BoundedVecVisitor<T, const MAXIMUM: usize>(PhantomData<T>);

    impl<'de, T, const MAXIMUM: usize> de::Visitor<'de> for BoundedVecVisitor<T, MAXIMUM>
    where
        T: Deserialize<'de>,
    {
        type Value = Vec<T>;

        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            write!(formatter, "a sequence with at most {MAXIMUM} elements")
        }

        fn visit_seq<A>(self, mut sequence: A) -> std::result::Result<Self::Value, A::Error>
        where
            A: de::SeqAccess<'de>,
        {
            if sequence.size_hint().is_some_and(|hint| hint > MAXIMUM) {
                return Err(de::Error::custom(format_args!(
                    "sequence exceeds maximum of {MAXIMUM}"
                )));
            }
            let capacity = sequence.size_hint().unwrap_or(0).min(MAXIMUM);
            let mut values = Vec::with_capacity(capacity);
            while values.len() < MAXIMUM {
                match sequence.next_element()? {
                    Some(value) => values.push(value),
                    None => return Ok(values),
                }
            }
            if sequence.next_element::<de::IgnoredAny>()?.is_some() {
                return Err(de::Error::custom(format_args!(
                    "sequence exceeds maximum of {MAXIMUM}"
                )));
            }
            Ok(values)
        }
    }

    deserializer.deserialize_seq(BoundedVecVisitor::<T, MAXIMUM>(PhantomData))
}

pub(crate) fn deserialize_bounded_string<'de, D, const MAXIMUM: usize>(
    deserializer: D,
) -> std::result::Result<String, D::Error>
where
    D: Deserializer<'de>,
{
    struct BoundedStringVisitor<const MAXIMUM: usize>;

    impl<'de, const MAXIMUM: usize> de::Visitor<'de> for BoundedStringVisitor<MAXIMUM> {
        type Value = String;

        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            write!(formatter, "a UTF-8 string of at most {MAXIMUM} bytes")
        }

        fn visit_borrowed_str<E>(self, value: &'de str) -> std::result::Result<Self::Value, E>
        where
            E: de::Error,
        {
            self.visit_str(value)
        }

        fn visit_str<E>(self, value: &str) -> std::result::Result<Self::Value, E>
        where
            E: de::Error,
        {
            if value.len() > MAXIMUM {
                return Err(E::custom(format_args!(
                    "string exceeds maximum of {MAXIMUM} bytes"
                )));
            }
            Ok(value.to_owned())
        }

        fn visit_string<E>(self, value: String) -> std::result::Result<Self::Value, E>
        where
            E: de::Error,
        {
            if value.len() > MAXIMUM {
                return Err(E::custom(format_args!(
                    "string exceeds maximum of {MAXIMUM} bytes"
                )));
            }
            Ok(value)
        }
    }

    deserializer.deserialize_string(BoundedStringVisitor::<MAXIMUM>)
}

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct IdempotencyKey(String);

impl IdempotencyKey {
    pub const MAX_UTF8_BYTES: usize = 128;

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl TryFrom<&str> for IdempotencyKey {
    type Error = HeleosError;

    fn try_from(value: &str) -> Result<Self> {
        if value.is_empty()
            || value.len() > Self::MAX_UTF8_BYTES
            || value.chars().any(char::is_control)
        {
            return Err(HeleosError::InvalidId);
        }
        Ok(Self(value.to_owned()))
    }
}

impl TryFrom<String> for IdempotencyKey {
    type Error = HeleosError;

    fn try_from(value: String) -> Result<Self> {
        Self::try_from(value.as_str())
    }
}

impl FromStr for IdempotencyKey {
    type Err = HeleosError;

    fn from_str(value: &str) -> Result<Self> {
        Self::try_from(value)
    }
}

impl Serialize for IdempotencyKey {
    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.0)
    }
}

impl<'de> Deserialize<'de> for IdempotencyKey {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let value = deserialize_bounded_string::<D, { Self::MAX_UTF8_BYTES }>(deserializer)?;
        Self::try_from(value).map_err(de::Error::custom)
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ProjectCreateRequest {
    pub project_id: Option<ProjectId>,
    pub name: String,
    pub actor: ActorId,
    pub data_class: DataClass,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawProjectCreateRequest {
    project_id: Option<ProjectId>,
    #[serde(deserialize_with = "deserialize_project_name")]
    name: String,
    actor: ActorId,
    data_class: DataClass,
}

fn deserialize_project_name<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<String, D::Error> {
    deserialize_bounded_string::<D, 256>(deserializer)
}

impl<'de> Deserialize<'de> for ProjectCreateRequest {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let raw = RawProjectCreateRequest::deserialize(deserializer)?;
        if !project_name_is_valid(&raw.name) {
            return Err(de::Error::custom("invalid project name"));
        }
        Ok(Self {
            project_id: raw.project_id,
            name: raw.name,
            actor: raw.actor,
            data_class: raw.data_class,
        })
    }
}

pub(crate) fn project_name_is_valid(value: &str) -> bool {
    !value.is_empty() && value.len() <= 256 && !value.chars().any(char::is_control)
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ProjectReceipt {
    pub project_id: ProjectId,
    pub created_at_ms: i64,
    pub audit_event_id: AuditEventId,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawProjectReceipt {
    project_id: ProjectId,
    created_at_ms: i64,
    audit_event_id: AuditEventId,
}

impl TryFrom<RawProjectReceipt> for ProjectReceipt {
    type Error = HeleosError;

    fn try_from(raw: RawProjectReceipt) -> Result<Self> {
        if raw.created_at_ms < 0 || (raw.created_at_ms as u64) > audit::JCS_SAFE_INTEGER_MAX {
            return Err(HeleosError::Integrity);
        }
        Ok(Self {
            project_id: raw.project_id,
            created_at_ms: raw.created_at_ms,
            audit_event_id: raw.audit_event_id,
        })
    }
}

impl<'de> Deserialize<'de> for ProjectReceipt {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let raw = RawProjectReceipt::deserialize(deserializer)?;
        Self::try_from(raw).map_err(de::Error::custom)
    }
}

pub struct ProjectService<'a> {
    store: &'a mut Store,
    clock: &'a dyn Clock,
    ids: &'a dyn IdGenerator,
}

impl<'a> ProjectService<'a> {
    pub fn new(
        store: &'a mut Store,
        clock: &'a dyn Clock,
        ids: &'a dyn IdGenerator,
    ) -> Result<Self> {
        store.require_writer_capability()?;
        Ok(Self { store, clock, ids })
    }

    pub fn create(&mut self, request: ProjectCreateRequest) -> Result<ProjectReceipt> {
        if !project_name_is_valid(&request.name) {
            return Err(HeleosError::InvalidId);
        }
        let created_at_ms = self.clock.now_unix_ms();
        if created_at_ms < 0 || (created_at_ms as u64) > audit::JCS_SAFE_INTEGER_MAX {
            return Err(HeleosError::Integrity);
        }
        let project_id = request
            .project_id
            .unwrap_or_else(|| ProjectId::from_uuid(self.ids.next_uuid()));
        let audit_event_id = AuditEventId::from_uuid(self.ids.next_uuid());
        self.store.project_create_with_audit(ProjectCreateCommand {
            project_id,
            name: request.name,
            actor: request.actor,
            data_class: request.data_class,
            created_at_ms,
            audit_event_id,
        })
    }
}

pub struct FoundationReader<'a> {
    store: &'a Store,
    vault: &'a Vault,
}

impl<'a> FoundationReader<'a> {
    pub const fn new(store: &'a Store, vault: &'a Vault) -> Self {
        Self { store, vault }
    }

    pub fn verify_audit_chain(&self) -> Result<AuditChainReport> {
        let mut verifier = audit::AuditVerifier::new();
        self.store.audit_chain_rows(|row| verifier.push(row))?;
        verifier.finish()
    }

    pub fn evidence_manifest_for_revision(
        &self,
        revision: crate::RevisionId,
    ) -> Result<EvidenceManifestReceipt> {
        let rows = self.store.revision_evidence_rows(revision)?;
        let anchor = rows.evidence.first().ok_or(HeleosError::Integrity)?;
        for row in &rows.evidence {
            if row.document_id != anchor.document_id
                || row.revision_id != anchor.revision_id
                || row.original != anchor.original
                || row.manifest != anchor.manifest
                || row.extraction_method != anchor.extraction_method
                || row.parameters != anchor.parameters
                || row.review_state != anchor.review_state
            {
                return Err(HeleosError::Integrity);
            }
        }
        if anchor.revision_id != revision
            || anchor.extraction_method != "heleos.pdf-probe/v1"
            || anchor.review_state != "accepted"
        {
            return Err(HeleosError::Integrity);
        }

        let original = self
            .vault
            .open_verified(anchor.original.digest)
            .map_err(committed_vault_error)?;
        if original.digest() != anchor.original.digest
            || original.byte_length() != anchor.original.byte_length
            || original.vault_key() != anchor.original.vault_key
        {
            return Err(HeleosError::Integrity);
        }

        let manifest_object = self
            .vault
            .open_verified(anchor.manifest.digest)
            .map_err(committed_vault_error)?;
        if manifest_object.digest() != anchor.manifest.digest
            || manifest_object.byte_length() != anchor.manifest.byte_length
            || manifest_object.vault_key() != anchor.manifest.vault_key
            || manifest_object.byte_length()
                > u64::try_from(MAX_EVIDENCE_MANIFEST_BYTES).map_err(|_| HeleosError::Integrity)?
        {
            return Err(HeleosError::Integrity);
        }
        let read_limit = u64::try_from(MAX_EVIDENCE_MANIFEST_BYTES)
            .map_err(|_| HeleosError::Integrity)?
            .checked_add(1)
            .ok_or(HeleosError::Integrity)?;
        let mut manifest_bytes = Vec::with_capacity(
            usize::try_from(manifest_object.byte_length()).map_err(|_| HeleosError::Integrity)?,
        );
        manifest_object
            .take(read_limit)
            .read_to_end(&mut manifest_bytes)
            .map_err(HeleosError::Io)?;
        if u64::try_from(manifest_bytes.len()).map_err(|_| HeleosError::Integrity)?
            != anchor.manifest.byte_length
        {
            return Err(HeleosError::Integrity);
        }
        let manifest = serde_json::from_slice::<EvidenceManifestV1>(&manifest_bytes)
            .map_err(|_| HeleosError::Integrity)?;
        if manifest.canonical_bytes()? != manifest_bytes
            || manifest.document_id != anchor.document_id
            || manifest.revision_id != anchor.revision_id
            || manifest.original.sha256 != anchor.original.digest
            || manifest.original.byte_length != anchor.original.byte_length
            || manifest.pages != rows.pages
            || manifest.probe_provenance != anchor.parameters.probe_provenance
            || manifest.requested_limits != anchor.parameters.requested_limits
        {
            return Err(HeleosError::Integrity);
        }

        let receipt = EvidenceManifestReceipt {
            manifest,
            manifest_content_sha256: anchor.manifest.digest,
            manifest_byte_length: anchor.manifest.byte_length,
            manifest_media_type: EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
            manifest_vault_key: anchor.manifest.vault_key.clone(),
            original_vault_key: anchor.original.vault_key.clone(),
            lineages: rows.evidence.into_iter().map(|row| row.lineage).collect(),
        };
        receipt.validate()?;
        Ok(receipt)
    }

    pub fn inspect_foundation(&self, project: ProjectId) -> Result<FoundationInspection> {
        self.store.foundation_inspection_rows(project)
    }
}

fn committed_vault_error(error: HeleosError) -> HeleosError {
    if matches!(error, HeleosError::NotFound | HeleosError::ResourceLimit) {
        HeleosError::Integrity
    } else {
        error
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn public_scalar_deserialization_enforces_utf8_byte_caps_before_validation() {
        // Break caught: public Task 6 strings allocating beyond their documented field caps.
        assert!(
            serde_json::from_value::<IdempotencyKey>(serde_json::json!("a".repeat(128))).is_ok()
        );
        let error = serde_json::from_value::<IdempotencyKey>(serde_json::json!("a".repeat(129)))
            .expect_err("129-byte key must fail in the bounded string visitor");
        assert!(
            error
                .to_string()
                .contains("string exceeds maximum of 128 bytes")
        );

        let request = |name: String| {
            serde_json::json!({
                "project_id": null,
                "name": name,
                "actor": "fixture-actor",
                "data_class": "INTERNAL",
            })
        };
        assert!(serde_json::from_value::<ProjectCreateRequest>(request("é".repeat(128))).is_ok());
        let error = serde_json::from_value::<ProjectCreateRequest>(request(format!(
            "{}a",
            "é".repeat(128)
        )))
        .expect_err("257-byte project name must fail in the bounded string visitor");
        assert!(
            error
                .to_string()
                .contains("string exceeds maximum of 256 bytes")
        );
    }
}
