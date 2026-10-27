use std::io::{self, Write};

use serde::{Deserialize, Deserializer, Serialize, de};

use crate::pdf::geometry::validate_page_metadata;
use crate::{
    DocumentId, EvidenceId, HeleosError, JobId, PageMetadata, PdfLimits, PdfProbeProvenance,
    ProjectId, Result, RevisionId, Sha256Digest, canonical_document_ids,
};

use super::audit::JCS_SAFE_INTEGER_MAX;

pub const EVIDENCE_MANIFEST_SCHEMA_V1: &str = "heleos.evidence-manifest/v1";
pub const PDF_MEDIA_TYPE: &str = "application/pdf";
pub const EVIDENCE_MANIFEST_MEDIA_TYPE: &str =
    "application/vnd.heleos.evidence-manifest+json;version=1";
pub const MAX_EVIDENCE_MANIFEST_BYTES: usize = 16 * 1024 * 1024;
pub(crate) const MAX_EVIDENCE_LINEAGES: usize = 100_000;
pub(crate) const EVIDENCE_PARAMETERS_SCHEMA_V1: &str = "heleos.evidence-parameters/v1";

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub(crate) struct EvidenceParametersV1 {
    pub schema: String,
    pub original_media_type: String,
    pub manifest_media_type: String,
    pub probe_provenance: PdfProbeProvenance,
    pub requested_limits: EvidencePdfLimitsV1,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawEvidenceParametersV1 {
    schema: String,
    original_media_type: String,
    manifest_media_type: String,
    probe_provenance: PdfProbeProvenance,
    requested_limits: EvidencePdfLimitsV1,
}

impl<'de> Deserialize<'de> for EvidenceParametersV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let raw = RawEvidenceParametersV1::deserialize(deserializer)?;
        let value = Self {
            schema: raw.schema,
            original_media_type: raw.original_media_type,
            manifest_media_type: raw.manifest_media_type,
            probe_provenance: raw.probe_provenance,
            requested_limits: raw.requested_limits,
        };
        value.validate().map_err(de::Error::custom)?;
        Ok(value)
    }
}

impl EvidenceParametersV1 {
    pub(crate) fn new(
        probe_provenance: PdfProbeProvenance,
        requested_limits: EvidencePdfLimitsV1,
    ) -> Result<Self> {
        let value = Self {
            schema: EVIDENCE_PARAMETERS_SCHEMA_V1.to_owned(),
            original_media_type: PDF_MEDIA_TYPE.to_owned(),
            manifest_media_type: EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
            probe_provenance,
            requested_limits,
        };
        value.validate()?;
        Ok(value)
    }

    pub(crate) fn validate(&self) -> Result<()> {
        validate_provenance(&self.probe_provenance)?;
        if self.schema != EVIDENCE_PARAMETERS_SCHEMA_V1
            || self.original_media_type != PDF_MEDIA_TYPE
            || self.manifest_media_type != EVIDENCE_MANIFEST_MEDIA_TYPE
            || self.requested_limits != EvidencePdfLimitsV1::from(PdfLimits::default())
        {
            return Err(HeleosError::Integrity);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct EvidenceContentV1 {
    pub sha256: Sha256Digest,
    pub byte_length: u64,
    pub media_type: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawEvidenceContentV1 {
    sha256: Sha256Digest,
    byte_length: u64,
    media_type: String,
}

impl TryFrom<RawEvidenceContentV1> for EvidenceContentV1 {
    type Error = HeleosError;

    fn try_from(raw: RawEvidenceContentV1) -> Result<Self> {
        if raw.byte_length > JCS_SAFE_INTEGER_MAX
            || !matches!(
                raw.media_type.as_str(),
                PDF_MEDIA_TYPE | EVIDENCE_MANIFEST_MEDIA_TYPE
            )
        {
            return Err(HeleosError::Integrity);
        }
        Ok(Self {
            sha256: raw.sha256,
            byte_length: raw.byte_length,
            media_type: raw.media_type,
        })
    }
}

impl<'de> Deserialize<'de> for EvidenceContentV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawEvidenceContentV1::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct EvidencePdfLimitsV1 {
    pub max_input_bytes: u64,
    pub max_pages: u32,
    pub max_indirect_objects: u32,
    pub max_nested_references: u32,
    pub max_metadata_bytes: u64,
    pub max_page_axis_points: u32,
    pub max_guest_memory_bytes: u64,
    pub max_instances: u32,
    pub max_tables: u32,
    pub max_fuel: u64,
    pub timeout_seconds: u64,
    pub max_protocol_output_bytes: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawEvidencePdfLimitsV1 {
    max_input_bytes: u64,
    max_pages: u32,
    max_indirect_objects: u32,
    max_nested_references: u32,
    max_metadata_bytes: u64,
    max_page_axis_points: u32,
    max_guest_memory_bytes: u64,
    max_instances: u32,
    max_tables: u32,
    max_fuel: u64,
    timeout_seconds: u64,
    max_protocol_output_bytes: u64,
}

impl TryFrom<RawEvidencePdfLimitsV1> for EvidencePdfLimitsV1 {
    type Error = HeleosError;

    fn try_from(raw: RawEvidencePdfLimitsV1) -> Result<Self> {
        let value = Self {
            max_input_bytes: raw.max_input_bytes,
            max_pages: raw.max_pages,
            max_indirect_objects: raw.max_indirect_objects,
            max_nested_references: raw.max_nested_references,
            max_metadata_bytes: raw.max_metadata_bytes,
            max_page_axis_points: raw.max_page_axis_points,
            max_guest_memory_bytes: raw.max_guest_memory_bytes,
            max_instances: raw.max_instances,
            max_tables: raw.max_tables,
            max_fuel: raw.max_fuel,
            timeout_seconds: raw.timeout_seconds,
            max_protocol_output_bytes: raw.max_protocol_output_bytes,
        };
        if value != Self::from(PdfLimits::default()) {
            return Err(HeleosError::Integrity);
        }
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for EvidencePdfLimitsV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawEvidencePdfLimitsV1::deserialize(deserializer)?)
            .map_err(de::Error::custom)
    }
}

impl From<PdfLimits> for EvidencePdfLimitsV1 {
    fn from(value: PdfLimits) -> Self {
        Self {
            max_input_bytes: value.max_input_bytes,
            max_pages: value.max_pages,
            max_indirect_objects: value.max_indirect_objects,
            max_nested_references: value.max_nested_references,
            max_metadata_bytes: value.max_metadata_bytes,
            max_page_axis_points: value.max_page_axis_points,
            max_guest_memory_bytes: value.max_guest_memory_bytes,
            max_instances: value.max_instances,
            max_tables: value.max_tables,
            max_fuel: value.max_fuel,
            timeout_seconds: value.timeout_seconds,
            max_protocol_output_bytes: value.max_protocol_output_bytes,
        }
    }
}

impl From<EvidencePdfLimitsV1> for PdfLimits {
    fn from(value: EvidencePdfLimitsV1) -> Self {
        Self {
            max_input_bytes: value.max_input_bytes,
            max_pages: value.max_pages,
            max_indirect_objects: value.max_indirect_objects,
            max_nested_references: value.max_nested_references,
            max_metadata_bytes: value.max_metadata_bytes,
            max_page_axis_points: value.max_page_axis_points,
            max_guest_memory_bytes: value.max_guest_memory_bytes,
            max_instances: value.max_instances,
            max_tables: value.max_tables,
            max_fuel: value.max_fuel,
            timeout_seconds: value.timeout_seconds,
            max_protocol_output_bytes: value.max_protocol_output_bytes,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct EvidenceManifestV1 {
    pub schema: String,
    pub original: EvidenceContentV1,
    pub document_id: DocumentId,
    pub revision_id: RevisionId,
    pub pages: Vec<PageMetadata>,
    pub probe_provenance: PdfProbeProvenance,
    pub requested_limits: EvidencePdfLimitsV1,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawEvidenceManifestV1 {
    schema: String,
    original: EvidenceContentV1,
    document_id: DocumentId,
    revision_id: RevisionId,
    #[serde(deserialize_with = "deserialize_pages")]
    pages: Vec<PageMetadata>,
    probe_provenance: PdfProbeProvenance,
    requested_limits: EvidencePdfLimitsV1,
}

impl TryFrom<RawEvidenceManifestV1> for EvidenceManifestV1 {
    type Error = HeleosError;

    fn try_from(raw: RawEvidenceManifestV1) -> Result<Self> {
        let value = Self {
            schema: raw.schema,
            original: raw.original,
            document_id: raw.document_id,
            revision_id: raw.revision_id,
            pages: raw.pages,
            probe_provenance: raw.probe_provenance,
            requested_limits: raw.requested_limits,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for EvidenceManifestV1 {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawEvidenceManifestV1::deserialize(deserializer)?).map_err(de::Error::custom)
    }
}

impl EvidenceManifestV1 {
    pub(crate) fn new(
        original_sha256: Sha256Digest,
        original_byte_length: u64,
        pages: Vec<PageMetadata>,
        probe_provenance: PdfProbeProvenance,
    ) -> Result<Self> {
        let (document_id, revision_id) = canonical_document_ids(original_sha256);
        let value = Self {
            schema: EVIDENCE_MANIFEST_SCHEMA_V1.to_owned(),
            original: EvidenceContentV1 {
                sha256: original_sha256,
                byte_length: original_byte_length,
                media_type: PDF_MEDIA_TYPE.to_owned(),
            },
            document_id,
            revision_id,
            pages,
            probe_provenance,
            requested_limits: PdfLimits::default().into(),
        };
        value.validate()?;
        Ok(value)
    }

    pub(crate) fn validate(&self) -> Result<()> {
        if self.schema != EVIDENCE_MANIFEST_SCHEMA_V1
            || self.original.media_type != PDF_MEDIA_TYPE
            || self.original.byte_length > JCS_SAFE_INTEGER_MAX
            || self.requested_limits != EvidencePdfLimitsV1::from(PdfLimits::default())
            || self.original.byte_length > self.requested_limits.max_input_bytes
            || self.pages.is_empty()
        {
            return Err(HeleosError::Integrity);
        }
        let (document_id, revision_id) = canonical_document_ids(self.original.sha256);
        if self.document_id != document_id || self.revision_id != revision_id {
            return Err(HeleosError::Integrity);
        }
        validate_provenance(&self.probe_provenance)?;
        validate_page_metadata(&self.pages, self.original.sha256, PdfLimits::default())
    }

    pub(crate) fn canonical_bytes(&self) -> Result<Vec<u8>> {
        self.validate()?;
        bounded_canonical_json(self, MAX_EVIDENCE_MANIFEST_BYTES)
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct EvidenceManifestLineage {
    pub project_id: ProjectId,
    pub evidence_id: EvidenceId,
    pub originating_job_id: JobId,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct EvidenceManifestReceipt {
    pub manifest: EvidenceManifestV1,
    pub manifest_content_sha256: Sha256Digest,
    pub manifest_byte_length: u64,
    pub manifest_media_type: String,
    pub manifest_vault_key: String,
    pub original_vault_key: String,
    pub lineages: Vec<EvidenceManifestLineage>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawEvidenceManifestReceipt {
    manifest: EvidenceManifestV1,
    manifest_content_sha256: Sha256Digest,
    manifest_byte_length: u64,
    manifest_media_type: String,
    manifest_vault_key: String,
    original_vault_key: String,
    #[serde(deserialize_with = "deserialize_lineages")]
    lineages: Vec<EvidenceManifestLineage>,
}

fn deserialize_pages<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Vec<PageMetadata>, D::Error> {
    super::deserialize_bounded_vec::<D, PageMetadata, 10_000>(deserializer)
}

fn deserialize_lineages<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Vec<EvidenceManifestLineage>, D::Error> {
    super::deserialize_bounded_vec::<D, EvidenceManifestLineage, MAX_EVIDENCE_LINEAGES>(
        deserializer,
    )
}

impl TryFrom<RawEvidenceManifestReceipt> for EvidenceManifestReceipt {
    type Error = HeleosError;

    fn try_from(raw: RawEvidenceManifestReceipt) -> Result<Self> {
        let value = Self {
            manifest: raw.manifest,
            manifest_content_sha256: raw.manifest_content_sha256,
            manifest_byte_length: raw.manifest_byte_length,
            manifest_media_type: raw.manifest_media_type,
            manifest_vault_key: raw.manifest_vault_key,
            original_vault_key: raw.original_vault_key,
            lineages: raw.lineages,
        };
        value.validate()?;
        Ok(value)
    }
}

impl<'de> Deserialize<'de> for EvidenceManifestReceipt {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        Self::try_from(RawEvidenceManifestReceipt::deserialize(deserializer)?)
            .map_err(de::Error::custom)
    }
}

impl EvidenceManifestReceipt {
    pub(crate) fn validate(&self) -> Result<()> {
        self.manifest.validate()?;
        let bytes = self.manifest.canonical_bytes()?;
        let byte_length = u64::try_from(bytes.len()).map_err(|_| HeleosError::Integrity)?;
        let digest = Sha256Digest::hash_reader(bytes.as_slice())?;
        if self.manifest_content_sha256 != digest
            || self.manifest_byte_length != byte_length
            || self.manifest_byte_length > JCS_SAFE_INTEGER_MAX
            || self.manifest_media_type != EVIDENCE_MANIFEST_MEDIA_TYPE
            || self.manifest_vault_key != crate::Vault::object_key(digest)
            || self.original_vault_key != crate::Vault::object_key(self.manifest.original.sha256)
            || self.lineages.is_empty()
            || self.lineages.len() > MAX_EVIDENCE_LINEAGES
        {
            return Err(HeleosError::Integrity);
        }
        let mut previous = None;
        for lineage in &self.lineages {
            let key = (
                *lineage.project_id.as_uuid().as_bytes(),
                *lineage.evidence_id.as_uuid().as_bytes(),
                *lineage.originating_job_id.as_uuid().as_bytes(),
            );
            if previous.as_ref().is_some_and(|previous| previous >= &key) {
                return Err(HeleosError::Integrity);
            }
            previous = Some(key);
        }
        Ok(())
    }
}

pub(crate) fn validate_provenance(value: &PdfProbeProvenance) -> Result<()> {
    value.validate()
}

struct BoundedWriter {
    bytes: Vec<u8>,
    maximum: usize,
}

impl Write for BoundedWriter {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        let next = self
            .bytes
            .len()
            .checked_add(buffer.len())
            .ok_or_else(|| io::Error::other("canonical JSON exceeds bound"))?;
        if next > self.maximum {
            return Err(io::Error::other("canonical JSON exceeds bound"));
        }
        self.bytes.extend_from_slice(buffer);
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

pub(crate) fn bounded_canonical_json<T: Serialize>(value: &T, maximum: usize) -> Result<Vec<u8>> {
    let mut writer = BoundedWriter {
        bytes: Vec::new(),
        maximum,
    };
    serde_jcs::to_writer(&mut writer, value).map_err(|_| HeleosError::ResourceLimit)?;
    if writer.bytes.is_empty() {
        return Err(HeleosError::Integrity);
    }
    Ok(writer.bytes)
}

#[cfg(test)]
mod tests {
    use uuid::Uuid;

    use super::*;
    use crate::{PageTransform, PageUnit, page_id};

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

    fn pages(content: Sha256Digest, count: u32) -> Vec<PageMetadata> {
        (0..count)
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
            .collect()
    }

    fn one_page_manifest() -> EvidenceManifestV1 {
        let content = Sha256Digest::from_bytes([1; 32]);
        EvidenceManifestV1::new(content, 7, pages(content, 1), provenance())
            .expect("one-page manifest")
    }

    #[test]
    fn manifest_deserialization_stops_at_the_10_000_page_cap() {
        // Break caught: hostile public JSON allocating every page before validation.
        let content = Sha256Digest::from_bytes([1; 32]);
        let manifest = EvidenceManifestV1::new(content, 7, pages(content, 10_000), provenance())
            .expect("manifest at exact page cap");
        let at_cap = serde_json::to_value(manifest).expect("manifest JSON");
        serde_json::from_value::<EvidenceManifestV1>(at_cap.clone())
            .expect("exact page cap round-trips");
        let mut over_cap = at_cap;
        let page = over_cap["pages"][9_999].clone();
        over_cap["pages"]
            .as_array_mut()
            .expect("pages array")
            .push(page);
        let error = serde_json::from_value::<EvidenceManifestV1>(over_cap)
            .expect_err("10,001st page must fail during sequence decoding");
        assert!(error.to_string().contains("maximum of 10000"));
    }

    #[test]
    fn public_evidence_limits_reject_nondefault_profiles() {
        // Break caught: a nested/public limit DTO bypassing Task 6's fixed default profile.
        let mut value = serde_json::to_value(EvidencePdfLimitsV1::from(PdfLimits::default()))
            .expect("limits JSON");
        value["max_pages"] = serde_json::json!(9_999);
        assert!(serde_json::from_value::<EvidencePdfLimitsV1>(value).is_err());
    }

    #[test]
    fn manifest_original_length_is_bounded_by_its_requested_input_limit() {
        // Break caught: an evidence manifest claiming an accepted over-limit original.
        let content = Sha256Digest::from_bytes([1; 32]);
        let maximum = PdfLimits::default().max_input_bytes;
        let at_maximum = EvidenceManifestV1::new(content, maximum, pages(content, 1), provenance())
            .expect("manifest at exact input cap");
        serde_json::from_value::<EvidenceManifestV1>(
            serde_json::to_value(&at_maximum).expect("manifest JSON"),
        )
        .expect("exact input cap round-trips");

        assert!(matches!(
            EvidenceManifestV1::new(content, maximum + 1, pages(content, 1), provenance(),),
            Err(HeleosError::Integrity)
        ));
        let mut over_limit = serde_json::to_value(at_maximum).expect("manifest JSON");
        over_limit["original"]["byte_length"] = serde_json::json!(maximum + 1);
        assert!(serde_json::from_value::<EvidenceManifestV1>(over_limit).is_err());
    }

    #[test]
    fn receipt_deserialization_stops_at_the_100_000_lineage_cap() {
        // Break caught: hostile reader/public JSON allocating unbounded evidence lineages.
        let manifest = one_page_manifest();
        let manifest_bytes = manifest.canonical_bytes().expect("canonical manifest");
        let manifest_digest =
            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest");
        let project_id = ProjectId::from_uuid(Uuid::from_u128(1));
        let job_id = JobId::from_uuid(Uuid::from_u128(2));
        let lineages = (1..=MAX_EVIDENCE_LINEAGES)
            .map(|index| EvidenceManifestLineage {
                project_id,
                evidence_id: EvidenceId::from_uuid(Uuid::from_u128(
                    u128::try_from(index).expect("lineage ordinal fits") + 10,
                )),
                originating_job_id: job_id,
            })
            .collect();
        let receipt = EvidenceManifestReceipt {
            original_vault_key: crate::Vault::object_key(manifest.original.sha256),
            manifest,
            manifest_content_sha256: manifest_digest,
            manifest_byte_length: u64::try_from(manifest_bytes.len())
                .expect("manifest length fits"),
            manifest_media_type: EVIDENCE_MANIFEST_MEDIA_TYPE.to_owned(),
            manifest_vault_key: crate::Vault::object_key(manifest_digest),
            lineages,
        };
        receipt.validate().expect("receipt at exact lineage cap");
        let at_cap = serde_json::to_value(receipt).expect("receipt JSON");
        serde_json::from_value::<EvidenceManifestReceipt>(at_cap.clone())
            .expect("exact lineage cap round-trips");
        let mut over_cap = at_cap;
        let lineage = over_cap["lineages"][MAX_EVIDENCE_LINEAGES - 1].clone();
        over_cap["lineages"]
            .as_array_mut()
            .expect("lineages array")
            .push(lineage);
        let error = serde_json::from_value::<EvidenceManifestReceipt>(over_cap)
            .expect_err("100,001st lineage must fail during sequence decoding");
        assert!(error.to_string().contains("maximum of 100000"));
    }
}
