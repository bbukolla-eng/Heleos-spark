use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    Queued,
    Running,
    Interrupted,
    Succeeded,
    Failed,
    Cancelled,
}

pub const fn can_transition(from: JobState, to: JobState) -> bool {
    matches!(
        (from, to),
        (JobState::Queued, JobState::Running | JobState::Cancelled)
            | (
                JobState::Running,
                JobState::Interrupted
                    | JobState::Succeeded
                    | JobState::Failed
                    | JobState::Cancelled
            )
            | (
                JobState::Interrupted,
                JobState::Running | JobState::Cancelled
            )
    )
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum IngestOutcome {
    AcceptedNew,
    AcceptedDuplicate,
    IdempotentReplay,
    QuarantinedCorrupt,
    QuarantinedEncrypted,
    QuarantinedUnsupported,
    QuarantinedSuspicious,
    QuarantinedLimit,
    Interrupted,
    DeniedConflict,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DataClass {
    Public,
    Internal,
    ProjectConfidential,
    Secret,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PdfLimits {
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

impl Default for PdfLimits {
    fn default() -> Self {
        Self {
            max_input_bytes: 256 * 1024 * 1024,
            max_pages: 10_000,
            max_indirect_objects: 250_000,
            max_nested_references: 64,
            max_metadata_bytes: 16 * 1024 * 1024,
            max_page_axis_points: 14_400,
            max_guest_memory_bytes: 768 * 1024 * 1024,
            max_instances: 1,
            max_tables: 4,
            max_fuel: 5_000_000_000,
            timeout_seconds: 120,
            max_protocol_output_bytes: 4 * 1024 * 1024,
        }
    }
}
