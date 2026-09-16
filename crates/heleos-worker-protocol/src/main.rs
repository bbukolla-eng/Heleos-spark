#![forbid(unsafe_code)]

use clap::{Parser, Subcommand};
use heleos_worker_protocol::{
    HANDOFF_SCHEMA, MAX_DOCUMENT_BYTES, TASK_SCHEMA, VALIDATION_SCHEMA, validate_handoff_json,
    validate_task_json, validate_task_json_with_internal_claude_approval,
};
use serde::Serialize;
use std::fs::File;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

#[derive(Parser)]
#[command(about = "Validate a bounded worker task or handoff without executing it")]
struct Arguments {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    Task {
        file: PathBuf,
        #[arg(long)]
        approved_internal_task_sha256: Option<String>,
    },
    Handoff {
        #[arg(long)]
        task: PathBuf,
        file: PathBuf,
        #[arg(long)]
        approved_internal_task_sha256: Option<String>,
    },
}

#[derive(Serialize)]
#[serde(deny_unknown_fields)]
struct Envelope<'a> {
    schema: &'static str,
    document_schema: &'static str,
    valid: bool,
    digest: &'a str,
}

fn main() -> ExitCode {
    let result = Arguments::try_parse()
        .map_err(|_| {
            "usage: heleos-worker-protocol task <file> | handoff --task <file> <file>".to_owned()
        })
        .and_then(run);
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            // All errors originate in fixed diagnostics. Do not echo arguments,
            // JSON values, paths, commands, or serde's unknown-field messages.
            let _ = writeln!(std::io::stderr().lock(), "error: {error}");
            ExitCode::from(2)
        }
    }
}

fn run(arguments: Arguments) -> Result<(), String> {
    let (schema, digest) = match arguments.command {
        Command::Task {
            file,
            approved_internal_task_sha256,
        } => {
            let raw = read_document(&file)?;
            let task = match approved_internal_task_sha256 {
                Some(approval) => validate_task_json_with_internal_claude_approval(&raw, &approval),
                None => validate_task_json(&raw),
            }
            .map_err(|error| error.to_string())?;
            (TASK_SCHEMA, task.digest().to_owned())
        }
        Command::Handoff {
            task,
            file,
            approved_internal_task_sha256,
        } => {
            let raw = read_document(&task)?;
            let task = match approved_internal_task_sha256 {
                Some(approval) => validate_task_json_with_internal_claude_approval(&raw, &approval),
                None => validate_task_json(&raw),
            }
            .map_err(|error| error.to_string())?;
            let handoff = validate_handoff_json(&read_document(&file)?, &task)
                .map_err(|error| error.to_string())?;
            (HANDOFF_SCHEMA, handoff.digest().to_owned())
        }
    };
    let envelope = Envelope {
        schema: VALIDATION_SCHEMA,
        document_schema: schema,
        valid: true,
        digest: &digest,
    };
    let mut bytes = serde_jcs::to_vec(&envelope).map_err(|_| "validation output failed")?;
    bytes.push(b'\n');
    std::io::stdout()
        .lock()
        .write_all(&bytes)
        .map_err(|_| "validation output failed".to_owned())
}

fn read_document(path: &Path) -> Result<Vec<u8>, String> {
    // Reject directories/devices before opening; recheck the opened object.
    let metadata = std::fs::metadata(path).map_err(|_| "cannot read document")?;
    if !metadata.is_file() || metadata.len() > MAX_DOCUMENT_BYTES as u64 {
        return Err("document must be a regular file within the byte limit".to_owned());
    }
    let file = File::open(path).map_err(|_| "cannot read document")?;
    if !file
        .metadata()
        .map_err(|_| "cannot read document")?
        .is_file()
    {
        return Err("document must be a regular file".to_owned());
    }
    let mut bytes = Vec::new();
    file.take(MAX_DOCUMENT_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "cannot read document")?;
    if bytes.len() > MAX_DOCUMENT_BYTES {
        return Err("document exceeds the byte limit".to_owned());
    }
    Ok(bytes)
}
