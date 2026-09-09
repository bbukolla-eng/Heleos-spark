#![forbid(unsafe_code)]
use clap::Parser;
use heleos_worker_protocol::Provider;
use heleos_worker_runner::{
    ContainmentMode, FailureCode, HARD_BYTE_LIMIT, ProviderCommand, RunError, RunnerConfig,
    run_json,
};
use std::ffi::OsString;
use std::fs::File;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::process::ExitCode;

#[derive(Parser)]
#[command(
    about = "Run one local Codex, Claude Code, Kimi, or Grok proposal in an isolated exact-base checkout"
)]
struct Arguments {
    #[arg(long)]
    task: PathBuf,
    #[arg(long)]
    source: PathBuf,
    #[arg(long)]
    workspace_root: PathBuf,
    #[arg(long, value_parser = ["codex", "claude_code", "kimi", "grok"])]
    provider: String,
    #[arg(long)]
    command: PathBuf,
    #[arg(long, default_value = "/usr/bin/git")]
    git: PathBuf,
    #[arg(long, default_value_t = 65_536)]
    max_prompt_bytes: usize,
    #[arg(long, default_value_t = 65_536)]
    max_output_bytes: usize,
    #[arg(long)]
    cleanup_on_failure: bool,
    /// Restrict provider path writes to checkout/home/tmp using the selected host backend.
    #[arg(long, value_enum, default_value = "none")]
    containment: ContainmentMode,
    /// Explicit environment names to inherit; values never enter the prompt/report.
    #[arg(long)]
    inherit_env: Vec<OsString>,
    /// Literal provider arguments, placed after --.
    #[arg(last = true)]
    args: Vec<OsString>,
}
fn main() -> ExitCode {
    let args = match Arguments::try_parse() {
        Ok(args) => args,
        Err(error)
            if matches!(
                error.kind(),
                clap::error::ErrorKind::DisplayHelp | clap::error::ErrorKind::DisplayVersion
            ) =>
        {
            let _ = error.print();
            return ExitCode::SUCCESS;
        }
        Err(_) => return emit_error(RunError::new(FailureCode::InvalidConfiguration)),
    };
    match execute(args) {
        Ok(value) => {
            if emit(&value).is_ok() {
                ExitCode::SUCCESS
            } else {
                ExitCode::from(2)
            }
        }
        Err(error) => emit_error(error),
    }
}
fn execute(args: Arguments) -> Result<serde_json::Value, RunError> {
    let metadata = std::fs::symlink_metadata(&args.task)
        .map_err(|_| RunError::new(FailureCode::InvalidTask))?;
    if !metadata.is_file() || metadata.len() > HARD_BYTE_LIMIT as u64 {
        return Err(RunError::new(FailureCode::InvalidTask));
    }
    let file = File::open(&args.task).map_err(|_| RunError::new(FailureCode::InvalidTask))?;
    if !file
        .metadata()
        .map_err(|_| RunError::new(FailureCode::InvalidTask))?
        .is_file()
    {
        return Err(RunError::new(FailureCode::InvalidTask));
    }
    let mut input = Vec::new();
    file.take(HARD_BYTE_LIMIT as u64 + 1)
        .read_to_end(&mut input)
        .map_err(|_| RunError::new(FailureCode::InvalidTask))?;
    let provider = match args.provider.as_str() {
        "codex" => Provider::Codex,
        "claude_code" => Provider::ClaudeCode,
        "kimi" => Provider::Kimi,
        "grok" => Provider::Grok,
        _ => return Err(RunError::new(FailureCode::InvalidConfiguration)),
    };
    let mut command = ProviderCommand::new(provider, args.command);
    command.args = args.args;
    for name in args.inherit_env {
        let value = std::env::var_os(&name)
            .ok_or_else(|| RunError::new(FailureCode::InvalidConfiguration))?;
        command.environment.insert(name, value);
    }
    let mut config = RunnerConfig::new(args.source, args.workspace_root, args.git, command);
    config.max_prompt_bytes = args.max_prompt_bytes;
    config.max_output_bytes = args.max_output_bytes;
    config.cleanup_on_failure = args.cleanup_on_failure;
    config.containment = args.containment;
    Ok(run_json(&input, &config)?.summary())
}
fn emit_error(error: RunError) -> ExitCode {
    let _ = emit(&error.summary());
    ExitCode::from(2)
}
fn emit(value: &serde_json::Value) -> std::io::Result<()> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    std::io::stdout().lock().write_all(&bytes)
}
