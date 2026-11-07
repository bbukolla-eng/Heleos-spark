//! Provider-neutral validation of bounded worker task and handoff documents.

//!
//! Validation checks packet claims, not whether a provider ran a check, whether
//! a commit exists, or whether filesystem paths contain symlinks. A controller
//! must independently establish those facts before accepting worker output.
#![forbid(unsafe_code)]

use serde::de::{MapAccess, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use thiserror::Error;

pub const TASK_SCHEMA: &str = "heleos.worker-task/v1";
pub const HANDOFF_SCHEMA: &str = "heleos.worker-handoff/v1";
pub const VALIDATION_SCHEMA: &str = "heleos.worker-validation/v1";
pub const MAX_DOCUMENT_BYTES: usize = 1_048_576;
pub const MAX_ITEMS: usize = 256;
pub const MAX_TEXT_BYTES: usize = 4096;
pub const MAX_PATH_BYTES: usize = 1024;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Provider {
    Codex,
    ClaudeCode,
    Kimi,
    Grok,
    Cursor,
    NotebookLm,
    GrokBots,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Implementation,
    Research,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DataClass {
    Public,
    Internal,
    ProjectConfidential,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EgressPolicy {
    LocalOnly,
    ApprovedExternal,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Limits {
    pub max_actions: u32,
    pub max_duration_seconds: u32,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Task {
    pub schema: String,
    pub task_id: String,
    pub provider: Provider,
    pub mode: Mode,
    pub base_commit: String,
    pub objective: String,
    pub allowed_paths: Vec<String>,
    pub forbidden_paths: Vec<String>,
    pub input_data_class: DataClass,
    pub egress_policy: EgressPolicy,
    #[serde(deserialize_with = "unique_instruction_map")]
    pub instruction_sha256: BTreeMap<String, String>,
    pub acceptance_commands: Vec<String>,
    pub limits: Limits,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TerminalState {
    Completed,
    Blocked,
    Failed,
    Cancelled,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CheckRecord {
    pub command: String,
    pub exit_code: i32,
    pub output_sha256: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Handoff {
    pub schema: String,
    pub task_digest: String,
    pub provider: Provider,
    pub terminal_state: TerminalState,
    pub changed_paths: Vec<String>,
    pub checks: Vec<CheckRecord>,
    pub candidate_commit: Option<String>,
    pub unresolved_items: Vec<String>,
}

/// An immutable validated document and the identity of its canonical JSON.
/// Only validation functions can construct this type.
#[derive(Debug)]
pub struct Validated<T> {
    document: T,
    canonical_json: Vec<u8>,
    digest: String,
}

impl<T> Validated<T> {
    pub fn document(&self) -> &T {
        &self.document
    }

    /// UTF-8 JCS bytes without a trailing newline. An absent optional candidate
    /// commit is represented as JSON null in the canonical handoff.
    pub fn canonical_json(&self) -> &[u8] {
        &self.canonical_json
    }

    pub fn digest(&self) -> &str {
        &self.digest
    }
}

/// Errors contain fixed field labels, never untrusted JSON, commands, or paths.
#[derive(Debug, Error, Eq, PartialEq)]
pub enum ValidationError {
    #[error("document exceeds the byte limit")]
    DocumentTooLarge,
    #[error("invalid strict JSON document")]
    InvalidJson,
    #[error("unsupported document schema")]
    InvalidSchema,
    #[error("invalid {0}")]
    InvalidField(&'static str),
    #[error("provider does not support the assigned mode")]
    ProviderMode,
    #[error("data class does not permit the assigned egress policy")]
    EgressPolicy,
    #[error("handoff does not match its task identity or provider")]
    TaskMismatch,
    #[error("changed path is outside the task write scope")]
    OutOfScope,
    #[error("check records do not match the task acceptance commands")]
    InvalidChecks,
    #[error("terminal state is inconsistent with checks or unresolved items")]
    InvalidTerminalState,
    #[error("canonical serialization failed")]
    Canonicalization,
}

/// Parse strict JSON, validate all assignment constraints, and hash JCS bytes.
pub fn validate_task_json(input: &[u8]) -> Result<Validated<Task>, ValidationError> {
    validate_task(input, None)
}

/// Controller-only exception for an owner-authorized INTERNAL Claude submission.
/// The approval is the SHA-256 of the exact input bytes, supplied separately from
/// the untrusted packet. It is not a credential or proof of owner authorization;
/// the controller must establish and record that authority before calling.
/// Default validation and PROJECT_CONFIDENTIAL/SECRET restrictions are unchanged.
pub fn validate_task_json_with_internal_claude_approval(
    input: &[u8],
    approved_task_sha256: &str,
) -> Result<Validated<Task>, ValidationError> {
    validate_task(input, Some(approved_task_sha256))
}

fn validate_task(
    input: &[u8],
    approved_task_sha256: Option<&str>,
) -> Result<Validated<Task>, ValidationError> {
    let task: Task = parse(input)?;
    if task.schema != TASK_SCHEMA {
        return Err(ValidationError::InvalidSchema);
    }
    if task.task_id.is_empty()
        || task.task_id.len() > 128
        || !task.task_id.as_bytes()[0].is_ascii_alphanumeric()
        || !task
            .task_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-_".contains(&byte))
    {
        return Err(ValidationError::InvalidField("task_id"));
    }
    hex_identity(&task.base_commit, 40, "base_commit")?;
    text_field(&task.objective, true, "objective")?;
    paths(&task.allowed_paths, true, "allowed_paths")?;
    paths(&task.forbidden_paths, false, "forbidden_paths")?;
    if matches!(task.provider, Provider::NotebookLm | Provider::GrokBots)
        && task.mode != Mode::Research
    {
        return Err(ValidationError::ProviderMode);
    }
    if let Some(approval) = approved_task_sha256 {
        hex_identity(approval, 64, "approved_internal_task_sha256")?;
        if task.provider != Provider::ClaudeCode
            || task.input_data_class != DataClass::Internal
            || task.egress_policy != EgressPolicy::ApprovedExternal
            || Sha256::digest(input)
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect::<String>()
                != approval
        {
            return Err(ValidationError::EgressPolicy);
        }
    } else if task.input_data_class != DataClass::Public
        && task.egress_policy != EgressPolicy::LocalOnly
    {
        return Err(ValidationError::EgressPolicy);
    }
    count(task.instruction_sha256.len(), true, "instruction_sha256")?;
    for (path, digest) in &task.instruction_sha256 {
        relative_path(path, "instruction_sha256 path")?;
        hex_identity(digest, 64, "instruction_sha256 digest")?;
    }
    count(task.acceptance_commands.len(), true, "acceptance_commands")?;
    let mut commands = BTreeSet::new();
    for command in &task.acceptance_commands {
        text_field(command, false, "acceptance command")?;
        if !commands.insert(command) {
            return Err(ValidationError::InvalidField(
                "duplicate acceptance command",
            ));
        }
    }
    if task.limits.max_actions == 0 || task.limits.max_duration_seconds == 0 {
        return Err(ValidationError::InvalidField("limits"));
    }
    canonical(task)
}

/// Validate a handoff against the immutable, previously validated assignment.
/// Path scope is lexical and case-sensitive; no filesystem objects are opened.
pub fn validate_handoff_json(
    input: &[u8],
    task: &Validated<Task>,
) -> Result<Validated<Handoff>, ValidationError> {
    let handoff: Handoff = parse(input)?;
    if handoff.schema != HANDOFF_SCHEMA {
        return Err(ValidationError::InvalidSchema);
    }
    hex_identity(&handoff.task_digest, 64, "task_digest")?;
    let assignment = task.document();
    if handoff.task_digest != task.digest() || handoff.provider != assignment.provider {
        return Err(ValidationError::TaskMismatch);
    }
    paths(&handoff.changed_paths, false, "changed_paths")?;
    for path in &handoff.changed_paths {
        if !assignment
            .allowed_paths
            .iter()
            .any(|prefix| contains_path(prefix, path))
            || assignment
                .forbidden_paths
                .iter()
                .any(|prefix| contains_path(prefix, path))
        {
            return Err(ValidationError::OutOfScope);
        }
    }
    if let Some(commit) = &handoff.candidate_commit {
        hex_identity(commit, 40, "candidate_commit")?;
    }
    count(handoff.unresolved_items.len(), false, "unresolved_items")?;
    for item in &handoff.unresolved_items {
        text_field(item, true, "unresolved item")?;
    }
    count(handoff.checks.len(), false, "checks")?;
    let mut next_index = 0;
    for check in &handoff.checks {
        text_field(&check.command, false, "check command")?;
        hex_identity(&check.output_sha256, 64, "check output_sha256")?;
        // Records form a unique subsequence of the task's declared check order.
        let offset = assignment.acceptance_commands[next_index..]
            .iter()
            .position(|command| *command == check.command)
            .ok_or(ValidationError::InvalidChecks)?;
        next_index += offset + 1;
    }
    if handoff.terminal_state == TerminalState::Completed {
        if handoff.checks.len() != assignment.acceptance_commands.len()
            || handoff.checks.iter().any(|check| check.exit_code != 0)
            || !handoff.unresolved_items.is_empty()
        {
            return Err(ValidationError::InvalidTerminalState);
        }
    } else if handoff.unresolved_items.is_empty() {
        return Err(ValidationError::InvalidTerminalState);
    }
    canonical(handoff)
}

fn parse<T: serde::de::DeserializeOwned>(input: &[u8]) -> Result<T, ValidationError> {
    if input.len() > MAX_DOCUMENT_BYTES {
        return Err(ValidationError::DocumentTooLarge);
    }
    serde_json::from_slice(input).map_err(|_| ValidationError::InvalidJson)
}

fn canonical<T: Serialize>(document: T) -> Result<Validated<T>, ValidationError> {
    let canonical_json =
        serde_jcs::to_vec(&document).map_err(|_| ValidationError::Canonicalization)?;
    let digest = Sha256::digest(&canonical_json)
        .iter()
        .flat_map(|byte| {
            let digits = b"0123456789abcdef";
            [
                digits[usize::from(byte >> 4)] as char,
                digits[usize::from(byte & 15)] as char,
            ]
        })
        .collect();
    Ok(Validated {
        document,
        canonical_json,
        digest,
    })
}

fn hex_identity(value: &str, length: usize, field: &'static str) -> Result<(), ValidationError> {
    if value.len() != length
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(ValidationError::InvalidField(field));
    }
    Ok(())
}

fn count(length: usize, nonempty: bool, field: &'static str) -> Result<(), ValidationError> {
    if length > MAX_ITEMS || nonempty && length == 0 {
        return Err(ValidationError::InvalidField(field));
    }
    Ok(())
}

fn text_field(value: &str, prose: bool, field: &'static str) -> Result<(), ValidationError> {
    if value.trim().is_empty()
        || value.len() > MAX_TEXT_BYTES
        || value
            .chars()
            .any(|ch| ch.is_control() && !(prose && matches!(ch, '\n' | '\t')))
    {
        return Err(ValidationError::InvalidField(field));
    }
    Ok(())
}

fn paths(values: &[String], nonempty: bool, field: &'static str) -> Result<(), ValidationError> {
    count(values.len(), nonempty, field)?;
    if values.windows(2).any(|pair| pair[0] >= pair[1]) {
        return Err(ValidationError::InvalidField(field));
    }
    for value in values {
        relative_path(value, field)?;
    }
    Ok(())
}

fn relative_path(value: &str, field: &'static str) -> Result<(), ValidationError> {
    if value.is_empty()
        || value.len() > MAX_PATH_BYTES
        || value
            .chars()
            .any(|ch| ch.is_control() || "\\:*?\"<>|".contains(ch))
    {
        return Err(ValidationError::InvalidField(field));
    }
    for component in value.split('/') {
        let basename = component
            .split('.')
            .next()
            .unwrap_or("")
            .to_ascii_uppercase();
        let reserved = matches!(basename.as_str(), "CON" | "PRN" | "AUX" | "NUL")
            || basename
                .strip_prefix("COM")
                .or_else(|| basename.strip_prefix("LPT"))
                .is_some_and(|suffix| {
                    matches!(
                        suffix,
                        "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
                    )
                });
        if component.is_empty()
            || matches!(component, "." | "..")
            || component.trim() != component
            || component.ends_with('.')
            || reserved
        {
            return Err(ValidationError::InvalidField(field));
        }
    }
    Ok(())
}

fn contains_path(prefix: &str, path: &str) -> bool {
    path == prefix
        || path
            .strip_prefix(prefix)
            .is_some_and(|suffix| suffix.starts_with('/'))
}

fn unique_instruction_map<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> Result<BTreeMap<String, String>, D::Error> {
    struct UniqueMap;
    impl<'de> Visitor<'de> for UniqueMap {
        type Value = BTreeMap<String, String>;
        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str("a bounded instruction digest object with unique keys")
        }
        fn visit_map<A: MapAccess<'de>>(self, mut access: A) -> Result<Self::Value, A::Error> {
            let mut map = BTreeMap::new();
            while let Some((key, value)) = access.next_entry::<String, String>()? {
                if map.len() == MAX_ITEMS || map.insert(key, value).is_some() {
                    return Err(serde::de::Error::custom(
                        "duplicate or excessive instruction keys",
                    ));
                }
            }
            Ok(map)
        }
    }
    deserializer.deserialize_map(UniqueMap)
}
