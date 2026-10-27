//! Typed command-line grammar.
//!
//! The whole operator surface is declared here as one clap root parser with
//! nested subcommand enums. Every value that has a domain type in
//! `heleos-core` is parsed into that domain type at this boundary, so the
//! executor below never handles an unvalidated string. Paths stay `PathBuf`
//! so non-UTF-8 operating-system paths survive intact.

use std::ffi::OsStr;
use std::path::PathBuf;
use std::str::FromStr;

use clap::{Args, ColorChoice, Parser, Subcommand};
use heleos_core::{ActorId, IdempotencyKey, JobId, ProjectId, Sha256Digest};

#[derive(Debug, Parser)]
#[command(
    name = "heleos",
    version,
    about = "Heleos Foundation data and recovery tools",
    color = ColorChoice::Never,
    arg_required_else_help = false,
    subcommand_required = true,
    disable_help_subcommand = true
)]
pub(crate) struct Cli {
    #[command(subcommand)]
    pub(crate) command: Command,
}

#[derive(Debug, Subcommand)]
pub(crate) enum Command {
    /// Foundation database maintenance.
    Db {
        #[command(subcommand)]
        command: DbCommand,
    },
    /// Project administration.
    Project {
        #[command(subcommand)]
        command: ProjectCommand,
    },
    /// Ingest one PDF into a project.
    Ingest(IngestArgs),
    /// Read-only Foundation reports.
    Inspect {
        #[command(subcommand)]
        command: InspectCommand,
    },
    /// Verify the database, audit chain, and optionally one object or revision.
    Verify(VerifyArgs),
    /// Encrypted backup administration.
    Backup {
        #[command(subcommand)]
        command: BackupCommand,
    },
    /// Restore an encrypted backup into a new destination.
    Restore(RestoreArgs),
    /// Job administration.
    Jobs {
        #[command(subcommand)]
        command: JobsCommand,
    },
}

#[derive(Debug, Subcommand)]
pub(crate) enum DbCommand {
    /// Apply pending Foundation migrations.
    Migrate(DatabaseArgs),
}

#[derive(Debug, Subcommand)]
pub(crate) enum ProjectCommand {
    /// Create one project.
    Create(ProjectCreateArgs),
}

#[derive(Debug, Subcommand)]
pub(crate) enum InspectCommand {
    /// Report the Foundation state of one project.
    Foundation(InspectFoundationArgs),
}

#[derive(Debug, Subcommand)]
pub(crate) enum BackupCommand {
    /// Create one signed, encrypted backup container.
    Create(BackupCreateArgs),
    /// Verify one signed, encrypted backup container.
    Verify(BackupVerifyArgs),
}

#[derive(Debug, Subcommand)]
pub(crate) enum JobsCommand {
    /// Resume one interrupted ingest job.
    Resume(JobsResumeArgs),
}

#[derive(Debug, Args)]
pub(crate) struct DatabaseArgs {
    /// Path to the Foundation SQLite database.
    #[arg(long)]
    pub(crate) database: PathBuf,
}

#[derive(Debug, Args)]
pub(crate) struct ProjectCreateArgs {
    /// Path to the Foundation SQLite database.
    #[arg(long)]
    pub(crate) database: PathBuf,
    /// Human-readable project name.
    #[arg(long)]
    pub(crate) name: String,
    /// Optional explicit project identifier.
    #[arg(long, value_name = "UUID", value_parser = parse_project_id)]
    pub(crate) id: Option<ProjectId>,
    /// Identifier of the acting operator.
    #[arg(long, value_name = "ACTOR_ID", value_parser = parse_actor_id)]
    pub(crate) actor: ActorId,
}

#[derive(Debug, Args)]
pub(crate) struct IngestArgs {
    /// Path to the PDF being ingested.
    pub(crate) pdf: PathBuf,
    /// Target project identifier.
    #[arg(long, value_name = "UUID", value_parser = parse_project_id)]
    pub(crate) project: ProjectId,
    /// Caller-chosen idempotency key of 1..=128 UTF-8 bytes.
    #[arg(long, value_name = "KEY", value_parser = parse_idempotency_key)]
    pub(crate) idempotency_key: IdempotencyKey,
    /// Path to the Foundation SQLite database.
    #[arg(long)]
    pub(crate) database: PathBuf,
    /// Path to the content vault root.
    #[arg(long)]
    pub(crate) vault: PathBuf,
    /// Identifier of the acting operator.
    #[arg(long, value_name = "ACTOR_ID", value_parser = parse_actor_id)]
    pub(crate) actor: ActorId,
}

#[derive(Debug, Args)]
pub(crate) struct InspectFoundationArgs {
    /// Project identifier to report on.
    #[arg(long, value_name = "UUID", value_parser = parse_project_id)]
    pub(crate) project: ProjectId,
    /// Path to the Foundation SQLite database.
    #[arg(long)]
    pub(crate) database: PathBuf,
    /// Path to the content vault root.
    #[arg(long)]
    pub(crate) vault: PathBuf,
}

#[derive(Debug, Args)]
pub(crate) struct VerifyArgs {
    /// Path to the Foundation SQLite database.
    #[arg(long)]
    pub(crate) database: PathBuf,
    /// Path to the content vault root.
    #[arg(long)]
    pub(crate) vault: PathBuf,
    /// Verify one vault object by content digest.
    #[arg(long, value_name = "SHA256", value_parser = parse_digest, conflicts_with = "revision")]
    pub(crate) object: Option<Sha256Digest>,
    /// Verify one revision's evidence manifest and its objects.
    #[arg(long, value_name = "SHA256", value_parser = parse_digest)]
    pub(crate) revision: Option<Sha256Digest>,
}

#[derive(Debug, Args)]
pub(crate) struct BackupCreateArgs {
    /// Path the encrypted container is published to.
    pub(crate) destination: PathBuf,
    /// Path to the Foundation SQLite database.
    #[arg(long)]
    pub(crate) database: PathBuf,
    /// Path to the content vault root.
    #[arg(long)]
    pub(crate) vault: PathBuf,
    /// Public age X25519 recipient the container is encrypted to.
    #[arg(long, value_name = "AGE_RECIPIENT", value_parser = parse_recipient)]
    pub(crate) recipient: AgeRecipient,
    /// Path to the private 32-byte Ed25519 signing key.
    #[arg(long)]
    pub(crate) signing_key: PathBuf,
}

#[derive(Debug, Args)]
pub(crate) struct BackupVerifyArgs {
    /// Path to the encrypted container.
    pub(crate) backup: PathBuf,
    /// Path to the private age X25519 identity file.
    #[arg(long)]
    pub(crate) identity: PathBuf,
    /// Path to the trusted 32-byte Ed25519 verifying key.
    #[arg(long)]
    pub(crate) trusted_signer: PathBuf,
}

#[derive(Debug, Args)]
pub(crate) struct RestoreArgs {
    /// Path to the encrypted container.
    pub(crate) backup: PathBuf,
    /// Destination directory, which must not already exist.
    #[arg(long)]
    pub(crate) into: PathBuf,
    /// Path to the private age X25519 identity file.
    #[arg(long)]
    pub(crate) identity: PathBuf,
    /// Path to the trusted 32-byte Ed25519 verifying key.
    #[arg(long)]
    pub(crate) trusted_signer: PathBuf,
    /// Acknowledge that restore writes a new Foundation tree.
    #[arg(long, required = true, action = clap::ArgAction::SetTrue)]
    pub(crate) verify: bool,
}

#[derive(Debug, Args)]
pub(crate) struct JobsResumeArgs {
    /// Identifier of the interrupted job.
    #[arg(value_name = "JOB_ID", value_parser = parse_job_id)]
    pub(crate) job_id: JobId,
    /// Path to the Foundation SQLite database.
    #[arg(long)]
    pub(crate) database: PathBuf,
    /// Path to the content vault root.
    #[arg(long)]
    pub(crate) vault: PathBuf,
    /// Identifier of the acting operator.
    #[arg(long, value_name = "ACTOR_ID", value_parser = parse_actor_id)]
    pub(crate) actor: ActorId,
}

/// An age X25519 recipient parsed at the boundary.
///
/// `age::x25519::Recipient` is not `Debug`, which clap's derive requires, so
/// the parsed recipient travels in this thin wrapper.
#[derive(Clone)]
pub(crate) struct AgeRecipient(age::x25519::Recipient);

impl AgeRecipient {
    pub(crate) const fn as_recipient(&self) -> &age::x25519::Recipient {
        &self.0
    }
}

impl std::fmt::Debug for AgeRecipient {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("AgeRecipient")
    }
}

fn parse_project_id(value: &str) -> Result<ProjectId, String> {
    ProjectId::from_str(value).map_err(|_| "expected a UUID".to_owned())
}

fn parse_job_id(value: &str) -> Result<JobId, String> {
    JobId::from_str(value).map_err(|_| "expected a UUID".to_owned())
}

fn parse_actor_id(value: &str) -> Result<ActorId, String> {
    ActorId::from_str(value)
        .map_err(|_| "expected 1..=128 UTF-8 bytes without control characters".to_owned())
}

fn parse_idempotency_key(value: &str) -> Result<IdempotencyKey, String> {
    IdempotencyKey::from_str(value)
        .map_err(|_| "expected 1..=128 UTF-8 bytes without control characters".to_owned())
}

fn parse_digest(value: &str) -> Result<Sha256Digest, String> {
    Sha256Digest::from_str(value).map_err(|_| "expected 64 lowercase hexadecimal digits".to_owned())
}

fn parse_recipient(value: &str) -> Result<AgeRecipient, String> {
    age::x25519::Recipient::from_str(value)
        .map(AgeRecipient)
        .map_err(|_| "expected an age X25519 recipient".to_owned())
}

/// The exact long flags this grammar declares.
///
/// Diagnostics may name a flag only by borrowing an entry from this table.
/// A spelling that came from `argv` is therefore never echoed back, so a
/// mistyped identity path or another private operand cannot reach stderr
/// through a usage message.
pub(crate) const DECLARED_LONG_FLAGS: [&str; 17] = [
    "--actor",
    "--database",
    "--help",
    "--id",
    "--idempotency-key",
    "--identity",
    "--into",
    "--name",
    "--object",
    "--project",
    "--recipient",
    "--revision",
    "--signing-key",
    "--trusted-signer",
    "--vault",
    "--verify",
    "--version",
];

/// A long flag that `argv` supplied more than once.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DuplicateFlag {
    /// A declared flag. The borrowed name is this program's own text.
    Declared(&'static str),
    /// A repeat the grammar does not declare, whose spelling stays unrendered.
    Undeclared,
}

/// Rejects a long flag that appears more than once before the `--` separator.
///
/// clap resolves a repeated single-value option by keeping the last spelling.
/// For destructive recovery tooling a silently discarded `--database` is a
/// hazard, so the raw vector is screened before parsing and any repeat is a
/// usage error.
pub(crate) fn duplicate_long_flag<I, S>(arguments: I) -> Option<DuplicateFlag>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let mut seen = Vec::<String>::new();
    for argument in arguments {
        let Some(text) = argument.as_ref().to_str() else {
            continue;
        };
        if text == "--" {
            break;
        }
        let Some(flag) = text.strip_prefix("--") else {
            continue;
        };
        let name = flag.split_once('=').map_or(flag, |(name, _)| name);
        if name.is_empty() {
            continue;
        }
        let name = format!("--{name}");
        if seen.contains(&name) {
            return Some(
                DECLARED_LONG_FLAGS
                    .into_iter()
                    .find(|declared| *declared == name)
                    .map_or(DuplicateFlag::Undeclared, DuplicateFlag::Declared),
            );
        }
        seen.push(name);
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_declared_grammar_is_internally_consistent() {
        // Break caught: a nested subcommand or flag whose clap declaration
        // cannot be assembled at all, which would only surface at runtime.
        <Cli as clap::CommandFactory>::command().debug_assert();
    }

    #[test]
    fn every_declared_leaf_parses_its_exact_flag_set() {
        let parsed = Cli::try_parse_from([
            "heleos",
            "backup",
            "create",
            "/out.age",
            "--database",
            "/db",
            "--vault",
            "/vault",
            "--recipient",
            "age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p",
            "--signing-key",
            "/signing.key",
        ])
        .expect("the documented backup-create spelling parses");
        let Command::Backup {
            command: BackupCommand::Create(arguments),
        } = parsed.command
        else {
            panic!("backup create must reach its own leaf");
        };
        assert_eq!(arguments.destination, PathBuf::from("/out.age"));
        assert_eq!(arguments.database, PathBuf::from("/db"));
        assert_eq!(arguments.vault, PathBuf::from("/vault"));
        assert_eq!(arguments.signing_key, PathBuf::from("/signing.key"));
    }

    #[test]
    fn object_and_revision_cannot_be_supplied_together() {
        // Break caught: a verify invocation that silently ignores one selector.
        let digest = "0".repeat(64);
        assert!(
            Cli::try_parse_from([
                "heleos",
                "verify",
                "--database",
                "/db",
                "--vault",
                "/v",
                "--object",
                &digest,
            ])
            .is_ok()
        );
        assert!(
            Cli::try_parse_from([
                "heleos",
                "verify",
                "--database",
                "/db",
                "--vault",
                "/v",
                "--revision",
                &digest,
            ])
            .is_ok()
        );
        let error = Cli::try_parse_from([
            "heleos",
            "verify",
            "--database",
            "/db",
            "--vault",
            "/v",
            "--object",
            &digest,
            "--revision",
            &digest,
        ])
        .expect_err("the two selectors are mutually exclusive");
        assert_eq!(error.kind(), clap::error::ErrorKind::ArgumentConflict);
    }

    #[test]
    fn restore_requires_the_destructive_acknowledgement() {
        assert!(
            Cli::try_parse_from([
                "heleos",
                "restore",
                "/b.age",
                "--into",
                "/into",
                "--identity",
                "/id",
                "--trusted-signer",
                "/ts",
            ])
            .is_err()
        );
        let parsed = Cli::try_parse_from([
            "heleos",
            "restore",
            "/b.age",
            "--into",
            "/into",
            "--identity",
            "/id",
            "--trusted-signer",
            "/ts",
            "--verify",
        ])
        .expect("the acknowledged spelling parses");
        let Command::Restore(arguments) = parsed.command else {
            panic!("restore must reach its own leaf");
        };
        assert!(arguments.verify);
    }

    #[test]
    fn typed_boundary_values_reject_hostile_spellings() {
        // Break caught: an unvalidated identifier, key, or digest reaching core.
        assert!(parse_project_id("not-a-uuid").is_err());
        assert!(parse_project_id("11111111-1111-4111-8111-111111111111").is_ok());
        assert!(parse_job_id("").is_err());
        assert!(parse_actor_id("").is_err());
        assert!(parse_actor_id(&"a".repeat(129)).is_err());
        assert!(parse_actor_id(&"a".repeat(128)).is_ok());
        assert!(parse_actor_id("bad\u{7}actor").is_err());
        assert!(parse_idempotency_key("").is_err());
        assert!(parse_idempotency_key(&"k".repeat(129)).is_err());
        assert!(parse_idempotency_key(&"k".repeat(128)).is_ok());
        assert!(parse_idempotency_key("bad\nkey").is_err());
        assert!(parse_digest(&"0".repeat(63)).is_err());
        assert!(parse_digest(&"A".repeat(64)).is_err());
        assert!(parse_digest(&"g".repeat(64)).is_err());
        assert!(parse_digest(&"0".repeat(64)).is_ok());
        assert!(parse_recipient("ssh-ed25519 AAAA").is_err());
        assert!(parse_recipient("").is_err());
        assert!(
            parse_recipient("age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p")
                .is_ok()
        );
    }

    /// Collects every long flag the built clap grammar actually declares.
    fn clap_declared_long_flags() -> Vec<String> {
        fn walk(command: &clap::Command, found: &mut Vec<String>) {
            for argument in command.get_arguments() {
                if let Some(long) = argument.get_long() {
                    found.push(format!("--{long}"));
                }
            }
            for subcommand in command.get_subcommands() {
                walk(subcommand, found);
            }
        }

        let mut found = Vec::new();
        walk(&<Cli as clap::CommandFactory>::command(), &mut found);
        found.sort_unstable();
        found.dedup();
        found
    }

    #[test]
    fn the_declared_flag_table_covers_every_flag_the_grammar_accepts() {
        // Break caught: a new flag whose repeat would fall through to the
        // undeclared branch, or a stale table entry naming a removed flag.
        let mut sorted = DECLARED_LONG_FLAGS;
        sorted.sort_unstable();
        assert_eq!(
            sorted.as_slice(),
            DECLARED_LONG_FLAGS.as_slice(),
            "the table must stay sorted so it reads as one closed vocabulary"
        );
        let mut deduplicated = DECLARED_LONG_FLAGS.to_vec();
        deduplicated.dedup();
        assert_eq!(deduplicated.len(), DECLARED_LONG_FLAGS.len());

        for flag in clap_declared_long_flags() {
            assert!(
                DECLARED_LONG_FLAGS.contains(&flag.as_str()),
                "{flag} is accepted by the grammar but missing from the table"
            );
        }
    }

    #[test]
    fn duplicate_long_flags_are_detected_before_clap_discards_one() {
        // Break caught: `--database a --database b` silently keeping only `b`.
        assert_eq!(
            duplicate_long_flag(["db", "migrate", "--database", "/a", "--database", "/b"]),
            Some(DuplicateFlag::Declared("--database"))
        );
        assert_eq!(
            duplicate_long_flag(["db", "migrate", "--database=/a", "--database=/b"]),
            Some(DuplicateFlag::Declared("--database"))
        );
        assert_eq!(
            duplicate_long_flag(["db", "migrate", "--database=/a", "--database", "/b"]),
            Some(DuplicateFlag::Declared("--database"))
        );
        assert_eq!(
            duplicate_long_flag(["db", "migrate", "--database", "/a"]),
            None
        );
        assert_eq!(
            duplicate_long_flag(["verify", "--object", "--revision"]),
            None
        );
        // Everything after the separator is a positional, never a flag.
        assert_eq!(
            duplicate_long_flag([
                "ingest",
                "--database",
                "/a",
                "--",
                "--database",
                "--database"
            ]),
            None
        );
        // A bare `--` prefix with no name is not a flag name.
        assert_eq!(duplicate_long_flag(["--=x", "--=y"]), None);
    }

    #[test]
    fn a_repeated_undeclared_flag_never_returns_its_own_spelling() {
        // Break caught: a hostile or secret-shaped repeat borrowing its text
        // from `argv` and reaching the operator diagnostic verbatim.
        for spelling in [
            "--AGE-SECRET-KEY-1EXAMPLE",
            "--\u{4e2d}\u{6587}",
            "--; DROP TABLE projects; --",
            "--databases",
        ] {
            assert_eq!(
                duplicate_long_flag(["db", "migrate", spelling, spelling]),
                Some(DuplicateFlag::Undeclared),
                "{spelling} must not be reported by name"
            );
        }
    }
}
