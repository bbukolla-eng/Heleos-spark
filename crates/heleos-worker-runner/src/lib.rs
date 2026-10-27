//! Local, guarded proposal execution for configured Codex, Claude Code, Grok, and Cursor commands.
//!
//! Explicit macOS or Windows containment restricts provider host-path writes.
//! Reads, network, credentials, and provider authority need separate authorization.
//! Only one provider invocation is counted; internal provider actions are not attested.
//! Successful proposals remain `blocked` pending controller acceptance checks.
#![forbid(unsafe_code)]

#[cfg(any(unix, windows))]
mod containment;
#[cfg(any(unix, windows))]
mod filesystem;
#[cfg(unix)]
mod process;
#[cfg(windows)]
#[path = "process_windows.rs"]
mod process;

use heleos_worker_protocol::{Handoff, Provider, Task, Validated};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

pub const RUN_SCHEMA: &str = "heleos.worker-run/v1";
pub const FAILURE_SCHEMA: &str = "heleos.worker-run-failure/v1";
pub const HARD_BYTE_LIMIT: usize = 1_048_576;

/// Explicit host-path write restriction. No mode restricts reads or network.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize, clap::ValueEnum)]
#[serde(rename_all = "snake_case")]
#[value(rename_all = "snake_case")]
pub enum ContainmentMode {
    #[default]
    None,
    MacosSeatbelt,
    WindowsRestrictedTokenJob,
}

#[derive(Debug)]
pub struct ProviderCommand {
    pub provider: Provider,
    /// Absolute path to a regular local executable.
    pub executable: PathBuf,
    /// Literal arguments. Neither task data nor these arguments become shell code.
    pub args: Vec<OsString>,
    /// Explicit environment additions. No ambient credentials are inherited.
    pub environment: BTreeMap<OsString, OsString>,
}
impl ProviderCommand {
    pub fn new(provider: Provider, executable: PathBuf) -> Self {
        Self {
            provider,
            executable,
            args: Vec::new(),
            environment: BTreeMap::new(),
        }
    }
}

#[derive(Debug)]
pub struct RunnerConfig {
    pub source_repository: PathBuf,
    /// Existing directory; it must not contain, equal, or be inside the source.
    pub workspace_root: PathBuf,
    pub git_executable: PathBuf,
    pub provider: ProviderCommand,
    pub max_prompt_bytes: usize,
    /// Per stream; excess output is drained without retention.
    pub max_output_bytes: usize,
    /// Opt-in cleanup of this run's identity-checked directory after failure.
    pub cleanup_on_failure: bool,
    pub containment: ContainmentMode,
    /// Controller-supplied approval of exact raw INTERNAL Claude task bytes.
    /// Never derive this from untrusted task content or use without owner authority.
    pub approved_internal_task_sha256: Option<String>,
}
impl RunnerConfig {
    pub fn new(
        source_repository: PathBuf,
        workspace_root: PathBuf,
        git_executable: PathBuf,
        provider: ProviderCommand,
    ) -> Self {
        Self {
            source_repository,
            workspace_root,
            git_executable,
            provider,
            max_prompt_bytes: 65_536,
            max_output_bytes: 65_536,
            cleanup_on_failure: false,
            containment: ContainmentMode::None,
            approved_internal_task_sha256: None,
        }
    }
}

#[derive(Debug, Default)]
pub struct Capture {
    pub bytes: Vec<u8>,
    pub truncated: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, thiserror::Error)]
#[serde(rename_all = "snake_case")]
pub enum FailureCode {
    #[error("invalid bounded task document")]
    InvalidTask,
    #[error("invalid local runner configuration")]
    InvalidConfiguration,
    #[error("worker changed a path outside its permitted scope")]
    OutOfScope,
    #[error("task duration limit exceeded")]
    Timeout,
    #[error("provider exited unsuccessfully")]
    ProviderExit,
    #[error("bounded prompt cannot contain required task and instructions")]
    PromptTooLarge,
    #[error("exact-base instruction bytes do not match the task")]
    InstructionMismatch,
    #[error("exact task base is unavailable locally")]
    BaseUnavailable,
    #[error("configured provider does not match the task")]
    ProviderMismatch,
    #[error("runner supports implementation tasks only")]
    UnsupportedMode,
    #[error("provider has no admitted implementation adapter")]
    UnsupportedProvider,
    #[error("runner has no backend for this platform")]
    UnsupportedPlatform,
    #[error("requested host-write containment backend is unavailable")]
    ContainmentUnavailable,
    #[error("repository metadata changed outside permitted Git operations")]
    RepositoryMutation,
    #[error("filesystem path or object cannot be safely inventoried")]
    UnsafePath,
    #[error("local process or filesystem operation failed")]
    Io,
    #[error("local Git operation failed")]
    Git,
    #[error("bounded inventory could not be completed")]
    InventoryLimit,
    #[error("generated handoff failed protocol validation")]
    InvalidHandoff,
}

#[derive(Debug, thiserror::Error)]
#[error("{code}")]
pub struct RunError {
    pub code: FailureCode,
    pub exit_code: Option<i32>,
    pub stdout: Box<Capture>,
    pub stderr: Box<Capture>,
    pub checkout_path: Option<PathBuf>,
    pub run_directory: Option<PathBuf>,
    pub cleanup_failed: bool,
    pub evidence_write_failed: bool,
}
impl RunError {
    pub fn new(code: FailureCode) -> Self {
        Self {
            code,
            exit_code: None,
            stdout: Box::default(),
            stderr: Box::default(),
            checkout_path: None,
            run_directory: None,
            cleanup_failed: false,
            evidence_write_failed: false,
        }
    }
    pub fn summary(&self) -> serde_json::Value {
        serde_json::json!({
            "schema": FAILURE_SCHEMA, "code": self.code, "message": self.code.to_string(),
            "exit_code": self.exit_code, "checkout_path": self.checkout_path, "run_directory": self.run_directory,
            "cleanup_failed": self.cleanup_failed,
            "evidence_write_failed": self.evidence_write_failed,
            "stdout": {"retained_bytes": self.stdout.bytes.len(), "truncated": self.stdout.truncated},
            "stderr": {"retained_bytes": self.stderr.bytes.len(), "truncated": self.stderr.truncated},
            "next_action": "Controller inspects retained evidence before assigning any further work."
        })
    }
}

#[derive(Debug, Serialize)]
pub struct ChangedFile {
    pub path: String,
    /// None denotes deletion; otherwise SHA-256 identifies final regular-file bytes.
    pub sha256: Option<String>,
    pub executable: Option<bool>,
}

#[derive(Debug)]
pub struct RunResult {
    pub handoff: Validated<Handoff>,
    pub stdout: Capture,
    pub stderr: Capture,
    pub changed_files: Vec<ChangedFile>,
    pub run_directory: PathBuf,
    pub elapsed_milliseconds: u128,
    checkout: PathBuf,
    containment: ContainmentMode,
    approved_internal_task_sha256: Option<String>,
}
impl RunResult {
    pub fn checkout_path(&self) -> &Path {
        &self.checkout
    }
    /// Bounded structured evidence; raw logs live in separate files.
    pub fn summary(&self) -> serde_json::Value {
        serde_json::json!({
            "schema": RUN_SCHEMA, "checkout_path": self.checkout, "run_directory": self.run_directory,
            "handoff": self.handoff.document(), "changed_files": self.changed_files,
            "provider_exit_code": 0, "provider_invocations": 1,
            "internal_provider_actions_attested": false, "elapsed_milliseconds": self.elapsed_milliseconds,
            "approved_internal_task_sha256": self.approved_internal_task_sha256,
            "containment": {
                "mode": self.containment,
                "host_path_writes_restricted": self.containment != ContainmentMode::None,
                "process_tree_contained": self.containment == ContainmentMode::WindowsRestrictedTokenJob,
                "write_scope": if self.containment != ContainmentMode::None {
                    vec!["checkout", "home", "tmp"]
                } else { Vec::<&str>::new() },
                "reads_restricted": false, "network_restricted": false,
                "inherited_external_authority_restricted": false
            },
            "stdout": {"retained_bytes": self.stdout.bytes.len(), "truncated": self.stdout.truncated},
            "stderr": {"retained_bytes": self.stderr.bytes.len(), "truncated": self.stderr.truncated},
            "next_action": "Controller independently executes the declared acceptance checks before integration."
        })
    }
}

/// Validate and preserve the original JSON representation before local execution.
pub fn run_json(input: &[u8], config: &RunnerConfig) -> Result<RunResult, RunError> {
    let task = validate_for_run(input, config)?;
    run_inner(&task, input, config)
}
/// Execute an immutable validated assignment; retain its canonical representation.
/// An internal approval here must bind those canonical bytes, not another encoding.
pub fn run(task: &Validated<Task>, config: &RunnerConfig) -> Result<RunResult, RunError> {
    // A prevalidated task cannot carry authorization across controller contexts.
    validate_for_run(task.canonical_json(), config)?;
    run_inner(task, task.canonical_json(), config)
}

fn validate_for_run(input: &[u8], config: &RunnerConfig) -> Result<Validated<Task>, RunError> {
    if let Some(approval) = &config.approved_internal_task_sha256 {
        if config.containment == ContainmentMode::None {
            return Err(RunError::new(FailureCode::InvalidConfiguration));
        }
        heleos_worker_protocol::validate_task_json_with_internal_claude_approval(input, approval)
    } else {
        heleos_worker_protocol::validate_task_json(input)
    }
    .map_err(|_| RunError::new(FailureCode::InvalidTask))
}

#[cfg(not(any(unix, windows)))]
fn run_inner(_: &Validated<Task>, _: &[u8], config: &RunnerConfig) -> Result<RunResult, RunError> {
    Err(RunError::new(
        if config.containment == ContainmentMode::None {
            FailureCode::UnsupportedPlatform
        } else {
            FailureCode::ContainmentUnavailable
        },
    ))
}

#[cfg(any(unix, windows))]
fn run_inner(
    task: &Validated<Task>,
    original: &[u8],
    config: &RunnerConfig,
) -> Result<RunResult, RunError> {
    use heleos_worker_protocol::{HANDOFF_SCHEMA, Mode, TerminalState, validate_handoff_json};
    use std::collections::BTreeSet;
    #[cfg(unix)]
    use std::fs;
    use std::time::{Duration, Instant};
    let started = Instant::now();
    let deadline =
        started + Duration::from_secs(u64::from(task.document().limits.max_duration_seconds));
    let assignment = task.document();
    if assignment.mode != Mode::Implementation {
        return Err(RunError::new(FailureCode::UnsupportedMode));
    }
    if !matches!(
        assignment.provider,
        Provider::Codex | Provider::ClaudeCode | Provider::Grok | Provider::Cursor
    ) {
        return Err(RunError::new(FailureCode::UnsupportedProvider));
    }
    if assignment.provider != config.provider.provider {
        return Err(RunError::new(FailureCode::ProviderMismatch));
    }
    #[cfg(windows)]
    containment::validate(config.containment)?;
    let (source, workspace_root) = filesystem::validate_config(config)?;
    let owned = filesystem::OwnedWorkspace::create(&workspace_root)?;
    let run_directory = owned.path().to_path_buf();
    let checkout = run_directory.join("checkout");
    let ownership = owned.ownership_record()?;
    let work = || -> Result<RunResult, RunError> {
        owned.write("ownership.json", &ownership)?;
        owned.write("task.original.json", original)?;
        owned.write("task.canonical.json", task.canonical_json())?;
        owned.write("task.sha256", task.digest().as_bytes())?;
        if let Some(approval) = &config.approved_internal_task_sha256 {
            owned.write("approved-internal-task.sha256", approval.as_bytes())?;
        }
        containment::validate(config.containment)?;
        #[cfg(windows)]
        let writable_roots = containment::prepare_windows_roots(&run_directory)?;
        // Standalone local objects: no shared worktree registration or hardlinks.
        let mut clone = git_command(config, &workspace_root)?;
        clone
            .args([
                "clone",
                "--local",
                "--no-hardlinks",
                "--no-checkout",
                "--no-tags",
                "--",
            ])
            .arg(&source)
            .arg(&checkout);
        git_output(clone, deadline, FailureCode::Git)?;
        let mut remote = git_command(config, &checkout)?;
        remote.args(["remote", "remove", "origin"]);
        git_output(remote, deadline, FailureCode::Git)?;
        for path in ["objects/info/alternates", "shallow"] {
            if checkout.join(".git").join(path).exists() {
                return Err(RunError::new(FailureCode::RepositoryMutation));
            }
        }
        let mut resolve = git_command(config, &checkout)?;
        resolve.args([
            "rev-parse",
            "--verify",
            "--end-of-options",
            &format!("{}^{{commit}}", assignment.base_commit),
        ]);
        let resolved = git_output(resolve, deadline, FailureCode::BaseUnavailable)?;
        if resolved != format!("{}\n", assignment.base_commit).as_bytes() {
            return Err(RunError::new(FailureCode::BaseUnavailable));
        }
        let mut detach = git_command(config, &checkout)?;
        detach.args([
            "checkout",
            "--detach",
            "--force",
            &assignment.base_commit,
            "--",
        ]);
        git_output(detach, deadline, FailureCode::BaseUnavailable)?;
        let before = filesystem::snapshot(&checkout, true, deadline)?;
        let git_before = filesystem::snapshot(&checkout.join(".git"), false, deadline)?;
        let prompt = filesystem::prompt(task, &checkout, config.max_prompt_bytes, deadline)?;
        owned.write("prompt.txt", &prompt)?;
        #[cfg(unix)]
        let output = {
            fs::create_dir(run_directory.join("home"))
                .map_err(|_| RunError::new(FailureCode::Io))?;
            fs::create_dir(run_directory.join("tmp"))
                .map_err(|_| RunError::new(FailureCode::Io))?;
            let mut command = containment::provider_command(config, &checkout, &run_directory)?;
            command
                .args(&config.provider.args)
                .current_dir(&checkout)
                .env_clear()
                .env("PATH", "/usr/bin:/bin")
                .env("HOME", run_directory.join("home"))
                .env("TMPDIR", run_directory.join("tmp"))
                .envs(&config.provider.environment);
            process::execute(command, &prompt, config.max_output_bytes, deadline)?
        };
        #[cfg(windows)]
        let output = containment::execute_windows_provider(
            config,
            &checkout,
            &run_directory,
            writable_roots,
            &prompt,
            deadline,
        )?;
        if !output.status.success() {
            let mut failure = RunError::new(FailureCode::ProviderExit);
            failure.exit_code = output.status.code();
            failure.stdout = Box::new(output.stdout);
            failure.stderr = Box::new(output.stderr);
            return Err(failure);
        }
        let inventory = || -> Result<_, RunError> {
            owned.validate()?;
            owned.verify("ownership.json", &ownership)?;
            owned.verify("task.original.json", original)?;
            owned.verify("task.canonical.json", task.canonical_json())?;
            owned.verify("task.sha256", task.digest().as_bytes())?;
            owned.verify("prompt.txt", &prompt)?;
            let git_after = filesystem::snapshot(&checkout.join(".git"), false, deadline)?;
            filesystem::validate_git_metadata(&git_before, &git_after)?;
            let after = filesystem::snapshot(&checkout, true, deadline)?;
            let mut paths = BTreeSet::new();
            let mut diff = git_command(config, &checkout)?;
            diff.args([
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--name-only",
                "-z",
                &assignment.base_commit,
                "--",
            ]);
            paths.extend(filesystem::nul_paths(&git_output(
                diff,
                deadline,
                FailureCode::Git,
            )?)?);
            let mut untracked = git_command(config, &checkout)?;
            // Deliberately omit --exclude-standard: ignored writes are inventoried.
            untracked.args(["ls-files", "--others", "-z", "--"]);
            paths.extend(filesystem::nul_paths(&git_output(
                untracked,
                deadline,
                FailureCode::Git,
            )?)?);
            for path in before.keys().chain(after.keys()) {
                if before.get(path) != after.get(path) {
                    paths.insert(path.clone());
                }
            }
            if paths.len() > heleos_worker_protocol::MAX_ITEMS {
                return Err(RunError::new(FailureCode::InventoryLimit));
            }
            #[cfg(windows)]
            validate_windows_forbidden_paths(
                &assignment.forbidden_paths,
                &paths.iter().cloned().collect::<Vec<_>>(),
            )?;
            let changed_files = paths
                .iter()
                .map(|path| ChangedFile {
                    path: path.clone(),
                    sha256: after.get(path).map(|entry| entry.sha256.clone()),
                    executable: after.get(path).map(|entry| entry.executable),
                })
                .collect::<Vec<_>>();
            let handoff = Handoff {
                schema: HANDOFF_SCHEMA.to_owned(), task_digest: task.digest().to_owned(), provider: assignment.provider,
                terminal_state: TerminalState::Blocked, changed_paths: paths.into_iter().collect(), checks: Vec::new(),
                candidate_commit: None, unresolved_items: vec!["Controller acceptance checks are pending; provider proposal has not been accepted.".to_owned()],
            };
            let bytes = serde_json::to_vec(&handoff)
                .map_err(|_| RunError::new(FailureCode::InvalidHandoff))?;
            let handoff = validate_handoff_json(&bytes, task).map_err(|error| {
                RunError::new(match error {
                    heleos_worker_protocol::ValidationError::OutOfScope => FailureCode::OutOfScope,
                    _ => FailureCode::InvalidHandoff,
                })
            })?;
            Ok((handoff, changed_files))
        };
        let (handoff, changed_files) = match inventory() {
            Ok(value) => value,
            Err(mut error) => {
                error.stdout = Box::new(output.stdout);
                error.stderr = Box::new(output.stderr);
                return Err(error);
            }
        };
        Ok(RunResult {
            handoff,
            stdout: output.stdout,
            stderr: output.stderr,
            changed_files,
            checkout: checkout.clone(),
            run_directory: run_directory.clone(),
            elapsed_milliseconds: started.elapsed().as_millis(),
            containment: config.containment,
            approved_internal_task_sha256: config.approved_internal_task_sha256.clone(),
        })
    };
    match work() {
        Ok(result) => {
            let persist = || -> Result<(), RunError> {
                owned.write("stdout.bin", &result.stdout.bytes)?;
                owned.write("stderr.bin", &result.stderr.bytes)?;
                owned.write("handoff.json", result.handoff.canonical_json())?;
                owned.write(
                    "run.json",
                    &serde_json::to_vec_pretty(&result.summary())
                        .map_err(|_| RunError::new(FailureCode::Io))?,
                )
            };
            if let Err(mut error) = persist() {
                error.checkout_path = Some(checkout);
                error.run_directory = Some(run_directory);
                error.stdout = Box::new(result.stdout);
                error.stderr = Box::new(result.stderr);
                error.evidence_write_failed = true;
                let _ = owned.write(
                    "failure.json",
                    &serde_json::to_vec_pretty(&error.summary()).unwrap_or_default(),
                );
                owned.retain();
                return Err(error);
            }
            owned.retain();
            Ok(result)
        }
        Err(mut error) => {
            error.checkout_path = Some(checkout);
            error.run_directory = Some(run_directory.clone());
            error.evidence_write_failed = owned.write("stdout.bin", &error.stdout.bytes).is_err();
            error.evidence_write_failed |= owned.write("stderr.bin", &error.stderr.bytes).is_err();
            error.evidence_write_failed |= owned
                .write(
                    "failure.json",
                    &serde_json::to_vec_pretty(&error.summary()).unwrap_or_default(),
                )
                .is_err();
            if config.cleanup_on_failure {
                if owned.cleanup().is_err() {
                    error.cleanup_failed = true;
                } else {
                    error.checkout_path = None;
                    error.run_directory = None;
                }
            } else {
                owned.retain();
            }
            Err(error)
        }
    }
}

#[cfg(any(unix, windows))]
fn git_command(config: &RunnerConfig, cwd: &Path) -> Result<std::process::Command, RunError> {
    let mut command = std::process::Command::new(&config.git_executable);
    #[cfg(unix)]
    command
        .current_dir(cwd)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GIT_ALLOW_PROTOCOL", "file")
        .env("GIT_NO_LAZY_FETCH", "1")
        .env("GIT_OPTIONAL_LOCKS", "0")
        .args([
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.attributesFile=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.file.allow=always",
        ]);
    #[cfg(windows)]
    {
        command.current_dir(cwd).env_clear();
        containment::controller_environment(config, &mut command)?;
        command
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env("GIT_CONFIG_GLOBAL", "NUL")
            .env("GIT_TERMINAL_PROMPT", "0")
            .env("GIT_ALLOW_PROTOCOL", "file")
            .env("GIT_NO_LAZY_FETCH", "1")
            .env("GIT_OPTIONAL_LOCKS", "0")
            .args([
                "-c",
                "core.hooksPath=NUL",
                "-c",
                "core.attributesFile=NUL",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "protocol.allow=never",
                "-c",
                "protocol.file.allow=always",
                "-c",
                "core.autocrlf=false",
                "-c",
                "core.filemode=false",
                "-c",
                "maintenance.auto=false",
                "-c",
                "gc.auto=0",
            ]);
    }
    Ok(command)
}
#[cfg(any(unix, windows))]
fn git_output(
    command: std::process::Command,
    deadline: std::time::Instant,
    code: FailureCode,
) -> Result<Vec<u8>, RunError> {
    let output = process::execute(command, &[], HARD_BYTE_LIMIT, deadline)?;
    if !output.status.success() {
        return Err(RunError::new(code));
    }
    if output.stdout.truncated || output.stderr.truncated {
        return Err(RunError::new(FailureCode::InventoryLimit));
    }
    Ok(output.stdout.bytes)
}

#[cfg(any(windows, test))]
fn validate_windows_forbidden_paths(
    forbidden: &[String],
    paths: &[String],
) -> Result<(), RunError> {
    // The protocol's exact lexical allowlist still applies. On Windows a case
    // alias must additionally not evade a forbidden file or directory entry.
    for path in paths {
        let path = path.to_uppercase();
        for prefix in forbidden {
            let prefix = prefix.to_uppercase();
            if path == prefix
                || path
                    .strip_prefix(&prefix)
                    .is_some_and(|suffix| suffix.starts_with('/'))
            {
                return Err(RunError::new(FailureCode::OutOfScope));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod evidence_tests {
    use super::*;

    #[test]
    fn windows_forbidden_path_aliases_cannot_bypass_task_scope() {
        let forbidden = vec!["allowed/secret".to_owned()];
        for path in ["allowed/SECRET", "allowed/Secret/key", "ALLOWED/secret/key"] {
            assert_eq!(
                validate_windows_forbidden_paths(&forbidden, &[path.to_owned()])
                    .unwrap_err()
                    .code,
                FailureCode::OutOfScope
            );
        }
        assert!(
            validate_windows_forbidden_paths(&forbidden, &["allowed/secret-public.txt".to_owned()])
                .is_ok()
        );
    }

    #[test]
    fn containment_evidence_reports_exact_platform_claims() {
        use heleos_worker_protocol::{validate_handoff_json, validate_task_json};
        let task = serde_json::json!({
            "schema":"heleos.worker-task/v1", "task_id":"evidence-test", "provider":"claude_code",
            "mode":"implementation", "base_commit":"a".repeat(40), "objective":"Synthetic evidence probe.",
            "allowed_paths":["allowed"], "forbidden_paths":[], "input_data_class":"PUBLIC",
            "egress_policy":"local_only", "instruction_sha256":{"AGENTS.md":"b".repeat(64)},
            "acceptance_commands":["synthetic check"], "limits":{"max_actions":1,"max_duration_seconds":5}
        });
        let task = validate_task_json(&serde_json::to_vec(&task).unwrap()).unwrap();
        for (mode, writes, tree, scope) in [
            ("none", false, false, Vec::<&str>::new()),
            (
                "macos_seatbelt",
                true,
                false,
                vec!["checkout", "home", "tmp"],
            ),
            (
                "windows_restricted_token_job",
                true,
                true,
                vec!["checkout", "home", "tmp"],
            ),
        ] {
            let handoff = serde_json::json!({"schema":"heleos.worker-handoff/v1", "task_digest":task.digest(),
                "provider":"claude_code", "terminal_state":"blocked", "changed_paths":[], "checks":[],
                "candidate_commit":null, "unresolved_items":["Controller checks pending."]});
            let handoff =
                validate_handoff_json(&serde_json::to_vec(&handoff).unwrap(), &task).unwrap();
            let result = RunResult {
                handoff,
                stdout: Capture::default(),
                stderr: Capture::default(),
                changed_files: Vec::new(),
                run_directory: PathBuf::from("synthetic-run"),
                elapsed_milliseconds: 0,
                checkout: PathBuf::from("synthetic-run/checkout"),
                containment: serde_json::from_value(serde_json::json!(mode)).unwrap(),
                approved_internal_task_sha256: None,
            };
            assert_eq!(
                result.summary()["containment"],
                serde_json::json!({
                    "mode":mode, "host_path_writes_restricted":writes, "process_tree_contained":tree,
                    "write_scope":scope, "reads_restricted":false, "network_restricted":false,
                    "inherited_external_authority_restricted":false
                })
            );
        }
    }
}
